# Covers: distillate/welcome_state.py
"""Tests for the welcome screen state synthesizer (7-state fallback chain)."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from distillate.welcome_state import synthesize_welcome_state


def _make_state(projects=None, workspaces=None, documents=None):
    """Build a mock State with the given data."""
    state = MagicMock()
    state.experiments = projects or {}
    state.workspaces = workspaces or {}

    docs = documents or []
    state.documents_with_status = MagicMock(return_value=[d for d in docs if d.get("status") == "tracked"])

    return state


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


class TestFallbackChain:
    """Test that the priority chain resolves correctly."""

    def test_state_7_onboarding_when_empty(self):
        """Brand new install — no projects, no experiments, no papers."""
        state = _make_state()
        result = synthesize_welcome_state(state)
        assert result["state_id"] == "onboarding"
        assert "Distillate" in result["narration_paragraphs"][0]

    def test_state_6_reflective_when_projects_exist(self):
        """Projects exist but nothing urgent."""
        state = _make_state(
            workspaces={"ws1": {"id": "ws1", "name": "Test", "created_at": _days_ago(30), "updated_at": _now_iso()}},
        )
        result = synthesize_welcome_state(state)
        assert result["state_id"] == "reflective"

    def test_state_5_stale_project(self):
        """A project has been quiet for >7 days."""
        state = _make_state(
            workspaces={"ws1": {
                "id": "ws1", "name": "Aeris",
                "created_at": _days_ago(30),
                "updated_at": _days_ago(10),
            }},
        )
        result = synthesize_welcome_state(state)
        assert result["state_id"] == "stale_project"
        assert "Aeris" in result["narration_paragraphs"][0]

    def test_state_1_active_experiment(self):
        """An experiment has a running session."""
        state = _make_state(
            projects={"exp1": {
                "id": "exp1",
                "name": "matmul-sweep",
                "runs": {"r1": {"started_at": _now_iso(), "decision": "completed"}},
                "sessions": {"s1": {"status": "running", "started_at": _now_iso()}},
            }},
        )
        result = synthesize_welcome_state(state)
        assert result["state_id"] == "active"
        assert "matmul-sweep" in result["strip"]["label"]

    def test_state_2_recent_win(self):
        """An experiment completed recently with a positive result."""
        state = _make_state(
            projects={"exp1": {
                "id": "exp1",
                "name": "lr-warmup",
                "runs": {"r1": {"started_at": _days_ago(2), "decision": "best"}},
                "sessions": {},
            }},
        )
        result = synthesize_welcome_state(state)
        assert result["state_id"] == "recent_win"

    def test_state_3_stuck(self):
        """An experiment has been flat for 3+ runs (all outside the recent-win window)."""
        runs = {}
        for i in range(5):
            runs[f"r{i}"] = {
                "started_at": _days_ago(20 - i),
                "decision": "completed",
            }
        state = _make_state(
            projects={"exp1": {
                "id": "exp1",
                "name": "stuck-exp",
                "runs": runs,
                "sessions": {},
            }},
        )
        result = synthesize_welcome_state(state)
        assert result["state_id"] == "stuck"

    def test_active_beats_recent_win(self):
        """Active experiment takes priority over recent win."""
        state = _make_state(
            projects={
                "exp1": {
                    "id": "exp1", "name": "running",
                    "runs": {},
                    "sessions": {"s1": {"status": "running"}},
                },
                "exp2": {
                    "id": "exp2", "name": "done",
                    "runs": {"r1": {"started_at": _days_ago(1), "decision": "best"}},
                    "sessions": {},
                },
            },
        )
        result = synthesize_welcome_state(state)
        assert result["state_id"] == "active"


class TestWelcomeStateSchema:
    """Test the return schema is complete and consistent."""

    def test_all_states_have_required_fields(self):
        """Every state should return greeting, strip, narration, suggestions, input_placeholder."""
        state = _make_state()
        result = synthesize_welcome_state(state, user_name="Romain")

        assert "state_id" in result
        assert "greeting" in result
        assert "strip" in result
        assert "narration_paragraphs" in result
        assert "suggestions" in result
        assert "input_placeholder" in result
        assert isinstance(result["narration_paragraphs"], list)
        assert isinstance(result["suggestions"], list)

    def test_greeting_includes_name(self):
        state = _make_state()
        result = synthesize_welcome_state(state, user_name="Romain")
        assert "Romain" in result["greeting"]

    def test_suggestions_have_prompt(self):
        state = _make_state()
        result = synthesize_welcome_state(state)
        for sg in result["suggestions"]:
            assert "label" in sg
            assert "prompt" in sg

    def test_strip_has_type(self):
        state = _make_state()
        result = synthesize_welcome_state(state)
        assert "type" in result["strip"]
