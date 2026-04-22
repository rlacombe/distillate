// Covers: desktop/renderer/experiments.js (groupExperimentsByProject + render)
/**
 * Experiments belong to research Projects (the v2 Project primitive,
 * stored in code as `workspace_id`). The Experiments sidebar should
 * surface that hierarchy with a heading per Project so users can scan
 * "what work belongs to which research direction" without reading
 * every name.
 *
 * Pure DOM/grouping logic -> jsdom unit tests.
 *
 * Invariants:
 * - One heading per distinct workspace_id.
 * - The Project containing an active experiment sorts to the top
 *   (active work always visible without scrolling).
 * - Within a group, the existing experiment sort is preserved
 *   (active first, then most-recently-added).
 * - Heading text is the workspace name (which already embeds emojis
 *   like "Distillate ⚗️"), falling back to the workspace_id.
 * - Experiments missing a workspace_id are gathered under an "Unfiled"
 *   group at the very bottom.
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
    <div class="sidebar-header"></div>
  </body></html>`, {
    runScripts: "outside-only",
    // Non-opaque origin so localStorage is available (experiments.js reads it)
    url: "http://localhost/",
  });
  dom.window.serverPort = 0;
  dom.window.cachedProjects = [];
  dom.window.currentProjectId = null;
  dom.window.escapeHtml = (s) => String(s ?? "");
  dom.window.showToast = () => {};
  dom.window.renderProjectDetail = () => {};
  dom.window.startSessionPolling = () => {};
  dom.window.stopSessionPolling = () => {};
  dom.window.fetch = () => Promise.resolve({ json: () => Promise.resolve({ projects: [] }) });
  dom.window.localStorage.setItem("distillate-onboarded", "1");
  // charts.js dependency referenced from experiments.js (getDisplayRuns)
  dom.window.getDisplayRuns = (runs) =>
    (runs || []).filter((r) => r.results && Object.values(r.results).some((v) => typeof v === "number"));
  // Other module-locals experiments.js references that we don't exercise here
  dom.window.eval("var sessionDoneBells = new Set(); var sseSource = null; function selectProject(){}");
  return dom.window;
}

function loadExperimentsJs(win) {
  const src = fs.readFileSync(
    path.join(__dirname, "..", "renderer", "experiments.js"),
    "utf-8",
  );
  win.eval(src);
}

function headings(win) {
  return [...win.document.querySelectorAll(".sidebar-project-heading")];
}

function itemsUnder(heading) {
  // Walk siblings until the next heading or end
  const out = [];
  let n = heading.nextElementSibling;
  while (n && !n.classList.contains("sidebar-project-heading")) {
    if (n.classList.contains("sidebar-item")) out.push(n);
    n = n.nextElementSibling;
  }
  return out;
}

describe("groupExperimentsByProject", () => {
  let win, group;
  beforeEach(() => {
    win = makeWindow();
    loadExperimentsJs(win);
    group = win.groupExperimentsByProject;
    assert.equal(typeof group, "function",
      "experiments.js must expose window.groupExperimentsByProject");
  });

  it("returns one group per distinct workspace_id", () => {
    const groups = group([
      { id: "e1", name: "A", workspace_id: "ws1", workspace_name: "P1" },
      { id: "e2", name: "B", workspace_id: "ws2", workspace_name: "P2" },
      { id: "e3", name: "C", workspace_id: "ws1", workspace_name: "P1" },
    ]);
    assert.equal(groups.length, 2);
  });

  it("sorts the group containing an active experiment to the top", () => {
    const groups = group([
      { id: "e1", name: "Idle1", workspace_id: "wsA", workspace_name: "Alpha", active_sessions: 0 },
      { id: "e2", name: "Idle2", workspace_id: "wsA", workspace_name: "Alpha", active_sessions: 0 },
      { id: "e3", name: "Live",  workspace_id: "wsB", workspace_name: "Beta",  active_sessions: 1 },
    ]);
    assert.equal(groups[0].workspace_id, "wsB",
      "group with an active experiment must be first");
  });

  it("uses workspace_name for the heading, falling back to workspace_id", () => {
    const groups = group([
      { id: "e1", workspace_id: "wsA", workspace_name: "Alpha" },
      { id: "e2", workspace_id: "wsB", workspace_name: "" },
    ]);
    const byId = Object.fromEntries(groups.map((g) => [g.workspace_id, g]));
    assert.equal(byId["wsA"].label, "Alpha");
    assert.equal(byId["wsB"].label, "wsB", "missing name -> fall back to id");
  });

  it("places experiments without a workspace_id in an 'Unfiled' group at the bottom", () => {
    const groups = group([
      { id: "e1", workspace_id: "wsA", workspace_name: "Alpha" },
      { id: "e2", workspace_id: "",    workspace_name: "" },
      { id: "e3", workspace_id: null,  workspace_name: null },
    ]);
    const last = groups[groups.length - 1];
    assert.equal(last.label, "Unfiled");
    assert.equal(last.experiments.length, 2);
  });

  it("preserves the input order of experiments within a group", () => {
    // The caller (renderProjectsList) pre-sorts by active-then-added_at;
    // the grouping function must NOT re-shuffle that order.
    const groups = group([
      { id: "first",  workspace_id: "ws", workspace_name: "P" },
      { id: "second", workspace_id: "ws", workspace_name: "P" },
      { id: "third",  workspace_id: "ws", workspace_name: "P" },
    ]);
    // Array.from(): coerce jsdom-realm Array to node-realm so strict
    // deepEqual prototype check passes.
    assert.deepEqual(Array.from(groups[0].experiments.map((e) => e.id)),
      ["first", "second", "third"]);
  });
});

describe("renderProjectsList renders project headings", () => {
  let win;
  beforeEach(() => {
    win = makeWindow();
    loadExperimentsJs(win);
  });

  it("renders one heading element per Project group", () => {
    win.renderProjectsList([
      { id: "e1", name: "Alpha XP",  workspace_id: "wsA", workspace_name: "Alpha" },
      { id: "e2", name: "Beta XP",   workspace_id: "wsB", workspace_name: "Beta" },
      { id: "e3", name: "Alpha XP2", workspace_id: "wsA", workspace_name: "Alpha" },
    ]);
    assert.equal(headings(win).length, 2);
  });

  it("heading text contains the workspace name", () => {
    win.renderProjectsList([
      { id: "e1", name: "Test", workspace_id: "wsA", workspace_name: "Distillate ⚗️" },
    ]);
    const h = headings(win)[0];
    assert.match(h.textContent, /Distillate/);
  });

  it("heading shows the count of experiments in the group", () => {
    win.renderProjectsList([
      { id: "e1", workspace_id: "ws", workspace_name: "P", name: "a" },
      { id: "e2", workspace_id: "ws", workspace_name: "P", name: "b" },
      { id: "e3", workspace_id: "ws", workspace_name: "P", name: "c" },
    ]);
    const h = headings(win)[0];
    const countEl = h.querySelector(".sidebar-project-heading-count");
    assert.ok(countEl, "heading must include a count element");
    assert.equal(countEl.textContent, "3");
  });

  it("groups experiments under their own heading", () => {
    win.renderProjectsList([
      { id: "e1", name: "Alpha-A", workspace_id: "wsA", workspace_name: "Alpha" },
      { id: "e2", name: "Beta-A",  workspace_id: "wsB", workspace_name: "Beta" },
      { id: "e3", name: "Alpha-B", workspace_id: "wsA", workspace_name: "Alpha" },
    ]);
    const hs = headings(win);
    const labels = hs.map((h) => h.textContent);
    const alphaIdx = labels.findIndex((l) => /Alpha/.test(l));
    const betaIdx  = labels.findIndex((l) => /Beta/.test(l));
    const alphaItems = Array.from(itemsUnder(hs[alphaIdx]).map((i) => i.dataset.id).sort());
    const betaItems  = Array.from(itemsUnder(hs[betaIdx]).map((i) => i.dataset.id).sort());
    assert.deepEqual(alphaItems, ["e1", "e3"]);
    assert.deepEqual(betaItems, ["e2"]);
  });

  it("group containing an active experiment renders before idle groups", () => {
    win.renderProjectsList([
      { id: "e1", name: "Idle1", workspace_id: "wsA", workspace_name: "Alpha", active_sessions: 0, added_at: "2026-01-01" },
      { id: "e2", name: "Idle2", workspace_id: "wsA", workspace_name: "Alpha", active_sessions: 0, added_at: "2026-01-02" },
      { id: "e3", name: "Live",  workspace_id: "wsB", workspace_name: "Beta",  active_sessions: 1, added_at: "2026-01-03" },
    ]);
    const hs = headings(win);
    assert.match(hs[0].textContent, /Beta/, "Beta has the active experiment, must render first");
  });

  it("still renders experiments correctly when only one Project is present", () => {
    win.renderProjectsList([
      { id: "e1", name: "Solo", workspace_id: "ws", workspace_name: "Workbench" },
    ]);
    assert.equal(headings(win).length, 1);
    const items = win.document.querySelectorAll(".sidebar-item");
    assert.equal(items.length, 1);
    assert.equal(items[0].dataset.id, "e1");
  });
});
