/* ───── Canvas editor (inline, mounted into the project detail) ─────
 *
 * A Canvas is any editable document the user works on with an agent:
 * a LaTeX paper, a Markdown draft, a Python script, a config file. Each
 * canvas has a type that drives a small registry of behaviors:
 *
 *   - latex:    CodeMirror (stex) + PDF.js preview + Tectonic compile
 *   - markdown: CodeMirror (md)  + marked-rendered HTML live preview
 *   - plain:    CodeMirror only  (no preview pane)
 *
 * The agent panel at the bottom is always available regardless of type:
 * a Claude Code session with cwd = canvas.dir, pre-primed on launch with
 * the filename + type so it knows what file it's helping the user edit.
 * The session stays live across drill-out/drill-in via the writeup record.
 *
 * ⌘S = save the buffer, then refresh the preview (compile for LaTeX,
 * re-render the HTML for Markdown, no-op for plain).
 * ⌘K = open the inline-edit modal with the current selection as context;
 * the agent receives a structured prompt and edits the file via its
 * Edit tool. The file watcher picks up the change and hot-reloads.
 *
 * Clicking a filename in the agent terminal opens that file as a new
 * canvas (or refocuses an existing one) via the xterm link provider.
 */

console.log("[canvas.js] script start");
const _bufferCache = new Map(); // `${wsId}::${cvId}` -> { content, dirty }
let _current = null;            // { wsId, cvId, canvas, editor, onBack, ... }

// _pdfjsLib, _pdfjsPromise, and _loadPdfJs() are provided by paper-reader.js
// which is loaded earlier and shares the same global scope.

function _bufKey(wsId, cvId) { return `${wsId}::${cvId}`; }

// ---- Canvas type registry ------------------------------------------------
//
// Each handler describes how a given canvas type renders its preview pane
// and reacts to saves. LaTeX goes through Tectonic → PDF.js. Markdown
// uses the already-loaded markedParse global. Plain has no preview.

const _typeHandlers = {
  latex: {
    previewLabel: "PDF",
    hasPreview: true,
    // After a successful write, run Tectonic and show the PDF.
    async onAfterSave() {
      await _compileLatex();
    },
    // Initial preview render on canvas mount (existing PDF from disk).
    async onMount() {
      await _renderLatexPdf();
    },
    // Render the preview pane now (called when the user toggles back into
    // Split or PDF view mode). For LaTeX that means re-drawing the PDF.
    async refreshPreview() {
      await _renderLatexPdf();
    },
  },
  markdown: {
    previewLabel: "Preview",
    hasPreview: true,
    async onAfterSave() {
      _renderMarkdownPreview();
    },
    async onMount() {
      _renderMarkdownPreview();
    },
    async refreshPreview() {
      _renderMarkdownPreview();
    },
  },
  plain: {
    previewLabel: "",
    hasPreview: false,
    async onAfterSave() {},
    async onMount() {},
    async refreshPreview() {},
  },
};

function _handlerFor(type) {
  return _typeHandlers[type] || _typeHandlers.plain;
}

// ---- Shell HTML ----------------------------------------------------------

function _renderShell(title, type) {
  const handler = _handlerFor(type);
  const previewLabel = handler.previewLabel || "Preview";
  const previewTabHtml = handler.hasPreview ? `
    <div class="canvas-view-toggle" role="tablist" aria-label="View mode">
      <button class="canvas-view-btn" data-view="source" title="Source only">Source</button>
      <button class="canvas-view-btn active" data-view="split" title="Split view">Split</button>
      <button class="canvas-view-btn" data-view="preview" title="${previewLabel} only">${previewLabel}</button>
    </div>
  ` : "";

  return `
    <div class="canvas-inline" data-view-mode="split" data-canvas-type="${type}">
      <div class="canvas-inline-toolbar">
        <button class="sidebar-header-btn" id="canvas-inline-back">&larr; Back</button>
        <span class="canvas-inline-title">${_escapeHtml(title || "Canvas")}</span>
        <div class="canvas-inline-spacer"></div>
        ${previewTabHtml}
        <button class="paper-action-btn primary" id="canvas-inline-save" title="Save (⌘S)">Save</button>
        <span id="canvas-inline-status" class="canvas-status"></span>
      </div>
      <div class="canvas-inline-split">
        <div class="canvas-inline-editor-pane">
          <div id="canvas-inline-mount"></div>
        </div>
        <div class="resize-handle vertical canvas-inline-resize" id="canvas-inline-resize"></div>
        <div class="canvas-inline-preview-pane" id="canvas-inline-preview-pane">
          <div id="canvas-inline-preview-empty" class="canvas-preview-empty">
            <p>Press <strong>⌘S</strong> to build the preview.</p>
          </div>
          <div id="canvas-inline-preview-body"></div>
        </div>
      </div>
      <div id="canvas-inline-error-panel" class="hidden"></div>
      <div class="canvas-inline-bottom" id="canvas-inline-bottom">
        <div class="canvas-inline-bottom-header" id="canvas-agent-header">
          <span class="canvas-inline-bottom-title">Agent</span>
          <span id="canvas-agent-status" class="canvas-agent-status"></span>
          <div class="canvas-inline-spacer"></div>
          <button class="canvas-agent-chevron" id="canvas-agent-toggle" title="Collapse">&#x25BC;</button>
        </div>
        <div class="canvas-inline-bottom-body" id="canvas-agent-body">
          <div id="canvas-agent-empty" class="canvas-agent-empty">
            <button class="paper-action-btn primary" id="canvas-agent-launch">Launch agent</button>
            <p class="canvas-agent-hint">Claude Code runs in the canvas directory and knows it's helping you edit this file. Double-click any filename it mentions to open it as a canvas.</p>
          </div>
          <div id="xterm-canvas-bottom" class="canvas-xterm hidden"></div>
        </div>
      </div>
      <div id="canvas-reload-banner" class="hidden"></div>
      <div id="canvas-inline-install-modal" class="hidden">
        <div class="canvas-install-card">
          <h3>Installing Tectonic</h3>
          <p id="canvas-inline-install-msg">Downloading LaTeX engine…</p>
          <div class="canvas-install-bar"><div id="canvas-inline-install-fill"></div></div>
          <p class="canvas-install-hint">One-time setup. ~30 MB.</p>
        </div>
      </div>
      <div id="canvas-inline-edit-modal" class="hidden">
        <div class="canvas-inline-edit-card">
          <div class="canvas-inline-edit-header">
            <span class="canvas-inline-edit-title">Edit with Claude</span>
            <span class="canvas-inline-edit-kbd">⌘K</span>
          </div>
          <div class="canvas-inline-edit-context" id="canvas-inline-edit-context"></div>
          <textarea id="canvas-inline-edit-input" placeholder="How should this be changed? (e.g. tighten the prose, fix the math, add a citation)" rows="3"></textarea>
          <div class="canvas-inline-edit-actions">
            <button class="paper-action-btn" id="canvas-inline-edit-cancel">Cancel</button>
            <button class="paper-action-btn primary" id="canvas-inline-edit-submit">Send to agent</button>
          </div>
          <div id="canvas-inline-edit-status" class="canvas-inline-edit-status"></div>
        </div>
      </div>
    </div>
  `;
}

// ---- Mount / destroy -----------------------------------------------------

async function mountCanvasEditor(container, wsId, cvId, opts = {}) {
  if (!container) return;
  destroyCanvasEditor();

  // Fetch the canvas record (title + type + entry).
  let canvas = null;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${wsId}/canvases`);
    const data = await resp.json();
    if (data.ok) {
      canvas = (data.canvases || []).find((c) => c.id === cvId) || null;
    }
  } catch {}

  if (!canvas) {
    container.innerHTML = `<div class="canvas-error">Canvas not found.</div>`;
    return;
  }

  const title = canvas.title || "Canvas";
  const type = canvas.type || "plain";
  container.innerHTML = _renderShell(title, type);

  _current = {
    wsId,
    cvId,
    canvas,
    container,
    editor: null,
    compileInFlight: false,
    dirty: false,
    lastSavedContent: "",
    agentAttached: false,
    onBack: opts.onBack || (() => {
      if (typeof selectWorkspace === "function") selectWorkspace(wsId);
    }),
  };

  document.getElementById("canvas-inline-back")?.addEventListener("click", async () => {
    if (_current?.dirty) {
      try { await _saveCurrentFile(); } catch {}
    }
    const onBack = _current?.onBack;
    destroyCanvasEditor();
    if (onBack) onBack();
  });
  document.getElementById("canvas-inline-save")?.addEventListener("click", () => _saveAndRefresh());

  _wireViewToggle();
  _applyViewMode(_loadViewMode(wsId, cvId));

  _wireInlineResize(wsId, cvId);
  _applySavedSplitRatio(wsId, cvId);

  _wireAgentPanel();
  await _setupAgentPanel();

  _wireInlineEditModal();
  _wireFileWatcher();

  await _loadEntryFile();

  // Let the type handler do any mount-time work (e.g. render the existing
  // PDF if one is on disk, or generate the markdown preview).
  try { await _handlerFor(type).onMount(); } catch {}

  // Auto-launch the agent if requested (e.g. from "Work on this" button).
  // _setupAgentPanel already auto-attaches if a session exists, so this
  // only triggers for canvases with no running session.
  if (opts.autoLaunch && !_current?.agentAttached) {
    _launchAgent();
  }
}

function destroyCanvasEditor() {
  if (_current?.editor?.destroy) {
    try { _current.editor.destroy(); } catch {}
  }
  // Only touch the xterm terminal if the canvas still actively owns it.
  // window._canvasTermActive is cleared by preload.js init() when the main
  // terminal reclaims xterm-container after a canvas session is stopped.
  // Without this guard, destroying the canvas editor would dispose a working
  // main terminal (Stop/Complete → selectWorkspace → destroyCanvasEditor crash).
  if (window._canvasTermActive) {
    if (_current?.agentAttached && typeof currentTerminalProject !== "undefined" && currentTerminalProject) {
      try { window.nicolas?.terminalDetach(currentTerminalProject); } catch {}
    }
    if (window.xtermBridge && _current?.agentAttached) {
      try { window.xtermBridge.dispose(); } catch {}
    }
    if (_current?.agentAttached) {
      if (typeof terminalInitialized !== "undefined") terminalInitialized = false;
      if (typeof currentTerminalProject !== "undefined") currentTerminalProject = null;
      if (typeof currentTerminalSession !== "undefined") currentTerminalSession = null;
      _termReadyPromise = null;
    }
  }
  // Cleanup canvas terminal resize observers, handlers, and timers
  if (_current?._canvasTermResizeObs) {
    try { _current._canvasTermResizeObs.disconnect(); } catch {}
  }
  if (_current?._canvasTermResizeHandle && _current?._canvasTermResizeHandler) {
    try { _current._canvasTermResizeHandle.removeEventListener("mouseup", _current._canvasTermResizeHandler); } catch {}
  }
  if (_current?._canvasTermResizeTimer) {
    try { clearTimeout(_current._canvasTermResizeTimer); } catch {}
  }
  if (_current?._canvasTermFitFrame) {
    try { cancelAnimationFrame(_current._canvasTermFitFrame); } catch {}
  }
  if (_current?.wsId && _current?.cvId && window.distillate?.canvas?.stopWatch) {
    try { window.distillate.canvas.stopWatch(_current.wsId, _current.cvId); } catch {}
  }
  _current = null;
  // Release the guard so the main terminal can re-initialize.
  window._canvasTermActive = false;
}

// ---- CodeMirror lifecycle ------------------------------------------------

async function _ensureEditor() {
  if (_current?.editor) return _current.editor;
  if (!window.createCanvasEditor) {
    await new Promise((resolve) => {
      if (window.__canvasEditorReady) { resolve(); return; }
      window.addEventListener("canvas-editor-ready", () => resolve(), { once: true });
      setTimeout(resolve, 5000);
    });
  }
  if (!window.createCanvasEditor || !_current) {
    _setStatus("Editor failed to load", "error");
    return null;
  }
  const mount = document.getElementById("canvas-inline-mount");
  if (!mount) return null;
  _current.editor = window.createCanvasEditor(mount, {
    type: _current.canvas?.type || "plain",
    doc: "",
    onChange: (text) => {
      if (!_current) return;
      _current.dirty = text !== _current.lastSavedContent;
      _bufferCache.set(_bufKey(_current.wsId, _current.cvId), { content: text, dirty: _current.dirty });
      _setStatus(_current.dirty ? "Modified" : "");
      // Live markdown preview — no save required.
      if (_current.canvas?.type === "markdown") _renderMarkdownPreview();
    },
    onSave: () => _saveAndRefresh(),
  });
  return _current.editor;
}

async function _loadEntryFile() {
  if (!_current) return;
  const { wsId, cvId, canvas } = _current;
  if (!window.distillate?.canvas) return;
  const editor = await _ensureEditor();
  if (!editor || !_current) return;

  const cached = _bufferCache.get(_bufKey(wsId, cvId));
  if (cached?.dirty) {
    editor.setDoc(cached.content);
    _current.lastSavedContent = cached.content;
    _current.dirty = true;
    _setStatus("Modified");
    editor.focus();
    return;
  }

  const entry = canvas?.entry || "main.tex";
  try {
    const result = await window.distillate.canvas.readFile(wsId, cvId, entry);
    if (!result.ok) {
      _setStatus(`Read failed: ${result.error}`, "error");
      return;
    }
    editor.setDoc(result.content || "");
    _current.lastSavedContent = result.content || "";
    _current.dirty = false;
    _bufferCache.set(_bufKey(wsId, cvId), { content: _current.lastSavedContent, dirty: false });
    _setStatus("");
    editor.focus();
  } catch (err) {
    _setStatus(`Read failed: ${err.message}`, "error");
  }
}

async function _saveCurrentFile() {
  if (!_current?.editor || !window.distillate?.canvas) return false;
  const { wsId, cvId, editor, canvas } = _current;
  const entry = canvas?.entry || "main.tex";
  const content = editor.getDoc();
  try {
    const result = await window.distillate.canvas.writeFile(wsId, cvId, entry, content);
    if (!result.ok) {
      _setStatus(`Save failed: ${result.error}`, "error");
      return false;
    }
    _current.lastSavedContent = content;
    _current.dirty = false;
    _bufferCache.set(_bufKey(wsId, cvId), { content, dirty: false });
    return true;
  } catch (err) {
    _setStatus(`Save failed: ${err.message}`, "error");
    return false;
  }
}

/** Save the buffer, then delegate to the type handler's onAfterSave. */
async function _saveAndRefresh() {
  if (!_current) return;
  const saveBtn = document.getElementById("canvas-inline-save");
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Saving…"; }
  _setStatus("Saving…");

  const ok = await _saveCurrentFile();
  if (!ok) {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "Save"; }
    return;
  }

  try {
    await _handlerFor(_current.canvas?.type).onAfterSave();
  } catch (err) {
    _setStatus(`Refresh failed: ${err.message}`, "error");
  }
  if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "Save"; }
}

// ---- LaTeX-specific compile + PDF render ---------------------------------

async function _compileLatex() {
  if (!_current) return;
  const { wsId, cvId } = _current;
  if (!window.distillate?.tectonic) return;
  if (_current.compileInFlight) return;

  // First-run: install Tectonic if missing.
  const status = await window.distillate.tectonic.status();
  if (!status.installed) {
    const ok = await _runInstallFlow();
    if (!ok) return;
  }

  _current.compileInFlight = true;
  _setStatus("Compiling…");

  try {
    const result = await window.distillate.tectonic.compile(wsId, cvId);
    if (result.ok) {
      _setStatus(`Built in ${(result.durationMs / 1000).toFixed(1)}s`, "success");
      _renderErrorPanel(result.errors || []);
      await _renderLatexPdf();
    } else {
      const n = (result.errors || []).filter((e) => e.severity === "error").length;
      _setStatus(`Build failed · ${n} error${n === 1 ? "" : "s"}`, "error");
      _renderErrorPanel(result.errors || []);
    }
  } catch (err) {
    _setStatus(`Compile error: ${err.message}`, "error");
  } finally {
    if (_current) _current.compileInFlight = false;
  }
}

async function _runInstallFlow() {
  const modal = document.getElementById("canvas-inline-install-modal");
  const fill = document.getElementById("canvas-inline-install-fill");
  const msg = document.getElementById("canvas-inline-install-msg");
  modal?.classList.remove("hidden");

  const progressHandler = (p) => {
    if (fill && typeof p.pct === "number") fill.style.width = `${p.pct}%`;
    if (msg && p.msg) msg.textContent = p.msg;
  };
  window.distillate.tectonic.onInstallProgress(progressHandler);

  try {
    const result = await window.distillate.tectonic.install();
    if (!result.ok) {
      if (msg) msg.textContent = `Install failed: ${result.error}`;
      setTimeout(() => modal?.classList.add("hidden"), 3500);
      _setStatus("Tectonic install failed", "error");
      return false;
    }
    modal?.classList.add("hidden");
    return true;
  } catch (err) {
    if (msg) msg.textContent = `Install failed: ${err.message}`;
    setTimeout(() => modal?.classList.add("hidden"), 3500);
    return false;
  }
}

async function _renderLatexPdf() {
  if (!_current) return;
  const { wsId, cvId } = _current;
  const empty = document.getElementById("canvas-inline-preview-empty");
  const body = document.getElementById("canvas-inline-preview-body");
  if (!body) return;

  try {
    const result = await window.distillate.canvas.readPdf(wsId, cvId);
    if (!result.ok) {
      body.innerHTML = "";
      empty?.classList.remove("hidden");
      return;
    }
    empty?.classList.add("hidden");

    const pdfjs = await _loadPdfJs();
    const bytes = result.bytes instanceof Uint8Array
      ? result.bytes
      : new Uint8Array(result.bytes);
    const loadingTask = pdfjs.getDocument({ data: bytes });
    const pdf = await loadingTask.promise;

    const pane = body.parentElement;
    const savedScroll = pane?.scrollTop || 0;

    body.innerHTML = "";
    body.classList.add("pdf-canvas");
    const devicePixelRatio = window.devicePixelRatio || 1;
    for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
      const page = await pdf.getPage(pageNum);
      const paneWidth = (pane?.clientWidth || 800) - 40;
      const viewport1 = page.getViewport({ scale: 1 });
      const scale = Math.min(1.5, paneWidth / viewport1.width);
      const viewport = page.getViewport({ scale: scale * devicePixelRatio });

      const c = document.createElement("canvas");
      c.width = viewport.width;
      c.height = viewport.height;
      c.style.width = `${viewport.width / devicePixelRatio}px`;
      c.style.height = `${viewport.height / devicePixelRatio}px`;
      body.appendChild(c);

      const ctx = c.getContext("2d");
      await page.render({ canvasContext: ctx, viewport }).promise;
    }

    if (pane) pane.scrollTop = savedScroll;
  } catch (err) {
    console.error("[canvas] pdf render failed:", err);
    _setStatus(`PDF render failed: ${err.message}`, "error");
  }
}

// ---- Markdown-specific preview -------------------------------------------

function _renderMarkdownPreview() {
  if (!_current?.editor) return;
  const body = document.getElementById("canvas-inline-preview-body");
  const empty = document.getElementById("canvas-inline-preview-empty");
  if (!body) return;
  const md = _current.editor.getDoc();
  if (!md || !md.trim()) {
    body.innerHTML = "";
    empty?.classList.remove("hidden");
    return;
  }
  empty?.classList.add("hidden");
  body.classList.remove("pdf-canvas");
  body.classList.add("markdown-preview", "markdown-body");
  try {
    body.innerHTML = window.markedParse ? window.markedParse(md) : _escapeHtml(md);
  } catch (err) {
    body.innerHTML = `<pre>${_escapeHtml(String(err))}</pre>`;
  }
}

// ---- Error panel ---------------------------------------------------------

function _renderErrorPanel(errors) {
  const panel = document.getElementById("canvas-inline-error-panel");
  if (!panel) return;
  if (!errors || errors.length === 0) {
    panel.innerHTML = "";
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  panel.innerHTML = errors.map((e, i) => `
    <div class="canvas-error-row severity-${e.severity || "error"}" data-line="${e.line || 0}" data-index="${i}">
      <span class="canvas-error-line">${e.line ? `l.${e.line}` : ""}</span>
      <span class="canvas-error-msg">${_escapeHtml(e.message || "")}</span>
    </div>
  `).join("");
  panel.querySelectorAll(".canvas-error-row").forEach((row) => {
    row.addEventListener("click", () => {
      const line = parseInt(row.dataset.line, 10);
      if (line > 0 && _current?.editor) _current.editor.gotoLine(line);
    });
  });
}

// ---- Status helpers ------------------------------------------------------

function _escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function _setStatus(text, kind = "") {
  const el = document.getElementById("canvas-inline-status");
  if (!el) return;
  el.textContent = text || "";
  el.className = "canvas-status" + (kind ? " " + kind : "");
}

function _setStatusText() {
  return document.getElementById("canvas-inline-status")?.textContent || "";
}

// ---- View mode (Source / Split / Preview) --------------------------------

const _VALID_VIEW_MODES = new Set(["source", "split", "preview"]);

function _viewModeKey(wsId, cvId) {
  return `distillate.canvas.viewMode.${wsId}.${cvId}`;
}

function _loadViewMode(wsId, cvId) {
  try {
    const v = localStorage.getItem(_viewModeKey(wsId, cvId));
    return _VALID_VIEW_MODES.has(v) ? v : "split";
  } catch { return "split"; }
}

function _saveViewMode(wsId, cvId, mode) {
  try { localStorage.setItem(_viewModeKey(wsId, cvId), mode); } catch {}
}

function _applyViewMode(mode) {
  if (!_VALID_VIEW_MODES.has(mode)) mode = "split";
  const root = document.querySelector(".canvas-inline");
  if (!root) return;
  // If this canvas type has no preview, force source-only and hide the toggle.
  const hasPreview = _handlerFor(_current?.canvas?.type).hasPreview;
  if (!hasPreview) mode = "source";
  root.dataset.viewMode = mode;
  root.querySelectorAll(".canvas-view-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === mode);
  });
  // Give the preview a chance to refresh when coming back into view.
  if (mode !== "source" && _current) {
    _handlerFor(_current.canvas?.type).refreshPreview?.();
  }
}

function _wireViewToggle() {
  const root = document.querySelector(".canvas-inline");
  if (!root || !_current) return;
  root.querySelectorAll(".canvas-view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.view;
      if (!_VALID_VIEW_MODES.has(mode)) return;
      _applyViewMode(mode);
      _saveViewMode(_current.wsId, _current.cvId, mode);
    });
  });
}

// ---- Resize handle between editor and preview panes ---------------------

function _splitRatioKey(wsId, cvId) {
  return `distillate.canvas.splitRatio.${wsId}.${cvId}`;
}

function _applySavedSplitRatio(wsId, cvId) {
  let ratio = null;
  try {
    const raw = localStorage.getItem(_splitRatioKey(wsId, cvId));
    if (raw) ratio = parseFloat(raw);
  } catch {}
  if (!ratio || isNaN(ratio) || ratio < 0.15 || ratio > 0.85) return;
  const editorPane = document.querySelector(".canvas-inline .canvas-inline-editor-pane");
  const previewPane = document.querySelector(".canvas-inline .canvas-inline-preview-pane");
  if (!editorPane || !previewPane) return;
  editorPane.style.flex = `${ratio} 1 0`;
  previewPane.style.flex = `${1 - ratio} 1 0`;
}

function _wireInlineResize(wsId, cvId) {
  const handle = document.getElementById("canvas-inline-resize");
  const split = document.querySelector(".canvas-inline .canvas-inline-split");
  const editorPane = document.querySelector(".canvas-inline .canvas-inline-editor-pane");
  const previewPane = document.querySelector(".canvas-inline .canvas-inline-preview-pane");
  if (!handle || !split || !editorPane || !previewPane) return;

  const onMouseDown = (e) => {
    e.preventDefault();
    const rect = split.getBoundingClientRect();
    const totalW = rect.width;
    if (totalW <= 0) return;
    handle.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMove = (ev) => {
      const x = ev.clientX - rect.left;
      let ratio = x / totalW;
      ratio = Math.max(0.15, Math.min(0.85, ratio));
      editorPane.style.flex = `${ratio} 1 0`;
      previewPane.style.flex = `${1 - ratio} 1 0`;
    };

    const onUp = () => {
      handle.classList.remove("dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      const finalRatio = editorPane.offsetWidth / (editorPane.offsetWidth + previewPane.offsetWidth);
      try { localStorage.setItem(_splitRatioKey(wsId, cvId), String(finalRatio)); } catch {}
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  handle.addEventListener("mousedown", onMouseDown);
}

// ---- Bottom agent panel --------------------------------------------------

function _wireAgentPanel() {
  document.getElementById("canvas-agent-launch")?.addEventListener("click", () => {
    _launchAgent();
  });
  document.getElementById("canvas-agent-toggle")?.addEventListener("click", () => {
    const panel = document.getElementById("canvas-inline-bottom");
    if (!panel) return;
    panel.classList.toggle("collapsed");
    const chevron = document.getElementById("canvas-agent-toggle");
    if (chevron) chevron.innerHTML = panel.classList.contains("collapsed") ? "&#x25B2;" : "&#x25BC;";
  });
}

async function _setupAgentPanel() {
  if (!_current) return;
  const { wsId, canvas } = _current;
  const sessionId = canvas?.session_id || "";
  if (!sessionId) {
    _setAgentStatus("Not running");
    return;
  }

  let session = null;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${wsId}`);
    const data = await resp.json();
    if (data.success) {
      session = (data.workspace.sessions || []).find(
        (s) => s.id === sessionId && s.status === "running"
      );
    }
  } catch {}

  if (!session) {
    _setAgentStatus("Not running");
    return;
  }

  await _attachAgentTerminal(wsId, session.id, session.tmux_name);
}

async function _launchAgent() {
  if (!_current) return;
  const { wsId, cvId } = _current;
  _setAgentStatus("Launching…");
  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${wsId}/canvases/${cvId}/sessions`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }
    );
    const data = await resp.json();
    if (!data.success) {
      _setAgentStatus(`Launch failed: ${data.error || "unknown"}`);
      return;
    }
    if (_current?.canvas) _current.canvas.session_id = data.session_id;
    // Give tmux a moment to spin up Claude Code before attaching.
    setTimeout(() => {
      _attachAgentTerminal(wsId, data.session_id, data.tmux_name);
    }, 500);
  } catch (err) {
    _setAgentStatus(`Launch error: ${err.message}`);
  }
}

async function _attachAgentTerminal(wsId, sessionId, tmuxName) {
  if (!_current) return;
  if (!window.xtermBridge || !window.nicolas) return;
  if (_current.agentAttached && _current.agentSessionId === sessionId) return;

  // Mark canvas terminal as active — prevents the main terminal code
  // (ensureTerminalReady / attachToTerminalSession in layout.js) from
  // re-initializing xtermBridge while we own it.
  window._canvasTermActive = true;

  try { window.xtermBridge.dispose(); } catch {}
  if (typeof terminalInitialized !== "undefined") terminalInitialized = false;
  if (typeof currentTerminalProject !== "undefined") currentTerminalProject = null;
  if (typeof currentTerminalSession !== "undefined") currentTerminalSession = null;
  _termReadyPromise = null;

  // Cleanup any existing canvas ResizeObserver before attaching new one.
  if (_current._canvasTermResizeObs) {
    try { _current._canvasTermResizeObs.disconnect(); } catch {}
    _current._canvasTermResizeObs = null;
  }
  if (_current._canvasTermResizeHandle) {
    try { _current._canvasTermResizeHandle.removeEventListener("mouseup", _current._canvasTermResizeHandler); } catch {}
    _current._canvasTermResizeHandler = null;
    _current._canvasTermResizeHandle = null;
  }
  if (_current._canvasTermResizeTimer) {
    try { clearTimeout(_current._canvasTermResizeTimer); } catch {}
    _current._canvasTermResizeTimer = null;
  }
  if (_current._canvasTermFitFrame) {
    try { cancelAnimationFrame(_current._canvasTermFitFrame); } catch {}
    _current._canvasTermFitFrame = null;
  }

  try { await document.fonts.ready; } catch {}

  document.getElementById("canvas-agent-empty")?.classList.add("hidden");
  const container = document.getElementById("xterm-canvas-bottom");
  if (!container) return;
  container.classList.remove("hidden");

  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

  const ok = window.xtermBridge.init("xterm-canvas-bottom");
  if (!ok) {
    _setAgentStatus("Terminal init failed");
    return;
  }

  window.xtermBridge.onData((data) => {
    if (currentTerminalProject && window.nicolas) {
      window.nicolas.terminalInput(currentTerminalProject, data);
    }
  });

  // Install the file-link provider so users can click any filename the
  // agent mentions to open/focus it as a canvas.
  if (window.xtermBridge.registerFileLinkProvider) {
    window.xtermBridge.registerFileLinkProvider((filename) => {
      _onTerminalFileClick(filename);
    });
  }

  if (typeof currentTerminalProject !== "undefined") currentTerminalProject = wsId;
  if (typeof currentTerminalSession !== "undefined") currentTerminalSession = tmuxName;
  _current.agentAttached = true;
  _current.agentSessionId = sessionId;
  _current.agentTmuxName = tmuxName;

  window.xtermBridge.fit();
  const dims = window.xtermBridge.getDimensions();
  const result = await window.nicolas.terminalAttach(wsId, tmuxName, dims.cols, dims.rows);
  if (result && !result.ok) {
    _setAgentStatus(`Attach failed: ${result.reason || "unknown"}`);
    _current.agentAttached = false;
    return;
  }

  _setAgentStatus("Running");

  // Setup ResizeObserver for the canvas terminal container so it refits when
  // the canvas inline-bottom panel is resized (window resize, split drag, etc).
  let _lastSentCanvasCols = 0;
  let _lastSentCanvasRows = 0;
  const resizeObserver = new ResizeObserver(() => {
    if (!window.xtermBridge || !_current?.agentAttached) return;
    // Debounce the fit calls to avoid measuring stale layout.
    // Fire fit after the browser paints, so layout is settled.
    if (_current._canvasTermResizeTimer) clearTimeout(_current._canvasTermResizeTimer);
    if (_current._canvasTermFitFrame) cancelAnimationFrame(_current._canvasTermFitFrame);
    _current._canvasTermFitFrame = requestAnimationFrame(() => {
      if (window.xtermBridge) window.xtermBridge.fit();
      _current._canvasTermResizeTimer = setTimeout(() => {
        if (!currentTerminalProject || !window.nicolas || !window.xtermBridge) return;
        window.xtermBridge.fit();
        const d = window.xtermBridge.getDimensions();
        // Only send terminalResize if dimensions actually changed.
        if (d.cols === _lastSentCanvasCols && d.rows === _lastSentCanvasRows) return;
        _lastSentCanvasCols = d.cols;
        _lastSentCanvasRows = d.rows;
        window.nicolas.terminalResize(currentTerminalProject, d.cols, d.rows);
      }, 150);
    });
  });
  resizeObserver.observe(container);
  _current._canvasTermResizeObs = resizeObserver;

  // Also watch for split handle drags (resize handle between editor and preview).
  // When the user finishes dragging, refit the terminal to the new layout.
  const splitHandle = document.getElementById("canvas-inline-resize");
  if (splitHandle) {
    const resizeHandler = () => {
      if (!window.xtermBridge || !_current?.agentAttached) return;
      setTimeout(() => {
        if (!window.xtermBridge || !_current?.agentAttached) return;
        window.xtermBridge.fit();
        const d = window.xtermBridge.getDimensions();
        if (d.cols === _lastSentCanvasCols && d.rows === _lastSentCanvasRows) return;
        _lastSentCanvasCols = d.cols;
        _lastSentCanvasRows = d.rows;
        window.nicolas.terminalResize(currentTerminalProject, d.cols, d.rows);
      }, 50);
    };
    splitHandle.addEventListener("mouseup", resizeHandler, { capture: true });
    _current._canvasTermResizeHandle = splitHandle;
    _current._canvasTermResizeHandler = resizeHandler;
  }

  setTimeout(() => {
    if (window.xtermBridge && _current?.agentAttached) {
      window.xtermBridge.fit();
      const d = window.xtermBridge.getDimensions();
      _lastSentCanvasCols = d.cols;
      _lastSentCanvasRows = d.rows;
      window.nicolas.terminalResize(wsId, d.cols, d.rows);
    }
  }, 300);
}

function _setAgentStatus(text) {
  const el = document.getElementById("canvas-agent-status");
  if (el) el.textContent = text || "";
}

// ---- Clickable filenames in the agent terminal ---------------------------

async function _onTerminalFileClick(filename) {
  if (!_current || !filename) return;
  const { wsId, canvas } = _current;
  const cwd = canvas?.dir || "";
  // Build absolute path. xterm already filtered to "file-like" tokens.
  let absPath = filename;
  if (!filename.startsWith("/")) {
    absPath = `${cwd}/${filename}`;
  }
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${wsId}/canvases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ import_path: absPath }),
    });
    const data = await resp.json();
    if (!data.ok) {
      if (typeof showToast === "function") showToast(data.error || "Could not open file", "error");
      return;
    }
    const newCanvasId = data.canvas.id;
    // Drill into the new canvas. Re-mounting replaces this editor cleanly.
    const container = _current.container;
    destroyCanvasEditor();
    await mountCanvasEditor(container, wsId, newCanvasId, {
      onBack: () => selectWorkspace(wsId),
    });
  } catch (err) {
    if (typeof showToast === "function") showToast(`Could not open file: ${err.message}`, "error");
  }
}

// ---- ⌘K inline edit modal ------------------------------------------------

function _wireInlineEditModal() {
  document.getElementById("canvas-inline-edit-cancel")?.addEventListener("click", _closeInlineEditModal);
  document.getElementById("canvas-inline-edit-submit")?.addEventListener("click", _submitInlineEdit);
  document.addEventListener("keydown", _inlineEditEscHandler);
}

function _inlineEditEscHandler(e) {
  if (e.key !== "Escape") return;
  const modal = document.getElementById("canvas-inline-edit-modal");
  if (modal && !modal.classList.contains("hidden")) {
    e.preventDefault();
    _closeInlineEditModal();
  }
}

function openInlineEditModal() {
  if (!_current?.editor) return;
  const view = _current.editor.view;
  const state = view.state;
  const sel = state.selection.main;
  const selText = state.doc.sliceString(sel.from, sel.to);

  if (!selText || selText.trim().length === 0) {
    _setStatus("Select text first, then press ⌘K", "error");
    setTimeout(() => _setStatus(""), 2000);
    return;
  }

  _current.inlineEditSelection = {
    from: sel.from,
    to: sel.to,
    text: selText,
    fromLine: state.doc.lineAt(sel.from).number,
    toLine: state.doc.lineAt(sel.to).number,
  };

  const preview = selText.length > 200 ? selText.slice(0, 200) + "…" : selText;
  const ctxEl = document.getElementById("canvas-inline-edit-context");
  if (ctxEl) ctxEl.textContent = preview;

  const modal = document.getElementById("canvas-inline-edit-modal");
  modal?.classList.remove("hidden");
  const input = document.getElementById("canvas-inline-edit-input");
  if (input) { input.value = ""; setTimeout(() => input.focus(), 50); }
  const statusEl = document.getElementById("canvas-inline-edit-status");
  if (statusEl) statusEl.textContent = "";
}

function _closeInlineEditModal() {
  const modal = document.getElementById("canvas-inline-edit-modal");
  modal?.classList.add("hidden");
  if (_current) _current.inlineEditSelection = null;
  _current?.editor?.focus?.();
}

async function _submitInlineEdit() {
  if (!_current) return;
  const { wsId, cvId, canvas } = _current;
  const selection = _current.inlineEditSelection;
  if (!selection) { _closeInlineEditModal(); return; }
  const input = document.getElementById("canvas-inline-edit-input");
  const instructions = (input?.value || "").trim();
  if (!instructions) return;

  const statusEl = document.getElementById("canvas-inline-edit-status");
  const submitBtn = document.getElementById("canvas-inline-edit-submit");
  if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = "Sending…"; }
  if (statusEl) statusEl.textContent = "Saving buffer…";

  const saved = await _saveCurrentFile();
  if (!saved) {
    if (statusEl) statusEl.textContent = "Save failed";
    if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Send to agent"; }
    return;
  }

  let sessionId = canvas?.session_id || "";
  let tmuxName = _current.agentTmuxName || "";
  if (!sessionId || !_current.agentAttached) {
    if (statusEl) statusEl.textContent = "Launching agent…";
    try {
      const resp = await fetch(
        `http://127.0.0.1:${serverPort}/workspaces/${wsId}/canvases/${cvId}/sessions`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }
      );
      const data = await resp.json();
      if (!data.success) {
        if (statusEl) statusEl.textContent = `Launch failed: ${data.error || "unknown"}`;
        if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Send to agent"; }
        return;
      }
      sessionId = data.session_id;
      tmuxName = data.tmux_name;
      if (_current.canvas) _current.canvas.session_id = sessionId;
      await new Promise((r) => setTimeout(r, 800));
      _attachAgentTerminal(wsId, sessionId, tmuxName);
    } catch (err) {
      if (statusEl) statusEl.textContent = `Launch error: ${err.message}`;
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Send to agent"; }
      return;
    }
  }

  const entry = canvas?.entry || "main.tex";
  const prompt =
    `In the file ${entry}, please edit lines ${selection.fromLine}-${selection.toLine}. ` +
    `The current text of that range is:\n\n` +
    `"""\n${selection.text}\n"""\n\n` +
    `Please modify it as follows: ${instructions}\n\n` +
    `Use your Edit tool to replace exactly the text above with your revision. ` +
    `Do not alter anything else in the file. ` +
    `Reply with a one-sentence confirmation once done.`;

  if (statusEl) statusEl.textContent = "Sending to agent…";

  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${wsId}/sessions/${sessionId}/inject-prompt`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }) }
    );
    const data = await resp.json();
    if (!data.success) {
      if (statusEl) statusEl.textContent = `Inject failed: ${data.error || "unknown"}`;
      if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Send to agent"; }
      return;
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = `Inject error: ${err.message}`;
    if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Send to agent"; }
    return;
  }

  if (statusEl) statusEl.textContent = "Agent is editing…";
  _setStatus("Agent editing…");
  setTimeout(() => {
    _closeInlineEditModal();
    if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Send to agent"; }
  }, 400);
}

// ---- File watcher + hot reload -------------------------------------------

function _wireFileWatcher() {
  if (!window.distillate?.canvas || !_current) return;
  const { wsId, cvId } = _current;
  if (!window.__canvasFileChangedHandlerInstalled) {
    window.distillate.canvas.onFileChanged((change) => {
      if (!_current) return;
      if (change.wsId !== _current.wsId || change.cvId !== _current.cvId) return;
      _handleFileChanged(change);
    });
    window.__canvasFileChangedHandlerInstalled = true;
  }
  window.distillate.canvas.startWatch(wsId, cvId).catch(() => {});
}

async function _handleFileChanged(change) {
  if (!_current?.editor) return;
  const { wsId, cvId } = _current;
  const entry = _current.canvas?.entry || "main.tex";
  let result;
  try {
    result = await window.distillate.canvas.readFile(wsId, cvId, entry);
  } catch { return; }
  if (!result?.ok) return;
  const newContent = result.content || "";
  if (newContent === _current.lastSavedContent) return;

  if (_current.dirty) {
    _showReloadBanner(newContent);
    return;
  }

  _applyFileContent(newContent);
  _setStatus("Updated by agent", "success");
  setTimeout(() => _setStatus(""), 2500);
  // For markdown, refresh the live preview. For LaTeX, leave the PDF
  // stale until the user hits Save (per the "only on save" preference).
  if (_current.canvas?.type === "markdown") _renderMarkdownPreview();
}

function _applyFileContent(newContent) {
  if (!_current?.editor) return;
  const view = _current.editor.view;
  const oldPos = view.state.selection.main.head;
  _current.editor.setDoc(newContent);
  _current.lastSavedContent = newContent;
  _current.dirty = false;
  _bufferCache.set(_bufKey(_current.wsId, _current.cvId), { content: newContent, dirty: false });
  try {
    const clamped = Math.min(oldPos, view.state.doc.length);
    view.dispatch({ selection: { anchor: clamped }, scrollIntoView: true });
  } catch {}
}

function _showReloadBanner(newContent) {
  const banner = document.getElementById("canvas-reload-banner");
  if (!banner) return;
  banner.classList.remove("hidden");
  banner.innerHTML = `
    <span>The agent updated this file. You have unsaved changes.</span>
    <button class="paper-action-btn" id="canvas-reload-accept">Reload</button>
    <button class="paper-action-btn" id="canvas-reload-dismiss">Keep mine</button>
  `;
  document.getElementById("canvas-reload-accept")?.addEventListener("click", () => {
    _applyFileContent(newContent);
    banner.classList.add("hidden");
  });
  document.getElementById("canvas-reload-dismiss")?.addEventListener("click", () => {
    banner.classList.add("hidden");
  });
}

// Expose for workspaces.js and canvas-editor.mjs
console.log("[canvas.js] about to export, mountCanvasEditor is", typeof mountCanvasEditor);
window.mountCanvasEditor = mountCanvasEditor;
window.destroyCanvasEditor = destroyCanvasEditor;
window.openInlineEditModal = openInlineEditModal;
console.log("[canvas.js] exports done, window.mountCanvasEditor is", typeof window.mountCanvasEditor);
