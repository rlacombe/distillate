const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const {
  enterScrollbackState,
  exitScrollbackState,
  computeEdgeScrollAmount,
} = require("../electron/scrollback-state");

function mockAltBuffer({ altLineCount = 25, cols = 80 } = {}) {
  const _noopEmitter = { event: () => ({ dispose: () => {} }) };
  const _items = new Array(altLineCount).fill(null).map((_, i) => `alt-${i}`);
  const altLines = {
    get(i) { return _items[i]; },
    set() {},
    get length() { return _items.length; },
    set length(v) {},
    get maxLength() { return _items.length; },
    set maxLength(v) {},
    get isFull() { return true; },
    push() {}, pop() { return undefined; },
    recycle() {}, splice() {}, trimStart() {}, shiftElements() {},
    onDeleteEmitter: _noopEmitter, onDelete: _noopEmitter.event,
    onInsertEmitter: _noopEmitter, onInsert: _noopEmitter.event,
    onTrimEmitter: _noopEmitter, onTrim: _noopEmitter.event,
  };
  return {
    lines: altLines,
    ybase: 0,
    ydisp: 0,
    _hasScrollback: false,
    getBlankLine() {
      const cells = new Array(cols).fill(null).map(() => ({ cp: 0 }));
      return { setCellFromCodePoint(col, cp) { cells[col] = { cp }; }, _cells: cells };
    },
  };
}

describe("enterScrollbackState", () => {
  it("scrollPos=0 (at live view): ydisp = ybase, no downward room", () => {
    const altBuf = mockAltBuffer();
    const cap = new Array(100).fill("x");
    enterScrollbackState(altBuf, cap, 0, 80, 25);

    assert.equal(altBuf.ybase, 75); // 100 - 25
    assert.equal(altBuf.ydisp, 75); // at live
    assert.equal(altBuf._hasScrollback, true);
  });

  it("scrollPos=30 (scrolled up 30): ydisp = ybase - 30", () => {
    const altBuf = mockAltBuffer();
    const cap = new Array(100).fill("x");
    enterScrollbackState(altBuf, cap, 30, 80, 25);

    assert.equal(altBuf.ybase, 75);
    assert.equal(altBuf.ydisp, 45); // 75 - 30
    assert.ok(altBuf.ydisp < altBuf.ybase, "must have downward room");
    assert.ok(altBuf.ydisp > 0, "must have upward room");
  });

  it("scrollPos larger than ybase: ydisp clamped to 0", () => {
    const altBuf = mockAltBuffer();
    const cap = new Array(50).fill("x");
    enterScrollbackState(altBuf, cap, 9999, 80, 25);

    assert.equal(altBuf.ybase, 25);
    assert.equal(altBuf.ydisp, 0);
  });

  it("selection offset matches ydisp", () => {
    const altBuf = mockAltBuffer();
    const cap = new Array(100).fill("x");
    const sel = { selectionStart: [10, 5], selectionEnd: [20, 8] };
    enterScrollbackState(altBuf, cap, 30, 80, 25, sel);

    // ydisp = 45, so offset = 45
    assert.deepEqual(sel.selectionStart, [10, 50]);
    assert.deepEqual(sel.selectionEnd, [20, 53]);
  });

  it("exit restores original state", () => {
    const altBuf = mockAltBuffer();
    const originalLines = altBuf.lines;
    const saved = enterScrollbackState(altBuf, ["a", "b"], 0, 80, 2);

    altBuf.ydisp = 50;
    exitScrollbackState(altBuf, saved);

    assert.equal(altBuf.lines, originalLines);
    assert.equal(altBuf.ybase, 0);
    assert.equal(altBuf.ydisp, 0);
    assert.equal(altBuf._hasScrollback, false);
  });
});

describe("computeEdgeScrollAmount", () => {
  const rect = { top: 100, bottom: 600 };

  it("returns 0 in the middle", () => {
    assert.equal(computeEdgeScrollAmount(350, rect, 50), 0);
  });

  it("returns -1 near top", () => {
    assert.equal(computeEdgeScrollAmount(110, rect, 50), -1);
  });

  it("returns +1 near bottom", () => {
    assert.equal(computeEdgeScrollAmount(590, rect, 50), 1);
  });
});
