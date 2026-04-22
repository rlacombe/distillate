/**
 * Pure functions for scroll-back mode state transitions.
 *
 * `tmux capture-pane` captures the FULL pane buffer regardless of copy
 * mode — including any content below the user's current scroll view.
 * tmux's `#{scroll_position}` tells us exactly where the user is viewing
 * (lines above the live bottom).  We use this directly to place ydisp —
 * no fragile text matching.
 */

const { CombinedCircularList } = require("./combined-circular-list");

/**
 * Transition the alt buffer into scroll-back mode.
 *
 * @param {object} altBuf       xterm alt Buffer
 * @param {string[]} capturedLines  Full pane buffer (plain text from tmux)
 * @param {number} scrollPos    Lines scrolled above live (0 = at live)
 * @param {number} cols
 * @param {number} rows
 * @param {object} selModel     Optional xterm selection model
 * @returns saved state for restoration
 */
function enterScrollbackState(altBuf, capturedLines, scrollPos, cols, rows, selModel) {
  const saved = {
    lines: altBuf.lines,
    ybase: altBuf.ybase,
    ydisp: altBuf.ydisp,
    hasScrollback: altBuf._hasScrollback,
  };

  // Empty stub for altLines — captured already contains the full buffer.
  const emptyAltLines = {
    get: () => undefined,
    length: 0, maxLength: 0, isFull: true,
    set() {}, push() {}, pop() { return undefined; },
    recycle() { return undefined; },
    splice() {}, trimStart() {}, shiftElements() {},
    onDeleteEmitter: saved.lines.onDeleteEmitter,
    onDelete: saved.lines.onDelete,
    onInsertEmitter: saved.lines.onInsertEmitter,
    onInsert: saved.lines.onInsert,
    onTrimEmitter: saved.lines.onTrimEmitter,
    onTrim: saved.lines.onTrim,
  };

  const combined = new CombinedCircularList(capturedLines, emptyAltLines, altBuf, cols);
  altBuf.lines = combined;
  altBuf.ybase = Math.max(0, capturedLines.length - rows);
  // User is scrollPos lines above live → viewport starts at ybase - scrollPos.
  altBuf.ydisp = Math.max(0, altBuf.ybase - scrollPos);
  altBuf._hasScrollback = true;

  // Offset selection coordinates: the user's click at (col, alt_row) with
  // their original ydisp=0 corresponds to (col, new_ydisp + alt_row) in
  // the combined buffer.
  const offset = altBuf.ydisp;
  if (selModel?.selectionStart) selModel.selectionStart[1] += offset;
  if (selModel?.selectionEnd) selModel.selectionEnd[1] += offset;

  return saved;
}

function exitScrollbackState(altBuf, saved) {
  altBuf.lines = saved.lines;
  altBuf.ybase = saved.ybase;
  altBuf.ydisp = saved.ydisp;
  altBuf._hasScrollback = saved.hasScrollback;
}

function computeEdgeScrollAmount(clientY, rect, edgeZone = 50) {
  const distTop = clientY - rect.top;
  const distBot = rect.bottom - clientY;
  if (distTop < edgeZone) return -1;
  if (distBot < edgeZone) return 1;
  return 0;
}

module.exports = {
  enterScrollbackState,
  exitScrollbackState,
  computeEdgeScrollAmount,
};
