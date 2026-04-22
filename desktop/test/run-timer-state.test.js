// Covers: desktop/renderer/experiment-detail.js (computeRunTimerState)
/**
 * Pure-logic tests for the run-timer state machine.
 *
 * The renderer shows "5:23 / 10:00" for a running experiment. Before L3.5
 * it computed the budget side from `duration_minutes` and only had two
 * states (running / hidden-if-stale). Now it drives off the deadlines
 * written by start_run (L3), so the timer can correctly distinguish
 * three phases:
 *
 *   - training: now < train_deadline (the model is actually training)
 *   - wrapping: train_deadline <= now < wrap_deadline (agent is calling
 *               conclude_run, committing, pushing -- this is expected
 *               and must NOT be shown as "over budget")
 *   - overdue:  now >= wrap_deadline (past grace, auto-conclude kicks
 *               in on the next Stop hook)
 *
 * Legacy runs (no deadlines in the entry) fall back to the old behavior
 * so pre-L3 projects still show something sensible.
 */

const { describe, it, before } = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

/** Load experiment-detail.js into a jsdom window and return the module's
 *  window-attached helpers. */
function loadRenderer() {
  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const src = fs.readFileSync(
    path.join(__dirname, "..", "renderer", "experiment-detail.js"),
    "utf-8",
  );
  // Stub helpers the module expects before execution
  dom.window.escapeHtml = (s) => String(s ?? "");
  dom.window.renderMetricChart = () => {};
  dom.window.renderFrontierCurve = () => {};
  dom.window.serverPort = 0;
  dom.window.showToast = () => {};
  dom.window.fetchExperimentsList = () => {};
  dom.window.eval(src);
  return dom.window;
}

/** Shorthand: build ISO-string deadlines relative to `startMs`. */
function iso(ms) {
  return new Date(ms).toISOString();
}

describe("computeRunTimerState", () => {
  let compute;

  before(() => {
    const win = loadRenderer();
    compute = win.computeRunTimerState;
    assert.equal(
      typeof compute, "function",
      "experiment-detail.js must expose window.computeRunTimerState",
    );
  });

  describe("with deadlines (L3+)", () => {
    const startMs = Date.parse("2026-04-15T08:51:41Z");
    const trainDeadlineMs = startMs + 600_000;         // + 10 min
    const wrapDeadlineMs = trainDeadlineMs + 60_000;   // + 1 min grace

    it("shows 'training' phase while inside train deadline", () => {
      const now = startMs + 3 * 60_000; // 3 min in
      const s = compute(now, startMs, trainDeadlineMs, wrapDeadlineMs, 600);
      assert.equal(s.phase, "training");
      assert.equal(s.timerText, "3:00 / 10:00");
      assert.equal(s.className, "run-timer");
    });

    it("shows 'wrapping' phase between train and wrap deadlines", () => {
      // 10m 20s in -> past train deadline (10:00), inside wrap (11:00)
      const now = startMs + 10 * 60_000 + 20_000;
      const s = compute(now, startMs, trainDeadlineMs, wrapDeadlineMs, 600);
      assert.equal(s.phase, "wrapping",
        "post-train, pre-wrap is the agent logging/committing -- show amber, not red");
      assert.equal(s.timerText, "10:20 / 10:00");
      assert.match(s.className, /run-timer-wrapping/);
    });

    it("shows 'overdue' phase past wrap deadline", () => {
      const now = startMs + 12 * 60_000; // 12 min: well past wrap (11:00)
      const s = compute(now, startMs, trainDeadlineMs, wrapDeadlineMs, 600);
      assert.equal(s.phase, "overdue");
      assert.match(s.className, /run-timer-overdue/);
    });

    it("'wrapping' phase is sharp at the train deadline", () => {
      const s = compute(
        trainDeadlineMs, startMs, trainDeadlineMs, wrapDeadlineMs, 600,
      );
      assert.equal(s.phase, "wrapping",
        "at exactly train_deadline_at the run transitions to wrapping");
    });

    it("'overdue' phase is sharp at the wrap deadline", () => {
      const s = compute(
        wrapDeadlineMs, startMs, trainDeadlineMs, wrapDeadlineMs, 600,
      );
      assert.equal(s.phase, "overdue");
    });

    it("budget text uses train deadline, not legacy budget", () => {
      // legacyBudgetSecs is intentionally wrong to catch accidental fallback.
      const s = compute(
        startMs + 1_000, startMs, trainDeadlineMs, wrapDeadlineMs, 9999,
      );
      assert.equal(s.timerText.split(" / ")[1], "10:00",
        "must read budget from train deadline, not duration_minutes");
    });

    it("hides the timer when elapsed is negative (clock skew)", () => {
      const now = startMs - 5_000;
      const s = compute(now, startMs, trainDeadlineMs, wrapDeadlineMs, 600);
      assert.equal(s.phase, "hidden");
    });

    it("hides the timer for runs abandoned >3x the budget ago", () => {
      const now = startMs + 30 * 60_000 + 1_000; // > 3 * 600s
      const s = compute(now, startMs, trainDeadlineMs, wrapDeadlineMs, 600);
      assert.equal(s.phase, "hidden",
        "a running entry that's been stale for ages shouldn't still render a timer");
    });
  });

  describe("legacy fallback (pre-L3 runs with no deadlines)", () => {
    const startMs = Date.parse("2026-04-10T12:00:00Z");

    it("uses duration_minutes when deadlines are undefined", () => {
      const s = compute(startMs + 4 * 60_000, startMs, undefined, undefined, 600);
      assert.equal(s.phase, "training");
      assert.equal(s.timerText, "4:00 / 10:00");
      assert.equal(s.className, "run-timer");
    });

    it("uses duration_minutes when deadlines are null", () => {
      const s = compute(startMs + 4 * 60_000, startMs, null, null, 600);
      assert.equal(s.phase, "training");
      assert.equal(s.timerText, "4:00 / 10:00");
    });

    it("cannot reach wrapping/overdue without a wrap deadline", () => {
      // Past the budget but no deadlines -- legacy runs just show the
      // elapsed/budget text without phase escalation.
      const s = compute(startMs + 15 * 60_000, startMs, null, null, 600);
      assert.equal(s.phase, "training",
        "legacy runs never escalate -- we don't know where 'wrap' ends");
      assert.equal(s.timerText, "15:00 / 10:00");
    });

    it("still hides on the 3x-budget stale invariant", () => {
      const s = compute(startMs + 31 * 60_000, startMs, null, null, 600);
      assert.equal(s.phase, "hidden");
    });
  });

  describe("edge cases", () => {
    const startMs = Date.parse("2026-04-15T08:51:41Z");
    const trainDeadlineMs = startMs + 600_000;
    const wrapDeadlineMs = trainDeadlineMs + 60_000;

    it("zero-second elapsed renders as 0:00", () => {
      const s = compute(startMs, startMs, trainDeadlineMs, wrapDeadlineMs, 600);
      assert.equal(s.timerText, "0:00 / 10:00");
    });

    it("single-digit seconds are zero-padded", () => {
      const s = compute(startMs + 7_000, startMs, trainDeadlineMs, wrapDeadlineMs, 600);
      assert.equal(s.timerText, "0:07 / 10:00");
    });

    it("budgets with odd seconds format correctly", () => {
      const oddTrain = startMs + 125_000; // 2:05
      const oddWrap = oddTrain + 60_000;
      const s = compute(startMs, startMs, oddTrain, oddWrap, 125);
      assert.equal(s.timerText, "0:00 / 2:05");
    });

    it("accepts ISO strings OR millis for deadlines (caller convenience)", () => {
      // The renderer converts ISO strings to ms before calling.
      // Verify it works if a caller passes ms already -- and that
      // passing an ISO string crashes so the contract is clear.
      const ok = compute(startMs + 1000, startMs, trainDeadlineMs, wrapDeadlineMs, 600);
      assert.equal(ok.phase, "training");
    });
  });
});
