/* ───── Nicolas Desktop — IDE Pane Layout ───── */

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
let cachedPapers = [];
let cachedProjects = [];
let liveMetrics = {};  // Per-project live metric_update events: { projectId: [...] }
let terminalInitialized = false;
let chartLogScale = false;  // persists across re-renders
let currentTerminalProject = null;

const messagesEl = document.getElementById("messages");
const welcomeEl = document.getElementById("welcome");
const chatWelcomeEl = document.getElementById("chat-welcome");
const inputEl = document.getElementById("input");
const formEl = document.getElementById("input-form");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const modelSelect = document.getElementById("model-select");

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

/* ───── Pane layout ───── */
const sidebarLeft = document.getElementById("sidebar-left");
const sidebarRight = document.getElementById("sidebar-right");
const bottomPanel = document.getElementById("bottom-panel");
const chatArea = document.getElementById("chat-area");

/* ───── Model selector ───── */
let selectedModel = "claude-haiku-4-5-20251001";

if (modelSelect) {
  modelSelect.addEventListener("change", () => {
    selectedModel = modelSelect.value;
    // Tell the server about the model change
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "set_model", model: selectedModel }));
    }
  });
}

/* ───── Tool labels (hardcoded defaults, can be overridden by server) ───── */
let toolLabels = {
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
  scan_project: "\uD83D\uDD2C Scanning project",
  launch_experiment: "\uD83D\uDE80 Launching experiment",
  stop_experiment: "\u23F9\uFE0F Stopping experiment",
  experiment_status: "\uD83D\uDCCA Checking experiment status",
  manage_session: "\uD83C\uDFAC Managing session",
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
};

/* ───── marked.js config ───── */
if (typeof marked !== "undefined") {
  marked.setOptions({
    breaks: true,
    gfm: true,
  });

  // Custom renderer for syntax highlighting (hljs exposed via preload)
  const renderer = new marked.Renderer();
  renderer.code = function ({ text, lang }) {
    let highlighted;
    if (window.hljs && lang && window.hljs.getLanguage(lang)) {
      highlighted = window.hljs.highlight(text, { language: lang }).value;
      highlighted = `<code class="hljs language-${lang}">${highlighted}</code>`;
    } else if (window.hljs) {
      const auto = window.hljs.highlightAuto(text).value;
      highlighted = `<code class="hljs">${auto}</code>`;
    } else {
      const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      highlighted = `<code>${escaped}</code>`;
    }
    // Wrap in a container with a copy button
    const escapedRaw = text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    return `<div class="code-block-wrapper"><button class="copy-btn" data-code="${escapedRaw}">Copy</button><pre>${highlighted}</pre></div>`;
  };
  marked.use({ renderer });
}

/* ───── Sparkline SVG utility ───── */

function sparklineSvg(values, highlightIdx, opts = {}) {
  const w = opts.width || 60, h = opts.height || 16;
  const color = opts.color || "#6366f1";
  const highlightColor = opts.highlightColor || "#22c55e";
  if (!values.length) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
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
    inputEl.focus();

    // Briefly show "Reconnected" then clear
    if (wasReconnecting) {
      setTimeout(() => {
        if (statusText.textContent === "Reconnected") {
          statusText.textContent = "Connected";
        }
      }, 2000);
    }

    // Send selected model to server
    ws.send(JSON.stringify({ type: "set_model", model: selectedModel }));

    // Fetch stats, tool labels, experiments, and papers
    fetchWelcomeStats();

    // Pull latest state from cloud on connect
    triggerCloudSync();

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
      addToolIndicator(event.name, false, event.input);
      scrollToBottom();
      break;

    case "tool_done": {
      const mutatingTools = [
        "run_sync", "add_paper_to_zotero", "reprocess_paper",
        "promote_papers", "refresh_metadata", "scan_project",
        "delete_paper", "launch_experiment", "stop_experiment",
        "manage_session",
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
        refreshTabData();
        turnHadMutation = false;
      }
      // Notify if app is not focused
      if (document.hidden && window.nicolas && window.nicolas.notify) {
        window.nicolas.notify("Nicolas", "Response ready");
      }
      break;

    case "session_init":
      // Agent SDK session started — nothing to render
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
  if (chatWelcomeEl) chatWelcomeEl.classList.add("hidden");
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
  setStreamingUI(true);
}

function renderAssistantMessage() {
  if (!currentAssistantEl) return;
  if (typeof marked !== "undefined") {
    currentAssistantEl.innerHTML = marked.parse(currentText);
    // Attach copy button listeners to any new code blocks
    currentAssistantEl.querySelectorAll(".copy-btn").forEach(attachCopyHandler);
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
  setStreamingUI(false);
  inputEl.focus();
}

function addToolIndicator(name, done, input) {
  const el = document.createElement("div");
  el.className = `tool-indicator${done ? " done" : ""}`;
  el.dataset.toolName = name;

  const label = toolLabels[name] || name.replace(/_/g, " ");

  // Build dynamic subtitle from tool input
  let subtitle = "";
  if (input) {
    if (name === "search_papers" && input.query) {
      subtitle = `\u2018${input.query}\u2019`;
    } else if (name === "get_paper_details" && input.identifier) {
      subtitle = `${input.identifier}`;
    } else if (name === "suggest_next_reads" && input.count) {
      subtitle = `top ${input.count}`;
    } else if (name === "promote_papers" && input.identifiers) {
      const ids = input.identifiers;
      subtitle = ids.length === 1 ? `${ids[0]}` : `${ids.length} papers`;
    } else if (name === "reprocess_paper" && input.identifier) {
      subtitle = `${input.identifier}`;
    } else if (name === "add_paper_to_zotero" && input.identifier) {
      subtitle = `${input.identifier}`;
    } else if (name === "synthesize_across_papers" && input.question) {
      const q = input.question.length > 40 ? input.question.slice(0, 40) + "\u2026" : input.question;
      subtitle = `\u2018${q}\u2019`;
    } else if (name === "get_trending_papers" && input.limit) {
      subtitle = `top ${input.limit}`;
    } else if (name === "refresh_metadata" && input.identifier) {
      subtitle = `${input.identifier}`;
    } else if (name === "scan_project" && input.path) {
      subtitle = input.path.split("/").pop();
    } else if (name === "launch_experiment" && input.prompt) {
      const p = input.prompt.length > 40 ? input.prompt.slice(0, 40) + "\u2026" : input.prompt;
      subtitle = `\u2018${p}\u2019`;
    } else if (name === "manage_session" && input.action) {
      subtitle = `${input.action}${input.project ? ` \u2014 ${input.project}` : ""}`;
    }
    // Claude Code built-in tools
    else if ((name === "Read" || name === "Edit" || name === "Write") && input.file_path) {
      subtitle = input.file_path.split("/").pop();
    } else if (name === "Bash" && input.command) {
      const cmd = input.command.length > 60 ? input.command.slice(0, 60) + "\u2026" : input.command;
      subtitle = cmd;
    } else if (name === "Glob" && input.pattern) {
      subtitle = input.pattern;
    } else if (name === "Grep" && input.pattern) {
      subtitle = `\u2018${input.pattern}\u2019`;
    } else if (name === "WebSearch" && input.query) {
      subtitle = `\u2018${input.query}\u2019`;
    } else if (name === "WebFetch" && input.url) {
      try { subtitle = new URL(input.url).hostname; } catch { subtitle = input.url.slice(0, 40); }
    } else if (name === "Agent" && input.description) {
      subtitle = input.description;
    }
  }

  const subtitleHtml = subtitle ? `<span class="tool-subtitle">${subtitle}</span>` : "";

  if (!done) {
    el.innerHTML = `<div class="spinner"></div><span>${label}</span>${subtitleHtml}`;
  } else {
    el.innerHTML = `<span>${label}</span>${subtitleHtml}`;
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

  const span = document.createElement("span");
  span.textContent = text;
  el.appendChild(span);

  // Add retry button if we have a last message to retry
  if (lastUserMessage) {
    const retryBtn = document.createElement("button");
    retryBtn.className = "retry-btn";
    retryBtn.textContent = "Retry";
    retryBtn.addEventListener("click", () => {
      el.remove();
      inputEl.value = lastUserMessage;
      sendMessage();
    });
    el.appendChild(retryBtn);
  }

  messagesEl.appendChild(el);
  scrollToBottom();
}

function isNearBottom() {
  if (!chatArea) return true;
  const threshold = 80;
  return chatArea.scrollHeight - chatArea.scrollTop - chatArea.clientHeight < threshold;
}

function scrollToBottom(force = false) {
  if (!chatArea) return;
  if (force || isNearBottom()) {
    chatArea.scrollTop = chatArea.scrollHeight;
  }
}

/* ───── Code block copy ───── */

function attachCopyHandler(btn) {
  if (btn.dataset.bound) return;
  btn.dataset.bound = "1";
  btn.addEventListener("click", () => {
    const code = btn.dataset.code
      .replace(/&amp;/g, "&").replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">").replace(/&quot;/g, '"');
    navigator.clipboard.writeText(code).then(() => {
      btn.textContent = "Copied!";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.textContent = "Copy";
        btn.classList.remove("copied");
      }, 1500);
    });
  });
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
  lastUserMessage = "";
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

/* ───── Welcome stats + initial data load ───── */

const welcomeStatsEl = document.getElementById("welcome-stats");

function fetchWelcomeStats() {
  if (!serverPort) return;

  // Single /status call for welcome screen + tool labels
  fetch(`http://127.0.0.1:${serverPort}/status`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok) return;

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

      // Merge server-provided tool labels
      if (data.tool_labels && typeof data.tool_labels === "object") {
        toolLabels = { ...toolLabels, ...data.tool_labels };
      }

      // Live tab stats from /status (quick counts)
      // Note: fetchExperimentsList() is called separately below — no need to duplicate here
    })
    .catch(() => {});

  // Load experiments from dedicated endpoint (owns Live tab empty/content state)
  fetchExperimentsList();

  // Prefetch papers for Papers tab
  fetchPapersData();
}

/* ───── Cloud sync ───── */

function triggerCloudSync() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/sync`, { method: "POST" })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) console.log("[sync] Cloud sync complete");
    })
    .catch(() => {}); // Sync is background, silent failure is fine
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
  fetchExperimentsList();
  fetchPapersData();
}

/* ───── Input handling ───── */

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  sendMessage();
});

sendBtn.addEventListener("click", (e) => {
  if (isStreaming) {
    e.preventDefault();
    e.stopPropagation();
    stopGeneration();
  }
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
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + "px";
});

const sendIcon = document.getElementById("send-icon");
const stopIcon = document.getElementById("stop-icon");

function setStreamingUI(streaming) {
  if (streaming) {
    sendBtn.classList.add("streaming");
    sendBtn.type = "button"; // prevent form submit
    sendBtn.disabled = false;
    if (sendIcon) sendIcon.classList.add("hidden");
    if (stopIcon) stopIcon.classList.remove("hidden");
  } else {
    sendBtn.classList.remove("streaming");
    sendBtn.type = "submit";
    if (sendIcon) sendIcon.classList.remove("hidden");
    if (stopIcon) stopIcon.classList.add("hidden");
  }
}

function stopGeneration() {
  if (!isStreaming) return;

  // Finish whatever partial text we have
  finishStreaming();

  // Add a concise system-style message so the agent knows to stop
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      text: "stopped by user -- what should i do instead?",
    }));
    // Immediately start streaming the new response
    isStreaming = true;
    inputEl.disabled = true;
    setStreamingUI(true);
  }
}

function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  lastUserMessage = text;
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
const settingsOnboarding = document.getElementById("settings-onboarding");

function openSettings(isOnboarding = false) {
  settingsStatus.textContent = "";

  // Show/hide onboarding banner
  if (settingsOnboarding) {
    settingsOnboarding.classList.toggle("hidden", !isOnboarding);
  }

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
    const apiKey = settingApiKey.value.trim();

    // Validate API key format
    if (apiKey && !apiKey.startsWith("sk-ant-")) {
      settingsStatus.textContent = "API key should start with sk-ant-...";
      settingsStatus.className = "setting-status";
      return;
    }

    if (window.nicolas && window.nicolas.saveSettings) {
      window.nicolas.saveSettings({
        apiKey,
        authToken: settingAuthToken.value.trim(),
      }).then(() => {
        settingsStatus.textContent = "Saved! Reconnecting\u2026";
        settingsStatus.className = "setting-status success";
        // Auto-reconnect instead of requiring restart
        closeSettings();
        if (ws) {
          ws.close();
          // onclose handler will trigger reconnect with backoff
        }
      }).catch((err) => {
        settingsStatus.textContent = `Failed to save: ${err.message || "unknown error"}`;
        settingsStatus.className = "setting-status";
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

// Export/Import state buttons
const exportBtn = document.getElementById("settings-export");
const importBtn = document.getElementById("settings-import");

if (exportBtn) {
  exportBtn.addEventListener("click", async () => {
    if (!window.nicolas || !window.nicolas.exportState) return;
    exportBtn.disabled = true;
    exportBtn.textContent = "Exporting...";
    try {
      const result = await window.nicolas.exportState();
      if (result.ok) {
        settingsStatus.textContent = `Exported to ${result.path}`;
        settingsStatus.className = "setting-status success";
      } else if (result.reason !== "canceled") {
        settingsStatus.textContent = `Export failed: ${result.reason}`;
        settingsStatus.className = "setting-status";
      }
    } catch (err) {
      settingsStatus.textContent = `Export failed: ${err.message}`;
      settingsStatus.className = "setting-status";
    }
    exportBtn.textContent = "Export State";
    exportBtn.disabled = false;
  });
}

if (importBtn) {
  importBtn.addEventListener("click", async () => {
    if (!window.nicolas || !window.nicolas.importState) return;
    importBtn.disabled = true;
    importBtn.textContent = "Importing...";
    try {
      const result = await window.nicolas.importState();
      if (result.ok) {
        settingsStatus.textContent = `Imported ${result.papers} papers. Refreshing...`;
        settingsStatus.className = "setting-status success";
        refreshTabData();
      } else if (result.reason !== "canceled") {
        settingsStatus.textContent = `Import failed: ${result.reason}`;
        settingsStatus.className = "setting-status";
      }
    } catch (err) {
      settingsStatus.textContent = `Import failed: ${err.message}`;
      settingsStatus.className = "setting-status";
    }
    importBtn.textContent = "Import State";
    importBtn.disabled = false;
  });
}

// Esc to close settings or stop generation
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!settingsOverlay.classList.contains("hidden")) {
      closeSettings();
    } else if (isStreaming) {
      stopGeneration();
    }
  }
});

/* ───── Message search (Cmd+F) ───── */

const searchBar = document.getElementById("search-bar");
const searchInput = document.getElementById("search-input");
const searchCount = document.getElementById("search-count");
const searchPrev = document.getElementById("search-prev");
const searchNext = document.getElementById("search-next");
const searchCloseBtn = document.getElementById("search-close");

let searchMatches = [];
let searchCurrentIdx = -1;

function openSearch() {
  searchBar.classList.remove("hidden");
  searchInput.focus();
  searchInput.select();
}

function closeSearch() {
  searchBar.classList.add("hidden");
  searchInput.value = "";
  clearSearchHighlights();
  searchMatches = [];
  searchCurrentIdx = -1;
  searchCount.textContent = "";
}

function clearSearchHighlights() {
  messagesEl.querySelectorAll("mark").forEach((mark) => {
    const parent = mark.parentNode;
    parent.replaceChild(document.createTextNode(mark.textContent), mark);
    parent.normalize();
  });
}

function performSearch() {
  clearSearchHighlights();
  searchMatches = [];
  searchCurrentIdx = -1;

  const query = searchInput.value.trim().toLowerCase();
  if (!query) {
    searchCount.textContent = "";
    return;
  }

  // Walk text nodes in messages and wrap matches with <mark>
  const messages = messagesEl.querySelectorAll(".message");
  messages.forEach((msg) => {
    highlightTextInNode(msg, query);
  });

  searchMatches = Array.from(messagesEl.querySelectorAll("mark"));
  if (searchMatches.length > 0) {
    searchCurrentIdx = 0;
    searchMatches[0].classList.add("current");
    searchMatches[0].scrollIntoView({ block: "center", behavior: "smooth" });
    searchCount.textContent = `1 of ${searchMatches.length}`;
  } else {
    searchCount.textContent = "No matches";
  }
}

function highlightTextInNode(node, query) {
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, null);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);

  for (const textNode of textNodes) {
    const text = textNode.textContent.toLowerCase();
    const idx = text.indexOf(query);
    if (idx === -1) continue;

    const before = textNode.textContent.slice(0, idx);
    const match = textNode.textContent.slice(idx, idx + query.length);
    const after = textNode.textContent.slice(idx + query.length);

    const mark = document.createElement("mark");
    mark.textContent = match;

    const parent = textNode.parentNode;
    if (before) parent.insertBefore(document.createTextNode(before), textNode);
    parent.insertBefore(mark, textNode);
    if (after) parent.insertBefore(document.createTextNode(after), textNode);
    parent.removeChild(textNode);

    // Recursively search remaining text in the after node
    if (after.toLowerCase().includes(query)) {
      highlightTextInNode(mark.nextSibling.parentNode === parent ? parent : mark.parentNode, query);
    }
  }
}

function navigateSearch(direction) {
  if (searchMatches.length === 0) return;
  searchMatches[searchCurrentIdx].classList.remove("current");
  searchCurrentIdx = (searchCurrentIdx + direction + searchMatches.length) % searchMatches.length;
  searchMatches[searchCurrentIdx].classList.add("current");
  searchMatches[searchCurrentIdx].scrollIntoView({ block: "center", behavior: "smooth" });
  searchCount.textContent = `${searchCurrentIdx + 1} of ${searchMatches.length}`;
}

if (searchInput) {
  searchInput.addEventListener("input", performSearch);
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      navigateSearch(e.shiftKey ? -1 : 1);
    }
    if (e.key === "Escape") {
      closeSearch();
    }
  });
}

if (searchPrev) searchPrev.addEventListener("click", () => navigateSearch(-1));
if (searchNext) searchNext.addEventListener("click", () => navigateSearch(1));
if (searchCloseBtn) searchCloseBtn.addEventListener("click", closeSearch);

// Cmd+F / Ctrl+F to open search
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "f") {
    e.preventDefault();
    openSearch();
  }
});

/* ───── Electron bridge ───── */

if (window.nicolas) {
  // Running inside Electron
  window.nicolas.onUpdateProgress(({ message }) => {
    statusDot.className = "dot updating";
    statusText.textContent = message;
  });

  window.nicolas.onServerReady(({ port }) => {
    connect(port);
  });

  window.nicolas.onServerError(({ message }) => {
    statusText.textContent = `Error: ${message}`;
    statusDot.className = "dot disconnected";
  });

  window.nicolas.onDeepLink((url) => {
    console.log("Deep link received:", url);
    handleDeepLink(url);
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

/* ───── Deep link handling ───── */

function handleDeepLink(url) {
  try {
    const parsed = new URL(url);
    // distillate://auth?token=XXX
    if (parsed.hostname === "auth" || parsed.pathname === "//auth" || parsed.pathname === "/auth") {
      const token = parsed.searchParams.get("token");
      if (token && window.nicolas && window.nicolas.saveSettings) {
        window.nicolas.saveSettings({ authToken: token }).then(() => {
          statusText.textContent = "Cloud authenticated!";
          setTimeout(() => {
            if (statusText.textContent === "Cloud authenticated!") {
              statusText.textContent = "Connected";
            }
          }, 3000);
          // Reconnect so Python server picks up the new token from env
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

/* ───── Pane management ───── */

function togglePane(paneName) {
  const paneMap = {
    "sidebar-left": sidebarLeft,
    "sidebar-right": sidebarRight,
    "bottom-panel": bottomPanel,
  };
  const pane = paneMap[paneName];
  if (!pane) return;

  pane.classList.toggle("collapsed");

  // Update activity bar button
  const btn = document.querySelector(`.activity-btn[data-pane="${paneName}"]`);
  if (btn) {
    btn.classList.toggle("active", !pane.classList.contains("collapsed"));
    // Clear notification badge when opening
    if (!pane.classList.contains("collapsed")) {
      btn.classList.remove("has-notification");
    }
  }

  // Persist layout
  saveLayoutState();
}

// Activity bar buttons
document.querySelectorAll(".activity-btn[data-pane]").forEach((btn) => {
  btn.addEventListener("click", () => togglePane(btn.dataset.pane));
});

// Settings button in activity bar
const activitySettings = document.getElementById("activity-settings");
if (activitySettings) {
  activitySettings.addEventListener("click", () => openSettings());
}

// Editor tabs (Control Panel / Session / Notebook)
const editorViews = ["control-panel", "session", "results", "prompt-editor"];

function switchEditorTab(viewName, { skipSessionAttach = false } = {}) {
  document.querySelectorAll(".editor-tab").forEach((t) => t.classList.remove("active"));
  document.querySelector(`.editor-tab[data-view="${viewName}"]`)?.classList.add("active");

  for (const v of editorViews) {
    const el = document.getElementById(`${v}-view`);
    if (el) el.classList.toggle("hidden", v !== viewName);
  }

  if (viewName === "results" && currentProjectId) loadResults(currentProjectId);
  if (viewName === "prompt-editor" && currentProjectId) loadPromptEditor(currentProjectId);
  if (viewName === "session" && !skipSessionAttach) {
    if (currentProjectId) {
      showSessionTerminal(currentProjectId);
    } else {
      showSessionEmpty();
    }
    // Clear notification dot
    const sessionTab = document.querySelector('.editor-tab[data-view="session"]');
    if (sessionTab) sessionTab.classList.remove("has-update");
  }
}

document.querySelectorAll(".editor-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchEditorTab(tab.dataset.view));
});

// Session tab — xterm.js terminal

let _termReadyPromise = null;

function ensureTerminalReady() {
  if (terminalInitialized) return Promise.resolve(true);
  if (_termReadyPromise) return _termReadyPromise; // coalesce concurrent calls

  _termReadyPromise = new Promise((resolve) => {
    if (!window.xtermBridge) { resolve(false); return; }
    let attempts = 0;
    function tryInit() {
      const container = document.getElementById("xterm-container");
      if (!container || container.classList.contains("hidden") || container.offsetHeight === 0) {
        if (++attempts < 20) { requestAnimationFrame(tryInit); return; }
        _termReadyPromise = null; resolve(false); return;
      }
      const ok = window.xtermBridge.init("xterm-container");
      if (ok) {
        terminalInitialized = true;
        window.xtermBridge.onData((data) => {
          if (currentTerminalProject && window.nicolas)
            window.nicolas.terminalInput(currentTerminalProject, data);
        });
      }
      _termReadyPromise = null; resolve(ok);
    }
    requestAnimationFrame(tryInit);
  });
  return _termReadyPromise;
}

let currentTerminalSession = null;

async function attachToTerminalSession(projectId, sessionName) {
  if (!window.nicolas || !window.xtermBridge) return;

  // Skip if already attached to this exact session
  if (currentTerminalProject === projectId && currentTerminalSession === sessionName) return;

  // Detach previous
  if (currentTerminalProject && currentTerminalProject !== projectId) {
    window.nicolas.terminalDetach(currentTerminalProject);
  }

  const ready = await ensureTerminalReady();
  if (!ready) { console.warn("[terminal] init failed"); return; }

  window.xtermBridge.clear();
  currentTerminalProject = projectId;
  currentTerminalSession = sessionName;
  window.xtermBridge.fit();

  const dims = window.xtermBridge.getDimensions();
  window.nicolas.terminalAttach(projectId, sessionName, dims.cols, dims.rows);
}

function detachTerminal() {
  if (currentTerminalProject && window.nicolas) {
    window.nicolas.terminalDetach(currentTerminalProject);
  }
  currentTerminalProject = null;
  currentTerminalSession = null;
}

function showSessionTerminal(projectId) {
  const emptyEl = document.getElementById("session-empty");
  const xtermEl = document.getElementById("xterm-container");

  // Find the project and its active tmux session
  const proj = cachedProjects.find((p) => p.id === projectId);
  if (!proj || proj.active_sessions === 0) {
    showSessionEmpty();
    return;
  }

  // Get session name from project data
  const sessions = proj.sessions || {};
  const activeSession = Object.values(sessions).find((s) => s.tmux_session);
  const sessionName = activeSession?.tmux_session;
  if (!sessionName) {
    showSessionEmpty();
    return;
  }

  if (emptyEl) emptyEl.classList.add("hidden");
  if (xtermEl) xtermEl.classList.remove("hidden");

  attachToTerminalSession(projectId, sessionName);
}

function showSessionEmpty() {
  const xtermEl = document.getElementById("xterm-container");
  const emptyEl = document.getElementById("session-empty");
  if (emptyEl) emptyEl.classList.remove("hidden");
  if (xtermEl) xtermEl.classList.add("hidden");
  detachTerminal();
}

// Receive PTY data and pipe to xterm
if (window.nicolas) {
  window.nicolas.onTerminalData(({ projectId, data }) => {
    if (projectId === currentTerminalProject && window.xtermBridge) {
      window.xtermBridge.write(data);
    }
  });

  window.nicolas.onTerminalExit(({ projectId }) => {
    if (projectId === currentTerminalProject && window.xtermBridge) {
      window.xtermBridge.write("\r\n\x1b[2m--- Session ended ---\x1b[0m\r\n");
      currentTerminalProject = null;
      currentTerminalSession = null;
    }
  });
}

// ResizeObserver for terminal fit
const xtermContainerEl = document.getElementById("xterm-container");
if (xtermContainerEl) {
  new ResizeObserver(() => {
    if (window.xtermBridge && terminalInitialized) {
      window.xtermBridge.fit();
      if (currentTerminalProject && window.nicolas) {
        const dims = window.xtermBridge.getDimensions();
        window.nicolas.terminalResize(currentTerminalProject, dims.cols, dims.rows);
      }
    }
  }).observe(xtermContainerEl);
}

// Keyboard shortcuts
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "b") {
    e.preventDefault();
    togglePane("sidebar-left");
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "j") {
    e.preventDefault();
    togglePane("bottom-panel");
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "r") {
    e.preventDefault();
    reloadCurrentProject();
    fetchPapersData();
  }
  // Cmd+1/2/3/4 to switch editor tabs
  if ((e.metaKey || e.ctrlKey) && e.key >= "1" && e.key <= "4") {
    e.preventDefault();
    const tabs = ["control-panel", "session", "results", "prompt-editor"];
    switchEditorTab(tabs[parseInt(e.key) - 1]);
  }
  // Escape to deselect experiment
  if (e.key === "Escape" && !e.metaKey && !e.ctrlKey) {
    const settingsOverlay = document.getElementById("settings-overlay");
    if (settingsOverlay && !settingsOverlay.classList.contains("hidden")) return; // let settings handle it
    if (currentProjectId) {
      e.preventDefault();
      currentProjectId = null;
      const detailEl = document.getElementById("experiment-detail");
      if (detailEl) { detailEl.classList.add("hidden"); detailEl.innerHTML = ""; }
      welcomeEl?.classList.remove("hidden");
      const tabLabel = document.getElementById("editor-tabs-project-name");
      if (tabLabel) tabLabel.textContent = "";
      document.querySelectorAll("#experiments-sidebar .sidebar-item").forEach((el) => el.classList.remove("active"));
      switchEditorTab("control-panel");
    }
  }
});

/* ───── Resize handles ───── */

function initResize(handleId, target, prop, direction) {
  const handle = document.getElementById(handleId);
  if (!handle || !target) return;

  let startPos = 0;
  let startSize = 0;

  function onMouseDown(e) {
    e.preventDefault();
    startPos = direction === "horizontal" ? e.clientY : e.clientX;
    startSize = direction === "horizontal" ? target.offsetHeight : target.offsetWidth;
    handle.classList.add("dragging");
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    document.body.style.cursor = direction === "horizontal" ? "row-resize" : "col-resize";
    document.body.style.userSelect = "none";
  }

  function onMouseMove(e) {
    const delta = direction === "horizontal"
      ? startPos - e.clientY // inverted: drag up = bigger
      : (prop === "right" ? startPos - e.clientX : e.clientX - startPos);
    const maxSize = direction === "horizontal" ? window.innerHeight * 0.6 : window.innerWidth * 0.4;
    const newSize = Math.max(120, Math.min(startSize + delta, maxSize));
    target.style[direction === "horizontal" ? "height" : "width"] = newSize + "px";
  }

  function onMouseUp() {
    handle.classList.remove("dragging");
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    saveLayoutState();
  }

  handle.addEventListener("mousedown", onMouseDown);

  // Double-click to collapse
  handle.addEventListener("dblclick", () => {
    const paneMap = { "resize-left": "sidebar-left", "resize-right": "sidebar-right", "resize-bottom": "bottom-panel" };
    const pane = paneMap[handleId];
    if (pane) togglePane(pane);
  });
}

initResize("resize-left", sidebarLeft, "left", "vertical");
initResize("resize-right", sidebarRight, "right", "vertical");
initResize("resize-bottom", bottomPanel, "bottom", "horizontal");

/* ───── Layout state persistence ───── */

function saveLayoutState() {
  try {
    const state = {
      leftCollapsed: sidebarLeft?.classList.contains("collapsed") || false,
      rightCollapsed: sidebarRight?.classList.contains("collapsed") || false,
      bottomCollapsed: bottomPanel?.classList.contains("collapsed") || false,
      leftWidth: sidebarLeft?.offsetWidth,
      rightWidth: sidebarRight?.offsetWidth,
      bottomHeight: bottomPanel?.offsetHeight,
    };
    localStorage.setItem("distillate-layout", JSON.stringify(state));
  } catch {}
}

function restoreLayoutState() {
  try {
    const state = JSON.parse(localStorage.getItem("distillate-layout"));
    if (!state) return;
    if (state.leftCollapsed) { sidebarLeft?.classList.add("collapsed"); document.querySelector('.activity-btn[data-pane="sidebar-left"]')?.classList.remove("active"); }
    if (state.rightCollapsed) { sidebarRight?.classList.add("collapsed"); document.querySelector('.activity-btn[data-pane="sidebar-right"]')?.classList.remove("active"); }
    if (state.bottomCollapsed) { bottomPanel?.classList.add("collapsed"); document.querySelector('.activity-btn[data-pane="bottom-panel"]')?.classList.remove("active"); }
    if (state.leftWidth && sidebarLeft) sidebarLeft.style.width = state.leftWidth + "px";
    if (state.rightWidth && sidebarRight) sidebarRight.style.width = state.rightWidth + "px";
    if (state.bottomHeight && bottomPanel) bottomPanel.style.height = state.bottomHeight + "px";
  } catch {}
}

restoreLayoutState();

/* ───── refreshTabData replacement ───── */

/* ───── Live tab: SSE + experiment cards ───── */

function startExperimentSSE() {
  if (!serverPort || sseSource) return;

  sseSource = new EventSource(
    `http://127.0.0.1:${serverPort}/experiments/stream`
  );

  sseSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleSSEEvent(data);
    } catch (e) {
      console.warn("SSE parse error:", e);
    }
  };

  sseSource.onerror = () => {
    console.warn("SSE connection error, will retry...");
  };
}

function stopExperimentSSE() {
  if (sseSource) { sseSource.close(); sseSource = null; }
}

window.addEventListener("beforeunload", () => {
  stopExperimentSSE();
  detachTerminal();
});

function handleSSEEvent(data) {
  // --- session_completed: auto-rescan finished, refresh everything ---
  if (data.type === "session_completed") {
    fetchExperimentsList();
    // OS notification
    if (window.nicolas?.notify && !document.hasFocus()) {
      let msg = `${data.new_runs} new run(s) recorded`;
      if (data.best_metric) {
        const [k, v] = Object.entries(data.best_metric)[0];
        msg += ` — best ${k}: ${v}`;
      }
      window.nicolas.notify("Session finished", msg);
    }
    return;
  }

  // --- run_update: new line in runs.jsonl, inject into cached data ---
  if (data.type === "run_update" && data.run) {
    const proj = cachedProjects.find((p) => p.id === data.project_id);
    if (proj) {
      // Build a run summary compatible with what /experiments/list returns
      const run = data.run;
      const results = run.results || {};
      let keyMetric = "";
      for (const k of ["accuracy", "exact_match", "test_accuracy", "val_accuracy",
                        "best_val_acc", "f1", "loss"]) {
        if (k in results) { keyMetric = `${k}=${results[k]}`; break; }
      }
      if (!keyMetric) {
        const first = Object.entries(results).find(([, v]) => typeof v === "number");
        if (first) keyMetric = `${first[0]}=${first[1]}`;
      }

      const runSummary = {
        id: run.id || "",
        name: run.id || "",
        status: run.status || "",
        decision: run.status || "",
        key_metric: keyMetric,
        results: Object.fromEntries(
          Object.entries(results).filter(([, v]) => typeof v === "number")
        ),
        hyperparameters: run.hyperparameters || {},
        hypothesis: run.hypothesis || "",
        reasoning: run.reasoning || "",
        baseline_comparison: run.baseline_comparison || null,
        started_at: run.timestamp || "",
        duration_minutes: 0,
        tags: [],
      };

      // Avoid duplicates
      if (!proj.runs) proj.runs = [];
      if (!proj.runs.some((r) => r.id === runSummary.id)) {
        proj.runs.push(runSummary);
        proj.run_count = proj.runs.length;
      }

      // Re-render if this project is currently displayed
      if (currentProjectId === data.project_id) {
        renderProjectDetail(data.project_id);
      }
      // Update sidebar counts
      renderProjectsList(cachedProjects);
    }
    // Also notify
    notifyExperimentEvent(data.run);
    return;
  }

  // --- session_continued: auto-continue launched a new session ---
  if (data.type === "session_continued") {
    fetchExperimentsList();
    if (window.nicolas?.notify && !document.hasFocus()) {
      const remaining = data.queue_remaining > 0 ? ` (${data.queue_remaining} queued)` : "";
      window.nicolas.notify("Auto-continuing", `New session: ${data.tmux_session}${remaining}`);
    }
    return;
  }

  // --- metric_update: live per-epoch metrics ---
  if (data.type === "metric_update") {
    const pid = data.project_id;
    if (!liveMetrics[pid]) liveMetrics[pid] = [];
    liveMetrics[pid].push(data);
    if (liveMetrics[pid].length > 1000) liveMetrics[pid].splice(0, liveMetrics[pid].length - 1000);

    // Re-render chart if this project is currently displayed
    if (currentProjectId === pid) {
      const canvas = document.querySelector("#experiment-detail .metric-chart-canvas");
      if (canvas) {
        const proj = cachedProjects.find((p) => p.id === pid);
        if (proj) {
          // Read the activeMetric from the chart title (contains "metricName arrow")
          const titleEl = document.querySelector("#experiment-detail .metric-chart-title");
          const activeMetric = titleEl ? titleEl.textContent.replace(/\s*[\u2191\u2193]\s*$/, "") : "";
          if (activeMetric) {
            renderMetricChart(canvas, proj.runs, activeMetric, liveMetrics[pid]);
          }
        }
      }
    }

    addLiveCard(data);
    return;
  }

  // --- goal_reached: experiment hit its goal threshold ---
  if (data.type === "goal_reached") {
    fetchExperimentsList();
    // Success banner
    const banner = document.createElement("div");
    banner.className = "goal-reached-banner";
    banner.innerHTML = `<strong>Goal reached!</strong> ${data.metric} = ${data.value} (target: ${data.target})`;
    document.getElementById("experiment-detail")?.prepend(banner);
    setTimeout(() => banner.remove(), 10000);
    // OS notification
    if (window.nicolas?.notify) {
      window.nicolas.notify("Goal reached!", `${data.metric} = ${data.value} (target: ${data.target})`);
    }
    return;
  }

  // --- campaign_run_started: new session in campaign ---
  if (data.type === "campaign_run_started") {
    fetchExperimentsList();
    if (window.nicolas?.notify && !document.hasFocus()) {
      window.nicolas.notify("Campaign", `Session ${data.sessions_launched} started (${data.budget_remaining} remaining)`);
    }
    return;
  }

  // --- campaign_completed: campaign finished ---
  if (data.type === "campaign_completed") {
    fetchExperimentsList();
    if (window.nicolas?.notify) {
      window.nicolas.notify("Campaign finished", `${data.sessions_launched} sessions, reason: ${data.stop_reason}`);
    }
    return;
  }

  // --- session_end: raw session end event ---
  if (data.type === "session_end") {
    fetchExperimentsList();
    if (!document.hasFocus() && window.nicolas?.notify) {
      window.nicolas.notify("Session finished", `Experiment session ended for ${data.project_name || data.project_id || "experiment"}`);
    }
    return;
  }

  // --- Default: existing live card + notification behavior ---
  addLiveCard(data);
  notifyExperimentEvent(data);
}

function addLiveCard(event) {
  // Append live events to the experiment detail runs grid
  const detailEl = document.getElementById("experiment-detail");
  const timeline = detailEl?.querySelector(".exp-runs-grid");
  if (!timeline) return;

  if (event.type === "session_end") return; // skip raw session end events

  const card = document.createElement("div");
  card.className = "exp-run-card";

  const header = document.createElement("div");
  header.className = "exp-run-header";

  const id = document.createElement("span");
  id.className = "exp-run-name";
  id.textContent = event.ts ? new Date(event.ts).toLocaleTimeString() : "";
  header.appendChild(id);

  if (event.status) {
    const decision = document.createElement("span");
    decision.className = `exp-run-decision ${event.status}`;
    decision.textContent = event.status;
    header.appendChild(decision);
  }

  card.appendChild(header);

  // Metric
  if (event.results) {
    const metricEl = document.createElement("div");
    metricEl.className = "exp-run-metric";
    const entries = Object.entries(event.results);
    if (entries.length) {
      metricEl.textContent = entries.map(([k, v]) => `${k}=${v}`).join(", ");
      card.appendChild(metricEl);
    }
  }

  // Hypothesis
  if (event.hypothesis) {
    const hyp = document.createElement("div");
    hyp.className = "exp-run-meta";
    hyp.textContent = event.hypothesis;
    card.appendChild(hyp);
  }

  // Command (for hook events)
  if (event.command && !event.hypothesis) {
    const cmd = document.createElement("div");
    cmd.className = "exp-run-meta";
    cmd.textContent = event.command;
    card.appendChild(cmd);
  }

  timeline.prepend(card); // newest first
}

/* ───── Papers sidebar ───── */

const papersSidebarEl = document.getElementById("papers-sidebar");
const papersFiltersEl = document.getElementById("papers-sidebar-filters");
const papersInsightsPanel = document.getElementById("papers-insights-panel");
const papersCountEl = document.getElementById("papers-count");
const papersInsightsToggle = document.getElementById("papers-insights-toggle");
let papersShowInsights = false;

if (papersInsightsToggle) {
  papersInsightsToggle.addEventListener("click", () => {
    papersShowInsights = !papersShowInsights;
    papersInsightsToggle.classList.toggle("active", papersShowInsights);
    if (papersSidebarEl) papersSidebarEl.classList.toggle("hidden", papersShowInsights);
    if (papersFiltersEl) papersFiltersEl.classList.toggle("hidden", papersShowInsights);
    if (papersInsightsPanel) papersInsightsPanel.classList.toggle("hidden", !papersShowInsights);
    if (papersShowInsights) fetchInsightsData();
  });
}

let papersFirstLoad = true;

function fetchPapersData() {
  if (!serverPort) return;
  if (papersFirstLoad && papersSidebarEl) {
    papersSidebarEl.innerHTML = '<div class="sidebar-skeleton">' +
      '<div class="skeleton-item"></div>'.repeat(3) + '</div>';
  }
  fetch(`http://127.0.0.1:${serverPort}/papers`)
    .then((r) => r.json())
    .then((data) => {
      papersFirstLoad = false;
      if (!data.ok) return;
      renderPapersList(data.papers || []);
    })
    .catch(() => { papersFirstLoad = false; });
}

function fetchInsightsData() {
  if (!serverPort || !papersInsightsPanel) return;
  papersInsightsPanel.innerHTML = '<div class="insights-empty">Loading insights...</div>';

  fetch(`http://127.0.0.1:${serverPort}/report`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok) return;
      if (data.empty) {
        papersInsightsPanel.innerHTML = '<div class="insights-empty">No processed papers yet. Read some papers first!</div>';
        return;
      }
      renderInsights(data);
    })
    .catch(() => {
      papersInsightsPanel.innerHTML = '<div class="insights-empty">Could not load insights.</div>';
    });
}

function renderInsights(data) {
  if (!papersInsightsPanel) return;
  papersInsightsPanel.innerHTML = "";

  const grid = document.createElement("div");
  grid.className = "insights-grid";

  // Lifetime stats (full width)
  if (data.lifetime) {
    const card = document.createElement("div");
    card.className = "insights-card full-width";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Lifetime";
    card.appendChild(title);

    const row = document.createElement("div");
    row.className = "insights-lifetime";
    const stats = [
      { value: data.lifetime.papers, label: "Papers" },
      { value: data.lifetime.pages.toLocaleString(), label: "Pages" },
      { value: data.lifetime.words.toLocaleString(), label: "Words" },
      { value: `${data.lifetime.avg_engagement}%`, label: "Avg Engagement" },
    ];
    for (const s of stats) {
      const stat = document.createElement("div");
      stat.className = "insights-lifetime-stat";
      stat.innerHTML = `<div class="insights-lifetime-value">${s.value}</div><div class="insights-lifetime-label">${s.label}</div>`;
      row.appendChild(stat);
    }
    card.appendChild(row);
    grid.appendChild(card);
  }

  // Reading velocity
  if (data.velocity && data.velocity.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Reading Velocity (8 weeks)";
    card.appendChild(title);

    const maxCount = Math.max(...data.velocity.map((v) => v.count));
    for (const week of [...data.velocity].reverse()) {
      const row = document.createElement("div");
      row.className = "insights-bar-row";
      const label = document.createElement("span");
      label.className = "insights-bar-label";
      const d = new Date(week.week);
      label.textContent = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      row.appendChild(label);

      const bar = document.createElement("div");
      bar.className = "insights-bar";
      const fill = document.createElement("div");
      fill.className = "insights-bar-fill";
      fill.style.width = `${(week.count / maxCount) * 100}%`;
      bar.appendChild(fill);
      row.appendChild(bar);

      const count = document.createElement("span");
      count.className = "insights-bar-count";
      count.textContent = week.count;
      row.appendChild(count);

      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  // Engagement distribution
  if (data.engagement && data.engagement.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Engagement Distribution";
    card.appendChild(title);

    const maxCount = Math.max(...data.engagement.map((e) => e.count));
    for (const bucket of data.engagement) {
      const row = document.createElement("div");
      row.className = "insights-bar-row";
      const label = document.createElement("span");
      label.className = "insights-bar-label";
      label.textContent = bucket.range;
      row.appendChild(label);

      const bar = document.createElement("div");
      bar.className = "insights-bar";
      const fill = document.createElement("div");
      fill.className = "insights-bar-fill";
      fill.style.width = maxCount > 0 ? `${(bucket.count / maxCount) * 100}%` : "0%";
      bar.appendChild(fill);
      row.appendChild(bar);

      const count = document.createElement("span");
      count.className = "insights-bar-count";
      count.textContent = bucket.count;
      row.appendChild(count);

      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  // Top topics
  if (data.topics && data.topics.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Top Topics";
    card.appendChild(title);

    for (const [i, topic] of data.topics.entries()) {
      const row = document.createElement("div");
      row.className = "insights-list-item";
      row.innerHTML = `<span class="insights-list-rank">${i + 1}.</span><span class="insights-list-name">${escapeHtml(topic.topic)}</span><span class="insights-list-count">${topic.count}</span>`;
      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  // Most-cited papers
  if (data.cited_papers && data.cited_papers.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Most-Cited Papers Read";
    card.appendChild(title);

    for (const [i, paper] of data.cited_papers.entries()) {
      const row = document.createElement("div");
      row.className = "insights-list-item";
      row.innerHTML = `<span class="insights-list-rank">${i + 1}.</span><span class="insights-list-name">${escapeHtml(paper.title)}</span><span class="insights-list-count">${paper.citations.toLocaleString()}</span>`;
      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  // Most-read authors
  if (data.top_authors && data.top_authors.length) {
    const card = document.createElement("div");
    card.className = "insights-card";
    const title = document.createElement("div");
    title.className = "insights-card-title";
    title.textContent = "Most-Read Authors";
    card.appendChild(title);

    for (const [i, author] of data.top_authors.entries()) {
      const row = document.createElement("div");
      row.className = "insights-list-item";
      row.innerHTML = `<span class="insights-list-rank">${i + 1}.</span><span class="insights-list-name">${escapeHtml(author.name)}</span><span class="insights-list-count">${author.count}</span>`;
      card.appendChild(row);
    }
    grid.appendChild(card);
  }

  papersInsightsPanel.appendChild(grid);
}

let currentPaperFilter = "all";
let currentPaperKey = null;

function renderPapersList(papers) {
  cachedPapers = papers;
  if (!papersSidebarEl) return;

  const read = papers.filter((p) => p.status === "processed").length;
  const inQueue = papers.filter((p) => p.status !== "processed").length;
  const promoted = papers.filter((p) => p.promoted).length;

  // Update count badge
  if (papersCountEl) {
    papersCountEl.textContent = papers.length ? `${papers.length}` : "";
  }

  // Render filter pills in filter area
  if (papersFiltersEl) {
    papersFiltersEl.innerHTML = "";
    const filters = [
      { label: "All", value: "all", count: papers.length },
      { label: "Unread", value: "unread", count: inQueue },
      { label: "Read", value: "read", count: read },
      { label: "Promoted", value: "promoted", count: promoted },
    ];
    for (const f of filters) {
      const btn = document.createElement("button");
      btn.className = `sidebar-filter-btn${f.value === currentPaperFilter ? " active" : ""}`;
      btn.textContent = `${f.label} ${f.count}`;
      btn.addEventListener("click", () => {
        currentPaperFilter = f.value;
        renderPapersList(cachedPapers);
      });
      papersFiltersEl.appendChild(btn);
    }
  }

  // Render compact sidebar items
  renderPaperSidebarItems(papers, currentPaperFilter);
}

function renderPaperSidebarItems(papers, filter) {
  if (!papersSidebarEl) return;
  papersSidebarEl.innerHTML = "";

  const filtered = filter === "all" ? papers
    : filter === "unread" ? papers.filter((p) => p.status !== "processed")
    : filter === "read" ? papers.filter((p) => p.status === "processed")
    : papers.filter((p) => p.promoted);

  if (!filtered.length) {
    papersSidebarEl.innerHTML = `<div class="sidebar-empty"><p>No ${filter === "all" ? "" : filter + " "}papers.</p></div>`;
    return;
  }

  for (const paper of filtered) {
    const isRead = paper.status === "processed";
    const item = document.createElement("div");
    item.className = `sidebar-item${paper.key === currentPaperKey ? " active" : ""}`;
    item.dataset.key = paper.key;

    const dot = document.createElement("span");
    dot.className = `sidebar-item-dot${isRead ? " read" : ""}`;
    item.appendChild(dot);

    const name = document.createElement("span");
    name.className = "sidebar-item-name";
    name.textContent = paper.title || paper.key;
    item.appendChild(name);

    const metaParts = [];
    if (paper.publication_date) metaParts.push(paper.publication_date.slice(0, 4));
    if (paper.citation_count) metaParts.push(`${paper.citation_count} cit.`);
    if (metaParts.length) {
      const meta = document.createElement("span");
      meta.className = "sidebar-item-meta";
      meta.textContent = metaParts.join(" \u00B7 ");
      item.appendChild(meta);
    }

    if (paper.promoted) {
      const badge = document.createElement("span");
      badge.className = "sidebar-item-badge promoted";
      badge.textContent = "\u2605";
      item.appendChild(badge);
    }

    item.addEventListener("click", () => selectPaper(paper.key));
    papersSidebarEl.appendChild(item);
  }
}

function selectPaper(paperKey) {
  currentPaperKey = paperKey;

  // Update sidebar selection
  papersSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.key === paperKey);
  });

  // Show paper detail in experiment-detail area (reuse editor area)
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  // Switch to control panel and show detail
  welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML = '<div class="exp-detail-loading">Loading paper...</div>';

  switchEditorTab("control-panel");

  fetch(`http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}`)
    .then((r) => r.json())
    .then((resp) => {
      if (!resp.ok) {
        detailEl.innerHTML = '<div class="exp-detail-loading">Could not load paper details.</div>';
        return;
      }

      const data = resp.paper || resp;
      detailEl.innerHTML = "";

      // Header
      const header = document.createElement("div");
      header.className = "exp-detail-header";

      const title = document.createElement("h2");
      title.className = "exp-detail-title";
      title.textContent = data.title || paperKey;
      header.appendChild(title);

      // Status badges
      const badges = document.createElement("div");
      badges.className = "exp-detail-badges";
      if (data.status === "processed") {
        const b = document.createElement("span");
        b.className = "exp-detail-badge keep";
        b.textContent = "read";
        badges.appendChild(b);
      }
      if (data.promoted) {
        const b = document.createElement("span");
        b.className = "exp-detail-badge";
        b.style.background = "var(--accent)";
        b.textContent = "promoted";
        badges.appendChild(b);
      }
      header.appendChild(badges);

      // Authors
      if (data.authors && data.authors.length) {
        const authorsEl = document.createElement("div");
        authorsEl.className = "exp-detail-meta";
        authorsEl.textContent = data.authors.join(", ");
        header.appendChild(authorsEl);
      }

      // Venue + date + IDs
      const metaParts = [];
      if (data.venue) metaParts.push(data.venue);
      if (data.publication_date) metaParts.push(data.publication_date);
      if (data.doi) metaParts.push(`DOI: ${data.doi}`);
      if (data.arxiv_id) metaParts.push(`arXiv: ${data.arxiv_id}`);
      if (metaParts.length) {
        const metaEl = document.createElement("div");
        metaEl.className = "exp-detail-meta";
        metaEl.textContent = metaParts.join(" \u00B7 ");
        header.appendChild(metaEl);
      }

      // Paper URL link
      const paperUrl = data.url
        || (data.arxiv_id ? `https://arxiv.org/abs/${data.arxiv_id}` : "")
        || (data.doi ? `https://doi.org/${data.doi}` : "");
      if (paperUrl) {
        const linkEl = document.createElement("a");
        linkEl.className = "paper-external-link";
        linkEl.href = "#";
        linkEl.textContent = data.arxiv_id ? `arxiv.org/abs/${data.arxiv_id}` : paperUrl.replace(/^https?:\/\//, "");
        linkEl.addEventListener("click", (e) => {
          e.preventDefault();
          window.nicolas.openExternal(paperUrl);
        });
        header.appendChild(linkEl);
      }

      // Stats row
      const statParts = [];
      if (data.page_count) statParts.push(`${data.page_count} pages`);
      if (data.citation_count) statParts.push(`${data.citation_count} citations`);
      if (data.engagement) statParts.push(`${data.engagement}% engagement`);
      if (statParts.length) {
        const statsEl = document.createElement("div");
        statsEl.className = "exp-detail-meta";
        statsEl.textContent = statParts.join(" \u00B7 ");
        header.appendChild(statsEl);
      }

      detailEl.appendChild(header);

      // Action buttons
      const actions = document.createElement("div");
      actions.className = "exp-detail-actions";

      const promoteBtn = document.createElement("button");
      const isPromoted = !!data.promoted;
      promoteBtn.className = isPromoted ? "paper-action-btn promoted" : "paper-action-btn";
      promoteBtn.textContent = isPromoted ? "Unpromote" : "Promote";
      promoteBtn.dataset.promoted = isPromoted ? "1" : "0";
      promoteBtn.addEventListener("click", () => {
        const wantPromote = promoteBtn.dataset.promoted === "0";
        togglePromote(paperKey, wantPromote, promoteBtn);
      });
      actions.appendChild(promoteBtn);

      const refreshBtn = document.createElement("button");
      refreshBtn.className = "paper-action-btn";
      refreshBtn.textContent = "Refresh metadata";
      refreshBtn.addEventListener("click", () => refreshPaperMetadata(paperKey, refreshBtn));
      actions.appendChild(refreshBtn);

      detailEl.appendChild(actions);

      // Summary (or S2 TLDR fallback)
      if (data.summary) {
        const section = document.createElement("div");
        section.className = "exp-detail-section";
        const sTitle = document.createElement("h3");
        sTitle.textContent = "Summary";
        section.appendChild(sTitle);
        const p = document.createElement("p");
        p.textContent = data.summary;
        section.appendChild(p);
        detailEl.appendChild(section);
      } else if (data.s2_tldr) {
        const section = document.createElement("div");
        section.className = "exp-detail-section";
        const sTitle = document.createElement("h3");
        sTitle.textContent = "Semantic Scholar TLDR";
        section.appendChild(sTitle);
        const p = document.createElement("p");
        p.className = "paper-card-s2-tldr";
        p.textContent = data.s2_tldr;
        section.appendChild(p);
        detailEl.appendChild(section);
      }

      // Tags
      if (data.tags && data.tags.length) {
        const section = document.createElement("div");
        section.className = "exp-detail-section";
        const sTitle = document.createElement("h3");
        sTitle.textContent = "Topics";
        section.appendChild(sTitle);
        const tags = document.createElement("div");
        tags.className = "paper-card-tags";
        for (const tag of data.tags) {
          const chip = document.createElement("span");
          chip.className = "paper-tag";
          chip.textContent = tag;
          tags.appendChild(chip);
        }
        section.appendChild(tags);
        detailEl.appendChild(section);
      }

      // Highlights
      if (data.highlights && data.highlights.length) {
        const section = document.createElement("div");
        section.className = "exp-detail-section";
        const sTitle = document.createElement("h3");
        sTitle.textContent = "Highlights";
        section.appendChild(sTitle);
        const ul = document.createElement("ul");
        ul.className = "paper-highlights-list";
        for (const h of data.highlights) {
          const li = document.createElement("li");
          li.textContent = typeof h === "string" ? h : h.text || JSON.stringify(h);
          ul.appendChild(li);
        }
        section.appendChild(ul);
        detailEl.appendChild(section);
      }
    })
    .catch(() => {
      detailEl.innerHTML = '<div class="exp-detail-loading">Failed to load paper details.</div>';
    });
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function togglePromote(paperKey, promote, btn) {
  if (!serverPort) return;
  const endpoint = promote ? "promote" : "unpromote";
  btn.disabled = true;
  btn.textContent = "...";
  fetch(`http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}/${endpoint}`, { method: "POST" })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        const nowPromoted = !!data.promoted;
        btn.textContent = nowPromoted ? "Unpromote" : "Promote";
        btn.dataset.promoted = nowPromoted ? "1" : "0";
        btn.classList.toggle("promoted", nowPromoted);
        // Sync source data so re-renders (filters/sort) stay correct
        const paperObj = cachedPapers.find((p) => p.key === paperKey);
        if (paperObj) paperObj.promoted = nowPromoted;
        // Update the badge on the card
        const card = btn.closest(".paper-card");
        const header = card.querySelector(".paper-card-header");
        const existingBadge = header.querySelector(".paper-promoted-badge");
        if (nowPromoted && !existingBadge) {
          const badge = document.createElement("span");
          badge.className = "paper-promoted-badge";
          badge.textContent = "promoted";
          header.appendChild(badge);
        } else if (!nowPromoted && existingBadge) {
          existingBadge.remove();
        }
      }
    })
    .catch(() => {
      btn.textContent = promote ? "Promote" : "Unpromote";
      showToast(`Failed to ${promote ? "promote" : "unpromote"} paper`);
    })
    .finally(() => { btn.disabled = false; });
}

function refreshPaperMetadata(paperKey, btn) {
  if (!serverPort) return;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Refreshing...";
  fetch(`http://127.0.0.1:${serverPort}/papers/${encodeURIComponent(paperKey)}/refresh-metadata`, { method: "POST" })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        btn.textContent = "Done!";
        setTimeout(() => { btn.textContent = originalText; btn.disabled = false; }, 1500);
        // Refresh the papers list to show updated data
        fetchPapersData();
      } else {
        btn.textContent = "Failed";
        setTimeout(() => { btn.textContent = originalText; btn.disabled = false; }, 1500);
      }
    })
    .catch(() => {
      btn.textContent = originalText;
      btn.disabled = false;
      showToast("Failed to refresh metadata");
    });
}

/* ───── Metric evolution chart ───── */

// Metric classification — checked in priority order (first match wins)
const _METRIC_CATEGORIES = [
  ["ratio", ["accuracy", "precision", "recall", "f1", "auc", "map", "ap",
             "iou", "dice", "bleu", "rouge", "meteor", "exact_match", "score"]],
  ["loss", ["loss", "error", "mae", "rmse", "mse", "perplexity", "nll",
            "cross_entropy", "bpb"]],
  ["count", ["param", "count", "num_", "flops", "size", "steps", "epochs",
             "samples", "vocab"]],
  ["time", ["time", "duration", "seconds", "minutes", "latency"]],
  ["cost", ["cost", "price"]],
  ["hyperparameter", ["lr", "learning_rate", "weight_decay", "dropout",
                      "momentum", "beta", "epsilon", "warmup"]],
];

const _LOWER_BETTER_CATEGORIES = new Set(["loss", "count", "time", "cost"]);

function classifyMetric(name) {
  const nl = name.toLowerCase();
  for (const [category, keywords] of _METRIC_CATEGORIES) {
    if (keywords.some((kw) => nl.includes(kw))) return category;
  }
  return "generic";
}

function isLowerBetter(metricName) {
  return _LOWER_BETTER_CATEGORIES.has(classifyMetric(metricName));
}

function findBestRun(runs, metricName) {
  const lower = isLowerBetter(metricName);
  let best = null;
  for (const r of (runs || [])) {
    if (r.decision !== "keep") continue;
    const v = r.results?.[metricName];
    if (v == null) continue;
    if (!best || (lower ? v < best.results[metricName] : v > best.results[metricName]))
      best = r;
  }
  return best;
}

function formatMetric(name, val) {
  if (val == null) return "\u2014";
  if (typeof val !== "number") return String(val);
  const cat = classifyMetric(name);
  if (cat === "ratio") {
    if (val > 0 && val <= 1) return (val * 100).toFixed(2) + "%";
    return val.toFixed(2);
  }
  if (cat === "loss") {
    if (Math.abs(val) < 0.001) return val.toExponential(2);
    if (Math.abs(val) < 1) return val.toFixed(4);
    return val.toFixed(2);
  }
  if (cat === "count") {
    if (val === Math.floor(val)) {
      const iv = Math.trunc(val);
      const v = Math.abs(iv);
      if (v >= 1e9) return (iv / 1e9).toFixed(2) + "B (" + iv.toLocaleString() + ")";
      if (v >= 1e6) return (iv / 1e6).toFixed(2) + "M (" + iv.toLocaleString() + ")";
      return iv.toLocaleString();
    }
    return val.toFixed(2);
  }
  if (cat === "time") {
    const v = Math.abs(val);
    if (v >= 3600) {
      const h = Math.floor(v / 3600);
      const m = Math.floor((v % 3600) / 60);
      return `${h}h ${m}m`;
    }
    if (v >= 60) {
      const m = Math.floor(v / 60);
      const s = Math.floor(v % 60);
      return `${m}m ${s}s`;
    }
    return val.toFixed(2) + "s";
  }
  if (cat === "cost") return "$" + val.toFixed(2);
  if (cat === "hyperparameter") {
    if (Math.abs(val) < 0.01 || Math.abs(val) >= 1000) return val.toExponential(2);
    return val.toPrecision(4);
  }
  // generic
  if (Number.isInteger(val)) return val.toLocaleString();
  if (val > 0 && val <= 1) return (val * 100).toFixed(2) + "%";
  if (Math.abs(val) < 0.001) return val.toExponential(2);
  if (Math.abs(val) < 1) return val.toFixed(4);
  return val.toFixed(2);
}

function runDisplayNum(run) {
  // Use run_number from server if available, otherwise fall back to name parsing
  if (run.run_number > 0) return `${run.run_number}${run.run_suffix || ""}`;
  return run.name || run.id || "?";
}

function renderMetricChart(canvas, runs, metricName, liveEvents, opts = {}) {
  const useLogScale = opts.logScale || false;
  // Filter runs that have a numeric value for this metric
  const points = [];
  for (let i = 0; i < runs.length; i++) {
    const val = runs[i].results?.[metricName];
    if (typeof val === "number" && isFinite(val)) {
      points.push({ index: i, value: val, run: runs[i] });
    }
  }

  // Build live points from metric_update events
  const livePoints = [];
  if (liveEvents && liveEvents.length) {
    for (const ev of liveEvents) {
      const val = ev.metrics?.[metricName];
      if (typeof val === "number" && isFinite(val)) {
        livePoints.push({
          value: val,
          epoch: ev.epoch,
          step: ev.step,
          ts: ev.ts,
        });
      }
    }
  }

  const totalPoints = points.length + livePoints.length;

  if (totalPoints < 2) {
    // Clear canvas but keep container visible with a message
    const ctx2 = canvas.getContext("2d");
    const dpr2 = window.devicePixelRatio || 1;
    const rect2 = canvas.getBoundingClientRect();
    canvas.width = rect2.width * dpr2;
    canvas.height = rect2.height * dpr2;
    ctx2.scale(dpr2, dpr2);
    ctx2.clearRect(0, 0, rect2.width, rect2.height);
    ctx2.fillStyle = "#8888a0";
    ctx2.font = "12px -apple-system, sans-serif";
    ctx2.textAlign = "center";
    ctx2.fillText(
      totalPoints === 0 ? `No data for ${metricName}` : `Only 1 data point for ${metricName}`,
      rect2.width / 2, rect2.height / 2
    );
    // Remove old tooltip
    const oldTip = canvas.parentElement.querySelector(".metric-chart-tooltip");
    if (oldTip) oldTip.style.display = "none";
    canvas.onmousemove = null;
    canvas.onmouseleave = null;
    return;
  }

  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = rect.width;
  const h = rect.height;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const pad = { top: 12, right: 16, bottom: 24, left: 48 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  // Combine all values for Y-axis range calculation
  const allValues = points.map((p) => p.value).concat(livePoints.map((p) => p.value));
  let minVal = Math.min(...allValues);
  let maxVal = Math.max(...allValues);

  // Include goal thresholds in Y-axis range so the line is always visible
  const proj = cachedProjects.find((p) => p.id === currentProjectId);
  const matchingGoals = (proj?.goals || []).filter((g) => g.metric === metricName);
  for (const g of matchingGoals) {
    if (typeof g.threshold === "number") {
      if (g.threshold < minVal) minVal = g.threshold;
      if (g.threshold > maxVal) maxVal = g.threshold;
    }
  }

  // For linear scale: anchor Y at 0 for non-negative metrics
  // For log scale: use actual data range so graph fills vertical space
  if (!useLogScale && minVal >= 0) minVal = 0;
  if (minVal === maxVal) { maxVal = minVal + 1; }
  const range = maxVal - minVal;
  if (useLogScale) {
    // Add padding on both sides in log space
    const logRange = Math.log10(Math.max(maxVal, 1e-10)) - Math.log10(Math.max(minVal, 1e-10));
    minVal = Math.pow(10, Math.log10(Math.max(minVal, 1e-10)) - logRange * 0.05);
    maxVal = Math.pow(10, Math.log10(Math.max(maxVal, 1e-10)) + logRange * 0.05);
  } else {
    maxVal += range * 0.05;
  }

  // X maps over totalPoints (run points + live points)
  function toX(i) { return pad.left + (i / (totalPoints - 1)) * plotW; }

  // Log scale: use log10 for Y mapping
  const logMin = useLogScale ? Math.log10(Math.max(minVal, 1e-10)) : minVal;
  const logMax = useLogScale ? Math.log10(Math.max(maxVal, 1e-10)) : maxVal;
  function toY(v) {
    const sv = useLogScale ? Math.log10(Math.max(v, 1e-10)) : v;
    return pad.top + (1 - (sv - logMin) / (logMax - logMin)) * plotH;
  }

  // Clear
  ctx.clearRect(0, 0, w, h);

  // Y-axis labels with nice rounded ticks
  ctx.fillStyle = "#8888a0";
  ctx.font = "10px -apple-system, sans-serif";
  ctx.textAlign = "right";

  function niceNum(range, round) {
    const exp = Math.floor(Math.log10(range));
    const frac = range / Math.pow(10, exp);
    let nice;
    if (round) {
      if (frac < 1.5) nice = 1;
      else if (frac < 3) nice = 2;
      else if (frac < 7) nice = 5;
      else nice = 10;
    } else {
      if (frac <= 1) nice = 1;
      else if (frac <= 2) nice = 2;
      else if (frac <= 5) nice = 5;
      else nice = 10;
    }
    return nice * Math.pow(10, exp);
  }

  const maxTicks = Math.max(3, Math.min(8, Math.floor(plotH / 50)));
  let yTickValues = [];
  if (useLogScale) {
    // Log scale: use powers of 10 and simple multiples
    const logMinFloor = Math.floor(Math.log10(Math.max(minVal, 1e-10)));
    const logMaxCeil = Math.ceil(Math.log10(Math.max(maxVal, 1e-10)));
    for (let e = logMinFloor; e <= logMaxCeil; e++) {
      const base = Math.pow(10, e);
      for (const mult of [1, 2, 5]) {
        const v = base * mult;
        if (v >= minVal && v <= maxVal) yTickValues.push(v);
      }
    }
    if (yTickValues.length < 2) {
      for (let i = 0; i <= 4; i++) {
        const logV = logMin + (i / 4) * (logMax - logMin);
        yTickValues.push(Math.pow(10, logV));
      }
    }
  } else {
    // Linear scale: nice rounded intervals
    const dataRange = maxVal - minVal;
    const tickSpacing = niceNum(dataRange / (maxTicks - 1), true);
    const niceMin = Math.floor(minVal / tickSpacing) * tickSpacing;
    const niceMax = Math.ceil(maxVal / tickSpacing) * tickSpacing;
    for (let v = niceMin; v <= niceMax + tickSpacing * 0.5; v += tickSpacing) {
      if (v >= minVal - tickSpacing * 0.1 && v <= maxVal + tickSpacing * 0.1) {
        yTickValues.push(v);
      }
    }
  }
  // Cull ticks that are too close in pixel space (min 30px apart)
  if (yTickValues.length > maxTicks) {
    const step = Math.ceil(yTickValues.length / maxTicks);
    const culled = [];
    for (let i = 0; i < yTickValues.length; i += step) culled.push(yTickValues[i]);
    if (culled[culled.length - 1] !== yTickValues[yTickValues.length - 1]) culled.push(yTickValues[yTickValues.length - 1]);
    yTickValues = culled;
  }

  for (const v of yTickValues) {
    const y = toY(v);
    if (y < pad.top - 5 || y > pad.top + plotH + 5) continue;
    ctx.fillStyle = "#8888a0";
    ctx.fillText(formatMetric(metricName, v), pad.left - 6, y + 3);
    ctx.strokeStyle = "rgba(136,136,160,0.1)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(w - pad.right, y);
    ctx.stroke();
  }

  // X-axis labels (run points)
  ctx.textAlign = "center";
  const xStep = Math.max(1, Math.floor(totalPoints / 6));
  for (let i = 0; i < points.length; i += xStep) {
    ctx.fillStyle = "#8888a0";
    ctx.fillText(`#${runDisplayNum(points[i].run)}`, toX(i), h - 4);
  }
  // X-axis labels (live points — show epoch)
  for (let i = 0; i < livePoints.length; i++) {
    const globalIdx = points.length + i;
    if (globalIdx % xStep === 0) {
      ctx.fillStyle = "rgba(99,102,241,0.5)";
      const label = livePoints[i].epoch != null ? `e${livePoints[i].epoch}` : `+${i + 1}`;
      ctx.fillText(label, toX(globalIdx), h - 4);
    }
  }

  // Best-so-far frontier: running min/max over ALL runs (not just keeps)
  const lowerBetter = isLowerBetter(metricName);
  const frontierSet = new Set(); // indices of runs that improved the frontier
  if (points.length > 0) {
    let bestSoFar = points[0].value;
    const bestLine = [];
    frontierSet.add(0);
    bestLine.push({ x: toX(0), y: toY(bestSoFar) });

    for (let i = 1; i < points.length; i++) {
      const v = points[i].value;
      const improved = lowerBetter ? v < bestSoFar : v > bestSoFar;
      if (improved) {
        bestSoFar = v;
        frontierSet.add(i);
      }
      bestLine.push({ x: toX(i), y: toY(bestSoFar) });
    }
    // Extend frontier to right edge
    if (bestLine.length > 0 && bestSoFar !== null) {
      const lastX = bestLine[bestLine.length - 1].x;
      const rightEdge = toX(totalPoints - 1);
      if (lastX < rightEdge) {
        bestLine.push({ x: rightEdge, y: toY(bestSoFar) });
      }
    }
    if (bestLine.length > 1) {
      ctx.strokeStyle = "rgba(34,197,94,0.5)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      for (let i = 0; i < bestLine.length; i++) {
        if (i === 0) ctx.moveTo(bestLine[i].x, bestLine[i].y);
        else ctx.lineTo(bestLine[i].x, bestLine[i].y);
      }
      ctx.stroke();
    }
  }

  // Live points: dashed lighter line connecting from last run point
  if (livePoints.length > 0) {
    ctx.strokeStyle = "rgba(99,102,241,0.5)";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 3]);
    ctx.beginPath();
    // Start from the last run point if it exists
    const startIdx = points.length > 0 ? points.length - 1 : 0;
    const startVal = points.length > 0 ? points[points.length - 1].value : livePoints[0].value;
    ctx.moveTo(toX(startIdx), toY(startVal));
    for (let i = 0; i < livePoints.length; i++) {
      ctx.lineTo(toX(points.length + i), toY(livePoints[i].value));
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Run dots: green = frontier-improving keeps, gray = everything else
  for (let i = 0; i < points.length; i++) {
    const x = toX(i);
    const y = toY(points[i].value);
    if (frontierSet.has(i)) {
      ctx.fillStyle = "#22c55e";
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#0f0f23";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    } else {
      ctx.fillStyle = "#666";
      ctx.globalAlpha = 0.45;
      ctx.beginPath();
      ctx.arc(x, y, 2.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }
  }

  // Tilted description labels on every kept run
  {
    const angle = -Math.PI / 6; // -30 degrees
    ctx.save();
    ctx.font = "8px -apple-system, sans-serif";
    ctx.fillStyle = "rgba(160,160,180,0.5)";
    ctx.textAlign = "left";
    for (let i = 0; i < points.length; i++) {
      if (!frontierSet.has(i)) continue;
      const desc = points[i].run.description || points[i].run.hypothesis || "";
      if (!desc) continue;
      const label = desc.length > 24 ? desc.slice(0, 22) + "\u2026" : desc;
      const x = toX(i);
      const y = toY(points[i].value);
      ctx.save();
      ctx.translate(x + 5, y - 7);
      ctx.rotate(angle);
      ctx.fillText(label, 0, 0);
      ctx.restore();
    }
    ctx.restore();
  }

  // Live dots (lighter, smaller)
  for (let i = 0; i < livePoints.length; i++) {
    const x = toX(points.length + i);
    const y = toY(livePoints[i].value);
    ctx.fillStyle = "rgba(99,102,241,0.5)";
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#0f0f23";
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  // Goal threshold lines
  for (const goal of matchingGoals) {
    if (typeof goal.threshold !== "number") continue;
    const goalY = toY(goal.threshold);
    // Only draw if within the visible plot area
    if (goalY >= pad.top && goalY <= pad.top + plotH) {
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = "rgba(34, 197, 94, 0.6)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(pad.left, goalY);
      ctx.lineTo(pad.left + plotW, goalY);
      ctx.stroke();
      ctx.setLineDash([]);
      // Label
      ctx.fillStyle = "rgba(34, 197, 94, 0.8)";
      ctx.font = "10px sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(`goal: ${goal.threshold}`, pad.left + plotW - 4, goalY - 4);
    }
  }

  // Tooltip on hover (covers both run and live points)
  const container = canvas.parentElement;
  let tooltip = container.querySelector(".metric-chart-tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "metric-chart-tooltip";
    tooltip.style.display = "none";
    container.appendChild(tooltip);
  }

  canvas.onmousemove = (e) => {
    const cRect = canvas.getBoundingClientRect();
    const mx = e.clientX - cRect.left;
    const my = e.clientY - cRect.top;
    let closest = null;
    let closestDist = Infinity;
    // Check run points
    for (let i = 0; i < points.length; i++) {
      const x = toX(i);
      const y = toY(points[i].value);
      const dist = Math.sqrt((mx - x) ** 2 + (my - y) ** 2);
      if (dist < closestDist && dist < 20) {
        closestDist = dist;
        const run = points[i].run;
        const desc = run.description || run.hypothesis || "";
        const descLine = desc ? `\n${desc.length > 60 ? desc.slice(0, 58) + "\u2026" : desc}` : "";
        closest = { x, y, label: `#${runDisplayNum(run)} ${run.name || run.id}: ${metricName}=${formatMetric(metricName, points[i].value)} (${run.decision || run.status || "?"})${descLine}` };
      }
    }
    // Check live points
    for (let i = 0; i < livePoints.length; i++) {
      const x = toX(points.length + i);
      const y = toY(livePoints[i].value);
      const dist = Math.sqrt((mx - x) ** 2 + (my - y) ** 2);
      if (dist < closestDist && dist < 20) {
        closestDist = dist;
        const epochLabel = livePoints[i].epoch != null ? `epoch ${livePoints[i].epoch}` : `step ${livePoints[i].step || i}`;
        closest = { x, y, label: `[live] ${epochLabel}: ${metricName}=${formatMetric(metricName, livePoints[i].value)}` };
      }
    }
    if (closest) {
      tooltip.innerHTML = closest.label.replace(/\n/g, "<br>");
      tooltip.style.display = "";
      let tx = closest.x + 10;
      let ty = closest.y - 28;
      if (tx + 200 > w) tx = closest.x - 160;
      if (ty < 0) ty = closest.y + 10;
      tooltip.style.left = tx + "px";
      tooltip.style.top = ty + "px";
    } else {
      tooltip.style.display = "none";
    }
  };

  canvas.onmouseleave = () => {
    tooltip.style.display = "none";
  };
}

// ResizeObserver for chart redraw
let chartResizeObserver = null;

function setupChartResize(container, canvas, runs, metricNameRef, projectIdRef, optsRef) {
  if (chartResizeObserver) chartResizeObserver.disconnect();
  // metricNameRef can be a string or a function returning the current metric
  chartResizeObserver = new ResizeObserver(() => {
    const name = typeof metricNameRef === "function" ? metricNameRef() : metricNameRef;
    const pid = typeof projectIdRef === "function" ? projectIdRef() : projectIdRef;
    const opts = typeof optsRef === "function" ? optsRef() : (optsRef || {});
    renderMetricChart(canvas, runs, name, pid ? liveMetrics[pid] : undefined, opts);
  });
  chartResizeObserver.observe(container);
}

/* ───── Session polling ───── */

let sessionPollInterval = null;

function startSessionPolling() {
  if (sessionPollInterval) return;
  sessionPollInterval = setInterval(() => {
    fetchExperimentsList();
  }, 15000);
}

function stopSessionPolling() {
  if (sessionPollInterval) {
    clearInterval(sessionPollInterval);
    sessionPollInterval = null;
  }
}

/* ───── Live tab: experiment list ───── */

let experimentsFirstLoad = true;

function fetchExperimentsList() {
  if (!serverPort) return;
  // Show skeleton on first load
  if (experimentsFirstLoad && experimentsSidebarEl) {
    experimentsSidebarEl.innerHTML = '<div class="sidebar-skeleton">' +
      '<div class="skeleton-item"></div>'.repeat(4) + '</div>';
  }
  fetch(`http://127.0.0.1:${serverPort}/experiments/list`)
    .then((r) => r.json())
    .then((data) => {
      experimentsFirstLoad = false;
      if (!data.ok) return;
      const projects = data.projects || [];
      renderProjectsList(projects);
      // Re-render the detail view if a project is selected (picks up new runs/metrics)
      if (currentProjectId && cachedProjects.find((p) => p.id === currentProjectId)) {
        renderProjectDetail(currentProjectId);
      }
      // Start SSE if we have projects with active sessions
      if (projects.some((p) => p.active_sessions > 0)) {
        startExperimentSSE();
      }
    })
    .catch(() => { experimentsFirstLoad = false; });
}

function reloadCurrentProject() {
  if (!serverPort || !currentProjectId) return fetchExperimentsList();
  // Rescan disk first, then fetch updated data
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(currentProjectId)}/scan`, { method: "POST" })
    .then((r) => r.json())
    .then(() => fetchExperimentsList())
    .catch(() => fetchExperimentsList());
}

function doReload(projectId, btn) {
  if (!serverPort) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "...";
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/scan`, { method: "POST" })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        const parts = [];
        if (data.new_runs) parts.push(`${data.new_runs} new`);
        if (data.backfilled) parts.push(`${data.backfilled} backfilled`);
        btn.textContent = parts.length ? parts.join(", ") : "\u2713";
        fetchExperimentsList();
      } else {
        btn.textContent = data.reason || "Failed";
      }
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
    })
    .catch(() => {
      btn.textContent = "Error";
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
    });
}

const experimentsSidebarEl = document.getElementById("experiments-sidebar");
const experimentsCountEl = document.getElementById("experiments-count");

function renderProjectsList(projects) {
  // Sort: active sessions first, then by most recent activity
  projects.sort((a, b) => {
    // Active sessions always on top
    if (a.active_sessions > 0 && b.active_sessions === 0) return -1;
    if (b.active_sessions > 0 && a.active_sessions === 0) return 1;
    // Then by last_scanned_at (most recent first)
    const aDate = a.last_scanned_at || a.added_at || "";
    const bDate = b.last_scanned_at || b.added_at || "";
    return bDate.localeCompare(aDate);
  });

  cachedProjects = projects;
  if (!experimentsSidebarEl) return;

  // Manage session polling
  const hasActive = projects.some((p) => p.active_sessions > 0);
  if (hasActive) startSessionPolling();
  else stopSessionPolling();

  // Update count badge
  if (experimentsCountEl) {
    experimentsCountEl.textContent = projects.length ? `${projects.length}` : "";
  }

  if (!projects.length) {
    experimentsSidebarEl.innerHTML = `
      <div class="sidebar-empty">
        <p>No experiments yet.</p>
        <p class="sidebar-empty-hint">Ask Nicolas to run an experiment.</p>
      </div>`;
    return;
  }

  experimentsSidebarEl.innerHTML = "";

  // Add/remove compare button in sidebar header when 2+ projects
  const sidebarHeader = experimentsSidebarEl.parentElement?.querySelector(".sidebar-header");
  let compareBtn = sidebarHeader?.querySelector(".compare-btn");
  if (projects.length >= 2 && sidebarHeader && !compareBtn) {
    compareBtn = document.createElement("button");
    compareBtn.className = "compare-btn sidebar-header-btn";
    compareBtn.textContent = "\u2194";
    compareBtn.title = "Compare experiments";
    compareBtn.style.cssText = "font-size:13px;";
    compareBtn.addEventListener("click", showComparisonGrid);
    const newExpBtn = sidebarHeader.querySelector("#new-experiment-btn");
    if (newExpBtn) newExpBtn.before(compareBtn);
    else sidebarHeader.appendChild(compareBtn);
  } else if (projects.length < 2 && compareBtn) {
    compareBtn.remove();
  }

  for (const proj of projects) {
    const item = document.createElement("div");
    item.className = `sidebar-item${proj.id === currentProjectId ? " active" : ""}`;
    item.dataset.id = proj.id;

    const icon = document.createElement("span");
    icon.className = "sidebar-item-icon";
    if (proj.campaign && proj.campaign.status === "running") {
      icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="none" stroke="#6366f1" stroke-width="1.5" stroke-dasharray="6 3"><animateTransform attributeName="transform" type="rotate" values="0 5 5;360 5 5" dur="2s" repeatCount="indefinite"/></circle></svg>`;
      icon.title = "Campaign running";
    } else if (proj.active_sessions > 0 && proj.current_run === "Session active") {
      // Idle: session alive but agent waiting
      icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="1" width="3" height="8" rx="0.5" fill="#f59e0b"/><rect x="6" y="1" width="3" height="8" rx="0.5" fill="#f59e0b"/></svg>`;
      icon.title = "Idle";
    } else if (proj.active_sessions > 0) {
      icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10" class="blink-play"><polygon points="1,0 9,5 1,10" fill="#22c55e"/></svg>`;
      icon.title = "Running";
    } else if (proj.status === "paused") {
      icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="1" width="3" height="8" rx="0.5" fill="#f59e0b"/><rect x="6" y="1" width="3" height="8" rx="0.5" fill="#f59e0b"/></svg>`;
      icon.title = "Paused";
    } else {
      icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" rx="1.5" fill="#8888a0"/></svg>`;
      icon.title = "Stopped";
    }
    item.appendChild(icon);

    const name = document.createElement("span");
    name.className = "sidebar-item-name";
    name.textContent = proj.name || proj.id;
    item.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "sidebar-item-meta";
    // Deduplicate by run ID, count only completed runs
    let sidebarTotal = 0, sidebarKept = 0;
    if (proj.runs) {
      const byId = new Map();
      for (const r of proj.runs) byId.set(r.id, r);
      for (const r of byId.values()) {
        const d = r.decision || r.status || "other";
        if (d === "running") continue;
        sidebarTotal++;
        if (d === "keep") sidebarKept++;
      }
    }
    meta.textContent = `${sidebarTotal} runs \u00B7 ${sidebarKept} kept`;
    item.appendChild(meta);

    if (proj.active_sessions > 0) {
      const badge = document.createElement("span");
      badge.className = "sidebar-item-badge running";
      badge.textContent = proj.active_sessions;
      item.appendChild(badge);
    }

    item.addEventListener("click", () => selectProject(proj.id));
    experimentsSidebarEl.appendChild(item);
  }

  // Re-render detail if the currently selected project was updated
  if (currentProjectId) {
    const stillExists = projects.find((p) => p.id === currentProjectId);
    if (stillExists) renderProjectDetail(currentProjectId);
  }
}

// ---------------------------------------------------------------------------
// New Experiment wizard
// ---------------------------------------------------------------------------

const newExperimentBtn = document.getElementById("new-experiment-btn");
if (newExperimentBtn) {
  newExperimentBtn.addEventListener("click", showNewExperimentWizard);
}

async function showNewExperimentWizard() {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;

  // Deselect any project
  currentProjectId = null;
  const _tabLabel = document.getElementById("editor-tabs-project-name");
  if (_tabLabel) _tabLabel.textContent = "";
  experimentsSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => {
    el.classList.remove("active");
  });

  welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML = "";
  switchEditorTab("control-panel");

  // Check if experiments root is configured
  const settings = await window.nicolas.getSettings();
  let experimentsRoot = settings.experimentsRoot || "";

  const wizard = document.createElement("div");
  wizard.className = "new-experiment-wizard";

  wizard.innerHTML = `
    <h3>\uD83E\uDDEA New Experiment</h3>
    <div class="wizard-field">
      <label>Name</label>
      <input id="wizard-name" type="text" placeholder="my-experiment" spellcheck="false" />
    </div>
    <div class="wizard-field">
      <label>Goal</label>
      <textarea id="wizard-goal" placeholder="Describe what you want to optimize, the success criteria, and target metrics\u2026" rows="4"></textarea>
    </div>
    <div class="wizard-field">
      <label>Primary metric</label>
      <div style="display:flex; gap:8px; align-items:center;">
        <input id="wizard-metric" type="text" placeholder="e.g. param_count, val_loss, test_accuracy" spellcheck="false" style="flex:1;" />
        <select id="wizard-metric-dir" style="width:auto; background:var(--bg); border:1px solid var(--border); border-radius:6px; color:var(--text); font-size:12px; padding:4px 8px;">
          <option value="minimize">minimize</option>
          <option value="maximize">maximize</option>
        </select>
      </div>
      <span style="font-size:10px; color:var(--text-dim);">The metric the agent optimizes. Getting this wrong means it pushes in the wrong direction.</span>
    </div>
    <div class="wizard-field">
      <label>Metric constraint (optional)</label>
      <input id="wizard-metric-constraint" type="text" placeholder="e.g. test_accuracy >= 0.99" spellcheck="false" />
    </div>
    <div class="wizard-field">
      <label>Constraints (optional)</label>
      <input id="wizard-constraints" type="text" placeholder="e.g. MacBook M3, no GPU, must use PyTorch" spellcheck="false" />
    </div>
    <div class="wizard-field" style="display:flex; gap:12px;">
      <div style="flex:0 0 120px;">
        <label>Iteration time</label>
        <div style="display:flex; align-items:center; gap:4px;">
          <input id="wizard-duration" type="number" value="5" min="1" max="120" style="width:60px;" />
          <span style="font-size:11px; color:var(--text-dim);">min</span>
        </div>
      </div>
    </div>
    <div class="wizard-field">
      <label>Experiments folder</label>
      <div style="display:flex; gap:6px;">
        <input id="wizard-root" type="text" value="${experimentsRoot}" placeholder="~/experiments" spellcheck="false" style="flex:1;" />
        <button id="wizard-browse" class="wizard-btn-cancel" style="flex:0; padding:6px 10px; white-space:nowrap;">Browse</button>
      </div>
    </div>
    <div class="wizard-actions">
      <button class="wizard-btn-cancel" id="wizard-cancel">Cancel</button>
      <button class="wizard-btn-create" id="wizard-create" disabled>Create</button>
    </div>
    <div id="wizard-flow" class="wizard-flow hidden"></div>
  `;
  detailEl.appendChild(wizard);

  const STEPS = [
    { id: 1, label: "Create project directory" },
    { id: 2, label: "Draft PROMPT.md with Claude" },
    { id: 3, label: "Install hooks & reporting" },
    { id: 4, label: "Register experiment" },
    { id: 5, label: "Create GitHub repository" },
    { id: 6, label: "Launch Claude Code session" },
  ];

  // Browse button
  wizard.querySelector("#wizard-browse").addEventListener("click", async () => {
    const dir = await window.nicolas.selectDirectory("Select experiments folder");
    if (dir) {
      wizard.querySelector("#wizard-root").value = dir;
      experimentsRoot = dir;
    }
  });

  // Cancel
  wizard.querySelector("#wizard-cancel").addEventListener("click", () => {
    detailEl.classList.add("hidden");
    detailEl.innerHTML = "";
    welcomeEl.classList.remove("hidden");
  });

  const nameInput = wizard.querySelector("#wizard-name");
  const goalInput = wizard.querySelector("#wizard-goal");
  const createBtn = wizard.querySelector("#wizard-create");

  function updateCreateBtn() {
    createBtn.disabled = !nameInput.value.trim();
  }
  nameInput.addEventListener("input", updateCreateBtn);

  // Build flowchart
  function renderFlow(flowEl) {
    flowEl.innerHTML = "";
    for (let i = 0; i < STEPS.length; i++) {
      const step = STEPS[i];
      const node = document.createElement("div");
      node.className = "flow-step";
      node.dataset.step = step.id;
      node.innerHTML = `
        <span class="flow-dot pending"></span>
        <span class="flow-label">${step.label}</span>
        <span class="flow-detail"></span>
      `;
      flowEl.appendChild(node);
      if (i < STEPS.length - 1) {
        const connector = document.createElement("div");
        connector.className = "flow-connector";
        flowEl.appendChild(connector);
      }
    }
  }

  function updateStep(flowEl, stepId, status, detail) {
    const node = flowEl.querySelector(`.flow-step[data-step="${stepId}"]`);
    if (!node) return;
    const dot = node.querySelector(".flow-dot");
    const detailSpan = node.querySelector(".flow-detail");
    dot.className = `flow-dot ${status}`;
    if (detail) detailSpan.textContent = detail;
  }

  // Create (scaffold, GitHub repo, then pause for PROMPT.md review)
  createBtn.addEventListener("click", async () => {
    const name = nameInput.value.trim();
    const goal = goalInput.value.trim();
    const constraints = wizard.querySelector("#wizard-constraints").value.trim();
    const primaryMetric = wizard.querySelector("#wizard-metric").value.trim();
    const metricDirection = wizard.querySelector("#wizard-metric-dir").value;
    const metricConstraint = wizard.querySelector("#wizard-metric-constraint").value.trim();
    const root = wizard.querySelector("#wizard-root").value.trim();
    const durationMinutes = parseInt(wizard.querySelector("#wizard-duration").value) || 5;
    if (!name) return;

    // Save experiments root for next time
    if (root && root !== settings.experimentsRoot) {
      window.nicolas.saveSettings({ ...settings, experimentsRoot: root });
    }

    // Disable inputs, show flowchart
    createBtn.disabled = true;
    createBtn.textContent = "Running\u2026";
    nameInput.disabled = true;
    goalInput.disabled = true;
    wizard.querySelector("#wizard-constraints").disabled = true;
    wizard.querySelector("#wizard-duration").disabled = true;
    wizard.querySelector("#wizard-root").disabled = true;
    wizard.querySelector("#wizard-browse").disabled = true;

    const flowEl = wizard.querySelector("#wizard-flow");
    flowEl.classList.remove("hidden");
    renderFlow(flowEl);

    let projectId = null;
    let hasError = false;
    let errorDetail = "";

    // Steps 1-4: scaffold via streaming endpoint
    try {
      const body = { name, goal, constraints, duration_minutes: durationMinutes, launch: false };
      if (primaryMetric) body.primary_metric = primaryMetric;
      if (metricDirection) body.metric_direction = metricDirection;
      if (metricConstraint) body.metric_constraint = metricConstraint;
      if (root) body.target = root + "/" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

      const resp = await fetch(`http://127.0.0.1:${serverPort}/experiments/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const msg = JSON.parse(line);
            if (msg.step) updateStep(flowEl, msg.step, msg.status, msg.detail || "");
            if (msg.project_id) projectId = msg.project_id;
            if (msg.status === "error") { hasError = true; errorDetail = msg.detail || ""; }
          } catch (e) { /* ignore */ }
        }
      }
    } catch (err) {
      hasError = true;
    }

    if (hasError || !projectId) {
      createBtn.textContent = errorDetail ? `Failed: ${errorDetail}` : "Failed";
      createBtn.style.background = "var(--error)";
      createBtn.style.borderColor = "var(--error)";
      createBtn.style.whiteSpace = "normal";
      createBtn.style.height = "auto";
      nameInput.disabled = false;
      goalInput.disabled = false;
      wizard.querySelector("#wizard-constraints").disabled = false;
      wizard.querySelector("#wizard-root").disabled = false;
      wizard.querySelector("#wizard-browse").disabled = false;
      return;
    }

    // Step 5: Create GitHub repo
    updateStep(flowEl, 5, "active", "");
    try {
      const ghResp = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/github`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: projectId, private: true }),
      });
      const ghData = await ghResp.json();
      if (ghData.ok) {
        updateStep(flowEl, 5, "done", ghData.url || "");
      } else {
        updateStep(flowEl, 5, "error", ghData.reason || "");
        // Non-fatal — continue without GitHub
      }
    } catch (e) {
      updateStep(flowEl, 5, "error", "Network error");
    }

    // Show PROMPT.md review
    createBtn.style.display = "none";
    wizard.querySelector("#wizard-cancel").style.display = "none";

    const reviewSection = document.createElement("div");
    reviewSection.className = "wizard-field";
    reviewSection.innerHTML = `
      <label style="margin-top:12px;">Review PROMPT.md <span style="font-weight:400; text-transform:none; color:var(--text-dim);">\u2014 edit before launching</span></label>
      <textarea id="wizard-prompt-review" rows="16" style="font-family:var(--mono); font-size:11px; line-height:1.5;">Loading\u2026</textarea>
      <div class="wizard-actions" style="margin-top:8px;">
        <button class="wizard-btn-cancel" id="wizard-back">Back</button>
        <button class="wizard-btn-create" id="wizard-launch">\u25B6 Launch Experiment</button>
      </div>
    `;
    wizard.appendChild(reviewSection);

    // Fetch PROMPT.md content
    try {
      const promptResp = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/prompt`);
      const promptData = await promptResp.json();
      wizard.querySelector("#wizard-prompt-review").value = promptData.ok ? promptData.content : "Failed to load PROMPT.md";
    } catch (e) {
      wizard.querySelector("#wizard-prompt-review").value = "Failed to load PROMPT.md";
    }

    // Back — go to experiment list
    wizard.querySelector("#wizard-back").addEventListener("click", () => {
      refreshExperiments(projectId);
    });

    // Launch — save edits, then launch
    wizard.querySelector("#wizard-launch").addEventListener("click", async () => {
      const launchBtn = wizard.querySelector("#wizard-launch");
      launchBtn.disabled = true;
      launchBtn.textContent = "Saving & launching\u2026";

      // Save edited PROMPT.md
      const editedContent = wizard.querySelector("#wizard-prompt-review").value;
      try {
        await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/prompt`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: editedContent }),
        });
      } catch (e) { /* continue */ }

      // Launch
      updateStep(flowEl, 6, "active", "");
      try {
        const launchResp = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/launch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: "claude-sonnet-4-6" }),
        });
        const launchData = await launchResp.json();
        if (launchData.ok) {
          updateStep(flowEl, 6, "done", launchData.tmux_session || "");
          launchBtn.textContent = "Launched!";
          launchBtn.style.background = "var(--green)";
          launchBtn.style.borderColor = "var(--green)";
          // Attach xterm to the new tmux session
          // Switch to session tab and attach directly (don't wait for refresh)
          setTimeout(async () => {
            switchEditorTab("session", { skipSessionAttach: true });
            if (launchData.tmux_session) {
              const xtermEl = document.getElementById("xterm-container");
              const emptyEl = document.getElementById("session-empty");
              if (emptyEl) emptyEl.classList.add("hidden");
              if (xtermEl) xtermEl.classList.remove("hidden");
              await attachToTerminalSession(projectId, launchData.tmux_session);
            }
            await refreshExperiments(projectId);
          }, 500);
        } else {
          updateStep(flowEl, 6, "error", launchData.reason || "");
          launchBtn.textContent = "Failed";
          launchBtn.style.background = "var(--error)";
          launchBtn.disabled = false;
        }
      } catch (e) {
        updateStep(flowEl, 6, "error", e.message);
        launchBtn.textContent = "Failed";
        launchBtn.disabled = false;
      }
    });
  });
}

function refreshExperiments(selectId) {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/experiments/list`)
    .then((r) => r.json())
    .then((data) => {
      if (data.projects) {
        renderProjectsList(data.projects);
        if (selectId) selectProject(selectId);
      }
    })
    .catch(() => {});
}

function selectProject(projectId) {
  const previousProject = currentProjectId;
  currentProjectId = projectId;

  // Show experiment name in tab bar
  const tabLabel = document.getElementById("editor-tabs-project-name");
  if (tabLabel) {
    const proj = cachedProjects.find((p) => p.id === projectId);
    tabLabel.textContent = proj ? (proj.name || proj.id) : "";
  }

  // Clear notification badge
  const expBtn = document.querySelector('.activity-btn[data-pane="sidebar-left"]');
  if (expBtn) expBtn.classList.remove("has-notification");

  // Update sidebar selection
  experimentsSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === projectId);
  });

  // Handle terminal session switching if Session tab is visible
  // Skip if terminal is already attached to this project (avoids double-attach race)
  const sessionView = document.getElementById("session-view");
  if (sessionView && !sessionView.classList.contains("hidden") && currentTerminalProject !== projectId) {
    showSessionTerminal(projectId);
  }

  renderProjectDetail(projectId);
}

function renderProjectDetail(projectId) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;

  const proj = cachedProjects.find((p) => p.id === projectId);
  if (!proj) return;

  // Show detail (only switch to control panel if no tab is active yet)
  welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  // Clean up any live timers from previous render
  if (window._activeTimers) {
    window._activeTimers.forEach(clearInterval);
    window._activeTimers = [];
  }
  detailEl.innerHTML = "";

  const activeTab = document.querySelector(".editor-tab.active");
  if (!activeTab) switchEditorTab("control-panel");

  // Light up Session tab when there's an active session
  const sessionTab = document.querySelector('.editor-tab[data-view="session"]');
  if (sessionTab) {
    sessionTab.classList.toggle("has-update", proj.active_sessions > 0);
  }

  // Collect all available numeric metric names across runs (needed for hero + chart)
  const allMetricNames = new Set();
  if (proj.runs) {
    for (const r of proj.runs) {
      for (const [k, v] of Object.entries(r.results || {})) {
        if (typeof v === "number") allMetricNames.add(k);
      }
    }
  }

  // Header: title row with hero metric right-aligned
  const header = document.createElement("div");
  header.className = "exp-detail-header";

  const titleRow = document.createElement("div");
  titleRow.className = "exp-detail-title-row";

  const titleLeft = document.createElement("div");
  titleLeft.className = "exp-detail-title-left";
  const title = document.createElement("h2");
  title.className = "exp-detail-title";
  title.textContent = proj.name || proj.id;
  titleLeft.appendChild(title);
  const isIdle = proj.active_sessions > 0 && proj.current_run === "Session active";
  if (proj.active_sessions > 0) {
    const badge = document.createElement("span");
    badge.className = isIdle ? "exp-detail-badge idle" : "exp-detail-badge running";
    badge.innerHTML = isIdle
      ? `<svg class="badge-pause-icon" width="8" height="10" viewBox="0 0 8 10"><rect x="0" y="0" width="2.5" height="10" rx="0.5" fill="currentColor"/><rect x="5.5" y="0" width="2.5" height="10" rx="0.5" fill="currentColor"/></svg> idle`
      : `<span class="badge-play-icon">\u25B6</span> active`;
    titleLeft.appendChild(badge);
  }

  // Total experiment time: sum of completed run durations + live current run
  if (proj.experiment_total_secs != null || proj.active_run_start) {
    const timerEl = document.createElement("span");
    timerEl.className = "exp-detail-timer";
    titleLeft.appendChild(timerEl);
    const baseSecs = proj.experiment_total_secs || 0;
    const activeStartMs = (!isIdle && proj.active_run_start) ? new Date(proj.active_run_start).getTime() : 0;
    const formatDur = (totalSecs) => {
      const h = Math.floor(totalSecs / 3600);
      const m = Math.floor((totalSecs % 3600) / 60);
      if (h > 0) return `${h}h ${m}m`;
      return `${m}m`;
    };
    if (activeStartMs) {
      // Live: base + elapsed since current run started
      const update = () => {
        const liveSecs = Math.max(0, (Date.now() - activeStartMs) / 1000);
        timerEl.textContent = formatDur(Math.floor(baseSecs + liveSecs));
      };
      update();
      const iv = setInterval(update, 10000); // update every 10s
      if (!window._activeTimers) window._activeTimers = [];
      window._activeTimers.push(iv);
    } else {
      timerEl.textContent = formatDur(Math.floor(baseSecs));
    }
  }
  // Compact stats inline with title (deduplicate by run ID, last entry wins)
  const decisionCounts = {};
  if (proj.runs) {
    const byId = new Map();
    for (const r of proj.runs) byId.set(r.id, r);
    for (const r of byId.values()) {
      const d = r.decision || r.status || "other";
      if (d === "running") continue; // don't count announcements
      decisionCounts[d] = (decisionCounts[d] || 0) + 1;
    }
  }
  const completedRuns = Object.values(decisionCounts).reduce((a, b) => a + b, 0);
  const statParts = [`${completedRuns} runs`];
  if (decisionCounts.keep) statParts.push(`${decisionCounts.keep} kept`);
  const statsSpan = document.createElement("span");
  statsSpan.className = "exp-detail-stats-inline";
  statsSpan.textContent = statParts.join(" \u00B7 ");
  titleLeft.appendChild(statsSpan);
  titleRow.appendChild(titleLeft);

  // Hero metric (right-aligned in title row)
  const currentKey = proj.key_metric_name && allMetricNames.has(proj.key_metric_name) ? proj.key_metric_name : "";
  if (currentKey) {
    const heroEl = document.createElement("div");
    heroEl.className = "hero-metric";
    const bestRun = findBestRun(proj.runs, currentKey);
    const currentVal = bestRun?.results?.[currentKey];
    const direction = isLowerBetter(currentKey) ? "\u2193" : "\u2191";
    heroEl.innerHTML = `
      <div class="hero-metric-value">${formatMetric(currentKey, currentVal)}</div>
      <div class="hero-metric-label">${currentKey} ${direction}</div>
    `;
    titleRow.appendChild(heroEl);
  }
  header.appendChild(titleRow);

  // Single description line (prefer experiment_summary, fall back to description)
  const descText = proj.experiment_summary || proj.description;
  if (descText) {
    const desc = document.createElement("div");
    desc.className = "exp-detail-meta";
    desc.textContent = descText;
    header.appendChild(desc);
  }

  // Status card: show current_run if running, idle notice if idle, otherwise latest_learning
  if (proj.current_run && !isIdle) {
    const current = document.createElement("div");
    current.className = "exp-detail-status-card running";
    const runNum = proj.run_count || "?";
    let runTimerHtml = "";
    if (proj.current_run_started) {
      runTimerHtml = ` <span class="run-timer" data-started="${proj.current_run_started}"></span>`;
    }
    current.innerHTML = `<span class="status-card-label running">Run ${runNum}:</span> ${escapeHtml(proj.current_run)}${runTimerHtml}`;
    header.appendChild(current);
    // Start per-run timer
    const runTimerEl = current.querySelector(".run-timer");
    if (runTimerEl) {
      const runStartMs = new Date(proj.current_run_started).getTime();
      const updateRunTimer = () => {
        const elapsed = Date.now() - runStartMs;
        if (elapsed < 0) { runTimerEl.textContent = ""; return; }
        const secs = Math.floor(elapsed / 1000);
        const m = Math.floor(secs / 60);
        const s = secs % 60;
        runTimerEl.textContent = `${m}:${String(s).padStart(2, "0")}`;
      };
      updateRunTimer();
      const iv = setInterval(updateRunTimer, 1000);
      if (!window._activeTimers) window._activeTimers = [];
      window._activeTimers.push(iv);
    }
  } else if (isIdle) {
    const idleCard = document.createElement("div");
    idleCard.className = "exp-detail-status-card idle";
    idleCard.innerHTML = `<span class="status-card-label idle">Idle</span> Agent waiting for input`;
    header.appendChild(idleCard);
  } else if (proj.latest_learning) {
    const learning = document.createElement("div");
    learning.className = "exp-detail-status-card";
    learning.innerHTML = `<span class="status-card-label">Latest:</span> ${escapeHtml(proj.latest_learning)}`;
    header.appendChild(learning);
  }

  // Research Insights from LLM enrichment
  if (proj.insights && (proj.insights.key_breakthrough || (proj.insights.lessons_learned && proj.insights.lessons_learned.length))) {
    const insightsEl = document.createElement("div");
    insightsEl.className = "research-insights";
    let insightsHtml = '<h3 class="insights-heading">Research Insights</h3>';
    if (proj.insights.key_breakthrough) {
      insightsHtml += `<div class="insight-breakthrough"><span class="insight-section-label">Key Breakthrough</span><p>${escapeHtml(proj.insights.key_breakthrough)}</p></div>`;
    }
    if (proj.insights.lessons_learned && proj.insights.lessons_learned.length) {
      insightsHtml += '<div class="insight-lessons"><span class="insight-section-label">Lessons Learned</span><ul>';
      for (const lesson of proj.insights.lessons_learned) {
        insightsHtml += `<li>${escapeHtml(lesson)}</li>`;
      }
      insightsHtml += '</ul></div>';
    }
    insightsEl.innerHTML = insightsHtml;
    header.appendChild(insightsEl);
  }

  // Goal chips
  {
    const goalsEl = document.createElement("div");
    goalsEl.className = "exp-detail-goals";
    if (proj.goals && proj.goals.length) {
      for (const g of proj.goals) {
        const chip = document.createElement("span");
        chip.className = "goal-chip";
        const dir = g.direction === "maximize" ? "\u2265" : "\u2264";
        chip.textContent = `${g.metric} ${dir} ${g.threshold}`;
        chip.title = "Click to remove";
        chip.style.cursor = "pointer";
        chip.addEventListener("click", () => {
          const updated = proj.goals.filter((x) => x !== g);
          fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ goals: updated }),
          })
            .then((r) => r.json())
            .then((data) => { if (data.ok) fetchExperimentsList(); })
            .catch(() => showToast("Failed to remove goal"));
        });
        goalsEl.appendChild(chip);
      }
    }
    const addGoalBtn = document.createElement("button");
    addGoalBtn.className = "goal-chip goal-chip-add";
    addGoalBtn.textContent = "+ goal";
    addGoalBtn.addEventListener("click", () => {
      const metric = prompt("Metric name (e.g. test_accuracy, val_loss):");
      if (!metric) return;
      const threshold = prompt("Threshold value:");
      if (!threshold || isNaN(parseFloat(threshold))) return;
      const direction = confirm("Maximize this metric?\n\nOK = maximize (higher is better)\nCancel = minimize (lower is better)") ? "maximize" : "minimize";
      const goals = [...(proj.goals || []), { metric, threshold: parseFloat(threshold), direction }];
      fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goals }),
      })
        .then((r) => r.json())
        .then((data) => {
          if (data.ok) { showToast("Goal added", "success"); fetchExperimentsList(); }
          else showToast(data.reason || "Failed to add goal");
        })
        .catch(() => showToast("Failed to add goal"));
    });
    goalsEl.appendChild(addGoalBtn);
    header.appendChild(goalsEl);
  }

  detailEl.appendChild(header);

  // Action buttons
  const actions = document.createElement("div");
  actions.className = "exp-detail-actions";

  if (proj.active_sessions > 0) {
    // Stop button
    const stopBtn = document.createElement("button");
    stopBtn.className = "action-btn action-btn-stop";
    stopBtn.textContent = "Stop";
    stopBtn.addEventListener("click", () => stopProject(proj.id, stopBtn));
    actions.appendChild(stopBtn);

    // Reload button next to stop
    const reloadBtn = document.createElement("button");
    reloadBtn.className = "paper-action-btn";
    reloadBtn.textContent = "\u21BB";
    reloadBtn.title = "Rescan & refresh (\u2318R)";
    reloadBtn.addEventListener("click", () => doReload(proj.id, reloadBtn));
    actions.appendChild(reloadBtn);

    // Open in Terminal (opens external Terminal.app with tmux attach)
    const attachBtn = document.createElement("button");
    attachBtn.className = "paper-action-btn";
    attachBtn.textContent = "Open in Terminal";
    attachBtn.addEventListener("click", () => attachToProject(proj.id, attachBtn));
    actions.appendChild(attachBtn);
  } else {
    // Launch button
    const launchBtn = document.createElement("button");
    launchBtn.className = "action-btn action-btn-launch";
    launchBtn.textContent = "Launch";
    launchBtn.addEventListener("click", () => {
      launchProject(proj.id, "claude-sonnet-4-6", launchBtn);
    });
    actions.appendChild(launchBtn);

    // Reload button next to launch
    const reloadBtn = document.createElement("button");
    reloadBtn.className = "paper-action-btn";
    reloadBtn.textContent = "\u21BB";
    reloadBtn.title = "Rescan & refresh (\u2318R)";
    reloadBtn.addEventListener("click", () => doReload(proj.id, reloadBtn));
    actions.appendChild(reloadBtn);

    // Queue sessions button
    if (proj.run_count > 0) {
      const queueBtn = document.createElement("button");
      queueBtn.className = "paper-action-btn";
      queueBtn.textContent = "Queue sessions";
      queueBtn.title = "Queue N continuation sessions";
      queueBtn.addEventListener("click", () => {
        const count = prompt("How many continuation sessions to queue?", "3");
        if (!count || isNaN(parseInt(count))) return;
        queueBtn.disabled = true;
        queueBtn.textContent = "Queuing...";
        fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/queue`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ count: parseInt(count) }),
        })
          .then((r) => r.json())
          .then((data) => {
            if (data.ok) {
              queueBtn.textContent = `Queued ${count}!`;
              showToast(`Queued ${count} continuation sessions`, "success");
              setTimeout(() => fetchExperimentsList(), 1000);
            } else {
              queueBtn.textContent = data.reason || "Failed";
            }
            setTimeout(() => { queueBtn.textContent = "Queue sessions"; queueBtn.disabled = false; }, 2000);
          })
          .catch(() => {
            queueBtn.textContent = "Error";
            showToast("Failed to queue sessions");
            setTimeout(() => { queueBtn.textContent = "Queue sessions"; queueBtn.disabled = false; }, 2000);
          });
      });
      actions.appendChild(queueBtn);
    }
  }

  // Start Campaign button (only if project has goals but no active campaign)
  if (proj.goals && proj.goals.length && !proj.campaign) {
    const campaignBtn = document.createElement("button");
    campaignBtn.className = "action-btn action-btn-launch";
    campaignBtn.textContent = "Start Campaign";
    campaignBtn.addEventListener("click", () => showCampaignConfig(proj));
    actions.appendChild(campaignBtn);
  }

  const resultsBtn = document.createElement("button");
  resultsBtn.className = "paper-action-btn";
  resultsBtn.textContent = "View results";
  resultsBtn.addEventListener("click", () => {
    currentProjectId = proj.id;
    switchEditorTab("results");
  });
  actions.appendChild(resultsBtn);

  // Settings button (text, not gear icon)
  const settingsActionBtn = document.createElement("button");
  settingsActionBtn.className = "paper-action-btn";
  settingsActionBtn.textContent = "Settings";
  settingsActionBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    showSettingsPopover(proj, settingsActionBtn);
  });
  actions.appendChild(settingsActionBtn);

  detailEl.appendChild(actions);


  // Metric chart
  let activeMetric = "";

  if (allMetricNames.size > 0 && proj.runs && proj.runs.length >= 2) {
    const defaultMetric = proj.key_metric_name && allMetricNames.has(proj.key_metric_name)
      ? proj.key_metric_name
      : allMetricNames.values().next().value;
    activeMetric = defaultMetric;

    const chartContainer = document.createElement("div");
    chartContainer.className = "metric-chart-container";

    const chartHeader = document.createElement("div");
    chartHeader.style.cssText = "display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;";

    const chartTitle = document.createElement("div");
    chartTitle.className = "metric-chart-title";
    chartTitle.style.marginBottom = "0";
    const direction = isLowerBetter(activeMetric) ? "\u2193" : "\u2191";
    chartTitle.textContent = `${activeMetric} ${direction}`;

    // Chart export button
    const exportBtn = document.createElement("button");
    exportBtn.className = "chart-export-btn";
    exportBtn.title = "Export chart as PNG";
    exportBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`;
    exportBtn.addEventListener("click", () => {
      if (!serverPort || !currentProjectId || !activeMetric) return;
      exportBtn.disabled = true;
      const logParam = chartLogScale ? "&log_scale=1" : "";
      const logSuffix = chartLogScale ? "_log" : "";
      fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(currentProjectId)}/chart/export?metric=${encodeURIComponent(activeMetric)}&format=png${logParam}`)
        .then((r) => {
          if (!r.ok) throw new Error("Export failed");
          return r.blob();
        })
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `${proj.name || proj.id}_${activeMetric}${logSuffix}.png`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          URL.revokeObjectURL(url);
        })
        .catch(() => {
          // Fallback: export canvas directly
          try {
            const dataUrl = canvas.toDataURL("image/png");
            const a = document.createElement("a");
            a.href = dataUrl;
            a.download = `${proj.name || proj.id}_${activeMetric}${logSuffix}.png`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
          } catch (e) {
            console.error("Chart export failed:", e);
          }
        })
        .finally(() => { exportBtn.disabled = false; });
    });

    chartHeader.appendChild(chartTitle);

    // Right-side controls group
    const chartControls = document.createElement("div");
    chartControls.style.cssText = "display:flex;align-items:center;gap:6px;";

    // Metric selector (only if multiple metrics)
    if (allMetricNames.size > 1) {
      const selector = document.createElement("select");
      selector.className = "chart-metric-select";
      for (const m of allMetricNames) {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        if (m === activeMetric) opt.selected = true;
        selector.appendChild(opt);
      }
      selector.addEventListener("change", () => {
        activeMetric = selector.value;
        const dir = isLowerBetter(activeMetric) ? "\u2193" : "\u2191";
        chartTitle.textContent = `${activeMetric} ${dir}`;
        renderMetricChart(canvas, proj.runs, activeMetric, liveMetrics[proj.id], { logScale: chartLogScale });
      });
      chartControls.appendChild(selector);
    }

    // Log/linear toggle (two-state on/off)
    const logToggle = document.createElement("button");
    logToggle.className = "chart-log-toggle";
    logToggle.title = "Toggle log scale";
    logToggle.innerHTML = `<span class="chart-log-toggle-label chart-log-toggle-lin">lin</span><span class="chart-log-toggle-label chart-log-toggle-log">log</span>`;
    if (chartLogScale) logToggle.classList.add("active");
    logToggle.addEventListener("click", () => {
      chartLogScale = !chartLogScale;
      logToggle.classList.toggle("active", chartLogScale);
      renderMetricChart(canvas, proj.runs, activeMetric, liveMetrics[proj.id], { logScale: chartLogScale });
    });
    chartControls.appendChild(logToggle);

    chartControls.appendChild(exportBtn);
    chartHeader.appendChild(chartControls);
    chartContainer.appendChild(chartHeader);

    const canvas = document.createElement("canvas");
    canvas.className = "metric-chart-canvas";
    chartContainer.appendChild(canvas);

    // Resize handle for chart height
    const resizeHandle = document.createElement("div");
    resizeHandle.className = "chart-resize-handle";
    chartContainer.appendChild(resizeHandle);

    resizeHandle.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const startY = e.clientY;
      const startH = canvas.getBoundingClientRect().height;
      const onMove = (ev) => {
        const newH = Math.max(100, Math.min(600, startH + ev.clientY - startY));
        canvas.style.height = newH + "px";
        renderMetricChart(canvas, proj.runs, activeMetric, liveMetrics[proj.id], { logScale: chartLogScale });
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });

    detailEl.appendChild(chartContainer);

    // Render after DOM insertion so dimensions are available
    requestAnimationFrame(() => {
      renderMetricChart(canvas, proj.runs, activeMetric, liveMetrics[proj.id], { logScale: chartLogScale });
      setupChartResize(chartContainer, canvas, proj.runs, () => activeMetric, () => proj.id, () => ({ logScale: chartLogScale }));
    });
  }

  // Runs grid
  if (proj.runs && proj.runs.length) {
    const section = document.createElement("div");
    section.className = "exp-detail-section";
    const sTitle = document.createElement("h3");
    sTitle.textContent = "Runs";
    section.appendChild(sTitle);

    // Sort controls
    const runsInOrder = proj.runs; // chronological (oldest first)
    let currentSort = "newest";

    const sortModes = [
      { key: "newest", label: "Newest" },
      { key: "oldest", label: "Oldest" },
      { key: "best", label: "Best metric" },
      { key: "decision", label: "Keeps first" },
    ];

    function sortRuns(mode) {
      let sorted = [...runsInOrder];
      if (mode === "newest") sorted.reverse();
      else if (mode === "oldest") { /* already chronological */ }
      else if (mode === "best") {
        sorted.sort((a, b) => {
          const va = a.results?.[activeMetric];
          const vb = b.results?.[activeMetric];
          if (va == null && vb == null) return 0;
          if (va == null) return 1;
          if (vb == null) return -1;
          return isLowerBetter(activeMetric) ? va - vb : vb - va;
        });
      } else if (mode === "decision") {
        const order = { keep: 0, discard: 1, crash: 2 };
        sorted.sort((a, b) => (order[a.decision] ?? 3) - (order[b.decision] ?? 3));
      }
      return sorted;
    }

    const sortBar = document.createElement("div");
    sortBar.className = "exp-runs-sort";
    for (const sm of sortModes) {
      const btn = document.createElement("button");
      btn.textContent = sm.label;
      btn.dataset.sort = sm.key;
      if (sm.key === currentSort) btn.classList.add("active");
      btn.addEventListener("click", () => {
        currentSort = sm.key;
        sortBar.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        renderRunCards(sortRuns(currentSort));
      });
      sortBar.appendChild(btn);
    }
    section.appendChild(sortBar);

    const grid = document.createElement("div");
    grid.className = "exp-runs-grid";

    function renderRunCards(runs) {
      grid.innerHTML = "";
      for (let ri = 0; ri < runs.length; ri++) {
        const run = runs[ri];
        const origIndex = runsInOrder.indexOf(run);
        const card = document.createElement("div");
        card.className = "exp-run-card";

        const runHeader = document.createElement("div");
        runHeader.className = "exp-run-header";

        const runName = document.createElement("span");
        runName.className = "exp-run-name";
        const displayNum = `#${runDisplayNum(run)}`;
        const rawName = run.name || "";
        const truncatedName = rawName.length > 40 ? rawName.slice(0, 40) + "\u2026" : rawName;
        runName.textContent = truncatedName ? `${displayNum} ${truncatedName}` : displayNum;
        runHeader.appendChild(runName);

        // Metric delta
        if (activeMetric && run.results?.[activeMetric] != null) {
          const val = run.results[activeMetric];
          let deltaText = "";
          let deltaClass = "";

          if (run.baseline_comparison && run.baseline_comparison.delta != null) {
            const d = run.baseline_comparison.delta;
            deltaText = `${d >= 0 ? "+" : ""}${(d * 100).toFixed(1)}% ${activeMetric}`;
            const lowerB = isLowerBetter(activeMetric);
            deltaClass = (lowerB ? d < 0 : d > 0) ? "positive" : (d === 0 ? "" : "negative");
          } else if (origIndex > 0) {
            // Compute vs previous kept run (or just previous)
            let prevVal = null;
            for (let pi = origIndex - 1; pi >= 0; pi--) {
              const pr = runsInOrder[pi];
              if (pr.results?.[activeMetric] != null && (pr.decision === "keep" || prevVal === null)) {
                prevVal = pr.results[activeMetric];
                if (pr.decision === "keep") break;
              }
            }
            if (prevVal !== null && prevVal !== 0) {
              const d = (val - prevVal) / Math.abs(prevVal);
              deltaText = `${d >= 0 ? "+" : ""}${(d * 100).toFixed(1)}% ${activeMetric}`;
              const lowerB = isLowerBetter(activeMetric);
              deltaClass = (lowerB ? d < 0 : d > 0) ? "positive" : (d === 0 ? "" : "negative");
            }
          }

          if (deltaText) {
            const deltaEl = document.createElement("span");
            deltaEl.className = `exp-run-delta ${deltaClass}`;
            deltaEl.textContent = deltaText;
            runHeader.appendChild(deltaEl);
          }
        }

        // Sparkline (metric progression up to this run)
        if (activeMetric) {
          const sparkValues = [];
          for (let si = 0; si <= origIndex; si++) {
            const sv = runsInOrder[si].results?.[activeMetric];
            if (typeof sv === "number" && isFinite(sv)) sparkValues.push(sv);
          }
          if (sparkValues.length >= 2) {
            const sparkEl = document.createElement("span");
            sparkEl.className = "run-sparkline";
            sparkEl.innerHTML = sparklineSvg(sparkValues, sparkValues.length - 1);
            runHeader.appendChild(sparkEl);
          }
        }

        if (run.decision) {
          const decision = document.createElement("span");
          decision.className = `exp-run-decision ${run.decision}`;
          decision.textContent = run.decision;
          runHeader.appendChild(decision);
        }
        card.appendChild(runHeader);

        if (run.key_metric) {
          const metric = document.createElement("div");
          metric.className = "exp-run-metric";
          metric.textContent = run.key_metric;
          card.appendChild(metric);
        }

        // Description (agent's announcement of what it's trying)
        if (run.description && run.description !== run.hypothesis) {
          const desc = document.createElement("div");
          desc.className = "exp-run-description";
          desc.textContent = run.description;
          card.appendChild(desc);
        }

        // Hypothesis
        if (run.hypothesis) {
          const hyp = document.createElement("div");
          hyp.className = "exp-run-hypothesis";
          hyp.textContent = run.hypothesis;
          card.appendChild(hyp);
        }

        // HP diff vs previous run
        if (run.hyperparameters && Object.keys(run.hyperparameters).length && origIndex > 0) {
          const prevRun = runsInOrder[origIndex - 1];
          const prevHP = prevRun?.hyperparameters || {};
          const diffs = [];
          for (const [k, v] of Object.entries(run.hyperparameters)) {
            if (JSON.stringify(prevHP[k]) !== JSON.stringify(v)) {
              const prev = prevHP[k] != null ? String(prevHP[k]) : "–";
              diffs.push(`${k}: ${prev} \u2192 ${v}`);
            }
          }
          if (diffs.length) {
            const hpDiff = document.createElement("div");
            hpDiff.className = "exp-run-hp-diff";
            hpDiff.textContent = diffs.join("  \u00B7  ");
            card.appendChild(hpDiff);
          }
        }

        const meta = [];
        if (run.duration_minutes) meta.push(`${run.duration_minutes}min`);
        if (run.started_at) {
          const d = new Date(run.started_at);
          if (!isNaN(d)) meta.push(d.toLocaleDateString());
        }
        if (run.tags && run.tags.length) meta.push(run.tags.join(", "));
        if (meta.length) {
          const metaEl = document.createElement("div");
          metaEl.className = "exp-run-meta";
          metaEl.textContent = meta.join(" \u00B7 ");
          card.appendChild(metaEl);
        }

        // Reasoning (collapsible)
        if (run.reasoning) {
          const reasoning = document.createElement("div");
          reasoning.className = "exp-run-reasoning collapsed";
          reasoning.textContent = run.reasoning;
          reasoning.addEventListener("click", () => {
            reasoning.classList.toggle("collapsed");
          });
          card.appendChild(reasoning);

          // Toggle label
          const toggleLabel = document.createElement("div");
          toggleLabel.style.cssText = "font-size:10px;color:var(--text-dim);cursor:pointer;margin-top:4px;";
          toggleLabel.textContent = "\u25B6 reasoning";
          toggleLabel.addEventListener("click", () => {
            reasoning.classList.toggle("collapsed");
            toggleLabel.textContent = reasoning.classList.contains("collapsed")
              ? "\u25B6 reasoning" : "\u25BC reasoning";
          });
          card.appendChild(toggleLabel);
        }

        grid.appendChild(card);
      }
    }

    // Initial render with default sort
    renderRunCards(sortRuns(currentSort));
    section.appendChild(grid);
    detailEl.appendChild(section);
  }

  // Campaign timeline (after runs grid)
  if (proj.campaign) {
    const campaign = proj.campaign;
    const section = document.createElement("div");
    section.className = "exp-detail-section campaign-section";

    const sTitle = document.createElement("h3");
    sTitle.textContent = "Campaign";
    section.appendChild(sTitle);

    // Status + progress
    const statusRow = document.createElement("div");
    statusRow.className = "campaign-status-row";

    const statusBadge = document.createElement("span");
    statusBadge.className = `campaign-status ${campaign.status}`;
    statusBadge.textContent = campaign.status;
    statusRow.appendChild(statusBadge);

    if (campaign.budget) {
      const progress = document.createElement("div");
      progress.className = "campaign-progress";
      const launched = campaign.sessions_launched || 0;
      const total = campaign.budget.max_sessions || 10;
      const pct = Math.min(100, (launched / total) * 100);
      progress.innerHTML = `
        <div class="campaign-progress-bar">
          <div class="campaign-progress-fill" style="width:${pct}%"></div>
        </div>
        <span class="campaign-progress-label">${launched}/${total} sessions</span>
      `;
      statusRow.appendChild(progress);
    }
    section.appendChild(statusRow);

    // Timeline nodes (from runs)
    if (proj.runs && proj.runs.length) {
      const timeline = document.createElement("div");
      timeline.className = "campaign-timeline";
      for (const run of proj.runs) {
        const node = document.createElement("div");
        node.className = `campaign-node ${run.decision || run.status || ""}`;

        const dot = document.createElement("div");
        dot.className = "campaign-node-dot";
        node.appendChild(dot);

        const info = document.createElement("div");
        info.className = "campaign-node-info";
        info.innerHTML = `<strong>#${runDisplayNum(run)} ${run.name || run.id}</strong>`;
        if (run.key_metric) info.innerHTML += `<br><span class="dim">${run.key_metric}</span>`;
        if (run.hypothesis) info.innerHTML += `<br><span class="dim">${run.hypothesis.slice(0, 60)}${run.hypothesis.length > 60 ? "..." : ""}</span>`;
        node.appendChild(info);

        timeline.appendChild(node);
      }
      section.appendChild(timeline);
    }

    // Campaign controls
    const controls = document.createElement("div");
    controls.className = "campaign-controls";

    if (campaign.status === "running") {
      const pauseBtn = document.createElement("button");
      pauseBtn.className = "action-btn action-btn-stop";
      pauseBtn.textContent = "Pause";
      pauseBtn.addEventListener("click", () => campaignAction(proj.id, "pause", pauseBtn));
      controls.appendChild(pauseBtn);

      const stopBtn = document.createElement("button");
      stopBtn.className = "action-btn action-btn-stop";
      stopBtn.textContent = "Stop Campaign";
      stopBtn.addEventListener("click", () => campaignAction(proj.id, "stop", stopBtn));
      controls.appendChild(stopBtn);
    } else if (campaign.status === "paused") {
      const resumeBtn = document.createElement("button");
      resumeBtn.className = "action-btn action-btn-launch";
      resumeBtn.textContent = "Resume";
      resumeBtn.addEventListener("click", () => campaignAction(proj.id, "resume", resumeBtn));
      controls.appendChild(resumeBtn);

      const stopBtn = document.createElement("button");
      stopBtn.className = "action-btn action-btn-stop";
      stopBtn.textContent = "Stop Campaign";
      stopBtn.addEventListener("click", () => campaignAction(proj.id, "stop", stopBtn));
      controls.appendChild(stopBtn);
    }

    section.appendChild(controls);

    // Steering input
    if (campaign.status === "running" || campaign.status === "paused") {
      const steerSection = document.createElement("div");
      steerSection.className = "campaign-steer";

      const steerLabel = document.createElement("label");
      steerLabel.textContent = "Steer next session:";
      steerSection.appendChild(steerLabel);

      const steerInput = document.createElement("textarea");
      steerInput.className = "campaign-steer-input";
      steerInput.placeholder = "e.g., Try a larger learning rate, focus on data augmentation...";
      steerInput.rows = 2;
      steerSection.appendChild(steerInput);

      const steerBtn = document.createElement("button");
      steerBtn.className = "paper-action-btn";
      steerBtn.textContent = "Send";
      steerBtn.addEventListener("click", () => {
        const text = steerInput.value.trim();
        if (!text) return;
        steerBtn.disabled = true;
        steerBtn.textContent = "Sending...";
        fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/steer`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({text}),
        })
          .then(r => r.json())
          .then(data => {
            steerBtn.textContent = data.ok ? "Sent!" : "Failed";
            if (data.ok) steerInput.value = "";
            setTimeout(() => { steerBtn.textContent = "Send"; steerBtn.disabled = false; }, 2000);
          })
          .catch(() => {
            steerBtn.textContent = "Error";
            showToast("Failed to send steering instruction");
            setTimeout(() => { steerBtn.textContent = "Send"; steerBtn.disabled = false; }, 2000);
          });
      });
      steerSection.appendChild(steerBtn);
      section.appendChild(steerSection);
    }

    detailEl.appendChild(section);
  }

}

function showSettingsPopover(proj, anchorBtn) {
  // Toggle popover
  const existing = document.querySelector(".exp-settings-popover");
  if (existing) { existing.remove(); return; }

  const popover = document.createElement("div");
  popover.className = "exp-settings-popover";

  // Rename experiment
  const renameBtn = document.createElement("button");
  renameBtn.className = "action-btn";
  renameBtn.textContent = "Rename";
  renameBtn.addEventListener("click", () => {
    const current = proj.name || proj.id;
    const newName = prompt("Rename experiment:", current);
    if (!newName || newName === current) return;
    renameBtn.disabled = true;
    renameBtn.textContent = "Saving...";
    fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.ok) {
          popover.remove();
          fetchExperimentsList();
        } else {
          renameBtn.textContent = "Failed";
          setTimeout(() => { renameBtn.textContent = "Rename"; renameBtn.disabled = false; }, 2000);
        }
      })
      .catch(() => {
        renameBtn.textContent = "Error";
        setTimeout(() => { renameBtn.textContent = "Rename"; renameBtn.disabled = false; }, 2000);
      });
  });

  // Backfill runs — inject backfill instructions into the running session
  const backfillBtn = document.createElement("button");
  backfillBtn.className = "action-btn";
  backfillBtn.textContent = "Backfill missing runs";
  backfillBtn.title = "Send instructions to the agent to log all missing experiment results";
  backfillBtn.addEventListener("click", () => {
    if (!confirm("This will instruct the agent to scan git history and log all missing runs to runs.jsonl. This may take a few minutes. Continue?")) return;
    popover.remove();
    startBackfill(proj);
  });

  // Rescan full history — force re-read of all Claude logs + events
  const fullRescanBtn = document.createElement("button");
  fullRescanBtn.className = "action-btn";
  fullRescanBtn.textContent = "Rescan full history";
  fullRescanBtn.title = "Deep rescan: re-read all Claude Code logs, events, and git history";
  fullRescanBtn.addEventListener("click", () => {
    fullRescanBtn.disabled = true;
    fullRescanBtn.textContent = "Scanning...";
    fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/scan?full=1`, { method: "POST" })
      .then((r) => r.json())
      .then((data) => {
        if (data.ok) {
          const parts = [];
          if (data.new_runs) parts.push(`${data.new_runs} new`);
          if (data.backfilled) parts.push(`${data.backfilled} backfilled`);
          fullRescanBtn.textContent = parts.length ? parts.join(", ") : "Up to date";
          fetchExperimentsList();
        } else {
          fullRescanBtn.textContent = data.reason || "Failed";
        }
        setTimeout(() => { fullRescanBtn.textContent = "Rescan full history"; fullRescanBtn.disabled = false; }, 3000);
      })
      .catch(() => {
        fullRescanBtn.textContent = "Error";
        setTimeout(() => { fullRescanBtn.textContent = "Rescan full history"; fullRescanBtn.disabled = false; }, 3000);
      });
  });

  const separator = document.createElement("div");
  separator.className = "danger-warning";
  separator.textContent = "Danger zone";

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "action-btn action-btn-danger";
  deleteBtn.textContent = "Delete experiment";
  deleteBtn.addEventListener("click", () => {
    const name = proj.name || proj.id;
    if (!confirm(`Delete "${name}" from tracking?\n\nThis removes ${proj.run_count || 0} run(s) from Distillate.\nSource files and GitHub repo will NOT be deleted.`)) return;
    const typed = prompt(`Type the experiment name to confirm deletion:\n\n${name}`);
    if (typed !== name) { alert("Name didn't match. Deletion cancelled."); return; }

    deleteBtn.disabled = true;
    deleteBtn.textContent = "Deleting...";
    const detailEl = document.getElementById("experiment-detail");
    const welcomeEl = document.getElementById("welcome");
    fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}`, { method: "DELETE" })
      .then(r => r.json())
      .then(data => {
        if (data.ok) {
          currentProjectId = null;
          fetchExperimentsList();
          if (detailEl) detailEl.innerHTML = "";
          if (welcomeEl) { welcomeEl.classList.remove("hidden"); }
          if (detailEl) detailEl.classList.add("hidden");
        } else {
          alert(data.reason || "Failed to delete");
          deleteBtn.disabled = false;
          deleteBtn.textContent = "Delete experiment";
        }
      })
      .catch(() => {
        alert("Network error");
        deleteBtn.disabled = false;
        deleteBtn.textContent = "Delete experiment";
      });
  });

  const warning = document.createElement("p");
  warning.className = "danger-warning";
  warning.style.fontSize = "11px";
  warning.textContent = "Removes from tracking. Files and repo untouched.";

  // Primary metric selector
  const metricGroup = document.createElement("div");
  metricGroup.style.cssText = "margin-bottom:8px;";
  const metricLabel = document.createElement("label");
  metricLabel.style.cssText = "font-size:10px;color:var(--text-dim);display:block;margin-bottom:4px;";
  metricLabel.textContent = "Primary metric";
  metricGroup.appendChild(metricLabel);
  const metricSelect = document.createElement("select");
  metricSelect.style.cssText = "width:100%;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:12px;padding:4px 6px;";
  // Collect all metric names from runs
  const metricNames = new Set();
  for (const run of Object.values(proj.runs || {})) {
    for (const k of Object.keys(run.results || {})) metricNames.add(k);
  }
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = "Auto-detect";
  metricSelect.appendChild(noneOpt);
  for (const m of metricNames) {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    if (m === proj.key_metric_name) opt.selected = true;
    metricSelect.appendChild(opt);
  }
  metricSelect.addEventListener("change", () => {
    fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key_metric_name: metricSelect.value }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.ok) { showToast("Primary metric updated", "success"); fetchExperimentsList(); }
      })
      .catch(() => showToast("Failed to update metric"));
  });
  metricGroup.appendChild(metricSelect);

  popover.appendChild(metricGroup);
  popover.appendChild(backfillBtn);
  popover.appendChild(fullRescanBtn);
  popover.appendChild(separator);
  popover.appendChild(renameBtn);
  popover.appendChild(deleteBtn);
  popover.appendChild(warning);
  anchorBtn.parentElement.appendChild(popover);

  // Close on outside click — delay listener so the opening click doesn't immediately close
  const close = (ev) => {
    if (!popover.contains(ev.target) && ev.target !== anchorBtn) {
      popover.remove();
      document.removeEventListener("click", close);
    }
  };
  requestAnimationFrame(() => requestAnimationFrame(() => {
    document.addEventListener("click", close);
  }));
}

// Backfill: write prompt to file, then inject a short command into the terminal
async function startBackfill(proj) {
  // Insert progress banner above editor tabs (visible regardless of active tab)
  const editorArea = document.getElementById("editor-area");
  if (!editorArea) return;

  let bar = editorArea.querySelector(".backfill-progress");
  if (bar) bar.remove();
  bar = document.createElement("div");
  bar.className = "backfill-progress";
  bar.innerHTML = `
    <div class="backfill-progress-inner">
      <div class="backfill-progress-bar indeterminate"></div>
    </div>
    <span class="backfill-progress-text">Sending backfill instructions...</span>
  `;
  editorArea.insertBefore(bar, editorArea.firstChild);

  const progressText = bar.querySelector(".backfill-progress-text");
  const progressBar = bar.querySelector(".backfill-progress-bar");

  const backfillPrompt =
    "STOP. You have experiment results NOT logged to .distillate/runs.jsonl. Fix this NOW before doing anything else. " +
    "1) Read .distillate/runs.jsonl to see existing runs. " +
    "2) Run git log --oneline to find ALL commits with experiment results. " +
    "3) Use git log --format='%aI %s' for timestamps. " +
    "4) For EACH commit with results not in runs.jsonl, read the diff and extract metrics. " +
    '5) Append entries: {"$schema":"distillate/run/v1","id":"run_NNN","timestamp":"<ISO8601>","status":"keep|discard","description":"...","hypothesis":"...","hyperparameters":{...},"results":{...},"reasoning":"..."}. ' +
    "6) Sequential IDs after the last existing run. " +
    "7) Include ALL numeric results. " +
    "8) Chronological order. " +
    "9) When done: git add .distillate/runs.jsonl && git commit -m 'backfill: log N missing runs' && git push.";

  if (proj.active_sessions > 0) {
    // Agent is running — type the instruction directly into the Claude Code input
    const sessions = proj.sessions || {};
    // sessions dict from the server only contains active sessions (already filtered)
    const activeSession = Object.values(sessions).find((s) => s.tmux_session);
    const sessionName = activeSession?.tmux_session;
    if (!sessionName) {
      progressText.textContent = "No active session found";
      progressBar.classList.remove("indeterminate"); progressBar.style.background = "var(--danger, #ef4444)";
      progressBar.style.width = "100%";
      setTimeout(() => bar.remove(), 5000);
      return;
    }

    // Switch to session tab FIRST so xterm container is visible for init
    switchEditorTab("session");
    await sleep(100);

    const attachResult = await ensureTerminalAttached(proj.id, sessionName);
    if (attachResult && !attachResult.ok) {
      progressText.textContent = "Could not attach to terminal: " + (attachResult.reason || "unknown");
      progressBar.classList.remove("indeterminate"); progressBar.style.background = "var(--danger, #ef4444)";
      progressBar.style.width = "100%";
      setTimeout(() => bar.remove(), 5000);
      return;
    }
    await sleep(300);

    await window.nicolas.terminalInput(proj.id, backfillPrompt + "\r");
    progressBar.classList.remove("indeterminate");
    progressBar.style.width = "100%";
    progressBar.style.background = "var(--green, #22c55e)";
    progressText.textContent = "Backfill instructions sent — watch the Session tab";
    setTimeout(() => bar.remove(), 4000);
  } else {
    // Agent stopped (tmux session is dead) — launch a fresh session with backfill prompt
    progressText.textContent = "Launching backfill agent...";
    progressBar.style.width = "30%";
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/launch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: "claude-sonnet-4-6", prompt_override: backfillPrompt }),
      });
      const data = await r.json();
      if (!data.ok) {
        progressText.textContent = "Failed: " + (data.reason || "unknown");
        progressBar.classList.remove("indeterminate"); progressBar.style.background = "var(--danger, #ef4444)";
        progressBar.style.width = "100%";
        setTimeout(() => bar.remove(), 5000);
        return;
      }
      // Switch to session tab to watch
      if (data.tmux_session) {
        await ensureTerminalAttached(proj.id, data.tmux_session);
      }
      switchEditorTab("session");
      progressBar.classList.remove("indeterminate");
      progressBar.style.width = "100%";
      progressBar.style.background = "var(--green, #22c55e)";
      progressText.textContent = "Backfill agent launched — watch the Session tab, reload when done";
      fetchExperimentsList();
      setTimeout(() => bar.remove(), 6000);
    } catch (err) {
      progressText.textContent = "Error: " + err.message;
      progressBar.classList.remove("indeterminate"); progressBar.style.background = "var(--danger, #ef4444)";
      progressBar.style.width = "100%";
      setTimeout(() => bar.remove(), 5000);
    }
  }
}

// Helper: ensure terminal is attached, with await on the IPC.
// Returns {ok: true} on success, {ok: false, reason} on failure, or null if already attached.
async function ensureTerminalAttached(projectId, sessionName) {
  if (!window.nicolas || !window.xtermBridge) return { ok: false, reason: "no bridge" };
  if (currentTerminalProject === projectId && currentTerminalSession === sessionName) return null;

  if (currentTerminalProject && currentTerminalProject !== projectId) {
    window.nicolas.terminalDetach(currentTerminalProject);
  }

  const ready = await ensureTerminalReady();
  if (!ready) return { ok: false, reason: "terminal init failed" };

  window.xtermBridge.clear();
  window.xtermBridge.fit();

  const dims = window.xtermBridge.getDimensions();
  const result = await window.nicolas.terminalAttach(projectId, sessionName, dims.cols, dims.rows);
  if (result && result.ok) {
    currentTerminalProject = projectId;
    currentTerminalSession = sessionName;
  }
  return result;
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// ---------------------------------------------------------------------------
// Cross-Experiment Comparison Grid (M4 stub)
// ---------------------------------------------------------------------------

function showComparisonGrid() {
  if (!serverPort || cachedProjects.length < 2) return;

  const ids = cachedProjects.map((p) => p.id).join(",");
  fetch(`http://127.0.0.1:${serverPort}/experiments/compare?ids=${encodeURIComponent(ids)}`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok) return;
      renderComparisonGrid(data);
    })
    .catch(() => {});
}

function renderComparisonGrid(data) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;

  currentProjectId = null;
  experimentsSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => el.classList.remove("active"));

  welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  detailEl.innerHTML = "";

  switchEditorTab("control-panel");

  const header = document.createElement("h2");
  header.className = "exp-detail-title";
  header.textContent = "Experiment Comparison";
  detailEl.appendChild(header);

  if (!data.metrics || !data.metrics.length) {
    const empty = document.createElement("div");
    empty.className = "sidebar-empty";
    empty.textContent = "No comparable metrics found across experiments.";
    detailEl.appendChild(empty);
    return;
  }

  // Build comparison table
  const table = document.createElement("table");
  table.className = "comparison-table";

  // Header row: experiment names
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  headerRow.innerHTML = "<th>Metric</th>";
  for (const proj of data.projects) {
    const th = document.createElement("th");
    th.textContent = proj.name;
    th.title = `${proj.run_count} runs`;
    headerRow.appendChild(th);
  }
  thead.appendChild(headerRow);
  table.appendChild(thead);

  // Metric rows
  const tbody = document.createElement("tbody");
  for (const metric of data.metrics) {
    const row = document.createElement("tr");
    const labelCell = document.createElement("td");
    labelCell.className = "comparison-metric-name";
    labelCell.textContent = metric;
    row.appendChild(labelCell);

    // Find best value for highlighting
    let bestVal = null;
    const lowerBetter = /loss|error|mae|rmse|mse|perplexity/.test(metric);
    for (const proj of data.projects) {
      const val = proj.best_metrics[metric];
      if (val != null) {
        if (bestVal === null || (lowerBetter ? val < bestVal : val > bestVal)) {
          bestVal = val;
        }
      }
    }

    for (const proj of data.projects) {
      const cell = document.createElement("td");
      const val = proj.best_metrics[metric];
      if (val != null) {
        cell.textContent = typeof val === "number" ? formatMetric(metric, val) : val;
        if (val === bestVal) cell.className = "comparison-best";
      } else {
        cell.textContent = "\u2014";
        cell.className = "comparison-na";
      }
      row.appendChild(cell);
    }
    tbody.appendChild(row);
  }
  table.appendChild(tbody);
  detailEl.appendChild(table);

  // "Save as Template" buttons
  const templateSection = document.createElement("div");
  templateSection.className = "exp-detail-section";
  const tTitle = document.createElement("h3");
  tTitle.textContent = "Templates";
  templateSection.appendChild(tTitle);

  for (const proj of data.projects) {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:8px;margin:4px 0;";

    const name = document.createElement("span");
    name.textContent = proj.name;
    name.style.cssText = "font-size:12px;color:var(--text);";
    row.appendChild(name);

    const btn = document.createElement("button");
    btn.className = "paper-action-btn";
    btn.textContent = "Save as Template";
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "Saving...";
      fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/save-template`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: proj.name }),
      })
        .then((r) => r.json())
        .then((d) => {
          btn.textContent = d.ok ? `Saved: ${d.template_name}` : (d.reason || "Failed");
          setTimeout(() => { btn.textContent = "Save as Template"; btn.disabled = false; }, 3000);
        })
        .catch(() => {
          btn.textContent = "Error";
          setTimeout(() => { btn.textContent = "Save as Template"; btn.disabled = false; }, 3000);
        });
    });
    row.appendChild(btn);
    templateSection.appendChild(row);
  }
  detailEl.appendChild(templateSection);
}

function launchProject(projectId, model, btn) {
  if (!serverPort) return;
  btn.disabled = true;
  btn.textContent = "Launching...";
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/launch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        btn.textContent = "Launched!";
        // Attach xterm only if session tab is visible
        if (data.tmux_session) {
          const sessionView = document.getElementById("session-view");
          if (sessionView && !sessionView.classList.contains("hidden"))
            attachToTerminalSession(projectId, data.tmux_session);
          const st = document.querySelector('.editor-tab[data-view="session"]');
          if (st) st.classList.add("has-update");
        }
        setTimeout(() => {
          fetchExperimentsList();
        }, 1000);
      } else {
        btn.textContent = data.reason || "Failed";
      }
      setTimeout(() => { btn.textContent = "Launch"; btn.disabled = false; }, 2000);
    })
    .catch((err) => {
      btn.textContent = "Error";
      btn.style.color = "var(--error)";
      console.error("Launch failed:", err);
      showToast("Failed to launch experiment");
      setTimeout(() => { btn.textContent = "Go"; btn.style.color = ""; btn.disabled = false; }, 3000);
    });
}

function stopProject(projectId, btn) {
  if (!serverPort) return;
  btn.disabled = true;
  btn.textContent = "Stopping...";
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/stop`, {
    method: "POST",
  })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        btn.textContent = "Stopped!";
        setTimeout(() => fetchExperimentsList(), 2000);
      } else {
        btn.textContent = data.reason || "Failed";
        setTimeout(() => { btn.textContent = "Stop"; btn.disabled = false; }, 2000);
      }
    })
    .catch((err) => {
      btn.textContent = "Error";
      btn.style.color = "var(--error)";
      console.error("Stop failed:", err);
      showToast("Failed to stop experiment");
      setTimeout(() => { btn.textContent = "Stop"; btn.style.color = ""; btn.disabled = false; }, 3000);
    });
}

function attachToProject(projectId, btn) {
  if (!serverPort) return;
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Opening\u2026";
  fetch(`http://127.0.0.1:${serverPort}/experiments/attach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: projectId }),
  })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        btn.textContent = "Opened!";
      } else if (data.reason === "no_running_session") {
        btn.textContent = "No active session";
      } else {
        btn.textContent = data.reason || "Failed";
      }
      setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 2000);
    })
    .catch(() => {
      btn.textContent = label;
      btn.disabled = false;
      showToast("Failed to attach to session");
    });
}

/* ───── Campaign helpers ───── */

function campaignAction(projectId, action, btn) {
  btn.disabled = true;
  const origText = btn.textContent;
  btn.textContent = action === "pause" ? "Pausing..." : action === "resume" ? "Resuming..." : "Stopping...";
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/campaign/${action}`, { method: "POST" })
    .then(r => r.json())
    .then(data => {
      if (data.ok) {
        fetchExperimentsList();
      } else {
        btn.textContent = data.reason || "Failed";
        setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 2000);
      }
    })
    .catch(() => {
      btn.textContent = "Error";
      showToast(`Campaign ${action} failed`);
      setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 2000);
    });
}

function showCampaignConfig(proj) {
  const detailEl = document.getElementById("experiment-detail");
  // Check if config already visible
  if (detailEl.querySelector(".campaign-config")) return;

  const cfg = document.createElement("div");
  cfg.className = "campaign-config";
  cfg.innerHTML = `
    <label>Max sessions</label>
    <input type="number" id="campaign-max-sessions" value="10" min="1" max="100">
    <label>Model</label>
    <select id="campaign-model">
      <option value="claude-sonnet-4-6">Sonnet 4.6</option>
      <option value="claude-opus-4-6">Opus 4.6</option>
      <option value="claude-haiku-4-5-20251001">Haiku 4.5</option>
    </select>
    <label>Max turns per session</label>
    <input type="number" id="campaign-max-turns" value="100" min="1" max="1000">
  `;

  const goBtn = document.createElement("button");
  goBtn.className = "action-btn action-btn-launch";
  goBtn.textContent = "Go";
  goBtn.addEventListener("click", () => {
    goBtn.disabled = true;
    goBtn.textContent = "Starting...";
    fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/campaign/start`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        max_sessions: parseInt(document.getElementById("campaign-max-sessions").value) || 10,
        model: document.getElementById("campaign-model").value,
        max_turns: parseInt(document.getElementById("campaign-max-turns").value) || 100,
      }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.ok) fetchExperimentsList();
        else { goBtn.textContent = data.reason || "Failed"; goBtn.disabled = false; }
      })
      .catch(() => { goBtn.textContent = "Error"; goBtn.disabled = false; });
  });
  cfg.appendChild(goBtn);

  // Insert after the actions row
  const actionsEl = detailEl.querySelector(".exp-detail-actions");
  if (actionsEl) actionsEl.after(cfg);
}

/* ───── Results tab ───── */

function loadResults(projectId) {
  if (!serverPort || !projectId) return;
  const rendered = document.getElementById("results-rendered");
  const toolbar = document.getElementById("results-toolbar");
  const emptyEl = document.getElementById("results-empty");
  if (!rendered) return;

  rendered.classList.remove("hidden");
  if (toolbar) toolbar.classList.remove("hidden");
  if (emptyEl) emptyEl.classList.add("hidden");
  rendered.innerHTML = '<div style="color:#888;padding:20px">Loading RESULTS.md...</div>';

  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/results`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok || !data.content) {
        rendered.classList.add("hidden");
        if (toolbar) toolbar.classList.add("hidden");
        if (emptyEl) emptyEl.classList.remove("hidden");
        return;
      }
      rendered.innerHTML = marked.parse(data.content);
      rendered.querySelectorAll("pre code").forEach((block) => {
        if (window.hljs) hljs.highlightElement(block);
      });
    })
    .catch((err) => {
      rendered.classList.add("hidden");
      if (toolbar) toolbar.classList.add("hidden");
      if (emptyEl) emptyEl.classList.remove("hidden");
      console.error("[results] fetch error:", err);
    });
}

function exportNotebookHtml() {
  if (!serverPort || !currentProjectId) return;
  window.open(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(currentProjectId)}/notebook`, "_blank");
}

document.getElementById("results-export-btn")?.addEventListener("click", exportNotebookHtml);

/* ───── Setup tab (PROMPT.md + CLAUDE.md) ───── */

let setupActivePane = "prompt"; // "prompt" or "claude"
let promptRawMd = "";
let claudeRawMd = "";
let setupEditing = false;

function switchSetupPane(pane) {
  setupActivePane = pane;
  document.querySelectorAll(".setup-tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.setup === pane);
  });
  const promptPane = document.getElementById("setup-prompt-pane");
  const claudePane = document.getElementById("setup-claude-pane");
  if (promptPane) promptPane.classList.toggle("hidden", pane !== "prompt");
  if (claudePane) claudePane.classList.toggle("hidden", pane !== "claude");

  // Exit edit mode when switching panes
  if (setupEditing) cancelSetupEdit();

  // Load content for the active pane
  if (currentProjectId) {
    if (pane === "prompt") loadSetupPrompt(currentProjectId);
    else loadSetupClaude(currentProjectId);
  }
}

document.querySelectorAll(".setup-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchSetupPane(tab.dataset.setup));
});

async function loadPromptEditor(projectId) {
  // Load whichever sub-pane is active
  if (setupActivePane === "claude") loadSetupClaude(projectId);
  else loadSetupPrompt(projectId);
}

async function loadSetupPrompt(projectId) {
  const editor = document.getElementById("prompt-editor");
  const rendered = document.getElementById("prompt-rendered");
  if (!editor || !serverPort) return;

  editor.classList.add("hidden");
  if (rendered) rendered.classList.remove("hidden");
  showSetupViewMode();
  if (rendered) rendered.innerHTML = '<div style="color:#888;padding:20px">Loading PROMPT.md...</div>';

  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/prompt`);
    const data = await r.json();
    if (!data.ok || !data.content) {
      promptRawMd = "";
      editor.value = "";
      if (rendered) rendered.innerHTML = `<div class="tab-empty-state" style="padding:40px;text-align:center">
        <div class="empty-icon" style="font-size:28px;margin-bottom:8px">&#x1F4DD;</div>
        <h2 style="font-size:14px;color:var(--text);margin-bottom:4px">No PROMPT.md yet</h2>
        <p style="font-size:12px;color:var(--text-dim)">Click <strong>Edit</strong> to create one. The prompt tells the agent what to optimize.</p>
      </div>`;
      return;
    }
    promptRawMd = data.content;
    editor.value = promptRawMd;
    if (rendered) {
      rendered.innerHTML = marked.parse(promptRawMd);
      rendered.querySelectorAll("pre code").forEach((block) => {
        if (window.hljs) hljs.highlightElement(block);
      });
    }
  } catch (err) {
    promptRawMd = "";
    editor.value = "";
    console.error("[setup] prompt fetch error:", err);
    if (rendered) rendered.innerHTML = `<div class="tab-empty-state" style="padding:40px;text-align:center">
      <div class="empty-icon" style="font-size:28px;margin-bottom:8px">&#x1F4DD;</div>
      <h2 style="font-size:14px;color:var(--text);margin-bottom:4px">No PROMPT.md yet</h2>
      <p style="font-size:12px;color:var(--text-dim)">Click <strong>Edit</strong> to create one. The prompt tells the agent what to optimize.</p>
    </div>`;
  }
}

async function loadSetupClaude(projectId) {
  const editor = document.getElementById("claude-editor");
  const rendered = document.getElementById("claude-rendered");
  if (!editor || !serverPort) return;

  editor.classList.add("hidden");
  if (rendered) rendered.classList.remove("hidden");
  showSetupViewMode();
  if (rendered) rendered.innerHTML = '<div style="color:#888;padding:20px">Loading CLAUDE.md...</div>';

  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/claude-md`);
    const data = await r.json();
    if (!data.ok || !data.content) {
      claudeRawMd = "";
      editor.value = "";
      if (rendered) rendered.innerHTML = `<div class="tab-empty-state" style="padding:40px;text-align:center">
        <div class="empty-icon" style="font-size:28px;margin-bottom:8px">&#x2699;&#xFE0F;</div>
        <h2 style="font-size:14px;color:var(--text);margin-bottom:4px">No CLAUDE.md yet</h2>
        <p style="font-size:12px;color:var(--text-dim)">Click <strong>Edit</strong> to define agent instructions. One will also be auto-created on first launch.</p>
      </div>`;
      return;
    }
    claudeRawMd = data.content;
    editor.value = claudeRawMd;
    if (rendered) {
      rendered.innerHTML = marked.parse(claudeRawMd);
      rendered.querySelectorAll("pre code").forEach((block) => {
        if (window.hljs) hljs.highlightElement(block);
      });
    }
  } catch (err) {
    claudeRawMd = "";
    editor.value = "";
    console.error("[setup] claude-md fetch error:", err);
    if (rendered) rendered.innerHTML = `<div class="tab-empty-state" style="padding:40px;text-align:center">
      <div class="empty-icon" style="font-size:28px;margin-bottom:8px">&#x2699;&#xFE0F;</div>
      <h2 style="font-size:14px;color:var(--text);margin-bottom:4px">No CLAUDE.md yet</h2>
      <p style="font-size:12px;color:var(--text-dim)">Click <strong>Edit</strong> to define agent instructions. One will also be auto-created on first launch.</p>
    </div>`;
  }
}

function showSetupViewMode() {
  setupEditing = false;
  document.getElementById("setup-edit-btn")?.classList.remove("hidden");
  document.getElementById("setup-save-btn")?.classList.add("hidden");
  document.getElementById("setup-cancel-btn")?.classList.add("hidden");
}

function enterSetupEdit() {
  setupEditing = true;
  const isPrompt = setupActivePane === "prompt";
  const editor = document.getElementById(isPrompt ? "prompt-editor" : "claude-editor");
  const rendered = document.getElementById(isPrompt ? "prompt-rendered" : "claude-rendered");
  if (!editor) return;

  if (rendered) rendered.classList.add("hidden");
  editor.classList.remove("hidden");
  editor.value = isPrompt ? promptRawMd : claudeRawMd;
  editor.focus();

  document.getElementById("setup-edit-btn")?.classList.add("hidden");
  document.getElementById("setup-save-btn")?.classList.remove("hidden");
  document.getElementById("setup-cancel-btn")?.classList.remove("hidden");
}

function cancelSetupEdit() {
  setupEditing = false;
  const isPrompt = setupActivePane === "prompt";
  const editor = document.getElementById(isPrompt ? "prompt-editor" : "claude-editor");
  const rendered = document.getElementById(isPrompt ? "prompt-rendered" : "claude-rendered");
  if (!editor) return;

  editor.classList.add("hidden");
  if (rendered) rendered.classList.remove("hidden");
  showSetupViewMode();
}

document.getElementById("setup-edit-btn")?.addEventListener("click", enterSetupEdit);
document.getElementById("setup-cancel-btn")?.addEventListener("click", cancelSetupEdit);

document.getElementById("setup-save-btn")?.addEventListener("click", async () => {
  const isPrompt = setupActivePane === "prompt";
  const editor = document.getElementById(isPrompt ? "prompt-editor" : "claude-editor");
  const rendered = document.getElementById(isPrompt ? "prompt-rendered" : "claude-rendered");
  const status = document.getElementById("setup-save-status");
  if (!editor || !currentProjectId) return;

  const endpoint = isPrompt ? "prompt" : "claude-md";
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(currentProjectId)}/${endpoint}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: editor.value }),
    });
    const data = await r.json();
    if (data.ok) {
      if (isPrompt) promptRawMd = editor.value;
      else claudeRawMd = editor.value;
      cancelSetupEdit();
      if (rendered) {
        rendered.innerHTML = marked.parse(isPrompt ? promptRawMd : claudeRawMd);
        rendered.querySelectorAll("pre code").forEach((block) => {
          if (window.hljs) hljs.highlightElement(block);
        });
      }
    }
    if (status) {
      const msg = isPrompt
        ? (data.ok ? "Saved \u2014 agent notified automatically" : "Failed")
        : (data.ok ? "Saved" : "Failed");
      status.textContent = msg;
      setTimeout(() => status.textContent = "", 4000);
    }
  } catch {
    if (status) { status.textContent = "Error"; setTimeout(() => status.textContent = "", 2000); }
  }
});

/* ───── Experiment notifications ───── */

let consecutiveDiscards = 0;

function notifyExperimentEvent(data) {
  if (data.type === "run_completed" || data.$schema === "distillate/run/v1") {
    const status = data.status || "";

    // Activity bar notification badge when sidebar is collapsed
    if (sidebarLeft?.classList.contains("collapsed") &&
        (status === "keep" || status === "crash" || (status === "discard" && consecutiveDiscards >= 4))) {
      const expBtn = document.querySelector('.activity-btn[data-pane="sidebar-left"]');
      if (expBtn) expBtn.classList.add("has-notification");
    }

    // Track discards regardless of focus/notification state
    if (status === "keep") consecutiveDiscards = 0;
    else if (status === "discard") consecutiveDiscards++;
    else if (status === "crash") consecutiveDiscards = 0;

    // Crashes always get OS notification (even when focused)
    if (status === "crash" && window.nicolas?.notify) {
      window.nicolas.notify(
        "Experiment crashed",
        data.reasoning || data.hypothesis || "Check logs"
      );
      return;
    }

    if (!window.nicolas?.notify || document.hasFocus()) return;

    if (status === "keep" && data.results) {
      const metric = Object.entries(data.results)[0];
      if (metric) {
        window.nicolas.notify(
          "New baseline",
          `${metric[0]} improved to ${metric[1]}`
        );
      }
      // Check if goal is reached
      const proj = cachedProjects.find(p => p.id === data.project_id);
      if (proj?.goals) {
        for (const g of proj.goals) {
          const val = data.results[g.metric];
          if (val != null) {
            const reached = g.direction === "maximize" ? val >= g.threshold : val <= g.threshold;
            if (reached) {
              window.nicolas.notify("Goal reached!", `${g.metric} = ${val} (target: ${g.direction === "maximize" ? "\u2265" : "\u2264"} ${g.threshold})`);
            }
          }
        }
      }
    } else if (status === "discard") {
      if (consecutiveDiscards >= 5) {
        window.nicolas.notify(
          "Agent may be stuck",
          `${consecutiveDiscards} consecutive discards`
        );
      }
    }
  }
}
