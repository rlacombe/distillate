# Covers: distillate/agent_runtime/usage_tracker.py
"""Tests for distillate.agent_runtime.usage_tracker — JSONL event log + aggregates.

RED tests. See docs/research/nicolas-billing-action-plan.md §7.3.

The tracker module doesn't exist yet — the fixture import raises
ImportError per-test so pytest collects every case (red bar) rather
than skipping the file.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def tracker(tmp_path):
    from distillate.agent_runtime.usage_tracker import UsageTracker
    return UsageTracker(path=tmp_path / "usage.jsonl")


def _tokens(inp=0, out=0, cr=0, cc=0):
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cr,
        "cache_creation_input_tokens": cc,
    }


# ---------------------------------------------------------------------------
# record()
# ---------------------------------------------------------------------------

class TestRecord:
    def test_record_appends_jsonl_row(self, tracker, tmp_path):
        tracker.record(
            model="claude-opus-4-6",
            role="nicolas_turn",
            session_id="sess-1",
            tokens=_tokens(inp=1000, out=500),
        )
        lines = (tmp_path / "usage.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["model"] == "claude-opus-4-6"
        assert row["role"] == "nicolas_turn"
        assert row["session_id"] == "sess-1"

    def test_record_includes_all_fields(self, tracker, tmp_path):
        tracker.record(
            model="claude-haiku-4-5-20251001",
            role="lab_repl_subcall",
            session_id="sess-2",
            tokens=_tokens(inp=100, out=50, cr=900, cc=0),
        )
        row = json.loads((tmp_path / "usage.jsonl").read_text().strip())
        assert {"ts", "model", "role", "session_id", "tokens", "cost_usd"} <= row.keys()
        assert row["tokens"] == _tokens(inp=100, out=50, cr=900, cc=0)
        assert row["cost_usd"] > 0

    def test_record_cost_matches_pricing_module(self, tracker, tmp_path):
        from distillate import pricing

        tokens = _tokens(inp=1_000_000, out=1_000_000, cr=1_000_000, cc=0)
        tracker.record(
            model="claude-opus-4-6",
            role="nicolas_turn",
            session_id="s",
            tokens=tokens,
        )
        row = json.loads((tmp_path / "usage.jsonl").read_text().strip())
        assert row["cost_usd"] == pytest.approx(
            pricing.cost_for_usage("claude-opus-4-6", tokens), abs=1e-9
        )

    def test_explicit_cost_override(self, tracker, tmp_path):
        """If caller passes cost_usd, don't recompute."""
        tracker.record(
            model="claude-opus-4-6",
            role="nicolas_turn",
            session_id="s",
            tokens=_tokens(inp=10, out=10),
            cost_usd=0.9999,
        )
        row = json.loads((tmp_path / "usage.jsonl").read_text().strip())
        assert row["cost_usd"] == pytest.approx(0.9999)

    def test_ts_is_iso8601_utc_z(self, tracker, tmp_path):
        tracker.record(
            model="claude-opus-4-6", role="nicolas_turn",
            session_id="s", tokens=_tokens(inp=10, out=10),
        )
        row = json.loads((tmp_path / "usage.jsonl").read_text().strip())
        assert row["ts"].endswith("Z")
        # Parseable
        datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# snapshot() — aggregates
# ---------------------------------------------------------------------------

class TestSnapshotAggregates:
    def test_empty(self, tracker):
        snap = tracker.snapshot()
        for scope in ("session", "today", "week", "all"):
            assert snap[scope]["input_tokens"] == 0
            assert snap[scope]["output_tokens"] == 0
            assert snap[scope]["cost_usd"] == 0.0
        assert snap["by_model"] == {}

    def test_session_filters_by_id(self, tracker):
        tracker.record(model="claude-opus-4-6", role="nicolas_turn",
                       session_id="A", tokens=_tokens(inp=1000, out=1000))
        tracker.record(model="claude-opus-4-6", role="nicolas_turn",
                       session_id="B", tokens=_tokens(inp=5000, out=5000))
        snap = tracker.snapshot(session_id="A")
        assert snap["session"]["input_tokens"] == 1000
        assert snap["all"]["input_tokens"] == 6000

    def test_today_respects_utc_midnight(self, tracker, tmp_path):
        # Hand-write a row dated yesterday.
        yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        (tmp_path / "usage.jsonl").write_text(json.dumps({
            "ts": yesterday,
            "model": "claude-opus-4-6",
            "role": "nicolas_turn",
            "session_id": "old",
            "tokens": _tokens(inp=999, out=999),
            "cost_usd": 1.23,
        }) + "\n")
        tracker.record(model="claude-opus-4-6", role="nicolas_turn",
                       session_id="new", tokens=_tokens(inp=10, out=10))
        snap = tracker.snapshot()
        assert snap["today"]["input_tokens"] == 10
        assert snap["all"]["input_tokens"] == 1009

    def test_week_is_rolling_7_days(self, tracker, tmp_path):
        eight_days = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        three_days = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        (tmp_path / "usage.jsonl").write_text(
            json.dumps({
                "ts": eight_days, "model": "claude-opus-4-6", "role": "nicolas_turn",
                "session_id": "s", "tokens": _tokens(inp=100), "cost_usd": 0.01,
            }) + "\n" +
            json.dumps({
                "ts": three_days, "model": "claude-opus-4-6", "role": "nicolas_turn",
                "session_id": "s", "tokens": _tokens(inp=200), "cost_usd": 0.02,
            }) + "\n"
        )
        snap = tracker.snapshot()
        assert snap["week"]["input_tokens"] == 200
        assert snap["all"]["input_tokens"] == 300

    def test_by_model_breakdown(self, tracker):
        tracker.record(model="claude-opus-4-6", role="nicolas_turn",
                       session_id="s", tokens=_tokens(inp=1000, out=500))
        tracker.record(model="claude-haiku-4-5-20251001", role="lab_repl_subcall",
                       session_id="s", tokens=_tokens(inp=2000, out=1000))
        snap = tracker.snapshot()
        assert "claude-opus-4-6" in snap["by_model"]
        assert "claude-haiku-4-5-20251001" in snap["by_model"]
        assert snap["by_model"]["claude-opus-4-6"]["input_tokens"] == 1000
        assert snap["by_model"]["claude-haiku-4-5-20251001"]["input_tokens"] == 2000

    def test_mixed_roles_both_counted(self, tracker):
        tracker.record(model="claude-opus-4-6", role="nicolas_turn",
                       session_id="s", tokens=_tokens(inp=100, out=50))
        tracker.record(model="claude-haiku-4-5-20251001", role="lab_repl_subcall",
                       session_id="s", tokens=_tokens(inp=200, out=100))
        snap = tracker.snapshot(session_id="s")
        assert snap["session"]["input_tokens"] == 300
        assert snap["session"]["output_tokens"] == 150

    def test_current_model_reflects_preferences(self, tracker, tmp_path, monkeypatch):
        from distillate import preferences, config
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")
        if hasattr(preferences, "_reset_cache"):
            preferences._reset_cache()
        preferences.set("nicolas_model", "claude-opus-4-6")
        snap = tracker.snapshot()
        assert snap["current_model"] == "claude-opus-4-6"

    def test_cost_usd_stable_sum(self, tracker):
        """Cost summed from rows should match sum of recorded costs exactly."""
        costs = []
        for i in range(10):
            tracker.record(
                model="claude-opus-4-6", role="nicolas_turn", session_id="s",
                tokens=_tokens(inp=1000 * i, out=500 * i),
            )
        snap = tracker.snapshot()
        # File-sourced total must equal the implicit sum.
        rows = [json.loads(l) for l in (tracker.path).read_text().splitlines()]
        assert snap["all"]["cost_usd"] == pytest.approx(sum(r["cost_usd"] for r in rows))


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_malformed_line_skipped(self, tracker, tmp_path):
        tracker.record(model="claude-opus-4-6", role="nicolas_turn",
                       session_id="s", tokens=_tokens(inp=100, out=50))
        # Hand-insert a junk line.
        with (tmp_path / "usage.jsonl").open("a") as f:
            f.write("this is not json\n")
        tracker.record(model="claude-opus-4-6", role="nicolas_turn",
                       session_id="s", tokens=_tokens(inp=200, out=100))
        snap = tracker.snapshot()
        assert snap["all"]["input_tokens"] == 300  # both valid rows counted

    def test_missing_file_returns_empty_snapshot(self, tmp_path):
        from distillate.agent_runtime.usage_tracker import UsageTracker
        t = UsageTracker(path=tmp_path / "nonexistent.jsonl")
        snap = t.snapshot()
        assert snap["all"]["cost_usd"] == 0.0

    def test_concurrent_record_does_not_interleave(self, tracker):
        """Two threads × 100 records each = 200 parseable JSON lines."""
        def worker():
            for _ in range(100):
                tracker.record(
                    model="claude-opus-4-6", role="nicolas_turn",
                    session_id="s", tokens=_tokens(inp=1, out=1),
                )
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()

        lines = tracker.path.read_text().splitlines()
        assert len(lines) == 200
        for line in lines:
            json.loads(line)  # all parseable


# ---------------------------------------------------------------------------
# reset_session
# ---------------------------------------------------------------------------

class TestResetSession:
    def test_reset_session_clears_in_memory_only(self, tracker):
        tracker.record(model="claude-opus-4-6", role="nicolas_turn",
                       session_id="A", tokens=_tokens(inp=1000, out=500))
        tracker.reset_session("A")
        snap = tracker.snapshot(session_id="A")
        # After reset, session scope is zeroed but all-time remains.
        assert snap["session"]["input_tokens"] == 0
        assert snap["all"]["input_tokens"] == 1000
