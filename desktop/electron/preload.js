const { contextBridge, ipcRenderer, clipboard, webUtils } = require("electron");
const { createPasteHandlers } = require("./paste-handlers");
const {
  createTerminalController,
  stripMouseModes,
  forceSelectionEnabled,
  disposeTerminalSafely,
} = require("./terminal-controller");
const { CombinedCircularList } = require("./combined-circular-list");
const {
  enterScrollbackState,
  exitScrollbackState,
} = require("./scrollback-state");

// Shared paste/drop handlers with dedupe guard across both Cmd+V keydown
// and native paste event entry points.
const _pasteCore = createPasteHandlers({
  clipboard,
  fs: require("fs"),
  os: require("os"),
  path: require("path"),
});

// Load highlight.js in Node context and expose to renderer
const hljs = require("highlight.js/lib/core");

// Load xterm in Node context (runs in preload world, shares DOM with renderer)
const { Terminal } = require("xterm");
const { FitAddon } = require("@xterm/addon-fit");
hljs.registerLanguage("python", require("highlight.js/lib/languages/python"));
hljs.registerLanguage("javascript", require("highlight.js/lib/languages/javascript"));
hljs.registerLanguage("typescript", require("highlight.js/lib/languages/typescript"));
hljs.registerLanguage("bash", require("highlight.js/lib/languages/bash"));
hljs.registerLanguage("shell", require("highlight.js/lib/languages/bash"));
hljs.registerLanguage("json", require("highlight.js/lib/languages/json"));
hljs.registerLanguage("markdown", require("highlight.js/lib/languages/markdown"));
hljs.registerLanguage("yaml", require("highlight.js/lib/languages/yaml"));
hljs.registerLanguage("xml", require("highlight.js/lib/languages/xml"));
hljs.registerLanguage("html", require("highlight.js/lib/languages/xml"));
hljs.registerLanguage("css", require("highlight.js/lib/languages/css"));
hljs.registerLanguage("sql", require("highlight.js/lib/languages/sql"));
hljs.registerLanguage("latex", require("highlight.js/lib/languages/latex"));
hljs.registerLanguage("tex", require("highlight.js/lib/languages/latex"));

contextBridge.exposeInMainWorld("nicolas", {
  // Server lifecycle
  onServerReady: (callback) =>
    ipcRenderer.on("server-ready", (_event, data) => callback(data)),
  onServerError: (callback) =>
    ipcRenderer.on("server-error", (_event, data) => callback(data)),
  onUpdateProgress: (callback) =>
    ipcRenderer.on("update-progress", (_event, data) => callback(data)),
  getServerPort: () => ipcRenderer.invoke("get-server-port"),

  // Navigation
  onDeepLink: (callback) =>
    ipcRenderer.on("deep-link", (_event, url) => callback(url)),
  onNewConversation: (callback) =>
    ipcRenderer.on("new-conversation", () => callback()),
  onOpenSettings: (callback) =>
    ipcRenderer.on("open-settings", () => callback()),

  // Settings
  getSettings: () => ipcRenderer.invoke("get-settings"),
  saveSettings: (settings) => ipcRenderer.invoke("save-settings", settings),

  // Shell
  openExternal: (url) => ipcRenderer.invoke("open-external", url),

  // Notifications
  notify: (title, body) => ipcRenderer.invoke("notify", title, body),

  // Dialogs
  selectDirectory: (title) => ipcRenderer.invoke("select-directory", title),

  // State export/import
  exportState: () => ipcRenderer.invoke("export-state"),
  importState: () => ipcRenderer.invoke("import-state"),

  // Terminal PTY
  terminalAttach: (projectId, sessionName, cols, rows) =>
    ipcRenderer.invoke("terminal:attach", { projectId, sessionName, cols, rows }),
  terminalInput: (projectId, data) =>
    ipcRenderer.send("terminal:input", { projectId, data }),
  terminalResize: (projectId, cols, rows) =>
    ipcRenderer.invoke("terminal:resize", { projectId, cols, rows }),
  terminalDetach: (projectId) =>
    ipcRenderer.invoke("terminal:detach", { projectId }),
  onTerminalData: (callback) =>
    ipcRenderer.on("terminal:data", (_event, payload) => callback(payload)),
  onTerminalExit: (callback) =>
    ipcRenderer.on("terminal:exit", (_event, payload) => callback(payload)),

  // Theme
  onThemeChange: (callback) =>
    ipcRenderer.on("theme-changed", (_event, isDark) => callback(isDark)),
  setTheme: (mode) => ipcRenderer.invoke("set-theme", mode),
  getTheme: () => ipcRenderer.invoke("get-theme"),

  // macOS menu bar tray icon — renderer pushes agent status + session details + summary
  updateTrayStatus: (status, sessions, summary) =>
    ipcRenderer.send("tray:status", { status, sessions, summary }),

  // Main sends this when the user clicks the tray icon / "Show Distillate"
  // and there's a waiting session — the renderer should navigate to it.
  onFocusWaitingSession: (callback) =>
    ipcRenderer.on("focus-waiting-session", (_event, target) => callback(target)),

  // Main sends this when the user clicks an auto-experiment in the tray menu.
  onFocusExperiment: (callback) =>
    ipcRenderer.on("focus-experiment", (_event, experimentId) => callback(experimentId)),

  // Main sends this after a PDF is imported via Dock icon drag-drop.
  onPaperImported: (callback) =>
    ipcRenderer.on("paper-imported", (_event, data) => callback(data)),

  // Cmd+K: focus Nicolas from any view
  onFocusNicolas: (callback) =>
    ipcRenderer.on("focus-nicolas", () => callback()),

  // Terminal bell detected (\x07) from Claude Code / agent — triggers amber state
  onBellDetected: (callback) =>
    ipcRenderer.on("distillate:bell-detected", () => callback()),
});

// Canvas editor bridge — file I/O + Tectonic compile + file watcher.
// All methods take (wsId, cvId) because a workspace may have multiple
// canvases. Kept separate from window.nicolas so the surface can evolve
// independently.
contextBridge.exposeInMainWorld("distillate", {
  canvas: {
    listFiles: (wsId, cvId) =>
      ipcRenderer.invoke("canvas:list-files", { wsId, cvId }),
    readFile: (wsId, cvId, relPath) =>
      ipcRenderer.invoke("canvas:read-file", { wsId, cvId, relPath }),
    writeFile: (wsId, cvId, relPath, content) =>
      ipcRenderer.invoke("canvas:write-file", { wsId, cvId, relPath, content }),
    readPdf: (wsId, cvId) =>
      ipcRenderer.invoke("canvas:read-pdf", { wsId, cvId }),
    invalidateDirCache: (wsId, cvId) =>
      ipcRenderer.invoke("canvas:invalidate-dir-cache", { wsId, cvId }),
    startWatch: (wsId, cvId) =>
      ipcRenderer.invoke("canvas:start-watch", { wsId, cvId }),
    stopWatch: (wsId, cvId) =>
      ipcRenderer.send("canvas:stop-watch", { wsId, cvId }),
    onFileChanged: (callback) =>
      ipcRenderer.on("canvas:file-changed", (_e, change) => callback(change)),
  },
  tectonic: {
    status: () => ipcRenderer.invoke("tectonic:status"),
    install: () => ipcRenderer.invoke("tectonic:install"),
    compile: (wsId, cvId) =>
      ipcRenderer.invoke("tectonic:compile", { wsId, cvId }),
    abort: (wsId, cvId) =>
      ipcRenderer.send("tectonic:abort", { wsId, cvId }),
    onInstallProgress: (callback) =>
      ipcRenderer.on("tectonic:install-progress", (_e, p) => callback(p)),
  },
  powerMetrics: {
    start: () => ipcRenderer.invoke("powermetrics:start"),
    stop: () => ipcRenderer.invoke("powermetrics:stop"),
    onSample: (callback) =>
      ipcRenderer.on("powermetrics:sample", (_e, s) => callback(s)),
    onUnavailable: (callback) =>
      ipcRenderer.on("powermetrics:unavailable", (_e, info) => callback(info)),
  },
});

// Load Nerd Font by injecting @font-face into the shared DOM.
// Uses the server URL (same origin) since file:// is blocked by CSP.
{
  const _style = document.createElement("style");
  _style.textContent = `
    @font-face {
      font-family: 'MesloLGS Nerd Font Mono';
      src: url('/ui/fonts/MesloLGSNerdFontMono-Regular.ttf') format('truetype');
      font-weight: normal; font-style: normal;
    }
    @font-face {
      font-family: 'MesloLGS Nerd Font Mono';
      src: url('/ui/fonts/MesloLGSNerdFontMono-Bold.ttf') format('truetype');
      font-weight: bold; font-style: normal;
    }
  `;
  if (document.head) {
    document.head.appendChild(_style);
  } else {
    document.addEventListener("DOMContentLoaded", () => document.head.appendChild(_style));
  }
}

// Expose xterm terminal bridge (Terminal instance lives in preload world, renders to shared DOM)
let _term = null;
let _fitAddon = null;
let _writeBuffered = null;
let _currentTmuxName = null;   // set by layout.js via xtermBridge.setTmuxName()
let _linkProviderDisposable = null;
let _scrollSelectionDisposable = null; // onSelectionChange listener for scroll accumulator
let _scrollbackListenerTimer = null;   // 50ms timer for mousedown/selChange registration
let _origDragScroll = null;            // original _dragScroll before wrapping

// ── Scroll-back mode state ──────────────────────────────────────────
//
// On-demand buffer switch: let tmux use alt-screen normally (stable
// viewport).  When the user drags a selection to the edge, temporarily
// swap to the normal buffer with pre-captured scrollback.  On mouseup,
// swap back and let tmux repaint.
//
// Alt-screen sequences MUST pass through to xterm unmodified.
let _scrollbackMode = false;         // true while in scroll-back view
let _capturedContent = null;         // raw string from tmux capture-pane (eagerly refreshed)
let _scrollbackWriteBuffer = [];     // PTY data buffered during scroll-back mode
let _scrollbackWriteBufferBytes = 0; // running byte count for overflow guard
const _SCROLLBACK_BUFFER_MAX_BYTES = 4 * 1024 * 1024; // 4 MB — if we exceed
// this while stuck in scroll-back, something is wrong: force-exit and drain
// rather than let a chatty agent pin an unbounded string array.
let _scrollbackCaptureTimer = null;  // periodic refresh interval
let _writeBypassUntil = 0;          // timestamp: bypass writeBuffered until this time
let _scrollSelectionAccum = "";     // accumulated selection text during wheel-scroll
let _sessionGeneration = 0;         // monotonic counter — incremented on setTmuxName, checked by async IPC
let _savedAltLines = null;           // original altBuffer.lines CircularList
let _savedAltYbase = 0;             // original altBuffer.ybase (always 0)
let _savedAltYdisp = 0;             // original altBuffer.ydisp (always 0)
let _savedAltHasScrollback = false;  // original altBuffer._hasScrollback (always false)
// Set by clear() and startAttachBurst() to cancel any in-flight async
// _enterScrollbackMode. Without this, a pending capture-pane IPC from a
// drag-to-edge on session A can resolve AFTER session B's burst starts,
// setting _scrollbackMode=true and routing burst data to the wrong buffer.
let _cancelScrollbackEntry = false;

// ── Attach-burst buffering ──────────────────────────────────────────
//
// `tmux attach-session` redraws the pane as many small PTY chunks
// (cursor moves + style codes + per-cell text). xterm paints each
// chunk on arrival, so the alt-screen fills in row-by-row — visually
// identical to "scrolling through history."
//
// Fix: when startAttachBurst() arms the buffer, every write() chunk is
// accumulated. After 150 ms of idle (burst complete) or a 1.5 s hard
// cap (steadily-streaming agent), the accumulated bytes are written
// to xterm in one synchronous _term.write() call so the final pane
// state lands in a single frame.
let _attachBurstActive = false;
let _attachBurstBuffer = [];
let _attachBurstBufferBytes = 0;
let _attachBurstIdleTimer = null;
let _attachBurstHardTimer = null;
const ATTACH_BURST_IDLE_MS = 150;
const ATTACH_BURST_MAX_MS = 1500;
const ATTACH_BURST_MAX_BYTES = 4 * 1024 * 1024;

function _flushAttachBurst() {
  if (_attachBurstIdleTimer) { clearTimeout(_attachBurstIdleTimer); _attachBurstIdleTimer = null; }
  if (_attachBurstHardTimer) { clearTimeout(_attachBurstHardTimer); _attachBurstHardTimer = null; }
  _attachBurstActive = false;
  // Burst settled — allow drag-scroll on the newly attached session again.
  _cancelScrollbackEntry = false;
  if (_attachBurstBuffer.length === 0 || !_term) { _attachBurstBuffer = []; _attachBurstBufferBytes = 0; return; }
  const merged = _attachBurstBuffer.join("");
  _attachBurstBuffer = [];
  _attachBurstBufferBytes = 0;
  _term.write(merged);
}

function _bumpAttachBurstIdle() {
  if (_attachBurstIdleTimer) clearTimeout(_attachBurstIdleTimer);
  _attachBurstIdleTimer = setTimeout(_flushAttachBurst, ATTACH_BURST_IDLE_MS);
}

// Fit the terminal to its mount container in a single resize pass.
// Measures the parent container directly (like FitAddon does) and applies
// a 2px safety margin to avoid right-edge clipping — without calling
// _fitAddon.fit() first, which would trigger an intermediate resize and
// make the 300ms post-attach column check non-deterministic.
function _safeFit() {
  if (!_fitAddon || !_term) return;
  const el = _term.element;
  const cell = _term._core?._renderService?.dimensions?.css?.cell;
  if (!el || !el.parentElement || !cell || !cell.width || !cell.height) {
    _fitAddon.fit();
    return;
  }
  const parentEl = el.parentElement;
  const parentStyle = getComputedStyle(parentEl);
  const parentWidth = parseFloat(parentStyle.width) || parentEl.clientWidth;
  const parentHeight = parseFloat(parentStyle.height) || parentEl.clientHeight;
  // If the container has zero size (e.g. parent tab is display:none while the
  // user is on a different editor tab), bail without resizing. Resizing to 2
  // cols would send terminalResize(2) to the PTY and reflow all tmux output.
  if (parentWidth < 20 || parentHeight < 10) return;
  const cs = getComputedStyle(el);
  const padLeft = parseFloat(cs.paddingLeft) || 0;
  const padRight = parseFloat(cs.paddingRight) || 0;
  const SAFETY = 2;
  const cols = Math.max(2, Math.floor((parentWidth - padLeft - padRight - SAFETY) / cell.width));
  const rows = Math.max(1, Math.floor(parentHeight / cell.height));
  if (cols < 10 || rows < 3) return;
  if (cols !== _term.cols || rows !== _term.rows) _term.resize(cols, rows);
}

/**
 * Enter scroll-back mode via CombinedCircularList proxy.
 *
 * The alt buffer stays active the entire time.  We:
 *   1. Populate the normal buffer with captured tmux scrollback (invisible)
 *   2. Replace altBuffer.lines with a proxy that concatenates normal + alt
 *   3. Set ybase/ydisp so xterm thinks the alt buffer has scrollback
 *
 * NO buffer switching.  NO activateNormalBuffer/activateAltBuffer.
 * NO onBufferActivate event.  NO escape sequence modification.
 * All in one synchronous JS turn — no render cycle fires mid-operation.
 */
let _enteringScrollbackMode = false;

async function _enterScrollbackMode() {
  if (_scrollbackMode || _enteringScrollbackMode || !_term || !_currentTmuxName) return;
  _enteringScrollbackMode = true;
  const gen = _sessionGeneration;
  try {
    const data = await ipcRenderer.invoke("terminal:capture-pane", {
      tmuxName: _currentTmuxName,
      lines: 5000,
    });
    if (!data?.ok || !data.content) return;
    // Race checks: session may have switched while the IPC was in-flight.
    // _cancelScrollbackEntry is set by clear() and startAttachBurst() to
    // prevent a stale IPC response from hijacking the new session's burst.
    // _sessionGeneration check is belt-and-suspenders: monotonic, timing-proof.
    if (_scrollbackMode || !_term || _cancelScrollbackEntry || gen !== _sessionGeneration) return;

    let capturedLines = data.content.split("\n");
    // Remove trailing empty line from split (terminal output usually ends with \n)
    if (capturedLines.length > 0 && capturedLines[capturedLines.length - 1] === "") {
      capturedLines.pop();
    }
    if (capturedLines.length === 0) return;
    const scrollPos = data.scrollPos || 0;
    _capturedContent = data.content; // refresh cache

    const core = _term._core;
    const altBuf = core?.buffers?._alt || core?.buffers?.alt;
    const selSvc = core?._selectionService;
    if (!altBuf || !selSvc) return;

    const saved = enterScrollbackState(
      altBuf, capturedLines, scrollPos,
      _term.cols, _term.rows, selSvc?._model
    );
    _savedAltLines = saved.lines;
    _savedAltYbase = saved.ybase;
    _savedAltYdisp = saved.ydisp;
    _savedAltHasScrollback = saved.hasScrollback;

    // Tell xterm's viewport about the new buffer dimensions.
    // In alt-screen mode the scroll area has height 0 (bufferLength = rows,
    // so (rows-rows)*cellH = 0). After installing the CombinedCircularList the
    // buffer is 5000+ lines. If we only update _lastRecordedBufferLength without
    // also updating _scrollArea.style.height, syncScrollArea() sees a matching
    // cached length and skips the height update — leaving height=0. Then any
    // scrollLines() call tries to set scrollTop = ydisp*cellH but the browser
    // clamps it to 0, fires a DOM scroll event, and the viewport reads scrollTop=0
    // → resets ydisp=0, snapping the view to the oldest history.
    const viewport = core.viewport || core._viewport;
    if (viewport) {
      const cellH = core._renderService?.dimensions?.css?.cell?.height;
      if (cellH && viewport._scrollArea) {
        // xterm formula: lines.length * cellH — the scroll container has
        // clientHeight = rows * cellH, so the effective scrollable range is
        // (lines.length - rows) * cellH = ybase * cellH, exactly right.
        // Using (lines.length - rows) here would be off by rows, clamping
        // scrollTop for all ydisp > ybase - rows.
        viewport._scrollArea.style.height = `${altBuf.lines.length * cellH}px`;
      }
      viewport._lastRecordedBufferLength = altBuf.lines.length;
    }

    _scrollbackMode = true;

    // Force an immediate full redraw of the viewport against the proxy buffer.
    // Without this, the first frame after entering scrollback overlays the
    // new CombinedCircularList rows on top of the stale alt-screen canvas —
    // the DOM renderer only marks rows dirty on scroll, and entering
    // scrollback flipped the buffer *under* it without moving ydisp, so no
    // row is considered dirty. Result: scrambled glyphs until the next
    // scrollLines() tick. Refreshing here forces every visible row to be
    // re-read from `altBuf.lines.get(ydisp + rowY)`.
    _term.refresh(0, _term.rows - 1);

    // Wrap _dragScroll to force a full viewport refresh after each tick.
    // The canvas renderer's scroll-optimization path fails silently with
    // our proxy — calling _term.refresh() triggers a full redraw.
    if (selSvc._dragScroll && !selSvc._dragScroll.__wrapped) {
      _origDragScroll = selSvc._dragScroll.bind(selSvc);
      const wrapped = function() {
        const prevYdisp = _term._core?.buffer?.ydisp;
        _origDragScroll();
        const newYdisp = _term._core?.buffer?.ydisp;
        if (prevYdisp !== newYdisp && _term) {
          _term.refresh(0, _term.rows - 1);
        }
      };
      wrapped.__wrapped = true;
      selSvc._dragScroll = wrapped;
    }

    // Exit on mousedown outside the terminal (click elsewhere = dismiss).
    // Keypress exit is handled by the `d` listener registered in wheelScroll.
    // onSelectionChange is NOT used as an exit trigger — it fires spuriously
    // when scrollLines() resets xterm's selection model internally, which
    // caused immediate scrollback exit on every scroll tick.
    if (_scrollbackListenerTimer) clearTimeout(_scrollbackListenerTimer);
    _scrollbackListenerTimer = setTimeout(() => {
      _scrollbackListenerTimer = null;
      if (!_scrollbackMode) return;
      const onMouseDown = (e) => {
        const xtermEl = document.getElementById("xterm-container");
        if (xtermEl && xtermEl.contains(e.target)) return;
        _exitScrollbackMode();
      };
      document.addEventListener("mousedown", onMouseDown, { capture: true, once: true });
    }, 50);

  } finally {
    _enteringScrollbackMode = false;
  }
}

/**
 * Exit scroll-back mode: remove the proxy, restore alt buffer state,
 * clear normal buffer, flush buffered PTY data.
 *
 * All synchronous — the terminal returns to exactly the state it was
 * in before entering scroll-back mode.
 */
function _exitScrollbackMode() {
  if (!_scrollbackMode || !_term) return;

  // Cancel pending listener registration from _enterScrollbackMode
  if (_scrollbackListenerTimer) {
    clearTimeout(_scrollbackListenerTimer);
    _scrollbackListenerTimer = null;
  }

  const core = _term._core;
  const buffers = core?.buffers;
  if (!buffers) return;

  const altBuf = buffers._alt || buffers.alt;
  const normalBuf = buffers._normal || buffers.normal;
  const selSvc = core?._selectionService;

  _term.clearSelection();

  // Restore alt buffer to pre-scrollback state
  if (_savedAltLines) altBuf.lines = _savedAltLines;
  altBuf.ybase = _savedAltYbase;
  altBuf.ydisp = _savedAltYdisp;
  altBuf._hasScrollback = _savedAltHasScrollback;

  // Restore viewport scroll area to match the original buffer (rows lines → height 0)
  const viewport = core.viewport || core._viewport;
  if (viewport) {
    if (viewport._scrollArea) viewport._scrollArea.style.height = "0px";
    viewport._lastRecordedBufferLength = _savedAltLines ? _savedAltLines.length : _term.rows;
  }

  // Restore original _dragScroll (unwrap the refresh-forcing wrapper)
  if (_origDragScroll && selSvc) {
    selSvc._dragScroll = _origDragScroll;
    _origDragScroll = null;
  }

  if (normalBuf.clear) normalBuf.clear();

  _savedAltLines = null;
  _savedAltYbase = 0;
  _savedAltYdisp = 0;
  _savedAltHasScrollback = false;
  _scrollbackMode = false;

  // Flush PTY data that arrived during scroll-back mode.
  // Write directly — selection was already cleared above (line 419), and
  // routing through _writeBuffered could re-buffer if a selection race occurs.
  if (_scrollbackWriteBuffer.length > 0) {
    const data = _scrollbackWriteBuffer.join("");
    _scrollbackWriteBuffer.length = 0;
    _scrollbackWriteBufferBytes = 0;
    _term.write(data);
  } else {
    _scrollbackWriteBufferBytes = 0;
  }

  _term.refresh(0, _term.rows - 1);
}

// Terminal color architecture (xterm.js v5, canvas renderer):
//  1. Theme object — controls ANSI colors 0-15, foreground, background, cursor
//  2. _apply256Palette() — patches internal color manager for 256-color indices 16-255
//  3. minimumContrastRatio — enforces contrast at render time for ALL color types,
//     including 24-bit true color (SGR 38;2;R;G;B) which bypasses both 1 and 2.
// CSS class selectors (.xterm-rows span) have NO EFFECT — canvas, not DOM.
const _darkTermTheme = {
  background: "rgba(11, 10, 20, 0.65)", foreground: "#e0dce8", cursor: "#8b7cf6",
  selectionBackground: "rgba(139, 124, 246, 0.35)",
  black: "#0c0a14", red: "#e05555", green: "#5eae76", yellow: "#e8c06a",
  blue: "#8fb0e0", magenta: "#b89cf0", cyan: "#60b8b0", white: "#e0dce8",
  // brightBlack lifted from dim violet-grey (#7a7298) → light violet pastel.
  // Claude Code uses ANSI 8 (brightBlack) via `chalk.gray()` for its
  // user-input block fg + all "dim" chrome. At #7a7298 on top of the
  // dark-grey box bg (256-color 238) the pair landed near 2:1 contrast, and
  // once minimumContrastRatio kicked in it would *reduce* fg luminance to
  // hit the ratio against the lighter grey — producing the dark-on-dark
  // the user sees. Setting brightBlack to a pale lavender solves it at the
  // source: fg is lighter than bg, contrast is high on its own, and the
  // colour matches the brand accent family.
  brightBlack: "#c9b5f0", brightRed: "#f07070", brightGreen: "#70c890",
  brightYellow: "#f0d080", brightBlue: "#a0c4f0", brightMagenta: "#d0b0f8",
  brightCyan: "#70d0c8", brightWhite: "#f0ecf8",
};
const _lightTermTheme = {
  // Pastel palette — reads well as BG (diff rows, input bar). The
  // minimumContrastRatio below auto-darkens these same colors when
  // xterm uses them as FG so foreground text stays readable.
  background: "rgba(255, 255, 255, 0.95)", foreground: "#0a0a14", cursor: "#6356d4",
  selectionBackground: "rgba(99, 86, 212, 0.12)",
  black: "#0a0a14", red: "#fca5a5", green: "#86efac", yellow: "#fde68a",
  blue: "#93c5fd", magenta: "#c4b5fd", cyan: "#67e8f9", white: "#f4f2f8",
  brightBlack: "#6b7280", brightRed: "#fecaca", brightGreen: "#bbf7d0",
  brightYellow: "#fef3c7", brightBlue: "#dbeafe", brightMagenta: "#e9d5ff",
  brightCyan: "#cffafe", brightWhite: "#ffffff",
};

// 256-color palette overrides for Claude Code's UI elements.
// Claude uses specific indices for UI chrome, file paths, accents, and
// code block backgrounds.  These are tuned for brand fit and readability
// on our dark/light terminal backgrounds.
//
// Grayscale indices 237–246 are critical: Claude uses them for tool-result
// panels and dim text.  Gaps between overrides (e.g., 238 not overridden)
// fall back to xterm's default palette, which is nearly invisible on our
// semi-transparent backgrounds.  We now cover the full range.
const _dark256 = {
  16: [30,28,48], 37: [64,184,176], 114: [94,174,118], 147: [167,139,250],
  153: [147,180,245], 174: [192,136,136], 204: [240,96,136], 210: [240,128,128],
  211: [232,160,192], 216: [232,176,128], 222: [232,192,106], 231: [240,236,255],
  // Grayscale ramp — violet-tinted DARK greys used as BACKGROUND by Claude
  // Code's user-input box (256-color 238) and code/diff blocks. Keep these
  // dark: the fg that sits on top is `brightBlack` (ANSI 8) above, which is
  // the real "light violet pastel" the user sees. If these were brightened,
  // the fg↔bg luminance inverts and `minimumContrastRatio` would *reduce*
  // the fg luminance to hit ratio, producing dark-on-lightish.
  237: [38,36,56], 238: [50,48,70], 239: [74,74,94], 240: [84,82,104],
  241: [94,92,112], 242: [100,98,118], 243: [107,105,124], 244: [114,112,134],
  245: [124,122,146], 246: [136,136,160],
};
const _light256 = {
  16: [240,236,248], 37: [10,112,104], 114: [24,112,74], 147: [96,64,192],
  153: [48,80,176], 174: [144,64,80], 204: [200,32,96], 210: [184,48,48],
  211: [176,48,112], 216: [160,96,32], 222: [139,105,20], 231: [26,16,40],
  // Grayscale ramp — ensure every step is readable on rgba(244,242,248,0.40)
  237: [228,222,232], 238: [210,204,216], 239: [160,144,160], 240: [148,136,148],
  241: [136,124,136], 242: [128,116,128], 243: [120,108,120], 244: [112,100,112],
  245: [100,90,104], 246: [88,80,96],
};

// Patch the xterm.js internal 256-color palette with brand-aligned overrides.
// xterm.js v5 stores the palette in _colorManager or _themeService internals.
// We walk the prototype chain to find it after open().
function _apply256Palette(isDark) {
  if (!_term) return;
  const overrides = isDark ? _dark256 : _light256;
  // xterm.js v5 stores colors in _core._colorManager._colors or similar
  const core = _term._core;
  if (!core) return;
  // Try known internal paths (xterm.js v5.x)
  // Always refetch colors to ensure we get the latest after theme change
  const colors = core._colorManager?.colors?.ansi;
  if (!colors) {
    console.warn("[xterm] _apply256Palette: color manager path not found — 256-color overrides skipped");
    return;
  }
  for (const [idx, [r, g, b]] of Object.entries(overrides)) {
    const i = parseInt(idx);
    if (i < colors.length && colors[i]) {
      // xterm.js v5 stores colors as {css: string, rgba: number}
      if (typeof colors[i] === "object") {
        colors[i].css = `#${r.toString(16).padStart(2,"0")}${g.toString(16).padStart(2,"0")}${b.toString(16).padStart(2,"0")}`;
        colors[i].rgba = ((r << 24) | (g << 16) | (b << 8) | 0xff) >>> 0;
      }
    }
  }
  // Force full re-render
  if (_term.refresh) _term.refresh(0, _term.rows - 1);
}

// Track the current theme setting globally. Updated when main process sends
// "theme-changed" event. Used by reapplyColors() to know the actual theme
// (not just the system preference from matchMedia).
let _currentThemeIsDark = window.matchMedia("(prefers-color-scheme: dark)").matches;

// Listen for theme changes from main process (nativeTheme)
ipcRenderer.on("theme-changed", (_event, isDark) => {
  _currentThemeIsDark = isDark;
  if (_term) {
    _term.options.theme = isDark ? _darkTermTheme : _lightTermTheme;
    _term.options.minimumContrastRatio = isDark ? 4.5 : 7;
    _apply256Palette(isDark);
    const cols = _term.cols, rows = _term.rows;
    _term.resize(cols, rows + 1);
    _term.resize(cols, rows);
    _term.refresh(0, _term.rows - 1);
  }
});

contextBridge.exposeInMainWorld("xtermBridge", {
  init: async (containerId) => {
    if (_term) {
      // Verify _term is mounted in the requested container. If the canvas took
      // over xterm (mounted in xterm-canvas-bottom) and the main terminal now
      // wants xterm-container, the containers won't match — dispose and re-init.
      const target = document.getElementById(containerId);
      if (target && _term.element && target.contains(_term.element)) {
        return true; // already in the right container
      }
      // Wrong container: full disposal so we can re-init in the correct place.
      if (_linkProviderDisposable) { try { _linkProviderDisposable.dispose(); } catch {} _linkProviderDisposable = null; }
      if (_scrollSelectionDisposable) { try { _scrollSelectionDisposable.dispose(); } catch {} _scrollSelectionDisposable = null; }
      if (_scrollbackListenerTimer) { clearTimeout(_scrollbackListenerTimer); _scrollbackListenerTimer = null; }
      disposeTerminalSafely(_term);
      _term = null; _fitAddon = null;
      window._canvasTermActive = false; // canvas no longer holds the terminal
    }
    const container = document.getElementById(containerId);
    if (!container) return false;

    // Sync current theme from main process before creating terminal.
    // Ensures _currentThemeIsDark is correct even if theme-changed event hasn't fired yet.
    try {
      const themeData = await ipcRenderer.invoke("get-theme");
      if (themeData && themeData.isDark !== undefined) {
        _currentThemeIsDark = themeData.isDark;
      }
    } catch {
      // If the call fails, _currentThemeIsDark keeps its init value (based on matchMedia),
      // which should be correct in most cases.
    }

    _term = new Terminal({
      theme: _currentThemeIsDark ? _darkTermTheme : _lightTermTheme,
      fontFamily: "'MesloLGS Nerd Font Mono', 'Andale Mono', Menlo, monospace",
      fontSize: 12.5,
      lineHeight: 1.2,
      cursorBlink: true,
      scrollback: 5000,
      scrollOnUserInput: false, // don't snap to bottom when user is reading/selecting
      scrollSensitivity: 0.1, // slow wheel scroll — default 1 is too fast to read long messages
      fastScrollSensitivity: 1, // alt-held fast scroll (default 5)
      rightClickSelectsWord: true, allowTransparency: true,
      // Dark mode: Claude Code's user-input box uses 24-bit RGB dark-grey fg
      // (~rgb(80,80,90)) on a 24-bit RGB dark-grey bg (~rgb(40,40,45)) —
      // both outside our palette overrides (CM_RGB bypasses the ANSI table)
      // so the only fix is `minimumContrastRatio`. Ratio 3 sat at the
      // threshold and xterm's `increaseLuminance` barely lifted — 4.5 (WCAG
      // AA normal text) puts the lift well clear of the pair. Brand accents
      // (`#8a80d8`, `#5eae76`, …) already sit >7:1 on theme bg so they're
      // unaffected. Light mode stays at 7 (paper bg tolerates AAA).
      minimumContrastRatio: _currentThemeIsDark ? 4.5 : 7,
    });
    _fitAddon = new FitAddon();
    _term.loadAddon(_fitAddon);
    _term.open(container);

    // Apply brand-aligned 256-color palette overrides
    _apply256Palette(_currentThemeIsDark);

    // Lock xterm's mouse tracking OFF so left-click-drag always selects
    // text.  Must run right after open() (once internals are constructed)
    // and before any PTY data arrives that could enable mouse tracking.
    // See forceSelectionEnabled() in terminal-controller.js for why.
    forceSelectionEnabled(_term);

    // Exit scroll-back mode on resize — the proxy's index mapping
    // becomes invalid if the terminal dimensions change.
    _term.onResize(() => { if (_scrollbackMode) _exitScrollbackMode(); });

    // ── Drag-scroll: CombinedCircularList approach ───────────────
    //
    // tmux runs in alt-screen normally (stable viewport).  When the
    // user drags a selection to the top/bottom edge, we temporarily
    // swap to the normal buffer with pre-captured tmux scrollback.
    // xterm's native _dragScroll() handles scrolling + selection
    // extension.  On mouseup we swap back and let tmux repaint.
    //
    // See memory/project_scroll_select_brief.md for the full spec.

    // Patch _getMouseEventScrollAmount for edge-proximity detection.
    // Only returns non-zero during scroll-back mode (plus triggers entry).
    const _selSvc = _term._core?._selectionService;
    if (_selSvc) {
      const EDGE_ZONE = 50; // px
      _selSvc._getMouseEventScrollAmount = (e) => {
        const screenEl = _term._core?.screenElement;
        if (!screenEl) return 0;
        const rect = screenEl.getBoundingClientRect();
        const distTop = e.clientY - rect.top;
        const distBot = rect.bottom - e.clientY;

        // Not in any edge zone → nothing to do
        const inTopZone = distTop < EDGE_ZONE;
        const inBotZone = distBot < EDGE_ZONE;
        if (!inTopZone && !inBotZone) return 0;

        // Enter scroll-back mode on first edge trigger. _enterScrollbackMode
        // fetches fresh scrollback itself via IPC, so we don't need a pre-
        // warmed cache — the previous `_capturedContent` gate was tied to a
        // 2s setTimeout that caused an unrelated pane-redraw stall on attach.
        if (!_scrollbackMode) _enterScrollbackMode();
        if (!_scrollbackMode) return 0;

        // CRITICAL: only trigger _dragScroll (amount != 0) when there's
        // actual room to scroll.  If ydisp is already at the boundary,
        // returning non-zero causes _dragScroll to jump selectionEnd to
        // the viewport edge in one tick — visible as an instant selection
        // jump to the end.  Returning 0 lets xterm's _handleMouseMove
        // extend selection row-by-row based on actual mouse position.
        const buf = _term._core?.buffer;
        if (!buf) return 0;

        // Acceleration: depth into the edge zone drives speed.
        //   zone boundary (0 px in): 1 line/tick
        //   25 px into zone:         2 lines/tick
        //   50 px (terminal edge):   3 lines/tick
        //   past terminal edge:      up to 5 lines/tick
        // Using depth (not overshoot) so downward scroll accelerates even
        // when the terminal fills the screen and the mouse can't go below it.
        const MAX = 5;
        const ZONE_STEP = 25; // px per additional line within zone
        if (inTopZone && buf.ydisp > 0) {
          const depth = Math.max(0, EDGE_ZONE - distTop);
          return -Math.min(MAX, 1 + Math.floor(depth / ZONE_STEP));
        }
        if (inBotZone) {
          if (buf.ydisp >= buf.ybase) return 0;
          const depth = Math.max(0, EDGE_ZONE - distBot);
          return Math.min(MAX, 1 + Math.floor(depth / ZONE_STEP));
        }
        return 0;
      };
    }

    // Mouseup exit is now handled as a one-shot listener inside
    // _enterScrollbackMode() — avoids premature exit from synthetic
    // mouseup events during the synchronous parse().

    // Intercept native copy/paste events on xterm's textarea.
    // These fire when the user clicks Edit > Copy/Paste from the menu bar
    // (macOS NSResponder chain dispatches DOM clipboard events on the focused
    // element). Without these listeners, the native copy finds no DOM selection
    // (xterm renders via canvas) and the native paste double-sends to the PTY.
    const _xtTextarea = _term.textarea;
    if (_xtTextarea) {
      _xtTextarea.addEventListener("copy", (e) => {
        if (_term.hasSelection()) {
          e.preventDefault();
          e.clipboardData.setData("text/plain", _term.getSelection());
        }
      }, { capture: true });

      // Capture-phase listener runs BEFORE xterm.js's own bubble-phase
      // paste handler (xterm v5 registers via addEventListener without
      // capture). stopPropagation blocks xterm's handler from also
      // running. preventDefault stops Chromium from inserting the
      // clipboard content into the hidden textarea (which xterm's input
      // pipeline would otherwise forward to the PTY as plain text —
      // double-paste and image loss).
      //
      // handlePasteEvent is bulletproof (never throws, always returns
      // a descriptor), so no try/catch is needed here. Diagnostics
      // opt-in: set window.__pasteDebug = true in devtools to trace
      // paste activity — logs are off by default.
      _xtTextarea.addEventListener("paste", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const debug = window.__pasteDebug === true;
        if (debug) {
          const dt = e.clipboardData;
          console.log("[distillate-paste] event fired",
            "types=", dt ? Array.from(dt.types || []) : "(none)",
            "items=", dt && dt.items ? dt.items.length : 0,
            "files=", dt && dt.files ? dt.files.length : 0);
        }
        if (!_term) {
          console.warn("[distillate-paste] paste ignored: terminal not initialized");
          return;
        }
        const result = await _pasteCore.handlePasteEvent(_term, e, {
          readBlob: async (file) => {
            // Same resilient chain as drop: arrayBuffer → retry after delay.
            // Paste events don't have a webUtils fallback (no File path).
            try {
              const buf = Buffer.from(await file.arrayBuffer());
              if (buf.length > 0) return buf;
            } catch {}
            // Retry after a short delay
            await new Promise((r) => setTimeout(r, 100));
            return Buffer.from(await file.arrayBuffer());
          },
          log: debug ? (...a) => console.log("[distillate-paste]", ...a) : null,
        });
        if (debug) console.log("[distillate-paste] result", result);
        // Show user-visible feedback on failure
        if (result && result.type === "none" && result.reason && result.reason !== "no clipboardData") {
          _term.write(`\r\n\x1b[33m[Paste] Nothing usable on clipboard\x1b[0m\r\n`);
        }
      }, { capture: true });
    }

    // Strip mouse-mode sequences so xterm.js stays in normal mode —
    // click-drag selects text, scroll is handled by our wheel handler.
    // stripMouseModes() correctly handles multi-param forms like
    // \x1b[?1002;1006h that tmux/Claude Code send when enabling SGR mouse
    // tracking; the prior single-param regex let those leak through and
    // mouse tracking hijacked left-click-drag away from text selection.
    const _origWrite = _term.write.bind(_term);
    _term.write = (data) => {
      // Catch bell character from Claude Code / agent and trigger waiting state
      if (data.includes('\x07')) {
        window.ipcRenderer?.invoke('bell-detected');
      }
      _origWrite(stripMouseModes(data));
    };

    _safeFit();

    // Wire up selection, write-buffer, and key-event logic via the
    // extracted + unit-tested terminal controller. See
    // ./terminal-controller.js and test/terminal-controller.test.js.
    const _controller = createTerminalController({
      term: _term,
      clipboard,
    });
    _writeBuffered = _controller.writeBuffered;

    // Scroll-selection accumulator: when the user scrolls during a
    // selection, text is accumulated across scroll positions. This
    // handler runs AFTER terminal-controller's auto-copy (registered
    // first) and overwrites the clipboard with the full accumulated text.
    _scrollSelectionDisposable = _term.onSelectionChange(() => {
      if (_term.hasSelection() && _scrollSelectionAccum) {
        const current = _term.getSelection();
        clipboard.writeText(_scrollSelectionAccum + "\n" + current);
      } else if (!_term.hasSelection()) {
        _scrollSelectionAccum = "";
      }
    });

    return true;
  },
  write: (data) => {
    // During scroll-back mode, buffer ALL PTY data.
    if (_scrollbackMode) {
      _scrollbackWriteBuffer.push(data);
      _scrollbackWriteBufferBytes += (data && data.length) || 0;
      if (_scrollbackWriteBufferBytes > _SCROLLBACK_BUFFER_MAX_BYTES) {
        _exitScrollbackMode();  // drains the buffer and resets the byte count
      }
      return;
    }
    // During the attach burst, accumulate so the whole tmux alt-screen
    // redraw lands on xterm in one synchronous write — no row-by-row
    // "scroll through history" animation when selecting a session.
    if (_attachBurstActive) {
      _attachBurstBuffer.push(data);
      _attachBurstBufferBytes += (data && data.length) || 0;
      if (_attachBurstBufferBytes > ATTACH_BURST_MAX_BYTES) {
        _flushAttachBurst();
        return;
      }
      _bumpAttachBurstIdle();
      return;
    }
    // During wheel-scroll bypass, write directly so the user sees the
    // tmux redraw even though a selection is active.
    if (_writeBypassUntil && Date.now() < _writeBypassUntil) {
      if (_term) _term.write(data);
      return;
    }
    if (_writeBuffered) _writeBuffered(data);
    else if (_term) _term.write(data);
  },
  // Open a burst window: subsequent write() calls accumulate into a
  // single buffer, flushed after 150 ms idle or 1.5 s hard cap. Caller
  // invokes this right before triggering the PTY attach.
  startAttachBurst: () => {
    // Arm the capture-cancellation flag: any _enterScrollbackMode IPC that
    // resolves while the burst is active belongs to the previous session and
    // must not set _scrollbackMode=true on this session's data stream.
    _cancelScrollbackEntry = true;
    _attachBurstActive = true;
    _attachBurstBuffer = [];
    _attachBurstBufferBytes = 0;
    if (_attachBurstIdleTimer) { clearTimeout(_attachBurstIdleTimer); _attachBurstIdleTimer = null; }
    if (_attachBurstHardTimer) clearTimeout(_attachBurstHardTimer);
    _attachBurstHardTimer = setTimeout(_flushAttachBurst, ATTACH_BURST_MAX_MS);
  },
  flushAttachBurst: () => { _flushAttachBurst(); },
  clear: () => {
    // Cancel any pending async _enterScrollbackMode so a stale capture-pane
    // IPC from the previous session can't set _scrollbackMode=true after the
    // new session's burst starts. Hard-reset all scrollback state rather than
    // calling _exitScrollbackMode (which would restore the old alt buffer and
    // flush stale write-buffered data into the new session's terminal).
    _cancelScrollbackEntry = true;
    _scrollbackMode = false;
    _enteringScrollbackMode = false;
    if (_scrollbackListenerTimer) { clearTimeout(_scrollbackListenerTimer); _scrollbackListenerTimer = null; }
    _scrollbackWriteBuffer.length = 0;
    _scrollbackWriteBufferBytes = 0;
    _savedAltLines = null;
    _savedAltYbase = 0;
    _savedAltYdisp = 0;
    _savedAltHasScrollback = false;
    _writeBypassUntil = 0;
    _scrollSelectionAccum = "";
    // Discard any buffered burst from a previous attach — don't flush it
    // to the terminal, since that briefly flashes stale content before
    // _term.clear() wipes it.
    if (_attachBurstActive) {
      if (_attachBurstIdleTimer) { clearTimeout(_attachBurstIdleTimer); _attachBurstIdleTimer = null; }
      if (_attachBurstHardTimer) { clearTimeout(_attachBurstHardTimer); _attachBurstHardTimer = null; }
      _attachBurstActive = false;
      _attachBurstBuffer = [];
      _attachBurstBufferBytes = 0;
    }
    if (_term) _term.clear();
  },
  fit: () => { _safeFit(); },
  // Reapply theme + 256-color palette overrides + force full canvas repaint.
  // Call after session switches: xterm's canvas renderer can lose its color
  // state when the container cycles through display:none, causing the
  // in-place palette mutations to reference stale/replaced color objects.
  // Uses _currentThemeIsDark (tracked from main process) not matchMedia (system
  // preference) so manually selected themes are respected across session switches.
  reapplyColors: () => {
    if (!_term) return;
    _term.options.theme = _currentThemeIsDark ? _darkTermTheme : _lightTermTheme;
    _term.options.minimumContrastRatio = _currentThemeIsDark ? 4.5 : 7;
    _apply256Palette(_currentThemeIsDark);
    // xterm.js v5 canvas renderer needs a resize cycle to fully repaint
    // the background — refresh() alone doesn't do it. This is internal to
    // xterm (changes the grid, not the PTY), so it never sends SIGWINCH.
    const cols = _term.cols, rows = _term.rows;
    _term.resize(cols, rows + 1);
    _term.resize(cols, rows);
    _term.refresh(0, _term.rows - 1);
  },
  onData: (callback) => {
    if (!_term) return;
    // Wrap the callback so paste / drop activity can be traced when
    // diagnostics are on. A log line here confirms xterm's coreService
    // fired for a term.paste() call; missing one means the paste never
    // made it past xterm. Single-char data (regular typing) is filtered
    // so the log stays usable. Opt-in via window.__pasteDebug = true.
    _term.onData((data) => {
      if (window.__pasteDebug === true && data && data.length > 1) {
        console.log("[distillate-paste] onData len=", data.length,
          "sample=", JSON.stringify(data.slice(0, 120)));
      }
      callback(data);
    });
  },
  hasSelection: () => _term ? _term.hasSelection() : false,
  clearSelection: () => { if (_term) _term.clearSelection(); },
  scrollLines: (n) => { if (_term) _term.scrollLines(n); },
  scrollToBottom: () => { if (_term) _term.scrollToBottom(); },
  // Wheel scroll using the CombinedCircularList scrollback mechanism (same as
  // drag-to-edge). Keeps scrolling entirely at the xterm level — nothing is
  // written to the PTY, so TUI modals (Claude Code multi-choice) are never
  // corrupted. First scroll fetches tmux history async; subsequent ticks instant.
  wheelScroll: async (direction) => {
    if (!_term) return;
    if (direction === "up") {
      if (!_scrollbackMode) {
        if (_enteringScrollbackMode) return; // async fetch in flight, skip
        // Register the keypress exit listener BEFORE the async fetch.
        // If the user types during the ~100ms IPC round-trip, earlyKey is set
        // and we abort rather than leaving them stuck in a frozen terminal.
        let earlyKey = false;
        const d = _term.onKey(() => {
          d.dispose();
          earlyKey = true;
          if (_scrollbackMode) _exitScrollbackMode();
        });
        await _enterScrollbackMode();
        if (earlyKey) {
          // Keypress arrived during fetch — scrollback may have partially
          // activated; clean up and bail.
          if (_scrollbackMode) _exitScrollbackMode();
          return;
        }
        if (!_scrollbackMode) return;
      }
      if (_scrollbackMode) {
        _term.scrollLines(-3);
        // The canvas renderer's scroll-optimization path doesn't redraw
        // newly-revealed rows from our CombinedCircularList proxy — force it.
        _term.refresh(0, _term.rows - 1);
      }
    } else {
      if (_scrollbackMode) {
        _term.scrollLines(3);
        _term.refresh(0, _term.rows - 1);
        // Exit when scrolled back to the live bottom.
        const buf = _term._core?.buffers?.active;
        if (buf && buf.ydisp >= buf.ybase) _exitScrollbackMode();
      }
    }
  },
  // Temporarily bypass writeBuffered so PTY data reaches xterm directly
  // even during an active selection.  Used by the wheel handler to let
  // tmux redraws through while keeping the selection visible.
  bypassWriteBuffer: (ms) => {
    _writeBypassUntil = Date.now() + (ms || 300);
  },
  // Fetch a fresh tmux capture-pane for the transcript overlay.
  // Returns { ok, content, error }.
  getTranscript: async () => {
    if (!_currentTmuxName) return { ok: false, error: "No terminal attached" };
    try {
      const data = await ipcRenderer.invoke("terminal:capture-pane", {
        tmuxName: _currentTmuxName,
        lines: 5000,
      });
      return data;
    } catch (err) {
      return { ok: false, error: err.message || String(err) };
    }
  },
  // Snapshot the current selection text into the accumulator before
  // tmux redraws change the cells.  The terminal-controller's auto-copy
  // will prepend this to the next clipboard write.
  accumulateSelection: () => {
    if (!_term || !_term.hasSelection()) return;
    const text = _term.getSelection();
    if (text) {
      // Avoid duplicating if the same text is already at the end
      if (!_scrollSelectionAccum.endsWith(text)) {
        _scrollSelectionAccum += (_scrollSelectionAccum ? "\n" : "") + text;
      }
    }
  },
  // Get and clear the accumulated selection (called by auto-copy).
  getAccumulatedSelection: () => {
    const accum = _scrollSelectionAccum;
    return accum;
  },
  clearAccumulatedSelection: () => {
    _scrollSelectionAccum = "";
  },
  setTmuxName: (name) => {
    // Force-exit scroll-back mode if active (e.g., session switch mid-drag).
    if (_scrollbackMode) _exitScrollbackMode();
    _sessionGeneration++;
    _currentTmuxName = name;
    _capturedContent = null;
    _writeBypassUntil = 0;
    _scrollSelectionAccum = "";
    if (_scrollbackCaptureTimer) {
      clearInterval(_scrollbackCaptureTimer);
      _scrollbackCaptureTimer = null;
    }
    // Proactive scrollback capture (setTimeout _backgroundCapture, 2000)
    // was removed — it caused the attach-session's pane redraw to stall
    // behind the capture-pane IPC on a busy tmux server. Scrollback is
    // now fetched on demand by _enterScrollbackMode when the user drags
    // to an edge.
  },
  // Diagnostic: dump terminal buffer state to the console.
  // Run window.xtermBridge.debug() in devtools to inspect.
  debug: () => {
    if (!_term) return "no terminal";
    const core = _term._core;
    const buf = core?.buffer;
    const normal = core?.buffers?.normal;
    const alt = core?.buffers?.alt;
    const activeIsAlt = buf === alt;
    const selSvc = core?._selectionService;
    const info = {
      activeBuffer: activeIsAlt ? "ALTERNATE" : "NORMAL",
      cols: _term.cols,
      rows: _term.rows,
      buffer: {
        ydisp: buf?.ydisp,       // viewport scroll position
        ybase: buf?.ybase,       // max scroll position (lines above viewport)
        scrollTop: buf?.scrollTop,
        scrollBottom: buf?.scrollBottom,
        linesLength: buf?.lines?.length,
      },
      normalBuffer: {
        ybase: normal?.ybase,
        linesLength: normal?.lines?.length,
      },
      altBuffer: {
        ybase: alt?.ybase,
        linesLength: alt?.lines?.length,
      },
      selection: {
        enabled: selSvc?._enabled,
        hasSelection: _term.hasSelection(),
        model: selSvc?._model ? {
          selectionStart: selSvc._model.selectionStart,
          selectionEnd: selSvc._model.selectionEnd,
        } : null,
      },
      mouseService: {
        areMouseEventsActive: core?._coreMouseService?.areMouseEventsActive,
        activeProtocol: core?._coreMouseService?.activeProtocol,
      },
      screenElement: {
        exists: !!core?.screenElement,
        rect: core?.screenElement?.getBoundingClientRect(),
      },
      scrollback: {
        mode: _scrollbackMode,
        capturedContentLength: _capturedContent?.length || 0,
        writeBufferLength: _scrollbackWriteBuffer.length,
        tmuxName: _currentTmuxName,
      },
    };
    console.table ? console.log(JSON.stringify(info, null, 2)) : console.log(info);
    return info;
  },
  // Dump one row's cells. y is relative to the top of the visible viewport
  // (0 = top row). Reads from the *active* buffer at `ydisp + y`, so in
  // scroll-back mode it returns cells from the CombinedCircularList proxy.
  //
  // Protocol to localise the scroll-up jumble in one round-trip:
  //   1. Scroll up once, then window.xtermBridge.dumpRow(0)   → snap_A
  //   2. Scroll up 3 more ticks, dumpRow(0)                    → snap_B
  //   3. Scroll back down 3 ticks (same ydisp as A), dumpRow(0) → snap_C
  //   4. Compare A vs C: if different, cells at the same absolute index
  //      changed between visits — points at cache mutation or width drift.
  dumpRow: (y = 0) => {
    if (!_term) return "no terminal";
    const buf = _term._core?.buffer;
    if (!buf) return "no buffer";
    const absIdx = (buf.ydisp || 0) + y;
    const line = buf.lines?.get?.(absIdx);
    if (!line) return { y, absIdx, error: "no line" };
    const cells = [];
    const cols = _term.cols;
    for (let x = 0; x < cols; x++) {
      try {
        cells.push({
          x,
          cp: line.getCodePoint ? line.getCodePoint(x) : null,
          ch: line.getString ? line.getString(x) : null,
          w:  line.getWidth ? line.getWidth(x) : null,
          fg: line.getFg ? line.getFg(x) : null,
          bg: line.getBg ? line.getBg(x) : null,
        });
      } catch (e) { cells.push({ x, err: String(e).slice(0, 80) }); break; }
    }
    return {
      y, absIdx, ydisp: buf.ydisp, ybase: buf.ybase,
      linesLength: buf.lines?.length, cols,
      scrollbackMode: _scrollbackMode,
      cells,
    };
  },
  dumpBuffer: ({ start = 0, end = null } = {}) => {
    if (!_term) return "no terminal";
    const last = end ?? (_term.rows - 1);
    const out = [];
    for (let y = start; y <= last; y++) {
      out.push(window.xtermBridge.dumpRow(y));
    }
    return out;
  },
  getDimensions: () => {
    if (!_term) return { cols: 120, rows: 30 };
    return { cols: _term.cols, rows: _term.rows };
  },
  focus: () => { if (_term) _term.focus(); },
  /**
   * Diagnostic: scan visible terminal rows for file-like tokens.
   * Call from devtools: ``xtermBridge.debugLinks()``
   */
  debugLinks: () => {
    if (!_term) return "No terminal";
    const buf = _term.buffer.active;
    const FILE_SCAN = /\/?[A-Za-z0-9_][\w./-]*\.(?:tex|md|markdown|mdx|py|rs|ts|tsx|js|jsx|mjs|cjs|json|yaml|yml|toml|sh|bash|zsh|txt|ini|cfg|env)/g;
    const results = [];
    for (let row = 0; row < _term.rows; row++) {
      const line = buf.getLine(buf.viewportY + row);
      if (!line) continue;
      const text = line.translateToString(true);
      FILE_SCAN.lastIndex = 0;
      const matches = [];
      let m;
      while ((m = FILE_SCAN.exec(text)) !== null) matches.push(m[0]);
      if (matches.length) results.push({ row, y: buf.viewportY + row + 1, matches, text: text.slice(0, 100) });
    }
    console.log("[link] debugLinks", {
      rows: _term.rows, viewportY: buf.viewportY, baseY: buf.baseY,
      bufLen: buf.length, linkProvider: !!_linkProviderDisposable,
      hasRegisterLinkProvider: typeof _term.registerLinkProvider === "function",
    });
    console.table(results);
    return results;
  },
  dispose: () => {
    if (_term) {
      if (_linkProviderDisposable) {
        try { _linkProviderDisposable.dispose(); } catch {}
        _linkProviderDisposable = null;
      }
      if (_scrollSelectionDisposable) {
        try { _scrollSelectionDisposable.dispose(); } catch {}
        _scrollSelectionDisposable = null;
      }
      if (_scrollbackListenerTimer) {
        clearTimeout(_scrollbackListenerTimer);
        _scrollbackListenerTimer = null;
      }
      disposeTerminalSafely(_term);
      _term = null; _fitAddon = null;
    }
  },
  /**
   * Register a file-open handler on the terminal: hovering a filename shows
   * an underline + pointer cursor; clicking fires the provided callback.
   * Double-clicking a filename also works as a fallback.
   *
   * Primary: xterm.js native ``registerLinkProvider`` — the Linkifier2
   * calls ``provideLinks(y)`` on mouse-hover, we scan the line for file
   * tokens, and xterm handles decoration + click activation.
   *
   * Fallback: document-level ``dblclick`` handler (capture phase) reads
   * xterm's word-selection after a double-click.
   *
   * Set ``window.__linkDebug = true`` to trace all link activity.
   */
  registerFileLinkProvider: (onActivate) => {
    if (!_term) return false;
    if (_linkProviderDisposable) {
      try { _linkProviderDisposable.dispose?.(); } catch {}
      _linkProviderDisposable = null;
    }

    // Anchored — validates a word-selection that should be ONLY a filename.
    const FILE_RE = /^[A-Za-z0-9_][\w./-]*\.(?:tex|md|markdown|mdx|py|rs|ts|tsx|js|jsx|mjs|cjs|json|yaml|yml|toml|sh|bash|zsh|txt|ini|cfg|env)$/;
    // Unanchored — scans a full line for embedded filenames.
    const FILE_SCAN_RE = /\/?[A-Za-z0-9_][\w./-]*\.(?:tex|md|markdown|mdx|py|rs|ts|tsx|js|jsx|mjs|cjs|json|yaml|yml|toml|sh|bash|zsh|txt|ini|cfg|env)/g;

    const disposables = [];

    // ── Primary: xterm.js native link provider ─────────────────────────
    // Linkifier2 calls provideLinks(y) on mouse-hover for each viewport
    // row. We scan the line for file tokens and return link descriptors
    // that xterm decorates (underline + pointer) and activates on click.
    try {
      if (typeof _term.registerLinkProvider === "function") {
        const d = _term.registerLinkProvider({
          provideLinks(y, callback) {
            try {
              // y is 1-based buffer line index (Linkifier2 already
              // adds viewportY before calling provideLinks).
              const line = _term.buffer.active.getLine(y - 1);
              if (!line) return callback(undefined);
              const text = line.translateToString(true);
              if (!text || !text.trim()) return callback(undefined);

              const links = [];
              FILE_SCAN_RE.lastIndex = 0;
              let m;
              while ((m = FILE_SCAN_RE.exec(text)) !== null) {
                links.push({
                  range: {
                    start: { x: m.index + 1, y },
                    end: { x: m.index + m[0].length, y },
                  },
                  text: m[0],
                  activate: (_event, linkText) => {
                    try { onActivate?.(linkText); }
                    catch (err) { console.error("[link] activate threw:", err); }
                  },
                });
              }
              if (window.__linkDebug) {
                console.log("[link] provideLinks y=", y, "links=", links.length,
                  "text=", JSON.stringify(text.slice(0, 120)));
              }
              callback(links.length ? links : undefined);
            } catch (err) {
              if (window.__linkDebug) console.error("[link] provideLinks error:", err);
              callback(undefined);
            }
          },
        });
        disposables.push(d);
        if (window.__linkDebug) console.log("[link] native link provider registered");
      }
    } catch (err) {
      if (window.__linkDebug) console.error("[link] registerLinkProvider failed:", err);
    }

    // ── Fallback: double-click handler (document capture phase) ────────
    // Capture-phase on document ensures we see the event even if a child
    // calls stopPropagation. We filter to clicks inside either terminal
    // container (canvas agent panel or main session terminal).
    const dblHandler = (e) => {
      if (!e.target?.closest?.("#xterm-canvas-bottom, #xterm-container")) return;
      setTimeout(() => {
        let sel = "";
        try { sel = (_term?.getSelection?.() || "").trim(); } catch {}
        sel = sel.replace(/(:\d+)+$/, "")           // strip :line or :line:col
                 .replace(/[.,;:!?)\]}>'"`]+$/, "")
                 .replace(/^[(\[{<'"`]+/, "");
        if (window.__linkDebug) {
          console.log("[link] dblclick selection=", JSON.stringify(sel),
            "matches=", FILE_RE.test(sel));
        }
        if (sel && FILE_RE.test(sel)) {
          try { onActivate?.(sel); }
          catch (err) { console.error("[link] onActivate threw:", err); }
        }
      }, 40);
    };
    document.addEventListener("dblclick", dblHandler, true);
    disposables.push({
      dispose: () => { try { document.removeEventListener("dblclick", dblHandler, true); } catch {} },
    });

    _linkProviderDisposable = {
      dispose: () => { for (const d of disposables) try { d.dispose?.(); } catch {} },
    };
    if (window.__linkDebug) console.log("[link] file link providers installed (native + dblclick)");
    return true;
  },
});

// Expose hljs separately (functions can't be passed through contextBridge directly,
// but we can wrap the needed methods)
contextBridge.exposeInMainWorld("hljs", {
  highlight: (code, opts) => hljs.highlight(code, opts),
  highlightAuto: (code) => hljs.highlightAuto(code),
  getLanguage: (name) => !!hljs.getLanguage(name),
});

// Load and configure marked in preload (has access to hljs + require)
const { marked } = require("marked");
marked.setOptions({ breaks: true, gfm: true });

const renderer = new marked.Renderer();
renderer.code = function ({ text, lang }) {
  let highlighted;
  if (lang && hljs.getLanguage(lang)) {
    highlighted = hljs.highlight(text, { language: lang }).value;
    highlighted = `<code class="hljs language-${lang}">${highlighted}</code>`;
  } else {
    const auto = hljs.highlightAuto(text).value;
    highlighted = `<code class="hljs">${auto}</code>`;
  }
  const escapedRaw = text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  return `<div class="code-block-wrapper"><button class="copy-btn" data-code="${escapedRaw}">Copy</button><pre>${highlighted}</pre></div>`;
};
renderer.link = function ({ href, title, text }) {
  // Prevent autolinks for Unicode escape sequences like u2588
  if (/^u[0-9a-f]{4}$/i.test(href)) {
    return text;
  }
  return `<a href="${href}"${title ? ` title="${title}"` : ''}>${text}</a>`;
};
marked.use({ renderer });

contextBridge.exposeInMainWorld("markedParse", (md) => {
  // Convert Unicode escape sequences like \u2588 to actual characters
  const converted = md.replace(/\\u([0-9a-f]{4})/gi, (match, code) => {
    return String.fromCharCode(parseInt(code, 16));
  });
  return marked.parse(converted);
});

// ── Global drag-and-drop: paste file paths into terminal ──
// All drop logic lives in paste-handlers.js (shared with the paste path);
// preload only wires DOM events and hit-tests the terminal overlay.
// Diagnostics are opt-in: set window.__pasteDebug = true in devtools
// to trace dragenter / drop / materialization / paste-into-terminal.
{
  const _dlog = (...a) => {
    if (window.__pasteDebug !== true) return;
    console.log("[distillate-drop]", ...a);
  };

  // Overlay is driven by a dragover watchdog rather than an enter/leave
  // counter: counters desync on macOS file-promise drags (dragleave after
  // drop is flaky), leaving the overlay stuck on. While a drag is in
  // progress, dragover fires continuously, so the watchdog keeps deferring
  // the hide. The moment the drag ends (drop or the pointer leaves) the
  // watchdog fires and clears the class.
  let _hideTimer = null;

  const _xtermEl = () => document.getElementById("xterm-container");

  const _showOverlay = () => {
    const el = _xtermEl();
    if (el && !el.classList.contains("hidden")) el.classList.add("drop-active");
    if (_hideTimer) clearTimeout(_hideTimer);
    _hideTimer = setTimeout(_hideOverlay, 150);
  };

  const _hideOverlay = () => {
    if (_hideTimer) { clearTimeout(_hideTimer); _hideTimer = null; }
    const el = _xtermEl();
    if (el) el.classList.remove("drop-active");
  };

  // Permissive file-drag detection. macOS file-promise drags (the
  // Cmd+Shift+4 screenshot thumbnail, "drag from preview", etc.) often
  // DON'T advertise "Files" in dataTransfer.types during dragover, and
  // dt.items is locked to kind="" during dragover for security. So we
  // accept ANY drag that carries types the browser would hide behind
  // a file-ish label. The final filter happens on drop where items
  // become inspectable.
  const _looksLikeFileDrag = (dt) => {
    if (!dt) return false;
    const types = dt.types ? Array.from(dt.types) : [];
    if (types.includes("Files")) return true;
    // macOS file-promise drags expose downloadable content and UTI types.
    for (const t of types) {
      const s = String(t).toLowerCase();
      if (s === "files") return true;
      if (s.startsWith("application/x-moz-file")) return true;
      if (s === "downloadurl") return true;
      if (s.includes("file-url")) return true;           // public.file-url
      if (s.includes("image")) return true;              // public.image etc.
      if (s.startsWith("public.")) return true;          // macOS UTI
    }
    // Items can be inspected during drop but not always dragover.
    if (dt.items) {
      for (const it of dt.items) if (it && it.kind === "file") return true;
    }
    return false;
  };

  document.addEventListener("dragenter", (e) => {
    const types = e.dataTransfer && e.dataTransfer.types
      ? Array.from(e.dataTransfer.types) : [];
    _dlog("dragenter", "types=", types,
      "looksLikeFile=", _looksLikeFileDrag(e.dataTransfer));
    if (_looksLikeFileDrag(e.dataTransfer)) {
      e.preventDefault();
      _showOverlay();
    }
  }, true);

  document.addEventListener("dragover", (e) => {
    // MUST preventDefault here for drop to fire at all, regardless of
    // whether we're sure it's a file. If it turns out not to be a file
    // on drop, we just return without doing anything. Being generous
    // here is safer than missing a macOS file-promise drag.
    if (_looksLikeFileDrag(e.dataTransfer)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      _showOverlay();
    }
  }, true);

  // Collect File objects from both legacy `files` and modern `items` APIs,
  // deduping by (name,size,type,lastModified). macOS screenshot-preview drags
  // reliably surface in `items` but not always in `files`.
  const _collectDropFiles = (dt) => {
    const seen = new Set();
    const out = [];
    const add = (f) => {
      if (!f) return;
      const key = `${f.name || ""}|${f.size || 0}|${f.type || ""}|${f.lastModified || 0}`;
      if (seen.has(key)) return;
      seen.add(key);
      out.push(f);
    };
    if (dt.items) {
      for (const it of dt.items) if (it && it.kind === "file") add(it.getAsFile());
    }
    if (dt.files) for (const f of dt.files) add(f);
    return out;
  };

  document.addEventListener("drop", async (e) => {
    _hideOverlay();

    const dt = e.dataTransfer;
    const types = dt && dt.types ? Array.from(dt.types) : [];
    const itemCount = dt && dt.items ? dt.items.length : 0;
    const fileCount = dt && dt.files ? dt.files.length : 0;
    _dlog("drop event fired",
      "types=", types,
      "items=", itemCount,
      "files=", fileCount);

    if (!dt) { _dlog("drop: no dataTransfer"); return; }
    // Always preventDefault on drop so the browser doesn't navigate
    // to a file:// URL. If nothing usable is in the drop, we bail
    // after preventing the default.
    e.preventDefault();
    e.stopPropagation();
    if (!_term) {
      console.warn("[distillate-drop] drop ignored: no terminal initialized");
      _dlog("drop: no terminal yet");
      return;
    }

    const collected = _collectDropFiles(dt);
    _dlog("drop: collected",
      collected.map((f) => ({
        name: f.name, size: f.size, type: f.type, hasPath: !!f.path,
      })));
    if (!collected.length) {
      _dlog("drop: nothing to do (no kind=file items and empty .files)");
      return;
    }

    // Delegate resolution + tempfile materialization to paste-handlers so
    // drop and paste share one implementation. webUtils.getPathForFile is
    // used for Finder drags; readBlob handles file-promise / screenshot
    // thumbnail drops that have no on-disk path.
    //
    // readBlob with fallback + retry: macOS deletes the backing NSIRD file
    // for screenshot-thumbnail file-promise drags as soon as the drag source
    // releases. This creates a race between arrayBuffer() (async) and file
    // cleanup. We try arrayBuffer() first, then disk read via webUtils, then
    // retry arrayBuffer() once after a short delay (the OS sometimes locks
    // the file briefly before deleting it).
    const _readBlobWithFallback = async (file) => {
      // Attempt 1: standard async read
      try {
        const buf = Buffer.from(await file.arrayBuffer());
        if (buf.length > 0) return buf;
        _dlog("drop: arrayBuffer() returned empty buffer");
      } catch (blobErr) {
        _dlog("drop: arrayBuffer() failed", blobErr?.message);
      }
      // Attempt 2: synchronous disk read via webUtils path
      try {
        const diskPath = webUtils.getPathForFile(file);
        if (diskPath) {
          const buf = require("fs").readFileSync(diskPath);
          if (buf.length > 0) {
            _dlog("drop: disk fallback succeeded", diskPath, buf.length, "bytes");
            return buf;
          }
        }
      } catch (diskErr) {
        _dlog("drop: disk fallback failed", diskErr?.message);
      }
      // Attempt 3: retry arrayBuffer() after a short delay (macOS may still
      // be flushing the file-promise data to disk)
      try {
        await new Promise((r) => setTimeout(r, 100));
        const buf = Buffer.from(await file.arrayBuffer());
        if (buf.length > 0) {
          _dlog("drop: retry arrayBuffer() succeeded", buf.length, "bytes");
          return buf;
        }
      } catch (retryErr) {
        _dlog("drop: retry also failed", retryErr?.message);
      }
      throw new Error("all readBlob attempts failed");
    };

    let paths;
    try {
      paths = await _pasteCore.handleDrop(collected, {
        webUtils,
        readBlob: _readBlobWithFallback,
        log: _dlog,
      });
    } catch (dropErr) {
      console.error("[distillate-drop] handleDrop threw:", dropErr);
      _term.write(`\r\n\x1b[31m[Drop failed] ${dropErr?.message || "unknown error"}\x1b[0m\r\n`);
      return;
    }
    _dlog("drop: resolved paths", paths.length, "files from", collected.length, "collected");

    if (paths.length) {
      // Validate: every materialized file must exist and be non-empty.
      const validPaths = paths.filter((p) => {
        try {
          const s = require("fs").statSync(p);
          if (s.size === 0) {
            console.warn("[distillate-drop] empty file:", p);
            return false;
          }
          return true;
        } catch {
          console.warn("[distillate-drop] file missing:", p);
          return false;
        }
      });
      _dlog("drop: validated", validPaths.length, "paths after fs checks");

      if (!validPaths.length) {
        _term.write("\r\n\x1b[31m[Drop failed] File could not be saved — try pasting instead (Cmd+V)\x1b[0m\r\n");
        return;
      }

      const preview = validPaths
        .map((p) => (p.includes(" ") ? `"${p}"` : p))
        .join(" ");
      _dlog("drop: term.paste() input=", JSON.stringify(preview),
        "hasSelection=", !!(_term && _term.hasSelection && _term.hasSelection()));

      _pasteCore.pastePathsIntoTerm(_term, validPaths);
      _dlog("drop: paste submitted; refocusing terminal");
      _term.focus();
    } else {
      console.warn("[distillate-drop] no paths resolved from", collected.length, "files");
      _term.write("\r\n\x1b[31m[Drop failed] Could not read the dropped file\x1b[0m\r\n");
    }
  }, true);
}
