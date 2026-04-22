"""Session boundaries must isolate Nicolas's lab_repl sandbox.

# Covers: distillate/agent_sdk.py (new_conversation, switch_session)
# Covers: distillate/agent_runtime/lab_repl.py (reset_sandbox)

Root cause guard: the lab_repl sandbox is a module-level global keyed to the
MCP server process. Without an explicit reset at conversation/session
boundaries, variables from Session A leak into Session B — the user
perceives this as Nicolas "ingesting tool use in the wrong context."

The guarantee these tests lock in: when the user switches sessions, the
REPL's working memory is wiped. Within a single session, it persists.
"""

from __future__ import annotations

import asyncio

import pytest

from distillate import agent_sdk
from distillate.agent_runtime import lab_repl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path, monkeypatch):
    """Point the sessions registry at a temp file so tests don't touch real state."""
    sessions_file = tmp_path / "nicolas_sessions.json"
    legacy_file = tmp_path / "nicolas_session.json"
    monkeypatch.setattr(agent_sdk, "_SESSIONS_FILE", sessions_file)
    monkeypatch.setattr(agent_sdk, "_LEGACY_SESSION_FILE", legacy_file)
    yield
    # Ensure no sandbox state bleeds between tests.
    lab_repl.reset_sandbox()


@pytest.fixture
def state():
    """Minimal state stub — lab_repl only needs a few attributes for LabAPI."""
    from unittest.mock import MagicMock
    s = MagicMock()
    s.reload = MagicMock()
    s.documents_with_status = MagicMock(return_value=[])
    s.documents_processed_since = MagicMock(return_value=[])
    s.agents = {}
    s.experiments_for_workspace = MagicMock(return_value=[])
    return s


@pytest.fixture
def client(monkeypatch, state):
    """A NicolasClient with a real state handle but no SDK client connected."""
    from distillate.state import State
    monkeypatch.setattr(State, "__init__", lambda self: None)
    c = agent_sdk.NicolasClient(state=state)
    # Disconnect is a no-op when _client is None — which is the case here.
    assert c._client is None
    return c


def _run(coro):
    """Run an async coroutine from a sync test."""
    return asyncio.run(coro)


def _set_var(state, name: str, value) -> None:
    """Run a trivial sandbox assignment via the public execute() path."""
    result = lab_repl.execute(f"{name} = {value!r}", state)
    assert result["success"], result["output"]


def _get_var(state, name: str):
    """Read back a variable from the sandbox via execute()."""
    result = lab_repl.execute(f"FINAL({name})", state)
    assert result["success"], result["output"]
    return result["output"]


def _var_exists(state, name: str) -> bool:
    """True if ``name`` is a user-defined variable in the current sandbox."""
    result = lab_repl.execute("FINAL(list(SHOW_VARS().split()))", state)
    # SHOW_VARS returns '  name: type\n  …'; we just look for the name.
    return result["success"] and name in result["output"]


# ---------------------------------------------------------------------------
# Core guarantee: session switch isolates REPL state
# ---------------------------------------------------------------------------

class TestSessionSwitchWipesSandbox:
    """The keystone invariant: Session A's variables are unreachable from B."""

    def test_variable_from_session_a_not_visible_in_session_b(self, client, state):
        _set_var(state, "secret", "session-A-value")
        assert _get_var(state, "secret") == "session-A-value"

        _run(client.switch_session("session-B"))

        # After the switch, referencing `secret` must fail — fresh namespace.
        result = lab_repl.execute("FINAL(secret)", state)
        assert not result["success"]
        assert "NameError" in result["output"]

    def test_user_variables_gone_after_switch(self, client, state):
        _set_var(state, "exp", "design-system")
        _set_var(state, "notes", "[1, 2, 3]")
        assert _var_exists(state, "exp")
        assert _var_exists(state, "notes")

        _run(client.switch_session("session-B"))

        assert not _var_exists(state, "exp")
        assert not _var_exists(state, "notes")

    def test_switch_back_does_not_restore_variables(self, client, state):
        """Reset is destructive — returning to a session id doesn't revive vars.

        This matches the app-restart semantics: the sandbox is never persisted,
        so 'resume by click' must behave the same as 'resume after restart.'
        """
        _run(client.switch_session("session-A"))
        _set_var(state, "x", "A-value")

        _run(client.switch_session("session-B"))
        _set_var(state, "x", "B-value")

        _run(client.switch_session("session-A"))

        result = lab_repl.execute("FINAL(x)", state)
        assert not result["success"]
        assert "NameError" in result["output"]


class TestNewConversationWipesSandbox:
    """new_conversation() is the second boundary; same guarantees apply."""

    def test_variable_gone_after_new_conversation(self, client, state):
        _set_var(state, "draft", "carried-over")
        assert _get_var(state, "draft") == "carried-over"

        _run(client.new_conversation())

        result = lab_repl.execute("FINAL(draft)", state)
        assert not result["success"]
        assert "NameError" in result["output"]

    def test_new_conversation_with_pending_name_still_resets(self, client, state):
        """pending_name is cosmetic — it must not affect isolation."""
        _set_var(state, "exp_handle", "{'id': 'old-exp'}")

        _run(client.new_conversation(pending_name="New Experiment Thread"))

        assert not _var_exists(state, "exp_handle")


# ---------------------------------------------------------------------------
# No regression: within-session persistence is preserved
# ---------------------------------------------------------------------------

class TestWithinSessionPersistence:
    """The RLM paradigm requires the sandbox to accumulate state across turns
    within one conversation. These tests guard against an over-eager reset."""

    def test_variables_persist_across_execute_calls(self, state):
        _set_var(state, "x", 42)
        # A second execute must see x.
        result = lab_repl.execute("FINAL(x + 1)", state)
        assert result["success"]
        assert result["output"] == "43"

    def test_variables_persist_after_many_calls(self, state):
        _set_var(state, "acc", 0)
        for i in range(5):
            result = lab_repl.execute(f"acc = acc + {i}", state)
            assert result["success"]
        assert _get_var(state, "acc") == "10"

    def test_reserved_names_survive_user_overwrite(self, state):
        """lab, delegate, etc. must be restored after exec so users can't break
        the environment for their own next call."""
        # User tries to clobber `lab`.
        lab_repl.execute("lab = 'clobbered'", state)
        # Next call must still have a working LabAPI.
        result = lab_repl.execute("FINAL(type(lab).__name__)", state)
        assert result["success"]
        assert result["output"] == "LabAPI"


# ---------------------------------------------------------------------------
# Rebuilt sandbox is usable — LabAPI is fresh and functional
# ---------------------------------------------------------------------------

class TestSandboxRebuildsCorrectly:
    """After reset, the next execute() must get a working sandbox — not a stub."""

    def test_lab_api_available_after_switch(self, client, state):
        _set_var(state, "stale", "A")
        _run(client.switch_session("session-B"))

        result = lab_repl.execute("FINAL(type(lab).__name__)", state)
        assert result["success"]
        assert result["output"] == "LabAPI"

    def test_lab_api_not_the_same_instance_after_switch(self, client, state):
        """The new sandbox must hold a fresh LabAPI, not the previous one.

        This is the thing that makes stale cached handles impossible: the
        reference itself is new, so any closure over the old `lab` object
        in user variables wouldn't be reachable from new code anyway.
        """
        lab_repl.execute("old_lab_id = id(lab)", state)
        old_id = _get_var(state, "old_lab_id")

        _run(client.switch_session("session-B"))

        result = lab_repl.execute("FINAL(id(lab))", state)
        assert result["success"]
        assert result["output"] != old_id

    def test_delegate_helpers_available_after_switch(self, client, state):
        _run(client.switch_session("session-B"))
        # delegate/llm_query/FINAL etc. must be present in the rebuilt namespace.
        result = lab_repl.execute(
            "FINAL(all(callable(x) for x in [delegate, llm_query, FINAL]))",
            state,
        )
        assert result["success"]
        assert result["output"] == "True"

    def test_stdlib_modules_available_after_switch(self, client, state):
        _run(client.switch_session("session-B"))
        result = lab_repl.execute("FINAL(math.sqrt(16))", state)
        assert result["success"]
        assert result["output"] == "4.0"


# ---------------------------------------------------------------------------
# Edge cases: reset must be safe in all orderings
# ---------------------------------------------------------------------------

class TestResetIdempotency:
    def test_switch_session_before_any_execute_does_not_crash(self, client):
        """Sandbox never initialized — reset must be a no-op, not an error."""
        _run(client.switch_session("session-A"))  # must not raise
        _run(client.switch_session("session-B"))  # must not raise

    def test_new_conversation_before_any_execute_does_not_crash(self, client):
        _run(client.new_conversation())
        _run(client.new_conversation())

    def test_rapid_switch_does_not_corrupt_sandbox(self, client, state):
        """Multiple switches in succession — the final sandbox must still work."""
        _run(client.switch_session("A"))
        _run(client.switch_session("B"))
        _run(client.switch_session("C"))
        _set_var(state, "post_switch", 123)
        assert _get_var(state, "post_switch") == "123"

    def test_reset_sandbox_is_idempotent(self, state):
        """Direct calls to the reset function must be safe to chain."""
        _set_var(state, "x", 1)
        lab_repl.reset_sandbox()
        lab_repl.reset_sandbox()
        lab_repl.reset_sandbox()
        # Next execute rebuilds cleanly.
        result = lab_repl.execute("FINAL(1 + 1)", state)
        assert result["success"]
        assert result["output"] == "2"


# ---------------------------------------------------------------------------
# Session pointer + sandbox wipe happen together
# ---------------------------------------------------------------------------

class TestSessionPointerAndSandboxConsistency:
    """If the pointer moves, the sandbox must be gone. If the sandbox is gone,
    the pointer must have moved. These two facts should always agree."""

    def test_switch_updates_pointer_and_wipes_together(self, client, state):
        _set_var(state, "marker", "before-switch")
        _run(client.switch_session("new-sid"))

        reg = agent_sdk._load_registry()
        assert reg["active_session_id"] == "new-sid"
        # And sandbox is wiped.
        result = lab_repl.execute("FINAL(marker)", state)
        assert not result["success"]

    def test_new_conversation_clears_pointer_and_wipes_together(self, client, state):
        _set_var(state, "marker", "before-new")
        _run(client.new_conversation())

        reg = agent_sdk._load_registry()
        assert reg["active_session_id"] is None
        result = lab_repl.execute("FINAL(marker)", state)
        assert not result["success"]


# ---------------------------------------------------------------------------
# Cost tracker is part of REPL state — it should reset too
# ---------------------------------------------------------------------------

class TestCostTrackerResetsWithSandbox:
    """The cost tracker accumulates session-scoped sub-LLM spend. On session
    switch we must not carry Session A's tally into Session B's budget."""

    def test_cost_tracker_reset_on_session_switch(self, client):
        # Simulate accumulated usage from Session A.
        lab_repl._cost_tracker.input_tokens = 10_000
        lab_repl._cost_tracker.output_tokens = 5_000
        lab_repl._cost_tracker.api_calls = 7
        assert lab_repl._cost_tracker.estimated_cost_usd > 0

        _run(client.switch_session("fresh"))

        # A fresh tracker starts at zero on every counter.
        assert lab_repl._cost_tracker.input_tokens == 0
        assert lab_repl._cost_tracker.output_tokens == 0
        assert lab_repl._cost_tracker.api_calls == 0
        assert lab_repl._cost_tracker.estimated_cost_usd == 0.0

    def test_cost_tracker_reset_on_new_conversation(self, client):
        lab_repl._cost_tracker.input_tokens = 500
        _run(client.new_conversation())
        assert lab_repl._cost_tracker.input_tokens == 0

    def test_cost_tracker_is_a_new_instance_after_reset(self, client):
        """Identity check: the singleton is rebuilt, not just zeroed in place."""
        original = lab_repl._cost_tracker
        _run(client.switch_session("new-sid"))
        assert lab_repl._cost_tracker is not original
