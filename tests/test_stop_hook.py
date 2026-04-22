# Covers: distillate/hooks/on_stop.py (L4 auto-conclude overdue runs),
#          distillate/launcher.py + distillate/experiment_tools/run_tools.py
#          (cross-cutting wrap-up grace model)
"""L4 — Stop hook auto-concludes overdue runs.

When the Claude Code session ends with a ``running`` entry past its
``wrap_deadline_at``, the on_stop hook writes a synthetic completion with
``status="timeout"`` and ``auto_concluded=true``. This closes the "10:33 /
10:00" UI fiction and lets the next launch start clean.

Cross-cutting: the wrap-up grace model — train and wrap deadlines are distinct
so the renderer can show "wrapping up" vs "over budget".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers shared in this file
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path):
    """A bare project directory with .distillate/ ready."""
    p = tmp_path / "proj"
    p.mkdir()
    (p / ".distillate").mkdir()
    return p


def _write_budget(project: Path, *, train: int, wrap: Optional[int] = None) -> Path:
    """Helper: write a budget.json with the new L2 fields."""
    if wrap is None:
        wrap = max(60, int(train * 0.1))
    data = {
        "run_budget_seconds": train,
        "train_budget_seconds": train,
        "wrap_budget_seconds": wrap,
        "session_budget_seconds": None,
        "session_started_at": None,
    }
    path = project / ".distillate" / "budget.json"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return path


def _runs_jsonl(project: Path) -> Path:
    return project / ".distillate" / "runs.jsonl"


def _read_runs(project: Path) -> list[dict]:
    path = _runs_jsonl(project)
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ===========================================================================
# L4 — Stop hook auto-concludes overdue runs.
# ===========================================================================


class TestL4StopHookAutoConcludes:
    """When the agent's session stops with a ``running`` entry past its
    ``wrap_deadline_at``, the stop hook synthesizes a ``timeout`` completion
    so the run isn't shown as "still in progress" forever.
    """

    def _make_running_run(
        self, project_dir, *, started_minutes_ago: int,
        train_budget: int = 600, wrap_budget: int = 60,
    ) -> str:
        """Create a 'running' entry that started ``started_minutes_ago`` ago."""
        started = datetime.now(timezone.utc) - timedelta(minutes=started_minutes_ago)
        train_deadline = started + timedelta(seconds=train_budget)
        wrap_deadline = train_deadline + timedelta(seconds=wrap_budget)
        run_id = f"xp-{started_minutes_ago:04d}"
        entry = {
            "id": run_id,
            "timestamp": started.isoformat(),
            "started_at": started.isoformat(),
            "status": "running",
            "description": "test run",
            "train_deadline_at": train_deadline.isoformat(),
            "wrap_deadline_at": wrap_deadline.isoformat(),
        }
        with open(_runs_jsonl(project_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return run_id

    def _invoke_stop_hook(self, project_dir, monkeypatch):
        """Call the on_stop main() with a synthesized Stop event."""
        from distillate.hooks import on_stop
        monkeypatch.chdir(project_dir)
        monkeypatch.setenv("DISTILLATE_SESSION", "1")
        # Stop event payload (matches Claude Code hook contract)
        payload = json.dumps({"session_id": "sess-1", "stop_reason": "user"})
        # Replace stdin with our payload
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        on_stop.main()

    def test_overdue_running_is_concluded(self, project_dir, monkeypatch):
        run_id = self._make_running_run(
            project_dir, started_minutes_ago=15,  # well past 10 + 1 wrap
            train_budget=600, wrap_budget=60,
        )
        self._invoke_stop_hook(project_dir, monkeypatch)

        runs = _read_runs(project_dir)
        # Find the most recent entry for this run_id
        last_for_id = [r for r in runs if r["id"] == run_id][-1]
        assert last_for_id["status"] == "timeout", (
            f"Overdue run must be concluded with status='timeout'; "
            f"got status={last_for_id.get('status')!r}"
        )

    def test_auto_conclusion_marks_origin(self, project_dir, monkeypatch):
        """The synthetic completion is flagged so the UI can show it
        differently from a normal completion (and so we can audit later).
        """
        run_id = self._make_running_run(project_dir, started_minutes_ago=15)
        self._invoke_stop_hook(project_dir, monkeypatch)

        runs = _read_runs(project_dir)
        last_for_id = [r for r in runs if r["id"] == run_id][-1]
        assert last_for_id.get("auto_concluded") is True

    def test_non_overdue_running_left_alone(self, project_dir, monkeypatch):
        """A run that's still inside its wrap window is the agent's job to
        finish — the hook must not preempt it.
        """
        run_id = self._make_running_run(
            project_dir, started_minutes_ago=2,  # well inside 10 + 1 budget
        )
        self._invoke_stop_hook(project_dir, monkeypatch)

        runs = _read_runs(project_dir)
        # The original 'running' entry stays as-is, no synthetic completion
        for_id = [r for r in runs if r["id"] == run_id]
        assert len(for_id) == 1
        assert for_id[0]["status"] == "running"

    def test_already_concluded_runs_left_alone(self, project_dir, monkeypatch):
        """Runs that already have a completed/best/crash entry must not get
        a duplicate 'timeout' entry just because their original 'running'
        line is still in runs.jsonl."""
        run_id = self._make_running_run(project_dir, started_minutes_ago=15)
        # Append a normal completion (what conclude_run would do)
        with open(_runs_jsonl(project_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "id": run_id, "status": "completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "results": {"loss": 0.42},
            }) + "\n")

        before_count = len(_read_runs(project_dir))
        self._invoke_stop_hook(project_dir, monkeypatch)
        after_count = len(_read_runs(project_dir))

        assert before_count == after_count, (
            "Stop hook duplicated a run that was already concluded"
        )

    def test_handles_multiple_overdue_runs(self, project_dir, monkeypatch):
        """If two runs both went over (rare but possible after a crash +
        restart), both get a synthetic completion."""
        rid1 = self._make_running_run(project_dir, started_minutes_ago=15)
        rid2 = self._make_running_run(project_dir, started_minutes_ago=20)

        self._invoke_stop_hook(project_dir, monkeypatch)

        runs = _read_runs(project_dir)
        timed_out = {r["id"] for r in runs if r.get("status") == "timeout"}
        assert {rid1, rid2}.issubset(timed_out)

    def test_missing_runs_jsonl_is_silent(self, project_dir, monkeypatch):
        """No runs file -> nothing to do, no crash."""
        # _runs_jsonl is not created
        # Should not raise
        self._invoke_stop_hook(project_dir, monkeypatch)

    def test_run_without_wrap_deadline_uses_budget_json_fallback(
        self, project_dir, monkeypatch
    ):
        """Older runs (started before L3 shipped) won't have
        ``wrap_deadline_at``. The hook must still be able to reason about
        them using budget.json + started_at as a fallback.
        """
        _write_budget(project_dir, train=600, wrap=60)
        # Legacy entry: no train_deadline_at, no wrap_deadline_at
        started = datetime.now(timezone.utc) - timedelta(minutes=15)
        run_id = "xp-legacy"
        with open(_runs_jsonl(project_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "id": run_id, "status": "running",
                "timestamp": started.isoformat(),
                "started_at": started.isoformat(),
                "description": "legacy run",
            }) + "\n")

        self._invoke_stop_hook(project_dir, monkeypatch)

        runs = _read_runs(project_dir)
        timed_out = [r for r in runs if r["id"] == run_id and r.get("status") == "timeout"]
        assert timed_out, (
            "Legacy run (no wrap_deadline_at) past budget.json budget should "
            "still be auto-concluded via fallback"
        )

    def test_synthetic_completion_includes_metrics_field(
        self, project_dir, monkeypatch
    ):
        """The renderer assumes ``results`` exists on a non-running entry.
        Synthesize an empty dict so downstream chart rendering doesn't crash.
        """
        run_id = self._make_running_run(project_dir, started_minutes_ago=15)
        self._invoke_stop_hook(project_dir, monkeypatch)

        runs = _read_runs(project_dir)
        last_for_id = [r for r in runs if r["id"] == run_id][-1]
        assert "results" in last_for_id
        assert isinstance(last_for_id["results"], dict)

    def test_emits_session_end_event_unchanged(self, project_dir, monkeypatch):
        """L4 adds auto-conclude, but the existing session_end event must
        still be appended -- other consumers depend on it."""
        self._make_running_run(project_dir, started_minutes_ago=15)
        self._invoke_stop_hook(project_dir, monkeypatch)

        events_path = project_dir / ".distillate" / "events.jsonl"
        assert events_path.exists()
        events = [json.loads(line) for line in
                  events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert any(e.get("type") == "session_end" for e in events)


# ===========================================================================
# Cross-cutting — wrap-up grace is the user-visible win.
# ===========================================================================


class TestWrapUpGraceModel:
    """The user-visible scenario: training finishes near the budget, the
    agent calls conclude_run + commits over the next 30s. With L1+L3,
    that 30s is *expected* (it's the wrap window) and the run should NOT
    show as overdue until the wrap window also expires.
    """

    def test_wrap_window_distinct_from_train_window(self, tmp_path):
        from distillate.launcher import write_budget_json
        write_budget_json(tmp_path, {"duration_minutes": 10})
        data = json.loads((tmp_path / ".distillate" / "budget.json").read_text())
        assert data["wrap_budget_seconds"] != data["train_budget_seconds"], (
            "Train and wrap budgets must be distinguishable so the renderer "
            "can show 'wrapping up' vs 'over budget'."
        )

    def test_total_deadline_is_train_plus_wrap(self, project_dir, monkeypatch):
        """Sanity: a run whose wrap deadline = start + train + wrap."""
        from distillate.experiment_tools.run_tools import start_run
        from distillate.state import State
        _write_budget(project_dir, train=600, wrap=60)
        state = State()
        state.add_experiment("p1", "p", str(project_dir))
        state.update_experiment("p1", duration_minutes=10)

        start_run(state=state, project="p", description="d")

        run = _read_runs(project_dir)[-1]
        started = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
        wrap_deadline = datetime.fromisoformat(
            run["wrap_deadline_at"].replace("Z", "+00:00")
        )
        total = (wrap_deadline - started).total_seconds()
        assert abs(total - 660) < 2, (
            f"Total deadline should be 600 + 60 = 660s; got {total:.1f}"
        )


# ===========================================================================
# Experimentalist cost tracking — delta recording
# ===========================================================================


def _write_transcript(path: Path, messages: list[tuple[str, str, dict]]) -> None:
    """Write a minimal Claude Code transcript JSONL.

    Each entry in *messages* is (msg_id, model, usage_dict).
    """
    with path.open("w", encoding="utf-8") as f:
        for msg_id, model, usage in messages:
            row = {
                "type": "assistant",
                "message": {
                    "id": msg_id,
                    "model": model,
                    "role": "assistant",
                    "usage": usage,
                },
            }
            f.write(json.dumps(row) + "\n")


class TestExperimentalistCostTracking:
    """_record_session_tokens must append incremental deltas, not cumulative
    session totals.  Re-reading the same transcript should never double-count.
    """

    def _setup_tracker(self, tmp_path, monkeypatch):
        """Point the usage_tracker singleton at a fresh tmp file."""
        import distillate.agent_runtime.usage_tracker as ut
        monkeypatch.setattr(ut, "USAGE_PATH", tmp_path / "usage.jsonl")
        ut._reset_singleton()
        return ut.get_tracker()

    def test_first_stop_records_full_total(self, tmp_path, monkeypatch):
        """First on_stop for a session records all tokens seen so far."""
        from distillate.hooks.on_stop import _record_session_tokens

        tracker = self._setup_tracker(tmp_path, monkeypatch)
        transcript = tmp_path / "session.jsonl"
        _write_transcript(transcript, [
            ("msg_001", "claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
            ("msg_002", "claude-sonnet-4-6", {"input_tokens": 120, "output_tokens": 60,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
        ])

        _record_session_tokens("sess-abc", transcript)

        rows = list(tracker._iter_rows())
        assert len(rows) == 1
        assert rows[0]["model"] == "claude-sonnet-4-6"
        assert rows[0]["tokens"]["input_tokens"] == 220   # 100 + 120
        assert rows[0]["tokens"]["output_tokens"] == 110  # 50 + 60

    def test_second_stop_records_only_delta(self, tmp_path, monkeypatch):
        """Second on_stop for the same session records only new tokens, not the
        cumulative total.  This is the core regression guard for the bug where
        the full transcript was re-summed on every run stop.
        """
        from distillate.hooks.on_stop import _record_session_tokens

        tracker = self._setup_tracker(tmp_path, monkeypatch)
        transcript = tmp_path / "session.jsonl"

        # Run 1 stops with 2 messages
        _write_transcript(transcript, [
            ("msg_001", "claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
            ("msg_002", "claude-sonnet-4-6", {"input_tokens": 120, "output_tokens": 60,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
        ])
        _record_session_tokens("sess-abc", transcript)

        # Run 2 stops — transcript grows with 2 new messages
        _write_transcript(transcript, [
            ("msg_001", "claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
            ("msg_002", "claude-sonnet-4-6", {"input_tokens": 120, "output_tokens": 60,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
            ("msg_003", "claude-sonnet-4-6", {"input_tokens": 200, "output_tokens": 80,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
            ("msg_004", "claude-sonnet-4-6", {"input_tokens": 150, "output_tokens": 70,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
        ])
        _record_session_tokens("sess-abc", transcript)

        rows = list(tracker._iter_rows())
        assert len(rows) == 2, f"Expected 2 rows (one per run), got {len(rows)}"

        # First row: msgs 001+002
        assert rows[0]["tokens"]["input_tokens"] == 220
        assert rows[0]["tokens"]["output_tokens"] == 110

        # Second row: only delta from msgs 003+004
        assert rows[1]["tokens"]["input_tokens"] == 350   # 200 + 150
        assert rows[1]["tokens"]["output_tokens"] == 150  # 80 + 70

    def test_repeated_stop_with_no_new_tokens_records_nothing(self, tmp_path, monkeypatch):
        """If the transcript hasn't changed since the last stop (e.g. a stop
        event fired without any new agent turns), nothing is appended.
        """
        from distillate.hooks.on_stop import _record_session_tokens

        tracker = self._setup_tracker(tmp_path, monkeypatch)
        transcript = tmp_path / "session.jsonl"
        _write_transcript(transcript, [
            ("msg_001", "claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
        ])

        _record_session_tokens("sess-abc", transcript)
        _record_session_tokens("sess-abc", transcript)  # Same transcript, no new tokens

        rows = list(tracker._iter_rows())
        assert len(rows) == 1, (
            f"Repeated stop with no new tokens must not duplicate the entry; "
            f"got {len(rows)} rows"
        )

    def test_model_switch_mid_session_tracked_separately(self, tmp_path, monkeypatch):
        """When a session uses two models, each model's delta is tracked
        independently so mixed-model sessions are correctly attributed.
        """
        from distillate.hooks.on_stop import _record_session_tokens

        tracker = self._setup_tracker(tmp_path, monkeypatch)
        transcript = tmp_path / "session.jsonl"

        # Run 1: Opus only
        _write_transcript(transcript, [
            ("msg_001", "claude-opus-4-7", {"input_tokens": 50, "output_tokens": 200,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
        ])
        _record_session_tokens("sess-abc", transcript)

        # Run 2: Opus unchanged, Sonnet added
        _write_transcript(transcript, [
            ("msg_001", "claude-opus-4-7", {"input_tokens": 50, "output_tokens": 200,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
            ("msg_002", "claude-sonnet-4-6", {"input_tokens": 300, "output_tokens": 400,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
        ])
        _record_session_tokens("sess-abc", transcript)

        rows = list(tracker._iter_rows())
        assert len(rows) == 2, f"Expected 2 rows total; got {len(rows)}"

        models = [r["model"] for r in rows]
        assert "claude-opus-4-7" in models
        assert "claude-sonnet-4-6" in models

        opus_row = next(r for r in rows if r["model"] == "claude-opus-4-7")
        sonnet_row = next(r for r in rows if r["model"] == "claude-sonnet-4-6")

        # Opus: only from run 1 (no new opus in run 2)
        assert opus_row["tokens"]["output_tokens"] == 200
        # Sonnet: only the delta (run 2 only)
        assert sonnet_row["tokens"]["input_tokens"] == 300
