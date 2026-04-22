# Covers: distillate/launcher.py, distillate/state.py — GitHub repo creation and session state management

import subprocess
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# GitHub repo creation (launcher)
# ---------------------------------------------------------------------------

class TestCreateGithubRepo:
    def test_github_url_first_line_only(self, tmp_path, monkeypatch):
        """create_github_repo extracts only the first line from gh output as URL."""
        from distillate.launcher import create_github_repo

        call_log = []

        def mock_run(cmd, **kwargs):
            call_log.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            # gh repo create prints URL on first line, then push messages
            if "repo" in cmd and "create" in cmd:
                result.stdout = (
                    "https://github.com/user/distillate-xp-test\n"
                    "remote: Enumerating objects: 5, done.\n"
                    "remote: Counting objects: 100% (5/5), done.\n"
                )
            else:
                result.stdout = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr("distillate.launcher.shutil.which", lambda x: "/usr/bin/" + x)

        # Create minimal git repo in tmp_path
        (tmp_path / ".git").mkdir()

        result = create_github_repo(tmp_path, "distillate-xp-test")
        assert result["ok"] is True
        assert result["url"] == "https://github.com/user/distillate-xp-test"
        # Ensure no git push messages leaked into the URL
        assert "remote:" not in result["url"]
        assert "\n" not in result["url"]


# ---------------------------------------------------------------------------
# State integration — session management
# ---------------------------------------------------------------------------

class TestStateSessionMethods:
    def test_add_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Project 1", str(tmp_path))
        state.add_session("p1", "session_001", {"status": "running", "model": "sonnet"})

        proj = state.get_experiment("p1")
        assert "sessions" in proj
        assert "session_001" in proj["sessions"]
        assert proj["sessions"]["session_001"]["status"] == "running"

    def test_update_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Project 1", str(tmp_path))
        state.add_session("p1", "s1", {"status": "running"})
        state.update_session("p1", "s1", status="completed")

        sess = state.get_experiment("p1")["sessions"]["s1"]
        assert sess["status"] == "completed"

    def test_update_nonexistent_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Project 1", str(tmp_path))
        state.update_session("p1", "nonexistent", status="completed")
        # Should not raise

    def test_active_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Project 1", str(tmp_path))
        state.add_session("p1", "s1", {"status": "running"})
        state.add_session("p1", "s2", {"status": "completed"})
        state.add_experiment("p2", "Project 2", str(tmp_path))
        state.add_session("p2", "s3", {"status": "running"})

        active = state.active_sessions()
        assert len(active) == 2
        ids = [(pid, sid) for pid, sid, _ in active]
        assert ("p1", "s1") in ids
        assert ("p2", "s3") in ids

    def test_active_sessions_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Project 1", str(tmp_path))
        assert state.active_sessions() == []
