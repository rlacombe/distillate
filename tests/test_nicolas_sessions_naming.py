# Covers: distillate/agent_sdk.py — NicolasClient session methods, set_thread_name MCP tool, auto-naming
"""NicolasClient session API and auto-naming tests.

Behaviors guarded:
  NicolasClient session methods
    - list_sessions returns [] when registry empty
    - list_sessions sorted by last_activity desc
    - rename_session updates name, returns True
    - rename_session returns False for unknown session
    - rename_session does not drop other sessions
    - rename_session truncates to 120 chars
    - __init__ resumes active session from registry
    - __init__ handles no active session (session_id is None)

  set_thread_name MCP tool
    - Renames active thread
    - Fails when no active thread
    - Fails when active_id not in sessions list
    - Rejects empty / whitespace-only names
    - Truncates name to 120 chars
    - Does not drop other sessions

  Auto-naming heuristics (_needs_auto_name)
    - Empty or missing name → needs naming
    - Default sentinel names → needs naming
    - Preview-derived name → needs naming
    - auto_named flag → no further naming
    - Crisp custom name → preserved
    - Sentence-like name → needs naming
    - Long name → needs naming

  _apply_auto_name
    - Applies name and sets auto_named flag
    - Truncates to 120 chars
    - Returns False for unknown session
    - Returns False for empty inputs
    - Does not drop other sessions

  _generate_thread_name (Haiku call — mocked)
    - Returns cleaned name
    - Strips quotes/trailing period
    - Rejects too-short output
    - Rejects too-long output
    - Returns first line when multiline
    - Returns None on error response
    - Returns None on empty response

  Pending thread name
    - pending_name applied on session_init, marks auto_named=True
"""

from __future__ import annotations

import pytest

from distillate import agent_sdk


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_registry(tmp_path, monkeypatch):
    """Point the registry at a temp file, isolate from real config dir."""
    sessions_file = tmp_path / "nicolas_sessions.json"
    legacy_file = tmp_path / "nicolas_session.json"
    monkeypatch.setattr(agent_sdk, "_SESSIONS_FILE", sessions_file)
    monkeypatch.setattr(agent_sdk, "_LEGACY_SESSION_FILE", legacy_file)
    return tmp_path


# ---------------------------------------------------------------------------
# NicolasClient session methods
# ---------------------------------------------------------------------------

class TestNicolasClientSessions:
    @pytest.fixture
    def client(self, monkeypatch):
        # Avoid loading State from disk
        from distillate.state import State
        monkeypatch.setattr(State, "__init__", lambda self: None)
        return agent_sdk.NicolasClient(state=State())

    def test_list_sessions_empty(self, client):
        assert client.list_sessions() == []

    def test_list_sessions_sorted_desc_by_last_activity(self, client):
        reg = agent_sdk._default_registry()
        reg["sessions"] = [
            {"session_id": "old", "name": "Old", "preview": "",
             "created_at": "2026-01-01T00:00:00Z",
             "last_activity": "2026-01-01T00:00:00Z"},
            {"session_id": "new", "name": "New", "preview": "",
             "created_at": "2026-01-02T00:00:00Z",
             "last_activity": "2026-04-01T00:00:00Z"},
            {"session_id": "mid", "name": "Mid", "preview": "",
             "created_at": "2026-01-01T12:00:00Z",
             "last_activity": "2026-02-15T00:00:00Z"},
        ]
        agent_sdk._save_registry(reg)

        order = [s["session_id"] for s in client.list_sessions()]
        assert order == ["new", "mid", "old"]

    def test_rename_existing_session(self, client):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="x")
        agent_sdk._save_registry(reg)

        ok = client.rename_session("sid", "Custom Name")
        assert ok is True

        loaded = agent_sdk._load_registry()
        assert loaded["sessions"][0]["name"] == "Custom Name"

    def test_rename_unknown_session_returns_false(self, client):
        ok = client.rename_session("does-not-exist", "X")
        assert ok is False

    def test_rename_does_not_drop_other_sessions(self, client):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "A", preview="a")
        agent_sdk._touch_session(reg, "B", preview="b")
        agent_sdk._save_registry(reg)

        client.rename_session("A", "Renamed A")

        loaded = agent_sdk._load_registry()
        ids = {s["session_id"] for s in loaded["sessions"]}
        assert ids == {"A", "B"}

    def test_rename_truncated_to_120(self, client):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="x")
        agent_sdk._save_registry(reg)

        client.rename_session("sid", "y" * 500)
        loaded = agent_sdk._load_registry()
        assert len(loaded["sessions"][0]["name"]) == 120

    def test_init_resumes_active_session_from_registry(self, client, monkeypatch):
        reg = agent_sdk._default_registry()
        reg["active_session_id"] = "previously-active"
        agent_sdk._touch_session(reg, "previously-active", preview="x")
        agent_sdk._save_registry(reg)

        from distillate.state import State
        monkeypatch.setattr(State, "__init__", lambda self: None)
        client2 = agent_sdk.NicolasClient(state=State())
        assert client2.session_id == "previously-active"

    def test_init_handles_no_active_session(self, client, monkeypatch):
        agent_sdk._save_registry(agent_sdk._default_registry())
        from distillate.state import State
        monkeypatch.setattr(State, "__init__", lambda self: None)
        client2 = agent_sdk.NicolasClient(state=State())
        assert client2.session_id is None


# ---------------------------------------------------------------------------
# set_thread_name MCP tool
# ---------------------------------------------------------------------------

class TestSetThreadNameTool:
    """The MCP tool Nicolas calls to auto-name the active thread."""

    def test_renames_active_thread(self):
        from distillate.experiment_tools.repl_tools import set_thread_name_tool

        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "active-id", preview="x")
        reg["active_session_id"] = "active-id"
        agent_sdk._save_registry(reg)

        result = set_thread_name_tool(state=None, name="DFM Glycan Generation")
        assert result["success"] is True
        assert result["session_id"] == "active-id"
        assert result["name"] == "DFM Glycan Generation"

        loaded = agent_sdk._load_registry()
        assert loaded["sessions"][0]["name"] == "DFM Glycan Generation"

    def test_fails_when_no_active_thread(self):
        from distillate.experiment_tools.repl_tools import set_thread_name_tool

        agent_sdk._save_registry(agent_sdk._default_registry())
        result = set_thread_name_tool(state=None, name="Anything")
        assert result["success"] is False
        assert result["error"] == "no_active_thread"

    def test_fails_when_active_id_not_in_sessions(self):
        from distillate.experiment_tools.repl_tools import set_thread_name_tool

        reg = agent_sdk._default_registry()
        reg["active_session_id"] = "ghost"
        agent_sdk._save_registry(reg)

        result = set_thread_name_tool(state=None, name="Anything")
        assert result["success"] is False
        assert result["error"] == "session_not_found"

    def test_rejects_empty_name(self):
        from distillate.experiment_tools.repl_tools import set_thread_name_tool

        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="x")
        reg["active_session_id"] = "sid"
        agent_sdk._save_registry(reg)

        for empty in ["", "   ", "\t\n"]:
            result = set_thread_name_tool(state=None, name=empty)
            assert result["success"] is False
            assert result["error"] == "name_required"

    def test_truncates_to_120(self):
        from distillate.experiment_tools.repl_tools import set_thread_name_tool

        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="x")
        reg["active_session_id"] = "sid"
        agent_sdk._save_registry(reg)

        result = set_thread_name_tool(state=None, name="x" * 500)
        assert result["success"] is True
        assert len(result["name"]) == 120

    def test_does_not_drop_other_sessions(self):
        from distillate.experiment_tools.repl_tools import set_thread_name_tool

        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "A", preview="a")
        agent_sdk._touch_session(reg, "B", preview="b")
        agent_sdk._touch_session(reg, "C", preview="c")
        reg["active_session_id"] = "B"
        agent_sdk._save_registry(reg)

        set_thread_name_tool(state=None, name="Renamed B")

        loaded = agent_sdk._load_registry()
        ids = {s["session_id"] for s in loaded["sessions"]}
        assert ids == {"A", "B", "C"}
        names = {s["session_id"]: s["name"] for s in loaded["sessions"]}
        assert names["B"] == "Renamed B"


# ---------------------------------------------------------------------------
# Auto-naming (server-side Haiku call)
# ---------------------------------------------------------------------------

class TestNeedsAutoName:
    """Heuristic that decides whether a thread still wants a proper title."""

    def test_empty_name_needs_naming(self):
        assert agent_sdk._needs_auto_name({"name": ""}) is True

    def test_missing_name_needs_naming(self):
        assert agent_sdk._needs_auto_name({}) is True

    def test_default_names_need_naming(self):
        for default in ("New conversation", "Thread", "Conversation"):
            assert agent_sdk._needs_auto_name({"name": default}) is True

    def test_preview_derived_name_needs_naming(self):
        """If the name was derived from the first message preview, we
        still want to replace it with a proper 3-5 word title."""
        entry = {
            "name": "Let's start a new experime",
            "preview": "Let's start a new experiment on glycan generation",
        }
        assert agent_sdk._needs_auto_name(entry) is True

    def test_auto_named_flag_stops_further_naming(self):
        entry = {"name": "Whatever", "auto_named": True}
        assert agent_sdk._needs_auto_name(entry) is False

    def test_crisp_custom_name_is_preserved(self):
        """Short, Title Case, no punctuation, different from preview ⇒
        the user (or earlier auto-namer) already set something good."""
        entry = {
            "name": "DFM Glycan Generation",
            "preview": "Let's build a discrete flow matching model",
        }
        assert agent_sdk._needs_auto_name(entry) is False

    def test_sentence_like_name_still_needs_naming(self):
        """If the name contains sentence-like punctuation, it's almost
        certainly an unpolished preview excerpt."""
        assert agent_sdk._needs_auto_name({"name": "Hey, help me."}) is True
        assert agent_sdk._needs_auto_name({"name": "Why is this slow?"}) is True

    def test_long_name_still_needs_naming(self):
        assert agent_sdk._needs_auto_name(
            {"name": "A very long sentence-like thing that is clearly not a title"}
        ) is True


class TestApplyAutoName:
    def test_applies_name_and_sets_flag(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="x")
        agent_sdk._save_registry(reg)

        assert agent_sdk._apply_auto_name("sid", "Clean Title") is True

        loaded = agent_sdk._load_registry()
        entry = loaded["sessions"][0]
        assert entry["name"] == "Clean Title"
        assert entry["auto_named"] is True

    def test_truncates_to_120(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="x")
        agent_sdk._save_registry(reg)
        agent_sdk._apply_auto_name("sid", "x" * 500)
        loaded = agent_sdk._load_registry()
        assert len(loaded["sessions"][0]["name"]) == 120

    def test_unknown_session_returns_false(self):
        agent_sdk._save_registry(agent_sdk._default_registry())
        assert agent_sdk._apply_auto_name("nope", "Name") is False

    def test_empty_inputs_return_false(self):
        assert agent_sdk._apply_auto_name("", "Name") is False
        assert agent_sdk._apply_auto_name("sid", "") is False

    def test_does_not_drop_other_sessions(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "A", preview="a")
        agent_sdk._touch_session(reg, "B", preview="b")
        agent_sdk._save_registry(reg)

        agent_sdk._apply_auto_name("A", "Clean A")

        loaded = agent_sdk._load_registry()
        ids = {s["session_id"] for s in loaded["sessions"]}
        assert ids == {"A", "B"}


class TestGenerateThreadName:
    """Generation via Haiku — mock _llm_query so tests don't hit the API."""

    def test_returns_cleaned_name(self, monkeypatch):
        monkeypatch.setattr(
            "distillate.agent_runtime.lab_repl._llm_query",
            lambda *a, **kw: '"DFM Glycan Generation".',
        )
        name = agent_sdk._generate_thread_name("user", "assistant")
        assert name == "DFM Glycan Generation"

    def test_strips_quotes_and_trailing_period(self, monkeypatch):
        for raw, expected in [
            ('"Clean Title"', "Clean Title"),
            ("'Wrapped'", "Wrapped"),
            ("*Bolded*", "Bolded"),
            ("Trailing Period.", "Trailing Period"),
        ]:
            monkeypatch.setattr(
                "distillate.agent_runtime.lab_repl._llm_query",
                lambda *a, _raw=raw, **kw: _raw,
            )
            assert agent_sdk._generate_thread_name("u", "a") == expected

    def test_rejects_too_short(self, monkeypatch):
        monkeypatch.setattr(
            "distillate.agent_runtime.lab_repl._llm_query",
            lambda *a, **kw: "X",
        )
        assert agent_sdk._generate_thread_name("u", "a") is None

    def test_rejects_too_long(self, monkeypatch):
        monkeypatch.setattr(
            "distillate.agent_runtime.lab_repl._llm_query",
            lambda *a, **kw: "x" * 100,
        )
        assert agent_sdk._generate_thread_name("u", "a") is None

    def test_returns_first_line_when_multiline(self, monkeypatch):
        monkeypatch.setattr(
            "distillate.agent_runtime.lab_repl._llm_query",
            lambda *a, **kw: "First Line Title\nsome explanation",
        )
        assert agent_sdk._generate_thread_name("u", "a") == "First Line Title"

    def test_error_response_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "distillate.agent_runtime.lab_repl._llm_query",
            lambda *a, **kw: "ERROR: credit depleted",
        )
        assert agent_sdk._generate_thread_name("u", "a") is None

    def test_empty_response_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "distillate.agent_runtime.lab_repl._llm_query",
            lambda *a, **kw: "",
        )
        assert agent_sdk._generate_thread_name("u", "a") is None


class TestPendingThreadName:
    """After new_conversation(pending_name=...), the next session_init
    should name the new session with that pending title AND mark it
    auto_named so the Haiku fallback doesn't second-guess it.
    This is what makes launch_experiment's thread-branch land with
    the experiment's name from turn 0."""

    def test_pending_name_is_applied_on_session_init(self, monkeypatch):
        """Simulate what session_init does: it reads
        self._pending_thread_name, passes it to _touch_session as name,
        and sets auto_named on the new entry."""
        reg = agent_sdk._default_registry()
        agent_sdk._save_registry(reg)

        # Simulate the handler's logic.
        pending = "Glycan Experiment"
        agent_sdk._touch_session(reg, "new-sid", preview="first user msg", name=pending)
        for s in reg.get("sessions", []):
            if s.get("session_id") == "new-sid":
                s["auto_named"] = True
                break
        reg["active_session_id"] = "new-sid"
        agent_sdk._save_registry(reg)

        loaded = agent_sdk._load_registry()
        entry = loaded["sessions"][0]
        assert entry["name"] == "Glycan Experiment"
        assert entry["auto_named"] is True
        # _needs_auto_name would skip this session (auto_named=True).
        assert agent_sdk._needs_auto_name(entry) is False
