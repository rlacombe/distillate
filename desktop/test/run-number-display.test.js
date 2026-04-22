// Covers: desktop/renderer/experiment-detail.js (displayRunNumber)
/**
 * The "Run N:" label in the experiment status card used to be computed
 * as ``displayRuns.length + 1`` — a client-side recount of whatever
 * ``state.runs`` cached. When state contains phantom or stale
 * ``running`` entries (from interrupted sessions, scanner
 * reconstructions, etc.), that count drifts from the canonical
 * ``run_number`` the backend issues via ``start_run``.
 *
 * ``displayRunNumber(proj, displayRuns)`` is the pure function that
 * picks the right number: the backend-propagated
 * ``proj.current_run_number`` when available, the length fallback only
 * for legacy entries that predate ``run_number``.
 */

const { describe, it, before } = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

function loadRenderer() {
  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    runScripts: "outside-only",
    url: "http://localhost/",
  });
  dom.window.escapeHtml = (s) => String(s ?? "");
  dom.window.renderMetricChart = () => {};
  dom.window.renderFrontierCurve = () => {};
  dom.window.serverPort = 0;
  dom.window.showToast = () => {};
  dom.window.fetchExperimentsList = () => {};
  dom.window.eval(fs.readFileSync(
    path.join(__dirname, "..", "renderer", "experiment-detail.js"),
    "utf-8",
  ));
  return dom.window;
}

describe("displayRunNumber", () => {
  let pick;
  before(() => {
    const win = loadRenderer();
    pick = win.displayRunNumber;
    assert.equal(typeof pick, "function",
      "experiment-detail.js must expose window.displayRunNumber");
  });

  it("uses the backend-propagated current_run_number when present", () => {
    const proj = { current_run_number: 13 };
    const displayRuns = [{}, {}, {}, {}, {}]; // 5 items — would be 6 via fallback
    assert.equal(pick(proj, displayRuns), 13,
      "canonical run_number must win over length-based recount");
  });

  it("falls back to displayRuns.length + 1 when run_number is absent", () => {
    const proj = {}; // legacy: no current_run_number
    assert.equal(pick(proj, [{}, {}, {}]), 4);
    assert.equal(pick(proj, []), 1);
  });

  it("ignores non-numeric / zero / negative current_run_number", () => {
    // The backend might accidentally send a string or a 0 — those should
    // NOT override the fallback, since they'd render as "Run NaN" / "Run 0".
    assert.equal(pick({ current_run_number: "13" }, [{}]), 2,
      "string is not a number — fall through");
    assert.equal(pick({ current_run_number: 0 }, [{}]), 2);
    assert.equal(pick({ current_run_number: -1 }, [{}]), 2);
    assert.equal(pick({ current_run_number: NaN }, [{}]), 2);
  });

  it("tolerates missing proj / missing displayRuns", () => {
    assert.equal(pick(null, null), 1,
      "null inputs must not throw; return 1 as the zeroth+1 case");
    assert.equal(pick(undefined, undefined), 1);
  });

  it("canonical number and length fallback can disagree (that's the point)", () => {
    // state.runs has phantom entries -> fallback would say 16.
    // But start_run gave us run_number=13. Trust the backend.
    const proj = { current_run_number: 13 };
    const displayRuns = new Array(15); // pretend 15 displayable runs
    assert.equal(pick(proj, displayRuns), 13,
      "when state.runs is stale the backend number is authoritative");
  });
});
