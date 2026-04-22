# Covers: desktop/renderer/styles.css, desktop/renderer/workspaces.js,
#          desktop/renderer/agents-ui.js, distillate/experiment_tools/workspace_tools.py

"""Tests for the sidebar status dot renderer contract (CSS + JS + tool shape).

The status dot system has regressed twice before (CSS keyframe hollowed out,
agents sidebar left behind on a dumb static dot). These tests freeze the
contract so a future refactor fails loudly instead of silently going stale.
"""

import pytest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CSS_PATH = _REPO_ROOT / "desktop" / "renderer" / "styles.css"
_PROJECTS_JS = _REPO_ROOT / "desktop" / "renderer" / "workspaces.js"
_AGENTS_JS = _REPO_ROOT / "desktop" / "renderer" / "agents-ui.js"

_EXPECTED_STATES = ("working", "waiting", "idle", "lost", "completed", "unknown")


class TestStatusDotCssContract:
    """Freeze the CSS contract for session status dots."""

    @pytest.fixture(scope="class")
    def css(self):
        return _CSS_PATH.read_text(encoding="utf-8")

    def test_text_keyframes_exist(self, css):
        # Text ● glyphs need text-shadow (box-shadow has no effect on them).
        assert "@keyframes green-breathe-text" in css
        assert "@keyframes amber-breathe-text" in css

    def test_dot_keyframes_exist(self, css):
        # CSS-circle dots need box-shadow for the actual breathing halo.
        assert "@keyframes green-breathe-dot" in css
        assert "@keyframes amber-breathe-dot" in css

    def test_green_dot_keyframe_uses_box_shadow(self, css):
        # Regression: the keyframe was once rewritten to text-shadow only,
        # which silently killed the glow on .sidebar-status-dot circles.
        import re as _re
        m = _re.search(
            r"@keyframes green-breathe-dot\s*\{(.*?)\}\s*(?:@|\.)",
            css,
            _re.DOTALL,
        )
        assert m is not None, "green-breathe-dot keyframe missing"
        body = m.group(1)
        assert "box-shadow" in body, "green-breathe-dot must use box-shadow for a visible halo"

    def test_all_icon_state_classes_exist(self, css):
        for state in _EXPECTED_STATES:
            assert f".sidebar-status-icon.status-{state}" in css, \
                f"missing .sidebar-status-icon.status-{state}"

    def test_working_icon_is_animated(self, css):
        assert "status-working" in css
        assert "animation: green-breathe-text" in css

    def test_waiting_icon_is_animated(self, css):
        # Users scan the sidebar for "needs your input" — motion matters here.
        assert "animation: amber-breathe-text" in css

    def test_working_dot_is_animated(self, css):
        assert "animation: green-breathe-dot" in css

    def test_waiting_dot_is_animated(self, css):
        assert "animation: amber-breathe-dot" in css


class TestStatusDotJsContract:
    """Freeze the JS contract: both sidebars map every state and poll live."""

    @pytest.fixture(scope="class")
    def projects_js(self):
        return _PROJECTS_JS.read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def agents_js(self):
        return _AGENTS_JS.read_text(encoding="utf-8")

    def test_projects_js_maps_every_state(self, projects_js):
        for state in _EXPECTED_STATES:
            assert f'"{state}"' in projects_js or f"'{state}'" in projects_js, \
                f"workspaces.js should handle status '{state}'"

    def test_projects_js_polls_agent_status_endpoint(self, projects_js):
        assert "/workspaces/agent-status" in projects_js

    def test_projects_js_has_both_updater_functions(self, projects_js):
        # Sessions and agents use different DOM shapes and keys — they
        # need distinct updater functions or agents will silently no-op.
        assert "_updateSessionDotsInContainer" in projects_js
        assert "_updateAgentDotsInContainer" in projects_js

    def test_agents_sidebar_uses_rich_status_icon(self, agents_js):
        # Regression: agents-ui.js used to render a dumb static `sidebar-live-dot`
        # for running agents and never picked up waiting/idle/lost states.
        assert "sidebar-status-icon" in agents_js, \
            "agents-ui.js must render sidebar-status-icon so the poll loop can update state"

    def test_agents_sidebar_exposes_agent_id_for_polling(self, agents_js):
        # The poll loop matches agent items by [data-agent-id].
        assert "data-agent-id" in agents_js


class TestAgentStatusToolReturnShape:
    """`agent_status_tool` must return both sessions and agents buckets
    so the poll loop can update coding sessions and long-lived agents."""

    def test_return_shape_has_both_keys(self):
        # Minimal stub state: no sessions, no agents, no tmux calls needed.
        class _StubState:
            workspaces: dict = {}
            agents: dict = {}
            def save(self):
                pass

        from distillate.experiment_tools.workspace_tools import agent_status_tool
        result = agent_status_tool(state=_StubState())
        assert "sessions" in result
        assert "agents" in result
        assert isinstance(result["sessions"], dict)
        assert isinstance(result["agents"], dict)
