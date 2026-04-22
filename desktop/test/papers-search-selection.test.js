/**
 * Tests for papers-search-selection — opening a paper from the search bar.
 *
 * Covers: renderer/papers.js, renderer/layout.js (search integration)
 *
 * Run: cd desktop && node --test test/papers-search-selection.test.js
 *
 * Strategy: Unit tests for the core logic (guards, state transitions) +
 * static analysis for critical code patterns.
 */

const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { JSDOM } = require("jsdom");

const papersSource = readFileSync(
  resolve(__dirname, "../renderer/papers.js"),
  "utf-8",
);

const layoutSource = readFileSync(
  resolve(__dirname, "../renderer/layout.js"),
  "utf-8",
);

// ─── Setup: minimal DOM for testing ───────────────────────────────────

let dom;
let window;
let document;

beforeEach(() => {
  dom = new JSDOM(`
    <html>
      <head></head>
      <body>
        <div id="experiment-detail"></div>
        <div id="papers-sidebar"></div>
      </body>
    </html>
  `);
  window = dom.window;
  document = window.document;
  global.window = window;
  global.document = document;
  global.serverPort = 8000;
});

afterEach(() => {
  global.window = undefined;
  global.document = undefined;
  global.serverPort = undefined;
});

// ─── Static analysis: critical patterns in the source ─────────────────

describe("papers.js — search-to-detail flow invariants", () => {
  it("renderPapersHome has a guard to avoid overwriting selected paper", () => {
    // The bug: renderPapersHome() was called after selectPaper(), and it
    // overwrote the detail pane without checking if currentPaperKey was set.
    // Fix: renderPapersHome must check if a paper is already selected.
    assert.match(
      papersSource,
      /function renderPapersHome\([\s\S]+?if\s*\(!detailEl\s*\|\|.*currentPaperKey/,
      "renderPapersHome should guard against rendering if currentPaperKey is set",
    );
  });

  it("selectPaper sets currentPaperKey before fetching detail", () => {
    assert.match(
      papersSource,
      /currentPaperKey\s*=\s*paperKey;[\s\S]*?fetch.*\/papers/,
      "selectPaper must set currentPaperKey before any async operations",
    );
  });

  it("fetchPapersHome is async and calls renderPapersHome", () => {
    assert.match(
      papersSource,
      /async\s+function\s+fetchPapersHome/,
      "fetchPapersHome should be async",
    );
    assert.match(
      papersSource,
      /fetchPapersHome[\s\S]+?renderPapersHome/,
      "fetchPapersHome should eventually call renderPapersHome",
    );
  });
});

describe("layout.js — search bar integration", () => {
  it("clicking a paper from search calls selectPaper with the paper id", () => {
    // The search bar click handler should pass the paper key to selectPaper.
    assert.match(
      layoutSource,
      /type\s*===\s*['""]paper[""][\s\S]+?selectPaper\s*\(\s*id\s*\)/,
      "Search bar should call selectPaper(id) when a paper is clicked",
    );
  });

  it("search paper handler calls switchSidebarView and selectPaper", () => {
    // Both are called: switchSidebarView then selectPaper.
    // This tests the current sequence (switchSidebarView triggers
    // showPapersHome which starts an async fetch; selectPaper must set
    // currentPaperKey to prevent that fetch from overwriting the detail).
    assert.match(
      layoutSource,
      /type\s*===\s*['""]paper[""][\s\S]+?switchSidebarView\s*\(\s*['""]papers['""][\s\S]+?selectPaper\s*\(\s*id\s*\)/,
      "Paper click handler should call switchSidebarView('papers') then selectPaper(id)",
    );
  });
});

describe("renderPapersHome — guard logic", () => {
  it("skips rendering when currentPaperKey is set", () => {
    // Directly test the guard without needing full DOM simulation.
    // This verifies the fix pattern is in place.
    const renderFn = papersSource.match(
      /function renderPapersHome\(data\)\s*\{([\s\S]+?)^\}/m,
    );
    assert.ok(renderFn, "Could not locate renderPapersHome function");
    const body = renderFn[1];
    // Must have early return if currentPaperKey is set.
    assert.match(
      body,
      /if\s*\(!detailEl\s*\|\|.*currentPaperKey/,
      "renderPapersHome must have guard: if (!detailEl || currentPaperKey) return",
    );
  });
});
