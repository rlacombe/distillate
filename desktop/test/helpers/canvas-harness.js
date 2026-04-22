/**
 * jsdom harness for renderer-side Canvas tests.
 *
 * The renderer scripts (canvas.js, workspaces.js, layout.js, ...) are
 * regular browser scripts: no module exports, communicate via `window.*`
 * globals, manipulate the DOM directly. We load them into a jsdom window
 * with mocks for the host-provided bridges (`window.distillate.canvas`,
 * `window.xtermBridge`, `window.nicolas`) and `fetch`.
 *
 * Usage:
 *
 *   const h = makeHarness({ html: "<div id=...></div>" });
 *   h.installFetchMock([
 *     { match: /workspaces\/ws1$/, body: { workspace: { id: "ws1" } } },
 *   ]);
 *   loadRenderer(h.win, "workspaces.js");
 *   await h.win.openCanvasInline("ws1", "cv_001");
 *   assert.deepEqual(h.fetchCalls[0].url, "http://127.0.0.1:8742/...");
 */

const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const RENDERER_DIR = path.join(__dirname, "..", "..", "renderer");
const MIN_HTML = `<!DOCTYPE html><html><body></body></html>`;

function _escapeHtml(s) {
  return String(s).replace(/[<>&"]/g, (c) => ({
    "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;",
  }[c]));
}

/**
 * Build a fresh jsdom + the standard global stubs the renderer expects.
 *
 * @param {Object} [opts]
 * @param {string} [opts.html] - initial HTML body
 * @param {function} [opts.beforeLoad] - hook to set extra globals before scripts load
 */
function makeHarness({ html = MIN_HTML, beforeLoad } = {}) {
  const dom = new JSDOM(html, {
    url: "http://localhost/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const win = dom.window;

  // ---- Globals the renderer references unconditionally ----
  win.serverPort = 8742;
  win.escapeHtml = _escapeHtml;
  win.markedParse = (s) => s; // markdown stub
  win.showToast = function (msg, kind) {
    (win._toasts ||= []).push({ msg, kind });
  };
  // jsdom omits these — renderer scripts touch them at load.
  if (typeof win.ResizeObserver === "undefined") {
    win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  }
  if (typeof win.IntersectionObserver === "undefined") {
    win.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  }

  // ---- Mock bridges ----
  // Mirror enough of the real xtermBridge API for layout.js + canvas.js
  // to attach/detach without crashing. Methods are intentionally no-ops;
  // tests that care about a specific call should spy() on it.
  win.xtermBridge = {
    _registeredCallbacks: [],
    init: (id) => true,
    onData: (cb) => { win.xtermBridge._onDataCb = cb; },
    fit: () => {},
    write: () => {},
    clear: () => {},
    focus: () => {},
    dispose: () => { win.xtermBridge._disposed = true; },
    getDimensions: () => ({ cols: 120, rows: 30 }),
    hasSelection: () => false,
    scrollLines: () => {},
    setTmuxName: () => {},
    reapplyColors: () => {},
    registerFileLinkProvider: (cb) => {
      win.xtermBridge._registeredCallbacks.push(cb);
      return true;
    },
  };

  win.distillate = {
    canvas: {
      listFiles: async () => ({ ok: true, files: [] }),
      readFile: async (wsId, cvId, rel) => ({ ok: true, content: "" }),
      writeFile: async () => ({ ok: true }),
      readPdf: async () => ({ ok: false, error: "no pdf" }),
      invalidateDirCache: async () => ({ ok: true }),
      startWatch: async () => ({ ok: true }),
      stopWatch: () => {},
      onFileChanged: (cb) => { win.distillate.canvas._fileChangedCb = cb; },
    },
    tectonic: {
      status: async () => ({ installed: false }),
      install: async () => ({ ok: true }),
      compile: async () => ({ ok: true, errors: [] }),
      abort: () => {},
      onInstallProgress: (cb) => { win.distillate.tectonic._progressCb = cb; },
    },
  };

  win.nicolas = {
    terminalInput: () => {},
    terminalAttach: async () => ({ ok: true }),
    terminalDetach: () => {},
    terminalResize: () => {},
    onTerminalData: (cb) => { win.nicolas._onDataCb = cb; },
    onTerminalExit: (cb) => { win.nicolas._onExitCb = cb; },
  };

  // ---- Globals that some renderer files set; pre-declare so they survive `var` ----
  win.terminalInitialized = false;
  win.currentTerminalProject = null;
  win.currentTerminalSession = null;
  win.currentProjectId = null;
  win._canvasTermActive = false;

  if (beforeLoad) beforeLoad(win);
  return makeHarnessApi(dom, win);
}

function makeHarnessApi(dom, win) {
  const api = {
    dom,
    win,
    fetchCalls: [],
    /**
     * Install a fetch mock that matches request URL against a list of
     * { match: RegExp, body: object } rules. Each call records into
     * `api.fetchCalls`. Unmatched calls return ok=false.
     */
    installFetchMock(rules = []) {
      win.fetch = async (url, opts = {}) => {
        const method = (opts.method || "GET").toUpperCase();
        let body = null;
        if (opts.body) {
          try { body = JSON.parse(opts.body); } catch { body = opts.body; }
        }
        api.fetchCalls.push({ url, method, body, opts });
        for (const r of rules) {
          if (r.match.test(url) && (!r.method || r.method === method)) {
            const respBody = typeof r.body === "function" ? r.body({ url, method, body }) : r.body;
            return _mockResponse(respBody, r.status || 200);
          }
        }
        return _mockResponse({ ok: false, error: `unmocked: ${method} ${url}` }, 404);
      };
    },
    /**
     * Replace a window-level function with a spy that records calls.
     * Returns the spy info object: { calls: [], restore: fn }.
     */
    spy(name, impl = () => {}) {
      const orig = win[name];
      const calls = [];
      win[name] = (...args) => {
        calls.push(args);
        return impl(...args);
      };
      return {
        calls,
        restore: () => { win[name] = orig; },
      };
    },
  };
  return api;
}

function _mockResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  };
}

/**
 * Load a renderer script into the jsdom window. Throws on syntax errors,
 * but lets runtime errors bubble up to the test (so we see the real failure).
 */
function loadRenderer(win, scriptName) {
  const scriptPath = path.join(RENDERER_DIR, scriptName);
  const code = fs.readFileSync(scriptPath, "utf8");
  // Wrap in IIFE so any top-level `let`/`const` doesn't leak between loads
  // in the same window — but we DO want functions to land on `window`,
  // so we use indirect eval.
  win.eval(code);
}

/** Yield to event loop — handy after triggering an async user action. */
function flush() {
  return new Promise((r) => setImmediate(r));
}

module.exports = { makeHarness, loadRenderer, flush };
