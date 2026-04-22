# Covers: distillate/launcher.py (write_steering, inject_steering_to_tmux,
#         _build_launch_prompt steering consumption),
#         distillate/experiment_tools/session_tools.py (steer_experiment_tool)

"""Steering pipeline: live tmux injection + steering.md durable record.

The steering contract has three deliveries, in order of preference:

1. Live injection into the running tmux Claude Code TUI — typed + Enter
   so the agent sees it as its next user turn immediately.
2. Launch-prompt inline — consumed by ``_build_launch_prompt`` on next
   session start (single-shot: file is unlinked after consume).
3. Post-bash hook banner — ``*** USER INSTRUCTION ***`` in the next
   bash tool result of the currently running session.

These tests pin #1 (live inject) and #2 (launch consume). #3 is covered
by the existing post_bash hook tests.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# inject_steering_to_tmux — the primitive that sends keys + Enter
# ---------------------------------------------------------------------------

class TestInjectSteeringToTmux:
    def test_sends_literal_text_then_enter_in_order(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        from distillate.launcher import inject_steering_to_tmux
        ok = inject_steering_to_tmux("distillate-xp-001", "try beam=32")

        assert ok is True
        assert len(calls) == 2, f"expected 2 send-keys calls, got {calls}"
        assert calls[0] == [
            "tmux", "send-keys", "-t", "distillate-xp-001", "-l", "try beam=32",
        ], f"first call must type literal text, got: {calls[0]}"
        assert calls[1] == [
            "tmux", "send-keys", "-t", "distillate-xp-001", "Enter",
        ], f"second call must submit with Enter, got: {calls[1]}"

    def test_injects_even_when_agent_working(self, monkeypatch):
        # Keystrokes buffer in the PTY and are processed when Claude Code
        # returns to its input prompt — injection must not be skipped.
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)) or MagicMock(returncode=0))

        from distillate.launcher import inject_steering_to_tmux
        ok = inject_steering_to_tmux("distillate-xp-001", "try beam=32")

        assert ok is True, "must attempt injection regardless of agent state"
        assert len(calls) == 2, f"expected text + Enter, got: {calls}"

    def test_returns_false_when_type_step_fails(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1  # simulate tmux failure
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        from distillate.launcher import inject_steering_to_tmux
        assert inject_steering_to_tmux("bad-session", "text") is False

    def test_returns_false_when_enter_step_fails(self, monkeypatch):
        seq = iter([0, 1])  # type OK, Enter fails

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = next(seq)
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        from distillate.launcher import inject_steering_to_tmux
        assert inject_steering_to_tmux("sess", "text") is False

    def test_empty_tmux_name_returns_false(self):
        from distillate.launcher import inject_steering_to_tmux
        assert inject_steering_to_tmux("", "text") is False

    def test_subprocess_exception_returns_false(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise OSError("tmux not found")

        monkeypatch.setattr(subprocess, "run", fake_run)

        from distillate.launcher import inject_steering_to_tmux
        assert inject_steering_to_tmux("sess", "text") is False

    def test_remote_host_uses_ssh(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)

        from distillate.launcher import inject_steering_to_tmux
        ok = inject_steering_to_tmux("sess", "hi", host="user@gpu")

        assert ok is True
        assert all(c[0] == "ssh" and c[1] == "user@gpu" for c in calls)
        assert any("send-keys" in c[2] and "-l" in c[2] for c in calls)
        assert any("Enter" in c[2] for c in calls)


# ---------------------------------------------------------------------------
# _build_launch_prompt — consumes steering.md at session start
# ---------------------------------------------------------------------------

class TestLaunchPromptConsumesSteering:
    def test_inlines_steering_when_present(self, tmp_path):
        (tmp_path / ".distillate").mkdir()
        (tmp_path / ".distillate" / "steering.md").write_text(
            "# Steering Instructions\n\nTry beam=32.\n", encoding="utf-8",
        )

        from distillate.launcher import _build_launch_prompt
        prompt = _build_launch_prompt(None, tmp_path, None)

        assert "## Steering Instructions" in prompt
        assert "Try beam=32." in prompt

    def test_unlinks_steering_after_consume(self, tmp_path):
        (tmp_path / ".distillate").mkdir()
        steering = tmp_path / ".distillate" / "steering.md"
        steering.write_text("Something", encoding="utf-8")

        from distillate.launcher import _build_launch_prompt
        _build_launch_prompt(None, tmp_path, None)

        assert not steering.exists(), \
            "steering.md must be unlinked after consume (single-shot)"

    def test_no_steering_section_when_file_absent(self, tmp_path):
        from distillate.launcher import _build_launch_prompt
        prompt = _build_launch_prompt(None, tmp_path, None)
        assert "## Steering Instructions" not in prompt

    def test_empty_steering_file_does_not_add_section(self, tmp_path):
        (tmp_path / ".distillate").mkdir()
        (tmp_path / ".distillate" / "steering.md").write_text(
            "   \n\n", encoding="utf-8",
        )

        from distillate.launcher import _build_launch_prompt
        prompt = _build_launch_prompt(None, tmp_path, None)

        assert "## Steering Instructions" not in prompt


# ---------------------------------------------------------------------------
# steer_experiment_tool — dispatches live inject + always writes file
# ---------------------------------------------------------------------------

def _make_state_with_project(proj_path: Path, sessions: dict | None = None):
    """Build a MagicMock state returning one project with the given sessions."""
    project = {
        "id": "proj-1",
        "name": "My Experiment",
        "path": str(proj_path),
        "sessions": sessions or {},
    }
    state = MagicMock()
    state.find_all_experiments.return_value = [project]
    state.get_experiment.return_value = project
    return state, project


class TestSteerExperimentToolDelivery:
    def test_writes_steering_md_always(self, tmp_path, monkeypatch):
        # No running session — still writes the file.
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))
        state, _ = _make_state_with_project(tmp_path, sessions={})

        from distillate.experiment_tools.session_tools import steer_experiment_tool
        result = steer_experiment_tool(
            state=state, project="proj-1", text="try beam=32",
        )

        assert result["success"] is True
        steering = tmp_path / ".distillate" / "steering.md"
        assert steering.exists()
        assert "try beam=32" in steering.read_text(encoding="utf-8")

    def test_no_tmux_call_when_no_running_session(self, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return MagicMock(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        state, _ = _make_state_with_project(
            tmp_path,
            sessions={"s1": {"status": "completed", "tmux_session": "old"}},
        )

        from distillate.experiment_tools.session_tools import steer_experiment_tool
        result = steer_experiment_tool(
            state=state, project="proj-1", text="hi",
        )

        assert result["injected_live"] is False
        assert [c for c in calls if "tmux" in c[:1]] == [], \
            "must not call tmux when no running session"

    def test_injects_into_running_session(self, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return MagicMock(returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        state, _ = _make_state_with_project(
            tmp_path,
            sessions={
                "s1": {"status": "running", "tmux_session": "distillate-xp-abc"},
            },
        )

        from distillate.experiment_tools.session_tools import steer_experiment_tool
        result = steer_experiment_tool(
            state=state, project="proj-1", text="try beam=32",
        )

        assert result["injected_live"] is True
        tmux_calls = [c for c in calls if c[:2] == ["tmux", "send-keys"]]
        assert len(tmux_calls) == 2, \
            f"expected 2 tmux send-keys (text + Enter), got: {tmux_calls}"
        # Order: -l <text>, then Enter
        assert tmux_calls[0][-2:] == ["-l", "try beam=32"]
        assert tmux_calls[1][-1] == "Enter"

    def test_file_still_written_when_tmux_inject_fails(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=1)  # always fail

        monkeypatch.setattr(subprocess, "run", fake_run)

        state, _ = _make_state_with_project(
            tmp_path,
            sessions={"s1": {"status": "running", "tmux_session": "sess"}},
        )

        from distillate.experiment_tools.session_tools import steer_experiment_tool
        result = steer_experiment_tool(
            state=state, project="proj-1", text="fallback",
        )

        assert result["success"] is True
        assert result["injected_live"] is False
        steering = tmp_path / ".distillate" / "steering.md"
        assert steering.exists()
        assert "fallback" in steering.read_text(encoding="utf-8")

    def test_message_reflects_live_delivery(self, tmp_path, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))
        state, _ = _make_state_with_project(
            tmp_path,
            sessions={"s1": {"status": "running", "tmux_session": "sess-live"}},
        )

        from distillate.experiment_tools.session_tools import steer_experiment_tool
        result = steer_experiment_tool(
            state=state, project="proj-1", text="go",
        )

        assert "delivered live" in result["message"].lower()
        assert "sess-live" in result["message"]

    def test_message_reflects_file_only_delivery(self, tmp_path, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))
        state, _ = _make_state_with_project(tmp_path, sessions={})

        from distillate.experiment_tools.session_tools import steer_experiment_tool
        result = steer_experiment_tool(
            state=state, project="proj-1", text="later",
        )

        assert "steering.md" in result["message"]
        # Must NOT falsely claim live delivery
        assert "delivered live" not in result["message"].lower()
