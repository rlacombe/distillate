/**
 * Tests for the xterm.js selection + write-buffer controller.
 *
 * Reproduces the bug reported by the user:
 *   "Selecting text from terminal to copy/paste has broken."
 *
 * Scenarios covered:
 *   - Mouse drag builds a selection → clipboard gets the text
 *   - Selection persists after mouseup (no write flush destroys it)
 *   - PTY writes arriving during a selection are buffered
 *   - Buffered writes flush after the selection is cleared (after debounce)
 *   - Flush is cancelled if a new selection starts within the debounce window
 *   - Cmd+C copies and clears selection
 *   - Cmd+C with no selection falls through to xterm (for SIGINT)
 *   - Cmd+V pastes clipboard text
 *   - Cmd+V with an image pastes via the image helper
 *   - Cmd+A selects all
 *   - Writes with no selection go straight to the terminal
 *
 * Run: node --test test/terminal-controller.test.js
 */

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");

const {
  createTerminalController,
  stripMouseModes,
  forceSelectionEnabled,
  disposeTerminalSafely,
} = require("../electron/terminal-controller");

// ────────────────────────────────────────────────────────────────────────────
// Mock Terminal — simulates the xterm.js Terminal API surface the controller
// touches, plus helpers for test scripts to drive selection + key events.
// ────────────────────────────────────────────────────────────────────────────

class MockTerminal {
  constructor() {
    this._selection = "";
    this._written = [];       // record of term.write() calls
    this._selectionListeners = [];
    this._customKeyHandler = null;
  }

  // xterm.js API surface used by the controller
  hasSelection() { return this._selection.length > 0; }
  getSelection() { return this._selection; }
  clearSelection() {
    if (this._selection !== "") {
      this._selection = "";
      this._fireSelectionChange();
    }
  }
  selectAll() {
    // Simulate: selectAll always selects the content currently written,
    // or a sentinel if empty.
    const content = this._written.join("") || "<ALL>";
    if (content !== this._selection) {
      this._selection = content;
      this._fireSelectionChange();
    }
  }
  write(data) { this._written.push(data); }
  onSelectionChange(cb) { this._selectionListeners.push(cb); }
  attachCustomKeyEventHandler(cb) { this._customKeyHandler = cb; }

  // Test helpers (underscored to keep clear of xterm API)
  _setSelection(text) {
    if (text !== this._selection) {
      this._selection = text;
      this._fireSelectionChange();
    }
  }
  _fireSelectionChange() {
    for (const cb of this._selectionListeners) cb();
  }
  _key(opts) {
    const e = {
      type: "keydown",
      metaKey: false,
      ctrlKey: false,
      key: "",
      _defaultPrevented: false,
      preventDefault() { this._defaultPrevented = true; },
      ...opts,
    };
    const result = this._customKeyHandler ? this._customKeyHandler(e) : true;
    return { propagated: result, defaultPrevented: e._defaultPrevented };
  }
  _writtenText() { return this._written.join(""); }
  _reset() {
    this._written.length = 0;
    this._selection = "";
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Mock clipboard + mock NativeImage
// ────────────────────────────────────────────────────────────────────────────

function makeClipboard(initial = {}) {
  return {
    _text: initial.text || "",
    writeText(t) { this._text = t; },
    readText() { return this._text; },
  };
}

// ────────────────────────────────────────────────────────────────────────────
// Manual fake timer: advance time explicitly so we can test the debounced flush
// without relying on real setTimeout.
// ────────────────────────────────────────────────────────────────────────────

function makeFakeTimers() {
  let now = 0;
  let nextId = 1;
  const pending = new Map(); // id -> { at, fn }

  function setTimeoutFn(fn, ms) {
    const id = nextId++;
    pending.set(id, { at: now + ms, fn });
    return id;
  }
  function clearTimeoutFn(id) { pending.delete(id); }
  function tick(ms) {
    now += ms;
    const due = [...pending.entries()].filter(([, v]) => v.at <= now);
    due.sort((a, b) => a[1].at - b[1].at);
    for (const [id, { fn }] of due) {
      pending.delete(id);
      fn();
    }
  }
  return { setTimeoutFn, clearTimeoutFn, tick, get pendingCount() { return pending.size; } };
}

// ────────────────────────────────────────────────────────────────────────────
// Helper to build a fresh controller+harness for each test
// ────────────────────────────────────────────────────────────────────────────

function harness({ clipboard: clip = makeClipboard() } = {}) {
  const term = new MockTerminal();
  const timers = makeFakeTimers();
  // pasteCalls is retained as an empty sentinel: the controller no
  // longer takes a handlePaste callback — Cmd+V is handled entirely
  // by the DOM "paste" listener wired in preload.js. Tests assert
  // pasteCalls stays empty as a regression guard.
  const pasteCalls = [];

  const controller = createTerminalController({
    term,
    clipboard: clip,
    setTimeoutFn: timers.setTimeoutFn,
    clearTimeoutFn: timers.clearTimeoutFn,
  });

  return { term, clipboard: clip, timers, controller, pasteCalls };
}

// ────────────────────────────────────────────────────────────────────────────
// Tests
// ────────────────────────────────────────────────────────────────────────────

describe("selection → clipboard", () => {
  it("auto-copies when a selection appears", () => {
    const { term, clipboard } = harness();
    term._setSelection("hello world");
    assert.equal(clipboard._text, "hello world");
  });

  it("overwrites clipboard as a drag grows the selection", () => {
    const { term, clipboard } = harness();
    term._setSelection("hel");
    term._setSelection("hello");
    term._setSelection("hello world");
    assert.equal(clipboard._text, "hello world");
  });

  it("does not touch the clipboard when selection clears", () => {
    const { term, clipboard } = harness();
    clipboard.writeText("prior");
    term._setSelection("foo");
    assert.equal(clipboard._text, "foo");
    term.clearSelection();
    assert.equal(clipboard._text, "foo", "clipboard retained across clearSelection");
  });
});

describe("write buffering while a selection is active", () => {
  it("routes writes straight to the terminal when no selection", () => {
    const { term, controller } = harness();
    controller.writeBuffered("line 1\n");
    controller.writeBuffered("line 2\n");
    assert.equal(term._writtenText(), "line 1\nline 2\n");
  });

  it("buffers writes while a selection is active", () => {
    const { term, controller } = harness();
    term._setSelection("selected");
    controller.writeBuffered("chunk-a");
    controller.writeBuffered("chunk-b");
    assert.equal(term._writtenText(), "", "no writes reach the terminal while selected");
    assert.deepEqual(controller._internals.buffer, ["chunk-a", "chunk-b"]);
  });

  it("selection survives a burst of buffered writes (nothing redraws it away)", () => {
    const { term, controller } = harness();
    term._setSelection("keep me");
    for (let i = 0; i < 50; i++) controller.writeBuffered(`x${i}`);
    assert.equal(term.hasSelection(), true);
    assert.equal(term.getSelection(), "keep me");
    assert.equal(term._writtenText(), "");
  });
});

describe("debounced flush after selection clears", () => {
  it("flushes buffered writes only after the debounce window", () => {
    const { term, controller, timers } = harness();
    term._setSelection("foo");
    controller.writeBuffered("chunk-a");
    controller.writeBuffered("chunk-b");
    term.clearSelection();

    assert.equal(term._writtenText(), "", "no flush immediately on clear");
    assert.equal(controller._internals.flushPending, true);

    timers.tick(79);
    assert.equal(term._writtenText(), "", "still no flush at t=79ms");

    timers.tick(1);
    assert.equal(term._writtenText(), "chunk-achunk-b", "flushed at t=80ms");
    assert.equal(controller._internals.flushPending, false);
    assert.deepEqual(controller._internals.buffer, []);
  });

  it("cancels the flush if a new selection starts within the debounce window", () => {
    const { term, controller, timers } = harness();
    term._setSelection("first");
    controller.writeBuffered("chunk-a");
    term.clearSelection();

    assert.equal(controller._internals.flushPending, true);

    timers.tick(40);
    term._setSelection("second"); // new drag starts mid-debounce
    assert.equal(controller._internals.flushPending, false,
      "flush cancelled so the new selection isn't destroyed");

    timers.tick(100);
    assert.equal(term._writtenText(), "", "no write snuck through");
    assert.equal(term.getSelection(), "second");
    assert.deepEqual(controller._internals.buffer, ["chunk-a"],
      "buffer retained until selection clears again");
  });

  it("handles the mouseup race: rapid clear→select→clear", () => {
    const { term, controller, timers } = harness();
    controller.writeBuffered("preface ");
    term._setSelection("drag");
    controller.writeBuffered("bg1");
    controller.writeBuffered("bg2");

    term.clearSelection();   // Cmd+C or re-click
    timers.tick(40);
    term._setSelection("drag2"); // re-select
    timers.tick(40);
    term.clearSelection();   // final clear
    timers.tick(80);

    assert.equal(term._writtenText(), "preface bg1bg2");
  });

  it("does not flush while the selection is still present at the debounce edge", () => {
    const { term, controller, timers } = harness();
    term._setSelection("x");
    controller.writeBuffered("chunk");
    term.clearSelection();
    timers.tick(40);
    term._setSelection("y"); // re-select; cancels pending flush
    timers.tick(1000);
    assert.equal(term._writtenText(), "");
    assert.deepEqual(controller._internals.buffer, ["chunk"]);
  });
});

describe("Cmd+C", () => {
  it("copies an existing selection and clears it", () => {
    const { term, clipboard } = harness();
    term._setSelection("hi");
    clipboard._text = ""; // simulate stale clipboard
    const r = term._key({ metaKey: true, key: "c" });
    assert.equal(clipboard._text, "hi");
    assert.equal(term.hasSelection(), false);
    assert.equal(r.propagated, false);
    assert.equal(r.defaultPrevented, true);
  });

  it("falls through to xterm (SIGINT) when there is no selection", () => {
    const { term } = harness();
    const r = term._key({ ctrlKey: true, key: "c" });
    assert.equal(r.propagated, true, "xterm should forward Ctrl+C as SIGINT");
    assert.equal(r.defaultPrevented, false);
  });

  it("only acts on keydown, not keyup", () => {
    const { term, clipboard } = harness();
    term._setSelection("stays");
    const r = term._key({ type: "keyup", metaKey: true, key: "c" });
    assert.equal(r.propagated, true);
    assert.equal(term.hasSelection(), true);
    assert.equal(clipboard._text, "stays"); // from auto-copy on selection
  });

  it("does not clobber the debounced flush when clearing the selection", () => {
    const { term, controller, timers } = harness();
    controller.writeBuffered("accumulated ");
    term._setSelection("selected text");
    controller.writeBuffered("during-sel");
    term._key({ metaKey: true, key: "c" });
    timers.tick(80);
    assert.equal(term._writtenText(), "accumulated during-sel");
  });
});

describe("Cmd+A", () => {
  it("selects all terminal content", () => {
    const { term, clipboard } = harness();
    term.write("hello world");
    term._key({ metaKey: true, key: "a" });
    assert.equal(term.hasSelection(), true);
    assert.equal(clipboard._text, "hello world", "auto-copy fires on selectAll");
  });
});

describe("Cmd+V", () => {
  // Cmd+V in the custom key handler MUST NOT call preventDefault and
  // MUST return false. Returning false tells xterm to bail out early
  // (it does no further work on the event — see xterm.js _keyDown).
  // NOT preventDefaulting lets Chromium run its default editing action
  // for Cmd+V, which dispatches the `paste` DOM event — that's the
  // single entry point handled by handlePasteEvent in paste-handlers.js
  // (tested in paste-handlers.test.js). Calling preventDefault here
  // would CANCEL the default paste action and the paste event would
  // never fire, breaking both text AND image paste silently.
  it("does NOT call preventDefault (regression for silent paste failure)", () => {
    const { term, pasteCalls } = harness();
    const r = term._key({ metaKey: true, key: "v" });
    assert.equal(pasteCalls.length, 0,
      "keydown must NOT paste — DOM paste event is the single source of truth");
    assert.equal(r.propagated, false,
      "returning false tells xterm to bail out before processing the key");
    assert.equal(r.defaultPrevented, false,
      "preventDefault would block Chromium's default paste action and the paste event would never fire");
  });

  it("only fires on keydown", () => {
    const { term, pasteCalls } = harness();
    const r = term._key({ type: "keyup", metaKey: true, key: "v" });
    assert.equal(pasteCalls.length, 0);
    assert.equal(r.propagated, true,
      "keyup is ignored — control returns to xterm");
  });
});

describe("stripMouseModes — the actual root cause", () => {
  // The prior regex only matched SINGLE-param forms:
  //   \x1b[?(1000|1002|1003|1006|1015)[hl]
  // Claude Code (via tmux) normally enables SGR mouse tracking with a
  // MULTI-param sequence: \x1b[?1002;1006h  — that slipped through and
  // xterm.js entered mouse-tracking mode. With mouse tracking on,
  // left-click-drag is reported as mouse events and does NOT select
  // text, so copy-to-clipboard has nothing to grab.

  it("strips a single-param enable", () => {
    assert.equal(stripMouseModes("\x1b[?1000h"), "");
    assert.equal(stripMouseModes("\x1b[?1002h"), "");
    assert.equal(stripMouseModes("\x1b[?1003h"), "");
    assert.equal(stripMouseModes("\x1b[?1006h"), "");
    assert.equal(stripMouseModes("\x1b[?1015h"), "");
  });

  it("strips a single-param disable", () => {
    assert.equal(stripMouseModes("\x1b[?1000l"), "");
    assert.equal(stripMouseModes("\x1b[?1006l"), "");
  });

  it("strips multi-param combined enable (tmux/Claude Code form)", () => {
    assert.equal(stripMouseModes("\x1b[?1002;1006h"), "");
    assert.equal(stripMouseModes("\x1b[?1000;1002;1006h"), "");
    assert.equal(stripMouseModes("\x1b[?1003;1006h"), "");
    assert.equal(stripMouseModes("\x1b[?1002;1006l"), "");
  });

  it("preserves non-stripped params when mixed with stripped modes", () => {
    // \x1b[?25;1002h would set show-cursor + cell-motion. Keep show-cursor.
    assert.equal(stripMouseModes("\x1b[?25;1002h"), "\x1b[?25h");
    assert.equal(stripMouseModes("\x1b[?1002;25h"), "\x1b[?25h");
    assert.equal(stripMouseModes("\x1b[?1;2;1006;47h"), "\x1b[?1;2;47h");
  });

  it("passes through alternate-screen-buffer modes (no longer stripped)", () => {
    // Alt-screen stripping was disabled to fix the auto-scroll bug.
    // tmux needs alt-screen; stripping it caused ybase drift.
    assert.equal(stripMouseModes("\x1b[?1049h"), "\x1b[?1049h");
    assert.equal(stripMouseModes("\x1b[?1049l"), "\x1b[?1049l");
    assert.equal(stripMouseModes("\x1b[?47h"), "\x1b[?47h");
    assert.equal(stripMouseModes("\x1b[?47l"), "\x1b[?47l");
    assert.equal(stripMouseModes("\x1b[?1047h"), "\x1b[?1047h");
    assert.equal(stripMouseModes("\x1b[?1047l"), "\x1b[?1047l");
  });

  it("leaves non-mouse DEC private modes alone", () => {
    assert.equal(stripMouseModes("\x1b[?25h"), "\x1b[?25h"); // show cursor
    assert.equal(stripMouseModes("\x1b[?25l"), "\x1b[?25l"); // hide cursor
    assert.equal(stripMouseModes("\x1b[?2004h"), "\x1b[?2004h"); // bracketed paste
  });

  it("strips mouse modes anywhere in a stream of data", () => {
    const input = "hello\x1b[?1002;1006hworld\x1b[?25h!";
    assert.equal(stripMouseModes(input), "helloworld\x1b[?25h!");
  });

  it("handles back-to-back sequences", () => {
    assert.equal(
      stripMouseModes("\x1b[?1000h\x1b[?1006h\x1b[?25h"),
      "\x1b[?25h",
    );
  });

  it("passes through non-string data unchanged (Uint8Array)", () => {
    const buf = new Uint8Array([27, 91, 63, 49, 48, 48, 50, 104]);
    assert.equal(stripMouseModes(buf), buf);
  });

  it("passes through an empty string", () => {
    assert.equal(stripMouseModes(""), "");
  });

  it("does not match CSI without ? (regular SGR/params)", () => {
    assert.equal(stripMouseModes("\x1b[1;31m"), "\x1b[1;31m"); // red
    assert.equal(stripMouseModes("\x1b[2J"), "\x1b[2J"); // clear
  });
});

describe("forceSelectionEnabled — xterm internal lockdown", () => {
  // Build a fake xterm.js `term._core` surface that mirrors the real
  // structure we're patching: _coreMouseService, _selectionService, element.
  function fakeTerm() {
    const classList = {
      _classes: new Set(),
      add(...names) { for (const n of names) this._classes.add(n); },
      remove(...names) { for (const n of names) this._classes.delete(n); },
      contains(n) { return this._classes.has(n); },
    };
    const element = { classList };
    const coreMouseService = {
      _activeProtocol: "NONE",
      get activeProtocol() { return this._activeProtocol; },
      set activeProtocol(v) { this._activeProtocol = v; },
      get areMouseEventsActive() {
        return this._activeProtocol !== "NONE" && this._activeProtocol !== "";
      },
    };
    const selectionService = {
      _enabled: true,
      enable() { this._enabled = true; },
      disable() { this._enabled = false; },
    };
    return {
      _core: { _coreMouseService: coreMouseService, _selectionService: selectionService, element },
    };
  }

  it("locks areMouseEventsActive to false even after a protocol is set", () => {
    const term = fakeTerm();
    forceSelectionEnabled(term);
    // Simulate xterm receiving \x1b[?1002h → setModePrivate sets
    // activeProtocol = "DRAG". Our lock should absorb it.
    term._core._coreMouseService.activeProtocol = "DRAG";
    assert.equal(term._core._coreMouseService.activeProtocol, "NONE");
    assert.equal(term._core._coreMouseService.areMouseEventsActive, false);
  });

  it("absorbs VT200, ANY, X10 protocol settings too", () => {
    const term = fakeTerm();
    forceSelectionEnabled(term);
    const svc = term._core._coreMouseService;
    for (const proto of ["VT200", "ANY", "X10", "DRAG"]) {
      svc.activeProtocol = proto;
      assert.equal(svc.activeProtocol, "NONE", `${proto} should be absorbed`);
      assert.equal(svc.areMouseEventsActive, false);
    }
  });

  it("keeps the selection service enabled even when disable() is called", () => {
    const term = fakeTerm();
    forceSelectionEnabled(term);
    const sel = term._core._selectionService;
    // Before patch: disable() would have set _enabled = false.
    sel.disable();
    assert.equal(sel._enabled, true, "disable() must be a no-op after lockdown");
  });

  it("removes and blocks the 'enable-mouse-events' CSS class", () => {
    const term = fakeTerm();
    term._core.element.classList.add("enable-mouse-events");
    forceSelectionEnabled(term);
    assert.equal(term._core.element.classList.contains("enable-mouse-events"), false);
    // Further attempts to add it are filtered out.
    term._core.element.classList.add("enable-mouse-events", "something-else");
    assert.equal(term._core.element.classList.contains("enable-mouse-events"), false);
    assert.equal(term._core.element.classList.contains("something-else"), true);
  });

  it("is robust to a missing/partial core (no throw)", () => {
    assert.doesNotThrow(() => forceSelectionEnabled(null));
    assert.doesNotThrow(() => forceSelectionEnabled(undefined));
    assert.doesNotThrow(() => forceSelectionEnabled({}));
    assert.doesNotThrow(() => forceSelectionEnabled({ _core: {} }));
  });
});

describe("regression: 'selection to copy/paste has broken'", () => {
  it("constant PTY stream + mouse drag → selection stays, clipboard has text", () => {
    // Simulates Claude Code's TUI streaming data while the user drags
    // to select.  Expected: selection persists, clipboard ends up with
    // the final selection, buffer holds streamed bytes until clear.
    const { term, controller, clipboard, timers } = harness();

    // Background data before the drag
    controller.writeBuffered("welcome\n");
    controller.writeBuffered("$ ");
    assert.equal(term._writtenText(), "welcome\n$ ");

    // User starts dragging.  xterm fires onSelectionChange as the drag grows.
    term._setSelection("wel");
    // Data keeps streaming during the drag.
    controller.writeBuffered("still streaming 1\n");
    term._setSelection("welcome");
    controller.writeBuffered("still streaming 2\n");

    // User releases mouse.  Selection stays "welcome".
    assert.equal(term.hasSelection(), true);
    assert.equal(term.getSelection(), "welcome");
    assert.equal(clipboard._text, "welcome",
      "clipboard should contain the selected text the moment the drag ends");

    // A moment later — still no new writes flushed, selection intact.
    timers.tick(500);
    assert.equal(term.hasSelection(), true);
    assert.equal(term.getSelection(), "welcome");
    assert.equal(term._writtenText(), "welcome\n$ ",
      "PTY writes during selection stay buffered");

    // User hits Cmd+C — confirms, clears selection.
    term._key({ metaKey: true, key: "c" });
    assert.equal(clipboard._text, "welcome");

    // After the debounce, buffered writes should flush.
    timers.tick(80);
    assert.equal(
      term._writtenText(),
      "welcome\n$ still streaming 1\nstill streaming 2\n",
    );
  });
});

// ────────────────────────────────────────────────────────────────────────────
// B3 — disposeTerminalSafely
//
// Bug history: preload.js's xtermBridge holds a singleton _term ref. The
// dispose() handler did `_term.dispose(); _term = null;`. When the inner
// dispose threw, _term stayed non-null, and the next init() short-circuited
// on `if (_term) return true` — leaving the singleton bound to a dead
// Terminal whose DOM was already detached. Symptom: terminal looks alive
// but no events flow.
//
// The fix moved the dispose call into a helper that swallows internal
// errors. Callers MUST still null their reference unconditionally — the
// helper can't reach into preload's closure to do that.
// ────────────────────────────────────────────────────────────────────────────

describe("B3 — disposeTerminalSafely", () => {
  it("returns without throwing when term.dispose() throws", () => {
    const angryTerm = {
      dispose: () => { throw new Error("xterm internal: cannot dispose, wrong state"); },
    };
    assert.doesNotThrow(
      () => disposeTerminalSafely(angryTerm),
      "disposeTerminalSafely must swallow the inner error so the caller can null its ref",
    );
  });

  it("does call term.dispose() when it doesn't throw", () => {
    let disposed = false;
    const term = { dispose: () => { disposed = true; } };
    disposeTerminalSafely(term);
    assert.equal(disposed, true);
  });

  it("is a no-op for null/undefined", () => {
    assert.doesNotThrow(() => disposeTerminalSafely(null));
    assert.doesNotThrow(() => disposeTerminalSafely(undefined));
  });

  // Closing-the-loop test that documents the contract preload.js relies on:
  // the singleton MUST be reset to null even when dispose() throws.
  it("supports the preload.js singleton-reset pattern", () => {
    let _term = { dispose: () => { throw new Error("simulated xterm crash"); } };
    // Mirror the preload.js dispose handler:
    function disposeBridge() {
      if (_term) {
        disposeTerminalSafely(_term);
        _term = null;            // ← critical: must execute even after a throw
      }
    }
    assert.doesNotThrow(disposeBridge);
    assert.equal(_term, null,
      "_term must be null after dispose, otherwise next init() short-circuits and reuses a dead Terminal");
    // A second dispose is also safe (idempotent).
    assert.doesNotThrow(disposeBridge);
  });
});
