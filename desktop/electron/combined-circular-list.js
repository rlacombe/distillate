/**
 * CombinedCircularList — read-only proxy that gives the alt buffer the
 * illusion of scrollback by prepending text lines before its real content.
 *
 * The "normal" portion is plain text strings from `tmux capture-pane`.
 * BufferLine objects are created lazily on first access by cloning a
 * template line from the alt buffer and filling it with character codes.
 * No inputHandler.parse(), no buffer swapping, no escape sequences.
 *
 * Index mapping:
 *   [0 .. textLen-1]              → lazily-built BufferLine from text
 *   [textLen .. textLen+altLen-1] → altLines.get(i - textLen)
 */

class CombinedCircularList {
  /**
   * @param {string[]} textLines  Plain text lines (from tmux capture-pane)
   * @param {object}   altLines   The alt buffer's original CircularList
   * @param {object}   altBuffer  The alt Buffer object (for getBlankLine)
   * @param {number}   cols       Terminal columns
   */
  constructor(textLines, altLines, altBuffer, cols) {
    this._textLines = textLines;
    this._cache = new Array(textLines.length); // lazily filled
    this._altLines = altLines;
    this._altBuffer = altBuffer;
    this._cols = cols;

    // Forward events from the original alt buffer lines.
    this.onDeleteEmitter = altLines.onDeleteEmitter;
    this.onDelete = altLines.onDelete;
    this.onInsertEmitter = altLines.onInsertEmitter;
    this.onInsert = altLines.onInsert;
    this.onTrimEmitter = altLines.onTrimEmitter;
    this.onTrim = altLines.onTrim;
  }

  _buildLine(index) {
    if (this._cache[index]) return this._cache[index];
    const text = this._textLines[index] || "";
    const line = this._altBuffer.getBlankLine();
    const len = Math.min(text.length, this._cols);
    for (let i = 0; i < len; i++) {
      const cp = text.charCodeAt(i);
      // setCellFromCodePoint(col, codePoint, width, fg, bg)
      // fg=0, bg=0 = default colors
      line.setCellFromCodePoint(i, cp, 1, 0, 0);
    }
    this._cache[index] = line;
    return line;
  }

  get(index) {
    const nLen = this._textLines.length;
    if (index < nLen) return this._buildLine(index);
    return this._altLines.get(index - nLen);
  }

  get length() { return this._textLines.length + this._altLines.length; }
  set length(v) { /* no-op */ }
  get maxLength() { return this._textLines.length + this._altLines.length; }
  set maxLength(v) { /* no-op */ }
  get isFull() { return true; }

  set(index, value) { /* no-op */ }
  push(value) { /* no-op */ }
  pop() { return undefined; }
  recycle() { return this._altLines.get(this._altLines.length - 1); }
  splice(start, deleteCount, ...items) { /* no-op */ }
  trimStart(count) { /* no-op */ }
  shiftElements(start, count, offset) { /* no-op */ }
}

module.exports = { CombinedCircularList };
