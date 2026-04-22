// Attach-burst buffering: coalesce PTY chunks from tmux attach-session
// into a single write so xterm paints the final pane state in one frame.
//
// Usage:
//   const burst = createAttachBurst({ term, idleMs, maxMs });
//   burst.start();            // arm the buffer
//   burst.write(data);        // returns true if buffered, false if not active
//   burst.flush();            // force-flush
//   burst.isActive();         // check if burst is armed

function createAttachBurst({
  term,
  idleMs = 150,
  maxMs = 1500,
  setTimeoutFn = setTimeout,
  clearTimeoutFn = clearTimeout,
} = {}) {
  let _active = false;
  let _buffer = [];
  let _idleTimer = null;
  let _hardTimer = null;
  let _onFlush = null;

  function flush() {
    if (_idleTimer) { clearTimeoutFn(_idleTimer); _idleTimer = null; }
    if (_hardTimer) { clearTimeoutFn(_hardTimer); _hardTimer = null; }
    _active = false;
    if (_buffer.length === 0 || !term) { _buffer = []; return; }
    const merged = _buffer.join("");
    _buffer = [];
    term.write(merged);
    if (_onFlush) _onFlush(merged);
  }

  function bumpIdle() {
    if (_idleTimer) clearTimeoutFn(_idleTimer);
    _idleTimer = setTimeoutFn(flush, idleMs);
  }

  return {
    start() {
      _active = true;
      _buffer = [];
      if (_idleTimer) { clearTimeoutFn(_idleTimer); _idleTimer = null; }
      if (_hardTimer) clearTimeoutFn(_hardTimer);
      _hardTimer = setTimeoutFn(flush, maxMs);
    },
    write(data) {
      if (!_active) return false;
      _buffer.push(data);
      bumpIdle();
      return true;
    },
    flush,
    isActive() { return _active; },
    getBuffer() { return _buffer.slice(); },
    onFlush(cb) { _onFlush = cb; },
  };
}

module.exports = { createAttachBurst };
