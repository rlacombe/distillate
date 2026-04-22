/* ───── Experiments — list, detail, SSE, session polling, notifications ───── */

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

function alertKindTitle(kind) {
  if (kind === "wrong_platform") return "Wrong Platform";
  if (kind === "compute_budget_exceeded") return "Compute Budget Exceeded";
  if (kind === "time_budget_exhausted") return "Time Budget Exhausted";
  if (kind === "gpu_standby_timeout") return "GPU Standby Timeout";
  return "Experiment Alert";
}

function handleSSEEvent(data) {
  // --- session_completed: auto-rescan finished, refresh everything ---
  if (data.type === "session_completed") {
    // Bell notification: mark as needing attention if not currently focused
    const scExpId = data.experiment_id || data.project_id;
    if (scExpId && scExpId !== currentProjectId) {
      sessionDoneBells.add("xp:" + scExpId);
      if (sidebarLeft?.classList.contains("collapsed")) {
        const btn = document.querySelector('.activity-btn[data-pane="sidebar-left"]');
        if (btn) btn.classList.add("has-notification");
      }
    }
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
    const proj = cachedProjects.find((p) => p.id === (data.experiment_id || data.project_id));
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
        prediction: run.prediction || "",
        outcome: run.outcome || "",
        reasoning: run.reasoning || "",
        baseline_comparison: run.baseline_comparison || null,
        started_at: run.timestamp || "",
        duration_minutes: 0,
        tags: [],
      };

      if (!proj.runs) proj.runs = [];
      const existingIdx = proj.runs.findIndex((r) => r.id === runSummary.id);
      if (existingIdx >= 0) {
        // Only advance state — never regress. start_run writes "running",
        // conclude_run later writes "best"/"completed" with the same id.
        // On SSE reconnect the stream replays all of runs.jsonl from offset 0,
        // so "running" lines arrive before "best" lines for already-finished runs;
        // skipping non-terminal updates prevents downgrading completed runs.
        const terminal = ["best", "completed", "crash"];
        if (terminal.includes(runSummary.status) && !terminal.includes(proj.runs[existingIdx].status || "")) {
          Object.assign(proj.runs[existingIdx], runSummary);
        }
      } else {
        proj.runs.push(runSummary);
        proj.run_count = proj.runs.length;
      }

      // Re-render only if experiment detail is actually being viewed. Don't force it to
      // the front if user is viewing Nicolas, Notebook, Papers, or anything else.
      const onPapers = typeof _activeSidebarView !== "undefined" && _activeSidebarView === "papers";
      const detailEl = document.getElementById("experiment-detail");
      const runExpId = data.experiment_id || data.project_id;
      if (!onPapers && currentProjectId === runExpId && detailEl && !detailEl.classList.contains("hidden")) {
        renderProjectDetail(runExpId);
      }
      // Update sidebar counts
      renderProjectsList(cachedProjects);
    }
    // Also notify
    notifyExperimentEvent(data.run);

    // Auto-switch to Control Panel on first onboarding run (skip if user is on Papers)
    if (window._onboardingProjectId && (data.experiment_id || data.project_id) === window._onboardingProjectId) {
      const onPapers = typeof _activeSidebarView !== "undefined" && _activeSidebarView === "papers";
      if (!onPapers) {
        selectProject(window._onboardingProjectId);
        switchEditorTab("control-panel");
      }
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
    const pid = data.experiment_id || data.project_id;
    if (!liveMetrics[pid]) liveMetrics[pid] = [];
    liveMetrics[pid].push(data);
    if (liveMetrics[pid].length > 1000) liveMetrics[pid].splice(0, liveMetrics[pid].length - 1000);

    // Throttle paints to ≤2 Hz per project. A training run can fire a
    // metric_update every few tens of ms; painting on every one pins a
    // fresh canvas backing store (IOSurface) that Chromium can't reclaim
    // until the event stream quiets, which during training is never —
    // observed as tens of GB of shared memory over an hour.
    _scheduleMetricPaint(pid);
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
    // Refresh the Notebook so the session's run summary appears immediately
    if (typeof fetchNotebookEntries === "function") fetchNotebookEntries();
    if (!document.hasFocus() && window.nicolas?.notify) {
      window.nicolas.notify("Session finished", `Experiment session ended for ${data.project_name || data.project_id || "experiment"}`);
    }
    return;
  }

  // --- experiment_alert: wrong platform, budget exhausted, etc. ---
  if (data.type === "experiment_alert" || data.type === "hfjobs_budget_exceeded") {
    const expId = data.experiment_id;
    const kind = data.kind || (data.type === "hfjobs_budget_exceeded" ? "compute_budget_exceeded" : "unknown");
    const msg = data.message || (data.type === "hfjobs_budget_exceeded"
      ? `HF Jobs budget exceeded ($${data.spent_usd} of $${data.budget_usd})`
      : "");
    const ts = data.ts || new Date().toISOString();

    // Merge into cached project (frontend source of truth until next list fetch)
    const proj = cachedProjects.find((p) => p.id === expId);
    if (proj) {
      if (!proj.alerts) proj.alerts = [];
      const alreadyKnown = proj.alerts.some((a) => a.kind === kind && a.ts === ts);
      if (!alreadyKnown) {
        proj.alerts.push({ kind, message: msg, ts, dismissed: false });
        // Re-render sidebar and detail if visible
        if (cachedProjects.length) renderProjectsList(cachedProjects);
        const _onPapers = typeof _activeSidebarView !== "undefined" && _activeSidebarView === "papers";
        if (!_onPapers && currentProjectId === expId) renderProjectDetail(expId);
      }
    }

    // OS notification — always fire (critical signal, unlike run events)
    if (window.nicolas?.notify) {
      const expName = proj?.name || expId;
      window.nicolas.notify(`⚠ ${alertKindTitle(kind)}`, `${expName}: ${msg}`);
    }
    return;
  }

  // --- Default: existing live card + notification behavior ---
  addLiveCard(data);
  notifyExperimentEvent(data);
}

// Per-project trailing debounce for metric-chart + sidebar re-renders.
// Coalesces bursty metric_update events into ≤2 paints/sec. Without this,
// the chart canvas (charts.js renderMetricChart) and the sidebar SVG tree
// are rebuilt on every epoch — each paint pins a new GPU texture and the
// renderer leaks ~10 MB/s of shared memory under live training.
const _metricPaintTimers = {};
const METRIC_PAINT_DELAY_MS = 500;

function _scheduleMetricPaint(pid) {
  if (_metricPaintTimers[pid]) return;
  _metricPaintTimers[pid] = setTimeout(() => {
    delete _metricPaintTimers[pid];
    _paintMetricNow(pid);
  }, METRIC_PAINT_DELAY_MS);
}

function _paintMetricNow(pid) {
  if (currentProjectId === pid) {
    const canvas = document.querySelector("#experiment-detail .metric-chart-canvas");
    if (canvas) {
      const proj = cachedProjects.find((p) => p.id === pid);
      if (proj) {
        const titleEl = document.querySelector("#experiment-detail .metric-chart-title");
        const activeMetric = titleEl ? titleEl.textContent.replace(/\s*[\u2191\u2193]\s*$/, "") : "";
        if (activeMetric) {
          renderMetricChart(canvas, getDisplayRuns(proj.runs), activeMetric, liveMetrics[pid]);
        }
      }
    }
  }

  if (cachedProjects.length) renderProjectsList(cachedProjects);

  if (currentProjectId === pid && typeof updateStatusCardSparkline === "function") {
    const proj = cachedProjects.find((p) => p.id === pid);
    if (proj) updateStatusCardSparkline(proj);
  }
}

// Bound on the number of live event cards held in .exp-runs-grid. The old
// code prepended forever, which left tens of thousands of DOM nodes after
// a long session.
const MAX_LIVE_CARDS = 200;

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

  // Preregistration
  if (event.prediction) {
    const predEl = document.createElement("div");
    predEl.className = "exp-run-prediction";
    predEl.innerHTML = `<span class="prereg-label">predicted</span> ${escapeHtml(event.prediction)}`;
    card.appendChild(predEl);
  }
  if (event.outcome) {
    const outEl = document.createElement("div");
    outEl.className = "exp-run-outcome";
    outEl.innerHTML = `<span class="prereg-label">outcome</span> ${escapeHtml(event.outcome)}`;
    card.appendChild(outEl);
  }

  // Hypothesis (legacy)
  if (event.hypothesis && !event.prediction) {
    const hyp = document.createElement("div");
    hyp.className = "exp-run-meta";
    hyp.textContent = event.hypothesis;
    card.appendChild(hyp);
  }

  // Command (for hook events)
  if (event.command && !event.hypothesis && !event.prediction) {
    const cmd = document.createElement("div");
    cmd.className = "exp-run-meta";
    cmd.textContent = event.command;
    card.appendChild(cmd);
  }

  timeline.prepend(card); // newest first
  while (timeline.children.length > MAX_LIVE_CARDS) {
    timeline.lastElementChild?.remove();
  }
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
      const exps = data.experiments || data.projects;
      if (exps) {
        renderProjectsList(exps);
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
            const exps = listData.experiments || listData.projects;
            if (exps) {
              renderProjectsList(exps);
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
const experimentsFiltersEl = document.getElementById("experiments-sidebar-filters");

// Experiments rail filter/sort/search state. Persisted across re-renders
// triggered by polling/SSE so user selection isn't wiped by live updates.
let currentExpFilter = "all";   // all | active | paused | best
let currentExpSort = "recent";  // recent | oldest | runs
let currentExpSearch = "";

/**
 * Extract the current run's per-epoch training-loss series from the
 * project's liveMetrics event stream.
 *
 * Events accumulate across runs in one session, so this helper filters
 * by ``runStartedAtMs``: only events whose ``ts`` is on/after that
 * cutoff belong to the active run. An unparseable or missing ``ts``
 * falls through to included — better to briefly show a leftover point
 * than to go blank on a formatter quirk.
 *
 * Metric choice: prefer the in-run canonical names (``train_loss``,
 * ``loss``, ``val_loss``) over the project's frontier metric, since
 * the sparkline is about watching *this run* converge, not comparing
 * across runs. Caller can override via ``metricNames``. Returns
 * ``{ metric: string, values: number[] }`` — always defined, with
 * ``values.length >= 2`` only when a trend is actually available.
 */
function liveMetricSeriesForCurrentRun(events, opts) {
  const { runStartedAtMs = null, metricNames = null, keyMetricName = "" } = opts || {};

  const priority = metricNames && metricNames.length
    ? metricNames
    : ["train_loss", "loss", "val_loss", keyMetricName].filter(Boolean);

  if (!Array.isArray(events) || events.length === 0 || priority.length === 0) {
    return { metric: "", values: [] };
  }

  const inRun = [];
  for (const e of events) {
    if (!e || !e.metrics) continue;
    if (runStartedAtMs != null && e.ts) {
      const eventMs = Date.parse(e.ts);
      if (Number.isFinite(eventMs) && eventMs < runStartedAtMs) continue;
    }
    inRun.push(e);
  }
  if (!inRun.length) return { metric: "", values: [] };

  for (const metric of priority) {
    if (!metric) continue;
    const values = [];
    for (const e of inRun) {
      const v = e.metrics[metric];
      if (typeof v === "number" && Number.isFinite(v)) values.push(v);
    }
    if (values.length >= 2) return { metric, values };
  }
  return { metric: "", values: [] };
}
if (typeof window !== "undefined") {
  window.liveMetricSeriesForCurrentRun = liveMetricSeriesForCurrentRun;
}

/**
 * Toggle the breathing green live-dot on the Experiments activity-bar
 * button. Used to signal "an experiment is running" from any view.
 * Idempotent: calling update(true) twice won't duplicate the dot.
 */
function updateExperimentsActivityDot(hasActive) {
  const btn = document.querySelector(
    '#activity-bar [data-sidebar-view="experiments"]',
  );
  if (!btn) return;
  const existing = btn.querySelector(".activity-btn-live-dot");
  if (hasActive) {
    if (existing) return;
    const dot = document.createElement("span");
    dot.className = "activity-btn-live-dot";
    dot.setAttribute("aria-label", "experiment running");
    btn.appendChild(dot);
  } else if (existing) {
    existing.remove();
  }
}
if (typeof window !== "undefined") {
  window.updateExperimentsActivityDot = updateExperimentsActivityDot;
}

function renderProjectsList(projects) {
  // Secondary sort selector (active-first always wins). Applied inside
  // applyExperimentFilterSort before grouping.
  cachedProjects = _sortExperiments(projects, currentExpSort);

  // Reflect activity on the Experiments sidebar icon before any early
  // returns — the dot matters whether or not the sidebar tab is mounted.
  const hasActive = projects.some((p) => p.active_sessions > 0);
  updateExperimentsActivityDot(hasActive);

  if (!experimentsSidebarEl) return;

  // Manage session polling — skip if SSE is already pushing updates
  if (hasActive && !sseSource) startSessionPolling();
  else if (!hasActive || sseSource) stopSessionPolling();

  // Update count badge
  if (experimentsCountEl) {
    experimentsCountEl.textContent = projects.length ? `${projects.length}` : "";
  }

  // Skip full re-render if the experiments view is not active. This prevents
  // focus-stealing DOM thrashing during polling when viewing Nicolas.
  const isExperimentsViewActive = typeof _activeSidebarView !== "undefined" && _activeSidebarView === "experiments";
  if (!isExperimentsViewActive) return;

  if (!projects.length) {
    if (experimentsFiltersEl) experimentsFiltersEl.innerHTML = "";
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

  // Controls above the list (filter + sort + search). Rebuilt every render
  // so filter counts stay in sync with the underlying project set.
  _renderExperimentFilters(projects);

  // Filter + search applied to the already-sorted `projects` array.
  const visible = _filterExperiments(projects, currentExpFilter, currentExpSearch);

  experimentsSidebarEl.innerHTML = "";

  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "sidebar-empty";
    empty.innerHTML = `<p>No experiments match.</p>`;
    experimentsSidebarEl.appendChild(empty);
    return;
  }

  for (const grp of groupExperimentsByProject(visible)) {
    const heading = document.createElement("div");
    heading.className = "sidebar-project-heading";
    if (grp.hasActive) heading.classList.add("has-active");
    const labelEl = document.createElement("span");
    labelEl.className = "sidebar-project-heading-label";
    labelEl.textContent = grp.label;
    heading.appendChild(labelEl);
    const countEl = document.createElement("span");
    countEl.className = "sidebar-project-heading-count";
    countEl.textContent = String(grp.experiments.length);
    heading.appendChild(countEl);
    experimentsSidebarEl.appendChild(heading);

    for (const proj of grp.experiments) {
      experimentsSidebarEl.appendChild(_renderExperimentSidebarItem(proj));
    }
  }
}

/**
 * Sort experiments by the chosen secondary key. Active sessions always
 * float to the top regardless of the selected sort — live runs are the
 * keystone signal and must stay visible.
 *
 *   recent  → added_at desc (matches legacy default)
 *   oldest  → added_at asc
 *   runs    → run_count desc (most active experiment first)
 */
function _sortExperiments(projects, sortKey) {
  const copy = (projects || []).slice();
  copy.sort((a, b) => {
    if (a.active_sessions > 0 && b.active_sessions === 0) return -1;
    if (b.active_sessions > 0 && a.active_sessions === 0) return 1;
    const aDate = a.added_at || "";
    const bDate = b.added_at || "";
    if (sortKey === "oldest") return aDate.localeCompare(bDate);
    if (sortKey === "runs") {
      const ar = a.run_count || (a.runs ? a.runs.length : 0) || 0;
      const br = b.run_count || (b.runs ? b.runs.length : 0) || 0;
      if (ar !== br) return br - ar;
      return bDate.localeCompare(aDate);
    }
    return bDate.localeCompare(aDate); // recent (default)
  });
  return copy;
}
if (typeof window !== "undefined") {
  window._sortExperiments = _sortExperiments;
}

/**
 * Apply the current filter + free-text search. Filter counts shown in
 * the dropdown come from the unfiltered input so the user can see how
 * many are hiding behind each filter.
 */
function _filterExperiments(projects, filter, search) {
  let out = projects.slice();
  if (filter === "active") {
    out = out.filter((p) => (p.active_sessions || 0) > 0);
  } else if (filter === "paused") {
    out = out.filter((p) => !(p.active_sessions > 0));
  } else if (filter === "best") {
    out = out.filter((p) => (p.runs || []).some((r) => (r.decision || "") === "best"));
  }
  if (search) {
    const q = search.toLowerCase();
    out = out.filter((p) => {
      if ((p.name || "").toLowerCase().includes(q)) return true;
      if ((p.id || "").toLowerCase().includes(q)) return true;
      if ((p.workspace_name || "").toLowerCase().includes(q)) return true;
      return false;
    });
  }
  return out;
}
if (typeof window !== "undefined") {
  window._filterExperiments = _filterExperiments;
}

function _renderExperimentFilters(projects) {
  if (!experimentsFiltersEl) return;
  experimentsFiltersEl.innerHTML = "";

  const active = projects.filter((p) => (p.active_sessions || 0) > 0).length;
  const paused = projects.length - active;
  const best = projects.filter((p) => (p.runs || []).some((r) => (r.decision || "") === "best")).length;

  const searchRow = document.createElement("div");
  searchRow.className = "exp-search-row";
  const searchIcon = document.createElement("span");
  searchIcon.className = "exp-search-icon";
  searchIcon.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`;
  const searchInput = document.createElement("input");
  searchInput.type = "text";
  searchInput.className = "exp-search-input";
  searchInput.placeholder = "Search experiments…";
  searchInput.value = currentExpSearch;
  const clearBtn = document.createElement("button");
  clearBtn.className = "exp-search-clear";
  clearBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
  clearBtn.style.display = currentExpSearch ? "flex" : "none";
  searchInput.addEventListener("input", () => {
    currentExpSearch = searchInput.value;
    clearBtn.style.display = currentExpSearch ? "flex" : "none";
    renderProjectsList(cachedProjects);
  });
  clearBtn.addEventListener("click", () => {
    currentExpSearch = "";
    searchInput.value = "";
    clearBtn.style.display = "none";
    renderProjectsList(cachedProjects);
  });
  searchRow.appendChild(searchIcon);
  searchRow.appendChild(searchInput);
  searchRow.appendChild(clearBtn);
  experimentsFiltersEl.appendChild(searchRow);

  const controlsRow = document.createElement("div");
  controlsRow.className = "exp-controls-row";

  const sel = document.createElement("select");
  sel.className = "exp-filter-select";
  const filters = [
    { label: `All  ${projects.length}`, value: "all" },
    { label: `Active  ${active}`, value: "active" },
    { label: `Paused  ${paused}`, value: "paused" },
    { label: `Best  ${best}`, value: "best" },
  ];
  for (const f of filters) {
    const opt = document.createElement("option");
    opt.value = f.value;
    opt.textContent = f.label;
    if (f.value === currentExpFilter) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", () => {
    currentExpFilter = sel.value;
    renderProjectsList(cachedProjects);
  });
  controlsRow.appendChild(sel);

  const SORT_ICONS = {
    recent: { svg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>`, title: "Newest first" },
    oldest: { svg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>`, title: "Oldest first" },
    runs: { svg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`, title: "Most runs" },
  };
  for (const [val, { svg, title }] of Object.entries(SORT_ICONS)) {
    const btn = document.createElement("button");
    btn.className = `exp-sort-icon-btn${val === currentExpSort ? " active" : ""}`;
    btn.title = title;
    btn.innerHTML = svg;
    btn.addEventListener("click", () => {
      if (currentExpSort === val) return;
      currentExpSort = val;
      renderProjectsList(cachedProjects);
    });
    controlsRow.appendChild(btn);
  }

  experimentsFiltersEl.appendChild(controlsRow);
}

/**
 * Group experiments by their parent Project (workspace_id in code).
 *
 * Returns an array of { workspace_id, label, hasActive, experiments }.
 * Groups containing an active experiment sort to the top; among the
 * rest, alphabetical by label. Experiments missing a workspace_id are
 * collected into an 'Unfiled' group at the very bottom.
 *
 * Within each group the input order is preserved -- the caller has
 * already sorted (active first, then by added_at desc).
 */
function groupExperimentsByProject(projects) {
  const byWs = new Map(); // workspace_id -> { label, experiments[] }
  for (const p of (projects || [])) {
    const wid = (p.workspace_id || "").trim();
    // Skip experiments without a workspace (all should be assigned to Workbench now)
    if (!wid) continue;
    if (!byWs.has(wid)) {
      byWs.set(wid, {
        workspace_id: wid,
        label: p.workspace_name || wid,
        experiments: [],
      });
    } else if (p.workspace_name && byWs.get(wid).label === wid) {
      // First entry didn't have a name but this one does; upgrade.
      byWs.get(wid).label = p.workspace_name;
    }
    byWs.get(wid).experiments.push(p);
  }
  const groups = [...byWs.values()].map((g) => ({
    ...g,
    hasActive: g.experiments.some((e) => (e.active_sessions || 0) > 0),
  }));
  // Active first, then alphabetical by label.
  groups.sort((a, b) => {
    if (a.hasActive !== b.hasActive) return a.hasActive ? -1 : 1;
    return a.label.localeCompare(b.label);
  });
  return groups;
}
if (typeof window !== "undefined") {
  window.groupExperimentsByProject = groupExperimentsByProject;
}

/** Build one experiment <div.sidebar-item>. Extracted so the grouping
 *  loop reads cleanly. Behavior preserved from the previous flat render. */
function _renderExperimentSidebarItem(proj) {
  const item = document.createElement("div");
  item.className = `sidebar-item${proj.id === currentProjectId ? " active" : ""}`;
  item.dataset.id = proj.id;

  const icon = document.createElement("span");
  icon.className = "sidebar-item-icon";
  if (proj.active_sessions > 0 && proj.current_run !== "Session active") {
    icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10" class="blink-play"><polygon points="1,0 9,5 1,10" fill="var(--green)"/></svg>`;
    icon.title = "Running";
  } else if (proj.active_sessions > 0) {
    icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="var(--accent)"/></svg>`;
    icon.title = "Ready";
  } else {
    icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" rx="1.5" fill="var(--text-dim)"/></svg>`;
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
  let sidebarTotal = 0, sidebarBest = 0;
  if (proj.runs) {
    sidebarTotal = proj.run_count || proj.runs.length || 0;
    for (const r of getDisplayRuns(proj.runs)) {
      if ((r.decision || "") === "best") sidebarBest++;
    }
  }
  const harnessId = proj.harness_id || proj.agent_type || "claude-code";
  const harnessLabel = harnessId !== "claude-code" && harnessId !== "claude" ? ` \u00B7 ${harnessId}` : "";
  meta.textContent = `${sidebarTotal} runs \u00B7 ${sidebarBest} best${harnessLabel}`;
  nameGroup.appendChild(meta);

  // Live per-run training-loss sparkline (only for actively running
  // experiments with >=2 live metric points). Filters the metric stream
  // to the current run by timestamp so prior runs don't leak in.
  if (proj.active_sessions > 0 && proj.current_run_started
      && typeof liveMetrics !== "undefined" && liveMetrics[proj.id]
      && typeof liveMetricSeriesForCurrentRun === "function"
      && typeof sparklineSvg === "function") {
    const runStartedAtMs = Date.parse(proj.current_run_started);
    const series = liveMetricSeriesForCurrentRun(liveMetrics[proj.id], {
      runStartedAtMs: Number.isFinite(runStartedAtMs) ? runStartedAtMs : null,
      keyMetricName: proj.key_metric_name || "",
    });
    if (series.values.length >= 2) {
      const spark = document.createElement("span");
      spark.className = "run-sparkline";
      spark.title = `${series.metric} — last ${series.values.length} epochs`;
      spark.innerHTML = sparklineSvg(series.values, series.values.length - 1, {
        width: 48, height: 14,
      });
      nameGroup.appendChild(spark);
    }
  }

  item.appendChild(nameGroup);

  if (proj.active_sessions > 0) {
    const badge = document.createElement("span");
    badge.className = "sidebar-item-badge running";
    badge.textContent = proj.active_sessions;
    item.appendChild(badge);
  }

  if (sessionDoneBells.has("xp:" + proj.id)) {
    const bell = document.createElement("span");
    bell.className = "sidebar-session-bell";
    bell.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M8 1.5a.5.5 0 0 1 .5.5v.6A4.5 4.5 0 0 1 12.5 7v2.5l1 1.5H2.5l1-1.5V7a4.5 4.5 0 0 1 4-4.4V2a.5.5 0 0 1 .5-.5ZM6.5 13a1.5 1.5 0 0 0 3 0" fill="var(--gold)"/></svg>`;
    bell.title = "Session finished";
    item.appendChild(bell);
  }

  if (proj.sister_of) {
    item.style.paddingLeft = "28px";
    const agentType = proj.agent_type || "claude";
    const colors = typeof AGENT_COLORS !== "undefined" && AGENT_COLORS[agentType];
    if (colors) {
      icon.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="${colors.dot}"/></svg>`;
      icon.title = agentType;
    }
  }

  const activeAlerts = (proj.alerts || []).filter((a) => !a.dismissed);
  if (activeAlerts.length) {
    const alertBadge = document.createElement("span");
    alertBadge.className = "sidebar-alert-badge";
    alertBadge.textContent = "⚠";
    alertBadge.title = activeAlerts.map((a) => alertKindTitle(a.kind)).join(", ");
    item.appendChild(alertBadge);
  }

  const menuBtn = document.createElement("button");
  menuBtn.className = "sidebar-item-menu-btn";
  menuBtn.title = "Experiment actions";
  menuBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="3" r="1" fill="currentColor"/><circle cx="8" cy="8" r="1" fill="currentColor"/><circle cx="8" cy="13" r="1" fill="currentColor"/></svg>`;
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleExperimentMenu(proj.id, proj.workspace_id, menuBtn);
  });
  item.appendChild(menuBtn);

  item.addEventListener("click", () => selectProject(proj.id));
  return item;
}


// New Experiment wizard — moved to experiment-wizard.js

function refreshExperiments(selectId) {
  if (!serverPort) return;
  fetch(`http://127.0.0.1:${serverPort}/experiments/list`)
    .then((r) => r.json())
    .then((data) => {
      const exps = data.experiments || data.projects;
      if (exps) {
        renderProjectsList(exps);
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
  const editorTabs = document.getElementById("editor-tabs");
  if (editorTabs) editorTabs.classList.add("hidden");
  const tabLabel = document.getElementById("editor-tabs-project-name");
  if (tabLabel) tabLabel.textContent = "";
  document.querySelectorAll("#experiments-sidebar .sidebar-item").forEach((el) => el.classList.remove("active"));
  papersSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => el.classList.remove("active"));
  resetResultsTab();
  resetSetupTab();
  resetCalibrationTab();
  switchEditorTab("control-panel");
  refreshChatSuggestions();

  // If we're in the Papers view, show the papers home page instead of the generic welcome
  if (typeof _activeSidebarView !== "undefined" && _activeSidebarView === "papers" && typeof showPapersHome === "function") {
    showPapersHome();
  } else {
    welcomeEl?.classList.remove("hidden");
  }

  // Sync highlights in the Projects tab
  if (typeof renderWorkspacesList === "function" && typeof _workspaces !== "undefined") {
    renderWorkspacesList(_workspaces);
  }
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
  // Reset log/lin user toggle when switching experiments so each one picks
  // up its natural default (log for lower-is-better, lin for higher-is-better).
  if (typeof chartLogScaleUserSet !== "undefined") chartLogScaleUserSet = false;

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

  // Clear session-done bell for this experiment
  sessionDoneBells.delete("xp:" + projectId);

  // Update sidebar selection + remove bell immediately
  experimentsSidebarEl?.querySelectorAll(".sidebar-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === projectId);
    if (el.dataset.id === projectId) {
      const bell = el.querySelector(".sidebar-session-bell");
      if (bell) bell.remove();
    }
  });

  // Handle terminal session switching if Session tab is visible
  // Skip if terminal is already attached to this project (avoids double-attach race)
  const sessionView = document.getElementById("session-view");
  if (sessionView && !sessionView.classList.contains("hidden") && currentTerminalProject !== projectId) {
    showSessionTerminal(projectId);
  }

  renderProjectDetail(projectId);
  refreshChatSuggestions();

  // Sync highlights in the Projects tab (experiment children under workspaces)
  if (typeof renderWorkspacesList === "function" && typeof _workspaces !== "undefined") {
    renderWorkspacesList(_workspaces);
  }
}


// Experiment detail, settings, backfill, comparison — moved to experiment-detail.js

function launchProject(projectId, model, btn, agent = null, effort = "high", durationMinutes = null) {
  if (!serverPort) return;
  _sessionTransition = "launching";
  btn.disabled = true;
  btn.textContent = "Launching\u2026";
  btn.classList.add("action-btn-spinner");
  showSessionConnecting();
  const _launchBody = { model, agent_type: agent, effort };
  if (durationMinutes) _launchBody.duration_minutes = durationMinutes;
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/launch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(_launchBody),
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

function stopAfterRun(projectId, btn) {
  if (!serverPort) return;
  btn.disabled = true;
  btn.textContent = "Requested…";
  fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projectId)}/stop-after-run`, {
    method: "POST",
  })
    .then((r) => r.json())
    .then(() => fetchExperimentsList())
    .catch((err) => {
      btn.disabled = false;
      btn.textContent = "Stop after run";
      console.error("Stop-after-run failed:", err);
      showToast("Failed to request graceful stop");
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

// ---------------------------------------------------------------------------
// Experiment context menu
// ---------------------------------------------------------------------------

function toggleExperimentMenu(experimentId, currentWorkspaceId, btnEl) {
  const existing = document.querySelector(".experiment-actions-menu");
  if (existing) { existing.remove(); return; }

  const menu = document.createElement("div");
  menu.className = "experiment-actions-menu";
  menu.innerHTML = `
    <button onclick="showChangeExperimentWorkspaceDialog('${experimentId}', '${currentWorkspaceId || ''}');this.closest('.experiment-actions-menu').remove()">
      <span class="menu-item-label">Move to workspace</span>
    </button>
  `;
  btnEl.parentElement.style.position = "relative";
  btnEl.parentElement.appendChild(menu);

  setTimeout(() => {
    const close = (e) => { if (!menu.contains(e.target) && e.target !== btnEl) { menu.remove(); document.removeEventListener("click", close); } };
    document.addEventListener("click", close);
  }, 0);
}

async function showChangeExperimentWorkspaceDialog(experimentId, currentWorkspaceId) {
  let allWorkspaces = [];
  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces`);
    if (resp.ok) {
      const data = await resp.json();
      allWorkspaces = data.workspaces || [];
    }
  } catch (e) { /* ignore */ }

  if (allWorkspaces.length === 0) {
    if (typeof showToast === "function") showToast("No workspaces available", "info");
    return;
  }

  document.querySelector(".modal-overlay.experiment-modal")?.remove();
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay experiment-modal";

  const options = allWorkspaces.map((w) =>
    `<option value="${escapeHtml(w.id)}" ${w.id === currentWorkspaceId ? "selected" : ""}>${escapeHtml(w.name)}</option>`
  ).join("");

  overlay.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h2>Move experiment to workspace</h2>
        <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">&times;</button>
      </div>
      <div class="modal-body">
        <div class="setting-group">
          <label>Select workspace</label>
          <select id="modal-change-workspace" class="modal-input" style="font-family:inherit">${options}</select>
        </div>
        <div class="modal-actions">
          <button class="modal-btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
          <button class="modal-btn-submit" id="modal-change-workspace-btn">Move</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector("#modal-change-workspace-btn").addEventListener("click", async () => {
    const newWorkspaceId = document.getElementById("modal-change-workspace")?.value;
    if (!newWorkspaceId) return;
    await changeExperimentWorkspace(experimentId, newWorkspaceId, overlay);
  });
}

async function changeExperimentWorkspace(experimentId, newWorkspaceId, overlay) {
  if (!newWorkspaceId || !experimentId) return;

  try {
    const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${newWorkspaceId}/experiments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ experiment_id: experimentId }),
    });
    const data = await resp.json();
    if (data.success || data.ok) {
      overlay.remove();
      if (typeof fetchExperimentsList === "function") fetchExperimentsList();
      if (typeof fetchWorkspaces === "function") fetchWorkspaces();
      if (typeof showToast === "function") {
        const wsName = document.querySelector(`#modal-change-workspace option[value="${newWorkspaceId}"]`)?.textContent || "workspace";
        showToast(`Moved to ${wsName}`, "success");
      }
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to move experiment", "error");
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to move experiment", "error");
  }
}

// ── Tray menu → focus experiment ──
// Main process sends "focus-experiment" when the user clicks an auto-experiment
// name in the tray context menu. Switch to Experiments sidebar view and select.
if (window.nicolas?.onFocusExperiment) {
  window.nicolas.onFocusExperiment((experimentId) => {
    if (!experimentId) return;
    const onPapers = typeof _activeSidebarView !== "undefined" && _activeSidebarView === "papers";
    if (!onPapers) {
      // Open the left sidebar if collapsed
      const sidebar = document.getElementById("sidebar-left");
      if (sidebar?.classList.contains("collapsed") && typeof togglePane === "function") {
        togglePane("sidebar-left");
      }
      if (typeof switchSidebarView === "function") switchSidebarView("experiments");
    }
    // Always pre-select so data is ready when user switches manually
    if (typeof selectProject === "function") selectProject(experimentId);
  });
}
