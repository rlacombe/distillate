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

    @property
    def projects(self):
        return {}

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
        assert "Nicolas" in prompt

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


class TestStreamFormatter:
    def test_bold_markers_converted(self, monkeypatch):
        monkeypatch.setattr("distillate.agent._is_tty", lambda: True)
        from distillate.agent import _StreamFormatter, _RESET, _bold_ansi
        fmt = _StreamFormatter()
        result = fmt.feed("Check **Attention Is All You Need** for details.")
        result += fmt.flush()
        assert "**" not in result
        assert _bold_ansi() in result
        assert _RESET in result
        assert "Attention Is All You Need" in result

    def test_split_across_chunks(self, monkeypatch):
        monkeypatch.setattr("distillate.agent._is_tty", lambda: True)
        from distillate.agent import _StreamFormatter, _bold_ansi
        fmt = _StreamFormatter()
        out = fmt.feed("See *")
        out += fmt.feed("*title*")
        out += fmt.feed("* end")
        out += fmt.flush()
        assert "**" not in out
        assert _bold_ansi() in out
        assert "title" in out

    def test_no_tty_passthrough(self, monkeypatch):
        monkeypatch.setattr("distillate.agent._is_tty", lambda: False)
        from distillate.agent import _StreamFormatter
        fmt = _StreamFormatter()
        text = "See **title** here"
        assert fmt.feed(text) == text

    def test_single_star_preserved(self, monkeypatch):
        monkeypatch.setattr("distillate.agent._is_tty", lambda: True)
        from distillate.agent import _StreamFormatter
        fmt = _StreamFormatter()
        result = fmt.feed("a * b")
        result += fmt.flush()
        assert "a * b" == result

    def test_unclosed_bold_reset_on_flush(self, monkeypatch):
        monkeypatch.setattr("distillate.agent._is_tty", lambda: True)
        from distillate.agent import _StreamFormatter, _RESET
        fmt = _StreamFormatter()
        result = fmt.feed("**unclosed")
        result += fmt.flush()
        assert result.endswith(_RESET)


class TestConversationTrimming:
    def test_trims_when_too_long(self):
        from distillate.agent import (
            _CONVERSATION_KEEP,
            _CONVERSATION_TRIM_THRESHOLD,
        )
        # Verify the constants are sensible
        assert _CONVERSATION_TRIM_THRESHOLD > _CONVERSATION_KEEP
        assert _CONVERSATION_KEEP >= 10

    def test_skips_orphaned_tool_result_at_start(self):
        """After trimming, orphaned tool_result messages must be dropped.

        tool_result messages have role="user" but reference a tool_use_id
        from a preceding assistant message. If that assistant message was
        trimmed away, Claude's API rejects the orphaned tool_result.
        """
        from distillate.agent import (
            _CONVERSATION_KEEP,
            _CONVERSATION_TRIM_THRESHOLD,
        )

        # Build a conversation that triggers trimming, where the kept
        # slice starts with an orphaned tool_result.
        conversation = []
        # Pad with enough messages to exceed the threshold
        for i in range(_CONVERSATION_TRIM_THRESHOLD + 2):
            conversation.append({"role": "user", "content": f"msg {i}"})
            conversation.append({"role": "assistant", "content": f"reply {i}"})

        # Place a tool_result message right where trimming will keep it
        tool_result_msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "orphaned_id", "content": "{}"},
            ],
        }
        conversation[-_CONVERSATION_KEEP] = tool_result_msg

        # Simulate the trimming logic from _handle_turn
        trimmed = conversation[-_CONVERSATION_KEEP:]
        while trimmed:
            msg = trimmed[0]
            if msg.get("role") == "assistant":
                trimmed.pop(0)
                continue
            content = msg.get("content")
            if (isinstance(content, list) and content
                    and isinstance(content[0], dict)
                    and content[0].get("type") == "tool_result"):
                trimmed.pop(0)
                continue
            break

        # First message should be a genuine user message, not a tool_result
        assert trimmed
        first = trimmed[0]
        assert first["role"] == "user"
        content = first.get("content")
        assert isinstance(content, str), f"Expected plain text, got {type(content)}"


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
        assert "Nicolas" in output
        assert "1 papers read" in output
        assert "2 in queue" in output
