/* ───── Calibration View — prediction accuracy and confidence over time ───── */

function resetCalibrationTab() {
  const noSel = document.getElementById("calibration-no-selection");
  const rendered = document.getElementById("calibration-rendered");
  if (noSel) noSel.classList.remove("hidden");
  if (rendered) { rendered.classList.add("hidden"); rendered.innerHTML = ""; }
}

function loadCalibration(projectId) {
  if (!projectId) { resetCalibrationTab(); return; }
  const noSel = document.getElementById("calibration-no-selection");
  const rendered = document.getElementById("calibration-rendered");
  if (!rendered) return;

  const proj = cachedProjects.find((p) => p.id === projectId);
  if (!proj) { resetCalibrationTab(); return; }

  const calRuns = (proj.runs || []).filter(
    (r) => r.predicted_metric != null && r.predicted_value != null,
  );

  if (noSel) noSel.classList.add("hidden");
  rendered.classList.remove("hidden");
  rendered.innerHTML = "";

  if (calRuns.length === 0) {
    rendered.innerHTML = `
      <div class="tab-empty-state" style="padding:60px 0;text-align:center">
        <div class="empty-icon">&#x1F4D0;</div>
        <h2>No predictions recorded yet</h2>
        <p>The Experimentalist logs predictions automatically via <code style="font-family:monospace;font-size:12px;background:var(--bg-2,rgba(0,0,0,.06));padding:1px 5px;border-radius:3px">start_run</code>.</p>
      </div>`;
    return;
  }

  const metricSet = new Set(calRuns.map((r) => r.predicted_metric));
  const metrics = [...metricSet];
  let activeMetric = metrics[0];

  if (metrics.length > 1) {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:6px;margin-bottom:18px;";
    const label = document.createElement("span");
    label.style.cssText = "font:var(--type-label);letter-spacing:var(--label-tracking);text-transform:uppercase;color:var(--text-dim);";
    label.textContent = "Metric";
    const sel = document.createElement("select");
    sel.className = "chart-metric-select";
    for (const m of metrics) {
      const opt = document.createElement("option");
      opt.value = m; opt.textContent = m;
      sel.appendChild(opt);
    }
    sel.addEventListener("change", () => { activeMetric = sel.value; renderAll(); });
    row.appendChild(label);
    row.appendChild(sel);
    rendered.appendChild(row);
  }

  const sectionsEl = document.createElement("div");
  rendered.appendChild(sectionsEl);

  function renderAll() {
    sectionsEl.innerHTML = "";
    const runs = calRuns.filter((r) => r.predicted_metric === activeMetric);
    _calSummaryStrip(sectionsEl, runs);
    _calPredictionTimeline(sectionsEl, runs, activeMetric);
    _calConfidenceChart(sectionsEl, runs);
  }

  renderAll();
}

// ---------------------------------------------------------------------------
// Section A — Summary strip
// ---------------------------------------------------------------------------

function _calSummaryStrip(container, runs) {
  const confirmed = runs.filter((r) => r.verdict === "confirmed").length;
  const partial   = runs.filter((r) => r.verdict === "partial").length;
  const falsified = runs.filter((r) => ["falsified", "refuted"].includes(r.verdict)).length;
  const total     = runs.length;

  const accuracy = total > 0 ? Math.round((confirmed / total) * 100) : null;

  const confRuns = runs.filter((r) => typeof r.confidence === "number");
  const meanConf = confRuns.length
    ? Math.round(confRuns.reduce((s, r) => s + r.confidence, 0) / confRuns.length)
    : null;

  const strip = document.createElement("div");
  strip.className = "cal-summary-strip";

  const cards = [
    { label: "Runs predicted", value: String(total),
        tooltip: "Number of runs that included a pre-registration prediction." },
    { label: "Confirmed", value: `${confirmed}${accuracy != null ? ` (${accuracy}%)` : ""}`, color: "green",
        tooltip: "Runs where the actual result matched the prediction (verdict = confirmed)." },
    { label: "Partial",   value: String(partial),   color: "yellow",
        tooltip: "Runs where the result partially matched the prediction." },
    { label: "Falsified", value: String(falsified),  color: "red",
        tooltip: "Runs where the result contradicted the prediction (verdict = falsified / refuted)." },
    ...(meanConf != null ? [{ label: "Avg confidence", value: `${meanConf}%`,
        tooltip: "Average stated confidence across all predicted runs." }] : []),
  ];

  for (const c of cards) {
    const card = document.createElement("div");
    card.className = "cal-stat-card";
    if (c.color) card.dataset.calColor = c.color;
    if (c.tooltip) card.title = c.tooltip;
    card.innerHTML = `<div class="cal-stat-value">${escapeHtml(c.value)}</div><div class="cal-stat-label">${escapeHtml(c.label)}</div>`;
    strip.appendChild(card);
  }

  container.appendChild(strip);
}

// ---------------------------------------------------------------------------
// Section B — Prediction timeline (SVG, crisp text)
// ---------------------------------------------------------------------------

function _calPredictionTimeline(container, runs, metric) {
  const section = document.createElement("div");
  section.className = "exp-detail-section";
  const h3 = document.createElement("h3");

  const errRuns = runs.filter((r) => typeof r.prediction_error === "number");
  const meanErr = errRuns.length
    ? errRuns.reduce((s, r) => s + Math.abs(r.prediction_error), 0) / errRuns.length
    : null;

  const brierRuns = runs.filter((r) => typeof r.confidence === "number" && r.verdict);
  const brier = brierRuns.length
    ? brierRuns.reduce((s, r) => s + (r.confidence / 100 - (r.verdict === "confirmed" ? 1 : 0)) ** 2, 0) / brierRuns.length
    : null;

  let h3Html = "Predictions vs. Actuals";
  if (meanErr != null) {
    const val = meanErr < 0.01 ? meanErr.toFixed(4) : meanErr.toFixed(3);
    h3Html += ` <span class="cal-ece-badge" title="Mean absolute error between predicted and actual ${escapeHtml(metric)} values across all runs. Lower is better.">MAE ${val}</span>`;
  }
  if (brier != null) {
    h3Html += ` <span class="cal-ece-badge" title="Brier score = mean squared error between stated confidence (0–1) and outcome (confirmed=1, other=0). 0 = perfect, 0.25 = random, 1 = worst. Lower is better.">Brier ${brier.toFixed(3)}</span>`;
  }
  h3.innerHTML = h3Html;
  section.appendChild(h3);

  const points = runs.map((r, i) => ({
    run: r, idx: i,
    predicted: r.predicted_value,
    actual: r.results?.[metric] ?? null,
  }));

  const vals = [];
  for (const p of points) {
    if (p.predicted != null) vals.push(p.predicted);
    if (p.actual    != null) vals.push(p.actual);
  }
  if (!vals.length) { container.appendChild(section); return; }

  const lo0 = Math.min(...vals), hi0 = Math.max(...vals);
  const vpad = (hi0 - lo0 || 1) * 0.15;
  const lo = lo0 - vpad, hi = hi0 + vpad;

  // Fixed SVG coordinate space — scales to container via CSS
  const VW = 900, VH = 168;
  const PAD = { top: 20, right: 20, bottom: 34, left: 52 };
  const cW = VW - PAD.left - PAD.right;
  const cH = VH - PAD.top - PAD.bottom;

  const toY = (v) => PAD.top + cH - ((v - lo) / (hi - lo)) * cH;
  const toX = (i) => PAD.left + (points.length <= 1 ? cW / 2 : (i / (points.length - 1)) * cW);

  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${VW} ${VH}`);
  svg.setAttribute("aria-hidden", "true");
  svg.style.cssText = "width:100%;height:auto;display:block;overflow:visible;";

  const mk = (tag, attrs) => {
    const el = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };

  // Grid lines
  for (let g = 0; g <= 4; g++) {
    const y = PAD.top + (g / 4) * cH;
    svg.appendChild(mk("line", { x1: PAD.left, x2: VW - PAD.right, y1: y, y2: y,
      stroke: "currentColor", "stroke-width": "0.5", opacity: "0.12" }));
  }

  // Y-axis labels
  for (let g = 0; g <= 4; g++) {
    const v = lo + ((4 - g) / 4) * (hi - lo);
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", PAD.left - 6); t.setAttribute("y", PAD.top + (g / 4) * cH);
    t.setAttribute("text-anchor", "end"); t.setAttribute("dominant-baseline", "middle");
    t.setAttribute("font-size", "9"); t.setAttribute("fill", "currentColor"); t.setAttribute("opacity", "0.45");
    t.textContent = Math.abs(v) < 1 ? v.toFixed(3) : v.toFixed(2);
    svg.appendChild(t);
  }

  // Predicted polyline (dashed)
  const predPts = points.filter((p) => p.predicted != null)
    .map((p) => `${toX(p.idx)},${toY(p.predicted)}`).join(" ");
  if (predPts) {
    svg.appendChild(mk("polyline", { points: predPts, fill: "none",
      stroke: "var(--accent,#7366f1)", "stroke-width": "1.5",
      "stroke-dasharray": "3 4", opacity: "0.55" }));
  }

  // Actual polyline (solid)
  const actualPts = points.filter((p) => p.actual != null)
    .map((p) => `${toX(p.idx)},${toY(p.actual)}`).join(" ");
  if (actualPts) {
    svg.appendChild(mk("polyline", { points: actualPts, fill: "none",
      stroke: "currentColor", "stroke-width": "1.5", opacity: "0.38" }));
  }

  // Dots
  for (const p of points) {
    const x = toX(p.idx);
    if (p.predicted != null) {
      svg.appendChild(mk("circle", { cx: x, cy: toY(p.predicted), r: "2.5",
        fill: "var(--accent,#7366f1)", opacity: "0.65" }));
    }
    if (p.actual != null) {
      const v = p.run.verdict;
      const fill = v === "confirmed" ? "#4ade80"
        : v === "partial" ? "#fbbf24"
        : (v === "falsified" || v === "refuted") ? "#f87171"
        : "currentColor";
      svg.appendChild(mk("circle", { cx: x, cy: toY(p.actual), r: "3.5", fill }));
    }
  }

  // X-axis run labels — show every Nth to avoid crowding
  const step = Math.ceil(points.length / 40);
  for (const p of points) {
    if (p.idx % step !== 0 && p.idx !== points.length - 1) continue;
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", toX(p.idx)); t.setAttribute("y", VH - PAD.bottom + 11);
    t.setAttribute("text-anchor", "middle");
    t.setAttribute("font-size", "8"); t.setAttribute("fill", "currentColor"); t.setAttribute("opacity", "0.38");
    t.textContent = `#${runDisplayNum(p.run)}`;
    svg.appendChild(t);
  }

  // Legend (top-left)
  const legY = 10;
  let lx = PAD.left;
  for (const { dash, label } of [{ dash: true, label: "Predicted" }, { dash: false, label: "Actual" }]) {
    svg.appendChild(mk("line", { x1: lx, x2: lx + 16, y1: legY, y2: legY,
      stroke: dash ? "var(--accent,#7366f1)" : "currentColor",
      "stroke-width": "1.5", "stroke-dasharray": dash ? "3 4" : "none", opacity: "0.5" }));
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", lx + 20); t.setAttribute("y", legY);
    t.setAttribute("dominant-baseline", "middle");
    t.setAttribute("font-size", "9"); t.setAttribute("fill", "currentColor"); t.setAttribute("opacity", "0.5");
    t.textContent = label;
    svg.appendChild(t);
    lx += 72;
  }

  // Wrap SVG + tooltip
  const wrap = document.createElement("div");
  wrap.style.cssText = "position:relative;";
  wrap.appendChild(svg);

  const tip = document.createElement("div");
  tip.className = "cal-tooltip";
  tip.style.display = "none";
  wrap.appendChild(tip);

  svg.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    const mx = (e.clientX - rect.left) * (VW / rect.width);
    const my = (e.clientY - rect.top)  * (VH / rect.height);
    let hit = null, minD = 24;
    for (const p of points) {
      const x = toX(p.idx);
      const d = Math.min(
        p.actual    != null ? Math.hypot(mx - x, my - toY(p.actual))    : Infinity,
        p.predicted != null ? Math.hypot(mx - x, my - toY(p.predicted)) : Infinity,
      );
      if (d < minD) { minD = d; hit = p; }
    }
    if (hit) {
      const r = hit.run;
      const lines = [`Run #${runDisplayNum(r)}`];
      if (r.predicted_value != null) lines.push(`Predicted: ${r.predicted_value}`);
      if (hit.actual != null) lines.push(`Actual: ${hit.actual}`);
      if (typeof r.confidence === "number") lines.push(`Confidence: ${r.confidence}%`);
      if (r.verdict) lines.push(`Verdict: ${r.verdict}`);
      tip.innerHTML = lines.map((l) => escapeHtml(l)).join("<br>");
      tip.style.display = "block";
      tip.style.left = `${Math.min(e.clientX - rect.left + 14, rect.width - 220)}px`;
      tip.style.top  = `${Math.max(e.clientY - rect.top  - 44,  0)}px`;
    } else {
      tip.style.display = "none";
    }
  });
  svg.addEventListener("mouseleave", () => { tip.style.display = "none"; });

  section.appendChild(wrap);
  container.appendChild(section);
}

// ---------------------------------------------------------------------------
// Section C — Confidence calibration (HTML/CSS bars, crisp text)
// ---------------------------------------------------------------------------

function _calConfidenceChart(container, runs) {
  const withConf = runs.filter((r) => typeof r.confidence === "number");
  if (withConf.length < 2) return;

  // Bucket into 10 bands (0–9, 10–19, … 90–100)
  const buckets = Array.from({ length: 10 }, () => ({ total: 0, confirmed: 0, sumConf: 0 }));
  for (const r of withConf) {
    const bi = Math.min(9, Math.floor(r.confidence / 10));
    buckets[bi].total++;
    if (r.verdict === "confirmed") buckets[bi].confirmed++;
    buckets[bi].sumConf += r.confidence;
  }

  // ECE = Σ_b (n_b/N) × |frac_confirmed_b − mean_confidence_b|
  const N = withConf.length;
  const ece = buckets.reduce((s, b) => {
    if (b.total === 0) return s;
    return s + (b.total / N) * Math.abs(b.confirmed / b.total - (b.sumConf / b.total) / 100);
  }, 0);

  const section = document.createElement("div");
  section.className = "exp-detail-section";
  const h3 = document.createElement("h3");
  h3.innerHTML = `Confidence Calibration <span class="cal-ece-badge" title="ECE = Expected Calibration Error. Weighted mean absolute gap between stated confidence and fraction confirmed. 0 = perfectly calibrated, &lt;0.1 = good, &gt;0.2 = overconfident.">ECE ${ece.toFixed(3)}</span>`;
  section.appendChild(h3);

  // Top row: y-axis + plot — both share the explicit 180px height
  const top = document.createElement("div");
  top.className = "cal-calib-top";

  // Y-axis
  const yAxis = document.createElement("div");
  yAxis.className = "cal-calib-yaxis";
  for (const [pct, label] of [[100, "100%"], [75, "75%"], [50, "50%"], [25, "25%"], [0, "0%"]]) {
    const el = document.createElement("div");
    el.className = "cal-calib-ylabel";
    el.style.bottom = `${pct}%`;
    el.textContent = label;
    yAxis.appendChild(el);
  }
  top.appendChild(yAxis);

  // Plot area (bars + SVG grid/diagonal overlay)
  const plot = document.createElement("div");
  plot.className = "cal-calib-plot";

  // SVG overlay: grid lines + perfect-calibration diagonal
  const ns = "http://www.w3.org/2000/svg";
  const diagSvg = document.createElementNS(ns, "svg");
  diagSvg.setAttribute("viewBox", "0 0 100 100");
  diagSvg.setAttribute("preserveAspectRatio", "none");
  diagSvg.setAttribute("aria-hidden", "true");
  diagSvg.style.cssText = "position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible;";
  for (const y of [0, 25, 50, 75, 100]) {
    const l = document.createElementNS(ns, "line");
    l.setAttribute("x1", "0"); l.setAttribute("x2", "100");
    l.setAttribute("y1", String(100 - y)); l.setAttribute("y2", String(100 - y));
    l.setAttribute("stroke", "currentColor"); l.setAttribute("stroke-width", "0.4");
    l.setAttribute("opacity", "0.12"); l.setAttribute("vector-effect", "non-scaling-stroke");
    diagSvg.appendChild(l);
  }
  const diag = document.createElementNS(ns, "line");
  diag.setAttribute("x1", "0"); diag.setAttribute("y1", "100");
  diag.setAttribute("x2", "100"); diag.setAttribute("y2", "0");
  diag.setAttribute("stroke", "currentColor"); diag.setAttribute("stroke-width", "0.8");
  diag.setAttribute("stroke-dasharray", "2 2"); diag.setAttribute("opacity", "0.28");
  diag.setAttribute("vector-effect", "non-scaling-stroke");
  diagSvg.appendChild(diag);
  plot.appendChild(diagSvg);

  // Bar columns
  const barsRow = document.createElement("div");
  barsRow.className = "cal-calib-bars";

  for (let i = 0; i < 10; i++) {
    const b = buckets[i];
    const frac = b.total > 0 ? b.confirmed / b.total : 0;
    const pct  = Math.round(frac * 100);

    const col = document.createElement("div");
    col.className = "cal-calib-col";

    if (b.total > 0) {
      const pctLabel = document.createElement("div");
      pctLabel.className = "cal-calib-pct";
      pctLabel.style.bottom = `calc(${pct}% + 4px)`;
      pctLabel.textContent = `${pct}%`;
      col.appendChild(pctLabel);

      const bar = document.createElement("div");
      bar.className = "cal-calib-bar";
      bar.style.height = `${pct}%`;
      bar.dataset.calColor = frac >= 0.6 ? "green" : frac >= 0.3 ? "yellow" : "red";
      col.appendChild(bar);
    }

    barsRow.appendChild(col);
  }
  plot.appendChild(barsRow);

  // "perfect calibration" legend note (top-right of plot)
  const diagNote = document.createElement("div");
  diagNote.className = "cal-calib-diagnote";
  diagNote.innerHTML = `<span class="cal-calib-diagnote-dash">\u2508\u2508</span> perfect calibration`;
  plot.appendChild(diagNote);

  top.appendChild(plot);
  section.appendChild(top);

  // X-axis area — indented by yaxis width so labels align with plot columns
  const xArea = document.createElement("div");
  xArea.className = "cal-calib-xarea";

  const xAxis = document.createElement("div");
  xAxis.className = "cal-calib-xaxis";
  for (const label of ["0", "10", "20", "30", "40", "50", "60", "70", "80", "90"]) {
    const el = document.createElement("div");
    el.className = "cal-calib-xlabel";
    el.textContent = label;
    xAxis.appendChild(el);
  }
  xArea.appendChild(xAxis);

  const nRow = document.createElement("div");
  nRow.className = "cal-calib-xaxis cal-calib-nrow";
  for (const b of buckets) {
    const el = document.createElement("div");
    el.className = "cal-calib-xlabel";
    el.textContent = b.total > 0 ? `n=${b.total}` : "";
    nRow.appendChild(el);
  }
  xArea.appendChild(nRow);

  const xTitle = document.createElement("div");
  xTitle.className = "cal-calib-xtitle";
  xTitle.textContent = "Confidence (%)";
  xArea.appendChild(xTitle);

  section.appendChild(xArea);
  container.appendChild(section);
}
