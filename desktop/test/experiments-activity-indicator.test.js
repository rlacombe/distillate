// Covers: desktop/renderer/experiments.js (updateExperimentsActivityDot)
/**
 * When any experiment has an active session, the Experiments button in
 * the activity bar (left sidebar icon rail) must show a breathing live
 * dot so the user knows work is happening even when they're on another
 * view. Absence of active sessions removes the dot.
 *
 * Pure DOM toggle -- jsdom is the right surface.
 */

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="activity-bar">
      <button class="activity-btn active" data-sidebar-view="nicolas" aria-label="Nicolas"></button>
      <button class="activity-btn" data-sidebar-view="experiments" aria-label="Experiments"></button>
      <button class="activity-btn" data-sidebar-view="papers" aria-label="Papers"></button>
    </div>
  </body></html>`, {
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  // Stubs experiments.js expects at load time
  dom.window.serverPort = 0;
  dom.window.cachedProjects = [];
  dom.window.currentProjectId = null;
  dom.window.escapeHtml = (s) => String(s ?? "");
  dom.window.showToast = () => {};
  dom.window.renderProjectDetail = () => {};
  dom.window.startSessionPolling = () => {};
  dom.window.stopSessionPolling = () => {};
  dom.window.fetch = () => Promise.resolve({ json: () => Promise.resolve({ projects: [] }) });
  // Provide document shims before experiments.js runs (it queries for elements at top level)
  return dom.window;
}

function loadExperimentsJs(win) {
  const src = fs.readFileSync(
    path.join(__dirname, "..", "renderer", "experiments.js"),
    "utf-8",
  );
  win.eval(src);
}

describe("updateExperimentsActivityDot", () => {
  let win, btn, update;

  beforeEach(() => {
    win = makeWindow();
    loadExperimentsJs(win);
    btn = win.document.querySelector('[data-sidebar-view="experiments"]');
    update = win.updateExperimentsActivityDot;
    assert.equal(typeof update, "function",
      "experiments.js must expose window.updateExperimentsActivityDot");
  });

  it("adds a live dot when an experiment is active", () => {
    update(true);
    const dot = btn.querySelector(".activity-btn-live-dot");
    assert.ok(dot, "expected .activity-btn-live-dot child on the Experiments button");
  });

  it("removes the live dot when nothing is active", () => {
    update(true);
    update(false);
    const dot = btn.querySelector(".activity-btn-live-dot");
    assert.equal(dot, null, "live dot must be removed when hasActive=false");
  });

  it("is idempotent — calling update(true) twice yields exactly one dot", () => {
    update(true);
    update(true);
    const dots = btn.querySelectorAll(".activity-btn-live-dot");
    assert.equal(dots.length, 1, "must not accumulate multiple dots");
  });

  it("only attaches to the Experiments button (not other activity buttons)", () => {
    update(true);
    const nicolasBtn = win.document.querySelector('[data-sidebar-view="nicolas"]');
    const papersBtn = win.document.querySelector('[data-sidebar-view="papers"]');
    assert.equal(nicolasBtn.querySelector(".activity-btn-live-dot"), null);
    assert.equal(papersBtn.querySelector(".activity-btn-live-dot"), null);
  });

  it("is safe when the activity bar isn't in the DOM", () => {
    // Remove the button entirely
    btn.remove();
    // Must not throw
    update(true);
    update(false);
  });

  it("driven by renderProjectsList: hasActive inferred from active_sessions", () => {
    // End-to-end through the real call site: rendering a project list with
    // an active session should toggle the dot on.
    win.renderProjectsList([
      { id: "p1", name: "Running", active_sessions: 1 },
      { id: "p2", name: "Idle",    active_sessions: 0 },
    ]);
    const dotOn = btn.querySelector(".activity-btn-live-dot");
    assert.ok(dotOn, "active_sessions > 0 must light the dot via renderProjectsList");

    // Now rerender with nothing active
    win.renderProjectsList([
      { id: "p1", name: "Idle",    active_sessions: 0 },
      { id: "p2", name: "Idle",    active_sessions: 0 },
    ]);
    const dotOff = btn.querySelector(".activity-btn-live-dot");
    assert.equal(dotOff, null, "no active sessions must clear the dot");
  });
});
