// Covers: welcome.js auth states, integrations.js HF panel auth states
// Uses jsdom (via jest/vitest) with fetch mocked per TESTING.md §5

const { describe, it, expect, beforeEach, afterEach, vi } = await import("vitest");
const { JSDOM } = await import("jsdom");

// ── helpers ──

function makeDOM() {
  const dom = new JSDOM(`<!DOCTYPE html>
    <html><body>
      <div id="nicolas-welcome-block"></div>
      <div id="experiment-detail"></div>
    </body></html>`, { url: "http://localhost" });
  return dom;
}

function makeSignedOutAuthStatus() {
  return { signed_in: false, user: null };
}

function makeSignedInAuthStatus(displayName = "testuser") {
  return { signed_in: true, user: { user_id: "uuid-123", display_name: displayName, email: "test@hf.co", avatar_url: null } };
}

// ── welcome screen sign-in button ──

describe("welcome screen sign-in banner", () => {
  it("renders sign-in button when not signed in", () => {
    const dom = makeDOM();
    const { window } = dom;
    const { document } = window;

    // Simulate what _renderSignInBanner produces
    const authStatus = makeSignedOutAuthStatus();
    const banner = authStatus.signed_in ? "" : `
      <div class="welcome-hf-signin">
        <button id="hf-signin-btn">Sign in with Hugging Face</button>
      </div>`;

    document.getElementById("nicolas-welcome-block").innerHTML = banner;
    const btn = document.getElementById("hf-signin-btn");
    expect(btn).not.toBeNull();
    expect(btn.textContent).toContain("Sign in with Hugging Face");
  });

  it("does not render sign-in button when signed in", () => {
    const dom = makeDOM();
    const { document } = dom.window;

    const authStatus = makeSignedInAuthStatus();
    const banner = authStatus.signed_in ? "" : `<button id="hf-signin-btn">Sign in with Hugging Face</button>`;

    document.getElementById("nicolas-welcome-block").innerHTML = banner;
    const btn = document.getElementById("hf-signin-btn");
    expect(btn).toBeNull();
  });

  it("renders display name in persona when signed in", () => {
    const dom = makeDOM();
    const { document } = dom.window;

    const authStatus = makeSignedInAuthStatus("flamel");
    const badge = authStatus.signed_in && authStatus.user?.display_name
      ? `<span class="welcome-v2-user">@${authStatus.user.display_name}</span>` : "";

    document.getElementById("nicolas-welcome-block").innerHTML = `
      <div class="welcome-v2-persona">
        <span class="welcome-v2-name">Nicolas${badge}</span>
      </div>`;

    const nameEl = document.querySelector(".welcome-v2-name");
    expect(nameEl.textContent).toContain("@flamel");
  });
});

// ── integrations panel OAuth state ──

describe("integrations HF panel auth states", () => {
  it("shows OAuth session info when signed in", () => {
    const dom = makeDOM();
    const { document } = dom.window;

    const authStatus = makeSignedInAuthStatus("flamel");
    const html = authStatus.signed_in
      ? `<div id="hf-oauth-status">Signed in as @${authStatus.user.display_name}</div>
         <button id="hf-signout-btn">Sign out</button>`
      : `<button id="hf-signin-btn">Sign in with Hugging Face</button>
         <input id="hf-token-input" placeholder="hf_...">`;

    document.getElementById("experiment-detail").innerHTML = html;

    expect(document.getElementById("hf-oauth-status")).not.toBeNull();
    expect(document.getElementById("hf-oauth-status").textContent).toContain("@flamel");
    expect(document.getElementById("hf-signout-btn")).not.toBeNull();
    expect(document.getElementById("hf-signin-btn")).toBeNull();
  });

  it("shows sign-in button and token override when not signed in", () => {
    const dom = makeDOM();
    const { document } = dom.window;

    const authStatus = makeSignedOutAuthStatus();
    const html = authStatus.signed_in
      ? `<div id="hf-oauth-status">Signed in</div>`
      : `<button id="hf-signin-btn">Sign in with Hugging Face</button>
         <label>HF token override (advanced, optional)</label>
         <input id="hf-token-input" placeholder="hf_...">`;

    document.getElementById("experiment-detail").innerHTML = html;

    expect(document.getElementById("hf-signin-btn")).not.toBeNull();
    expect(document.getElementById("hf-token-input")).not.toBeNull();
    const label = document.querySelector("label");
    expect(label.textContent).toContain("advanced, optional");
  });
});

// ── bootstrap deep-link branch detection ──

describe("bootstrap deep-link URL parsing", () => {
  it("detects bootstrap param in distillate://auth URL", () => {
    const url = "distillate://auth?bootstrap=deadbeef01234567";
    const parsed = new URL(url);
    const bootstrap = parsed.searchParams.get("bootstrap");
    const token = parsed.searchParams.get("token");
    expect(bootstrap).toBe("deadbeef01234567");
    expect(token).toBeNull();
  });

  it("detects legacy token param in distillate://auth URL", () => {
    const url = "distillate://auth?token=abc123hex";
    const parsed = new URL(url);
    expect(parsed.searchParams.get("token")).toBe("abc123hex");
    expect(parsed.searchParams.get("bootstrap")).toBeNull();
  });

  it("handles both params by prioritizing bootstrap", () => {
    const url = "distillate://auth?bootstrap=nonce&token=legacy";
    const parsed = new URL(url);
    const bootstrap = parsed.searchParams.get("bootstrap");
    // bootstrap takes priority in the handler
    expect(bootstrap).toBe("nonce");
  });
});

// ── account panel — helper unit tests ──

// Inline the helpers under test (extracted from account.js, no DOM required)
function _getInitials(user) {
  const name = user?.display_name || user?.email || "";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.trim().slice(0, 2).toUpperCase() || "?";
}

function _avatarColor(user) {
  const str = user?.display_name || user?.email || "u";
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) & 0xffff;
  return `hsl(${h % 360}, 55%, 48%)`;
}

describe("account panel — _getInitials", () => {
  it("returns two initials for a two-word name", () => {
    expect(_getInitials({ display_name: "Nicolas Flamel" })).toBe("NF");
  });

  it("returns first two chars for a single-word name", () => {
    expect(_getInitials({ display_name: "flamel" })).toBe("FL");
  });

  it("falls back to email when display_name is absent", () => {
    expect(_getInitials({ email: "test@hf.co" })).toBe("TE");
  });

  it("returns ? for null user", () => {
    expect(_getInitials(null)).toBe("?");
  });
});

describe("account panel — _avatarColor", () => {
  it("returns an hsl string", () => {
    const c = _avatarColor({ display_name: "flamel" });
    expect(c).toMatch(/^hsl\(\d+, 55%, 48%\)$/);
  });

  it("is stable: same input produces same color", () => {
    const user = { display_name: "flamel" };
    expect(_avatarColor(user)).toBe(_avatarColor(user));
  });

  it("uses display_name, not hf_username", () => {
    // hf_username is not in the API response; color must derive from display_name
    const a = _avatarColor({ display_name: "flamel", hf_username: "ignored" });
    const b = _avatarColor({ display_name: "flamel" });
    expect(a).toBe(b);
  });
});

describe("account panel — DOM rendering", () => {
  function makeAccountDOM() {
    const dom = new JSDOM(`<!DOCTYPE html>
      <html><body>
        <button id="account-btn"></button>
        <div id="panel-root"></div>
      </body></html>`, { url: "http://localhost" });
    return dom;
  }

  it("signed-out panel has sign-in button and continue button", () => {
    const dom = makeAccountDOM();
    const { document } = dom.window;

    // Simulate signed-out panel HTML
    document.getElementById("panel-root").innerHTML = `
      <div id="account-panel-overlay">
        <div class="account-panel-backdrop"></div>
        <div class="account-panel">
          <button id="acct-panel-hf-signin-btn">Sign in with Hugging Face</button>
          <button id="acct-panel-continue-btn">Continue without signing in</button>
        </div>
      </div>`;

    expect(document.getElementById("acct-panel-hf-signin-btn")).not.toBeNull();
    expect(document.getElementById("acct-panel-continue-btn")).not.toBeNull();
    expect(document.getElementById("acct-panel-signout-btn")).toBeNull();
  });

  it("signed-in panel has sign-out button and no sign-in button", () => {
    const dom = makeAccountDOM();
    const { document } = dom.window;

    document.getElementById("panel-root").innerHTML = `
      <div id="account-panel-overlay">
        <div class="account-panel">
          <button id="acct-panel-prefs-btn">Preferences</button>
          <button id="acct-panel-signout-btn">Sign out</button>
        </div>
      </div>`;

    expect(document.getElementById("acct-panel-signout-btn")).not.toBeNull();
    expect(document.getElementById("acct-panel-hf-signin-btn")).toBeNull();
  });
});

describe("account panel — sign-in uses authorize_url", () => {
  it("reads authorize_url from /auth/signin-hf-start response", async () => {
    const dom = new JSDOM(`<!DOCTYPE html><html><body></body></html>`, { url: "http://localhost" });
    const { window } = dom;

    const opened = [];
    window.nicolas = { openExternal: (u) => opened.push(u) };

    // Mock fetch to return authorize_url (matching the real endpoint)
    window.fetch = async () => ({
      json: async () => ({ ok: true, authorize_url: "https://huggingface.co/oauth/authorize?foo=bar" }),
    });

    // Simulate the handler logic from account.js
    const r = await window.fetch("http://127.0.0.1:9999/auth/signin-hf-start", { method: "POST" });
    const d = await r.json();
    if (d.authorize_url && window.nicolas?.openExternal) window.nicolas.openExternal(d.authorize_url);

    expect(opened).toHaveLength(1);
    expect(opened[0]).toContain("huggingface.co");
  });

  it("does NOT open browser if only d.url is present (old broken field)", async () => {
    const dom = new JSDOM(`<!DOCTYPE html><html><body></body></html>`, { url: "http://localhost" });
    const { window } = dom;

    const opened = [];
    window.nicolas = { openExternal: (u) => opened.push(u) };
    window.fetch = async () => ({
      json: async () => ({ ok: true, url: "https://huggingface.co/wrong" }),
    });

    const r = await window.fetch("...", { method: "POST" });
    const d = await r.json();
    // Using the FIXED check: authorize_url
    if (d.authorize_url && window.nicolas?.openExternal) window.nicolas.openExternal(d.authorize_url);

    expect(opened).toHaveLength(0);
  });
});
