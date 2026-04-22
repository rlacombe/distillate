# Covers: distillate/agent_sdk.py
"""Integration tests for NicolasClient billing wiring.

Mocks the claude-agent-sdk ClaudeSDKClient. Verifies:
  - turn_end event carries the token breakdown + model + our-computed cost
  - UsageTracker receives one event per turn
  - set_model persists to preferences
  - init reads preferred model if none passed
  - lab_repl sub-LLM calls record into the same tracker

RED tests. Written before implementation — many will import-fail until the
new modules exist, which is expected.

See docs/research/nicolas-billing-action-plan.md §7.4.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fake SDK primitives
# ---------------------------------------------------------------------------

class _FakeUsage:
    def __init__(self, inp=1200, out=450, cr=10200, cc=0):
        self.input_tokens = inp
        self.output_tokens = out
        self.cache_read_input_tokens = cr
        self.cache_creation_input_tokens = cc


class _FakeResult:
    """Mimics claude_agent_sdk.ResultMessage with .usage.

    Accepts ``usage`` as either a _FakeUsage object (attribute access) or
    a plain dict (which is what the real SDK actually returns — see
    ``claude_agent_sdk._internal.message_parser`` where ``usage=data.get("usage")``).
    """
    def __init__(self, session_id="sess-1", model="claude-opus-4-6",
                 cost_usd=0.0510, num_turns=1, usage=None, model_usage=None):
        self.session_id = session_id
        self.model = model
        self.total_cost_usd = cost_usd
        self.num_turns = num_turns
        self.usage = usage if usage is not None else _FakeUsage()
        self.model_usage = model_usage


class _FakeInit:
    subtype = "init"
    def __init__(self, session_id="sess-1"):
        self.data = {"session_id": session_id}


def _install_fake_sdk(monkeypatch, response_stream):
    """Patch claude_agent_sdk so ClaudeSDKClient yields the given stream."""
    import claude_agent_sdk as sdk_mod

    class FakeClient:
        def __init__(self, options):
            self.options = options
        async def connect(self): pass
        async def query(self, text): pass
        async def receive_response(self):
            for m in response_stream:
                yield m
        async def disconnect(self): pass
        async def interrupt(self): pass
        async def set_model(self, m): pass

    monkeypatch.setattr(sdk_mod, "ClaudeSDKClient", FakeClient)
    # Make isinstance(init, SystemMessage) and isinstance(result, ResultMessage) work.
    monkeypatch.setattr(sdk_mod, "SystemMessage", _FakeInit)
    monkeypatch.setattr(sdk_mod, "ResultMessage", _FakeResult)
    # Empty placeholders for message types we don't emit.
    for name in ("AssistantMessage", "UserMessage", "TextBlock",
                 "ToolUseBlock", "ToolResultBlock"):
        monkeypatch.setattr(sdk_mod, name, type(name, (), {}))


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    from distillate import config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "NICOLAS_SESSIONS_FILE", tmp_path / "nicolas_sessions.json")

    try:
        from distillate import preferences
        monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")
        if hasattr(preferences, "_reset_cache"):
            preferences._reset_cache()
    except ImportError:
        pass

    try:
        from distillate.agent_runtime import usage_tracker
        monkeypatch.setattr(usage_tracker, "USAGE_PATH", tmp_path / "usage.jsonl")
        if hasattr(usage_tracker, "_reset_singleton"):
            usage_tracker._reset_singleton()
    except ImportError:
        pass
    return tmp_path


async def _collect_async(agen):
    return [e async for e in agen]


def _run_turn(nc, text="hi"):
    return asyncio.run(_collect_async(nc.send(text)))


# ---------------------------------------------------------------------------
# turn_end shape
# ---------------------------------------------------------------------------

class TestTurnEndShape:
    def test_turn_end_includes_model(self, isolated_paths, monkeypatch):
        from distillate.agent_sdk import NicolasClient
        from distillate.state import State

        _install_fake_sdk(monkeypatch, [_FakeInit("s1"),
                                        _FakeResult(session_id="s1",
                                                    model="claude-opus-4-6")])
        nc = NicolasClient(State(), model="claude-opus-4-6")
        events = _run_turn(nc)
        turn = next(e for e in events if e["type"] == "turn_end")
        assert turn["model"] == "claude-opus-4-6"

    def test_turn_end_includes_token_breakdown(self, isolated_paths, monkeypatch):
        from distillate.agent_sdk import NicolasClient
        from distillate.state import State

        usage = _FakeUsage(inp=1200, out=450, cr=10200, cc=500)
        _install_fake_sdk(monkeypatch, [_FakeInit(), _FakeResult(usage=usage)])
        nc = NicolasClient(State(), model="claude-opus-4-6")
        events = _run_turn(nc)
        turn = next(e for e in events if e["type"] == "turn_end")
        assert turn["tokens"]["input"] == 1200
        assert turn["tokens"]["output"] == 450
        assert turn["tokens"]["cache_read"] == 10200
        assert turn["tokens"]["cache_creation"] == 500

    def test_turn_end_cost_matches_pricing_module(self, isolated_paths, monkeypatch):
        from distillate.agent_sdk import NicolasClient
        from distillate.state import State
        from distillate import pricing

        usage = _FakeUsage(inp=1_000_000, out=1_000_000, cr=0, cc=0)
        _install_fake_sdk(monkeypatch, [_FakeInit(),
                                        _FakeResult(model="claude-opus-4-6",
                                                    usage=usage)])
        nc = NicolasClient(State(), model="claude-opus-4-6")
        events = _run_turn(nc)
        turn = next(e for e in events if e["type"] == "turn_end")
        expected = pricing.cost_for_usage("claude-opus-4-6", {
            "input_tokens": 1_000_000, "output_tokens": 1_000_000,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        })
        assert turn["cost_usd"] == pytest.approx(expected, abs=1e-9)

    def test_turn_end_includes_sdk_reported_cost(self, isolated_paths, monkeypatch):
        from distillate.agent_sdk import NicolasClient
        from distillate.state import State

        _install_fake_sdk(monkeypatch, [_FakeInit(), _FakeResult(cost_usd=0.0510)])
        nc = NicolasClient(State(), model="claude-opus-4-6")
        events = _run_turn(nc)
        turn = next(e for e in events if e["type"] == "turn_end")
        assert turn["sdk_reported_cost_usd"] == pytest.approx(0.0510)

    def test_dict_shaped_usage_extracted(self, isolated_paths, monkeypatch):
        """Regression: the real SDK hands us ``usage`` as a plain dict.

        The old extraction used ``getattr(usage_dict, "input_tokens", 0)``
        which always returned ``0`` on a dict — turn_end would silently
        report zero tokens and our pricing.cost_for_usage path wouldn't
        fire. Pin the dict-shaped contract so this can't silently break.
        """
        from distillate.agent_sdk import NicolasClient
        from distillate.state import State

        usage_dict = {
            "input_tokens": 1500,
            "output_tokens": 800,
            "cache_read_input_tokens": 12000,
            "cache_creation_input_tokens": 400,
        }
        _install_fake_sdk(monkeypatch, [
            _FakeInit(),
            _FakeResult(model="claude-opus-4-6", usage=usage_dict),
        ])
        nc = NicolasClient(State(), model="claude-opus-4-6")
        events = _run_turn(nc)
        turn = next(e for e in events if e["type"] == "turn_end")
        assert turn["tokens"]["input"] == 1500
        assert turn["tokens"]["output"] == 800
        assert turn["tokens"]["cache_read"] == 12000
        assert turn["tokens"]["cache_creation"] == 400
        assert turn["cost_usd"] > 0

    def test_missing_usage_falls_back_to_total_cost_usd(
        self, isolated_paths, monkeypatch,
    ):
        """If the SDK omits the usage dict, the pill still reflects spend
        via ``total_cost_usd`` so the user isn't stuck at $0.00 forever.
        """
        from distillate.agent_sdk import NicolasClient
        from distillate.state import State

        _install_fake_sdk(monkeypatch, [
            _FakeInit(),
            # Empty dict = SDK omitted token breakdown; fallback must fire.
            _FakeResult(model="claude-opus-4-6", usage={}, cost_usd=0.12),
        ])
        nc = NicolasClient(State(), model="claude-opus-4-6")
        events = _run_turn(nc)
        turn = next(e for e in events if e["type"] == "turn_end")
        assert turn["cost_usd"] == pytest.approx(0.12)


# ---------------------------------------------------------------------------
# UsageTracker wiring
# ---------------------------------------------------------------------------

class TestUsageTrackerWiring:
    def test_turn_records_into_usage_tracker(self, isolated_paths, monkeypatch):
        from distillate.agent_sdk import NicolasClient
        from distillate.agent_runtime import usage_tracker
        from distillate.state import State

        _install_fake_sdk(monkeypatch, [_FakeInit("s1"), _FakeResult(session_id="s1")])
        nc = NicolasClient(State(), model="claude-opus-4-6")
        _run_turn(nc)

        tracker = usage_tracker.get_tracker()
        snap = tracker.snapshot(session_id="s1")
        assert snap["session"]["input_tokens"] > 0
        assert snap["session"]["cost_usd"] > 0

    def test_turn_recorded_with_role_nicolas_turn(self, isolated_paths, monkeypatch):
        from distillate.agent_sdk import NicolasClient
        from distillate.agent_runtime import usage_tracker
        from distillate.state import State

        _install_fake_sdk(monkeypatch, [_FakeInit("s1"), _FakeResult(session_id="s1")])
        nc = NicolasClient(State(), model="claude-opus-4-6")
        _run_turn(nc)

        rows = [json.loads(l) for l in usage_tracker.USAGE_PATH.read_text().splitlines()]
        assert any(r["role"] == "nicolas_turn" for r in rows)


# ---------------------------------------------------------------------------
# set_model persistence
# ---------------------------------------------------------------------------

class TestModelPersistence:
    def test_set_model_persists_to_preferences(self, isolated_paths, monkeypatch):
        from distillate.agent_sdk import NicolasClient
        from distillate import preferences
        from distillate.state import State

        _install_fake_sdk(monkeypatch, [])
        nc = NicolasClient(State(), model="claude-sonnet-4-6")
        asyncio.run(nc.set_model("claude-opus-4-6"))
        assert preferences.get("nicolas_model") == "claude-opus-4-6"

    def test_init_reads_model_from_preferences(self, isolated_paths, monkeypatch):
        from distillate.agent_sdk import NicolasClient
        from distillate import preferences
        from distillate.state import State

        preferences.set("nicolas_model", "claude-haiku-4-5-20251001")
        nc = NicolasClient(State())  # no model passed
        assert nc._model == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Sub-LLM -> tracker
# ---------------------------------------------------------------------------

class TestSubLLMTracking:
    def test_cost_tracker_record_also_appends_to_usage_tracker(
        self, isolated_paths, monkeypatch
    ):
        """CostTracker.record() must also flow a row into UsageTracker.

        After wiring, each record() writes to usage.jsonl with
        role='lab_repl_subcall'.
        """
        from distillate.agent_runtime.lab_repl import CostTracker
        from distillate.agent_runtime import usage_tracker

        ct = CostTracker()
        ct.set_session("s1")  # new helper threaded through by the impl
        fake_resp = MagicMock()
        fake_resp.usage.input_tokens = 1000
        fake_resp.usage.output_tokens = 500
        fake_resp.usage.cache_read_input_tokens = 0
        fake_resp.usage.cache_creation_input_tokens = 0
        ct.record(fake_resp, model="claude-haiku-4-5-20251001")

        rows = [json.loads(l) for l in usage_tracker.USAGE_PATH.read_text().splitlines()]
        assert any(r["role"] == "lab_repl_subcall" for r in rows)
        assert any(r["model"] == "claude-haiku-4-5-20251001" for r in rows)
        # API path tags rows so the UI can split spend.
        assert all(r.get("billing_source") == "api" for r in rows)

    def test_record_cli_tags_row_as_subscription(
        self, isolated_paths, monkeypatch
    ):
        """CostTracker.record_cli() ingests a `claude -p` JSON blob and
        writes a usage row tagged ``billing_source="subscription"``, with
        the CLI's ``total_cost_usd`` preserved verbatim as the shadow cost.
        """
        from distillate.agent_runtime.lab_repl import CostTracker
        from distillate.agent_runtime import usage_tracker

        ct = CostTracker()
        ct.set_session("s1")
        cli_payload = {
            "result": "pong",
            "total_cost_usd": 0.015755,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 58,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 10232,
            },
            "modelUsage": {
                "claude-haiku-4-5-20251001": {
                    "inputTokens": 10, "outputTokens": 58,
                    "costUSD": 0.015755,
                }
            },
        }
        ct.record_cli(cli_payload, model="claude-haiku-4-5-20251001")

        rows = [json.loads(l) for l in usage_tracker.USAGE_PATH.read_text().splitlines()]
        assert len(rows) == 1
        row = rows[0]
        assert row["role"] == "lab_repl_subcall"
        assert row["billing_source"] == "subscription"
        assert row["model"] == "claude-haiku-4-5-20251001"
        assert row["tokens"]["cache_creation_input_tokens"] == 10232
        # Shadow cost from the CLI is preserved, not recomputed.
        assert row["cost_usd"] == pytest.approx(0.015755, abs=1e-6)

    def test_snapshot_splits_subscription_and_api_spend(
        self, isolated_paths, monkeypatch
    ):
        """UsageTracker.snapshot() buckets must split api vs subscription
        cost so the billing UI can show real API spend separately from
        subscription-backed shadow cost.
        """
        from distillate.agent_runtime import usage_tracker

        t = usage_tracker.get_tracker()
        t.record(
            model="claude-haiku-4-5-20251001", role="lab_repl_subcall",
            session_id="s1",
            tokens={"input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0},
            cost_usd=0.01, billing_source="api",
        )
        t.record(
            model="claude-haiku-4-5-20251001", role="lab_repl_subcall",
            session_id="s1",
            tokens={"input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 10000},
            cost_usd=0.015, billing_source="subscription",
        )

        snap = t.snapshot(session_id="s1")
        assert snap["session"]["cost_usd"] == pytest.approx(0.025, abs=1e-6)
        assert snap["session"]["api_cost_usd"] == pytest.approx(0.01, abs=1e-6)
        assert snap["session"]["subscription_cost_usd"] == pytest.approx(0.015, abs=1e-6)
