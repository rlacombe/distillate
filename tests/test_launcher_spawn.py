# Covers: distillate/launcher.py — subprocess spawn, session guard, teardown/cleanup

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestSlugify:
    def test_basic(self):
        from distillate.launcher import _slugify
        assert _slugify("Tiny Gene Code") == "tiny-gene-code"

    def test_special_chars(self):
        from distillate.launcher import _slugify
        assert _slugify("my_exp@v2!") == "my-expv2"

    def test_strips_dashes(self):
        from distillate.launcher import _slugify
        assert _slugify("--hello--") == "hello"


class TestSessionName:
    def test_format(self):
        from distillate.launcher import _session_name
        assert _session_name("tiny-gene-code", 1) == "distillate-tiny-gene-code-001"
        assert _session_name("My Project", 42) == "distillate-my-project-042"


class TestNextSessionId:
    def test_first_session(self):
        from distillate.launcher import _next_session_id
        assert _next_session_id({}) == "session_001"

    def test_increments(self):
        from distillate.launcher import _next_session_id
        proj = {"sessions": {"session_001": {}, "session_002": {}}}
        assert _next_session_id(proj) == "session_003"


class TestBuildClaudeCommand:
    def test_default_params(self):
        from distillate.launcher import _build_claude_command
        cmd = _build_claude_command(Path("/project/PROMPT.md"))
        assert "claude" in cmd
        assert "--permission-mode" in cmd

    def test_custom_params(self):
        from distillate.launcher import _build_claude_command
        cmd = _build_claude_command(
            Path("/project/PROMPT.md"),
            model="claude-opus-4-20250514",
        )
        assert "claude" in cmd
        assert "--permission-mode" in cmd


class TestSpawnLocal:
    def test_success(self, monkeypatch):
        from distillate.launcher import _spawn_local

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "12345\n"
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr("time.sleep", lambda _: None)

        _spawn_local("test-session", Path("/tmp"), "echo hello")
        assert len(calls) == 8  # status off (global) + escape-time 0 (global) + window-size latest (global) + new-session + status off (session) + mouse on + send Enter + display-message
        assert "new-session" in calls[3]

    def test_failure_raises(self, monkeypatch):
        from distillate.launcher import _spawn_local

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stderr = "tmux error"
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        with pytest.raises(RuntimeError, match="Failed to create tmux"):
            _spawn_local("bad-session", Path("/tmp"), "echo fail")


class TestSpawnSSH:
    def test_builds_ssh_command(self, monkeypatch):
        from distillate.launcher import _spawn_ssh

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        _spawn_ssh("test-session", "user@host", "/remote/dir", "echo hello")

        assert calls[0][0] == "ssh"
        assert calls[0][1] == "user@host"
        assert "tmux new-session" in calls[0][2]

    def test_failure_raises(self, monkeypatch):
        from distillate.launcher import _spawn_ssh

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stderr = "connection refused"
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        with pytest.raises(RuntimeError, match="Failed to create remote"):
            _spawn_ssh("s", "host", "/dir", "cmd")


class TestLaunchExperiment:
    def test_no_prompt_raises(self, tmp_path, monkeypatch):
        from distillate.launcher import launch_experiment

        # Empty dir, no PROMPT.md
        with pytest.raises(FileNotFoundError, match="No PROMPT.md"):
            launch_experiment(tmp_path)

    def test_successful_launch(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "cfg")
        (tmp_path / "PROMPT.md").write_text("do the thing\n")
        (tmp_path / ".distillate").mkdir()

        # Mock subprocess calls
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "999\n"
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        from distillate.launcher import launch_experiment
        proj = {"name": "test-project", "runs": {"r1": {}, "r2": {}}}
        data = launch_experiment(tmp_path, project=proj)

        assert data["status"] == "running"
        assert data["tmux_session"].startswith("distillate-")
        assert data["model"] == "claude-sonnet-4-5-20250929"
        assert data["runs_at_start"] == 2

    def test_ssh_launch(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "cfg")
        (tmp_path / "PROMPT.md").write_text("prompt\n")
        (tmp_path / ".distillate").mkdir()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        from distillate.launcher import launch_experiment
        data = launch_experiment(tmp_path, host="user@gpu")

        assert data["host"] == "user@gpu"
        # SSH call should be present
        ssh_calls = [c for c in calls if c[0] == "ssh"]
        assert len(ssh_calls) >= 1

    def test_hfjobs_compute_sets_distillate_compute_env(self, tmp_path, monkeypatch):
        """When project has compute=hfjobs, launch_experiment must set
        DISTILLATE_COMPUTE=hfjobs in the tmux session environment so the
        agent knows to dispatch training via HF Jobs."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "cfg")
        (tmp_path / "PROMPT.md").write_text("hfjobs experiment\n")
        (tmp_path / ".distillate").mkdir()

        captured_extra_env: dict = {}

        def mock_spawn_local(session_name, work_dir, command, *,
                             run_budget=300, session_budget=None, extra_env=None):
            captured_extra_env.update(extra_env or {})
            return 0

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "999\n"
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)
        monkeypatch.setattr("distillate.launcher._spawn_local", mock_spawn_local)
        monkeypatch.setattr("distillate.auth.hf_token_for", lambda *_: "hf_test_token")
        monkeypatch.setattr("distillate.config.HF_NAMESPACE", "test-namespace")

        from distillate.launcher import launch_experiment
        proj = {
            "name": "hf-test",
            "compute": {"provider": "hfjobs", "gpu_type": "a100-large", "budget_usd": 25.0},
        }
        data = launch_experiment(tmp_path, project=proj)

        assert data["status"] == "running"
        assert captured_extra_env.get("DISTILLATE_COMPUTE") == "hfjobs", (
            "DISTILLATE_COMPUTE must be set to 'hfjobs' in the tmux env when "
            "compute.provider == 'hfjobs'"
        )
        assert captured_extra_env.get("DISTILLATE_GPU_FLAVOR") == "a100-large"
        assert captured_extra_env.get("HF_TOKEN") == "hf_test_token"


