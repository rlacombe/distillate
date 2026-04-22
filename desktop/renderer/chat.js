/* ───── Chat — messaging, input handling, search, settings ───── */

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

/* ───── Event handling ───── */

function handleEvent(event) {
  // Log key events for debugging button state issues
  if (['text_delta', 'tool_start', 'tool_done', 'turn_end', 'error'].includes(event.type)) {
    console.log(`[UI] Event: ${event.type}${event.name ? ` (${event.name})` : ''} | isStreaming=${isStreaming} | currentAssistantEl=${!!currentAssistantEl}`);
  }

  // Ignore events from a cancelled turn (except turn_end which resets state)
  if (_cancelledTurn && event.type !== "turn_end") return;
  if (_cancelledTurn && event.type === "turn_end") { _cancelledTurn = false; return; }

  switch (event.type) {
    case "text_delta":
      _turnOutputChars += (event.text || "").length;
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
        currentAssistantBodyEl = null;
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
      _turnOutputChars = 0;
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
      // Bell on Nicolas activity button when user is on a different sidebar view
      if (typeof _activeSidebarView !== "undefined" && _activeSidebarView !== "nicolas") {
        const nicolasBtn = document.querySelector('.activity-btn[data-sidebar-view="nicolas"]');
        if (nicolasBtn) nicolasBtn.classList.add("has-notification");
        nicolasWaiting = true;  // feed into tray icon via status poll
      }
      // Update active session status to "waiting" and send notification
      if (_nicolasActiveSessionId && typeof serverPort !== "undefined" && serverPort) {
        try {
          fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions/${encodeURIComponent(_nicolasActiveSessionId)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: "waiting" }),
          }).catch(() => {});
        } catch {}
        // Send native notification
        if (window.nicolas && window.nicolas.notify) {
          window.nicolas.notify("Nicolas", "Waiting for your input");
        }
      }
      break;

    case "session_init":
      // New Agent SDK session started — refresh the sessions sidebar so
      // the new entry appears.
      if (typeof refreshNicolasSessions === "function") refreshNicolasSessions();
      break;

    case "session_renamed":
      // Server-side auto-namer (Haiku) gave this thread a proper title.
      // Refresh the sidebar so the rename is visible immediately.
      if (typeof refreshNicolasSessions === "function") refreshNicolasSessions();
      break;

    case "thread_branched":
      // launch_experiment fired — the active thread is now cleared and
      // the next user message will open a fresh thread pre-named after
      // the experiment. Clear the chat surface and tell the user.
      if (typeof messagesEl !== "undefined" && messagesEl) {
        messagesEl.innerHTML = "";
      }
      if (typeof currentAssistantEl !== "undefined") currentAssistantEl = null;
      currentAssistantBodyEl = null;
      if (typeof currentText !== "undefined") currentText = "";
      if (typeof refreshNicolasSessions === "function") refreshNicolasSessions();
      addSystemMessage(
        `Opened a new thread for "${event.suggested_name || 'the experiment'}". ` +
        `Your next message starts it fresh — the setup conversation stays in the previous thread.`
      );
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

    case "budget_warning":
      showToast(
        `Session at $${event.session_cost_usd?.toFixed(2) || "?"} — heads up.`,
        "warn",
      );
      break;

    case "day_budget_warning":
      showToast(
        `Today's spend: $${event.today_cost_usd?.toFixed(2) || "?"}. ` +
        `Consider switching to Haiku or starting fresh conversations.`,
        "warn",
      );
      break;

    case "context_warning": {
      // Auto-compact suggestion. Rendered as a dismissable banner. The
      // message is ALREADY sent to Claude (context_warning is non-blocking)
      // so we must NOT call finishStreaming() here — the response will stream
      // in normally. Only remove the thinking indicator so the banner is
      // visible, but leave streaming state intact so the input stays locked.
      const onSubscription = event.billing_source === "subscription";
      const costStr = _fmtUsd(event.session_cost_usd);
      const title = onSubscription
        ? `This thread is getting long (≈${costStr} on your plan so far).`
        : `This conversation is getting heavy (${costStr}).`;
      const body = onSubscription
        ? "You're on your Claude Code subscription, so this counts against your plan rather than being billed per-token. A fresh thread re-warms the cache and frees context — still worth doing."
        : "Starting a fresh thread re-warms the prompt cache and cuts the per-turn cost. Your current thread stays in the sidebar.";
      removeThinkingIndicator();
      addBudgetBanner({
        kind: "context",
        title,
        body,
        primary: { label: "Start fresh conversation", action: "new_conversation" },
        secondary: { label: "Keep going", action: "dismiss" },
      });
      break;
    }

    case "budget_blocked":
      // Hard cap — send() bailed WITHOUT calling Claude. Surface a banner
      // and require an explicit user choice. "Keep going" unblocks the cap
      // AND resends the stored last message so the user doesn't have to
      // retype it. "Start fresh" discards it and opens a new thread.
      removeThinkingIndicator();
      finishStreaming();
      addBudgetBanner({
        kind: "blocked",
        title:
          event.reason === "day"
            ? `Daily spend cap hit: $${event.today_cost_usd?.toFixed(2)} / $${event.threshold_usd?.toFixed(2)}.`
            : `Session cap hit: $${event.session_cost_usd?.toFixed(2)} / $${event.threshold_usd?.toFixed(2)}.`,
        body: "Nicolas paused before the next turn. Continue or start fresh.",
        primary: { label: "Start fresh conversation", action: "new_conversation" },
        secondary: { label: "Keep going", action: "keep_going_blocked" },
      });
      break;

    case "budget_unblocked":
      showToast("Budget override enabled for this session.", "info");
      break;

    case "usage_update":
      // Track the session-level output token total so the thinking indicator
      // can show the real per-turn count (delta from turn start).
      _lastSessionOutputTokens = (event.session || {}).output_tokens || _lastSessionOutputTokens;
      break;
  }
}

function _fmtUsd(v) {
  const n = Number.isFinite(v) ? v : 0;
  return "$" + n.toFixed(2);
}

function addBudgetBanner({ kind, title, body, primary, secondary }) {
  const container = document.getElementById("budget-banner-container");
  if (!container) return;

  container.innerHTML = "";
  const el = document.createElement("div");
  el.className = `budget-banner budget-${kind}`;
  const iconSvg = kind === "blocked"
    ? `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 1.5 14.5 13H1.5L8 1.5Z"/><path d="M8 6.5v3"/><circle cx="8" cy="11.5" r="0.5" fill="currentColor"/></svg>`
    : `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6.5"/><path d="M8 7.25v4"/><circle cx="8" cy="5" r="0.5" fill="currentColor"/></svg>`;
  el.innerHTML = `
    <div class="budget-banner-inner">
      <div class="budget-banner-icon" aria-hidden="true">${iconSvg}</div>
      <div class="budget-banner-content">
        <div class="budget-banner-title">${title}</div>
        <div class="budget-banner-body">${body}</div>
      </div>
      <div class="budget-banner-actions">
        <button class="budget-btn secondary" data-action="${secondary.action}">${secondary.label}</button>
        <button class="budget-btn primary" data-action="${primary.action}">${primary.label}</button>
      </div>
    </div>
  `;
  el.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "new_conversation") {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "new_conversation" }));
      }
    } else if (action === "unblock_budget") {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "unblock_budget" }));
      }
    } else if (action === "keep_going_blocked") {
      // Unblock the hard cap AND resend the last user message — it was
      // never sent to Claude (blocking event), so the user shouldn't
      // have to retype it. Message is already in DOM; just re-enter
      // streaming state and send the WS payload.
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "unblock_budget" }));
        if (lastUserMessage) {
          isStreaming = true;
          setStreamingUI(true);
          showThinkingIndicator();
          ws.send(JSON.stringify({ text: lastUserMessage }));
        }
      }
    } else if (action === "dismiss") {
      el.remove();
    }
    if (action !== "dismiss") el.remove();
  });
  container.appendChild(el);
}

/* ───── Message rendering ───── */

// Cap on live-streamed message DOM nodes. loadSessionHistory replaces the
// container wholesale when switching sessions, so this only governs a single
// long in-progress conversation. 500 is enough to keep plenty of scroll-up
// context without letting the renderer hold every turn ever streamed.
const MAX_LIVE_MESSAGES = 500;
function _capMessagesEl() {
  if (!messagesEl) return;
  while (messagesEl.children.length > MAX_LIVE_MESSAGES) {
    messagesEl.firstElementChild?.remove();
  }
}

function addUserMessage(text) {
  const el = document.createElement("div");
  el.className = "message user";
  el.textContent = text;
  messagesEl.appendChild(el);
  _capMessagesEl();
  scrollToBottom(true);
}

function addSystemMessage(text) {
  // Ephemeral neutral note — used for thread_branched and similar
  // server-initiated transitions. Not a real chat turn.
  const el = document.createElement("div");
  el.className = "message system";
  el.textContent = text;
  messagesEl.appendChild(el);
  _capMessagesEl();
  scrollToBottom(true);
}

function startAssistantMessage() {
  console.log("[UI] startAssistantMessage() - starting new assistant block");
  const wrap = document.createElement("div");
  wrap.className = "message assistant markdown-body streaming-cursor";
  const persona = _buildPersonaByline();
  if (persona) wrap.appendChild(persona);
  const body = document.createElement("div");
  body.className = "message-body";
  wrap.appendChild(body);
  messagesEl.appendChild(wrap);
  currentAssistantEl = wrap;
  currentAssistantBodyEl = body;
  _capMessagesEl();
  currentText = "";
  isStreaming = true;
  console.log("[UI] startAssistantMessage: set isStreaming=true");
  setStreamingUI(true);
  // Hide suggestions while streaming
  const cs = document.getElementById("chat-suggestions");
  if (cs) cs.classList.add("hidden");
}

let currentAssistantBodyEl = null;

function _buildPersonaByline() {
  const row = document.createElement("div");
  row.className = "message-persona";
  // Live dot — green pulse marks the active speaker (mirror of the topbar
  // live pill). Pure decoration, no runtime state.
  const dot = document.createElement("span");
  dot.className = "message-persona-dot";
  row.appendChild(dot);
  const name = document.createElement("span");
  name.className = "message-persona-name";
  name.textContent = "Nicolas";
  const sep1 = document.createElement("span");
  sep1.className = "message-persona-sep";
  sep1.textContent = "\u00B7";
  const role = document.createElement("span");
  role.className = "message-persona-role";
  role.textContent = "the Alchemist";
  row.append(name, sep1, role);
  // Model badge — sourced from the billing <select> which billing.js keeps in sync.
  try {
    const sel = document.getElementById("model-pill");
    const label = sel && sel.options[sel.selectedIndex]
      ? sel.options[sel.selectedIndex].textContent
      : "";
    if (label) {
      const badge = document.createElement("span");
      badge.className = "message-persona-model";
      badge.textContent = label;
      row.appendChild(badge);
    }
  } catch {}
  return row;
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
  const target = currentAssistantBodyEl || currentAssistantEl;
  if (typeof marked !== "undefined") {
    target.innerHTML = window.markedParse(currentText);
    // Turn [N] paper references into clickable links
    target.innerHTML = target.innerHTML.replace(
      /\[(\d{1,4})\]/g,
      '<a href="#" class="paper-ref" data-index="$1">[$1]</a>'
    );
  } else {
    target.textContent = currentText;
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
  const caller = new Error().stack.split('\n')[2]?.trim() || 'unknown';
  console.log(`[UI] finishStreaming() called from ${caller}`);
  // Flush any pending throttled render so final content is complete
  if (_renderRAF) { cancelAnimationFrame(_renderRAF); _renderRAF = null; }
  if (_scrollRAF) { cancelAnimationFrame(_scrollRAF); _scrollRAF = null; }
  if (currentAssistantEl) {
    currentAssistantEl.classList.remove("streaming-cursor");
    renderAssistantMessage();
  }
  currentAssistantEl = null;
  currentAssistantBodyEl = null;
  currentText = "";
  isStreaming = false;
  console.log("[UI] finishStreaming: set isStreaming=false, disabled buttons");
  inputEl.disabled = false;
  sendBtn.disabled = false;
  setStreamingUI(false);
  // Only auto-focus chat if the terminal isn't active
  const sessionView = document.getElementById("session-view");
  if (!sessionView || sessionView.classList.contains("hidden")) {
    inputEl.focus();
  }
  // Restore suggestions after streaming (only if not yet dismissed)
  const cs = document.getElementById("chat-suggestions");
  if (cs && !cs.dataset.dismissed) {
    cs.classList.remove("hidden");
    refreshChatSuggestions();
  }

  // Send queued message if user typed while agent was working
  if (_queuedMessage) {
    const queued = _queuedMessage;
    _queuedMessage = null;
    // Upgrade the dimmed queued message to normal style
    const qEl = messagesEl.querySelector(".message.queued");
    if (qEl) qEl.classList.remove("queued");
    // Send it
    lastUserMessage = queued;
    console.log("[UI] Sending queued message, entering streaming mode");
    isStreaming = true;
    setStreamingUI(true);
    showThinkingIndicator();
    ws.send(JSON.stringify({ text: queued }));
  }
}

// Build (but don't append) a tool-indicator DOM node. The live chat path
// (addToolIndicator) appends to #messages; the history-replay path in
// core.js composes it into a DocumentFragment instead. Both share the
// same subtitle-derivation logic — keep them in one place.
function buildToolIndicatorEl(name, done, input, serverLabel) {
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
    } else if (name === "compare_experiments" && input.projects) {
      subtitle = input.projects.join(" vs ");
    } else if (name === "compare_runs") {
      const parts = [];
      if (input.run_a) parts.push(input.run_a);
      if (input.run_b) parts.push(input.run_b);
      subtitle = parts.join(" vs ");
    } else if (name === "list_experiments") {
      subtitle = "all experiments";
    } else if (name === "get_experiment_details" && input.identifier) {
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

  return el;
}

function addToolIndicator(name, done, input, serverLabel) {
  // Group consecutive calls of the same tool into a single indicator
  // with an ×N count pill. Prevents walls of "Reading file" or
  // "Running command" rows when Nicolas is doing many small steps.
  const prev = messagesEl.lastElementChild;
  if (
    prev &&
    prev.classList.contains("tool-indicator") &&
    prev.classList.contains("done") &&
    prev.dataset.toolName === name
  ) {
    const count = parseInt(prev.dataset.groupCount || "1", 10) + 1;
    prev.dataset.groupCount = String(count);
    // Promote to grouped state: replace per-call subtitle with an ×N pill.
    let countEl = prev.querySelector(".tool-count");
    if (!countEl) {
      countEl = document.createElement("span");
      countEl.className = "tool-count";
      prev.appendChild(countEl);
      const sub = prev.querySelector(".tool-subtitle");
      if (sub) sub.remove();
    }
    countEl.textContent = `\u00D7${count}`;
    // A new call is running inside the group: spin until markToolDone.
    prev.classList.remove("done");
    if (!prev.querySelector(".spinner")) {
      const sp = document.createElement("div");
      sp.className = "spinner";
      prev.insertBefore(sp, prev.firstChild);
    }
    return;
  }
  const el = buildToolIndicatorEl(name, done, input, serverLabel);
  messagesEl.appendChild(el);
  _capMessagesEl();
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
  _capMessagesEl();
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
  if (e.key === "Enter" && !e.shiftKey && !e.altKey) {
    e.preventDefault();
    sendMessage();
    return;
  }

  // Numeric shortcuts 1-9: click the Nth suggestion pill when composer is
  // empty — lets keyboard users fire suggestions without leaving the input.
  if (!e.ctrlKey && !e.metaKey && !e.altKey && /^[1-9]$/.test(e.key) && inputEl.value === "") {
    const cs = document.getElementById("chat-suggestions");
    if (cs && !cs.classList.contains("hidden")) {
      const pills = cs.querySelectorAll(".suggestion");
      const idx = parseInt(e.key, 10) - 1;
      if (pills[idx]) {
        e.preventDefault();
        pills[idx].click();
        return;
      }
    }
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
  const caller = new Error().stack.split('\n')[2]?.trim() || 'unknown';
  console.log(`[UI] setStreamingUI(${streaming}) from ${caller}`);
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
  console.log(`[UI] button state: disabled=${sendBtn.disabled}, streaming=${sendBtn.classList.contains('streaming')}, isStreaming=${isStreaming}`);
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
  scrollToBottom(true);

  // Re-enable input. Don't pre-fill — it caused the user message to render
  // twice when re-sent. Up-arrow recalls history if they want to retry.
  inputEl.disabled = false;
  sendBtn.disabled = false;
  const sv = document.getElementById("session-view");
  if (!sv || sv.classList.contains("hidden")) inputEl.focus();
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
  el.innerHTML = `<div class="thinking-spinner"></div><span>${phrase}</span><span class="thinking-stats"></span>`;
  messagesEl.appendChild(el);
  scrollToBottom(true);

  _thinkingStart = Date.now();
  function _updateThinkingStats() {
    const statsEl = document.querySelector("#thinking-indicator .thinking-stats");
    if (!statsEl) return;
    const secs = Math.floor((Date.now() - _thinkingStart) / 1000);
    // Prefer real token delta from usage_update (includes delegate sub-calls);
    // fall back to char-based estimate before the first usage_update arrives.
    const realDelta = _lastSessionOutputTokens - _turnStartSessionOutputTokens;
    const toks = realDelta > 0 ? realDelta : Math.round(_turnOutputChars / 4);
    statsEl.textContent = toks > 0 ? `${secs}s · ${toks} tok` : `${secs}s`;
  }
  _updateThinkingStats();
  _thinkingTimer = setInterval(_updateThinkingStats, 500);
}

function removeThinkingIndicator() {
  clearInterval(_thinkingTimer);
  _thinkingTimer = null;
  const el = document.getElementById("thinking-indicator");
  if (el) el.remove();
}

let _inputSafetyTimer = null;
let _queuedMessage = null;

// Elapsed-time + token counter for the thinking indicator
let _thinkingTimer = null;
let _thinkingStart = 0;
let _turnOutputChars = 0; // accumulated text_delta chars this turn (~4 chars/tok)
// Real session output-token total from usage_update events (includes delegate calls).
// _turnStartSessionOutputTokens is snapshotted at send time so the delta = this turn only.
let _lastSessionOutputTokens = 0;
let _turnStartSessionOutputTokens = 0;

function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  // If agent is still working, queue the message instead of blocking
  if (isStreaming || inputEl.disabled) {
    console.log(`[UI] sendMessage: queueing (isStreaming=${isStreaming}, inputDisabled=${inputEl.disabled})`);
    _queuedMessage = text;
    // Show queued message in dimmed style
    const qEl = document.createElement("div");
    qEl.className = "message user queued";
    qEl.textContent = text;
    messagesEl.appendChild(qEl);
    _capMessagesEl();
    scrollToBottom(true);
    inputEl.value = "";
    inputEl.style.height = "auto";
    return;
  }

  _cancelledTurn = false;
  nicolasWaiting = false;
  lastUserMessage = text;

  // Clear session waiting status when user sends input
  if (_nicolasActiveSessionId && typeof serverPort !== "undefined" && serverPort) {
    try {
      fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions/${encodeURIComponent(_nicolasActiveSessionId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "idle" }),
      }).catch(() => {});
    } catch {}
  }

  // Permanently dismiss suggestions after the first user turn
  const _cs = document.getElementById("chat-suggestions");
  if (_cs && !_cs.dataset.dismissed) {
    _cs.dataset.dismissed = "1";
    _cs.classList.add("hidden");
  }
  _messageHistory.unshift(text);
  if (_messageHistory.length > 50) _messageHistory.pop();
  _historyIndex = -1;
  _historyDraft = "";

  // Immediately enter streaming mode before any async work
  console.log("[UI] sendMessage: entering streaming mode");
  isStreaming = true;
  setStreamingUI(true);
  // Snapshot session output tokens so per-turn delta is accurate.
  _turnStartSessionOutputTokens = _lastSessionOutputTokens;

  addUserMessage(text);
  showThinkingIndicator();
  // Build context payload: active project (auto) + explicitly-pinned focus items.
  const payload = { text };
  if (typeof currentProjectId !== "undefined" && currentProjectId) {
    payload.context = { active_project_id: currentProjectId };
  }
  const focusItems = (window._composerContextItems || []).slice();
  if (focusItems.length) {
    payload.context = payload.context || {};
    payload.context.focus = focusItems;
  }
  ws.send(JSON.stringify(payload));

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

/* ───── Settings sidebar ───── */

const settingsStatus = document.getElementById("settings-status");
const settingAuthToken = document.getElementById("setting-auth-token");
const settingPrivateRepos = document.getElementById("setting-private-repos");

function _refreshHfSettingsPanel() {}

// Populate the Account section profile card with current user data.
async function _refreshSettingsAccountSection() {
  const profileCard = document.getElementById("settings-account-profile");
  const signoutBtn = document.getElementById("settings-signout-btn");
  if (!profileCard) return;

  let user = null;
  if (serverPort) {
    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/auth/status`);
      const d = await r.json();
      user = d.signed_in ? d.user : null;
    } catch {}
  }

  const cloudTokenGroup = document.getElementById("settings-cloud-token-group");

  if (user) {
    const signinPrompt = document.getElementById("settings-signin-prompt");
    if (signinPrompt) signinPrompt.classList.add("hidden");
    const initials = (typeof _getInitials === "function") ? _getInitials(user) : "?";
    const color = (typeof _avatarColor === "function") ? _avatarColor(user) : "#888";
    const name = escapeHtml(user.display_name || user.hf_username || "");
    const username = user.hf_username ? `@${escapeHtml(user.hf_username)}` : "";
    const email = escapeHtml(user.email || "");
    const avatarHtml = user.avatar_url
      ? `<img class="account-avatar account-avatar-xl account-avatar-img" src="${escapeHtml(user.avatar_url)}" alt="${name}" id="settings-profile-avatar-img">`
      : `<span class="account-avatar account-avatar-xl" style="background:${color}">${escapeHtml(initials)}</span>`;

    profileCard.innerHTML = `
      ${avatarHtml}
      <div class="settings-profile-name">${name}</div>
      ${email ? `<div class="settings-profile-email">${email}</div>` : (username ? `<div class="settings-profile-username">${username}</div>` : "")}
      <div class="settings-profile-provider">🤗 Connected via Hugging Face</div>
      <div class="settings-profile-stats" id="settings-profile-stats"></div>`;

    profileCard.classList.remove("hidden");
    if (signoutBtn) signoutBtn.classList.remove("hidden");
    // Cloud Token redundant once OAuth session exists
    if (cloudTokenGroup) cloudTokenGroup.classList.add("hidden");

    // Avatar image fallback
    const img = document.getElementById("settings-profile-avatar-img");
    if (img) {
      img.onerror = () => {
        img.replaceWith(Object.assign(document.createElement("span"), {
          className: "account-avatar account-avatar-xl",
          style: `background:${color}`,
          textContent: initials,
        }));
      };
    }

    // Populate token usage stat for the current calendar month
    const statsEl = document.getElementById("settings-profile-stats");
    if (statsEl && serverPort) {
      try {
        const r = await fetch(`http://127.0.0.1:${serverPort}/account/usage`);
        const d = await r.json();
        if (d.ok) {
          // Exclude cache_read tokens — they're cheap context re-reads (0.1× price)
          // that inflate the count 10-100× without representing actual work done.
          // Count: new input + cache-written (full-price, one-time) + output.
          const total = (d.tokens_input ?? 0) + (d.tokens_cache_creation ?? 0) + (d.tokens_output ?? 0);
          const fmt = n => n >= 1_000_000 ? (n / 1_000_000).toFixed(1) + "M"
                         : n >= 1_000     ? Math.round(n / 1_000) + "K"
                         : String(n);
          const cost = d.cost_usd ?? 0;
          const costStr = cost > 0 ? ` · $${cost.toFixed(2)}` : "";
          statsEl.innerHTML = `
            <div class="settings-profile-stat" style="cursor:pointer" onclick="_settingsSwitchSection('usage');_loadAndRenderStats()" title="View usage breakdown">
              <div class="settings-profile-stat-value">${fmt(total)}</div>
              <div class="settings-profile-stat-label">Tokens this month${costStr}</div>
            </div>
            <div class="settings-profile-stat-link" onclick="_settingsSwitchSection('usage');_loadAndRenderStats()">View usage</div>`;
        }
      } catch {}
    }
  } else {
    profileCard.classList.add("hidden");
    if (signoutBtn) signoutBtn.classList.add("hidden");
    const signinPrompt = document.getElementById("settings-signin-prompt");
    if (signinPrompt) signinPrompt.classList.remove("hidden");
    // Signed-out users may rely on the legacy cloud token
    if (cloudTokenGroup) cloudTokenGroup.classList.remove("hidden");
  }
}

// Wire HF sign-in button in settings sidebar.
document.getElementById("settings-hf-signin-btn")?.addEventListener("click", async () => {
  const btn = document.getElementById("settings-hf-signin-btn");
  if (btn) { btn.disabled = true; btn.textContent = "Opening browser\u2026"; }
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/auth/signin-hf-start`, { method: "POST" });
    const d = await r.json();
    if (d.authorize_url && window.nicolas?.openExternal) window.nicolas.openExternal(d.authorize_url);
  } catch {}
  if (btn) { btn.disabled = false; btn.textContent = "🤗 Sign in with Hugging Face"; }
});

function openSettings(section = "account") {
  const overlay = document.getElementById("settings-overlay");
  if (!overlay) return;
  overlay.hidden = false;
  _settingsSwitchSection(section);
  if (section === "account") _refreshSettingsAccountSection();
  if (section === "integrations" && typeof fetchIntegrations === "function") fetchIntegrations();
  if (section === "usage" && typeof _loadAndRenderStats === "function") _loadAndRenderStats();
  document.getElementById("account-btn")?.classList.add("active");
  // Load saved preferences into controls on every open
  if (window.nicolas?.getSettings) {
    window.nicolas.getSettings().then((s) => {
      const authEl = document.getElementById("setting-auth-token");
      if (authEl) authEl.value = s.authToken || "";
      const privEl = document.getElementById("setting-private-repos");
      if (privEl) privEl.checked = !!s.privateRepos;
    }).catch(() => {});
  }
}

const openPreferences = openSettings;

function _settingsSwitchSection(section) {
  document.querySelectorAll(".settings-ov-section").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".settings-ov-nav-item").forEach(el =>
    el.classList.toggle("active", el.dataset.section === section));
  const target = document.getElementById(`settings-section-${section}`);
  if (target) target.classList.add("active");
}

function closeSettings() {
  const overlay = document.getElementById("settings-overlay");
  if (overlay) overlay.hidden = true;
  document.getElementById("account-btn")?.classList.remove("active");
  inputEl?.focus();
}

// Wire overlay nav items and ESC key (runs once on DOM ready)
(function _wireSettingsOverlay() {
  function _wire() {
    document.querySelectorAll(".settings-ov-nav-item").forEach(btn => {
      btn.addEventListener("click", () => {
        const section = btn.dataset.section;
        _settingsSwitchSection(section);
        if (section === "account") _refreshSettingsAccountSection();
        if (section === "integrations" && typeof fetchIntegrations === "function") fetchIntegrations();
        if (section === "usage" && typeof _loadAndRenderStats === "function") _loadAndRenderStats();
      });
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        const overlay = document.getElementById("settings-overlay");
        if (overlay && !overlay.hidden) { e.stopPropagation(); closeSettings(); }
      }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", _wire);
  else _wire();
}());

// Save button — saves auth token (private repos auto-saves on toggle change below).
document.getElementById("settings-save")?.addEventListener("click", () => {
  if (window.nicolas && window.nicolas.saveSettings) {
    window.nicolas.saveSettings({
      authToken: settingAuthToken ? settingAuthToken.value.trim() : "",
      privateRepos: settingPrivateRepos ? settingPrivateRepos.checked : false,
    }).then(() => {
      if (settingsStatus) {
        settingsStatus.textContent = "Saved";
        settingsStatus.className = "settings-status success";
        setTimeout(() => { if (settingsStatus) settingsStatus.textContent = ""; }, 2000);
      }
    }).catch((err) => {
      if (settingsStatus) {
        settingsStatus.textContent = `Failed: ${err.message || "unknown error"}`;
        settingsStatus.className = "settings-status";
      }
    });
  }
});

// Private repos toggle: auto-save on change.
settingPrivateRepos?.addEventListener("change", () => {
  if (window.nicolas && window.nicolas.saveSettings) {
    window.nicolas.saveSettings({
      authToken: settingAuthToken ? settingAuthToken.value.trim() : "",
      privateRepos: settingPrivateRepos.checked,
    });
  }
});

// Sign out from settings Account section.
document.getElementById("settings-signout-btn")?.addEventListener("click", async () => {
  if (typeof _doSignOut === "function") await _doSignOut();
  closeSettings();
});


// About links.
document.getElementById("settings-changelog-link")?.addEventListener("click", (e) => {
  e.preventDefault();
  if (window.nicolas?.openExternal) window.nicolas.openExternal("https://github.com/rlacombe/distillate/releases");
});
document.getElementById("settings-docs-link")?.addEventListener("click", (e) => {
  e.preventDefault();
  if (window.nicolas?.openExternal) window.nicolas.openExternal("https://distillate.dev");
});
document.getElementById("settings-feedback-link")?.addEventListener("click", (e) => {
  e.preventDefault();
  if (window.nicolas?.openExternal) window.nicolas.openExternal("https://github.com/rlacombe/distillate/issues");
});

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
        if (settingsStatus) {
          settingsStatus.textContent = `Exported to ${result.path}`;
          settingsStatus.className = "settings-status success";
        }
      } else if (result.reason !== "canceled") {
        if (settingsStatus) {
          settingsStatus.textContent = `Export failed: ${result.reason}`;
          settingsStatus.className = "settings-status";
        }
      }
    } catch (err) {
      if (settingsStatus) {
        settingsStatus.textContent = `Export failed: ${err.message}`;
        settingsStatus.className = "settings-status";
      }
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
        if (settingsStatus) {
          settingsStatus.textContent = `Imported ${result.papers} papers. Refreshing...`;
          settingsStatus.className = "settings-status success";
        }
        refreshTabData();
      } else if (result.reason !== "canceled") {
        if (settingsStatus) {
          settingsStatus.textContent = `Import failed: ${result.reason}`;
          settingsStatus.className = "settings-status";
        }
      }
    } catch (err) {
      if (settingsStatus) {
        settingsStatus.textContent = `Import failed: ${err.message}`;
        settingsStatus.className = "settings-status";
      }
    }
    importBtn.textContent = "Import State";
    importBtn.disabled = false;
  });
}

// (Settings ESC is handled inside _wireSettingsOverlay above.)

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

// Chat message search is available via the search icon in the chat header.

// Redirect stray keystrokes to the chat input when focus drifts to the
// scrollable message area (e.g. after user scrolls up to read history and
// the chat-area div picks up scroll-focus).
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key.length !== 1) return; // skip arrows, Enter, Backspace, F-keys, etc.
  const active = document.activeElement;
  const tag = active?.tagName;
  // Already in an input-like element — don't interfere
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
      active?.contentEditable === "true") return;
  // Don't redirect when an overlay or search bar is open
  if (!(document.getElementById("settings-overlay")?.hidden)) return;
  if (searchBar && !searchBar.classList.contains("hidden")) return;
  if (inputEl.disabled) return;
  // Refocus the chat input — the browser will deliver the character to it
  inputEl.focus();
});
// Cmd+F is now used for resource search (agents, experiments, projects) in layout.js.
