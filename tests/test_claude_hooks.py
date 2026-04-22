# Covers: distillate/claude_hooks.py, distillate/routes/hooks.py
"""Tests for the Claude Code hooks integration.

These tests define the contract for the forthcoming
`distillate.claude_hooks` module + `distillate/routes/hooks.py` route file.

Authoritative hook payload + settings.json schema is from
https://code.claude.com/docs/en/hooks.md.

The implementation doesn't exist yet — these tests currently fail
on ImportError. That's intentional: they ARE the spec.
"""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_hook_state():
    """Fresh in-memory hook state + server port per test (both are module-level)."""
    try:
        from distillate.claude_hooks import clear_hook_state, set_server_port
    except ImportError:
        # Implementation not present yet — allow import-time failure to surface
        # in the test that needs it, not here.
        return
    clear_hook_state()
    set_server_port(0)
    yield
    clear_hook_state()
    set_server_port(0)


# ---------------------------------------------------------------------------
# Group A — Hook config writer
# ---------------------------------------------------------------------------


class TestWriteHookConfig:
    """`.claude/settings.local.json` generation."""

    def test_write_hook_config_creates_json(self, tmp_path):
        """Fresh project dir → settings.local.json with all 3 hooks."""
        from distillate.claude_hooks import write_hook_config

        write_hook_config(project_dir=tmp_path, server_port=8742)

        cfg = tmp_path / ".claude" / "settings.local.json"
        assert cfg.exists(), "settings.local.json should be created"

        data = json.loads(cfg.read_text())
        hooks = data.get("hooks", {})

        # All three hook events are registered
        assert "Stop" in hooks
        assert "Notification" in hooks
        assert "UserPromptSubmit" in hooks

        # URLs point at the local Distillate server
        stop_url = hooks["Stop"][0]["hooks"][0]["url"]
        assert stop_url == "http://127.0.0.1:8742/hooks/claude-code/stop"

        notif_url = hooks["Notification"][0]["hooks"][0]["url"]
        assert notif_url == "http://127.0.0.1:8742/hooks/claude-code/notification"

        ups_url = hooks["UserPromptSubmit"][0]["hooks"][0]["url"]
        assert ups_url == "http://127.0.0.1:8742/hooks/claude-code/user-prompt-submit"

        # Notification uses permission_prompt matcher (per docs — see hooks.md)
        assert hooks["Notification"][0]["matcher"] == "permission_prompt"

        # All hooks are HTTP type
        for event in ("Stop", "Notification", "UserPromptSubmit"):
            assert hooks[event][0]["hooks"][0]["type"] == "http"

    def test_write_hook_config_preserves_existing_hooks(self, tmp_path):
        """User's pre-existing hooks in settings.local.json are not clobbered."""
        from distillate.claude_hooks import write_hook_config

        cfg = tmp_path / ".claude" / "settings.local.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "echo user-hook"}
                    ]}
                ]
            },
            "permissions": {"allow": ["Bash(git:*)"]},
        }))

        write_hook_config(project_dir=tmp_path, server_port=8742)

        data = json.loads(cfg.read_text())
        # User hook survives
        assert "PreToolUse" in data["hooks"]
        assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo user-hook"
        # Non-hooks settings survive
        assert data["permissions"] == {"allow": ["Bash(git:*)"]}
        # Our hooks are added
        assert "Stop" in data["hooks"]
        assert "Notification" in data["hooks"]
        assert "UserPromptSubmit" in data["hooks"]


# ---------------------------------------------------------------------------
# Group B — Session resolver
# ---------------------------------------------------------------------------


def _seed_workspace_with_session(
    state, ws_id: str, repo_path: Path, sid: str,
    claude_session_id: str = "", tmux_name: str = "",
):
    """Helper: create a workspace + one coding session pointing at a repo."""
    state.add_workspace(ws_id, name=ws_id, root_path=str(repo_path))
    state.add_coding_session(
        ws_id, sid, repo_path=str(repo_path),
        tmux_name=tmux_name, agent_session_id=claude_session_id,
    )


class TestResolveSession:
    """Map hook payload → (workspace_id, session_id) in state."""

    def test_resolve_by_claude_session_id(self, tmp_path):
        """Exact match on claude_session_id → unambiguous resolution."""
        from distillate.state import State
        from distillate.claude_hooks import resolve_session

        state = State()
        repo = tmp_path / "repo1"
        repo.mkdir()
        _seed_workspace_with_session(
            state, "ws1", repo, "sid1", claude_session_id="claude-uuid-abc",
        )

        result = resolve_session(
            state, claude_session_id="claude-uuid-abc", cwd=str(repo),
        )
        assert result == ("ws1", "sid1")

    def test_resolve_falls_back_to_cwd_when_only_one_session(self, tmp_path):
        """Unknown claude_session_id but cwd matches exactly one active session → match."""
        from distillate.state import State
        from distillate.claude_hooks import resolve_session

        state = State()
        repo = tmp_path / "repo1"
        repo.mkdir()
        # Session has no claude_session_id yet (first turn, hook fires before poll)
        _seed_workspace_with_session(state, "ws1", repo, "sid1")

        result = resolve_session(
            state, claude_session_id="never-seen-before", cwd=str(repo),
        )
        assert result == ("ws1", "sid1")

    def test_resolve_returns_none_for_ambiguous_cwd(self, tmp_path):
        """Two sessions, same workspace, no claude_session_id match → None (ambiguous)."""
        from distillate.state import State
        from distillate.claude_hooks import resolve_session

        state = State()
        repo = tmp_path / "repo1"
        repo.mkdir()
        state.add_workspace("ws1", name="ws1", root_path=str(repo))
        state.add_coding_session("ws1", "sid1", repo_path=str(repo))
        state.add_coding_session("ws1", "sid2", repo_path=str(repo))

        result = resolve_session(
            state, claude_session_id="unknown", cwd=str(repo),
        )
        assert result is None


# ---------------------------------------------------------------------------
# Group C — Hook receiver endpoints
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not importlib.util.find_spec("fastapi"),
    reason="fastapi not installed (desktop-only dependency)",
)
class TestHookEndpoints:
    """FastAPI routes that receive Claude Code hook POSTs."""

    @pytest.fixture
    def client_with_session(self, tmp_path, monkeypatch):
        """TestClient + a seeded workspace with one coding session."""
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.config.CONFIG_DIR", tmp_path / "cfg")
        (tmp_path / "cfg").mkdir(parents=True, exist_ok=True)

        from starlette.testclient import TestClient
        from distillate.server import _create_app
        from distillate.state import State

        # Seed state BEFORE the app reads it on startup
        state = State()
        repo = tmp_path / "repo1"
        repo.mkdir()
        _seed_workspace_with_session(
            state, "ws1", repo, "sid1",
            claude_session_id="claude-uuid-abc", tmux_name="distillate-coding-sid1",
        )
        state.save()

        app = _create_app()
        return TestClient(app), str(repo)

    def test_stop_endpoint_sets_idle(self, client_with_session):
        """POST a Stop hook payload → state store has idle."""
        from distillate.claude_hooks import get_hook_state
        client, cwd = client_with_session

        payload = {
            "session_id": "claude-uuid-abc",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": cwd,
            "permission_mode": "default",
            "hook_event_name": "Stop",
            "stop_reason": "end_turn",
            "message": "All done.",
        }
        resp = client.post("/hooks/claude-code/stop", json=payload)
        assert resp.status_code == 200
        assert resp.json().get("matched") is True

        assert get_hook_state(("ws1", "sid1")) == "idle"

    def test_notification_permission_prompt_sets_waiting(self, client_with_session):
        """Notification with notification_type=permission_prompt → state=waiting."""
        from distillate.claude_hooks import get_hook_state
        client, cwd = client_with_session

        payload = {
            "session_id": "claude-uuid-abc",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": cwd,
            "hook_event_name": "Notification",
            "title": "Permission needed",
            "message": "Claude needs your permission to use Bash",
            "notification_type": "permission_prompt",
        }
        resp = client.post("/hooks/claude-code/notification", json=payload)
        assert resp.status_code == 200
        assert get_hook_state(("ws1", "sid1")) == "waiting"

    def test_notification_non_permission_is_ignored(self, client_with_session):
        """Notification with notification_type=idle_prompt → NOT waiting."""
        from distillate.claude_hooks import get_hook_state
        client, cwd = client_with_session

        payload = {
            "session_id": "claude-uuid-abc",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": cwd,
            "hook_event_name": "Notification",
            "title": "Still here?",
            "message": "Idle check",
            "notification_type": "idle_prompt",
        }
        resp = client.post("/hooks/claude-code/notification", json=payload)
        assert resp.status_code == 200
        # Idle-prompt doesn't flip waiting; state store has no entry (or != waiting)
        assert get_hook_state(("ws1", "sid1")) != "waiting"

    def test_user_prompt_submit_sets_working(self, client_with_session):
        """UserPromptSubmit → state=working."""
        from distillate.claude_hooks import get_hook_state
        client, cwd = client_with_session

        payload = {
            "session_id": "claude-uuid-abc",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": cwd,
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Hello",
        }
        resp = client.post("/hooks/claude-code/user-prompt-submit", json=payload)
        assert resp.status_code == 200
        assert get_hook_state(("ws1", "sid1")) == "working"

    def test_unknown_session_returns_200_with_matched_false(self, client_with_session):
        """Unknown claude_session_id + unknown cwd → 200 {matched: false}, no state change."""
        client, _cwd = client_with_session

        payload = {
            "session_id": "never-seen",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": "/nowhere",
            "permission_mode": "default",
            "hook_event_name": "Stop",
            "stop_reason": "end_turn",
            "message": "",
        }
        resp = client.post("/hooks/claude-code/stop", json=payload)
        # Claude Code treats non-2xx as non-blocking failure, so we return 200
        # with an explicit matched=false rather than 404, to keep its debug log clean.
        assert resp.status_code == 200
        assert resp.json().get("matched") is False


# ---------------------------------------------------------------------------
# Group D — Overlay helper (hook state wins over tmux classifier)
# ---------------------------------------------------------------------------


class TestMergeWithHookState:
    """`merge_with_hook_state` overlays hook-reported status on a sessions dict."""

    def test_hook_status_overrides_classifier(self):
        from distillate.claude_hooks import merge_with_hook_state, set_hook_state

        sessions = {"ws1/sid1": {"status": "working", "name": "Claude"}}
        set_hook_state(("ws1", "sid1"), "waiting")

        out = merge_with_hook_state(sessions)

        assert out["ws1/sid1"]["status"] == "waiting"
        assert out["ws1/sid1"]["name"] == "Claude"  # other fields preserved

    def test_sessions_without_hook_state_untouched(self):
        from distillate.claude_hooks import merge_with_hook_state

        sessions = {"ws1/sid1": {"status": "idle", "name": "Claude"}}
        # No hook state set for this key.
        out = merge_with_hook_state(sessions)
        assert out["ws1/sid1"]["status"] == "idle"

    def test_malformed_key_is_skipped(self):
        from distillate.claude_hooks import merge_with_hook_state

        sessions = {"not-a-slash-separated-key": {"status": "idle"}}
        # Must not raise on malformed keys.
        out = merge_with_hook_state(sessions)
        assert out["not-a-slash-separated-key"]["status"] == "idle"


# ---------------------------------------------------------------------------
# Group E — Launcher writes hook config on session start
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_subprocess_factory():
    """Return a fake subprocess.run that answers tmux commands benignly."""
    def fake_run(*args, **kwargs):
        if args and isinstance(args[0], list) and "list-sessions" in args[0]:
            return _FakeCompleted(returncode=1, stdout="")
        return _FakeCompleted(returncode=0)
    return fake_run


class TestWorkspaceCodingSessionDoesNotWriteHooks:
    """Workspace coding sessions must NOT install HTTP status hooks.

    Hooks are reserved for Experimentalist sessions (Experiments). Coding
    sessions are interactive — the user drives them directly, so
    Stop/Notification/UserPromptSubmit hook roundtrips are unwanted noise.
    """

    def test_coding_session_does_not_write_hook_config(self, isolate_state, monkeypatch):
        import subprocess as sp
        from distillate.state import State
        from distillate.experiment_tools import launch_coding_session_tool
        from distillate.claude_hooks import set_server_port

        monkeypatch.setattr(sp, "run", _fake_subprocess_factory())

        repo = isolate_state / "repo1"
        repo.mkdir()

        state = State()
        state.add_workspace("ws1", name="WS", root_path=str(isolate_state))
        state.add_workspace_repo("ws1", str(repo))
        state.save()

        set_server_port(8742)
        try:
            result = launch_coding_session_tool(state=state, workspace="ws1")
            assert result.get("success") is True, result

            cfg = repo / ".claude" / "settings.local.json"
            # Even with the server port set, a coding session must not emit
            # a settings.local.json populated with our Stop/Notification/UPS hooks.
            if cfg.exists():
                data = json.loads(cfg.read_text())
                hooks = data.get("hooks", {})
                assert "Stop" not in hooks, "coding sessions must not install Stop hook"
                assert "Notification" not in hooks
                assert "UserPromptSubmit" not in hooks
        finally:
            set_server_port(0)


class TestExperimentLauncherWritesHookConfig:
    """launch_experiment writes .claude/settings.local.json with HTTP hooks.

    This is the Experimentalist surface — the one place hook roundtrips are
    wanted, so the experiments chart sidebar can reflect live session state
    (working/idle/waiting) without tmux content polling.
    """

    def test_experiment_launch_writes_hook_config_when_port_set(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock, patch
        from distillate.launcher import launch_experiment
        from distillate.claude_hooks import set_server_port

        project_path = tmp_path / "xp"
        project_path.mkdir()
        (project_path / ".distillate").mkdir()
        (project_path / "PROMPT.md").write_text("test prompt")

        set_server_port(8742)
        try:
            with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="tmux 3.3a")), \
                 patch("distillate.launcher._next_session_id", return_value="session_001"), \
                 patch("distillate.launcher._session_name", return_value="xp-001"), \
                 patch("distillate.launcher._install_hooks_into"), \
                 patch("distillate.launcher._refresh_protocol_files"), \
                 patch("distillate.launcher._spawn_local"), \
                 patch("distillate.launcher.write_budget_json"):
                launch_experiment(project_path, agent_type="claude", project={"name": "xp"})

            cfg = project_path / ".claude" / "settings.local.json"
            assert cfg.exists(), "settings.local.json should be created"
            data = json.loads(cfg.read_text())
            hooks = data.get("hooks", {})
            assert "Stop" in hooks
            assert "Notification" in hooks
            assert "UserPromptSubmit" in hooks
            stop_url = hooks["Stop"][0]["hooks"][0]["url"]
            assert "8742" in stop_url
        finally:
            set_server_port(0)

    def test_experiment_launch_skips_hook_config_when_port_unset(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock, patch
        from distillate.launcher import launch_experiment
        from distillate.claude_hooks import set_server_port

        project_path = tmp_path / "xp"
        project_path.mkdir()
        (project_path / ".distillate").mkdir()
        (project_path / "PROMPT.md").write_text("test prompt")

        set_server_port(0)

        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="tmux 3.3a")), \
             patch("distillate.launcher._next_session_id", return_value="session_001"), \
             patch("distillate.launcher._session_name", return_value="xp-001"), \
             patch("distillate.launcher._install_hooks_into"), \
             patch("distillate.launcher._refresh_protocol_files"), \
             patch("distillate.launcher._spawn_local"), \
             patch("distillate.launcher.write_budget_json"):
            launch_experiment(project_path, agent_type="claude", project={"name": "xp"})

        cfg = project_path / ".claude" / "settings.local.json"
        assert not cfg.exists(), "no port ⇒ no hook config written"
