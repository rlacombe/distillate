# Covers: distillate/agents.py
"""Tests for long-lived agents primitive and agent templates."""

import shutil
import subprocess
from pathlib import Path

import pytest

from distillate.agents import ensure_agent_dir, get_agent_config_dir
from distillate.agent_templates import BUILTIN_TEMPLATES, get_template, list_all_templates
from distillate.experiment_tools.agent_tools import (
    _check_tmux_alive,
    create_agent_tool,
    delete_agent_tool,
    get_agent_details_tool,
    list_agent_templates_tool,
    list_agents_tool,
    start_agent_session_tool,
    stop_agent_session_tool,
    update_agent_tool,
)
from distillate.state import State


@pytest.fixture
def state():
    """Return a fresh State instance with Nicolas bootstrapped."""
    s = State()
    if not s.get_agent("nicolas"):
        s.add_agent("nicolas", "Nicolas", agent_type="nicolas", builtin=True)
        s.save()
    return s


@pytest.fixture(autouse=True)
def cleanup_test_agents(state):
    """Remove test agents after each test."""
    yield
    for aid in list(state.agents.keys()):
        if aid.startswith("test") or aid in ("tooltest", "tmuxtest", "recontest"):
            # Kill tmux if running
            tmux = state.agents[aid].get("tmux_name", "")
            if tmux:
                subprocess.run(["tmux", "kill-session", "-t", tmux], capture_output=True)
            # Remove config dir
            config_dir = state.agents[aid].get("config_dir", "")
            if config_dir:
                shutil.rmtree(config_dir, ignore_errors=True)
            state.remove_agent(aid)
    state.save()


# ── State layer ──────────────────────────────────────────────────────────────


class TestStateLayer:
    def test_add_agent(self, state):
        agent = state.add_agent("test-crud", "Test Agent", working_dir="/tmp", command="echo hi")
        assert agent["id"] == "test-crud"
        assert agent["name"] == "Test Agent"
        assert agent["command"] == "echo hi"
        assert agent["session_status"] == "stopped"

    def test_get_agent(self, state):
        state.add_agent("test-get", "Get Test")
        a = state.get_agent("test-get")
        assert a is not None
        assert a["name"] == "Get Test"

    def test_update_agent(self, state):
        state.add_agent("test-upd", "Before")
        state.update_agent("test-upd", name="After", model="claude-sonnet-4-6")
        a = state.get_agent("test-upd")
        assert a["name"] == "After"
        assert a["model"] == "claude-sonnet-4-6"

    def test_remove_agent(self, state):
        state.add_agent("test-rm", "Remove Me")
        assert state.remove_agent("test-rm") is True
        assert state.get_agent("test-rm") is None

    def test_remove_nonexistent(self, state):
        assert state.remove_agent("no-such-agent") is False

    def test_nicolas_bootstrap(self, state):
        nicolas = state.get_agent("nicolas")
        assert nicolas is not None
        assert nicolas["builtin"] is True
        assert nicolas["agent_type"] == "nicolas"


# ── Config directory helpers ─────────────────────────────────────────────────


class TestConfigDir:
    def test_get_agent_config_dir(self):
        d = get_agent_config_dir("test-dir")
        assert "agents/test-dir" in str(d)

    def test_ensure_agent_dir(self):
        d = ensure_agent_dir("test-ensure", "Test Ensure", "You are a test agent.")
        try:
            assert d.exists()
            claude_md = d / "CLAUDE.md"
            assert claude_md.exists()
            content = claude_md.read_text()
            assert "Test Ensure" in content
            assert "You are a test agent." in content
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ── Tool functions ───────────────────────────────────────────────────────────


class TestToolFunctions:
    def test_create_agent(self, state):
        result = create_agent_tool(state=state, name="ToolTest", personality="A test personality")
        assert result["success"]
        assert result["agent"]["id"] == "tooltest"

    def test_create_duplicate(self, state):
        create_agent_tool(state=state, name="TestDup")
        result = create_agent_tool(state=state, name="TestDup")
        assert not result["success"]
        assert "already exists" in result["error"]

    def test_list_agents(self, state):
        create_agent_tool(state=state, name="TestList")
        result = list_agents_tool(state=state)
        assert result["success"]
        names = [a["name"] for a in result["agents"]]
        assert "TestList" in names
        assert "Nicolas" in names

    def test_get_details_includes_claude_md(self, state):
        create_agent_tool(state=state, name="TestDetails", personality="test personality content")
        result = get_agent_details_tool(state=state, agent="testdetails")
        assert result["success"]
        assert "claude_md" in result["agent"]
        assert "test personality content" in result["agent"]["claude_md"].lower()

    def test_update_agent(self, state):
        create_agent_tool(state=state, name="TestUpd")
        result = update_agent_tool(state=state, agent="testupd", name="Renamed", model="claude-haiku-4-5")
        assert result["success"]
        a = state.get_agent("testupd")
        assert a["name"] == "Renamed"
        assert a["model"] == "claude-haiku-4-5"

    def test_delete_builtin_guard(self, state):
        result = delete_agent_tool(state=state, agent="nicolas")
        assert not result["success"]
        assert "built-in" in result["error"]

    def test_delete_agent(self, state):
        create_agent_tool(state=state, name="TestDel")
        result = delete_agent_tool(state=state, agent="testdel")
        assert result["success"]
        assert state.get_agent("testdel") is None

    def test_agent_not_found(self, state):
        result = get_agent_details_tool(state=state, agent="nonexistent")
        assert not result["success"]
        assert "not found" in result["error"].lower()


# ── Schema registration ─────────────────────────────────────────────────────


class TestSchemas:
    def test_schemas_registered(self):
        from distillate.agent_core import TOOL_LABELS
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS

        agent_tools = [s["name"] for s in EXPERIMENT_TOOL_SCHEMAS if "agent" in s["name"]]
        expected = [
            "create_agent", "list_agents", "start_agent_session",
            "stop_agent_session", "update_agent", "delete_agent",
        ]
        for e in expected:
            assert e in agent_tools, f"Missing schema: {e}"
            assert e in TOOL_LABELS, f"Missing label: {e}"


# ── tmux session management ─────────────────────────────────────────────────


class TestTmuxSessions:
    def test_check_tmux_dead(self):
        assert _check_tmux_alive("nonexistent-xyz-999") is False

    def test_start_and_stop(self, state):
        create_agent_tool(state=state, name="TmuxTest", command="sleep 30")
        result = start_agent_session_tool(state=state, agent="tmuxtest")
        assert result["success"]
        assert result["tmux_name"] == "agent-tmuxtest"
        assert _check_tmux_alive("agent-tmuxtest")

        # Already running guard
        result2 = start_agent_session_tool(state=state, agent="tmuxtest")
        assert result2["success"]
        assert result2.get("already_running")

        # Stop
        result3 = stop_agent_session_tool(state=state, agent="tmuxtest")
        assert result3["success"]
        assert not _check_tmux_alive("agent-tmuxtest")
        assert state.get_agent("tmuxtest")["session_status"] == "stopped"

    def test_nicolas_rejects_terminal(self, state):
        result = start_agent_session_tool(state=state, agent="nicolas")
        assert not result["success"]
        assert "chat panel" in result["error"]


# ── Session reconciliation ───────────────────────────────────────────────────


class TestReconciliation:
    def test_detects_orphaned_tmux(self, state):
        create_agent_tool(state=state, name="ReconTest")
        state.update_agent("recontest", session_status="stopped", tmux_name="")
        state.save()

        # Manually spawn tmux session (simulates surviving restart)
        subprocess.run(
            'tmux new-session -d -s agent-recontest "sleep 30"',
            shell=True, check=True,
        )
        try:
            result = list_agents_tool(state=state)
            recon = next(a for a in result["agents"] if a["id"] == "recontest")
            assert recon["session_status"] == "running"
            assert recon["tmux_name"] == "agent-recontest"
        finally:
            subprocess.run(["tmux", "kill-session", "-t", "agent-recontest"], capture_output=True)


# ── Agent templates ──────────────────────────────────────────────────────────


class TestTemplates:
    def test_builtin_count(self):
        assert len(BUILTIN_TEMPLATES) == 12

    def test_get_template(self):
        t = get_template("read-papers")
        assert t is not None
        assert t.name == "Read papers"
        assert "search_papers" in t.relevant_tools

    def test_get_nonexistent(self):
        assert get_template("nonexistent") is None

    def test_list_all(self):
        templates = list_all_templates()
        assert len(templates) == 12
        ids = [t["id"] for t in templates]
        assert "read-papers" in ids
        assert "draft-write-up" in ids
        assert "check-results" in ids
        assert "find-papers" in ids
        assert "watch-experiments" in ids

    def test_list_templates_tool(self, state):
        result = list_agent_templates_tool(state=state)
        assert result["success"]
        assert len(result["templates"]) == 12
        # No full personality in list output
        assert "personality" not in result["templates"][0]

    def test_list_templates_category_filter(self, state):
        result = list_agent_templates_tool(state=state, category="research")
        assert result["success"]
        assert len(result["templates"]) == 4  # read-papers, find-papers, compare-papers, literature-review
        categories = {t["category"] for t in result["templates"]}
        assert categories == {"research"}

    def test_create_from_template(self, state):
        result = create_agent_tool(state=state, name="TestReader", template="read-papers")
        assert result["success"]
        a = state.get_agent("testreader")
        assert a is not None
        claude_md = Path(a["config_dir"]) / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text()
        assert "Paper Reader" in content
        assert "search_papers" in content

    def test_template_personality_override(self, state):
        result = create_agent_tool(
            state=state, name="TestOverride",
            template="read-papers", personality="Custom personality only"
        )
        assert result["success"]
        a = state.get_agent("testoverride")
        claude_md = Path(a["config_dir"]) / "CLAUDE.md"
        content = claude_md.read_text()
        assert "Custom personality only" in content
        assert "deep paper analysis" not in content.lower()

    def test_create_unknown_template(self, state):
        result = create_agent_tool(state=state, name="TestBad", template="nonexistent")
        assert not result["success"]
        assert "Unknown template" in result["error"]

    def test_template_categories(self):
        categories = {t.category for t in BUILTIN_TEMPLATES.values()}
        assert categories == {"research", "writing", "analysis", "monitoring"}

    def test_all_templates_have_personality(self):
        for t in BUILTIN_TEMPLATES.values():
            assert len(t.personality) > 100, f"{t.id} personality too short"


# ── Nicolas context awareness ────────────────────────────────────────────────


class TestNicolasContext:
    def test_agents_in_context(self, state):
        create_agent_tool(state=state, name="TestCtx")
        from distillate.agent_sdk import _build_dynamic_context
        ctx = _build_dynamic_context(state)
        assert "TestCtx" in ctx
        # Nicolas should not appear in his own context
        assert "\n- Nicolas" not in ctx

    def test_template_guidelines_in_context(self, state):
        from distillate.agent_sdk import _build_dynamic_context
        ctx = _build_dynamic_context(state)
        assert "start_agent_session" in ctx
        assert "initial_task" in ctx
        assert "specialist" in ctx.lower()

    def test_schema_registered(self):
        from distillate.agent_core import TOOL_LABELS
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        names = [s["name"] for s in EXPERIMENT_TOOL_SCHEMAS]
        assert "list_agent_templates" in names
        assert "list_agent_templates" in TOOL_LABELS
