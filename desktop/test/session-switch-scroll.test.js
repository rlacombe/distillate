/**
 * Tests for session-switch scroll bug.
 *
 * Covers: electron/attach-burst.js, electron/preload.js (write pipeline)
 *
 * Reproduces the bug: clicking an existing coding session triggers a visual
 * "scroll" that refills the xterm window with old cached entries.
 *
 * Scenarios:
 *   1. Attach-burst buffers all PTY data and flushes in one write
 *   2. Burst buffer is properly armed before PTY data can arrive
 *   3. clear() followed by setTmuxName() doesn't leak scrollback data
 *   4. Alt-screen no-op: xterm ignores ESC[?1049h when already in alt mode
 *   5. Post-flush data bypasses the burst buffer (goes directly to term)
 *   6. Second attach within settling window doesn't double-flush
 *   7. Burst flush + subsequent resize doesn't cause a second redraw burst
 *
 * Run: node --test test/session-switch-scroll.test.js
 */

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const { createAttachBurst } = require("../electron/attach-burst");

// ── Mock Terminal ──────────────────────────────────────────────────────
// Simulates the xterm.js Terminal API surface relevant to session switching.

class MockTerminal {
  constructor({ rows = 24, cols = 80 } = {}) {
    this.rows = rows;
    this.cols = cols;
    this._written = [];
    this._cleared = 0;
    this._inAltScreen = false;
    this._altBuffer = [];
    this._normalBuffer = [];
    this._ydisp = 0;
    this._ybase = 0;
    this._scrollback = [];
  }

  write(data) {
    this._written.push(data);
    // Simulate xterm's alt-screen-switch no-op:
    // ESC[?1049h when already in alt mode does nothing
    if (data.includes("\x1b[?1049h")) {
      if (!this._inAltScreen) {
        this._inAltScreen = true;
        this._normalBuffer = [...this._altBuffer];
        this._altBuffer = [];
      }
      // else: NO-OP — this is the bug vector
    }
  }

  clear() {
    this._cleared++;
    // xterm.js clear(): clears scrollback, keeps viewport content.
    // In alt mode, alt buffer has no scrollback, so this is effectively a no-op.
    if (!this._inAltScreen) {
      this._scrollback = [];
    }
  }

  get writtenChunks() { return this._written.length; }
  get allWrittenData() { return this._written.join(""); }
  get lastWrite() { return this._written[this._written.length - 1]; }
  reset() {
    this._written = [];
    this._cleared = 0;
    this._inAltScreen = false;
    this._scrollback = [];
  }
}

// ── Fake timers ────────────────────────────────────────────────────────

function createFakeTimers() {
  let _id = 0;
  const pending = new Map();

  return {
    setTimeout(cb, ms) {
      const id = ++_id;
      pending.set(id, { cb, ms, created: Date.now() });
      return id;
    },
    clearTimeout(id) {
      pending.delete(id);
    },
    flush(label) {
      // Fire all pending timers
      const toFire = [...pending.values()];
      pending.clear();
      for (const t of toFire) t.cb();
    },
    flushByDelay(ms) {
      // Fire timers with the given delay
      for (const [id, t] of pending) {
        if (t.ms === ms) {
          pending.delete(id);
          t.cb();
        }
      }
    },
    get pendingCount() { return pending.size; },
  };
}

// ── Tests ──────────────────────────────────────────────────────────────

describe("Attach-burst buffering", () => {
  let term, timers, burst;

  beforeEach(() => {
    term = new MockTerminal();
    timers = createFakeTimers();
    burst = createAttachBurst({
      term,
      idleMs: 150,
      maxMs: 1500,
      setTimeoutFn: timers.setTimeout.bind(timers),
      clearTimeoutFn: timers.clearTimeout.bind(timers),
    });
  });

  it("buffers all writes when active", () => {
    burst.start();
    burst.write("chunk1");
    burst.write("chunk2");
    burst.write("chunk3");
    assert.equal(term.writtenChunks, 0, "no data should reach terminal during burst");
    assert.deepEqual(burst.getBuffer(), ["chunk1", "chunk2", "chunk3"]);
  });

  it("flushes all buffered data as one write", () => {
    burst.start();
    burst.write("A");
    burst.write("B");
    burst.write("C");
    burst.flush();
    assert.equal(term.writtenChunks, 1, "all data should be in a single write");
    assert.equal(term.allWrittenData, "ABC");
  });

  it("returns false for write when not active", () => {
    assert.equal(burst.write("data"), false, "write should return false when burst inactive");
    assert.equal(term.writtenChunks, 0,
      "burst module does not write to term — caller routes data when write() returns false");
  });

  it("idle timer triggers flush after quiet period", () => {
    burst.start();
    burst.write("data");
    // Idle timer should be pending
    assert.ok(timers.pendingCount > 0, "idle timer should be scheduled");
    timers.flushByDelay(150); // fire idle timer
    assert.equal(term.writtenChunks, 1);
    assert.equal(burst.isActive(), false, "burst should be deactivated after flush");
  });

  it("hard cap timer forces flush even with continuous data", () => {
    burst.start();
    burst.write("data");
    // Only flush the 1500ms hard cap timer, not the idle timer
    timers.flushByDelay(1500);
    assert.equal(term.writtenChunks, 1);
    assert.equal(burst.isActive(), false);
  });

  it("deactivates BEFORE writing to terminal", () => {
    let wasActiveAtWrite = null;
    const spyTerm = {
      write(data) {
        wasActiveAtWrite = burst.isActive();
        term.write(data);
      },
    };
    const spyBurst = createAttachBurst({
      term: spyTerm,
      idleMs: 150,
      maxMs: 1500,
      setTimeoutFn: timers.setTimeout.bind(timers),
      clearTimeoutFn: timers.clearTimeout.bind(timers),
    });
    spyBurst.start();
    spyBurst.write("test");
    spyBurst.flush();
    assert.equal(wasActiveAtWrite, false,
      "BUG VECTOR: burst is deactivated before write — post-flush data bypasses the buffer");
  });

  it("second start() clears previous buffer", () => {
    burst.start();
    burst.write("old-session-data");
    burst.start(); // re-arm for new session
    assert.deepEqual(burst.getBuffer(), [], "buffer should be cleared on re-start");
  });
});

describe("Session switch: clear() + alt-screen", () => {
  let term;

  beforeEach(() => {
    term = new MockTerminal();
  });

  it("clear() in alt-screen mode does NOT clear viewport content", () => {
    // Simulate: terminal is in alt-screen (Claude Code running)
    term._inAltScreen = true;
    term.write("session-A-content");
    term.clear();

    // After clear(), the viewport content from session A is still present
    // because xterm.js's clear() only removes scrollback, and the alt
    // buffer has no scrollback.
    assert.ok(term._written.includes("session-A-content"),
      "BUG: clear() does NOT erase alt-screen viewport — old content persists");
  });

  it("ESC[?1049h is a no-op when already in alt-screen", () => {
    // First alt-screen switch
    term.write("\x1b[?1049h");
    assert.equal(term._inAltScreen, true);

    // Write session A content
    term.write("session-A-row-1\r\n");
    term.write("session-A-row-2\r\n");

    // Second alt-screen switch (from new tmux attach) — NO-OP
    const writeCountBefore = term.writtenChunks;
    term.write("\x1b[?1049h");
    assert.equal(term._inAltScreen, true);

    // The alt buffer is NOT cleared — session A content still exists
    // New session B content will overwrite row-by-row, creating the
    // "scroll through old entries" visual effect
    assert.ok(term._written.includes("session-A-row-1\r\n"),
      "BUG: old content persists through alt-screen re-entry because xterm no-ops");
  });
});

describe("Session switch: write pipeline integrity", () => {
  let term, timers, burst;

  // Simulates the full write pipeline from preload.js
  function createWritePipeline(term, burst) {
    let scrollbackMode = false;
    let scrollbackWriteBuffer = [];
    let writeBypassUntil = 0;
    let writeBuffered = null;

    function write(data) {
      if (scrollbackMode) { scrollbackWriteBuffer.push(data); return; }
      if (burst.isActive()) {
        burst.write(data);
        return;
      }
      if (writeBypassUntil && Date.now() < writeBypassUntil) {
        term.write(data);
        return;
      }
      if (writeBuffered) writeBuffered(data);
      else term.write(data);
    }

    return {
      write,
      enterScrollback() { scrollbackMode = true; },
      exitScrollback() {
        scrollbackMode = false;
        if (scrollbackWriteBuffer.length > 0) {
          const data = scrollbackWriteBuffer.join("");
          scrollbackWriteBuffer.length = 0;
          term.write(data);
        }
      },
      get scrollbackBuffer() { return scrollbackWriteBuffer.slice(); },
      setScrollbackMode(v) { scrollbackMode = v; },
    };
  }

  beforeEach(() => {
    term = new MockTerminal();
    timers = createFakeTimers();
    burst = createAttachBurst({
      term,
      idleMs: 150,
      maxMs: 1500,
      setTimeoutFn: timers.setTimeout.bind(timers),
      clearTimeoutFn: timers.clearTimeout.bind(timers),
    });
  });

  it("scrollback exit after clear() writes buffered data to terminal", () => {
    const pipeline = createWritePipeline(term, burst);

    // Simulate: user is in scrollback mode, PTY data arrives
    pipeline.enterScrollback();
    pipeline.write("data-during-scrollback");
    assert.equal(term.writtenChunks, 0, "data should be buffered during scrollback");

    // Simulate session switch: clear() then exit scrollback (via setTmuxName)
    term.clear();
    pipeline.exitScrollback();

    // BUG: scrollback buffer is flushed AFTER clear(), writing old data
    // to the freshly-cleared terminal
    assert.equal(term.writtenChunks, 1,
      "BUG VECTOR: scrollback flush writes to terminal after clear()");
    assert.equal(term.allWrittenData, "data-during-scrollback");
  });

  it("data arriving between clear() and startAttachBurst() bypasses buffer", () => {
    const pipeline = createWritePipeline(term, burst);

    // Simulate session switch steps:
    // 1. clear()
    term.clear();
    // 2. setTmuxName() — exits scrollback if needed (no-op here)
    // 3. (data arrives during rAF gap — from old PTY or other source)
    pipeline.write("stray-data-during-gap");
    // 4. startAttachBurst()
    burst.start();
    // 5. New PTY data arrives
    pipeline.write("new-session-data");

    // The stray data went directly to the terminal, bypassing the burst
    assert.ok(term._written.includes("stray-data-during-gap"),
      "BUG VECTOR: data during rAF gap bypasses burst buffer");
    // The new session data is properly buffered
    assert.deepEqual(burst.getBuffer(), ["new-session-data"]);
  });

  it("post-flush PTY data bypasses the burst buffer", () => {
    const pipeline = createWritePipeline(term, burst);

    // Arm burst, buffer initial data, flush
    burst.start();
    pipeline.write("burst-data");
    burst.flush();

    assert.equal(burst.isActive(), false, "burst deactivated after flush");

    // More PTY data arrives (e.g., tmux sends additional updates
    // triggered by a resize or agent-status poll waking the server)
    pipeline.write("post-flush-data");

    // This data goes directly to the terminal, not buffered
    assert.equal(term.writtenChunks, 2, "post-flush data is a separate write");
    assert.equal(term._written[0], "burst-data");
    assert.equal(term._written[1], "post-flush-data");
  });

  it("full session switch: clear → setTmuxName → burst → attach → flush", () => {
    const pipeline = createWritePipeline(term, burst);

    // Session A is showing
    term._inAltScreen = true;
    term.write("session-A-content");
    term.reset(); // reset tracking

    // === Session switch to B ===
    // Step 1: clear()
    if (burst.isActive()) burst.flush();
    term.clear();

    // Step 2: setTmuxName() — no scrollback active, no-op
    // Step 3: 3 rAFs (simulated gap)
    // Step 4: fit()
    // Step 5: startAttachBurst()
    burst.start();

    // Step 6: terminalAttach() → PTY starts sending tmux redraw
    const tmuxRedraw = [
      "\x1b[?1049h",     // alt-screen switch (no-op in xterm when already in alt!)
      "\x1b[H",          // cursor home
      "Session B line 1\r\n",
      "Session B line 2\r\n",
      "Session B line 3\r\n",
    ];
    for (const chunk of tmuxRedraw) {
      pipeline.write(chunk);
    }

    assert.equal(term.writtenChunks, 0, "all PTY data buffered during burst");

    // Step 7: idle timer fires → flush
    burst.flush();
    assert.equal(term.writtenChunks, 1, "all data flushed in single write");
    assert.equal(term.lastWrite, tmuxRedraw.join(""),
      "flush should produce a single merged write");
  });
});

describe("Session switch: resize-triggered redraw", () => {
  let term, timers, burst;

  beforeEach(() => {
    term = new MockTerminal();
    timers = createFakeTimers();
    burst = createAttachBurst({
      term,
      idleMs: 150,
      maxMs: 1500,
      setTimeoutFn: timers.setTimeout.bind(timers),
      clearTimeoutFn: timers.clearTimeout.bind(timers),
    });
  });

  it("resize after burst flush causes a second unprotected redraw", () => {
    // Arm burst, buffer initial tmux redraw, flush
    burst.start();
    burst.write("initial-redraw-data");
    burst.flush();

    assert.equal(burst.isActive(), false);

    // Simulate: resize triggers tmux to send another redraw
    // This data arrives AFTER the burst has deactivated
    term.write("resize-triggered-redraw-chunk-1");
    term.write("resize-triggered-redraw-chunk-2");
    term.write("resize-triggered-redraw-chunk-3");

    // These writes go directly to the terminal, one at a time
    // Each is a separate term.write() call → xterm renders incrementally
    // → visible as "scroll through history" effect
    assert.equal(term.writtenChunks, 4, // 1 burst + 3 direct
      "BUG VECTOR: post-burst resize redraw arrives as unbuffered chunks");
  });
});
