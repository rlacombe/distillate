const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { CombinedCircularList } = require("../electron/combined-circular-list");

/** Mock CircularList (alt buffer's original lines). */
function mockCircularList(items) {
  const _items = [...items];
  const _noopEmitter = { event: () => ({ dispose: () => {} }) };
  return {
    get(i) { return _items[i]; },
    set(i, v) { _items[i] = v; },
    get length() { return _items.length; },
    set length(v) { _items.length = v; },
    get maxLength() { return _items.length + 100; },
    set maxLength(v) {},
    get isFull() { return false; },
    push(v) { _items.push(v); },
    pop() { return _items.pop(); },
    recycle() { return _items[0]; },
    splice() {},
    trimStart() {},
    shiftElements() {},
    onDeleteEmitter: _noopEmitter,
    onDelete: _noopEmitter.event,
    onInsertEmitter: _noopEmitter,
    onInsert: _noopEmitter.event,
    onTrimEmitter: _noopEmitter,
    onTrim: _noopEmitter.event,
    _items,
  };
}

/** Mock alt buffer with getBlankLine that returns a mock BufferLine. */
function mockAltBuffer(cols) {
  return {
    getBlankLine() {
      const cells = new Array(cols).fill(null).map(() => ({ cp: 0, w: 1, fg: 0, bg: 0 }));
      return {
        setCellFromCodePoint(col, cp, w, fg, bg) { cells[col] = { cp, w, fg, bg }; },
        _cells: cells,
      };
    },
  };
}

describe("CombinedCircularList", () => {
  it("get() returns lazily-built BufferLine for text indices", () => {
    const alt = mockCircularList(["ALT0", "ALT1"]);
    const altBuf = mockAltBuffer(80);
    const combined = new CombinedCircularList(["hello", "world"], alt, altBuf, 80);

    // Text lines
    const line0 = combined.get(0);
    assert.equal(line0._cells[0].cp, "h".charCodeAt(0));
    assert.equal(line0._cells[4].cp, "o".charCodeAt(0));

    // Alt lines (passthrough)
    assert.equal(combined.get(2), "ALT0");
    assert.equal(combined.get(3), "ALT1");
  });

  it("caches BufferLine objects (same reference on repeat access)", () => {
    const alt = mockCircularList([]);
    const altBuf = mockAltBuffer(80);
    const combined = new CombinedCircularList(["abc"], alt, altBuf, 80);

    assert.equal(combined.get(0), combined.get(0));
  });

  it("length returns text + alt count", () => {
    const alt = mockCircularList(["a", "b"]);
    const combined = new CombinedCircularList(["x", "y", "z"], alt, mockAltBuffer(80), 80);
    assert.equal(combined.length, 5);
  });

  it("maxLength equals length", () => {
    const alt = mockCircularList(["a"]);
    const combined = new CombinedCircularList(["x"], alt, mockAltBuffer(80), 80);
    assert.equal(combined.maxLength, 2);
  });

  it("isFull always returns true", () => {
    const combined = new CombinedCircularList([], mockCircularList([]), mockAltBuffer(80), 80);
    assert.equal(combined.isFull, true);
  });

  it("mutations are no-ops", () => {
    const alt = mockCircularList(["a"]);
    const combined = new CombinedCircularList(["x"], alt, mockAltBuffer(80), 80);
    combined.push("Z");
    combined.set(0, "Z");
    combined.splice(0, 1);
    combined.trimStart(1);
    combined.shiftElements(0, 1, 1);
    assert.equal(combined.length, 2); // unchanged
  });

  it("events forwarded from altLines", () => {
    const alt = mockCircularList([]);
    const combined = new CombinedCircularList([], alt, mockAltBuffer(80), 80);
    assert.equal(combined.onTrimEmitter, alt.onTrimEmitter);
    assert.equal(combined.onInsertEmitter, alt.onInsertEmitter);
    assert.equal(combined.onDeleteEmitter, alt.onDeleteEmitter);
  });

  it("works with empty text lines", () => {
    const alt = mockCircularList(["A0", "A1"]);
    const combined = new CombinedCircularList([], alt, mockAltBuffer(80), 80);
    assert.equal(combined.length, 2);
    assert.equal(combined.get(0), "A0");
  });

  it("handles text longer than cols (truncates)", () => {
    const alt = mockCircularList([]);
    const combined = new CombinedCircularList(["abcdef"], alt, mockAltBuffer(4), 4);
    const line = combined.get(0);
    assert.equal(line._cells[3].cp, "d".charCodeAt(0));
    // col 4 stays at default (0) since cols=4
    assert.equal(line._cells[3].cp, 100); // 'd'
  });
});
