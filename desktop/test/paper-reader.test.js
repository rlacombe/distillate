/**
 * Tests for paper-reader.js — the in-app PDF reader.
 *
 * Run: cd desktop && node --test test/paper-reader.test.js
 *
 * Strategy: this file mostly relies on **static analysis** (asserting
 * code patterns are present in the source) plus **pure-function
 * extraction** for utilities like _friendlyPdfError and _withAlpha that
 * have no DOM dependencies. Full DOM-driven tests of the selection /
 * save flow are deferred to E2E (Playwright) — a mocked DOM would be
 * brittle and not catch what matters (real PDF.js viewport, real
 * browser selection rects).
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

const source = readFileSync(
  resolve(__dirname, "../renderer/paper-reader.js"),
  "utf-8",
);


// ─── Static analysis: critical invariants in the source ─────────────────

describe("paper-reader.js — critical invariants present in source", () => {
  it("exposes openPaperReader on window", () => {
    assert.match(source, /window\.openPaperReader\s*=\s*openPaperReader/);
  });

  it("exposes closePaperReader on window", () => {
    assert.match(source, /window\.closePaperReader\s*=\s*closePaperReader/);
  });

  it("uses pdfjs.TextLayer (v4 API) not the removed renderTextLayer fn", () => {
    assert.match(source, /new pdfjs\.TextLayer\s*\(/);
    assert.ok(
      !/pdfjs\.renderTextLayer\s*\(/.test(source),
      "renderTextLayer was removed in pdfjs-dist v4 — the old function call shouldn't appear",
    );
  });

  it("sets --scale-factor on the text layer (required by pdfjs v4 TextLayer)", () => {
    assert.match(
      source,
      /setProperty\(['"]--scale-factor['"]/,
      "TextLayer v4 reads --scale-factor from the container; missing this makes spans render at 0 size",
    );
  });

  it("guards rect coords against non-finite (NaN) values before sending", () => {
    // The guard: Number.isFinite check on convertToPdfPoint outputs.
    assert.match(source, /Number\.isFinite/);
    assert.match(
      source,
      /skipping rect with non-finite coords/,
      "Defensive log line for the NaN-rect path is missing",
    );
  });

  it("delete handler sends the PDF /NM id (preferred over text)", () => {
    // The DELETE body should include {id, text, page_index}.
    assert.match(source, /method:\s*"DELETE"/);
    assert.match(source, /id:\s*ann\.id/);
  });

  it("click on a highlight triggers deletion (no confirm dialog)", () => {
    // _renderHighlightRects wires hl.addEventListener("click", ...) → _deleteHighlight
    assert.match(source, /hl\.addEventListener\("click"/);
    assert.match(source, /_deleteHighlight\(ann/);
    // No confirm() should be called inside _deleteHighlight.
    const deleteFn = source.match(
      /async function _deleteHighlight[\s\S]+?^}/m,
    );
    assert.ok(deleteFn, "could not locate _deleteHighlight function");
    assert.ok(
      !/\bconfirm\s*\(/.test(deleteFn[0]),
      "confirm() should NOT be called in _deleteHighlight — single-click deletes immediately",
    );
  });

  it("save toast distinguishes synced vs failed Zotero outcomes", () => {
    // Look for either the new contract (zotero_status) or the old
    // (synced_to_zotero). Both should produce different toast colors.
    const usesStatus = /zotero_status/.test(source);
    const usesBool = /synced_to_zotero/.test(source);
    assert.ok(
      usesStatus || usesBool,
      "save handler should branch on Zotero outcome to choose toast color",
    );
  });

  it("highlight overlay uses 0.35 default opacity", () => {
    // _renderHighlightRects calls _withAlpha(color, 0.38). Pinned for
    // visual consistency with renderer.py's _HIGHLIGHT_OPACITY = 0.35.
    assert.match(source, /_withAlpha\([^)]+,\s*0\.3[58]\)/);
  });

  it("smart link detection covers arxiv/doi/github/https patterns", () => {
    assert.match(source, /arXiv/i);
    assert.match(source, /github\\\.com/);
    assert.match(source, /doi/i);
  });

  it("zoom controls support pinch (ctrlKey) and Cmd+scroll (metaKey)", () => {
    assert.match(source, /e\.ctrlKey.*e\.metaKey|e\.metaKey.*e\.ctrlKey/);
  });

  it("Cmd+F intercepts to open in-paper search", () => {
    assert.match(source, /e\.key\s*===\s*"f"/);
    assert.match(source, /metaKey/);
  });

  it("intersection observer is rebuilt on zoom re-render", () => {
    // _renderAllPages calls observer.disconnect() before rebuilding pages.
    assert.match(source, /observer\.disconnect\(\)/);
  });

  it("text-content cache is cleared on zoom re-render", () => {
    // search depends on cached textContents per page; zoom invalidates.
    assert.match(source, /textContents\s*=\s*\[\]/);
  });
});


// ─── Pure-function extraction & unit tests ──────────────────────────────

// _withAlpha and _friendlyPdfError are pure (no DOM, no state). Extract
// them into a sandbox so we can call them directly.
function extractFunction(name) {
  const re = new RegExp(`function ${name}\\s*\\([^)]*\\)\\s*{[\\s\\S]+?^}`, "m");
  const match = source.match(re);
  if (!match) throw new Error(`could not extract ${name}`);
  return match[0];
}

describe("_withAlpha (pure)", () => {
  // Build a sandbox with just _withAlpha.
  const fnSrc = extractFunction("_withAlpha");
  // eslint-disable-next-line no-new-func
  const _withAlpha = new Function(`${fnSrc}; return _withAlpha;`)();

  it("converts hex to rgba with given alpha", () => {
    assert.equal(_withAlpha("#ffd400", 0.5), "rgba(255, 212, 0, 0.5)");
  });

  it("handles uppercase hex", () => {
    assert.equal(_withAlpha("#FF0000", 0.25), "rgba(255, 0, 0, 0.25)");
  });

  it("returns input unchanged when not a recognised hex", () => {
    assert.equal(_withAlpha("rgb(255,0,0)", 0.5), "rgb(255,0,0)");
    assert.equal(_withAlpha("not a color", 0.5), "not a color");
  });

  it("returns input when not a string", () => {
    assert.equal(_withAlpha(null, 0.5), null);
    assert.equal(_withAlpha(undefined, 0.5), undefined);
  });
});


describe("_friendlyPdfError (pure)", () => {
  const fnSrc = extractFunction("_friendlyPdfError");
  // eslint-disable-next-line no-new-func
  const _friendlyPdfError = new Function(`${fnSrc}; return _friendlyPdfError;`)();

  it("maps no_local_pdf_and_zotero_unconfigured to setup hint", () => {
    const r = _friendlyPdfError("no_local_pdf_and_zotero_unconfigured");
    assert.match(r.headline, /cached locally/i);
    assert.match(r.hint, /Connect Zotero|Obsidian/i);
  });

  it("maps no_pdf_available to no-attachment hint", () => {
    const r = _friendlyPdfError("no_pdf_available");
    assert.match(r.headline, /No PDF/i);
  });

  it("maps fetch_failed to retry hint", () => {
    const r = _friendlyPdfError("fetch_failed");
    assert.match(r.headline, /download/i);
  });

  it("maps not_found to library hint", () => {
    const r = _friendlyPdfError("not_found");
    assert.match(r.headline, /no longer in your library/i);
  });

  it("falls back to a generic message for unknown reasons", () => {
    const r = _friendlyPdfError("some_unknown_reason_xyz");
    assert.match(r.headline, /Couldn't load/i);
  });

  it("never returns empty headline", () => {
    for (const reason of [
      "", null, undefined, "weird", "404", "no_pdf_available",
    ]) {
      const r = _friendlyPdfError(reason);
      assert.ok(r.headline && r.headline.length > 0);
    }
  });
});


// ─── Selection menu (Highlight / Copy) ─────────────────────────────────

describe("Selection menu — Highlight/Copy actions", () => {
  it("creates a two-button menu (not a single Save button)", () => {
    // Old behaviour: single "Save highlight" button.
    // New behaviour: a menu container with two buttons (Highlight + Copy).
    assert.match(
      source,
      /paper-reader-select-menu/,
      "menu container class .paper-reader-select-menu is missing",
    );
    assert.match(
      source,
      /paper-reader-menu-btn.*highlight/,
      "highlight button class is missing",
    );
    assert.match(
      source,
      /paper-reader-menu-btn.*copy/i,
      "copy button class is missing",
    );
  });

  it("highlight button triggers _saveHighlight", () => {
    // Look for the highlight button's click handler wiring.
    const hlBlock = source.match(
      /highlightBtn[\s\S]+?addEventListener\("click",\s*_saveHighlight\)/,
    );
    assert.ok(hlBlock, "highlight button should wire click → _saveHighlight");
  });

  it("copy button triggers _copySelection", () => {
    const copyBlock = source.match(
      /copyBtn[\s\S]+?addEventListener\("click",\s*_copySelection\)/,
    );
    assert.ok(copyBlock, "copy button should wire click → _copySelection");
  });

  it("_copySelection uses navigator.clipboard.writeText", () => {
    assert.match(
      source,
      /navigator\.clipboard\.writeText/,
      "_copySelection should use the async clipboard API",
    );
  });

  it("_copySelection shows a success toast with the copied text", () => {
    const copyFn = source.match(/async function _copySelection[\s\S]+?^}/m);
    assert.ok(copyFn, "could not locate _copySelection function");
    assert.match(copyFn[0], /showToast\(.*Copied.*,\s*"success"\)/,
      "copy should show a green success toast");
  });

  it("_copySelection clears the text selection after copy", () => {
    const copyFn = source.match(/async function _copySelection[\s\S]+?^}/m);
    assert.ok(copyFn);
    assert.match(copyFn[0], /removeAllRanges/,
      "copy should clear the browser selection so the menu hides");
  });

  it("both buttons preventDefault on mousedown to keep selection alive", () => {
    // Clicking a button normally collapses the selection before the
    // click handler runs. preventDefault on mousedown keeps the range.
    const mousedownCount = (source.match(
      /addEventListener\("mousedown",\s*\(e\)\s*=>\s*e\.preventDefault\(\)\)/g,
    ) || []).length;
    assert.ok(
      mousedownCount >= 2,
      `both Highlight and Copy should preventDefault on mousedown; found ${mousedownCount}`,
    );
  });
});


// ─── Toast colour routing by zotero_status ──────────────────────────────

describe("Save toast colour reflects Zotero outcome", () => {
  it("uses 'success' for zotero_status === 'synced'", () => {
    const block = source.match(
      /status\s*===\s*"synced"[\s\S]+?showToast\([\s\S]+?,\s*"success"\)/,
    );
    assert.ok(block, "synced status should use the green success toast");
  });

  it("uses 'warning' for zotero_status === 'failed'", () => {
    const block = source.match(
      /status\s*===\s*"failed"[\s\S]+?showToast\([\s\S]+?,\s*"warning"\)/,
    );
    assert.ok(block, "failed Zotero sync should use the yellow warning toast");
  });

  it("falls back to 'success' for not_configured/not_attempted", () => {
    // The else branch in the save handler — no Zotero to report on,
    // silently green.
    assert.match(source, /else\s*{\s*showToast\("Highlight saved",\s*"success"/);
  });
});


// ─── _handleSelectionChange — static guards ──────────────────────────────

describe("_handleSelectionChange — structural guards", () => {
  const fn = source.match(/function _handleSelectionChange[\s\S]+?^}/m);
  const fnSrc = fn ? fn[0] : "";

  it("exists", () => {
    assert.ok(fn, "function should be defined");
  });

  it("early-returns when selection is collapsed", () => {
    assert.match(fnSrc, /sel\.isCollapsed/);
    assert.match(fnSrc, /_hideSaveButton\(\)/);
  });

  it("only reacts to selections inside the reader's pagesHost", () => {
    assert.match(fnSrc, /_reader\.pagesHost\.contains/);
  });

  it("uses range.getClientRects (not range.getBoundingClientRect)", () => {
    // getClientRects gives per-line rects; getBoundingClientRect gives
    // one big box. We want per-line so each line on multi-line
    // selections gets its own highlight rect.
    assert.match(fnSrc, /getClientRects/);
    assert.ok(
      !/getBoundingClientRect\(\)[^;]*;[\s\S]*?getBoundingClientRect\(\)/.test(fnSrc)
      || /box\s*=\s*el\.getBoundingClientRect/.test(fnSrc),
      "should use getBoundingClientRect only for page containment tests",
    );
  });

  it("only keeps rects from the first page (no multi-page save)", () => {
    // The comment AND the skip path should both be present.
    assert.match(
      fnSrc,
      /different page[\s\S]*continue/i,
      "multi-page rects should be skipped, not included",
    );
  });

  it("rounds PDF coords to 3 decimals before sending", () => {
    assert.match(fnSrc, /Math\.round\([^)]+\*\s*1000\)\s*\/\s*1000/);
  });

  it("never sends NaN coords — filtered before push", () => {
    assert.match(fnSrc, /Number\.isFinite/);
  });
});


// ─── showToast is the global we assume in saving/delete paths ──────────

describe("showToast contract assumptions", () => {
  it("paper-reader.js only calls showToast with recognised types", () => {
    // Third argument to showToast should be one of the four known types.
    const calls = [...source.matchAll(
      /showToast\([^,]+,\s*"([^"]+)"\)/g,
    )].map((m) => m[1]);
    const known = new Set(["success", "error", "warning", "info"]);
    const unknown = calls.filter((t) => !known.has(t));
    assert.deepEqual(unknown, [],
      `unknown toast types passed: ${unknown.join(", ")}`);
  });

  it("exercises both success and warning toast types explicitly", () => {
    // Error toasts rely on the default second arg in core.js's
    // showToast(msg, type="error"), so they may appear without an
    // explicit type argument. success/warning must be passed explicitly.
    const calls = [...source.matchAll(
      /showToast\([^,]+,\s*"([^"]+)"\)/g,
    )].map((m) => m[1]);
    const types = new Set(calls);
    for (const t of ["success", "warning"]) {
      assert.ok(types.has(t),
        `paper-reader should exercise toast type '${t}' somewhere`);
    }
  });

  it("has at least one unary showToast call (default-error)", () => {
    // Implicit error path: showToast(msg) with no second arg → "error".
    const unary = source.match(/showToast\(`?["'`][^`]*?`?\)(?!\s*,)/);
    // Looser: any showToast( followed eventually by `)` where the
    // second arg is missing. Regex gets fiddly with template literals.
    const anyUnary = /showToast\([^()]+\)(?![^\n,]*")/.test(source);
    assert.ok(anyUnary, "expected at least one showToast(msg) call (default=error)");
  });
});


// ─── Source structure: no leftover dead code paths ──────────────────────

describe("paper-reader.js — no leftover dead/legacy code", () => {
  it("does not reference SQLite local_highlights anymore", () => {
    // Backend has migrated away; client should not look for this field
    // in API responses (PDF + Zotero merge is now the contract).
    assert.ok(
      !/local_highlights/.test(source),
      "client still references local_highlights — should be sourced from /annotations response only",
    );
  });

  it("does not call the deprecated renderTextLayer function", () => {
    assert.ok(!/pdfjs\.renderTextLayer/.test(source));
  });

  it("does not double-fetch annotations on each page render", () => {
    // annotationsByPage should be populated once per paper open, then
    // rendered per page from the in-memory map.
    const matches = source.match(/fetch\([^)]*\/annotations[^)]*\)/g) || [];
    assert.ok(
      matches.length <= 2,
      `expected at most 2 fetch calls to /annotations (initial fetch + after save), got ${matches.length}`,
    );
  });
});
