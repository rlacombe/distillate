/**
 * Usage Analytics page — token breakdown by flow, model, function, and day.
 *
 * Entry point: openUsageStats() → openSettings("usage") → renders into #stats-content.
 */

// ─── Formatters ─────────────────────────────────────────────────────────────

function _sTok(n) {
  const v = Math.round(n || 0);
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (v >= 1_000)     return Math.round(v / 1_000) + "K";
  return String(v);
}
function _sCost(usd) {
  const n = Number.isFinite(usd) ? usd : 0;
  if (n === 0) return "$0";
  if (n < 0.01) return "<$0.01";
  return "$" + n.toFixed(2);
}
function _sPct(a, total) {
  if (!total) return "0%";
  return Math.round((a / total) * 100) + "%";
}
// Short month label for daily axis
function _shortDate(iso) {
  const d = new Date(iso + "T00:00:00Z");
  return (d.getUTCMonth() + 1) + "/" + d.getUTCDate();
}

// ─── SVG helpers ────────────────────────────────────────────────────────────

function _svg(w, h, content) {
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg">${content}</svg>`;
}

// Horizontal stacked bar (3 segments): a=input, b=cache_creation, c=output
function _stackedBar(a, b, c, w = 248, h = 10) {
  const gap = 3;
  const minW = 8;
  const segs = [
    { v: a, color: "var(--stats-in)"  },
    { v: b, color: "var(--stats-cc)"  },
    { v: c, color: "var(--stats-out)" },
  ].filter(s => s.v > 0);
  if (segs.length === 0) return "";

  const usable = w - gap * (segs.length - 1);
  const total  = segs.reduce((s, x) => s + x.v, 0) || 1;

  // Assign proportional widths, enforce minimum
  segs.forEach(s => { s.w = Math.max(minW, (s.v / total) * usable); });
  // Scale back to fit exactly in usable width
  const rawSum = segs.reduce((s, x) => s + x.w, 0);
  segs.forEach(s => { s.w = (s.w / rawSum) * usable; });

  const r = h / 2;
  let x = 0;
  const rects = segs.map((s, i) => {
    const rx_ = r, ry_ = r;
    const rect = `<rect x="${x.toFixed(1)}" y="0" width="${s.w.toFixed(1)}" height="${h}" fill="${s.color}" rx="${rx_}" ry="${ry_}"/>`;
    x += s.w + gap;
    return rect;
  }).join("");
  return _svg(w, h, rects);
}

// Single horizontal bar (fraction 0–1)
function _bar(frac, w = 180, h = 8, color = "var(--accent)") {
  const filled = Math.max(2, frac * w);
  return _svg(w, h,
    `<rect x="0" y="0" width="${w}" height="${h}" fill="var(--border)" rx="4"/>` +
    `<rect x="0" y="0" width="${filled.toFixed(1)}" height="${h}" fill="${color}" rx="4"/>`,
  );
}

// Daily bar chart — tokens per day
function _dailyChart(daily, w = 280, h = 56) {
  if (!daily || daily.length === 0) return "";
  const maxTok = Math.max(...daily.map(d => d.tokens), 1);
  const n      = daily.length;
  const gap    = 2;
  const barW   = Math.max(2, (w - gap * (n - 1)) / n);
  const labelEvery = Math.ceil(n / 6);   // show ~6 x-axis labels max
  const chartH = h - 22;   // 14px x-labels + 8px y-label row at top
  let bars = "", labels = "", yLabel = "";
  daily.forEach((d, i) => {
    const x   = i * (barW + gap);
    const frac = d.tokens / maxTok;
    const bh  = Math.max(2, frac * (chartH - 4));
    const y   = 8 + (chartH - 4) - bh;
    const col = d.tokens > 0 ? "var(--accent)" : "var(--border)";
    bars += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${bh.toFixed(1)}" fill="${col}" rx="1">` +
            `<title>${_shortDate(d.date)}: ${_sTok(d.tokens)} tokens</title></rect>`;
    if (i % labelEvery === 0 || i === n - 1) {
      const lx = x + barW / 2;
      labels += `<text x="${lx.toFixed(1)}" y="${h - 1}" text-anchor="middle" fill="var(--text-muted)" font-size="9">${_shortDate(d.date)}</text>`;
    }
  });
  // y-axis max label (top-right)
  yLabel = `<text x="${w}" y="7" text-anchor="end" fill="var(--text-muted)" font-size="8">${_sTok(maxTok)}</text>`;
  return _svg(w, h,
    `<g>${yLabel}</g><g>${bars}</g><g>${labels}</g>`,
  );
}

// ─── Section builders ────────────────────────────────────────────────────────

function _sectionHead(title) {
  return `<div class="stats-section-title">${title}</div>`;
}

function _tokenFlow(t) {
  const active = t.input + t.cache_creation + t.output;
  const total  = active + t.cache_read;
  const cacheHit = total > 0 ? Math.round((t.cache_read / total) * 100) : 0;
  return `
    <div class="stats-card">
      ${_sectionHead("Token Flow")}
      <div class="stats-flow-bar">${_stackedBar(t.input, t.cache_creation, t.output)}</div>
      <div class="stats-flow-legend">
        <span class="stats-legend-dot" style="background:var(--stats-in)"></span>
        <span class="stats-legend-label">Input</span>
        <span class="stats-legend-val">${_sTok(t.input)}</span>
        <span class="stats-legend-dot" style="background:var(--stats-cc)"></span>
        <span class="stats-legend-label">Cache writes</span>
        <span class="stats-legend-val">${_sTok(t.cache_creation)}</span>
        <span class="stats-legend-dot" style="background:var(--stats-out)"></span>
        <span class="stats-legend-label">Output</span>
        <span class="stats-legend-val">${_sTok(t.output)}</span>
      </div>
      <div class="stats-flow-sub">
        ${_sTok(t.cache_read)} cache reads excluded · ${cacheHit}% cache hit rate
      </div>
    </div>`;
}

function _byModel(models) {
  if (!models || models.length === 0) return "";
  const maxCost = Math.max(...models.map(m => m.cost_usd), 0.001);
  const rows = models.map(m => {
    const active = m.input + m.cache_creation + m.output;
    return `
      <div class="stats-row">
        <div class="stats-row-label" title="${m.model}">${m.label}</div>
        <div class="stats-row-bar">${_bar(m.cost_usd / maxCost, 140, 8)}</div>
        <div class="stats-row-tok">${_sTok(active)}</div>
        <div class="stats-row-cost">${_sCost(m.cost_usd)}</div>
      </div>`;
  }).join("");
  return `
    <div class="stats-card">
      ${_sectionHead("By Model")}
      ${rows}
    </div>`;
}

const _ROLE_ORDER = ["nicolas_turn", "lab_repl_subcall", "experimentalist_run"];
function _byRole(roles) {
  if (!roles || roles.length === 0) return "";
  const sorted = [...roles].sort((a, b) =>
    _ROLE_ORDER.indexOf(a.role) - _ROLE_ORDER.indexOf(b.role));
  const maxCost = Math.max(...sorted.map(r => r.cost_usd), 0.001);
  const rows = sorted.map(r => {
    const active = r.input + r.cache_creation + r.output;
    const avgCost = r.calls > 0 ? r.cost_usd / r.calls : 0;
    return `
      <div class="stats-row">
        <div class="stats-row-label">${r.label}</div>
        <div class="stats-row-bar">${_bar(r.cost_usd / maxCost, 140, 8, "var(--stats-role)")}</div>
        <div class="stats-row-tok">${_sTok(active)}</div>
        <div class="stats-row-cost" title="${r.calls} turns · avg ${_sCost(avgCost)}/turn">${_sCost(r.cost_usd)}</div>
      </div>`;
  }).join("");
  return `
    <div class="stats-card">
      ${_sectionHead("By Function")}
      ${rows}
    </div>`;
}

function _dailySection(daily, title = "Daily") {
  if (!daily || daily.length === 0) return "";
  const hasData = daily.some(d => d.tokens > 0);
  const chart = hasData ? _dailyChart(daily, 540, 80) : `<div class="stats-empty-chart">No data yet</div>`;
  const activePeriods = daily.filter(d => d.tokens > 0).length;
  const totalTok = daily.reduce((s, d) => s + d.tokens, 0);
  const unit = title.startsWith("Monthly") ? "month" : "day";
  return `
    <div class="stats-card">
      ${_sectionHead(title)}
      <div class="stats-daily-chart">${chart}</div>
      ${hasData ? `<div class="stats-flow-sub">${_sTok(totalTok)} tokens total · ${activePeriods} active ${unit}${activePeriods !== 1 ? "s" : ""}</div>` : ""}
    </div>`;
}

function _efficiency(t) {
  const active   = t.input + t.cache_creation + t.output;
  const total    = active + t.cache_read;
  const cacheHit = total > 0 ? (t.cache_read / total * 100).toFixed(0) : 0;
  const avgPerCall = t.calls > 0 ? (t.cost_usd / t.calls) : 0;
  return `
    <div class="stats-card stats-card-efficiency">
      ${_sectionHead("Efficiency")}
      <div class="stats-eff-grid">
        <div class="stats-eff-item">
          <div class="stats-eff-val">${cacheHit}%</div>
          <div class="stats-eff-label">Cache hit rate</div>
        </div>
        <div class="stats-eff-item">
          <div class="stats-eff-val">${_sCost(avgPerCall)}</div>
          <div class="stats-eff-label">Avg per turn</div>
        </div>
        <div class="stats-eff-item">
          <div class="stats-eff-val">${t.calls.toLocaleString()}</div>
          <div class="stats-eff-label">Total turns</div>
        </div>
        <div class="stats-eff-item">
          <div class="stats-eff-val">${_sCost(t.cost_usd)}</div>
          <div class="stats-eff-label">Total cost</div>
        </div>
      </div>
    </div>`;
}

// ─── Period selector ─────────────────────────────────────────────────────────

let _statsPeriod = "30d";

const _PERIOD_OPTS = [
  { value: "day",  label: "Day"  },
  { value: "7d",   label: "7d"   },
  { value: "30d",  label: "30d"  },
  { value: "all",  label: "All"  },
];

function _periodSelector() {
  return `<div class="stats-period-selector">${
    _PERIOD_OPTS.map(o =>
      `<button class="stats-period-btn${o.value === _statsPeriod ? " active" : ""}" data-period="${o.value}">${o.label}</button>`
    ).join("")
  }</div>`;
}

// ─── Main render ─────────────────────────────────────────────────────────────

async function _loadAndRenderStats() {
  const el = document.getElementById("stats-content");
  if (!el) return;
  el.innerHTML = '<div class="stats-loading">Loading…</div>';

  let data;
  try {
    const r = await fetch(`http://127.0.0.1:${serverPort}/account/stats?period=${_statsPeriod}`);
    data = await r.json();
  } catch {
    el.innerHTML = '<div class="stats-error">Could not load usage data.</div>';
    return;
  }
  if (!data.ok) {
    el.innerHTML = '<div class="stats-error">Usage data unavailable.</div>';
    return;
  }

  const t = data.totals;
  const active = t.input + t.cache_creation + t.output;
  const dailyLabel = data.daily_mode === "months" ? "Monthly · All Time"
                   : data.daily_mode === "days"   ? `Daily · ${data.period.replace(/^(LAST\s+|TODAY\s+·\s+)/, "")}`
                   : "";

  el.innerHTML = `
    <div class="stats-header-row">
      <div class="stats-period">${data.period}</div>
      ${_periodSelector()}
    </div>
    <div class="stats-hero">
      <span class="stats-hero-tok">${_sTok(active)}</span>
      <span class="stats-hero-sub">tokens · ${_sCost(t.cost_usd)}</span>
    </div>
    <div class="stats-cards-row">
      ${_tokenFlow(t)}
      ${_efficiency(t)}
    </div>
    ${_byModel(data.by_model)}
    ${_byRole(data.by_role)}
    ${data.daily && data.daily.length ? _dailySection(data.daily, dailyLabel) : ""}
  `;

  // Wire period selector clicks
  el.querySelectorAll(".stats-period-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      _statsPeriod = btn.dataset.period;
      _loadAndRenderStats();
    });
  });
}

function openUsageStats() {
  if (typeof openSettings === "function") openSettings("usage");
  else _loadAndRenderStats();
}

window.openUsageStats = openUsageStats;
