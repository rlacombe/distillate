/**
 * Drafts dock — non-blocking panel holding pending session wrapup summaries.
 *
 * Replaces the single blocking completion modal with a floating stack so
 * multiple sessions can be wrapped up in parallel: clicking ✓ on N
 * sessions produces N draft cards in the dock, each independently
 * editable + Saveable + Discardable.
 *
 * Persistence: drafts live on the backend as `session.draft_summary`.
 * The sidebar listing surfaces the field, so repopulate() restores the
 * dock on app reload.
 *
 * Public API (on window):
 *   addDraftToDock(workspaceId, sessionId, sessionName, draftSummary)
 *   removeDraftFromDock(sessionId)
 *   hasDraftInDock(sessionId) -> boolean
 *   getDraftsDockCount() -> number
 */

// sessionId → { workspaceId, sessionName } — source of truth for the dock.
const _draftEntries = new Map();

const dockEl = document.getElementById("drafts-dock");


function _updateVisibility() {
  if (!dockEl) return;
  dockEl.hidden = _draftEntries.size === 0;
}


function addDraftToDock(workspaceId, sessionId, sessionName, draftSummary) {
  if (!dockEl) return;

  // Idempotent: clicking ✓ again on a session that already has a draft
  // just refreshes the summary text; it doesn't spawn a second card.
  if (_draftEntries.has(sessionId)) {
    const existing = document.getElementById(`draft-summary-${sessionId}`);
    if (existing && draftSummary) existing.value = draftSummary;
    _updateVisibility();
    return;
  }

  _draftEntries.set(sessionId, { workspaceId, sessionName });

  const card = document.createElement("div");
  card.className = "draft-card";
  card.id = `draft-card-${sessionId}`;
  // setAttribute (not dataset) so the explicit strings "data-ws-id" /
  // "data-session-id" appear in source for CSS selectors + tests.
  card.setAttribute("data-ws-id", workspaceId);
  card.setAttribute("data-session-id", sessionId);

  // Header: session name + close (discard) button.
  const header = document.createElement("div");
  header.className = "draft-card-header";

  const name = document.createElement("span");
  name.className = "draft-card-name";
  name.textContent = sessionName || "Coding session";
  header.appendChild(name);

  const closeBtn = document.createElement("button");
  closeBtn.className = "draft-card-close";
  closeBtn.setAttribute("aria-label", "Discard — keep session running");
  closeBtn.title = "Discard — keep session running";
  closeBtn.innerHTML = "&times;";
  closeBtn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    await _discardDraft(workspaceId, sessionId);
  });
  header.appendChild(closeBtn);

  card.appendChild(header);

  // Body: editable summary textarea.
  const textarea = document.createElement("textarea");
  textarea.className = "draft-card-summary";
  textarea.id = `draft-summary-${sessionId}`;
  textarea.rows = 10;
  textarea.value = draftSummary || "";
  card.appendChild(textarea);

  // Footer: Save button. Saving ends the session.
  const actions = document.createElement("div");
  actions.className = "draft-card-actions";

  const saveBtn = document.createElement("button");
  saveBtn.className = "draft-card-save";
  saveBtn.textContent = "Save & end session";
  saveBtn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    await _saveDraft(workspaceId, sessionId);
  });
  actions.appendChild(saveBtn);

  card.appendChild(actions);
  dockEl.appendChild(card);

  _updateVisibility();
}


function removeDraftFromDock(sessionId) {
  const card = document.getElementById(`draft-card-${sessionId}`);
  if (card) card.remove();
  _draftEntries.delete(sessionId);
  _updateVisibility();
}


function hasDraftInDock(sessionId) {
  return _draftEntries.has(sessionId);
}


function getDraftsDockCount() {
  return _draftEntries.size;
}


async function _saveDraft(workspaceId, sessionId) {
  const textarea = document.getElementById(`draft-summary-${sessionId}`);
  const edited = (textarea?.value || "").trim();
  if (!edited) {
    if (typeof showToast === "function") {
      showToast("Summary cannot be empty", "error");
    }
    return;
  }

  const saveBtn = document.querySelector(
    `#draft-card-${sessionId} .draft-card-save`,
  );
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "Saving..."; }

  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/sessions/${sessionId}/summary`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ summary: edited }),
      },
    );
    const data = await resp.json();
    if (!data.success) throw new Error(data.error || "save failed");

    removeDraftFromDock(sessionId);

    // If this was the currently-viewed session, clear the terminal —
    // its tmux is gone now. Otherwise leave the user's view untouched:
    // the dock is non-blocking, so saving shouldn't yank focus.
    if (typeof _selectedSession !== "undefined"
        && _selectedSession
        && _selectedSession.sessionId === sessionId) {
      if (typeof detachTerminal === "function") detachTerminal();
      if (typeof showSessionEmpty === "function") showSessionEmpty();
    }

    // Refresh sidebar so the saved session drops to "completed".
    // Intentionally do NOT call selectWorkspace — that would navigate
    // the user away from wherever they are.
    // Delay the sidebar rebuild to allow any active xterm connections to
    // stabilize before HTML is replaced (avoids race condition where stale
    // onclick handlers could trigger selectSession on the wrong session).
    setTimeout(() => {
      if (typeof fetchWorkspaces === "function") fetchWorkspaces();
    }, 300);

    // Refresh the Notebook view so the saved session appears immediately.
    if (typeof fetchNotebookEntries === "function") fetchNotebookEntries();
    // Signal new content on the Notebook rail button (dot clears on click).
    document.querySelector('.activity-btn[data-sidebar-view="notebook"]')?.classList.add("has-notification");

    if (typeof showToast === "function") {
      showToast("Saved to lab notebook", "success");
    }
  } catch (err) {
    if (typeof showToast === "function") {
      showToast("Failed to save summary", "error");
    }
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save & end session";
    }
  }
}


async function _discardDraft(workspaceId, sessionId) {
  // Remove from UI immediately — the server call is best-effort.
  removeDraftFromDock(sessionId);
  try {
    await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/sessions/${sessionId}/wrapup/discard`,
      { method: "POST" },
    );
    if (typeof showToast === "function") {
      showToast("Kept session running — wrap-up discarded", "info");
    }
  } catch (e) {
    // Best-effort: session keeps running locally either way.
  }
}


// Repopulate on app reload: any running session with a draft_summary
// in the workspace listing should appear in the dock.
function _repopulateFromWorkspaces() {
  if (typeof _workspaces === "undefined" || !Array.isArray(_workspaces)) return;
  for (const ws of _workspaces) {
    for (const s of ws.running_sessions || []) {
      if (s.draft_summary && !hasDraftInDock(s.id)) {
        addDraftToDock(
          ws.id, s.id,
          s.agent_name || s.tmux_name || "Coding session",
          s.draft_summary,
        );
      }
    }
  }
}


// Hook into any post-fetch workspace update that the app triggers.
if (typeof window !== "undefined") {
  window.addEventListener("workspaces-updated", _repopulateFromWorkspaces);
}


// Initial hidden-when-empty state.
_updateVisibility();


// Public API.
window.addDraftToDock = addDraftToDock;
window.removeDraftFromDock = removeDraftFromDock;
window.hasDraftInDock = hasDraftInDock;
window.getDraftsDockCount = getDraftsDockCount;
