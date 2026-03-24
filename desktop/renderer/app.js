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
let _sessionTransition = null; // "stopping" | "launching" | null
let cachedPapers = [];
let cachedProjects = [];
let liveMetrics = {};  // Per-project live metric_update events: { projectId: [...] }
let terminalInitialized = false;
let chartLogScale = true;  // persists across re-renders
let currentTerminalProject = null;
let libraryConfigured = false;  // Set from /status — whether Zotero credentials are configured

const messagesEl = document.getElementById("messages");
const welcomeEl = document.getElementById("welcome");

// Delegated click handler for paper refs and external links in messages
messagesEl?.addEventListener("click", (e) => {
  // Paper [N] references
  const paperRef = e.target.closest(".paper-ref");
  if (paperRef) {
    e.preventDefault();
    const idx = parseInt(paperRef.dataset.index);
    if (window._cachedPapersData) {
      const paper = window._cachedPapersData.find((p) => p.index === idx);
      if (paper) { selectPaper(paper.key); return; }
    }
    if (typeof serverPort !== "undefined" && serverPort) {
      fetch(`http://127.0.0.1:${serverPort}/papers`)
        .then((r) => r.json())
        .then((data) => {
          const paper = (data.papers || []).find((p) => p.index === idx);
          if (paper) selectPaper(paper.key);
        })
        .catch(() => {});
    }
    return;
  }
  // External links in assistant messages
  const link = e.target.closest(".message.assistant a[href]");
  if (link && !link.classList.contains("paper-ref") && !link.classList.contains("copy-btn")) {
    const href = link.getAttribute("href");
    if (href && !href.startsWith("#")) {
      e.preventDefault();
      handleExternalLink(href, link);
    }
  }
});

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

let chatBannerInjected = false;

function injectChatBanner(stats) {
  if (chatBannerInjected || !messagesEl) return;
  chatBannerInjected = true;

  const nExp = stats?.experiments || 0;
  const nRuns = stats?.runs || 0;
  const nRunning = stats?.running || 0;
  const nPapers = stats?.papers || 0;
  const nQueue = stats?.queue || 0;

  let expLine = "";
  if (nExp > 0) {
    expLine = `🧪 ${nExp} experiment${nExp !== 1 ? "s" : ""} · ${nRuns} run${nRuns !== 1 ? "s" : ""}`;
    if (nRunning > 0) expLine += ` · ${nRunning} running`;
  }
  const papersLine = `📚 ${nPapers} paper${nPapers !== 1 ? "s" : ""} read` + (nQueue > 0 ? ` · ${nQueue} in queue` : "");

  const greeting = NICOLAS_GREETINGS[Math.floor(Math.random() * NICOLAS_GREETINGS.length)];

  const bannerHtml = `<div class="chat-banner">
    <div class="welcome-banner">
      <div class="banner-line">
        <span class="banner-dashes">&#x2500;&#x2500;&#x2500;</span>
        <span class="banner-flask">&#x2697;&#xFE0F;</span>
        <span class="banner-name">Nicolas</span>
        <span class="banner-dashes-tail"></span>
      </div>
      <div class="banner-body">
        <p class="banner-tagline">Your research alchemist.</p>
        ${expLine ? `<p class="banner-stats">${expLine}</p>` : ""}
        <p class="banner-stats">${papersLine}</p>
      </div>
      <div class="banner-footer"></div>
    </div>
  </div>
  <div class="message assistant">${greeting}</div>`;

  messagesEl.insertAdjacentHTML("afterbegin", bannerHtml);

  // Ensure chat input is focused after banner renders
  setTimeout(() => inputEl?.focus(), 50);
}

// Restore chat messages from previous session (survives app restart)
try {
  const savedChat = localStorage.getItem("distillate-chat");
  if (savedChat && messagesEl) {
    messagesEl.innerHTML = savedChat;
    chatBannerInjected = true;
  }
} catch {}

// Save chat before unload
window.addEventListener("beforeunload", () => {
  try {
    if (messagesEl && messagesEl.children.length > 0) {
      localStorage.setItem("distillate-chat", messagesEl.innerHTML);
    }
  } catch {}
});
const inputEl = document.getElementById("input");
const formEl = document.getElementById("input-form");
const sendBtn = document.getElementById("send-btn");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
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
  list_projects: "\uD83E\uDDEA Surveying the laboratory",
  get_project_details: "\uD83D\uDD2C Examining the experiment",
  compare_runs: "\u2696\uFE0F Weighing the results",
  scan_project: "\uD83D\uDD0D Scanning for experiments",
  get_experiment_notebook: "\uD83D\uDCD3 Opening the lab notebook",
  add_project: "\uD83D\uDCC1 Adding project to the lab",
  rename_project: "\u270F\uFE0F Relabeling the project",
  rename_run: "\u270F\uFE0F Relabeling the run",
  delete_project: "\uD83D\uDDD1\uFE0F Removing from the lab",
  delete_run: "\uD83D\uDDD1\uFE0F Removing the run",
  update_project: "\uD83D\uDCDD Updating project details",
  link_paper: "\uD83D\uDD17 Linking paper to project",
  update_goals: "\uD83C\uDFAF Setting project goals",
  annotate_run: "\uD83D\uDCDD Adding note to run",
  init_experiment: "\u2697\uFE0F Drafting experiment prompt",
  continue_experiment: "\uD83D\uDD04 Continuing experiment",
  sweep_experiment: "\uD83E\uDDF9 Launching sweep",
  steer_experiment: "\uD83E\uDDE7 Steering the experiment",
  compare_projects: "\u2696\uFE0F Comparing experiments",
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
  // Ignore events from a cancelled turn (except turn_end which resets state)
  if (_cancelledTurn && event.type !== "turn_end") return;
  if (_cancelledTurn && event.type === "turn_end") { _cancelledTurn = false; return; }

  switch (event.type) {
    case "text_delta":
      removeThinkingIndicator();
      if (!currentAssistantEl) {
        startAssistantMessage();
      }
      currentText += event.text;
      scheduleStreamingRender();
      scheduleScrollToBottom();
      break;

    case "tool_start":
      removeThinkingIndicator();
      // Close current text block so the indicator appears between text sections
      if (currentAssistantEl) {
        currentAssistantEl.classList.remove("streaming-cursor");
        renderAssistantMessage();
        currentAssistantEl = null;
        currentText = "";
      }
      // Hide internal plumbing tools (ToolSearch just loads schemas)
      if (event.name !== "ToolSearch") {
        addToolIndicator(event.name, false, event.input, event.label);
        scrollToBottom();
      }
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
      // Show thinking indicator while agent processes the tool result
      showThinkingIndicator();
      break;
    }

    case "turn_end":
      clearTimeout(_inputSafetyTimer);
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
      removeThinkingIndicator();
      finishStreaming();
      addErrorMessage(event.message || "Something went wrong.");
      break;

    case "cancelled":
      // Server confirmed cancellation — UI already handled by stopGeneration()
      removeThinkingIndicator();
      finishStreaming();
      break;
  }
}

/* ───── Message rendering ───── */

function addUserMessage(text) {
  const el = document.createElement("div");
  el.className = "message user";
  el.textContent = text;
  messagesEl.appendChild(el);
  scrollToBottom(true);
}

function startAssistantMessage() {
  currentAssistantEl = document.createElement("div");
  currentAssistantEl.className = "message assistant markdown-body streaming-cursor";
  messagesEl.appendChild(currentAssistantEl);
  currentText = "";
  isStreaming = true;
  setStreamingUI(true);
  // Hide suggestions while streaming
  const cs = document.getElementById("chat-suggestions");
  if (cs) cs.classList.add("hidden");
}

// --- Streaming render throttle (P0 perf fix) ---
// During streaming, batch text_delta renders to one per animation frame
// instead of re-parsing the full markdown on every character chunk.
let _renderRAF = null;
let _scrollRAF = null;

function scheduleStreamingRender() {
  if (_renderRAF) return;
  _renderRAF = requestAnimationFrame(() => {
    _renderRAF = null;
    renderAssistantMessage();
  });
}

function scheduleScrollToBottom() {
  if (_scrollRAF) return;
  _scrollRAF = requestAnimationFrame(() => {
    _scrollRAF = null;
    scrollToBottom();
  });
}

function renderAssistantMessage() {
  if (!currentAssistantEl) return;
  if (typeof marked !== "undefined") {
    currentAssistantEl.innerHTML = window.markedParse(currentText);
    // Turn [N] paper references into clickable links
    currentAssistantEl.innerHTML = currentAssistantEl.innerHTML.replace(
      /\[(\d{1,4})\]/g,
      '<a href="#" class="paper-ref" data-index="$1">[$1]</a>'
    );
  } else {
    currentAssistantEl.textContent = currentText;
  }
}

function _extractPaperId(url) {
  // arXiv: arxiv.org/abs/2301.12345 or arxiv.org/pdf/2301.12345
  const arxiv = url.match(/arxiv\.org\/(?:abs|pdf)\/(\d{4}\.\d{4,5}(?:v\d+)?)/);
  if (arxiv) return { type: "arxiv", id: arxiv[1], label: `arXiv:${arxiv[1]}` };
  // DOI
  const doi = url.match(/doi\.org\/(10\.\d{4,}\/\S+)/);
  if (doi) return { type: "doi", id: doi[1], label: `DOI:${doi[1]}` };
  // Semantic Scholar
  if (url.includes("semanticscholar.org/paper/")) return { type: "url", id: url, label: "Semantic Scholar paper" };
  return null;
}

function handleExternalLink(url, anchorEl) {
  const paper = _extractPaperId(url);

  if (!paper) {
    // Not a paper link — just open externally
    if (window.nicolas?.openExternal) window.nicolas.openExternal(url);
    else window.open(url, "_blank");
    return;
  }

  // Paper link — show popup with options
  const existing = document.querySelector(".link-popup");
  if (existing) existing.remove();

  const popup = document.createElement("div");
  popup.className = "link-popup";
  popup.innerHTML = `
    <div class="link-popup-header">${paper.label}</div>
    <button class="link-popup-btn" data-action="open">Open in browser</button>
    <button class="link-popup-btn link-popup-btn-accent" data-action="queue">Add to library queue</button>`;

  popup.addEventListener("click", async (e) => {
    const action = e.target.dataset?.action;
    if (!action) return;
    popup.remove();
    if (action === "open") {
      if (window.nicolas?.openExternal) window.nicolas.openExternal(url);
      else window.open(url, "_blank");
    } else if (action === "queue") {
      // Use the add_paper_to_zotero tool via chat
      const identifier = paper.type === "arxiv" ? paper.id : url;
      inputEl.value = `Add this paper to my queue: ${identifier}`;
      sendMessage();
    }
  });

  // Position near the link
  const rect = anchorEl.getBoundingClientRect();
  popup.style.position = "fixed";
  popup.style.left = `${rect.left}px`;
  popup.style.top = `${rect.bottom + 4}px`;
  document.body.appendChild(popup);

  // Close on click outside
  const close = (e) => {
    if (!popup.contains(e.target)) { popup.remove(); document.removeEventListener("mousedown", close); }
  };
  setTimeout(() => document.addEventListener("mousedown", close), 0);
}

function finishStreaming() {
  // Flush any pending throttled render so final content is complete
  if (_renderRAF) { cancelAnimationFrame(_renderRAF); _renderRAF = null; }
  if (_scrollRAF) { cancelAnimationFrame(_scrollRAF); _scrollRAF = null; }
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
  // Restore suggestions after streaming
  const cs = document.getElementById("chat-suggestions");
  if (cs) cs.classList.remove("hidden");
  refreshChatSuggestions();

  // Send queued message if user typed while agent was working
  if (_queuedMessage) {
    const queued = _queuedMessage;
    _queuedMessage = null;
    // Upgrade the dimmed queued message to normal style
    const qEl = messagesEl.querySelector(".message.queued");
    if (qEl) qEl.classList.remove("queued");
    // Send it
    lastUserMessage = queued;
    showThinkingIndicator();
    ws.send(JSON.stringify({ text: queued }));
    inputEl.disabled = true;
    sendBtn.disabled = true;
  }
}

function addToolIndicator(name, done, input, serverLabel) {
  const el = document.createElement("div");
  el.className = `tool-indicator${done ? " done" : ""}`;
  el.dataset.toolName = name;

  const label = serverLabel || toolLabels[name] || name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

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
    } else if (name === "suggest_from_literature") {
      const parts = [];
      if (input.project) parts.push(input.project);
      if (input.focus) parts.push(`\u2018${input.focus}\u2019`);
      subtitle = parts.join(" \u2014 ");
    } else if (name === "compare_projects" && input.projects) {
      subtitle = input.projects.join(" vs ");
    } else if (name === "compare_runs") {
      const parts = [];
      if (input.run_a) parts.push(input.run_a);
      if (input.run_b) parts.push(input.run_b);
      subtitle = parts.join(" vs ");
    } else if (name === "list_projects") {
      subtitle = "all experiments";
    } else if (name === "get_project_details" && input.identifier) {
      subtitle = input.identifier;
    } else if (name === "extract_baselines" && input.paper) {
      subtitle = input.paper;
    } else if (name === "replicate_paper" && input.paper) {
      subtitle = input.paper;
    } else if (name === "steer_experiment" && input.project) {
      subtitle = input.project;
    } else if (name === "continue_experiment" && input.project) {
      subtitle = input.project;
    } else if (name === "init_experiment" && input.name) {
      subtitle = input.name;
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
    } else if (name === "ToolSearch" && input.query) {
      // Translate raw tool queries into readable text
      const q = input.query;
      if (q.includes("mcp__distillate__")) {
        const toolName = q.replace(/.*mcp__distillate__/, "").replace(/,.*/, "").trim();
        const readable = toolLabels[toolName] || toolName.replace(/_/g, " ");
        subtitle = readable.replace(/^[^\w]*/, "").toLowerCase();
      } else {
        subtitle = q.replace(/^select:/, "").replace(/mcp__\w+__/g, "").replace(/_/g, " ");
      }
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

  // Refresh stats (sidebars already loaded, just refresh welcome screen)
  _fetchStatusOnly();

  // Tell server to start fresh
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "new_conversation" }));
  }
}

/* ───── Welcome stats + initial data load ───── */

const welcomeStatsEl = document.getElementById("welcome-stats");

function _applyStatusData(data) {
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

  // Inject chat banner with stats (CLI-style welcome)
  const exp = data.experiments || {};
  injectChatBanner({
    experiments: exp.total_projects || 0,
    runs: exp.total_runs || 0,
    running: exp.active_sessions || 0,
    papers: data.papers_read || 0,
    queue: data.papers_queued || 0,
  });

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

  // One-shot migration toast
  if (data.migration_message) {
    const toast = document.createElement("div");
    toast.className = "migration-toast";
    toast.textContent = data.migration_message;
    document.body.appendChild(toast);
    setTimeout(() => { toast.classList.add("visible"); }, 50);
    setTimeout(() => {
      toast.classList.remove("visible");
      setTimeout(() => toast.remove(), 400);
    }, 5000);
  }
}

function _applyExperimentsData(data) {
  experimentsFirstLoad = false;
  if (!data.ok) return;
  const projects = data.projects || [];
  renderProjectsList(projects);
  if (currentProjectId) renderProjectDetail(currentProjectId);
  _sessionTransition = null;
  if (projects.some((p) => p.active_sessions > 0)) startExperimentSSE();
}

function _applyPapersData(data) {
  papersFirstLoad = false;
  if (!data.ok) return;
  window._cachedPapersData = data.papers || [];
  renderPapersList(window._cachedPapersData);
}

function _applyConnectorsData(data) {
  if (!data.ok) return;
  cachedConnectors = data.connectors || [];
  renderConnectors(cachedConnectors);
}

function fetchWelcomeStats() {
  if (!serverPort) return;

  // Batched init: one round-trip instead of four parallel fetches
  fetch(`http://127.0.0.1:${serverPort}/init`)
    .then((r) => r.json())
    .then((data) => {
      if (!data.ok) return;
      _applyStatusData(data.status);
      _applyExperimentsData(data.experiments);
      _applyPapersData(data.papers);
      _applyConnectorsData(data.connectors);
    })
    .catch(() => {
      // Fallback: if /init not available, use individual endpoints
      _fetchStatusOnly();
      fetchExperimentsList();
      fetchPapersData();
      fetchConnectors();
    });
}

function _fetchStatusOnly() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/status`)
    .then((r) => r.json())
    .then((data) => { if (data.ok) _applyStatusData(data); })
    .catch(() => {});
}

function updateSuggestions(isFirstUse, hasPapers, hasExperiments) {
  const container = document.getElementById("chat-suggestions");
  const suggestions = _buildSuggestions(isFirstUse, hasPapers, hasExperiments);
  _renderSuggestions(container, suggestions);
}

function refreshChatSuggestions() {
  const container = document.getElementById("chat-suggestions");
  if (!container) return;

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
      { text: "What's in my queue?", label: "What's in my queue?" },
      { text: "How are my experiments going?", label: "Experiment status" },
      { text: "Summarize my last read", label: "Summarize last read" },
      { text: "What should I try next?", label: "What should I try?" },
    ];
  }
}

function _renderSuggestions(container, suggestions) {
  if (!container) return;
  container.innerHTML = "";
  for (const s of suggestions) {
    const btn = document.createElement("button");
    btn.className = "suggestion" + (s.action ? " suggestion-primary" : "");
    btn.dataset.text = s.text;
    btn.textContent = s.label;
    btn.addEventListener("click", () => {
      if (s.action) { s.action(); return; }
      if (s.text === "__launch_demo__") { launchDemoExperiment(); return; }
      inputEl.value = s.text;
      sendMessage();
    });
    container.appendChild(btn);
  }
}

/* ───── Cloud sync ───── */

function triggerCloudSync() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/sync`, { method: "POST" })
    .then((r) => { if (!r.ok) return; return r.json(); })
    .then((data) => { if (data?.ok) console.log("[sync] Cloud sync complete"); })
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
  fetchExperimentsList();
  fetchPapersData();
}

/* ───── Input handling ───── */

formEl.addEventListener("submit", (e) => {
  e.preventDefault();
  if (isStreaming) return; // stop button handles this
  sendMessage();
});

sendBtn.addEventListener("click", (e) => {
  if (isStreaming) {
    e.preventDefault();
    e.stopPropagation();
    stopGeneration();
    return false;
  }
});

// Also handle mousedown for more reliable stop (click can miss during fast UI updates)
sendBtn.addEventListener("mousedown", (e) => {
  if (isStreaming) {
    e.preventDefault();
    e.stopPropagation();
    stopGeneration();
  }
});

// Message history (arrow up/down like CLI)
const _messageHistory = [];
let _historyIndex = -1;
let _historyDraft = "";

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
    return;
  }

  // Arrow up/down for history navigation (only when cursor is at start/end)
  if (e.key === "ArrowUp" && inputEl.selectionStart === 0 && _messageHistory.length > 0) {
    e.preventDefault();
    if (_historyIndex === -1) _historyDraft = inputEl.value;
    _historyIndex = Math.min(_historyIndex + 1, _messageHistory.length - 1);
    inputEl.value = _messageHistory[_historyIndex];
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
  } else if (e.key === "ArrowDown" && _historyIndex >= 0) {
    e.preventDefault();
    _historyIndex--;
    inputEl.value = _historyIndex >= 0 ? _messageHistory[_historyIndex] : _historyDraft;
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
  }
});

// Auto-resize textarea
inputEl.addEventListener("input", () => {
  inputEl.style.height = "auto";
  const maxH = 120;
  const newH = Math.min(inputEl.scrollHeight, maxH);
  inputEl.style.height = newH + "px";
  inputEl.classList.toggle("has-scroll", inputEl.scrollHeight > maxH);
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

let _cancelledTurn = false;

function stopGeneration() {
  if (!isStreaming) return;

  _cancelledTurn = true;

  // Tell the server to interrupt Claude
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "cancel" }));
  }

  // Finish whatever partial text we have
  removeThinkingIndicator();
  finishStreaming();

  // Mark any in-progress tool indicators as stopped
  document.querySelectorAll(".tool-indicator:not(.done)").forEach((el) => {
    el.classList.add("done", "cancelled");
    const spinner = el.querySelector(".spinner");
    if (spinner) spinner.remove();
  });

  // Show interruption indicator (styled like a tool indicator)
  const stopEl = document.createElement("div");
  stopEl.className = "tool-indicator done cancelled";
  stopEl.innerHTML = '<span class="stop-x">\u2715</span><span>Nicolas was interrupted by the user.</span>';
  messagesEl.appendChild(stopEl);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  // Re-enable input and pre-fill with last message for easy retry
  inputEl.disabled = false;
  sendBtn.disabled = false;
  if (lastUserMessage) {
    inputEl.value = lastUserMessage;
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
  }
  inputEl.focus();
}

const THINKING_PHRASES = [
  "Pondering\u2026",
  "Distilling\u2026",
  "Transmuting\u2026",
  "Dissolving\u2026",
  "Crystallizing\u2026",
  "Sublimating\u2026",
  "Calcinating\u2026",
  "Condensing\u2026",
];

function showThinkingIndicator() {
  removeThinkingIndicator();
  const phrase = THINKING_PHRASES[Math.floor(Math.random() * THINKING_PHRASES.length)];
  const el = document.createElement("div");
  el.className = "thinking-indicator";
  el.id = "thinking-indicator";
  el.innerHTML = `<div class="thinking-spinner"></div><span>${phrase}</span>`;
  messagesEl.appendChild(el);
  scrollToBottom(true);
}

function removeThinkingIndicator() {
  const el = document.getElementById("thinking-indicator");
  if (el) el.remove();
}

let _inputSafetyTimer = null;
let _queuedMessage = null;

function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  // If agent is still working, queue the message instead of blocking
  if (isStreaming || inputEl.disabled) {
    _queuedMessage = text;
    // Show queued message in dimmed style
    const qEl = document.createElement("div");
    qEl.className = "message user queued";
    qEl.textContent = text;
    messagesEl.appendChild(qEl);
    scrollToBottom(true);
    inputEl.value = "";
    inputEl.style.height = "auto";
    return;
  }

  _cancelledTurn = false;
  lastUserMessage = text;
  _messageHistory.unshift(text);
  if (_messageHistory.length > 50) _messageHistory.pop();
  _historyIndex = -1;
  _historyDraft = "";
  addUserMessage(text);
  showThinkingIndicator();
  ws.send(JSON.stringify({ text }));

  // Immediately enter streaming mode so stop button is visible
  isStreaming = true;
  setStreamingUI(true);

  inputEl.value = "";
  inputEl.style.height = "auto";

  // Safety: re-enable input after 120s if turn_end never arrives
  clearTimeout(_inputSafetyTimer);
  _inputSafetyTimer = setTimeout(() => {
    if (isStreaming) {
      console.warn("[safety] Re-enabling input after timeout");
      removeThinkingIndicator();
      finishStreaming();
    }
  }, 120000);
}

/* ───── Settings modal ───── */

const settingsOverlay = document.getElementById("settings-overlay");
const settingsClose = document.getElementById("settings-close");
const settingsSave = document.getElementById("settings-save");
const settingsStatus = document.getElementById("settings-status");
const settingAuthToken = document.getElementById("setting-auth-token");
const settingPrivateRepos = document.getElementById("setting-private-repos");

function openSettings() {
  settingsStatus.textContent = "";

  // Load current values
  if (window.nicolas && window.nicolas.getSettings) {
    window.nicolas.getSettings().then((s) => {
      settingAuthToken.value = s.authToken || "";
      if (settingPrivateRepos) settingPrivateRepos.checked = !!s.privateRepos;
    });
  }
  settingsOverlay.classList.remove("hidden");
  if (settingAuthToken) settingAuthToken.focus();
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
        authToken: settingAuthToken.value.trim(),
        privateRepos: settingPrivateRepos ? settingPrivateRepos.checked : false,
      }).then(() => {
        settingsStatus.textContent = "Saved!";
        settingsStatus.className = "setting-status success";
        closeSettings();
      }).catch((err) => {
        settingsStatus.textContent = `Failed to save: ${err.message || "unknown error"}`;
        settingsStatus.className = "setting-status";
      });
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

// Esc to close settings — handled here because settingsOverlay is in scope
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsOverlay.classList.contains("hidden")) {
    closeSettings();
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
    if (!ws) connect(port);
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


// Editor tabs (Control Panel / Session / Notebook)
const editorViews = ["control-panel", "session", "results", "prompt-editor"];

function switchEditorTab(viewName, { skipSessionAttach = false } = {}) {
  document.querySelectorAll(".editor-tab").forEach((t) => t.classList.remove("active"));
  document.querySelector(`.editor-tab[data-view="${viewName}"]`)?.classList.add("active");

  for (const v of editorViews) {
    const el = document.getElementById(`${v}-view`);
    if (el) el.classList.toggle("hidden", v !== viewName);
  }

  if (viewName === "control-panel") {
    if (currentProjectId) {
      renderProjectDetail(currentProjectId);
    }
  }
  if (viewName === "results") {
    if (currentProjectId) {
      loadResults(currentProjectId);
    } else {
      showResultsNoSelection();
    }
  }
  if (viewName === "prompt-editor") {
    if (currentProjectId) {
      showSetupWithContent();
      loadPromptEditor(currentProjectId);
    } else {
      showSetupNoSelection();
    }
  }
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
          // Don't forward mouse reports to PTY (they'd confuse tmux)
          if (data.match(/\x1b\[<?\d+;\d+;\d+[Mm]/)) return;
          if (currentTerminalProject && window.nicolas)
            window.nicolas.terminalInput(currentTerminalProject, data);
        });
        // Forward scroll wheel to tmux as SGR mouse sequences
        window.xtermBridge.onScroll((button, lines) => {
          if (currentTerminalProject && window.nicolas) {
            for (let i = 0; i < lines; i++)
              window.nicolas.terminalInput(currentTerminalProject, `\x1b[<${button};1;1M`);
          }
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
    // Don't flash "No active session" while launch is in flight
    if (_sessionTransition === "launching") {
      showSessionConnecting();
    } else {
      showSessionEmpty();
    }
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

function showSessionConnecting() {
  const emptyEl = document.getElementById("session-empty");
  const xtermEl = document.getElementById("xterm-container");
  if (emptyEl) {
    emptyEl.innerHTML = '<div class="empty-icon spinner-icon"></div><h2>Connecting\u2026</h2><p>Starting Claude session.</p>';
    emptyEl.classList.remove("hidden");
  }
  if (xtermEl) xtermEl.classList.add("hidden");
}

function showSessionEmpty() {
  const xtermEl = document.getElementById("xterm-container");
  const emptyEl = document.getElementById("session-empty");
  if (emptyEl) {
    emptyEl.innerHTML = '<div class="empty-icon">&#x1F4BB;</div><h2>No active session</h2><p>Launch an experiment to see the live Claude session here.</p>';
  }
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
  if ((e.metaKey || e.ctrlKey) && e.key === "k") {
    e.preventDefault();
    // Focus chat input — open bottom panel if collapsed, but never close it
    if (bottomPanel?.classList.contains("collapsed")) {
      togglePane("bottom-panel");
    }
    inputEl?.focus();
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "e") {
    e.preventDefault();
    togglePane("sidebar-left");
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
  // Escape: stop generation first, then deselect experiment
  if (e.key === "Escape" && !e.metaKey && !e.ctrlKey) {
    const settingsOverlay = document.getElementById("settings-overlay");
    if (settingsOverlay && !settingsOverlay.classList.contains("hidden")) return;
    if (isStreaming) {
      e.preventDefault();
      stopGeneration();
    } else if (currentProjectId || currentPaperKey) {
      e.preventDefault();
      deselectAll();
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

// Only restore layout if not a fresh app launch (preserves sizes within session,
// but resets to CSS defaults on full restart)
if (sessionStorage.getItem("distillate-session-active")) {
  restoreLayoutState();
}
sessionStorage.setItem("distillate-session-active", "1");

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

      // Re-render if this project is displayed AND control panel is visible
      const cpVisible = !document.getElementById("control-panel-view")?.classList.contains("hidden");
      if (currentProjectId === data.project_id && cpVisible) {
        renderProjectDetail(data.project_id);
      }
      // Update sidebar counts
      renderProjectsList(cachedProjects);
    }
    // Also notify
    notifyExperimentEvent(data.run);

    // Auto-switch to Control Panel on first onboarding run
    if (window._onboardingProjectId && data.project_id === window._onboardingProjectId) {
      selectProject(window._onboardingProjectId);
      switchEditorTab("control-panel");
      delete window._onboardingProjectId;
    }
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
            renderMetricChart(canvas, getDisplayRuns(proj.runs), activeMetric, liveMetrics[pid]);
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

const brewSyncBtn = document.getElementById("brew-sync-btn");
if (brewSyncBtn) {
  brewSyncBtn.addEventListener("click", () => {
    inputEl.value = "Sync my paper library";
    sendMessage();
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
    .then((data) => _applyPapersData(data))
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

  // Show library onboarding CTA when no papers and Zotero not configured
  if (!papers.length && !libraryConfigured) {
    if (papersCountEl) papersCountEl.textContent = "";
    if (papersFiltersEl) papersFiltersEl.innerHTML = "";
    papersSidebarEl.innerHTML = `
      <div class="sidebar-empty sidebar-empty-onboarding">
        <p>Your library is empty</p>
        <button class="onboarding-btn" id="library-setup-btn">Connect your library</button>
        <p class="sidebar-empty-hint">Sync your papers, highlights, and reading notes</p>
      </div>`;
    papersSidebarEl.querySelector("#library-setup-btn")
      ?.addEventListener("click", launchLibrarySetup);
    return;
  }

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

async function launchLibrarySetup() {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  // Show wizard in main detail area
  if (welcomeEl) welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  switchEditorTab("control-panel");

  detailEl.innerHTML = `
    <div class="onboarding-progress">
      <h2 class="exp-detail-title">Connect your paper library</h2>
      <p class="exp-detail-meta">Distillate syncs with Zotero to track your reading and extract highlights.</p>

      <div class="library-setup-wizard" id="library-wizard">
        <div class="library-step" id="lib-step-zotero">
          <div class="library-step-header">
            <span class="library-step-num">1</span>
            <span>Zotero credentials</span>
          </div>
          <p class="library-step-help">
            Create an API key at
            <a href="https://www.zotero.org/settings/keys/new" class="library-link" id="zotero-key-link">zotero.org/settings/keys</a>
            with read/write library access. Your user ID is shown on the same page.
          </p>
          <div class="library-field">
            <label for="lib-api-key">API key</label>
            <input type="password" id="lib-api-key" placeholder="your Zotero API key" spellcheck="false" autocomplete="off">
          </div>
          <div class="library-field">
            <label for="lib-user-id">User ID</label>
            <input type="text" id="lib-user-id" placeholder="numeric user ID" spellcheck="false" autocomplete="off">
          </div>
          <div class="library-error hidden" id="lib-zotero-error"></div>
          <button class="onboarding-btn" id="lib-verify-btn">Verify &amp; connect</button>
        </div>

        <div class="library-step hidden" id="lib-step-done">
          <div class="library-step-header">
            <span class="library-step-num">2</span>
            <span>Syncing your library</span>
          </div>
          <div class="wizard-flow" id="lib-sync-flow">
            <div class="flow-step" data-step="1"><span class="flow-dot active"></span><span class="flow-label">Pulling papers from Zotero...</span><span class="flow-detail"></span></div>
          </div>
        </div>
      </div>
    </div>`;

  // Open external links in browser
  document.getElementById("zotero-key-link")?.addEventListener("click", (e) => {
    e.preventDefault();
    if (window.nicolas?.openExternal) window.nicolas.openExternal("https://www.zotero.org/settings/keys/new");
    else window.open("https://www.zotero.org/settings/keys/new", "_blank");
  });

  const verifyBtn = document.getElementById("lib-verify-btn");
  const apiKeyInput = document.getElementById("lib-api-key");
  const userIdInput = document.getElementById("lib-user-id");
  const errorEl = document.getElementById("lib-zotero-error");

  // Step 1: Verify Zotero credentials
  verifyBtn.addEventListener("click", async () => {
    const apiKey = apiKeyInput.value.trim();
    const userId = userIdInput.value.trim();
    if (!apiKey || !userId) {
      errorEl.textContent = "Both fields are required.";
      errorEl.classList.remove("hidden");
      return;
    }
    verifyBtn.disabled = true;
    verifyBtn.textContent = "Verifying...";
    errorEl.classList.add("hidden");

    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/library/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zotero_api_key: apiKey, zotero_user_id: userId }),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.reason || "Verification failed");

      // Move to sync step
      document.getElementById("lib-step-zotero").classList.add("library-step-done");
      document.getElementById("lib-step-done").classList.remove("hidden");

      // Trigger paper sync
      try {
        const r = await fetch(`http://127.0.0.1:${serverPort}/sync`, { method: "POST" });
        const syncFlow = document.getElementById("lib-sync-flow");
        const dot = syncFlow?.querySelector(".flow-dot");
        const label = syncFlow?.querySelector(".flow-label");
        if (dot) dot.className = "flow-dot done";
        if (label) label.textContent = r.ok ? "Library synced!" : "Connected!";
      } catch {
        const syncFlow = document.getElementById("lib-sync-flow");
        const dot = syncFlow?.querySelector(".flow-dot");
        const label = syncFlow?.querySelector(".flow-label");
        if (dot) dot.className = "flow-dot done";
        if (label) label.textContent = "Connected!";
      }

      // Refresh papers sidebar
      libraryConfigured = true;
      await new Promise((r) => setTimeout(r, 800));
      fetchPapersData();
      fetchConnectors();
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.classList.remove("hidden");
      verifyBtn.disabled = false;
      verifyBtn.textContent = "Verify & connect";
    }
  });
}

function selectPaper(paperKey) {
  // Toggle: clicking the already-selected paper deselects
  if (currentPaperKey === paperKey) {
    deselectAll();
    return;
  }
  currentPaperKey = paperKey;
  currentProjectId = null;

  // Update sidebar selection
  papersSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.key === paperKey);
  });
  refreshChatSuggestions();

  // Show paper detail in experiment-detail area (reuse editor area)
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  // Hide experiment tabs, switch to control panel and show detail
  const editorTabs = document.getElementById("editor-tabs");
  if (editorTabs) editorTabs.classList.add("hidden");
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

      // Authors + date + venue on one line
      const metaLine1 = [];
      if (data.authors && data.authors.length) metaLine1.push(data.authors.join(", "));
      if (data.publication_date) metaLine1.push(data.publication_date);
      if (data.venue) metaLine1.push(data.venue);
      if (metaLine1.length) {
        const el = document.createElement("div");
        el.className = "exp-detail-meta";
        el.style.marginTop = "6px";
        el.textContent = metaLine1.join(" \u00B7 ");
        header.appendChild(el);
      }

      // URL + stats + badges on one line
      const metaLine2 = document.createElement("div");
      metaLine2.className = "exp-detail-meta";
      metaLine2.style.display = "flex";
      metaLine2.style.alignItems = "center";
      metaLine2.style.gap = "12px";
      metaLine2.style.flexWrap = "wrap";

      const paperUrl = data.url
        || (data.arxiv_id ? `https://arxiv.org/abs/${data.arxiv_id}` : "")
        || (data.doi ? `https://doi.org/${data.doi}` : "");
      if (data.status === "processed") {
        const b = document.createElement("span");
        b.className = "exp-detail-badge keep";
        b.textContent = "read";
        metaLine2.appendChild(b);
      }
      if (data.promoted) {
        const b = document.createElement("span");
        b.className = "exp-detail-badge";
        b.style.background = "var(--accent)";
        b.textContent = "promoted";
        metaLine2.appendChild(b);
      }

      const statParts = [];
      if (data.engagement) statParts.push(`${data.engagement}% engagement`);
      if (data.page_count) statParts.push(`${data.page_count} pages`);
      if (data.citation_count) statParts.push(`${data.citation_count} citations`);
      if (statParts.length) {
        const statsSpan = document.createElement("span");
        statsSpan.textContent = statParts.join(" \u00B7 ");
        metaLine2.appendChild(statsSpan);
      }

      if (paperUrl) {
        const linkEl = document.createElement("a");
        linkEl.className = "paper-external-link";
        linkEl.href = "#";
        linkEl.style.margin = "0";
        linkEl.textContent = data.arxiv_id ? `arxiv.org/abs/${data.arxiv_id}` : paperUrl.replace(/^https?:\/\//, "");
        linkEl.addEventListener("click", (e) => {
          e.preventDefault();
          window.nicolas.openExternal(paperUrl);
        });
        metaLine2.appendChild(linkEl);
      }

      if (metaLine2.children.length) header.appendChild(metaLine2);

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
        let hlText = typeof data.highlights === "string"
          ? data.highlights
          : data.highlights.map((h) => typeof h === "string" ? `- ${h}` : `- ${h.text || JSON.stringify(h)}`).join("\n");
        // Strip leading markdown heading (already shown as section title)
        hlText = hlText.replace(/^#{1,3}\s*Highlights?\s*\n*/i, "").trim();
        if (hlText) {
          const section = document.createElement("div");
          section.className = "exp-detail-section";
          const sTitle = document.createElement("h3");
          sTitle.textContent = "Highlights";
          section.appendChild(sTitle);
          const hl = document.createElement("div");
          hl.className = "paper-highlights-list markdown-body";
          hl.innerHTML = window.markedParse(hlText);
          section.appendChild(hl);
          detailEl.appendChild(section);
        }
      }

      // Related Experiments (cross-reference from linked_projects)
      if (data.linked_projects && data.linked_projects.length > 0) {
        const section = document.createElement("div");
        section.className = "exp-detail-section";
        const sTitle = document.createElement("h3");
        sTitle.textContent = "Related Experiments";
        section.appendChild(sTitle);
        const list = document.createElement("div");
        list.className = "related-experiments-list";
        for (const proj of data.linked_projects) {
          const item = document.createElement("div");
          item.className = "related-experiment-item";
          item.textContent = proj.name || proj.id;
          item.style.cursor = "pointer";
          item.addEventListener("click", () => {
            selectProject(proj.id);
          });
          list.appendChild(item);
        }
        section.appendChild(list);
        detailEl.appendChild(section);
      }
    })
    .catch((err) => {
      console.error("[paper-detail] Error:", err);
      detailEl.innerHTML = `<div class="exp-detail-loading">Failed to load paper details: ${err.message || err}</div>`;
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
        // Update the badge on the card (if in paper card view)
        const card = btn.closest(".paper-card");
        if (card) {
          const header = card.querySelector(".paper-card-header");
          if (header) {
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
        }
        // Update sidebar badge
        fetchPapersData();
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

/** A run is displayable if it's completed and has at least one numeric result. */
function isDisplayableRun(r) {
  const d = r.decision || r.status || "other";
  if (d === "running") return false;
  return r.results && Object.values(r.results).some(v => typeof v === "number");
}

/** Deduplicate runs by ID (last entry wins) and keep only displayable ones. */
function getDisplayRuns(runs) {
  const byId = new Map();
  for (const r of (runs || [])) byId.set(r.id, r);
  return [...byId.values()].filter(isDisplayableRun)
    .sort((a, b) => (a.run_number || 0) - (b.run_number || 0));
}

function findBestRun(runs, metricName) {
  const lower = isLowerBetter(metricName);
  let best = null;
  for (const r of (runs || [])) {
    const decision = r.decision || r.status || "";
    if (decision === "crash") continue;
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
  // Filter runs that have a real numeric value for this metric
  const points = [];
  for (let i = 0; i < runs.length; i++) {
    const val = runs[i].results?.[metricName];
    if (typeof val === "number" && isFinite(val)) {
      const decision = runs[i].decision || runs[i].status || "";
      points.push({ index: i, value: val, run: runs[i], best: decision === "best" });
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
  canvas.style.width = w + "px";
  canvas.style.height = h + "px";
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const pad = { top: 12, right: 32, bottom: 24, left: 48 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  // Y-axis range: scale to best runs + live points so the frontier
  // fills the chart. Dim dots outside this range are drawn at the edge.
  const bestValues = points.filter((p) => p.best).map((p) => p.value)
    .concat(livePoints.map((p) => p.value));
  // Fall back to all points if no best runs yet
  const rangeValues = bestValues.length ? bestValues : points.map((p) => p.value);
  let minVal = Math.min(...rangeValues);
  let maxVal = Math.max(...rangeValues);

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

  // Clip to canvas bounds to prevent overflow
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, w, h);
  ctx.clip();

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
      // Data spans less than one decade — fall back to linear-style
      // nice ticks so labels stay clean (e.g. 0.50, 0.60, 0.70)
      yTickValues = [];
      const linRange = maxVal - minVal;
      const linSpacing = niceNum(linRange / (maxTicks - 1), true);
      const linMin = Math.floor(minVal / linSpacing) * linSpacing;
      const linMax = Math.ceil(maxVal / linSpacing) * linSpacing;
      for (let v = linMin; v <= linMax + linSpacing * 0.5; v += linSpacing) {
        if (v >= minVal - linSpacing * 0.1 && v <= maxVal + linSpacing * 0.1) {
          yTickValues.push(v);
        }
      }
    }
  } else {
    // Linear scale: nice rounded intervals — increase spacing until we fit maxTicks
    let dataRange = maxVal - minVal;
    let tickSpacing = niceNum(dataRange / (maxTicks - 1), true);
    let niceMin = Math.floor(minVal / tickSpacing) * tickSpacing;
    let niceMax = Math.ceil(maxVal / tickSpacing) * tickSpacing;
    // If too many ticks, double spacing until it fits
    while (Math.round((niceMax - niceMin) / tickSpacing) + 1 > maxTicks) {
      tickSpacing = niceNum(tickSpacing * 2.1, true);
      niceMin = Math.floor(minVal / tickSpacing) * tickSpacing;
      niceMax = Math.ceil(maxVal / tickSpacing) * tickSpacing;
    }
    for (let v = niceMin; v <= niceMax + tickSpacing * 0.5; v += tickSpacing) {
      if (v >= minVal - tickSpacing * 0.1 && v <= maxVal + tickSpacing * 0.1) {
        yTickValues.push(v);
      }
    }
  }
  // Cull log-scale ticks that exceed maxTicks (linear ticks are already fitted above)
  if (useLogScale && yTickValues.length > maxTicks) {
    const step = Math.ceil(yTickValues.length / maxTicks);
    const culled = [];
    for (let i = 0; i < yTickValues.length; i += step) culled.push(yTickValues[i]);
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

  // Best-so-far frontier: use server-computed "best" flag
  const lowerBetter = isLowerBetter(metricName);
  if (points.length > 0) {
    // Seed frontier from the first "best" run (not the first point,
    // which may be a non-frontier run with a misleading value)
    const firstBestIdx = points.findIndex(p => p.best);
    const bestLine = [];

    if (firstBestIdx >= 0) {
      let bestSoFar = points[firstBestIdx].value;
      // Flat line from chart start to first best
      bestLine.push({ x: toX(0), y: toY(bestSoFar) });
      if (firstBestIdx > 0) {
        bestLine.push({ x: toX(firstBestIdx), y: toY(bestSoFar) });
      }

      // Downgrade "best" dots that sit above the frontier in display
      // order (they were best chronologically but timestamps put them
      // out of order on the chart)
      for (let i = firstBestIdx + 1; i < points.length; i++) {
        if (points[i].best) {
          const v = points[i].value;
          if (lowerBetter ? v < bestSoFar : v > bestSoFar) {
            bestSoFar = v;
          } else {
            // Above the frontier line — demote to dim dot
            points[i].best = false;
          }
        }
        bestLine.push({ x: toX(i), y: toY(bestSoFar) });
      }
      // Extend frontier to right edge
      const lastX = bestLine[bestLine.length - 1].x;
      const rightEdge = toX(totalPoints - 1);
      if (lastX < rightEdge) {
        bestLine.push({ x: rightEdge, y: toY(bestSoFar) });
      }
    }

    if (bestLine.length > 1) {
      ctx.strokeStyle = "rgba(74,222,128,0.5)";
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

  // Run dots: green = best (frontier-improving), dim = completed
  for (let i = 0; i < points.length; i++) {
    const x = toX(i);
    const y = toY(points[i].value);
    if (points[i].best) {
      ctx.fillStyle = "#4ade80";
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#0f0f23";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    } else {
      ctx.fillStyle = "#555";
      ctx.globalAlpha = 0.3;
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }
  }

  // Tilted description labels on every best run
  {
    const angle = -Math.PI / 6; // -30 degrees
    ctx.save();
    ctx.font = "8px -apple-system, sans-serif";
    ctx.fillStyle = "rgba(160,160,180,0.5)";
    ctx.textAlign = "left";
    for (let i = 0; i < points.length; i++) {
      if (!points[i].best) continue;
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
      ctx.fillStyle = "rgba(74, 222, 128, 0.8)";
      ctx.font = "10px sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(`goal: ${goal.threshold}`, pad.left + plotW - 4, goalY - 4);
    }
  }

  ctx.restore(); // End clip region

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

/* ───── Connectors ───── */

let cachedConnectors = [];

function fetchConnectors() {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/connectors`)
    .then((r) => r.json())
    .then((data) => _applyConnectorsData(data))
    .catch(() => {});
}

function renderConnectors(connectors) {
  const listEl = document.getElementById("connectors-list");
  if (!listEl) return;
  listEl.innerHTML = "";

  // Counter badge
  const countEl = document.getElementById("connectors-count");
  const connected = connectors.filter((c) => c.connected).length;
  if (countEl) countEl.textContent = `${connected}/${connectors.length}`;

  for (const c of connectors) {
    const item = document.createElement("div");
    item.className = "connector-item";
    item.dataset.id = c.id;
    item.dataset.setup = c.setup || "";

    const statusCheck = document.createElement("span");
    statusCheck.className = `connector-status ${c.connected ? "connected" : "disconnected"}`;
    statusCheck.textContent = c.connected ? "\u2713" : "\u2013";
    item.appendChild(statusCheck);

    const label = document.createElement("span");
    label.className = "connector-label";
    label.textContent = c.label;
    item.appendChild(label);

    if (c.service) {
      const service = document.createElement("span");
      service.className = "connector-service";
      service.textContent = c.service;
      item.appendChild(service);
    }

    if (c.connected && c.detail) {
      const detail = document.createElement("span");
      detail.className = "connector-detail";
      detail.textContent = c.detail;
      item.appendChild(detail);
    }

    item.addEventListener("click", () => handleConnectorClick(c));
    listEl.appendChild(item);
  }
}

function handleConnectorClick(connector) {
  if (!connector.connected) {
    launchConnectorSetup(connector.setup);
  } else {
    showConnectorSettings(connector.id);
  }
}

async function showConnectorSettings(connectorId) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  welcomeEl?.classList.add("hidden");
  detailEl.classList.remove("hidden");

  // Show control panel view
  const cpView = document.getElementById("control-panel-view");
  if (cpView && cpView.classList.contains("hidden")) {
    for (const v of editorViews) {
      const viewEl = document.getElementById(`${v}-view`);
      if (viewEl) viewEl.classList.toggle("hidden", v !== "control-panel");
    }
  }

  detailEl.innerHTML = '<div class="exp-detail-loading">Loading...</div>';

  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/connectors/${connectorId}`);
    const data = await r.json();
    if (!data.ok) throw new Error(data.reason || "Failed to load");
    const c = data.connector;

    detailEl.innerHTML = "";

    const header = document.createElement("div");
    header.className = "exp-detail-header";

    const titleRow = document.createElement("div");
    titleRow.className = "exp-detail-title-row";
    const title = document.createElement("h2");
    title.className = "exp-detail-title";
    title.textContent = c.label;
    const badge = document.createElement("span");
    const emailPending = c.id === "email" && c.connected && !c.verified;
    badge.className = `exp-detail-badge ${!c.connected ? "paused" : emailPending ? "waiting" : "running"}`;
    badge.textContent = !c.connected ? "not connected" : emailPending ? "pending verification" : "connected";

    titleRow.appendChild(title);
    titleRow.appendChild(badge);
    header.appendChild(titleRow);

    const service = document.createElement("div");
    service.className = "exp-detail-meta";
    service.style.marginTop = "4px";
    service.textContent = c.service;
    header.appendChild(service);

    detailEl.appendChild(header);

    // Settings table
    if (c.settings && c.settings.length) {
      const section = document.createElement("div");
      section.className = "connector-settings";

      for (const s of c.settings) {
        const row = document.createElement("div");
        row.className = "connector-setting-row";

        const label = document.createElement("span");
        label.className = "connector-setting-label";
        label.textContent = s.label;
        row.appendChild(label);

        const value = document.createElement("span");
        value.className = "connector-setting-value";
        value.textContent = s.value || "—";
        if (s.sensitive && s.value) value.classList.add("sensitive");
        row.appendChild(value);

        section.appendChild(row);
      }

      detailEl.appendChild(section);
    }

    // Email: resend verification link if pending
    if (c.id === "email" && c.connected && !c.verified) {
      const verifyRow = document.createElement("div");
      verifyRow.style.cssText = "margin-top:12px;font-size:12px;color:var(--text-dim);";
      verifyRow.innerHTML = 'Check your inbox to verify · <a href="#" id="resend-verify-settings" style="color:var(--accent);">Resend</a>';
      detailEl.appendChild(verifyRow);
      document.getElementById("resend-verify-settings")?.addEventListener("click", async (e) => {
        e.preventDefault();
        const link = e.target;
        link.textContent = "Sending...";
        try {
          const r = await fetch(`http://127.0.0.1:${serverPort}/email/resend-verification`, { method: "POST" });
          const d = await r.json();
          link.textContent = d.ok ? "Sent!" : "Failed";
        } catch { link.textContent = "Failed"; }
      });
    }

    // Reconfigure button
    const actions = document.createElement("div");
    actions.className = "connector-actions";
    const reconfigBtn = document.createElement("button");
    reconfigBtn.className = "onboarding-btn";
    reconfigBtn.textContent = c.connected ? "Reconfigure" : "Set up";
    reconfigBtn.addEventListener("click", () => launchConnectorSetup(c.id === "zotero" ? "library" : c.id === "email" ? "email" : c.id));
    actions.appendChild(reconfigBtn);
    detailEl.appendChild(actions);

  } catch (err) {
    detailEl.innerHTML = `<div class="exp-detail-loading">Failed: ${err.message}</div>`;
  }
}

function launchConnectorSetup(setup) {
  if (setup === "library") {
    launchLibrarySetup();
  } else if (setup === "email") {
    launchEmailSetup();
  } else if (setup === "remarkable") {
    inputEl.value = "How do I set up my reMarkable?";
    sendMessage();
  } else if (setup === "obsidian") {
    inputEl.value = "How do I connect Obsidian for my notes?";
    sendMessage();
  }
}

function launchEmailSetup() {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl || !serverPort) return;

  welcomeEl?.classList.add("hidden");
  detailEl.classList.remove("hidden");

  const cpView = document.getElementById("control-panel-view");
  if (cpView && cpView.classList.contains("hidden")) {
    for (const v of editorViews) {
      const viewEl = document.getElementById(`${v}-view`);
      if (viewEl) viewEl.classList.toggle("hidden", v !== "control-panel");
    }
  }

  detailEl.innerHTML = `
    <div class="onboarding-progress">
      <h2 class="exp-detail-title">Create your account</h2>
      <p class="exp-detail-meta" style="margin-top:6px;">Sync your library across devices and choose what lands in your inbox.</p>
      <div class="library-setup-wizard" style="margin-top:20px;">
        <div class="library-step" id="email-step">
          <div class="library-step-header">
            <span class="library-step-num">1</span>
            <span>Your email</span>
          </div>
          <div class="library-field">
            <label for="email-setup-input">Email address</label>
            <input type="email" id="email-setup-input" placeholder="you@example.com" spellcheck="false" autocomplete="email">
          </div>

          <div class="library-step-header" style="margin-top:16px;">
            <span class="library-step-num">2</span>
            <span>What to receive</span>
          </div>

          <div class="email-toggle-list">
            <label class="email-toggle">
              <input type="checkbox" name="email-experiment-reports" checked>
              <div class="email-toggle-text">
                <span class="email-toggle-name">Experiment reports</span>
                <span class="email-toggle-desc">When an experiment completes — results, best metric, key insight</span>
              </div>
            </label>
            <label class="email-toggle">
              <input type="checkbox" name="email-daily-papers" checked>
              <div class="email-toggle-text">
                <span class="email-toggle-name">Daily paper suggestions</span>
                <span class="email-toggle-desc">Every morning at 7am — three papers matching your interests</span>
              </div>
            </label>
            <label class="email-toggle">
              <input type="checkbox" name="email-weekly-digest" checked>
              <div class="email-toggle-text">
                <span class="email-toggle-name">Weekly digest</span>
                <span class="email-toggle-desc">Every Monday — papers read, experiments ran, highlights</span>
              </div>
            </label>
          </div>

          <p class="email-privacy-note">Emails are sent from distillate.dev via Supabase and Resend. Enabling updates stores your email and a summary of your reading and experiment activity. You can disable at any time.</p>
          <div class="library-error hidden" id="email-setup-error"></div>
          <button class="onboarding-btn" id="email-setup-submit" style="margin-top:12px;">Enable updates</button>
        </div>
      </div>
    </div>`;

  document.getElementById("email-setup-submit")?.addEventListener("click", async () => {
    const email = document.getElementById("email-setup-input")?.value?.trim();
    const errorEl = document.getElementById("email-setup-error");

    if (!email || !email.includes("@")) {
      if (errorEl) { errorEl.textContent = "Please enter a valid email."; errorEl.classList.remove("hidden"); }
      return;
    }

    const experimentReports = document.querySelector('[name="email-experiment-reports"]')?.checked ?? true;
    const dailyPapers = document.querySelector('[name="email-daily-papers"]')?.checked ?? true;
    const weeklyDigest = document.querySelector('[name="email-weekly-digest"]')?.checked ?? true;

    const btn = document.getElementById("email-setup-submit");
    if (btn) { btn.disabled = true; btn.textContent = "Setting up..."; }

    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/email/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          experiment_reports: experimentReports,
          daily_papers: dailyPapers,
          weekly_digest: weeklyDigest,
        }),
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.reason || "Failed");

      localStorage.setItem("distillate-email-asked", "1");
      const enabled = [];
      if (experimentReports) enabled.push("experiment reports");
      if (dailyPapers) enabled.push("daily papers");
      if (weeklyDigest) enabled.push("weekly digest");
      const enabledText = enabled.join(", ").replace(/^./, c => c.toUpperCase());
      const verified = data.verified;
      const stepEl = document.getElementById("email-step");
      stepEl.innerHTML =
        '<div style="text-align:center;padding:24px 16px;">' +
        '<div style="font-size:28px;margin-bottom:6px;">✓</div>' +
        '<div style="font-size:15px;font-weight:600;color:var(--green);margin-bottom:6px;">You\'re in!</div>' +
        '<div style="font-size:13px;color:var(--text-dim);margin-bottom:12px;">' + enabledText + ' coming to ' + email + '</div>' +
        (verified ? '' :
          '<div style="font-size:12px;color:var(--warning);margin-bottom:6px;">Check your inbox to verify your email</div>' +
          '<a href="#" id="resend-verify-link" style="font-size:12px;color:var(--text-dim);">Resend verification email</a>') +
        '</div>';
      if (!verified) {
        document.getElementById("resend-verify-link")?.addEventListener("click", async (e) => {
          e.preventDefault();
          const link = e.target;
          link.textContent = "Sending...";
          link.style.pointerEvents = "none";
          try {
            const r = await fetch(`http://127.0.0.1:${serverPort}/email/resend-verification`, { method: "POST" });
            const d = await r.json();
            link.textContent = d.ok ? "Sent! Check your inbox" : "Failed — try again";
          } catch { link.textContent = "Failed — try again"; }
          link.style.pointerEvents = "";
        });
      }
      fetchConnectors();
    } catch (err) {
      if (errorEl) { errorEl.textContent = err.message; errorEl.classList.remove("hidden"); }
      if (btn) { btn.disabled = false; btn.textContent = "Enable updates"; }
    }
  });
}

// + button: show menu of disconnected connectors
document.getElementById("add-connector-btn")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const existing = document.querySelector(".connector-add-menu");
  if (existing) { existing.remove(); return; }

  const disconnected = cachedConnectors.filter((c) => !c.connected);
  if (!disconnected.length) return;

  const section = document.getElementById("connectors-section");
  if (!section) return;
  section.style.position = "relative";

  const menu = document.createElement("div");
  menu.className = "connector-add-menu";

  for (const c of disconnected) {
    const item = document.createElement("div");
    item.className = "connector-add-item";
    item.innerHTML = `<span class="connector-label">${c.label}</span><span class="connector-add-desc">${c.service}</span>`;
    item.addEventListener("click", () => {
      menu.remove();
      launchConnectorSetup(c.setup);
    });
    menu.appendChild(item);
  }
  section.appendChild(menu);

  // Close on click outside
  setTimeout(() => {
    const close = (ev) => {
      if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener("mousedown", close); }
    };
    document.addEventListener("mousedown", close);
  }, 0);
});

// Toggle collapse
document.getElementById("connectors-toggle")?.addEventListener("click", () => {
  const listEl = document.getElementById("connectors-list");
  const chevron = document.getElementById("connectors-toggle");
  if (listEl) listEl.classList.toggle("hidden");
  if (chevron) chevron.classList.toggle("collapsed");
});

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
    .then((data) => _applyExperimentsData(data))
    .catch(() => { experimentsFirstLoad = false; _sessionTransition = null; });
}

function reloadCurrentProject() {
  if (!serverPort || !currentProjectId) return fetchExperimentsList();
  // Rescan disk first, then fetch updated data and re-render
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(currentProjectId)}/scan`, { method: "POST" })
    .then((r) => r.json())
    .then(() => fetch(`http://127.0.0.1:${serverPort}/experiments/list`))
    .then((r) => r.json())
    .then((data) => {
      if (data.projects) {
        renderProjectsList(data.projects);
        if (currentProjectId) renderProjectDetail(currentProjectId);
      }
    })
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
        // Fetch fresh data, THEN re-render
        return fetch(`http://127.0.0.1:${serverPort}/experiments/list`)
          .then((r) => r.json())
          .then((listData) => {
            if (listData.projects) {
              renderProjectsList(listData.projects);
              if (currentProjectId) renderProjectDetail(currentProjectId);
            }
          });
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
  // Sort: active sessions first, then by when the experiment was added (stable)
  projects.sort((a, b) => {
    // Active sessions always on top
    if (a.active_sessions > 0 && b.active_sessions === 0) return -1;
    if (b.active_sessions > 0 && a.active_sessions === 0) return 1;
    // Then by added_at (most recent first) — stable across rescans
    const aDate = a.added_at || "";
    const bDate = b.added_at || "";
    return bDate.localeCompare(aDate);
  });

  cachedProjects = projects;
  if (!experimentsSidebarEl) return;

  // Manage session polling — skip if SSE is already pushing updates
  const hasActive = projects.some((p) => p.active_sessions > 0);
  if (hasActive && !sseSource) startSessionPolling();
  else if (!hasActive || sseSource) stopSessionPolling();

  // Update count badge
  if (experimentsCountEl) {
    experimentsCountEl.textContent = projects.length ? `${projects.length}` : "";
  }

  if (!projects.length) {
    const onboarded = localStorage.getItem("distillate-onboarded");
    experimentsSidebarEl.innerHTML = onboarded
      ? `<div class="sidebar-empty">
          <p>No experiments yet.</p>
          <p class="sidebar-empty-hint">Click <strong>+ New</strong> or ask Nicolas.</p>
        </div>`
      : `<div class="sidebar-empty sidebar-empty-onboarding">
          <p>Your laboratory is empty</p>
          <button class="onboarding-btn" id="sidebar-demo-btn">Launch demo experiment</button>
          <p class="sidebar-empty-hint">Watch an AI agent train a neural network in real time</p>
        </div>`;
    if (!onboarded) {
      experimentsSidebarEl.querySelector("#sidebar-demo-btn")
        ?.addEventListener("click", launchDemoExperiment);
    }
    return;
  }

  experimentsSidebarEl.innerHTML = "";

  // Add/remove compare button in sidebar header when 2+ projects
  const sidebarHeader = experimentsSidebarEl.parentElement?.querySelector(".sidebar-header");
  // Compare button removed — not ready for prime time yet

  for (const proj of projects) {
    const item = document.createElement("div");
    item.className = `sidebar-item${proj.id === currentProjectId ? " active" : ""}`;
    item.dataset.id = proj.id;

    const icon = document.createElement("span");
    icon.className = "sidebar-item-icon";
    if (proj.active_sessions > 0 && proj.current_run !== "Session active") {
      // Running: agent actively working
      icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10" class="blink-play"><polygon points="1,0 9,5 1,10" fill="#4ade80"/></svg>`;
      icon.title = "Running";
    } else if (proj.active_sessions > 0) {
      // Ready: session alive, agent waiting for steering
      icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#7366f1"/></svg>`;
      icon.title = "Ready";
    } else {
      // Paused: no active session
      icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" rx="1.5" fill="#8888a0"/></svg>`;
      icon.title = "Paused";
    }
    item.appendChild(icon);

    const nameGroup = document.createElement("div");
    nameGroup.className = "sidebar-item-name-group";

    const name = document.createElement("span");
    name.className = "sidebar-item-name";
    name.textContent = proj.name || proj.id;
    nameGroup.appendChild(name);

    const meta = document.createElement("span");
    meta.className = "sidebar-item-meta";
    // Count displayable runs (deduped, non-running, with metrics)
    let sidebarTotal = 0, sidebarBest = 0;
    if (proj.runs) {
      for (const r of getDisplayRuns(proj.runs)) {
        sidebarTotal++;
        if ((r.decision || "") === "best") sidebarBest++;
      }
    }
    meta.textContent = `${sidebarTotal} runs \u00B7 ${sidebarBest} best`;
    nameGroup.appendChild(meta);
    item.appendChild(nameGroup);

    if (proj.active_sessions > 0) {
      const badge = document.createElement("span");
      badge.className = "sidebar-item-badge running";
      badge.textContent = proj.active_sessions;
      item.appendChild(badge);
    }

    item.addEventListener("click", () => selectProject(proj.id));
    experimentsSidebarEl.appendChild(item);
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
  resetResultsTab();
  resetSetupTab();
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
    node.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
    flowEl.scrollIntoView({ behavior: "smooth", block: "nearest" });

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
    const privateRepo = settingPrivateRepos ? settingPrivateRepos.checked : false;
    try {
      const ghResp = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/github`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: `distillate-xp-${projectId}`, private: privateRepo }),
      });
      const ghData = await ghResp.json();
      if (ghData.ok) {
        const repoUrl = ghData.url || "";
        updateStep(flowEl, 5, "done", "");
        // Show copyable repo URL
        if (repoUrl) {
          const visibility = privateRepo ? "private" : "public";
          const visColor = privateRepo ? "var(--yellow)" : "var(--emerald)";
          const urlEl = document.createElement("div");
          urlEl.style.cssText = "margin: 8px 0; display: flex; align-items: center; gap: 6px; flex-wrap: wrap;";
          urlEl.innerHTML = `<span style="font-size:10px; font-weight:600; color:${visColor}; background:${privateRepo ? 'rgba(245,158,11,0.1)' : 'rgba(52,211,153,0.1)'}; padding:1px 6px; border-radius:8px; text-transform:uppercase; letter-spacing:0.04em;">${visibility}</span><a href="#" class="paper-external-link" style="font-size:12px;">${repoUrl}</a><button class="paper-action-btn" style="font-size:10px; padding:2px 6px;">Copy</button>`;
          urlEl.querySelector("a").addEventListener("click", (e) => { e.preventDefault(); window.nicolas?.openExternal(repoUrl); });
          urlEl.querySelector("button").addEventListener("click", () => { navigator.clipboard.writeText(repoUrl); urlEl.querySelector("button").textContent = "Copied!"; setTimeout(() => urlEl.querySelector("button").textContent = "Copy", 1500); });
          flowEl.appendChild(urlEl);
        }
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
    reviewSection.scrollIntoView({ behavior: "smooth", block: "nearest" });

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

async function launchDemoExperiment() {
  const detailEl = document.getElementById("experiment-detail");
  const welcomeEl = document.getElementById("welcome");
  if (!detailEl || !serverPort) return;

  // Show progress in the detail area
  if (welcomeEl) welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  switchEditorTab("control-panel");

  // Build 3-step flowchart (reuse wizard-flow CSS)
  detailEl.innerHTML = `
    <div class="onboarding-progress">
      <h2 class="exp-detail-title">Setting up your first experiment</h2>
      <p class="exp-detail-meta">A tiny transformer will learn matrix multiplication while you watch.</p>
      <div class="wizard-flow" id="onboarding-flow">
        <div class="flow-step" data-step="1"><span class="flow-dot pending"></span><span class="flow-label">Scaffolding experiment</span><span class="flow-detail"></span></div>
        <div class="flow-step" data-step="2"><span class="flow-dot pending"></span><span class="flow-label">Creating GitHub repository</span><span class="flow-detail"></span></div>
        <div class="flow-step" data-step="3"><span class="flow-dot pending"></span><span class="flow-label">Launching Claude Code agent</span><span class="flow-detail"></span></div>
      </div>
    </div>`;

  const flowEl = document.getElementById("onboarding-flow");

  function updateOnboardingStep(stepId, status, detail) {
    const node = flowEl.querySelector(`.flow-step[data-step="${stepId}"]`);
    if (!node) return;
    const dot = node.querySelector(".flow-dot");
    const detailSpan = node.querySelector(".flow-detail");
    dot.className = `flow-dot ${status}`;
    if (detail) detailSpan.textContent = detail;
    node.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  let projectId = null;

  // Step 1: Scaffold from template
  updateOnboardingStep(1, "active", "");
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/scaffold`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template: "tiny-matmul", name: "TinyMatMul" }),
    });
    const data = await r.json();
    if (!data.ok) throw new Error(data.reason || "Scaffold failed");
    projectId = data.project_id;
    updateOnboardingStep(1, "done", data.path);
    if (data.already_exists) {
      // Already scaffolded — skip to launch
      updateOnboardingStep(2, "done", "already created");
    }
  } catch (err) {
    updateOnboardingStep(1, "error", err.message);
    return;
  }

  // Step 2: Create GitHub repo (non-fatal)
  if (!flowEl.querySelector('[data-step="2"] .flow-dot.done')) {
    updateOnboardingStep(2, "active", "");
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/github`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: `distillate-xp-${projectId}`, private: false }),
      });
      const data = await r.json();
      if (data.ok) {
        updateOnboardingStep(2, "done", data.url || "");
      } else {
        updateOnboardingStep(2, "skipped", data.reason || "gh CLI not found");
      }
    } catch {
      updateOnboardingStep(2, "skipped", "GitHub unavailable");
    }
  }

  // Step 3: Launch Claude Code session
  updateOnboardingStep(3, "active", "");
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/launch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: "claude-sonnet-4-6" }),
    });
    const data = await r.json();
    if (!data.ok) throw new Error(data.reason || "Launch failed");
    updateOnboardingStep(3, "done", "Agent started");
  } catch (err) {
    updateOnboardingStep(3, "error", err.message);
    return;
  }

  // Success: refresh sidebar, select project, switch to Session tab
  window._onboardingProjectId = projectId;
  localStorage.setItem("distillate-onboarded", "1");
  await fetchExperimentsList();
  selectProject(projectId);
  switchEditorTab("session");
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

function deselectAll() {
  currentProjectId = null;
  currentPaperKey = null;
  const detailEl = document.getElementById("experiment-detail");
  if (detailEl) { detailEl.classList.add("hidden"); detailEl.innerHTML = ""; }
  welcomeEl?.classList.remove("hidden");
  const editorTabs = document.getElementById("editor-tabs");
  if (editorTabs) editorTabs.classList.add("hidden");
  const tabLabel = document.getElementById("editor-tabs-project-name");
  if (tabLabel) tabLabel.textContent = "";
  document.querySelectorAll("#experiments-sidebar .sidebar-item").forEach((el) => el.classList.remove("active"));
  papersSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => el.classList.remove("active"));
  resetResultsTab();
  resetSetupTab();
  switchEditorTab("control-panel");
  refreshChatSuggestions();
}

function selectProject(projectId) {
  // Toggle: clicking the already-selected project deselects
  if (currentProjectId === projectId) {
    deselectAll();
    return;
  }
  const previousProject = currentProjectId;
  currentProjectId = projectId;
  currentPaperKey = null;

  // Show experiment tabs and name
  const editorTabs = document.getElementById("editor-tabs");
  if (editorTabs) editorTabs.classList.remove("hidden");
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
  refreshChatSuggestions();
}

function renderProjectDetail(projectId) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;

  const proj = cachedProjects.find((p) => p.id === projectId);
  if (!proj) return;

  // Show detail
  welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");
  // Clean up any live timers from previous render
  if (window._activeTimers) {
    window._activeTimers.forEach(clearInterval);
    window._activeTimers = [];
  }
  detailEl.innerHTML = "";

  // Switch to control panel only on first selection of a new project.
  // Re-renders of the same project must NOT yank the user off their current tab.
  const isNewProject = renderProjectDetail._lastProjectId !== projectId;
  renderProjectDetail._lastProjectId = projectId;
  const cpView = document.getElementById("control-panel-view");
  if (isNewProject && cpView && cpView.classList.contains("hidden")) {
    for (const v of editorViews) {
      const viewEl = document.getElementById(`${v}-view`);
      if (viewEl) viewEl.classList.toggle("hidden", v !== "control-panel");
    }
    document.querySelectorAll(".editor-tab").forEach((t) => t.classList.remove("active"));
    document.querySelector('.editor-tab[data-view="control-panel"]')?.classList.add("active");
  }


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

  // Shared filtered array: deduped, non-running, with at least one numeric result
  const displayRuns = getDisplayRuns(proj.runs);

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
  const isReady = proj.active_sessions > 0 && proj.current_run === "Session active";
  const badge = document.createElement("span");
  if (proj.active_sessions > 0 && !isReady) {
    badge.className = "exp-detail-badge running";
    badge.innerHTML = `<span class="badge-play-icon">\u25B6</span> running`;
    titleLeft.appendChild(badge);
  } else if (proj.active_sessions > 0) {
    badge.className = "exp-detail-badge ready";
    badge.innerHTML = `<svg width="8" height="8" viewBox="0 0 8 8" style="margin-right:3px;position:relative;top:0.5px"><circle cx="4" cy="4" r="3.5" fill="currentColor"/></svg> ready`;
    titleLeft.appendChild(badge);
  } else {
    badge.className = "exp-detail-badge paused";
    badge.innerHTML = `<svg width="8" height="8" viewBox="0 0 8 8" style="margin-right:3px;position:relative;top:0.5px"><rect x="1" y="1" width="6" height="6" rx="1" fill="currentColor"/></svg> paused`;
    titleLeft.appendChild(badge);
  }

  // Training timer: only show when a session is actively running
  if (proj.active_sessions > 0 && proj.runs && proj.runs.length) {
    // Deduplicate by run ID — take the latest entry per ID
    const byId = new Map();
    for (const r of proj.runs) byId.set(r.id, r);
    const uniqueRuns = [...byId.values()];

    // Sum completed run durations
    let totalTrainingSecs = 0;
    let liveRunStartMs = null;
    for (const r of uniqueRuns) {
      if (r.duration_seconds) {
        totalTrainingSecs += r.duration_seconds;
      }
      // Check for a currently running run (has started_at but no completed_at)
      // Ignore stale announcements older than 4 hours (likely never concluded)
      if ((r.decision === "running" || r.status === "running") && r.started_at && !r.completed_at) {
        const startMs = new Date(r.started_at).getTime();
        const ageHours = (Date.now() - startMs) / 3600000;
        if (ageHours < 4) {
          liveRunStartMs = startMs;
        }
      }
    }

    if (totalTrainingSecs > 0 || liveRunStartMs) {
      const timerEl = document.createElement("span");
      timerEl.className = "exp-detail-timer";
      titleLeft.appendChild(timerEl);
      const update = () => {
        let secs = totalTrainingSecs;
        if (liveRunStartMs) {
          secs += Math.max(0, Math.floor((Date.now() - liveRunStartMs) / 1000));
        }
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        const label = liveRunStartMs ? "" : "";
        timerEl.textContent = h > 0
          ? `${h}h ${m}m training`
          : `${m}:${String(s).padStart(2, "0")} training`;
      };
      update();
      if (liveRunStartMs) {
        const iv = setInterval(update, 1000);
        if (!window._activeTimers) window._activeTimers = [];
        window._activeTimers.push(iv);
      }
    }
  }
  // Compact stats inline with title (displayRuns is already deduped and filtered)
  const decisionCounts = {};
  for (const r of displayRuns) {
    const d = r.decision || r.status || "other";
    decisionCounts[d] = (decisionCounts[d] || 0) + 1;
  }
  const completedRuns = Object.values(decisionCounts).reduce((a, b) => a + b, 0);
  const statParts = [`${completedRuns} runs`];
  if (decisionCounts.best) statParts.push(`${decisionCounts.best} best`);
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
    const bestRun = findBestRun(displayRuns, currentKey);
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

  // Status card: show current_run if running, ready notice if waiting, otherwise latest_learning
  if (proj.current_run && !isReady) {
    const current = document.createElement("div");
    current.className = "exp-detail-status-card running";
    const runNum = displayRuns.length + 1;
    current.innerHTML = `<span class="status-card-label running">Run ${runNum}:</span> ${escapeHtml(proj.current_run)}`;
    header.appendChild(current);

    // Per-run elapsed timer (only if the announcement is recent)
    if (proj.current_run_started) {
      const runStartMs = new Date(proj.current_run_started).getTime();
      const elapsed = Date.now() - runStartMs;
      const budgetMs = (proj.duration_minutes || 5) * 60 * 1000;
      // Only show if announcement is within 3x budget (otherwise it's stale)
      if (elapsed >= 0 && elapsed < budgetMs * 3) {
        const timerSpan = document.createElement("span");
        timerSpan.className = "run-timer";
        current.appendChild(timerSpan);
        const updateRunTimer = () => {
          const secs = Math.floor((Date.now() - runStartMs) / 1000);
          const m = Math.floor(secs / 60);
          const s = secs % 60;
          timerSpan.textContent = `${m}:${String(s).padStart(2, "0")}`;
        };
        updateRunTimer();
        const iv = setInterval(updateRunTimer, 1000);
        if (!window._activeTimers) window._activeTimers = [];
        window._activeTimers.push(iv);
      }
    }
  } else if (isReady) {
    const readyCard = document.createElement("div");
    readyCard.className = "exp-detail-status-card ready";
    readyCard.innerHTML = `<span class="status-card-label ready">Active</span> Agent working — analyzing runs, planning next steps`;
    header.appendChild(readyCard);
  } else if (proj.latest_learning) {
    const learning = document.createElement("div");
    learning.className = "exp-detail-status-card";
    learning.innerHTML = `<span class="status-card-label">Latest:</span> ${escapeHtml(proj.latest_learning)}`;
    header.appendChild(learning);
  }

  // Goal chips
  {
    const goalsEl = document.createElement("div");
    goalsEl.className = "exp-detail-goals";
    if (proj.goals && proj.goals.length) {
      for (const g of proj.goals) {
        if (g.threshold == null) continue;  // skip goals without a threshold
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
    header.appendChild(goalsEl);
  }

  // Action buttons — below title row
  const actions = document.createElement("div");
  actions.className = "exp-detail-actions";

  if (_sessionTransition === "stopping") {
    // Show spinner while stop is in flight
    const btn = document.createElement("button");
    btn.className = "action-btn action-btn-stop action-btn-spinner";
    btn.textContent = "Stopping\u2026";
    btn.disabled = true;
    actions.appendChild(btn);
  } else if (_sessionTransition === "launching") {
    const btn = document.createElement("button");
    btn.className = "action-btn action-btn-launch action-btn-spinner";
    btn.textContent = "Launching\u2026";
    btn.disabled = true;
    actions.appendChild(btn);
  } else if (proj.active_sessions > 0) {
    const stopBtn = document.createElement("button");
    stopBtn.className = "action-btn action-btn-stop";
    stopBtn.textContent = "Stop";
    stopBtn.addEventListener("click", () => stopProject(proj.id, stopBtn));
    actions.appendChild(stopBtn);

    const attachBtn = document.createElement("button");
    attachBtn.className = "paper-action-btn";
    attachBtn.textContent = "Open in Terminal";
    attachBtn.title = "Detach session to Terminal.app";
    attachBtn.addEventListener("click", () => attachToProject(proj.id, attachBtn));
    actions.appendChild(attachBtn);
  } else {
    const launchBtn = document.createElement("button");
    launchBtn.className = "action-btn action-btn-launch";
    launchBtn.textContent = "Launch";
    launchBtn.addEventListener("click", () => {
      launchProject(proj.id, "claude-sonnet-4-6", launchBtn);
    });
    actions.appendChild(launchBtn);
  }

  const reloadBtn = document.createElement("button");
  reloadBtn.className = "paper-action-btn";
  reloadBtn.textContent = "\u21BB Reload";
  reloadBtn.title = "Rescan & refresh (\u2318R)";
  reloadBtn.addEventListener("click", () => doReload(proj.id, reloadBtn));
  actions.appendChild(reloadBtn);

  const settingsActionBtn = document.createElement("button");
  settingsActionBtn.className = "paper-action-btn";
  settingsActionBtn.textContent = "\u2699 Settings";
  settingsActionBtn.title = "Experiment settings";
  settingsActionBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    showSettingsPopover(proj, settingsActionBtn);
  });
  actions.appendChild(settingsActionBtn);

  if (proj.github_url) {
    const ghBtn = document.createElement("a");
    ghBtn.className = "paper-action-btn github-flare";
    ghBtn.href = "#";
    ghBtn.title = "Open public repo";
    ghBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>`;
    ghBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      window.nicolas.openExternal(proj.github_url);
    });
    actions.appendChild(ghBtn);
  }

  titleLeft.appendChild(actions);

  detailEl.appendChild(header);


  // Metric chart
  let activeMetric = "";

  if (allMetricNames.size > 0 && displayRuns.length >= 2) {
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
        renderMetricChart(canvas, displayRuns, activeMetric, liveMetrics[proj.id], { logScale: chartLogScale });
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
      renderMetricChart(canvas, displayRuns, activeMetric, liveMetrics[proj.id], { logScale: chartLogScale });
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
        renderMetricChart(canvas, displayRuns, activeMetric, liveMetrics[proj.id], { logScale: chartLogScale });
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
      renderMetricChart(canvas, displayRuns, activeMetric, liveMetrics[proj.id], { logScale: chartLogScale });
      setupChartResize(chartContainer, canvas, displayRuns, () => activeMetric, () => proj.id, () => ({ logScale: chartLogScale }));
    });
  }

  // Runs grid and insights are rendered in the Results tab (see loadResults)

  // Related Papers section (cross-reference)
  if (proj.linked_papers && proj.linked_papers.length > 0) {
    const section = document.createElement("div");
    section.className = "exp-detail-section";
    const sTitle = document.createElement("h3");
    sTitle.textContent = "Related Papers";
    section.appendChild(sTitle);
    const list = document.createElement("div");
    list.className = "related-papers-list";
    for (const paperTitle of proj.linked_papers) {
      const item = document.createElement("div");
      item.className = "related-paper-item";
      item.textContent = paperTitle;
      item.style.cursor = "pointer";
      item.addEventListener("click", () => {
        // Try to find the paper in cached papers by title match
        const match = (cachedPapers || []).find(
          (p) => p.title === paperTitle || p.citekey === paperTitle
        );
        if (match) {
          selectPaper(match.key);
        }
      });
      list.appendChild(item);
    }
    section.appendChild(list);
    detailEl.appendChild(section);
  }

  // Refresh the visible tab's content (Results or Prompt) for this project
  const visibleTab = document.querySelector(".editor-tab.active")?.dataset?.view;
  if (visibleTab === "results") {
    loadResults(projectId);
  } else if (visibleTab === "prompt-editor") {
    showSetupWithContent();
    loadPromptEditor(projectId);
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
  backfillBtn.textContent = "Backfill via agent";
  backfillBtn.title = "Instructs the running agent to discover missing runs from git history";
  backfillBtn.addEventListener("click", () => {
    if (!confirm("This will instruct the agent to scan git history and log all missing runs to runs.jsonl. This may take a few minutes. Continue?")) return;
    popover.remove();
    startBackfill(proj);
  });

  // Rescan logs — force re-read of all session logs + runs.jsonl
  const fullRescanBtn = document.createElement("button");
  fullRescanBtn.className = "action-btn";
  fullRescanBtn.textContent = "Rescan logs";
  fullRescanBtn.title = "Re-read session logs and runs.jsonl — no agent needed";
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
        setTimeout(() => { fullRescanBtn.textContent = "Rescan logs"; fullRescanBtn.disabled = false; }, 3000);
      })
      .catch(() => {
        fullRescanBtn.textContent = "Error";
        setTimeout(() => { fullRescanBtn.textContent = "Rescan logs"; fullRescanBtn.disabled = false; }, 3000);
      });
  });

  const separator = document.createElement("div");
  separator.className = "danger-warning";
  separator.textContent = "Danger zone";

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "action-btn action-btn-danger";
  deleteBtn.textContent = "Delete experiment";
  deleteBtn.addEventListener("click", () => {
    popover.remove();
    const name = proj.name || proj.id;
    if (!confirm(`Delete "${name}" from tracking?\n\nThis removes ${proj.run_count || 0} run(s) from Distillate.\nSource files and GitHub repo will NOT be deleted.`)) return;

    const detailEl = document.getElementById("experiment-detail");
    const welcomeEl = document.getElementById("welcome");
    const deleteUrl = `http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}`;
    console.log("[delete] DELETE", deleteUrl);
    fetch(deleteUrl, { method: "DELETE" })
      .then(r => {
        console.log("[delete] response status:", r.status);
        return r.json();
      })
      .then(data => {
        console.log("[delete] response:", data);
        if (data.ok) {
          currentProjectId = null;
          fetchExperimentsList();
          if (detailEl) detailEl.innerHTML = "";
          if (welcomeEl) { welcomeEl.classList.remove("hidden"); }
          if (detailEl) detailEl.classList.add("hidden");
          resetResultsTab();
          resetSetupTab();
        } else {
          alert(data.reason || "Failed to delete");
        }
      })
      .catch((err) => {
        console.error("[delete] error:", err);
        alert("Network error: " + err.message);
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
    progressBar.style.background = "var(--green, #4ade80)";
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
      progressBar.style.background = "var(--green, #4ade80)";
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
  resetResultsTab();
  resetSetupTab();

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
  _sessionTransition = "launching";
  btn.disabled = true;
  btn.textContent = "Launching\u2026";
  btn.classList.add("action-btn-spinner");
  showSessionConnecting();
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/launch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  })
    .then((r) => r.json())
    .then((data) => {
      if (data.ok) {
        if (data.tmux_session) {
          const sessionView = document.getElementById("session-view");
          if (sessionView && !sessionView.classList.contains("hidden"))
            attachToTerminalSession(projectId, data.tmux_session);
          const st = document.querySelector('.editor-tab[data-view="session"]');
          if (st) st.classList.add("has-update");
        }
        fetchExperimentsList();
      } else {
        showSessionEmpty();
        btn.classList.remove("action-btn-spinner");
        btn.textContent = data.reason || "Failed";
        setTimeout(() => { btn.textContent = "Launch"; btn.disabled = false; }, 2000);
      }
    })
    .catch((err) => {
      _sessionTransition = null;
      showSessionEmpty();
      btn.classList.remove("action-btn-spinner");
      btn.textContent = "Error";
      console.error("Launch failed:", err);
      showToast("Failed to launch experiment");
      setTimeout(() => { btn.textContent = "Launch"; btn.disabled = false; }, 3000);
    });
}

function stopProject(projectId, btn) {
  if (!serverPort) return;
  _sessionTransition = "stopping";
  btn.disabled = true;
  btn.textContent = "Stopping\u2026";
  btn.classList.add("action-btn-spinner");
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/stop`, {
    method: "POST",
  })
    .then((r) => r.json())
    .then(() => fetchExperimentsList())
    .catch((err) => {
      _sessionTransition = null;
      btn.classList.remove("action-btn-spinner");
      btn.textContent = "Error";
      console.error("Stop failed:", err);
      showToast("Failed to stop experiment");
      setTimeout(() => { btn.textContent = "Stop"; btn.disabled = false; }, 3000);
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

/* ───── Results tab ───── */

// ---------------------------------------------------------------------------
// Results + Setup tab helpers for empty/no-selection states
// ---------------------------------------------------------------------------

function showResultsNoSelection() {
  const noSel = document.getElementById("results-no-selection");
  const empty = document.getElementById("results-empty");
  const rendered = document.getElementById("results-rendered");
  if (noSel) noSel.classList.remove("hidden");
  if (empty) empty.classList.add("hidden");
  if (rendered) rendered.classList.add("hidden");
}

function resetResultsTab() {
  showResultsNoSelection();
}

function showSetupNoSelection() {
  const noSel = document.getElementById("setup-no-selection");
  const toolbar = document.getElementById("setup-toolbar");
  const promptPane = document.getElementById("setup-prompt-pane");
  if (noSel) noSel.classList.remove("hidden");
  if (toolbar) toolbar.classList.add("hidden");
  if (promptPane) promptPane.classList.add("hidden");
}

function showSetupWithContent() {
  const noSel = document.getElementById("setup-no-selection");
  const toolbar = document.getElementById("setup-toolbar");
  const promptPane = document.getElementById("setup-prompt-pane");
  if (noSel) noSel.classList.add("hidden");
  if (toolbar) toolbar.classList.remove("hidden");
  if (promptPane) promptPane.classList.remove("hidden");
}

function resetSetupTab() {
  showSetupNoSelection();
}

function loadResults(projectId) {
  if (!projectId) return;
  const noSel = document.getElementById("results-no-selection");
  const rendered = document.getElementById("results-rendered");
  const emptyEl = document.getElementById("results-empty");
  if (!rendered) return;

  const proj = cachedProjects.find((p) => p.id === projectId);
  if (!proj) {
    if (noSel) noSel.classList.remove("hidden");
    rendered.classList.add("hidden");
    if (emptyEl) emptyEl.classList.add("hidden");
    return;
  }

  if (noSel) noSel.classList.add("hidden");
  if (emptyEl) emptyEl.classList.add("hidden");
  rendered.classList.remove("hidden");
  rendered.innerHTML = "";

  // Find the active metric for this project
  const activeMetric = proj.key_metric_name || _guessMetric(proj);

  // --- Research Insights ---
  if (proj.insights && (proj.insights.key_breakthrough || (proj.insights.lessons_learned && proj.insights.lessons_learned.length))) {
    const insightsSection = document.createElement("details");
    insightsSection.className = "research-insights-collapsible";
    insightsSection.open = true;
    const summary = document.createElement("summary");
    summary.className = "insights-summary";
    summary.innerHTML = '<span class="insights-toggle-icon"></span>Research Insights';
    insightsSection.appendChild(summary);

    const body = document.createElement("div");
    body.className = "insights-body";
    if (proj.insights.key_breakthrough) {
      body.innerHTML += `<div class="insight-breakthrough markdown-body"><span class="insight-section-label">Key Breakthrough</span>${window.markedParse(proj.insights.key_breakthrough)}</div>`;
    }
    if (proj.insights.lessons_learned && proj.insights.lessons_learned.length) {
      let lessonsHtml = '<div class="insight-lessons"><span class="insight-section-label">Lessons Learned</span><ul>';
      for (const lesson of proj.insights.lessons_learned) {
        lessonsHtml += `<li>${window.markedParse(lesson)}</li>`;
      }
      lessonsHtml += '</ul></div>';
      body.innerHTML += lessonsHtml;
    }
    if (proj.insights.dead_ends && proj.insights.dead_ends.length) {
      let deHtml = '<div class="insight-lessons"><span class="insight-section-label">Dead Ends</span><ul>';
      for (const de of proj.insights.dead_ends) {
        deHtml += `<li>${window.markedParse(de)}</li>`;
      }
      deHtml += '</ul></div>';
      body.innerHTML += deHtml;
    }
    insightsSection.appendChild(body);
    rendered.appendChild(insightsSection);
  }

  // --- Runs grid ---
  if (proj.runs && proj.runs.length) {
    const section = document.createElement("div");
    section.className = "exp-detail-section";
    const displayRuns = getDisplayRuns(proj.runs);
    const sTitle = document.createElement("h3");
    sTitle.textContent = `Runs (${displayRuns.length})`;
    section.appendChild(sTitle);

    const runsInOrder = displayRuns;
    let currentSort = "newest";

    const sortModes = [
      { key: "newest", label: "Newest" },
      { key: "oldest", label: "Oldest" },
      { key: "best", label: "Best metric" },
      { key: "decision", label: "Best first" },
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
        const order = { best: 0, completed: 1, crash: 2 };
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
            let prevVal = null;
            for (let pi = origIndex - 1; pi >= 0; pi--) {
              const pr = runsInOrder[pi];
              if (pr.results?.[activeMetric] != null && (pr.decision === "best" || prevVal === null)) {
                prevVal = pr.results[activeMetric];
                if (pr.decision === "best") break;
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

        // Sparkline
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

        if (run.description && run.description !== run.hypothesis) {
          const desc = document.createElement("div");
          desc.className = "exp-run-description";
          desc.textContent = run.description;
          card.appendChild(desc);
        }

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
              const prev = prevHP[k] != null ? String(prevHP[k]) : "\u2013";
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
          if (!isNaN(d)) {
            const hoursAgo = (Date.now() - d.getTime()) / 3600000;
            if (hoursAgo < 1) meta.push("just now");
            else if (hoursAgo < 24) meta.push(`${Math.floor(hoursAgo)}h ago`);
            else if (hoursAgo < 48) meta.push("yesterday");
            else meta.push(d.toLocaleDateString());
          }
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

    renderRunCards(sortRuns(currentSort));
    section.appendChild(grid);
    rendered.appendChild(section);
  } else {
    // No runs yet
    if (!proj.insights) {
      if (emptyEl) {
        emptyEl.querySelector("h2").textContent = "No runs yet";
        emptyEl.querySelector("p").textContent = "Launch a session to start collecting results.";
        emptyEl.classList.remove("hidden");
      }
      rendered.classList.add("hidden");
      }
  }
}

// Guess the active metric from project data
function _guessMetric(proj) {
  if (!proj.runs || !proj.runs.length) return "";
  const metricPriority = ["accuracy", "exact_match", "test_accuracy", "val_accuracy", "best_val_acc", "f1", "loss", "val_bpb", "rmse"];
  for (const run of [...proj.runs].reverse()) {
    if (!run.results) continue;
    for (const m of metricPriority) {
      if (m in run.results) return m;
    }
    // Fallback to first numeric result
    for (const [k, v] of Object.entries(run.results)) {
      if (typeof v === "number") return k;
    }
  }
  return "";
}


/* ───── Prompt tab (PROMPT.md) ───── */

let promptRawMd = "";
let setupEditing = false;

async function loadPromptEditor(projectId) {
  loadSetupPrompt(projectId);
}

async function loadSetupPrompt(projectId) {
  const editor = document.getElementById("prompt-editor");
  const rendered = document.getElementById("prompt-rendered");
  if (!editor) return;
  if (!serverPort) {
    if (rendered) rendered.innerHTML = '<div style="color:#888;padding:20px">Waiting for server...</div>';
    if (rendered) rendered.classList.remove("hidden");
    return;
  }

  // Ensure prompt pane is visible (defensive — covers all code paths)
  const promptPane = document.getElementById("setup-prompt-pane");
  const noSel = document.getElementById("setup-no-selection");
  const toolbar = document.getElementById("setup-toolbar");
  if (noSel) noSel.classList.add("hidden");
  if (toolbar) toolbar.classList.remove("hidden");
  if (promptPane) promptPane.classList.remove("hidden");

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
      rendered.innerHTML = window.markedParse(promptRawMd);
      try {
        rendered.querySelectorAll("pre code").forEach((block) => {
          if (window.hljs) window.hljs.highlightElement(block);
        });
      } catch (_) { /* syntax highlight is best-effort */ }
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

function showSetupViewMode() {
  setupEditing = false;
  document.getElementById("setup-edit-btn")?.classList.remove("hidden");
  document.getElementById("setup-save-btn")?.classList.add("hidden");
  document.getElementById("setup-cancel-btn")?.classList.add("hidden");
}

function enterSetupEdit() {
  setupEditing = true;
  const editor = document.getElementById("prompt-editor");
  const rendered = document.getElementById("prompt-rendered");
  if (!editor) return;

  if (rendered) rendered.classList.add("hidden");
  editor.classList.remove("hidden");
  editor.value = promptRawMd;
  editor.focus();

  document.getElementById("setup-edit-btn")?.classList.add("hidden");
  document.getElementById("setup-save-btn")?.classList.remove("hidden");
  document.getElementById("setup-cancel-btn")?.classList.remove("hidden");
}

function cancelSetupEdit() {
  setupEditing = false;
  const editor = document.getElementById("prompt-editor");
  const rendered = document.getElementById("prompt-rendered");
  if (!editor) return;

  editor.classList.add("hidden");
  if (rendered) rendered.classList.remove("hidden");
  showSetupViewMode();
}

document.getElementById("setup-edit-btn")?.addEventListener("click", enterSetupEdit);
document.getElementById("setup-cancel-btn")?.addEventListener("click", cancelSetupEdit);

document.getElementById("setup-save-btn")?.addEventListener("click", async () => {
  const editor = document.getElementById("prompt-editor");
  const rendered = document.getElementById("prompt-rendered");
  const status = document.getElementById("setup-save-status");
  if (!editor || !currentProjectId) return;

  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(currentProjectId)}/prompt`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: editor.value }),
    });
    const data = await r.json();
    if (data.ok) {
      promptRawMd = editor.value;
      cancelSetupEdit();
      if (rendered) {
        rendered.innerHTML = window.markedParse(promptRawMd);
        rendered.querySelectorAll("pre code").forEach((block) => {
          if (window.hljs) hljs.highlightElement(block);
        });
      }
    }
    if (status) {
      status.textContent = data.ok ? "Saved \u2014 agent notified automatically" : "Failed";
      setTimeout(() => status.textContent = "", 4000);
    }
  } catch {
    if (status) { status.textContent = "Error"; setTimeout(() => status.textContent = "", 2000); }
  }
});

/* ───── Experiment notifications ───── */

let _emailPromptShown = false;

function showEmailPrompt() {
  if (_emailPromptShown || localStorage.getItem("distillate-email-asked") || localStorage.getItem("distillate-email")) return;
  _emailPromptShown = true;

  const toast = document.createElement("div");
  toast.className = "email-prompt-toast";
  toast.innerHTML = `
    <div class="email-prompt-text">Sync across devices and get experiment reports by email?</div>
    <div class="email-prompt-form">
      <input type="email" id="email-prompt-input" placeholder="your@email.com" spellcheck="false">
      <button class="onboarding-btn" id="email-prompt-submit">Enable</button>
      <button class="email-prompt-dismiss" id="email-prompt-dismiss">No thanks</button>
    </div>`;

  document.getElementById("chat-area")?.appendChild(toast);
  toast.scrollIntoView({ behavior: "smooth" });

  document.getElementById("email-prompt-submit")?.addEventListener("click", async () => {
    const email = document.getElementById("email-prompt-input")?.value?.trim();
    if (!email || !email.includes("@")) return;
    try {
      await fetch(`http://127.0.0.1:${serverPort}/email/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, daily_papers: true, weekly_digest: true, experiment_reports: true }),
      });
      toast.innerHTML = `<div class="email-prompt-text" style="color:var(--green);">You're in! Daily suggestions + weekly digests coming to ${email}</div>`;
      localStorage.setItem("distillate-email-asked", "1");
      setTimeout(() => toast.remove(), 4000);
    } catch {
      toast.innerHTML = `<div class="email-prompt-text" style="color:var(--error);">Failed to register. Try again later.</div>`;
    }
  });

  document.getElementById("email-prompt-dismiss")?.addEventListener("click", () => {
    localStorage.setItem("distillate-email-asked", "1");
    toast.remove();
  });
}

function notifyExperimentEvent(data) {
  if (data.type === "run_completed" || data.$schema === "distillate/run/v1") {
    // Email setup is in Control Panel → Updates connector (no longer prompted in chat)
    const status = data.status || "";

    // Activity bar notification badge when sidebar is collapsed
    if (sidebarLeft?.classList.contains("collapsed") &&
        (status === "best" || status === "crash")) {
      const expBtn = document.querySelector('.activity-btn[data-pane="sidebar-left"]');
      if (expBtn) expBtn.classList.add("has-notification");
    }

    // Crashes always get OS notification (even when focused)
    if (status === "crash" && window.nicolas?.notify) {
      window.nicolas.notify(
        "Experiment crashed",
        data.reasoning || data.hypothesis || "Check logs"
      );
      return;
    }

    if (!window.nicolas?.notify || document.hasFocus()) return;

    if (status === "best" && data.results) {
      const metric = Object.entries(data.results)[0];
      if (metric) {
        window.nicolas.notify(
          "New best",
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
    }
  }
}
