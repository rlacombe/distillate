// Covers: desktop/renderer/experiments.js (liveMetricSeriesForCurrentRun + sidebar row sparkline)
/**
 * The sidebar row sparkline must show the CURRENT run's training loss
 * curve — the within-run, per-epoch signal — not the cross-run frontier
 * (that's what the big chart in the detail view shows).
 *
 * ``liveMetrics[pid]`` accumulates metric_update SSE events across
 * multiple runs in a session, so the sparkline helper must filter to
 * the current run by timestamp: only events with ``ts >= runStartedAt``
 * belong to the active run.
 *
 * Metric priority (pure function picks the first one with >=2 points):
 *   1. train_loss — the canonical in-run training signal
 *   2. loss       — when the script names it plainly
 *   3. val_loss   — some scripts print val instead of train per epoch
 *   4. key_metric_name (explicit override, e.g. the project's frontier metric)
 *
 * Pure logic is the only stable surface to test — DOM integration is
 * Playwright's job. This file sticks to the extraction helper.
 */

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="activity-bar">
      <button class="activity-btn" data-sidebar-view="experiments"></button>
    </div>
    <div id="experiments-sidebar"></div>
    <span id="experiments-count"></span>
  </body></html>`, {
    runScripts: "outside-only",
    url: "http://localhost/",
  });
  const w = dom.window;
  w.serverPort = 0;
  w.cachedProjects = [];
  w.currentProjectId = null;
  w.escapeHtml = (s) => String(s ?? "");
  w.showToast = () => {};
  w.renderProjectDetail = () => {};
  w.startSessionPolling = () => {};
  w.stopSessionPolling = () => {};
  w.fetch = () => Promise.resolve({ json: () => Promise.resolve({ projects: [] }) });
  w.localStorage.setItem("distillate-onboarded", "1");
  w.getDisplayRuns = (runs) => (runs || []).filter((r) =>
    r.results && Object.values(r.results).some((v) => typeof v === "number"));
  w.eval("var sessionDoneBells = new Set(); var sseSource = null; function selectProject(){}");
  // sparklineSvg lives in core.js — extract just that helper so experiments.js can call it.
  const coreSrc = fs.readFileSync(
    path.join(__dirname, "..", "renderer", "core.js"),
    "utf-8",
  );
  const m = coreSrc.match(/function sparklineSvg[\s\S]*?^\}/m);
  if (m) w.eval(m[0] + "\nwindow.sparklineSvg = sparklineSvg;");
  w.eval(fs.readFileSync(
    path.join(__dirname, "..", "renderer", "experiments.js"),
    "utf-8",
  ));
  return w;
}

describe("liveMetricSeriesForCurrentRun", () => {
  let w, series;
  beforeEach(() => {
    w = makeWindow();
    series = w.liveMetricSeriesForCurrentRun;
    assert.equal(typeof series, "function",
      "experiments.js must expose window.liveMetricSeriesForCurrentRun");
  });

  // ---- time filtering (the core correctness property) ----

  it("includes events whose ts >= runStartedAtMs", () => {
    const runStart = Date.parse("2026-04-15T10:00:00Z");
    const out = series([
      { ts: "2026-04-15T10:00:30Z", metrics: { train_loss: 0.8 } },
      { ts: "2026-04-15T10:01:00Z", metrics: { train_loss: 0.6 } },
      { ts: "2026-04-15T10:02:00Z", metrics: { train_loss: 0.4 } },
    ], { runStartedAtMs: runStart });
    assert.deepEqual(Array.from(out.values), [0.8, 0.6, 0.4]);
  });

  it("EXCLUDES events from a previous run (ts < runStartedAtMs)", () => {
    const runStart = Date.parse("2026-04-15T10:00:00Z");
    // The first two events are from run 5 (earlier); only the last two
    // belong to the current run 6.
    const out = series([
      { ts: "2026-04-15T09:50:00Z", metrics: { train_loss: 0.9 } },  // prior run
      { ts: "2026-04-15T09:55:00Z", metrics: { train_loss: 0.7 } },  // prior run
      { ts: "2026-04-15T10:00:30Z", metrics: { train_loss: 0.5 } },  // current
      { ts: "2026-04-15T10:01:00Z", metrics: { train_loss: 0.3 } },  // current
    ], { runStartedAtMs: runStart });
    assert.deepEqual(Array.from(out.values), [0.5, 0.3],
      "events from earlier runs must not pollute the current-run sparkline");
  });

  it("includes all events when runStartedAtMs is null/undefined", () => {
    const out = series([
      { ts: "2026-04-15T09:00:00Z", metrics: { train_loss: 0.5 } },
      { ts: "2026-04-15T10:00:00Z", metrics: { train_loss: 0.3 } },
    ], { runStartedAtMs: null });
    assert.deepEqual(Array.from(out.values), [0.5, 0.3]);
  });

  // ---- metric priority (train_loss > loss > val_loss > override) ----

  it("prefers train_loss over loss/val_loss", () => {
    const out = series([
      { metrics: { train_loss: 0.8, loss: 0.9, val_loss: 1.0 } },
      { metrics: { train_loss: 0.6, loss: 0.7, val_loss: 0.8 } },
    ], {});
    assert.equal(out.metric, "train_loss");
    assert.deepEqual(Array.from(out.values), [0.8, 0.6]);
  });

  it("falls back to loss when train_loss isn't emitted", () => {
    const out = series([
      { metrics: { loss: 0.9 } },
      { metrics: { loss: 0.7 } },
    ], {});
    assert.equal(out.metric, "loss");
    assert.deepEqual(Array.from(out.values), [0.9, 0.7]);
  });

  it("falls back to val_loss when neither train_loss nor loss exists", () => {
    const out = series([
      { metrics: { val_loss: 1.0 } },
      { metrics: { val_loss: 0.8 } },
    ], {});
    assert.equal(out.metric, "val_loss");
    assert.deepEqual(Array.from(out.values), [1.0, 0.8]);
  });

  it("honors an explicit metricNames override", () => {
    const out = series([
      { metrics: { train_loss: 0.5, accuracy: 0.8 } },
      { metrics: { train_loss: 0.4, accuracy: 0.9 } },
    ], { metricNames: ["accuracy"] });
    assert.equal(out.metric, "accuracy");
    assert.deepEqual(Array.from(out.values), [0.8, 0.9]);
  });

  it("uses key_metric_name as a last-resort fallback", () => {
    const out = series([
      { metrics: { top1_structural_accuracy: 0.02 } },
      { metrics: { top1_structural_accuracy: 0.05 } },
      { metrics: { top1_structural_accuracy: 0.12 } },
    ], { keyMetricName: "top1_structural_accuracy" });
    assert.equal(out.metric, "top1_structural_accuracy");
    assert.deepEqual(Array.from(out.values), [0.02, 0.05, 0.12]);
  });

  it("requires >=2 points to pick a metric (single-point doesn't count)", () => {
    // loss has 1 point, train_loss has 2 — train_loss should still win
    // because the priority list doesn't care about counts once found.
    // But if only loss has 1 point and nothing else has any, we return empty.
    const out = series([
      { metrics: { loss: 0.5 } },
    ], {});
    assert.equal(out.values.length, 0,
      "a single data point is not a trend -- return empty, not a degenerate 1-point series");
  });

  it("returns a stable shape even for no matches", () => {
    const out = series([], {});
    // Consumer code pattern: `if (series.values.length >= 2) draw(series)`
    assert.ok(Array.isArray(out.values),
      ".values must always be an array, even when empty");
    assert.equal(out.values.length, 0);
    // metric may be "" or null; caller just checks length
  });

  // ---- robustness (don't crash on messy events) ----

  it("ignores events without a metrics dict", () => {
    const out = series([
      { metrics: { train_loss: 0.5 } },
      { ts: "2026-04-15T10:00:00Z" },  // no metrics
      { metrics: null },
      { metrics: { train_loss: 0.3 } },
    ], {});
    assert.deepEqual(Array.from(out.values), [0.5, 0.3]);
  });

  it("drops non-numeric values", () => {
    const out = series([
      { metrics: { train_loss: 0.5 } },
      { metrics: { train_loss: "crashed" } },
      { metrics: { train_loss: NaN } },
      { metrics: { train_loss: null } },
      { metrics: { train_loss: 0.2 } },
    ], {});
    assert.deepEqual(Array.from(out.values), [0.5, 0.2]);
  });

  it("tolerates invalid timestamp strings (keeps the event)", () => {
    // An unparseable ts shouldn't nuke the series; fall through to
    // "include" rather than "exclude" so we err on the side of showing data.
    const runStart = Date.parse("2026-04-15T10:00:00Z");
    const out = series([
      { ts: "not-a-date", metrics: { train_loss: 0.4 } },
      { ts: "2026-04-15T10:01:00Z", metrics: { train_loss: 0.3 } },
    ], { runStartedAtMs: runStart });
    // Unparseable ts => include. The rendered sparkline might briefly
    // show a point that "belongs" to the previous run, but that's better
    // than the whole sparkline going blank on a formatting bug.
    assert.equal(out.values.length, 2);
  });
});

describe("liveMetricSeriesForCurrentRun — integration with proj", () => {
  // Lightweight check that a caller passing (events, proj-like) works.
  // Purpose: catch signature drift without retesting all the filter logic.
  let w;
  beforeEach(() => { w = makeWindow(); });

  it("pulls runStartedAtMs and keyMetricName from options", () => {
    const runStart = Date.parse("2026-04-15T10:00:00Z");
    const out = w.liveMetricSeriesForCurrentRun([
      { ts: "2026-04-15T09:00:00Z", metrics: { top1_acc: 0.01 } }, // before run
      { ts: "2026-04-15T10:01:00Z", metrics: { top1_acc: 0.05 } },
      { ts: "2026-04-15T10:02:00Z", metrics: { top1_acc: 0.10 } },
    ], { runStartedAtMs: runStart, keyMetricName: "top1_acc" });
    assert.deepEqual(Array.from(out.values), [0.05, 0.10]);
    assert.equal(out.metric, "top1_acc");
  });
});
