# Covers: distillate/pricing.py
"""Tests for distillate.pricing — single source of truth for model prices.

These are RED tests written before implementation. See
docs/research/nicolas-billing-action-plan.md §7.1.

Imports are inlined per-test so pytest collects every case individually
(surfacing a full red bar once the module stubs arrive).
"""
import pytest


# ---------------------------------------------------------------------------
# Supported model matrix
# ---------------------------------------------------------------------------

class TestModelMatrix:
    def test_all_four_supported_models_priced(self):
        from distillate import pricing
        for mid in (
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-sonnet-4-5-20250929",
            "claude-haiku-4-5-20251001",
        ):
            assert mid in pricing.MODEL_PRICES, f"{mid} missing from MODEL_PRICES"
            tup = pricing.MODEL_PRICES[mid]
            assert len(tup) == 4, f"{mid} price tuple should be (in, out, cache_read, cache_creation)"
            assert all(isinstance(v, (int, float)) for v in tup)
            assert all(v > 0 for v in tup), f"{mid} has non-positive price"

    def test_default_model_is_priced(self):
        from distillate import pricing
        assert pricing.DEFAULT_MODEL in pricing.MODEL_PRICES
        assert pricing.DEFAULT_PRICE == pricing.MODEL_PRICES[pricing.DEFAULT_MODEL]

    def test_cache_prices_sensible_relative_to_input(self):
        """Cache read should be cheaper than input; for Claude, cache creation pricier."""
        from distillate import pricing
        for mid, (inp, _out, c_read, c_create) in pricing.MODEL_PRICES.items():
            assert c_read < inp, f"{mid}: cache_read should be cheaper than input"
            assert c_read / inp < 0.5, f"{mid}: cache_read looks too expensive"
            # Claude charges a creation surcharge (1.25x input); Gemini's cache_creation
            # field is a storage fee and can be cheaper than input.
            if mid.startswith("claude"):
                assert c_create > inp, f"{mid}: cache_creation should be pricier than input"


# ---------------------------------------------------------------------------
# cost_for_usage
# ---------------------------------------------------------------------------

class TestCostForUsage:
    def test_plain_input_output(self):
        from distillate import pricing
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        cost = pricing.cost_for_usage("claude-opus-4-6", usage)
        # Opus: $15 in, $75 out per MTok — exactly $90 for 1M + 1M
        assert cost == pytest.approx(90.0, abs=1e-9)

    def test_with_cache(self):
        from distillate import pricing
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
            "cache_creation_input_tokens": 1_000_000,
        }
        cost = pricing.cost_for_usage("claude-opus-4-6", usage)
        inp, out, c_read, c_create = pricing.MODEL_PRICES["claude-opus-4-6"]
        assert cost == pytest.approx(inp + out + c_read + c_create, abs=1e-9)

    def test_unknown_model_falls_back_to_default(self):
        from distillate import pricing
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        fallback_cost = pricing.cost_for_usage("not-a-real-model", usage)
        default_cost = pricing.cost_for_usage(pricing.DEFAULT_MODEL, usage)
        assert fallback_cost == pytest.approx(default_cost, abs=1e-9)

    def test_missing_cache_keys_treated_as_zero(self):
        from distillate import pricing
        usage = {"input_tokens": 1000, "output_tokens": 500}  # no cache keys
        # Must not KeyError.
        cost = pricing.cost_for_usage("claude-haiku-4-5-20251001", usage)
        assert cost > 0

    def test_empty_usage_returns_zero(self):
        from distillate import pricing
        assert pricing.cost_for_usage("claude-opus-4-6", {}) == 0.0

    def test_negative_or_none_counts_safe(self):
        from distillate import pricing
        # Defensive — API shouldn't return negatives, but don't crash.
        usage = {"input_tokens": None, "output_tokens": 0}
        cost = pricing.cost_for_usage("claude-opus-4-6", usage)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# friendly_model_name
# ---------------------------------------------------------------------------

class TestFriendlyName:
    @pytest.mark.parametrize("mid,expected", [
        ("claude-opus-4-6", "Opus 4.6"),
        ("claude-sonnet-4-6", "Sonnet 4.6"),
        ("claude-sonnet-4-5-20250929", "Sonnet 4.5"),
        ("claude-haiku-4-5-20251001", "Haiku 4.5"),
    ])
    def test_known_ids(self, mid, expected):
        from distillate import pricing
        assert pricing.friendly_model_name(mid) == expected

    def test_unknown_id_returns_raw(self):
        from distillate import pricing
        assert pricing.friendly_model_name("claude-foo-bar") == "claude-foo-bar"


# ---------------------------------------------------------------------------
# supported_models (for picker)
# ---------------------------------------------------------------------------

class TestSupportedModels:
    def test_returns_expected_count(self):
        from distillate import pricing
        models = pricing.supported_models()
        assert len(models) == len(pricing._PICKER_ORDER)

    def test_entries_have_required_fields(self):
        from distillate import pricing
        for m in pricing.supported_models():
            assert set(m.keys()) >= {"id", "label", "family", "input_price", "output_price"}
            assert m["id"] in pricing.MODEL_PRICES
            assert m["label"] == pricing.friendly_model_name(m["id"])

    def test_order_matches_picker_order(self):
        from distillate import pricing
        ids = [m["id"] for m in pricing.supported_models()]
        assert ids == list(pricing._PICKER_ORDER)

    def test_family_grouping(self):
        from distillate import pricing
        families = {m["id"]: m["family"] for m in pricing.supported_models()}
        assert families["claude-opus-4-6"] == "opus"
        assert families["claude-sonnet-4-6"] == "sonnet"
        assert families["claude-sonnet-4-5-20250929"] == "sonnet"
        assert families["claude-haiku-4-5-20251001"] == "haiku"
        assert families["gemini-3.0"] == "gemini"
        assert families["gemini-3.1"] == "gemini"
