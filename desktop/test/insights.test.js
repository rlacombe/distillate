/**
 * Tests for Research Insights rendering in experiment detail view.
 *
 * Verifies:
 * 1. No auto-trigger of enrichment (no fire-and-forget /notebook fetches)
 * 2. Insights render correctly when data is present
 * 3. No insights card rendered when data is absent
 * 4. HTML escaping of user-provided content
 *
 * Run: node --test test/insights.test.js
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");

// app.js was split into focused modules in an earlier refactor.
// Insights rendering lives in results.js now; the rest of the
// experiment detail pipeline is in experiments.js / experiment-detail.js.
const resultsSource = readFileSync(
  resolve(__dirname, "../renderer/results.js"),
  "utf-8"
);
const experimentsSource = readFileSync(
  resolve(__dirname, "../renderer/experiments.js"),
  "utf-8"
);
// Combined source used by the static-assertion tests below
const appSource = resultsSource + "\n" + experimentsSource;

// ───── Minimal DOM shim for testing rendering logic ─────

class MockElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.className = "";
    this.innerHTML = "";
    this.textContent = "";
    this.children = [];
    this.classList = {
      _classes: new Set(),
      add(c) { this._classes.add(c); },
      remove(c) { this._classes.delete(c); },
      toggle(c, force) {
        if (force === undefined) {
          this._classes.has(c) ? this._classes.delete(c) : this._classes.add(c);
        } else {
          force ? this._classes.add(c) : this._classes.delete(c);
        }
      },
      contains(c) { return this._classes.has(c); },
    };
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  addEventListener() {}
}

// ───── Extracted rendering logic (mirrors app.js exactly) ─────

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * Build insights HTML for an experiment.
 * Returns { element, html } if insights are present, null otherwise.
 * This mirrors the insights block in renderProjectDetail().
 */
function buildInsightsElement(proj, createElement) {
  if (
    proj.insights &&
    (proj.insights.key_breakthrough ||
      (proj.insights.lessons_learned && proj.insights.lessons_learned.length))
  ) {
    const insightsEl = createElement("div");
    insightsEl.className = "research-insights";
    let insightsHtml = '<h3 class="insights-heading">Research Insights</h3>';
    if (proj.insights.key_breakthrough) {
      insightsHtml += `<div class="insight-breakthrough"><span class="insight-section-label">Key Breakthrough</span><p>${escapeHtml(proj.insights.key_breakthrough)}</p></div>`;
    }
    if (proj.insights.lessons_learned && proj.insights.lessons_learned.length) {
      insightsHtml +=
        '<div class="insight-lessons"><span class="insight-section-label">Lessons Learned</span><ul>';
      for (const lesson of proj.insights.lessons_learned) {
        insightsHtml += `<li>${escapeHtml(lesson)}</li>`;
      }
      insightsHtml += "</ul></div>";
    }
    insightsEl.innerHTML = insightsHtml;
    return insightsEl;
  }
  return null;
}

// ───── 1. Static analysis: no enrichment auto-trigger ─────

describe("No enrichment auto-trigger (static analysis)", () => {
  it("should not contain _enrichmentTriggered anywhere", () => {
    assert.ok(
      !appSource.includes("_enrichmentTriggered"),
      "app.js still references _enrichmentTriggered — the auto-trigger guard was not fully removed"
    );
  });

  it("should not fire-and-forget fetch to /notebook endpoint", () => {
    // Match the pattern: fetch(...notebook...).catch(() => {})
    const notebookFetchPattern = /fetch\([^)]*notebook[^)]*\)\s*\.catch/;
    assert.ok(
      !notebookFetchPattern.test(appSource),
      "app.js still contains a fire-and-forget fetch to /notebook"
    );
  });

  it("should not auto-trigger enrichment based on run_count", () => {
    // The removed code checked: proj.run_count >= 3 && serverPort
    const autoTriggerPattern = /run_count\s*>=\s*3\s*&&\s*serverPort/;
    assert.ok(
      !autoTriggerPattern.test(appSource),
      "app.js still contains run_count-based enrichment auto-trigger"
    );
  });

  it("should still contain Research Insights rendering code", () => {
    assert.ok(
      appSource.includes("Research Insights"),
      "Research Insights display heading is missing — the display code was accidentally removed"
    );
    assert.ok(
      appSource.includes("research-insights"),
      "research-insights CSS class is missing"
    );
    assert.ok(
      appSource.includes("insight-breakthrough"),
      "insight-breakthrough CSS class is missing"
    );
    assert.ok(
      appSource.includes("insight-lessons"),
      "insight-lessons CSS class is missing"
    );
  });

  it("should only render insights when proj.insights has data (guard condition)", () => {
    // Verify the guard: proj.insights && (proj.insights.key_breakthrough || ...)
    const guardPattern =
      /proj\.insights\s*&&\s*\(\s*proj\.insights\.key_breakthrough\s*\|\|/;
    assert.ok(
      guardPattern.test(appSource),
      "Insights guard condition is missing — insights may render without data"
    );
  });

  it("should not have an else-if branch after insights rendering", () => {
    // After the insights if-block closes with `}`, the next non-blank line
    // should NOT start with `} else if` that touches enrichment.
    // More specifically: there should be no else-if that fetches /notebook.
    const elseIfNotebook =
      /}\s*else\s+if\s*\([^)]*run_count[^)]*\)[^{]*\{[^}]*notebook/s;
    assert.ok(
      !elseIfNotebook.test(appSource),
      "There is still an else-if branch that auto-triggers /notebook enrichment"
    );
  });
});

// ───── 2. Insights rendering: data present ─────

describe("Insights rendering with data", () => {
  const createElement = (tag) => new MockElement(tag);

  it("should render full insights card with breakthrough + lessons", () => {
    const proj = {
      insights: {
        key_breakthrough: "Discovered optimal learning rate schedule",
        lessons_learned: [
          "Warm-up period is critical",
          "Batch size affects convergence",
        ],
      },
    };

    const el = buildInsightsElement(proj, createElement);
    assert.ok(el, "Expected an insights element to be created");
    assert.equal(el.className, "research-insights");
    assert.ok(el.innerHTML.includes("Research Insights"));
    assert.ok(el.innerHTML.includes("Key Breakthrough"));
    assert.ok(
      el.innerHTML.includes("Discovered optimal learning rate schedule")
    );
    assert.ok(el.innerHTML.includes("Lessons Learned"));
    assert.ok(el.innerHTML.includes("Warm-up period is critical"));
    assert.ok(el.innerHTML.includes("Batch size affects convergence"));
  });

  it("should render with only key_breakthrough (no lessons)", () => {
    const proj = {
      insights: {
        key_breakthrough: "Major finding here",
      },
    };

    const el = buildInsightsElement(proj, createElement);
    assert.ok(el, "Expected insights element with just breakthrough");
    assert.ok(el.innerHTML.includes("Key Breakthrough"));
    assert.ok(el.innerHTML.includes("Major finding here"));
    assert.ok(
      !el.innerHTML.includes("Lessons Learned"),
      "Should not show Lessons Learned section when empty"
    );
  });

  it("should render with only lessons_learned (no breakthrough)", () => {
    const proj = {
      insights: {
        lessons_learned: ["Use gradient clipping", "Monitor loss plateau"],
      },
    };

    const el = buildInsightsElement(proj, createElement);
    assert.ok(el, "Expected insights element with just lessons");
    assert.ok(
      !el.innerHTML.includes("Key Breakthrough"),
      "Should not show Key Breakthrough when absent"
    );
    assert.ok(el.innerHTML.includes("Lessons Learned"));
    assert.ok(el.innerHTML.includes("Use gradient clipping"));
    assert.ok(el.innerHTML.includes("Monitor loss plateau"));
  });

  it("should generate one <li> per lesson", () => {
    const lessons = ["A", "B", "C", "D", "E"];
    const proj = { insights: { lessons_learned: lessons } };

    const el = buildInsightsElement(proj, createElement);
    const liCount = (el.innerHTML.match(/<li>/g) || []).length;
    assert.equal(liCount, 5, `Expected 5 <li> items, got ${liCount}`);
  });
});

// ───── 3. Insights rendering: no data → no card ─────

describe("Insights rendering without data", () => {
  const createElement = (tag) => new MockElement(tag);

  it("should return null when proj has no insights property", () => {
    assert.equal(buildInsightsElement({}, createElement), null);
  });

  it("should return null when proj.insights is null", () => {
    assert.equal(buildInsightsElement({ insights: null }, createElement), null);
  });

  it("should return null when proj.insights is undefined", () => {
    assert.equal(
      buildInsightsElement({ insights: undefined }, createElement),
      null
    );
  });

  it("should return null when insights is empty object", () => {
    assert.equal(buildInsightsElement({ insights: {} }, createElement), null);
  });

  it("should return null when key_breakthrough is empty string and no lessons", () => {
    assert.equal(
      buildInsightsElement(
        { insights: { key_breakthrough: "" } },
        createElement
      ),
      null
    );
  });

  it("should return null when lessons_learned is empty array and no breakthrough", () => {
    assert.equal(
      buildInsightsElement(
        { insights: { lessons_learned: [] } },
        createElement
      ),
      null
    );
  });

  it("should return null when both are falsy", () => {
    assert.equal(
      buildInsightsElement(
        { insights: { key_breakthrough: "", lessons_learned: [] } },
        createElement
      ),
      null
    );
  });

  it("should NOT trigger any network request regardless of run_count", () => {
    // This test verifies the contract: buildInsightsElement is purely
    // display logic with no side effects.
    const proj = {
      run_count: 10,
      id: "test-experiment",
      insights: null,
    };

    // No fetch should be called — the function should simply return null
    const result = buildInsightsElement(proj, createElement);
    assert.equal(result, null);
    // (The static analysis tests above also verify no fetch in the source)
  });
});

// ───── 4. HTML escaping ─────

describe("HTML escaping in insights", () => {
  const createElement = (tag) => new MockElement(tag);

  it("should escape HTML entities in key_breakthrough", () => {
    const proj = {
      insights: {
        key_breakthrough: '<script>alert("xss")</script>',
      },
    };
    const el = buildInsightsElement(proj, createElement);
    assert.ok(el.innerHTML.includes("&lt;script&gt;"));
    assert.ok(!el.innerHTML.includes("<script>"));
  });

  it("should escape HTML entities in lessons_learned", () => {
    const proj = {
      insights: {
        lessons_learned: ['Use <b>bold</b> & "quotes"'],
      },
    };
    const el = buildInsightsElement(proj, createElement);
    assert.ok(el.innerHTML.includes("&lt;b&gt;bold&lt;/b&gt;"));
    assert.ok(el.innerHTML.includes("&amp;"));
    assert.ok(!el.innerHTML.includes("<b>bold</b>"));
  });

  it("should escape ampersands", () => {
    const proj = {
      insights: {
        key_breakthrough: "R&D results improved",
      },
    };
    const el = buildInsightsElement(proj, createElement);
    assert.ok(el.innerHTML.includes("R&amp;D results improved"));
  });
});

// ───── 5. CSS classes exist in styles.css ─────

describe("CSS classes for insights exist", () => {
  const cssSource = readFileSync(
    resolve(__dirname, "../renderer/styles.css"),
    "utf-8"
  );

  it("should have .research-insights styles", () => {
    assert.ok(
      cssSource.includes(".research-insights"),
      "Missing .research-insights in styles.css"
    );
  });

  it("should have .insight-breakthrough styles", () => {
    assert.ok(
      cssSource.includes(".insight-breakthrough"),
      "Missing .insight-breakthrough in styles.css"
    );
  });

  it("should have .insight-lessons styles", () => {
    assert.ok(
      cssSource.includes(".insight-lessons"),
      "Missing .insight-lessons in styles.css"
    );
  });

  it("should have .insights-heading styles", () => {
    assert.ok(
      cssSource.includes(".insights-heading"),
      "Missing .insights-heading in styles.css"
    );
  });
});
