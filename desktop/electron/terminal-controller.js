/**
 * Terminal selection + write-buffer controller for the xterm.js instance
 * hosted in preload.js.
 *
 * Responsibilities:
 *   1. Auto-copy a non-empty selection to the clipboard whenever it changes,
 *      giving iTerm2-style "select-to-copy" behavior.
 *   2. Buffer PTY writes while a selection is active so incoming output
 *      can't scroll / redraw it away.
 *   3. Flush the buffer after the selection clears, but delayed by
 *      FLUSH_DELAY_MS so a rapid re-select (mouseup races, Cmd+C followed
 *      by another drag) can cancel the flush before it destroys the
 *      visual selection.
 *   4. Cmd+C / Cmd+V / Cmd+A handling via xterm's custom key handler.
 *
 * Also exports stripMouseModes(): strips CSI ? ... h/l sequences that
 * would enable xterm.js mouse tracking. Claude Code (via tmux) normally
 * sends \x1b[?1002;1006h to enable SGR mouse mode; if that leaks through,
 * xterm starts consuming mouse events as reports and click-drag stops
 * selecting text.
 *
 * Exported as a factory so unit tests can drive it with a mock Terminal
 * and a mock clipboard — no DOM or canvas required.
 */

const FLUSH_DELAY_MS = 80;
// Upper bound on data buffered while a terminal selection is held. A chatty
// agent with a forgotten selection can stream megabytes per second; without
// a cap the buffer grows unbounded until the selection clears.
const WRITE_BUFFER_MAX_BYTES = 2 * 1024 * 1024;

// DEC private modes to strip from incoming PTY data.
//
// Mouse-tracking modes (1000–1016, 9): if xterm receives one of these
// with `h` (set), it starts reporting mouse events and click-drag stops
// selecting text.
//
// NOTE: alternate-screen-buffer modes (47, 1047, 1049) were previously
// stripped here to keep xterm in normal-buffer mode for drag-scroll across
// multiple screens.  That caused a critical auto-scroll bug: tmux expects
// alt-screen, and in normal-buffer mode its rendering operations accumulate
// scrollback (ybase grows), making the viewport drift on every keystroke.
// Alt-screen stripping + scrollback injection are now disabled; drag-scroll
// across screens will be revisited with an on-demand buffer-switch approach.
const STRIP_MODES = new Set([
  // Mouse tracking
  "9",    // X10 mouse
  "1000", // X11 button press/release tracking
  "1001", // Highlight mouse tracking
  "1002", // Cell motion tracking (button held)
  "1003", // All motion tracking
  "1004", // Focus in/out reporting
  "1005", // UTF-8 mouse mode
  "1006", // SGR mouse mode
  "1015", // urxvt mouse mode
  "1016", // Pixel-position mouse mode
]);

/**
 * Strip mouse-tracking and alternate-screen-buffer CSI private-mode
 * sequences from incoming PTY data.
 *
 * Handles both single-param (\x1b[?1000h) and multi-param (\x1b[?1002;1006h)
 * forms. For mixed sequences where some params are stripped and others
 * are not, the non-stripped params are preserved.
 *
 * Note: this is ONE of two layers that defend selection — see
 * forceSelectionEnabled() for the other.
 */
function stripMouseModes(data) {
  if (typeof data !== "string") return data;
  return data.replace(/\x1b\[\?([\d;]+)([hl])/g, (match, params, terminator) => {
    const paramList = params.split(";");
    const kept = paramList.filter((p) => !STRIP_MODES.has(p));
    if (kept.length === paramList.length) return match; // nothing to strip
    if (kept.length === 0) return ""; // every param was stripped
    return `\x1b[?${kept.join(";")}${terminator}`;
  });
}

/**
 * Defensive layer: monkey-patch xterm.js internals so mouse tracking can
 * NEVER be turned on, regardless of what leaks through stripMouseModes.
 *
 * Why this is necessary: xterm.js exposes the gate
 *   `coreMouseService.areMouseEventsActive`
 * and whenever it's true, the SelectionService is disabled — left-click-drag
 * starts reporting mouse events to the PTY instead of selecting text. Once
 * disabled, a re-enable only happens on a new options change. That is the
 * source of the "selection doesn't work" bug: some CSI sequence sets the
 * active mouse protocol to "VT200"/"DRAG"/"ANY" and the selection service
 * never comes back until a reload.
 *
 * The fix: force `areMouseEventsActive` to always return false (selection
 * service always enabled), and force `activeProtocol` setters to be no-ops
 * so nothing can flip the underlying state.
 */
function forceSelectionEnabled(term) {
  try {
    const core = term && term._core;
    const coreMouseService = core && core._coreMouseService;
    const selectionService = core && core._selectionService;

    if (coreMouseService) {
      // Lock areMouseEventsActive to false.
      Object.defineProperty(coreMouseService, "areMouseEventsActive", {
        get: () => false,
        configurable: true,
      });
      // Lock activeProtocol to "NONE" so setModePrivate can't re-enable
      // mouse tracking via ?9/?1000/?1002/?1003.
      let _protocolBacking = "NONE";
      Object.defineProperty(coreMouseService, "activeProtocol", {
        get: () => _protocolBacking,
        set: () => { _protocolBacking = "NONE"; },
        configurable: true,
      });
    }

    if (selectionService && typeof selectionService.enable === "function") {
      selectionService.enable();
      // Prevent xterm from disabling it on subsequent refresh cycles.
      const _origDisable = selectionService.disable?.bind(selectionService);
      selectionService.disable = () => {}; // swallow disable calls
      selectionService._origDisable = _origDisable; // kept for debugging
    }

    // xterm adds "enable-mouse-events" to the element when mouse tracking
    // is on; that CSS class flips cursor + pointer behavior. Remove it and
    // prevent it from being added back.
    const el = core && core.element;
    if (el) {
      el.classList.remove("enable-mouse-events");
      const _origAdd = el.classList.add.bind(el.classList);
      el.classList.add = (...names) => {
        _origAdd(...names.filter((n) => n !== "enable-mouse-events"));
      };
    }
  } catch {
    // Best-effort; if xterm internals change we fall back to stripMouseModes.
  }
}

function createTerminalController({
  term,
  clipboard,
  flushDelayMs = FLUSH_DELAY_MS,
  setTimeoutFn = setTimeout,
  clearTimeoutFn = clearTimeout,
  logger = null,
}) {
  // Optional diagnostic logger — set via window.__termDebug in devtools.
  // No-op by default to keep production logs quiet.
  const log = (...a) => { if (logger) logger(...a); };

  // Shared array between onSelectionChange and writeBuffered.  We keep the
  // same reference forever (writeBuffer.length = 0 to clear) so that any
  // caller holding onto the reference sees the live state.
  const writeBuffer = [];
  let writeBufferBytes = 0;
  let flushTimer = null;

  const cancelFlush = () => {
    if (flushTimer) {
      clearTimeoutFn(flushTimer);
      flushTimer = null;
    }
  };

  const scheduleFlush = () => {
    if (flushTimer) return;
    flushTimer = setTimeoutFn(() => {
      flushTimer = null;
      // Re-check: a new selection may have started during the delay.
      if (!term.hasSelection() && writeBuffer.length > 0) {
        term.write(writeBuffer.join(""));
        writeBuffer.length = 0;
        writeBufferBytes = 0;
      }
    }, flushDelayMs);
  };

  term.onSelectionChange(() => {
    if (term.hasSelection()) {
      // Auto-copy on every selection change.  Cheap, and matches iTerm2.
      clipboard.writeText(term.getSelection());
      cancelFlush();
    } else if (writeBuffer.length > 0) {
      scheduleFlush();
    }
  });

  term.attachCustomKeyEventHandler((e) => {
    if (e.type !== "keydown") return true;
    const mod = e.metaKey || e.ctrlKey;
    if (!mod) return true;

    if (e.key === "c" || e.key === "C") {
      if (term.hasSelection()) {
        clipboard.writeText(term.getSelection());
        term.clearSelection();
        e.preventDefault();
        return false;
      }
      // No selection — let xterm forward Ctrl+C (SIGINT) to the PTY.
      return true;
    }

    if (e.key === "a" || e.key === "A") {
      term.selectAll();
      e.preventDefault();
      return false;
    }

    if (e.key === "v" || e.key === "V") {
      // CRITICAL: do NOT call e.preventDefault() here.
      //
      // In modern Chromium/Electron, calling preventDefault() on the
      // keydown for Cmd+V cancels the default editing action, which
      // means the "paste" event is NEVER dispatched on the textarea.
      // That breaks our entire paste pipeline — handlePasteEvent would
      // silently never fire and neither text nor images would paste.
      //
      // Returning false here tells xterm.js's _keyDown to bail out
      // before touching the event (see xterm.js source: after
      // `!1 === this._customKeyEventHandler(e)` it returns early and
      // does no preventDefault / insertion / composition / anything).
      // Chromium then runs the default Cmd+V action, which dispatches
      // a `paste` event — picked up by our capture-phase listener on
      // the hidden textarea in preload.js, which routes everything
      // through paste-handlers.handlePasteEvent.
      return false;
    }

    return true;
  });

  function writeBuffered(data) {
    if (term.hasSelection()) {
      const len = (data && data.length) || 0;
      if (writeBufferBytes + len > WRITE_BUFFER_MAX_BYTES) {
        // Selection held too long + too much PTY output pending. Drop the
        // selection so the buffer flushes rather than growing unboundedly.
        const pending = writeBuffer.join("");
        writeBuffer.length = 0;
        writeBufferBytes = 0;
        try { term.clearSelection(); } catch {}
        term.write(pending + data);
        return;
      }
      writeBuffer.push(data);
      writeBufferBytes += len;
    } else {
      term.write(data);
    }
  }

  return {
    writeBuffered,
    // Exposed for tests / debugging.
    _internals: {
      get buffer() { return writeBuffer.slice(); },
      get flushPending() { return flushTimer !== null; },
    },
  };
}

/**
 * Tear down an xterm Terminal instance, swallowing errors from the
 * underlying ``term.dispose()`` call.
 *
 * Why this exists: the xtermBridge singleton in preload.js holds a
 * module-private ``_term`` reference. Its ``dispose()`` step has been
 * known to throw (xterm internal state corruption, double-dispose, etc.).
 * If that throws, the next ``init()`` call sees ``_term`` still truthy
 * and short-circuits — leaving the singleton attached to a dead Terminal
 * with a detached DOM element. Wrapping the call here means callers can
 * safely null their reference unconditionally.
 *
 * Called by: preload.js xtermBridge.dispose
 * Tested by: test/terminal-controller.test.js (B3 regression)
 */
function disposeTerminalSafely(term) {
  if (!term) return;
  try { term.dispose(); } catch { /* swallow — caller still nulls its ref */ }
}

module.exports = {
  createTerminalController,
  stripMouseModes,
  forceSelectionEnabled,
  disposeTerminalSafely,
  FLUSH_DELAY_MS,
};
