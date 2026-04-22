# Covers: distillate/experiment_tools/workspace_tools.py
"""Tests for launching a coding session attached to a canvas."""
import subprocess
from types import SimpleNamespace

import pytest


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_launch_session_with_cwd_override_and_canvas_id(isolate_state, monkeypatch):
    from distillate.state import State
    from distillate.experiment_tools import launch_coding_session_tool

    state = State()
    state.add_workspace("ws1", name="Paper Project",
                        root_path=str(isolate_state))

    canvas_dir = isolate_state / "canvases" / "main"
    canvas_dir.mkdir(parents=True)
    (canvas_dir / "main.tex").write_text("")
    cv = state.add_workspace_canvas(
        "ws1", "Main paper", "latex", directory=str(canvas_dir),
        entry="main.tex",
    )
    state.save()

    calls = []

    def fake_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        if args and isinstance(args[0], list) and "list-sessions" in args[0]:
            return _FakeCompleted(returncode=1, stdout="")  # no live sessions
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = launch_coding_session_tool(
        state=state, workspace="ws1",
        cwd_override=str(canvas_dir),
        canvas_id=cv["id"],
    )
    assert result["success"] is True
    assert result["canvas_id"] == cv["id"]

    # The tmux new-session call should carry ``-c <canvas-dir>``.
    new_session_calls = [
        c for c in calls
        if isinstance(c["args"][0], str) and "tmux new-session" in c["args"][0]
    ]
    assert new_session_calls, "tmux new-session was not invoked"
    tmux_cmd = new_session_calls[0]["args"][0]
    assert str(canvas_dir) in tmux_cmd
    assert "-c " in tmux_cmd

    # The session record carries canvas_id, and the canvas record points at it.
    state.reload()
    sessions = state.workspaces["ws1"].get("coding_sessions") or {}
    assert len(sessions) == 1
    sess = next(iter(sessions.values()))
    assert sess["canvas_id"] == cv["id"]

    updated_cv = state.get_workspace_canvas("ws1", cv["id"])
    assert updated_cv["session_id"] == sess["id"]


def test_launch_session_without_cwd_override_needs_repo(isolate_state, monkeypatch):
    """When cwd_override is empty, the tool still requires a linked repo."""
    from distillate.state import State
    from distillate.experiment_tools import launch_coding_session_tool

    state = State()
    state.add_workspace("ws1", name="Rootless",
                        root_path=str(isolate_state))

    result = launch_coding_session_tool(state=state, workspace="ws1")
    assert result["success"] is False
    assert "repo" in result.get("error", "").lower()
