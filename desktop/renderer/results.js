/* ───── Results + Prompt — results tab, prompt editor ───── */

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
      body.innerHTML += `<div class="insight-breakthrough"><span class="insight-section-label">Key Breakthrough</span>${window.markedParse(proj.insights.key_breakthrough)}</div>`;
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
    const totalRuns = proj.run_count || proj.runs.length || 0;
    sTitle.textContent = totalRuns !== displayRuns.length
      ? `Runs (${displayRuns.length} of ${totalRuns})`
      : `Runs (${displayRuns.length})`;
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

        // Per-run training-loss sparkline (Tier A): each row shows
        // THIS run's own convergence curve from metrics_series (the
        // hook-emitted per-epoch metric_update events frozen onto the
        // run by conclude_run), NOT the cross-run frontier. If this
        // run has no series, fall back to the frontier sparkline so
        // legacy runs still show something.
        if (typeof liveMetricSeriesForCurrentRun === "function"
            && Array.isArray(run.metrics_series) && run.metrics_series.length >= 2) {
          const series = liveMetricSeriesForCurrentRun(run.metrics_series, {
            keyMetricName: activeMetric || run.key_metric_name || "",
          });
          if (series.values.length >= 2) {
            const sparkEl = document.createElement("span");
            sparkEl.className = "run-sparkline";
            sparkEl.title = `${series.metric} — ${series.values.length} epochs`;
            sparkEl.innerHTML = sparklineSvg(
              series.values, series.values.length - 1,
            );
            runHeader.appendChild(sparkEl);
          }
        } else if (activeMetric) {
          // Legacy fallback: cross-run frontier mini for runs that
          // predate metrics_series (no per-epoch data captured).
          const sparkValues = [];
          for (let si = 0; si <= origIndex; si++) {
            const sv = runsInOrder[si].results?.[activeMetric];
            if (typeof sv === "number" && isFinite(sv)) sparkValues.push(sv);
          }
          if (sparkValues.length >= 2) {
            const sparkEl = document.createElement("span");
            sparkEl.className = "run-sparkline run-sparkline-legacy";
            sparkEl.title = `${activeMetric} across prior runs (no per-epoch curve)`;
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

        // Preregistration: prediction → outcome
        if (run.prediction || run.outcome) {
          const preregEl = document.createElement("div");
          preregEl.className = "exp-run-prereg";
          if (run.prediction) {
            const predEl = document.createElement("div");
            predEl.className = "exp-run-prediction";
            predEl.innerHTML = `<span class="prereg-label">predicted</span> ${escapeHtml(run.prediction)}`;
            preregEl.appendChild(predEl);
          }
          if (run.outcome) {
            const outEl = document.createElement("div");
            outEl.className = "exp-run-outcome";
            outEl.innerHTML = `<span class="prereg-label">outcome</span> ${escapeHtml(run.outcome)}`;
            preregEl.appendChild(outEl);
          }
          card.appendChild(preregEl);
        } else if (run.hypothesis) {
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

        // Checkpoint link for [best] runs
        if (run.checkpoint_url) {
          const ckpt = document.createElement("div");
          ckpt.style.cssText = "font-size:10px; margin-top:4px;";
          ckpt.innerHTML = `<a href="${escapeHtml(run.checkpoint_url)}" style="color:var(--accent); text-decoration:none;" target="_blank">\u{1F4E6} checkpoint</a>`;
          card.appendChild(ckpt);
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
