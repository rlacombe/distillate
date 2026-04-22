/**
 * Tests for the [!experiment] markdown callout — the renderer turns
 *
 *   > [!experiment] Glycan Generation
 *   > # Task
 *   > Objective: ...
 *
 * into a styled card (.experiment-callout) so Nicolas's PROMPT.md
 * previews are clearly framed as scaffolds awaiting review.
 *
 * Run: cd desktop && node --test test/experiment-callout.test.js
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

// Inline the same setup used in production (index.html).
function setupRenderer() {
  const markedSrc = readFileSync(
    resolve(__dirname, "../renderer/marked.min.js"),
    "utf-8",
  );
  const dom = new JSDOM("<!DOCTYPE html><body></body>", {
    runScripts: "dangerously",
  });
  const { window } = dom;
  // Load marked into the jsdom window.
  const s1 = window.document.createElement("script");
  s1.textContent = markedSrc;
  window.document.head.appendChild(s1);
  // Apply the same configuration as index.html.
  const s2 = window.document.createElement("script");
  s2.textContent = `
    marked.setOptions({ breaks: true, gfm: true });
    function _escapeHtmlInline(s) {
      return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }
    function _preprocessExperimentCallouts(md) {
      return md.replace(
        /(^|\\n)(> \\[!experiment\\][^\\n]*(?:\\n> [^\\n]*)*)/gi,
        (_m, prefix, block) => {
          const lines = block.split(/\\n/);
          const titleLine = lines[0].replace(/^> \\[!experiment\\] ?/i, "").trim();
          const bodyMd = lines.slice(1)
            .map((l) => l.replace(/^> ?/, ""))
            .join("\\n")
            .replace(/\\s+$/, "");
          const bodyHtml = marked.parse(bodyMd);
          const titleHtml = titleLine
            ? '<span class="experiment-callout-title">' + _escapeHtmlInline(titleLine) + '</span>'
            : "";
          return prefix + '<div class="experiment-callout">' +
            '<div class="experiment-callout-header">' +
            '<span class="experiment-callout-icon">⚗️</span>' +
            '<span class="experiment-callout-label">Experiment Scaffold — review before launch</span>' +
            titleHtml +
            '</div>' +
            '<div class="experiment-callout-body">' + bodyHtml + '</div>' +
            '</div>';
        }
      );
    }
    window.markedParse = (text) => marked.parse(_preprocessExperimentCallouts(text || ""));
  `;
  window.document.head.appendChild(s2);
  return window;
}

describe("[!experiment] callout preprocessing", () => {

  it("plain markdown is rendered as before (no callout)", () => {
    const window = setupRenderer();
    const html = window.markedParse("# Hello\n\nworld");
    assert.match(html, /<h1>Hello<\/h1>/);
    assert.doesNotMatch(html, /experiment-callout/);
  });

  it("wraps a [!experiment] block in a styled card", () => {
    const window = setupRenderer();
    const md = [
      "> [!experiment] Glycan Generation",
      "> # Task",
      "> Build a DFM model.",
    ].join("\n");
    const html = window.markedParse(md);
    assert.match(html, /class="experiment-callout"/);
    assert.match(html, /class="experiment-callout-header"/);
    assert.match(html, /Experiment Scaffold/);
    assert.match(html, /Glycan Generation/);
    // Body markdown should still be rendered (not the raw `> ` syntax).
    assert.match(html, /<h1>Task<\/h1>/);
    assert.match(html, /Build a DFM model\./);
    // Raw `>` quote markers shouldn't leak.
    assert.doesNotMatch(html, /^&gt; /m);
  });

  it("title is optional", () => {
    const window = setupRenderer();
    const md = [
      "> [!experiment]",
      "> body line",
    ].join("\n");
    const html = window.markedParse(md);
    assert.match(html, /class="experiment-callout"/);
    assert.doesNotMatch(html, /class="experiment-callout-title"/);
  });

  it("escapes HTML in the title", () => {
    const window = setupRenderer();
    const md = [
      "> [!experiment] <img src=x onerror=alert(1)>",
      "> body",
    ].join("\n");
    const html = window.markedParse(md);
    // Inside the title span, the < must be escaped.
    assert.match(html, /experiment-callout-title">&lt;img/);
    // No live <img> in the title region.
    const titleStart = html.indexOf("experiment-callout-title");
    const titleEnd = html.indexOf("</span>", titleStart);
    const titleHtml = html.slice(titleStart, titleEnd);
    assert.doesNotMatch(titleHtml, /<img/);
  });

  it("regular blockquotes (no [!experiment] tag) are untouched", () => {
    const window = setupRenderer();
    const md = "> just a quote\n> with two lines";
    const html = window.markedParse(md);
    assert.doesNotMatch(html, /experiment-callout/);
    assert.match(html, /<blockquote>/);
  });

  it("multiple callouts in one message render independently", () => {
    const window = setupRenderer();
    const md = [
      "> [!experiment] First",
      "> body 1",
      "",
      "Some text.",
      "",
      "> [!experiment] Second",
      "> body 2",
    ].join("\n");
    const html = window.markedParse(md);
    const matches = html.match(/class="experiment-callout"/g) || [];
    assert.equal(matches.length, 2, "Expected two callout cards");
    assert.match(html, /First/);
    assert.match(html, /Second/);
  });
});
