/**
 * Workspaces tab — workspace management.
 *
 * Workspaces are top-level containers for repos, experiments, papers,
 * resources, coding sessions, and canvases.
 */

// One-time migration: purge stale `distillate-folder-collapsed-*` keys left
// over from an earlier folder-grouping UI that had collapse/expand. The
// cartouche redesign dropped collapse entirely, so these keys are orphans.
// Idempotent — if no stale keys exist, the loop is a no-op.
try {
  const stale = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && key.startsWith("distillate-folder-collapsed-")) stale.push(key);
  }
  for (const k of stale) localStorage.removeItem(k);
} catch {}

// ---------------------------------------------------------------------------
// Sidebar view switching (driven by activity bar icons)
// ---------------------------------------------------------------------------

// Restore last-selected sidebar view from previous session
let _activeSidebarView = (() => {
  try {
    const saved = localStorage.getItem("distillate-active-sidebar-view");
    const valid = ["nicolas", "experiments", "workspaces", "notebook", "papers", "agents", "vault", "integrations"];
    if (saved && valid.includes(saved)) return saved;
    // Migrate: "agents" -> "nicolas", "projects" -> "workspaces"
    if (saved === "agents") return "nicolas";
    if (saved === "projects") return "workspaces";
  } catch {}
  return "nicolas";
})();

function switchSidebarView(viewName) {
  // Settings and usage-stats are now a full-page overlay, not sidebar views.
  if (viewName === "settings" || viewName === "usage-stats") {
    const section = viewName === "usage-stats" ? "usage" : "account";
    if (typeof openSettings === "function") openSettings(section);
    return;
  }

  _activeSidebarView = viewName;
  // Persist for next launch
  try { localStorage.setItem("distillate-active-sidebar-view", viewName); } catch {}

  const views = document.querySelectorAll(".sidebar-tab-content");
  views.forEach((v) => v.classList.toggle("active", v.id === `${viewName}-view`));

  // Update activity bar active state + clear notification on the view being focused
  document.querySelectorAll('.activity-btn[data-pane="sidebar-left"]').forEach((b) => {
    b.classList.toggle("active", b.dataset.sidebarView === viewName);
    if (b.dataset.sidebarView === viewName) b.classList.remove("has-notification");
  });

  // Update secondary view labels (e.g. Projects/Notebook demoted views)
  document.querySelectorAll('.sidebar-view-label').forEach((l) => {
    // Labels might use slightly different view names than the rail button
    // (e.g. 'projects' view vs 'notebook' view both live in the same rail).
    // We check if the onclick calls switchSidebarView with this viewName.
    const onclick = l.getAttribute('onclick') || '';
    l.classList.toggle("active", onclick.includes(`'${viewName}'`));
  });

  // When switching away from papers view and nothing is selected,
  // restore the welcome screen (papers home lives in the detail pane).
  if (viewName !== "papers" && !currentProjectId && !currentPaperKey) {
    const detailEl = document.getElementById("experiment-detail");
    if (detailEl) { detailEl.classList.add("hidden"); detailEl.innerHTML = ""; }
    if (typeof welcomeEl !== "undefined" && welcomeEl) welcomeEl.classList.remove("hidden");
  }

  // Fetch data for the view
  if (viewName === "nicolas") {
    nicolasWaiting = false;
    // Tell the backend to drop the pending-turn flag so other windows
    // (and the next status poll here) stop advertising the bell.
    fetch(`http://127.0.0.1:${serverPort}/nicolas/ack`, { method: "POST" }).catch(() => {});
    if (typeof onNicolasViewActivated === "function") onNicolasViewActivated();
  }
  if (viewName === "workspaces") fetchWorkspaces();
  if (viewName === "experiments") {
    if (typeof fetchExperimentsList === "function") fetchExperimentsList();
  }
  if (viewName === "agents") {
    if (typeof fetchSpirits === "function") fetchSpirits();
  }
  if (viewName === "notebook") {
    // Reset day drill-down, date filter, and change-detection fingerprint
    // so the recent feed is shown fresh — stale state from a previous
    // notebook visit caused entries to silently not render.
    if (typeof _notebookOpenDay !== "undefined") _notebookOpenDay = "";
    if (typeof _notebookCurrentDate !== "undefined") _notebookCurrentDate = "";
    if (typeof _notebookLastEntryKey !== "undefined") _notebookLastEntryKey = "";
    if (typeof fetchNotebookEntries === "function") fetchNotebookEntries();
  }
  if (viewName === "papers") {
    if (typeof fetchPapersData === "function") fetchPapersData();
    // Show papers home page in center pane when no paper is selected
    if (!currentPaperKey && typeof showPapersHome === "function") showPapersHome();
  }
  if (viewName === "vault") {
    if (typeof fetchVaultTree === "function") fetchVaultTree();
  }
  if (viewName === "integrations") {
    if (typeof fetchIntegrations === "function") fetchIntegrations();
  }
}

// Apply the restored view on DOM ready (before any data fetches)
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => switchSidebarView(_activeSidebarView));
} else {
  switchSidebarView(_activeSidebarView);
}

// ── Smart tray click: jump to oldest waiting session ──
// Main process sends "focus-waiting-session" when the user clicks the tray
// icon (or "Show Distillate") and there's a session needing attention.
// `target` is a session/agent descriptor built in the status poll above.
function focusWaitingSession(target) {
  if (!target || !target.status) return;

  // Ensure the left sidebar is open
  const sidebar = document.getElementById("sidebar-left");
  if (sidebar?.classList.contains("collapsed") && typeof togglePane === "function") {
    togglePane("sidebar-left");
  }

  if (target.kind === "agent" && target.agentId) {
    switchSidebarView("agents");
    if (typeof selectSpirit === "function") selectSpirit(target.agentId);
    return;
  }

  if (target.kind === "nicolas") {
    switchSidebarView("nicolas");
    return;
  }

  if (target.kind === "session" && target.workspaceId && target.sessionId) {
    // Prefer Projects view — that's where coding sessions live in the tree
    switchSidebarView("workspaces");
    // Look up tmux name from _workspaces
    let tmuxName = "";
    const ws = _workspaces.find((w) => w.id === target.workspaceId);
    if (ws) {
      const sess = (ws.running_sessions || []).find((s) => s.id === target.sessionId);
      if (sess) tmuxName = sess.tmux_name || "";
    }
    // Give the view a frame to render before selecting (fetchWorkspaces is async)
    const doSelect = () => {
      if (typeof selectSession === "function") {
        selectSession(target.workspaceId, target.sessionId, tmuxName);
      }
    };
    // If we don't have tmux name yet (sidebar not rendered), wait for the
    // workspace fetch to complete then retry once.
    if (!tmuxName && typeof fetchWorkspaces === "function") {
      Promise.resolve(fetchWorkspaces()).then(() => {
        const ws2 = _workspaces.find((w) => w.id === target.workspaceId);
        const sess2 = ws2?.running_sessions?.find((s) => s.id === target.sessionId);
        const tname = sess2?.tmux_name || "";
        if (typeof selectSession === "function") {
          selectSession(target.workspaceId, target.sessionId, tname);
        }
      });
    } else {
      doSelect();
    }
  }
}

if (window.nicolas?.onFocusWaitingSession) {
  window.nicolas.onFocusWaitingSession(focusWaitingSession);
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _workspaces = [];
let _selectedWorkspace = null;
let _selectedSession = null; // { workspaceId, sessionId, tmuxName }
let _prevSessionStatus = {};  // key → last-known status string (for bell transition detection)

const workspacesSidebarEl = document.getElementById("workspaces-sidebar");
const workspacesCountEl = document.getElementById("workspaces-count");
const newWorkspaceBtn = document.getElementById("new-workspace-btn");

// ---------------------------------------------------------------------------
// Fetch workspaces from server
// ---------------------------------------------------------------------------

async function fetchWorkspaces() {
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces`);
    if (!resp.ok) return;
    const data = await resp.json();
    _workspaces = data.workspaces || [];
    renderWorkspacesList(_workspaces);
    // Cache for instant render on next launch
    try { localStorage.setItem("distillate-workspaces-cache", JSON.stringify(_workspaces)); } catch {}
  } catch (e) {
    // Server not ready yet
  }
}

// ---------------------------------------------------------------------------
// Render workspace list in sidebar
// ---------------------------------------------------------------------------

// ── Per-project collapsed state (persisted) ─────────────────────────
// Individual projects can be collapsed to show just their heading. A
// collective toggle collapses/expands all at once. State lives in
// localStorage so it survives reloads.
const _PROJECTS_COLLAPSED_KEY = "distillate.projects.collapsed";
function _loadCollapsedProjects() {
  try {
    const raw = localStorage.getItem(_PROJECTS_COLLAPSED_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr : []);
  } catch (_) { return new Set(); }
}
function _saveCollapsedProjects(set) {
  try {
    localStorage.setItem(_PROJECTS_COLLAPSED_KEY, JSON.stringify([...set]));
  } catch (_) {}
}
function _isProjectCollapsed(wsId) {
  return _loadCollapsedProjects().has(wsId);
}
function toggleProjectCollapsed(wsId) {
  const set = _loadCollapsedProjects();
  if (set.has(wsId)) set.delete(wsId); else set.add(wsId);
  _saveCollapsedProjects(set);
  renderWorkspacesList(_workspaces);
}
function toggleAllProjectsCollapsed() {
  const all = (_workspaces || []).map((w) => w.id);
  if (!all.length) return;
  const set = _loadCollapsedProjects();
  // If any is expanded → collapse all; else expand all.
  const anyExpanded = all.some((id) => !set.has(id));
  const next = new Set();
  if (anyExpanded) for (const id of all) next.add(id);
  _saveCollapsedProjects(next);
  renderWorkspacesList(_workspaces);
}

function renderWorkspacesList(workspaces) {
  workspacesCountEl.textContent = workspaces.length || "";
  const collapseBtn = document.getElementById("workspaces-collapse-all-btn");
  if (collapseBtn) {
    const collapsed = _loadCollapsedProjects();
    const allCollapsed = workspaces.length > 0 && workspaces.every((w) => collapsed.has(w.id));
    collapseBtn.title = allCollapsed ? "Expand all" : "Collapse all";
    collapseBtn.setAttribute("aria-label", allCollapsed ? "Expand all" : "Collapse all");
    collapseBtn.classList.toggle("is-all-collapsed", allCollapsed);
  }

  if (!workspaces.length) {
    workspacesSidebarEl.innerHTML = `
      <div class="sidebar-empty">
        <p>No workspaces yet.</p>
        <p class="sidebar-empty-hint">Create a workspace to start tracking repos and experiments.</p>
      </div>`;
    return;
  }

  // Sort: non-default projects first, Workbench (default) pinned last
  const sorted = [...workspaces].sort((a, b) => {
    if (a.default && !b.default) return 1;
    if (!a.default && b.default) return -1;
    return 0;
  });

  const liveNowHtml = _renderLiveNow(workspaces);
  workspacesSidebarEl.innerHTML = liveNowHtml + sorted.map(_renderWorkspaceBlock).join("");
}

// ── Per-project frontier chart ──────────────────────────────────────
// Overlays each experiment's metric trajectory as a normalized line on a
// shared run-index axis. Each line has its own y-range (metrics may
// differ across experiments), so this is a "progress direction" view —
// not a calibrated comparison. Matches redesign §11.4 option (a).
function _renderProjectFrontierChart(experiments) {
  const series = (experiments || []).filter(
    (e) => Array.isArray(e.metric_history) && e.metric_history.length > 1
  );
  if (series.length === 0) return "";

  const w = 460, h = 110;
  const padL = 6, padR = 6, padT = 6, padB = 10;
  const innerW = w - padL - padR, innerH = h - padT - padB;
  const maxLen = Math.max(...series.map((e) => e.metric_history.length));

  // Stable per-experiment colors from a small palette.
  const palette = ["#8a80d8", "#6ab0c6", "#e0b76a", "#c08a6e", "#d8a2b8", "#5db76a"];
  const lines = series.map((e, i) => {
    const vals = e.metric_history;
    const min = Math.min(...vals), max = Math.max(...vals);
    const range = (max - min) || 1;
    const pts = vals.map((v, idx) => {
      const x = padL + (vals.length === 1 ? innerW / 2 : (idx / (maxLen - 1)) * innerW);
      const y = padT + innerH - ((v - min) / range) * innerH;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    const color = palette[i % palette.length];
    const lastX = padL + ((vals.length - 1) / Math.max(maxLen - 1, 1)) * innerW;
    const lastY = padT + innerH - ((vals[vals.length - 1] - min) / range) * innerH;
    return {
      name: e.name || e.id,
      color,
      polyline: `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" opacity="0.85"/>`,
      dot: `<circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="2.2" fill="${color}"/>`,
    };
  });

  const legendHtml = lines.map((l) =>
    `<span class="workspace-chart-legend-item"><span class="workspace-chart-swatch" style="background:${l.color}"></span>${escapeHtml(l.name)}</span>`
  ).join("");

  const svg =
    `<svg class="workspace-frontier-chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">` +
    `<line x1="${padL}" y1="${h - padB}" x2="${w - padR}" y2="${h - padB}" stroke="var(--border)" stroke-width="0.5"/>` +
    lines.map((l) => l.polyline + l.dot).join("") +
    `</svg>`;

  return `
    <div class="workspace-frontier-chart-wrap">
      ${svg}
      <div class="workspace-chart-legend">${legendHtml}</div>
    </div>`;
}

// ── Live Now: pinned strip at top of Projects sidebar ──
// Aggregates everything currently working across all projects:
//   - Experiments with active_sessions > 0  (type: xp)
//   - Running sessions with agent_status === "working" (type by session_type)
// Only renders when there's at least one live item.
function _renderLiveNow(workspaces) {
  const items = [];
  for (const ws of workspaces) {
    const wsName = ws.name || "";
    // Live experiments
    for (const exp of (ws.experiments || [])) {
      if ((exp.active_sessions || 0) > 0) {
        items.push({
          kind: "xp",
          name: exp.name || exp.id,
          wsId: ws.id,
          wsName,
          expId: exp.id,
        });
      }
    }
    // Working sessions (skip waiting/idle — "live now" means actively generating)
    for (const s of (ws.running_sessions || [])) {
      if (s.agent_status !== "working") continue;
      // Classify session by canvas category; fall back to "code" for plain coding sessions.
      let kind = "c";
      if (s.canvas_id) {
        const canvas = (ws.canvases || []).find((c) => c.id === s.canvas_id);
        if (canvas) {
          const cat = _canvasCategory(canvas.type);
          if (cat === "write") kind = "w";
          else if (cat === "survey") kind = "s";
          else if (cat === "data") kind = "d";
          else kind = "c";
        }
      }
      items.push({
        kind,
        name: s.agent_name || s.repo || "session",
        wsId: ws.id,
        wsName,
        sessId: s.id,
        tmux: s.tmux_name || "",
      });
    }
  }
  if (!items.length) return "";

  // Experiments first, then sessions
  items.sort((a, b) => (b.kind === "xp") - (a.kind === "xp"));

  const rows = items.map((it) => {
    const typeLabel = { xp: "XP", c: "CODE", w: "WRITE", s: "SURVEY", d: "DATA" }[it.kind] || it.kind.toUpperCase();
    const name = escapeHtml(it.name);
    const ws = escapeHtml(it.wsName);
    const clickAttr = it.kind === "xp"
      ? `onclick="selectWorkspace('${it.wsId}')"`
      : `onclick="selectSession('${it.wsId}','${it.sessId}','${escapeHtml(it.tmux)}')"`;
    return `
      <div class="live-now-item live-now-${it.kind}" ${clickAttr} title="${name} — ${ws}">
        <span class="live-now-dot"></span>
        <span class="live-now-name">${name}</span>
        <span class="live-now-type">${typeLabel}</span>
        <span class="live-now-proj">${ws}</span>
      </div>`;
  }).join("");

  return `
    <div class="live-now-strip">
      <div class="live-now-head">
        <span class="live-now-label">Live now</span>
        <span class="live-now-count">${items.length}</span>
      </div>
      <div class="live-now-items">${rows}</div>
    </div>`;
}

// Normalize workspace.repos entries to an array of string names.
// The list endpoint returns plain strings; detail endpoints return objects.
function _normalizeRepoNames(ws) {
  return (ws.repos || [])
    .map((r) => typeof r === "string" ? r : (r && (r.name || (r.path ? r.path.split("/").pop() : ""))))
    .filter(Boolean);
}

function _renderWorkspaceBlock(ws) {
  const sessions = ws.running_sessions || [];
  const hasSelectedSession = _selectedSession && _selectedSession.workspaceId === ws.id;
  const wsActive = _selectedWorkspace === ws.id && !hasSelectedSession ? " active" : "";
  const isDefault = ws.default;

  // Only show live sessions (working/waiting) as individual rows; idle sessions
  // are noise — they appear only in the compact status summary below.
  const liveSessions = sessions.filter((s) => s.agent_status === "working" || s.agent_status === "waiting");
  const idleCount = sessions.filter((s) => s.agent_status === "idle").length;

  const sessionsBadge = liveSessions.length > 0
    ? `<span class="sidebar-item-badge running">${liveSessions.length}</span>` : "";

  // Status summary: compact counts — only for working/waiting/idle/error
  let statusSummary = "";
  if (sessions.length > 0) {
    const parts = [];
    const working = sessions.filter((s) => s.agent_status === "working").length;
    const waiting = sessions.filter((s) => s.agent_status === "waiting").length;
    const lost = sessions.filter((s) => s.agent_status === "lost").length;
    if (working) parts.push(`<span class="status-summary-working">${working} working</span>`);
    if (waiting) parts.push(`<span class="status-summary-waiting">${waiting} waiting</span>`);
    if (lost)    parts.push(`<span class="status-summary-lost">${lost} error</span>`);
    if (idleCount) parts.push(`<span class="status-summary-idle">${idleCount} idle</span>`);
    if (parts.length) statusSummary = `<div class="sidebar-status-summary">${parts.join(" \u00b7 ")}</div>`;
  }

  // Workbench: default catch-all for unfiled experiments, pinned last
  const nameHtml = isDefault
    ? `<span class="sidebar-item-name is-default">${escapeHtml(ws.name)}</span>`
    : `<span class="sidebar-item-name">${escapeHtml(ws.name)}</span>`;

  const isCollapsed = _isProjectCollapsed(ws.id);
  const collapsedCls = isCollapsed ? " is-collapsed" : "";
  const toggleSymbol = isCollapsed ? "+" : "−";

  let html = `
    <div class="sidebar-workspace-group${isDefault ? " workspace-default" : ""}${collapsedCls}" data-workspace-id="${ws.id}">
      <div class="sidebar-item sidebar-workspace-heading${wsActive}" onclick="selectWorkspace('${ws.id}')">
        <button class="workspace-header-collapse-btn" onclick="event.stopPropagation(); toggleProjectCollapsed('${ws.id}')" title="${isCollapsed ? 'Expand' : 'Collapse'}">${toggleSymbol}</button>
        ${nameHtml}
        ${sessionsBadge}
        <button class="workspace-header-add-btn" onclick="event.stopPropagation(); launchQuickSession('${ws.id}')" title="New session">+</button>
      </div>`;

  if (isCollapsed) {
    html += `</div>`;
    return html;
  }

  html += statusSummary;

  const repoNames = _normalizeRepoNames(ws);
  const linkedPapers = ws.linked_papers || [];
  const hasPapers = linkedPapers.length > 0;
  const useGrouping = repoNames.length > 1 || hasPapers;

  if (!useGrouping) {
    if (sessions.length > 0) {
      html += `<div class="sidebar-session-list">`;
      html += sessions.map((s) => _renderSessionRow(ws.id, s)).join("");
      html += `</div>`;
    }
  } else {
    // Group sessions by their `repo` field; anything unmatched falls into `loose`.
    const sessionsByRepo = {};
    const looseSessions = [];
    for (const s of sessions) {
      const repo = s.repo || "";
      if (repo && repoNames.includes(repo)) {
        (sessionsByRepo[repo] = sessionsByRepo[repo] || []).push(s);
      } else {
        looseSessions.push(s);
      }
    }

    // Sort folders by most recent session (empty folders sink to the bottom).
    const folders = repoNames.map((name) => {
      const sess = sessionsByRepo[name] || [];
      const mostRecent = sess.reduce((max, s) => {
        const t = s.started_at ? new Date(s.started_at).getTime() : 0;
        return t > max ? t : max;
      }, 0);
      return { name, sessions: sess, mostRecent };
    });
    folders.sort((a, b) => b.mostRecent - a.mostRecent);

    for (const folder of folders) {
      html += _renderFolderGroup(ws.id, folder);
    }

    if (hasPapers) {
      html += _renderPapersRow(ws.id, linkedPapers.length);
    }

    if (looseSessions.length > 0) {
      html += _renderFolderGroup(ws.id, { name: "__loose__", label: "Other", sessions: looseSessions });
    }
  }

  // Work session (canvas) items — all except archived (done items shown dimmed)
  const visibleCanvases = (ws.canvases || []).filter((c) => c.status !== "archived");
  if (visibleCanvases.length > 0) {
    html += `<div class="sidebar-session-list sidebar-canvas-list">`;
    html += visibleCanvases.map((c) => _renderCanvasItem(ws.id, c)).join("");
    html += `</div>`;
  }

  // "N archived" link at the bottom
  const archivedCount = (ws.total_sessions || 0) - sessions.length;
  if (archivedCount > 0) {
    html += `<div class="sidebar-archived-link" onclick="event.stopPropagation(); selectWorkspace('${ws.id}')">${archivedCount} archived</div>`;
  }

  html += `</div>`;
  return html;
}

function _renderFolderGroup(wsId, folder) {
  const { name, sessions, label } = folder;
  const displayName = label || name;
  const count = sessions.length;
  const emptyClass = count === 0 ? " is-empty" : "";

  const escName = escapeHtml(name);
  const escDisplay = escapeHtml(displayName);
  const addBtn = name === "__loose__" ? "" : `
        <button class="folder-header-add-btn" onclick="event.stopPropagation(); launchCodingSession('${wsId}', '${escName}')" title="New session in ${escDisplay}">+</button>`;

  let html = `
    <div class="sidebar-folder-group${emptyClass}" data-folder="${escName}">
      <div class="sidebar-folder-heading">
        <span class="sidebar-folder-tag">${escDisplay}</span>${addBtn}
      </div>
      <div class="sidebar-folder-sessions">`;

  for (const s of sessions) {
    html += _renderSessionRow(wsId, s);
  }

  html += `</div></div>`;
  return html;
}

// SVG icons for the 4 work session types + default write/canvas type
const _WORK_ITEM_ICONS = {
  code: `<svg class="sidebar-canvas-icon sidebar-canvas-icon-code" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>`,
  write: `<svg class="sidebar-canvas-icon sidebar-canvas-icon-write" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
  survey: `<svg class="sidebar-canvas-icon sidebar-canvas-icon-survey" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
  data: `<svg class="sidebar-canvas-icon sidebar-canvas-icon-data" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`,
};

function _canvasCategory(canvasType) {
  if (["latex", "markdown", "plain"].includes(canvasType)) return "write";
  return canvasType || "write";
}

function _renderCanvasItem(wsId, canvas) {
  const label = (canvas.title && String(canvas.title).trim()) || "canvas";
  const safeId = escapeHtml(canvas.id || "");
  const safeLabel = escapeHtml(label);
  const category = _canvasCategory(canvas.type);
  const icon = _WORK_ITEM_ICONS[category] || _WORK_ITEM_ICONS.write;
  const typeBadge = (category !== "write")
    ? `<span class="work-item-type-badge work-item-type-${category}">${category}</span>`
    : "";
  // Show a live dot if there's a running session attached to this canvas
  const ws = _workspaces.find((w) => w.id === wsId);
  const liveSession = (ws?.running_sessions || []).find((s) => s.canvas_id === canvas.id);
  const liveDot = liveSession
    ? `<span class="sidebar-canvas-live-dot status-working" title="Session running">\u25CF</span>`
    : "";
  const isDone = canvas.status === "done";
  return `
    <div class="sidebar-item sidebar-canvas-item${isDone ? " sidebar-canvas-item-done" : ""}" onclick="_sidebarOpenCanvas('${wsId}','${safeId}')" title="${isDone ? "Done — " : ""}Open ${safeLabel}">
      ${icon}<span class="sidebar-item-name">${safeLabel}</span>${liveDot}${typeBadge}
    </div>`;
}

// Sidebar click → find the canvas's running session and select it,
// or launch a new writing session for this canvas.
async function _sidebarOpenCanvas(wsId, canvasId) {
  if (typeof selectWorkspace === "function" && _selectedWorkspace !== wsId) {
    await selectWorkspace(wsId);
  }
  // Find a running session linked to this canvas
  const ws = _workspaces.find((w) => w.id === wsId);
  const sess = (ws?.running_sessions || []).find((s) => s.canvas_id === canvasId);
  if (sess && sess.tmux_name) {
    selectSession(wsId, sess.id, sess.tmux_name);
    return;
  }
  // No running session — launch one
  launchCanvasSession(wsId, canvasId);
}
window._sidebarOpenCanvas = _sidebarOpenCanvas;

async function _openWorkItem(wsId, canvasId) {
  await _sidebarOpenCanvas(wsId, canvasId);
}
window._openWorkItem = _openWorkItem;

async function _completeWorkItem(wsId, canvasId) {
  try {
    await fetch(`http://127.0.0.1:${serverPort}/workspaces/${wsId}/canvases/${canvasId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "done" }),
    });
    if (typeof selectWorkspace === "function") selectWorkspace(wsId);
  } catch (e) {
    console.error("_completeWorkItem failed:", e);
  }
}
window._completeWorkItem = _completeWorkItem;

async function _createWorkItem(wsId, type) {
  const title = prompt(`New ${type} session title:`);
  if (!title) return;
  const canvasTypeMap = { write: "markdown", code: "code", survey: "survey", data: "data" };
  const canvasType = canvasTypeMap[type] || "markdown";
  try {
    const createResp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${wsId}/canvases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, type: canvasType }),
    });
    const createData = await createResp.json();
    if (!createData.ok) { console.error("create work item failed:", createData); return; }
    const canvasId = createData.canvas.id;
    await fetch(`http://127.0.0.1:${serverPort}/workspaces/${wsId}/canvases/${canvasId}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (typeof selectWorkspace === "function") selectWorkspace(wsId);
  } catch (e) {
    console.error("_createWorkItem failed:", e);
  }
}
window._createWorkItem = _createWorkItem;

function _renderPapersRow(wsId, count) {
  return `
    <div class="sidebar-folder-group">
      <div class="sidebar-folder-heading sidebar-resource-heading" onclick="switchSidebarView('papers')" title="Open Papers">
        <span class="sidebar-folder-tag">papers</span>
        <span class="sidebar-folder-count">${count}</span>
      </div>
    </div>`;
}

function _renderSessionRow(wsId, s) {
  const isSelected = _selectedSession &&
    _selectedSession.workspaceId === wsId &&
    _selectedSession.sessionId === s.id;
  const selClass = isSelected ? " active" : "";
  const agentName = s.agent_name || s.tmux_name || "Claude Code";
  const since = s.started_at ? _relativeTime(s.started_at) : "";
  const status = s.agent_status || "unknown";
  // Only show dot for live/attention states; idle sessions show no dot (cleaner)
  const isLiveState = status === "working" || status === "waiting" || status === "lost" || status === "completed";
  const dotClass = status === "working" ? "status-working"
    : status === "waiting" ? "status-waiting"
    : status === "lost" ? "status-lost"
    : status === "completed" ? "status-completed" : "";
  const statusIcon = status === "completed" ? "\u2713" : "\u25CF"; // ✓ for completed, ● otherwise
  // Show a type icon for non-coding sessions
  const sessionType = s.session_type || (s.canvas_id ? "writing" : "coding");
  const typeIconMap = {
    writing: `<svg class="sidebar-session-doc-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
    survey: `<svg class="sidebar-session-doc-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
    data: `<svg class="sidebar-session-doc-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`,
  };
  const docIcon = typeIconMap[sessionType] || "";
  const bellHtml = s.attention_needed ? `<span class="session-bell" title="Action needed">\ud83d\udd14</span>` : "";
  const clickAction = status === "lost"
    ? `recoverSession('${wsId}', '${escapeHtml(s.id)}', '${escapeHtml(s.tmux_name || "")}')`
    : `selectSession('${wsId}', '${escapeHtml(s.id)}', '${escapeHtml(s.tmux_name || "")}')`;
  return `
    <div class="sidebar-item sidebar-session-item${selClass}"
         data-ws-id="${wsId}" data-session-id="${escapeHtml(s.id)}"
         data-tmux-name="${escapeHtml(s.tmux_name || "")}"
         onclick="${clickAction}"
         ondblclick="focusSessionTerminal('${wsId}', '${escapeHtml(s.id)}')">
      ${isLiveState ? `<span class="sidebar-status-icon ${dotClass}" title="${status}">${statusIcon}</span>` : `<span class="sidebar-status-icon sidebar-status-idle-spacer"></span>`}
      ${docIcon}
      <div class="sidebar-session-info">
        <span class="sidebar-session-name" title="${escapeHtml(agentName)}">${escapeHtml(agentName)}${bellHtml}</span>
      </div>
      <span class="sidebar-session-time">${since}</span>
    </div>`;
}

// ---------------------------------------------------------------------------
// Select session -> attach terminal immediately
// ---------------------------------------------------------------------------

function selectSession(workspaceId, sessionId, tmuxName) {
  // Guard: ignore if we're being called during a sidebar rebuild with the same session
  // This prevents stale onclick handlers from switching sessions unintentionally
  if (_selectedSession && _selectedSession.sessionId === sessionId && _selectedSession.workspaceId === workspaceId) {
    return; // Already viewing this session, don't re-attach
  }

  _selectedWorkspace = workspaceId;
  _selectedSession = { workspaceId, sessionId, tmuxName };

  // Clear session-done bell for this session (Set + DOM)
  const bellKey = `${workspaceId}/${sessionId}`;
  sessionDoneBells.delete(bellKey);

  // Update selection highlights in-place (no full re-render to avoid dot flash)
  workspacesSidebarEl.querySelectorAll(".sidebar-item").forEach((el) => el.classList.remove("active"));
  workspacesSidebarEl.querySelectorAll(".sidebar-session-item").forEach((el) => {
    const onclick = el.getAttribute("onclick") || "";
    if (onclick.includes(`'${sessionId}'`) && onclick.includes(`'${workspaceId}'`)) {
      el.classList.add("active");
      // Remove bell immediately on focus
      const bell = el.querySelector(".sidebar-session-bell");
      if (bell) bell.remove();
    }
  });
  // Also update in agents sidebar
  const agentsSidebar = document.getElementById("agents-sidebar");
  if (agentsSidebar) {
    agentsSidebar.querySelectorAll(".sidebar-item").forEach((el) => el.classList.remove("active"));
    agentsSidebar.querySelectorAll(".sidebar-session-item").forEach((el) => {
      const onclick = el.getAttribute("onclick") || "";
      if (onclick.includes(`'${sessionId}'`) && onclick.includes(`'${workspaceId}'`)) {
        el.classList.add("active");
      }
    });
  }

  // Find project and session names for the title bar
  const ws = _workspaces.find((w) => w.id === workspaceId);
  const workspaceName = ws ? ws.name : "";
  const sess = (ws?.running_sessions || []).find((s) => s.id === sessionId);
  const agentName = sess?.agent_name || sess?.tmux_name || tmuxName || "Claude Code";
  const agentStatus = sess?.agent_status || "unknown";
  const canvasId = sess?.canvas_id || "";

  attachToCodingSession(sessionId, workspaceId, tmuxName, workspaceName, agentName, canvasId);
  if (typeof updateSessionSummary === "function") updateSessionSummary(agentStatus, agentName);
}

// ---------------------------------------------------------------------------
// Double-click a session item → scroll terminal to bottom + focus input
// ---------------------------------------------------------------------------

function focusSessionTerminal(workspaceId, sessionId) {
  // Make sure this session is selected first (single-click may not have fired)
  const ws = _workspaces.find((w) => w.id === workspaceId);
  const sess = (ws?.running_sessions || []).find((s) => s.id === sessionId);
  if (sess && (!_selectedSession || _selectedSession.sessionId !== sessionId)) {
    selectSession(workspaceId, sessionId, sess.tmux_name || "");
  }
  // Switch to session tab in case the user is on Results / Control Panel
  if (typeof switchEditorTab === "function") switchEditorTab("session", { skipSessionAttach: true });
  // Scroll to latest output (exits scrollback mode if active) and focus
  if (window.xtermBridge) {
    window.xtermBridge.scrollToBottom();
    setTimeout(() => window.xtermBridge.focus(), 50);
  }
}

// ---------------------------------------------------------------------------
// Recover a lost session (tmux died, Claude conversation still resumable)
// ---------------------------------------------------------------------------

function refreshWorkspacesSidebar() { fetchWorkspaces(); }

async function recoverSession(workspaceId, sessionId, tmuxName) {
  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/sessions/${sessionId}/recover`,
      { method: "POST" },
    );
    const data = await resp.json();
    if (data.success) {
      if (typeof showToast === "function") showToast("Session recovered", "success");
      // Re-select to attach the terminal
      selectSession(workspaceId, sessionId, tmuxName);
      // Refresh sidebar to clear "lost" status
      if (typeof refreshWorkspacesSidebar === "function") refreshWorkspacesSidebar();
    } else {
      if (typeof showToast === "function") showToast(data.error || "Recovery failed", "error");
    }
  } catch (e) {
    console.error("Failed to recover session:", e);
    if (typeof showToast === "function") showToast("Recovery failed", "error");
  }
}

async function stopCodingSession(workspaceId, sessionId) {
  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/sessions/${sessionId}/stop`,
      { method: "POST" },
    );
    const data = await resp.json();
    if (data.success) {
      if (typeof showToast === "function") showToast("Session stopped", "info");
      // Detach terminal if viewing this session
      if (_selectedSession && _selectedSession.sessionId === sessionId) {
        if (typeof detachTerminal === "function") detachTerminal();
        if (typeof showSessionEmpty === "function") showSessionEmpty();
      }
      fetchWorkspaces();
      if (_selectedWorkspace === workspaceId) selectWorkspace(workspaceId);
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to stop", "error");
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to stop session", "error");
  }
}

// Braille dot spinner used on any button while a wrap-up is in flight.
// Cycles roughly 10fps, matching the existing tool-indicator spinner cadence.
const _WRAPUP_SPINNER_FRAMES = ["\u280B","\u2819","\u2839","\u2838","\u283C","\u2834","\u2826","\u2827","\u2807","\u280F"];

function _startWrapupSpinner(el) {
  if (!el) return () => {};
  const originalHTML = el.innerHTML;
  const originalTitle = el.title;
  el.classList.add("is-wrapping-up");
  el.disabled = true;
  el.title = "Wrapping up...";
  let i = 0;
  const id = setInterval(() => {
    el.textContent = _WRAPUP_SPINNER_FRAMES[i % _WRAPUP_SPINNER_FRAMES.length];
    i += 1;
  }, 100);
  return () => {
    clearInterval(id);
    el.classList.remove("is-wrapping-up");
    el.disabled = false;
    el.innerHTML = originalHTML;
    el.title = originalTitle;
  };
}

async function completeCodingSession(workspaceId, sessionId) {
  // Spin the ✓ button while the backend injects a wrap-up prompt into the
  // live Claude agent and waits for its reply to stabilise (10–60s).
  const rowBtn = document.querySelector(
    `.session-row-complete[onclick*="${sessionId}"]`);
  const stopSpinner = _startWrapupSpinner(rowBtn);
  if (typeof showToast === "function") {
    showToast("Asking Claude to summarise the session...", "info");
  }
  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/sessions/${sessionId}/complete`,
      { method: "POST" },
    );
    const data = await resp.json();
    if (data.success) {
      // Session is STILL running — don't detach the terminal, don't kill
      // the session. The dock's Save button persists + ends the session;
      // its X (discard) clears the draft and the session keeps running.
      if (typeof window.addDraftToDock === "function") {
        window.addDraftToDock(workspaceId, sessionId, data.session_name, data.summary);
      }
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to wrap up", "error");
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to wrap up session", "error");
  } finally {
    stopSpinner();
  }
}

async function restartCodingSession(workspaceId, sessionId) {
  try {
    if (typeof showToast === "function") showToast("Restarting session...", "info");
    // Detach terminal if viewing this session
    if (_selectedSession && _selectedSession.sessionId === sessionId) {
      if (typeof detachTerminal === "function") detachTerminal();
    }
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/sessions/${sessionId}/restart`,
      { method: "POST" },
    );
    const data = await resp.json();
    if (data.success) {
      if (typeof showToast === "function") showToast(data.message || "Session restarted", "success");
      fetchWorkspaces();
      if (_selectedWorkspace === workspaceId) selectWorkspace(workspaceId);
      // Re-attach if this was the selected session
      if (_selectedSession && _selectedSession.sessionId === sessionId && data.tmux_name) {
        setTimeout(() => selectSession(workspaceId, sessionId, data.tmux_name), 500);
      }
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to restart", "error");
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to restart session", "error");
  }
}

async function recoverCodingSession(workspaceId, sessionId) {
  // Alias for recoverSession from workspace detail (uses same endpoint)
  const ws = _workspaces.find((w) => w.id === workspaceId);
  const sess = (ws?.running_sessions || []).find((s) => s.id === sessionId);
  const tmuxName = sess?.tmux_name || "";
  recoverSession(workspaceId, sessionId, tmuxName);
}

async function recoverAllSessions(workspaceId) {
  try {
    if (typeof showToast === "function") showToast("Recovering sessions...", "info");
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/recover`,
      { method: "POST" },
    );
    const data = await resp.json();
    if (data.success) {
      const msg = data.recovered > 0
        ? `Recovered ${data.recovered} session${data.recovered > 1 ? "s" : ""}${data.failed > 0 ? ` (${data.failed} failed)` : ""}`
        : "No sessions needed recovery";
      if (typeof showToast === "function") showToast(msg, data.recovered > 0 ? "success" : "info");
      fetchWorkspaces();
      if (workspaceId) selectWorkspace(workspaceId);
    } else {
      if (typeof showToast === "function") showToast("Recovery failed", "error");
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Recovery failed", "error");
  }
}

async function stopAllSessions(workspaceId) {
  try {
    if (typeof showToast === "function") showToast("Stopping idle sessions...", "info");
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/sessions/stop-all`,
      { method: "POST" },
    );
    const data = await resp.json();
    if (data.success) {
      const msg = data.stopped > 0
        ? `Stopped ${data.stopped} session${data.stopped > 1 ? "s" : ""}${data.skipped_working > 0 ? ` (${data.skipped_working} still working)` : ""}`
        : "No sessions to stop";
      if (typeof showToast === "function") showToast(msg, data.stopped > 0 ? "success" : "info");
      // Detach terminal if the selected session was stopped
      if (_selectedSession && _selectedSession.workspaceId === workspaceId) {
        if (typeof detachTerminal === "function") detachTerminal();
        if (typeof showSessionEmpty === "function") showSessionEmpty();
        _selectedSession = null;
      }
      fetchWorkspaces();
      if (_selectedWorkspace === workspaceId) selectWorkspace(workspaceId);
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to stop sessions", "error");
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to stop sessions", "error");
  }
}

// ---------------------------------------------------------------------------
// Select experiment child -> show experiment detail
// ---------------------------------------------------------------------------

function selectWorkspaceFromWorkspace(experimentId) {
  _selectedWorkspace = null;
  _selectedSession = null;
  // Update highlights in-place (selectProject triggers cross-tab sync which re-renders)
  workspacesSidebarEl.querySelectorAll(".sidebar-session-item").forEach((el) => el.classList.remove("active"));
  if (typeof selectProject === "function") {
    selectProject(experimentId);
  }
}

// ---------------------------------------------------------------------------
// Select workspace -> show detail in center column
// ---------------------------------------------------------------------------

let _wsSelectGeneration = 0; // prevent stale fetches from overwriting

async function selectWorkspace(workspaceId) {
  _selectedWorkspace = workspaceId;
  _selectedSession = null;
  const myGen = ++_wsSelectGeneration;

  if (typeof currentProjectId !== "undefined" && currentProjectId) {
    document.querySelectorAll("#experiments-sidebar .sidebar-item").forEach((el) => el.classList.remove("active"));
    // #experiment-detail is a shared container for workspace AND experiment
    // pages. If currentProjectId still points at the previously-viewed live
    // experiment, its SSE run_update handler (experiments.js) will keep
    // re-rendering the experiment detail into this container every few
    // seconds — yanking the user away from the workspace. Clear it.
    currentProjectId = null;
  }

  renderWorkspacesList(_workspaces);

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}`);
    if (!resp.ok) return;
    if (myGen !== _wsSelectGeneration) return; // stale — a newer call won
    const data = await resp.json();
    if (data.success) {
      renderWorkspaceDetail(data.workspace);
    }
  } catch (e) {
    console.error("Failed to load workspace:", e);
  }
}

// ---------------------------------------------------------------------------
// Render workspace detail in the editor area
// ---------------------------------------------------------------------------

// (race condition guarded by _wsSelectGeneration in selectWorkspace)

function renderWorkspaceDetail(ws) {
  const detailEl = document.getElementById("experiment-detail");
  const welcomeEl = document.getElementById("welcome");
  if (!detailEl) return;

  // Tear down any active canvas editor before overwriting the detail pane —
  // otherwise the canvas DOM is destroyed but its terminal/watcher state leaks.
  if (typeof window.destroyCanvasEditor === "function") window.destroyCanvasEditor();

  if (welcomeEl) welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");

  // Show control-panel view, but hide the editor tabs bar — projects
  // don't use Session/Results/Prompt tabs (agents are accessed from sidebar).
  // Guard on _selectedSession: a stale workspace-detail fetch (e.g. one
  // kicked off by launchCodingSession before the 500ms terminal-attach
  // timer fired) must not revert focus away from the session terminal.
  const cpView = document.getElementById("control-panel-view");
  if (cpView && cpView.classList.contains("hidden") && !_selectedSession) {
    if (typeof switchEditorTab === "function") switchEditorTab("control-panel");
  }
  const editorTabs = document.getElementById("editor-tabs");
  if (editorTabs) editorTabs.classList.add("hidden");

  const allSessions = ws.sessions || [];
  const runningSessions = allSessions.filter((s) => s.status === "running");
  const endedSessions = allSessions.filter((s) => s.status !== "running");
  const experiments = ws.experiments || [];
  const papers = ws.linked_papers || [];
  const resources = ws.resources || [];
  const tags = ws.tags || [];
  const hasRepos = (ws.repos || []).length > 0;

  // Check if project is essentially empty
  const isEmpty = !hasRepos && experiments.length === 0 && runningSessions.length === 0
    && papers.length === 0 && resources.length === 0;

  // -- Tags --
  const tagsHtml = tags.length > 0
    ? tags.map((t) => `<span class="workspace-tag">${escapeHtml(t)}</span>`).join("")
    : "";

  // -- Description (editable on double-click) --
  const descHtml = ws.description
    ? `<p class="workspace-detail-desc" id="workspace-desc-text" ondblclick="editWorkspaceDescription('${ws.id}', this)">${escapeHtml(ws.description)}</p>`
    : `<p class="workspace-detail-desc workspace-detail-desc-empty" id="workspace-desc-text" onclick="editWorkspaceDescription('${ws.id}', this)">Add a description...</p>`;

  // Build sections array — only include non-empty sections
  const sections = [];
  const blocks = {};

  // Check for lost sessions and show recovery banner
  const lostSessions = runningSessions.filter((s) => s.agent_status === "lost");
  if (lostSessions.length > 0) {
    sections.push(`
      <div class="workspace-recovery-banner">
        <span class="workspace-recovery-icon">&#x26A0;</span>
        <span>${lostSessions.length} session${lostSessions.length > 1 ? "s" : ""} lost (tmux died)</span>
        <button class="workspace-recovery-btn" onclick="recoverAllSessions('${ws.id}')">Recover all</button>
      </div>`);
  }

  // Group sessions by repo
  const sessionsByRepo = {};
  for (const s of allSessions) {
    const repo = s.repo || "unknown";
    if (!sessionsByRepo[repo]) sessionsByRepo[repo] = [];
    sessionsByRepo[repo].push(s);
  }

  // Coding Sessions — repos with inline running sessions
  if (hasRepos || runningSessions.length > 0) {
    const reposHtml = (ws.repos || []).map((r) => {
      const repoName = r.name || r.path.split("/").pop();
      const repoSessions = (sessionsByRepo[repoName] || []).filter((s) => s.status === "running");

      let sessionsHtml = "";
      if (repoSessions.length > 0) {
        sessionsHtml = `<div class="workspace-repo-sessions">` +
          repoSessions.map((s) => {
            const sessionName = s.agent_name || s.tmux_name || s.id;
            const since = s.started_at ? _relativeTime(s.started_at) : "";
            const status = s.agent_status || "unknown";
            const isLost = status === "lost";
            const isLive = status === "working" || status === "waiting";
            // Show dot only for live sessions (working/waiting/lost)
            const dotHtml = isLive || isLost
              ? `<span class="sidebar-status-dot ${status === "working" ? "status-working" : status === "waiting" ? "status-waiting" : "status-lost"}"></span>`
              : "";
            return `
              <div class="workspace-repo-session-row${isLost ? " session-lost" : ""}"
                   onclick="${isLost ? "" : `attachToCodingSession('${escapeHtml(s.id)}', '${ws.id}', '${escapeHtml(s.tmux_name || "")}')`}">
                ${dotHtml}
                <span class="workspace-repo-session-name">${escapeHtml(sessionName)}</span>
                <span class="workspace-repo-session-time">${since}</span>
                <span class="workspace-repo-session-actions">
                  ${isLost
                    ? `<button class="session-row-btn session-row-recover" onclick="event.stopPropagation();recoverCodingSession('${ws.id}','${escapeHtml(s.id)}')" title="Recover">&#x21BB;</button>`
                    : `<button class="session-row-btn session-row-restart" onclick="event.stopPropagation();restartCodingSession('${ws.id}','${escapeHtml(s.id)}')" title="Restart">&#x21BB;</button>`}
                  <button class="session-row-btn session-row-complete" onclick="event.stopPropagation();completeCodingSession('${ws.id}','${escapeHtml(s.id)}')" title="Complete">&#x2713;</button>
                  <button class="session-row-btn session-row-stop" onclick="event.stopPropagation();stopCodingSession('${ws.id}','${escapeHtml(s.id)}')" title="Stop">&#x25A0;</button>
                </span>
              </div>`;
          }).join("") + `</div>`;
      }

      return `
        <div class="workspace-repo-card${repoSessions.length > 0 ? " has-sessions" : ""}">
          <div class="workspace-repo-header">
            <span class="workspace-repo-name">${escapeHtml(repoName)}</span>
            <span class="workspace-repo-path">${escapeHtml(_shortenPath(r.path))}</span>
            <button class="workspace-item-remove" onclick="event.stopPropagation();unlinkRepo('${ws.id}','${escapeHtml(r.path)}')" title="Unlink repo">&times;</button>
          </div>
          ${sessionsHtml}
          <button class="workspace-launch-btn" onclick="launchCodingSession('${ws.id}', '${escapeHtml(repoName)}')">+ New session</button>
        </div>`;
    }).join("");
    blocks.coding = `
      <div class="workspace-detail-section">
        <h3>Coding Sessions</h3>
        <div class="workspace-repos-list">${reposHtml}</div>
        <button class="sidebar-header-btn new-btn" onclick="showAddRepoDialog('${ws.id}')" style="margin-top:8px">+ Link repo</button>
      </div>`;
  }

  // Work Sessions (canvas-based deliverable-oriented work items)
  {
    const canvases = (ws.canvases || []).filter((c) => c.status !== "archived");
    const workItemTypeIcons = {
      code: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>`,
      write: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
      survey: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
      data: `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`,
    };
    const workItemsHtml = canvases.length > 0 ? canvases.map((c) => {
      const category = _canvasCategory(c.type);
      const typeIcon = workItemTypeIcons[category] || workItemTypeIcons.write;
      const isLive = !!c.session_id && (ws.sessions || []).some((s) => s.id === c.session_id && s.status === "running");
      const liveDot = isLive ? `<span class="work-item-live-dot" title="Session running"></span>` : "";
      const done = c.status === "done";
      const sessionCount = (c.sessions || []).length;
      const sessionHint = sessionCount > 0 ? `<span class="work-item-session-count">${sessionCount} session${sessionCount !== 1 ? "s" : ""}</span>` : "";
      return `
        <div class="work-item-card${done ? " work-item-done" : ""}" onclick="_openWorkItem('${ws.id}','${escapeHtml(c.id)}')">
          <div class="work-item-card-header">
            <span class="work-item-type-icon work-item-icon-${category}">${typeIcon}</span>
            <span class="work-item-title">${escapeHtml(c.title || "Untitled")}</span>
            ${liveDot}
            <span class="work-item-type-badge work-item-type-${category}">${category}</span>
          </div>
          <div class="work-item-card-meta">
            ${sessionHint}
            ${c.entry ? `<span class="work-item-entry">${escapeHtml(c.entry)}</span>` : ""}
          </div>
          <div class="work-item-card-actions">
            ${!done ? `<button class="workspace-item-action-btn" onclick="event.stopPropagation();_openWorkItem('${ws.id}','${escapeHtml(c.id)}')" title="Open">Open</button>` : ""}
            ${!done ? `<button class="workspace-item-action-btn" onclick="event.stopPropagation();_completeWorkItem('${ws.id}','${escapeHtml(c.id)}')" title="Mark done">Done</button>` : ""}
          </div>
        </div>`;
    }).join("") : "";

    const newWorkItemHtml = `
      <div class="new-work-item-row">
        <span class="new-work-item-label">+ New work session</span>
        <div class="new-work-item-types">
          <button class="work-item-new-btn work-item-type-code" onclick="_createWorkItem('${ws.id}','code')" title="Code session">Code</button>
          <button class="work-item-new-btn work-item-type-write" onclick="_createWorkItem('${ws.id}','write')" title="Write session">Write</button>
          <button class="work-item-new-btn work-item-type-survey" onclick="_createWorkItem('${ws.id}','survey')" title="Survey session">Survey</button>
          <button class="work-item-new-btn work-item-type-data" onclick="_createWorkItem('${ws.id}','data')" title="Data session">Data</button>
        </div>
      </div>`;

    blocks.work = `
      <div class="workspace-detail-section">
        <h3>Work Sessions ${canvases.length > 0 ? `<span class="sidebar-count">${canvases.length}</span>` : ""}</h3>
        ${workItemsHtml || '<div class="sidebar-empty-hint">No work sessions yet</div>'}
        ${newWorkItemHtml}
      </div>`;
  }

  // Metrics dashboard (only if experiments have data)
  const summary = ws.summary || {};
  if (summary.total_runs > 0) {
    const metricsItems = [];
    metricsItems.push(`<span class="workspace-stat">${summary.total_experiments} experiment${summary.total_experiments !== 1 ? "s" : ""}</span>`);
    metricsItems.push(`<span class="workspace-stat">${summary.total_runs} run${summary.total_runs !== 1 ? "s" : ""}</span>`);
    if (summary.total_running > 0) {
      metricsItems.push(`<span class="workspace-stat workspace-stat-live">${summary.total_running} running</span>`);
    }
    if (summary.runs_improving > 0) {
      metricsItems.push(`<span class="workspace-stat">${summary.runs_improving} best run${summary.runs_improving !== 1 ? "s" : ""}</span>`);
    }
    const chartHtml = _renderProjectFrontierChart(experiments);
    blocks.metrics = `
      <div class="workspace-metrics-dashboard">
        ${chartHtml}
        <div class="workspace-stats-row">${metricsItems.join('<span class="workspace-stat-sep">&middot;</span>')}</div>
      </div>`;
  }

  // Experiments (rich cards)
  {
    // Compute a shared y-range so each experiment's sparkline sits at the
    // correct vertical position relative to its siblings — a higher-scoring
    // experiment should visually sit above a lower one, not auto-fit its
    // own min/max into the same strip.
    let _sharedYMin = Infinity, _sharedYMax = -Infinity;
    for (const e of experiments) {
      const h = Array.isArray(e.metric_history) ? e.metric_history : [];
      for (const v of h) {
        if (typeof v === "number" && Number.isFinite(v)) {
          if (v < _sharedYMin) _sharedYMin = v;
          if (v > _sharedYMax) _sharedYMax = v;
        }
      }
    }
    const _hasSharedRange = Number.isFinite(_sharedYMin) && Number.isFinite(_sharedYMax) && _sharedYMax > _sharedYMin;

    const expsHtml = experiments.length > 0 ? experiments.map((e) => {
      const runLabel = e.run_count > 0 ? `${e.run_count} run${e.run_count !== 1 ? "s" : ""}` : "no runs";
      const isRunning = (e.active_sessions || 0) > 0;
      const metricStr = e.best_metric_value != null && e.key_metric_name
        ? `<span class="workspace-xp-metric">${escapeHtml(e.key_metric_name)}: ${typeof e.best_metric_value === "number" ? e.best_metric_value.toFixed(4) : e.best_metric_value}</span>`
        : "";
      const lastAct = e.last_activity ? _relativeTime(e.last_activity) : "";
      // Always-on sparkline — empty strip when there's no data yet.
      const history = Array.isArray(e.metric_history) ? e.metric_history : [];
      const sparkOpts = { width: 72, height: 18 };
      if (_hasSharedRange) { sparkOpts.yMin = _sharedYMin; sparkOpts.yMax = _sharedYMax; }
      const sparkHtml = (history.length > 1 && typeof sparklineSvg === "function")
        ? `<span class="workspace-xp-spark">${sparklineSvg(history, history.length - 1, sparkOpts)}</span>`
        : `<span class="workspace-xp-spark workspace-xp-spark-empty" title="No runs yet"></span>`;
      // Harmonized status dot — same taxonomy the sidebar uses.
      const statusClass = isRunning ? "status-working" : "status-idle";
      const statusDot = `<span class="sidebar-status-dot ${statusClass}" title="${isRunning ? 'running' : 'idle'}"></span>`;
      return `
        <div class="workspace-experiment-card" onclick="if(typeof selectProject==='function')selectProject('${e.id}')">
          <div class="workspace-experiment-card-header">
            ${statusDot}
            <span class="workspace-experiment-card-name">${escapeHtml(e.name || e.id)}</span>
            ${sparkHtml}
            <button class="workspace-item-remove" onclick="event.stopPropagation();unlinkExperiment('${ws.id}','${e.id}')" title="Unlink">&times;</button>
          </div>
          <div class="workspace-experiment-card-meta">
            <span>${runLabel}</span>
            ${metricStr}
            ${lastAct ? `<span class="workspace-xp-time">${lastAct}</span>` : ""}
          </div>
        </div>`;
    }).join("") : '<div class="sidebar-empty-hint">No experiments linked</div>';

    blocks.experiments = `
      <div class="workspace-detail-section">
        <h3>Experiments <span class="sidebar-count">${experiments.length || ""}</span></h3>
        <div class="workspace-experiments-list">${expsHtml}</div>
        <div class="workspace-experiment-actions">
          <button class="sidebar-header-btn new-btn" onclick="if(typeof showNewExperimentWizard==='function')showNewExperimentWizard('${ws.id}')" style="margin-top:6px">+ New experiment</button>
          <button class="sidebar-header-btn new-btn" onclick="showLinkExperimentDialog('${ws.id}')" style="margin-top:6px">+ Link existing</button>
        </div>
      </div>`;
  }

  // Papers
  if (papers.length > 0) {
    const papersHtml = papers.map((ck) =>
      `<div class="workspace-paper-item">
        <span class="workspace-paper-citekey">${escapeHtml(ck)}</span>
        <button class="workspace-item-remove" onclick="unlinkPaper('${ws.id}','${escapeHtml(ck)}')" title="Unlink">&times;</button>
      </div>`
    ).join("");
    blocks.papers = `
      <div class="workspace-detail-section">
        <h3>Papers <span class="sidebar-count">${papers.length}</span></h3>
        <div class="workspace-papers-list">${papersHtml}</div>
        <button class="sidebar-header-btn new-btn" onclick="showLinkPaperDialog('${ws.id}')" style="margin-top:8px">+ Link paper</button>
      </div>`;
  }

  // Resources
  if (resources.length > 0) {
    const resourcesHtml = resources.map((r, i) => {
      const icon = _resourceIcon(r.type);
      const label = r.name || r.id || r.url || r.type;
      const urlAttr = r.url ? ` onclick="if(typeof require!=='undefined'){require('electron').shell.openExternal('${escapeHtml(r.url)}')}else{window.open('${escapeHtml(r.url)}')}"` : "";
      return `
        <div class="workspace-resource-item"${urlAttr}>
          <span class="workspace-resource-icon">${icon}</span>
          <span class="workspace-resource-label">${escapeHtml(label)}</span>
          <span class="workspace-resource-type">${escapeHtml(r.type || "")}</span>
          <button class="workspace-item-remove" onclick="event.stopPropagation();removeResource('${ws.id}',${i})" title="Remove">&times;</button>
        </div>`;
    }).join("");
    blocks.resources = `
      <div class="workspace-detail-section">
        <h3>Resources</h3>
        <div class="workspace-resources-list">${resourcesHtml}</div>
        <button class="sidebar-header-btn new-btn" onclick="showAddResourceDialog('${ws.id}')" style="margin-top:8px">+ Add resource</button>
      </div>`;
  }

  // Empty state: getting-started grid
  let getStartedHtml = "";
  if (isEmpty) {
    getStartedHtml = `
      <div class="workspace-get-started">
        <p class="workspace-get-started-label">Get started</p>
        <div class="workspace-get-started-grid">
          <button class="workspace-action-card" onclick="showAddRepoDialog('${ws.id}')">
            <span class="workspace-action-icon">&#x1F4C2;</span>
            <span class="workspace-action-title">Link a repository</span>
            <span class="workspace-action-desc">Connect a code repo to launch coding sessions</span>
          </button>
          <button class="workspace-action-card" onclick="if(typeof showNewExperimentWizard==='function')showNewExperimentWizard('${ws.id}')">
            <span class="workspace-action-icon">&#x1F9EA;</span>
            <span class="workspace-action-title">New experiment</span>
            <span class="workspace-action-desc">Set up an auto-research experiment</span>
          </button>
          <button class="workspace-action-card" onclick="showLinkPaperDialog('${ws.id}')">
            <span class="workspace-action-icon">&#x1F4D1;</span>
            <span class="workspace-action-title">Link papers</span>
            <span class="workspace-action-desc">Connect papers from your library</span>
          </button>
        </div>
      </div>`;
  }

  // Assemble sections in the v2 canonical order: chart/metrics → experiments
  // → work sessions → coding sessions → papers → resources. Experiments and
  // the chart come first because they answer "is the research improving?" —
  // the keystone question. Sessions are the vehicle and live below.
  for (const key of ["metrics", "experiments", "work", "coding", "papers", "resources"]) {
    if (blocks[key]) sections.push(blocks[key]);
  }

  detailEl.innerHTML = `
    <div class="workspace-detail">
      <div class="workspace-detail-header">
        <div class="workspace-detail-title-row">
          <h2 class="workspace-detail-name" data-name="${escapeHtml(ws.name)}" onclick="editWorkspaceName('${ws.id}', this)">${escapeHtml(ws.name)}<span class="workspace-name-edit-icon"></span></h2>
          ${ws.status && ws.status !== "active" ? `<span class="workspace-detail-status">${ws.status}</span>` : ""}
          <button class="workspace-header-menu-btn" onclick="toggleWorkspaceMenu('${ws.id}', this)" title="Workspace actions">&#x22EF;</button>
        </div>
        ${tagsHtml ? `<div class="workspace-tags-row">${tagsHtml}</div>` : ""}
        ${descHtml}
      </div>
      ${getStartedHtml}
      ${sections.join("")}
      <div class="workspace-detail-section workspace-notebook-section">
        <div class="workspace-section-header-row">
          <h3 class="workspace-section-title">Notebook</h3>
          <button class="workspace-notebook-add-btn" onclick="toggleWorkspaceNotebookForm('${ws.id}', '${escapeHtml(ws.name)}')">+ Note</button>
        </div>
        <div id="workspace-notebook-form" class="workspace-notebook-form hidden"></div>
        <div id="workspace-notebook-feed"><div class="sidebar-empty-hint">Loading…</div></div>
      </div>
    </div>
  `;

  // Load the notebook feed for this project
  if (typeof loadWorkspaceNotebook === "function") loadWorkspaceNotebook(ws.id, ws.name);

  const sessionTab = document.querySelector('.editor-tab[data-view="session"]');
  if (sessionTab) {
    sessionTab.classList.toggle("has-update", runningSessions.length > 0);
  }
}

// ---------------------------------------------------------------------------
// Resource type icons
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Emoji picker for projects
// ---------------------------------------------------------------------------

const _WORKSPACE_EMOJIS = [
  "\u{1F9EA}", "\u{1F52C}", "\u{1F4CA}", "\u{1F680}", "\u{1F916}", "\u{2697}\uFE0F",
  "\u{1F9E0}", "\u{1F4A1}", "\u{1F30D}", "\u{1F3AF}", "\u{1F4D0}", "\u{1F50D}",
  "\u{1F52D}", "\u{1F9F2}", "\u{1F4BB}", "\u{1F4D6}", "\u{2728}", "\u{1F525}",
  "\u{1F331}", "\u{1F3D7}\uFE0F", "\u{1F9EC}", "\u{1F4E1}", "\u{26A1}", "\u{1F300}",
];

let _emojiIndex = Math.floor(Math.random() * _WORKSPACE_EMOJIS.length);

function _nextWorkspaceEmoji() {
  _emojiIndex = (_emojiIndex + 1) % _WORKSPACE_EMOJIS.length;
  return _WORKSPACE_EMOJIS[_emojiIndex];
}

function _randomWorkspaceEmoji() {
  _emojiIndex = Math.floor(Math.random() * _WORKSPACE_EMOJIS.length);
  return _WORKSPACE_EMOJIS[_emojiIndex];
}

function cycleWorkspaceEmoji() {
  const btn = document.getElementById("new-workspace-emoji");
  if (!btn) return;

  // Toggle picker grid
  const existing = document.querySelector(".emoji-picker-grid");
  if (existing) { existing.remove(); return; }

  const grid = document.createElement("div");
  grid.className = "emoji-picker-grid";
  grid.innerHTML = _WORKSPACE_EMOJIS.map((e) =>
    `<button class="emoji-picker-item" onclick="pickWorkspaceEmoji('${e}')">${e}</button>`
  ).join("")
    + `<input class="emoji-picker-custom" type="text" placeholder="..." maxlength="2" title="Type any emoji" oninput="if(this.value.trim())pickWorkspaceEmoji(this.value.trim())">`;

  btn.parentElement.style.position = "relative";
  btn.parentElement.appendChild(grid);

  setTimeout(() => {
    const close = (ev) => {
      if (!grid.contains(ev.target) && ev.target !== btn) {
        grid.remove();
        document.removeEventListener("click", close);
      }
    };
    document.addEventListener("click", close);
  }, 0);
}

function pickWorkspaceEmoji(emoji) {
  const btn = document.getElementById("new-workspace-emoji");
  if (btn) btn.textContent = emoji;
  document.querySelector(".emoji-picker-grid")?.remove();
}

function _shortenPath(p) {
  if (!p) return p;
  const parts = p.split("/");
  // /Users/<username>/... → ~/...
  if (parts.length >= 3 && parts[1] === "Users") {
    return "~" + p.slice(("/Users/" + parts[2]).length);
  }
  return p;
}

function _resourceIcon(type) {
  const icons = {
    huggingface_model: "&#x1F917;",
    huggingface_dataset: "&#x1F4CA;",
    wandb: "&#x1F4C8;",
    github: "&#x1F4BB;",
    arxiv: "&#x1F4D1;",
    overleaf: "&#x1F4DD;",
    link: "&#x1F517;",
  };
  return icons[type] || "&#x1F517;";
}

// ---------------------------------------------------------------------------
// Relative time helper
// ---------------------------------------------------------------------------

function _relativeTime(isoString) {
  if (!isoString) return "";
  const now = Date.now();
  const then = new Date(isoString).getTime();
  if (isNaN(then)) return "";
  const diff = Math.max(0, now - then);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

function launchQuickSession(workspaceId) {
  const ws = _workspaces.find((w) => w.id === workspaceId);
  if (!ws || !ws.repos || ws.repos.length === 0) {
    selectWorkspace(workspaceId);
    return;
  }
  // List endpoint returns repos as flat strings, detail returns objects — normalise
  const repoNames = ws.repos.map((r) => typeof r === "string" ? r : (r.name || r.path.split("/").pop()));
  if (repoNames.length === 1) {
    launchCodingSession(workspaceId, repoNames[0]);
    return;
  }
  // Multiple repos — show picker
  document.querySelector(".modal-overlay.repo-picker-modal")?.remove();
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay repo-picker-modal";
  const repoItems = repoNames.map((name) => {
    return `<button class="repo-picker-item" onclick="this.closest('.modal-overlay').remove(); launchCodingSession('${workspaceId}', '${escapeHtml(name)}')">${escapeHtml(name)}</button>`;
  }).join("");
  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h2>New session in ${escapeHtml(ws.name)}</h2>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div class="repo-picker-list">${repoItems}</div>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  document.addEventListener("keydown", function _esc(e) {
    if (e.key === "Escape") { overlay.remove(); document.removeEventListener("keydown", _esc); }
  });
}

const HARNESS_MODELS = {
  claude: [
    { id: "claude-opus-4-7", label: "Opus 4.7" },
    { id: "claude-opus-4-6", label: "Opus 4.6" },
    { id: "claude-sonnet-4-6", label: "Sonnet 4.6" },
    { id: "claude-sonnet-4-5", label: "Sonnet 4.5" },
    { id: "claude-haiku-4-5", label: "Haiku 4.5" }
  ],
  gemini: [
    { id: "gemini-3.1-pro", label: "Gemini 3.1 Pro" },
    { id: "gemini-3.0-flash", label: "Gemini 3.0 Flash" }
  ]
};

function getModelsForHarness(harness) {
  return (HARNESS_MODELS[harness] || []).map(m => ({ value: m.id, label: m.label }));
}

async function launchCodingSession(workspaceId, repoName, model = null, agent = null) {
  if (!model) {
    if (typeof _showModal === "function") {
      _showModal({
        title: "Launch Coding Session",
        fields: [
          {
            id: "agent",
            label: "Agent",
            type: "select",
            options: [
              { value: "claude", label: "Claude Code" },
              { value: "gemini", label: "Gemini CLI" }
            ],
            value: "claude"
          },
          {
            id: "model",
            label: "Model",
            type: "select",
            options: getModelsForHarness("claude"),
            value: "claude-opus-4-7",
            dependsOn: "agent"
          }
        ],
        submitLabel: "Launch",
        onSubmit: (vals, overlay) => {
          overlay.remove();
          launchCodingSession(workspaceId, repoName, vals.model, vals.agent);
        }
      });
      return;
    }
    model = "claude-opus-4-7";
    agent = "claude";
  }

  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/sessions`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo: repoName, model: model, agent: agent }) }
    );
    const data = await resp.json();
    if (data.success) {
      if (typeof showToast === "function") showToast(data.message, "success");
      fetchWorkspaces();
      selectWorkspace(workspaceId);
      // Claim the new session eagerly. selectWorkspace just set
      // _selectedSession = null synchronously; reassign here so the
      // stale workspace-detail fetch it kicked off won't revert focus
      // away from the terminal we're about to attach (see the guard in
      // renderWorkspaceDetail).
      if (data.session_id && data.tmux_name) {
        _selectedSession = {
          workspaceId,
          sessionId: data.session_id,
          tmuxName: data.tmux_name,
        };
      }
      if (data.tmux_name) {
        setTimeout(() => {
          attachToCodingSession(data.session_id, workspaceId, data.tmux_name);
        }, 500);
      }
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to launch", "error");
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to launch session", "error");
  }
}

function attachToCodingSession(sessionId, workspaceId, tmuxName, workspaceName, agentName, canvasId) {
  if (!tmuxName) {
    if (typeof showToast === "function") showToast("No tmux session to attach", "error");
    return;
  }
  const terminalKey = `ws_${workspaceId}_${sessionId}`;
  if (typeof showTerminalForSession === "function") {
    showTerminalForSession(terminalKey, tmuxName, workspaceName, agentName, workspaceId, canvasId);
  }
}

// ---------------------------------------------------------------------------
// Modal dialog helper (replaces browser prompt())
// ---------------------------------------------------------------------------

function _showConfirm({ title, message, confirmLabel, danger, onConfirm }) {
  document.querySelector(".modal-overlay.workspace-modal")?.remove();

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay workspace-modal";
  const btnClass = danger ? "modal-btn-danger" : "modal-btn-submit";

  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h2>${title}</h2>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <p class="modal-message">${message}</p>
        <div class="modal-actions">
          <button class="modal-btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
          <button class="${btnClass}" id="modal-confirm-btn">${confirmLabel || "Confirm"}</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  document.addEventListener("keydown", function _esc(e) {
    if (e.key === "Escape") { overlay.remove(); document.removeEventListener("keydown", _esc); }
  });
  overlay.querySelector("#modal-confirm-btn").addEventListener("click", () => {
    overlay.remove();
    onConfirm();
  });
  setTimeout(() => overlay.querySelector("#modal-confirm-btn")?.focus(), 50);
}

function _showModal({ title, fields, submitLabel, onSubmit, extraHtml }) {
  // Remove any existing modal
  document.querySelector(".modal-overlay.workspace-modal")?.remove();

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay workspace-modal";

  function renderFields(currentFields) {
    const fieldHtml = currentFields.map((f) => {
      if (f.type === "select") {
        const optionsHtml = (f.options || []).map(opt =>
          `<option value="${opt.value}" ${opt.value === f.value ? "selected" : ""}>${opt.label}</option>`
        ).join("");
        return `
          <div class="setting-group">
            <label for="modal-${f.id}">${f.label}</label>
            ${f.hint ? `<div class="modal-field-hint">${f.hint}</div>` : ""}
            <select id="modal-${f.id}" class="modal-input">
              ${optionsHtml}
            </select>
          </div>
        `;
      }
      return `
        <div class="setting-group">
          <label for="modal-${f.id}">${f.label}</label>
          ${f.hint ? `<div class="modal-field-hint">${f.hint}</div>` : ""}
          <input type="text" id="modal-${f.id}" class="modal-input"
            placeholder="${f.placeholder || ""}" value="${f.value || ""}" ${f.autofocus ? "autofocus" : ""}>
        </div>
      `;
    }).join("");
    return fieldHtml;
  }

  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h2>${title}</h2>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div id="modal-fields-container">
          ${renderFields(fields)}
        </div>
        ${extraHtml || ""}
        <div class="modal-actions">
          <button class="modal-btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
          <button class="modal-btn-submit" id="modal-submit-btn">${submitLabel || "Add"}</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  document.addEventListener("keydown", function _esc(e) {
    if (e.key === "Escape") { overlay.remove(); document.removeEventListener("keydown", _esc); }
  });

  // Set up dynamic field updates (e.g., model list based on agent selection)
  for (const f of fields) {
    if (f.dependsOn) {
      const depField = document.getElementById(`modal-${f.dependsOn}`);
      if (depField) {
        depField.addEventListener("change", () => {
          const depValue = depField.value;
          const harness = f.dependsOn === "agent" ? depValue : null;
          if (harness && typeof getModelsForHarness === "function") {
            const newOptions = getModelsForHarness(harness);
            const modelSelect = document.getElementById(`modal-${f.id}`);
            if (modelSelect) {
              const currentValue = modelSelect.value;
              const defaultModel = newOptions[0]?.value || "";
              modelSelect.innerHTML = newOptions
                .map(opt => `<option value="${opt.value}">${opt.label}</option>`)
                .join("");
              const hasCurrentValue = newOptions.some(opt => opt.value === currentValue);
              modelSelect.value = hasCurrentValue ? currentValue : defaultModel;
            }
          }
        });
      }
    }
  }

  const submitBtn = overlay.querySelector("#modal-submit-btn");
  const submit = () => {
    if (submitBtn.disabled) return;
    const values = {};
    for (const f of fields) {
      values[f.id] = document.getElementById(`modal-${f.id}`)?.value.trim() || "";
    }
    // Disable button while processing
    submitBtn.disabled = true;
    submitBtn.textContent = "...";
    onSubmit(values, overlay);
  };

  submitBtn.addEventListener("click", submit);

  for (const f of fields) {
    const el = document.getElementById(`modal-${f.id}`);
    if (el) el.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  }

  const first = overlay.querySelector("input[autofocus]") || overlay.querySelector("input");
  if (first) setTimeout(() => first.focus(), 50);
}

// _showCompletionModal was a blocking overlay that destroyed in-flight
// drafts when a second wrapup finished (`.modal-overlay.completion-modal`
// + `?.remove()`). Replaced by the non-blocking drafts dock in
// drafts-dock.js — the caller now invokes window.addDraftToDock.

function showAddRepoDialog(workspaceId) {
  _showModal({
    title: "Link Repository",
    fields: [
      { id: "path", label: "Path", placeholder: "/path/to/repository", hint: "Absolute path to a local folder", autofocus: true },
    ],
    submitLabel: "Link",
    onSubmit: (vals, overlay) => {
      if (!vals.path) return;
      fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/repos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: vals.path }),
      }).then((r) => r.json()).then((data) => {
        if (data.success) {
          overlay.remove();
          selectWorkspace(workspaceId);
          fetchWorkspaces();
        } else {
          if (typeof showToast === "function") showToast(data.error || "Failed to link repo", "error");
        }
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Edit project name inline
// ---------------------------------------------------------------------------

function editWorkspaceName(workspaceId, el) {
  // Guard against double-click creating two inputs
  if (el.tagName === "INPUT") return;
  const current = el.dataset.name || el.textContent;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "workspace-name-edit-input";
  input.value = current;
  input.maxLength = 100;
  el.replaceWith(input);
  input.focus();
  input.select();

  let saved = false;
  const save = () => {
    if (saved) return;
    saved = true;
    const val = input.value.trim();
    if (!val || val === current) { selectWorkspace(workspaceId); return; }
    fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: val }),
    }).then((r) => {
      if (!r.ok) throw new Error("Failed");
      fetchWorkspaces(); selectWorkspace(workspaceId);
    }).catch(() => {
      if (typeof showToast === "function") showToast("Failed to rename workspace", "error");
      selectWorkspace(workspaceId);
    });
  };
  input.addEventListener("blur", save);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); save(); }
    if (e.key === "Escape") selectWorkspace(workspaceId);
  });
}

// ---------------------------------------------------------------------------
// Unlink repo
// ---------------------------------------------------------------------------

function unlinkRepo(workspaceId, repoPath) {
  _showConfirm({
    title: "Unlink Repository",
    message: "Remove this repository from the project? The folder itself won't be deleted.",
    confirmLabel: "Unlink",
    danger: true,
    onConfirm: async () => {
      try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/repos`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: repoPath }),
        });
        const data = await resp.json();
        if (data.success) {
          fetchWorkspaces();
          selectWorkspace(workspaceId);
        } else {
          if (typeof showToast === "function") showToast(data.error || "Failed to unlink", "error");
        }
      } catch (e) {
        if (typeof showToast === "function") showToast("Failed to unlink repo", "error");
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Unlink experiment from project
// ---------------------------------------------------------------------------

async function unlinkExperiment(workspaceId, experimentId) {
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/experiments`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ experiment_id: experimentId }),
    });
    const data = await resp.json();
    if (data.success) {
      selectWorkspace(workspaceId);
      if (typeof fetchExperimentsList === "function") fetchExperimentsList();
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to unlink experiment", "error");
  }
}

// ---------------------------------------------------------------------------
// Link existing experiment to project
// ---------------------------------------------------------------------------

async function showLinkExperimentDialog(workspaceId) {
  // Fetch all experiments not already linked to this workspace
  let allExperiments = [];
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/experiments/list`);
    if (resp.ok) {
      const data = await resp.json();
      allExperiments = (data.experiments || []).filter((e) => e.workspace_id !== workspaceId);
    }
  } catch (e) { /* ignore */ }

  if (allExperiments.length === 0) {
    if (typeof showToast === "function") showToast("No experiments available to link", "info");
    return;
  }

  // Build options for a modal with a select
  document.querySelector(".modal-overlay.workspace-modal")?.remove();
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay workspace-modal";

  const options = allExperiments.map((e) =>
    `<option value="${escapeHtml(e.id)}">${escapeHtml(e.name || e.id)} (${e.run_count || 0} runs)</option>`
  ).join("");

  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h2>Link Experiment</h2>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div class="setting-group">
          <label>Select an experiment to link to this project</label>
          <select id="modal-link-experiment" class="modal-input" style="font-family:inherit">${options}</select>
        </div>
        <div class="modal-actions">
          <button class="modal-btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
          <button class="modal-btn-submit" id="modal-link-btn">Link</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector("#modal-link-btn").addEventListener("click", async () => {
    const expId = document.getElementById("modal-link-experiment")?.value;
    if (!expId) return;
    try {
      const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/experiments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ experiment_id: expId }),
      });
      const data = await resp.json();
      if (data.success) {
        overlay.remove();
        selectWorkspace(workspaceId);
        fetchWorkspaces();
        if (typeof fetchExperimentsList === "function") fetchExperimentsList();
      } else {
        if (typeof showToast === "function") showToast(data.error || "Failed to link", "error");
      }
    } catch (e) {
      if (typeof showToast === "function") showToast("Failed to link experiment", "error");
    }
  });
}

// ---------------------------------------------------------------------------
// Activity timeline
// ---------------------------------------------------------------------------

async function loadActivityTimeline(workspaceId) {
  const container = document.getElementById("workspace-activity-timeline");
  if (!container) return;

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/activity`);
    if (!resp.ok) throw new Error("Failed");
    const data = await resp.json();
    const events = data.events || [];

    if (events.length === 0) {
      container.innerHTML = '<div class="sidebar-empty-hint">No activity yet</div>';
      return;
    }

    container.innerHTML = events.slice(0, 15).map((ev) => {
      const time = ev.at ? _relativeTime(ev.at) : "";
      const typeClass = ev.type.includes("run") ? "activity-run" : "activity-session";
      const expLabel = ev.experiment ? `<span class="activity-experiment">${escapeHtml(ev.experiment)}</span>` : "";
      return `
        <div class="activity-event ${typeClass}">
          <span class="activity-dot"></span>
          <span class="activity-label">${escapeHtml(ev.label)}</span>
          ${expLabel}
          <span class="activity-time">${time}</span>
        </div>`;
    }).join("");
  } catch (e) {
    container.innerHTML = '<div class="sidebar-empty-hint">Failed to load activity</div>';
  }
}

// ---------------------------------------------------------------------------
// Unlink paper
// ---------------------------------------------------------------------------

async function unlinkPaper(workspaceId, citekey) {
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/papers`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ citekey }),
    });
    const data = await resp.json();
    if (data.success) {
      selectWorkspace(workspaceId);
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to unlink paper", "error");
  }
}

// ---------------------------------------------------------------------------
// Delete project
// ---------------------------------------------------------------------------

function deleteWorkspace(workspaceId) {
  _showConfirm({
    title: "Delete Workspace",
    message: "Remove this workspace from Distillate? No files or repos will be deleted.",
    confirmLabel: "Delete",
    danger: true,
    onConfirm: async () => {
      try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}`, {
          method: "DELETE",
        });
        const data = await resp.json();
        if (data.success) {
          _selectedWorkspace = null;
          if (typeof liveMetrics === "object" && liveMetrics) delete liveMetrics[workspaceId];
          fetchWorkspaces();
          const detailEl = document.getElementById("experiment-detail");
          const welcomeEl = document.getElementById("welcome");
          if (detailEl) { detailEl.classList.add("hidden"); detailEl.innerHTML = ""; }
          if (welcomeEl) welcomeEl.classList.remove("hidden");
        }
      } catch (e) {
        if (typeof showToast === "function") showToast("Failed to delete workspace", "error");
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Workspace actions menu (three-dot)
// ---------------------------------------------------------------------------

function toggleWorkspaceMenu(workspaceId, btnEl) {
  // Remove any existing menu
  const existing = document.querySelector(".workspace-actions-menu");
  if (existing) { existing.remove(); return; }

  const menu = document.createElement("div");
  menu.className = "workspace-actions-menu";
  menu.innerHTML = `
    <button onclick="if(typeof showNewExperimentWizard==='function')showNewExperimentWizard('${workspaceId}');this.closest('.workspace-actions-menu').remove()">
      <span class="menu-item-label">New experiment</span>
      <span class="menu-item-desc">Set up an auto-research experiment</span>
    </button>
    <button onclick="showLinkExperimentDialog('${workspaceId}');this.closest('.workspace-actions-menu').remove()">
      <span class="menu-item-label">Link experiment</span>
      <span class="menu-item-desc">Attach an existing experiment</span>
    </button>
    <button onclick="showAddRepoDialog('${workspaceId}');this.closest('.workspace-actions-menu').remove()">
      <span class="menu-item-label">Link repository</span>
      <span class="menu-item-desc">Connect a local code folder</span>
    </button>
    <button onclick="showLinkPaperDialog('${workspaceId}');this.closest('.workspace-actions-menu').remove()">
      <span class="menu-item-label">Link paper</span>
      <span class="menu-item-desc">Reference a paper from your library</span>
    </button>
    <button onclick="showAddResourceDialog('${workspaceId}');this.closest('.workspace-actions-menu').remove()">
      <span class="menu-item-label">Add resource</span>
      <span class="menu-item-desc">HuggingFace, W&B, or any URL</span>
    </button>
    <button onclick="createCanvas('${workspaceId}');this.closest('.workspace-actions-menu').remove()">
      <span class="menu-item-label">New canvas</span>
      <span class="menu-item-desc">LaTeX, Markdown, or any editable document</span>
    </button>
    <div class="workspace-actions-menu-sep"></div>
    <button class="workspace-actions-menu-danger" onclick="deleteWorkspace('${workspaceId}')">Delete workspace</button>
  `;
  btnEl.parentElement.style.position = "relative";
  btnEl.parentElement.appendChild(menu);

  // Close on click outside
  setTimeout(() => {
    const close = (e) => { if (!menu.contains(e.target) && e.target !== btnEl) { menu.remove(); document.removeEventListener("click", close); } };
    document.addEventListener("click", close);
  }, 0);
}

// ---------------------------------------------------------------------------
// Edit description inline
// ---------------------------------------------------------------------------

function editWorkspaceDescription(workspaceId, el) {
  if (el.tagName === "TEXTAREA") return;
  const current = el.textContent === "Add a description..." ? "" : el.textContent;
  const input = document.createElement("textarea");
  input.className = "workspace-desc-edit-input";
  input.value = current;
  input.rows = 3;
  el.replaceWith(input);
  input.focus();

  let saved = false;
  const save = () => {
    if (saved) return;
    saved = true;
    const val = input.value.trim();
    fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: val }),
    }).then((r) => {
      if (!r.ok) throw new Error("Failed");
      selectWorkspace(workspaceId);
    }).catch(() => {
      if (typeof showToast === "function") showToast("Failed to save description", "error");
      selectWorkspace(workspaceId);
    });
  };
  input.addEventListener("blur", save);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save(); }
    if (e.key === "Escape") selectWorkspace(workspaceId);
  });
}

// ---------------------------------------------------------------------------
// Link paper dialog
// ---------------------------------------------------------------------------

function showLinkPaperDialog(workspaceId) {
  _showModal({
    title: "Link Paper",
    fields: [
      { id: "citekey", label: "Paper", placeholder: "e.g. vaswani2017attention or Attention Is All You Need", hint: "Citekey or title from your library", autofocus: true },
    ],
    submitLabel: "Link",
    onSubmit: (vals, overlay) => {
      if (!vals.citekey) return;
      fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/papers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ citekey: vals.citekey }),
      }).then((r) => r.json()).then((data) => {
        if (data.success) {
          overlay.remove();
          selectWorkspace(workspaceId);
        } else {
          if (typeof showToast === "function") showToast(data.error || "Failed to link paper", "error");
        }
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Add resource dialog
// ---------------------------------------------------------------------------

function showAddResourceDialog(workspaceId) {
  let container = document.querySelector(".workspace-resources-list");
  // If section isn't rendered (no resources yet), inject one
  if (!container) {
    const detailEl = document.getElementById("experiment-detail");
    if (!detailEl) return;
    const section = document.createElement("div");
    section.className = "workspace-detail-section";
    section.innerHTML = `<h3>Resources</h3><div class="workspace-resources-list"></div>`;
    const quickAdd = detailEl.querySelector(".workspace-quick-add");
    if (quickAdd) quickAdd.before(section); else detailEl.querySelector(".workspace-detail")?.appendChild(section);
    container = section.querySelector(".workspace-resources-list");
  }
  if (container.querySelector(".resource-add-form")) return;

  const form = document.createElement("div");
  form.className = "resource-add-form";
  form.innerHTML = `
    <div class="resource-form-row">
      <select class="resource-form-type">
        <option value="link">Link</option>
        <option value="huggingface_model">HuggingFace Model</option>
        <option value="huggingface_dataset">HuggingFace Dataset</option>
        <option value="wandb">Weights & Biases</option>
        <option value="github">GitHub</option>
        <option value="arxiv">ArXiv</option>
        <option value="overleaf">Overleaf</option>
      </select>
    </div>
    <div class="resource-form-row">
      <input type="text" class="resource-form-name" placeholder="Name (optional)">
    </div>
    <div class="resource-form-row">
      <input type="text" class="resource-form-url" placeholder="URL or identifier">
    </div>
    <div class="resource-form-actions">
      <button class="sidebar-header-btn" onclick="submitResource('${workspaceId}', this.closest('.resource-add-form'))">Add</button>
      <button class="sidebar-header-btn" onclick="this.closest('.resource-add-form').remove()">Cancel</button>
    </div>
  `;
  container.parentElement.insertBefore(form, container.parentElement.querySelector(".new-btn"));
}

async function submitResource(workspaceId, formEl) {
  const type = formEl.querySelector(".resource-form-type").value;
  const name = formEl.querySelector(".resource-form-name").value.trim();
  const url = formEl.querySelector(".resource-form-url").value.trim();
  if (!url) return;

  const btn = formEl.querySelector(".sidebar-header-btn");
  if (btn) { btn.disabled = true; btn.textContent = "..."; }

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/resources`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, name: name || undefined, url }),
    });
    if (!resp.ok) throw new Error("Failed");
    const data = await resp.json();
    if (data.success) {
      selectWorkspace(workspaceId);
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to add resource", "error");
      if (btn) { btn.disabled = false; btn.textContent = "Add"; }
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to add resource", "error");
    if (btn) { btn.disabled = false; btn.textContent = "Add"; }
  }
}

async function removeResource(workspaceId, index) {
  try {
    await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/resources/${index}`, {
      method: "DELETE",
    });
    selectWorkspace(workspaceId);
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to remove resource", "error");
  }
}

// ---------------------------------------------------------------------------
// Canvases (plural resources on a project)
// ---------------------------------------------------------------------------

/** Render a single canvas card for the project detail Canvases section. */
function _renderCanvasCard(workspaceId, canvas, runningSessions = []) {
  const type = canvas.type || "plain";
  const typeLabel = type === "latex" ? "LaTeX" : type === "markdown" ? "Markdown" : "Text";
  const lastCompile = canvas.last_compile;
  let statusHtml = "";
  if (type === "latex") {
    if (lastCompile && lastCompile.ok) {
      const ago = _relativeTime(lastCompile.at);
      statusHtml = `<span class="canvas-card-status success">Built ${ago}</span>`;
    } else if (lastCompile && !lastCompile.ok) {
      const n = lastCompile.error_count || 0;
      statusHtml = `<span class="canvas-card-status error">Build failed · ${n} error${n === 1 ? "" : "s"}</span>`;
    } else {
      statusHtml = `<span class="canvas-card-status">Not compiled yet</span>`;
    }
  }

  const sessionId = canvas.session_id || "";
  const liveSession = sessionId
    ? runningSessions.find((s) => s.id === sessionId && s.status === "running")
    : null;
  const sessionDot = liveSession
    ? `<span class="canvas-session-dot" title="Session running (${liveSession.agent_status || "unknown"})"></span>`
    : "";

  const sessionBtnHtml = liveSession
    ? `<button class="sidebar-header-btn" onclick="event.stopPropagation();attachCanvasSession('${workspaceId}','${canvas.id}')">Attach</button>`
    : `<button class="sidebar-header-btn" onclick="event.stopPropagation();launchCanvasSession('${workspaceId}','${canvas.id}')">Work on this</button>`;

  return `
    <div class="canvas-card" onclick="openCanvasInline('${workspaceId}','${canvas.id}')">
      <div class="canvas-card-main">
        <div class="canvas-card-title">${sessionDot}${escapeHtml(canvas.title || "Untitled")}</div>
        <div class="canvas-card-meta">
          <span class="canvas-card-badge">${typeLabel}</span>
          ${statusHtml}
        </div>
      </div>
      <div class="canvas-card-actions">
        ${sessionBtnHtml}
        <button class="canvas-card-menu-btn" onclick="event.stopPropagation();toggleCanvasCardMenu('${workspaceId}','${canvas.id}',this)" title="More actions">⋯</button>
      </div>
    </div>
  `;
}

/** Dropdown menu on the card's ⋯ button: rename, delete. */
function toggleCanvasCardMenu(workspaceId, canvasId, btnEl) {
  document.querySelectorAll(".canvas-card-dropdown").forEach((el) => el.remove());
  const menu = document.createElement("div");
  menu.className = "canvas-card-dropdown";
  menu.innerHTML = `
    <button onclick="event.stopPropagation();renameCanvas('${workspaceId}','${canvasId}')">Rename</button>
    <button class="danger" onclick="event.stopPropagation();deleteCanvas('${workspaceId}','${canvasId}')">Delete</button>
  `;
  btnEl.parentElement.style.position = "relative";
  btnEl.parentElement.appendChild(menu);
  setTimeout(() => {
    const close = (e) => {
      if (!menu.contains(e.target) && e.target !== btnEl) {
        menu.remove();
        document.removeEventListener("click", close);
      }
    };
    document.addEventListener("click", close);
  }, 0);
}

/** Create a new canvas — title + type picker + detect existing files. */
function createCanvas(workspaceId) {
  _showModal({
    title: "New Canvas",
    fields: [
      { id: "title", label: "Title", placeholder: "e.g. Main paper, System card, README", autofocus: true },
    ],
    extraHtml: `
      <div class="wizard-field">
        <label>Type</label>
        <div class="canvas-type-picker-inline">
          <label class="canvas-type-radio">
            <input type="radio" name="canvas-type" value="latex" checked>
            <span>LaTeX</span>
            <small>Papers, system cards, technical reports</small>
          </label>
          <label class="canvas-type-radio">
            <input type="radio" name="canvas-type" value="markdown">
            <span>Markdown</span>
            <small>READMEs, blog posts, lab notes</small>
          </label>
        </div>
      </div>
      <div class="wizard-field canvas-detected-wrap hidden" id="canvas-detected-wrap">
        <label>Existing documents in this project</label>
        <div class="canvas-detected-hint">Claude drafted these already — click to register one instead of scaffolding a new blank canvas.</div>
        <div class="canvas-detected-list" id="canvas-detected-list"></div>
      </div>
    `,
    submitLabel: "Create",
    onSubmit: async (vals, overlay) => {
      const title = (vals.title || "").trim();
      if (!title) return;
      const typeEl = overlay.querySelector('input[name="canvas-type"]:checked');
      const type = typeEl ? typeEl.value : "latex";
      try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/canvases`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, type }),
        });
        const data = await resp.json();
        if (!data.ok) {
          if (data.error === "root_path_required") {
            if (typeof showToast === "function") showToast(
              "Canvases need a project folder. Link a repo to this project first.",
              "error",
            );
          } else {
            if (typeof showToast === "function") showToast(data.error || "Failed to create", "error");
          }
          const submitBtn = overlay.querySelector("#modal-submit-btn");
          if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = "Create"; }
          return;
        }
        overlay.remove();
        await selectWorkspace(workspaceId);
        setTimeout(() => openCanvasInline(workspaceId, data.canvas.id), 150);
      } catch (e) {
        if (typeof showToast === "function") showToast("Failed to create canvas", "error");
      }
    },
  });

  _fetchAndRenderDetectedCanvases(workspaceId);
}

async function _fetchAndRenderDetectedCanvases(workspaceId) {
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/canvases/detect`);
    const data = await resp.json();
    if (!data.ok || !Array.isArray(data.candidates) || data.candidates.length === 0) return;
    const wrap = document.getElementById("canvas-detected-wrap");
    const list = document.getElementById("canvas-detected-list");
    if (!wrap || !list) return;
    list.innerHTML = data.candidates.map((c, i) => `
      <div class="canvas-detected-row" data-index="${i}">
        <div class="canvas-detected-main">
          <div class="canvas-detected-title">${escapeHtml(c.title || c.entry)}</div>
          <div class="canvas-detected-path">${escapeHtml(c.rel || `${c.dir}/${c.entry}`)} · ${c.type}</div>
        </div>
        <button class="sidebar-header-btn canvas-detected-use" data-index="${i}">Use this</button>
      </div>
    `).join("");
    wrap.classList.remove("hidden");
    list.querySelectorAll(".canvas-detected-use").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.index, 10);
        const candidate = data.candidates[idx];
        if (candidate) _importDetectedCanvas(workspaceId, candidate);
      });
    });
  } catch (e) {
    console.debug("[canvas] detect failed:", e);
  }
}

async function _importDetectedCanvas(workspaceId, candidate) {
  const overlay = document.querySelector(".modal-overlay.workspace-modal");
  const titleInput = document.getElementById("modal-title");
  const title = (titleInput?.value || "").trim() || candidate.title || candidate.entry;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/canvases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        import_path: candidate.path,
      }),
    });
    const data = await resp.json();
    if (!data.ok) {
      if (typeof showToast === "function") showToast(data.error || "Import failed", "error");
      return;
    }
    overlay?.remove();
    if (typeof showToast === "function") showToast("Registered as canvas", "success");
    await selectWorkspace(workspaceId);
    setTimeout(() => openCanvasInline(workspaceId, data.canvas.id), 150);
  } catch (e) {
    if (typeof showToast === "function") showToast("Import failed", "error");
  }
}

/** Rename a canvas. */
async function renameCanvas(workspaceId, canvasId) {
  _showModal({
    title: "Rename Canvas",
    fields: [
      { id: "title", label: "Title", autofocus: true },
    ],
    submitLabel: "Rename",
    onSubmit: async (vals, overlay) => {
      const title = (vals.title || "").trim();
      if (!title) return;
      try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/canvases/${canvasId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title }),
        });
        const data = await resp.json();
        if (data.ok) {
          overlay.remove();
          selectWorkspace(workspaceId);
        } else {
          if (typeof showToast === "function") showToast(data.error || "Rename failed", "error");
        }
      } catch (e) {
        if (typeof showToast === "function") showToast("Rename failed", "error");
      }
    },
  });
}

/** Remove a canvas from the project (files on disk stay). */
async function deleteCanvas(workspaceId, canvasId) {
  _showConfirm({
    title: "Delete Canvas?",
    message: "The canvas card will be removed from this project. Files on disk are left untouched.",
    confirmLabel: "Delete",
    danger: true,
    onConfirm: async () => {
      try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/canvases/${canvasId}`, {
          method: "DELETE",
        });
        const data = await resp.json();
        if (data.ok) {
          selectWorkspace(workspaceId);
        } else {
          if (typeof showToast === "function") showToast(data.error || "Delete failed", "error");
        }
      } catch (e) {
        if (typeof showToast === "function") showToast("Delete failed", "error");
      }
    },
  });
}

/** Open a canvas by selecting its linked session, or launching a new one. */
async function openCanvasInline(workspaceId, canvasId) {
  // Find a running session linked to this canvas
  const ws = _workspaces.find((w) => w.id === workspaceId);
  const sess = (ws?.running_sessions || []).find((s) => s.canvas_id === canvasId);
  if (sess && sess.tmux_name) {
    selectSession(workspaceId, sess.id, sess.tmux_name);
    return;
  }
  // No running session — launch one
  await launchCanvasSession(workspaceId, canvasId);
}

/** Launch a new writing session for a canvas. */
async function launchCanvasSession(workspaceId, canvasId) {
  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${workspaceId}/canvases/${canvasId}/sessions`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }
    );
    const data = await resp.json();
    if (data.success) {
      if (typeof showToast === "function") showToast("Writing session started", "success");
      fetchWorkspaces();
      // Claim the session and attach after a brief delay for tmux to spin up
      _selectedSession = { workspaceId, sessionId: data.session_id, tmuxName: data.tmux_name };
      setTimeout(() => {
        attachToCodingSession(data.session_id, workspaceId, data.tmux_name, "", "", canvasId);
      }, 500);
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to launch", "error");
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to launch writing session", "error");
  }
}

/** Attach to the existing session for a canvas. */
function attachCanvasSession(workspaceId, canvasId) {
  openCanvasInline(workspaceId, canvasId);
}

// Expose globally so inline HTML handlers can reach them
window.openCanvasInline = openCanvasInline;
window.launchCanvasSession = launchCanvasSession;
window.attachCanvasSession = attachCanvasSession;
window.createCanvas = createCanvas;
window.renameCanvas = renameCanvas;
window.deleteCanvas = deleteCanvas;
window.toggleCanvasCardMenu = toggleCanvasCardMenu;

// ---------------------------------------------------------------------------
// New project creation form
// ---------------------------------------------------------------------------

if (newWorkspaceBtn) {
  newWorkspaceBtn.addEventListener("click", () => {
    showNewWorkspaceForm();
  });
}

function showNewWorkspaceForm() {
  const detailEl = document.getElementById("experiment-detail");
  const welcomeEl = document.getElementById("welcome");
  if (!detailEl) return;

  if (welcomeEl) welcomeEl.classList.add("hidden");
  detailEl.classList.remove("hidden");

  // Ensure control-panel-view is visible (it may be hidden if the user was
  // viewing a session terminal).  No _selectedSession guard here — this is
  // an explicit user action so we always switch.
  const cpView = document.getElementById("control-panel-view");
  if (cpView && cpView.classList.contains("hidden")) {
    if (typeof switchEditorTab === "function") switchEditorTab("control-panel");
  }
  const editorTabs = document.getElementById("editor-tabs");
  if (editorTabs) editorTabs.classList.add("hidden");

  detailEl.innerHTML = `
    <div class="new-experiment-wizard">
      <h3>New Workspace</h3>

      <div class="wizard-field">
        <label>Name</label>
        <div class="workspace-name-with-emoji">
          <button class="workspace-emoji-picker" id="new-workspace-emoji" onclick="cycleWorkspaceEmoji()" title="Click to change emoji">${_randomWorkspaceEmoji()}</button>
          <input type="text" id="new-workspace-name" placeholder="e.g. Efficient Attention Mechanisms" autofocus>
        </div>
      </div>

      <div class="wizard-field">
        <label>Description</label>
        <textarea id="new-workspace-desc" placeholder="What is this workspace about?" rows="3"></textarea>
      </div>

      <div class="wizard-field">
        <label>Root folder (optional)</label>
        <div style="display:flex;gap:6px">
          <input type="text" id="new-workspace-root" placeholder="/path/to/project" style="flex:1">
          <button class="wizard-btn-cancel" onclick="browseWorkspaceFolder()" style="white-space:nowrap;flex:none;padding:0 12px">Browse</button>
        </div>
      </div>

      <div class="wizard-field">
        <label>Tags (comma-separated)</label>
        <input type="text" id="new-workspace-tags" placeholder="e.g. nlp, transformers, efficiency">
      </div>

      <div class="wizard-field">
        <label>Initial repository (optional)</label>
        <input type="text" id="new-workspace-repo" placeholder="/path/to/repo or leave blank">
      </div>

      <div class="wizard-actions">
        <button class="wizard-btn-cancel" onclick="cancelNewWorkspace()">Cancel</button>
        <button class="wizard-btn-create" onclick="submitNewWorkspace()" id="create-workspace-btn">Create Workspace</button>
      </div>
    </div>
  `;

  const nameInput = document.getElementById("new-workspace-name");
  if (nameInput) nameInput.focus();
}

async function browseWorkspaceFolder() {
  const input = document.getElementById("new-workspace-root");
  if (window.nicolas?.selectDirectory) {
    const dir = await window.nicolas.selectDirectory("Select project folder");
    if (dir && input) input.value = dir;
  } else if (input) {
    input.focus();
  }
}

function cancelNewWorkspace() {
  const detailEl = document.getElementById("experiment-detail");
  const welcomeEl = document.getElementById("welcome");
  if (detailEl) detailEl.classList.add("hidden");
  if (welcomeEl) welcomeEl.classList.remove("hidden");
}

async function submitNewWorkspace() {
  const rawName = document.getElementById("new-workspace-name")?.value.trim();
  const emoji = document.getElementById("new-workspace-emoji")?.textContent.trim() || "";
  const name = emoji && rawName ? `${emoji} ${rawName}` : rawName;
  const description = document.getElementById("new-workspace-desc")?.value.trim() || "";
  const rootPath = document.getElementById("new-workspace-root")?.value.trim() || "";
  const tagsRaw = document.getElementById("new-workspace-tags")?.value.trim() || "";
  const repoPath = document.getElementById("new-workspace-repo")?.value.trim() || "";
  const btn = document.getElementById("create-workspace-btn");

  if (!name) {
    if (typeof showToast === "function") showToast("Workspace name is required", "error");
    return;
  }

  if (btn) { btn.disabled = true; btn.textContent = "Creating..."; }

  const tags = tagsRaw ? tagsRaw.split(",").map((t) => t.trim()).filter(Boolean) : [];
  const repos = repoPath ? [repoPath] : [];

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description, root_path: rootPath, tags, repos }),
    });
    const data = await resp.json();
    if (data.success) {
      if (typeof showToast === "function") showToast(`Workspace "${name}" created`, "success");
      await fetchWorkspaces();
      selectWorkspace(data.workspace_id);
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to create workspace", "error");
      if (btn) { btn.disabled = false; btn.textContent = "Create Workspace"; }
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to create workspace", "error");
    if (btn) { btn.disabled = false; btn.textContent = "Create Workspace"; }
  }
}

// Fetch workspaces when the app loads (if projects tab is active)
if (_activeSidebarView === "workspaces") {
  fetchWorkspaces();
}

// No periodic full fetch — workspaces refresh on tab switch and user actions.
// Only the lightweight status dot poll runs on a timer (below).

// Lightweight status + name poll every 1s — updates dots and names in-place
// Runs on both Projects and Agents views
function _statusClass(status) {
  return status === "working" ? "status-working"
    : status === "waiting" ? "status-waiting"
    : status === "idle" ? "status-idle"
    : status === "lost" ? "status-lost"
    : status === "completed" ? "status-completed" : "status-unknown";
}

function _updateSessionDotsInContainer(container, sessions) {
  if (!container) return;

  // Step 1: update each session row's dot and name from live poll data.
  container.querySelectorAll(".sidebar-session-item").forEach((el) => {
    const onclick = el.getAttribute("onclick") || "";
    const match = onclick.match(/selectSession\('([^']+)',\s*'([^']+)'/);
    if (!match) return;
    const key = `${match[1]}/${match[2]}`;
    const info = sessions[key];
    if (!info) return;
    const dot = el.querySelector(".sidebar-status-icon");
    if (dot) {
      const cls = _statusClass(info.status);
      dot.className = `sidebar-status-icon ${cls}`;
      dot.textContent = info.status === "completed" ? "\u2713" : "\u25CF";
    }
    // Remember the live status on the element for step 2 (summary recompute)
    el.dataset.liveStatus = info.status || "";
    if (info.name) {
      const nameEl = el.querySelector(".sidebar-session-name");
      if (nameEl && nameEl.textContent !== info.name) {
        nameEl.textContent = info.name;
      }
    }

    // Bell notification: agent stopped working (→ idle at prompt, or → waiting for input)
    // Bell notification: agent needs user input (entered "waiting" = orange dot)
    const prevStatus = _prevSessionStatus[key];
    const newStatus = info.status;
    if (prevStatus !== "waiting" && newStatus === "waiting") {
      const isSelected = _selectedSession &&
        _selectedSession.workspaceId === match[1] &&
        _selectedSession.sessionId === match[2];
      if (!isSelected) {
        sessionDoneBells.add(key);
        if (sidebarLeft?.classList.contains("collapsed")) {
          const btn = document.querySelector('.activity-btn[data-pane="sidebar-left"]');
          if (btn) btn.classList.add("has-notification");
        }
      }
    }
    // Clear bell when session leaves the waiting state (user attended OR agent resumed)
    if (newStatus !== "waiting") sessionDoneBells.delete(key);
    _prevSessionStatus[key] = newStatus;

    // Render or remove bell element
    const existingBell = el.querySelector(".sidebar-session-bell");
    if (sessionDoneBells.has(key)) {
      if (!existingBell) {
        const bell = document.createElement("span");
        bell.className = "sidebar-session-bell";
        bell.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M8 1.5a.5.5 0 0 1 .5.5v.6A4.5 4.5 0 0 1 12.5 7v2.5l1 1.5H2.5l1-1.5V7a4.5 4.5 0 0 1 4-4.4V2a.5.5 0 0 1 .5-.5ZM6.5 13a1.5 1.5 0 0 0 3 0" fill="var(--warm)"/></svg>`;
        bell.title = "Done — click to review";
        el.appendChild(bell);
      }
    } else if (existingBell) {
      existingBell.remove();
    }
  });

  // Step 2: recompute every workspace's summary header from the live dot
  // classes. We iterate over ALL groups (not just ones that got updates)
  // because a session could transition out of "waiting" and disappear from
  // the poll payload, leaving the old summary stuck otherwise.
  container.querySelectorAll(".sidebar-workspace-group").forEach((group) => {
    const summaryEl = group.querySelector(".sidebar-status-summary");
    if (!summaryEl) return;

    const counts = {};
    group.querySelectorAll(".sidebar-session-item").forEach((el) => {
      // Prefer the dataset we just set; fall back to reading the dot class
      // so workspaces without fresh poll data still get cleaned up.
      let status = el.dataset.liveStatus;
      if (!status) {
        const dot = el.querySelector(".sidebar-status-icon");
        const cls = dot && [...dot.classList].find((c) => c.startsWith("status-"));
        status = cls ? cls.replace("status-", "") : "unknown";
      }
      counts[status] = (counts[status] || 0) + 1;
    });

    const parts = [];
    if (counts.working)   parts.push(`<span class="status-summary-working">${counts.working} working</span>`);
    if (counts.waiting)   parts.push(`<span class="status-summary-waiting">${counts.waiting} waiting</span>`);
    if (counts.completed) parts.push(`<span class="status-summary-completed">${counts.completed} done</span>`);
    if (counts.lost)      parts.push(`<span class="status-summary-lost">${counts.lost} error</span>`);
    if (counts.idle)      parts.push(`<span class="status-summary-idle">${counts.idle} idle</span>`);
    summaryEl.innerHTML = parts.join(" \u00b7 ");
  });
}

// Long-lived agents use a flat agent-id key and a different DOM shape
// (.agent-item[data-agent-id]), so they need their own updater.
function _updateAgentDotsInContainer(container, agents) {
  if (!container) return;
  container.querySelectorAll(".agent-item[data-agent-id]").forEach((el) => {
    const aid = el.getAttribute("data-agent-id");
    const info = agents[aid];
    if (!info) return;
    const dot = el.querySelector(".sidebar-status-icon");
    if (!dot) return;
    const cls = _statusClass(info.status);
    dot.className = `sidebar-status-icon ${cls}`;
    dot.textContent = info.status === "completed" ? "\u2713" : "\u25CF";
  });
}

// Status polling: updates sidebar dots AND macOS menu bar tray icon.
// Always runs — sidebar is visible regardless of what's in focus.
// Backend uses batched tmux calls + parallel capture-pane, so it
// finishes in ~100ms even with 15+ sessions, making 1s polling viable.
let _statusPollInFlight = false;
setInterval(async () => {
  if (_statusPollInFlight) return;  // skip if previous poll still running
  // Skip while a terminal attach is in flight. agent-status issues tmux
  // list-panes + capture-pane on the shared server; those wake the server
  // mid-attach and flush the pending pane redraw as a chunked burst —
  // visible as "view scrolls through history." Set by attachToTerminalSession.
  if (window._attachSettlingUntil && Date.now() < window._attachSettlingUntil) return;
  _statusPollInFlight = true;
  try {
    const [resp, nResp] = await Promise.all([
      fetch(`http://127.0.0.1:${serverPort}/workspaces/agent-status`),
      fetch(`http://127.0.0.1:${serverPort}/nicolas/state`).catch(() => null),
    ]);
    if (!resp.ok) return;
    const data = await resp.json();
    const sessions = data.sessions || {};
    const agents = data.agents || {};

    // Backend is authoritative for "did Nicolas finish a turn?". If it's
    // idle and the user hasn't focused Nicolas, raise the tray bell —
    // covers cases the renderer-local turn_end handler might miss
    // (window reload, multi-window, backend restart mid-turn).
    if (nResp && nResp.ok) {
      try {
        const { status } = await nResp.json();
        if (status === "idle" && _activeSidebarView !== "nicolas") {
          nicolasWaiting = true;
        }
      } catch {}
    }

    // Coding sessions live in both sidebars (some builds surface them in
    // Agents too), so update both containers with the session payload.
    _updateSessionDotsInContainer(workspacesSidebarEl, sessions);
    _updateSessionDotsInContainer(document.getElementById("agents-sidebar"), sessions);
    // Long-lived agents are only rendered in the Agents sidebar.
    _updateAgentDotsInContainer(document.getElementById("agents-sidebar"), agents);

    // Push aggregate status + session details to macOS menu bar tray icon.
    // Agents and coding sessions both feed the tray so any pane waiting on
    // the user lights up the menu bar.
    if (window.nicolas?.updateTrayStatus) {
      // Build a lookup of started_at timestamps from cached workspaces
      // (the status endpoint only returns status + name, not timestamps).
      const startedAtByKey = {};
      for (const ws of (typeof _workspaces !== "undefined" ? _workspaces : [])) {
        for (const rs of (ws.running_sessions || [])) {
          if (rs.id && rs.started_at) {
            startedAtByKey[`${ws.id}/${rs.id}`] = rs.started_at;
          }
        }
      }

      // Coding sessions: keys look like "workspaceId/sessionId"
      const codingEntries = Object.entries(sessions).map(([key, s]) => {
        const [workspaceId, sessionId] = key.split("/");
        return {
          kind: "session",
          key,
          workspaceId,
          sessionId,
          tmuxName: s.tmux_name || "",
          status: s.status || s.agent_status || "unknown",
          name: s.name || s.agent_name || "",
          startedAt: startedAtByKey[key] || s.started_at || s.startedAt || "",
        };
      });
      // Long-lived agents: keyed by agent id
      const agentEntries = Object.entries(agents || {}).map(([agentId, a]) => ({
        kind: "agent",
        agentId,
        status: a.status || a.agent_status || "unknown",
        name: a.name || a.agent_name || "",
        startedAt: a.started_at || a.startedAt || "",
      }));
      const sessionList = codingEntries.concat(agentEntries);

      // Include Nicolas chat if it finished a turn and user hasn't focused back
      if (nicolasWaiting) {
        sessionList.push({
          kind: "nicolas",
          status: "waiting",
          name: "Nicolas",
          startedAt: "",
        });
      }

      const trayStatus = sessionList.some((s) => s.status === "waiting") ? "waiting"
        : sessionList.some((s) => s.status === "working") ? "working" : "idle";

      // Counts for tray context menu
      const counts = {
        working: sessionList.filter((s) => s.status === "working").length,
        idle: sessionList.filter((s) => s.status === "idle").length,
        waiting: sessionList.filter((s) => s.status === "waiting").length,
      };

      // Auto-experiments running (from cachedProjects)
      const autoXps = (typeof cachedProjects !== "undefined" ? cachedProjects : [])
        .filter((p) => (p.active_sessions || 0) > 0)
        .map((p) => ({ id: p.id, name: p.name }));

      window.nicolas.updateTrayStatus(trayStatus, sessionList, { counts, autoXps });

      // Mirror the tray feed into a snapshot the topbar bell popover can read.
      window._bellSnapshot = { trayStatus, sessions: sessionList, counts, autoXps, ts: Date.now() };
      window.dispatchEvent(new CustomEvent("distillate:bell-update", { detail: window._bellSnapshot }));
    }
  } catch (e) { /* ignore */ } finally {
    _statusPollInFlight = false;
  }
}, 4000);

// ---------------------------------------------------------------------------
// Drag-and-drop reordering of sessions within a project
// ---------------------------------------------------------------------------

let _draggedEl = null;
let _dragStartY = 0;
const _DRAG_THRESHOLD = 5; // px before a mousedown becomes a drag

function _initSessionDragDrop(container) {
  if (!container) return;

  let _dragging = false;

  // Defensive cleanup: clear state and all visual classes. Called on
  // mouseup, window blur, document mouseleave — anywhere the drag might
  // end without a proper mouseup reaching us.
  const cleanup = () => {
    if (_draggedEl) _draggedEl.classList.remove("dragging");
    document.querySelectorAll(".sidebar-session-item.dragging").forEach((el) => {
      el.classList.remove("dragging");
    });
    document.querySelectorAll(".drag-over-above, .drag-over-below").forEach((el) => {
      el.classList.remove("drag-over-above", "drag-over-below");
    });
    _draggedEl = null;
    _dragging = false;
  };

  container.addEventListener("mousedown", (e) => {
    console.log("[DRAG] mousedown", { button: e.button, tag: e.target.tagName, cls: e.target.className });
    if (e.button !== 0) return;
    const item = e.target.closest(".sidebar-session-item");
    console.log("[DRAG] item found:", item?.dataset?.sessionId || "NONE");
    if (!item) return;
    _draggedEl = item;
    _dragStartY = e.clientY;
    _dragging = false;
  });

  document.addEventListener("mousemove", (e) => {
    if (!_draggedEl) return;

    if (!_dragging) {
      if (Math.abs(e.clientY - _dragStartY) < _DRAG_THRESHOLD) return;
      _dragging = true;
      _draggedEl.classList.add("dragging");
      console.log("[DRAG] threshold crossed");
    }

    const target = document.elementFromPoint(e.clientX, e.clientY)?.closest(".sidebar-session-item");
    container.querySelectorAll(".drag-over-above, .drag-over-below").forEach((el) => {
      el.classList.remove("drag-over-above", "drag-over-below");
    });
    if (!target || target === _draggedEl) return;
    if (target.dataset.wsId !== _draggedEl.dataset.wsId) return;

    const rect = target.getBoundingClientRect();
    const above = e.clientY < rect.top + rect.height / 2;
    target.classList.add(above ? "drag-over-above" : "drag-over-below");
  });

  document.addEventListener("mouseup", (e) => {
    console.log("[DRAG] mouseup, dragging=", _dragging, "el=", _draggedEl?.dataset?.sessionId);
    if (!_draggedEl) { cleanup(); return; }

    if (_dragging) {
      const target = document.elementFromPoint(e.clientX, e.clientY)?.closest(".sidebar-session-item");
      console.log("[DRAG] drop target:", target?.dataset?.sessionId || "NONE");
      if (target && target !== _draggedEl && target.dataset.wsId === _draggedEl.dataset.wsId) {
        if (target.classList.contains("drag-over-above")) {
          target.before(_draggedEl);
        } else {
          target.after(_draggedEl);
        }

        const wsId = _draggedEl.dataset.wsId;
        const projectGroup = _draggedEl.closest(".sidebar-workspace-group");
        const scope = projectGroup || container;
        const newOrder = [...scope.querySelectorAll(
          `.sidebar-session-item[data-ws-id="${wsId}"]`
        )].map((el) => el.dataset.sessionId);
        console.log("[DRAG] persisting:", newOrder);
        _persistSessionOrder(wsId, newOrder);
      } else {
        console.log("[DRAG] drop rejected", { hasTarget: !!target, isSelf: target === _draggedEl, wsIdMatch: target?.dataset?.wsId === _draggedEl?.dataset?.wsId });
      }
    }

    cleanup();
  });

  // Safety nets: if the user drags outside the window, alt-tabs, or the
  // mouseup otherwise doesn't reach us, these prevent stuck .dragging state.
  window.addEventListener("blur", cleanup);
  document.addEventListener("mouseleave", cleanup);
}

async function _persistSessionOrder(wsId, sessionIds) {
  try {
    await fetch(`http://127.0.0.1:${serverPort}/workspaces/${wsId}/sessions/reorder`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_ids: sessionIds }),
    });
  } catch (e) { /* ignore */ }
}

// Init drag-drop on the projects sidebar (delegates, so works with re-rendered content)
_initSessionDragDrop(workspacesSidebarEl);
_initSessionDragDrop(document.getElementById("agents-sidebar"));

// ---------------------------------------------------------------------------
// Workspace Lab Notebook section
// ---------------------------------------------------------------------------

/**
 * Load the lab notebook feed for a specific project. Filters /notebook/entries
 * by project tag (slugged on the backend), localizes timestamps via the
 * notebook UI helpers, and renders entries using the shared timeline renderer.
 */
async function loadWorkspaceNotebook(workspaceId, workspaceName) {
  const feedEl = document.getElementById("workspace-notebook-feed");
  if (!feedEl) return;

  if (!serverPort) {
    feedEl.innerHTML = '<div class="sidebar-empty-hint">Server not ready</div>';
    return;
  }

  try {
    const params = new URLSearchParams({ n: "100", project: workspaceName || workspaceId });
    const resp = await fetch(`http://127.0.0.1:${serverPort}/notebook/entries?${params}`);
    if (!resp.ok) throw new Error("Failed");
    const data = await resp.json();
    let entries = data.entries || [];

    // Reuse the notebook UI's localizer + renderer if available
    if (typeof _localizeEntries === "function") {
      entries = _localizeEntries(entries);
      // Push these into the cached entries so the global edit/delete flow works
      // (the buttons look up entries by _id from _notebookEntries).
      if (Array.isArray(_notebookEntries)) {
        // Merge unique by _id
        const existingIds = new Set(_notebookEntries.map((e) => e._id));
        for (const e of entries) {
          if (!existingIds.has(e._id)) _notebookEntries.push(e);
        }
      }
    }

    if (!entries.length) {
      feedEl.innerHTML = '<div class="nb-feed-empty">No notebook entries for this project yet.</div>';
      return;
    }

    let html = '<div class="nb-feed workspace-notebook-timeline">';
    let currentDate = "";
    for (const entry of entries) {
      if (entry.date !== currentDate) {
        currentDate = entry.date;
        const dt = new Date(currentDate + "T00:00:00");
        const label = dt.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
        html += `<h2 class="nb-feed-day">${escapeHtml(label)}</h2>`;
      }
      if (typeof _renderTimelineEntry === "function") {
        html += _renderTimelineEntry(entry);
      }
    }
    html += "</div>";
    feedEl.innerHTML = html;
  } catch (e) {
    feedEl.innerHTML = '<div class="sidebar-empty-hint">Failed to load notebook</div>';
  }
}

/**
 * Toggle the inline add-note form for the project notebook section. Pre-fills
 * the project tag with the workspace name.
 */
function toggleWorkspaceNotebookForm(workspaceId, workspaceName) {
  const formEl = document.getElementById("workspace-notebook-form");
  if (!formEl) return;

  if (!formEl.classList.contains("hidden")) {
    formEl.classList.add("hidden");
    formEl.innerHTML = "";
    return;
  }

  formEl.innerHTML = `
    <textarea class="workspace-notebook-text" placeholder="What happened?" rows="3"></textarea>
    <div class="workspace-notebook-form-row">
      <select class="workspace-notebook-type">
        <option value="note">Note</option>
        <option value="observation">Observation</option>
        <option value="decision">Decision</option>
        <option value="milestone">Milestone</option>
      </select>
      <span class="workspace-notebook-tag">#${escapeHtml(workspaceName || workspaceId)}</span>
      <div class="workspace-notebook-form-spacer"></div>
      <button class="btn-secondary" onclick="toggleWorkspaceNotebookForm('${workspaceId}', '${escapeHtml(workspaceName)}')">Cancel</button>
      <button class="btn-primary" onclick="submitWorkspaceNotebookEntry('${workspaceId}', '${escapeHtml(workspaceName)}')">Save</button>
    </div>`;
  formEl.classList.remove("hidden");
  const textEl = formEl.querySelector(".workspace-notebook-text");
  if (textEl) textEl.focus();
}

/**
 * Submit a new lab notebook entry tagged with this project. POSTs to
 * /notebook and refreshes the project notebook feed.
 */
async function submitWorkspaceNotebookEntry(workspaceId, workspaceName) {
  const formEl = document.getElementById("workspace-notebook-form");
  if (!formEl || !serverPort) return;
  const textEl = formEl.querySelector(".workspace-notebook-text");
  const typeEl = formEl.querySelector(".workspace-notebook-type");
  if (!textEl || !textEl.value.trim()) return;

  const body = {
    entry: textEl.value.trim(),
    entry_type: typeEl ? typeEl.value : "note",
    project: workspaceName || workspaceId,
  };

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/notebook`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.success) {
      alert(`Failed to save: ${data.error || "unknown error"}`);
      return;
    }
    // Hide form, reload feed
    formEl.classList.add("hidden");
    formEl.innerHTML = "";
    loadWorkspaceNotebook(workspaceId, workspaceName);
    // Also refresh the main notebook view if it's currently open
    if (typeof fetchNotebookEntries === "function" && _activeSidebarView === "notebook") {
      fetchNotebookEntries();
    }
  } catch (e) {
    alert("Failed to save entry");
  }
}
