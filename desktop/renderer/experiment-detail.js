/* ───── Experiment Detail — control panel, settings, backfill, comparison ───── */

/**
 * Replace the title <h2> with an inline <input> so the user can rename the
 * experiment. Enter commits (PATCH /experiments/:id {name}); Esc cancels.
 * Blur commits too, matching the macOS rename-in-place pattern.
 */
function _enterTitleEdit(titleEl, proj) {
  if (titleEl.dataset.editing === "1") return;
  titleEl.dataset.editing = "1";

  const original = proj.name || proj.id;
  const input = document.createElement("input");
  input.type = "text";
  input.className = "exp-detail-title exp-detail-title-input";
  input.value = original;
  input.spellcheck = false;

  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let committed = false;
  const restore = (newText) => {
    if (committed) return;
    committed = true;
    const restoredTitle = titleEl.cloneNode(false);
    restoredTitle.className = "exp-detail-title exp-detail-title-editable";
    restoredTitle.textContent = newText;
    restoredTitle.title = "Click to rename";
    restoredTitle.addEventListener("click", () => _enterTitleEdit(restoredTitle, { ...proj, name: newText }));
    input.replaceWith(restoredTitle);
  };

  const commit = () => {
    const next = input.value.trim();
    if (!next || next === original) { restore(original); return; }
    restore(next);
    fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: next }),
    })
      .then((r) => r.json())
      .then((data) => {
        if (data.ok) {
          if (typeof showToast === "function") showToast("Renamed", "success");
          if (typeof fetchExperimentsList === "function") fetchExperimentsList();
        }
      })
      .catch(() => {
        if (typeof showToast === "function") showToast("Rename failed", "error");
      });
  };

  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); commit(); }
    else if (ev.key === "Escape") { ev.preventDefault(); restore(original); }
  });
  input.addEventListener("blur", commit);
}

/**
 * Open a compact dropdown anchored to the hero-metric button, letting the
 * user pick which metric is the "hero" for this experiment. Updates via
 * PATCH /experiments/:id {key_metric_name}. Sending an empty string reverts
 * to auto-detect.
 */
function _openHeroMetricPicker(anchor, proj, allMetricNames) {
  // Dismiss any already-open picker.
  const existing = document.querySelector(".hero-metric-picker");
  if (existing) { existing.remove(); return; }

  const picker = document.createElement("div");
  picker.className = "hero-metric-picker";
  picker.setAttribute("role", "listbox");

  const mkItem = (value, label, isActive) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "hero-metric-picker-item" + (isActive ? " active" : "");
    item.textContent = label;
    item.addEventListener("click", (ev) => {
      ev.stopPropagation();
      picker.remove();
      fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key_metric_name: value }),
      })
        .then((r) => r.json())
        .then((data) => {
          if (data.ok) {
            if (typeof showToast === "function") showToast("Hero metric updated", "success");
            if (typeof fetchExperimentsList === "function") fetchExperimentsList();
          }
        })
        .catch(() => {
          if (typeof showToast === "function") showToast("Failed to update metric", "error");
        });
    });
    return item;
  };

  picker.appendChild(mkItem("", "Auto-detect", !proj.key_metric_name));
  const names = [...allMetricNames];
  names.sort();
  for (const m of names) {
    picker.appendChild(mkItem(m, m, m === proj.key_metric_name));
  }

  // Anchor the picker below the hero button, right-aligned.
  const rect = anchor.getBoundingClientRect();
  picker.style.position = "fixed";
  picker.style.top = `${rect.bottom + 4}px`;
  picker.style.right = `${Math.max(8, window.innerWidth - rect.right)}px`;
  picker.style.zIndex = "1000";
  document.body.appendChild(picker);

  // Dismiss on outside click / Esc.
  const onOutside = (ev) => {
    if (!picker.contains(ev.target) && ev.target !== anchor) {
      picker.remove();
      document.removeEventListener("mousedown", onOutside, true);
      document.removeEventListener("keydown", onKey, true);
    }
  };
  const onKey = (ev) => {
    if (ev.key === "Escape") {
      picker.remove();
      document.removeEventListener("mousedown", onOutside, true);
      document.removeEventListener("keydown", onKey, true);
    }
  };
  setTimeout(() => {
    document.addEventListener("mousedown", onOutside, true);
    document.addEventListener("keydown", onKey, true);
  }, 0);
}


/**
 * Compute the run timer display state.
 *
 * Three phases, driven by the deadlines start_run (L3) writes into the
 * run entry:
 *   - training: now < trainDeadline (model is training)
 *   - wrapping: trainDeadline <= now < wrapDeadline (agent is calling
 *               conclude_run + committing; this is the grace window --
 *               NOT "over budget")
 *   - overdue:  now >= wrapDeadline (L4's on_stop hook will auto-conclude)
 *
 * Legacy runs without deadlines fall back to a single-phase training
 * display using `legacyBudgetSecs` (derived from project.duration_minutes).
 *
 * Returns { phase, elapsedSecs, budgetSecs, timerText, className } where
 * phase === "hidden" means the caller should not render a timer (stale
 * run or clock-skew).
 */
function computeRunTimerState(
  nowMs, runStartMs, trainDeadlineMs, wrapDeadlineMs, legacyBudgetSecs,
) {
  const elapsedMs = nowMs - runStartMs;
  // Prefer the train deadline for the "budget" side of the display so the
  // number the user sees matches the number the wrapper enforces.
  const budgetSecs = (trainDeadlineMs != null)
    ? Math.max(1, Math.round((trainDeadlineMs - runStartMs) / 1000))
    : legacyBudgetSecs;

  if (elapsedMs < 0) return { phase: "hidden" };
  if (elapsedMs > budgetSecs * 3 * 1000) return { phase: "hidden" };

  let phase = "training";
  if (wrapDeadlineMs != null && nowMs >= wrapDeadlineMs) {
    phase = "overdue";
  } else if (trainDeadlineMs != null && nowMs >= trainDeadlineMs) {
    phase = "wrapping";
  }

  const elapsedSecs = Math.floor(elapsedMs / 1000);
  const m = Math.floor(elapsedSecs / 60);
  const s = elapsedSecs % 60;
  const bm = Math.floor(budgetSecs / 60);
  const bs = budgetSecs % 60;
  const timerText = `${m}:${String(s).padStart(2, "0")} / ${bm}:${String(bs).padStart(2, "0")}`;

  const classByPhase = {
    training: "run-timer",
    wrapping: "run-timer run-timer-wrapping",
    overdue: "run-timer run-timer-overdue",
  };
  return {
    phase, elapsedSecs, budgetSecs, timerText,
    className: classByPhase[phase],
  };
}

// Expose for jsdom unit tests (node --test desktop/test/run-timer-state.test.js).
if (typeof window !== "undefined") {
  window.computeRunTimerState = computeRunTimerState;
}

function _modelIdToLabel(id) {
  if (!id) return "";
  if (typeof getSupportedModels === "function") {
    const m = getSupportedModels().find((x) => x.id === id);
    if (m) return m.label;
  }
  // Fallback: strip vendor prefix and prettify
  return id.replace(/^claude-/, "").replace(/^gemini-/, "Gemini ").replace(/-/g, " ").trim();
}

/**
 * Update (or insert) the live per-run training-loss sparkline in the
 * status card of the currently displayed experiment. Called on each
 * metric_update SSE event so the curve grows as epochs complete.
 *
 * Finds the running status-card in the detail panel and updates the
 * ``.status-card-spark`` child in place. No-op if that card isn't on
 * screen (user is on a different project).
 */
function updateStatusCardSparkline(proj) {
  if (!proj || !proj.current_run_started) return;
  const card = document.querySelector(
    "#experiment-detail .exp-detail-status-card.running",
  );
  if (!card) return;
  if (typeof liveMetricSeriesForCurrentRun !== "function"
      || typeof sparklineSvg !== "function") return;
  const pid = proj.id;
  if (typeof liveMetrics === "undefined" || !liveMetrics[pid]) return;

  const runStartMs = new Date(proj.current_run_started).getTime();
  const series = liveMetricSeriesForCurrentRun(liveMetrics[pid], {
    runStartedAtMs: runStartMs,
    keyMetricName: proj.key_metric_name || "",
  });
  if (series.values.length < 2) return;

  let spark = card.querySelector(".status-card-spark");
  if (!spark) {
    spark = document.createElement("span");
    spark.className = "run-sparkline status-card-spark";
    card.appendChild(spark);
  }
  spark.title = `${series.metric} — epoch ${series.values.length} / live`;
  spark.innerHTML = sparklineSvg(
    series.values, series.values.length - 1,
    { width: 72, height: 18 },
  );
}
if (typeof window !== "undefined") {
  window.updateStatusCardSparkline = updateStatusCardSparkline;
}

/**
 * Pick which integer to show as "Run N" in the status card.
 *
 * Prefers the canonical run_number from the backend (propagated via
 * ``/experiments/list`` as ``proj.current_run_number``, itself from
 * ``start_run``). Falls back to ``displayRuns.length + 1`` only for
 * legacy running entries that predate A+B.
 *
 * Using the canonical number keeps the UI, commit messages, and the
 * agent's own summaries aligned on the same integer; the fallback
 * drifts whenever state.runs contains stale or phantom entries.
 */
function displayRunNumber(proj, displayRuns) {
  const n = proj && proj.current_run_number;
  if (typeof n === "number" && Number.isFinite(n) && n > 0) return n;
  return (displayRuns ? displayRuns.length : 0) + 1;
}
if (typeof window !== "undefined") {
  window.displayRunNumber = displayRunNumber;
}

function renderProjectDetail(projectId) {
  const detailEl = document.getElementById("experiment-detail");
  if (!detailEl) return;

  // Switch the center pane to the experiment-detail view BEFORE we look the
  // project up. If we bail out of this function early (proj missing from
  // cache, etc.) without hiding #welcome first, the Nicolas chat / thread
  // sitting inside #welcome stays visible behind a "selected" experiment —
  // which is the bug where clicking an experiment surfaces the latest
  // Nicolas thread instead of the experiment content.
  welcomeEl.classList.add("hidden");
  ["notebook-detail", "vault-detail"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
  });
  detailEl.classList.remove("hidden");

  const proj = cachedProjects.find((p) => p.id === projectId);
  if (!proj) {
    // Cache miss (e.g. clicked an experiment from a workspace before
    // /experiments/list refreshed): show a placeholder + trigger a refresh
    // that will re-enter selectProject(projectId) once the data lands.
    detailEl.innerHTML = '<div class="exp-detail-empty">Loading experiment\u2026</div>';
    if (typeof refreshExperiments === "function") refreshExperiments(projectId);
    return;
  }

  // Clean up any live timers from previous render
  if (window._activeTimers) {
    window._activeTimers.forEach(clearInterval);
    window._activeTimers = [];
  }
  detailEl.innerHTML = "";

  // Alert banners — shown at top of detail for any active (non-dismissed) alerts
  const activeAlerts = (proj.alerts || []).filter((a) => !a.dismissed);
  if (activeAlerts.length) {
    const bannersEl = document.createElement("div");
    bannersEl.className = "exp-alert-banners";
    for (const alert of activeAlerts) {
      const banner = document.createElement("div");
      const kindClass = alert.kind === "wrong_platform" ? "wrong-platform"
        : alert.kind === "gpu_standby_timeout" ? "gpu-standby"
        : alert.kind === "time_budget_exhausted" ? "time-exhausted"
        : "compute-exceeded";
      banner.className = `exp-alert-banner exp-alert-banner--${kindClass}`;
      const titleText = typeof alertKindTitle === "function"
        ? alertKindTitle(alert.kind)
        : alert.kind;
      banner.innerHTML = `
        <span class="exp-alert-banner-icon">⚠</span>
        <span class="exp-alert-banner-body">
          <strong>${escapeHtml(titleText)}</strong>
          <span class="exp-alert-banner-msg">${escapeHtml(alert.message || "")}</span>
        </span>
        <button class="exp-alert-banner-dismiss" data-kind="${escapeHtml(alert.kind)}" title="Dismiss">✕</button>
      `;
      banner.querySelector(".exp-alert-banner-dismiss").addEventListener("click", async (e) => {
        const kind = e.currentTarget.dataset.kind;
        try {
          await fetch(`http://127.0.0.1:${serverPort}/experiments/${projectId}/dismiss-alert`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ kind }),
          });
        } catch (_) {}
        const p = cachedProjects.find((x) => x.id === projectId);
        if (p?.alerts) {
          p.alerts = p.alerts.map((a) => a.kind === kind ? { ...a, dismissed: true } : a);
        }
        renderProjectDetail(projectId);
        if (typeof renderProjectsList === "function") renderProjectsList(cachedProjects);
      });
      bannersEl.appendChild(banner);
    }
    detailEl.appendChild(bannersEl);
  }

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
  title.className = "exp-detail-title exp-detail-title-editable";
  title.textContent = proj.name || proj.id;
  title.title = "Click to rename";
  title.addEventListener("click", () => _enterTitleEdit(title, proj));
  titleLeft.appendChild(title);

  // Dedicated metadata row — badges, timers, stats all align at center on
  // one baseline. Separates metadata visually from the title.
  const metaRow = document.createElement("div");
  metaRow.className = "exp-detail-meta-row";
  titleLeft.appendChild(metaRow);

  // Attention needed (bell) indicator
  const attention = proj.sessions && Object.values(proj.sessions).some(s => s.attention_needed);
  if (attention) {
    const bellBadge = document.createElement("span");
    bellBadge.className = "exp-detail-badge waiting";
    bellBadge.innerHTML = `<span class="badge-bell-icon">\ud83d\udd14</span> action needed`;
    bellBadge.title = "Agent is waiting for your input or approval";
    metaRow.appendChild(bellBadge);
  }

  const isReady = proj.active_sessions > 0 && proj.current_run === "Session active";
  const badge = document.createElement("span");
  if (proj.active_sessions > 0 && !isReady) {
    badge.className = "exp-detail-badge running";
    badge.innerHTML = `<span class="badge-play-icon">\u25B6</span> running`;
    metaRow.appendChild(badge);
  } else if (proj.active_sessions > 0) {
    badge.className = "exp-detail-badge ready";
    badge.innerHTML = `<svg width="8" height="8" viewBox="0 0 8 8" style="margin-right:3px;position:relative;top:0.5px"><circle cx="4" cy="4" r="3.5" fill="currentColor"/></svg> ready`;
    metaRow.appendChild(badge);
  } else {
    badge.className = "exp-detail-badge paused";
    badge.innerHTML = `<svg width="8" height="8" viewBox="0 0 8 8" style="margin-right:3px;position:relative;top:0.5px"><rect x="1" y="1" width="6" height="6" rx="1" fill="currentColor"/></svg> paused`;
    metaRow.appendChild(badge);
  }

  // Harness · model pill (merged — reflects what the session actually runs on).
  // If no model is persisted yet, fall back to claude-sonnet-4-6 (the server
  // default in POST /experiments and the launch endpoint).
  const harnessId = proj.harness_id || proj.agent_type || "claude-code";
  if (harnessId) {
    const hLabel = harnessId === "claude-code" || harnessId === "claude" ? "Claude Code" : harnessId;
    const effectiveModel = proj.model || "claude-sonnet-4-6";
    const mLabel = _modelIdToLabel(effectiveModel);
    const harnessBadge = document.createElement("span");
    harnessBadge.className = "exp-detail-badge harness";
    harnessBadge.textContent = mLabel ? `${hLabel} · ${mLabel}` : hLabel;
    harnessBadge.title = `Running on ${mLabel || hLabel}`;
    metaRow.appendChild(harnessBadge);
  }

  {
    const effortVal = proj.effort || "high";
    const effortBadge = document.createElement("span");
    effortBadge.className = `exp-detail-badge effort effort-${effortVal}`;
    effortBadge.textContent = effortVal;
    metaRow.appendChild(effortBadge);
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
      metaRow.appendChild(timerEl);
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
  // Session wall-clock timer: "2h 15m elapsed" / "2h 15m / 24h budget"
  if (proj.active_sessions > 0 && proj.session_started_at) {
    const sessEl = document.createElement("span");
    sessEl.className = "exp-detail-session-timer";
    metaRow.appendChild(sessEl);
    const sessStartMs = new Date(proj.session_started_at).getTime();
    const budgetSecs = proj.session_budget_seconds;
    const updateSess = () => {
      const el = Math.max(0, Math.floor((Date.now() - sessStartMs) / 1000));
      const eh = Math.floor(el / 3600);
      const em = Math.floor((el % 3600) / 60);
      let text = eh > 0 ? `${eh}h ${em}m` : `${em}m`;
      if (budgetSecs) {
        const bh = Math.floor(budgetSecs / 3600);
        const bm = Math.floor((budgetSecs % 3600) / 60);
        const budgetText = bh > 0 ? `${bh}h${bm > 0 ? ` ${bm}m` : ""}` : `${bm}m`;
        text += ` / ${budgetText} budget`;
      } else {
        text += " elapsed";
      }
      sessEl.textContent = text;
    };
    updateSess();
    const iv = setInterval(updateSess, 1000);
    if (!window._activeTimers) window._activeTimers = [];
    window._activeTimers.push(iv);
  }

  // GPU compute provenance + cost subtitle
  if (proj.compute_spend_usd > 0 || proj.compute) {
    const gpuEl = document.createElement("span");
    gpuEl.className = "exp-detail-gpu-cost";
    const provider = proj.compute?.provider;
    const gpuType  = proj.compute?.gpu_type || "";
    const providerLabel = provider === "hfjobs" ? "HuggingFace" : provider === "modal" ? "Modal" : "";
    const gpuLabel = gpuType ? gpuType.toUpperCase().replace(/-LARGE$|-SMALL$/, "").replace(/X(\d+)$/, "\xd7$1") : "GPU";
    const parts = providerLabel ? [providerLabel, gpuLabel] : [gpuLabel];
    if (proj.compute_spend_usd > 0) parts.push(`$${proj.compute_spend_usd.toFixed(2)} spent`);
    if (proj.compute_jobs_count > 0) parts.push(`${proj.compute_jobs_count} job${proj.compute_jobs_count === 1 ? "" : "s"}`);
    gpuEl.textContent = parts.join(" \u00b7 ");
    metaRow.appendChild(gpuEl);
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
  metaRow.appendChild(statsSpan);
  titleRow.appendChild(titleLeft);

  // Hero metric (right-aligned in title row)
  const heroMetricKey = proj.key_metric_name && allMetricNames.has(proj.key_metric_name)
    ? proj.key_metric_name
    : (allMetricNames.size > 0 ? allMetricNames.values().next().value : "");
  if (heroMetricKey) {
    const heroEl = document.createElement("button");
    heroEl.type = "button";
    heroEl.className = "hero-metric hero-metric-btn";
    heroEl.title = "Click to change the hero metric";
    const bestRun = findBestRun(displayRuns, heroMetricKey);
    const currentVal = bestRun?.results?.[heroMetricKey];
    const direction = isLowerBetter(heroMetricKey) ? "\u2193" : "\u2191";
    heroEl.innerHTML = `
      <div class="hero-metric-value">${formatMetric(heroMetricKey, currentVal)}</div>
      <div class="hero-metric-label">${heroMetricKey} ${direction}</div>
    `;
    heroEl.addEventListener("click", (ev) => {
      ev.stopPropagation();
      _openHeroMetricPicker(heroEl, proj, allMetricNames);
    });
    titleRow.appendChild(heroEl);
  }
  header.appendChild(titleRow);

  // Objective — clamped to 2 lines, click to expand. Keeps the chart above
  // the fold without hiding the experiment's actual goal.
  const descText = proj.experiment_summary || proj.description;
  if (descText) {
    const desc = document.createElement("div");
    desc.className = "exp-detail-objective";
    desc.textContent = descText;
    desc.title = "Click to expand / collapse";
    desc.addEventListener("click", () => desc.classList.toggle("expanded"));
    header.appendChild(desc);
  }

  // Status card: show current_run if running, ready notice if waiting, otherwise latest_learning
  if (proj.current_run && !isReady) {
    const current = document.createElement("div");
    current.className = "exp-detail-status-card running";
    const runNum = displayRunNumber(proj, displayRuns);
    current.innerHTML = `<span class="status-card-label running">Run ${runNum}:</span> ${escapeHtml(proj.current_run)}`;
    header.appendChild(current);

    // Per-run elapsed timer. Reads deadlines written by start_run (L3)
    // and falls back to duration_minutes for legacy entries.
    if (proj.current_run_started) {
      const runStartMs = new Date(proj.current_run_started).getTime();
      const trainDeadlineMs = proj.current_run_train_deadline
        ? new Date(proj.current_run_train_deadline).getTime() : null;
      const wrapDeadlineMs = proj.current_run_wrap_deadline
        ? new Date(proj.current_run_wrap_deadline).getTime() : null;
      const legacyBudgetSecs = (proj.duration_minutes || 5) * 60;

      const initial = computeRunTimerState(
        Date.now(), runStartMs, trainDeadlineMs, wrapDeadlineMs, legacyBudgetSecs,
      );
      if (initial.phase !== "hidden") {
        const timerSpan = document.createElement("span");
        timerSpan.className = initial.className;
        timerSpan.textContent = initial.timerText;
        current.appendChild(timerSpan);

        const updateRunTimer = () => {
          const s = computeRunTimerState(
            Date.now(), runStartMs, trainDeadlineMs, wrapDeadlineMs, legacyBudgetSecs,
          );
          if (s.phase === "hidden") {
            timerSpan.textContent = "";
            timerSpan.className = "run-timer";
            return;
          }
          timerSpan.textContent = s.timerText;
          timerSpan.className = s.className;
        };
        const iv = setInterval(updateRunTimer, 1000);
        if (!window._activeTimers) window._activeTimers = [];
        window._activeTimers.push(iv);
      }

      // Live loss sparkline next to the Run N: label — shows the
      // current run's per-epoch training-loss curve as it's drawn.
      // Refreshes on every metric_update SSE event (handler in
      // experiments.js calls renderProjectDetail).
      if (typeof liveMetrics !== "undefined" && liveMetrics[proj.id]
          && typeof liveMetricSeriesForCurrentRun === "function"
          && typeof sparklineSvg === "function") {
        const series = liveMetricSeriesForCurrentRun(liveMetrics[proj.id], {
          runStartedAtMs: runStartMs,
          keyMetricName: proj.key_metric_name || "",
        });
        if (series.values.length >= 2) {
          const live = document.createElement("span");
          live.className = "run-sparkline status-card-spark";
          live.title = `${series.metric} — epoch ${series.values.length} / live`;
          live.innerHTML = sparklineSvg(
            series.values, series.values.length - 1,
            { width: 72, height: 18 },
          );
          current.appendChild(live);
        }
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
    const isStopping = Object.values(proj.sessions || {}).some(
      (s) => s.agent_status === "stopping"
    );

    const stopBtn = document.createElement("button");
    stopBtn.className = "action-btn action-btn-stop";
    stopBtn.textContent = "Stop";
    stopBtn.addEventListener("click", () => stopProject(proj.id, stopBtn));
    actions.appendChild(stopBtn);

    if (isStopping) {
      const finishingBtn = document.createElement("button");
      finishingBtn.className = "paper-action-btn";
      finishingBtn.textContent = "Finishing run…";
      finishingBtn.disabled = true;
      actions.appendChild(finishingBtn);
    } else {
      const gracefulBtn = document.createElement("button");
      gracefulBtn.className = "paper-action-btn";
      gracefulBtn.textContent = "Stop after run";
      gracefulBtn.title = "Finish the current run, then stop";
      gracefulBtn.addEventListener("click", () => stopAfterRun(proj.id, gracefulBtn));
      actions.appendChild(gracefulBtn);
    }

    const attachBtn = document.createElement("button");
    attachBtn.className = "paper-action-btn";
    attachBtn.textContent = "Open in Terminal";
    attachBtn.title = "Detach session to Terminal.app";
    attachBtn.addEventListener("click", () => attachToProject(proj.id, attachBtn));
    actions.appendChild(attachBtn);
  } else {
    const durationSel = document.createElement("select");
    durationSel.title = "Iteration time per run";
    durationSel.style.cssText = "background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:11px;padding:4px 6px;cursor:pointer;";
    const currentDuration = proj.duration_minutes || 5;
    for (const m of [5, 10, 15, 30, 60]) {
      const o = document.createElement("option");
      o.value = m;
      o.textContent = `${m} min`;
      if (m === currentDuration) o.selected = true;
      durationSel.appendChild(o);
    }
    if (![5, 10, 15, 30, 60].includes(currentDuration)) {
      const o = document.createElement("option");
      o.value = currentDuration;
      o.textContent = `${currentDuration} min`;
      o.selected = true;
      durationSel.insertBefore(o, durationSel.firstChild);
    }
    actions.appendChild(durationSel);

    const launchBtn = document.createElement("button");
    launchBtn.className = "action-btn action-btn-launch";
    launchBtn.textContent = "Launch";
    launchBtn.addEventListener("click", () => {
      launchProject(
        proj.id,
        proj.model || "claude-sonnet-4-6",
        launchBtn,
        proj.agent_type || proj.harness_id || "claude",
        proj.effort || "high",
        parseInt(durationSel.value, 10),
      );
    });
    actions.appendChild(launchBtn);

  }

  // GitHub repo link or create button
  if (proj.github_url) {
    const ghBtn = document.createElement("a");
    ghBtn.className = "paper-action-btn";
    ghBtn.textContent = "GitHub";
    ghBtn.href = proj.github_url;
    ghBtn.target = "_blank";
    ghBtn.title = proj.github_url;
    actions.appendChild(ghBtn);
  } else {
    const ghBtn = document.createElement("button");
    ghBtn.className = "paper-action-btn";
    ghBtn.textContent = "GitHub";
    ghBtn.title = "Create GitHub repository for this experiment";
    ghBtn.addEventListener("click", async () => {
      ghBtn.disabled = true;
      ghBtn.textContent = "Creating\u2026";
      try {
        const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/github`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: `distillate-xp-${proj.id}`, private: false }),
        });
        const d = await r.json();
        if (d.ok && d.url) {
          const link = document.createElement("a");
          link.className = "paper-action-btn";
          link.textContent = "GitHub";
          link.href = d.url;
          link.target = "_blank";
          link.title = d.url;
          ghBtn.replaceWith(link);
        } else {
          ghBtn.textContent = d.reason || "Failed";
          setTimeout(() => { ghBtn.textContent = "GitHub"; ghBtn.disabled = false; }, 3000);
        }
      } catch {
        ghBtn.textContent = "Error";
        setTimeout(() => { ghBtn.textContent = "GitHub"; ghBtn.disabled = false; }, 3000);
      }
    });
    actions.appendChild(ghBtn);
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

  // Actions live at the bottom of the header as a proper toolbar row
  // (separated by a top border in CSS). Keeps primary verbs prominent
  // without fighting the title for attention.
  header.appendChild(actions);

  detailEl.appendChild(header);

  // GPU Jobs section (only for HF Jobs experiments with active/completed jobs)
  if (proj.compute_jobs_count > 0 || (proj.compute?.provider === "hfjobs" && proj.active_sessions > 0)) {
    const jobsSection = document.createElement("div");
    jobsSection.className = "gpu-jobs-section";
    jobsSection.innerHTML = `<div class="gpu-jobs-header">GPU Jobs</div><div class="gpu-jobs-list">Loading\u2026</div>`;
    detailEl.appendChild(jobsSection);

    const loadJobs = async () => {
      try {
        const resp = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/jobs`);
        const data = await resp.json();
        if (!data.ok || !data.jobs?.length) {
          jobsSection.style.display = "none";
          return;
        }
        const listEl = jobsSection.querySelector(".gpu-jobs-list");
        listEl.innerHTML = "";
        for (const j of data.jobs) {
          const row = document.createElement("div");
          row.className = "gpu-job-row";
          const dur = j.duration_seconds ? `${Math.round(j.duration_seconds / 60)}m` : "\u2014";
          const cost = j.cost_usd > 0 ? `$${j.cost_usd.toFixed(2)}` : "\u2014";
          const statusClass = j.status === "running" ? "running" : (j.status === "completed" ? "done" : "");
          row.innerHTML = `<span class="gpu-job-id">${j.job_id.slice(0, 10)}</span>`
            + `<span class="gpu-job-flavor">${j.flavor}</span>`
            + `<span class="gpu-job-status ${statusClass}">${j.status}</span>`
            + `<span class="gpu-job-dur">${dur}</span>`
            + `<span class="gpu-job-cost">${cost}</span>`;
          if (j.status === "running") {
            const cancelBtn = document.createElement("button");
            cancelBtn.className = "gpu-job-cancel";
            cancelBtn.textContent = "Cancel";
            cancelBtn.addEventListener("click", async () => {
              cancelBtn.disabled = true;
              await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/jobs/${j.job_id}/cancel`, { method: "POST" });
              loadJobs();
            });
            row.appendChild(cancelBtn);
          }
          listEl.appendChild(row);
        }
        jobsSection.querySelector(".gpu-jobs-header").textContent = `GPU Jobs (${data.jobs.length})`;
      } catch (_) {
        jobsSection.style.display = "none";
      }
    };
    loadJobs();
    // Poll every 30s while session is active
    if (proj.active_sessions > 0) {
      const jobsPollIv = setInterval(loadJobs, 30000);
      if (!window._activeTimers) window._activeTimers = [];
      window._activeTimers.push(jobsPollIv);
    }
  }

  // Metric chart
  let activeMetric = "";

  // Show placeholder when no runs yet
  if (displayRuns.length === 0) {
    const emptyState = document.createElement("div");
    emptyState.className = "metric-chart-empty-state";
    emptyState.innerHTML = `
      <div class="empty-state-icon">📈</div>
      <div class="empty-state-text">No runs yet</div>
      <div class="empty-state-hint">Launch an experiment to see results on the chart</div>
    `;
    detailEl.appendChild(emptyState);
  } else if (allMetricNames.size > 0 && displayRuns.length >= 1) {
    // Metric priority: declared key_metric > ratio > loss > generic > count > time > cost > hyperparameter
    const _METRIC_PRIORITY = ["ratio", "loss", "generic", "count", "time", "cost", "hyperparameter"];
    const defaultMetric = proj.key_metric_name && allMetricNames.has(proj.key_metric_name)
      ? proj.key_metric_name
      : [...allMetricNames].sort((a, b) =>
          _METRIC_PRIORITY.indexOf(classifyMetric(a)) - _METRIC_PRIORITY.indexOf(classifyMetric(b))
        )[0];
    activeMetric = defaultMetric;

    // Default log/lin: log for lower-is-better (loss) metrics, lin for
    // higher-is-better (accuracy). The user's explicit toggle (if any)
    // overrides this until they switch metrics.
    const effectiveLogScale = () =>
      chartLogScaleUserSet ? chartLogScale : isLowerBetter(activeMetric);

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
    exportBtn.addEventListener("click", async () => {
      if (!activeMetric) return;
      exportBtn.disabled = true;
      try {
        const scale = effectiveLogScale();
        const dataUrl = await exportChartAsPng(canvas, displayRuns, activeMetric,
          proj.name || proj.id, { logScale: scale, summary: proj.experiment_summary || proj.description || "" });
        triggerChartDownload(dataUrl, proj.name || proj.id, activeMetric, scale);
      } catch (e) {
        console.error("Chart export failed:", e);
      }
      exportBtn.disabled = false;
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
        // Switching metrics resets the user's toggle so the new metric
        // gets its natural default (log for loss, lin for accuracy).
        chartLogScaleUserSet = false;
        const scale = effectiveLogScale();
        logToggle.classList.toggle("active", scale);
        renderMetricChart(canvas, displayRuns, activeMetric, liveMetrics[proj.id], { logScale: scale });
      });
      chartControls.appendChild(selector);
    }

    // Log/linear toggle (two-state on/off)
    const logToggle = document.createElement("button");
    logToggle.className = "chart-log-toggle";
    logToggle.title = "Toggle log scale";
    logToggle.innerHTML = `<span class="chart-log-toggle-label chart-log-toggle-lin">lin</span><span class="chart-log-toggle-label chart-log-toggle-log">log</span>`;
    if (effectiveLogScale()) logToggle.classList.add("active");
    logToggle.addEventListener("click", () => {
      chartLogScale = !effectiveLogScale();
      chartLogScaleUserSet = true;
      logToggle.classList.toggle("active", chartLogScale);
      renderMetricChart(canvas, displayRuns, activeMetric, liveMetrics[proj.id], { logScale: chartLogScale });
    });
    chartControls.appendChild(logToggle);

    chartControls.appendChild(exportBtn);

    // Compare Agents button
    const compareBtn = document.createElement("button");
    compareBtn.className = "chart-export-btn";
    compareBtn.title = "Compare agents on this experiment";
    compareBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/></svg>`;
    compareBtn.addEventListener("click", () => showCompareAgentsModal(proj, displayRuns, activeMetric, canvas));
    chartControls.appendChild(compareBtn);

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
        renderMetricChart(canvas, displayRuns, activeMetric, liveMetrics[proj.id], { logScale: effectiveLogScale() });
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
    requestAnimationFrame(async () => {
      // Check for sister projects — if found, auto-show comparison chart
      let showedComparison = false;
      if (proj.sister_of || cachedProjects?.some?.((p) => p.sister_of === proj.id)) {
        try {
          const sr = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}/sisters`);
          const sd = await sr.json();
          if (sd.ok && sd.family && sd.family.length > 1) {
            renderComparisonChart(canvas, sd.family, activeMetric);
            showComparisonSummary(sd.family, activeMetric, canvas);
            showedComparison = true;
          }
        } catch (_) {}
      }
      if (!showedComparison) {
        renderMetricChart(canvas, displayRuns, activeMetric, liveMetrics[proj.id], { logScale: effectiveLogScale() });
      }
      setupChartResize(chartContainer, canvas, displayRuns, () => activeMetric, () => proj.id, () => ({ logScale: effectiveLogScale() }));
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
  } else if (visibleTab === "calibration") {
    loadCalibration(projectId);
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
    if (typeof _showModal !== "function") return;
    popover.remove();
    _showModal({
      title: "Rename Experiment",
      fields: [
        { id: "name", label: "Name", value: current, autofocus: true },
      ],
      submitLabel: "Rename",
      onSubmit: (vals, overlay) => {
        if (!vals.name || vals.name === current) { overlay.remove(); return; }
        fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(proj.id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: vals.name }),
        })
          .then((r) => r.json())
          .then((data) => {
            overlay.remove();
            if (data.ok) fetchExperimentsList();
          })
          .catch(() => { overlay.remove(); });
      },
    });
  });

  // Backfill runs — inject backfill instructions into the running session
  const backfillBtn = document.createElement("button");
  backfillBtn.className = "action-btn";
  backfillBtn.textContent = "Backfill via agent";
  backfillBtn.title = "Instructs the running agent to discover missing runs from git history";
  backfillBtn.addEventListener("click", () => {
    if (typeof _showConfirm !== "function") return;
    popover.remove();
    _showConfirm({
      title: "Backfill Runs",
      message: "This will instruct the agent to scan git history and log all missing runs. This may take a few minutes.",
      confirmLabel: "Start Backfill",
      danger: false,
      onConfirm: () => startBackfill(proj),
    });
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
    if (typeof _showConfirm !== "function") return;
    _showConfirm({
      title: "Delete Experiment",
      message: `Remove "${name}" from tracking? This removes ${proj.run_count || 0} run(s) from Distillate. Source files and GitHub repo will not be deleted.`,
      confirmLabel: "Delete",
      danger: true,
      onConfirm: () => {
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
              resetResultsTab();
              resetSetupTab();
            } else {
              if (typeof showToast === "function") showToast(data.reason || "Failed to delete", "error");
            }
          })
          .catch((err) => {
            if (typeof showToast === "function") showToast("Network error: " + err.message, "error");
          });
      },
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

    const attachResult = await attachToTerminalSession(proj.id, sessionName);
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
      // Switch to session tab to watch (must switch BEFORE attach so container is visible)
      switchEditorTab("session");
      if (data.tmux_session) {
        await attachToTerminalSession(proj.id, data.tmux_session);
      }
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

