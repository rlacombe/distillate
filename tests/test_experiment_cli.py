# Covers: distillate/commands.py (_delete_experiment, _edit_prompt, _chart_export)
#         distillate/hooks/post_bash.py (_check_run_elapsed)
"""Tests for CLI commands and the time-enforcement hook.

Covers bugs found during overnight run audit (2026-03-14):
  BUG 7: time enforcement hook
"""

import importlib.util
import io
import json
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper: write runs.jsonl
# ---------------------------------------------------------------------------

def _write_runs_jsonl(directory: Path, entries: list[dict]):
    """Write a list of run entries to .distillate/runs.jsonl."""
    dist_dir = directory / ".distillate"
    dist_dir.mkdir(parents=True, exist_ok=True)
    with open(dist_dir / "runs.jsonl", "w", encoding="utf-8") as f:
        for entry in entries:
            entry.setdefault("$schema", "distillate/run/v1")
            f.write(json.dumps(entry) + "\n")


def _make_entry(run_id, status, ts, description="", results=None, **extra):
    """Build a runs.jsonl entry."""
    e = {
        "$schema": "distillate/run/v1",
        "id": run_id,
        "status": status,
        "timestamp": ts,
        "description": description,
    }
    if results:
        e["results"] = results
    e.update(extra)
    return e


# ---------------------------------------------------------------------------
# BUG 7: Time enforcement hook
# ---------------------------------------------------------------------------

class TestTimeEnforcementHook:
    """PostToolUse hook should detect runs exceeding time budget."""

    def test_warns_at_80pct_of_budget(self, tmp_path):
        """Hook should print warning when run reaches 80% of its budget."""
        # 4.5 min into default 5-min budget = 90% → should warn
        _write_runs_jsonl(tmp_path, [
            _make_entry(
                "run_001", "running",
                (datetime.now(timezone.utc) - timedelta(minutes=4, seconds=30)).isoformat(),
                "Long running experiment",
            ),
        ])
        from distillate.hooks.post_bash import _budget_cache, _check_run_elapsed
        _budget_cache.clear()
        f = io.StringIO()
        with redirect_stdout(f):
            _check_run_elapsed(tmp_path)
        output = f.getvalue()
        assert "TIME WARNING" in output
        assert "run_001" in output

    def test_no_warning_under_threshold(self, tmp_path):
        """No warning when run is under 80% of budget."""
        # 2 min into default 5-min budget = 40% → no warning
        _write_runs_jsonl(tmp_path, [
            _make_entry(
                "run_001", "running",
                (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
                "Short run",
            ),
        ])
        from distillate.hooks.post_bash import _budget_cache, _check_run_elapsed
        _budget_cache.clear()
        f = io.StringIO()
        with redirect_stdout(f):
            _check_run_elapsed(tmp_path)
        output = f.getvalue()
        assert output == ""

    def test_exceeded_warning(self, tmp_path):
        """Hook should print BUDGET EXCEEDED when run is over budget."""
        # 8 min into default 5-min budget → exceeded
        _write_runs_jsonl(tmp_path, [
            _make_entry(
                "run_001", "running",
                (datetime.now(timezone.utc) - timedelta(minutes=8)).isoformat(),
                "Very long experiment",
            ),
        ])
        from distillate.hooks.post_bash import _budget_cache, _check_run_elapsed
        _budget_cache.clear()
        f = io.StringIO()
        with redirect_stdout(f):
            _check_run_elapsed(tmp_path)
        output = f.getvalue()
        assert "BUDGET EXCEEDED" in output
        assert "run_001" in output

    def test_no_warning_for_completed_run(self, tmp_path):
        """No warning when the last entry is a completed run."""
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z"),
        ])
        from distillate.hooks.post_bash import _check_run_elapsed
        f = io.StringIO()
        with redirect_stdout(f):
            _check_run_elapsed(tmp_path)
        output = f.getvalue()
        assert "TIME WARNING" not in output


# ---------------------------------------------------------------------------
# CLI command: --delete-experiment
# ---------------------------------------------------------------------------

class TestDeleteExperiment:
    """_delete_experiment removes project from state."""

    def test_deletes_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("proj-1", "My Project", str(tmp_path))
        state.save()

        monkeypatch.setattr("sys.argv", ["distillate", "--delete-experiment", "proj-1", "--yes"])
        from distillate.commands import _delete_experiment
        _delete_experiment(["proj-1"])

        state2 = State()
        assert state2.get_experiment("proj-1") is None

    def test_refuses_with_running_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("proj-1", "My Project", str(tmp_path))
        proj = state.get_experiment("proj-1")
        proj["sessions"] = {"s1": {"status": "running", "tmux_session": "dist-proj-1"}}
        state.save()

        monkeypatch.setattr("sys.argv", ["distillate", "--delete-experiment", "proj-1", "--yes"])
        monkeypatch.setattr("distillate.commands._tmux_session_exists", lambda n: True,
                            raising=False)
        import distillate.commands as cmd_mod
        original_fn = cmd_mod._delete_experiment

        captured = io.StringIO()

        # Re-patch _tmux_session_exists inside the launcher module
        monkeypatch.setattr("distillate.launcher._tmux_session_exists", lambda n: True)
        with redirect_stdout(captured):
            original_fn(["proj-1"])

        assert "still running" in captured.getvalue().lower()
        # Project should still exist
        state2 = State()
        assert state2.get_experiment("proj-1") is not None


# ---------------------------------------------------------------------------
# CLI command: --edit-prompt
# ---------------------------------------------------------------------------

class TestEditPrompt:
    """_edit_prompt opens editor and detects metric."""

    def test_detects_metric_after_edit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        state.add_experiment("proj-1", "My Project", str(proj_dir))
        state.save()

        # Write PROMPT.md with a metric
        prompt = proj_dir / "PROMPT.md"
        prompt.write_text("# Experiment\nPrimary metric: test_accuracy (maximize)\n")

        # Mock editor to be a no-op (just return)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: None)
        monkeypatch.setattr("sys.argv", ["distillate", "--edit-prompt", "proj-1"])

        from distillate.commands import _edit_prompt
        _edit_prompt(["proj-1"])

        state2 = State()
        proj = state2.get_experiment("proj-1")
        assert proj["key_metric_name"] == "test_accuracy"

    def test_writes_prompt_updated_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        state.add_experiment("proj-1", "My Project", str(proj_dir))
        state.save()

        prompt = proj_dir / "PROMPT.md"
        prompt.write_text("# Experiment\nJust a description.\n")

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: None)
        monkeypatch.setattr("sys.argv", ["distillate", "--edit-prompt", "proj-1"])

        from distillate.commands import _edit_prompt
        _edit_prompt(["proj-1"])

        flag = proj_dir / ".distillate" / "prompt_updated"
        assert flag.exists()


# ---------------------------------------------------------------------------
# CLI command: --chart
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not importlib.util.find_spec("matplotlib"),
    reason="matplotlib not installed (optional dependency)",
)
class TestChartExport:
    """_chart_export generates a PNG file."""

    def test_writes_chart_png(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        state.add_experiment("proj-1", "My Project", str(proj_dir))

        from distillate.experiments import _create_run
        for i in range(3):
            run = _create_run(
                prefix="sr", name=f"run_{i:03d}",
                results={"accuracy": 0.7 + i * 0.05},
                started_at=f"2026-03-14T{i:02d}:00:00Z",
                decision="best",
            )
            state.add_run("proj-1", run["id"], run)
        state.save()

        monkeypatch.setattr("sys.argv", ["distillate", "--chart", "proj-1", "--metric", "accuracy"])
        monkeypatch.setattr("webbrowser.open", lambda url: None)

        from distillate.commands import _chart_export
        _chart_export(["proj-1"])

        chart_path = proj_dir / ".distillate" / "chart.png"
        assert chart_path.exists()
        assert chart_path.stat().st_size > 1000  # PNG should be non-trivial

    def test_no_runs_prints_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        state.add_experiment("proj-1", "My Project", str(proj_dir))
        state.save()

        monkeypatch.setattr("sys.argv", ["distillate", "--chart", "proj-1", "--metric", "accuracy"])

        captured = io.StringIO()

        from distillate.commands import _chart_export
        with redirect_stdout(captured):
            _chart_export(["proj-1"])

        assert "no runs" in captured.getvalue().lower()
