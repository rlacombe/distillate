# Nicolas Billing & Model Selector — Action Plan

**Status:** RED (tests written, implementation pending)
**Target version:** v0.7.2
**Keystone tie-in:** Cost visibility is load-bearing for trust. If the user can't tell what they're spending or which model thought up an answer, they can't trust Nicolas to "run experiment → see it on the chart" at scale.

---

## 1. Context

Nicolas was rebuilt as a **Recursive Language Model (RLM)** on 2026-04-13 (`project_rlm_nicolas.md`). Architecture today:

- **Root turn** — `NicolasClient` wraps `claude-agent-sdk.ClaudeSDKClient`. The SDK hands back a `ResultMessage` per turn with `total_cost_usd` and `num_turns`, but no token breakdown.
- **Sub-LLM calls** — Inside `lab_repl.execute()`, `llm_query` / `delegate` / `delegate_batch` hit the Anthropic API directly. Usage is tracked by `CostTracker` (at `distillate/agent_runtime/lab_repl.py:47`) but:
  - Uses a single default price table (`_DEFAULT_PRICE` = Sonnet-tier), so cost for Haiku / Opus sub-calls is mis-estimated.
  - No cache-token accounting.
  - Totals scoped to a single REPL call, not surfaced to the UI beyond `result["cost"]`.

Both layers spend real money on the user's API key. The user can't see either number today.

### The old "no model picker" rule is inverted

`feedback_model_picker.md` (8 days old, pre-RLM) said: *no model picker — Claude Code manages it*. That rule assumed a Claude Code subscription as the only auth. It no longer holds: Nicolas now reaches through the SDK's transport and the lab-REPL pathway to bill directly against the user's Anthropic key. Billing visibility + model choice become a correctness issue, not a UX nicety.

---

## 2. Goals & non-goals

**Goals**

1. User sees, at a glance, **which model Nicolas is using** and can switch without leaving the chat surface.
2. User sees, at a glance, **how much this conversation has cost** and **how much today has cost**.
3. Selection persists across app restarts.
4. Per-turn breakdown is captured: input, output, cache-read, cache-creation tokens, dollar cost, per model.
5. Sub-LLM calls from `lab_repl` roll into the same aggregate (one coherent total).
6. Budget-exhausted errors remain possible but are informative, not silent.

**Non-goals**

- No budget controls UI (user-settable caps) in this pass — backend hook only.
- No per-project billing splits — we don't know the project at turn time yet.
- No historical charts / analytics beyond today/week/all-time totals.
- No cost attribution by sub-agent persona (Knowledge Agent, Research Agent) — out of scope.
- Budgets for sub-LLM calls (`session_budget_usd`, `call_budget_usd` in `CostTracker`) stay as code-level guards; no UI.

---

## 3. UX

### 3.1 Status bar (primary surface)

The bottom-right status bar (`desktop/renderer/index.html:470`) today shows: `● Connected ... [theme toggle]`. Add two compact elements just left of the theme toggle:

```
● Connected       [ Opus 4.6 ▾ ]   $0.42 session • $4.13 today    🌗
```

- **Model pill** — click opens a dropdown listing supported models (Opus 4.6, Sonnet 4.6, Sonnet 4.5, Haiku 4.5). Current selection checked. Selection dispatches `set_model` and persists.
- **Cost pill** — two numbers, session + today. Hover opens a tooltip with:
  - Token breakdown (input, output, cache-read, cache-creation).
  - Per-model rollup (e.g., `Opus 4.6: 12,400 in / 3,200 out → $0.28`).
  - Week + all-time totals.
- Both pills styled as subtle chips, matching the existing status-bar language.

### 3.2 First-time state

On fresh install, the picker defaults to **Sonnet 4.6** (sane default: best quality/cost tradeoff). Cost pill reads `$0.00 session • $0.00 today`.

### 3.3 Error surface

If `BudgetExhaustedError` fires from a sub-LLM call, Nicolas receives a normal tool-error and the tooltip grows a red footer: *"Lab REPL budget hit for this turn — tell Nicolas 'continue' to retry."* No modal, no blocking.

---

## 4. Architecture

Five new/modified modules + thin wiring.

```
┌──────────────────────────────────────────────────────────────────┐
│  desktop/renderer/billing.js   ← NEW (model picker + cost pill)  │
│  desktop/renderer/core.js      ← wires turn_end + usage_update   │
│  desktop/renderer/index.html   ← adds #model-pill + #cost-pill   │
└──────────────────────────────────────────────────────────────────┘
                │ WebSocket / HTTP
                ▼
┌──────────────────────────────────────────────────────────────────┐
│  distillate/server.py          ← /usage GET + WS messages        │
│    • get_preferences  →  preferences.load()                      │
│    • get_usage        →  usage_tracker.snapshot()                │
│    • set_model persists via preferences.set("nicolas_model")    │
│    • usage_update pushed after every turn_end                    │
│                                                                  │
│  distillate/agent_sdk.py       ← richer turn_end + record_turn   │
│                                                                  │
│  distillate/preferences.py     ← NEW (JSON store)                │
│  distillate/pricing.py         ← NEW (single source of truth)    │
│  distillate/agent_runtime/                                       │
│    usage_tracker.py            ← NEW (JSONL persistence +        │
│                                   aggregates)                    │
│    lab_repl.py                 ← CostTracker uses pricing.py +   │
│                                   records into usage_tracker     │
└──────────────────────────────────────────────────────────────────┘
                │
                ▼
         ~/.config/distillate/
           preferences.json   { "nicolas_model": "claude-opus-4-6" }
           usage.jsonl        {ts, model, role, session_id, tokens, cost}
```

### 4.1 `distillate/pricing.py` (new)

Single source of truth for model pricing. Replaces the private `_MODEL_PRICES` dict in `lab_repl.py`.

```python
# distillate/pricing.py

# $/1M tokens, canonical as of 2026-04-15.
# Order: (input, output, cache_read, cache_creation_5m)
MODEL_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-6":            (15.00, 75.00, 1.50, 18.75),
    "claude-sonnet-4-6":          ( 3.00, 15.00, 0.30,  3.75),
    "claude-sonnet-4-5-20250929": ( 3.00, 15.00, 0.30,  3.75),
    "claude-haiku-4-5-20251001":  ( 0.80,  4.00, 0.08,  1.00),
}
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_PRICE = MODEL_PRICES[DEFAULT_MODEL]

def cost_for_usage(model: str, usage: dict) -> float:
    """usage keys: input_tokens, output_tokens,
                   cache_read_input_tokens, cache_creation_input_tokens."""

def friendly_model_name(model_id: str) -> str:
    """'claude-opus-4-6' -> 'Opus 4.6'"""

def supported_models() -> list[dict]:
    """For the dropdown: [{'id','label','family','input_price','output_price'}]."""
```

Tests pin down behavior before implementation (see §7.1).

### 4.2 `distillate/preferences.py` (new)

Tiny JSON store at `~/.config/distillate/preferences.json`. Not a DB — we have two keys to persist (`nicolas_model`, reserved room for future). Corrupt file → return defaults + quarantine the bad file.

```python
def load() -> dict: ...
def save(prefs: dict) -> None: ...
def get(key: str, default=None): ...
def set(key: str, value) -> None: ...
```

### 4.3 `distillate/agent_runtime/usage_tracker.py` (new)

Event-per-row JSONL. One line per `turn_end` or `lab_repl` sub-call. No schema migrations — append only.

```python
# Event shape
{
  "ts": "2026-04-15T21:04:22Z",
  "model": "claude-opus-4-6",
  "role": "nicolas_turn" | "lab_repl_subcall",
  "session_id": "abc-123",
  "tokens": {"input": 1200, "output": 450,
             "cache_read": 10200, "cache_creation": 0},
  "cost_usd": 0.0483,
}

# API
class UsageTracker:
    def __init__(self, path: Path): ...
    def record(self, *, model, role, session_id, tokens, cost_usd) -> None: ...
    def snapshot(self, session_id: str | None = None) -> dict:
        """Returns {
          'session': {...totals...},
          'today':   {...},
          'week':    {...},
          'all':     {...},
          'by_model': {model_id: {...totals...}},
          'current_model': "<id>",
        }"""
    def reset_session(self, session_id: str) -> None:
        """Drop in-memory session counter (file stays)."""
```

**Persistence:** `~/.config/distillate/usage.jsonl`. Log rotation deferred — at the expected volume (≤100 events/day) the file is tiny.

**Concurrency:** file append + fsync on each record; aggregates computed by streaming the file on snapshot. Only one process writes (the server). Good enough.

### 4.4 `distillate/agent_sdk.py` changes

Two changes to `NicolasClient`:

1. **Richer `turn_end` event.** `ResultMessage` carries `.usage` alongside `.total_cost_usd`. Capture all four token counts + per-model cost (recomputed from `pricing.cost_for_usage` so it matches the rest of the pipeline). New event shape:
   ```json
   {"type": "turn_end", "session_id": "...", "num_turns": 1,
    "model": "claude-opus-4-6",
    "tokens": {"input": 1200, "output": 450,
               "cache_read": 10200, "cache_creation": 0},
    "cost_usd": 0.0483,
    "sdk_reported_cost_usd": 0.0510}
   ```
   `sdk_reported_cost_usd` retained for diffing; UI uses our computed number for consistency with sub-LLM calls.

2. **Record into `UsageTracker`.** After yielding `turn_end`, append to the tracker.

3. **`set_model()` persists.** Calls `preferences.set("nicolas_model", model)`. `__init__` reads from preferences when no model is passed.

### 4.5 `distillate/agent_runtime/lab_repl.py` changes

- `CostTracker` imports `pricing.MODEL_PRICES` (remove the local `_MODEL_PRICES` dict).
- `record(response, model)` uses the actual model's price, not the default.
- Each sub-call also appends to `UsageTracker` with `role="lab_repl_subcall"` and the current Nicolas `session_id` (threaded through `execute()` via a new optional param).

### 4.6 `distillate/server.py` changes

WebSocket messages:

| In | Out | Purpose |
|----|-----|---------|
| `{"type":"get_preferences"}` | `{"type":"preferences", ...}` | Restore picker selection on mount |
| `{"type":"get_usage"}` | `{"type":"usage", ...snapshot}` | Restore cost pill on mount |
| `{"type":"set_model","model":...}` | (existing) | Now persists to prefs |
| — | `{"type":"usage_update", ...snapshot}` | Pushed after every `turn_end` |

HTTP: `GET /usage` returns the same snapshot. Useful for the /status dashboard and for tests.

### 4.7 `desktop/renderer/billing.js` (new)

Self-contained module. Exports `mountBilling({ws, elements})`. Responsibilities:

- On mount: request `get_preferences` + `get_usage`.
- Render model pill dropdown against `supported_models()` (fetched once).
- On pill click: open menu, on select dispatch `set_model` + optimistically update label.
- On `turn_end` / `usage_update`: update cost pill + recompute tooltip content.
- A single `_fmt_cost(usd)` helper — `$0.00` for <$0.01, `$0.42` for <$1, `$12.34` otherwise.

`core.js` calls `mountBilling` once the WS is open. The existing status-bar DOM gets two new `<span>` nodes in `index.html`.

---

## 5. WebSocket protocol — full spec

Client → Server:

```jsonc
{"type": "get_preferences"}
{"type": "get_usage"}
{"type": "set_model", "model": "claude-opus-4-6"}
```

Server → Client:

```jsonc
{"type": "preferences", "nicolas_model": "claude-opus-4-6",
 "supported_models": [{"id":"claude-opus-4-6","label":"Opus 4.6",...}, ...]}

{"type": "usage",        /* snapshot shape from UsageTracker */}
{"type": "usage_update", /* same shape, pushed after each turn */}

{"type": "turn_end", "session_id": "...", "num_turns": 1,
 "model": "claude-opus-4-6",
 "tokens": {"input": ..., "output": ..., "cache_read": ..., "cache_creation": ...},
 "cost_usd": 0.0483, "sdk_reported_cost_usd": 0.0510}
```

---

## 6. Phased rollout (TDD)

1. **RED (this commit)** — plan + failing tests for pricing, preferences, usage tracker, Nicolas integration, server, renderer.
2. **GREEN #1** — `pricing.py` + `preferences.py` + `usage_tracker.py` with their unit tests passing.
3. **GREEN #2** — `NicolasClient` richer `turn_end` + `UsageTracker` record + `set_model` persists. Integration tests pass.
4. **GREEN #3** — `lab_repl` wired to `pricing.py` and `UsageTracker`. Existing `test_lab_repl.py::TestCostTracker` tests updated to cover cache and per-model pricing.
5. **GREEN #4** — Server WS + HTTP endpoints. Server tests pass.
6. **GREEN #5** — Renderer `billing.js` + `index.html` edits. Desktop tests pass.
7. **MANUAL** — Launch the app, confirm picker persists, chat and verify cost pill animates, switch models mid-chat.

---

## 7. Test plan

All files live in `tests/` (Python) or `desktop/test/` (JS). Every test in §7.1–§7.6 is written **before** any implementation — they must all fail on `pytest` / `node --test` right now.

### 7.1 `tests/test_pricing.py`

- `test_all_four_supported_models_priced` — Opus 4.6, Sonnet 4.6, Sonnet 4.5, Haiku 4.5 each have 4-tuple prices.
- `test_cache_prices_are_fractions_of_input` — cache_read ≈ 10%, cache_creation ≈ 125% of input (sanity).
- `test_cost_for_usage_plain` — input+output only, matches `(in*in_price + out*out_price) / 1e6`.
- `test_cost_for_usage_with_cache` — all four token kinds included.
- `test_cost_for_usage_unknown_model_falls_back` — unknown model → `DEFAULT_PRICE`.
- `test_cost_for_usage_missing_keys` — missing cache keys treated as 0, no KeyError.
- `test_friendly_model_name` — `claude-opus-4-6` → `"Opus 4.6"`, `claude-sonnet-4-5-20250929` → `"Sonnet 4.5"`, `claude-haiku-4-5-20251001` → `"Haiku 4.5"`.
- `test_friendly_model_name_unknown` — unknown id returns the raw id (no crash).
- `test_supported_models_for_picker` — returns 4 entries, each with `id`, `label`, `family`, `input_price`, `output_price`. Deterministic order (Opus first, then Sonnet 4.6, Sonnet 4.5, Haiku).
- `test_default_model_is_priced` — `DEFAULT_MODEL` is a key in `MODEL_PRICES`.

### 7.2 `tests/test_preferences.py`

- `test_load_empty_returns_defaults` — file missing → `{"nicolas_model": DEFAULT_MODEL}`.
- `test_set_and_get_roundtrip` — set → save → load → get returns same value.
- `test_set_persists_to_disk` — `set()` writes JSON file, content reloadable by another call.
- `test_get_missing_key_returns_default` — `get("unknown", "x") == "x"`.
- `test_corrupt_file_returns_defaults_and_quarantines` — garbage JSON → defaults returned, file moved to `preferences.json.bak`.
- `test_load_ignores_unknown_keys` — tolerates extra keys without crashing.
- `test_config_dir_honored` — uses `config.CONFIG_DIR` (patched in test).

### 7.3 `tests/test_usage_tracker.py`

- `test_record_appends_jsonl_row` — one call → one line, parseable JSON.
- `test_record_includes_all_fields` — ts, model, role, session_id, tokens, cost_usd.
- `test_record_cost_computed_from_pricing` — cost equals `pricing.cost_for_usage(model, tokens)`.
- `test_snapshot_empty` — no events → zero totals, empty by_model.
- `test_snapshot_session_filters_by_id` — events from other sessions don't bleed in.
- `test_snapshot_today_respects_utc_midnight` — event from yesterday not counted in today.
- `test_snapshot_week_rolling_7_days` — events ≤7 days ago counted, older excluded.
- `test_snapshot_all_includes_everything`.
- `test_snapshot_by_model_breakdown` — per-model rollup sums tokens + cost correctly.
- `test_snapshot_current_model_from_prefs` — snapshot includes `current_model` from preferences.
- `test_concurrent_record_does_not_interleave` — two threads each record 100 events; file has 200 parseable lines.
- `test_malformed_line_skipped_not_fatal` — hand-corrupt one line, snapshot still computes from remainder.
- `test_reset_session_clears_in_memory_only` — snapshot session=0 after reset, but all-time unchanged.
- `test_lab_repl_subcall_and_nicolas_turn_both_counted` — mixed roles both roll up.
- `test_cost_usd_rounded_consistently` — rounding is stable (no float drift when summed).

### 7.4 `tests/test_nicolas_billing.py`

Integration. Mocks the Agent SDK `ClaudeSDKClient` with a fake async generator that yields a `ResultMessage` carrying a `usage` object.

- `test_turn_end_event_includes_model` — emitted event has `model` key.
- `test_turn_end_event_includes_token_breakdown` — all four counts present.
- `test_turn_end_event_cost_matches_pricing_module` — `cost_usd` equals `pricing.cost_for_usage(...)`.
- `test_turn_end_event_includes_sdk_reported_cost` — `sdk_reported_cost_usd` passthrough.
- `test_turn_end_records_into_usage_tracker` — after streaming the turn, tracker snapshot shows one event.
- `test_set_model_persists_to_preferences` — calling `NicolasClient.set_model("claude-opus-4-6")` writes to preferences.
- `test_nicolas_reads_model_from_preferences_on_init` — if no model passed, init picks up the stored preference.
- `test_sub_llm_record_also_flows_to_tracker` — lab_repl `_cost_tracker.record()` triggers a `UsageTracker` append with role `lab_repl_subcall`.

### 7.5 `tests/test_server_billing.py`

Uses FastAPI `TestClient`. Mocks `NicolasClient` so no real SDK spin-up.

- `test_get_preferences_returns_default_and_supported_models` — WS client sends `get_preferences`, gets back `{type:"preferences", nicolas_model: DEFAULT_MODEL, supported_models:[...]}` with 4 models.
- `test_get_preferences_returns_persisted_model` — pre-seed prefs file, WS returns that model.
- `test_set_model_persists_to_preferences_file` — WS send `set_model`, disk file updated.
- `test_get_usage_returns_snapshot_shape` — WS returns keys `session`, `today`, `week`, `all`, `by_model`, `current_model`.
- `test_usage_update_pushed_after_turn_end` — mock Nicolas yields a turn, server pushes a `usage_update` frame to the WS client after forwarding `turn_end`.
- `test_http_usage_endpoint_matches_ws_snapshot` — GET `/usage` returns identical JSON to the WS `usage` payload.
- `test_set_model_on_unknown_model_rejected` — unknown id → error frame, prefs unchanged.

### 7.6 `desktop/test/billing-display.test.js`

Static-analysis style (matches the existing `paper-reader.test.js` pattern — no DOM harness).

- `it("status bar HTML contains #model-pill and #cost-pill")` — `index.html` has both ids.
- `it("billing.js exports mountBilling on window")`.
- `it("billing.js requests get_preferences on mount")` — source contains `"get_preferences"` ws send call.
- `it("billing.js requests get_usage on mount")`.
- `it("billing.js dispatches set_model on pick")`.
- `it("billing.js listens for turn_end event")` — source references `"turn_end"` handler.
- `it("billing.js listens for usage_update event")`.
- `it("_fmt_cost handles <$0.01, <$1, and >$1 ranges")` — extract pure fn, assert three cases.
- `it("core.js wires billing module on ws.onopen")` — `core.js` calls `mountBilling(`.
- `it("model dropdown lists exactly four models")` — source mentions all four model ids.

---

## 8. Risks & open questions

1. **SDK `usage` shape.** `claude-agent-sdk`'s `ResultMessage.usage` field shape should be verified against the live SDK before GREEN #2. If the SDK omits cache fields on newer Claude Code versions, we fall back to `sdk_reported_cost_usd` and show `—` for the cache row in the tooltip.
2. **Cache creation TTL price.** The 5-minute cache price is what we use; the 1-hour tier exists but we haven't seen Nicolas use it. Defer until we do.
3. **Sub-LLM `session_id` threading.** `lab_repl.execute()` today doesn't know Nicolas's `session_id`. Thread it through as an optional kwarg from `mcp_server.py` — harmless null fallback for CLI callers.
4. **Cost vs. SDK cost drift.** Expected: our computed cost will usually be within a few % of SDK's. A larger drift means our price table is stale — log a warning and move on. No blocking.
5. **`distillate` CLI path.** The CLI doesn't render a picker. For now, CLI reads the saved preference too — so what you pick in the desktop app governs CLI runs. Documented in CHANGELOG.

---

## 9. Done criteria

- All ~55 tests in §7 pass.
- `pytest tests/` green end-to-end.
- `cd desktop && node --test test/billing-display.test.js` green.
- Launch desktop, see model pill defaulting to Sonnet 4.6. Switch to Opus, quit, relaunch — pill still shows Opus.
- Chat once, see session cost rise by the right order of magnitude (cross-check against `anthropic` API console usage).
- `~/.config/distillate/usage.jsonl` contains one `nicolas_turn` row per user message and N `lab_repl_subcall` rows per lab-REPL invocation.
- CHANGELOG.md bumped to v0.7.2 with a "Billing & model selector" entry.
