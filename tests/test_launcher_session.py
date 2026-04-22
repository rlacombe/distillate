# Covers: distillate/launcher.py

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestSessionStatus:
    def test_running(self, monkeypatch):
        from distillate.launcher import session_status

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert session_status("test") == "running"

    def test_completed(self, monkeypatch):
        from distillate.launcher import session_status

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert session_status("test") == "completed"

    def test_ssh_variant(self, monkeypatch):
        from distillate.launcher import session_status

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        session_status("test", host="myhost")
        assert calls[0][0] == "ssh"
        assert calls[0][1] == "myhost"


class TestStopSession:
    def test_sends_ctrl_c(self, monkeypatch):
        from distillate.launcher import stop_session

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert stop_session("test-session") is True
        assert "send-keys" in calls[0]
        assert "C-c" in calls[0]

    def test_returns_false_on_failure(self, monkeypatch):
        from distillate.launcher import stop_session

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert stop_session("dead-session") is False


class TestListSessions:
    def test_filters_distillate_prefix(self, monkeypatch):
        from distillate.launcher import list_sessions

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = (
                "distillate-exp1-001 1234567\n"
                "other-session 9999999\n"
                "distillate-exp2-003 5555555\n"
            )
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        sessions = list_sessions()
        assert len(sessions) == 2
        assert sessions[0]["name"] == "distillate-exp1-001"
        assert sessions[1]["name"] == "distillate-exp2-003"

    def test_no_sessions(self, monkeypatch):
        from distillate.launcher import list_sessions

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        assert list_sessions() == []


class TestAttachSession:
    def test_macos(self, monkeypatch):
        from distillate.launcher import attach_session

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr("distillate.launcher.platform.system", lambda: "Darwin")

        attach_session("test-session")
        assert calls[0][0] == "osascript"

    def test_macos_ssh(self, monkeypatch):
        from distillate.launcher import attach_session

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr("distillate.launcher.platform.system", lambda: "Darwin")

        attach_session("test-session", host="user@gpu")
        script = calls[0][2]
        assert "ssh -t user@gpu" in script


class TestRefreshSessionStatuses:
    def test_updates_completed(self, monkeypatch):
        from distillate.launcher import refresh_session_statuses

        def mock_session_status(name, host=None):
            return "completed"

        monkeypatch.setattr("distillate.launcher.session_status", mock_session_status)

        state = MagicMock()
        state.experiments = {
            "proj1": {
                "sessions": {
                    "s1": {"status": "running", "tmux_session": "distillate-proj1-001"},
                    "s2": {"status": "completed", "tmux_session": "distillate-proj1-002"},
                },
            },
        }

        changed = refresh_session_statuses(state)
        assert changed == 1
        assert state.experiments["proj1"]["sessions"]["s1"]["status"] == "completed"
        assert "completed_at" in state.experiments["proj1"]["sessions"]["s1"]

    def test_no_change_when_still_running(self, monkeypatch):
        from distillate.launcher import refresh_session_statuses

        def mock_session_status(name, host=None):
            return "running"

        monkeypatch.setattr("distillate.launcher.session_status", mock_session_status)

        state = MagicMock()
        state.experiments = {
            "proj1": {
                "sessions": {
                    "s1": {"status": "running", "tmux_session": "distillate-proj1-001"},
                },
            },
        }

        changed = refresh_session_statuses(state)
        assert changed == 0
        assert state.experiments["proj1"]["sessions"]["s1"]["status"] == "running"


class TestCodingSessionRecovery:
    """Tests for auto-recovery of coding sessions when tmux dies."""

    @pytest.fixture(autouse=True)
    def _setup_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        self.tmp_path = tmp_path
        # Create a repo dir for the session
        self.repo = tmp_path / "my-repo"
        self.repo.mkdir()

    def _make_state_with_session(self, *, claude_session_id="", recovery_failed=False):
        from distillate.state import State
        state = State()
        state.add_workspace("ws1", "TestWorkspace")
        session = state.add_coding_session("ws1", "coding_001", str(self.repo), "ws-001")
        session["claude_session_id"] = claude_session_id
        if recovery_failed:
            session["recovery_failed"] = True
        state.save()
        return state

    def test_recovery_succeeds_with_session_id(self, monkeypatch):
        """When tmux dies but claude_session_id is stored, session auto-recovers."""
        from distillate.experiment_tools.workspace_tools import list_workspaces_tool

        state = self._make_state_with_session(claude_session_id="abc-123-uuid")

        # Mock _batch_pane_titles: first call = session absent (triggers recovery),
        # second call (after recovery) = session present with braille working spinner.
        _batch_call = [0]
        def mock_batch_titles():
            _batch_call[0] += 1
            if _batch_call[0] == 1:
                return {}  # Session not in titles → dead, triggers recovery
            return {"ws-001": "\u2840 working task"}  # Braille = "working"

        monkeypatch.setattr(
            "distillate.experiment_tools.workspace_tools._batch_pane_titles",
            mock_batch_titles,
        )

        recover_calls = []
        def mock_recover(tmux_name, session):
            recover_calls.append((tmux_name, session.get("claude_session_id")))
            return True

        monkeypatch.setattr(
            "distillate.experiment_tools.workspace_tools._recover_lost_session",
            mock_recover,
        )

        result = list_workspaces_tool(state=state)
        ws = result["workspaces"][0]

        # Recovery was attempted
        assert len(recover_calls) == 1
        assert recover_calls[0] == ("ws-001", "abc-123-uuid")

        # Session shows as running with working status
        assert ws["active_sessions"] == 1
        running = ws["running_sessions"]
        assert len(running) == 1
        assert running[0]["agent_status"] == "working"

    def test_recovery_fails_without_session_id(self, monkeypatch):
        """Without claude_session_id, session is auto-archived when tmux dies."""
        from distillate.experiment_tools.workspace_tools import list_workspaces_tool

        state = self._make_state_with_session(claude_session_id="")

        monkeypatch.setattr(
            "distillate.launcher._tmux_session_exists",
            lambda name: False,
        )

        result = list_workspaces_tool(state=state)
        ws = result["workspaces"][0]
        # Session should be auto-archived (ended). It briefly appears in
        # running_sessions with agent_status "completed" due to the 5-minute
        # recently-completed window, then disappears.
        sess = ws.get("coding_sessions", {}) if hasattr(ws, "get") else {}
        running = ws["running_sessions"]
        if running:
            assert running[0]["agent_status"] == "completed"
        # Underlying state is archived (completed) by save_session_summary_tool, not stuck as lost
        raw = state.workspaces["ws1"]["coding_sessions"]["coding_001"]
        assert raw["status"] == "completed"
        assert raw.get("completed_at") is not None

    def test_recovery_failure_auto_archives(self, monkeypatch):
        """When recovery fails, session is auto-archived instead of stuck as lost."""
        from distillate.experiment_tools.workspace_tools import agent_status_tool

        state = self._make_state_with_session(
            claude_session_id="abc-123-uuid",
        )

        monkeypatch.setattr(
            "distillate.launcher._tmux_session_exists",
            lambda name: False,
        )

        recover_calls = []
        monkeypatch.setattr(
            "distillate.experiment_tools.workspace_tools._recover_lost_session",
            lambda n, s: (recover_calls.append(1), False)[1],
        )

        result = agent_status_tool(state=state)
        assert len(recover_calls) == 1
        # Session should be gone from running sessions (auto-archived)
        assert "ws1/coding_001" not in result["sessions"]

    def test_agent_name_cached_during_normal_operation(self, monkeypatch):
        """agent_name is updated in state when extracted from a live pane title."""
        from distillate.experiment_tools.workspace_tools import list_workspaces_tool

        state = self._make_state_with_session(claude_session_id="")

        # Pane title with braille spinner (working) followed by agent name
        monkeypatch.setattr(
            "distillate.experiment_tools.workspace_tools._batch_pane_titles",
            lambda: {"ws-001": "\u2840 my-session"},
        )

        list_workspaces_tool(state=state)

        # Agent name extracted from title should be cached in state
        ws = state.workspaces["ws1"]
        session = ws["coding_sessions"]["coding_001"]
        assert session.get("agent_name") == "my-session"

    def test_recover_lost_session_spawns_tmux(self, monkeypatch):
        """_recover_lost_session spawns the correct tmux command."""
        from distillate.experiment_tools.workspace_tools import _recover_lost_session

        calls = []
        def mock_run(*args, **kwargs):
            calls.append(args)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr("subprocess.run", mock_run)

        session = {
            "claude_session_id": "abc-123",
            "repo_path": str(self.repo),
        }
        assert _recover_lost_session("ws-001", session) is True

        # First call should be the tmux new-session command
        tmux_cmd = calls[0][0]
        assert "tmux new-session" in tmux_cmd
        assert "claude --resume abc-123" in tmux_cmd
        assert "--permission-mode auto" in tmux_cmd

    def test_recover_fails_with_missing_repo(self):
        """Recovery fails if repo_path doesn't exist."""
        from distillate.experiment_tools.workspace_tools import _recover_lost_session

        session = {
            "claude_session_id": "abc-123",
            "repo_path": "/nonexistent/path",
        }
        assert _recover_lost_session("ws-001", session) is False
