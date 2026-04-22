/**
 * Renderer-side regression tests for the Canvas feature.
 *
 * These tests load the actual renderer scripts (canvas.js, workspaces.js,
 * layout.js) into a jsdom window with mocked bridges and `fetch`. They
 * exercise the user-action paths (clicking sidebar items, terminal
 * filename clicks, view switches) and assert on the resulting fetch
 * calls / DOM state.
 *
 * Each "B*" test in this file maps to a known bug documented in
 * docs/canvas-spec-and-tests.md §2.7. The naming is intentional so that
 * a `git log --grep=B1` shows the regression history.
 *
 * Run: node --test desktop/test/canvas-renderer.test.js
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { makeHarness, loadRenderer, flush } = require("./helpers/canvas-harness");

/**
 * Standard HTML the renderer expects for the editor area. Pulled out so
 * each test starts from the same baseline DOM (similar to what's in
 * index.html).
 */
const EDITOR_HTML = `<!DOCTYPE html><html><body>
  <div id="editor-tabs" class="hidden">
    <button class="editor-tab active" data-view="control-panel">CP</button>
    <button class="editor-tab" data-view="session">S</button>
    <button class="editor-tab" data-view="results">R</button>
    <button class="editor-tab" data-view="prompt-editor">P</button>
  </div>
  <div id="editor-content">
    <div id="control-panel-view">
      <div id="welcome"></div>
      <div id="experiment-detail" class="hidden"></div>
    </div>
    <div id="session-view" class="hidden">
      <div id="xterm-container" class="hidden" style="height:300px"></div>
    </div>
    <div id="results-view" class="hidden"></div>
    <div id="prompt-editor-view" class="hidden"></div>
  </div>
  <div id="projects-sidebar"></div>
  <div id="agents-sidebar"></div>
  <div id="experiments-sidebar"></div>
  <button id="new-project-btn"></button>
</body></html>`;

/**
 * Stub the cross-module globals workspaces.js touches at load time. Keeps
 * each test self-contained — we don't need core.js / experiments.js etc.
 */
function stubCrossModuleGlobals(win) {
  win.fetchWelcomeStats = () => {};
  win.fetchWorkspaces = () => {};
  win.fetchAgents = () => {};
  win.editorViews = ["control-panel", "session", "results", "prompt-editor"];
  win.switchEditorTab = () => {};
  win.selectProject = () => {};
  win.cachedProjects = [];
  win._workspaces = [];
  win.libraryConfigured = true;
  win._sessionTransition = null;
  win._relativeTime = () => "now";
  win.currentPaperKey = null; // defined in core.js; required by switchSidebarView
  win.fetch = async () => ({}); // workspaces.js fires fetch('/nicolas/ack') on load
}

// ---- Smoke: harness can load renderer scripts without crashing ----

describe("harness", () => {
  it("loads with default mocks", () => {
    const h = makeHarness();
    assert.equal(typeof h.win.fetch, "undefined"); // fetch only after installFetchMock
    assert.ok(h.win.xtermBridge);
    assert.ok(h.win.distillate.canvas);
  });

  it("can load workspaces.js without throwing", () => {
    const h = makeHarness({
      html: EDITOR_HTML,
      beforeLoad: stubCrossModuleGlobals,
    });
    assert.doesNotThrow(() => loadRenderer(h.win, "workspaces.js"));
    assert.equal(typeof h.win.openCanvasInline, "function");
  });
});

// ---- B2: openCanvasInline switches view + unhides container ----
//
// Bug history: when a session tab was active, #control-panel-view was
// hidden. openCanvasInline mounted the editor into #experiment-detail,
// which lives INSIDE #control-panel-view. Result: the editor was rendered
// but invisible — user perceived "Canvas doesn't open."
//
// Fix: openCanvasInline must (a) call switchEditorTab("control-panel"),
// (b) explicitly remove the .hidden class from #experiment-detail, and
// (c) hide #welcome.

describe("B2 — openCanvasInline view switching", () => {
  function setupOpenCanvasTest({ sessionViewActive }) {
    const h = makeHarness({
      html: EDITOR_HTML,
      beforeLoad: stubCrossModuleGlobals,
    });
    if (sessionViewActive) {
      // Simulate the user being on the Session tab
      h.win.document.getElementById("control-panel-view").classList.add("hidden");
      h.win.document.getElementById("session-view").classList.remove("hidden");
    }
    // Spy on switchEditorTab — the rest of its logic isn't relevant here
    const switchSpy = h.spy("switchEditorTab", (viewName) => {
      // Mimic the side-effect that matters: unhide the requested view
      for (const v of h.win.editorViews) {
        const el = h.win.document.getElementById(`${v}-view`);
        if (el) el.classList.toggle("hidden", v !== viewName);
      }
    });
    // Stub mountCanvasEditor — we're testing the wiring, not the editor
    const mountCalls = [];
    h.win.mountCanvasEditor = async (...args) => { mountCalls.push(args); };
    loadRenderer(h.win, "workspaces.js");
    return { h, switchSpy, mountCalls };
  }

  it("switches to control-panel when session view is active", async () => {
    const { h, switchSpy } = setupOpenCanvasTest({ sessionViewActive: true });
    await h.win.openCanvasInline("ws1", "cv_001");
    assert.ok(
      switchSpy.calls.some((c) => c[0] === "control-panel"),
      `expected switchEditorTab("control-panel") to be called; got ${JSON.stringify(switchSpy.calls)}`,
    );
    // The session-view should now be hidden, control-panel-view shown
    assert.ok(h.win.document.getElementById("control-panel-view").classList.contains("hidden") === false,
      "control-panel-view should be visible after openCanvasInline");
    assert.ok(h.win.document.getElementById("session-view").classList.contains("hidden") === true,
      "session-view should be hidden after openCanvasInline");
  });

  it("unhides #experiment-detail and hides #welcome", async () => {
    const { h } = setupOpenCanvasTest({ sessionViewActive: true });
    const detail = h.win.document.getElementById("experiment-detail");
    const welcome = h.win.document.getElementById("welcome");
    assert.ok(detail.classList.contains("hidden"), "precondition: detail starts hidden");
    await h.win.openCanvasInline("ws1", "cv_001");
    assert.ok(!detail.classList.contains("hidden"), "experiment-detail must be unhidden");
    assert.ok(welcome.classList.contains("hidden"), "welcome must be hidden");
  });

  it("calls mountCanvasEditor with the canvas args + onBack callback", async () => {
    const { h, mountCalls } = setupOpenCanvasTest({ sessionViewActive: false });
    await h.win.openCanvasInline("ws-foo", "cv_042");
    assert.equal(mountCalls.length, 1, "mountCanvasEditor must be called exactly once");
    const [container, wsId, cvId, opts] = mountCalls[0];
    assert.equal(container, h.win.document.getElementById("experiment-detail"));
    assert.equal(wsId, "ws-foo");
    assert.equal(cvId, "cv_042");
    assert.equal(typeof opts.onBack, "function");
  });
});

// ---- B1 + B4: layout.js terminal-link wiring ----
//
// Bug history (B1): the file-link callback used `currentTerminalProject`
// as the workspace id. For workspace sessions, that variable holds the
// terminal key `ws_<wsId>_<sessionId>`, NOT the bare workspace id, so the
// POST went to /workspaces/ws_<wsId>_<sessionId>/canvases and the backend
// returned "Project not found." Fix: prefer
// `_currentSessionContext.workspaceId`.
//
// Bug history (B4): no file-link callback was registered on the main
// session terminal at all — only the canvas agent terminal had one.
// Fix: register a callback in layout.js's `ensureTerminalReady` after
// the terminal initializes.

/**
 * Stubs that let layout.js load + run ensureTerminalReady to completion
 * inside jsdom. We replace requestAnimationFrame with a microtask shim
 * (jsdom's pretendToBeVisual rAF is too slow for tests) and force the
 * xterm container to report a non-zero offsetHeight.
 */
function setupLayoutTerminalTest({ workspaceId, terminalKey }) {
  const h = makeHarness({
    html: EDITOR_HTML,
    beforeLoad: (win) => {
      stubCrossModuleGlobals(win);
      // Sync rAF — tryInit retries on rAF, so we want it to fire promptly
      // but not synchronously (to avoid stack recursion on the retry path).
      win.requestAnimationFrame = (cb) => { setImmediate(cb); return 1; };
      // Cross-module symbols layout.js touches at load (defined elsewhere
      // in core.js / workspaces.js / chat.js).
      win.cachedProjects = [];
      win.currentPaperKey = null;
      win.sidebarLeft = win.document.createElement("div");
      win.sidebarRight = win.document.createElement("div");
      win.bottomPanel = win.document.createElement("div");
    },
  });
  // jsdom never lays out — force the container to look "visible" so
  // ensureTerminalReady's `offsetHeight === 0` guard passes.
  const xtermEl = h.win.document.getElementById("xterm-container");
  Object.defineProperty(xtermEl, "offsetHeight", { value: 300, configurable: true });
  // Spy on openCanvasInline so we can see what wsId the callback ultimately
  // hands to it (the closing-the-loop assertion).
  const openCanvasCalls = [];
  h.win.openCanvasInline = (wsId, cvId) => { openCanvasCalls.push({ wsId, cvId }); };
  loadRenderer(h.win, "layout.js");
  return { h, openCanvasCalls, terminalKey, workspaceId, xtermEl };
}

/**
 * Drive the layout into a session-attached state with a known
 * (terminalKey, workspaceId) pair, then return the file-link callback
 * that ensureTerminalReady registered.
 */
async function attachSessionAndCaptureCallback({ h, terminalKey, workspaceId }) {
  // showTerminalForSession sets _currentSessionContext.workspaceId
  // synchronously, and kicks off attachToTerminalSession → ensureTerminalReady
  // (async). One flush() lets the rAF + microtask chain complete.
  h.win.showTerminalForSession(terminalKey, "tmux-test", "ProjectName", "AgentName", workspaceId);
  await flush();
  await flush();
  const cbs = h.win.xtermBridge._registeredCallbacks;
  assert.ok(cbs.length >= 1,
    `expected at least one file-link callback to be registered after attach; got ${cbs.length}`);
  return cbs[cbs.length - 1];
}

describe("B4 — main session terminal registers a file-link callback", () => {
  it("ensureTerminalReady → registerFileLinkProvider is called with a function", async () => {
    const { h } = setupLayoutTerminalTest({ workspaceId: "foo", terminalKey: "ws_foo_sess1" });
    const cb = await attachSessionAndCaptureCallback({
      h,
      terminalKey: "ws_foo_sess1",
      workspaceId: "foo",
    });
    assert.equal(typeof cb, "function",
      "expected a file-link callback to be registered on the main session terminal");
  });
});

describe("B1 — workspace id resolution in main session terminal callback", () => {
  it("uses _currentSessionContext.workspaceId, NOT currentTerminalProject (which is the terminal key)", async () => {
    const { h, openCanvasCalls } = setupLayoutTerminalTest({
      workspaceId: "endlessbench",
      terminalKey: "ws_endlessbench_sess001",
    });
    const cb = await attachSessionAndCaptureCallback({
      h,
      terminalKey: "ws_endlessbench_sess001",
      workspaceId: "endlessbench",
    });
    // Sanity: layout.js sets currentTerminalProject = the terminal key,
    // _currentSessionContext.workspaceId = the bare workspace id.
    assert.equal(h.win.currentTerminalProject, "ws_endlessbench_sess001",
      "precondition: currentTerminalProject should be the terminal key");

    // Mock fetch for the workspace-detail GET (resolves baseDir) and the
    // canvas POST. Both URLs MUST contain "endlessbench" — never the key.
    h.installFetchMock([
      {
        match: /\/workspaces\/endlessbench$/,
        body: { workspace: { id: "endlessbench", root_path: "/tmp/eb", repos: [] } },
      },
      {
        match: /\/workspaces\/endlessbench\/canvases$/,
        method: "POST",
        body: { ok: true, canvas: { id: "cv_999" } },
      },
    ]);

    await cb("paper.tex");

    // The two fetches we expect, in the order the callback issues them.
    const wsFetch = h.fetchCalls.find((c) => /\/workspaces\/[^/]+$/.test(c.url));
    const canvasFetch = h.fetchCalls.find((c) => /\/canvases$/.test(c.url));
    assert.ok(wsFetch, "expected a GET /workspaces/<id> for baseDir resolution");
    assert.ok(canvasFetch, "expected a POST /workspaces/<id>/canvases for import");

    // The actual regression assertion: workspaceId must be in the URL.
    assert.match(wsFetch.url, /\/workspaces\/endlessbench$/,
      `wsId in workspace-detail URL must be the bare workspace id; got: ${wsFetch.url}`);
    assert.match(canvasFetch.url, /\/workspaces\/endlessbench\/canvases$/,
      `wsId in canvases URL must be the bare workspace id; got: ${canvasFetch.url}`);

    // And NO call should ever use the terminal key.
    for (const c of h.fetchCalls) {
      assert.doesNotMatch(c.url, /ws_endlessbench_sess001/,
        `terminal key must not appear in any URL; offender: ${c.url}`);
    }

    // The handler should have called openCanvasInline with the resolved canvas id.
    assert.deepEqual(openCanvasCalls, [{ wsId: "endlessbench", cvId: "cv_999" }]);
  });

  it("falls back to currentTerminalProject when _currentSessionContext is null", async () => {
    // This guards against an over-correction: we want the new logic to
    // PREFER sessionContext, but still work when no session is attached
    // (e.g. an experiment terminal where the key IS the project id).
    const { h, xtermEl } = setupLayoutTerminalTest({
      workspaceId: "exp1",
      terminalKey: "exp1",
    });
    // ensureTerminalReady's tryInit bails if the container is .hidden;
    // showTerminalForSession would normally remove that, but we're skipping it.
    xtermEl.classList.remove("hidden");
    await h.win.ensureTerminalReady();
    h.win.currentTerminalProject = "exp1";
    const cb = h.win.xtermBridge._registeredCallbacks[h.win.xtermBridge._registeredCallbacks.length - 1];
    assert.ok(cb, "callback should be registered");

    h.installFetchMock([
      { match: /\/workspaces\/exp1$/, body: { workspace: { id: "exp1", root_path: "/tmp/exp1", repos: [] } } },
      { match: /\/workspaces\/exp1\/canvases$/, method: "POST", body: { ok: true, canvas: { id: "cv_1" } } },
    ]);
    await cb("notes.md");
    const canvasFetch = h.fetchCalls.find((c) => /\/canvases$/.test(c.url));
    assert.match(canvasFetch.url, /\/workspaces\/exp1\/canvases$/,
      "fallback to currentTerminalProject should still work when sessionContext is null");
  });
});
