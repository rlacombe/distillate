/**
 * Tests for drafts-dock.js — the non-blocking panel that holds pending
 * session wrapup summaries.
 *
 * Run: cd desktop && node --test test/drafts-dock.test.js
 *
 * Strategy: static source analysis for DOM-touching code (following
 * paper-reader.test.js and insights.test.js), plus a few pure-behaviour
 * tests via a minimal DOM shim. No full Electron runtime is required.
 *
 * What this file pins:
 *   1. Public API surface on window (addDraftToDock, removeDraftFromDock,
 *      hasDraftInDock, getDraftsDockCount).
 *   2. Each card carries the session id in data + DOM id patterns the
 *      styles/tests can hook on.
 *   3. Save hits PATCH /summary; Discard hits POST /wrapup/discard.
 *   4. The dock hides itself when empty, shows when non-empty.
 *   5. REGRESSION: _showCompletionModal is gone from workspaces.js and the
 *      `.modal-overlay.completion-modal` selector is no longer used —
 *      that was the one line that made parallel wrapups destroy earlier
 *      drafts.
 */

const { describe, it, before, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, existsSync } = require("node:fs");
const { resolve } = require("node:path");

const dockPath = resolve(__dirname, "../renderer/drafts-dock.js");
const projectsPath = resolve(__dirname, "../renderer/workspaces.js");
const layoutPath = resolve(__dirname, "../renderer/layout.js");
const indexPath = resolve(__dirname, "../renderer/index.html");


// ─── File exists ──────────────────────────────────────────────────────

describe("drafts-dock.js — file layout", () => {
  it("drafts-dock.js exists", () => {
    assert.ok(
      existsSync(dockPath),
      "desktop/renderer/drafts-dock.js must exist as a dedicated module",
    );
  });

  it("drafts-dock.js is wired into index.html via a <script> tag", () => {
    const html = readFileSync(indexPath, "utf-8");
    assert.match(
      html,
      /<script[^>]+src=["']drafts-dock\.js["']/,
      "index.html must load drafts-dock.js",
    );
  });

  it("index.html contains the #drafts-dock container", () => {
    const html = readFileSync(indexPath, "utf-8");
    assert.match(
      html,
      /id=["']drafts-dock["']/,
      "index.html must have <div id=\"drafts-dock\"> as the mount point",
    );
  });
});


// ─── Public API surface on window ────────────────────────────────────

describe("drafts-dock.js — exposed API", () => {
  let source;
  before(() => { source = readFileSync(dockPath, "utf-8"); });

  it("exposes window.addDraftToDock", () => {
    assert.match(source, /window\.addDraftToDock\s*=/);
  });

  it("exposes window.removeDraftFromDock", () => {
    assert.match(source, /window\.removeDraftFromDock\s*=/);
  });

  it("exposes window.hasDraftInDock", () => {
    assert.match(source, /window\.hasDraftInDock\s*=/);
  });

  it("exposes window.getDraftsDockCount (for tests + dev)", () => {
    assert.match(source, /window\.getDraftsDockCount\s*=/);
  });
});


// ─── DOM structure invariants ────────────────────────────────────────

describe("drafts-dock.js — DOM scaffolding", () => {
  let source;
  before(() => { source = readFileSync(dockPath, "utf-8"); });

  it("creates per-session cards with id pattern draft-card-<sessionId>", () => {
    // Accept either template literal or string concatenation for the id.
    assert.ok(
      /draft-card-\$\{[^}]*sessionId[^}]*\}/.test(source) ||
      /"draft-card-"\s*\+\s*\w*sessionId/.test(source),
      "each card must use the id pattern draft-card-<sessionId> so removeDraftFromDock can find it",
    );
  });

  it("stores workspaceId + sessionId as data attributes on the card", () => {
    assert.match(source, /data-ws-id/);
    assert.match(source, /data-session-id/);
  });

  it("gives the summary textarea id draft-summary-<sessionId>", () => {
    assert.ok(
      /draft-summary-\$\{[^}]*sessionId[^}]*\}/.test(source) ||
      /"draft-summary-"\s*\+\s*\w*sessionId/.test(source),
      "save flow reads the edited summary by id — missing this breaks save",
    );
  });
});


// ─── Endpoint wiring ─────────────────────────────────────────────────

describe("drafts-dock.js — backend wiring", () => {
  let source;
  before(() => { source = readFileSync(dockPath, "utf-8"); });

  it("Save button PATCHes /workspaces/{ws}/sessions/{sid}/summary", () => {
    assert.match(
      source,
      /\/workspaces\/[^'"`]*\/sessions\/[^'"`]*\/summary/,
      "Save must hit the summary endpoint",
    );
    assert.match(source, /method:\s*["']PATCH["']/);
  });

  it("Discard (X) POSTs /workspaces/{ws}/sessions/{sid}/wrapup/discard", () => {
    assert.match(
      source,
      /\/workspaces\/[^'"`]*\/sessions\/[^'"`]*\/wrapup\/discard/,
      "Discard must hit the wrapup/discard endpoint — keeps session running",
    );
  });

  it("refreshes the workspace sidebar on save (session disappears)", () => {
    assert.match(
      source,
      /fetchWorkspaces\s*\(/,
      "after a save the session should disappear from the sidebar",
    );
  });

  it("does NOT navigate to the project view on save (focus preserved)", () => {
    // The modal flow called selectWorkspace(workspaceId) after save, which
    // yanked the user from wherever they were to the project detail page.
    // The dock is non-blocking, so Save must leave the user's view alone.
    assert.ok(
      !/selectWorkspace\s*\(/.test(source),
      "drafts-dock.js must not call selectWorkspace — saving should preserve focus",
    );
  });
});


// ─── Show/hide behaviour (pure function) ─────────────────────────────

describe("drafts-dock.js — visibility behaviour via DOM shim", () => {
  // Minimal shim: we don't need a real browser, just enough to exercise
  // the add/remove logic. The module should read/write document/window.
  let dom;

  beforeEach(() => {
    dom = setupShim();
    loadModuleIntoShim(dockPath, dom);
  });

  it("dock is hidden after initialization when empty", () => {
    const dock = dom.document.getElementById("drafts-dock");
    assert.ok(dock, "drafts-dock mount point must exist");
    assert.equal(dom.window.getDraftsDockCount(), 0);
    assert.ok(
      dock.hidden === true || /display:\s*none/i.test(dock.style.cssText || "")
        || dock.classList.contains("hidden"),
      "empty dock should be hidden (hidden attr, display:none, or .hidden class)",
    );
  });

  it("dock becomes visible when a draft is added", () => {
    dom.window.addDraftToDock("ws1", "s1", "Alpha", "# Alpha\n- bullet");
    const dock = dom.document.getElementById("drafts-dock");
    assert.equal(dom.window.getDraftsDockCount(), 1);
    assert.ok(
      dock.hidden === false
        || !/display:\s*none/i.test(dock.style.cssText || "")
        || !dock.classList.contains("hidden"),
      "dock must be visible when at least one draft is present",
    );
    assert.ok(dom.window.hasDraftInDock("s1"));
  });

  it("holds multiple drafts simultaneously (the whole point)", () => {
    dom.window.addDraftToDock("ws1", "s1", "Alpha", "# A\n- a");
    dom.window.addDraftToDock("ws1", "s2", "Beta",  "# B\n- b");
    dom.window.addDraftToDock("ws1", "s3", "Gamma", "# G\n- g");
    assert.equal(dom.window.getDraftsDockCount(), 3);
    assert.ok(dom.window.hasDraftInDock("s1"));
    assert.ok(dom.window.hasDraftInDock("s2"));
    assert.ok(dom.window.hasDraftInDock("s3"));
  });

  it("removing one draft leaves others intact", () => {
    dom.window.addDraftToDock("ws1", "s1", "Alpha", "# A\n- a");
    dom.window.addDraftToDock("ws1", "s2", "Beta",  "# B\n- b");
    dom.window.removeDraftFromDock("s1");
    assert.equal(dom.window.getDraftsDockCount(), 1);
    assert.ok(!dom.window.hasDraftInDock("s1"));
    assert.ok(dom.window.hasDraftInDock("s2"));
  });

  it("dock hides again once the last draft is removed", () => {
    dom.window.addDraftToDock("ws1", "s1", "Alpha", "# A");
    dom.window.removeDraftFromDock("s1");
    const dock = dom.document.getElementById("drafts-dock");
    assert.equal(dom.window.getDraftsDockCount(), 0);
    assert.ok(
      dock.hidden === true
        || /display:\s*none/i.test(dock.style.cssText || "")
        || dock.classList.contains("hidden"),
      "empty dock must re-hide",
    );
  });

  it("adding a draft for a session already in the dock is idempotent", () => {
    dom.window.addDraftToDock("ws1", "s1", "Alpha", "# A\n- a");
    dom.window.addDraftToDock("ws1", "s1", "Alpha", "# A refreshed\n- a");
    assert.equal(
      dom.window.getDraftsDockCount(), 1,
      "same sessionId must not produce two cards",
    );
  });

  it("clicking Save on one card removes only that card", async () => {
    dom.window.addDraftToDock("ws1", "s1", "Alpha", "# A\n- a");
    dom.window.addDraftToDock("ws1", "s2", "Beta",  "# B\n- b");

    // Find the Save button on card s1 and fire its click handler.
    const card = dom.document.getElementById("draft-card-s1");
    assert.ok(card, "card for s1 should exist");
    const saveBtn = card.querySelector(".draft-card-save");
    assert.ok(saveBtn, ".draft-card-save must exist on each card");

    // Stub fetch so Save's PATCH resolves success.
    dom.window.fetch = async () => ({
      ok: true, json: async () => ({ success: true }),
    });
    for (const fn of saveBtn._listeners.click || []) await fn({ preventDefault(){}, stopPropagation(){} });

    assert.ok(!dom.window.hasDraftInDock("s1"), "s1 should be gone after save");
    assert.ok(dom.window.hasDraftInDock("s2"), "s2 should still be in dock");
  });

  it("clicking Discard (X) on one card removes only that card", async () => {
    dom.window.addDraftToDock("ws1", "s1", "Alpha", "# A\n- a");
    dom.window.addDraftToDock("ws1", "s2", "Beta",  "# B\n- b");

    const card = dom.document.getElementById("draft-card-s1");
    const closeBtn = card.querySelector(".draft-card-close");
    assert.ok(closeBtn, ".draft-card-close must exist");

    dom.window.fetch = async () => ({
      ok: true, json: async () => ({ success: true }),
    });
    for (const fn of closeBtn._listeners.click || []) await fn({ preventDefault(){}, stopPropagation(){} });

    assert.ok(!dom.window.hasDraftInDock("s1"));
    assert.ok(dom.window.hasDraftInDock("s2"));
  });
});


// ─── Regression: the old blocking modal is gone ──────────────────────

describe("regression: completion-modal anti-pattern removed", () => {
  it("workspaces.js no longer defines _showCompletionModal", () => {
    const src = readFileSync(projectsPath, "utf-8");
    assert.ok(
      !/function\s+_showCompletionModal\s*\(/.test(src),
      "_showCompletionModal was the blocking modal — replace with addDraftToDock",
    );
  });

  it("workspaces.js no longer removes .completion-modal via ?.remove()", () => {
    const src = readFileSync(projectsPath, "utf-8");
    assert.ok(
      !/\.modal-overlay\.completion-modal["']?\)\??\.remove\(\)/.test(src),
      "the ?.remove() line was the bug — it destroyed in-flight drafts when a second wrapup finished",
    );
  });

  it("layout.js calls addDraftToDock instead of _showCompletionModal", () => {
    const src = readFileSync(layoutPath, "utf-8");
    assert.ok(
      !/_showCompletionModal\s*\(/.test(src),
      "title-bar ✓ must route through addDraftToDock now",
    );
    assert.match(
      src,
      /addDraftToDock\s*\(/,
      "layout.js should call window.addDraftToDock on successful /complete",
    );
  });

  it("workspaces.js calls addDraftToDock from completeCodingSession", () => {
    const src = readFileSync(projectsPath, "utf-8");
    assert.match(
      src,
      /addDraftToDock\s*\(/,
      "sidebar ✓ must route through addDraftToDock",
    );
  });
});


// ─── Helpers ─────────────────────────────────────────────────────────

/** Minimal DOM shim: enough for drafts-dock.js to render and query. */
function setupShim() {
  const mkEl = (tag) => {
    // classList backed by a Set; className setter syncs into it (the browser
    // behaviour the module relies on when it does `el.className = "foo"`).
    const classSet = new Set();
    const classList = {
      add: (c) => classSet.add(c),
      remove: (c) => classSet.delete(c),
      contains: (c) => classSet.has(c),
      toggle: (c, force) => {
        if (force === true) { classSet.add(c); return true; }
        if (force === false) { classSet.delete(c); return false; }
        if (classSet.has(c)) { classSet.delete(c); return false; }
        classSet.add(c); return true;
      },
    };
    let _className = "";
    const el = {
      tagName: tag.toUpperCase(),
      children: [],
      childNodes: [],
      attributes: {},
      dataset: {},
      style: { cssText: "" },
      classList,
      get className() { return _className; },
      set className(v) {
        _className = String(v || "");
        classSet.clear();
        _className.split(/\s+/).filter(Boolean).forEach((c) => classSet.add(c));
      },
      hidden: false,
      textContent: "",
      innerHTML: "",
      value: "",
      _listeners: {},
      setAttribute(k, v) { this.attributes[k] = v; if (k === "id") this.id = v; },
      getAttribute(k) { return this.attributes[k]; },
      appendChild(child) { this.children.push(child); this.childNodes.push(child); child.parentNode = this; return child; },
      removeChild(child) {
        this.children = this.children.filter((c) => c !== child);
        this.childNodes = this.children.slice();
        child.parentNode = null;
        return child;
      },
      remove() { if (this.parentNode) this.parentNode.removeChild(this); },
      addEventListener(ev, fn) { (this._listeners[ev] ||= []).push(fn); },
      querySelector(sel) {
        return findInTree(this, (n) => matchesSel(n, sel));
      },
      querySelectorAll(sel) {
        const out = [];
        walk(this, (n) => { if (matchesSel(n, sel)) out.push(n); });
        return out;
      },
      closest(sel) {
        let n = this;
        while (n) { if (matchesSel(n, sel)) return n; n = n.parentNode; }
        return null;
      },
    };
    return el;
  };
  const walk = (n, fn) => {
    for (const c of n.children || []) { fn(c); walk(c, fn); }
  };
  const findInTree = (n, pred) => {
    for (const c of n.children || []) {
      if (pred(c)) return c;
      const hit = findInTree(c, pred);
      if (hit) return hit;
    }
    return null;
  };
  const matchesSel = (n, sel) => {
    if (sel.startsWith("#")) return n.id === sel.slice(1);
    if (sel.startsWith(".")) return n.classList.contains(sel.slice(1));
    return n.tagName === sel.toUpperCase();
  };

  const document = {
    body: mkEl("body"),
    createElement: mkEl,
    getElementById(id) { return findInTree({ children: [this.body.children[0] ? this.body : this.body] }, (n) => n.id === id) || walkFind(this.body, (n) => n.id === id); },
  };
  function walkFind(root, pred) {
    if (pred(root)) return root;
    for (const c of root.children || []) {
      const hit = walkFind(c, pred);
      if (hit) return hit;
    }
    return null;
  }
  document.getElementById = (id) => walkFind(document.body, (n) => n.id === id);
  document.querySelector = (sel) => document.body.querySelector(sel);
  document.querySelectorAll = (sel) => document.body.querySelectorAll(sel);

  // Pre-install the #drafts-dock mount point (matches what index.html provides)
  const dock = mkEl("div");
  dock.setAttribute("id", "drafts-dock");
  dock.hidden = true;
  document.body.appendChild(dock);

  const window = {
    fetch: async () => ({ ok: true, json: async () => ({ success: true }) }),
    addEventListener: () => {},
    removeEventListener: () => {},
  };

  return { document, window };
}

/** Load a renderer module into our shim by evaluating it with injected globals. */
function loadModuleIntoShim(modulePath, dom) {
  const source = readFileSync(modulePath, "utf-8");
  // Provide showToast, fetchWorkspaces, serverPort, escapeHtml as no-ops / stubs.
  // `fetch` resolves to dom.window.fetch via a closure — tests can swap
  // dom.window.fetch at any time and the module will pick it up.
  const stubs = `
    var showToast = function(){};
    var fetchWorkspaces = function(){};
    var escapeHtml = function(s){ return String(s == null ? "" : s); };
    var serverPort = 8742;
    var selectWorkspace = function(){};
    var _selectedWorkspace = null;
    var _selectedSession = null;
    var detachTerminal = function(){};
    var showSessionEmpty = function(){};
    var fetch = function(){ return window.fetch.apply(window, arguments); };
  `;
  // Run in a function-scope with document + window injected.
  // eslint-disable-next-line no-new-func
  new Function("document", "window", stubs + "\n" + source)(dom.document, dom.window);
}
