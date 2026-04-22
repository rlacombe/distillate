/**
 * Behavioural tests for welcome.js — the "Resume last thread" card on
 * the welcome screen. Driven through a real DOM via jsdom.
 *
 * Strategy: load welcome.js into a jsdom window, stub fetch for both
 * /welcome/state and /nicolas/sessions, inject globals the script
 * references (escapeHtml, renderNarrationMarkdown, activateNicolasSession,
 * serverPort), then call renderWelcomeScreen() and assert on the DOM.
 *
 * These tests describe the DESIRED behaviour of the resume-last-thread
 * feature. They will FAIL until welcome.js is extended with the resume
 * card logic described in docs/research/resume-last-thread-plan.md.
 *
 * Run: cd desktop && node --test test/welcome-resume.test.js
 */

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { JSDOM } = require("jsdom");

const SOURCE = readFileSync(
  resolve(__dirname, "../renderer/welcome.js"),
  "utf-8",
);


// ─── Test harness ────────────────────────────────────────────────────────

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Build a jsdom window loaded with welcome.js.
 *
 * @param {object} opts
 * @param {Array}  opts.sessions       list returned by GET /nicolas/sessions
 * @param {object} opts.welcomeState   object returned by GET /welcome/state
 * @returns {{window, calls, fetchMock, activateCalls}}
 */
function setupWelcome({
  sessions = [],
  welcomeState = {
    state_id: "onboarding",
    greeting: "Welcome",
    strip: { type: "onboarding", label: "Welcome", annotation: "", steps: [] },
    narration_paragraphs: ["Welcome to Distillate."],
    suggestions: [],
    input_placeholder: "Say something...",
  },
} = {}) {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
      <div id="nicolas-welcome-block"></div>
    </body></html>`,
    { url: "http://127.0.0.1:8742/ui/", runScripts: "dangerously" },
  );

  const { window } = dom;
  const { document } = window;

  const calls = { fetches: [] };
  const activateCalls = [];

  // Globals that welcome.js references.
  window.serverPort = 8742;
  window.escapeHtml = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  window.renderNarrationMarkdown = (s) => window.escapeHtml(s);
  window.handleWelcomeSuggestion = () => {};
  window.activateNicolasSession = (sid) => {
    activateCalls.push(sid);
  };

  window.fetch = async (url) => {
    calls.fetches.push(url);
    if (url.includes("/nicolas/sessions")) {
      return {
        ok: true,
        json: async () => ({
          sessions,
          active_session_id: sessions.length ? sessions[0].session_id : null,
        }),
      };
    }
    if (url.includes("/welcome/state")) {
      return { ok: true, json: async () => welcomeState };
    }
    return { ok: false, json: async () => ({}) };
  };

  // Load welcome.js into the window context.
  const script = document.createElement("script");
  script.textContent = SOURCE;
  document.head.appendChild(script);

  return { window, document, calls, activateCalls };
}

function makeSession({
  session_id = "sid-1",
  name = "Thread",
  preview = "hello",
  ageMs = 0,
} = {}) {
  const iso = new Date(Date.now() - ageMs).toISOString();
  return {
    session_id,
    name,
    preview,
    created_at: iso,
    last_activity: iso,
  };
}


// ─── Tests ───────────────────────────────────────────────────────────────

describe("welcome.js — Resume last thread card", () => {

  it("renders no resume card when there are no threads", async () => {
    const { window, document } = setupWelcome({ sessions: [] });
    await window.renderWelcomeScreen();
    const card = document.querySelector(".welcome-v2-resume");
    assert.equal(card, null, "No resume card expected when sessions is empty");
  });

  it("renders a resume card when the latest thread is recent", async () => {
    const { window, document } = setupWelcome({
      sessions: [makeSession({
        session_id: "sid-recent",
        name: "DFM Glycan Generation",
        preview: "Let's build a DFM model",
        ageMs: 2 * 60 * 60 * 1000, // 2 hours
      })],
    });
    await window.renderWelcomeScreen();
    const card = document.querySelector(".welcome-v2-resume");
    assert.ok(card, "Resume card should render for a recent thread");
    assert.match(card.textContent, /DFM Glycan Generation/);
    assert.match(
      card.textContent,
      /Let's build a DFM model/,
      "Card should include the preview text",
    );
  });

  it("does NOT render a resume card when the latest thread is > 7 days old", async () => {
    const { window, document } = setupWelcome({
      sessions: [makeSession({
        session_id: "sid-stale",
        name: "Old Work",
        preview: "ancient",
        ageMs: 8 * DAY_MS, // 8 days
      })],
    });
    await window.renderWelcomeScreen();
    const card = document.querySelector(".welcome-v2-resume");
    assert.equal(card, null, "Stale threads (>7d) should not get a resume card");
  });

  it("clicking the resume card calls activateNicolasSession with the session id", async () => {
    const { window, document, activateCalls } = setupWelcome({
      sessions: [makeSession({
        session_id: "sid-click",
        name: "Click Me",
        ageMs: 10 * 60 * 1000, // 10 minutes
      })],
    });
    await window.renderWelcomeScreen();
    const card = document.querySelector(".welcome-v2-resume");
    assert.ok(card, "Card should exist");
    card.click();
    assert.deepEqual(
      activateCalls,
      ["sid-click"],
      "Clicking the card should activate the thread",
    );
  });

  it("picks the most recent thread when multiple exist (server pre-sorts)", async () => {
    const { window, document } = setupWelcome({
      sessions: [
        makeSession({ session_id: "first", name: "Most Recent", ageMs: 1 * 60 * 1000 }),
        makeSession({ session_id: "second", name: "Older", ageMs: 2 * 60 * 60 * 1000 }),
        makeSession({ session_id: "third", name: "Oldest", ageMs: 5 * 60 * 60 * 1000 }),
      ],
    });
    await window.renderWelcomeScreen();
    const card = document.querySelector(".welcome-v2-resume");
    assert.ok(card, "Card should exist");
    assert.match(
      card.textContent,
      /Most Recent/,
      "Should display the first (most recent) thread",
    );
    assert.doesNotMatch(card.textContent, /Older|Oldest/);
  });

  it("escapes HTML in the thread name", async () => {
    const { window, document } = setupWelcome({
      sessions: [makeSession({
        session_id: "sid-xss",
        name: "<img src=x onerror=alert(1)>",
        preview: "safe preview",
        ageMs: 5 * 60 * 1000,
      })],
    });
    await window.renderWelcomeScreen();
    const card = document.querySelector(".welcome-v2-resume");
    assert.ok(card);
    // No actual <img> tag should end up in the DOM — only the escaped text.
    assert.equal(card.querySelector("img"), null, "Raw <img> must not exist");
    assert.match(card.innerHTML, /&lt;img/);
  });

  it("places the resume card before the persona zone", async () => {
    const { window, document } = setupWelcome({
      sessions: [makeSession({
        session_id: "sid-order",
        name: "Order Test",
        ageMs: 5 * 60 * 1000,
      })],
    });
    await window.renderWelcomeScreen();
    const block = document.getElementById("nicolas-welcome-block");
    assert.ok(block);
    const card = block.querySelector(".welcome-v2-resume");
    const persona = block.querySelector(".welcome-v2-persona");
    assert.ok(card, "Resume card should render");
    assert.ok(persona, "Persona zone should render");
    // DOCUMENT_POSITION_FOLLOWING: `persona` appears after `card`.
    const pos = card.compareDocumentPosition(persona);
    assert.ok(
      pos & window.Node.DOCUMENT_POSITION_FOLLOWING,
      "Resume card must precede the persona zone",
    );
  });

  it("boundary: thread within the 7-day window renders; just past does not", async () => {
    // Slightly under 7 days — shown.
    {
      const { window, document } = setupWelcome({
        sessions: [makeSession({
          session_id: "inside",
          name: "Inside Window",
          ageMs: 7 * DAY_MS - 60 * 1000, // 7d minus 1 minute
        })],
      });
      await window.renderWelcomeScreen();
      assert.ok(
        document.querySelector(".welcome-v2-resume"),
        "Thread just inside the 7d window should show the card",
      );
    }
    // Slightly over 7 days — hidden.
    {
      const { window, document } = setupWelcome({
        sessions: [makeSession({
          session_id: "outside",
          name: "Outside Window",
          ageMs: 7 * DAY_MS + 60 * 1000, // 7d plus 1 minute
        })],
      });
      await window.renderWelcomeScreen();
      assert.equal(
        document.querySelector(".welcome-v2-resume"),
        null,
        "Thread just outside the 7d window should NOT show the card",
      );
    }
  });

  it("renders a relative-time indicator inside the card", async () => {
    const { window, document } = setupWelcome({
      sessions: [makeSession({
        session_id: "sid-time",
        name: "Time Test",
        ageMs: 3 * 60 * 60 * 1000, // 3 hours
      })],
    });
    await window.renderWelcomeScreen();
    const card = document.querySelector(".welcome-v2-resume");
    assert.ok(card);
    // "3h ago" is what _relativeTime would emit for 3 hours.
    assert.match(card.textContent, /3h ago|3 hours? ago/);
  });

  it("fetches /nicolas/sessions alongside /welcome/state at render time", async () => {
    const { window, calls } = setupWelcome({
      sessions: [makeSession({ ageMs: 5 * 60 * 1000 })],
    });
    await window.renderWelcomeScreen();
    const urls = calls.fetches.join(" ");
    assert.match(urls, /\/nicolas\/sessions/, "Must fetch sessions list");
    assert.match(urls, /\/welcome\/state/, "Must fetch welcome state");
  });
});
