/* ───── Nicolas Desktop — Chat UI ───── */

let ws = null;
let serverPort = null;
let isStreaming = false;
let currentAssistantEl = null;
let currentText = "";
let turnHadMutation = false;

const messagesEl = document.getElementById("messages");
const welcomeEl = document.getElementById("welcome");
const inputEl = document.getElementById("input");
const formEl = document.getElementById("input-form");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");

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
