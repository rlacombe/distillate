const { contextBridge, ipcRenderer, clipboard } = require("electron");

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
    ipcRenderer.invoke("terminal:input", { projectId, data }),
  terminalResize: (projectId, cols, rows) =>
    ipcRenderer.invoke("terminal:resize", { projectId, cols, rows }),
  terminalDetach: (projectId) =>
    ipcRenderer.invoke("terminal:detach", { projectId }),
  onTerminalData: (callback) =>
    ipcRenderer.on("terminal:data", (_event, payload) => callback(payload)),
  onTerminalExit: (callback) =>
    ipcRenderer.on("terminal:exit", (_event, payload) => callback(payload)),
});

// Expose xterm terminal bridge (Terminal instance lives in preload world, renders to shared DOM)
let _term = null;
let _fitAddon = null;
let _writeBuffered = null;
let _scrollCallback = null;

contextBridge.exposeInMainWorld("xtermBridge", {
  init: (containerId) => {
    if (_term) return true; // already initialized
    const container = document.getElementById(containerId);
    if (!container) return false;

    _term = new Terminal({
      theme: {
        background: "#0f0f23",
        foreground: "#e0e0e8",
        cursor: "#6366f1",
        selectionBackground: "rgba(99, 102, 241, 0.26)",
        black: "#0f0f23",
        red: "#ef4444",
        green: "#22c55e",
        yellow: "#f59e0b",
        blue: "#6366f1",
        magenta: "#a78bfa",
        cyan: "#38bdf8",
        white: "#e0e0e8",
      },
      fontFamily: "'SF Mono', 'Fira Code', 'JetBrains Mono', monospace",
      fontSize: 13,
      cursorBlink: true,
      scrollback: 5000,
      scrollOnUserInput: false, // don't snap to bottom when user is reading/selecting
      rightClickSelectsWord: true,
    });
    _fitAddon = new FitAddon();
    _term.loadAddon(_fitAddon);
    _term.open(container);

    // Strip mouse-mode enable/disable sequences from incoming data so xterm.js
    // never enters mouse mode — mouse events stay as text selection, not PTY input.
    const _origWrite = _term.write.bind(_term);
    _term.write = (data) => {
      if (typeof data === "string") {
        data = data.replace(/\x1b\[\?(?:1000|1002|1003|1004|1005|1006|1015|1016)[hl]/g, "");
      }
      _origWrite(data);
    };

    _fitAddon.fit();

    // Forward scroll wheel to tmux as SGR mouse sequences (button 64=up, 65=down).
    // xterm.js can't scroll the alternate screen buffer itself — tmux owns the scrollback.
    container.addEventListener("wheel", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (_scrollCallback) {
        const button = e.deltaY > 0 ? 65 : 64;
        const lines = Math.max(1, Math.abs(Math.round(e.deltaY / 25)));
        _scrollCallback(button, lines);
      }
    }, { capture: true, passive: false });

    // Buffer writes while there's a text selection so incoming output
    // doesn't auto-scroll and destroy it.  Flush when selection is cleared.
    let _writeBuffer = [];

    _term.onSelectionChange(() => {
      if (!_term.hasSelection() && _writeBuffer.length > 0) {
        _term.write(_writeBuffer.join(""));
        _writeBuffer = [];
      }
    });

    // Cmd+C copies selection (if any), Cmd+V pastes from clipboard
    _term.attachCustomKeyEventHandler((e) => {
      if (e.type !== "keydown") return true;
      if ((e.metaKey || e.ctrlKey) && e.key === "c" && _term.hasSelection()) {
        clipboard.writeText(_term.getSelection());
        _term.clearSelection();
        return false;
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "v") {
        const text = clipboard.readText();
        if (text) _term.paste(text);
        return false;
      }
      return true;
    });

    _writeBuffered = (data) => {
      if (_term.hasSelection()) {
        _writeBuffer.push(data);
      } else {
        _term.write(data);
      }
    };

    return true;
  },
  write: (data) => { if (_writeBuffered) _writeBuffered(data); else if (_term) _term.write(data); },
  clear: () => { if (_term) _term.clear(); },
  fit: () => { if (_fitAddon) _fitAddon.fit(); },
  onData: (callback) => { if (_term) _term.onData(callback); },
  onScroll: (callback) => { _scrollCallback = callback; },
  getDimensions: () => {
    if (!_term) return { cols: 120, rows: 30 };
    return { cols: _term.cols, rows: _term.rows };
  },
  dispose: () => {
    if (_term) { _term.dispose(); _term = null; _fitAddon = null; }
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
marked.use({ renderer });

contextBridge.exposeInMainWorld("markedParse", (md) => marked.parse(md));
