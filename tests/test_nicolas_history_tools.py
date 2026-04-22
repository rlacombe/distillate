# Covers: distillate/server.py, distillate/routes/nicolas.py
"""Tests for /nicolas/sessions/{id}/history tool-turn extraction.

Guards the extension that makes tool indicators survive session switching:
the endpoint now returns tool turns inline with text turns, preserving
the ordering from Claude Code's .jsonl session store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper: build a fake ~/.claude/projects/<slug>/<session_id>.jsonl
# ---------------------------------------------------------------------------

def _write_jsonl(claude_home: Path, session_id: str, entries: list[dict]) -> Path:
    projects = claude_home / ".claude" / "projects" / "some-project"
    projects.mkdir(parents=True, exist_ok=True)
    path = projects / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return path


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() so the server endpoint reads our temp dir."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def client():
    """FastAPI TestClient against a server instance that bypasses Nicolas."""
    from fastapi.testclient import TestClient
    from distillate.server import _create_app, _register_app_state
    from distillate.state import State

    state = State()
    _register_app_state(state)

    class FakeNicolas:
        def list_sessions(self):
            return []

        @property
        def session_id(self):
            return None

        async def new_conversation(self):
            pass

        async def switch_session(self, sid):
            pass

        def rename_session(self, sid, name):
            return True

    # Patch _get_nicolas for the lifetime of this test so we don't need
    # the full NicolasClient setup.
    from distillate import server
    server._nicolas_singleton = FakeNicolas()

    app = _create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# History endpoint — tool-turn extraction
# ---------------------------------------------------------------------------

class TestHistoryToolTurns:

    def test_empty_when_no_jsonl_for_session(self, client, fake_home):
        resp = client.get("/nicolas/sessions/nonexistent-id/history")
        assert resp.status_code == 200
        assert resp.json() == {"turns": []}

    def test_text_only_turns(self, client, fake_home):
        _write_jsonl(fake_home, "s1", [
            {"message": {"role": "user", "content": "hello"}},
            {"message": {"role": "assistant", "content": [
                {"type": "text", "text": "Hi there"},
            ]}},
        ])
        data = client.get("/nicolas/sessions/s1/history").json()
        assert data["turns"] == [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "Hi there"},
        ]

    def test_tool_use_emits_tool_turn(self, client, fake_home):
        _write_jsonl(fake_home, "s2", [
            {"message": {"role": "user", "content": "find glycan papers"}},
            {"message": {"role": "assistant", "content": [
                {"type": "text", "text": "Let me search"},
                {
                    "type": "tool_use",
                    "id": "tu-1",
                    "name": "mcp__distillate__search_papers",
                    "input": {"query": "glycan"},
                },
            ]}},
            {"message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu-1", "content": "Found 3"},
            ]}},
            {"message": {"role": "assistant", "content": [
                {"type": "text", "text": "Found 3 papers."},
            ]}},
        ])
        turns = client.get("/nicolas/sessions/s2/history").json()["turns"]

        # Expected inline ordering:
        #   user → assistant text → tool → assistant text
        assert [t["role"] for t in turns] == [
            "user", "assistant", "tool", "assistant",
        ]
        tool = turns[2]
        # MCP server prefix stripped for the renderer
        assert tool["name"] == "search_papers"
        assert tool["input"] == {"query": "glycan"}
        assert tool["tool_use_id"] == "tu-1"
        assert tool["is_error"] is False

    def test_tool_use_marked_error_when_result_is_error(self, client, fake_home):
        _write_jsonl(fake_home, "s3", [
            {"message": {"role": "user", "content": "do thing"}},
            {"message": {"role": "assistant", "content": [
                {
                    "type": "tool_use",
                    "id": "tu-err",
                    "name": "Bash",
                    "input": {"command": "exit 1"},
                },
            ]}},
            {"message": {"role": "user", "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu-err",
                    "content": "exit 1",
                    "is_error": True,
                },
            ]}},
        ])
        turns = client.get("/nicolas/sessions/s3/history").json()["turns"]
        tool = next(t for t in turns if t["role"] == "tool")
        assert tool["name"] == "Bash"
        assert tool["is_error"] is True

    def test_tool_result_only_user_messages_are_skipped(self, client, fake_home):
        """A user message whose content is purely tool_result blocks must
        NOT emit a standalone user turn — the tool_use has already been
        emitted from the assistant side."""
        _write_jsonl(fake_home, "s4", [
            {"message": {"role": "user", "content": "q"}},
            {"message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x"}},
            ]}},
            {"message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "file"},
            ]}},
        ])
        turns = client.get("/nicolas/sessions/s4/history").json()["turns"]
        roles = [t["role"] for t in turns]
        # Only ONE user turn (the actual question), then tool — NOT user→tool→user.
        assert roles == ["user", "tool"]

    def test_preserves_inline_ordering_text_tool_text(self, client, fake_home):
        _write_jsonl(fake_home, "s5", [
            {"message": {"role": "user", "content": "start"}},
            {"message": {"role": "assistant", "content": [
                {"type": "text", "text": "first thought"},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                {"type": "text", "text": "second thought"},
                {"type": "tool_use", "id": "t2", "name": "Grep", "input": {}},
                {"type": "text", "text": "third thought"},
            ]}},
        ])
        turns = client.get("/nicolas/sessions/s5/history").json()["turns"]
        assert [t["role"] for t in turns] == [
            "user", "assistant", "tool", "assistant", "tool", "assistant",
        ]
        assert turns[1]["text"] == "first thought"
        assert turns[2]["name"] == "Read"
        assert turns[3]["text"] == "second thought"
        assert turns[4]["name"] == "Grep"
        assert turns[5]["text"] == "third thought"

    def test_malformed_lines_do_not_crash(self, client, fake_home):
        projects = fake_home / ".claude" / "projects" / "p"
        projects.mkdir(parents=True)
        (projects / "s6.jsonl").write_text(
            '{"message": {"role": "user", "content": "ok"}}\n'
            "not json at all\n"
            '{"message": {"role": "assistant", "content": [{"type": "text", "text": "response"}]}}\n'
        )
        turns = client.get("/nicolas/sessions/s6/history").json()["turns"]
        assert [t["role"] for t in turns] == ["user", "assistant"]
        assert turns[0]["text"] == "ok"
        assert turns[1]["text"] == "response"

    def test_tool_input_defaults_to_empty_dict_when_missing(self, client, fake_home):
        _write_jsonl(fake_home, "s7", [
            {"message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "list_experiments"},
            ]}},
        ])
        turns = client.get("/nicolas/sessions/s7/history").json()["turns"]
        tool = next(t for t in turns if t["role"] == "tool")
        assert tool["input"] == {}
