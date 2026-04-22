"""Claude model pricing — single source of truth for cost calculations.

Prices are per 1M tokens in USD, tuple order:
    (input, output, cache_read, cache_creation_5m)

Used by:
  - distillate.agent_sdk.NicolasClient (per-turn cost)
  - distillate.agent_runtime.lab_repl.CostTracker (sub-LLM sub-calls)
  - distillate.agent_runtime.usage_tracker (aggregated reporting)
  - desktop billing display (dropdown + tooltip)

Update the table when pricing changes — nothing else needs to move.
"""
from __future__ import annotations

MODEL_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-7":            ( 5.00, 25.00, 0.50,  6.25),
    "claude-opus-4-6":            (15.00, 75.00, 1.50, 18.75),
    "claude-sonnet-4-6":          ( 3.00, 15.00, 0.30,  3.75),
    "claude-sonnet-4-5-20250929": ( 3.00, 15.00, 0.30,  3.75),
    "claude-haiku-4-5-20251001":  ( 1.00,  5.00, 0.10,  1.25),
    "gemini-3.0":                 ( 0.10,  0.40, 0.025, 0.025),
    "gemini-3.1":                 ( 1.25,  5.00, 0.3125, 0.3125),
}

DEFAULT_MODEL: str = "claude-sonnet-4-6"
DEFAULT_PRICE: tuple[float, float, float, float] = MODEL_PRICES[DEFAULT_MODEL]

# Picker order: flagship first, then sonnet tiers, then fast.
_PICKER_ORDER: tuple[str, ...] = (
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
    "gemini-3.1",
    "gemini-3.0",
)

_FRIENDLY: dict[str, str] = {
    "claude-opus-4-7": "Opus 4.7",
    "claude-opus-4-6": "Opus 4.6",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-sonnet-4-5-20250929": "Sonnet 4.5",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
    "gemini-3.1": "Gemini 3.1 Pro",
    "gemini-3.0": "Gemini 3.0 Flash",
}

_FAMILY: dict[str, str] = {
    "claude-opus-4-7": "opus",
    "claude-opus-4-6": "opus",
    "claude-sonnet-4-6": "sonnet",
    "claude-sonnet-4-5-20250929": "sonnet",
    "claude-haiku-4-5-20251001": "haiku",
    "gemini-3.1": "gemini",
    "gemini-3.0": "gemini",
}


def _safe_count(value) -> int:
    if value is None:
        return 0
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, v)


def cost_for_usage(model: str, usage: dict) -> float:
    """Compute USD cost for a usage dict with up-to-four token kinds.

    Recognized keys:
      input_tokens, output_tokens,
      cache_read_input_tokens, cache_creation_input_tokens

    Unknown models fall back to ``DEFAULT_PRICE``. Missing/None/negative
    token counts are treated as zero so callers don't need to normalize.
    """
    inp, out, c_read, c_create = MODEL_PRICES.get(model, DEFAULT_PRICE)
    tokens = {
        "input": _safe_count(usage.get("input_tokens")),
        "output": _safe_count(usage.get("output_tokens")),
        "cache_read": _safe_count(usage.get("cache_read_input_tokens")),
        "cache_creation": _safe_count(usage.get("cache_creation_input_tokens")),
    }
    cost = (
        tokens["input"] * inp
        + tokens["output"] * out
        + tokens["cache_read"] * c_read
        + tokens["cache_creation"] * c_create
    ) / 1_000_000
    return cost


def friendly_model_name(model_id: str) -> str:
    """Map a Claude model id to a short display label (e.g. 'Opus 4.6')."""
    return _FRIENDLY.get(model_id, model_id)


def supported_models() -> list[dict]:
    """Return the picker's model list in canonical display order.

    Each entry: {id, label, family, input_price, output_price}.
    """
    out = []
    for mid in _PICKER_ORDER:
        inp, out_price, _cr, _cc = MODEL_PRICES[mid]
        out.append({
            "id": mid,
            "label": friendly_model_name(mid),
            "family": _FAMILY[mid],
            "input_price": inp,
            "output_price": out_price,
        })
    return out
