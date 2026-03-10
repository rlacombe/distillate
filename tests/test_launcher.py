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


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

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
        assert "PROMPT.md" in cmd
        assert "claude-sonnet-4-5-20250929" in cmd
        assert "--max-turns 100" in cmd
        assert "--output-format stream-json" in cmd
        assert "--allowedTools" in cmd

    def test_custom_params(self):
        from distillate.launcher import _build_claude_command
        cmd = _build_claude_command(
            Path("/project/PROMPT.md"),
            model="claude-opus-4-20250514",
            max_turns=50,
        )
        assert "claude-opus-4-20250514" in cmd
        assert "--max-turns 50" in cmd


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

        pid = _spawn_local("test-session", Path("/tmp"), "echo hello")
        assert len(calls) == 2  # new-session + display-message
        assert "new-session" in calls[0]

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
        assert len(EXPERIMENT_TOOL_SCHEMAS) == 17  # 14 original + 3 new

    def test_new_tool_names(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        names = {s["name"] for s in EXPERIMENT_TOOL_SCHEMAS}
        assert "launch_experiment" in names
        assert "experiment_status" in names
        assert "stop_experiment" in names

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
