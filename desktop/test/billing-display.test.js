/**
 * Tests for billing-display wiring in the desktop renderer.
 *
 * Run: cd desktop && node --test test/billing-display.test.js
 *
 * Strategy (mirrors paper-reader.test.js): static analysis of source
 * files plus pure-function extraction for the formatter. DOM-driven
 * tests of the actual picker behavior are deferred to Playwright.
 *
 * These are RED tests written before implementation.
 * See docs/research/nicolas-billing-action-plan.md §7.6.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, existsSync } = require("node:fs");
const { resolve } = require("node:path");

const rendererDir = resolve(__dirname, "../renderer");
const indexHtmlPath = resolve(rendererDir, "index.html");
const billingJsPath = resolve(rendererDir, "billing.js");
const coreJsPath = resolve(rendererDir, "core.js");

const indexHtml = readFileSync(indexHtmlPath, "utf-8");
const coreJs = readFileSync(coreJsPath, "utf-8");
const billingJsExists = existsSync(billingJsPath);
const billingJs = billingJsExists ? readFileSync(billingJsPath, "utf-8") : "";


// ─── Source presence ────────────────────────────────────────────────────

describe("billing.js — file exists", () => {
  it("desktop/renderer/billing.js is present", () => {
    assert.ok(
      billingJsExists,
      "expected desktop/renderer/billing.js to exist",
    );
  });
});


// ─── index.html — DOM hooks ─────────────────────────────────────────────

describe("index.html — billing DOM hooks", () => {
  it("status bar contains #model-pill", () => {
    assert.match(indexHtml, /id=["']model-pill["']/);
  });

  it("status bar contains #cost-pill", () => {
    assert.match(indexHtml, /id=["']cost-pill["']/);
  });

  it("billing.js is loaded", () => {
    assert.match(indexHtml, /src=["']billing\.js["']/);
  });
});


// ─── billing.js — exports & wiring ──────────────────────────────────────

describe("billing.js — exports and WebSocket wiring", () => {
  it("exposes mountBilling on window", () => {
    assert.match(billingJs, /window\.mountBilling\s*=\s*mountBilling/);
  });

  it("requests get_preferences on mount", () => {
    assert.match(
      billingJs,
      /"type":\s*"get_preferences"|type:\s*["']get_preferences["']/,
      "mountBilling should send a get_preferences WS message",
    );
  });

  it("requests get_usage on mount", () => {
    assert.match(
      billingJs,
      /"type":\s*"get_usage"|type:\s*["']get_usage["']/,
      "mountBilling should send a get_usage WS message",
    );
  });

  it("dispatches set_model when user picks a model", () => {
    assert.match(
      billingJs,
      /"type":\s*"set_model"|type:\s*["']set_model["']/,
    );
  });

  it("listens for turn_end event", () => {
    assert.match(billingJs, /turn_end/);
  });

  it("listens for usage_update event", () => {
    assert.match(billingJs, /usage_update/);
  });

  it("handles preferences event to restore picker state", () => {
    assert.match(billingJs, /"preferences"|'preferences'/);
  });
});


// ─── Model list ────────────────────────────────────────────────────────

describe("billing.js — model dropdown", () => {
  it("mentions all four supported model ids", () => {
    // Either hardcoded in the file or (preferred) received from the server
    // via supported_models. Either way, Opus 4.6 should be a default label
    // baked in for the first paint before the WS reply lands.
    const labels = ["Opus 4.6", "Sonnet 4.6", "Sonnet 4.5", "Haiku 4.5"];
    for (const label of labels) {
      assert.ok(
        billingJs.includes(label) || indexHtml.includes(label),
        `expected label "${label}" to appear in billing.js or index.html`,
      );
    }
  });
});


// ─── _fmt_cost — pure function ──────────────────────────────────────────

describe("_fmt_cost — formatting ranges", () => {
  // Extract the pure fn from the source and eval it. If the impl is named
  // differently, update this matcher — the tests here pin the ranges, not
  // the exact name.
  const fnMatch = billingJs.match(
    /function\s+_fmt_cost\s*\([^)]*\)\s*\{[\s\S]*?\n\}/,
  );

  it("_fmt_cost is defined", () => {
    assert.ok(fnMatch, "could not locate _fmt_cost(usd) in billing.js");
  });

  if (fnMatch) {
    // eslint-disable-next-line no-new-func
    const _fmt_cost = new Function(`${fnMatch[0]}; return _fmt_cost;`)();

    it("formats sub-cent as $0.00", () => {
      assert.equal(_fmt_cost(0), "$0.00");
      assert.equal(_fmt_cost(0.004), "$0.00");
    });

    it("formats sub-dollar as two decimals", () => {
      assert.equal(_fmt_cost(0.42), "$0.42");
    });

    it("formats over-dollar as two decimals", () => {
      assert.equal(_fmt_cost(12.34), "$12.34");
    });
  }
});


// ─── core.js — wiring point ────────────────────────────────────────────

describe("core.js — mounts billing on ws open", () => {
  it("calls mountBilling after ws.onopen", () => {
    assert.match(
      coreJs,
      /mountBilling\s*\(/,
      "core.js should call mountBilling(…) to boot the billing pills",
    );
  });
});
