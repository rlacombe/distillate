/* ───── Papers — sidebar, detail, actions ───── */

const papersSidebarEl = document.getElementById("papers-sidebar");
const papersFiltersEl = document.getElementById("papers-sidebar-filters");
const papersCountEl = document.getElementById("papers-count");

const syncProgressBar = document.getElementById("sync-progress-bar");
const syncProgressFill = document.getElementById("sync-progress-fill");

function syncProgressStart() {
  if (!syncProgressBar) return;
  syncProgressFill.style.animation = "none";
  syncProgressFill.style.width = "0%";
  syncProgressBar.classList.remove("complete");
  // Force reflow so the animation restarts cleanly
  void syncProgressFill.offsetWidth;
  syncProgressBar.classList.add("active");
}

function syncProgressDone() {
  if (!syncProgressBar) return;
  syncProgressBar.classList.add("complete");
  setTimeout(() => syncProgressBar.classList.remove("active", "complete"), 600);
}

async function _runPapersSync(btn) {
  if (!serverPort || btn.classList.contains("syncing")) return;
  btn.classList.add("syncing");
  syncProgressStart();
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/papers/sync`, { method: "POST" });
    const data = await resp.json().catch(() => ({}));
    if (data.ok) {
      const msg = data.output?.includes("Nothing to do") ? "Already up to date" : "Library synced";
      if (typeof showToast === "function") showToast(msg, "success");
    } else {
      const msg = data.error || "Sync failed";
      if (typeof showToast === "function") showToast(msg, "error");
    }
    fetchPapersData();
    if (!currentPaperKey) fetchPapersHome();
  } catch (err) {
    console.error("[papers] sync failed:", err);
    if (typeof showToast === "function") showToast("Sync failed — check your Zotero credentials", "error");
  } finally {
    btn.classList.remove("syncing");
    syncProgressDone();
  }
}

// Click "Papers" sidebar title → deselect paper, return to papers home
const papersSidebarTitle = document.querySelector("#papers-view .sidebar-title");
if (papersSidebarTitle) {
  papersSidebarTitle.style.cursor = "pointer";
  papersSidebarTitle.addEventListener("click", () => {
    if (currentPaperKey) {
      currentPaperKey = null;
      papersSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => el.classList.remove("active"));
      showPapersHome();
    }
  });
}

// ── Drag-drop PDF → Zotero import ────────────────────────────────────────
// Dropping a PDF on the papers sidebar uploads it to Zotero and tracks it
// as a new paper. Runs in the capture phase so it beats the global
// paste-handlers drop listener in preload.js (which routes drops to the
// terminal).

let _papersDropOverlay = null;

function _showPapersDropOverlay(host) {
  if (_papersDropOverlay) return;
  const overlay = document.createElement("div");
  overlay.className = "papers-drop-overlay";
  overlay.innerHTML =
    '<div class="papers-drop-inner">' +
    '<div class="papers-drop-icon">⇣</div>' +
    '<div class="papers-drop-title">Drop PDF to import</div>' +
    '<div class="papers-drop-hint">Uploads to Zotero and tracks in your library.</div>' +
    "</div>";
  host.appendChild(overlay);
  _papersDropOverlay = overlay;
}

function _hidePapersDropOverlay() {
  if (_papersDropOverlay && _papersDropOverlay.parentNode) {
    _papersDropOverlay.parentNode.removeChild(_papersDropOverlay);
  }
  _papersDropOverlay = null;
}

function _eventHasFiles(e) {
  const dt = e.dataTransfer;
  if (!dt) return false;
  if (dt.types) {
    for (const t of dt.types) {
      if (t === "Files" || t === "application/x-moz-file") return true;
    }
  }
  return false;
}

async function _importDroppedPdf(file) {
  if (!serverPort) return;
  if (!file || !/\.pdf$/i.test(file.name || "")) {
    if (typeof showToast === "function") {
      showToast("Only PDF files can be dropped here.", "error");
    }
    return;
  }
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/papers/import`,
      { method: "POST", body: fd },
    );
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) {
      throw new Error(data.reason || `HTTP ${resp.status}`);
    }
    if (typeof showToast === "function") {
      showToast(`Imported "${data.title || file.name}"`, "success");
    }
    // Refresh the sidebar and select the new paper so the detail pane
    // opens to the newly imported item.
    fetchPapersData();
    if (data.paper_key && typeof selectPaper === "function") {
      setTimeout(() => selectPaper(data.paper_key), 250);
    }
  } catch (err) {
    console.error("[papers] import failed:", err);
    if (typeof showToast === "function") {
      showToast(`Import failed: ${err.message || err}`, "error");
    }
  }
}

function _initPapersDropZone() {
  // Scope drop handling to the entire papers rail region — the sidebar
  // itself is often empty on first load, and a wider target feels better.
  const target = document.getElementById("papers-view") || papersSidebarEl;
  if (!target) return;

  target.addEventListener("dragenter", (e) => {
    if (!_eventHasFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    _showPapersDropOverlay(target);
  }, true);

  target.addEventListener("dragover", (e) => {
    if (!_eventHasFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  }, true);

  target.addEventListener("dragleave", (e) => {
    if (!_eventHasFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    // Only hide if leaving the entire target tree, not entering a child element
    // (e.relatedTarget is where the cursor is moving to)
    if (!target.contains(e.relatedTarget)) {
      _hidePapersDropOverlay();
    }
  }, true);

  target.addEventListener("drop", async (e) => {
    if (!_eventHasFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    _hidePapersDropOverlay();
    const files = e.dataTransfer ? Array.from(e.dataTransfer.files || []) : [];
    if (!files.length) return;
    for (const f of files) {
      // eslint-disable-next-line no-await-in-loop
      await _importDroppedPdf(f);
    }
  }, true);
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _initPapersDropZone);
  } else {
    _initPapersDropZone();
  }
}

// ── IPC: PDF imported via Dock icon drag → navigate to it ────────────────
if (window.nicolas?.onPaperImported) {
  window.nicolas.onPaperImported((data) => {
    // Switch to Papers rail, refresh, then select the new paper.
    if (typeof switchSidebarView === "function") switchSidebarView("papers");
    fetchPapersData();
    if (data.paperKey && typeof selectPaper === "function") {
      setTimeout(() => selectPaper(data.paperKey), 400);
    }
    if (typeof showToast === "function") {
      showToast(`Imported "${data.title || "paper"}"`, "success");
    }
  });
}

let papersFirstLoad = true;

function fetchPapersData() {
  if (!serverPort) return;
  if (papersFirstLoad && papersSidebarEl) {
    papersSidebarEl.innerHTML = '<div class="sidebar-skeleton">' +
      '<div class="skeleton-item"></div>'.repeat(3) + '</div>';
  }
  fetch(`http://127.0.0.1:${serverPort}/papers`)
    .then((r) => r.json())
    .then((data) => _applyPapersData(data))
    .catch(() => { papersFirstLoad = false; });
}

function fetchInsightsData() {
  if (!serverPort || !papersInsightsPanel) return;
  papersInsightsPanel.innerHTML = '<div class="insights-empty">Loading insights...</div>';

  fetch(`http://127.0.0.1:${serverPort}/report`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok) return;
      if (data.empty) {
        papersInsightsPanel.innerHTML = '<div class="insights-empty">No processed papers yet. Read some papers first!</div>';
        return;
      }
      renderInsights(data);
    })
    .catch(() => {
      papersInsightsPanel.innerHTML = '<div class="insights-empty">Could not load insights.</div>';
    });
}

function renderInsights(data) {
  if (!papersInsightsPanel) return;
  papersInsightsPanel.innerHTML = "";

  const grid = document.createElement("div");
  grid.className = "insights-grid";

  // Lifetime stats (full width)
  if (data.lifetime) {
    const card = document.createElement("div");
    card.className = "insights-card full-width";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Lifetime";
    card.appendChild(title);

    const row = document.createElement("div");
    row.className = "insights-lifetime";
    const stats = [
      { value: data.lifetime.papers, label: "Papers" },
      { value: data.lifetime.pages.toLocaleString(), label: "Pages" },
      { value: data.lifetime.words.toLocaleString(), label: "Words" },
      { value: `${data.lifetime.avg_engagement}%`, label: "Avg Engagement" },
    ];
    for (const s of stats) {
      const stat = document.createElement("div");
      stat.className = "insights-lifetime-stat";
      stat.innerHTML = `<div class="insights-lifetime-value">${s.value}</div><div class="insights-lifetime-label">${s.label}</div>`;
      row.appendChild(stat);
    }
    card.appendChild(row);
    grid.appendChild(card);
  }

  // Reading velocity
  if (data.velocity && data.velocity.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Reading Velocity (8 weeks)";
    card.appendChild(title);

    const maxCount = Math.max(...data.velocity.map((v) => v.count));
    for (const week of [...data.velocity].reverse()) {
      const row = document.createElement("div");
      row.className = "insights-bar-row";
      const label = document.createElement("span");
      label.className = "insights-bar-label";
      const d = new Date(week.week);
      label.textContent = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      row.appendChild(label);

      const bar = document.createElement("div");
      bar.className = "insights-bar";
      const fill = document.createElement("div");
      fill.className = "insights-bar-fill";
      fill.style.width = `${(week.count / maxCount) * 100}%`;
      bar.appendChild(fill);
      row.appendChild(bar);

      const count = document.createElement("span");
      count.className = "insights-bar-count";
      count.textContent = week.count;
      row.appendChild(count);

      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  // Engagement distribution
  if (data.engagement && data.engagement.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Engagement Distribution";
    card.appendChild(title);

    const maxCount = Math.max(...data.engagement.map((e) => e.count));
    for (const bucket of data.engagement) {
      const row = document.createElement("div");
      row.className = "insights-bar-row";
      const label = document.createElement("span");
      label.className = "insights-bar-label";
      label.textContent = bucket.range;
      row.appendChild(label);

      const bar = document.createElement("div");
      bar.className = "insights-bar";
      const fill = document.createElement("div");
      fill.className = "insights-bar-fill";
      fill.style.width = maxCount > 0 ? `${(bucket.count / maxCount) * 100}%` : "0%";
      bar.appendChild(fill);
      row.appendChild(bar);

      const count = document.createElement("span");
      count.className = "insights-bar-count";
      count.textContent = bucket.count;
      row.appendChild(count);

      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  // Top topics
  if (data.topics && data.topics.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Top Topics";
    card.appendChild(title);

    for (const [i, topic] of data.topics.entries()) {
      const row = document.createElement("div");
      row.className = "insights-list-item";
      row.innerHTML = `<span class="insights-list-rank">${i + 1}.</span><span class="insights-list-name">${escapeHtml(topic.topic)}</span><span class="insights-list-count">${topic.count}</span>`;
      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  // Most-cited papers
  if (data.cited_papers && data.cited_papers.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Most-Cited Papers Read";
    card.appendChild(title);

    for (const [i, paper] of data.cited_papers.entries()) {
      const row = document.createElement("div");
      row.className = "insights-list-item";
      row.innerHTML = `<span class="insights-list-rank">${i + 1}.</span><span class="insights-list-name">${escapeHtml(paper.title)}</span><span class="insights-list-count">${paper.citations.toLocaleString()}</span>`;
      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  // Most-read authors
  if (data.top_authors && data.top_authors.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Most-Read Authors";
    card.appendChild(title);

    for (const [i, author] of data.top_authors.entries()) {
      const row = document.createElement("div");
      row.className = "insights-list-item";
      row.innerHTML = `<span class="insights-list-rank">${i + 1}.</span><span class="insights-list-name">${escapeHtml(author.name)}</span><span class="insights-list-count">${author.count}</span>`;
      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  papersInsightsPanel.appendChild(grid);
}

function renderPapersList(papers) {
  cachedPapers = papers;
  if (!papersSidebarEl) return;

  // Show library onboarding CTA when no papers and Zotero not configured
  if (!papers.length && !libraryConfigured) {
    if (papersCountEl) papersCountEl.textContent = "";
    if (papersFiltersEl) papersFiltersEl.innerHTML = "";
    papersSidebarEl.innerHTML = `
      <div class="sidebar-empty sidebar-empty-onboarding">
        <p>Your library is empty</p>
        <button class="onboarding-btn" id="library-setup-btn">Connect your library</button>
        <p class="sidebar-empty-hint">Sync your papers, highlights, and reading notes</p>
      </div>`;
    papersSidebarEl.querySelector("#library-setup-btn")
      ?.addEventListener("click", launchLibrarySetup);
    return;
  }

  const read = papers.filter((p) => p.status === "processed").length;
  const inQueue = papers.filter((p) => p.status !== "processed").length;
  const promoted = papers.filter((p) => p.promoted).length;

  // Update count badge
  if (papersCountEl) {
    papersCountEl.textContent = papers.length ? `${papers.length}` : "";
  }

  // Render filter dropdown + sort icon buttons
  if (papersFiltersEl) {
    papersFiltersEl.innerHTML = "";

    // Search row — updates list in-place without re-rendering controls
    const searchRow = document.createElement("div");
    searchRow.className = "papers-search-row";
    const searchIcon = document.createElement("span");
    searchIcon.className = "papers-search-icon";
    searchIcon.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`;
    const searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.className = "papers-search-input";
    searchInput.placeholder = "Search papers…";
    searchInput.value = currentPaperSearch;
    searchInput.addEventListener("input", () => {
      currentPaperSearch = searchInput.value;
      clearBtn.style.display = currentPaperSearch ? "flex" : "none";
      renderPaperSidebarItems(cachedPapers, currentPaperFilter, currentPaperSort);
    });
    const clearBtn = document.createElement("button");
    clearBtn.className = "papers-search-clear";
    clearBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
    clearBtn.style.display = currentPaperSearch ? "flex" : "none";
    clearBtn.addEventListener("click", () => {
      currentPaperSearch = "";
      searchInput.value = "";
      clearBtn.style.display = "none";
      renderPaperSidebarItems(cachedPapers, currentPaperFilter, currentPaperSort);
    });
    searchRow.appendChild(searchIcon);
    searchRow.appendChild(searchInput);
    searchRow.appendChild(clearBtn);
    papersFiltersEl.appendChild(searchRow);

    const controlsRow = document.createElement("div");
    controlsRow.className = "papers-controls-row";

    // Filter dropdown
    const sel = document.createElement("select");
    sel.className = "papers-filter-select";
    const filters = [
      { label: `All  ${papers.length}`, value: "all" },
      { label: `Unread  ${inQueue}`, value: "unread" },
      { label: `Read  ${read}`, value: "read" },
      { label: `Starred  ${promoted}`, value: "promoted" },
    ];
    for (const f of filters) {
      const opt = document.createElement("option");
      opt.value = f.value;
      opt.textContent = f.label;
      if (f.value === currentPaperFilter) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", () => {
      currentPaperFilter = sel.value;
      renderPapersList(cachedPapers);
    });
    controlsRow.appendChild(sel);

    // Sort icon buttons
    const SORT_ICONS = {
      newest: { svg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>`, title: "Newest first" },
      oldest: { svg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>`, title: "Oldest first" },
      citations: { svg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`, title: "Most cited" },
    };
    for (const [val, { svg, title }] of Object.entries(SORT_ICONS)) {
      const btn = document.createElement("button");
      btn.className = `papers-sort-icon-btn${val === currentPaperSort ? " active" : ""}`;
      btn.title = title;
      btn.innerHTML = svg;
      btn.addEventListener("click", () => {
        currentPaperSort = val;
        renderPapersList(cachedPapers);
      });
      controlsRow.appendChild(btn);
    }

    papersFiltersEl.appendChild(controlsRow);
  }

  // Render compact sidebar items
  renderPaperSidebarItems(papers, currentPaperFilter, currentPaperSort);
  _renderPapersSyncFooter(papers);
}

// "Last sync Xh ago" footer — derives from max(processed_at, uploaded_at)
// across papers rather than a separate sync-state file.
function _renderPapersSyncFooter(papers) {
  const view = document.getElementById("papers-view");
  if (!view) return;
  let footer = view.querySelector(".papers-sync-footer");
  if (!papers || !papers.length) {
    if (footer) footer.remove();
    return;
  }
  let latest = 0;
  for (const p of papers) {
    for (const k of ["processed_at", "uploaded_at"]) {
      const v = p[k];
      if (!v) continue;
      const t = Date.parse(v);
      if (isFinite(t) && t > latest) latest = t;
    }
  }
  if (!latest) {
    if (footer) footer.remove();
    return;
  }
  const d = (Date.now() - latest) / 1000;
  let rel;
  if (d < 60) rel = "just now";
  else if (d < 3600) rel = `${Math.floor(d / 60)}m ago`;
  else if (d < 86400) rel = `${Math.floor(d / 3600)}h ago`;
  else rel = `${Math.floor(d / 86400)}d ago`;
  if (!footer) {
    footer = document.createElement("div");
    footer.className = "papers-sync-footer";
    view.appendChild(footer);
  }
  footer.innerHTML = "";
  footer.title = new Date(latest).toLocaleString();
  const syncLabel = document.createElement("span");
  syncLabel.textContent = `Last sync ${rel}`;
  footer.appendChild(syncLabel);
  const syncBtn = document.createElement("button");
  syncBtn.id = "brew-sync-btn";
  syncBtn.className = "sidebar-action-btn brew-btn";
  syncBtn.title = "Sync paper library";
  syncBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>`;
  syncBtn.addEventListener("click", () => _runPapersSync(syncBtn));
  footer.appendChild(syncBtn);
}

function renderPaperSidebarItems(papers, filter, sort) {
  if (!papersSidebarEl) return;
  papersSidebarEl.innerHTML = "";

  let filtered = filter === "all" ? papers.slice()
    : filter === "unread" ? papers.filter((p) => p.status !== "processed")
    : filter === "read" ? papers.filter((p) => p.status === "processed")
    : papers.filter((p) => p.promoted);

  if (currentPaperSearch) {
    const q = currentPaperSearch.toLowerCase();
    filtered = filtered.filter((p) => {
      if ((p.title || "").toLowerCase().includes(q)) return true;
      if ((p.authors || []).some((a) => String(a).toLowerCase().includes(q))) return true;
      if ((p.arxiv_id || "").toLowerCase().includes(q)) return true;
      return false;
    });
  }

  // Sort by Zotero's dateAdded (ISO string — lexicographic == chronological).
  // Fall back to insertion index for papers that predate dateAdded storage.
  const dateKey = (p) => p.zotero_date_added || "";
  if (sort === "oldest") {
    filtered.sort((a, b) => {
      const da = dateKey(a), db = dateKey(b);
      if (da && db) return da < db ? -1 : da > db ? 1 : 0;
      return (a.index || 0) - (b.index || 0);
    });
  } else if (sort === "citations") {
    filtered.sort((a, b) => (b.citation_count || 0) - (a.citation_count || 0));
  } else {
    filtered.sort((a, b) => {
      const da = dateKey(a), db = dateKey(b);
      if (da && db) return da > db ? -1 : da < db ? 1 : 0;
      return (b.index || 0) - (a.index || 0);
    });
  }

  if (!filtered.length) {
    papersSidebarEl.innerHTML = `<div class="sidebar-empty"><p>No ${filter === "all" ? "" : filter + " "}papers.</p></div>`;
    return;
  }

  for (const paper of filtered) {
    const isRead = paper.status === "processed";
    const item = document.createElement("div");
    const classes = ["sidebar-item"];
    if (isRead) classes.push("is-read");
    if (paper.key === currentPaperKey) classes.push("active");
    item.className = classes.join(" ");
    item.dataset.key = paper.key;

    const dot = document.createElement("span");
    dot.className = "sidebar-item-dot";
    item.appendChild(dot);

    const name = document.createElement("span");
    name.className = "sidebar-item-name";
    name.textContent = paper.title || paper.key;
    item.appendChild(name);

    const metaParts = [];
    if (Array.isArray(paper.authors) && paper.authors.length) {
      const first = String(paper.authors[0] || "").trim();
      const lastName = first.includes(" ") ? first.split(" ").pop() : first;
      if (lastName) {
        metaParts.push(paper.authors.length > 1 ? `${lastName} et al.` : lastName);
      }
    }
    if (paper.publication_date) {
      const yearMatch = String(paper.publication_date).match(/(19|20)\d{2}/);
      if (yearMatch) metaParts.push(yearMatch[0]);
    }
    if (paper.citation_count) metaParts.push(`${paper.citation_count}`);
    if (metaParts.length) {
      const meta = document.createElement("span");
      meta.className = "sidebar-item-meta";
      meta.textContent = metaParts.join(" \u00B7 ");
      item.appendChild(meta);
    }

    if (paper.promoted) {
      const badge = document.createElement("span");
      badge.className = "sidebar-item-badge promoted";
      badge.textContent = "\u2605";
      item.appendChild(badge);
    }

    item.addEventListener("click", () => selectPaper(paper.key));
    papersSidebarEl.appendChild(item);
  }
}

/* ───── Papers Home Page — center pane when no paper selected ───── */

let _papersHomeData = null;
let _papersHomeLoading = false;

/** Byline string for a paper, with graceful fallbacks when authors are
 *  missing or placeholder-valued ("Anonymous", "Unknown"). Order:
 *  real author → venue → year → arxiv id → empty. */
function _paperByline(paper) {
  const authors = Array.isArray(paper.authors) ? paper.authors : [];
  const real = authors
    .map((a) => String(a || "").trim())
    .filter((a) => a && !/^(anonymous|unknown)$/i.test(a));
  if (real.length) {
    const first = real[0];
    const last = first.includes(" ") ? first.split(" ").pop() : first;
    return authors.length > 1 ? `${last} et al.` : last;
  }
  if (paper.venue) return paper.venue;
  if (paper.publication_date) {
    const y = String(paper.publication_date).match(/(19|20)\d{2}/);
    if (y) return y[0];
  }
  if (paper.arxiv_id) return `arXiv:${paper.arxiv_id}`;
  return "";
}

function showPapersHome() {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;

  // Hide experiment tabs + sibling detail panes, show papers home.
  const editorTabs = document.getElementById("editor-tabs");
  if (editorTabs) editorTabs.classList.add("hidden");
  if (welcomeEl) welcomeEl.classList.add("hidden");
  const nbDetail = document.getElementById("notebook-detail");
  const vaultDetail = document.getElementById("vault-detail");
  if (nbDetail) nbDetail.classList.add("hidden");
  if (vaultDetail) vaultDetail.classList.add("hidden");
  detailEl.classList.remove("hidden");
  detailEl.classList.remove("paper-tabbed");
  switchEditorTab("control-panel");

  // Show skeleton while loading
  detailEl.innerHTML = `
    <div class="papers-home">
      <div class="papers-home-header">
        <h2 class="papers-home-title">Papers</h2>
      </div>
      <div class="papers-home-loading">
        <div class="sidebar-skeleton">
          ${'<div class="skeleton-item"></div>'.repeat(3)}
        </div>
      </div>
    </div>`;

  fetchPapersHome();
}

async function fetchPapersHome() {
  if (!serverPort || _papersHomeLoading) return;
  _papersHomeLoading = true;

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/papers/home`);
    const data = await resp.json();
    if (data.ok) {
      _papersHomeData = data;
      renderPapersHome(data);
    }
  } catch (err) {
    console.warn("[papers-home] fetch failed:", err);
    const detailEl = document.getElementById("experiment-detail");
    if (detailEl) {
      const loading = detailEl.querySelector(".papers-home-loading");
      if (loading) loading.innerHTML = '<div class="sidebar-empty"><p>Could not load papers home.</p></div>';
    }
  } finally {
    _papersHomeLoading = false;
  }
}

function renderPapersHome(data) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || currentPaperKey) return;

  const home = document.createElement("div");
  home.className = "papers-home";
  const { insights } = data;

  // ── Title ──
  const header = document.createElement("div");
  header.className = "papers-home-header";
  const h1 = document.createElement("h1");
  h1.className = "papers-home-title";
  h1.textContent = "Papers";
  header.appendChild(h1);
  home.appendChild(header);

  // ── Reading velocity sparkline ──
  if (insights.velocity_4w && insights.velocity_4w.some(v => v > 0)) {
    const velSection = document.createElement("div");
    velSection.className = "papers-home-velocity";
    const max = Math.max(...insights.velocity_4w, 1);
    const thisWeek = insights.velocity_4w[0] || 0;
    const labels = ["This week", "Last week", "2 weeks ago", "3 weeks ago"];
    velSection.innerHTML = `
      <div class="papers-home-velocity-bars">
        ${insights.velocity_4w.map((v, i) => `
          <div class="velocity-bar-col" title="${labels[i]}: ${v} papers">
            <div class="velocity-bar" style="height: ${Math.max((v / max) * 32, 2)}px"></div>
            <span class="velocity-bar-label">${i === 0 ? "now" : `-${i}w`}</span>
          </div>
        `).join("")}
      </div>
      <span class="velocity-summary">${thisWeek} paper${thisWeek !== 1 ? "s" : ""} this week</span>`;
    home.appendChild(velSection);
  }

  // ── Recently Read — compact horizontal strip with progress ──
  if (data.recently_read && data.recently_read.length > 0) {
    const section = document.createElement("div");
    section.className = "papers-home-section";
    const thisWeek = insights?.velocity_4w?.[0] || 0;
    const hint = thisWeek > 0
      ? `<span class="papers-home-section-hint">${thisWeek} this week</span>`
      : "";
    section.innerHTML = `
      <div class="papers-home-section-header">
        <h3 class="papers-home-section-title">Recently Read</h3>
        ${hint}
      </div>`;
    const strip = document.createElement("div");
    strip.className = "papers-home-strip";

    for (const paper of data.recently_read) {
      const card = document.createElement("div");
      card.className = "papers-home-card papers-home-recent-mini";
      card.addEventListener("click", () => selectPaper(paper.key));

      const title = document.createElement("div");
      title.className = "papers-home-card-title";
      title.textContent = paper.title || paper.key;
      card.appendChild(title);

      const byline = _paperByline(paper);
      const hasEng = typeof paper.engagement === "number" && paper.engagement > 0;
      if (byline || hasEng) {
        const metaEl = document.createElement("div");
        metaEl.className = "papers-home-card-meta";
        if (byline) metaEl.appendChild(document.createTextNode(byline));
        if (hasEng) {
          const pct = Math.max(0, Math.min(100, paper.engagement));
          const dots = pct >= 67 ? " •••" : pct >= 34 ? " ••" : " •";
          const badge = document.createElement("span");
          badge.className = "papers-home-hl-dots";
          badge.textContent = dots;
          badge.title = `Highlight intensity (${pct})`;
          metaEl.appendChild(badge);
        }
        card.appendChild(metaEl);
      }

      strip.appendChild(card);
    }
    section.appendChild(strip);
    home.appendChild(section);
  }

  // ── Two-column layout: Up Next (main) | Your Topics (side) ──
  const hasQueue = data.queue && data.queue.length > 0;
  const hasTopics = insights.top_topics && insights.top_topics.length > 0;
  if (hasQueue || hasTopics) {
    const columns = document.createElement("div");
    columns.className = "papers-home-columns";

    const colMain = document.createElement("div");
    colMain.className = "papers-home-col-main";
    const colSide = document.createElement("div");
    colSide.className = "papers-home-col-side";

    if (hasQueue) {
      const section = document.createElement("div");
      section.className = "papers-home-section";
      section.innerHTML = '<h3 class="papers-home-section-title">Up Next</h3>';
      const list = document.createElement("div");
      list.className = "papers-home-queue";

      for (const paper of data.queue) {
        const row = document.createElement("div");
        row.className = "papers-home-queue-item";
        row.addEventListener("click", () => selectPaper(paper.key));

        const info = document.createElement("div");
        info.className = "papers-home-queue-info";

        const title = document.createElement("div");
        title.className = "papers-home-queue-title";
        title.textContent = paper.title || paper.key;
        info.appendChild(title);

        if (paper.reason) {
          const reason = document.createElement("div");
          reason.className = "papers-home-queue-reason";
          reason.textContent = paper.reason;
          info.appendChild(reason);
        }

        const metaParts = [];
        const byline = _paperByline(paper);
        if (byline) metaParts.push(byline);
        if (paper.citation_count) metaParts.push(`${paper.citation_count} cit.`);
        if (metaParts.length) {
          const metaEl = document.createElement("div");
          metaEl.className = "papers-home-queue-meta";
          metaEl.textContent = metaParts.join(" \u00B7 ");
          info.appendChild(metaEl);
        }

        row.appendChild(info);
        list.appendChild(row);
      }
      section.appendChild(list);
      colMain.appendChild(section);
    }

    if (hasTopics) {
      const section = document.createElement("div");
      section.className = "papers-home-section";
      section.innerHTML = '<h3 class="papers-home-section-title">Your Topics</h3>';
      const tags = document.createElement("div");
      tags.className = "papers-home-topics";
      for (const t of insights.top_topics) {
        const chip = document.createElement("span");
        chip.className = "paper-tag";
        chip.textContent = `${t.topic} (${t.count})`;
        tags.appendChild(chip);
      }
      section.appendChild(tags);
      colSide.appendChild(section);
    }

    if (colMain.children.length) columns.appendChild(colMain);
    if (colSide.children.length) columns.appendChild(colSide);
    home.appendChild(columns);
  }

  // ── Trending (HuggingFace) ──
  if (data.trending && data.trending.length > 0) {
    const section = document.createElement("div");
    section.className = "papers-home-section";
    section.innerHTML = `
      <div class="papers-home-section-header">
        <h3 class="papers-home-section-title">Trending on HuggingFace</h3>
        <span class="papers-home-section-hint">Today's top ML papers</span>
      </div>`;
    const list = document.createElement("div");
    list.className = "papers-home-trending";

    for (const paper of data.trending) {
      const row = document.createElement("div");
      row.className = "papers-home-trending-item";

      const info = document.createElement("div");
      info.className = "papers-home-trending-info";

      const titleRow = document.createElement("div");
      titleRow.className = "papers-home-trending-title-row";

      const title = document.createElement("div");
      title.className = "papers-home-trending-title";
      title.textContent = paper.title || "";
      titleRow.appendChild(title);
      info.appendChild(titleRow);

      const meta = document.createElement("div");
      meta.className = "papers-home-trending-meta";
      const parts = [];
      const trendByline = _paperByline(paper);
      if (trendByline) parts.push(trendByline);
      if (paper.upvotes) parts.push(`\u2B06 ${paper.upvotes}`);
      if (paper.github_stars) parts.push(`\u2605 ${paper.github_stars}`);
      meta.textContent = parts.join(" \u00B7 ");
      info.appendChild(meta);

      if (paper.relevance_hint) {
        const hint = document.createElement("div");
        hint.className = "papers-home-trending-relevance";
        hint.textContent = paper.relevance_hint;
        info.appendChild(hint);
      }

      if (paper.ai_keywords && paper.ai_keywords.length) {
        const tags = document.createElement("div");
        tags.className = "papers-home-card-tags";
        for (const kw of paper.ai_keywords.slice(0, 3)) {
          const chip = document.createElement("span");
          chip.className = "paper-tag mini";
          chip.textContent = kw;
          tags.appendChild(chip);
        }
        info.appendChild(tags);
      }

      row.appendChild(info);

      // Actions
      const actions = document.createElement("div");
      actions.className = "papers-home-trending-actions";

      if (paper.in_library) {
        const inLib = document.createElement("span");
        inLib.className = "papers-home-trending-inlib";
        inLib.textContent = "In library";
        actions.appendChild(inLib);
      } else {
        const addBtn = document.createElement("button");
        addBtn.className = "papers-home-trending-add";
        addBtn.textContent = "+ Add";
        addBtn.title = "Add to Zotero library";
        addBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          _addTrendingPaper(paper, addBtn);
        });
        actions.appendChild(addBtn);
      }

      if (paper.hf_url) {
        const linkBtn = document.createElement("button");
        linkBtn.className = "papers-home-trending-link";
        linkBtn.textContent = "HF";
        linkBtn.title = "Open on HuggingFace";
        linkBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          if (window.nicolas?.openExternal) window.nicolas.openExternal(paper.hf_url);
        });
        actions.appendChild(linkBtn);
      }

      if (paper.pdf_url) {
        const pdfBtn = document.createElement("button");
        pdfBtn.className = "papers-home-trending-link";
        pdfBtn.textContent = "PDF";
        pdfBtn.title = "Open PDF on arXiv";
        pdfBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          if (window.nicolas?.openExternal) window.nicolas.openExternal(paper.pdf_url);
        });
        actions.appendChild(pdfBtn);
      }

      row.appendChild(actions);

      // Click row to open on HF
      row.addEventListener("click", () => {
        if (paper.hf_url && window.nicolas?.openExternal) {
          window.nicolas.openExternal(paper.hf_url);
        }
      });

      list.appendChild(row);
    }
    section.appendChild(list);
    home.appendChild(section);
  }

  // ── Empty state ──
  if (!data.recently_read?.length && !data.queue?.length && !data.trending?.length) {
    const empty = document.createElement("div");
    empty.className = "papers-home-empty";
    empty.innerHTML = libraryConfigured
      ? `<p>Your library is quiet. Sync your papers or ask Nicolas for suggestions.</p>`
      : `<p>Connect your Zotero library to get started.</p>
         <button class="onboarding-btn" id="papers-home-setup-btn">Connect library</button>`;
    home.appendChild(empty);
    home.querySelector("#papers-home-setup-btn")
      ?.addEventListener("click", launchLibrarySetup);
  }

  detailEl.innerHTML = "";
  detailEl.appendChild(home);
}

async function _addTrendingPaper(paper, btn) {
  if (!serverPort || !paper.arxiv_id) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Adding\u2026";
  try {
    // Use the chat to add paper — send to Nicolas
    const prompt = `Add this paper to my Zotero library: arxiv:${paper.arxiv_id} "${paper.title}"`;
    if (typeof inputEl !== "undefined" && typeof sendMessage === "function") {
      inputEl.value = prompt;
      sendMessage();
      btn.textContent = "Sent";
      btn.className = "papers-home-trending-inlib";
    } else {
      btn.textContent = "Error";
    }
  } catch {
    btn.textContent = orig;
    btn.disabled = false;
  }
}

async function launchLibrarySetup() {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  // Show wizard in main detail area
  if (welcomeEl) welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  switchEditorTab("control-panel");

  detailEl.innerHTML = `
    <div class="onboarding-progress">
      <h2 class="exp-detail-title">Connect your paper library</h2>
      <p class="exp-detail-meta">Distillate syncs with Zotero to track your reading and extract highlights.</p>

      <div class="library-setup-wizard" id="library-wizard">
        <div class="library-step" id="lib-step-zotero">
          <div class="library-step-header">
            <span class="library-step-num">1</span>
            <span>Zotero credentials</span>
          </div>
          <p class="library-step-help">
            Create an API key at
            <a href="https://www.zotero.org/settings/keys/new" class="library-link" id="zotero-key-link">zotero.org/settings/keys</a>
            with read/write library access. Your user ID is shown on the same page.
          </p>
          <div class="library-field">
            <label for="lib-api-key">API key</label>
            <input type="password" id="lib-api-key" placeholder="your Zotero API key" spellcheck="false" autocomplete="off">
          </div>
          <div class="library-field">
            <label for="lib-user-id">User ID</label>
            <input type="text" id="lib-user-id" placeholder="numeric user ID" spellcheck="false" autocomplete="off">
          </div>
          <div class="library-error hidden" id="lib-zotero-error"></div>
          <button class="onboarding-btn" id="lib-verify-btn">Verify &amp; connect</button>
        </div>

        <div class="library-step hidden" id="lib-step-done">
          <div class="library-step-header">
            <span class="library-step-num">2</span>
            <span>Syncing your library</span>
          </div>
          <div class="wizard-flow" id="lib-sync-flow">
            <div class="flow-step" data-step="1"><span class="flow-dot active"></span><span class="flow-label">Pulling papers from Zotero...</span><span class="flow-detail"></span></div>
          </div>
        </div>
      </div>
    </div>`;

  // Open external links in browser
  document.getElementById("zotero-key-link")?.addEventListener("click", (e) => {
    e.preventDefault();
    if (window.nicolas?.openExternal) window.nicolas.openExternal("https://www.zotero.org/settings/keys/new");
    else window.open("https://www.zotero.org/settings/keys/new", "_blank");
  });

  const verifyBtn = document.getElementById("lib-verify-btn");
  const apiKeyInput = document.getElementById("lib-api-key");
  const userIdInput = document.getElementById("lib-user-id");
  const errorEl = document.getElementById("lib-zotero-error");

  // Step 1: Verify Zotero credentials
  verifyBtn.addEventListener("click", async () => {
    const apiKey = apiKeyInput.value.trim();
    const userId = userIdInput.value.trim();
    if (!apiKey || !userId) {
      errorEl.textContent = "Both fields are required.";
      errorEl.classList.remove("hidden");
      return;
    }
    verifyBtn.disabled = true;
    verifyBtn.textContent = "Verifying...";
    errorEl.classList.add("hidden");

    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/library/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zotero_api_key: apiKey, zotero_user_id: userId }),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.reason || "Verification failed");

      // Move to sync step
      document.getElementById("lib-step-zotero").classList.add("library-step-done");
      document.getElementById("lib-step-done").classList.remove("hidden");

      // Trigger paper sync
      try {
        const r = await fetch(`http://127.0.0.1:${serverPort}/sync`, { method: "POST" });
        const syncFlow = document.getElementById("lib-sync-flow");
        const dot = syncFlow?.querySelector(".flow-dot");
        const label = syncFlow?.querySelector(".flow-label");
        if (dot) dot.className = "flow-dot done";
        if (label) label.textContent = r.ok ? "Library synced!" : "Connected!";
      } catch {
        const syncFlow = document.getElementById("lib-sync-flow");
        const dot = syncFlow?.querySelector(".flow-dot");
        const label = syncFlow?.querySelector(".flow-label");
        if (dot) dot.className = "flow-dot done";
        if (label) label.textContent = "Connected!";
      }

      // Refresh papers sidebar
      libraryConfigured = true;
      await new Promise((r) => setTimeout(r, 800));
      fetchPapersData();
      fetchIntegrations();
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.classList.remove("hidden");
      verifyBtn.disabled = false;
      verifyBtn.textContent = "Verify & connect";
    }
  });
}

// Per-paper sticky tab state. Values: "overview" | "pdf".
const _paperActiveTab = new Map();

function selectPaper(paperKey) {
  // Toggle: clicking the already-selected paper deselects
  if (currentPaperKey === paperKey) {
    deselectAll();
    return;
  }
  // Tearing down the reader on paper switch prevents its IntersectionObserver
  // and debounced save-timer from lingering on detached DOM.
  if (typeof window.closePaperReader === "function") {
    window.closePaperReader();
  }
  currentPaperKey = paperKey;
  currentProjectId = null;

  // Update sidebar selection
  papersSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.key === paperKey);
  });
  refreshChatSuggestions();

  // Show paper detail in experiment-detail area (reuse editor area)
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  // Hide sibling detail panes + experiment tabs, then show paper detail.
  const editorTabs = document.getElementById("editor-tabs");
  if (editorTabs) editorTabs.classList.add("hidden");
  welcomeEl.classList.add("hidden");
  const nbDetail = document.getElementById("notebook-detail");
  const vaultDetail = document.getElementById("vault-detail");
  if (nbDetail) nbDetail.classList.add("hidden");
  if (vaultDetail) vaultDetail.classList.add("hidden");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML = '<div class="exp-detail-loading">Loading paper...</div>';

  switchEditorTab("control-panel");

  fetch(`http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}`)
    .then((r) => r.json())
    .then((resp) => {
      if (!resp.ok) {
        detailEl.innerHTML = '<div class="exp-detail-loading">Could not load paper details.</div>';
        return;
      }
      const data = resp.paper || resp;
      _renderPaperDetail(paperKey, data, detailEl);
    })
    .catch((err) => {
      console.error("[paper-detail] Error:", err);
      detailEl.innerHTML = `<div class="exp-detail-loading">Failed to load paper details: ${err.message || err}</div>`;
    });
}

/* ───── Paper detail layout: header + actions + tabs ─────
 *
 * The detail pane is structured as a flex column:
 *
 *   [ header band        ] title, authors, badges, external link
 *   [ actions            ] promote · refresh · open-in-obsidian
 *   [ tab bar            ] Overview | PDF
 *   [ tab body (flex:1)  ] ← Overview content OR PDF reader
 *
 * The body is the only growing child, so each tab owns its own scroll
 * container. The PDF tab is wired to openPaperReader (which itself mounts
 * into whatever container it's given), so the reader lives inline instead
 * of taking over the whole pane.
 */
function _renderPaperDetail(paperKey, data, detailEl) {
  detailEl.innerHTML = "";
  detailEl.classList.add("paper-tabbed");

  // ---- Header band --------------------------------------------------------
  const headerBand = document.createElement("div");
  headerBand.className = "paper-detail-header-band";
  detailEl.appendChild(headerBand);

  const header = document.createElement("div");
  header.className = "exp-detail-header";

  const title = document.createElement("h2");
  title.className = "exp-detail-title";
  title.textContent = data.title || paperKey;
  header.appendChild(title);

  // Authors + date + venue on one line
  const metaLine1 = [];
  if (data.authors && data.authors.length) metaLine1.push(data.authors.join(", "));
  if (data.publication_date) metaLine1.push(data.publication_date);
  if (data.venue) metaLine1.push(data.venue);
  if (metaLine1.length) {
    const el = document.createElement("div");
    el.className = "exp-detail-meta";
    el.textContent = metaLine1.join(" \u00B7 ");
    header.appendChild(el);
  }

  // URL + stats + badges on one line
  const metaLine2 = document.createElement("div");
  metaLine2.className = "exp-detail-meta paper-meta-badges";

  const paperUrl = data.url
    || (data.arxiv_id ? `https://arxiv.org/abs/${data.arxiv_id}` : "")
    || (data.doi ? `https://doi.org/${data.doi}` : "");
  if (data.status === "processed") {
    const b = document.createElement("span");
    b.className = "exp-detail-badge keep";
    b.textContent = "read";
    metaLine2.appendChild(b);
  }
  if (data.promoted) {
    const b = document.createElement("span");
    b.className = "exp-detail-badge";
    b.style.background = "var(--accent)";
    b.textContent = "promoted";
    metaLine2.appendChild(b);
  }

  const statParts = [];
  if (data.engagement) statParts.push(`${data.engagement}% engagement`);
  if (data.page_count) statParts.push(`${data.page_count} pages`);
  if (data.citation_count) statParts.push(`${data.citation_count} citations`);
  if (statParts.length) {
    const statsSpan = document.createElement("span");
    statsSpan.textContent = statParts.join(" \u00B7 ");
    metaLine2.appendChild(statsSpan);
  }

  if (paperUrl) {
    const linkEl = document.createElement("a");
    linkEl.className = "paper-external-link";
    linkEl.href = "#";
    linkEl.style.margin = "0";
    linkEl.textContent = data.arxiv_id ? `arxiv.org/abs/${data.arxiv_id}` : paperUrl.replace(/^https?:\/\//, "");
    linkEl.addEventListener("click", (e) => {
      e.preventDefault();
      window.nicolas.openExternal(paperUrl);
    });
    metaLine2.appendChild(linkEl);
  }

  if (metaLine2.children.length) header.appendChild(metaLine2);
  headerBand.appendChild(header);

  // ---- Paper-level actions (not tab-scoped) --------------------------------
  const actions = document.createElement("div");
  actions.className = "exp-detail-actions";

  const promoteBtn = document.createElement("button");
  const isPromoted = !!data.promoted;
  promoteBtn.className = isPromoted ? "paper-action-btn promoted" : "paper-action-btn";
  promoteBtn.textContent = isPromoted ? "★" : "☆";
  promoteBtn.title = isPromoted ? "Unstar paper" : "Star paper";
  promoteBtn.dataset.promoted = isPromoted ? "1" : "0";
  promoteBtn.addEventListener("click", () => {
    const wantPromote = promoteBtn.dataset.promoted === "0";
    togglePromote(paperKey, wantPromote, promoteBtn);
  });
  actions.appendChild(promoteBtn);

  const refreshBtn = document.createElement("button");
  refreshBtn.className = "paper-action-btn";
  refreshBtn.textContent = "↻";
  refreshBtn.title = "Refresh metadata";
  refreshBtn.style.fontSize = "13px";
  refreshBtn.addEventListener("click", () => refreshPaperMetadata(paperKey, refreshBtn));
  actions.appendChild(refreshBtn);

  headerBand.appendChild(actions);

  // ---- Tab bar -------------------------------------------------------------
  const tabBar = document.createElement("div");
  tabBar.className = "paper-tab-bar";
  headerBand.appendChild(tabBar);

  const tabBody = document.createElement("div");
  tabBody.className = "paper-tab-body";
  detailEl.appendChild(tabBody);

  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "pdf", label: _pdfTabLabel(data) },
  ];

  // Default tab selection: unread papers open straight into the PDF (the
  // actual work), processed papers default to Overview (where the distilled
  // value lives). A paper's last-selected tab overrides both.
  const defaultTab = _paperActiveTab.get(paperKey)
    || (data.status !== "processed" ? "pdf" : "overview");

  const tabButtons = {};
  for (const t of tabs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "paper-tab-btn";
    btn.textContent = t.label;
    btn.dataset.tab = t.id;
    btn.addEventListener("click", () => _switchPaperTab(paperKey, data, tabBody, tabButtons, tabControls, t.id));
    tabBar.appendChild(btn);
    tabButtons[t.id] = btn;
  }

  // Right-aligned slot in the tab bar for the reader's zoom / page controls.
  // Populated by openPaperReader when the PDF tab is active, emptied on switch.
  const tabControls = document.createElement("div");
  tabControls.className = "paper-tab-controls";
  tabBar.appendChild(tabControls);

  _switchPaperTab(paperKey, data, tabBody, tabButtons, tabControls, defaultTab);
}

function _switchPaperTab(paperKey, data, tabBody, tabButtons, tabControls, tabId) {
  _paperActiveTab.set(paperKey, tabId);
  for (const [id, btn] of Object.entries(tabButtons)) {
    btn.classList.toggle("active", id === tabId);
  }
  // Tear down any prior reader before reusing the tab body — prevents the
  // IntersectionObserver from observing detached pages.
  if (typeof window.closePaperReader === "function") {
    window.closePaperReader();
  }
  tabBody.innerHTML = "";
  tabBody.classList.remove("paper-overview", "paper-reader-host");
  tabBody.dataset.tab = tabId;
  tabControls.innerHTML = "";

  if (tabId === "overview") {
    renderPaperOverview(data, tabBody);
  } else if (tabId === "pdf") {
    if (typeof window.openPaperReader !== "function") {
      tabBody.innerHTML = '<div class="paper-reader-status"><p>Reader unavailable.</p></div>';
      return;
    }
    window.openPaperReader(paperKey, {
      container: tabBody,
      title: data.title || paperKey,
      showToolbar: false,
      status: data.status || "",
      controlsContainer: tabControls,
    });
  }
}

/** Label the PDF tab with a live page indicator when we have one. */
function _pdfTabLabel() {
  return "PDF";
}

/** Render the Overview content (summary, tags, highlights, related) into a
 *  container. Pure — does not touch anything outside its container arg. */
function renderPaperOverview(data, container) {
  // Let the overview scroll independently inside its tab body.
  container.classList.add("paper-overview");

  // Summary (or S2 TLDR fallback)
  if (data.summary) {
    const section = document.createElement("div");
    section.className = "exp-detail-section";
    const sTitle = document.createElement("h3");
    sTitle.textContent = "Summary";
    section.appendChild(sTitle);
    const p = document.createElement("p");
    p.textContent = data.summary;
    section.appendChild(p);
    container.appendChild(section);
  } else if (data.s2_tldr) {
    const section = document.createElement("div");
    section.className = "exp-detail-section";
    const sTitle = document.createElement("h3");
    sTitle.textContent = "Semantic Scholar TLDR";
    section.appendChild(sTitle);
    const p = document.createElement("p");
    p.className = "paper-card-s2-tldr";
    p.textContent = data.s2_tldr;
    section.appendChild(p);
    container.appendChild(section);
  }

  // Tags
  if (data.tags && data.tags.length) {
    const section = document.createElement("div");
    section.className = "exp-detail-section";
    const sTitle = document.createElement("h3");
    sTitle.textContent = "Topics";
    section.appendChild(sTitle);
    const tags = document.createElement("div");
    tags.className = "paper-card-tags";
    for (const tag of data.tags) {
      const chip = document.createElement("span");
      chip.className = "paper-tag";
      chip.textContent = tag;
      tags.appendChild(chip);
    }
    section.appendChild(tags);
    container.appendChild(section);
  }

  // Highlights
  if (data.highlights && data.highlights.length) {
    let hlText = typeof data.highlights === "string"
      ? data.highlights
      : data.highlights.map((h) => typeof h === "string" ? `- ${h}` : `- ${h.text || JSON.stringify(h)}`).join("\n");
    hlText = hlText.replace(/^#{1,3}\s*Highlights?\s*\n*/i, "").trim();
    if (hlText) {
      const section = document.createElement("div");
      section.className = "exp-detail-section";
      const sTitle = document.createElement("h3");
      sTitle.textContent = "Highlights";
      section.appendChild(sTitle);
      const hl = document.createElement("div");
      hl.className = "paper-highlights-list markdown-body";
      hl.innerHTML = window.markedParse(hlText);
      section.appendChild(hl);
      container.appendChild(section);
    }
  }

  // Related Experiments (cross-reference from linked_projects)
  if (data.linked_projects && data.linked_projects.length > 0) {
    const section = document.createElement("div");
    section.className = "exp-detail-section";
    const sTitle = document.createElement("h3");
    sTitle.textContent = "Related Experiments";
    section.appendChild(sTitle);
    const list = document.createElement("div");
    list.className = "related-experiments-list";
    for (const proj of data.linked_projects) {
      const item = document.createElement("div");
      item.className = "related-experiment-item";
      item.textContent = proj.name || proj.id;
      item.style.cursor = "pointer";
      item.addEventListener("click", () => {
        selectProject(proj.id);
      });
      list.appendChild(item);
    }
    section.appendChild(list);
    container.appendChild(section);
  }

  // ── Paper Radar: Related This Month ──
  if (currentPaperKey) {
    const radarSection = document.createElement("div");
    radarSection.className = "exp-detail-section exp-radar-section";
    container.appendChild(radarSection);
    _loadPaperRadar(currentPaperKey, radarSection);
  }
}

async function _loadPaperRadar(paperKey, container) {
  if (!serverPort) return;
  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}/radar`
    );
    const data = await resp.json();
    if (!data.ok) return;

    const hasLibrary = data.library_matches && data.library_matches.length > 0;
    const hasTrending = data.trending_matches && data.trending_matches.length > 0;
    if (!hasLibrary && !hasTrending) {
      container.remove();
      return;
    }

    const header = document.createElement("h3");
    header.textContent = "Related This Month";
    container.appendChild(header);

    if (hasLibrary) {
      const sub = document.createElement("div");
      sub.className = "radar-subheader";
      sub.textContent = "From your library";
      container.appendChild(sub);

      for (const match of data.library_matches) {
        const item = document.createElement("div");
        item.className = "radar-item";

        const title = document.createElement("div");
        title.className = "radar-item-title";
        title.textContent = match.title;
        item.appendChild(title);

        const metaParts = [];
        if (match.authors && match.authors.length) {
          const first = String(match.authors[0]).trim();
          const last = first.includes(" ") ? first.split(" ").pop() : first;
          metaParts.push(match.authors.length > 1 ? `${last} et al.` : last);
        }
        if (match.citation_count) metaParts.push(`${match.citation_count} citations`);
        if (metaParts.length) {
          const meta = document.createElement("div");
          meta.className = "radar-item-meta";
          meta.textContent = metaParts.join(" \u00B7 ");
          item.appendChild(meta);
        }

        const rel = document.createElement("div");
        rel.className = "radar-item-relevance";
        rel.textContent = match.relevance;
        item.appendChild(rel);

        item.addEventListener("click", () => {
          if (typeof selectPaper === "function") selectPaper(match.key);
        });
        container.appendChild(item);
      }
    }

    if (hasTrending) {
      const sub = document.createElement("div");
      sub.className = "radar-subheader";
      sub.textContent = "Trending";
      container.appendChild(sub);

      for (const match of data.trending_matches) {
        const item = document.createElement("div");
        item.className = "radar-item";

        const title = document.createElement("div");
        title.className = "radar-item-title";
        title.textContent = match.title;
        item.appendChild(title);

        const metaParts = [];
        if (match.authors && match.authors.length) {
          const first = String(match.authors[0]).trim();
          const last = first.includes(" ") ? first.split(" ").pop() : first;
          metaParts.push(match.authors.length > 1 ? `${last} et al.` : last);
        }
        if (match.upvotes) metaParts.push(`\u2B06 ${match.upvotes}`);
        if (match.github_stars) metaParts.push(`\u2605 ${match.github_stars}`);
        if (metaParts.length) {
          const meta = document.createElement("div");
          meta.className = "radar-item-meta";
          meta.textContent = metaParts.join(" \u00B7 ");
          item.appendChild(meta);
        }

        const rel = document.createElement("div");
        rel.className = "radar-item-relevance";
        rel.textContent = match.relevance;
        item.appendChild(rel);

        item.addEventListener("click", () => {
          if (match.hf_url && window.nicolas?.openExternal) {
            window.nicolas.openExternal(match.hf_url);
          }
        });
        container.appendChild(item);
      }
    }
  } catch (e) {
    container.remove();
  }
}

function togglePromote(paperKey, promote, btn) {
  if (!serverPort) return;
  const endpoint = promote ? "promote" : "unpromote";
  btn.disabled = true;
  btn.textContent = "⋯";
  fetch(`http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}/${endpoint}`, { method: "POST" })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        const nowPromoted = !!data.promoted;
        btn.textContent = nowPromoted ? "★" : "☆";
        btn.title = nowPromoted ? "Unstar paper" : "Star paper";
        btn.dataset.promoted = nowPromoted ? "1" : "0";
        btn.classList.toggle("promoted", nowPromoted);
        // Sync source data so re-renders (filters/sort) stay correct
        const paperObj = cachedPapers.find((p) => p.key === paperKey);
        if (paperObj) paperObj.promoted = nowPromoted;
        // Update the badge on the card (if in paper card view)
        const card = btn.closest(".paper-card");
        if (card) {
          const header = card.querySelector(".paper-card-header");
          if (header) {
            const existingBadge = header.querySelector(".paper-promoted-badge");
            if (nowPromoted && !existingBadge) {
              const badge = document.createElement("span");
              badge.className = "paper-promoted-badge";
              badge.textContent = "promoted";
              header.appendChild(badge);
            } else if (!nowPromoted && existingBadge) {
              existingBadge.remove();
            }
          }
        }
        // Update sidebar badge
        fetchPapersData();
      }
    })
    .catch(() => {
      const nowPromoted = btn.dataset.promoted === "1";
      btn.textContent = nowPromoted ? "★" : "☆";
      showToast(`Failed to ${promote ? "promote" : "unpromote"} paper`);
    })
    .finally(() => { btn.disabled = false; });
}

function refreshPaperMetadata(paperKey, btn) {
  if (!serverPort) return;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Refreshing...";
  fetch(`http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}/refresh-metadata`, { method: "POST" })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        btn.textContent = "Done!";
        setTimeout(() => { btn.textContent = originalText; btn.disabled = false; }, 1500);
        // Refresh the papers list to show updated data
        fetchPapersData();
      } else {
        btn.textContent = "Failed";
        setTimeout(() => { btn.textContent = originalText; btn.disabled = false; }, 1500);
      }
    })
    .catch(() => {
      btn.textContent = originalText;
      btn.disabled = false;
      showToast("Failed to refresh metadata");
    });
}
