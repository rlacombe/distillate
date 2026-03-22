"""Tests for the experiment launcher — templates, scaffolding, sessions."""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Template management
# ---------------------------------------------------------------------------

class TestTemplatesDir:
    def test_returns_config_subdir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        from distillate.launcher import templates_dir
        result = templates_dir()
        assert result == tmp_path / "templates"
        assert result.is_dir()

    def test_creates_dir_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        from distillate.launcher import templates_dir
        d = templates_dir()
        assert d.exists()


class TestListTemplates:
    def test_empty_when_no_templates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        from distillate.launcher import list_templates
        assert list_templates() == []

    def test_discovers_templates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl_dir = tmp_path / "templates" / "my-exp"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "PROMPT.md").write_text("line1\nline2\nline3\n")
        (tmpl_dir / "data").mkdir()

        from distillate.launcher import list_templates
        templates = list_templates()
        assert len(templates) == 1
        assert templates[0]["name"] == "my-exp"
        assert templates[0]["has_data"] is True
        assert templates[0]["prompt_lines"] == 3

    def test_skips_hidden_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl_dir = tmp_path / "templates"
        tmpl_dir.mkdir()
        (tmpl_dir / ".hidden").mkdir()
        (tmpl_dir / "visible").mkdir()
        (tmpl_dir / "visible" / "PROMPT.md").write_text("hello\n")

        from distillate.launcher import list_templates
        templates = list_templates()
        assert len(templates) == 1
        assert templates[0]["name"] == "visible"

    def test_no_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl_dir = tmp_path / "templates" / "simple"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "PROMPT.md").write_text("just a prompt\n")

        from distillate.launcher import list_templates
        templates = list_templates()
        assert templates[0]["has_data"] is False


class TestImportTemplate:
    def test_imports_prompt_and_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        source = tmp_path / "src-experiment"
        source.mkdir()
        (source / "PROMPT.md").write_text("do the thing\n")
        (source / "data").mkdir()
        (source / "data" / "train.csv").write_text("a,b,c\n")
        (source / "evaluate.py").write_text("print('eval')\n")
        (source / "random.txt").write_text("ignored\n")

        from distillate.launcher import import_template
        name = import_template(source)
        assert name == "src-experiment"

        dest = tmp_path / "templates" / "src-experiment"
        assert (dest / "PROMPT.md").exists()
        assert (dest / "data" / "train.csv").exists()
        assert (dest / "evaluate.py").exists()
        assert not (dest / "random.txt").exists()

    def test_custom_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        source = tmp_path / "my_dir"
        source.mkdir()
        (source / "PROMPT.md").write_text("prompt\n")

        from distillate.launcher import import_template
        name = import_template(source, name="Custom Name")
        assert name == "custom-name"
        assert (tmp_path / "templates" / "custom-name" / "PROMPT.md").exists()

    def test_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        dest = tmp_path / "templates" / "existing"
        dest.mkdir(parents=True)
        (dest / "old_file.txt").write_text("old\n")

        source = tmp_path / "new_src"
        source.mkdir()
        (source / "PROMPT.md").write_text("new prompt\n")

        from distillate.launcher import import_template
        import_template(source, name="existing")

        assert (dest / "PROMPT.md").read_text() == "new prompt\n"
        assert not (dest / "old_file.txt").exists()

    def test_source_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        from distillate.launcher import import_template
        with pytest.raises(FileNotFoundError):
            import_template(tmp_path / "nonexistent")


class TestScaffoldExperiment:
    def test_scaffold_creates_structure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)

        # Create template
        tmpl = tmp_path / "templates" / "test-tmpl"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("test prompt\n")
        (tmpl / "evaluate.py").write_text("print('eval')\n")

        # Create autoresearch directory with minimal files
        autoresearch = Path(__file__).parent.parent / "distillate" / "autoresearch"

        target = tmp_path / "output" / "my-exp"

        from distillate.launcher import scaffold_experiment
        result = scaffold_experiment("test-tmpl", target)

        assert result == target
        assert (target / "PROMPT.md").read_text() == "test prompt\n"
        assert (target / "evaluate.py").exists()
        assert (target / ".distillate").is_dir()
        assert (target / ".claude").is_dir()
        assert (target / ".claude" / "settings.local.json").exists()

        # Check settings.local.json has permissions
        local_cfg = json.loads((target / ".claude" / "settings.local.json").read_text())
        assert "permissions" in local_cfg
        assert "allow" in local_cfg["permissions"]

    def test_scaffold_template_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        (tmp_path / "templates").mkdir()

        from distillate.launcher import scaffold_experiment
        with pytest.raises(FileNotFoundError, match="Template not found"):
            scaffold_experiment("nonexistent", tmp_path / "out")

    def test_scaffold_target_not_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "t"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("p\n")

        target = tmp_path / "notempty"
        target.mkdir()
        (target / "file.txt").write_text("stuff\n")

        from distillate.launcher import scaffold_experiment
        with pytest.raises(FileExistsError, match="not empty"):
            scaffold_experiment("t", target)

    def test_scaffold_git_init(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "g"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "git-test"

        from distillate.launcher import scaffold_experiment
        scaffold_experiment("g", target)

        assert (target / ".git").exists()

    def test_scaffold_installs_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "h"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "hook-test"

        from distillate.launcher import scaffold_experiment
        scaffold_experiment("h", target)

        settings = target / ".claude" / "settings.json"
        if settings.exists():
            cfg = json.loads(settings.read_text())
            assert "hooks" in cfg

    def test_scaffold_creates_mcp_json(self, tmp_path, monkeypatch):
        """scaffold_experiment creates a .mcp.json file."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "mcp-test"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "mcp-exp"
        from distillate.launcher import scaffold_experiment
        scaffold_experiment("mcp-test", target)

        mcp_json = target / ".mcp.json"
        assert mcp_json.exists()
        cfg = json.loads(mcp_json.read_text())
        assert "mcpServers" in cfg

    def test_scaffold_mcp_json_has_distillate_server(self, tmp_path, monkeypatch):
        """The .mcp.json file references the distillate MCP server."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "mcp-srv"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "mcp-srv-exp"
        from distillate.launcher import scaffold_experiment
        scaffold_experiment("mcp-srv", target)

        cfg = json.loads((target / ".mcp.json").read_text())
        assert "distillate" in cfg["mcpServers"]
        server_cfg = cfg["mcpServers"]["distillate"]
        assert "command" in server_cfg
        assert server_cfg["args"] == ["-m", "distillate.mcp_server"]

    def test_scaffold_settings_local_has_mcp_permissions(self, tmp_path, monkeypatch):
        """settings.local.json includes MCP tool permissions (mcp__distillate__*)."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "perm-test"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "perm-exp"
        from distillate.launcher import scaffold_experiment
        scaffold_experiment("perm-test", target)

        local_cfg = json.loads((target / ".claude" / "settings.local.json").read_text())
        allow_list = local_cfg["permissions"]["allow"]
        assert "mcp__distillate__start_run" in allow_list
        assert "mcp__distillate__conclude_run" in allow_list
        assert "mcp__distillate__save_enrichment" in allow_list
        assert "mcp__distillate__scan_project" in allow_list
        assert "mcp__distillate__annotate_run" in allow_list


# ---------------------------------------------------------------------------
# Scaffold endpoint (server.py POST /experiments/scaffold)
# ---------------------------------------------------------------------------

class TestScaffoldEndpoint:
    """Test the scaffold_from_template endpoint logic via the server app."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        """Create a test client with isolated state and template dirs."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        monkeypatch.setenv("DISTILLATE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr("distillate.config.EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.server import _create_app
        from starlette.testclient import TestClient

        app = _create_app()
        return TestClient(app)

    def _make_template(self, tmp_path, name="tiny-matmul"):
        tmpl = tmp_path / "templates" / name
        tmpl.mkdir(parents=True, exist_ok=True)
        (tmpl / "PROMPT.md").write_text("# Test prompt\n")
        (tmpl / "evaluate.py").write_text("print('ok')\n")
        return tmpl

    def test_scaffold_endpoint(self, tmp_path, client):
        """POST with valid template registers project and returns ok."""
        self._make_template(tmp_path)
        resp = client.post("/experiments/scaffold", json={"template": "tiny-matmul", "name": "TinyMatMul"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["project_id"] == "tinymatmul"
        assert "path" in data

    def test_scaffold_already_exists(self, tmp_path, client):
        """POST when project already registered returns already_exists: true."""
        self._make_template(tmp_path)
        # First call scaffolds
        resp1 = client.post("/experiments/scaffold", json={"template": "tiny-matmul", "name": "TinyMatMul"})
        assert resp1.json()["ok"] is True
        # Second call returns existing
        resp2 = client.post("/experiments/scaffold", json={"template": "tiny-matmul", "name": "TinyMatMul"})
        data = resp2.json()
        assert data["ok"] is True
        assert data["already_exists"] is True

    def test_scaffold_missing_template(self, tmp_path, client):
        """POST with bogus template name returns 404."""
        (tmp_path / "templates").mkdir(parents=True, exist_ok=True)
        resp = client.post("/experiments/scaffold", json={"template": "nonexistent", "name": "Nope"})
        assert resp.status_code == 404
        assert resp.json()["ok"] is False

    def test_scaffold_no_template_param(self, client):
        """POST with empty body returns 400."""
        resp = client.post("/experiments/scaffold", json={})
        assert resp.status_code == 400
        assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# Session management
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

        pid = _spawn_local("test-session", Path("/tmp"), "echo hello")
        assert len(calls) == 6  # status off (global) + new-session + status off (session) + mouse on + send Enter + display-message
        assert "new-session" in calls[1]

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


class TestRefreshSessionStatuses:
    def test_updates_completed(self, monkeypatch):
        from distillate.launcher import refresh_session_statuses

        def mock_session_status(name, host=None):
            return "completed"

        monkeypatch.setattr("distillate.launcher.session_status", mock_session_status)

        state = MagicMock()
        state.projects = {
            "proj1": {
                "sessions": {
                    "s1": {"status": "running", "tmux_session": "distillate-proj1-001"},
                    "s2": {"status": "completed", "tmux_session": "distillate-proj1-002"},
                },
            },
        }

        changed = refresh_session_statuses(state)
        assert changed == 1
        assert state.projects["proj1"]["sessions"]["s1"]["status"] == "completed"
        assert "completed_at" in state.projects["proj1"]["sessions"]["s1"]

    def test_no_change_when_still_running(self, monkeypatch):
        from distillate.launcher import refresh_session_statuses

        def mock_session_status(name, host=None):
            return "running"

        monkeypatch.setattr("distillate.launcher.session_status", mock_session_status)

        state = MagicMock()
        state.projects = {
            "proj1": {
                "sessions": {
                    "s1": {"status": "running", "tmux_session": "distillate-proj1-001"},
                },
            },
        }

        changed = refresh_session_statuses(state)
        assert changed == 0
        assert state.projects["proj1"]["sessions"]["s1"]["status"] == "running"


# ---------------------------------------------------------------------------
# State integration
# ---------------------------------------------------------------------------

class TestStateSessionMethods:
    def test_add_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State

        state = State()
        state.add_project("p1", "Project 1", str(tmp_path))
        state.add_session("p1", "session_001", {"status": "running", "model": "sonnet"})

        proj = state.get_project("p1")
        assert "sessions" in proj
        assert "session_001" in proj["sessions"]
        assert proj["sessions"]["session_001"]["status"] == "running"

    def test_update_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State

        state = State()
        state.add_project("p1", "Project 1", str(tmp_path))
        state.add_session("p1", "s1", {"status": "running"})
        state.update_session("p1", "s1", status="completed")

        sess = state.get_project("p1")["sessions"]["s1"]
        assert sess["status"] == "completed"

    def test_update_nonexistent_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State

        state = State()
        state.add_project("p1", "Project 1", str(tmp_path))
        state.update_session("p1", "nonexistent", status="completed")
        # Should not raise

    def test_active_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State

        state = State()
        state.add_project("p1", "Project 1", str(tmp_path))
        state.add_session("p1", "s1", {"status": "running"})
        state.add_session("p1", "s2", {"status": "completed"})
        state.add_project("p2", "Project 2", str(tmp_path))
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
        state.add_project("p1", "Project 1", str(tmp_path))
        assert state.active_sessions() == []


# ---------------------------------------------------------------------------
# Experiment tools
# ---------------------------------------------------------------------------

class TestExperimentToolSchemas:
    def test_schema_count(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        assert len(EXPERIMENT_TOOL_SCHEMAS) == 37  # 35 + purge_hook_runs + discover_relevant_papers

    def test_new_tool_names(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        names = {s["name"] for s in EXPERIMENT_TOOL_SCHEMAS}
        assert "launch_experiment" in names
        assert "experiment_status" in names
        assert "stop_experiment" in names
        assert "compare_projects" in names
        assert "queue_sessions" in names
        assert "list_templates" in names
        assert "save_template" in names
        assert "create_github_repo" in names
        assert "reading_report" in names

    def test_all_schemas_have_required_fields(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        for schema in EXPERIMENT_TOOL_SCHEMAS:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema


class TestLaunchExperimentTool:
    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import launch_experiment_tool
        from distillate.state import State

        state = State()
        result = launch_experiment_tool(state=state, project="nonexistent")
        assert "error" in result

    def test_no_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import launch_experiment_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Project 1", "")
        result = launch_experiment_tool(state=state, project="p1")
        assert "error" in result
        assert "no path" in result["error"].lower()


class TestExperimentStatusTool:
    def test_all_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.session_status", lambda n, h=None: "running")

        from distillate.experiment_tools import experiment_status_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Exp 1", str(tmp_path))
        state.add_session("p1", "s1", {"status": "running", "tmux_session": "t1"})

        result = experiment_status_tool(state=state)
        assert result["total_active_sessions"] == 1
        assert len(result["experiments"]) == 1

    def test_specific_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.session_status", lambda n, h=None: "completed")

        from distillate.experiment_tools import experiment_status_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Exp 1", str(tmp_path))
        state.add_project("p2", "Exp 2", str(tmp_path))

        result = experiment_status_tool(state=state, project="p1")
        assert len(result["experiments"]) == 1
        assert result["experiments"][0]["name"] == "Exp 1"


class TestStopExperimentTool:
    def test_no_running_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")

        from distillate.experiment_tools import stop_experiment_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Exp 1", str(tmp_path))
        result = stop_experiment_tool(state=state, project="p1")
        assert "error" in result

    def test_stops_running(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.stop_session", lambda n, h=None: True)

        from distillate.experiment_tools import stop_experiment_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Exp 1", str(tmp_path))
        state.add_session("p1", "s1", {"status": "running", "tmux_session": "t1"})
        state.save()

        result = stop_experiment_tool(state=state, project="p1")
        assert result["success"] is True
        assert "t1" in result["stopped"]


# ---------------------------------------------------------------------------
# CLI command tests
# ---------------------------------------------------------------------------

class TestNewExperimentCLI:
    def test_no_templates_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("distillate.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        (tmp_path / "templates").mkdir()

        from distillate.main import _new_experiment
        _new_experiment([])

        output = capsys.readouterr().out
        assert "No templates available" in output


class TestListExperimentsCLI:
    def test_no_projects(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.session_status", lambda n, h=None: "completed")

        from distillate.main import _list_experiments
        _list_experiments()

        output = capsys.readouterr().out
        assert "No experiments tracked" in output

    def test_shows_projects(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.session_status", lambda n, h=None: "completed")

        from distillate.state import State

        state = State()
        state.add_project("tiny-gene-code", "Tiny Gene Code", str(tmp_path))
        state.add_session("tiny-gene-code", "s1", {"status": "running", "tmux_session": "t1"})
        state.save()

        from distillate.main import _list_experiments
        _list_experiments()

        output = capsys.readouterr().out
        assert "Tiny Gene Code" in output


# ---------------------------------------------------------------------------
# Server endpoint tests
# ---------------------------------------------------------------------------

class TestServerEndpoints:
    """Tests for the desktop-app REST endpoints in server.py."""

    @pytest.fixture(autouse=True)
    def _setup_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("ZOTERO_LIBRARY_ID", "12345")
        monkeypatch.setenv("ZOTERO_API_KEY", "fake")
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
        self.tmp_path = tmp_path

    def _make_client(self):
        from starlette.testclient import TestClient
        from distillate.server import _create_app
        app = _create_app()
        return TestClient(app)

    def test_papers_empty(self):
        client = self._make_client()
        resp = client.get("/papers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["papers"] == []
        assert data["total"] == 0

    def test_papers_returns_documents(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "ABC123": {
                "title": "Test Paper",
                "status": "processed",
                "authors": ["Alice", "Bob", "Charlie", "Dave"],
                "summary": "A great paper about testing.",
                "engagement": 75,
                "metadata": {
                    "citekey": "alice2025test",
                    "tags": ["ml", "testing"],
                    "citation_count": 42,
                    "publication_date": "2025-01-15",
                },
                "uploaded_at": "2025-01-10T00:00:00Z",
                "processed_at": "2025-01-12T00:00:00Z",
            },
        }
        state.save()

        client = self._make_client()
        resp = client.get("/papers")
        data = resp.json()
        assert data["total"] == 1
        paper = data["papers"][0]
        assert paper["key"] == "ABC123"
        assert paper["title"] == "Test Paper"
        assert paper["citekey"] == "alice2025test"
        assert paper["authors"] == ["Alice", "Bob", "Charlie"]  # truncated to 3
        assert paper["engagement"] == 75
        assert paper["citation_count"] == 42

    def test_papers_status_filter(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "A1": {"title": "Read", "status": "processed", "metadata": {}},
            "A2": {"title": "Queued", "status": "on_remarkable", "metadata": {}},
        }
        state.save()

        client = self._make_client()
        resp = client.get("/papers?status=processed")
        data = resp.json()
        assert data["total"] == 1
        assert data["papers"][0]["key"] == "A1"

    def test_experiments_list_empty(self):
        client = self._make_client()
        resp = client.get("/experiments/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["projects"] == []

    def test_experiments_list_with_projects(self):
        from distillate.state import State
        state = State()
        state.add_project("tiny-gene", "Tiny Gene Code", str(self.tmp_path))
        state._data["projects"]["tiny-gene"]["runs"] = {
            "run-1": {
                "id": "run-1",
                "name": "baseline",
                "status": "completed",
                "decision": "keep",
                "results": {"accuracy": 0.95},
                "started_at": "2025-01-01T00:00:00Z",
                "duration_minutes": 10,
                "tags": ["baseline"],
            },
        }
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/list")
        data = resp.json()
        assert len(data["projects"]) == 1
        proj = data["projects"][0]
        assert proj["id"] == "tiny-gene"
        assert proj["name"] == "Tiny Gene Code"
        assert proj["run_count"] == 0
        assert len(proj["runs"]) == 0
        assert "github_url" not in proj  # no github_url when not set

    def test_experiments_list_includes_github_url(self):
        from distillate.state import State
        state = State()
        state.add_project("xp-gh", "With GitHub", str(self.tmp_path))
        state.update_project("xp-gh", github_url="https://github.com/user/distillate-xp-test")
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/list")
        data = resp.json()
        proj = data["projects"][0]
        assert proj["github_url"] == "https://github.com/user/distillate-xp-test"

    def test_prompt_not_found(self):
        client = self._make_client()
        resp = client.get("/experiments/nonexistent/prompt")
        assert resp.status_code == 404

    def test_prompt_no_file(self):
        from distillate.state import State
        state = State()
        state.add_project("xp-noprompt", "No Prompt", str(self.tmp_path))
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/xp-noprompt/prompt")
        data = resp.json()
        assert data["ok"] is False
        assert data["reason"] == "no_prompt"

    def test_prompt_get(self):
        from distillate.state import State
        state = State()
        proj_dir = self.tmp_path / "xp-prompt"
        proj_dir.mkdir()
        (proj_dir / "PROMPT.md").write_text("# My Experiment\n\nOptimize accuracy.", encoding="utf-8")
        state.add_project("xp-prompt", "With Prompt", str(proj_dir))
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/xp-prompt/prompt")
        data = resp.json()
        assert data["ok"] is True
        assert "# My Experiment" in data["content"]
        assert "Optimize accuracy." in data["content"]

    def test_prompt_put(self):
        from distillate.state import State
        state = State()
        proj_dir = self.tmp_path / "xp-prompt-put"
        proj_dir.mkdir()
        state.add_project("xp-prompt-put", "Put Prompt", str(proj_dir))
        state.save()

        client = self._make_client()
        resp = client.put(
            "/experiments/xp-prompt-put/prompt",
            json={"content": "# Updated Prompt\n\nNew instructions."},
        )
        data = resp.json()
        assert data["ok"] is True

        # Verify the file was written
        content = (proj_dir / "PROMPT.md").read_text(encoding="utf-8")
        assert "# Updated Prompt" in content

        # Verify GET returns the updated content
        resp2 = client.get("/experiments/xp-prompt-put/prompt")
        assert resp2.json()["content"] == "# Updated Prompt\n\nNew instructions."

    def test_notebook_not_found(self):
        client = self._make_client()
        resp = client.get("/experiments/nonexistent/notebook")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_notebook_returns_html(self):
        from distillate.state import State
        state = State()
        state.add_project("proj1", "My Project", str(self.tmp_path))
        state._data["projects"]["proj1"]["runs"] = {
            "r1": {
                "id": "r1", "name": "run1", "status": "completed",
                "started_at": "2025-01-01T00:00:00Z",
                "results": {"accuracy": 0.9},
            },
        }
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/proj1/notebook")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<html" in resp.text

    def test_paper_detail_not_found(self):
        client = self._make_client()
        resp = client.get("/papers/NONEXIST")
        assert resp.status_code == 404
        assert resp.json()["reason"] == "not_found"

    def test_paper_detail_returns_full_data(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "XYZ789": {
                "title": "Attention Is All You Need",
                "status": "processed",
                "authors": ["Vaswani", "Shazeer", "Parmar", "Uszkoreit"],
                "summary": "This paper introduces the Transformer architecture.",
                "engagement": 95,
                "metadata": {
                    "citekey": "vaswani2017attention",
                    "tags": ["transformers", "attention", "nlp"],
                    "citation_count": 100000,
                    "publication_date": "2017-06-12",
                    "venue": "NeurIPS",
                    "doi": "10.5555/3295222.3295349",
                    "arxiv_id": "1706.03762",
                },
                "uploaded_at": "2025-01-01T00:00:00Z",
                "processed_at": "2025-01-05T00:00:00Z",
                "promoted_at": "2025-01-03T00:00:00Z",
            },
        }
        state.save()

        client = self._make_client()
        resp = client.get("/papers/XYZ789")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        paper = data["paper"]
        assert paper["key"] == "XYZ789"
        assert paper["title"] == "Attention Is All You Need"
        # Full authors list (not truncated)
        assert len(paper["authors"]) == 4
        # Full summary (not truncated)
        assert paper["summary"] == "This paper introduces the Transformer architecture."
        assert paper["venue"] == "NeurIPS"
        assert paper["doi"] == "10.5555/3295222.3295349"
        assert paper["arxiv_id"] == "1706.03762"
        assert paper["promoted_at"] == "2025-01-03T00:00:00Z"

    def test_papers_list_includes_promoted_flag(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "K1": {"title": "A", "status": "processed", "metadata": {}},
            "K2": {"title": "B", "status": "processed", "metadata": {}},
        }
        state._data["promoted_papers"] = ["K1"]
        state.save()

        client = self._make_client()
        resp = client.get("/papers")
        papers = {p["key"]: p for p in resp.json()["papers"]}
        assert papers["K1"]["promoted"] is True
        assert papers["K2"]["promoted"] is False

    def test_promote_and_unpromote(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "P1": {"title": "Paper One", "status": "on_remarkable", "metadata": {}},
        }
        state.save()

        client = self._make_client()

        # Promote
        resp = client.post("/papers/P1/promote")
        assert resp.status_code == 200
        assert resp.json()["promoted"] is True

        # Verify persisted
        state.reload()
        assert "P1" in state.promoted_papers

        # Unpromote
        resp = client.post("/papers/P1/unpromote")
        assert resp.status_code == 200
        assert resp.json()["promoted"] is False

        state.reload()
        assert "P1" not in state.promoted_papers

    def test_promote_not_found(self):
        client = self._make_client()
        assert client.post("/papers/NOPE/promote").status_code == 404
        assert client.post("/papers/NOPE/unpromote").status_code == 404

    def test_refresh_metadata_not_found(self):
        client = self._make_client()
        resp = client.post("/papers/NOPE/refresh-metadata")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# New tool tests (compare_projects, queue_sessions, list/save_templates,
# create_github_repo, reading_report)
# ---------------------------------------------------------------------------

class TestCompareProjectsTool:
    def test_needs_two_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import compare_projects_tool
        from distillate.state import State

        state = State()
        result = compare_projects_tool(state=state, projects=["p1"])
        assert "error" in result

    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import compare_projects_tool
        from distillate.state import State

        state = State()
        result = compare_projects_tool(state=state, projects=["a", "b"])
        assert "error" in result

    def test_compares_two_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import compare_projects_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.add_project("p2", "Proj 2", str(tmp_path))

        # Add kept runs with metrics
        state._data["projects"]["p1"]["runs"] = {
            "r1": {"status": "keep", "decision": "keep",
                    "results": {"accuracy": 0.85, "loss": 0.3}},
        }
        state._data["projects"]["p2"]["runs"] = {
            "r1": {"status": "keep", "decision": "keep",
                    "results": {"accuracy": 0.92, "loss": 0.15}},
        }
        state.save()

        result = compare_projects_tool(state=state, projects=["p1", "p2"])
        assert "projects" in result
        assert len(result["projects"]) == 2
        assert "metrics" in result
        assert "accuracy" in result["metrics"]
        assert "loss" in result["metrics"]
        assert result["projects"][0]["name"] == "Proj 1"
        assert result["projects"][0]["best_metrics"]["accuracy"] == 0.85
        assert result["projects"][1]["best_metrics"]["accuracy"] == 0.92

    def test_skips_non_kept_runs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import compare_projects_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.add_project("p2", "Proj 2", str(tmp_path))
        state._data["projects"]["p1"]["runs"] = {
            "r1": {"status": "discard", "decision": "discard",
                    "results": {"accuracy": 0.99}},
        }
        state._data["projects"]["p2"]["runs"] = {}
        state.save()

        result = compare_projects_tool(state=state, projects=["p1", "p2"])
        assert result["projects"][0]["best_metrics"] == {}
        assert result["metrics"] == []


class TestQueueSessionsTool:
    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import queue_sessions_tool
        from distillate.state import State

        state = State()
        result = queue_sessions_tool(state=state, project="nope")
        assert "error" in result

    def test_queues_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import queue_sessions_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        result = queue_sessions_tool(state=state, project="p1", count=3)
        assert result["success"] is True
        assert result["queued"] == 3
        assert "Proj 1" in result["message"]

        # Verify state was updated
        state.reload()
        proj = state.get_project("p1")
        assert proj["continuation_queue"]["count"] == 3
        assert proj["auto_continue"] is True

    def test_custom_model_and_turns(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import queue_sessions_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        result = queue_sessions_tool(
            state=state, project="p1", count=2,
            model="claude-opus-4-20250514", max_turns=50,
        )
        assert result["success"] is True
        assert result["model"] == "claude-opus-4-20250514"
        assert result["max_turns"] == 50


class TestListTemplatesTool:
    def test_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        (tmp_path / "templates").mkdir()
        from distillate.experiment_tools import list_templates_tool
        from distillate.state import State

        result = list_templates_tool(state=State())
        assert result["templates"] == []
        assert result["total"] == 0

    def test_returns_templates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        tmpl_dir = tmp_path / "templates" / "mlp-basic"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "PROMPT.md").write_text("line1\nline2\n")

        from distillate.experiment_tools import list_templates_tool
        from distillate.state import State

        result = list_templates_tool(state=State())
        assert result["total"] == 1
        assert result["templates"][0]["name"] == "mlp-basic"


class TestSaveTemplateTool:
    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import save_template_tool
        from distillate.state import State

        result = save_template_tool(state=State(), project="nope")
        assert "error" in result

    def test_no_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import save_template_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Proj 1", "")
        result = save_template_tool(state=state, project="p1")
        assert "error" in result

    def test_saves_template(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")

        # Create a project directory with PROMPT.md
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        (proj_dir / "PROMPT.md").write_text("# Experiment\nDo the thing.\n")

        from distillate.experiment_tools import save_template_tool
        from distillate.state import State

        state = State()
        state.add_project("my-project", "My Project", str(proj_dir))
        result = save_template_tool(state=state, project="my-project", name="my-tmpl")
        assert result["success"] is True
        assert result["template_name"] == "my-tmpl"
        # Verify file was created
        assert (tmp_path / "config" / "templates" / "my-tmpl" / "PROMPT.md").exists()


class TestCreateGithubRepoTool:
    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import create_github_repo_tool
        from distillate.state import State

        result = create_github_repo_tool(state=State(), project="nope")
        assert "error" in result

    def test_no_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import create_github_repo_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Proj 1", "")
        result = create_github_repo_tool(state=state, project="p1")
        assert "error" in result

    def test_calls_launcher_and_updates_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr(
            "distillate.launcher.create_github_repo",
            lambda path, name, private=True: {"ok": True, "url": "https://github.com/test/repo"},
        )
        from distillate.experiment_tools import create_github_repo_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        result = create_github_repo_tool(state=state, project="p1", name="repo")
        assert result["ok"] is True
        assert result["url"] == "https://github.com/test/repo"

        # Verify github_url saved to state
        state.reload()
        proj = state.get_project("p1")
        assert proj["github_url"] == "https://github.com/test/repo"

    def test_error_from_launcher(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr(
            "distillate.launcher.create_github_repo",
            lambda path, name, private=True: {"ok": False, "reason": "gh not installed"},
        )
        from distillate.experiment_tools import create_github_repo_tool
        from distillate.state import State

        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        result = create_github_repo_tool(state=state, project="p1")
        assert result["ok"] is False
        assert "gh not installed" in result["reason"]


class TestReadingReportTool:
    def test_no_papers(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import reading_report_tool
        from distillate.state import State

        result = reading_report_tool(state=State())
        assert "message" in result

    def test_returns_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import reading_report_tool
        from distillate.state import State

        state = State()
        # Add some processed papers
        state._data["documents"] = {
            "K1": {
                "title": "Paper One", "status": "processed",
                "zotero_item_key": "K1",
                "page_count": 10, "highlight_word_count": 200,
                "engagement": 75,
                "authors": ["Alice", "Bob"],
                "processed_at": "2026-03-10T12:00:00+00:00",
                "metadata": {
                    "tags": ["transformers", "nlp"],
                    "citation_count": 500,
                },
            },
            "K2": {
                "title": "Paper Two", "status": "processed",
                "zotero_item_key": "K2",
                "page_count": 8, "highlight_word_count": 150,
                "engagement": 50,
                "authors": ["Alice", "Charlie"],
                "processed_at": "2026-03-12T12:00:00+00:00",
                "metadata": {
                    "tags": ["transformers", "vision"],
                    "citation_count": 100,
                },
            },
        }
        state.save()

        result = reading_report_tool(state=state)
        assert result["lifetime"]["papers"] == 2
        assert result["lifetime"]["pages"] == 18
        assert result["lifetime"]["words_highlighted"] == 350
        assert result["lifetime"]["avg_engagement"] == 62  # round((75+50)/2)
        assert len(result["top_topics"]) >= 1
        assert result["top_topics"][0]["topic"] == "transformers"
        assert result["top_topics"][0]["count"] == 2
        assert len(result["most_cited"]) == 2
        assert result["most_cited"][0]["citations"] == 500
        assert len(result["top_authors"]) >= 1
        # Alice appears in 2 papers
        alice = [a for a in result["top_authors"] if a["author"] == "Alice"]
        assert len(alice) == 1
        assert alice[0]["count"] == 2


# ---------------------------------------------------------------------------
# New CLI command tests
# ---------------------------------------------------------------------------

class TestUpdateProjectCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _update_project
        _update_project([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _update_project
        _update_project(["nonexistent"])
        assert "No project found" in capsys.readouterr().out

    def test_updates_description(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--update", "p1", "--description", "New desc",
        ])
        from distillate.state import State
        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _update_project
        _update_project(["p1"])
        output = capsys.readouterr().out
        assert "Updated" in output
        assert "description" in output

        state.reload()
        assert state.get_project("p1")["description"] == "New desc"

    def test_updates_key_metric(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--update", "p1", "--key-metric", "f1",
        ])
        from distillate.state import State
        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _update_project
        _update_project(["p1"])

        state.reload()
        assert state.get_project("p1")["key_metric_name"] == "f1"

    def test_nothing_to_update(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--update", "p1"])
        from distillate.state import State
        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _update_project
        _update_project(["p1"])
        assert "Nothing to update" in capsys.readouterr().out


class TestQueueSessionsCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _queue_sessions
        _queue_sessions([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _queue_sessions
        _queue_sessions(["nope"])
        assert "No project found" in capsys.readouterr().out

    def test_queues_default(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--queue-sessions", "p1"])
        from distillate.state import State
        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _queue_sessions
        _queue_sessions(["p1"])
        output = capsys.readouterr().out
        assert "Queued" in output
        assert "1" in output

        state.reload()
        proj = state.get_project("p1")
        assert proj["continuation_queue"]["count"] == 1
        assert proj["auto_continue"] is True

    def test_queues_custom_count(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--queue-sessions", "p1", "--count", "5",
        ])
        from distillate.state import State
        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _queue_sessions
        _queue_sessions(["p1"])

        state.reload()
        assert state.get_project("p1")["continuation_queue"]["count"] == 5


class TestListTemplatesCLI:
    def test_no_templates(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        (tmp_path / "templates").mkdir()
        from distillate.commands import _list_templates
        _list_templates()
        assert "No templates available" in capsys.readouterr().out

    def test_shows_templates(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl_dir = tmp_path / "templates" / "my-exp"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "PROMPT.md").write_text("line1\nline2\n")

        from distillate.commands import _list_templates
        _list_templates()
        output = capsys.readouterr().out
        assert "my-exp" in output


class TestSaveTemplateCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _save_template
        _save_template([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _save_template
        _save_template(["nope"])
        assert "No project found" in capsys.readouterr().out

    def test_saves(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--save-template", "p1"])

        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / "PROMPT.md").write_text("experiment\n")

        from distillate.state import State
        state = State()
        state.add_project("p1", "Proj 1", str(proj_dir))
        state.save()

        from distillate.commands import _save_template
        _save_template(["p1"])
        output = capsys.readouterr().out
        assert "Saved template" in output


class TestCompareProjectsCLI:
    def test_needs_two(self, capsys):
        from distillate.commands import _compare_projects
        _compare_projects(["one"])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _compare_projects
        _compare_projects(["a", "b"])
        assert "No project found" in capsys.readouterr().out

    def test_comparison_table(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State
        state = State()
        state.add_project("p1", "Alpha", str(tmp_path))
        state.add_project("p2", "Beta", str(tmp_path))
        state._data["projects"]["p1"]["runs"] = {
            "r1": {"decision": "keep", "results": {"accuracy": 0.80}},
        }
        state._data["projects"]["p2"]["runs"] = {
            "r1": {"decision": "keep", "results": {"accuracy": 0.95}},
        }
        state.save()

        from distillate.commands import _compare_projects
        _compare_projects(["p1", "p2"])
        output = capsys.readouterr().out
        assert "Alpha" in output
        assert "Beta" in output
        assert "accuracy" in output
        assert "*" in output  # best value starred

    def test_no_metrics(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State
        state = State()
        state.add_project("p1", "Alpha", str(tmp_path))
        state.add_project("p2", "Beta", str(tmp_path))
        state.save()

        from distillate.commands import _compare_projects
        _compare_projects(["p1", "p2"])
        assert "No metrics" in capsys.readouterr().out


class TestGithubCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _github
        _github([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _github
        _github(["nope"])
        assert "No project found" in capsys.readouterr().out

    def test_success(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--github", "p1"])
        monkeypatch.setattr(
            "distillate.launcher.create_github_repo",
            lambda path, name, private=True: {"ok": True, "url": "https://github.com/u/r"},
        )
        from distillate.state import State
        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _github
        _github(["p1"])
        output = capsys.readouterr().out
        assert "https://github.com/u/r" in output

        state.reload()
        assert state.get_project("p1")["github_url"] == "https://github.com/u/r"

    def test_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--github", "p1"])
        monkeypatch.setattr(
            "distillate.launcher.create_github_repo",
            lambda path, name, private=True: {"ok": False, "reason": "no gh"},
        )
        from distillate.state import State
        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _github
        _github(["p1"])
        assert "no gh" in capsys.readouterr().out


class TestCreateExperimentCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _create_experiment
        _create_experiment([])
        assert "Usage" in capsys.readouterr().out

    def test_calls_init_experiment_tool(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--create-experiment", "test-exp",
            "--target", str(tmp_path / "exp"),
            "--goal", "Maximize accuracy",
        ])

        # Mock init_experiment_tool to avoid actual Claude calls
        monkeypatch.setattr(
            "distillate.experiment_tools.init_experiment_tool",
            lambda **kwargs: {
                "success": True,
                "project_id": "test-exp",
                "goals_set": [{"metric": "accuracy", "direction": "maximize"}],
            },
        )

        from distillate.commands import _create_experiment
        _create_experiment(["test-exp"])
        output = capsys.readouterr().out
        assert "test-exp" in output
        assert "Launch it" in output


class TestParallelCampaignCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _parallel_campaign
        _parallel_campaign([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _parallel_campaign
        _parallel_campaign(["nope1", "nope2"])
        assert "No project found" in capsys.readouterr().out

    def test_no_goals(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--parallel-campaign", "p1", "p2",
        ])
        from distillate.state import State
        state = State()
        state.add_project("p1", "Proj 1", str(tmp_path))
        state.add_project("p2", "Proj 2", str(tmp_path))
        state.save()

        from distillate.commands import _parallel_campaign
        _parallel_campaign(["p1", "p2"])
        assert "no goals" in capsys.readouterr().out.lower()


class TestWatchProjectNameResolution:
    def test_resolves_project_name_to_path(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        (proj_dir / ".git").mkdir()

        from distillate.state import State
        state = State()
        state.add_project("my-project", "My Project", str(proj_dir))
        state.save()

        # _watch will fail at scan_project (no actual experiment), but we can
        # verify the path resolution worked by checking it accesses the right dir
        from distillate.commands import _watch
        # Mock scan_project to return error (avoids infinite loop)
        monkeypatch.setattr(
            "distillate.experiments.scan_project",
            lambda p: {"error": f"test_path={p}"},
        )
        monkeypatch.setattr("distillate.config.setup_logging", lambda: None)

        _watch(["my-project"])
        output = capsys.readouterr().out
        assert str(proj_dir) in output  # "Watching /path..."
        assert "Error" in output  # scan_project returns error
