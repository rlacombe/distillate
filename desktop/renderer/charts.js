/* ───── Charts — metric classification, run helpers, chart rendering ───── */

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
    const bestVal = best?.results?.[metricName];
    const isBetter = lower ? v < bestVal : v > bestVal;
    const isTie = v === bestVal;
    const hasHigherRunNum = (r.run_number || 0) > (best?.run_number || 0);
    if (!best || isBetter || (isTie && hasHigherRunNum))
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

// Agent comparison colors
const AGENT_COLORS = {
  claude: { line: "rgba(138,128,216,0.85)", dot: "#a89eef", frontier: "rgba(138,128,216,0.45)", label: "Claude" },
  codex: { line: "rgba(16,185,129,0.8)", dot: "#10b981", frontier: "rgba(16,185,129,0.5)", label: "Codex" },
  gemini: { line: "rgba(245,158,11,0.8)", dot: "#f59e0b", frontier: "rgba(245,158,11,0.5)", label: "Gemini" },
  opencode: { line: "rgba(56,189,248,0.8)", dot: "#38bdf8", frontier: "rgba(56,189,248,0.5)", label: "OpenCode" },
  openclaw: { line: "rgba(244,63,94,0.8)", dot: "#f43f5e", frontier: "rgba(244,63,94,0.5)", label: "OpenClaw" },
  pi: { line: "rgba(236,72,153,0.8)", dot: "#ec4899", frontier: "rgba(236,72,153,0.5)", label: "Pi" },
};
const AGENT_COLOR_LIST = Object.values(AGENT_COLORS);

function renderComparisonChart(canvas, family, metricName, opts = {}) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const targetW = Math.round(rect.width * dpr);
  const targetH = Math.round(rect.height * dpr);
  if (canvas.width !== targetW || canvas.height !== targetH) {
    canvas.width = targetW;
    canvas.height = targetH;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = rect.width, H = rect.height;

  const pad = { top: 20, right: 20, bottom: 40, left: 50 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  // Background
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || "#0f0f23";
  ctx.fillRect(0, 0, W, H);

  const lowerBetter = isLowerBetter(metricName);

  // Collect all agent series
  const series = [];
  let globalMin = Infinity, globalMax = -Infinity;
  let maxRuns = 0;

  for (const member of family) {
    const agentType = member.agent_type || "claude";
    const colors = AGENT_COLORS[agentType] || AGENT_COLOR_LIST[series.length % AGENT_COLOR_LIST.length];
    const runs = getDisplayRuns(member.runs || []);
    const points = [];
    for (let i = 0; i < runs.length; i++) {
      const val = runs[i].results?.[metricName];
      if (typeof val === "number" && isFinite(val)) {
        points.push({ x: i, y: val, best: (runs[i].decision || "") === "best" });
        globalMin = Math.min(globalMin, val);
        globalMax = Math.max(globalMax, val);
      }
    }
    maxRuns = Math.max(maxRuns, runs.length);
    if (points.length > 0) {
      series.push({ agent: agentType, label: colors.label || agentType, colors, points, runCount: runs.length });
    }
  }

  if (series.length === 0 || maxRuns === 0) {
    ctx.fillStyle = "rgba(136,136,160,0.4)";
    ctx.font = "13px system-ui";
    ctx.textAlign = "center";
    ctx.fillText("No data to compare yet", W / 2, H / 2);
    return;
  }

  // Y range with padding
  const yRange = globalMax - globalMin || 1;
  const yMin = globalMin - yRange * 0.05;
  const yMax = globalMax + yRange * 0.05;

  const xScale = (x) => pad.left + (x / Math.max(1, maxRuns - 1)) * plotW;
  const yScale = (y) => pad.top + plotH - ((y - yMin) / (yMax - yMin)) * plotH;

  // Grid lines
  ctx.strokeStyle = "rgba(136,136,160,0.1)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (plotH / 4) * i;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(W - pad.right, y); ctx.stroke();
  }

  // Y-axis labels
  ctx.fillStyle = "rgba(136,136,160,0.5)";
  ctx.font = "10px system-ui";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const val = yMax - ((yMax - yMin) / 4) * i;
    ctx.fillText(val.toPrecision(3), pad.left - 10, pad.top + (plotH / 4) * i + 3);
  }

  // X-axis label
  ctx.textAlign = "center";
  ctx.fillText("Run #", W / 2, H - 4);

  // Draw each agent's frontier line + dots
  for (const s of series) {
    const pts = s.points;
    if (pts.length === 0) continue;

    // Scale x per-agent (each has its own run count)
    const axScale = (x) => pad.left + (x / Math.max(1, maxRuns - 1)) * plotW;

    // Frontier line
    ctx.strokeStyle = s.colors.frontier;
    ctx.lineWidth = 2;
    ctx.beginPath();
    let frontierVal = null;
    for (const p of pts) {
      let isFrontier = false;
      if (frontierVal === null) { isFrontier = true; }
      else if (lowerBetter && p.y < frontierVal) { isFrontier = true; }
      else if (!lowerBetter && p.y > frontierVal) { isFrontier = true; }
      if (isFrontier) {
        if (frontierVal !== null) ctx.lineTo(axScale(p.x), yScale(p.y));
        else ctx.moveTo(axScale(p.x), yScale(p.y));
        frontierVal = p.y;
      }
    }
    ctx.stroke();

    // Dots
    for (const p of pts) {
      ctx.beginPath();
      ctx.arc(axScale(p.x), yScale(p.y), p.best ? 4 : 2.5, 0, Math.PI * 2);
      ctx.fillStyle = p.best ? s.colors.dot : s.colors.frontier;
      ctx.fill();
    }

    // Latest-run halo — soft luminous ring on the current run's dot.
    // Matches the v6 mockup: small glow on the frontier's newest point only.
    const last = pts[pts.length - 1];
    if (last) {
      const lx = axScale(last.x);
      const ly = yScale(last.y);
      ctx.save();
      ctx.shadowBlur = 10;
      ctx.shadowColor = s.colors.dot;
      ctx.strokeStyle = s.colors.dot;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(lx, ly, 7, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      ctx.strokeStyle = s.colors.frontier;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(lx, ly, 10, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  // Legend
  const legendX = pad.left + 8;
  let legendY = pad.top + 12;
  ctx.font = "11px system-ui";
  for (const s of series) {
    ctx.fillStyle = s.colors.dot;
    ctx.beginPath();
    ctx.arc(legendX, legendY, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "rgba(200,200,220,0.8)";
    ctx.textAlign = "left";
    const bestVal = s.points.reduce((best, p) => {
      if (best === null) return p.y;
      return lowerBetter ? Math.min(best, p.y) : Math.max(best, p.y);
    }, null);
    ctx.fillText(`${s.label} (${s.points.length} runs, best: ${bestVal?.toPrecision(3) || "?"})`, legendX + 10, legendY + 4);
    legendY += 18;
  }

  // Hover tooltip — find nearest point across all agents
  const container = canvas.parentElement;
  if (container) {
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
      for (const s of series) {
        const axScale = (x) => pad.left + (x / Math.max(1, maxRuns - 1)) * plotW;
        for (const p of s.points) {
          const px = axScale(p.x);
          const py = yScale(p.y);
          const dist = Math.sqrt((mx - px) ** 2 + (my - py) ** 2);
          if (dist < closestDist && dist < 20) {
            closestDist = dist;
            closest = {
              x: px,
              y: py,
              agent: s.label,
              runNum: p.x + 1,
              value: p.y,
              isBest: p.best,
            };
          }
        }
      }
      if (closest) {
        const html = `<div class="tt-head">
                        <span class="dot" style="display:${closest.isBest ? '' : 'none'};"></span>
                        <span class="name">${closest.agent} · run ${closest.runNum}</span>
                      </div>
                      <div class="tt-row"><span class="k">${metricName}</span><span class="v${closest.isBest ? ' good' : ''}">${closest.value.toPrecision(4)}</span></div>`;
        tooltip.innerHTML = html;
        tooltip.style.display = "block";
        let tx = closest.x + 6;
        let ty = closest.y - 28;
        if (tx + 200 > W) tx = closest.x - 206;
        if (ty < 0) ty = closest.y + 10;
        tooltip.style.left = tx + "px";
        tooltip.style.top = ty + "px";
      } else {
        tooltip.style.display = "none";
      }
    };
    canvas.onmouseleave = () => { tooltip.style.display = "none"; };
  }
}

async function showCompareAgentsModal(proj, displayRuns, activeMetric, canvas) {
  // Check if this project already has sisters
  const projId = proj.id;
  let hasSisters = false;
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projId)}/sisters`);
    const data = await r.json();
    if (data.ok && data.family && data.family.length > 1) {
      hasSisters = true;
      // Switch to comparison view
      renderComparisonChart(canvas, data.family, activeMetric);
      // Show comparison summary below chart
      showComparisonSummary(data.family, activeMetric, canvas);
      return;
    }
  } catch (_) {}

  // No sisters yet — show agent picker modal
  const overlay = document.createElement("div");
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:1000;display:flex;align-items:center;justify-content:center;";
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });

  const modal = document.createElement("div");
  modal.style.cssText = "background:var(--bg-surface, #1a1a2e);border:1px solid var(--border);border-radius:12px;padding:20px;min-width:340px;max-width:400px;";

  modal.innerHTML = `
    <h3 style="margin:0 0 4px;font-size:14px;color:var(--text);">Compare Agents</h3>
    <p style="font-size:12px;color:var(--text-dim);margin:0 0 16px;">Launch the same experiment with different agents and compare their frontiers side-by-side.</p>
    <div id="compare-agents-list" style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px;"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;">
      <button id="compare-cancel" class="wizard-btn-cancel" style="font-size:12px;padding:6px 14px;">Cancel</button>
      <button id="compare-launch" class="wizard-btn-create" style="font-size:12px;padding:6px 14px;" disabled>Launch comparison</button>
    </div>
    <div id="compare-status" style="font-size:11px;color:var(--text-dim);margin-top:8px;display:none;"></div>
  `;

  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  // Escape to dismiss
  const _escHandler = (e) => {
    if (e.key === "Escape") {
      overlay.remove();
      document.removeEventListener("keydown", _escHandler);
    }
  };
  document.addEventListener("keydown", _escHandler);
  const _origRemove = overlay.remove.bind(overlay);
  overlay.remove = () => {
    document.removeEventListener("keydown", _escHandler);
    _origRemove();
  };

  // Populate agent checkboxes
  const listEl = modal.querySelector("#compare-agents-list");
  const launchBtn = modal.querySelector("#compare-launch");
  const statusEl = modal.querySelector("#compare-status");
  const selected = new Set();

  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/agents`);
    const data = await r.json();
    const currentAgent = proj.agent_type || "claude";

    for (const ag of (data.agents || [])) {
      if (ag.id === currentAgent) continue; // Skip the parent's agent
      const row = document.createElement("label");
      row.style.cssText = "display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:8px;border:1px solid var(--border);cursor:pointer;font-size:12px;color:var(--text);";
      if (!ag.available) row.style.opacity = "0.4";

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.disabled = !ag.available;
      cb.value = ag.id;
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(ag.id); else selected.delete(ag.id);
        launchBtn.disabled = selected.size === 0;
      });

      const dot = document.createElement("span");
      const colors = AGENT_COLORS[ag.id] || { dot: "#888" };
      dot.style.cssText = `width:8px;height:8px;border-radius:50%;background:${colors.dot};flex-shrink:0;`;

      const text = document.createElement("span");
      text.textContent = ag.label + (ag.available ? "" : " (not installed)");
      text.style.flex = "1";

      const badge = document.createElement("span");
      badge.style.cssText = "font-size:10px;color:var(--text-dim);";
      badge.textContent = ag.mcp ? "MCP" : "scan";

      row.appendChild(cb);
      row.appendChild(dot);
      row.appendChild(text);
      row.appendChild(badge);
      listEl.appendChild(row);
    }
  } catch (_) {}

  modal.querySelector("#compare-cancel").addEventListener("click", () => overlay.remove());

  launchBtn.addEventListener("click", async () => {
    launchBtn.disabled = true;
    launchBtn.textContent = "Launching\u2026";
    statusEl.style.display = "block";
    statusEl.textContent = "Creating sister projects and launching agents\u2026";

    try {
      const r = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projId)}/compare-agents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agents: [...selected] }),
      });
      const data = await r.json();
      if (data.ok) {
        statusEl.textContent = `Launched ${data.count} agent(s)! Refreshing\u2026`;
        setTimeout(() => {
          overlay.remove();
          fetchExperimentsList();
          // Switch to comparison view
          setTimeout(async () => {
            try {
              const sr = await fetch(`http://127.0.0.1:${serverPort}/experiments/${encodeURIComponent(projId)}/sisters`);
              const sd = await sr.json();
              if (sd.ok && sd.family) {
                renderComparisonChart(canvas, sd.family, activeMetric);
                showComparisonSummary(sd.family, activeMetric, canvas);
              }
            } catch (_) {}
          }, 2000);
        }, 1000);
      } else {
        statusEl.textContent = "Failed: " + (data.reason || "unknown error");
        launchBtn.disabled = false;
        launchBtn.textContent = "Retry";
      }
    } catch (err) {
      statusEl.textContent = "Error: " + err.message;
      launchBtn.disabled = false;
      launchBtn.textContent = "Retry";
    }
  });
}

function showComparisonSummary(family, metricName, canvas) {
  // Insert or update a summary card below the chart
  const container = canvas.closest(".metric-chart-container");
  if (!container) return;

  let summaryEl = container.querySelector(".comparison-summary");
  if (!summaryEl) {
    summaryEl = document.createElement("div");
    summaryEl.className = "comparison-summary";
    container.appendChild(summaryEl);
  }

  const lowerBetter = isLowerBetter(metricName);
  const rows = [];

  for (const member of family) {
    const runs = getDisplayRuns(member.runs || []);
    const values = runs
      .map((r) => r.results?.[metricName])
      .filter((v) => typeof v === "number" && isFinite(v));
    const bestVal = values.length
      ? values.reduce((a, b) => lowerBetter ? Math.min(a, b) : Math.max(a, b))
      : null;
    const agentType = member.agent_type || "claude";
    const colors = AGENT_COLORS[agentType] || { dot: "#888" };
    const active = member.active_sessions > 0;

    rows.push(`
      <div style="display:flex;align-items:center;gap:8px;padding:4px 0;">
        <span style="width:8px;height:8px;border-radius:50%;background:${colors.dot};flex-shrink:0;"></span>
        <span style="flex:1;font-size:12px;color:var(--text);">${escapeHtml(member.name)}</span>
        <span style="font-size:11px;color:var(--text-dim);">${values.length} runs</span>
        <span style="font-size:12px;font-weight:500;color:var(--text);font-variant-numeric:tabular-nums;">${bestVal !== null ? bestVal.toPrecision(4) : "\u2013"}</span>
        ${active ? '<span style="font-size:10px;color:var(--success);">\u25CF running</span>' : ""}
      </div>
    `);
  }

  summaryEl.innerHTML = `
    <div style="padding:8px 0;border-top:1px solid var(--border);margin-top:8px;">
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:6px;font-weight:500;">Agent Comparison \u2014 ${escapeHtml(metricName)}</div>
      ${rows.join("")}
    </div>
  `;
}

function renderMetricChart(canvas, runs, metricName, liveEvents, opts = {}) {
  const useLogScale = opts.logScale || false;
  // Filter runs that have a real numeric value for this metric
  const points = [];
  for (let i = 0; i < runs.length; i++) {
    const val = runs[i].results?.[metricName];
    if (typeof val === "number" && isFinite(val)) {
      points.push({ index: i, value: val, run: runs[i], best: false });
    }
  }
  // Compute frontier from actual metric improvements (spec: green dot = any
  // run that pushed the running min/max, starting from the first data point).
  {
    const _lower = isLowerBetter(metricName);
    let _frontier = null;
    for (const p of points) {
      const improves = _frontier === null || (_lower ? p.value < _frontier : p.value > _frontier);
      p.best = improves;
      if (improves) _frontier = p.value;
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
    const dpr2 = opts.dpr || (window.devicePixelRatio || 1);
    let _ew, _eh;
    if (opts.exportWidth && opts.exportHeight) {
      _ew = opts.exportWidth; _eh = opts.exportHeight;
    } else {
      const rect2 = canvas.getBoundingClientRect();
      _ew = rect2.width; _eh = rect2.height;
    }
    const targetW2 = Math.round(_ew * dpr2);
    const targetH2 = Math.round(_eh * dpr2);
    if (canvas.width !== targetW2 || canvas.height !== targetH2) {
      canvas.width = targetW2;
      canvas.height = targetH2;
    }
    ctx2.setTransform(dpr2, 0, 0, dpr2, 0, 0);
    ctx2.clearRect(0, 0, _ew, _eh);
    ctx2.fillStyle = "#8888a0";
    ctx2.font = "12px -apple-system, sans-serif";
    ctx2.textAlign = "center";
    ctx2.fillText(
      totalPoints === 0 ? `No data for ${metricName}` : `Only 1 data point for ${metricName}`,
      _ew / 2, _eh / 2
    );
    if (!opts.exportMode && canvas.parentElement) {
      const oldTip = canvas.parentElement.querySelector(".metric-chart-tooltip");
      if (oldTip) oldTip.style.display = "none";
    }
    canvas.onmousemove = null;
    canvas.onmouseleave = null;
    return;
  }

  const dpr = opts.dpr || (window.devicePixelRatio || 1);
  let w, h;
  if (opts.exportWidth && opts.exportHeight) {
    w = opts.exportWidth;
    h = opts.exportHeight;
  } else {
    const rect = canvas.getBoundingClientRect();
    w = rect.width;
    h = rect.height;
  }
  const targetW = Math.round(w * dpr);
  const targetH = Math.round(h * dpr);
  if (canvas.width !== targetW || canvas.height !== targetH) {
    canvas.width = targetW;
    canvas.height = targetH;
  }
  if (!opts.exportMode) {
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const _ex = !!opts.exportMode;
  const pad = _ex
    ? { top: 16, right: 36, bottom: 32, left: 64 }
    : { top: 12, right: 32, bottom: 24, left: 56 };
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

  // Clear / fill background
  if (opts.bgColor) {
    ctx.fillStyle = opts.bgColor;
    ctx.fillRect(0, 0, w, h);
  } else {
    ctx.clearRect(0, 0, w, h);
  }

  // Clip to canvas bounds to prevent overflow
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, w, h);
  ctx.clip();

  // Y-axis labels with nice rounded ticks
  ctx.fillStyle = _ex ? "#555" : "#8888a0";
  ctx.font = (_ex ? "11" : "10") + "px -apple-system, sans-serif";
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
    ctx.fillStyle = _ex ? "#555" : "#8888a0";
    ctx.fillText(formatMetric(metricName, v), pad.left - 10, y + 3);
    ctx.strokeStyle = _ex ? "rgba(0,0,0,0.08)" : "rgba(136,136,160,0.1)";
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
    ctx.fillStyle = _ex ? "#555" : "#8888a0";
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

  // Best-so-far frontier line: step-function from first data point.
  const lowerBetter = isLowerBetter(metricName);
  if (points.length > 0) {
    const bestLine = [];
    let bestSoFar = points[0].value;
    bestLine.push({ x: toX(0), y: toY(bestSoFar) });
    for (let i = 1; i < points.length; i++) {
      if (points[i].best) bestSoFar = points[i].value;
      bestLine.push({ x: toX(i), y: toY(bestSoFar) });
    }
    // Extend frontier to right edge (covers live points region)
    const rightEdge = toX(totalPoints - 1);
    if (bestLine[bestLine.length - 1].x < rightEdge) {
      bestLine.push({ x: rightEdge, y: toY(bestSoFar) });
    }

    if (bestLine.length > 1) {
      // Area fill under the frontier for visual weight.
      const baselineY = pad.top + plotH;
      const grad = ctx.createLinearGradient(0, pad.top, 0, baselineY);
      grad.addColorStop(0, _ex ? "rgba(74,222,128,0.08)" : "rgba(74,222,128,0.22)");
      grad.addColorStop(1, "rgba(74,222,128,0.0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(bestLine[0].x, baselineY);
      for (let i = 0; i < bestLine.length; i++) {
        ctx.lineTo(bestLine[i].x, bestLine[i].y);
      }
      ctx.lineTo(bestLine[bestLine.length - 1].x, baselineY);
      ctx.closePath();
      ctx.fill();

      ctx.strokeStyle = _ex ? "rgba(74,222,128,0.7)" : "rgba(74,222,128,0.55)";
      ctx.lineWidth = _ex ? 2.5 : 2;
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
      ctx.arc(x, y, _ex ? 5 : 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = _ex ? "#fff" : "#0f0f23";
      ctx.lineWidth = _ex ? 2 : 1.5;
      ctx.stroke();
    } else {
      ctx.fillStyle = _ex ? "#aaa" : "#555";
      ctx.globalAlpha = _ex ? 0.5 : 0.3;
      ctx.beginPath();
      ctx.arc(x, y, _ex ? 2.5 : 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }
  }

  // Ring marker on the latest best run (current frontier tip)
  {
    let lastBestIdx = -1;
    for (let i = points.length - 1; i >= 0; i--) {
      if (points[i].best) { lastBestIdx = i; break; }
    }
    if (lastBestIdx >= 0) {
      const x = toX(lastBestIdx);
      const y = toY(points[lastBestIdx].value);
      ctx.save();
      ctx.strokeStyle = _ex ? "rgba(74,222,128,0.40)" : "rgba(74,222,128,0.35)";
      ctx.lineWidth = 1.25;
      ctx.beginPath();
      ctx.arc(x, y, _ex ? 10 : 9, 0, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = _ex ? "rgba(74,222,128,0.20)" : "rgba(74,222,128,0.18)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(x, y, _ex ? 14 : 13, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  // Tilted description labels on best runs (deconflicted in export mode)
  {
    const angle = -Math.PI / 6;
    ctx.save();
    ctx.font = (_ex ? "9" : "8") + "px -apple-system, sans-serif";
    ctx.fillStyle = _ex ? "rgba(80,80,90,0.55)" : "rgba(160,160,180,0.5)";
    ctx.textAlign = "left";
    let _lastLabelX = -Infinity;
    for (let i = 0; i < points.length; i++) {
      if (!points[i].best) continue;
      const desc = points[i].run.description || points[i].run.hypothesis || "";
      if (!desc) continue;
      const x = toX(i);
      if (_ex && x - _lastLabelX < 90) continue;
      const y = toY(points[i].value);
      if (_ex) {
        // Max text width that keeps label within right and top chart bounds.
        // Origin (x+5, y-7); at ~-30 deg: cosA~0.866, sinA~-0.5.
        const cosA = Math.cos(angle);
        const sinA = Math.sin(angle);
        const maxByRight = (w - pad.right - (x + 5)) / cosA;
        const maxByTop = (y - 7 - pad.top) / (-sinA);
        const maxW = Math.min(maxByRight, maxByTop, 110);
        if (maxW < 40) continue;
        let label = desc;
        while (label.length > 2 && ctx.measureText(label + "\u2026").width > maxW) {
          label = label.slice(0, -1);
        }
        if (label.length < desc.length) label += "\u2026";
        ctx.save();
        ctx.translate(x + 5, y - 7);
        ctx.rotate(angle);
        ctx.fillText(label, 0, 0);
        ctx.restore();
      } else {
        const label = desc.length > 24 ? desc.slice(0, 22) + "\u2026" : desc;
        ctx.save();
        ctx.translate(x + 5, y - 7);
        ctx.rotate(angle);
        ctx.fillText(label, 0, 0);
        ctx.restore();
      }
      _lastLabelX = x;
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
    ctx.strokeStyle = _ex ? "#fff" : "#0f0f23";
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

  if (opts.exportMode) return;

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
        const isDot = points[i].best ? "" : "none";
        const isBest = points[i].best || false;
        closest = { x, y, run, value: points[i].value, isBest, isDot };
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
        closest = { x, y, isLive: true, epochLabel, value: livePoints[i].value };
      }
    }
    if (closest) {
      let html = '';
      if (closest.isLive) {
        html = `<div class="tt-head"><span class="name">[live] ${closest.epochLabel}</span></div>
                <div class="tt-row"><span class="k">${metricName}</span><span class="v">${formatMetric(metricName, closest.value)}</span></div>`;
      } else {
        const run = closest.run;
        const isBest = closest.isBest;
        const desc = run.description || run.hypothesis || "";
        html = `<div class="tt-head">
                  <span class="dot" style="display:${isBest ? '' : 'none'};"></span>
                  <span class="name">run ${runDisplayNum(run)}</span>
                </div>
                <div class="tt-row"><span class="k">${metricName}</span><span class="v${isBest ? ' good' : ''}">${formatMetric(metricName, closest.value)}</span></div>${desc ? `<div class="tt-desc">${escapeHtml(desc)}</div>` : ''}`;
      }
      tooltip.innerHTML = html;
      tooltip.style.display = "block";
      let tx = closest.x + 6;
      let ty = closest.y - 28;
      if (tx + 200 > w) tx = closest.x - 206;
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

async function exportChartAsPng(chartCanvas, runs, metricName, title, opts = {}) {
  const padX = 28;
  const chartRect = chartCanvas.getBoundingClientRect();
  const chartW = Math.max(Math.round(chartRect.width), 960);
  const headerH = 88;
  const footerH = 36;
  const totalH = Math.round(chartW * 9 / 16);
  const graphH = totalH - headerH - footerH;
  const totalW = chartW;
  const exportDpr = 2;

  const exportCanvas = document.createElement("canvas");
  exportCanvas.width = totalW * exportDpr;
  exportCanvas.height = totalH * exportDpr;
  const ectx = exportCanvas.getContext("2d");
  ectx.setTransform(exportDpr, 0, 0, exportDpr, 0, 0);

  ectx.fillStyle = "#ffffff";
  ectx.fillRect(0, 0, totalW, totalH);

  // --- Header: 3-row layout ---
  // Row 1 (y=38): experiment name left | hero metric value right
  // Row 2 (y=56): summary sentence left | metric label right
  // Row 3 (y=72): run count left

  const heroX = totalW - padX;
  const bestRun = findBestRun(runs, metricName);
  const bestVal = bestRun?.results?.[metricName];
  const direction = isLowerBetter(metricName) ? "\u2193" : "\u2191";
  let heroVal = formatMetric(metricName, bestVal);
  heroVal = heroVal.replace(/\s*\(.*\)$/, "");

  // Row 1 left: title
  ectx.fillStyle = "#1c1a16";
  ectx.font = '500 28px Fraunces, Georgia, "Times New Roman", serif';
  ectx.textAlign = "left";
  ectx.fillText(title, padX, 38);

  // Row 1 right: hero value with accent→emerald gradient
  const heroFont = '500 36px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  ectx.font = heroFont;
  ectx.textAlign = "right";
  const heroTextW = ectx.measureText(heroVal).width;
  const heroGrad = ectx.createLinearGradient(heroX - heroTextW, 0, heroX, 0);
  heroGrad.addColorStop(0, "#8a80d8");
  heroGrad.addColorStop(1, "#5db76a");
  ectx.fillStyle = heroGrad;
  ectx.fillText(heroVal, heroX, 38);

  // Row 2 left: summary (truncated to not crowd hero area)
  const summaryFont = '400 11px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  ectx.font = summaryFont;
  ectx.textAlign = "left";
  const rawSummary = opts.summary || "";
  if (rawSummary) {
    ectx.fillStyle = "#777";
    const maxSummaryW = heroX - 32 - padX;
    let summary = rawSummary;
    while (summary.length > 3 && ectx.measureText(summary).width > maxSummaryW) {
      summary = summary.slice(0, -1);
    }
    if (summary.length < rawSummary.length) summary += "\u2026";
    ectx.fillText(summary, padX, 56);
  }

  // Row 2 right: metric label
  ectx.fillStyle = "#504a3d";
  ectx.font = '600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  ectx.letterSpacing = "0.04em";
  ectx.textAlign = "right";
  ectx.fillText(`${metricName.toUpperCase()} ${direction}`, heroX, 56);
  ectx.letterSpacing = "0px";

  // Row 3 left: run count
  ectx.fillStyle = "#bbb";
  ectx.font = summaryFont;
  ectx.textAlign = "left";
  ectx.fillText(`${runs.length} runs`, padX, 72);

  ectx.strokeStyle = "rgba(0, 0, 0, 0.06)";
  ectx.lineWidth = 1;
  ectx.beginPath();
  ectx.moveTo(padX, headerH);
  ectx.lineTo(totalW - padX, headerH);
  ectx.stroke();

  // --- Chart (16:9 within 4:3 frame) ---
  const tempCanvas = document.createElement("canvas");
  renderMetricChart(tempCanvas, runs, metricName, null, {
    ...opts,
    exportMode: true,
    exportWidth: chartW,
    exportHeight: graphH,
    dpr: exportDpr,
    bgColor: "#ffffff",
  });
  ectx.drawImage(tempCanvas, 0, headerH, chartW, graphH);

  // --- Footer: tray-icon circle logo + "Distillate" watermark ---
  const logoSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#1c1a16" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="11" stroke-width="1.6"/>
    <g transform="matrix(0.943 0 0 0.943 0.684 0.684)">
      <polyline points="5.25,9.0 8.25,12.0 5.25,15.0" stroke-width="1.5" opacity="0.55"/>
      <line x1="9.3" y1="17.3" x2="13.8" y2="4.95" stroke-width="1.5"/>
      <line x1="12.4" y1="18.5" x2="16.9" y2="6.1" stroke-width="1.5"/>
      <path d="M9.3,17.3 A1.67,1.67 0,0,0 12.4,18.5" stroke-width="1.5"/>
      <line x1="12.9" y1="4.6" x2="17.9" y2="6.4" stroke-width="1.5"/>
      <path d="M11.20,11.45 Q13.00,12.40 14.80,12.55" stroke-width="1.2"/>
    </g>
  </svg>`;
  const logoImg = new Image();
  logoImg.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(logoSvg);
  await new Promise((resolve) => { logoImg.onload = resolve; logoImg.onerror = resolve; });

  const logoSize = 17;
  const brandFontSize = 14;
  const footerCenterY = headerH + graphH + footerH / 2;
  const textBaseline = footerCenterY + Math.round(brandFontSize * 0.36);
  ectx.save();
  ectx.globalAlpha = 0.35;
  ectx.fillStyle = "#1c1a16";
  ectx.font = `400 ${brandFontSize}px Fraunces, Georgia, serif`;
  ectx.textAlign = "right";
  const brandX = totalW - padX;
  ectx.fillText("Distillate", brandX, textBaseline);
  if (logoImg.complete && logoImg.naturalWidth > 0) {
    const textW = ectx.measureText("Distillate").width;
    ectx.drawImage(logoImg, brandX - textW - 8 - logoSize, footerCenterY - logoSize / 2, logoSize, logoSize);
  }
  ectx.restore();

  return exportCanvas.toDataURL("image/png");
}

function triggerChartDownload(dataUrl, title, metricName, logScale) {
  const slug = (title || "frontier").trim()
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "frontier";
  const logSuffix = logScale ? "_log" : "";
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = `distillate_${slug}_${metricName}${logSuffix}.png`;
  document.body.appendChild(a);
  a.click();
  a.remove();
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

// Mini frontier chart for welcome screen
async function renderMiniFrontierChart(container, experimentId) {
  console.log("[Frontier] renderMiniFrontierChart called", { experimentId, containerPresent: !!container, serverPort });
  if (!container || !experimentId || !serverPort) {
    console.log("[Frontier] Early return: missing container/experimentId/serverPort");
    return;
  }

  try {
    // Try to find the experiment in the global cache first
    let proj = null;
    if (typeof cachedProjects !== "undefined" && cachedProjects) {
      proj = cachedProjects.find((p) => p.id === experimentId);
      console.log("[Frontier] Searched cachedProjects:", { found: !!proj, count: cachedProjects.length });
    }

    // If not in cache, fetch from server
    if (!proj) {
      console.log("[Frontier] Fetching from server...");
      const resp = await fetch(`http://127.0.0.1:${serverPort}/experiments/list`);
      if (!resp.ok) {
        console.log("[Frontier] Server fetch failed");
        return;
      }
      const data = await resp.json();
      const experiments = data.experiments || [];
      proj = experiments.find((e) => e.id === experimentId);
      console.log("[Frontier] Server fetch result:", { found: !!proj, count: experiments.length });
    }

    if (!proj) {
      console.log("[Frontier] Project not found");
      return;
    }
    if (!proj.runs || proj.runs.length === 0) {
      console.log("[Frontier] No runs in project");
      return;
    }
    if (!proj.key_metric_name) {
      console.log("[Frontier] No key_metric_name");
      return;
    }

    console.log("[Frontier] Ready to render", { runs: proj.runs.length, metric: proj.key_metric_name });

    // Clear container and create canvas
    container.innerHTML = "";
    const canvas = document.createElement("canvas");

    // CSS styles for responsiveness
    canvas.style.display = "block";
    canvas.style.width = "100%";
    canvas.style.height = "100%";

    container.appendChild(canvas);

    // Use setTimeout to ensure DOM is fully laid out before measuring
    // getBoundingClientRect() needs the element to have been rendered
    setTimeout(() => {
      const rect = container.getBoundingClientRect();
      const w = Math.max(1, Math.round(rect.width || 0));
      const h = Math.max(1, Math.round(rect.height || 60));  // Fallback to 60px (CSS height)
      console.log("[Frontier] Setting canvas dimensions", { w, h, rectWidth: rect.width, rectHeight: rect.height });
      canvas.width = w;
      canvas.height = h;
      console.log("[Frontier] Calling renderMetricChart", { canvasW: canvas.width, canvasH: canvas.height });
      renderMetricChart(canvas, proj.runs, proj.key_metric_name);
      console.log("[Frontier] renderMetricChart returned");
    }, 50);
  } catch (err) {
    console.error("[Frontier] Error:", err);
  }
}
