/* ───── Lab Notebook — chronological research journal ───── */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _notebookEntries = [];
let _notebookDates = [];
let _notebookFilter = "all";
let _notebookCurrentDate = ""; // "" = recent, "YYYY-MM-DD" = specific day
let _notebookDatesLoaded = false;
let _notebookOpenDay = ""; // currently open day in center column ("" = none)
let _notebookPollInterval = null;
let _notebookLastEntryKey = ""; // for change detection
// ID of the entry currently being edited inline, or "" if none. We gate the
// center-view re-render on this so polling doesn't destroy a form the user
// is still typing into.
let _notebookEditingId = "";

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const notebookSidebarEl = document.getElementById("notebook-sidebar");
const notebookCountEl = document.getElementById("notebook-count");
const notebookFiltersEl = document.getElementById("notebook-filters");
const notebookDateNavEl = document.getElementById("notebook-date-nav");
const notebookAddBtn = document.getElementById("notebook-add-btn");
const notebookAddForm = document.getElementById("notebook-add-form");
const notebookAddText = document.getElementById("notebook-add-text");
const notebookAddType = document.getElementById("notebook-add-type");
const notebookAddProject = document.getElementById("notebook-add-project");
const notebookAddSubmit = document.getElementById("notebook-add-submit");
const notebookDetailEl = document.getElementById("notebook-detail");

// ---------------------------------------------------------------------------
// Entry type metadata
// ---------------------------------------------------------------------------

const ENTRY_TYPES = {
  session:       { label: "Session",    color: "var(--green)",    icon: "S" },
  experiment:    { label: "Experiment", color: "var(--accent)",   icon: "E" },
  milestone:     { label: "Milestone",  color: "var(--warm)",     icon: "M" },
  run_completed: { label: "Run",        color: "var(--green)",    icon: "R" },
  note:          { label: "Note",       color: "var(--text-dim)", icon: "N" },
  observation:   { label: "Observation",color: "var(--accent)",   icon: "O" },
  decision:      { label: "Decision",   color: "var(--warm)",     icon: "D" },
  paper:         { label: "Paper",      color: "var(--accent)",   icon: "P" },
};

// ---------------------------------------------------------------------------
// Fetch
// ---------------------------------------------------------------------------

async function fetchNotebookEntries() {
  if (!serverPort) return;

  // Start polling for live updates (idempotent)
  startNotebookPolling();

  // Load dates list (refresh each fetch so new days appear in nav)
  fetchNotebookDates();

  const params = new URLSearchParams();
  params.set("n", "100");
  if (_notebookCurrentDate) params.set("date", _notebookCurrentDate);

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/notebook/entries?${params}`);
    if (!resp.ok) return;
    const data = await resp.json();
    _notebookEntries = _localizeEntries(data.entries || []);

    // Compute a fingerprint of the entry set for change detection
    const newKey = _notebookEntries.map((e) => `${e.date}T${e.time}`).join("|");
    const changed = newKey !== _notebookLastEntryKey;
    _notebookLastEntryKey = newKey;

    renderNotebookSidebar();

    // While the user is editing an entry, skip all center-view re-renders —
    // they would destroy the inline form. The sidebar can still refresh.
    if (_notebookEditingId) return;

    // Auto-show the recent feed in the center column when the notebook view
    // is the active sidebar view and nothing else is selected. This makes the
    // notebook feel populated immediately on open instead of empty.
    if (_activeSidebarView === "notebook" && !_notebookOpenDay) {
      _renderNotebookRecentView(_notebookEntries);
    }

    // If a specific day view is open and the data changed, refresh it too
    if (_notebookOpenDay && changed) {
      _renderNotebookDayView(_notebookOpenDay);
    }
  } catch (e) {
    // Server not ready
  }
}

function startNotebookPolling() {
  if (_notebookPollInterval) return;
  _notebookPollInterval = setInterval(() => {
    // Skip polling while the window is hidden — no reason to keep fetching
    // entries that nobody is looking at. visibilitychange triggers a refresh
    // when the user comes back.
    if (document.hidden) return;
    if (_activeSidebarView === "notebook" || _notebookOpenDay) {
      fetchNotebookEntries();
    }
  }, 15000);
}

function stopNotebookPolling() {
  if (_notebookPollInterval) {
    clearInterval(_notebookPollInterval);
    _notebookPollInterval = null;
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && (_activeSidebarView === "notebook" || _notebookOpenDay)) {
    fetchNotebookEntries();
  }
});

async function fetchNotebookDates() {
  if (!serverPort) return;
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/notebook/dates`);
    if (!resp.ok) return;
    const data = await resp.json();
    _notebookDates = data.dates || [];
    _notebookDatesLoaded = true;
    renderNotebookDateNav();
  } catch (e) {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Render — sidebar entry list
// ---------------------------------------------------------------------------

/**
 * Apply the current filter pill selection to a list of entries. Shared by the
 * sidebar list and the center-column feed so clicking a pill updates both.
 */
function _applyNotebookFilter(entries) {
  if (_notebookFilter === "all") return entries;
  return entries.filter((e) => _matchesFilter(e, _notebookFilter));
}

function _matchesFilter(entry, filter) {
  if (filter === "pinned")      return !!entry.pinned;
  if (filter === "sessions")    return entry.type === "session";
  if (filter === "experiments") return entry.type === "experiment";
  if (filter === "runs")        return entry.type === "run_completed";
  // "Notes" is the catch-all for human-written entries. Legacy observation,
  // decision, and milestone types are grouped in here too so existing data
  // stays discoverable after we collapsed them into a single note type.
  if (filter === "notes") {
    return entry.type === "note"
      || entry.type === "observation"
      || entry.type === "decision"
      || entry.type === "milestone";
  }
  if (filter === "papers") return entry.type === "paper";
  return true;
}

function renderNotebookSidebar() {
  if (!notebookSidebarEl) return;

  const filtered = _applyNotebookFilter(_notebookEntries);

  // Badge: count entries from today (local time), fall back to total if none.
  const todayStr = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in local tz
  const todayCount = _notebookEntries.filter((e) => e.date === todayStr).length;
  if (notebookCountEl) {
    notebookCountEl.textContent = todayCount || _notebookEntries.length || "";
  }

  // Render filters
  renderNotebookFilters();

  if (!filtered.length) {
    notebookSidebarEl.innerHTML = `
      <div class="sidebar-empty">
        <p>No entries${_notebookFilter !== "all" ? " matching filter" : ""}.</p>
        <p class="sidebar-empty-hint">${_notebookCurrentDate ? "Try a different date." : "Activity from experiments, agents, and sessions will appear here."}</p>
      </div>`;
    return;
  }

  // Group entries by date
  let html = "";
  let currentDate = "";
  const activeDay = _notebookOpenDay || _notebookCurrentDate || "";

  for (const entry of filtered) {
    if (entry.date !== currentDate) {
      currentDate = entry.date;
      const label = _formatDateLabel(currentDate);
      const activeCls = currentDate === activeDay ? " active" : "";
      html += `<div class="nb-sidebar-date${activeCls}" onclick="notebookShowDay('${escapeHtml(currentDate)}')">${label}</div>`;
    }

    const cleanText = _cleanEntryText(entry.text);
    let displayText;
    if (entry.type === "session") {
      const parsed = _parseSessionText(cleanText);
      displayText = parsed.title || entry.session_name || _truncateText(cleanText, 80);
    } else if (entry.type === "run_completed") {
      const parsed = _parseRunText(cleanText);
      // Compact sidebar label: "R72 ★ loss=0.312 (-0.042)"
      displayText = parsed.runNum && parsed.status
        ? `R${parsed.runNum}${parsed.status === "best" ? " ★" : parsed.status === "crash" ? " ✗" : ""} ${parsed.metric}`.trim()
        : (parsed.title || _truncateText(cleanText, 80));
    } else {
      displayText = _truncateText(cleanText, 80);
    }
    const projectTag = _cleanTagForDisplay(_extractProject(entry.tags));
    const isSession = entry.type === "session";
    const entryActiveCls = entry.date === activeDay ? " active" : "";
    const pinnedCls = entry.pinned ? " pinned" : "";
    const pinMark = entry.pinned
      ? `<span class="nb-sidebar-pin" title="Pinned">★</span>`
      : "";

    html += `<div class="nb-sidebar-entry${isSession ? " nb-sidebar-session" : ""}${entryActiveCls}${pinnedCls}" onclick="notebookShowDay('${escapeHtml(entry.date)}')">
      <div class="nb-sidebar-entry-text">${pinMark}${escapeHtml(displayText)}</div>
      <div class="nb-sidebar-entry-meta">
        <span class="nb-sidebar-entry-time">${escapeHtml(entry.time)}</span>
        ${projectTag ? `<span class="nb-sidebar-entry-tag">${escapeHtml(projectTag)}</span>` : ""}
      </div>
    </div>`;
  }

  notebookSidebarEl.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Render — filter pills
// ---------------------------------------------------------------------------

function renderNotebookFilters() {
  if (!notebookFiltersEl) return;

  const filters = [
    { value: "all",         label: "All" },
    { value: "pinned",      label: "Pinned" },
    { value: "sessions",    label: "Sessions" },
    { value: "experiments", label: "Experiments" },
    { value: "runs",        label: "Runs" },
    { value: "notes",       label: "Notes" },
    { value: "papers",      label: "Papers" },
  ];

  // Pre-compute counts per filter so we can suppress empty pills and show
  // counts next to the label — the user can see at a glance what's there.
  const counts = {};
  for (const f of filters) {
    counts[f.value] = f.value === "all"
      ? _notebookEntries.length
      : _notebookEntries.filter((e) => _matchesFilter(e, f.value)).length;
  }

  notebookFiltersEl.innerHTML = "";
  for (const f of filters) {
    const n = counts[f.value];
    // Hide empty category pills (except "all"), so the bar stays tidy
    if (n === 0 && f.value !== "all") continue;
    const btn = document.createElement("button");
    btn.className = `nb-filter${f.value === _notebookFilter ? " active" : ""}`;
    btn.innerHTML = `${escapeHtml(f.label)}<span class="nb-filter-count">${n}</span>`;
    btn.addEventListener("click", () => {
      _notebookFilter = f.value;
      renderNotebookSidebar();
      // Refresh the center feed too so the filter actually feels responsive
      if (_notebookOpenDay) {
        _renderNotebookDayView(_notebookOpenDay);
      } else if (_activeSidebarView === "notebook") {
        _renderNotebookRecentView(_notebookEntries);
      }
    });
    notebookFiltersEl.appendChild(btn);
  }
}

// ---------------------------------------------------------------------------
// Render — date navigation
// ---------------------------------------------------------------------------

function renderNotebookDateNav() {
  if (!notebookDateNavEl) return;

  const currentLabel = _notebookCurrentDate
    ? _formatDateLabel(_notebookCurrentDate)
    : "Recent";

  // "Today" jump is only useful when the user has drilled into a different
  // day — hide it on the recent feed so the bar stays uncluttered.
  const showTodayJump = !!_notebookCurrentDate;

  notebookDateNavEl.innerHTML = `
    <button class="notebook-nav-btn" id="notebook-nav-prev" title="Previous day">&lsaquo;</button>
    <span class="notebook-nav-label" id="notebook-nav-label">${currentLabel}</span>
    <button class="notebook-nav-btn" id="notebook-nav-next" title="Next day">&rsaquo;</button>
    ${showTodayJump ? `<button class="notebook-nav-today" id="notebook-nav-today" title="Jump to recent feed">Today</button>` : ""}`;

  document.getElementById("notebook-nav-prev")?.addEventListener("click", () => notebookNavPrev());
  document.getElementById("notebook-nav-next")?.addEventListener("click", () => notebookNavNext());
  document.getElementById("notebook-nav-today")?.addEventListener("click", () => notebookBackToRecent());
  document.getElementById("notebook-nav-label")?.addEventListener("click", () => {
    // Click label to go back to "Recent" view
    notebookBackToRecent();
  });
}

function notebookNavPrev() {
  if (!_notebookDates.length) return;

  if (!_notebookCurrentDate) {
    // From "Recent", go to the latest date
    _notebookCurrentDate = _notebookDates[0];
  } else {
    const idx = _notebookDates.indexOf(_notebookCurrentDate);
    if (idx >= 0 && idx < _notebookDates.length - 1) {
      _notebookCurrentDate = _notebookDates[idx + 1]; // dates are reverse-chronological
    } else {
      return; // already at oldest
    }
  }

  fetchNotebookEntries();
  renderNotebookDateNav();
}

function notebookNavNext() {
  if (!_notebookDates.length || !_notebookCurrentDate) return;

  const idx = _notebookDates.indexOf(_notebookCurrentDate);
  if (idx > 0) {
    _notebookCurrentDate = _notebookDates[idx - 1]; // dates are reverse-chronological
  } else {
    // At most recent date — go back to "Recent"
    _notebookCurrentDate = "";
  }

  fetchNotebookEntries();
  renderNotebookDateNav();
}

// ---------------------------------------------------------------------------
// Render — center column day view
// ---------------------------------------------------------------------------

function notebookShowDay(date) {
  if (!notebookDetailEl) return;

  // Navigate to this date and show detail
  _notebookCurrentDate = date;
  fetchNotebookEntries();
  renderNotebookDateNav();

  // Fetch full day data and render detail
  _renderNotebookDayView(date);
}

/**
 * Swap the center column into notebook mode: hide welcome + experiment-detail,
 * show notebook-detail, ensure the control-panel editor tab is active.
 */
function _activateNotebookCenterColumn() {
  if (!notebookDetailEl) return;
  const welcomeEl = document.getElementById("welcome");
  const expDetailEl = document.getElementById("experiment-detail");
  if (welcomeEl) welcomeEl.classList.add("hidden");
  if (expDetailEl) expDetailEl.classList.add("hidden");
  notebookDetailEl.classList.remove("hidden");

  // Make sure the control-panel editor view is the visible one
  const editorViews = ["control-panel", "session", "results", "prompt-editor"];
  for (const v of editorViews) {
    const el = document.getElementById(`${v}-view`);
    if (el) el.classList.toggle("hidden", v !== "control-panel");
  }
}

/**
 * Render the unified "Recent Activity" view in the center column — entries
 * across all dates, grouped by day. Shown when the notebook tab is active
 * and no specific day has been drilled into.
 */
function _renderNotebookRecentView(entries) {
  if (!notebookDetailEl) return;
  _activateNotebookCenterColumn();

  const prevScroll = notebookDetailEl.scrollTop;

  // Apply the same filter the sidebar uses so clicking a pill updates both
  // panes in lock-step.
  const filtered = _applyNotebookFilter(entries);

  const filterNote = _notebookFilter !== "all"
    ? ` — filtered to <em>${escapeHtml(_notebookFilter)}</em>`
    : "";

  let html = `<div class="nb-page">
    <header class="nb-page-header">
      <p class="nb-page-eyebrow">Research journal</p>
      <h1 class="nb-page-title">Notebook</h1>
      <p class="nb-page-subtitle">A chronological record of activity across all projects${filterNote}.</p>
    </header>
    <div class="nb-feed">`;

  if (!filtered.length) {
    const emptyMsg = _notebookFilter === "all"
      ? "Nothing yet. Sessions, experiments, papers, and notes will appear here as they happen."
      : `No ${escapeHtml(_notebookFilter)} entries in the recent feed.`;
    html += `<div class="nb-feed-empty">${emptyMsg}</div>`;
  }

  // Entries are newest-first; group by date with day headers
  let currentDate = "";
  for (const entry of filtered) {
    if (entry.date !== currentDate) {
      currentDate = entry.date;
      const dt = new Date(currentDate + "T00:00:00");
      const dayLabel = dt.toLocaleDateString("en-US", {
        weekday: "long", year: "numeric", month: "long", day: "numeric",
      });
      html += `<h2 class="nb-feed-day" onclick="notebookShowDay('${escapeHtml(currentDate)}')">${escapeHtml(dayLabel)}</h2>`;
    }
    html += _renderTimelineEntry(entry);
  }

  html += `</div></div>`;
  notebookDetailEl.innerHTML = html;
  notebookDetailEl.scrollTop = prevScroll;
}

async function _renderNotebookDayView(date) {
  if (!notebookDetailEl || !serverPort) return;

  // Track the open day so polling can refresh it
  _notebookOpenDay = date;

  _activateNotebookCenterColumn();

  // Preserve scroll position across re-renders
  const prevScroll = notebookDetailEl.scrollTop;

  // Fetch a wider range so we catch entries that fall on this LOCAL date but
  // were stored under the adjacent UTC date file. We then filter client-side
  // after timezone conversion.
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/notebook/entries?n=500`);
    if (!resp.ok) return;
    const data = await resp.json();
    const allEntries = _localizeEntries(data.entries || []);
    const entries = allEntries.filter((e) => e.date === date);

    _renderDayContent(date, entries);
    notebookDetailEl.scrollTop = prevScroll;
  } catch (e) {
    notebookDetailEl.innerHTML = `<div class="notebook-day-error">Failed to load entries.</div>`;
  }
}

function _renderDayContent(date, entries) {
  if (!notebookDetailEl) return;

  const dt = new Date(date + "T00:00:00");
  const dayTitle = dt.toLocaleDateString("en-US", {
    weekday: "long", year: "numeric", month: "long", day: "numeric",
  });

  const filtered = _applyNotebookFilter(entries);
  const filterNote = _notebookFilter !== "all"
    ? ` · filtered to ${escapeHtml(_notebookFilter)}`
    : "";
  const countLabel = `${filtered.length} entr${filtered.length === 1 ? "y" : "ies"}${filterNote}`;

  let html = `
    <div class="nb-page">
      <header class="nb-page-header">
        <a class="nb-page-back" onclick="notebookBackToRecent()">&larr; All entries</a>
        <h1 class="nb-page-title">${escapeHtml(dayTitle)}</h1>
        <p class="nb-page-subtitle">${countLabel}</p>
      </header>
      <div class="nb-feed">`;

  if (!filtered.length) {
    html += `<div class="nb-feed-empty">No entries recorded this day.</div>`;
  }

  // Entries come newest-first from the API and we keep that order — the
  // user wants descending chronological in every view.
  for (const entry of filtered) {
    html += _renderTimelineEntry(entry);
  }

  html += `
      </div>
    </div>`;

  notebookDetailEl.innerHTML = html;
}

/**
 * Render a single timeline entry as HTML. Shared by the recent feed and the
 * per-day view so both look identical.
 */
function _renderTimelineEntry(entry) {
  const cleanText = _cleanEntryText(entry.text);

  let titleHtml = "";
  let bodyHtml = "";

  if (entry.type === "session") {
    const parsed = _parseSessionText(cleanText);
    if (parsed.title) {
      titleHtml = `<h2 class="nb-entry-title">${escapeHtml(parsed.title)}</h2>`;
    }
    if (parsed.body) {
      bodyHtml = typeof window.markedParse === "function"
        ? window.markedParse(parsed.body)
        : `<p>${escapeHtml(parsed.body)}</p>`;
    }
  } else if (entry.type === "run_completed") {
    const parsed = _parseRunText(cleanText);
    const statusIcon = parsed.status === "best" ? " ★" : parsed.status === "crash" ? " ✗" : "";
    const verdictCls = parsed.verdict === "confirmed" ? "nb-verdict-confirmed"
      : parsed.verdict === "refuted"    ? "nb-verdict-refuted"
      : parsed.verdict                  ? "nb-verdict-inconclusive" : "";
    const verdictHtml = parsed.verdict
      ? `<span class="nb-run-verdict ${verdictCls}">${escapeHtml(parsed.verdict)}</span>`
      : "";
    titleHtml = `<div class="nb-run-header">
      <span class="nb-run-badge nb-run-${escapeHtml(parsed.status)}">R${escapeHtml(parsed.runNum)}${statusIcon}</span>
      <span class="nb-run-metric">${escapeHtml(parsed.metric)}</span>
      ${verdictHtml}
    </div>`;
    if (parsed.segments.length) {
      bodyHtml = `<div class="nb-run-details">` +
        parsed.segments.map((seg) => {
          const colon = seg.indexOf(":");
          if (colon < 0) return `<div class="nb-run-detail-row">${escapeHtml(seg)}</div>`;
          const label = seg.slice(0, colon).trim();
          const val   = seg.slice(colon + 1).trim();
          return `<div class="nb-run-detail-row"><span class="nb-run-detail-label">${escapeHtml(label)}</span><span class="nb-run-detail-value">${escapeHtml(val)}</span></div>`;
        }).join("") +
        `</div>`;
    }
  } else {
    // Non-session entries use the cleaned text. Render markdown if it looks
    // like more than a single line.
    if (cleanText.length > 80 && typeof window.markedParse === "function") {
      bodyHtml = window.markedParse(cleanText);
    } else {
      bodyHtml = `<p class="nb-entry-line">${escapeHtml(cleanText)}</p>`;
    }
  }

  // Type label only when we don't have a serif title carrying the meaning
  const typeLabelHtml = ((entry.type === "session" || entry.type === "run_completed") && titleHtml)
    ? ""
    : `<span class="nb-entry-type">${escapeHtml(entry.type)}</span>`;

  const projectTag = _cleanTagForDisplay(_extractProject(entry.tags));
  const projectHtml = projectTag
    ? `<span class="nb-entry-project">${escapeHtml(projectTag)}</span>`
    : "";

  // Subtle text-style edit / delete affordances
  const actionsHtml = `
    <div class="nb-entry-actions">
      <button class="nb-entry-action" onclick="notebookEditEntry('${entry._id}')">Edit</button>
      <span class="nb-entry-action-sep">·</span>
      <button class="nb-entry-action" onclick="notebookDeleteEntry('${entry._id}')">Delete</button>
    </div>`;

  // Pin button — a filled star when pinned (always visible), hollow and
  // hover-revealed when not. Sits immediately left of the time so the two
  // form a single "entry handle" aligned with the title baseline.
  const pinClass = entry.pinned ? "nb-entry-pin pinned" : "nb-entry-pin";
  const pinIcon = entry.pinned ? "★" : "☆";
  const pinTitle = entry.pinned ? "Unpin" : "Pin as milestone";
  const pinHtml = `<button class="${pinClass}" onclick="notebookTogglePin('${entry._id}')" title="${pinTitle}">${pinIcon}</button>`;

  const sessionCls = entry.type === "session" ? " nb-entry-session" : "";
  const pinnedCls = entry.pinned ? " nb-entry-pinned" : "";

  return `
    <article class="nb-entry${sessionCls}${pinnedCls}" data-entry-id="${entry._id}">
      <aside class="nb-entry-marginalia">
        <div class="nb-entry-primary">
          ${pinHtml}
          <span class="nb-entry-time">${escapeHtml(entry.time)}</span>
        </div>
        ${typeLabelHtml}
      </aside>
      <div class="nb-entry-content">
        ${titleHtml}
        <div class="nb-entry-body">${bodyHtml}</div>
        <div class="nb-entry-footer">
          ${projectHtml}
          ${actionsHtml}
        </div>
      </div>
    </article>`;
}

/**
 * Return from a specific day view to the unified recent feed.
 */
function notebookBackToRecent() {
  _notebookOpenDay = "";
  _notebookCurrentDate = "";
  fetchNotebookEntries();
  renderNotebookDateNav();
}

// ---------------------------------------------------------------------------
// Edit / delete entries
// ---------------------------------------------------------------------------

/**
 * Open an inline editor for a single entry. Replaces the entry's content area
 * with a form. The form is different shape for workspace session entries
 * (title + body) vs. plain notebook lines (text + type + project).
 */
function notebookEditEntry(id) {
  const entry = _findEntryById(id);
  if (!entry) return;
  const entryEl = document.querySelector(`.nb-entry[data-entry-id="${id}"]`);
  if (!entryEl) return;
  const contentEl = entryEl.querySelector(".nb-entry-content");
  if (!contentEl) return;

  let formHtml = "";
  if (entry.source === "workspace") {
    const cleanText = _cleanEntryText(entry.text);
    const parsed = _parseSessionText(cleanText);
    formHtml = `
      <div class="nb-edit">
        <input type="text" class="nb-edit-title" placeholder="Session title"
               value="${escapeHtml(parsed.title || entry.session_name || "")}">
        <textarea class="nb-edit-body" rows="10" placeholder="Session summary…">${escapeHtml(parsed.body || "")}</textarea>
        <div class="nb-edit-footer">
          <button class="nb-entry-action" onclick="notebookCancelEdit('${id}')">Cancel</button>
          <span class="nb-entry-action-sep">·</span>
          <button class="nb-entry-action nb-entry-action-primary" onclick="notebookSaveEdit('${id}')">Save changes</button>
        </div>
      </div>`;
  } else {
    const cleanText = _cleanEntryText(entry.text);
    const project = entry.tags && entry.tags.length ? entry.tags[0] : "";
    formHtml = `
      <div class="nb-edit">
        <textarea class="nb-edit-text" rows="3" placeholder="Entry text">${escapeHtml(cleanText)}</textarea>
        <div class="nb-edit-row">
          <select class="nb-edit-type">
            ${["note", "observation", "decision", "milestone", "session", "experiment", "paper", "run_completed"]
              .map((t) => `<option value="${t}"${entry.type === t ? " selected" : ""}>${t}</option>`)
              .join("")}
          </select>
          <input type="text" class="nb-edit-project" placeholder="Project tag" value="${escapeHtml(project)}">
        </div>
        <div class="nb-edit-footer">
          <button class="nb-entry-action" onclick="notebookCancelEdit('${id}')">Cancel</button>
          <span class="nb-entry-action-sep">·</span>
          <button class="nb-entry-action nb-entry-action-primary" onclick="notebookSaveEdit('${id}')">Save changes</button>
        </div>
      </div>`;
  }

  // Stash the original content so cancel can restore it
  contentEl.dataset.originalHtml = contentEl.innerHTML;
  contentEl.innerHTML = formHtml;
  // Guard against the background poll wiping this form mid-edit
  _notebookEditingId = id;
  const firstField = contentEl.querySelector("input, textarea");
  if (firstField) firstField.focus();
}

function notebookCancelEdit(id) {
  const entryEl = document.querySelector(`.nb-entry[data-entry-id="${id}"]`);
  if (!entryEl) return;
  const contentEl = entryEl.querySelector(".nb-entry-content");
  if (!contentEl) return;
  if (contentEl.dataset.originalHtml) {
    contentEl.innerHTML = contentEl.dataset.originalHtml;
    delete contentEl.dataset.originalHtml;
  }
  if (_notebookEditingId === id) _notebookEditingId = "";
}

async function notebookSaveEdit(id) {
  const entry = _findEntryById(id);
  if (!entry || !serverPort) return;
  const entryEl = document.querySelector(`.nb-entry[data-entry-id="${id}"]`);
  if (!entryEl) return;

  let body;
  if (entry.source === "workspace") {
    const titleEl = entryEl.querySelector(".nb-edit-title");
    const bodyEl = entryEl.querySelector(".nb-edit-body");
    body = {
      source: "workspace",
      date: entry._utc_date,
      time: entry._utc_time,
      session_name: entry.session_name || "",
      title: titleEl ? titleEl.value.trim() : "",
      body: bodyEl ? bodyEl.value : "",
    };
  } else {
    const textEl = entryEl.querySelector(".nb-edit-text");
    const typeEl = entryEl.querySelector(".nb-edit-type");
    const projEl = entryEl.querySelector(".nb-edit-project");
    body = {
      source: "notebook",
      date: entry._utc_date,
      time: entry._utc_time,
      text: textEl ? textEl.value.trim() : "",
      entry_type: typeEl ? typeEl.value : "note",
      project: projEl ? projEl.value.trim() : "",
    };
  }

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/notebook/entry`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.success) {
      alert(`Failed to save: ${data.error || "unknown error"}`);
      return;
    }
    // Edit is done — release the render guard and force a refresh by clearing
    // the fingerprint, then refetch.
    if (_notebookEditingId === id) _notebookEditingId = "";
    _notebookLastEntryKey = "";
    fetchNotebookEntries();
  } catch (e) {
    alert("Failed to save entry");
  }
}

async function notebookDeleteEntry(id) {
  const entry = _findEntryById(id);
  if (!entry || !serverPort) return;
  const label = entry.source === "workspace"
    ? `session "${entry.session_name || "untitled"}"`
    : "this entry";
  if (!confirm(`Delete ${label}?`)) return;

  const body = {
    source: entry.source || "notebook",
    date: entry._utc_date,
    time: entry._utc_time,
  };
  if (entry.source === "workspace" && entry.session_name) {
    body.session_name = entry.session_name;
  }

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/notebook/entry`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.success) {
      alert(`Failed to delete: ${data.error || "unknown error"}`);
      return;
    }
    _notebookLastEntryKey = "";
    fetchNotebookEntries();
  } catch (e) {
    alert("Failed to delete entry");
  }
}

// ---------------------------------------------------------------------------
// Pin toggle
// ---------------------------------------------------------------------------

async function notebookTogglePin(id) {
  const entry = _findEntryById(id);
  if (!entry || !serverPort) return;
  const body = {
    source: entry.source || "notebook",
    date: entry._utc_date,
    time: entry._utc_time,
  };
  if (entry.source === "workspace" && entry.session_name) {
    body.session_name = entry.session_name;
  }
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/notebook/pin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.success) return;
    // Update the in-memory entry and re-render the visible views in place,
    // so the pin flip feels instant. A background fetch will reconcile.
    entry.pinned = !!data.pinned;
    renderNotebookSidebar();
    if (_notebookOpenDay) {
      _renderNotebookDayView(_notebookOpenDay);
    } else if (_activeSidebarView === "notebook") {
      _renderNotebookRecentView(_notebookEntries);
    }
  } catch (e) {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Add entry
// ---------------------------------------------------------------------------

function toggleNotebookAddForm() {
  if (!notebookAddForm) return;
  notebookAddForm.classList.toggle("hidden");
  if (!notebookAddForm.classList.contains("hidden") && notebookAddText) {
    notebookAddText.focus();
  }
}

async function submitNotebookEntry() {
  if (!notebookAddText || !serverPort) return;
  const text = notebookAddText.value.trim();
  if (!text) return;

  const body = {
    entry: text,
    entry_type: "note",
  };
  const project = notebookAddProject?.value.trim();
  if (project) body.project = project;

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/notebook`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) return;

    // Clear form and refresh
    notebookAddText.value = "";
    if (notebookAddProject) notebookAddProject.value = "";
    notebookAddForm.classList.add("hidden");

    // Reset to recent view and refresh
    _notebookCurrentDate = "";
    _notebookDatesLoaded = false;
    fetchNotebookEntries();
  } catch (e) {
    // ignore
  }
}

// Event listeners
notebookAddBtn?.addEventListener("click", toggleNotebookAddForm);
notebookAddSubmit?.addEventListener("click", submitNotebookEntry);
notebookAddText?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    submitNotebookEntry();
  }
  if (e.key === "Escape") {
    notebookAddForm?.classList.add("hidden");
  }
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _formatDateLabel(dateStr) {
  const today = new Date();
  const todayStr = today.toISOString().slice(0, 10);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const yesterdayStr = yesterday.toISOString().slice(0, 10);

  if (dateStr === todayStr) return "Today";
  if (dateStr === yesterdayStr) return "Yesterday";

  const dt = new Date(dateStr + "T00:00:00");
  // If within last 7 days, show day name
  const diffDays = Math.floor((today - dt) / 86400000);
  if (diffDays < 7) {
    return dt.toLocaleDateString("en-US", { weekday: "long" });
  }
  return dt.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function _cleanEntryText(text) {
  return text
    .replace(/#\S+/g, "")
    .replace(/\s*[—–]\s*$/, "")  // strip trailing em/en-dash left after tag removal
    .trim();
}

function _extractProject(tags) {
  // Return first tag as "project" (convention: first tag is the project)
  return tags && tags.length ? tags[0] : "";
}

/**
 * Strip decorative emoji and punctuation from a project tag before display.
 * Workspace names like "Distillate ⚗️" should render as "Distillate" in the
 * notebook UI — the emoji is part of the stored workspace name but only adds
 * noise to the feed.
 */
function _cleanTagForDisplay(tag) {
  if (!tag) return tag;
  // Strip anything in the Unicode symbols/pictographs range. The regex below
  // catches emoji presentation selectors, dingbats, and miscellaneous symbols
  // without pulling in a full emoji library.
  const stripped = tag.replace(
    /[\u2600-\u27BF\uFE0F\u{1F300}-\u{1FAFF}]/gu,
    "",
  ).trim();
  return stripped || tag;
}

function _truncateText(text, maxLen) {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen).replace(/\s+\S*$/, "") + "\u2026";
}

/**
 * Pass-through: the backend now stores all timestamps in local time
 * (datetime.now() in lab_notebook.py), so no conversion is needed here.
 * The original date+time pair is returned unchanged for display and for
 * PATCH/DELETE round-trips back to the backend.
 */
function _toLocalDateTime(date, time) {
  return { date, time };
}

/**
 * Mutate a list of entries to use the user's local time zone for date+time.
 * Also stamps each entry with a synthetic id so the edit/delete UI can find
 * it back. The id encodes the ORIGINAL UTC date+time (which is what the
 * backend needs for PATCH/DELETE) — we keep them on the entry as
 * `_utc_date` / `_utc_time` so the localized version is for display only.
 *
 * Then re-sort newest-first since the conversion can change ordering at day
 * boundaries.
 */
function _localizeEntries(entries) {
  let counter = 0;
  for (const e of entries) {
    e._utc_date = e.date;
    e._utc_time = e.time;
    const local = _toLocalDateTime(e.date, e.time);
    e.date = local.date;
    e.time = local.time;
    e._id = `${e._utc_date}_${e._utc_time}_${counter++}`;
  }
  entries.sort((a, b) => {
    const ka = `${a.date}T${a.time}`;
    const kb = `${b.date}T${b.time}`;
    return kb.localeCompare(ka); // newest first
  });
  return entries;
}

/** Find a cached entry by its synthetic id. */
function _findEntryById(id) {
  return _notebookEntries.find((e) => e._id === id);
}

/**
 * Parse a session entry text into a title + body.
 *
 * Session entries from workspace notes look like:
 *   Completed "Refactor X": # Session Summary: Title  Long body...
 *
 * We extract the markdown heading (or the quoted session name) as the title
 * and the rest as the body.
 */
function _parseSessionText(text) {
  if (!text) return { title: "", body: "" };

  // Strip leading "Completed "<name>": " prefix
  const prefixMatch = text.match(/^Completed\s+"([^"]+)"\s*:\s*(.*)$/s);
  let sessionName = "";
  let rest = text;
  if (prefixMatch) {
    sessionName = prefixMatch[1];
    rest = prefixMatch[2];
  }

  // Look for a leading markdown heading: "# Title" or "## Title"
  const headingMatch = rest.match(/^#{1,3}\s+(.+?)(?:\n|$)(.*)$/s);
  if (headingMatch) {
    return {
      title: _stripSummaryPrefix(headingMatch[1].trim()),
      body: headingMatch[2].trim(),
    };
  }

  // Fallback: use the session name as the title and everything as the body
  return {
    title: _stripSummaryPrefix(sessionName || ""),
    body: rest.trim(),
  };
}

/**
 * Parse a run_completed entry into structured fields.
 *
 * Storage format: "Run N [status]: metric (±delta) · Prediction: ... · Outcome: ... · Belief update: ..."
 *
 * Returns:
 *   runNum   — run number string ("72", "?")
 *   status   — "best" | "completed" | "crash" | "timeout"
 *   metric   — "loss=0.312 (-0.042)" or "(no metrics)"
 *   verdict  — "confirmed" | "refuted" | "inconclusive" | ""
 *   segments — remaining "Label: text" segments for the detail rows
 *   title    — first segment (for simple fallback)
 *   body     — remaining text (for simple fallback)
 */
function _parseRunText(text) {
  if (!text) return { title: "", body: "", runNum: "?", status: "", metric: "", verdict: "", segments: [] };
  const parts = text.split(" · ");
  const headline = parts[0] || "";

  // "Run 72 [best]: loss=0.312 (-0.042)"
  const headMatch = headline.match(/^Run\s+(\S+)\s+\[(\w+)\]:\s*(.*)$/);
  const runNum   = headMatch ? headMatch[1] : "?";
  const status   = headMatch ? headMatch[2] : "";
  const metric   = headMatch ? headMatch[3].trim() : headline;

  // Extract verdict from "Prediction: ... → confirmed/refuted/inconclusive"
  let verdict = "";
  for (const seg of parts.slice(1)) {
    const vm = seg.match(/→\s*(confirmed|refuted|inconclusive)\b/i);
    if (vm) { verdict = vm[1].toLowerCase(); break; }
  }

  return {
    title: headline,
    body: parts.slice(1).join(" · "),
    runNum,
    status,
    metric,
    verdict,
    segments: parts.slice(1),
  };
}

/**
 * Strip agent-generated "X Summary:" prefixes from titles. Catches patterns
 * like "Session Summary: X", "Lab Notebook Summary: X", "Summary: X" so the
 * title shows just the meaningful name.
 */
function _stripSummaryPrefix(title) {
  if (!title) return title;
  return title.replace(/^(?:(?:session|lab notebook|notebook)\s+)?summary\s*:\s*/i, "");
}
