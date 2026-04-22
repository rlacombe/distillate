/* ───── Distillate Desktop — Core (shared state, connection, init) ───── */

let ws = null;
let serverPort = null;
let isStreaming = false;
let currentAssistantEl = null;
let currentText = "";
let turnHadMutation = false;
let lastUserMessage = "";
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 10;
let sseSource = null;
let hasExperiments = false;
let currentProjectId = null;
let _sessionTransition = null; // "stopping" | "launching" | null
let cachedPapers = [];
let cachedProjects = [];
let liveMetrics = {};  // Per-project live metric_update events: { projectId: [...] }
let sessionDoneBells = new Set();   // keys needing bell: "wsId/sessionId" or experiment "xp:projectId"
let nicolasWaiting = false;         // true when Nicolas finished a turn and user hasn't focused back
let terminalInitialized = false;
// Log/lin chart scale. null = auto (derived from metric direction: log for
// lower-is-better like loss, lin for higher-is-better like accuracy).
// Set to true/false once the user toggles — their choice then sticks
// until the experiment/metric changes.
let chartLogScale = null;
let chartLogScaleUserSet = false;
let currentTerminalProject = null;
let libraryConfigured = false;  // Set from /status — whether Zotero credentials are configured
let currentPaperKey = null;
let currentPaperFilter = "all";
let currentPaperSort = "newest";
let currentPaperSearch = "";
let experimentsFirstLoad = true;

const messagesEl = document.getElementById("messages");
const welcomeEl = document.getElementById("welcome");
const inputEl = document.getElementById("input");
const formEl = document.getElementById("input-form");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const sidebarLeft = document.getElementById("sidebar-left");
const sidebarRight = document.getElementById("sidebar-right");
const chatArea = document.getElementById("chat-area");
const welcomeStatsEl = document.getElementById("welcome-stats");

const NICOLAS_GREETINGS = [
  // Warm & curious
  "Hello! What concoction shall we explore today?",
  "Good to see you! Any hypotheses to test or papers to read?",
  "What's on your mind? I'm ready when you are.",
  // Scene-setting
  "The laboratory is warm and the flasks are clean. What shall we work on?",
  "The cauldron is bubbling. What experiment shall we conjure?",
  "The alembic is ready. What shall we distill?",
  // Returning user
  "Welcome back! I've been tending the library while you were away.",
  "Ah, picking up where we left off. Let's go.",
  // Brief & energetic
  "Fresh notebook, sharp pencil. Let's discover something.",
  "Ready to make progress.",
  // Playful
  "Ah, a fellow seeker! What knowledge shall we transmute today?",
  "I read three papers while you were gone. Just kidding. Mostly.",
  "Another day, another hypothesis to break.",
  // Reflective
  "The best experiments start with a good question.",
  "Science is patient. But we don't have to be.",
];

// Show the Nicolas main-window chat: hide any experiment/canvas/paper/etc.
// view in the center column, reveal #welcome (our Nicolas home), focus the
// persistent input. Safe to call from anywhere; idempotent.
function showNicolasMain() {
  // Hide experiment/notebook/vault detail panes in the control-panel-view
  ["experiment-detail", "notebook-detail", "vault-detail"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
  });
  // Hide session/results/prompt-editor views; show control-panel-view
  ["session-view", "results-view", "prompt-editor-view"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
  });
  const cp = document.getElementById("control-panel-view");
  if (cp) cp.classList.remove("hidden");
  // Un-hide the Nicolas home
  welcomeEl.classList.remove("hidden");
  // Hide editor tabs (experiment-specific chrome)
  const tabs = document.getElementById("editor-tabs");
  if (tabs) tabs.classList.add("hidden");
  // Clear selection — background polling checks currentProjectId and calls
  // renderProjectDetail() if it's set, which would hide welcomeEl and restore
  // the experiment-detail while the user is on the Nicolas home screen.
  currentProjectId = null;
  currentPaperKey = null;
  document.querySelectorAll("#experiments-sidebar .sidebar-item").forEach((el) => el.classList.remove("active"));
  // Focus input unless a terminal session is active
  const sv = document.getElementById("session-view");
  if (!sv || sv.classList.contains("hidden")) {
    if (inputEl) inputEl.focus();
  }
  if (typeof _refreshHfAuthBar === "function") _refreshHfAuthBar();
  // Refresh experiments data to ensure frontier chart has current run data
  if (typeof fetchExperimentsList === "function") {
    fetchExperimentsList();
  }
  // Refresh the welcome dashboard (including frontier chart) when showing the home screen
  if (typeof populateWelcomeDashboard === "function") populateWelcomeDashboard();
  // Redraw the frontier chart after a layout change (canvas dimensions may have changed)
  requestAnimationFrame(() => {
    if (typeof renderHomeFrontierChart === "function") {
      try { renderHomeFrontierChart(); } catch {}
    }
  });
}

let chatBannerInjected = false;

function injectChatBanner(_stats, isFirstUse) {
  if (chatBannerInjected || !messagesEl) return;
  chatBannerInjected = true;

  const message = isFirstUse
    ? "Welcome to your lab. I\u2019m Nicolas \u2014 I run experiments, read papers, and keep the notebook. Start by launching your first experiment above, or just tell me what you\u2019re working on."
    : NICOLAS_GREETINGS[Math.floor(Math.random() * NICOLAS_GREETINGS.length)];

  const bannerHtml = `<div class="chat-banner">
    <div class="banner-line">
      <span class="banner-dashes">&#x2500;&#x2500;&#x2500;</span>
      <span class="banner-flask">&#x2697;&#xFE0F;</span>
      <span class="banner-name">Nicolas</span>
      <span class="banner-dashes-tail"></span>
    </div>
  </div>
  <div class="message assistant">${message}</div>`;

  messagesEl.insertAdjacentHTML("afterbegin", bannerHtml);
}

let _fucBound = false;

function updateFirstUseWidget(hasExps, hasPaps) {
  const widget = document.getElementById("first-use-checklist");
  const frontier = document.getElementById("home-frontier");
  if (!widget) return;

  if (hasExps) {
    widget.classList.add("hidden");
    if (frontier) frontier.classList.remove("fuc-hidden");
    return;
  }

  widget.classList.remove("hidden");
  if (frontier) frontier.classList.add("fuc-hidden");

  const hasProject = (cachedProjects || []).filter(p => !p.is_default).length > 0;
  const dataDone = (hasPaps || libraryConfigured) || !!localStorage.getItem("distillate-integrations-visited");
  _setFucDone("fuc-experiment", hasExps);
  _setFucDone("fuc-data-sources", dataDone);
  _setFucDone("fuc-workspace", hasProject);

  _checkFucComplete(hasExps, dataDone, hasProject);

  if (_fucBound) return;
  _fucBound = true;

  document.getElementById("fuc-experiment-btn")?.addEventListener("click", () => {
    if (typeof launchDemoExperiment === "function") launchDemoExperiment();
  });

  document.getElementById("fuc-data-sources-btn")?.addEventListener("click", () => {
    localStorage.setItem("distillate-integrations-visited", "1");
    _setFucDone("fuc-data-sources", true);
    _checkFucComplete(hasExps, true, hasProject);
    if (typeof openSettings === "function") openSettings("integrations");
  });

  document.getElementById("fuc-workspace-btn")?.addEventListener("click", () => {
    if (inputEl && formEl) {
      inputEl.value = "I have an existing project directory I'd like to import.";
      formEl.dispatchEvent(new Event("submit", { bubbles: true }));
    }
  });
}

function _checkFucComplete(hasExps, dataDone, hasProject) {
  const allDone = hasExps && dataDone && hasProject;
  const completeEl = document.getElementById("fuc-complete");
  if (!allDone || !completeEl) return;
  completeEl.classList.remove("hidden");
  setTimeout(() => {
    const widget = document.getElementById("first-use-checklist");
    widget?.classList.add("hidden");
    const frontier = document.getElementById("home-frontier");
    if (frontier) frontier.classList.remove("fuc-hidden");
  }, 2000);
}

function _setFucDone(itemId, done) {
  document.getElementById(itemId)?.classList.toggle("fuc-done", !!done);
}

// Save chat before unload
window.addEventListener("beforeunload", () => {
  try {
    if (messagesEl && messagesEl.children.length > 0) {
      localStorage.setItem("distillate-chat", messagesEl.innerHTML);
    }
  } catch {}
});

/* ───── Theme toggle ───── */
{
  const _sunIcon = document.getElementById("theme-icon-sun");
  const _moonIcon = document.getElementById("theme-icon-moon");
  const _autoIcon = document.getElementById("theme-icon-auto");
  const _toggleBtn = document.getElementById("theme-toggle");
  const _themeSelect = document.getElementById("setting-theme");
  const _titles = { system: "Auto (follows system)", light: "Light", dark: "Dark" };
  let _currentSource = "system";

  // Show the icon that represents the current mode
  function _updateThemeIcon(source) {
    if (_sunIcon && _moonIcon && _autoIcon) {
      _sunIcon.classList.toggle("hidden", source !== "light");
      _moonIcon.classList.toggle("hidden", source !== "dark");
      _autoIcon.classList.toggle("hidden", source !== "system");
    }
    if (_toggleBtn) _toggleBtn.title = _titles[source] || "Toggle appearance";
  }

  // Init: read current theme from main process
  if (window.nicolas?.getTheme) {
    window.nicolas.getTheme().then(({ source }) => {
      _currentSource = source;
      _updateThemeIcon(source);
      if (_themeSelect) _themeSelect.value = source;
    });
  } else {
    _updateThemeIcon("system");
  }

  // Status bar toggle: cycles system → light → dark → system
  if (_toggleBtn && window.nicolas?.setTheme) {
    _toggleBtn.addEventListener("click", async () => {
      const next = _currentSource === "system" ? "light" : _currentSource === "light" ? "dark" : "system";
      _currentSource = next;
      const result = await window.nicolas.setTheme(next);
      _updateThemeIcon(next);
      if (_themeSelect) _themeSelect.value = next;
    });
  }

  // Settings dropdown
  if (_themeSelect && window.nicolas?.setTheme) {
    _themeSelect.addEventListener("change", async () => {
      _currentSource = _themeSelect.value;
      const result = await window.nicolas.setTheme(_themeSelect.value);
      _updateThemeIcon(_themeSelect.value);
    });
  }

  // Listen for theme changes (from system or other source)
  if (window.nicolas?.onThemeChange) {
    window.nicolas.onThemeChange((isDark) => {
      // Only update visuals if in system mode (manual overrides don't change on system events)
    });
  }
}

/* ───── Top bar wiring ───── */
{
  const _bindTopBar = () => {
    const search = document.getElementById("topbar-search");
    if (search && !search._bound) {
      search._bound = true;
      search.addEventListener("click", () => {
        if (typeof openResourceSearch === "function") openResourceSearch();
      });
    }
    const exportBtn = document.getElementById("home-frontier-export");
    if (exportBtn && !exportBtn._bound) {
      exportBtn._bound = true;
      exportBtn.addEventListener("click", async () => {
        const canvas = document.getElementById("home-frontier-canvas");
        if (!canvas || !_homeFrontierData) return;
        const { runs, metricName, title, logScale, summary } = _homeFrontierData;
        try {
          const dataUrl = await exportChartAsPng(canvas, runs, metricName, title, { logScale, summary });
          triggerChartDownload(dataUrl, title, metricName, logScale);
        } catch {}
      });
    }
    // Topbar theme toggle — delegates to the existing status-bar button so
    // all theme logic stays in one place (core.js:135 init block).
    const themeBtn = document.getElementById("topbar-theme");
    if (themeBtn && !themeBtn._bound) {
      themeBtn._bound = true;
      themeBtn.addEventListener("click", () => {
        const src = document.getElementById("theme-toggle");
        if (src) src.click();
      });
    }
    // Topbar bell — badge + popover fed by the same snapshot as the tray.
    const bellBtn = document.getElementById("topbar-bell");
    const bellBadge = document.getElementById("topbar-bell-badge");
    if (bellBtn && !bellBtn._bound) {
      bellBtn._bound = true;

      const fmtAgo = (ts) => {
        if (!ts) return "";
        const t = typeof ts === "number" ? ts : Date.parse(ts);
        if (!isFinite(t)) return "";
        const d = (Date.now() - t) / 1000;
        if (d < 60) return "just now";
        if (d < 3600) return `${Math.floor(d / 60)}m ago`;
        if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
        return `${Math.floor(d / 86400)}d ago`;
      };

      const updateBadge = (snap) => {
        if (!bellBadge) return;
        const n = snap?.counts?.waiting || 0;
        if (n > 0) { bellBadge.textContent = String(n); bellBadge.hidden = false; }
        else { bellBadge.hidden = true; }
      };
      updateBadge(window._bellSnapshot);
      window.addEventListener("distillate:bell-update", (e) => updateBadge(e.detail));

      // Listen for bell character detected in terminal (Claude Code / agent)
      // Triggers amber/waiting state transition
      if (window.nicolas?.onBellDetected) {
        window.nicolas.onBellDetected(() => {
          const cProjectId = window.currentProjectId || null;
          if (cProjectId) {
            sessionDoneBells.add("xp:" + cProjectId);
            // Update tray status to waiting (amber)
            window.nicolas?.updateTrayStatus("waiting", [], {
              counts: { waiting: sessionDoneBells.size, working: 0, idle: 0 },
              autoXps: []
            });
          }
        });
      }

      let pop = null;
      const closePop = () => {
        if (pop) { pop.remove(); pop = null; }
        document.removeEventListener("mousedown", onDocDown, true);
        document.removeEventListener("keydown", onEsc, true);
      };
      const onDocDown = (ev) => {
        if (!pop) return;
        if (pop.contains(ev.target) || bellBtn.contains(ev.target)) return;
        closePop();
      };
      const onEsc = (ev) => { if (ev.key === "Escape") closePop(); };

      const navigateToEntry = (entry) => {
        closePop();
        if (entry.kind === "nicolas") {
          if (typeof switchSidebarView === "function") switchSidebarView("nicolas");
          if (typeof showNicolasMain === "function") showNicolasMain();
        } else if (entry.kind === "session" && entry.workspaceId && entry.tmuxName) {
          if (typeof switchSidebarView === "function") switchSidebarView("workspaces");
          if (typeof attachToTerminalSession === "function") {
            try { attachToTerminalSession(entry.workspaceId, entry.tmuxName); } catch {}
          }
        } else if (entry.kind === "agent") {
          if (typeof switchSidebarView === "function") switchSidebarView("agents");
        }
      };

      const openPop = () => {
        if (pop) { closePop(); return; }
        const snap = window._bellSnapshot || { sessions: [], counts: { waiting: 0, working: 0, idle: 0 } };
        const waiting = (snap.sessions || []).filter((s) => s.status === "waiting");
        const working = (snap.sessions || []).filter((s) => s.status === "working");

        pop = document.createElement("div");
        pop.className = "bell-popover";
        pop.setAttribute("role", "dialog");
        pop.innerHTML = `
          <div class="bell-pop-head">
            <span class="bell-pop-title">Notifications</span>
            <span class="bell-pop-sub">${waiting.length} waiting \u00B7 ${working.length} working</span>
          </div>
          <div class="bell-pop-list"></div>
        `;
        const list = pop.querySelector(".bell-pop-list");

        const rows = waiting.concat(working);
        if (!rows.length) {
          list.innerHTML = `<div class="bell-pop-empty">You're all caught up.</div>`;
        } else {
          for (const entry of rows) {
            const row = document.createElement("button");
            row.type = "button";
            row.className = `bell-pop-item status-${entry.status}`;
            const isWaiting = entry.status === "waiting";
            row.innerHTML = `
              <span class="bell-pop-dot"></span>
              <span class="bell-pop-body">
                <span class="bell-pop-name">${escapeHtml(entry.name || entry.kind || "Session")}</span>
                <span class="bell-pop-meta">${isWaiting ? "Waiting for you" : "Working"}</span>
              </span>
            `;
            row.addEventListener("click", () => navigateToEntry(entry));
            list.appendChild(row);
          }
        }

        document.body.appendChild(pop);
        const r = bellBtn.getBoundingClientRect();
        const pr = pop.getBoundingClientRect();
        pop.style.top = `${Math.round(r.bottom + 6)}px`;
        pop.style.left = `${Math.round(Math.min(r.right - pr.width, window.innerWidth - pr.width - 8))}px`;
        setTimeout(() => {
          document.addEventListener("mousedown", onDocDown, true);
          document.addEventListener("keydown", onEsc, true);
        }, 0);
      };

      bellBtn.addEventListener("click", openPop);
    }
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _bindTopBar);
  } else {
    _bindTopBar();
  }
}

/* ───── Toast notifications ───── */

function showToast(message, type = "error") {
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("toast-visible"));
  setTimeout(() => {
    toast.classList.remove("toast-visible");
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

/* ───── Tool labels (hardcoded defaults, can be overridden by server) ───── */
let toolLabels = {
  // Paper library tools
  search_papers: "\uD83D\uDD0D Searching the library",
  get_paper_details: "\uD83D\uDCDC Unrolling the manuscript",
  get_reading_stats: "\uD83D\uDCCA Tallying the ledger",
  get_queue: "\u2697\uFE0F Inspecting the queue",
  get_recent_reads: "\uD83D\uDCDA Reviewing recent reads",
  suggest_next_reads: "\uD83D\uDD2E Consulting the oracle",
  synthesize_across_papers: "\u2728 Cross-referencing texts",
  run_sync: "\uD83D\uDD25 Firing up the furnace",
  reprocess_paper: "\uD83E\uDDEA Re-extracting the essence",
  promote_papers: "\u2B50 Promoting to the shelf",
  get_trending_papers: "\uD83D\uDCC8 Scanning the latest papers",
  add_paper_to_zotero: "\uD83D\uDCD6 Adding to the library",
  delete_paper: "\uD83D\uDDD1\uFE0F Removing from the library",
  refresh_metadata: "\uD83D\uDD04 Refreshing metadata",
  reading_report: "\uD83D\uDCCA Compiling reading report",
  // Experiment tools
  list_experiments: "\uD83E\uDDEA Surveying the laboratory",
  get_experiment_details: "\uD83D\uDD2C Examining the experiment",
  compare_runs: "\u2696\uFE0F Weighing the results",
  scan_project: "\uD83D\uDD0D Scanning for experiments",
  get_experiment_notebook: "\uD83D\uDCD3 Opening the lab notebook",
  add_project: "\uD83D\uDCC1 Adding project to the lab",
  rename_experiment: "\u270F\uFE0F Relabeling the project",
  rename_run: "\u270F\uFE0F Relabeling the run",
  delete_experiment: "\uD83D\uDDD1\uFE0F Removing from the lab",
  delete_run: "\uD83D\uDDD1\uFE0F Removing the run",
  update_project: "\uD83D\uDCDD Updating project details",
  link_paper: "\uD83D\uDD17 Linking paper to project",
  update_goals: "\uD83C\uDFAF Setting project goals",
  annotate_run: "\uD83D\uDCDD Adding note to run",
  init_experiment: "\u2697\uFE0F Drafting experiment prompt",
  continue_experiment: "\uD83D\uDD04 Continuing experiment",
  sweep_experiment: "\uD83E\uDDF9 Launching sweep",
  steer_experiment: "\uD83E\uDDE7 Steering the experiment",
  compare_experiments: "\u2696\uFE0F Comparing experiments",
  queue_sessions: "\uD83D\uDCCB Queuing sessions",
  list_templates: "\uD83D\uDCC4 Listing templates",
  save_template: "\uD83D\uDCBE Saving template",
  create_github_repo: "\uD83D\uDCE4 Creating GitHub repo",
  manage_session: "\uD83C\uDFAC Managing session",
  replicate_paper: "\uD83E\uDDEA Scaffolding from paper",
  suggest_from_literature: "\uD83D\uDCDA Mining the literature",
  extract_baselines: "\uD83D\uDCCF Extracting baselines",
  save_enrichment: "\uD83D\uDCA1 Saving research insights",
  // Claude Code built-in tools
  Read: "\uD83D\uDCC4 Reading file",
  Edit: "\u270F\uFE0F Editing file",
  Write: "\uD83D\uDCDD Writing file",
  Bash: "\uD83D\uDCBB Running command",
  Glob: "\uD83D\uDD0D Finding files",
  Grep: "\uD83D\uDD0D Searching code",
  WebSearch: "\uD83C\uDF10 Searching the web",
  WebFetch: "\uD83C\uDF10 Fetching page",
  Agent: "\uD83E\uDD16 Delegating to subagent",
  ToolSearch: "\u2697\uFE0F Preparing the apparatus",
  NotebookEdit: "\uD83D\uDCD3 Editing notebook",
  TodoWrite: "\u2611\uFE0F Updating tasks",
  TaskCreate: "\uD83D\uDCCB Creating task",
  TaskUpdate: "\uD83D\uDCCB Updating task",
};

/* ───── marked.js (configured in preload.js, exposed as window.markedParse) ───── */

/* ───── Sparkline SVG utility ───── */

function sparklineSvg(values, highlightIdx, opts = {}) {
  const w = opts.width || 60, h = opts.height || 16;
  const color = opts.color || "#6366f1";
  const highlightColor = opts.highlightColor || "#4ade80";
  if (!values.length) return "";
  // When yMin/yMax are supplied, the caller wants multiple sparklines on a
  // shared vertical scale (e.g. to compare experiments side-by-side). Falls
  // back to per-series auto-scale when not provided.
  const min = (typeof opts.yMin === "number") ? opts.yMin : Math.min(...values);
  const max = (typeof opts.yMax === "number") ? opts.yMax : Math.max(...values);
  const range = max - min || 1;
  const points = values.map((v, i) => {
    const x = values.length === 1 ? w / 2 : (i / (values.length - 1)) * w;
    const y = h - 2 - ((v - min) / range) * (h - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  let svg = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="vertical-align:middle;margin-left:6px">`;
  svg += `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.2" opacity="0.5"/>`;
  // Highlight dot for current run
  if (highlightIdx >= 0 && highlightIdx < values.length) {
    const x = values.length === 1 ? w / 2 : (highlightIdx / (values.length - 1)) * w;
    const y = h - 2 - ((values[highlightIdx] - min) / range) * (h - 4);
    svg += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2" fill="${highlightColor}"/>`;
  }
  svg += `</svg>`;
  return svg;
}

function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* ───── Connection ───── */

function connect(port) {
  serverPort = port;
  ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);

  ws.onopen = () => {
    const wasReconnecting = reconnectAttempts > 0;
    reconnectAttempts = 0;

    statusDot.className = "dot connected";

    statusText.textContent = wasReconnecting ? "Reconnected" : "Connected";
    inputEl.disabled = false;
    sendBtn.disabled = false;
    // Only auto-focus chat if the terminal isn't active
    const sessionView = document.getElementById("session-view");
    if (!sessionView || sessionView.classList.contains("hidden")) {
      inputEl.focus();
    }

    // Briefly show "Reconnected" then clear
    if (wasReconnecting) {
      setTimeout(() => {
        if (statusText.textContent === "Reconnected") {
          statusText.textContent = "Connected";
        }
      }, 2000);
    }

    // Fetch stats, tool labels, experiments, papers, and workspaces
    fetchWelcomeStats();
    if (typeof fetchWorkspaces === "function") fetchWorkspaces();
    if (typeof fetchNicolasSessions === "function") fetchNicolasSessions();
    // Render the B+ welcome block into #nicolas-welcome-block (above #messages).
    if (typeof renderWelcomeScreen === "function") renderWelcomeScreen();

    // Pull latest state from cloud on connect
    triggerCloudSync();

    // Mount the billing pills (model picker + live cost).
    if (typeof mountBilling === "function") mountBilling(ws);

    // Mount account button (avatar / sign-in state).
    if (typeof mountAccount === "function") mountAccount();

  };

  ws.onclose = () => {
    statusDot.className = "dot disconnected";

    inputEl.disabled = true;
    sendBtn.disabled = true;

    reconnectAttempts++;

    if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
      statusText.textContent = "Connection lost. Please restart the app.";
      return;
    }

    const delay = Math.min(Math.pow(2, reconnectAttempts) * 1000, 30000);
    statusText.textContent = `Reconnecting\u2026 (attempt ${reconnectAttempts})`;
    setTimeout(() => connect(port), delay);
  };

  ws.onerror = () => {
    statusDot.className = "dot disconnected";

    statusText.textContent = "Connection error \u2014 check that the server is running";
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleEvent(data);
    } catch (err) {
      console.error("Failed to parse WebSocket message:", err);
      addErrorMessage("Received malformed data from server.");
    }
  };
}

/* ───── Session history replay ───── */

// Clear #messages and re-render past turns from the backend history endpoint.
// Used by nicolas-sessions.js when the user clicks a past session in the
// sidebar. Replay is plain text — tool calls are omitted for v1.
async function loadSessionHistory(sessionId) {
  if (!sessionId || !serverPort) return;
  currentAssistantEl = null;
  currentText = "";
  isStreaming = false;
  if (typeof setStreamingUI === "function") setStreamingUI(false);
  // Switch the chat container into "has-thread-history" mode BEFORE the
  // fetch so the welcome splash/dashboard/welcome-block disappear up front.
  // Otherwise the user sees the splash at the top of #chat-area while the
  // history request is in flight and then scrollTop snaps to the bottom
  // once the fragment is appended — reading as "restart at top, scroll
  // down" on every thread switch.
  if (welcomeEl) welcomeEl.classList.add("has-thread-history");
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions/${sessionId}/history`);
    if (!resp.ok) return;
    const data = await resp.json();
    const turns = data.turns || [];

    // Build every turn into a DocumentFragment and swap it in in a single
    // synchronous operation. Appending one-at-a-time with a scrollToBottom
    // after each user message causes the viewport to visibly jump from the
    // top of the (cleared) chat down through each new line — the "replays
    // the history" behavior users see when re-opening a thread.
    const fragment = document.createDocumentFragment();
    for (const turn of turns) {
      if (turn.role === "user") {
        const el = document.createElement("div");
        el.className = "message user";
        el.textContent = turn.text;
        fragment.appendChild(el);
      } else if (turn.role === "assistant") {
        const el = document.createElement("div");
        el.className = "message assistant markdown-body";
        if (typeof window.markedParse === "function") {
          el.innerHTML = window.markedParse(turn.text).replace(
            /\[(\d{1,4})\]/g,
            '<a href="#" class="paper-ref" data-index="$1">[$1]</a>'
          );
        } else {
          el.textContent = turn.text;
        }
        fragment.appendChild(el);
      } else if (turn.role === "tool") {
        // Replay tool calls as "done" indicators inline with text turns.
        // Reuses the same DOM builder the live chat uses so styling and
        // subtitle-derivation stay in sync.
        if (typeof buildToolIndicatorEl === "function") {
          const el = buildToolIndicatorEl(turn.name, true, turn.input || {});
          if (turn.is_error) el.classList.add("cancelled");
          fragment.appendChild(el);
        }
      }
    }
    messagesEl.innerHTML = "";
    messagesEl.appendChild(fragment);

    // If the session really is empty, restore the welcome surface. Only
    // swallow the welcome when there's actual conversation to show.
    if (welcomeEl && turns.length === 0) {
      welcomeEl.classList.remove("has-thread-history");
    }

    // Pin the viewport to the bottom after layout settles. Two RAFs: the
    // first lets the new DOM lay out, the second lets the fadeIn animation
    // on .message start without shifting the final scrollHeight.
    if (chatArea) {
      const pinBottom = () => { chatArea.scrollTop = chatArea.scrollHeight; };
      pinBottom();
      requestAnimationFrame(() => { pinBottom(); requestAnimationFrame(pinBottom); });
    }
  } catch (err) {
    console.error("Failed to load session history:", err);
  }
}

/* ───── New conversation ───── */

function clearConversation() {
  // Reset UI
  messagesEl.innerHTML = "";
  welcomeEl.classList.remove("hidden");
  // Brand-new conversation → restore the welcome splash/dashboard.
  welcomeEl.classList.remove("has-thread-history");
  currentAssistantEl = null;
  currentText = "";
  isStreaming = false;
  if (typeof setStreamingUI === "function") setStreamingUI(false);
  turnHadMutation = false;
  lastUserMessage = "";
  inputEl.disabled = false;
  sendBtn.disabled = false;
  inputEl.value = "";
  inputEl.style.height = "auto";
  const sv = document.getElementById("session-view");
  if (!sv || sv.classList.contains("hidden")) inputEl.focus();

  // Refresh stats (sidebars already loaded, just refresh welcome screen)
  _fetchStatusOnly();

  // Tell server to start fresh
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "new_conversation" }));
  }
}

/* ───── Welcome stats + initial data load ───── */

function showNicolasLocked() {
  chatBannerInjected = true;
  if (messagesEl && !messagesEl.querySelector(".nicolas-locked-narration")) {
    messagesEl.insertAdjacentHTML("afterbegin", `<div class="chat-banner">
      <div class="banner-line">
        <span class="banner-dashes">&#x2500;&#x2500;&#x2500;</span>
        <span class="banner-flask">&#x2697;&#xFE0F;</span>
        <span class="banner-name">Nicolas</span>
        <span class="banner-dashes-tail"></span>
      </div>
    </div>
    <div class="message assistant nicolas-locked-narration">
      Welcome to Distillate. I&#8217;m Nicolas, the Alchemist of your lab.<br><br>
      I run experiments, summarize papers, keep notes, and orchestrate a few specialists who help me out.<br><br>
      Connect Claude below to start a conversation.
    </div>`);
  }
  const panel = document.getElementById("nicolas-connect-panel");
  if (panel) panel.classList.remove("hidden");
  if (inputEl) {
    inputEl.disabled = true;
    inputEl.placeholder = "Connect Claude to activate Nicolas\u2026";
  }
  if (sendBtn) sendBtn.disabled = true;
  _bindConnectPanel();
}

function _bindConnectPanel() {
  const installBtn = document.getElementById("nicolas-install-link");
  if (installBtn && !installBtn._bound) {
    installBtn._bound = true;
    installBtn.addEventListener("click", async () => {
      if (window.nicolas?.openExternal) window.nicolas.openExternal("https://claude.ai/code");
      // After opening the installer, offer a re-check button
      const hint = installBtn.closest(".connect-primary")?.querySelector(".connect-primary-hint");
      if (hint && !hint.querySelector(".connect-recheck")) {
        hint.insertAdjacentHTML("beforeend",
          ' &mdash; <button type="button" class="connect-recheck" id="nicolas-recheck-btn">I\'ve installed it</button>');
        document.getElementById("nicolas-recheck-btn")?.addEventListener("click", async () => {
          const statusResp = await fetch(`http://127.0.0.1:${serverPort}/status`).catch(() => null);
          if (!statusResp) return;
          const status = await statusResp.json();
          if (status.nicolas_live) {
            _unlockNicolas();
          } else {
            showToast("Claude Code not detected yet \u2014 try restarting Distillate", "error");
          }
        });
      }
    });
  }

  const apiBtn = document.getElementById("nicolas-connect-btn");
  if (!apiBtn || apiBtn._bound) return;
  apiBtn._bound = true;

  const keyInput = document.getElementById("nicolas-api-key-input");
  if (keyInput) keyInput.addEventListener("keydown", (e) => { if (e.key === "Enter") apiBtn.click(); });

  apiBtn.addEventListener("click", async () => {
    const key = document.getElementById("nicolas-api-key-input")?.value?.trim();
    if (!key) return;
    apiBtn.disabled = true;
    apiBtn.textContent = "Connecting\u2026";
    try {
      const resp = await fetch(`http://127.0.0.1:${serverPort}/settings/env`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: "ANTHROPIC_API_KEY", value: key }),
      });
      const result = await resp.json();
      if (result.ok) {
        const statusResp = await fetch(`http://127.0.0.1:${serverPort}/status`);
        const status = await statusResp.json();
        if (status.nicolas_live) {
          _unlockNicolas();
        } else {
          apiBtn.textContent = "Not activated \u2014 check key";
          apiBtn.disabled = false;
        }
      } else {
        apiBtn.textContent = result.reason || "Failed \u2014 retry";
        apiBtn.disabled = false;
      }
    } catch {
      apiBtn.textContent = "Error \u2014 retry";
      apiBtn.disabled = false;
    }
  });
}

function _unlockNicolas() {
  const panel = document.getElementById("nicolas-connect-panel");
  if (panel) panel.classList.add("hidden");
  if (inputEl) {
    inputEl.disabled = false;
    inputEl.placeholder = "Ask Nicolas, or type / for commands\u2026";
  }
  if (sendBtn) sendBtn.disabled = false;
  if (messagesEl) {
    const lockedMsg = messagesEl.querySelector(".nicolas-locked-narration");
    if (lockedMsg) {
      const banner = lockedMsg.previousElementSibling;
      if (banner?.classList.contains("chat-banner")) banner.remove();
      lockedMsg.remove();
    }
  }
  chatBannerInjected = false;
  injectChatBanner({}, true);
  updateFirstUseWidget(false, false);
  updateSuggestions(true, false, false);
  inputEl?.focus();
}

function _applyStatusData(data) {
  if (data.nicolas_live === false) {
    showNicolasLocked();
    if (data.tool_labels && typeof data.tool_labels === "object") {
      toolLabels = { ...toolLabels, ...data.tool_labels };
    }
    return;
  }

  const hasPapers = (data.papers_read || 0) > 0;
  const hasExperiments = data.experiments && data.experiments.total_projects > 0;
  const isFirstUse = !hasPapers && !hasExperiments;
  libraryConfigured = !!data.library_configured;

  // Welcome screen stats — papers line
  if (welcomeStatsEl) {
    const parts = [];
    if (data.papers_read != null) parts.push(`${data.papers_read} papers read`);
    if (data.papers_queued != null) parts.push(`${data.papers_queued} in queue`);
    if (parts.length) {
      welcomeStatsEl.textContent = parts.join(" \u00B7 ");
    }
  }
  // Welcome screen stats — experiments line
  const expStatsEl = document.getElementById("welcome-stats-experiments");
  if (expStatsEl && data.experiments) {
    const exp = data.experiments;
    const expParts = [];
    if (exp.total_projects > 0) expParts.push(`${exp.total_projects} experiments`);
    if (exp.total_runs > 0) expParts.push(`${exp.total_runs} runs`);
    if (expParts.length) {
      expStatsEl.textContent = expParts.join(" \u00B7 ");
    }
  }

  // Inject chat banner — first-use gets a specific welcome, returning users get a random greeting
  const exp = data.experiments || {};
  injectChatBanner({
    experiments: exp.total_projects || 0,
    runs: exp.total_runs || 0,
    running: exp.active_sessions || 0,
    papers: data.papers_read || 0,
    queue: data.papers_queued || 0,
  }, isFirstUse);

  // Setup checklist (first-use) or frontier chart (returning user)
  updateFirstUseWidget(hasExperiments, hasPapers);

  // Context-aware suggestion buttons
  updateSuggestions(isFirstUse, hasPapers, hasExperiments);

  // Onboarding CTA on welcome screen for first-use
  const onboarded = localStorage.getItem("distillate-onboarded");
  const guidanceEl = document.querySelector(".welcome-guidance");
  const tipEl = document.querySelector(".welcome-tip");
  if (isFirstUse && !onboarded) {
    if (guidanceEl) guidanceEl.innerHTML = '<button class="onboarding-btn onboarding-btn-large" id="welcome-demo-btn">Launch your first experiment</button>';
    if (tipEl) tipEl.textContent = "A tiny transformer will learn matrix multiplication while you watch.";
    document.getElementById("welcome-demo-btn")
      ?.addEventListener("click", launchDemoExperiment);
  }

  // Merge server-provided tool labels
  if (data.tool_labels && typeof data.tool_labels === "object") {
    toolLabels = { ...toolLabels, ...data.tool_labels };
  }
}

function _applyExperimentsData(data) {
  experimentsFirstLoad = false;
  if (!data.ok) return;
  const projects = data.experiments || data.projects || [];
  // Evict liveMetrics for projects that no longer exist. Each entry caps at
  // 1000 events (~MBs) and the map was never pruned — deleted or renamed
  // projects left their streams pinned forever.
  if (typeof liveMetrics === "object" && liveMetrics) {
    const keep = new Set(projects.map((p) => p.id));
    for (const pid of Object.keys(liveMetrics)) {
      if (!keep.has(pid)) delete liveMetrics[pid];
    }
  }
  renderProjectsList(projects);
  // Only re-render the detail view if the control-panel tab is actually
  // visible. renderProjectDetail wipes #experiment-detail innerHTML and
  // (if prompt-editor/results is the active tab) refetches + re-renders
  // PROMPT.md, which resets scroll position and flashes "Loading…". The
  // run_update SSE handler in experiments.js already uses this same guard;
  // _applyExperimentsData (called from the 15 s poll + session_end /
  // session_completed / goal_reached / session_continued SSE events) was
  // missing it — on a fast-firing experiment that's a refresh every few
  // seconds while you're reading the prompt.
  if (currentProjectId) {
    const onPapers = typeof _activeSidebarView !== "undefined" && _activeSidebarView === "papers";
    const cpVisible = !document.getElementById("control-panel-view")?.classList.contains("hidden");
    if (!onPapers && cpVisible) renderProjectDetail(currentProjectId);
  }
  _sessionTransition = null;
  if (projects.some((p) => p.active_sessions > 0)) startExperimentSSE();
  if (typeof populateWelcomeDashboard === "function") populateWelcomeDashboard();
  updateFirstUseWidget(projects.length > 0, (window._cachedPapersData || []).length > 0);
  // Cache for instant render on next launch
  try { localStorage.setItem("distillate-experiments-cache", JSON.stringify(projects)); } catch {}
}

function _applyPapersData(data) {
  papersFirstLoad = false;
  if (!data.ok) return;
  window._cachedPapersData = data.papers || [];
  renderPapersList(window._cachedPapersData);
  if (typeof populateWelcomeDashboard === "function") populateWelcomeDashboard();
  // Cache (trim to first 200 papers to keep localStorage payload small)
  try {
    const trimmed = window._cachedPapersData.slice(0, 200);
    localStorage.setItem("distillate-papers-cache", JSON.stringify(trimmed));
  } catch {}
}

function _applyConnectorsData(data) {
  if (data.library) {
    cachedConnectors = data.library || [];
  } else if (data.connectors) {
    cachedConnectors = data.connectors || [];
  }
  _cacheObsidianContextFromConnectors(cachedConnectors);
  renderConnectors(cachedConnectors);
}

function _applyIntegrationsData(data) {
  cachedConnectors = data.library || [];
  cachedAgents = data.agents || [];
  cachedCompute = data.compute || [];
  _cacheObsidianContextFromConnectors(cachedConnectors);
  renderIntegrations(data);
}

// ─── Obsidian integration helpers ───
// Vault context cached from /integrations so every view can build obsidian://
// URIs without a round-trip. Stays null when no vault is configured — callers
// must always check before rendering any "Open in Obsidian" affordance.
let _obsidianCtx = null;

function _cacheObsidianContextFromConnectors(connectors) {
  const ob = (connectors || []).find((c) => c && c.id === "obsidian");
  _obsidianCtx = (ob && ob.connected && ob.vault_name)
    ? { vault_name: ob.vault_name, papers_folder: ob.papers_folder || "Distillate" }
    : null;
}

function obsidianConfigured() {
  return !!_obsidianCtx;
}

function _openInObsidianPath(relPath) {
  if (!_obsidianCtx || !relPath) return false;
  const vault = encodeURIComponent(_obsidianCtx.vault_name);
  // Encode each path segment but preserve slashes — Obsidian expects literal /
  const file = relPath.split("/").map(encodeURIComponent).join("/");
  const uri = `obsidian://open?vault=${vault}&file=${file}`;
  if (window.nicolas?.openExternal) {
    window.nicolas.openExternal(uri);
    return true;
  }
  // Browser fallback — fire the obsidian:// URI via a hidden anchor
  const a = document.createElement("a");
  a.href = uri;
  a.click();
  return true;
}

/**
 * Open a file in Obsidian by its path relative to the papers folder.
 * Used by the Wiki view where paths come from /vault/tree (e.g., "schema.md").
 */
function openObsidianWikiFile(wikiRelPath) {
  if (!_obsidianCtx || !wikiRelPath) return false;
  const clean = wikiRelPath.replace(/\.md$/, "");
  return _openInObsidianPath(`${_obsidianCtx.papers_folder}/${clean}`);
}

function openObsidianPaper(citekey) {
  if (!_obsidianCtx || !citekey) return false;
  return _openInObsidianPath(`${_obsidianCtx.papers_folder}/Papers/Notes/${citekey}`);
}

function openObsidianExperiment(projectId) {
  if (!_obsidianCtx || !projectId) return false;
  return _openInObsidianPath(`${_obsidianCtx.papers_folder}/Experiments/${projectId}`);
}

function openObsidianNotebook(date) {
  if (!_obsidianCtx || !date) return false;
  return _openInObsidianPath(`${_obsidianCtx.papers_folder}/Lab Notebook/${date}`);
}

function openObsidianVaultRoot() {
  if (!_obsidianCtx) return false;
  return _openInObsidianPath(`${_obsidianCtx.papers_folder}/`);
}

// Returns HTML for a subtle "Open in Obsidian" button, or empty string if no
// vault is configured. kind = "paper" | "experiment" | "notebook" | "vault".
function obsidianButtonHtml(kind, id) {
  if (!_obsidianCtx) return "";
  const handlers = {
    paper:      `openObsidianPaper('${(id || "").replace(/'/g, "\\'")}')`,
    experiment: `openObsidianExperiment('${(id || "").replace(/'/g, "\\'")}')`,
    notebook:   `openObsidianNotebook('${(id || "").replace(/'/g, "\\'")}')`,
    vault:      `openObsidianVaultRoot()`,
  };
  const onclick = handlers[kind];
  if (!onclick) return "";
  return `<button class="obsidian-btn" onclick="${onclick}" title="Open in Obsidian"
    ><img src="/ui/icons/obsidian.svg" alt="" width="14" height="14"
    ><span>Open in Obsidian</span></button>`;
}

// ── Welcome overview populator (v6 strip) ──
// Fills the 4 cells on Nicolas home: Frontier · loss / Experiments / Papers / Projects.
function populateWelcomeDashboard() {
  const byId = (id) => document.getElementById(id);
  const setText = (id, v) => { const el = byId(id); if (el) el.textContent = v == null ? "" : String(v); };

  // ── Projects cell was replaced by Compute cell (v6 redesign). Projects
  // now only appear in the sidebar rail; the count badge lives on that
  // sidebar header. We still capture the numbers for the snapshot cache.
  const wsArr = (typeof _workspaces !== "undefined" ? _workspaces : []);
  const projectsTotal = wsArr.length;
  const activeProjects = wsArr.filter((ws) => (ws.active_sessions || 0) > 0).length;

  // ── Compute cell ── fetch live usage so the cell is populated on every home visit
  if (typeof serverPort !== "undefined" && serverPort) {
    fetch(`http://127.0.0.1:${serverPort}/usage`)
      .then((r) => r.ok ? r.json() : null)
      .then((snap) => { if (snap && typeof applyUsageSnapshot === "function") applyUsageSnapshot(snap); })
      .catch(() => {});
  }

  // ── Experiments cell ──
  const exps = (typeof cachedProjects !== "undefined" ? cachedProjects : []);
  const expsActive = exps.filter((p) => (p.active_sessions || 0) > 0).length;
  const totalRuns = exps.reduce((sum, p) => sum + (p.total_runs || p.run_count || 0), 0);
  setText("dash-experiments-count", exps.length || 0);
  const expRunsEl = byId("ov-exp-runs");
  if (expRunsEl) expRunsEl.innerHTML = totalRuns > 0 ? `<span style="color:var(--warm)">${totalRuns} run${totalRuns !== 1 ? "s" : ""}</span>` : "";
  // Live-dot on label when something is running
  const liveDot = byId("ov-exp-livedot");
  if (liveDot) liveDot.classList.toggle("on", expsActive > 0);
  // Segmented bar + sub: live / queued / paused proportions. "Queued" is
  // every experiment that isn't actively running and isn't paused — idle
  // by default. Render the bar whenever there's at least one experiment
  // so the strip stays visually anchored.
  const pausedCount = exps.filter((p) => p.status === "paused").length;
  const queuedCount = Math.max(0, exps.length - expsActive - pausedCount);
  const segbar = byId("ov-exp-segbar");
  if (segbar) {
    const total = Math.max(1, exps.length);
    const parts = [
      { w: (expsActive / total) * 100, bg: "var(--green)" },
      { w: (queuedCount / total) * 100, bg: "var(--accent)" },
      { w: (pausedCount / total) * 100, bg: "var(--warm)" },
    ].filter((p) => p.w > 0);
    segbar.innerHTML = exps.length > 0
      ? parts.map((p) => `<span style="width:${p.w}%; background:${p.bg};"></span>`).join("")
      : "";
  }
  const expSub = byId("ov-exp-sub");
  if (expSub) {
    const pieces = [];
    if (expsActive > 0) pieces.push(`<span style="color:var(--green)">● ${expsActive} live</span>`);
    if (queuedCount > 0) pieces.push(`<span style="color:var(--accent)">● ${queuedCount} on stand-by</span>`);
    if (pausedCount > 0) pieces.push(`<span style="color:var(--warm)">● ${pausedCount} paused</span>`);
    expSub.innerHTML = pieces.join(" · ");
  }

  // ── Live runs sidebar section ──
  // Mirrors the "LIVE RUNS" block in the mockup. We only render experiments
  // that have an active session AND at least one run. When none are running
  // the whole section stays hidden — no empty-state noise.
  const liveRunsSec = byId("live-runs-section");
  const liveRunsList = byId("live-runs-list");
  const liveRunsCount = byId("live-runs-count");
  if (liveRunsSec && liveRunsList) {
    const liveExps = exps.filter((p) => (p.active_sessions || 0) > 0 && (p.runs || []).length > 0);
    if (liveExps.length === 0) {
      liveRunsSec.classList.add("hidden");
      liveRunsList.innerHTML = "";
    } else {
      liveRunsSec.classList.remove("hidden");
      if (liveRunsCount) liveRunsCount.textContent = String(liveExps.length);
      liveRunsList.innerHTML = liveExps.map((p) => {
        const runs = p.runs || [];
        const lastRun = runs[runs.length - 1] || {};
        const runSeq = String(runs.length).padStart(4, "0");
        const metric = p.key_metric_name || p.preferred_metric || p.objective_metric || null;
        const bestVal = _bestMetricValue(p, metric);
        const bestStr = (metric && bestVal != null) ? `${metric} ${_formatMetricValue(metric, bestVal)}` : "";
        const nameHtml = (typeof escapeHtml === "function" ? escapeHtml : String)(p.name || p.id);
        const metaBits = ['<span class="lr-dot"></span><span class="lr-state">running</span>'];
        if (bestStr) metaBits.push(`<span>${(typeof escapeHtml === "function" ? escapeHtml : String)(bestStr)}</span>`);
        return `
          <div class="live-run-item" data-exp-id="${(typeof escapeHtml === "function" ? escapeHtml : String)(p.id)}">
            <div class="live-run-name">${nameHtml} &middot; run ${runSeq}</div>
            <div class="live-run-meta">${metaBits.join(' <span class="live-run-sep">&middot;</span> ')}</div>
          </div>`;
      }).join("");
      liveRunsList.querySelectorAll(".live-run-item").forEach((item) => {
        item.addEventListener("click", () => {
          const id = item.dataset.expId;
          if (id && typeof selectHomeFrontier === "function") selectHomeFrontier(id);
        });
      });
    }
  }

  // ── Status bar + top bar live counters ──
  const statusLive = byId("status-live");
  const statusLiveCount = byId("status-live-count");
  if (statusLive && statusLiveCount) {
    if (expsActive > 0) {
      statusLiveCount.textContent = String(expsActive);
      statusLive.hidden = false;
    } else {
      statusLive.hidden = true;
    }
  }
  const topbarLive = byId("topbar-live-pill");
  const topbarLiveCount = byId("topbar-live-count");
  if (topbarLive && topbarLiveCount) {
    if (expsActive > 0) {
      topbarLiveCount.textContent = String(expsActive);
      topbarLive.hidden = false;
    } else {
      topbarLive.hidden = true;
    }
  }

  // Runs-today counter (mockup #8). Counts runs whose started_at falls
  // on the local calendar day — resets at midnight, no backend change.
  const runsToday = byId("status-runs-today");
  const runsTodayCount = byId("status-runs-today-count");
  if (runsToday && runsTodayCount) {
    const now = new Date();
    const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    let n = 0;
    for (const p of exps) {
      for (const r of p.runs || []) {
        if (!r.started_at) continue;
        const t = new Date(r.started_at).getTime();
        if (isFinite(t) && t >= dayStart) n++;
      }
    }
    if (n > 0) {
      runsTodayCount.textContent = String(n);
      runsToday.hidden = false;
    } else {
      runsToday.hidden = true;
    }
  }

  // ── Papers cell ──
  const papers = (typeof window._cachedPapersData !== "undefined" ? window._cachedPapersData : []) || [];
  // Papers never had a .read flag — status === "processed" is the
  // authoritative read/unread split (mirrors papers.js).
  const read = papers.filter((p) => p.status === "processed").length;
  const queue = papers.length - read;
  setText("dash-papers-count", papers.length || 0);
  const unreadEl = byId("ov-papers-unread");
  if (unreadEl) unreadEl.innerHTML = queue > 0 ? `<span style="color:var(--warm)">${queue} unread</span>` : (read > 0 ? `${read} read` : "");
  // "Last sync Xh ago" — derived from max(processed_at, uploaded_at)
  // across papers. Mirrors _renderPapersSyncFooter in papers.js.
  let lastSyncMs = 0;
  for (const p of papers) {
    for (const k of ["processed_at", "uploaded_at"]) {
      const v = p[k];
      if (!v) continue;
      const t = Date.parse(v);
      if (isFinite(t) && t > lastSyncMs) lastSyncMs = t;
    }
  }
  let papersSub = "";
  if (lastSyncMs) {
    const d = (Date.now() - lastSyncMs) / 1000;
    let rel;
    if (d < 60) rel = "just now";
    else if (d < 3600) rel = `${Math.floor(d / 60)}m ago`;
    else if (d < 86400) rel = `${Math.floor(d / 3600)}h ago`;
    else rel = `${Math.floor(d / 86400)}d ago`;
    papersSub = `Last sync ${rel}`;
  }
  setText("ov-papers-sub", papersSub);

  // ── Current experiment cell ── filled by renderHomeFrontierChart(), which
  // picks the featured experiment (user-selected or default) and also updates
  // the breadcrumb + picker label in the same pass.

  // Save snapshot for next launch (instant render before fresh fetch)
  try {
    localStorage.setItem("distillate-dashboard-cache-v2", JSON.stringify({
      projects: projectsTotal, codingSessions: activeProjects,
      experiments: exps.length, experimentsActive: expsActive, runs: totalRuns,
      papers: papers.length, papersRead: read, papersQueue: queue,
      ts: Date.now(),
    }));
  } catch {}

  // Trigger hero chart render if data is loaded
  if (typeof renderHomeFrontierChart === "function") {
    try { renderHomeFrontierChart(); } catch {}
  }
}

// ── Home frontier: featured experiment selection ──
// The user can pin any experiment as the home-page hero via the picker. If
// nothing is pinned (or the pinned one disappears), we auto-select a sensible
// default: any currently-running experiment, else the most recently active.
let _homeFrontierId = null;
let _homeFrontierData = null;
try { _homeFrontierId = localStorage.getItem("distillate-home-frontier") || null; } catch {}

function _lastActivityMs(p) {
  let max = 0;
  for (const r of p.runs || []) {
    if (r.started_at) {
      const t = new Date(r.started_at).getTime();
      if (isFinite(t) && t > max) max = t;
    }
  }
  return max;
}

function pickDefaultFrontierExp(exps) {
  const running = (exps || []).filter((p) => (p.active_sessions || 0) > 0 && (p.runs || []).length > 0);
  if (running.length > 0) {
    running.sort((a, b) => _lastActivityMs(b) - _lastActivityMs(a));
    return running[0];
  }
  let best = null, bestScore = -1;
  for (const p of exps || []) {
    const n = (p.runs || []).length;
    if (n === 0) continue;
    const score = _lastActivityMs(p) || n;
    if (score > bestScore) { best = p; bestScore = score; }
  }
  return best;
}

function selectHomeFrontier(expId) {
  _homeFrontierId = expId;
  try { localStorage.setItem("distillate-home-frontier", expId); } catch {}
  renderHomeFrontierChart();
  const menu = document.getElementById("home-frontier-menu");
  if (menu) menu.classList.add("hidden");
}

// Find the best metric value from the experiment's runs for the given metric.
function _bestMetricValue(exp, metric) {
  if (!exp || !metric) return null;
  const lower = (typeof isLowerBetter === "function") ? isLowerBetter(metric) : false;
  let bestVal = null;
  for (const r of exp.runs || []) {
    const v = r.results?.[metric];
    if (typeof v !== "number" || !isFinite(v)) continue;
    if (bestVal === null) bestVal = v;
    else if (lower ? v < bestVal : v > bestVal) bestVal = v;
  }
  return bestVal;
}

function _formatMetricValue(metric, v) {
  if (v == null) return "—";
  if (typeof formatMetric === "function") return formatMetric(metric, v);
  return Number.isInteger(v) ? String(v) : v.toFixed(3);
}

function updateBreadcrumb(exp) {
  const leaf = document.getElementById("topbar-crumb-view");
  const projectCrumb = document.getElementById("topbar-crumb-project");
  const projectSep = document.getElementById("topbar-crumb-sep-project");

  if (leaf) leaf.textContent = exp?.name || exp?.id || "Nicolas";

  if (exp) {
    // Show workspace name in the breadcrumb when viewing an experiment
    const workspace = exp?.workspace_id || exp?.workspace_name || "Workbench";
    if (projectCrumb) {
      projectCrumb.textContent = workspace;
      projectCrumb.classList.remove("hidden");
    }
    if (projectSep) projectSep.classList.remove("hidden");
  } else {
    if (projectCrumb) projectCrumb.classList.add("hidden");
    if (projectSep) projectSep.classList.add("hidden");
  }
}

function updateSessionBreadcrumb(sessionId) {
  const sessionCrumb = document.getElementById("topbar-crumb-session");
  const sessionSep = document.getElementById("topbar-crumb-sep-session");
  const projectCrumb = document.getElementById("topbar-crumb-project");
  const projectSep = document.getElementById("topbar-crumb-sep-project");
  const viewCrumb = document.getElementById("topbar-crumb-view");
  const viewSep = document.getElementById("topbar-crumb-sep-view");

  if (sessionId) {
    if (sessionCrumb) {
      sessionCrumb.textContent = sessionId;
      sessionCrumb.classList.remove("hidden");
    }
    if (sessionSep) sessionSep.classList.remove("hidden");
    // Hide project and view crumbs when session is active
    if (projectCrumb) projectCrumb.classList.add("hidden");
    if (projectSep) projectSep.classList.add("hidden");
    if (viewSep) viewSep.classList.add("hidden");
    if (viewCrumb) viewCrumb.classList.add("hidden");
  } else {
    if (sessionCrumb) sessionCrumb.classList.add("hidden");
    if (sessionSep) sessionSep.classList.add("hidden");
    // Show project and view crumbs when session is inactive
    if (projectCrumb) projectCrumb.classList.remove("hidden");
    if (projectSep) projectSep.classList.remove("hidden");
    if (viewSep) viewSep.classList.remove("hidden");
    if (viewCrumb) viewCrumb.classList.remove("hidden");
  }
}

function updateCurrentCell(exp, metric) {
  const valueEl = document.getElementById("ov-current-value");
  const subEl = document.getElementById("ov-current-sub");
  const labelEl = document.getElementById("ov-current-label");
  const expNameEl = document.getElementById("ov-current-experiment");
  const deltaEl = document.getElementById("ov-current-delta");
  const liveDotEl = document.getElementById("ov-current-livedot");
  const sparkEl = document.getElementById("ov-current-sparkline");
  // Retire any legacy .ov-current-name node left in the DOM from prior runs.
  const valueRow = document.querySelector("#ov-current .ov-value");
  if (valueRow) {
    const legacy = valueRow.querySelector(".ov-current-name");
    if (legacy) legacy.remove();
  }
  if (!valueEl) return;
  if (labelEl) labelEl.textContent = exp ? (exp.name || exp.id || "—") : "—";
  if (expNameEl) expNameEl.textContent = "";
  if (liveDotEl) liveDotEl.classList.toggle("on", (exp?.active_sessions || 0) > 0);
  if (!exp) {
    valueEl.textContent = "—";
    if (deltaEl) deltaEl.textContent = "";
    if (subEl) subEl.textContent = "No experiments yet";
    if (sparkEl) sparkEl.innerHTML = "";
    return;
  }
  const series = _bestSoFarSeries(exp, metric);
  const best = series.length ? series[series.length - 1] : null;
  valueEl.textContent = _formatMetricValue(metric, best);
  // Delta vs. prior best (penultimate distinct value in the best-so-far series).
  if (deltaEl) {
    const prior = _priorBest(series);
    if (best != null && prior != null && prior !== best) {
      const lower = (typeof isLowerBetter === "function") ? isLowerBetter(metric) : false;
      const improved = lower ? (best < prior) : (best > prior);
      const diff = best - prior;
      deltaEl.textContent = (diff > 0 ? "+" : "") + diff.toFixed(Math.abs(diff) < 1 ? 3 : 2);
      deltaEl.classList.toggle("up", improved);
      deltaEl.classList.toggle("down", !improved);
    } else {
      deltaEl.textContent = "";
      deltaEl.classList.remove("up", "down");
    }
  }
  if (sparkEl) _renderOvSparkline(sparkEl, series, metric);
  if (subEl) {
    const dirArrow = (typeof isLowerBetter === "function" && metric)
      ? (isLowerBetter(metric) ? " ↓" : " ↑") : "";
    subEl.textContent = metric ? `${metric}${dirArrow}` : "";
  }
}

// Walk runs in order and emit the running best metric value (best-so-far).
function _bestSoFarSeries(exp, metric) {
  if (!exp || !metric) return [];
  const lower = (typeof isLowerBetter === "function") ? isLowerBetter(metric) : false;
  const out = [];
  let best = null;
  for (const r of exp.runs || []) {
    const v = r.results?.[metric];
    if (typeof v !== "number" || !isFinite(v)) continue;
    if (best === null) best = v;
    else if (lower ? v < best : v > best) best = v;
    out.push(best);
  }
  return out;
}

function _priorBest(series) {
  if (series.length < 2) return null;
  const last = series[series.length - 1];
  for (let i = series.length - 2; i >= 0; i--) {
    if (series[i] !== last) return series[i];
  }
  return null;
}

function _renderOvSparkline(svg, series, metric) {
  if (!series || series.length < 2) { svg.innerHTML = ""; return; }
  const W = 200, H = 24;
  const lower = (typeof isLowerBetter === "function") ? isLowerBetter(metric) : false;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  const pts = series.map((v, i) => {
    const x = (i / (series.length - 1)) * W;
    // Higher values are "better" on screen when higher-is-better, and vice versa.
    const norm = lower ? (v - min) / range : (max - v) / range;
    const y = 2 + norm * (H - 4);
    return [x, y];
  });
  const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const area = line + ` L${W},${H} L0,${H} Z`;
  const last = pts[pts.length - 1];
  svg.innerHTML =
    `<path d="${area}" fill="var(--green)" opacity="0.15"/>` +
    `<path d="${line}" fill="none" stroke="var(--green)" stroke-width="1.4" stroke-linejoin="round"/>` +
    `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.5" fill="var(--green)"/>`;
}

function populateFrontierMenu(exps, activeId) {
  const menu = document.getElementById("home-frontier-menu");
  if (!menu) return;
  const items = (exps || []).filter((p) => (p.runs || []).length > 0);
  items.sort((a, b) => {
    const la = (a.active_sessions || 0) > 0 ? 1 : 0;
    const lb = (b.active_sessions || 0) > 0 ? 1 : 0;
    if (la !== lb) return lb - la;
    return _lastActivityMs(b) - _lastActivityMs(a);
  });
  if (items.length === 0) { menu.innerHTML = ""; return; }
  menu.innerHTML = items.map((p) => {
    const name = p.name || p.id;
    const runs = (p.runs || []).length;
    const live = (p.active_sessions || 0) > 0;
    const activeCls = p.id === activeId ? " active" : "";
    return `
      <div class="hpi${activeCls}" data-id="${escapeHtml ? escapeHtml(p.id) : p.id}" role="option">
        <div class="hpi-name">${live ? '<span class="hpi-live">●</span>' : ""}${escapeHtml ? escapeHtml(name) : name}</div>
        <div class="hpi-meta">${runs} run${runs !== 1 ? "s" : ""}</div>
      </div>`;
  }).join("");
  menu.querySelectorAll(".hpi").forEach((el) => {
    el.addEventListener("click", () => selectHomeFrontier(el.dataset.id));
  });
}

// Click-outside to dismiss picker menu. Installed once.
if (!window._homePickerBound) {
  window._homePickerBound = true;
  document.addEventListener("click", (e) => {
    const menu = document.getElementById("home-frontier-menu");
    const picker = document.getElementById("home-frontier-picker");
    if (!menu || menu.classList.contains("hidden")) return;
    if (picker && (picker === e.target || picker.contains(e.target))) return;
    if (menu.contains(e.target)) return;
    menu.classList.add("hidden");
  });
}

// Render the hero frontier chart on Nicolas home. Uses the user's pinned
// selection (from the picker dropdown) or an auto-picked default, and in
// the same pass updates the breadcrumb leaf and the Current-experiment cell.
function renderHomeFrontierChart() {
  const canvas = document.getElementById("home-frontier-canvas");
  const empty = document.getElementById("home-frontier-empty");
  const subject = document.getElementById("home-frontier-subject");
  const subtitle = document.getElementById("home-frontier-subtitle");
  const hero = document.getElementById("home-frontier");
  if (!canvas || !hero) return;

  const exps = (typeof cachedProjects !== "undefined" ? cachedProjects : []) || [];

  // Resolve featured experiment: pinned id → default pick.
  let featured = null;
  if (_homeFrontierId) {
    featured = exps.find((p) => p.id === _homeFrontierId && (p.runs || []).length > 0) || null;
  }
  if (!featured) featured = pickDefaultFrontierExp(exps);

  populateFrontierMenu(exps, featured?.id);

  if (!featured) {
    hero.classList.add("empty");
    if (empty) empty.classList.remove("hidden");
    if (subject) subject.textContent = "";
    if (subtitle) subtitle.innerHTML = "Nothing running yet.";
    updateBreadcrumb(null);
    updateCurrentCell(null, null);
    return;
  }

  // Hero metric: experiment's declared key_metric_name wins; fall back to any
  // numeric result on a run if the field isn't set.
  let metric = featured.key_metric_name || featured.preferred_metric || featured.objective_metric || null;
  if (!metric) {
    outer: for (const r of featured.runs || []) {
      for (const [k, v] of Object.entries(r.results || {})) {
        if (typeof v === "number" && isFinite(v)) { metric = k; break outer; }
      }
    }
  }

  updateBreadcrumb(featured);
  updateCurrentCell(featured, metric);
  if (subject) subject.textContent = featured.name || featured.id;

  if (!metric) {
    hero.classList.add("empty");
    if (empty) empty.classList.remove("hidden");
    if (subtitle) subtitle.innerHTML = "Runs in flight, awaiting first metric.";
    return;
  }
  hero.classList.remove("empty");
  if (empty) empty.classList.add("hidden");
  const runCount = (featured.runs || []).length;

  // Trending pill: delta from first run with this metric → current best.
  // Always computed in the "improvement" direction for the metric, so a
  // positive % means "we got better".
  let trendHtml = "";
  try {
    const vals = [];
    for (const r of featured.runs || []) {
      const v = r.results?.[metric];
      if (typeof v === "number" && isFinite(v)) vals.push(v);
    }
    if (vals.length >= 2) {
      const first = vals[0];
      const lower = (typeof isLowerBetter === "function") ? isLowerBetter(metric) : false;
      const best = lower ? Math.min(...vals) : Math.max(...vals);
      if (first !== 0 && isFinite(first)) {
        const rawDelta = lower ? (first - best) / Math.abs(first) : (best - first) / Math.abs(first);
        const pct = rawDelta * 100;
        if (Math.abs(pct) >= 0.5) {
          const cls = pct > 0 ? "trend-up" : "trend-down";
          const arrow = pct > 0 ? "\u2197" : "\u2198";
          const sign = pct > 0 ? "+" : "";
          trendHtml = ` · <span class="trend-pill ${cls}">${arrow} ${sign}${pct.toFixed(1)}%</span>`;
        }
      }
    }
  } catch {}

  if (subtitle) subtitle.innerHTML = `<b>${runCount}</b> run${runCount !== 1 ? "s" : ""} · <span style="color:var(--text-muted,var(--text-dim))">${metric}</span> · Pareto front in green${trendHtml}`;

  try {
    if (typeof renderMetricChart === "function") {
      const runs = (typeof getDisplayRuns === "function") ? getDisplayRuns(featured.runs || []) : (featured.runs || []);
      const liveEvents = (typeof liveMetrics !== "undefined" ? liveMetrics[featured.id] : null) || null;
      const logScale = (typeof isLowerBetter === "function") ? isLowerBetter(metric) : false;
      renderMetricChart(canvas, runs, metric, liveEvents, { logScale });
      _homeFrontierData = { runs, metricName: metric, title: featured.name || featured.id, logScale, summary: featured.experiment_summary || featured.description || "" };
    }
  } catch (e) {
    hero.classList.add("empty");
    if (empty) empty.classList.remove("hidden");
  }
}

// Wire the picker button + cell click to open the dropdown. Installed once.
function _bindHomeFrontierPicker() {
  if (window._homeFrontierPickerBound) return;
  window._homeFrontierPickerBound = true;
  const picker = document.getElementById("home-frontier-picker");
  const menu = document.getElementById("home-frontier-menu");
  const cell = document.getElementById("ov-current");
  const openMenu = (anchor) => {
    if (!menu || !anchor) return;
    const rect = anchor.getBoundingClientRect();
    menu.style.top = `${rect.bottom + 6}px`;
    menu.style.left = `${rect.left}px`;
    menu.classList.toggle("hidden");
  };
  if (picker) picker.addEventListener("click", (e) => { e.stopPropagation(); openMenu(picker); });
  if (cell) {
    const open = (e) => { e.stopPropagation(); openMenu(cell); };
    cell.addEventListener("click", open);
    cell.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") open(e); });
  }
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _bindHomeFrontierPicker);
} else {
  _bindHomeFrontierPicker();
}

// Re-render hero chart whenever the canvas container changes size (window
// resize OR sidebar open/close both change the container's rendered width).
{
  const _canvas = document.getElementById("home-frontier-canvas");
  if (_canvas && typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => {
      try { renderHomeFrontierChart(); } catch {}
    }).observe(_canvas.parentElement || _canvas);
  } else {
    window.addEventListener("resize", () => {
      try { renderHomeFrontierChart(); } catch {}
    });
  }
}

// On boot, render overview from last-known cache so the user sees real numbers
// immediately instead of zeros. Fresh data overwrites them once /init returns.
function _hydrateDashboardFromCache() {
  try { localStorage.removeItem("distillate-dashboard-cache"); } catch {}
  try {
    const raw = localStorage.getItem("distillate-dashboard-cache-v2");
    if (!raw) return;
    const c = JSON.parse(raw);
    const setText = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v == null ? "" : String(v);
    };
    // Projects cell retired — the Compute cell now occupies that slot and is
    // populated from the live billing pills (no cache restore needed).
    setText("dash-experiments-count", c.experiments || 0);
    const _runsEl = document.getElementById("ov-exp-runs");
    if (_runsEl) _runsEl.innerHTML = (c.runs || 0) > 0 ? `<span style="color:var(--warm)">${c.runs} run${c.runs !== 1 ? "s" : ""}</span>` : "";
    setText("dash-papers-count", c.papers || 0);
    const unreadEl = document.getElementById("ov-papers-unread");
    if (unreadEl) unreadEl.innerHTML = (c.papersQueue || 0) > 0 ? `<span style="color:var(--warm)">${c.papersQueue} unread</span>` : ((c.papersRead || 0) > 0 ? `${c.papersRead} read` : "");
  } catch {}
}

// Hydrate immediately on script load — runs before any fetches complete
_hydrateDashboardFromCache();

// ── Cache hydration for sidebars/lists ──
// Renders cached data instantly so sidebars are populated before fetches return.
// Fresh data overwrites once endpoints respond.
function _hydrateFromCache() {
  // Workspaces (Projects sidebar)
  try {
    const raw = localStorage.getItem("distillate-workspaces-cache");
    if (raw) {
      const ws = JSON.parse(raw);
      if (Array.isArray(ws) && typeof renderWorkspacesList === "function") {
        if (typeof _workspaces !== "undefined") _workspaces = ws;
        renderWorkspacesList(ws);
      }
    }
  } catch {}

  // Experiments (Experiments sidebar)
  try {
    const raw = localStorage.getItem("distillate-experiments-cache");
    if (raw) {
      const projs = JSON.parse(raw);
      if (Array.isArray(projs) && typeof renderProjectsList === "function") {
        cachedProjects = projs;
        renderProjectsList(projs);
      }
    }
  } catch {}

  // Agents
  try {
    const raw = localStorage.getItem("distillate-agents-cache");
    if (raw) {
      const ag = JSON.parse(raw);
      if (Array.isArray(ag) && typeof renderAgentsList === "function") {
        if (typeof _agents !== "undefined") _agents = ag;
        renderAgentsList();
      }
    }
  } catch {}

  // Papers
  try {
    const raw = localStorage.getItem("distillate-papers-cache");
    if (raw) {
      const papers = JSON.parse(raw);
      if (Array.isArray(papers) && typeof renderPapersList === "function") {
        window._cachedPapersData = papers;
        renderPapersList(papers);
      }
    }
  } catch {}
}

// Run hydration as soon as scripts load — DOM ready isn't required for
// localStorage reads, and the renderers are no-ops if their target elements
// haven't mounted yet (they'll re-render after DOMContentLoaded anyway).
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _hydrateFromCache);
} else {
  _hydrateFromCache();
}

function _applyWorkspacesData(data) {
  if (!data.ok) return;
  const workspaces = data.workspaces || [];
  if (Array.isArray(workspaces) && typeof renderWorkspacesList === "function") {
    if (typeof _workspaces !== "undefined") _workspaces = workspaces;
    renderWorkspacesList(workspaces);
  }
  // Cache for instant render on next launch
  try { localStorage.setItem("distillate-workspaces-cache", JSON.stringify(workspaces)); } catch {}
  if (typeof populateWelcomeDashboard === "function") populateWelcomeDashboard();
}

function fetchWelcomeStats() {
  if (!serverPort) return;

  // Batched init: one round-trip instead of five parallel fetches
  fetch(`http://127.0.0.1:${serverPort}/init`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok) return;
      _applyStatusData(data.status);
      _applyWorkspacesData(data);
      _applyExperimentsData(data.experiments);
      _applyPapersData(data.papers);
      if (data.integrations) {
        _applyIntegrationsData(data.integrations);
      } else {
        _applyConnectorsData(data.connectors || {});
      }
      // Populate dashboard once experiments + papers + workspaces are in
      populateWelcomeDashboard();
      // Fetch agents separately (not in /init bundle) to fill the agents card
      if (typeof fetchAgents === "function") {
        fetchAgents().then(() => populateWelcomeDashboard()).catch(() => {});
      }
      checkForUpdate();
    })
    .catch(() => {
      // Fallback: if /init not available, use individual endpoints
      _fetchStatusOnly();
      if (typeof fetchExperimentsList === "function") fetchExperimentsList();
      if (typeof fetchPapersData === "function") fetchPapersData();
      if (typeof fetchIntegrations === "function") fetchIntegrations();
      if (typeof fetchAgents === "function") fetchAgents();
    });
}

function _fetchStatusOnly() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/status`)
    .then((r) => r.json())
    .then((data) => { if (data.ok) _applyStatusData(data); })
    .catch(() => {});
}

function checkForUpdate() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/version/check`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok || !data.update_available) return;
      const dismissed = localStorage.getItem("distillate-update-dismissed");
      if (dismissed === data.latest) return;
      showUpdateBanner(data.current, data.latest);
    })
    .catch(() => {});
}

function showUpdateBanner(current, latest) {
  const existing = document.getElementById("update-banner");
  if (existing) return;
  const bar = document.getElementById("status-bar");
  if (!bar) return;
  const banner = document.createElement("div");
  banner.id = "update-banner";
  banner.className = "update-banner";
  banner.innerHTML = `<span>v${escapeHtml(latest)} available</span><code>uv pip install -U distillate</code><button id="update-dismiss" aria-label="Dismiss">\u00d7</button>`;
  bar.parentNode.insertBefore(banner, bar);
  banner.querySelector("#update-dismiss")?.addEventListener("click", () => {
    localStorage.setItem("distillate-update-dismissed", latest);
    banner.remove();
  });
}

function updateSuggestions(isFirstUse, hasPapers, hasExperiments) {
  const container = document.getElementById("chat-suggestions");
  const suggestions = _buildSuggestions(isFirstUse, hasPapers, hasExperiments);
  _renderSuggestions(container, suggestions);
}

function refreshChatSuggestions() {
  const container = document.getElementById("chat-suggestions");
  if (!container || container.dataset.dismissed) return;

  let suggestions;

  // Context: experiment selected
  if (currentProjectId) {
    const proj = cachedProjects.find((p) => p.id === currentProjectId);
    if (proj) {
      suggestions = [];
      if (proj.active_sessions > 0) {
        suggestions.push({ text: `How is ${proj.name || proj.id} going?`, label: "Check progress" });
        suggestions.push({ text: `Steer ${proj.name || proj.id} — what should it try next?`, label: "Steer experiment" });
      } else if (proj.run_count > 0) {
        suggestions.push({ text: `Analyze the results for ${proj.name || proj.id}`, label: "Analyze results" });
        suggestions.push({ text: `Continue ${proj.name || proj.id} with a new session`, label: "Continue experiment" });
      } else {
        suggestions.push({ text: `Launch ${proj.name || proj.id}`, label: "Launch experiment" });
      }
      if (proj.run_count >= 2) {
        suggestions.push({ text: `Compare the runs in ${proj.name || proj.id}`, label: "Compare runs" });
      }
      suggestions.push({ text: `What papers are relevant to ${proj.name || proj.id}?`, label: "Find related papers" });
    }
  }

  // Context: paper selected
  else if (currentPaperKey) {
    const paper = cachedPapers.find((p) => p.key === currentPaperKey);
    if (paper) {
      const shortTitle = (paper.title || "").length > 40
        ? paper.title.slice(0, 40) + "\u2026"
        : paper.title;
      suggestions = [];
      if (paper.status === "processed") {
        suggestions.push({ text: `Summarize "${shortTitle}"`, label: "Summarize" });
        suggestions.push({ text: `What are the key insights from "${shortTitle}"?`, label: "Key insights" });
        suggestions.push({ text: `What experiments could I run based on "${shortTitle}"?`, label: "Experiment ideas" });
      } else {
        suggestions.push({ text: `What is "${shortTitle}" about?`, label: "Quick overview" });
      }
      suggestions.push({ text: `Find papers similar to "${shortTitle}"`, label: "Similar papers" });
      if (!paper.promoted) {
        suggestions.push({ text: `Why should I promote "${shortTitle}"?`, label: "Worth promoting?" });
      }
    }
  }

  // Context: nothing selected — global suggestions
  else {
    const hasRunning = cachedProjects.some((p) => p.active_sessions > 0);
    const hasPapers = cachedPapers.length > 0;
    const hasExps = cachedProjects.length > 0;

    suggestions = [];
    if (hasRunning) {
      suggestions.push({ text: "What's running right now?", label: "Live status" });
    }
    if (hasExps) {
      suggestions.push({ text: "How are my experiments going?", label: "Experiment status" });
    }
    if (hasPapers) {
      suggestions.push({ text: "What's in my reading queue?", label: "Reading queue" });
      suggestions.push({ text: "Summarize my last read", label: "Last read" });
    }
    if (hasExps && hasPapers) {
      suggestions.push({ text: "What should I try next based on what I've read?", label: "What's next?" });
    }
    if (!hasExps && !hasPapers) {
      suggestions.push({ text: "__launch_demo__", label: "Launch demo experiment", action: launchDemoExperiment });
      suggestions.push({ text: "What can you do?", label: "What can you do?" });
    }
  }

  if (suggestions) {
    _renderSuggestions(container, suggestions);
  }
  // Context chips always refresh alongside suggestions so the chip row
  // mirrors whatever the suggestion logic is reacting to.
  refreshComposerContext();
}

function _buildSuggestions(isFirstUse, hasPapers, hasExperiments) {
  if (isFirstUse) {
    return [
      { text: "__launch_demo__", label: "Launch demo experiment", action: launchDemoExperiment },
      { text: "What can you do?", label: "What can you do?" },
      { text: "How does this work?", label: "How does it work?" },
      { text: "How do I connect my Zotero library?", label: "Connect Zotero" },
    ];
  } else if (!hasExperiments) {
    return [
      { text: "What's in my queue?", label: "What's in my queue?" },
      { text: "Run my first experiment", label: "My first experiment" },
      { text: "Summarize my last read", label: "Summarize last read" },
      { text: "What should I try next?", label: "What should I try?" },
    ];
  } else if (!hasPapers) {
    return [
      { text: "How are my experiments going?", label: "Experiment status" },
      { text: "How do I connect my Zotero library?", label: "Connect Zotero" },
      { text: "What should I try next?", label: "What should I try?" },
      { text: "Run a new experiment", label: "New experiment" },
    ];
  } else {
    return [
      { text: "What should I run next?", label: "What's next?" },
      { text: "Analyze my last run", label: "Analyze last run" },
      { text: "What patterns across my recent reads?", label: "Synthesize reads" },
      { text: "Draft a lab note from today", label: "Draft lab note" },
    ];
  }
}

// ── Composer context chips ─────────────────────────────────────────────────
// Reflects the current selection (project / paper / latest run) as tappable
// chips above the input. _composerContextItems holds explicitly-pinned focus
// items added via the "+ add context" palette; these are sent to the server
// as payload.context.focus on the next message.
window._composerContextItems = window._composerContextItems || [];

function refreshComposerContext() {
  const ctx = document.getElementById("composer-context");
  if (!ctx) return;
  ctx.innerHTML = "";

  const addChip = ({ kind, icon, label, onClear }) => {
    const chip = document.createElement("span");
    chip.className = `composer-ctx-chip is-${kind}`;
    if (icon) {
      const ico = document.createElement("span");
      ico.className = "ctx-ico";
      ico.textContent = icon;
      chip.appendChild(ico);
    }
    const txt = document.createElement("span");
    txt.textContent = label;
    chip.appendChild(txt);
    if (onClear) {
      const x = document.createElement("span");
      x.className = "ctx-x";
      x.setAttribute("role", "button");
      x.setAttribute("tabindex", "0");
      x.setAttribute("aria-label", `Remove ${label} from context`);
      x.textContent = "\u00D7";
      x.addEventListener("click", (e) => { e.stopPropagation(); onClear(); });
      chip.appendChild(x);
    }
    ctx.appendChild(chip);
  };

  if (typeof currentProjectId !== "undefined" && currentProjectId) {
    const proj = (typeof cachedProjects !== "undefined" ? cachedProjects : []).find((p) => p.id === currentProjectId);
    if (proj) {
      addChip({
        kind: "exp",
        icon: "\u25B3",
        label: proj.name || proj.id,
        onClear: () => {
          currentProjectId = null;
          if (typeof refreshChatSuggestions === "function") refreshChatSuggestions();
          refreshComposerContext();
        },
      });
      const latestRun = (proj.runs || []).slice(-1)[0];
      if (latestRun) {
        const runNum = latestRun.run_number > 0 ? latestRun.run_number : null;
        const rid = latestRun.id || latestRun.run_id || latestRun.name;
        const runLabel = runNum != null
          ? `run #${runNum}`
          : (rid ? `run ${String(rid).slice(0, 6)}` : `run #${(proj.runs || []).length}`);
        addChip({ kind: "run", icon: "\u25CB", label: runLabel });
      }
    }
  }

  if (typeof currentPaperKey !== "undefined" && currentPaperKey) {
    const paper = (typeof cachedPapers !== "undefined" ? cachedPapers : []).find((p) => p.key === currentPaperKey);
    if (paper) {
      const title = paper.title || paper.key;
      const shortTitle = title.length > 32 ? title.slice(0, 32) + "\u2026" : title;
      addChip({
        kind: "paper",
        icon: "\u275B",
        label: shortTitle,
        onClear: () => {
          currentPaperKey = null;
          if (typeof refreshChatSuggestions === "function") refreshChatSuggestions();
          refreshComposerContext();
        },
      });
    }
  }

  // Render explicitly-pinned focus items (added via "+ add context" palette)
  (window._composerContextItems || []).forEach((item) => {
    const iconMap = { paper: "\u275B", experiment: "\u25B3", project: "\u25A1" };
    const kindMap = { paper: "paper", experiment: "exp", project: "proj" };
    addChip({
      kind: kindMap[item.type] || "exp",
      icon: iconMap[item.type] || "\u25CB",
      label: item.label,
      onClear: () => {
        window._composerContextItems = (window._composerContextItems || []).filter(
          (x) => !(x.type === item.type && x.id === item.id)
        );
        refreshComposerContext();
      },
    });
  });

  // Always show "+ add context" so users can pin focus items
  const add = document.createElement("button");
  add.className = "composer-ctx-add";
  add.type = "button";
  add.textContent = "+ add context";
  add.addEventListener("click", () => {
    if (typeof openResourceSearch === "function") {
      openResourceSearch({
        onAttach: (item) => {
          window._composerContextItems = window._composerContextItems || [];
          if (!window._composerContextItems.some((x) => x.type === item.type && x.id === item.id)) {
            window._composerContextItems.push(item);
          }
          refreshComposerContext();
        },
      });
    }
  });
  ctx.appendChild(add);
}

function _renderSuggestions(container, suggestions) {
  if (!container) return;
  container.innerHTML = "";
  suggestions.forEach((s, i) => {
    const btn = document.createElement("button");
    btn.className = "suggestion" + (s.action ? " suggestion-primary" : "");
    btn.dataset.text = s.text;
    const num = document.createElement("span");
    num.className = "suggestion-num";
    num.textContent = String(i + 1);
    btn.appendChild(num);
    btn.appendChild(document.createTextNode(s.label));
    btn.addEventListener("click", () => {
      if (s.action) { s.action(); return; }
      if (s.text === "__launch_demo__") { launchDemoExperiment(); return; }
      inputEl.value = s.text;
      sendMessage();
    });
    container.appendChild(btn);
  });
}

/* ───── Cloud sync ───── */

function triggerCloudSync() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/sync`, { method: "POST" })
    .then((r) => { if (!r.ok) return; return r.json(); })
    .then(() => {})
    .catch(() => {});
}

/* ───── Post-mutation data refresh ───── */

function refreshTabData() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/status`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok) return;
      if (welcomeStatsEl) {
        const parts = [];
        if (data.papers_read != null)
          parts.push(`${data.papers_read} paper${data.papers_read !== 1 ? "s" : ""} read`);
        if (data.papers_queued != null) parts.push(`${data.papers_queued} in queue`);
        if (parts.length) {
          welcomeStatsEl.textContent = "\uD83D\uDCDA " + parts.join(" \u00B7 ");
        }
      }
    })
    .catch(() => {});

  // Refresh all visible panes
  if (typeof fetchExperimentsList === "function") fetchExperimentsList();
  if (typeof fetchPapersData === "function") fetchPapersData();
}

/* ───── Electron bridge ───── */

if (window.nicolas) {
  // Running inside Electron
  window.nicolas.onUpdateProgress(({ message }) => {
    statusDot.className = "dot updating";

    statusText.textContent = message;
  });

  window.nicolas.onServerReady(({ port }) => {
    if (!ws) connect(port);
  });

  window.nicolas.onServerError(({ message }) => {
    statusText.textContent = `Error: ${message}`;
    statusDot.className = "dot disconnected";

  });

  window.nicolas.onDeepLink((url) => {
    handleDeepLink(url);
  });

  window.nicolas.onNewConversation(() => {
    clearConversation();
  });

  window.nicolas.onOpenSettings(() => {
    openSettings();
  });

  // Cmd+K: focus Nicolas welcome input from any view
  // Cmd+K (from the Electron menu accelerator) opens the unified command
  // palette. Typing "Ask Nicolas: ..." routes to the main-window chat.
  window.nicolas.onFocusNicolas(() => {
    if (typeof openResourceSearch === "function") {
      openResourceSearch();
    }
  });

  // When served from the Python server (http://), we know it's ready — connect
  // immediately using the port from the URL. This avoids a race where the
  // server-ready IPC arrives before the listener is registered.
  if (window.location.protocol === "http:") {
    const port = window.location.port || 8742;
    connect(port);
  }
} else {
  // Running in a regular browser (development)
  const port = new URLSearchParams(window.location.search).get("port") || 8742;
  connect(port);
}

/* ───── Deep link handling ───── */

function handleDeepLink(url) {
  try {
    const parsed = new URL(url);
    // distillate://auth?bootstrap=XXX  (HF OAuth) or  distillate://auth?token=XXX  (legacy)
    if (parsed.hostname === "auth" || parsed.pathname === "//auth" || parsed.pathname === "/auth") {
      const bootstrap = parsed.searchParams.get("bootstrap");
      const token = parsed.searchParams.get("token");
      if (bootstrap) {
        _handleHfBootstrap(bootstrap);
      } else if (token && window.nicolas && window.nicolas.saveSettings) {
        // Legacy Distillate Cloud opaque token — keep working for backward compat
        window.nicolas.saveSettings({ authToken: token }).then(() => {
          statusText.textContent = "Cloud authenticated!";
          setTimeout(() => {
            if (statusText.textContent === "Cloud authenticated!") {
              statusText.textContent = "Connected";
            }
          }, 3000);
          if (ws) {
            ws.close();
          }
        });
      }
    }
  } catch (err) {
    console.error("Failed to handle deep link:", err);
  }
}

async function _handleHfBootstrap(bootstrap) {
  if (!serverPort) return;
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/auth/signin-hf-complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bootstrap }),
    });
    const data = await r.json();
    if (data.ok) {
      if (typeof _refreshHfAuthBar === "function") _refreshHfAuthBar();
      if (typeof refreshAccountState === "function") refreshAccountState();
      if (typeof renderWelcomeScreen === "function") {
        _welcomeState = null;
        renderWelcomeScreen();
      }
      const toastMsg = data.toast || (() => {
        const name = data.user?.display_name || data.user?.hf_username;
        return name ? `Welcome, ${name}` : "Signed in";
      })();
      if (typeof _showHfToast === "function") _showHfToast(toastMsg);
    } else {
      console.error("HF bootstrap exchange failed:", data.reason);
    }
  } catch (err) {
    console.error("HF bootstrap exchange request failed:", err);
  }
}

function _showHfToast(message) {
  const toast = document.createElement("div");
  toast.style.cssText = [
    "position:fixed", "bottom:24px", "right:24px", "z-index:9999",
    "background:var(--surface,#1e1e1e)", "color:var(--text,#e0e0e0)",
    "border:1px solid var(--border,#333)", "border-radius:8px",
    "padding:10px 16px", "font-size:13px", "box-shadow:0 4px 16px rgba(0,0,0,.4)",
    "max-width:340px", "animation:fadeInUp .25s ease",
  ].join(";");
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 6000);
}
