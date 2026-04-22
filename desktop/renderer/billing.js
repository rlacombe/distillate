/**
 * Billing pills — model picker + live cost display.
 *
 * Owns two status-bar elements:
 *   #model-pill  — native <select> over supported models. Change event
 *                  dispatches a `set_model` WS message.
 *   #cost-pill   — "$session · $today" summary. Updates on every
 *                  `turn_end` and `usage_update` push.
 *
 * Wires via `window.mountBilling(ws)`, called by core.js on ws.onopen.
 * Listens directly to `ws.onmessage` (independent of handleEvent so we
 * don't risk breaking chat-path routing).
 */

// Canonical model list — fallback before the server's supported_models
// response lands. Order mirrors distillate/pricing.py.
const DEFAULT_MODEL_LIST = [
  { id: "claude-opus-4-7",            label: "Opus 4.7",   family: "opus"   },
  { id: "claude-opus-4-6",            label: "Opus 4.6",   family: "opus"   },
  { id: "claude-sonnet-4-6",          label: "Sonnet 4.6", family: "sonnet" },
  { id: "claude-sonnet-4-5-20250929", label: "Sonnet 4.5", family: "sonnet" },
  { id: "claude-haiku-4-5-20251001",  label: "Haiku 4.5",  family: "haiku"  },
  { id: "gemini-3.1",                 label: "Gemini 3.1 Pro",   family: "gemini" },
  { id: "gemini-3.0",                 label: "Gemini 3.0 Flash", family: "gemini" },
];
const DEFAULT_MODEL_ID = "claude-sonnet-4-6";

/**
 * Format a USD cost for the pill.
 * _fmt_cost(0)      === "$0.00"
 * _fmt_cost(0.004)  === "$0.00"   (sub-cent rounds to zero)
 * _fmt_cost(0.42)   === "$0.42"
 * _fmt_cost(12.34)  === "$12.34"
 */
function _fmt_tokens(n) {
  const v = Number.isFinite(n) ? Math.round(n) : 0;
  if (v <= 0) return "0";
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (v >= 1_000) return Math.round(v / 1_000) + "K";
  return String(v);
}

function _fmt_cost(usd) {
  const n = Number.isFinite(usd) ? usd : 0;
  return "$" + n.toFixed(2);
}

// Sub-cent precision for the per-msg hint: "$0.003" reads cleaner than "$0.00".
function _fmt_per_msg(usd) {
  const n = Number.isFinite(usd) ? usd : 0;
  if (n >= 1) return "$" + n.toFixed(2);
  if (n >= 0.01) return "$" + n.toFixed(3);
  return "$" + n.toFixed(4);
}

// Per-Mtok rate formatter: integer for whole dollars, two decimals otherwise.
// Sonnet 4.6 input is $3 → "3"; Gemini 3 Flash input is $0.10 → "0.10".
function _fmt_rate(usdPerM) {
  const n = Number.isFinite(usdPerM) ? usdPerM : 0;
  if (n >= 1 && Number.isInteger(n)) return n.toFixed(0);
  if (n >= 1) return n.toFixed(2);
  return n.toFixed(2);
}

// Per-msg estimate from the model's per-MTok price and a typical Nicolas turn
// (roughly 1800 input + 500 output tokens — order-of-magnitude sanity check,
// not a billing guarantee). Mirrors distillate/pricing.py MODEL_PRICES.
const _PRICE_TABLE = {
  "claude-opus-4-7":            { input:  5.00, output: 25.00 },
  "claude-opus-4-6":            { input: 15.00, output: 75.00 },
  "claude-sonnet-4-6":          { input:  3.00, output: 15.00 },
  "claude-sonnet-4-5-20250929": { input:  3.00, output: 15.00 },
  "claude-haiku-4-5-20251001":  { input:  1.00, output:  5.00 },
  "gemini-3.0":                 { input:  0.10, output:  0.40 },
  "gemini-3.1":                 { input:  1.25, output:  5.00 },
};
function _estimatePerMsgCost(modelId) {
  const p = _PRICE_TABLE[modelId];
  if (!p) return 0;
  const IN_TOK = 1800, OUT_TOK = 500;
  return (IN_TOK / 1_000_000) * p.input + (OUT_TOK / 1_000_000) * p.output;
}

// Module-scoped state (one billing instance per app).
let _ws = null;
let _modelId = DEFAULT_MODEL_ID;
let _supportedModels = DEFAULT_MODEL_LIST.slice();
let _usageSnapshot = null;
let _mounted = false;

function _elements() {
  return {
    select: document.getElementById("model-pill"),
    session: document.getElementById("cost-pill-session"),
    today: document.getElementById("cost-pill-today"),
    cost: document.getElementById("cost-pill"),
    composerHint: document.getElementById("composer-cost-hint"),
  };
}

function _labelFor(id) {
  const m = _supportedModels.find((x) => x.id === id);
  return m ? m.label : id;
}

function _syncSelectOptions() {
  const { select } = _elements();
  if (!select) return;
  // Only rebuild if the set of options differs.
  const wanted = _supportedModels.map((m) => m.id).join(",");
  const current = Array.from(select.options).map((o) => o.value).join(",");
  if (wanted !== current) {
    select.innerHTML = "";
    for (const m of _supportedModels) {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.label;
      select.appendChild(opt);
    }
  }
  if (select.value !== _modelId) {
    select.value = _modelId;
  }
}

// Prefer an explicit api_cost_usd if the server emits it, otherwise
// fall back to the total cost (back-compat with older snapshots that
// predate the billing_source split).
function _apiCost(bucket) {
  if (!bucket) return 0;
  return typeof bucket.api_cost_usd === "number" ? bucket.api_cost_usd : (bucket.cost_usd || 0);
}
function _subCost(bucket) {
  if (!bucket) return 0;
  return typeof bucket.subscription_cost_usd === "number" ? bucket.subscription_cost_usd : 0;
}
function _totalCost(bucket) {
  return _apiCost(bucket) + _subCost(bucket);
}

function _renderCost() {
  const { session, today, cost, composerHint } = _elements();
  const snap = _usageSnapshot || {};
  const sApi = _apiCost(snap.session);
  const sSub = _subCost(snap.session);
  const tApi = _apiCost(snap.today);
  const tSub = _subCost(snap.today);
  // Display token counts instead of costs
  const sTok = (snap.session?.input_tokens || 0)
             + (snap.session?.cache_creation_tokens || 0)
             + (snap.session?.output_tokens || 0);
  const tTok = (snap.today?.input_tokens || 0)
             + (snap.today?.cache_creation_tokens || 0)
             + (snap.today?.output_tokens || 0);
  if (session) session.textContent = _fmt_tokens(sTok);
  if (today)   today.textContent = _fmt_tokens(tTok) + " tok today";

  // Compute overview cell — primary: today cost; sub: token volume; below: session.
  const computeVal = document.getElementById("ov-compute-value");
  if (computeVal) {
    const todayCost = _totalCost(snap.today);
    computeVal.textContent = todayCost > 0 ? _fmt_cost(todayCost) : "—";
  }
  const computeBadge = document.getElementById("ov-compute-badge");
  if (computeBadge) {
    const tSub = _subCost(snap.today), tApi = _apiCost(snap.today);
    computeBadge.textContent = tSub > 0 ? "Max" : (tApi > 0 ? "API" : "");
  }
  const computeInlineSub = document.getElementById("ov-compute-session");
  if (computeInlineSub) {
    const tTok = (snap.today?.input_tokens || 0)
               + (snap.today?.cache_creation_tokens || 0)
               + (snap.today?.output_tokens || 0);
    computeInlineSub.textContent = tTok > 0 ? _fmt_tokens(tTok) + " tok" : "";
  }
  const computeSub = document.getElementById("ov-compute-sub");
  if (computeSub) {
    const sCost = _totalCost(snap.session);
    const sTok = (snap.session?.input_tokens || 0)
               + (snap.session?.cache_creation_tokens || 0)
               + (snap.session?.output_tokens || 0);
    if (sCost > 0 || sTok > 0) {
      computeSub.textContent = `session · ${_fmt_cost(sCost)} · ${_fmt_tokens(sTok)} tok`;
    } else {
      computeSub.textContent = "";
    }
  }

  // Composer hint: model label + per-Mtok price so users see what they're
  // paying at decision time. "$IN / $OUT per M" preserves the in-vs-out
  // asymmetry without pretending a per-msg estimate is authoritative.
  if (composerHint) {
    const label = _labelFor(_modelId);
    const p = _PRICE_TABLE[_modelId];
    composerHint.innerHTML = "";
    const modelEl = document.createElement("span");
    modelEl.className = "cch-model";
    modelEl.textContent = label;
    composerHint.appendChild(modelEl);
    if (p) {
      const sep = document.createElement("span");
      sep.className = "cch-sep";
      sep.textContent = "\u00B7";
      const rate = document.createElement("span");
      rate.textContent = `$${_fmt_rate(p.input)} / $${_fmt_rate(p.output)} per M`;
      composerHint.append(sep, rate);
    }
    composerHint.classList.add("visible");
  }

  if (cost) {
    const lines = [];
    // Show token counts across time periods
    const sessionTok = (snap.session?.input_tokens || 0) + (snap.session?.cache_creation_tokens || 0) + (snap.session?.output_tokens || 0);
    const todayTok = (snap.today?.input_tokens || 0) + (snap.today?.cache_creation_tokens || 0) + (snap.today?.output_tokens || 0);
    const weekTok = (snap.week?.input_tokens || 0) + (snap.week?.cache_creation_tokens || 0) + (snap.week?.output_tokens || 0);
    const allTok = (snap.all?.input_tokens || 0) + (snap.all?.cache_creation_tokens || 0) + (snap.all?.output_tokens || 0);

    lines.push("Token usage by period:");
    lines.push(`Session:  ${_fmt_tokens(sessionTok)}`);
    lines.push(`Today:    ${_fmt_tokens(todayTok)}`);
    lines.push(`Week:     ${_fmt_tokens(weekTok)}`);
    lines.push(`All time: ${_fmt_tokens(allTok)}`);

    // Optionally add cost info if subscription metrics exist
    const hasSub = ["session", "today", "week", "all"].some(
      (k) => _subCost(snap[k]) > 0,
    );
    if (hasSub) {
      lines.push("");
      lines.push("API costs:");
      const costRow = (label, bucket) => {
        const api = _apiCost(bucket);
        const sub = _subCost(bucket);
        return `  ${label.padEnd(8)} API: ${_fmt_cost(api).padEnd(8)} Sub: ${_fmt_cost(sub)}`;
      };
      lines.push(costRow("Session:", snap.session));
      lines.push(costRow("Today:",   snap.today));
    }
    cost.title = lines.join("\n");
  }
}

function _pickModel(id) {
  if (!id || id === _modelId) return;
  _modelId = id;
  _syncSelectOptions();
  _renderCost();
  if (_ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(JSON.stringify({ type: "set_model", model: id }));
  }
}

function _onWsMessage(ev) {
  let data;
  try { data = JSON.parse(ev.data); } catch { return; }
  if (!data || typeof data !== "object") return;
  switch (data.type) {
    case "preferences":
      if (Array.isArray(data.supported_models) && data.supported_models.length) {
        _supportedModels = data.supported_models;
      }
      if (data.nicolas_model) {
        _modelId = data.nicolas_model;
      }
      _syncSelectOptions();
      _renderCost();
      // Populate budget inputs if they exist (Settings > Preferences section)
      if (data.budget_compact_suggest_usd != null) {
        const el = document.getElementById("setting-budget-compact");
        if (el) el.value = Number(data.budget_compact_suggest_usd).toFixed(2);
      }
      if (data.budget_session_hard_usd != null) {
        const el = document.getElementById("setting-budget-session");
        if (el) el.value = Number(data.budget_session_hard_usd).toFixed(2);
      }
      break;
    case "usage":
    case "usage_update":
      _usageSnapshot = data;
      _renderCost();
      break;
    case "turn_end":
      // usage_update usually follows immediately; reading the turn's own
      // model keeps the pill honest if the user switched mid-turn.
      if (data.model) {
        _modelId = data.model;
        _syncSelectOptions();
      }
      break;
  }
}

// Public: called by core.js after an HTTP GET /usage to eagerly populate
// the compute cell before the WS usage_update round-trips.
function applyUsageSnapshot(snap) {
  if (!snap || typeof snap !== "object") return;
  _usageSnapshot = snap;
  _renderCost();
}

function mountBilling(ws) {
  _ws = ws;
  const { select } = _elements();
  if (select && !_mounted) {
    select.addEventListener("change", (ev) => _pickModel(ev.target.value));
    _mounted = true;
  }
  _syncSelectOptions();
  _renderCost();

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "get_preferences" }));
    ws.send(JSON.stringify({ type: "get_usage" }));
  }
  ws.addEventListener("message", _onWsMessage);

  // Periodically refresh usage so experimentalist tokens (written by stop
  // hook after session ends) appear without requiring a Nicolas turn.
  setInterval(() => {
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify({ type: "get_usage" }));
    }
  }, 30_000);
}

function getSupportedModels() {
  return _supportedModels;
}

window.mountBilling = mountBilling;
window.getSupportedModels = getSupportedModels;

// Budget settings save button — uses HTTP POST so it works regardless of WS state.
(function _wireBudgetSettings() {
  function _wire() {
    const saveBtn = document.getElementById("settings-budget-save");
    if (!saveBtn || saveBtn._budgetBound) return;
    saveBtn._budgetBound = true;
    saveBtn.addEventListener("click", () => {
      const compactEl = document.getElementById("setting-budget-compact");
      const sessionEl = document.getElementById("setting-budget-session");
      const statusEl = document.getElementById("settings-budget-status");
      const compact = parseFloat(compactEl?.value ?? "1.00");
      const session = parseFloat(sessionEl?.value ?? "5.00");
      const port = typeof serverPort !== "undefined" ? serverPort : null;
      if (!port) {
        if (statusEl) { statusEl.textContent = "Not connected"; statusEl.className = "settings-status"; }
        return;
      }
      fetch(`http://127.0.0.1:${port}/preferences/budget`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          compact_suggest: isNaN(compact) ? null : compact,
          session_hard: isNaN(session) ? null : session,
        }),
      })
        .then((r) => {
          if (statusEl) {
            statusEl.textContent = r.ok ? "Saved" : "Error saving";
            statusEl.className = r.ok ? "settings-status success" : "settings-status";
            setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 2000);
          }
        })
        .catch(() => {
          if (statusEl) { statusEl.textContent = "Not connected"; statusEl.className = "settings-status"; }
        });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", _wire);
  else _wire();
}());
window.applyUsageSnapshot = applyUsageSnapshot;
