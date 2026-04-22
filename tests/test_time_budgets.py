# Covers: distillate/launcher.py
"""Tests for time-as-first-class-citizen: budget.json, hook warnings, MCP time info."""

import json
from datetime import datetime, timedelta, timezone



# ---------------------------------------------------------------------------
# Phase 1: write_budget_json
# ---------------------------------------------------------------------------

class TestWriteBudgetJson:
    def test_creates_budget_file(self, tmp_path):
        from distillate.launcher import write_budget_json
        proj = {"duration_minutes": 10, "session_budget_seconds": 3600}
        path = write_budget_json(tmp_path, proj)

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["run_budget_seconds"] == 600
        assert data["session_budget_seconds"] == 3600
        assert "session_started_at" in data

    def test_defaults_to_5_min(self, tmp_path):
        from distillate.launcher import write_budget_json
        path = write_budget_json(tmp_path, {})

        data = json.loads(path.read_text())
        assert data["run_budget_seconds"] == 300

    def test_no_session_budget(self, tmp_path):
        from distillate.launcher import write_budget_json
        path = write_budget_json(tmp_path, {"duration_minutes": 7})

        data = json.loads(path.read_text())
        assert data["run_budget_seconds"] == 420
        assert data["session_budget_seconds"] is None

    def test_custom_session_started_at(self, tmp_path):
        from distillate.launcher import write_budget_json
        ts = "2026-03-30T10:00:00+00:00"
        path = write_budget_json(tmp_path, {}, session_started_at=ts)

        data = json.loads(path.read_text())
        assert data["session_started_at"] == ts

    def test_creates_distillate_dir(self, tmp_path):
        from distillate.launcher import write_budget_json
        subdir = tmp_path / "new_project"
        subdir.mkdir()
        write_budget_json(subdir, {})
        assert (subdir / ".distillate" / "budget.json").exists()


# ---------------------------------------------------------------------------
# Phase 2: hook budget reading and warnings
# ---------------------------------------------------------------------------

class TestReadBudget:
    def test_reads_from_file(self, tmp_path):
        from distillate.hooks.post_bash import _budget_cache, _read_budget
        _budget_cache.clear()

        budget_dir = tmp_path / ".distillate"
        budget_dir.mkdir()
        (budget_dir / "budget.json").write_text(json.dumps({
            "run_budget_seconds": 900,
            "session_budget_seconds": 7200,
            "session_started_at": "2026-03-30T10:00:00+00:00",
        }))

        result = _read_budget(tmp_path)
        assert result["run_budget_seconds"] == 900
        assert result["session_budget_seconds"] == 7200

    def test_falls_back_to_env(self, tmp_path, monkeypatch):
        from distillate.hooks.post_bash import _budget_cache, _read_budget
        _budget_cache.clear()

        monkeypatch.setenv("DISTILLATE_RUN_BUDGET_SECONDS", "1200")
        monkeypatch.setenv("DISTILLATE_SESSION_BUDGET_SECONDS", "7200")
        result = _read_budget(tmp_path)
        assert result["run_budget_seconds"] == 1200
        assert result["session_budget_seconds"] == 7200

    def test_falls_back_to_default(self, tmp_path):
        from distillate.hooks.post_bash import _budget_cache, _read_budget
        _budget_cache.clear()

        result = _read_budget(tmp_path)
        assert result["run_budget_seconds"] == 300
        assert result["session_budget_seconds"] is None

    def test_rereads_on_mtime_change(self, tmp_path):
        from distillate.hooks.post_bash import _budget_cache, _read_budget
        _budget_cache.clear()

        budget_dir = tmp_path / ".distillate"
        budget_dir.mkdir()
        bf = budget_dir / "budget.json"
        bf.write_text(json.dumps({
            "run_budget_seconds": 600,
            "session_budget_seconds": None,
            "session_started_at": None,
        }))

        r1 = _read_budget(tmp_path)
        assert r1["run_budget_seconds"] == 600

        # Update with new value — mtime changes, cache should invalidate
        import time
        time.sleep(0.05)
        bf.write_text(json.dumps({
            "run_budget_seconds": 900,
            "session_budget_seconds": None,
            "session_started_at": None,
        }))

        r2 = _read_budget(tmp_path)
        assert r2["run_budget_seconds"] == 900


class TestRunWarnings:
    def _setup_run(self, tmp_path, minutes_ago, run_budget=600):
        """Set up a running run started `minutes_ago` with given budget."""
        from distillate.hooks.post_bash import _budget_cache
        _budget_cache.clear()

        d = tmp_path / ".distillate"
        d.mkdir(exist_ok=True)
        started = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        (d / "runs.jsonl").write_text(json.dumps({
            "id": "run_001", "status": "running",
            "timestamp": started, "started_at": started,
        }) + "\n")
        (d / "budget.json").write_text(json.dumps({
            "run_budget_seconds": run_budget,
            "session_budget_seconds": None,
            "session_started_at": None,
        }))

    def test_no_warning_under_80pct(self, tmp_path, capsys):
        from distillate.hooks.post_bash import _check_run_elapsed
        self._setup_run(tmp_path, minutes_ago=3, run_budget=600)  # 50%
        _check_run_elapsed(tmp_path)
        assert capsys.readouterr().out == ""

    def test_warning_at_80pct(self, tmp_path, capsys):
        from distillate.hooks.post_bash import _check_run_elapsed
        self._setup_run(tmp_path, minutes_ago=9, run_budget=600)  # 90%
        _check_run_elapsed(tmp_path)
        out = capsys.readouterr().out
        assert "TIME WARNING" in out
        assert "deadline" in out

    def test_exceeded_warning(self, tmp_path, capsys):
        from distillate.hooks.post_bash import _check_run_elapsed
        self._setup_run(tmp_path, minutes_ago=12, run_budget=600)  # 120%
        _check_run_elapsed(tmp_path)
        out = capsys.readouterr().out
        assert "BUDGET EXCEEDED" in out
        assert "deadline was" in out

    def test_respects_custom_budget(self, tmp_path, capsys):
        from distillate.hooks.post_bash import _check_run_elapsed
        # 9 min into a 60-min budget = 15%, should NOT warn
        self._setup_run(tmp_path, minutes_ago=9, run_budget=3600)
        _check_run_elapsed(tmp_path)
        assert capsys.readouterr().out == ""

    def test_no_warning_for_completed_run(self, tmp_path, capsys):
        from distillate.hooks.post_bash import _budget_cache, _check_run_elapsed
        _budget_cache.clear()
        d = tmp_path / ".distillate"
        d.mkdir(exist_ok=True)
        (d / "runs.jsonl").write_text(json.dumps({
            "id": "run_001", "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }) + "\n")
        (d / "budget.json").write_text(json.dumps({
            "run_budget_seconds": 300,
            "session_budget_seconds": None,
            "session_started_at": None,
        }))
        _check_run_elapsed(tmp_path)
        assert capsys.readouterr().out == ""


class TestSessionWarnings:
    def _setup_session(self, tmp_path, minutes_ago, session_budget=3600):
        from distillate.hooks.post_bash import _budget_cache
        _budget_cache.clear()

        d = tmp_path / ".distillate"
        d.mkdir(exist_ok=True)
        started = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        (d / "budget.json").write_text(json.dumps({
            "run_budget_seconds": 300,
            "session_budget_seconds": session_budget,
            "session_started_at": started,
        }))

    def test_no_warning_under_90pct(self, tmp_path, capsys):
        from distillate.hooks.post_bash import _check_session_elapsed
        self._setup_session(tmp_path, minutes_ago=30, session_budget=3600)  # 50%
        _check_session_elapsed(tmp_path)
        assert capsys.readouterr().out == ""

    def test_warning_at_90pct(self, tmp_path, capsys):
        from distillate.hooks.post_bash import _check_session_elapsed
        self._setup_session(tmp_path, minutes_ago=55, session_budget=3600)  # 92%
        _check_session_elapsed(tmp_path)
        out = capsys.readouterr().out
        assert "SESSION WARNING" in out
        assert "deadline" in out

    def test_expired_warning(self, tmp_path, capsys):
        from distillate.hooks.post_bash import _check_session_elapsed
        self._setup_session(tmp_path, minutes_ago=65, session_budget=3600)
        _check_session_elapsed(tmp_path)
        out = capsys.readouterr().out
        assert "SESSION BUDGET EXPIRED" in out
        assert "deadline was" in out

    def test_no_warning_without_session_budget(self, tmp_path, capsys):
        from distillate.hooks.post_bash import _budget_cache, _check_session_elapsed
        _budget_cache.clear()
        d = tmp_path / ".distillate"
        d.mkdir(exist_ok=True)
        (d / "budget.json").write_text(json.dumps({
            "run_budget_seconds": 300,
            "session_budget_seconds": None,
            "session_started_at": None,
        }))
        _check_session_elapsed(tmp_path)
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Phase 3: _compute_time_info
# ---------------------------------------------------------------------------

class TestComputeTimeInfo:
    def test_basic_time_info(self, tmp_path):
        from distillate.experiment_tools import _compute_time_info
        from distillate.launcher import write_budget_json

        proj = {
            "path": str(tmp_path),
            "duration_minutes": 10,
            "session_budget_seconds": 3600,
            "runs": {
                "run_001": {"status": "best", "duration_seconds": 480},
                "run_002": {"status": "completed", "duration_seconds": 520},
            },
        }
        started = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        write_budget_json(tmp_path, proj, session_started_at=started)

        info = _compute_time_info(proj)
        assert info["run_budget_seconds"] == 600
        assert info["session_budget_seconds"] == 3600
        assert 1190 <= info["session_elapsed_seconds"] <= 1210  # ~20 min
        assert info["session_remaining_seconds"] > 0
        assert info["total_training_seconds"] == 1000

    def test_running_run_elapsed(self, tmp_path):
        from distillate.experiment_tools import _compute_time_info
        from distillate.launcher import write_budget_json

        started = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
        proj = {
            "path": str(tmp_path),
            "duration_minutes": 10,
            "runs": {
                "run_001": {"status": "running", "started_at": started},
            },
        }
        write_budget_json(tmp_path, proj)

        info = _compute_time_info(proj)
        assert 170 <= info["run_elapsed_seconds"] <= 190  # ~3 min
        assert info["run_remaining_seconds"] > 0

    def test_empty_project(self, tmp_path):
        from distillate.experiment_tools import _compute_time_info
        info = _compute_time_info({"path": str(tmp_path), "runs": {}})
        assert info["run_budget_seconds"] == 300
        assert "session_budget_seconds" not in info
        assert "total_training_seconds" not in info

    def test_no_path(self):
        from distillate.experiment_tools import _compute_time_info
        assert _compute_time_info({}) == {}


# ---------------------------------------------------------------------------
# Phase 5: conclude_run overrun
# ---------------------------------------------------------------------------

class TestConcludeRunOverrun:
    def test_overrun_annotation(self, tmp_path, monkeypatch):
        """Verify budget_overrun_seconds is written to runs.jsonl."""
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()

        proj_dir = tmp_path / "xp"
        proj_dir.mkdir()
        d = proj_dir / ".distillate"
        d.mkdir()

        # Register project with 5-min budget
        state.add_experiment("test-xp", "Test XP", str(proj_dir))
        state.update_experiment("test-xp", duration_minutes=5)
        state.save()

        # Write a running entry started 8 min ago
        started = (datetime.now(timezone.utc) - timedelta(minutes=8)).isoformat()
        runs_file = d / "runs.jsonl"
        runs_file.write_text(json.dumps({
            "id": "run_001", "status": "running",
            "timestamp": started, "started_at": started,
        }) + "\n")

        from distillate.experiment_tools import conclude_run
        result = conclude_run(
            state=state, project="test-xp", run_id="run_001",
            results={"accuracy": 0.85}, reasoning="test run",
        )

        assert result["success"]
        # Read the concluded entry
        lines = runs_file.read_text().strip().splitlines()
        concluded = json.loads(lines[-1])
        assert concluded["budget_overrun_seconds"] > 0
        assert "over budget" in result["message"]

    def test_no_overrun_within_budget(self, tmp_path, monkeypatch):
        """Runs within budget should not have overrun annotation."""
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()

        proj_dir = tmp_path / "xp2"
        proj_dir.mkdir()
        d = proj_dir / ".distillate"
        d.mkdir()

        state.add_experiment("test-xp2", "Test XP2", str(proj_dir))
        state.update_experiment("test-xp2", duration_minutes=10)
        state.save()

        # Started 3 min ago — well within 10-min budget
        started = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
        runs_file = d / "runs.jsonl"
        runs_file.write_text(json.dumps({
            "id": "run_001", "status": "running",
            "timestamp": started, "started_at": started,
        }) + "\n")

        from distillate.experiment_tools import conclude_run
        result = conclude_run(
            state=state, project="test-xp2", run_id="run_001",
            results={"accuracy": 0.9}, reasoning="fast run",
        )

        assert result["success"]
        lines = runs_file.read_text().strip().splitlines()
        concluded = json.loads(lines[-1])
        assert "budget_overrun_seconds" not in concluded
        assert "over budget" not in result["message"]
