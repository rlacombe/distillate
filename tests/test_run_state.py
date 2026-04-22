# Covers: distillate/experiment_tools/run_tools.py (conclude_run metrics_series),
#          distillate/experiments.py (prune_orphan_state_runs),
#          distillate/experiment_tools/session_tools.py (launch_experiment_tool guard)
"""conclude_run metrics_series freezing, orphan state pruning, and the
single-active-session launch guard.
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


def _read_runs(project: Path) -> list[dict]:
    path = project / ".distillate" / "runs.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ===========================================================================
# conclude_run freezes per-epoch metric events as metrics_series.
# ===========================================================================


class TestConcludeRunPersistsMetricsSeries:
    """``conclude_run`` must freeze the per-epoch metric_update events
    that belong to this run onto the completion entry as
    ``metrics_series``. Without this, every consumer (sparkline in the
    runs list, convergence-shape classifier, overlay charts) has to
    replay events.jsonl and re-match by timestamp — expensive and
    fragile when sessions restart.
    """

    def _state(self, project_dir):
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "test-proj", str(project_dir))
        state.update_experiment("p1", duration_minutes=10)
        return state

    def _write_events(self, project_dir, events):
        events_path = project_dir / ".distillate" / "events.jsonl"
        with open(events_path, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

    def test_attaches_metrics_series_to_completion_entry(self, project_dir):
        from distillate.experiment_tools.run_tools import start_run, conclude_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        r = start_run(state=state, project="test-proj", description="r1")
        started_ts = r["started_at"]
        # Write 3 metric_update events within the run window
        start_dt = datetime.fromisoformat(started_ts.replace("Z", "+00:00"))
        self._write_events(project_dir, [
            {"type": "metric_update", "ts": (start_dt + timedelta(seconds=30)).isoformat(),
             "metrics": {"train_loss": 0.8}, "epoch": 1},
            {"type": "metric_update", "ts": (start_dt + timedelta(seconds=60)).isoformat(),
             "metrics": {"train_loss": 0.6}, "epoch": 2},
            {"type": "metric_update", "ts": (start_dt + timedelta(seconds=90)).isoformat(),
             "metrics": {"train_loss": 0.4}, "epoch": 3},
        ])

        conclude_run(state=state, project="test-proj", run_id=r["run_id"],
                     results={"train_loss": 0.4}, reasoning="done")

        runs = _read_runs(project_dir)
        completion = [x for x in runs if x["id"] == r["run_id"] and x.get("status") != "running"][-1]
        series = completion.get("metrics_series")
        assert isinstance(series, list), f"expected metrics_series list; got {series!r}"
        assert len(series) == 3
        # Preserve order and the essential fields each consumer reads
        values = [e["metrics"]["train_loss"] for e in series]
        assert values == [0.8, 0.6, 0.4]
        epochs = [e.get("epoch") for e in series]
        assert epochs == [1, 2, 3]

    def test_excludes_events_outside_the_run_window(self, project_dir):
        """A previous run's events in events.jsonl must not leak into
        this run's metrics_series. Time-window match is how we filter."""
        from distillate.experiment_tools.run_tools import start_run, conclude_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        # Prior run's events (before this run's start)
        early = datetime.now(timezone.utc) - timedelta(hours=1)
        self._write_events(project_dir, [
            {"type": "metric_update", "ts": early.isoformat(),
             "metrics": {"train_loss": 9.9}, "epoch": 99},
        ])

        r = start_run(state=state, project="test-proj", description="r2")
        start_dt = datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
        self._write_events(project_dir, [
            {"type": "metric_update", "ts": (start_dt + timedelta(seconds=30)).isoformat(),
             "metrics": {"train_loss": 0.5}},
            {"type": "metric_update", "ts": (start_dt + timedelta(seconds=60)).isoformat(),
             "metrics": {"train_loss": 0.3}},
        ])

        conclude_run(state=state, project="test-proj", run_id=r["run_id"],
                     results={"train_loss": 0.3}, reasoning="ok")

        runs = _read_runs(project_dir)
        completion = [x for x in runs if x["id"] == r["run_id"] and x.get("status") != "running"][-1]
        values = [e["metrics"]["train_loss"] for e in completion["metrics_series"]]
        assert values == [0.5, 0.3], (
            f"prior-run events must be filtered out by timestamp; got {values}"
        )

    def test_omits_metrics_series_when_no_events(self, project_dir):
        """A run with no emitted per-epoch events (agent forgot to print
        metrics in a parseable format) must not crash or emit an empty
        series -- just omit the field entirely so consumers treat it as
        "no curve available"."""
        from distillate.experiment_tools.run_tools import start_run, conclude_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        r = start_run(state=state, project="test-proj", description="r3")
        conclude_run(state=state, project="test-proj", run_id=r["run_id"],
                     results={"train_loss": 0.5}, reasoning="no hook events")

        runs = _read_runs(project_dir)
        completion = [x for x in runs if x["id"] == r["run_id"] and x.get("status") != "running"][-1]
        assert "metrics_series" not in completion or completion["metrics_series"] == []

    def test_preserves_event_metrics_dict(self, project_dir):
        """Each series entry must carry the full per-epoch metrics dict,
        not just one value — downstream consumers pick their own metric
        (train_loss vs val_loss vs accuracy) via the same priority list."""
        from distillate.experiment_tools.run_tools import start_run, conclude_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        r = start_run(state=state, project="test-proj", description="r4")
        start_dt = datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
        self._write_events(project_dir, [
            {"type": "metric_update",
             "ts": (start_dt + timedelta(seconds=30)).isoformat(),
             "metrics": {"train_loss": 0.5, "val_loss": 0.6, "accuracy": 0.8},
             "epoch": 1},
            {"type": "metric_update",
             "ts": (start_dt + timedelta(seconds=60)).isoformat(),
             "metrics": {"train_loss": 0.3, "val_loss": 0.4, "accuracy": 0.9},
             "epoch": 2},
        ])

        conclude_run(state=state, project="test-proj", run_id=r["run_id"],
                     results={"accuracy": 0.9}, reasoning="done")

        runs = _read_runs(project_dir)
        completion = [x for x in runs if x["id"] == r["run_id"] and x.get("status") != "running"][-1]
        series = completion["metrics_series"]
        assert series[0]["metrics"] == {"train_loss": 0.5, "val_loss": 0.6, "accuracy": 0.8}
        assert series[1]["metrics"] == {"train_loss": 0.3, "val_loss": 0.4, "accuracy": 0.9}


# ===========================================================================
# Orphan state pruning — stale state.runs entries that have no matching file.
# ===========================================================================


class TestPruneOrphanStateRuns:
    """``state.runs`` is a cache of what ``runs.jsonl`` said at last scan.
    When the file is rewritten (e.g. a session crash + restart reseeds
    the tail) or a pseudo-id from a prior parser version lingers,
    ``state.runs`` ends up with entries whose ``name`` doesn't appear
    in the file at all. Those phantoms:

    - inflate ``displayRuns.length`` (which the renderer used for the
      "Run N:" label until Fix 1 made it prefer the canonical backend
      counter) and
    - mask real phase state (a phantom ``running`` survives after the
      actual run has crashed).

    ``prune_orphan_state_runs`` is the pure helper the list route calls
    before serving, so stale entries never reach the UI.
    """

    def test_drops_entry_whose_name_is_not_in_runs_jsonl(self):
        from distillate.experiments import prune_orphan_state_runs
        state_runs = {
            "sr-alive":   {"name": "xp-real01"},
            "sr-phantom": {"name": "xp-gone99"},
        }
        pruned = prune_orphan_state_runs(state_runs, jsonl_ids={"xp-real01"})
        assert set(pruned.keys()) == {"sr-alive"}

    def test_keeps_entries_whose_name_is_in_runs_jsonl(self):
        from distillate.experiments import prune_orphan_state_runs
        state_runs = {
            "sr-a": {"name": "xp-one"},
            "sr-b": {"name": "xp-two"},
        }
        pruned = prune_orphan_state_runs(state_runs, jsonl_ids={"xp-one", "xp-two"})
        assert pruned == state_runs

    def test_empty_jsonl_drops_everything(self):
        from distillate.experiments import prune_orphan_state_runs
        state_runs = {"sr-a": {"name": "xp-one"}}
        assert prune_orphan_state_runs(state_runs, jsonl_ids=set()) == {}

    def test_empty_state_returns_empty(self):
        from distillate.experiments import prune_orphan_state_runs
        assert prune_orphan_state_runs({}, jsonl_ids={"xp-x"}) == {}

    def test_entry_without_name_is_dropped(self):
        """A state entry with no ``name`` can't possibly match anything in
        the file. Treat it as junk and drop it."""
        from distillate.experiments import prune_orphan_state_runs
        pruned = prune_orphan_state_runs(
            {"sr-junk": {}}, jsonl_ids={"xp-one"},
        )
        assert pruned == {}

    def test_preserves_the_entry_dict_value(self):
        """Pruning doesn't rewrite the entries it keeps -- consumers
        depend on the full shape (decision, results, etc.)."""
        from distillate.experiments import prune_orphan_state_runs
        keep = {"name": "xp-keep", "status": "best", "results": {"loss": 0.1}}
        pruned = prune_orphan_state_runs(
            {"sr-keep": keep, "sr-drop": {"name": "xp-gone"}},
            jsonl_ids={"xp-keep"},
        )
        assert pruned["sr-keep"] is keep


# ===========================================================================
# Single-active-session launch guard.
# ===========================================================================


class TestLauncherGuardsSingleActiveSession:
    """v2 rule: "1 Agent, N Runs sequentially." If a project already has
    a running session, don't spawn a second one — that's the bug that
    double-stacks MCP servers and produces phantom runs.
    """

    def test_refuses_to_launch_when_active_session_exists(self, tmp_path, monkeypatch):
        from distillate.state import State
        from distillate.experiment_tools.session_tools import launch_experiment_tool

        # Simulate a project with an active session already registered
        state = State()
        state.add_experiment("p1", "TestProj", str(tmp_path))
        state.add_session("p1", "sess-1", {
            "tmux_session": "distillate-p1-session_001",
            "status": "running",
            "started_at": "2026-04-15T12:00:00+00:00",
        })

        # Stub the actual launcher so a failed guard is the only way the
        # test can distinguish "refused" from "launched".
        launched = {"count": 0}
        def fake_launch_experiment(*_args, **_kwargs):
            launched["count"] += 1
            return {
                "session_id": "sess-2",
                "tmux_session": "distillate-p1-session_002",
            }
        monkeypatch.setattr(
            "distillate.launcher.launch_experiment", fake_launch_experiment,
        )

        result = launch_experiment_tool(state=state, project="TestProj")

        assert result.get("success") is not True, (
            f"Launch must refuse when an active session exists; got: {result}"
        )
        assert launched["count"] == 0, (
            "Guard must short-circuit BEFORE calling launch_experiment"
        )
        # Error message should be recognizable -- the user / agent needs to
        # know why the launch was refused.
        err = (result.get("error") or result.get("message") or "").lower()
        assert "active" in err or "running" in err or "already" in err

    def test_allows_launch_when_no_active_session(self, tmp_path, monkeypatch):
        """Negative: a clean project launches normally."""
        from distillate.state import State
        from distillate.experiment_tools.session_tools import launch_experiment_tool

        state = State()
        state.add_experiment("p1", "TestProj", str(tmp_path))

        def fake_launch(*_args, **_kwargs):
            return {
                "session_id": "sess-1",
                "tmux_session": "distillate-p1-session_001",
                "status": "running",
                "started_at": "2026-04-15T12:00:00+00:00",
            }
        monkeypatch.setattr(
            "distillate.launcher.launch_experiment", fake_launch,
        )
        result = launch_experiment_tool(state=state, project="TestProj")
        assert result.get("success") is True

    def test_allows_launch_after_prior_session_stopped(self, tmp_path, monkeypatch):
        """A ``completed`` session doesn't block — only ``running`` does."""
        from distillate.state import State
        from distillate.experiment_tools.session_tools import launch_experiment_tool

        state = State()
        state.add_experiment("p1", "TestProj", str(tmp_path))
        state.add_session("p1", "sess-old", {
            "tmux_session": "distillate-p1-session_001",
            "status": "completed",
            "started_at": "2026-04-15T10:00:00+00:00",
        })

        def fake_launch(*_args, **_kwargs):
            return {
                "session_id": "sess-new",
                "tmux_session": "distillate-p1-session_002",
                "status": "running",
                "started_at": "2026-04-15T12:00:00+00:00",
            }
        monkeypatch.setattr(
            "distillate.launcher.launch_experiment", fake_launch,
        )
        result = launch_experiment_tool(state=state, project="TestProj")
        assert result.get("success") is True
