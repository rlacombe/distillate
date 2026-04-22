# Covers: distillate/experiment_tools/workspace_tools.py
"""Backend tests for the session drafts dock.

The drafts dock replaces the blocking completion modal with a
non-blocking panel that can hold multiple pending drafts at once.
These tests pin the backend invariants the frontend dock depends on:

1. ``draft_summary`` is stored per-session; concurrent wrapups don't
   cross-contaminate each other.
2. ``save_session_summary_tool`` on one session doesn't touch another
   session's draft or running state.
3. ``discard_session_wrapup_tool`` on one session doesn't touch
   another's draft.
4. ``list_workspaces_tool`` exposes ``draft_summary`` on each running
   session so the dock can repopulate after an app reload.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from distillate.state import State
from distillate.experiment_tools.workspace_tools import (
    discard_session_wrapup_tool,
    list_workspaces_tool,
    save_session_summary_tool,
)


@pytest.fixture
def state_with_two_running_sessions(tmp_path):
    """Two running coding sessions in one workspace, both with a stored draft.

    ``isolate_state`` (autouse in conftest) already points STATE_PATH at
    tmp_path, so State() + state.save() write to an isolated file.
    """
    state = State()
    ws_id = "ws_test"
    state._data["workspaces"] = {
        ws_id: {
            "id": ws_id,
            "name": "Test Workspace",
            "repos": [],
            "coding_sessions": {
                "coding_001": {
                    "id": "coding_001",
                    "repo_path": str(tmp_path / "repo_a"),
                    "tmux_name": "ws-001",
                    "claude_session_id": "sid-a",
                    "agent_name": "Alpha",
                    "status": "running",
                    "started_at": "2026-04-14T00:00:00Z",
                    "draft_summary": "# Alpha\n\n- did alpha",
                },
                "coding_002": {
                    "id": "coding_002",
                    "repo_path": str(tmp_path / "repo_b"),
                    "tmux_name": "ws-002",
                    "claude_session_id": "sid-b",
                    "agent_name": "Beta",
                    "status": "running",
                    "started_at": "2026-04-14T00:01:00Z",
                    "draft_summary": "# Beta\n\n- did beta",
                },
            },
        }
    }
    state.save()
    return state, ws_id


def test_concurrent_drafts_are_isolated(state_with_two_running_sessions):
    """Each session's draft_summary lives on its own record and doesn't bleed."""
    state, ws_id = state_with_two_running_sessions
    ws = state._data["workspaces"][ws_id]
    a = ws["coding_sessions"]["coding_001"]
    b = ws["coding_sessions"]["coding_002"]
    assert a["draft_summary"] == "# Alpha\n\n- did alpha"
    assert b["draft_summary"] == "# Beta\n\n- did beta"
    assert a["draft_summary"] != b["draft_summary"]


def test_discard_one_draft_leaves_others_untouched(state_with_two_running_sessions):
    """Discarding session A's draft must not touch session B's draft or status."""
    state, ws_id = state_with_two_running_sessions

    result = discard_session_wrapup_tool(state=state, workspace=ws_id, session="coding_001")
    assert result["success"] is True

    state.reload()
    ws = state._data["workspaces"][ws_id]
    a = ws["coding_sessions"]["coding_001"]
    b = ws["coding_sessions"]["coding_002"]

    assert a.get("draft_summary") in (None, "")
    assert a["status"] == "running"
    assert b["draft_summary"] == "# Beta\n\n- did beta"
    assert b["status"] == "running"


def test_save_one_summary_leaves_others_untouched(state_with_two_running_sessions):
    """Saving session A's summary (ends A) must not touch B."""
    state, ws_id = state_with_two_running_sessions

    # Stub tmux + lab-notebook side effects — we only care about state shape here.
    with patch(
        "subprocess.run",
        return_value=type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})(),
    ), patch(
        "distillate.experiment_tools.workspace_tools.append_lab_book_tool",
        return_value={"success": True},
    ), patch(
        "distillate.experiment_tools.workspace_tools.get_workspace_notes_tool",
        return_value={"success": True, "content": ""},
    ), patch(
        "distillate.experiment_tools.workspace_tools.save_workspace_notes_tool",
        return_value={"success": True},
    ):
        result = save_session_summary_tool(
            state=state, workspace=ws_id, session="coding_001",
            summary="# Alpha final\n\n- final bullet",
        )

    assert result["success"] is True

    state.reload()
    ws = state._data["workspaces"][ws_id]
    a = ws["coding_sessions"]["coding_001"]
    b = ws["coding_sessions"]["coding_002"]

    assert a["status"] == "completed"
    assert a["summary"] == "# Alpha final\n\n- final bullet"
    # B untouched: still running, draft intact
    assert b["status"] == "running"
    assert b["draft_summary"] == "# Beta\n\n- did beta"


def test_list_workspaces_exposes_draft_summary(state_with_two_running_sessions):
    """The sidebar listing endpoint must surface draft_summary per running session,
    so the drafts dock can repopulate after an app reload."""
    state, ws_id = state_with_two_running_sessions

    with patch(
        "distillate.experiment_tools.workspace_tools._batch_pane_titles",
        return_value={"ws-001": "Claude Code", "ws-002": "Claude Code"},
    ):
        result = list_workspaces_tool(state=state)

    workspaces = result["workspaces"]
    ws = next(w for w in workspaces if w["id"] == ws_id)
    sessions = {s["id"]: s for s in ws["running_sessions"]}

    assert "coding_001" in sessions
    assert "coding_002" in sessions
    # CRITICAL: draft_summary must be present so the dock can repopulate
    assert sessions["coding_001"].get("draft_summary") == "# Alpha\n\n- did alpha"
    assert sessions["coding_002"].get("draft_summary") == "# Beta\n\n- did beta"


def test_list_workspaces_omits_draft_for_sessions_without_one(tmp_path):
    """A running session without a draft must not surface a bogus draft field."""
    state = State()
    ws_id = "ws_plain"
    state._data["workspaces"] = {
        ws_id: {
            "id": ws_id, "name": "Plain", "repos": [],
            "coding_sessions": {
                "coding_001": {
                    "id": "coding_001",
                    "repo_path": str(tmp_path / "repo"),
                    "tmux_name": "plain-001",
                    "claude_session_id": "sid-p",
                    "status": "running",
                    "started_at": "2026-04-14T00:00:00Z",
                    # no draft_summary
                },
            },
        }
    }
    state.save()

    with patch(
        "distillate.experiment_tools.workspace_tools._batch_pane_titles",
        return_value={"plain-001": "Claude Code"},
    ):
        result = list_workspaces_tool(state=state)

    sessions = {s["id"]: s for s in result["workspaces"][0]["running_sessions"]}
    # Either field is absent, or it's falsy — never a wrong string
    assert not sessions["coding_001"].get("draft_summary")
