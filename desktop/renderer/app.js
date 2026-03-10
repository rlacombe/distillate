/* ───── Nicolas Desktop — Chat + Lab UI ───── */

let ws = null;
let serverPort = null;
let isStreaming = false;
let currentAssistantEl = null;
let currentText = "";
let turnHadMutation = false;
let sseSource = null;
let hasExperiments = false;
let currentTab = "chat";

const messagesEl = document.getElementById("messages");
const welcomeEl = document.getElementById("welcome");
const inputEl = document.getElementById("input");
const formEl = document.getElementById("input-form");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const tabBar = document.getElementById("tab-bar");
const labContainer = document.getElementById("lab-container");
const notebookContainer = document.getElementById("notebook-container");
const chatContainer = document.getElementById("chat-container");

/* ───── marked.js config ───── */
if (typeof marked !== "undefined") {
  marked.setOptions({
    breaks: true,
    gfm: true,
  });

  // Custom renderer for syntax highlighting (hljs exposed via preload)
  const renderer = new marked.Renderer();
  renderer.code = function ({ text, lang }) {
    if (window.hljs && lang && window.hljs.getLanguage(lang)) {
      const highlighted = window.hljs.highlight(text, { language: lang }).value;
      return `<pre><code class="hljs language-${lang}">${highlighted}</code></pre>`;
    }
    if (window.hljs) {
      const auto = window.hljs.highlightAuto(text).value;
      return `<pre><code class="hljs">${auto}</code></pre>`;
    }
    const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return `<pre><code>${escaped}</code></pre>`;
  };
  marked.use({ renderer });
}

/* ───── Connection ───── */

function connect(port) {
  serverPort = port;
  ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);

  ws.onopen = () => {
    statusDot.className = "dot connected";
    statusText.textContent = "Connected";
    inputEl.disabled = false;
    sendBtn.disabled = false;
    inputEl.focus();

    // Fetch stats for welcome screen
    fetchWelcomeStats();

    // Pull latest state from cloud on connect
    triggerCloudSync();
  };

  ws.onclose = () => {
    statusDot.className = "dot disconnected";
    statusText.textContent = "Disconnected — restarting...";
    inputEl.disabled = true;
    sendBtn.disabled = true;
    // Attempt reconnect
    setTimeout(() => connect(port), 2000);
  };

  ws.onerror = () => {
    statusDot.className = "dot disconnected";
    statusText.textContent = "Connection error";
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleEvent(data);
  };
}

/* ───── Event handling ───── */

function handleEvent(event) {
  switch (event.type) {
    case "text_delta":
      if (!currentAssistantEl) {
        startAssistantMessage();
      }
      currentText += event.text;
      renderAssistantMessage();
      scrollToBottom();
      break;

    case "tool_start":
      // Close current text block so the indicator appears between text sections
      if (currentAssistantEl) {
        currentAssistantEl.classList.remove("streaming-cursor");
        renderAssistantMessage();
        currentAssistantEl = null;
        currentText = "";
      }
      addToolIndicator(event.name, false);
      scrollToBottom();
      break;

    case "tool_done": {
      const mutatingTools = [
        "run_sync", "add_paper_to_zotero", "reprocess_paper",
        "promote_papers", "refresh_metadata", "scan_project",
        "delete_paper",
      ];
      if (mutatingTools.includes(event.name)) {
        turnHadMutation = true;
      }
      markToolDone(event.tool_use_id || event.name);
      break;
    }

    case "turn_end":
      finishStreaming();
      if (turnHadMutation) {
        triggerCloudSync();
        turnHadMutation = false;
      }
      break;

    case "error":
      finishStreaming();
      addErrorMessage(event.message || "Something went wrong.");
      break;
  }
}

/* ───── Message rendering ───── */

function addUserMessage(text) {
  welcomeEl.classList.add("hidden");
  const el = document.createElement("div");
  el.className = "message user";
  el.textContent = text;
  messagesEl.appendChild(el);
  scrollToBottom(true);
}

function startAssistantMessage() {
  currentAssistantEl = document.createElement("div");
  currentAssistantEl.className = "message assistant streaming-cursor";
  messagesEl.appendChild(currentAssistantEl);
  currentText = "";
  isStreaming = true;
}

function renderAssistantMessage() {
  if (!currentAssistantEl) return;
  if (typeof marked !== "undefined") {
    currentAssistantEl.innerHTML = marked.parse(currentText);
  } else {
    currentAssistantEl.textContent = currentText;
  }
}

function finishStreaming() {
  if (currentAssistantEl) {
    currentAssistantEl.classList.remove("streaming-cursor");
    renderAssistantMessage();
  }
  currentAssistantEl = null;
  currentText = "";
  isStreaming = false;
  inputEl.disabled = false;
  sendBtn.disabled = false;
  inputEl.focus();
}

function addToolIndicator(name, done) {
  const el = document.createElement("div");
  el.className = `tool-indicator${done ? " done" : ""}`;
  el.dataset.toolName = name;

  // Map tool names to labels (mirrors agent_core TOOL_LABELS)
  const labels = {
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
    refresh_metadata: "\uD83D\uDD04 Refreshing metadata",
  };

  const label = labels[name] || name.replace(/_/g, " ");

  if (!done) {
    el.innerHTML = `<div class="spinner"></div><span>${label}</span>`;
  } else {
    el.innerHTML = `<span>${label}</span>`;
  }

  messagesEl.appendChild(el);
}

function markToolDone(nameOrId) {
  // Find the last matching tool indicator that isn't already done
  const indicators = messagesEl.querySelectorAll(
    ".tool-indicator:not(.done)"
  );
  for (const el of indicators) {
    el.classList.add("done");
    const spinner = el.querySelector(".spinner");
    if (spinner) spinner.remove();
    break; // mark only the first pending one
  }
}

function addErrorMessage(text) {
  const el = document.createElement("div");
  el.className = "message error";
  el.textContent = text;
  messagesEl.appendChild(el);
  scrollToBottom();
}

function isNearBottom() {
  const container = document.getElementById("chat-container");
  const threshold = 80; // px from bottom
  return container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
}

function scrollToBottom(force = false) {
  const container = document.getElementById("chat-container");
  if (force || isNearBottom()) {
    container.scrollTop = container.scrollHeight;
  }
}

/* ───── New conversation ───── */

function clearConversation() {
  // Reset UI
  messagesEl.innerHTML = "";
  welcomeEl.classList.remove("hidden");
  currentAssistantEl = null;
  currentText = "";
  isStreaming = false;
  turnHadMutation = false;
  inputEl.disabled = false;
  sendBtn.disabled = false;
  inputEl.value = "";
  inputEl.style.height = "auto";
  inputEl.focus();

  // Refresh stats
  fetchWelcomeStats();

  // Tell server to start fresh
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "new_conversation" }));
  }
}

/* ───── Welcome stats ───── */

const welcomeStatsEl = document.getElementById("welcome-stats");

function fetchWelcomeStats() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/status`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok || !welcomeStatsEl) return;
      const parts = [];
      if (data.papers_read != null) parts.push(`${data.papers_read} paper${data.papers_read !== 1 ? "s" : ""} read`);
      if (data.papers_queued != null) parts.push(`${data.papers_queued} in queue`);
      if (parts.length) {
        welcomeStatsEl.textContent = "\uD83D\uDCDA " + parts.join(" \u00B7 ");
      }
    })
    .catch(() => {}); // Silently ignore if unavailable
}

/* ───── Cloud sync ───── */

function triggerCloudSync() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/sync`, { method: "POST" })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) console.log("[sync] Cloud sync complete");
    })
    .catch(() => {}); // Silently ignore if sync unavailable
}

/* ───── Input handling ───── */

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage();
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Auto-resize textarea
inputEl.addEventListener("input", () => {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + "px";
});

function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  addUserMessage(text);
  ws.send(JSON.stringify({ text }));

  inputEl.value = "";
  inputEl.style.height = "auto";
  inputEl.disabled = true;
  sendBtn.disabled = true;
}

/* ───── Suggestion buttons ───── */

document.querySelectorAll(".suggestion").forEach((btn) => {
  btn.addEventListener("click", () => {
    inputEl.value = btn.dataset.text;
    sendMessage();
  });
});

/* ───── Settings modal ───── */

const settingsOverlay = document.getElementById("settings-overlay");
const settingsClose = document.getElementById("settings-close");
const settingsSave = document.getElementById("settings-save");
const settingsStatus = document.getElementById("settings-status");
const settingApiKey = document.getElementById("setting-api-key");
const settingAuthToken = document.getElementById("setting-auth-token");
const consoleLink = document.getElementById("console-link");

function openSettings() {
  settingsStatus.textContent = "";
  // Load current values
  if (window.nicolas && window.nicolas.getSettings) {
    window.nicolas.getSettings().then((s) => {
      settingApiKey.value = s.apiKey || "";
      settingAuthToken.value = s.authToken || "";
    });
  }
  settingsOverlay.classList.remove("hidden");
  settingApiKey.focus();
}

function closeSettings() {
  settingsOverlay.classList.add("hidden");
  inputEl.focus();
}

if (settingsClose) {
  settingsClose.addEventListener("click", closeSettings);
}

if (settingsOverlay) {
  settingsOverlay.addEventListener("click", (e) => {
    if (e.target === settingsOverlay) closeSettings();
  });
}

if (settingsSave) {
  settingsSave.addEventListener("click", () => {
    if (window.nicolas && window.nicolas.saveSettings) {
      window.nicolas.saveSettings({
        apiKey: settingApiKey.value.trim(),
        authToken: settingAuthToken.value.trim(),
      }).then(() => {
        settingsStatus.textContent = "Saved! Restart to apply.";
        settingsStatus.className = "setting-status success";
      });
    }
  });
}

if (consoleLink) {
  consoleLink.addEventListener("click", (e) => {
    e.preventDefault();
    if (window.nicolas && window.nicolas.openExternal) {
      window.nicolas.openExternal("https://console.anthropic.com/settings/keys");
    }
  });
}

// Esc to close settings
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsOverlay.classList.contains("hidden")) {
    closeSettings();
  }
});

/* ───── Electron bridge ───── */

if (window.nicolas) {
  // Running inside Electron
  window.nicolas.onServerReady(({ port }) => {
    connect(port);
  });

  window.nicolas.onServerError(({ message }) => {
    statusText.textContent = `Error: ${message}`;
    statusDot.className = "dot disconnected";
  });

  window.nicolas.onDeepLink((url) => {
    console.log("Deep link received:", url);
    // TODO: handle nicolas://auth?token=XXX
  });

  window.nicolas.onNewConversation(() => {
    clearConversation();
  });

  window.nicolas.onOpenSettings(() => {
    openSettings();
  });
} else {
  // Running in a regular browser (development)
  const port = new URLSearchParams(window.location.search).get("port") || 8742;
  connect(port);
}

/* ───── Tab switching ───── */

function switchTab(tabName) {
  currentTab = tabName;

  // Update tab buttons
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === tabName);
  });

  // Update tab content visibility
  const containers = {
    lab: labContainer,
    notebook: notebookContainer,
    chat: chatContainer,
  };
  for (const [name, el] of Object.entries(containers)) {
    if (el) {
      el.classList.toggle("active", name === tabName);
      el.classList.toggle("hidden", name !== tabName);
    }
  }

  // Show/hide input area for chat tab
  const inputArea = document.getElementById("input-area");
  if (inputArea) {
    inputArea.style.display = tabName === "chat" ? "" : "none";
  }
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

/* ───── Lab tab: SSE + experiment cards ───── */

function startExperimentSSE() {
  if (!serverPort || sseSource) return;

  sseSource = new EventSource(
    `http://127.0.0.1:${serverPort}/experiments/stream`
  );

  sseSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      addLabCard(data);
      notifyExperimentEvent(data);
    } catch (e) {
      console.warn("SSE parse error:", e);
    }
  };

  sseSource.onerror = () => {
    console.warn("SSE connection error, will retry...");
  };
}

function addLabCard(event) {
  const timeline = document.getElementById("lab-timeline");
  if (!timeline) return;

  if (event.type === "session_end") return; // skip session end events

  const card = document.createElement("div");
  card.className = "lab-card";

  const header = document.createElement("div");
  header.className = "lab-card-header";

  const id = document.createElement("span");
  id.className = "lab-card-id";
  id.textContent = event.ts ? new Date(event.ts).toLocaleTimeString() : "";
  header.appendChild(id);

  if (event.status) {
    const decision = document.createElement("span");
    decision.className = `lab-card-decision ${event.status}`;
    decision.textContent = event.status;
    header.appendChild(decision);
  }

  card.appendChild(header);

  // Metric
  if (event.results) {
    const metricEl = document.createElement("div");
    metricEl.className = "lab-card-metric";
    const entries = Object.entries(event.results);
    if (entries.length) {
      metricEl.textContent = entries.map(([k, v]) => `${k}=${v}`).join(", ");
      card.appendChild(metricEl);
    }
  }

  // Hypothesis
  if (event.hypothesis) {
    const hyp = document.createElement("div");
    hyp.className = "lab-card-hypothesis";
    hyp.textContent = event.hypothesis;
    card.appendChild(hyp);
  }

  // Command (for hook events)
  if (event.command && !event.hypothesis) {
    const cmd = document.createElement("div");
    cmd.className = "lab-card-hypothesis";
    cmd.textContent = event.command;
    card.appendChild(cmd);
  }

  timeline.prepend(card); // newest first
}

function updateLabStats(data) {
  const statsEl = document.getElementById("lab-stats");
  if (!statsEl || !data.experiments) return;

  const exp = data.experiments;
  statsEl.innerHTML = [
    `<div class="lab-stat"><div class="lab-stat-value">${exp.total_runs}</div><div class="lab-stat-label">Experiments</div></div>`,
    `<div class="lab-stat"><div class="lab-stat-value" style="color:var(--green)">${exp.runs_kept}</div><div class="lab-stat-label">Kept</div></div>`,
    `<div class="lab-stat"><div class="lab-stat-value" style="color:var(--error)">${exp.runs_discarded}</div><div class="lab-stat-label">Discarded</div></div>`,
    `<div class="lab-stat"><div class="lab-stat-value">${exp.active_sessions}</div><div class="lab-stat-label">Active</div></div>`,
  ].join("");
}

/* ───── Experiment notifications ───── */

let consecutiveDiscards = 0;

function notifyExperimentEvent(data) {
  if (!window.nicolas || !window.nicolas.showNotification) return;
  if (document.hasFocus()) return; // don't notify if app is focused

  if (data.type === "run_completed" || data.$schema === "distillate/run/v1") {
    const status = data.status || "";

    if (status === "keep" && data.results) {
      const metric = Object.entries(data.results)[0];
      if (metric) {
        window.nicolas.showNotification(
          "New baseline",
          `${metric[0]} improved to ${metric[1]}`
        );
      }
      consecutiveDiscards = 0;
    } else if (status === "discard") {
      consecutiveDiscards++;
      if (consecutiveDiscards >= 5) {
        window.nicolas.showNotification(
          "Agent may be stuck",
          `${consecutiveDiscards} consecutive discards`
        );
      }
    } else if (status === "crash") {
      window.nicolas.showNotification(
        "Experiment crashed",
        data.reasoning || data.hypothesis || "Check logs"
      );
      consecutiveDiscards = 0;
    }
  }
}

/* ───── Enhanced welcome stats (with experiment awareness) ───── */

const origFetchWelcomeStats = fetchWelcomeStats;
fetchWelcomeStats = function () {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/status`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok) return;

      // Papers stats
      if (welcomeStatsEl) {
        const parts = [];
        if (data.papers_read != null)
          parts.push(
            `${data.papers_read} paper${data.papers_read !== 1 ? "s" : ""} read`
          );
        if (data.papers_queued != null) parts.push(`${data.papers_queued} in queue`);
        if (parts.length) {
          welcomeStatsEl.textContent = "\uD83D\uDCDA " + parts.join(" \u00B7 ");
        }
      }

      // Experiment awareness — show tabs if experiments exist
      if (data.experiments && data.experiments.total_runs > 0) {
        hasExperiments = true;
        tabBar.classList.remove("hidden");
        updateLabStats(data);
        startExperimentSSE();

        // Default to lab tab when experiments are active
        if (data.experiments.active_sessions > 0 && currentTab === "chat") {
          switchTab("lab");
        }
      }
    })
    .catch(() => {});
};
