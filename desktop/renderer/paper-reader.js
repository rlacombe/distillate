/* ───── Paper reader — PDF.js viewer + Zotero annotation overlay ─────
 *
 * Renders a cached/downloaded PDF from the backend into a scrollable
 * canvas list inside the papers detail pane, overlays existing Zotero
 * highlights on each page, and persists the last-visible page so the
 * reader re-opens where you left off.
 *
 * Zotero is the source of truth for highlights and read state — this is
 * one of three parallel reading surfaces (reMarkable, Zotero desktop,
 * this). Text selections are written back to Zotero as plain user
 * highlights via POST /papers/{key}/annotations.
 */

/* globals serverPort, showToast */

// ---- PDF.js loader (singleton) -------------------------------------------
// Shared with canvas.js which reuses these globals for LaTeX preview.

let _pdfjsLib = null;
let _pdfjsPromise = null;
async function _loadPdfJs() {
  if (_pdfjsLib) return _pdfjsLib;
  if (_pdfjsPromise) return _pdfjsPromise;
  _pdfjsPromise = (async () => {
    const mod = await import("https://esm.sh/pdfjs-dist@4.7.76/build/pdf.mjs");
    mod.GlobalWorkerOptions.workerSrc =
      "https://esm.sh/pdfjs-dist@4.7.76/build/pdf.worker.mjs";
    _pdfjsLib = mod;
    return mod;
  })();
  return _pdfjsPromise;
}

// ---- Reader state (single active reader at a time) ----------------------

let _reader = null;  // { paperKey, pdfDoc, pages, pageEls, saveTimer, ... }

function _clearReader() {
  if (!_reader) return;
  if (_reader.saveTimer) clearTimeout(_reader.saveTimer);
  if (_reader.observer) _reader.observer.disconnect();
  if (_reader.selectionHandler) {
    document.removeEventListener("mouseup", _reader.selectionHandler);
    document.removeEventListener("selectionchange", _reader.selectionHandler);
  }
  if (_reader._keyHandler) {
    document.removeEventListener("keydown", _reader._keyHandler);
  }
  if (_reader.saveButton && _reader.saveButton.parentNode) {
    _reader.saveButton.parentNode.removeChild(_reader.saveButton);
  }
  // Release the PDF.js worker, page caches, and rendered bitmaps. Without
  // this, switching papers pins hundreds of MB of IOSurface per document
  // (each page's canvas is ~13 MB at 2× DPR, ×50 pages = 650 MB per paper).
  if (_reader.pdfDoc) {
    try { _reader.pdfDoc.destroy(); } catch {}
  }
  if (_reader.annotationsByPage) _reader.annotationsByPage.clear();
  _reader.textContents = [];
  _reader.pageEls = [];
  _reader.pageViewports = [];
  _reader = null;
}

/**
 * Open the desktop PDF reader for a given paper, mounted inside an
 * arbitrary container. The caller owns the container's layout — we paint
 * a scroll + pages structure into it and nothing else.
 *
 * @param {string} paperKey — Zotero item key
 * @param {{container: HTMLElement, showToolbar?: boolean, title?: string}} opts
 * @returns {() => void} — tear-down function; safe to call multiple times.
 */
async function openPaperReader(paperKey, opts = {}) {
  _clearReader();

  const host = opts.container;
  if (!host || !serverPort) return () => {};

  host.innerHTML = "";
  host.classList.add("paper-reader-host");

  // Layout: optional toolbar on top, scrollable pages below.
  const wrapper = document.createElement("div");
  wrapper.className = "paper-reader";
  host.appendChild(wrapper);

  let pageCounter = null;
  if (opts.showToolbar) {
    const toolbar = document.createElement("div");
    toolbar.className = "paper-reader-toolbar";
    wrapper.appendChild(toolbar);

    const titleEl = document.createElement("div");
    titleEl.className = "paper-reader-title";
    titleEl.textContent = opts.title || "Reading…";
    toolbar.appendChild(titleEl);

    pageCounter = document.createElement("div");
    pageCounter.className = "paper-reader-page-counter";
    pageCounter.textContent = "";
    toolbar.appendChild(pageCounter);
  }

  const pagesHost = document.createElement("div");
  pagesHost.className = "paper-reader-pages";
  wrapper.appendChild(pagesHost);

  const statusEl = document.createElement("div");
  statusEl.className = "paper-reader-status";
  statusEl.textContent = "Loading PDF…";
  pagesHost.appendChild(statusEl);

  // Single tear-down closure — returned to caller AND used as the early-
  // return path on errors. Idempotent.
  let _disposed = false;
  const _disposer = () => {
    if (_disposed) return;
    _disposed = true;
    _clearReader();
  };

  _reader = {
    paperKey,
    pdfDoc: null,
    pageEls: [],
    pageViewports: [],
    annotationsByPage: new Map(),
    visiblePage: 1,
    savedPage: 1,
    saveTimer: null,
    observer: null,
    host: wrapper,
    pagesHost,
    pageCounter,
    selectionHandler: null,
    saveButton: null,
    pendingHighlight: null,
  };

  // Fetch PDF bytes, annotations, and last-read position in parallel.
  let pdfResp, annResp, posResp;
  try {
    [pdfResp, annResp, posResp] = await Promise.all([
      fetch(`http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}/pdf`),
      fetch(`http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}/annotations`),
      fetch(`http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}/read-position`),
    ]);
  } catch (err) {
    statusEl.textContent = `Could not reach server: ${err.message || err}`;
    return _disposer();
  }

  if (!pdfResp.ok) {
    // Map the server's reason code to a friendly line. We never surface
    // raw exception strings (they leak Zotero API URLs etc.), but we DO
    // show the local file paths that were tried — that's actionable
    // diagnostic info on a local-only desktop app.
    let reason = "";
    let searched = [];
    let diag = null;
    try {
      const body = await pdfResp.json();
      reason = body.reason || "";
      searched = Array.isArray(body.searched) ? body.searched : [];
      diag = body.diag || null;
    } catch { /* not JSON */ }
    const friendly = _friendlyPdfError(reason);
    let html =
      `<p>${_escape(friendly.headline)}</p>` +
      (friendly.hint ? `<p class="paper-reader-status-detail">${_escape(friendly.hint)}</p>` : "");

    // Diagnostic block: vault + papers dir + searched paths. This is by
    // far the most useful signal when the resolver can't find a PDF —
    // it tells the user whether the vault is even configured, which
    // folder we resolved, and exactly what filenames we tried.
    if (diag || searched.length) {
      const diagRows = [];
      if (diag) {
        diagRows.push(`<div><span class="diag-k">Vault:</span> ${_escape(diag.vault_path || "<not configured>")}</div>`);
        diagRows.push(`<div><span class="diag-k">Papers folder:</span> ${_escape(diag.papers_dir || "<not resolved>")}</div>`);
        if (Array.isArray(diag.stems) && diag.stems.length) {
          diagRows.push(`<div><span class="diag-k">Filename stems:</span> ${diag.stems.map(_escape).join(", ")}</div>`);
        }
      }
      const pathItems = searched.map((p) => `<li>${_escape(p)}</li>`).join("");
      html += `
        <details class="paper-reader-searched" open>
          <summary>Debug info</summary>
          <div class="diag-block">${diagRows.join("")}</div>
          ${searched.length ? `<div class="diag-k" style="margin-top:8px">Paths probed (${searched.length}):</div><ul>${pathItems}</ul>` : ""}
        </details>`;
    }
    statusEl.innerHTML = html;
    return _disposer();
  }

  const pdfBuf = await pdfResp.arrayBuffer();
  const annData = annResp.ok ? await annResp.json() : { annotations: [] };
  const posData = posResp.ok ? await posResp.json() : { page: 1 };
  const savedPage = Math.max(1, parseInt(posData.page, 10) || 1);
  _reader.savedPage = savedPage;

  // Group annotations by 0-based page index for quick lookup per page.
  for (const ann of (annData.annotations || [])) {
    const idx = Number.isFinite(ann.page_index) ? ann.page_index : 0;
    if (!_reader.annotationsByPage.has(idx)) {
      _reader.annotationsByPage.set(idx, []);
    }
    _reader.annotationsByPage.get(idx).push(ann);
  }

  // Parse the PDF.
  let pdf;
  try {
    const pdfjs = await _loadPdfJs();
    pdf = await pdfjs.getDocument({ data: new Uint8Array(pdfBuf) }).promise;
  } catch (err) {
    console.error("[paper-reader] PDF parse failed:", err);
    statusEl.textContent = `Failed to open PDF: ${err.message || err}`;
    return _disposer();
  }
  _reader.pdfDoc = pdf;

  statusEl.remove();

  // Compute a fit-to-width base scale from page 1 (most papers have
  // uniform page sizes). The zoom multiplier scales relative to this.
  const firstPage = await pdf.getPage(1);
  const naturalVp = firstPage.getViewport({ scale: 1 });
  const paneWidth = Math.max(400, (pagesHost.clientWidth || 800) - 48);
  const baseScale = Math.min(1.8, paneWidth / naturalVp.width);

  _reader.baseScale = baseScale;
  _reader.zoomLevel = 0.75;

  // ── Controls: [−] 75% [+]  Mark read  Page N / M ──
  // Mounted into an external container (e.g. the tab bar) if provided,
  // otherwise into a dedicated bar inside the reader wrapper.
  const controlsHost = opts.controlsContainer || (() => {
    const bar = document.createElement("div");
    bar.className = "paper-reader-zoom-bar";
    wrapper.insertBefore(bar, pagesHost);
    return bar;
  })();

  const zoomOut = document.createElement("button");
  zoomOut.type = "button";
  zoomOut.className = "paper-reader-zoom-btn";
  zoomOut.textContent = "−";
  zoomOut.title = "Zoom out";
  zoomOut.addEventListener("click", () => _stepZoom(-1));
  controlsHost.appendChild(zoomOut);

  const zoomPct = document.createElement("span");
  zoomPct.className = "paper-reader-zoom-pct";
  zoomPct.textContent = "75%";
  zoomPct.title = "Reset zoom";
  zoomPct.addEventListener("click", () => _setZoom(0.75));
  controlsHost.appendChild(zoomPct);

  const zoomIn = document.createElement("button");
  zoomIn.type = "button";
  zoomIn.className = "paper-reader-zoom-btn";
  zoomIn.textContent = "+";
  zoomIn.title = "Zoom in";
  zoomIn.addEventListener("click", () => _stepZoom(1));
  controlsHost.appendChild(zoomIn);

  // "Mark read" button — only for unread papers.
  const isRead = opts.status === "processed";
  const markReadBtn = document.createElement("button");
  markReadBtn.type = "button";
  markReadBtn.className = "paper-reader-mark-read";
  if (isRead) {
    markReadBtn.textContent = "Read \u2713";
    markReadBtn.disabled = true;
    markReadBtn.classList.add("done");
  } else {
    markReadBtn.textContent = "Mark read";
    markReadBtn.addEventListener("click", () => _markRead(markReadBtn));
  }
  controlsHost.appendChild(markReadBtn);

  const pageCounterEl = document.createElement("div");
  pageCounterEl.className = "paper-reader-page-counter";
  pageCounterEl.textContent = `1 / ${pdf.numPages}`;
  controlsHost.appendChild(pageCounterEl);
  _reader.pageCounter = pageCounterEl;
  _reader.zoomPct = zoomPct;

  // ── Search bar (Cmd+F) ──
  const searchBar = document.createElement("div");
  searchBar.className = "paper-reader-search-bar hidden";
  wrapper.insertBefore(searchBar, pagesHost);

  const searchInput = document.createElement("input");
  searchInput.type = "text";
  searchInput.className = "paper-reader-search-input";
  searchInput.placeholder = "Find in paper…";
  searchBar.appendChild(searchInput);

  const searchCount = document.createElement("span");
  searchCount.className = "paper-reader-search-count";
  searchBar.appendChild(searchCount);

  const prevBtn = document.createElement("button");
  prevBtn.type = "button";
  prevBtn.className = "paper-reader-zoom-btn";
  prevBtn.textContent = "\u2191";
  prevBtn.title = "Previous match";
  prevBtn.addEventListener("click", () => _navigateMatch(-1));
  searchBar.appendChild(prevBtn);

  const nextBtn = document.createElement("button");
  nextBtn.type = "button";
  nextBtn.className = "paper-reader-zoom-btn";
  nextBtn.textContent = "\u2193";
  nextBtn.title = "Next match";
  nextBtn.addEventListener("click", () => _navigateMatch(1));
  searchBar.appendChild(nextBtn);

  const closeSearch = document.createElement("button");
  closeSearch.type = "button";
  closeSearch.className = "paper-reader-zoom-btn";
  closeSearch.textContent = "\u00D7";
  closeSearch.title = "Close search";
  closeSearch.addEventListener("click", _closeSearch);
  searchBar.appendChild(closeSearch);

  _reader.searchBar = searchBar;
  _reader.searchInput = searchInput;
  _reader.searchCount = searchCount;
  _reader.searchMatches = [];
  _reader.searchIdx = -1;
  _reader.searchHighlights = [];
  _reader.textContents = [];  // cached per page

  searchInput.addEventListener("input", _debounce(_runSearch, 200));
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      _navigateMatch(e.shiftKey ? -1 : 1);
    } else if (e.key === "Escape") {
      _closeSearch();
    }
  });

  // Intercept Cmd+F to open our search instead of the browser's.
  _reader._keyHandler = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "f") {
      e.preventDefault();
      _openSearch();
    }
  };
  document.addEventListener("keydown", _reader._keyHandler);

  // ── Render at current zoom ──
  await _renderAllPages();
  _startSelectionListener();
  _startZoomGestures();

  return _disposer;
}

// ---- Zoom helpers --------------------------------------------------------

const _ZOOM_STEPS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0];

function _stepZoom(dir) {
  if (!_reader) return;
  const cur = _reader.zoomLevel;
  // Find the nearest step, then move one position in the requested direction.
  let idx = 0;
  let best = Infinity;
  for (let i = 0; i < _ZOOM_STEPS.length; i++) {
    const d = Math.abs(_ZOOM_STEPS[i] - cur);
    if (d < best) { best = d; idx = i; }
  }
  const next = idx + dir;
  if (next < 0 || next >= _ZOOM_STEPS.length) return;
  _setZoom(_ZOOM_STEPS[next]);
}

async function _setZoom(level) {
  if (!_reader || !_reader.pdfDoc) return;
  level = Math.max(_ZOOM_STEPS[0], Math.min(_ZOOM_STEPS[_ZOOM_STEPS.length - 1], level));
  if (Math.abs(level - _reader.zoomLevel) < 0.001) return;
  _reader.zoomLevel = level;
  if (_reader.zoomPct) {
    _reader.zoomPct.textContent = `${Math.round(level * 100)}%`;
  }
  await _renderAllPages();
}

async function _markRead(btn) {
  if (!_reader) return;
  btn.disabled = true;
  btn.textContent = "Marking…";
  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(_reader.paperKey)}/mark-read`,
      { method: "POST" },
    );
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) {
      throw new Error(data.reason || `HTTP ${resp.status}`);
    }
    btn.textContent = "Read \u2713";
    btn.classList.add("done");
    if (typeof showToast === "function") {
      const eng = data.engagement ? ` (${data.engagement}% engagement)` : "";
      showToast(`Paper marked as read${eng}`);
    }
    // Refresh the sidebar so the paper moves to the Read filter.
    if (typeof fetchPapersData === "function") fetchPapersData();
  } catch (err) {
    console.error("[paper-reader] mark-read failed:", err);
    btn.disabled = false;
    btn.textContent = "Mark read";
    if (typeof showToast === "function") {
      showToast(`Couldn't mark as read: ${err.message || err}`);
    }
  }
}

function _startZoomGestures() {
  if (!_reader || !_reader.pagesHost) return;
  let zoomDebounce = null;
  _reader.pagesHost.addEventListener("wheel", (e) => {
    // Only intercept zoom gestures (pinch-to-zoom on trackpad sends
    // wheel events with ctrlKey; Cmd+scroll sends metaKey on Mac).
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    if (zoomDebounce) clearTimeout(zoomDebounce);
    const dir = e.deltaY < 0 ? 1 : -1;
    zoomDebounce = setTimeout(() => _stepZoom(dir), 80);
  }, { passive: false });
}

// ---- In-paper search (Cmd+F) ---------------------------------------------

function _openSearch() {
  if (!_reader || !_reader.searchBar) return;
  _reader.searchBar.classList.remove("hidden");
  _reader.searchInput.focus();
  _reader.searchInput.select();
}

function _closeSearch() {
  if (!_reader) return;
  _reader.searchBar?.classList.add("hidden");
  _clearSearchHighlights();
  if (_reader.searchInput) _reader.searchInput.value = "";
  if (_reader.searchCount) _reader.searchCount.textContent = "";
  _reader.searchMatches = [];
  _reader.searchIdx = -1;
}

async function _runSearch() {
  if (!_reader || !_reader.pdfDoc) return;
  _clearSearchHighlights();
  _reader.searchMatches = [];
  _reader.searchIdx = -1;

  const query = (_reader.searchInput?.value || "").trim().toLowerCase();
  if (!query) {
    if (_reader.searchCount) _reader.searchCount.textContent = "";
    return;
  }

  const pdf = _reader.pdfDoc;

  // Build / reuse cached text content per page.
  if (!_reader.textContents.length) {
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const tc = await page.getTextContent();
      _reader.textContents.push(tc);
    }
  }

  // Search each page's text items for substring matches.
  for (let pi = 0; pi < _reader.textContents.length; pi++) {
    const tc = _reader.textContents[pi];
    for (const item of tc.items) {
      if (!item.str) continue;
      const text = item.str.toLowerCase();
      let pos = 0;
      while ((pos = text.indexOf(query, pos)) !== -1) {
        _reader.searchMatches.push({ pageIndex: pi, item, pos, len: query.length });
        pos += query.length;
      }
    }
  }

  if (_reader.searchCount) {
    _reader.searchCount.textContent = _reader.searchMatches.length
      ? `${_reader.searchMatches.length} match${_reader.searchMatches.length > 1 ? "es" : ""}`
      : "No matches";
  }

  // Highlight all matches.
  for (const match of _reader.searchMatches) {
    _highlightSearchMatch(match, false);
  }

  // Auto-jump to the first match.
  if (_reader.searchMatches.length) {
    _navigateMatch(1);
  }
}

function _navigateMatch(dir) {
  if (!_reader || !_reader.searchMatches.length) return;
  // Remove "active" from previous
  if (_reader.searchIdx >= 0 && _reader.searchHighlights[_reader.searchIdx]) {
    for (const el of _reader.searchHighlights[_reader.searchIdx]) {
      el.classList.remove("active");
    }
  }
  _reader.searchIdx += dir;
  if (_reader.searchIdx >= _reader.searchMatches.length) _reader.searchIdx = 0;
  if (_reader.searchIdx < 0) _reader.searchIdx = _reader.searchMatches.length - 1;

  const els = _reader.searchHighlights[_reader.searchIdx];
  if (els && els.length) {
    for (const el of els) el.classList.add("active");
    els[0].scrollIntoView({ block: "center", behavior: "smooth" });
  }

  if (_reader.searchCount) {
    _reader.searchCount.textContent =
      `${_reader.searchIdx + 1} / ${_reader.searchMatches.length}`;
  }
}

function _highlightSearchMatch(match, isActive) {
  if (!_reader) return;
  const pageEl = _reader.pageEls[match.pageIndex];
  const viewport = _reader.pageViewports[match.pageIndex];
  if (!pageEl || !viewport) return;
  const overlay = pageEl.querySelector(".paper-reader-overlay");
  if (!overlay) return;

  // PDF.js text items carry a transform [scaleX, skewX, skewY, scaleY, tx, ty]
  // in PDF coordinate space. We convert the item's bounding box to viewport
  // coordinates to place the highlight.
  const item = match.item;
  const tx = item.transform;
  if (!tx || tx.length < 6) return;

  const fontSize = Math.sqrt(tx[0] * tx[0] + tx[1] * tx[1]);
  const itemWidth = item.width || 0;
  const charWidth = item.str.length > 0 ? itemWidth / item.str.length : fontSize * 0.6;
  const x0 = tx[4] + match.pos * charWidth;
  const x1 = x0 + match.len * charWidth;
  const y0 = tx[5];
  const y1 = y0 + fontSize;

  const [vx1, vy1, vx2, vy2] = viewport.convertToViewportRectangle([x0, y0, x1, y1]);
  const left = Math.min(vx1, vx2);
  const top = Math.min(vy1, vy2);
  const width = Math.abs(vx2 - vx1);
  const height = Math.abs(vy2 - vy1);

  if (width < 1 || height < 1) return;
  const el = document.createElement("div");
  el.className = "paper-reader-search-hl" + (isActive ? " active" : "");
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
  el.style.width = `${width}px`;
  el.style.height = `${height}px`;
  overlay.appendChild(el);

  // Track highlight elements per match index for navigation.
  const idx = _reader.searchMatches.indexOf(match);
  if (!_reader.searchHighlights[idx]) _reader.searchHighlights[idx] = [];
  _reader.searchHighlights[idx].push(el);
}

function _clearSearchHighlights() {
  if (!_reader) return;
  for (const els of _reader.searchHighlights) {
    if (!els) continue;
    for (const el of els) el.remove();
  }
  _reader.searchHighlights = [];
}

function _debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// ---- Smart link detection ------------------------------------------------
// Regex-scans rendered text items for arxiv IDs, DOIs, and URLs.
// Creates clickable overlays in the highlight layer.

const _SMART_LINK_PATTERNS = [
  // arxiv IDs: 2301.12345, arXiv:2301.12345v2
  { re: /(?:arXiv:?\s*)?((?:2[0-9]{3}|1[0-9]{3})\.\d{4,5}(?:v\d+)?)/gi,
    url: (m) => `https://arxiv.org/abs/${m[1]}`,
    label: (m) => `arxiv ${m[1]}` },
  // DOIs: 10.xxxx/...
  { re: /\b(10\.\d{4,9}\/[^\s,;)\]]+)/g,
    url: (m) => `https://doi.org/${m[1]}`,
    label: (m) => `doi:${m[1]}` },
  // GitHub URLs: github.com/owner/repo
  { re: /\b((?:https?:\/\/)?github\.com\/[a-zA-Z0-9_-]+\/[a-zA-Z0-9_.-]+)\b/gi,
    url: (m) => m[1].startsWith("http") ? m[1] : `https://${m[1]}`,
    label: (m) => m[1].replace(/^https?:\/\//, "") },
  // Plain https URLs (catch-all, but skip arxiv/doi we already caught)
  { re: /\b(https?:\/\/[^\s,;)\]]{8,})/gi,
    url: (m) => m[1],
    label: (m) => m[1].replace(/^https?:\/\//, "").slice(0, 40) },
];

function _renderSmartLinks(textContent, viewport, overlay) {
  if (!textContent || !textContent.items) return;
  const seen = new Set(); // deduplicate same URL on the same page

  for (const item of textContent.items) {
    if (!item.str || !item.transform || item.transform.length < 6) continue;
    const str = item.str;

    for (const pattern of _SMART_LINK_PATTERNS) {
      pattern.re.lastIndex = 0;
      let m;
      while ((m = pattern.re.exec(str)) !== null) {
        const url = pattern.url(m);
        if (seen.has(url)) continue;
        seen.add(url);

        const tx = item.transform;
        const fontSize = Math.sqrt(tx[0] * tx[0] + tx[1] * tx[1]);
        const itemWidth = item.width || 0;
        const charW = str.length > 0 ? itemWidth / str.length : fontSize * 0.6;
        const x0 = tx[4] + m.index * charW;
        const x1 = x0 + m[0].length * charW;
        const y0 = tx[5];
        const y1 = y0 + fontSize;

        const [vx1, vy1, vx2, vy2] = viewport.convertToViewportRectangle([x0, y0, x1, y1]);
        const left = Math.min(vx1, vx2);
        const top = Math.min(vy1, vy2);
        const width = Math.abs(vx2 - vx1);
        const height = Math.abs(vy2 - vy1);
        if (width < 1 || height < 1) continue;

        const link = document.createElement("a");
        link.className = "paper-reader-link smart";
        link.href = "#";
        link.title = pattern.label(m);
        link.style.left = `${left}px`;
        link.style.top = `${top}px`;
        link.style.width = `${width}px`;
        link.style.height = `${height}px`;
        link.addEventListener("click", (e) => {
          e.preventDefault();
          if (window.nicolas?.openExternal) {
            window.nicolas.openExternal(url);
          } else {
            window.open(url, "_blank");
          }
        });
        overlay.appendChild(link);
      }
    }
  }
}

// ---- Page rendering (extracted for zoom re-render) -----------------------

async function _renderAllPages() {
  if (!_reader || !_reader.pdfDoc) return;
  const pdf = _reader.pdfDoc;
  const pagesHost = _reader.pagesHost;

  // Save current visible page so we can scroll back to it after re-render.
  const restorePage = _reader.visiblePage || _reader.savedPage || 1;

  // Tear down observers on the old DOM nodes and clear search overlays.
  if (_reader.observer) { _reader.observer.disconnect(); _reader.observer = null; }
  _clearSearchHighlights();
  _reader.textContents = [];  // force re-cache after zoom
  _reader.searchMatches = [];
  _reader.searchIdx = -1;
  _reader.pageEls = [];
  _reader.pageViewports = [];
  pagesHost.innerHTML = "";

  const devicePixelRatio = window.devicePixelRatio || 1;
  const zoom = _reader.zoomLevel;
  const base = _reader.baseScale;

  for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
    const page = await pdf.getPage(pageNum);
    const effectiveScale = base * zoom;
    const cssViewport = page.getViewport({ scale: effectiveScale });
    const renderViewport = page.getViewport({ scale: effectiveScale * devicePixelRatio });

    const pageEl = document.createElement("div");
    pageEl.className = "paper-reader-page";
    pageEl.dataset.pageNum = String(pageNum);
    pageEl.style.width = `${cssViewport.width}px`;
    pageEl.style.height = `${cssViewport.height}px`;
    pagesHost.appendChild(pageEl);

    _reader.pageViewports.push(cssViewport);

    const canvas = document.createElement("canvas");
    canvas.width = renderViewport.width;
    canvas.height = renderViewport.height;
    canvas.style.width = `${cssViewport.width}px`;
    canvas.style.height = `${cssViewport.height}px`;
    pageEl.appendChild(canvas);

    // Text layer for selection.
    const textLayer = document.createElement("div");
    textLayer.className = "paper-reader-textlayer";
    pageEl.appendChild(textLayer);

    const overlay = document.createElement("div");
    overlay.className = "paper-reader-overlay";
    pageEl.appendChild(overlay);

    const ctx = canvas.getContext("2d");
    await page.render({ canvasContext: ctx, viewport: renderViewport }).promise;

    try {
      const pdfjs = await _loadPdfJs();
      const textContent = await page.getTextContent();

      // PDF.js v4 TextLayer: pixel-perfect span positioning using the
      // PDF's actual font metrics. Previous attempts failed because:
      // (a) the --scale-factor CSS property wasn't set on the container,
      // (b) z-index was missing so spans rendered behind the canvas.
      textLayer.style.setProperty("--scale-factor", effectiveScale);
      if (pdfjs.TextLayer) {
        const tl = new pdfjs.TextLayer({
          container: textLayer,
          textContentSource: textContent,
          viewport: cssViewport,
        });
        await tl.render();
      }

      // Smart link detection: scan text items for arxiv IDs, DOIs, and URLs.
      _renderSmartLinks(textContent, cssViewport, overlay);
    } catch (err) {
      console.warn("[paper-reader] text layer failed on page", pageNum, err);
    }

    // PDF annotation layer — renders embedded Link annotations from the
    // PDF itself (URLs, DOIs, internal page refs). These are separate from
    // Zotero highlights. Non-fatal if it fails (some PDFs have no annotations).
    try {
      const pdfjs = await _loadPdfJs();
      const annotations = await page.getAnnotations();
      const linkAnnotations = annotations.filter(
        (a) => a.subtype === "Link" && a.url,
      );
      for (const ann of linkAnnotations) {
        if (!ann.rect || ann.rect.length < 4) continue;
        const [vx1, vy1, vx2, vy2] = cssViewport.convertToViewportRectangle(ann.rect);
        const left = Math.min(vx1, vx2);
        const top = Math.min(vy1, vy2);
        const width = Math.abs(vx2 - vx1);
        const height = Math.abs(vy2 - vy1);
        if (width < 1 || height < 1) continue;
        const link = document.createElement("a");
        link.className = "paper-reader-link";
        link.href = "#";
        link.title = ann.url;
        link.style.left = `${left}px`;
        link.style.top = `${top}px`;
        link.style.width = `${width}px`;
        link.style.height = `${height}px`;
        link.addEventListener("click", (e) => {
          e.preventDefault();
          if (window.nicolas?.openExternal) {
            window.nicolas.openExternal(ann.url);
          } else {
            window.open(ann.url, "_blank");
          }
        });
        overlay.appendChild(link);
      }
    } catch (err) {
      console.debug("[paper-reader] annotation layer skipped on page", pageNum, err);
    }

    // Overlay highlights.
    const anns = _reader.annotationsByPage.get(pageNum - 1) || [];
    for (const ann of anns) {
      _renderHighlightRects(ann, cssViewport, overlay);
    }

    _reader.pageEls.push(pageEl);
  }

  // Rebuild the visibility observer on the fresh page elements.
  _startVisibilityObserver();

  // Scroll back to the page we were reading before the zoom change.
  const targetEl = _reader.pageEls[restorePage - 1];
  if (targetEl) {
    requestAnimationFrame(() => {
      targetEl.scrollIntoView({ block: "start", behavior: "auto" });
    });
  }
}

// ---- Text selection → "Save highlight" → Zotero write-back --------------

function _startSelectionListener() {
  if (!_reader) return;
  const handler = () => _handleSelectionChange();
  // Listen on both mouseup (covers the common case, fires after the range
  // stabilises) and selectionchange (catches keyboard selection too).
  document.addEventListener("mouseup", handler);
  document.addEventListener("selectionchange", handler);
  _reader.selectionHandler = handler;
}

/**
 * Merge per-span PDF rects into one rect per visual line.
 *
 * getClientRects() returns one rect per inline box. PDF.js TextLayer
 * creates one <span> per text item, so a 3-line selection may produce
 * 30+ tiny rects. This function groups rects whose Y-ranges overlap
 * (same visual line) and unions them into a single bounding rect per
 * line — matching the clean one-rect-per-line format that the
 * reMarkable / Zotero pipeline produces.
 */
function _coalesceLineRects(rects) {
  if (rects.length <= 1) return rects.map((r) => r.map((v) => Math.round(v * 1000) / 1000));

  // Sort by y0 ascending (bottom of rect in PDF bottom-left space,
  // so higher y0 = higher on the page).
  const sorted = rects.slice().sort((a, b) => a[1] - b[1]);

  const lines = [];  // each entry: [x0, y0, x1, y1] merged line rect
  let cur = sorted[0].slice();  // copy

  for (let i = 1; i < sorted.length; i++) {
    const r = sorted[i];
    const curH = cur[3] - cur[1];
    const rH = r[3] - r[1];
    const overlapY = Math.min(cur[3], r[3]) - Math.max(cur[1], r[1]);
    const threshold = Math.min(curH, rH) * 0.3;

    if (overlapY > threshold) {
      // Same visual line — extend the bounding box.
      cur[0] = Math.min(cur[0], r[0]);
      cur[1] = Math.min(cur[1], r[1]);
      cur[2] = Math.max(cur[2], r[2]);
      cur[3] = Math.max(cur[3], r[3]);
    } else {
      // New line — flush current.
      lines.push(cur);
      cur = r.slice();
    }
  }
  lines.push(cur);

  return lines.map((r) => r.map((v) => Math.round(v * 1000) / 1000));
}

function _handleSelectionChange() {
  if (!_reader) return;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
    _hideSaveButton();
    return;
  }

  // Only react when the selection is inside our pages host.
  const range = sel.getRangeAt(0);
  const anchorNode = range.commonAncestorContainer;
  const anchorEl = anchorNode.nodeType === 1 ? anchorNode : anchorNode.parentElement;
  if (!anchorEl || !_reader.pagesHost.contains(anchorEl)) {
    _hideSaveButton();
    return;
  }

  const text = sel.toString().trim();
  if (!text) {
    _hideSaveButton();
    return;
  }

  // Walk the selection's client rects, convert to PDF coordinates, and
  // coalesce into one rect per visual line. getClientRects() returns one
  // rect per inline box — in PDF.js's TextLayer every word/character is
  // its own <span>, so a paragraph selection produces dozens of tiny
  // rects. The reMarkable pipeline receives pre-coalesced line rects
  // from Zotero; we need to match that to get clean highlights.
  const rects = range.getClientRects();
  if (!rects.length) {
    _hideSaveButton();
    return;
  }

  let pageIndex = -1;
  let pageRect = null;
  let pageEl = null;
  let cssViewport = null;
  const rawPdfRects = [];

  for (const rect of rects) {
    if (rect.width < 1 || rect.height < 1) continue;
    for (let i = 0; i < _reader.pageEls.length; i++) {
      const el = _reader.pageEls[i];
      const box = el.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      if (cx >= box.left && cx <= box.right && cy >= box.top && cy <= box.bottom) {
        if (pageIndex < 0) {
          pageIndex = i;
          pageEl = el;
          pageRect = box;
          cssViewport = _reader.pageViewports[i];
        } else if (i !== pageIndex) {
          continue;
        }
        const localLeft = rect.left - pageRect.left;
        const localTop = rect.top - pageRect.top;
        const localRight = rect.right - pageRect.left;
        const localBottom = rect.bottom - pageRect.top;
        const pdfTL = cssViewport.convertToPdfPoint(localLeft, localTop);
        const pdfBR = cssViewport.convertToPdfPoint(localRight, localBottom);
        if (![...pdfTL, ...pdfBR].every(Number.isFinite)) continue;
        const x0 = Math.min(pdfTL[0], pdfBR[0]);
        const x1 = Math.max(pdfTL[0], pdfBR[0]);
        const y0 = Math.min(pdfTL[1], pdfBR[1]);
        const y1 = Math.max(pdfTL[1], pdfBR[1]);
        rawPdfRects.push([x0, y0, x1, y1]);
        break;
      }
    }
  }

  // Coalesce per-span rects into one rect per visual line. Two rects
  // are on the same line if their Y-ranges overlap by more than half
  // the smaller rect's height. This matches the reMarkable pipeline's
  // clean one-rect-per-line output.
  const pdfRects = _coalesceLineRects(rawPdfRects);

  if (pageIndex < 0 || pdfRects.length === 0) {
    _hideSaveButton();
    return;
  }

  const sortIndex = `${String(pageIndex).padStart(5, "0")}|000000|00000`;
  _reader.pendingHighlight = {
    text,
    page_index: pageIndex,
    page_label: String(pageIndex + 1),
    rects: pdfRects,
    color: "#ffd400",
    sort_index: sortIndex,
  };

  _showSaveButton(rects[rects.length - 1]);
}

function _showSaveButton(anchorRect) {
  if (!_reader) return;
  // Floating menu with two actions: Highlight (persist) and Copy
  // (clipboard). Both buttons share the same container so they live
  // as a single DOM node — easier to hide, position, and style.
  let menu = _reader.saveButton;
  if (!menu) {
    menu = document.createElement("div");
    menu.className = "paper-reader-select-menu";

    const highlightBtn = document.createElement("button");
    highlightBtn.type = "button";
    highlightBtn.className = "paper-reader-menu-btn highlight";
    highlightBtn.textContent = "Highlight";
    highlightBtn.addEventListener("mousedown", (e) => e.preventDefault());
    highlightBtn.addEventListener("click", _saveHighlight);

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "paper-reader-menu-btn copy";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("mousedown", (e) => e.preventDefault());
    copyBtn.addEventListener("click", _copySelection);

    menu.appendChild(highlightBtn);
    menu.appendChild(copyBtn);
    document.body.appendChild(menu);
    _reader.saveButton = menu;
    _reader.menuHighlightBtn = highlightBtn;
    _reader.menuCopyBtn = copyBtn;
  }
  // Position just below the end of the selection.
  // position: fixed → use viewport-relative coords directly.
  const top = anchorRect.bottom + 6;
  const left = anchorRect.left;
  menu.style.top = `${top}px`;
  menu.style.left = `${left}px`;
  menu.style.display = "flex";
  if (_reader.menuHighlightBtn) {
    _reader.menuHighlightBtn.disabled = false;
    _reader.menuHighlightBtn.textContent = "Highlight";
  }
  if (_reader.menuCopyBtn) {
    _reader.menuCopyBtn.disabled = false;
    _reader.menuCopyBtn.textContent = "Copy";
  }
}

async function _copySelection() {
  if (!_reader || !_reader.pendingHighlight) return;
  const text = _reader.pendingHighlight.text || "";
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    if (typeof showToast === "function") {
      const preview = text.slice(0, 60) + (text.length > 60 ? "…" : "");
      showToast(`Copied "${preview}"`, "success");
    }
  } catch (err) {
    console.error("[paper-reader] copy failed:", err);
    if (typeof showToast === "function") {
      showToast(`Couldn't copy: ${err.message || err}`);
    }
  } finally {
    _hideSaveButton();
    if (_reader) _reader.pendingHighlight = null;
    const sel = window.getSelection();
    if (sel) sel.removeAllRanges();
  }
}

function _hideSaveButton() {
  if (_reader && _reader.saveButton) {
    _reader.saveButton.style.display = "none";
  }
  // NOTE: pendingHighlight is NOT cleared here. The save/copy handlers
  // own the lifecycle — they capture it synchronously on click, then
  // clear it in their finally blocks. Clearing here caused a race
  // condition: selectionchange fires between mouseup and click,
  // triggering _hideSaveButton and nulling pendingHighlight before
  // the save handler could read it.
}

async function _saveHighlight() {
  if (!_reader || !_reader.pendingHighlight) return;
  const highlight = _reader.pendingHighlight;
  // Disable the highlight button (not the whole menu div) while saving.
  if (_reader.menuHighlightBtn) {
    _reader.menuHighlightBtn.disabled = true;
    _reader.menuHighlightBtn.textContent = "Saving…";
  }

  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(_reader.paperKey)}/annotations`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ highlights: [highlight] }),
      },
    );
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) {
      const msg = data.hint || data.detail || data.reason || `HTTP ${resp.status}`;
      throw new Error(msg);
    }
    _renderOptimisticHighlight(highlight);
    if (typeof showToast === "function") {
      // Pick toast color based on Zotero outcome:
      //   synced → green "Highlight saved + synced to Zotero"
      //   failed → yellow "Highlight saved (Zotero sync failed)"
      //   not_configured / not_attempted → silent green "Highlight saved"
      const status = data.zotero_status || (data.synced_to_zotero ? "synced" : "not_configured");
      if (status === "synced") {
        showToast("Highlight saved + synced to Zotero", "success");
      } else if (status === "failed") {
        showToast("Highlight saved (Zotero sync failed)", "warning");
      } else {
        showToast("Highlight saved", "success");
      }
    }
  } catch (err) {
    console.error("[paper-reader] save highlight failed:", err);
    _renderOptimisticHighlight(highlight);
    if (typeof showToast === "function") {
      showToast(`Couldn't save highlight: ${err.message || err}`);
    }
  } finally {
    _hideSaveButton();
    if (_reader) _reader.pendingHighlight = null;
    const sel = window.getSelection();
    if (sel) sel.removeAllRanges();
  }
}

function _renderOptimisticHighlight(highlight) {
  if (!_reader) return;
  const pageEl = _reader.pageEls[highlight.page_index];
  if (!pageEl) return;
  const overlay = pageEl.querySelector(".paper-reader-overlay");
  const cssViewport = _reader.pageViewports[highlight.page_index];
  if (!overlay || !cssViewport) return;
  // Track this highlight so it's dedup'd against future re-renders.
  const idx = highlight.page_index;
  if (!_reader.annotationsByPage.has(idx)) {
    _reader.annotationsByPage.set(idx, []);
  }
  const existingForPage = _reader.annotationsByPage.get(idx);
  const normText = (highlight.text || "").replace(/\s+/g, " ").trim();
  const dup = existingForPage.some(
    (a) => (a.text || "").replace(/\s+/g, " ").trim() === normText,
  );
  if (dup) return;  // already rendered — skip to avoid stacked translucent divs
  existingForPage.push(highlight);
  _renderHighlightRects(highlight, cssViewport, overlay);
}

/** Render a single annotation's rects into the overlay, wiring click-to-delete.
 *  Called both for initial load (from annotationsByPage) and optimistic saves. */
function _renderHighlightRects(ann, cssViewport, overlay) {
  const rects = ann.rects || [];
  if (!rects.length) return;

  // Group all rects for the annotation so clicking any one removes them all.
  const group = [];
  for (const rect of rects) {
    if (!Array.isArray(rect) || rect.length < 4) continue;
    const [vx1, vy1, vx2, vy2] = cssViewport.convertToViewportRectangle(rect);
    const left = Math.min(vx1, vx2);
    const top = Math.min(vy1, vy2);
    const width = Math.abs(vx2 - vx1);
    const height = Math.abs(vy2 - vy1);
    if (width < 1 || height < 1) continue;
    const hl = document.createElement("div");
    hl.className = "paper-reader-highlight";
    hl.style.left = `${left}px`;
    hl.style.top = `${top}px`;
    hl.style.width = `${width}px`;
    hl.style.height = `${height}px`;
    // Only set inline color for non-default highlights; CSS handles the
    // default yellow (including dark-mode overrides for blend-mode + alpha).
    if (ann.color && ann.color !== "#ffd400") {
      hl.style.background = _withAlpha(ann.color, 0.38);
    }
    if (ann.text) hl.title = `${ann.text}\n\nClick to remove`;
    hl.addEventListener("click", (e) => {
      e.stopPropagation();
      _deleteHighlight(ann, group);
    });
    overlay.appendChild(hl);
    group.push(hl);
  }
}

async function _deleteHighlight(ann, elements) {
  if (!_reader) return;
  const text = ann.text || "";

  // Optimistic removal — take the elements off-screen immediately.
  for (const el of elements) el.remove();
  // Drop from in-memory cache so re-renders don't bring it back.
  const pageEntries = _reader.annotationsByPage.get(ann.page_index) || [];
  const idx = pageEntries.indexOf(ann);
  if (idx >= 0) pageEntries.splice(idx, 1);

  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(_reader.paperKey)}/annotations`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: ann.id || "",
          text,
          page_index: ann.page_index,
        }),
      },
    );
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) throw new Error(data.reason || `HTTP ${resp.status}`);
    if (typeof showToast === "function") {
      const preview = text ? `"${text.slice(0, 60)}${text.length > 60 ? "…" : ""}"` : "";
      showToast(`Highlight removed ${preview}`.trim(), "success");
    }
  } catch (err) {
    console.error("[paper-reader] delete highlight failed:", err);
    if (typeof showToast === "function") {
      showToast(`Couldn't remove highlight: ${err.message || err}`);
    }
  }
}

// ---- Visibility tracking → last-read persistence ------------------------

function _startVisibilityObserver() {
  if (!_reader || !_reader.pagesHost) return;
  // Mark a page as "current" once at least 35% of it is visible in the
  // scroll viewport. Threshold is low enough that scrolling fast still
  // captures intermediate pages for the debounced save.
  const observer = new IntersectionObserver(
    (entries) => {
      if (!_reader) return;
      let best = null;
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        if (!best || entry.intersectionRatio > best.intersectionRatio) {
          best = entry;
        }
      }
      if (best) {
        const pageNum = parseInt(best.target.dataset.pageNum, 10) || 1;
        if (pageNum !== _reader.visiblePage) {
          _reader.visiblePage = pageNum;
          if (_reader.pageCounter && _reader.pdfDoc) {
            _reader.pageCounter.textContent = `${pageNum} / ${_reader.pdfDoc.numPages}`;
          }
          document.dispatchEvent(new CustomEvent("paper-reader:page-change", {
            detail: { paperKey: _reader.paperKey, page: pageNum, total: _reader.pdfDoc?.numPages || 0 },
          }));
          _schedulePositionSave();
        }
      }
    },
    {
      root: _reader.pagesHost,
      threshold: [0.35, 0.75],
    },
  );
  for (const pageEl of _reader.pageEls) observer.observe(pageEl);
  _reader.observer = observer;
}

function _schedulePositionSave() {
  if (!_reader) return;
  if (_reader.saveTimer) clearTimeout(_reader.saveTimer);
  _reader.saveTimer = setTimeout(_savePositionNow, 900);
}

async function _savePositionNow() {
  if (!_reader) return;
  const page = _reader.visiblePage;
  if (!page || page === _reader.savedPage) return;
  try {
    await fetch(
      `http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(_reader.paperKey)}/read-position`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ page }),
      },
    );
    _reader.savedPage = page;
  } catch (err) {
    // Non-fatal: position will simply not persist across reopens.
    console.warn("[paper-reader] position save failed:", err);
  }
}

function _restoreScrollPosition() {
  if (!_reader) return;
  const target = _reader.savedPage;
  if (!target || target <= 1) return;
  const pageEl = _reader.pageEls[target - 1];
  if (!pageEl || !_reader.pagesHost) return;
  // Wait a tick so the page canvas has layout, then scroll.
  requestAnimationFrame(() => {
    pageEl.scrollIntoView({ block: "start", behavior: "auto" });
  });
}

// ---- Utilities ----------------------------------------------------------

function _escape(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/**
 * Map a server error reason code to user-facing copy. Never leak raw
 * exception strings — they contain Zotero API URLs and scare users.
 */
function _friendlyPdfError(reason) {
  switch (reason) {
    case "no_local_pdf_and_zotero_unconfigured":
      return {
        headline: "This PDF isn't cached locally yet.",
        hint: "Connect Zotero (or sync the paper into your Obsidian Inbox) to open it here.",
      };
    case "no_pdf_available":
      return {
        headline: "No PDF is available for this paper.",
        hint: "Zotero didn't return a file — check the attachment in your library.",
      };
    case "fetch_failed":
      return {
        headline: "Couldn't download the PDF.",
        hint: "Zotero returned an error. Check your connection and try again.",
      };
    case "not_found":
      return { headline: "This paper is no longer in your library.", hint: "" };
    default:
      return {
        headline: "Couldn't load this PDF.",
        hint: "Try re-syncing your library.",
      };
  }
}

/**
 * Convert a hex color ("#ffd400") to rgba with the given alpha. Returns the
 * original string if it is not a recognised hex — the browser will fall
 * back to rendering it as-is.
 */
function _withAlpha(color, alpha) {
  if (typeof color !== "string") return color;
  const m = /^#([0-9a-f]{6})$/i.exec(color.trim());
  if (!m) return color;
  const n = parseInt(m[1], 16);
  const r = (n >> 16) & 0xff;
  const g = (n >> 8) & 0xff;
  const b = n & 0xff;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Tear down the active reader (observer, timers) without touching the
 *  DOM host. Safe to call when no reader is open. */
function closePaperReader() {
  _clearReader();
}

// Expose on window so papers.js can call them. We avoid ES modules here
// because the renderer loads a flat set of <script> tags, not modules.
window.openPaperReader = openPaperReader;
window.closePaperReader = closePaperReader;
