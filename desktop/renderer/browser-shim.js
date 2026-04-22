/**
 * Browser shim — replaces Electron IPC bridges with browser-native APIs.
 *
 * Loaded before app.js. No-op when running inside Electron (preload.js
 * already set window.nicolas). In a browser, this creates compatible shims
 * for window.nicolas, window.xtermBridge, and window.hljs.
 */

if (window.nicolas) {
  // Running inside Electron — preload.js already set everything up.
  // Nothing to do.
} else {
  // -----------------------------------------------------------------------
  // Browser mode
  // -----------------------------------------------------------------------

  const _serverPort = Number(window.location.port) || 8742;
  const _baseUrl = `http://${window.location.hostname}:${_serverPort}`;
  const _wsBase = `ws://${window.location.hostname}:${_serverPort}`;

  // Hide Electron titlebar (drag region is useless in a browser)
  const _titlebar = document.getElementById("titlebar");
  if (_titlebar) _titlebar.style.display = "none";

  // -----------------------------------------------------------------------
  // Terminal WebSocket connections (keyed by projectId)
  // -----------------------------------------------------------------------

  const _termSockets = new Map(); // projectId → WebSocket
  let _onTerminalDataCb = null;
  let _onTerminalExitCb = null;

  // -----------------------------------------------------------------------
  // window.nicolas shim
  // -----------------------------------------------------------------------

  window.nicolas = {
    // Server lifecycle — server is already running (it served this page)
    onServerReady: (cb) => setTimeout(() => cb({ port: _serverPort }), 0),
    onServerError: () => {},
    onUpdateProgress: () => {},
    getServerPort: () => Promise.resolve(_serverPort),

    // Navigation — no-ops (no Electron menu / deep-link protocol)
    onDeepLink: () => {},
    onNewConversation: () => {},
    onOpenSettings: () => {},

    // Settings — via server API
    getSettings: () => fetch(`${_baseUrl}/settings`).then((r) => r.json()),
    saveSettings: (settings) =>
      fetch(`${_baseUrl}/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      }).then((r) => r.json()),

    // Shell
    openExternal: (url) => window.open(url, "_blank"),

    // Notifications — Web Notifications API
    notify: (title, body) => {
      if (!("Notification" in window)) return;
      if (Notification.permission === "granted") {
        new Notification(title, { body });
      } else if (Notification.permission !== "denied") {
        Notification.requestPermission().then((p) => {
          if (p === "granted") new Notification(title, { body });
        });
      }
    },

    // File dialogs — browser fallbacks
    selectDirectory: () => new Promise((resolve) => {
      if (typeof _showModal === "function") {
        _showModal({
          title: "Select Directory",
          fields: [{ id: "path", label: "Path", placeholder: "/path/to/directory", hint: "Absolute path to a local folder", autofocus: true }],
          submitLabel: "Select",
          onSubmit: (vals, overlay) => { overlay.remove(); resolve(vals.path || null); },
        });
      } else {
        resolve(null);
      }
    }),

    exportState: () =>
      fetch(`${_baseUrl}/state/export`)
        .then((r) => r.json())
        .then((data) => {
          const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = "distillate-state.json";
          a.click();
          URL.revokeObjectURL(a.href);
        }),

    importState: () =>
      new Promise((resolve) => {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = ".json";
        input.onchange = async () => {
          if (!input.files.length) return resolve();
          const text = await input.files[0].text();
          await fetch(`${_baseUrl}/state/import`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: text,
          });
          resolve();
        };
        input.click();
      }),

    // Terminal PTY — via WebSocket to /ws/terminal/{tmux_name}
    terminalAttach: (projectId, sessionName, cols, rows) => {
      // Close any existing connection for this project
      if (_termSockets.has(projectId)) {
        try { _termSockets.get(projectId).close(); } catch {}
        _termSockets.delete(projectId);
      }

      const ws = new WebSocket(`${_wsBase}/ws/terminal/${encodeURIComponent(sessionName)}`);
      ws.binaryType = "arraybuffer";
      _termSockets.set(projectId, ws);

      ws.onopen = () => {
        // Send initial resize
        ws.send(JSON.stringify({ type: "resize", rows: rows || 24, cols: cols || 80 }));
      };

      ws.onmessage = (event) => {
        if (!_onTerminalDataCb) return;
        let data;
        if (event.data instanceof ArrayBuffer) {
          data = new TextDecoder().decode(event.data);
        } else {
          data = event.data;
        }
        _onTerminalDataCb({ projectId, data });
      };

      ws.onclose = () => {
        _termSockets.delete(projectId);
        if (_onTerminalExitCb) _onTerminalExitCb({ projectId });
      };

      ws.onerror = () => {
        // onclose will fire after onerror
      };

      return Promise.resolve();
    },

    terminalInput: (projectId, data) => {
      const ws = _termSockets.get(projectId);
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(data);
    },

    terminalResize: (projectId, cols, rows) => {
      const ws = _termSockets.get(projectId);
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", rows, cols }));
      }
    },

    terminalDetach: (projectId) => {
      const ws = _termSockets.get(projectId);
      if (ws) {
        try { ws.close(); } catch {}
        _termSockets.delete(projectId);
      }
    },

    onTerminalData: (cb) => { _onTerminalDataCb = cb; },
    onTerminalExit: (cb) => { _onTerminalExitCb = cb; },
  };

  // -----------------------------------------------------------------------
  // window.xtermBridge shim — lazy-loads xterm.js from CDN
  // -----------------------------------------------------------------------

  let _term = null;
  let _fitAddon = null;
  let _writeBuffered = null;
  let _xtermLoaded = false;
  let _xtermLoadPromise = null;

  function _loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`Failed to load ${src}`));
      document.head.appendChild(s);
    });
  }

  function _loadCSS(href) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.appendChild(link);
  }

  async function _ensureXterm() {
    if (_xtermLoaded) return true;
    if (_xtermLoadPromise) return _xtermLoadPromise;

    _xtermLoadPromise = (async () => {
      try {
        _loadCSS("https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css");
        await _loadScript("https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js");
        await _loadScript("https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js");
        _xtermLoaded = true;
        return true;
      } catch (e) {
        console.error("[browser-shim] Failed to load xterm.js:", e);
        return false;
      }
    })();
    return _xtermLoadPromise;
  }

  window.xtermBridge = {
    init: (containerId) => {
      if (_term) return true;
      // xterm.js must already be loaded (ensureXterm called before init)
      if (typeof Terminal === "undefined") {
        // Trigger lazy load; caller will retry via ensureTerminalReady
        _ensureXterm();
        return false;
      }

      const container = document.getElementById(containerId);
      if (!container) return false;

      const _isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      _term = new Terminal({
        theme: _isDark
          ? { background: "rgba(11,10,20,0.65)", foreground: "#e0dce8", cursor: "#8b7cf6",
              selectionBackground: "rgba(139,124,246,0.15)",
              black: "#0c0a14", red: "#6b2424", green: "#2e5c3f", yellow: "#6b5010",
              blue: "#8fb0e0", magenta: "#b89cf0", cyan: "#60b8b0", white: "#e0dce8",
              brightBlack: "#7a7298", brightRed: "#e05555", brightGreen: "#5eae76",
              brightYellow: "#e8c06a", brightBlue: "#a0c4f0" }
          // Pastel palette — readable as BG (diff rows, input bar); the
          // minimumContrastRatio below auto-darkens these same colors when
          // used as FG so they stay readable on the light surface.
          : { background: "rgba(255,255,255,0.95)", foreground: "#0a0a14", cursor: "#6356d4",
              selectionBackground: "rgba(99,86,212,0.12)",
              black: "#0a0a14", red: "#fca5a5", green: "#86efac", yellow: "#fde68a",
              blue: "#93c5fd", magenta: "#c4b5fd", cyan: "#67e8f9", white: "#f4f2f8",
              brightBlack: "#6b7280", brightRed: "#fecaca", brightGreen: "#bbf7d0",
              brightYellow: "#fef3c7", brightBlue: "#dbeafe", brightMagenta: "#e9d5ff",
              brightCyan: "#cffafe", brightWhite: "#ffffff" },
        fontFamily: "'MesloLGS Nerd Font Mono', 'Andale Mono', Menlo, monospace",
        fontSize: 12.5,
        lineHeight: 1.15,
        minimumContrastRatio: _isDark ? 1 : 7,
        cursorBlink: true,
        scrollback: 5000,
        scrollOnUserInput: false,
        scrollSensitivity: 0.1, // slow wheel scroll — default 1 is too fast to read long messages
        fastScrollSensitivity: 1, // alt-held fast scroll (default 5)
        rightClickSelectsWord: true,
      });
      _fitAddon = new FitAddon.FitAddon();
      _term.loadAddon(_fitAddon);
      _term.open(container);
      _fitAddon.fit();

      // Buffer writes during text selection
      let _writeBuffer = [];
      _term.onSelectionChange(() => {
        if (!_term.hasSelection() && _writeBuffer.length > 0) {
          _term.write(_writeBuffer.join(""));
          _writeBuffer = [];
        }
      });

      // Cmd/Ctrl+C copies selection, Cmd/Ctrl+V pastes
      _term.attachCustomKeyEventHandler((e) => {
        if (e.type !== "keydown") return true;
        if ((e.metaKey || e.ctrlKey) && e.key === "c" && _term.hasSelection()) {
          navigator.clipboard.writeText(_term.getSelection());
          _term.clearSelection();
          e.preventDefault();
          return false;
        }
        if ((e.metaKey || e.ctrlKey) && (e.key === "v" || e.key === "V")) {
          // Do NOT preventDefault — let the native paste event fire on
          // xterm's hidden textarea.  Chromium populates clipboardData
          // from the user-activated gesture, which does NOT trigger the
          // macOS 14+ "access data from other apps" permission prompt.
          // navigator.clipboard.readText() DOES trigger it — never use it.
          return true;
        }
        // Let app-level shortcuts (Cmd+R refresh, Cmd+K palette, Cmd+B sidebar,
        // Cmd+J chat, Cmd+1-7 views, Cmd+/ focus) bubble past xterm to the
        // document keydown handler / Electron menu accelerators.
        if ((e.metaKey || e.ctrlKey) && ["r","R","k","b","j","/","1","2","3","4","5","6","7"].includes(e.key)) {
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

    write: (data) => {
      if (_writeBuffered) _writeBuffered(data);
      else if (_term) _term.write(data);
    },
    clear: () => { if (_term) _term.clear(); },
    fit: () => { if (_fitAddon) _fitAddon.fit(); },
    onData: (callback) => { if (_term) _term.onData(callback); },
    getDimensions: () => {
      if (!_term) return { cols: 120, rows: 30 };
      return { cols: _term.cols, rows: _term.rows };
    },
    focus: () => { if (_term) _term.focus(); },
    dispose: () => {
      if (_term) { _term.dispose(); _term = null; _fitAddon = null; }
    },
  };

  // Pre-load xterm.js in the background so it's ready when Session tab opens
  _ensureXterm();

  // -----------------------------------------------------------------------
  // window.hljs shim — lazy-load highlight.js from CDN
  // -----------------------------------------------------------------------

  if (!window.hljs) {
    // Load highlight.js in the background
    _loadScript("https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.11.1/highlight.min.js")
      .then(() => {
        // Re-configure marked with syntax highlighting (matching preload.js)
        if (typeof marked !== "undefined" && window.hljs) {
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
          window.markedParse = (md) => marked.parse(md);
        }
      })
      .catch(() => { /* highlight.js unavailable — code blocks render without highlighting */ });
  }
}
