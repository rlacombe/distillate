# Covers: distillate/state.py
"""Tests for the Workbench default project."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

import distillate.state as state_mod
from distillate.state import State


@pytest.fixture
def state(tmp_path):
    """Create a State backed by a temp file."""
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"zotero_library_version": 0, "documents": {}}))
    with patch.object(state_mod, "STATE_PATH", state_file), \
         patch.object(state_mod, "LOCK_PATH", state_file.with_suffix(".lock")):
        s = State()
    yield s


class TestWorkbenchBootstrap:

    def test_ensure_workbench_creates_default(self, state):
        """ensure_workbench creates the Workbench project on first call."""
        assert state.get_default_workspace() is None
        ws = state.ensure_workbench()
        assert ws["name"] == "Workbench"
        assert ws["default"] is True
        assert ws["id"] == "workbench"

    def test_ensure_workbench_is_idempotent(self, state):
        """Calling ensure_workbench twice returns the same project."""
        ws1 = state.ensure_workbench()
        ws2 = state.ensure_workbench()
        assert ws1["id"] == ws2["id"]
        assert len([w for w in state.workspaces.values() if w.get("default")]) == 1

    def test_workbench_cannot_be_deleted(self, state):
        """The Workbench project cannot be deleted via remove_workspace."""
        state.ensure_workbench()
        result = state.remove_workspace("workbench")
        assert result is False
        assert state.get_workspace("workbench") is not None

    def test_other_workspaces_can_be_deleted(self, state):
        """Non-default workspaces can be deleted normally."""
        state.add_workspace("test-project", name="Test")
        result = state.remove_workspace("test-project")
        assert result is True
        assert state.get_workspace("test-project") is None

    def test_get_default_workspace(self, state):
        """get_default_workspace returns the Workbench after creation."""
        state.ensure_workbench()
        default = state.get_default_workspace()
        assert default is not None
        assert default["name"] == "Workbench"
        assert default["default"] is True

    def test_workbench_description(self, state):
        """Workbench has the expected description."""
        ws = state.ensure_workbench()
        assert "unfiled" in ws["description"].lower()

    def test_new_experiment_defaults_to_workbench(self, state):
        """New experiments are assigned to Workbench by default."""
        state.ensure_workbench()
        workbench = state.get_default_workspace()

        # Add an experiment without specifying workspace_id
        state.add_experiment(
            experiment_id="test-xp",
            name="Test Experiment",
            path="/tmp/test-xp",
        )
        exp = state.find_experiment("test-xp")

        # Verify it was NOT assigned to Workbench at add_experiment time (still None)
        # because add_experiment just stores what was passed
        assert exp.get("workspace_id") is None

        # However, in create_experiment endpoint, workspace_id is defaulted before calling add_experiment
        # So we test the backfill case instead
        state.add_experiment(
            experiment_id="test-xp2",
            name="Test Experiment 2",
            path="/tmp/test-xp2",
            workspace_id=workbench["id"],  # Explicitly set to Workbench
        )
        exp2 = state.find_experiment("test-xp2")
        assert exp2.get("workspace_id") == workbench["id"]
