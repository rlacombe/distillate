/**
 * Welcome Screen — B+ layout with 7-state fallback chain.
 *
 * Fetches /welcome/state and renders the 4 zones: persona, frontier strip,
 * narration, suggestions, and input field. Handles suggestion clicks by
 * pre-filling the chat input and submitting.
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _welcomeState = null;
let _welcomeLastFetch = 0;
const _WELCOME_STALE_MS = 5 * 60 * 1000; // 5 minutes

// Hide the resume card when the latest thread is older than this — at that
// point the user is clearly starting something new and the card is noise.
const _RESUME_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

// ---------------------------------------------------------------------------
// Tips and examples
// ---------------------------------------------------------------------------

const _NICOLAS_TIPS = [
  { title: "Papers", examples: ["What are the key claims in my queue?", "Find papers related to my recent experiments", "Summarize disagreements across 3 papers on transformers"] },
  { title: "Experiments", examples: ["What's my best run this month?", "Why is this experiment not converging?", "Compare my top 3 runs and tell me what's different"] },
  { title: "Writing", examples: ["Draft a summary of my results", "Generate a table comparing different approaches", "Write an abstract for my findings"] },
  { title: "Discovery", examples: ["What's trending in my research area right now?", "Find papers about [topic] in the library", "Suggest what I should read next"] },
  { title: "Analysis", examples: ["Analyze the trade-offs in my experiments", "Check if my results are statistically significant", "Diagnose what went wrong in this run"] },
];

function _renderTipsSection() {
  return `
    <div class="welcome-v2-tips">
      <div class="tips-header">💡 Things you can ask Nicolas</div>
      <div class="tips-grid">
        ${_NICOLAS_TIPS.map((category) => `
          <div class="tip-category">
            <div class="tip-title">${escapeHtml(category.title)}</div>
            <ul class="tip-examples">
              ${category.examples.map((ex) => `<li class="tip-example">${escapeHtml(ex)}</li>`).join("")}
            </ul>
          </div>
        `).join("")}
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Resume-last-thread helpers
// ---------------------------------------------------------------------------

async function _fetchLatestThread() {
  if (!serverPort) return null;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions`);
    if (!resp.ok) return null;
    const data = await resp.json();
    const sessions = data.sessions || [];
    if (sessions.length === 0) return null;
    // Server pre-sorts by last_activity desc; take [0].
    const latest = sessions[0];
    const then = Date.parse(latest.last_activity);
    if (isNaN(then)) return null;
    if (Date.now() - then > _RESUME_MAX_AGE_MS) return null;
    return latest;
  } catch {
    return null;
  }
}

function _resumeRelativeTime(iso) {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (isNaN(then)) return "";
  const diff = (Date.now() - then) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function _renderResumeCard(thread) {
  if (!thread) return "";
  const name = escapeHtml(thread.name || "Thread");
  const preview = escapeHtml(thread.preview || "");
  const rel = escapeHtml(_resumeRelativeTime(thread.last_activity));
  const sid = escapeHtml(thread.session_id || "");
  return `
    <button class="welcome-v2-resume" data-session-id="${sid}"
            onclick="_handleResumeClick(this)"
            title="Resume this thread">
      <span class="welcome-v2-resume-icon">\u25B6</span>
      <span class="welcome-v2-resume-body">
        <span class="welcome-v2-resume-title">${name}</span>
        ${preview ? `<span class="welcome-v2-resume-preview">${preview}</span>` : ""}
        <span class="welcome-v2-resume-meta">Last active ${rel}</span>
      </span>
    </button>`;
}

function _handleResumeClick(btn) {
  const sid = btn && btn.getAttribute("data-session-id");
  if (sid && typeof activateNicolasSession === "function") {
    activateNicolasSession(sid);
  }
}

// ---------------------------------------------------------------------------
// Fetch welcome state from backend
// ---------------------------------------------------------------------------

async function fetchWelcomeState() {
  if (!serverPort) return null;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/welcome/state`);
    if (resp.ok) {
      _welcomeState = await resp.json();
      _welcomeLastFetch = Date.now();
    }
  } catch (e) {
    // Server not ready — use onboarding fallback
    if (!_welcomeState) {
      _welcomeState = {
        state_id: "onboarding",
        greeting: "Welcome",
        strip: { type: "onboarding", label: "Welcome to your lab", annotation: "", steps: [] },
        narration_paragraphs: ["Welcome to Distillate. I'm Nicolas, the Alchemist of your lab."],
        suggestions: [],
        input_placeholder: "Tell me what you're researching\u2026",
      };
    }
  }
  return _welcomeState;
}

// ---------------------------------------------------------------------------
// Render welcome screen
// ---------------------------------------------------------------------------

async function _fetchAuthStatus() {
  if (!serverPort) return null;
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/auth/status`);
    if (r.ok) return await r.json();
  } catch {}
  return null;
}

async function _refreshHfAuthBar() {
  const bar = document.getElementById("hf-auth-bar");
  if (!bar) return;

  const authStatus = await _fetchAuthStatus();

  if (localStorage.getItem("distillate-hf-auth-bar-dismissed") === "1") {
    bar.classList.add("hidden");
    return;
  }
  if (!authStatus || authStatus.signed_in) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  const signinBtn = document.getElementById("hf-auth-bar-signin");
  if (signinBtn && !signinBtn._wired) {
    signinBtn._wired = true;
    signinBtn.addEventListener("click", async () => {
      if (!serverPort) return;
      try {
        const r = await fetch(`http://127.0.0.1:${serverPort}/auth/signin-hf-start`, { method: "POST" });
        const data = await r.json();
        if (data.ok && data.authorize_url && window.nicolas?.openExternal) {
          window.nicolas.openExternal(data.authorize_url);
        }
      } catch (err) { console.error("HF sign-in failed:", err); }
    });
  }
  const dismissBtn = document.getElementById("hf-auth-bar-dismiss");
  if (dismissBtn && !dismissBtn._wired) {
    dismissBtn._wired = true;
    dismissBtn.addEventListener("click", () => {
      localStorage.setItem("distillate-hf-auth-bar-dismissed", "1");
      bar.classList.add("hidden");
    });
  }
}

function _renderHfSignInCard() {
  return `
    <div class="strip-header" style="margin-bottom:10px;">
      <span class="strip-label">Connect your account</span>
    </div>
    <p style="font-size:12px;color:var(--text-dim);margin:0 0 12px;">
      Sync across devices · Run experiments on A100 GPUs · Back up your work
    </p>
    <button id="hf-signin-btn"
            style="display:flex;align-items:center;gap:8px;padding:8px 14px;border-radius:8px;
                   border:1px solid var(--border);background:var(--surface);
                   color:var(--text);font-size:13px;cursor:pointer;width:100%;margin-bottom:8px;">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0;">
        <path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2z" fill="#FFD21E"/>
        <path d="M8 11.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM16 11.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z" fill="#1a1a1a"/>
        <path d="M8.5 15.5c1 1 5 1 7 0" stroke="#1a1a1a" stroke-width="1.5" stroke-linecap="round"/>
      </svg>
      <span>Sign in with Hugging Face</span>
    </button>
    <div style="text-align:right;">
      <button id="hf-skip-btn"
              style="background:none;border:none;color:var(--text-dim);font-size:11px;cursor:pointer;padding:2px 0;">
        Skip for now
      </button>
    </div>`;
}

function _renderNeedsAttention() {
  if (typeof cachedProjects === "undefined") return "";
  const needsAttention = cachedProjects.filter(
    (p) => (p.alerts || []).some((a) => !a.dismissed)
  );
  if (!needsAttention.length) return "";
  const items = needsAttention.map((p) => {
    const alerts = (p.alerts || []).filter((a) => !a.dismissed);
    const title = typeof alertKindTitle === "function"
      ? alerts.map((a) => alertKindTitle(a.kind)).join(", ")
      : alerts.map((a) => a.kind).join(", ");
    return `
      <div class="welcome-attention-item" onclick="selectProject(${JSON.stringify(p.id)})">
        <span class="welcome-attention-icon">⚠</span>
        <span class="welcome-attention-name">${escapeHtml(p.name || p.id)}</span>
        <span class="welcome-attention-kind">${escapeHtml(title)}</span>
      </div>`;
  }).join("");
  return `
    <div class="welcome-needs-attention">
      <div class="welcome-attention-label">Needs attention</div>
      ${items}
    </div>`;
}

function _renderUserBadge(authStatus) {
  if (!authStatus || !authStatus.signed_in || !authStatus.user) return "";
  const user = authStatus.user;
  const name = escapeHtml(user.display_name || user.email || "");
  return name ? `<span class="welcome-v2-user" style="font-size:11px;color:var(--text-dim);margin-left:8px;">@${name}</span>` : "";
}

async function renderWelcomeScreen() {
  const container = document.getElementById("nicolas-welcome-block");
  if (!container) return;

  // Fetch auth status and welcome state in parallel on first render
  const needsFetch = !_welcomeState || (Date.now() - _welcomeLastFetch > _WELCOME_STALE_MS);
  const [, authStatus] = await Promise.all([
    needsFetch ? fetchWelcomeState() : Promise.resolve(),
    _fetchAuthStatus(),
  ]);

  if (!needsFetch && container.firstElementChild && !authStatus) {
    // Data is fresh and already rendered — skip to preserve scroll position.
    return;
  }

  if (!_welcomeState) return;
  const s = _welcomeState;

  const skipped = localStorage.getItem("distillate-hf-signin-skipped") === "1";
  const showSignInCard = authStatus && !authStatus.signed_in && !skipped;

  const latestThread = await _fetchLatestThread();

  container.innerHTML = `
    ${_renderNeedsAttention()}
    ${_renderResumeCard(latestThread)}
    <div class="welcome-v2 welcome-state-${escapeHtml(s.state_id)}">
      <!-- Zone 1: Persona -->
      <div class="welcome-v2-persona">
        <span class="welcome-v2-flask">\u2697\uFE0F</span>
        <div class="welcome-v2-persona-text">
          <span class="welcome-v2-name">Nicolas${_renderUserBadge(authStatus)}</span>
          <span class="welcome-v2-subtitle">The Alchemist of your Distillate lab.</span>
        </div>
      </div>
      <div class="welcome-v2-greeting">${escapeHtml(s.greeting)}</div>

      <!-- Zone 2: Sign-in card (unauthenticated) or Frontier Strip (authenticated/skipped) -->
      <div class="welcome-v2-strip" id="welcome-strip">
        ${showSignInCard ? _renderHfSignInCard() : renderStrip(s.strip)}
      </div>

      <!-- Zone 3: Narration -->
      <div class="welcome-v2-narration">
        ${(s.narration_paragraphs || []).map((p) =>
          `<p>${renderNarrationMarkdown(p)}</p>`
        ).join("")}
      </div>

      <!-- Zone 4: Suggestions -->
      <div class="welcome-v2-suggestions">
        ${(s.suggestions || []).map((sg) => `
          <button class="welcome-v2-suggestion"
                  data-prompt="${escapeHtml(sg.prompt)}"
                  onclick="handleWelcomeSuggestion(this)">
            <span class="welcome-v2-suggestion-label">${escapeHtml(sg.label)}</span>
            ${sg.specialist ? `<span class="welcome-v2-specialist">\u2697\uFE0F ${escapeHtml(sg.specialist)}</span>` : ""}
          </button>
        `).join("")}
      </div>

      <!-- Zone 5: Tips -->
      ${_renderTipsSection()}
    </div>`;

  // Wire sign-in button
  document.getElementById("hf-signin-btn")?.addEventListener("click", async () => {
    const btn = document.getElementById("hf-signin-btn");
    if (!serverPort || !btn) return;
    btn.disabled = true;
    btn.style.opacity = "0.6";
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/auth/signin-hf-start`, { method: "POST" });
      const data = await r.json();
      if (data.ok && data.authorize_url && window.nicolas?.openExternal) {
        window.nicolas.openExternal(data.authorize_url);
      }
    } catch (err) {
      console.error("Failed to start HF sign-in:", err);
    } finally {
      btn.disabled = false;
      btn.style.opacity = "";
    }
  });

  // Wire skip button
  document.getElementById("hf-skip-btn")?.addEventListener("click", () => {
    localStorage.setItem("distillate-hf-signin-skipped", "1");
    _welcomeState = null;
    renderWelcomeScreen();
  });

  if (!showSignInCard) loadStripChart();
}

// ---------------------------------------------------------------------------
// Strip renderer — dispatches by strip type
// ---------------------------------------------------------------------------

function renderStrip(strip) {
  if (!strip) return "";

  const label = escapeHtml(strip.label || "");
  const annotation = escapeHtml(strip.annotation || "");

  switch (strip.type) {
    case "frontier_chart":
      return `
        <div class="strip-header">
          <span class="strip-label">${label}</span>
          <span class="strip-annotation">${annotation}</span>
        </div>
        <div class="strip-chart" id="strip-chart-container"
             data-experiment-id="${escapeHtml(strip.experiment_id || "")}"></div>
        ${strip.secondary_link ? `
          <div class="strip-footer">
            <a class="strip-link" href="#" onclick="handleStripLink('${escapeHtml(strip.secondary_target || "")}')">${escapeHtml(strip.secondary_link)}</a>
          </div>` : ""}`;

    case "paper_queue":
      return `
        <div class="strip-header">
          <span class="strip-label">${label}</span>
          <span class="strip-annotation">${annotation}</span>
        </div>
        <div class="strip-papers">
          ${(strip.items || []).map((item) => `
            <div class="strip-paper-item">
              <span class="strip-paper-icon">\uD83D\uDCC4</span>
              <span class="strip-paper-title">${escapeHtml(item.title || "")}</span>
              ${item.unread ? `<span class="strip-paper-count">${item.unread} unread</span>` : ""}
            </div>
          `).join("")}
        </div>
        ${strip.secondary_link ? `
          <div class="strip-footer">
            <a class="strip-link" href="#" onclick="handleStripLink('${escapeHtml(strip.secondary_target || "")}')">${escapeHtml(strip.secondary_link)}</a>
          </div>` : ""}`;

    case "project_activity":
      return `
        <div class="strip-header">
          <span class="strip-label">${label}</span>
          <span class="strip-annotation">${annotation}</span>
        </div>
        ${strip.secondary_link ? `
          <div class="strip-footer">
            <a class="strip-link" href="#" onclick="handleStripLink('${escapeHtml(strip.secondary_target || "")}')">${escapeHtml(strip.secondary_link)}</a>
          </div>` : ""}`;

    case "week_in_review":
      return `
        <div class="strip-header">
          <span class="strip-label">${label}</span>
          <span class="strip-annotation">${annotation}</span>
        </div>
        ${strip.secondary_link ? `
          <div class="strip-footer">
            <a class="strip-link" href="#" onclick="handleStripLink('${escapeHtml(strip.secondary_target || "")}')">${escapeHtml(strip.secondary_link)}</a>
          </div>` : ""}`;

    case "onboarding":
      return `
        <div class="strip-onboarding">
          <div class="strip-onboarding-icon">\u2697\uFE0F</div>
          <div class="strip-onboarding-text">
            Distillate is your alchemy lab for AI research.<br>
            I'm Nicolas \u2014 I'll help you orchestrate everything.
          </div>
          ${(strip.steps || []).length ? `
            <div class="strip-onboarding-steps">
              ${strip.steps.map((step, i) => `
                <div class="strip-onboarding-step">${i + 1}. ${escapeHtml(step)}</div>
              `).join("")}
            </div>` : ""}
        </div>`;

    default:
      return `<div class="strip-header"><span class="strip-label">${label}</span></div>`;
  }
}

// ---------------------------------------------------------------------------
// Simple markdown for narration (bold only)
// ---------------------------------------------------------------------------

function renderNarrationMarkdown(text) {
  // Escape HTML first, then convert **bold** markers
  let escaped = escapeHtml(text);
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  return escaped;
}

// ---------------------------------------------------------------------------
// Interaction handlers
// ---------------------------------------------------------------------------

function handleWelcomeSuggestion(btn) {
  const prompt = btn.dataset.prompt;
  if (!prompt) return;
  // Seed the main-window Nicolas input and send.
  if (typeof inputEl !== "undefined" && inputEl && typeof sendMessage === "function") {
    inputEl.value = prompt;
    sendMessage();
  }
}

function handleStripLink(target) {
  if (!target) return;
  // Navigate to the appropriate sidebar view
  if (target === "/experiments" && typeof switchSidebarView === "function") {
    switchSidebarView("experiments");
  } else if (target === "/papers" && typeof switchSidebarView === "function") {
    switchSidebarView("papers");
  } else if (target.startsWith("/projects/") && typeof switchSidebarView === "function") {
    switchSidebarView("workspaces");
  } else if (target.startsWith("/experiments/")) {
    // Navigate to specific experiment
    const expId = target.split("/").pop();
    if (typeof switchSidebarView === "function") switchSidebarView("experiments");
    if (typeof selectExperiment === "function") selectExperiment(expId);
  }
}

// ---------------------------------------------------------------------------
// Refresh on window focus (if data >5 min stale)
// ---------------------------------------------------------------------------

window.addEventListener("focus", () => {
  if (_welcomeState && (Date.now() - _welcomeLastFetch > _WELCOME_STALE_MS)) {
    if (typeof _activeSidebarView !== "undefined" && _activeSidebarView === "nicolas") {
      renderWelcomeScreen();
    }
  }
});

// ---------------------------------------------------------------------------
// Focus welcome input (used by Cmd+K)
// ---------------------------------------------------------------------------

function focusWelcomeInput() {
  // Show the main-window Nicolas view and focus its persistent input.
  if (typeof showNicolasMain === "function") {
    showNicolasMain();
  }
  if (typeof inputEl !== "undefined" && inputEl) {
    inputEl.focus();
    return true;
  }
  return false;
}

// Load mini frontier chart into the strip after rendering
// (deferred because the chart container needs to be in the DOM first)
function loadStripChart() {
  if (!_welcomeState || !_welcomeState.strip) return;
  if (_welcomeState.strip.type !== "frontier_chart") return;

  const container = document.getElementById("strip-chart-container");
  if (!container) return;
  const experimentId = container.dataset.experimentId;
  if (!experimentId) return;

  // Fetch experiment data and render a mini chart
  if (typeof renderMiniFrontierChart === "function") {
    renderMiniFrontierChart(container, experimentId);
  }
}
