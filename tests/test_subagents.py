# Covers: distillate/agent_runtime/subagent.py, distillate/agent_runtime/breadcrumbs.py
"""Tests for Tier 2 sub-agent infrastructure."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from distillate.agent_runtime.subagent import (
    SubAgentContext,
    SubAgentResult,
    get_subagent,
    list_subagents,
)
from distillate.agent_runtime.breadcrumbs import (
    emit,
    make_emitter,
    flush,
    add_listener,
    remove_listener,
)


class TestSubAgentRegistry:

    def test_librarian_registered(self):
        """Librarian is auto-registered on import."""
        sa = get_subagent("librarian")
        assert sa is not None
        assert sa.name == "librarian"
        assert sa.label == "Librarian"
        assert sa.icon == "\U0001F4DA"

    def test_knowledge_agent_registered(self):
        sa = get_subagent("knowledge_agent")
        assert sa is not None
        assert sa.name == "knowledge_agent"

    def test_research_agent_registered(self):
        sa = get_subagent("research_agent")
        assert sa is not None
        assert sa.name == "research_agent"

    def test_list_subagents(self):
        agents = list_subagents()
        names = {a["name"] for a in agents}
        assert "librarian" in names
        assert "knowledge_agent" in names
        assert "research_agent" in names

    def test_unknown_subagent_returns_none(self):
        assert get_subagent("nonexistent") is None

    def test_subagent_has_tools(self):
        sa = get_subagent("librarian")
        assert isinstance(sa.tools, list)
        assert "search_papers" in sa.tools


class TestBreadcrumbs:

    def setup_method(self):
        flush()  # Clear pending breadcrumbs

    def test_emit_creates_breadcrumb(self):
        bc = emit("librarian", "\U0001F4DA", "testing...")
        assert bc.agent_name == "librarian"
        assert bc.message == "testing..."
        assert bc.timestamp  # Not empty

    def test_flush_returns_and_clears(self):
        emit("librarian", "\U0001F4DA", "msg1")
        emit("librarian", "\U0001F4DA", "msg2")
        pending = flush()
        assert len(pending) == 2
        assert flush() == []  # Now empty

    def test_make_emitter(self):
        emitter = make_emitter("research_agent", "\uD83D\uDD0D")
        emitter("searching...")
        pending = flush()
        assert len(pending) == 1
        assert pending[0].agent_name == "research_agent"
        assert pending[0].message == "searching..."

    def test_listener_called(self):
        received = []
        def listener(bc):
            received.append(bc)
        add_listener(listener)
        try:
            emit("librarian", "\U0001F4DA", "test")
            assert len(received) == 1
            assert received[0].message == "test"
        finally:
            remove_listener(listener)
            flush()


class TestSubAgentContext:

    def test_default_values(self):
        ctx = SubAgentContext()
        assert ctx.parent_session_id == ""
        assert ctx.experiment_id is None
        assert ctx.max_tokens == 4096

    def test_custom_values(self):
        ctx = SubAgentContext(
            parent_session_id="sess-123",
            experiment_id="proj-456",
            user_intent="summarize my papers",
        )
        assert ctx.parent_session_id == "sess-123"
        assert ctx.user_intent == "summarize my papers"


class TestSubAgentResult:

    def test_default_success(self):
        r = SubAgentResult(summary="Done")
        assert r.success is True
        assert r.error is None
        assert r.summary == "Done"

    def test_error_result(self):
        r = SubAgentResult(success=False, error="something broke")
        assert r.success is False
        assert r.error == "something broke"
