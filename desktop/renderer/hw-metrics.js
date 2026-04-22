/* Hardware metrics strip — shows Apple Silicon GPU/CPU/ANE stats while at
 * least one experiment session is running. Polls cachedProjects (the same
 * source experiments.js uses) every 2s and starts/stops the main-process
 * powermetrics stream accordingly. Hides itself on non-Mac or when sudo is
 * not available. */

(function () {
  const bridge = window.distillate?.powerMetrics;
  if (!bridge) return; // Preload bridge missing — nothing to do.

  const strip = document.getElementById("hw-metrics-strip");
  if (!strip) return;

  const pills = {
    gpuActive: document.getElementById("hw-gpu-active"),
    gpuWatts:  document.getElementById("hw-gpu-power"),
    cpuWatts:  document.getElementById("hw-cpu-power"),
    aneWatts:  document.getElementById("hw-ane-power"),
  };

  let streaming = false;     // powermetrics process is live in main
  let unavailable = false;   // proven unavailable — stop trying this session

  function formatPercent(v) {
    if (v === null || v === undefined) return null;
    return v.toFixed(0) + "%";
  }
  function formatWatts(v) {
    if (v === null || v === undefined) return null;
    return v.toFixed(1) + " W";
  }

  function setPill(el, text) {
    if (!el) return;
    if (text === null) { el.hidden = true; return; }
    el.hidden = false;
    const valueEl = el.querySelector(".hw-pill-value");
    if (valueEl) valueEl.textContent = text;
  }

  bridge.onSample((sample) => {
    if (unavailable) return;
    // First sample proves powermetrics is working — reveal the strip.
    strip.hidden = false;
    setPill(pills.gpuActive, formatPercent(sample.gpuActive));
    setPill(pills.gpuWatts,  formatWatts(sample.gpuWatts));
    setPill(pills.cpuWatts,  formatWatts(sample.cpuWatts));
    // ANE often reports 0 on idle machines — only show it when active.
    setPill(pills.aneWatts,  sample.aneWatts ? formatWatts(sample.aneWatts) : null);
    // Dim the GPU pill when utilization is effectively zero — still
    // visible (diagnostic signal) but visually quiet.
    const gpuIdle = sample.gpuActive !== null && sample.gpuActive < 3;
    pills.gpuActive?.classList.toggle("hw-idle", gpuIdle);
    pills.gpuWatts?.classList.toggle("hw-idle", gpuIdle);
  });

  bridge.onUnavailable(() => {
    // Silent graceful degrade — no errors, no strip, and don't ask again
    // this session (sudo config won't change while the app is running).
    unavailable = true;
    streaming = false;
    strip.hidden = true;
  });

  function countRunning() {
    const projects = (typeof cachedProjects !== "undefined" ? cachedProjects : []) || [];
    let n = 0;
    for (const p of projects) if ((p.active_sessions || 0) > 0) n++;
    return n;
  }

  async function tick() {
    if (unavailable) return;
    const running = countRunning();
    if (running > 0 && !streaming) {
      streaming = true;
      try { await bridge.start(); } catch { streaming = false; }
    } else if (running === 0 && streaming) {
      streaming = false;
      try { await bridge.stop(); } catch {}
      strip.hidden = true;
    }
  }

  // 2s matches the powermetrics sample interval — no value in faster checks.
  setInterval(tick, 2000);
  // Kick the first check once projects have loaded.
  setTimeout(tick, 1500);
})();

// ── HuggingFace Jobs GPU strip ──────────────────────────────────────────────
(function () {
  const hfStrip  = document.getElementById("hw-hf-strip");
  const utilPill = document.getElementById("hw-hf-gpu-util");
  const memPill  = document.getElementById("hw-hf-gpu-mem");
  const cpuPill  = document.getElementById("hw-hf-cpu");
  if (!hfStrip) return;

  function setPillText(pill, text) {
    if (!pill) return;
    const v = pill.querySelector(".hw-pill-value");
    if (v) v.textContent = text;
    pill.hidden = text === null;
  }

  async function pollHFMetrics() {
    if (!serverPort) return;
    try {
      const resp = await fetch(`http://127.0.0.1:${serverPort}/hf-jobs/latest-metrics`);
      if (!resp.ok) return;
      const data = await resp.json();
      const m = data.metrics;
      // Backend returns null when no jobs are active — that's the gate.
      if (!m) { hfStrip.hidden = true; return; }

      hfStrip.hidden = false;

      setPillText(utilPill, m.gpu_util_pct != null ? `${m.gpu_util_pct.toFixed(0)}%` : null);

      // Prefer GB display; fall back to %
      const memText = m.gpu_mem_gb  != null ? `${m.gpu_mem_gb.toFixed(1)} GB`
                    : m.gpu_mem_pct != null ? `${m.gpu_mem_pct.toFixed(0)}%`
                    : null;
      setPillText(memPill, memText);

      setPillText(cpuPill, m.cpu_pct != null ? `${m.cpu_pct.toFixed(0)}%` : null);

      const idle = m.gpu_util_pct != null && m.gpu_util_pct < 3;
      utilPill?.classList.toggle("hw-idle", idle);
      memPill?.classList.toggle("hw-idle", idle);
    } catch (_) { /* server not ready yet */ }
  }

  // Poll every 3s — HF metrics stream is 1Hz but we don't need sub-second UI.
  setInterval(pollHFMetrics, 3000);
  setTimeout(pollHFMetrics, 2000);
})();
