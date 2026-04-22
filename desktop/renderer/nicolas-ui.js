/**
 * Nicolas sessions sidebar — multi-session picker.
 *
 * Lists past Nicolas conversations (from GET /nicolas/sessions). Clicking
 * an entry activates it server-side and replays the history into the
 * main-window chat (#welcome / #messages). The main-window chat is owned
 * by welcome.js + core.js + chat.js; this module just drives the picker.
 */

const nicolasSessionsListEl = document.getElementById("nicolas-sessions-list");

let _nicolasSessions = [];
let _nicolasActiveSessionId = null;
// Tracks which session's history is currently rendered in #messages.
// Distinct from _nicolasActiveSessionId: the active session can change
// (session switch) while the DOM still shows the old one, and vice versa
// (banner injected into #messages before any history loads).
// Rule: only skip loadSessionHistory when _displayedSessionId === the
// requested session AND we're not streaming — any other state is stale.
let _displayedSessionId = null;

async function fetchNicolasSessions() {
  if (typeof serverPort === "undefined" || !serverPort) return;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions`);
    if (!resp.ok) return;
    const data = await resp.json();
    _nicolasSessions = data.sessions || [];
    _nicolasActiveSessionId = data.active_session_id || null;
    renderNicolasSessions();
  } catch (err) {
    console.error("Failed to fetch Nicolas sessions:", err);
  }
}

// Public alias for chat.js to call after session_init / turn_end.
function refreshNicolasSessions() {
  fetchNicolasSessions();
}

function renderNicolasSessions() {
  if (!nicolasSessionsListEl) return;

  const _DEFAULT_NAMES = new Set(["Thread", "New conversation", "Conversation", ""]);
  const _TRIVIAL_NAMES = new Set(["hi"]);
  const _FIVE_MIN_MS = 5 * 60 * 1000;
  const visibleSessions = _nicolasSessions.filter((s) => {
    if (s.session_id === _nicolasActiveSessionId) return true;
    const name = (s.name || "").trim();
    const isDefaultName = _DEFAULT_NAMES.has(name);
    const isTrivialName = _TRIVIAL_NAMES.has(name);
    // turn_count missing on old entries → treat as non-trivial (??  1 means keep)
    const isTrivial = s.turn_count !== undefined && s.turn_count <= 1;
    const isStale = s.last_activity &&
      (Date.now() - new Date(s.last_activity).getTime()) > _FIVE_MIN_MS;
    // Hide trivial names (like "hi") if they have only 1 turn; hide default names if trivial and stale
    if (isTrivialName && isTrivial) return false;
    return !(isDefaultName && isTrivial && isStale);
  });

  const countEl = document.getElementById("nicolas-sessions-count");
  if (countEl) countEl.textContent = visibleSessions.length ? String(visibleSessions.length) : "";

  if (!visibleSessions.length) {
    nicolasSessionsListEl.innerHTML = `
      <div class="sidebar-empty">
        <p>No threads yet.</p>
        <p class="sidebar-empty-hint">Type a message to start one.</p>
      </div>`;
    return;
  }

  nicolasSessionsListEl.innerHTML = visibleSessions.map((s) => {
    const isActive = s.session_id === _nicolasActiveSessionId;
    const relTime = _relativeTime(s.last_activity);
    const status = s.status || "idle";
    const statusClass = status === "waiting" ? " waiting" : "";
    return `
      <div class="nicolas-session-item${isActive ? " active" : ""}" data-session-id="${escapeHtml(s.session_id)}">
        <span class="nicolas-session-dot${statusClass}"></span>
        <div class="nicolas-session-body">
          <div class="nicolas-session-name" title="Double-click to rename">${escapeHtml(s.name || "Thread")}</div>
          ${s.preview ? `<div class="nicolas-session-preview">${escapeHtml(s.preview)}</div>` : ""}
          <div class="nicolas-session-meta">${escapeHtml(relTime)}</div>
        </div>
        <button class="nicolas-session-delete" title="Delete thread" aria-label="Delete thread" type="button">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
            <path d="M10 11v6"/><path d="M14 11v6"/>
          </svg>
        </button>
      </div>`;
  }).join("");

  nicolasSessionsListEl.querySelectorAll(".nicolas-session-item").forEach((item) => {
    const sid = item.dataset.sessionId;
    item.addEventListener("click", (e) => {
      if (e.target.closest(".nicolas-session-delete")) return;
      activateNicolasSession(sid);
    });
    const nameEl = item.querySelector(".nicolas-session-name");
    if (nameEl) {
      nameEl.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        startRenameNicolasSession(sid, nameEl);
      });
    }
    const deleteBtn = item.querySelector(".nicolas-session-delete");
    if (deleteBtn) {
      deleteBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteNicolasSession(sid);
      });
    }
  });

  // Update the continue-last-thread CTA with freshly-loaded sessions
  if (typeof renderContinueThreadCTA === "function") {
    renderContinueThreadCTA(_nicolasSessions);
  }
}

async function _clearSessionWaitingStatus(sessionId) {
  if (!sessionId || typeof serverPort === "undefined" || !serverPort) return;
  try {
    fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "idle" }),
    }).then(() => {
      // Update local session data to reflect cleared status
      const session = _nicolasSessions.find((s) => s.session_id === sessionId);
      if (session) {
        session.status = "idle";
        renderNicolasSessions();
      }
    }).catch(() => {});
  } catch {}
}

async function deleteNicolasSession(sessionId) {
  if (!sessionId) return;
  const entry = _nicolasSessions.find((s) => s.session_id === sessionId);
  const label = entry && entry.name ? `"${entry.name}"` : "this thread";
  if (!confirm(`Delete ${label}? This can't be undone.`)) return;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    if (!resp.ok) return;
    if (sessionId === _nicolasActiveSessionId) {
      _nicolasActiveSessionId = null;
      _displayedSessionId = null;
      if (typeof updateSessionBreadcrumb === "function") updateSessionBreadcrumb(null);
      if (messagesEl) messagesEl.innerHTML = "";
      const welcomeHostEl = document.getElementById("welcome");
      if (welcomeHostEl) welcomeHostEl.classList.remove("has-thread-history");
    }
    fetchNicolasSessions();
  } catch (err) {
    console.error("Failed to delete session:", err);
  }
}

async function activateNicolasSession(sessionId) {
  if (!sessionId) return;
  // Clear waiting status when user focuses on a session
  _clearSessionWaitingStatus(sessionId);
  // If it's already the active session, still open the pane — user may
  // have been looking at the welcome screen or another view.
  if (sessionId === _nicolasActiveSessionId) {
    if (typeof showNicolasMain === "function") showNicolasMain();
    if (typeof updateSessionBreadcrumb === "function") updateSessionBreadcrumb(sessionId);
    // Only reload history if this session isn't already rendered. We cannot
    // use messagesEl.children.length here because banners (chat-banner,
    // nicolas-locked-narration) are injected into #messages before any
    // conversation loads — a children-count check produces false negatives.
    // _displayedSessionId is the authoritative "what's in the DOM" signal.
    if (!isStreaming && _displayedSessionId !== sessionId && typeof loadSessionHistory === "function") {
      await loadSessionHistory(sessionId);
      _displayedSessionId = sessionId;
    }
    return;
  }
  if (isStreaming) {
    const proceed = confirm("Nicolas is still responding. Switch sessions anyway? The in-flight response will be cancelled.");
    if (!proceed) return;
    // Cancel the in-flight stream on the old session before switching
    if (typeof ws !== "undefined" && ws && ws.readyState === WebSocket.OPEN) {
      try { ws.send(JSON.stringify({ type: "cancel" })); } catch {}
    }
  }
  const draft = inputEl ? inputEl.value : "";
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions/${encodeURIComponent(sessionId)}/activate`, {
      method: "POST",
    });
    if (!resp.ok) return;
    _nicolasActiveSessionId = sessionId;
    if (typeof showNicolasMain === "function") showNicolasMain();
    if (typeof updateSessionBreadcrumb === "function") updateSessionBreadcrumb(sessionId);
    // Reset streaming UI state — the new session starts idle
    isStreaming = false;
    currentAssistantEl = null;
    currentText = "";
    if (inputEl) {
      inputEl.disabled = false;
      inputEl.value = draft;
    }
    if (sendBtn) sendBtn.disabled = false;
    if (typeof loadSessionHistory === "function") {
      await loadSessionHistory(sessionId);
      _displayedSessionId = sessionId;
    }
    renderNicolasSessions();
  } catch (err) {
    console.error("Failed to activate session:", err);
  }
}

async function createNicolasSession() {
  if (isStreaming) {
    const proceed = confirm("Nicolas is still responding. Start a new thread anyway?");
    if (!proceed) return;
  }
  const draft = inputEl ? inputEl.value : "";
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions`, { method: "POST" });
    if (!resp.ok) return;
    _nicolasActiveSessionId = null;
    _displayedSessionId = null;
    if (typeof updateSessionBreadcrumb === "function") updateSessionBreadcrumb(null);
    if (typeof showNicolasMain === "function") showNicolasMain();
    if (messagesEl) messagesEl.innerHTML = "";
    // New thread has no history — show the welcome splash/dashboard again.
    const welcomeHostEl = document.getElementById("welcome");
    if (welcomeHostEl) welcomeHostEl.classList.remove("has-thread-history");
    currentAssistantEl = null;
    currentText = "";
    isStreaming = false;
    if (inputEl) {
      inputEl.disabled = false;
      inputEl.value = draft;
      inputEl.focus();
    }
    if (sendBtn) sendBtn.disabled = false;
    renderNicolasSessions();
  } catch (err) {
    console.error("Failed to create session:", err);
  }
}

function startRenameNicolasSession(sessionId, nameEl) {
  const current = nameEl.textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.value = current;
  input.className = "nicolas-session-rename";
  nameEl.replaceWith(input);
  input.focus();
  input.select();

  let finished = false;
  const finish = async (commit) => {
    if (finished) return;
    finished = true;
    const newName = input.value.trim();
    const restored = document.createElement("div");
    restored.className = "nicolas-session-name";
    restored.title = "Double-click to rename";
    restored.textContent = current;
    if (commit && newName && newName !== current) {
      try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/nicolas/sessions/${encodeURIComponent(sessionId)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: newName }),
        });
        if (resp.ok) {
          restored.textContent = newName;
          fetchNicolasSessions();
        }
      } catch {}
    }
    input.replaceWith(restored);
    restored.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      startRenameNicolasSession(sessionId, restored);
    });
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
}

function onNicolasViewActivated() {
  fetchNicolasSessions();
}

function _wireNicolasButtons() {
  const newBtn = document.getElementById("nicolas-new-session-btn");
  if (newBtn) newBtn.addEventListener("click", createNicolasSession);
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _wireNicolasButtons);
} else {
  _wireNicolasButtons();
}

function _relativeTime(iso) {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (isNaN(then)) return "";
  const diff = (Date.now() - then) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
