/**
 * Behavioural tests for paper-reader.js — driven through a real DOM
 * via jsdom. Complements the static-analysis suite in
 * paper-reader.test.js.
 *
 * Strategy: load paper-reader.js into a jsdom window, inject the
 * globals it expects (serverPort, showToast, fetch), and synthesise
 * the state it would have mid-session (a ``_reader`` with page
 * elements + mock viewports). Then exercise the selection/save/
 * delete/copy flows and assert on the resulting fetch calls, DOM
 * mutations, and toast invocations.
 *
 * Run: cd desktop && node --test test/paper-reader-dom.test.js
 */

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { JSDOM } = require("jsdom");

const SOURCE = readFileSync(
  resolve(__dirname, "../renderer/paper-reader.js"),
  "utf-8",
);


// ─── Test harness ───────────────────────────────────────────────────────

/**
 * Build a jsdom window loaded with paper-reader.js and a working
 * ``_reader`` state. Returns ``{window, calls}`` where ``calls`` is
 * an object accumulating captured fetch requests, toast calls, and
 * clipboard writes so tests can assert on what happened.
 */
function setupReader({
  numPages = 2,
  pageWidth = 612,
  pageHeight = 792,
  viewportConvertToPdfPoint = null,
  fetchResponse = { ok: true, saved_to_pdf: true, zotero_status: "not_configured", annot_ids: ["distillate-test1"] },
} = {}) {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="experiment-detail"></div>
  </body></html>`, {
    url: "http://127.0.0.1:8742/ui/",
    runScripts: "dangerously",
  });

  const { window } = dom;
  const { document } = window;

  // Capture side effects for assertions.
  const calls = {
    fetches: [],
    toasts: [],
    clipboardWrites: [],
    consoleWarnings: [],
  };

  // Inject globals paper-reader.js references.
  window.serverPort = 8742;
  window.showToast = (msg, type) => {
    calls.toasts.push({ msg, type: type || "error" });
  };
  window.fetchPapersData = () => {};
  window.fetch = async (url, opts) => {
    const body = opts && opts.body ? JSON.parse(opts.body) : null;
    calls.fetches.push({
      url,
      method: (opts && opts.method) || "GET",
      body,
    });
    // Default response; tests can override via fetchResponse.
    return {
      ok: fetchResponse.ok !== false,
      status: fetchResponse.status || 200,
      json: async () => fetchResponse,
      arrayBuffer: async () => new ArrayBuffer(0),
    };
  };
  window.navigator = window.navigator || {};
  Object.defineProperty(window.navigator, "clipboard", {
    value: {
      writeText: async (text) => {
        calls.clipboardWrites.push(text);
      },
    },
    writable: true,
    configurable: true,
  });

  // Silence (and capture) console.warn noise.
  window.console.warn = (...args) => calls.consoleWarnings.push(args);

  // Load paper-reader.js in the window context. The file uses top-level
  // `let`/`async function` declarations; those go on the jsdom window
  // when the script executes.
  //
  // We append a small exposure block so tests can call the internal
  // functions (they're not on window by default — only openPaperReader
  // and closePaperReader are).
  const script = document.createElement("script");
  script.textContent = SOURCE + `
    window._handleSelectionChange = _handleSelectionChange;
    window._saveHighlight = _saveHighlight;
    window._copySelection = _copySelection;
    window._deleteHighlight = _deleteHighlight;
    window._hideSaveButton = _hideSaveButton;
  `;
  document.head.appendChild(script);

  // Build a fake ``_reader`` state that paper-reader.js's save/delete
  // handlers expect. Each page is a box at a known viewport position.
  const pagesHost = document.createElement("div");
  pagesHost.className = "paper-reader-pages";
  document.body.appendChild(pagesHost);

  const pageEls = [];
  const pageViewports = [];
  const PAGE_TOP_GAP = 24;
  for (let i = 0; i < numPages; i++) {
    const pageEl = document.createElement("div");
    pageEl.className = "paper-reader-page";
    pageEl.dataset.pageNum = String(i + 1);

    const overlay = document.createElement("div");
    overlay.className = "paper-reader-overlay";
    pageEl.appendChild(overlay);

    // Stub getBoundingClientRect so `_handleSelectionChange`'s
    // page-containment check works.
    const pageTop = i * (pageHeight + PAGE_TOP_GAP);
    pageEl.getBoundingClientRect = () => ({
      left: 0,
      top: pageTop,
      right: pageWidth,
      bottom: pageTop + pageHeight,
      width: pageWidth,
      height: pageHeight,
      x: 0,
      y: pageTop,
    });
    pagesHost.appendChild(pageEl);
    pageEls.push(pageEl);

    // Provide a default convertToPdfPoint that just flips Y (so a
    // viewport-space (x, y_top) → PDF-space (x, pageH - y_top)).
    // Tests can override by passing viewportConvertToPdfPoint.
    const viewport = {
      scale: 1,
      width: pageWidth,
      height: pageHeight,
      convertToPdfPoint: viewportConvertToPdfPoint
        || ((x, y) => [x, pageHeight - y]),
      // Inverse: PDF-space rect [x0, y0_bl, x1, y1_bl] → viewport-space
      // rect [vx1, vy1, vx2, vy2] with y flipped.
      convertToViewportRectangle: (rect) => {
        const [x0, y0_bl, x1, y1_bl] = rect;
        return [x0, pageHeight - y1_bl, x1, pageHeight - y0_bl];
      },
    };
    pageViewports.push(viewport);
  }

  // paper-reader.js stores state in a module-local `let _reader`. The
  // variable is not exposed on `window` directly. We can't mutate it
  // from outside via JS, but we *can* run another <script> in the
  // same window that reaches into the closure by using the same
  // top-level declaration. However — because paper-reader.js uses
  // `let _reader = null` at module scope, a second script's
  // `_reader = {...}` will actually re-assign the module-scope var
  // (since they share the same global script scope in jsdom's script-tag
  // execution). This is browser-script semantics.
  const stateScript = document.createElement("script");
  stateScript.textContent = `
    _reader = {
      paperKey: "TEST-PAPER",
      pdfDoc: null,
      pageEls: [],
      pageViewports: [],
      annotationsByPage: new Map(),
      visiblePage: 1,
      savedPage: 1,
      saveTimer: null,
      observer: null,
      host: document.body,
      pagesHost: null,
      pageCounter: null,
      selectionHandler: null,
      saveButton: null,
      pendingHighlight: null,
      zoomLevel: 1,
      baseScale: 1,
      textContents: [],
      searchMatches: [],
      searchIdx: -1,
      searchHighlights: [],
    };
    window._reader = _reader;  // expose for test assertions
  `;
  document.head.appendChild(stateScript);

  // Overwrite with real DOM references.
  dom.window._reader.pagesHost = pagesHost;
  dom.window._reader.pageEls = pageEls;
  dom.window._reader.pageViewports = pageViewports;

  return { dom, window, document, calls };
}

/**
 * Build a jsdom Selection + Range that spans a virtual rectangle on a
 * given page. The selection reports a single getClientRects entry at
 * the requested viewport coordinates; ``toString()`` returns the text.
 */
function makeSelection(document, pageEl, { left, top, right, bottom, text }) {
  // Create a span inside the page's overlay so it's "inside the reader".
  const overlay = pageEl.querySelector(".paper-reader-overlay");
  const anchor = document.createElement("span");
  anchor.textContent = text;
  overlay.appendChild(anchor);

  // Build a minimal Range-like object. jsdom's real Range doesn't
  // expose getClientRects in a way we can control, so we return a
  // fake Range that satisfies paper-reader.js's usage.
  const range = {
    commonAncestorContainer: anchor,
    getClientRects: () => {
      const rects = [{
        left, top, right, bottom,
        width: right - left,
        height: bottom - top,
      }];
      rects.length = 1;
      rects[Symbol.iterator] = function* () {
        for (let i = 0; i < this.length; i++) yield this[i];
      };
      return rects;
    },
  };

  const selection = {
    isCollapsed: false,
    rangeCount: 1,
    getRangeAt: () => range,
    toString: () => text,
    removeAllRanges: () => {
      selection.isCollapsed = true;
      selection.rangeCount = 0;
    },
  };

  return { selection, anchor };
}


// ─── 1. _handleSelectionChange — builds correct pdfRects ───────────────

describe("_handleSelectionChange — valid selection", () => {
  it("sets pendingHighlight and shows the menu", () => {
    const { window, document, calls } = setupReader();

    const pageEl = window._reader.pageEls[0];
    const { selection } = makeSelection(document, pageEl, {
      left: 72, top: 100,
      right: 540, bottom: 112,
      text: "Sample sentence.",
    });
    window.getSelection = () => selection;

    window._handleSelectionChange();

    const pending = window._reader.pendingHighlight;
    assert.ok(pending, "pendingHighlight should be populated for a valid selection");
    assert.equal(pending.page_index, 0);
    assert.equal(pending.text, "Sample sentence.");
    assert.ok(Array.isArray(pending.rects));
    assert.equal(pending.rects.length, 1);
    assert.equal(pending.rects[0].length, 4);
    // convertToPdfPoint in our default shim: (x, pageH - y) = bottom-left flip
    // so top-left (72, 100) → (72, 692), bottom-right (540, 112) → (540, 680)
    // pdfRects format: [min_x, min_y, max_x, max_y]
    assert.equal(pending.rects[0][0], 72);   // x0
    assert.equal(pending.rects[0][1], 680);  // y0 (min of 692, 680)
    assert.equal(pending.rects[0][2], 540);  // x1
    assert.equal(pending.rects[0][3], 692);  // y1

    // Save menu should be visible.
    const menu = document.querySelector(".paper-reader-select-menu");
    assert.ok(menu, "selection menu should be appended to body");
    assert.equal(menu.style.display, "flex");
  });
});


// ─── 2. NaN-producing viewport is filtered ─────────────────────────────

describe("_handleSelectionChange — NaN guard", () => {
  it("drops rects when convertToPdfPoint returns NaN", () => {
    const { window, document, calls } = setupReader({
      viewportConvertToPdfPoint: () => [NaN, NaN],
    });

    const pageEl = window._reader.pageEls[0];
    const { selection } = makeSelection(document, pageEl, {
      left: 72, top: 100,
      right: 540, bottom: 112,
      text: "NaN text",
    });
    window.getSelection = () => selection;
    window._handleSelectionChange();

    // pendingHighlight should NOT be set (no valid rects).
    assert.equal(window._reader.pendingHighlight, null,
      "pendingHighlight should not be set when all rects filtered");

    // Console warning should have fired.
    assert.ok(
      calls.consoleWarnings.some((args) =>
        String(args[0] || "").includes("skipping rect with non-finite coords")
      ),
      "expected console.warn for non-finite rect",
    );
  });
});


// ─── 3. Empty / collapsed selection hides menu ─────────────────────────

describe("_handleSelectionChange — empty / collapsed selections", () => {
  it("hides the save button when selection is collapsed", () => {
    const { window, document } = setupReader();
    window.getSelection = () => ({ isCollapsed: true, rangeCount: 0 });
    window._handleSelectionChange();
    assert.equal(window._reader.pendingHighlight, null);
  });

  it("hides when toString() returns empty after trim", () => {
    const { window, document } = setupReader();
    const pageEl = window._reader.pageEls[0];
    const { selection } = makeSelection(document, pageEl, {
      left: 72, top: 100, right: 540, bottom: 112,
      text: "   ",  // whitespace only
    });
    window.getSelection = () => selection;
    window._handleSelectionChange();
    assert.equal(window._reader.pendingHighlight, null);
  });

  it("hides when selection is outside the pagesHost", () => {
    const { window, document } = setupReader();
    const outsideEl = document.createElement("div");
    outsideEl.textContent = "outside";
    document.body.appendChild(outsideEl);
    const selection = {
      isCollapsed: false, rangeCount: 1,
      getRangeAt: () => ({
        commonAncestorContainer: outsideEl,
        getClientRects: () => [],
      }),
      toString: () => "outside",
      removeAllRanges: () => {},
    };
    window.getSelection = () => selection;
    window._handleSelectionChange();
    assert.equal(window._reader.pendingHighlight, null);
  });
});


// ─── 4. Save handler sends correct POST ─────────────────────────────────

describe("_saveHighlight — POSTs correct payload", () => {
  it("fires POST /annotations with pendingHighlight in the body", async () => {
    const { window, calls } = setupReader({
      fetchResponse: {
        ok: true, saved_to_pdf: true, zotero_status: "synced",
        annot_ids: ["distillate-abc"], zotero_keys: ["ZK1"],
      },
    });
    const sentHighlight = {
      text: "hello",
      page_index: 0,
      page_label: "1",
      rects: [[72, 700, 300, 712]],
      color: "#ffd400",
      sort_index: "00000|000000|00000",
    };
    window._reader.pendingHighlight = sentHighlight;
    await window._saveHighlight();

    assert.equal(calls.fetches.length, 1);
    const req = calls.fetches[0];
    assert.match(req.url, /\/papers\/TEST-PAPER\/annotations$/);
    assert.equal(req.method, "POST");
    // Capture the sent payload; pendingHighlight is cleared by save.
    assert.deepEqual(req.body, { highlights: [sentHighlight] });

    // Success toast, green.
    assert.equal(calls.toasts.length, 1);
    assert.equal(calls.toasts[0].type, "success");
    assert.match(calls.toasts[0].msg, /synced to Zotero/);
  });

  it("shows yellow warning toast when zotero_status === 'failed'", async () => {
    const { window, calls } = setupReader({
      fetchResponse: {
        ok: true, saved_to_pdf: true, zotero_status: "failed",
        annot_ids: ["distillate-abc"], zotero_keys: [],
      },
    });
    window._reader.pendingHighlight = {
      text: "hello",
      page_index: 0,
      rects: [[72, 700, 300, 712]],
      color: "#ffd400",
    };
    await window._saveHighlight();

    assert.equal(calls.toasts.length, 1);
    assert.equal(calls.toasts[0].type, "warning",
      "Zotero sync failed → yellow warning toast");
    assert.match(calls.toasts[0].msg, /Zotero sync failed/);
  });

  it("still renders optimistically when server returns failure", async () => {
    // Server error — highlight should still appear on the overlay so
    // the user's action isn't lost visually.
    const { window, calls } = setupReader({
      fetchResponse: { ok: false, status: 500, reason: "pdf_write_failed" },
    });
    window._reader.pendingHighlight = {
      text: "hello",
      page_index: 0,
      rects: [[72, 700, 300, 712]],
      color: "#ffd400",
    };
    await window._saveHighlight();
    // An error toast fired.
    assert.ok(calls.toasts.some((t) => t.msg && t.msg.includes("Couldn't save")));
    // Overlay has a new highlight element (optimistic render).
    const overlay = window._reader.pageEls[0].querySelector(
      ".paper-reader-overlay"
    );
    assert.ok(
      overlay.querySelector(".paper-reader-highlight"),
      "optimistic render should add a highlight div even on save failure",
    );
  });
});


// ─── 5. Copy handler writes to clipboard ───────────────────────────────

describe("_copySelection — clipboard + toast + clear selection", () => {
  it("writes the selection text to navigator.clipboard", async () => {
    const { window, calls } = setupReader();
    window._reader.pendingHighlight = {
      text: "clipboard candidate",
      page_index: 0,
      rects: [[72, 700, 300, 712]],
      color: "#ffd400",
    };
    // Stub getSelection().removeAllRanges()
    const removeAllRanges = () => { removeAllRanges.called = true; };
    removeAllRanges.called = false;
    window.getSelection = () => ({ removeAllRanges });

    await window._copySelection();

    assert.deepEqual(calls.clipboardWrites, ["clipboard candidate"]);
    assert.equal(calls.toasts.length, 1);
    assert.equal(calls.toasts[0].type, "success");
    assert.match(calls.toasts[0].msg, /Copied/);
    assert.ok(removeAllRanges.called, "selection should be cleared after copy");
  });

  it("shows error toast if clipboard write rejects", async () => {
    const { window, calls } = setupReader();
    window.navigator.clipboard.writeText = async () => {
      throw new Error("permission denied");
    };
    window._reader.pendingHighlight = {
      text: "nope",
      page_index: 0,
      rects: [[72, 700, 300, 712]],
      color: "#ffd400",
    };
    window.getSelection = () => ({ removeAllRanges: () => {} });

    await window._copySelection();

    const errors = calls.toasts.filter((t) => t.type !== "success");
    assert.ok(errors.length >= 1);
    assert.match(errors[0].msg, /Couldn't copy/);
  });
});


// ─── 6. Delete handler sends correct DELETE ─────────────────────────────

describe("_deleteHighlight — DELETEs with /NM id", () => {
  it("fires DELETE with the annotation's id in the body", async () => {
    const { window, calls } = setupReader({
      fetchResponse: { ok: true, removed_pdf: true },
    });
    // Create a fake annotation element + descriptor.
    const ann = {
      id: "distillate-abc123",
      text: "some highlight",
      page_index: 0,
      rects: [[72, 700, 300, 712]],
      color: "#ffd400",
    };
    const overlay = window._reader.pageEls[0].querySelector(
      ".paper-reader-overlay"
    );
    const hlEl = window.document.createElement("div");
    hlEl.className = "paper-reader-highlight";
    overlay.appendChild(hlEl);
    // Pre-populate the cache so the delete handler can remove it.
    window._reader.annotationsByPage.set(0, [ann]);

    await window._deleteHighlight(ann, [hlEl]);

    assert.equal(calls.fetches.length, 1);
    const req = calls.fetches[0];
    assert.equal(req.method, "DELETE");
    assert.match(req.url, /\/papers\/TEST-PAPER\/annotations$/);
    assert.equal(req.body.id, "distillate-abc123");
    assert.equal(req.body.text, "some highlight");
    assert.equal(req.body.page_index, 0);

    // Optimistic: the DOM element is removed immediately.
    assert.equal(overlay.querySelector(".paper-reader-highlight"), null);
    // Cache entry is removed.
    assert.deepEqual(window._reader.annotationsByPage.get(0), []);
    // Toast was success.
    assert.equal(calls.toasts.length, 1);
    assert.equal(calls.toasts[0].type, "success");
  });
});
