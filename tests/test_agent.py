"""Tests for distillate.agent — REPL and system prompt."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


class MockState:
    """Minimal State mock for agent testing."""

    def __init__(self, documents=None):
        self._documents = documents or {}

    @property
    def documents(self):
        return self._documents

    @property
    def promoted_papers(self):
        return []

    def documents_with_status(self, status):
        return [d for d in self._documents.values() if d["status"] == status]

    def documents_processed_since(self, since_iso):
        return sorted(
            [
                d for d in self._documents.values()
                if d["status"] == "processed" and (d.get("processed_at") or "") >= since_iso
            ],
            key=lambda d: d.get("processed_at", ""),
        )

    def reload(self):
        pass


def _make_doc(title="Test Paper", status="processed", tags=None, engagement=75):
    now = datetime.now(timezone.utc)
    return {
        "title": title,
        "status": status,
        "metadata": {"tags": tags or ["ML"], "citekey": "test2024"},
        "engagement": engagement,
        "highlight_count": 10,
        "processed_at": (now - timedelta(days=1)).isoformat(),
        "uploaded_at": (now - timedelta(days=5)).isoformat(),
    }


class TestBuildSystemPrompt:
    def test_includes_library_stats(self):
        from distillate.agent import _build_system_prompt
        state = MockState({
            "K1": _make_doc(status="processed"),
            "K2": _make_doc(title="Queue Paper", status="on_remarkable"),
        })
        prompt = _build_system_prompt(state)
        assert "1 papers read" in prompt
        assert "1 in queue" in prompt
        assert "Distillate" in prompt

    def test_includes_recent_reads(self):
        from distillate.agent import _build_system_prompt
        state = MockState({
            "K1": _make_doc(
                title="Fresh Paper",
            ),
        })
        prompt = _build_system_prompt(state)
        assert "Fresh Paper" in prompt

    def test_includes_tags(self):
        from distillate.agent import _build_system_prompt
        state = MockState({
            "K1": _make_doc(tags=["Deep Learning", "Transformers"]),
        })
        prompt = _build_system_prompt(state)
        # Tags should appear (from last 30 days processed papers)
        assert "Deep Learning" in prompt or "Transformers" in prompt

    def test_empty_library(self):
        from distillate.agent import _build_system_prompt
        state = MockState({})
        prompt = _build_system_prompt(state)
        assert "0 papers read" in prompt
        assert "none this week" in prompt


class TestExecuteTool:
    def test_dispatches_to_correct_function(self):
        from distillate.agent import _execute_tool
        state = MockState({"K1": _make_doc()})
        result = _execute_tool("get_reading_stats", {"period_days": 7}, state)
        assert "papers_read" in result

    def test_unknown_tool(self):
        from distillate.agent import _execute_tool
        state = MockState({})
        result = _execute_tool("nonexistent_tool", {}, state)
        assert "error" in result
        assert "Unknown tool" in result["error"]

    def test_tool_error_handled(self):
        from distillate.agent import _execute_tool
        state = MockState({})
        # search_papers with missing required arg
        with patch("distillate.tools.search_papers", side_effect=TypeError("missing arg")):
            result = _execute_tool("search_papers", {}, state)
        assert "error" in result


class TestConversationTrimming:
    def test_trims_when_too_long(self):
        from distillate.agent import (
            _CONVERSATION_KEEP,
            _CONVERSATION_TRIM_THRESHOLD,
        )
        # Verify the constants are sensible
        assert _CONVERSATION_TRIM_THRESHOLD > _CONVERSATION_KEEP
        assert _CONVERSATION_KEEP >= 10


class TestRunChat:
    def test_exits_without_api_key(self, monkeypatch):
        monkeypatch.setattr("distillate.agent.config.ANTHROPIC_API_KEY", "")
        from distillate.agent import run_chat
        with pytest.raises(SystemExit):
            run_chat()

    def test_exits_without_anthropic_package(self, monkeypatch):
        monkeypatch.setattr("distillate.agent.config.ANTHROPIC_API_KEY", "sk-test")

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from distillate.agent import run_chat
        with pytest.raises(SystemExit):
            run_chat()


class TestPrintWelcome:
    def test_welcome_output(self, capsys):
        from distillate.agent import _print_welcome
        state = MockState({
            "K1": _make_doc(status="processed"),
            "K2": _make_doc(title="Q", status="on_remarkable"),
            "K3": _make_doc(title="Q2", status="on_remarkable"),
        })
        _print_welcome(state)
        output = capsys.readouterr().out
        assert "1 papers read" in output
        assert "2 in queue" in output
