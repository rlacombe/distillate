# Covers: distillate/nicolas_state.py
"""Tests for the Nicolas chat-turn state store.

Nicolas is a singleton in-process ClaudeSDKClient (not keyed by
workspace/session), so it gets its own tiny state module + GET endpoint
rather than reusing the (ws_id, sid) hook store from claude_hooks.py.

These tests are the spec for `distillate.nicolas_state` + the
`GET /nicolas/state` route. They follow the pattern of
tests/test_claude_hooks.py.
"""
import asyncio
import importlib.util

import pytest


@pytest.fixture(autouse=True)
def _clear_nicolas_state():
    """Fresh in-memory Nicolas state per test (module-level var)."""
    try:
        from distillate.nicolas_state import clear_nicolas_state
    except ImportError:
        yield
        return
    clear_nicolas_state()
    yield
    clear_nicolas_state()


class TestNicolasStateStore:
    """In-memory `working | idle` state for Nicolas, module-level singleton."""

    def test_set_and_get_nicolas_state(self):
        from distillate.nicolas_state import get_nicolas_state, set_nicolas_state

        assert get_nicolas_state() is None

        set_nicolas_state("working")
        assert get_nicolas_state() == "working"

        set_nicolas_state("idle")
        assert get_nicolas_state() == "idle"

    def test_clear_nicolas_state(self):
        from distillate.nicolas_state import (
            clear_nicolas_state,
            get_nicolas_state,
            set_nicolas_state,
        )

        set_nicolas_state("idle")
        assert get_nicolas_state() == "idle"

        clear_nicolas_state()
        assert get_nicolas_state() is None


class TestNicolasClientInterrupt:
    """Cancelling a turn must not leave Nicolas stuck in "working" forever."""

    def test_interrupt_sets_idle(self):
        """User hits stop mid-turn → state flips to idle even before ResultMessage.

        Without this the SDK emits no ResultMessage on interrupt and the
        bell/tray would stay armed until the next prompt flipped it back.
        """
        from distillate.agent_sdk import NicolasClient
        from distillate.nicolas_state import get_nicolas_state, set_nicolas_state
        from distillate.state import State

        client = NicolasClient(State())
        set_nicolas_state("working")

        # No SDK subprocess is connected — interrupt() should be a no-op on
        # the SDK side but still reset our own state tracker.
        asyncio.run(client.interrupt())
        assert get_nicolas_state() == "idle"

    def test_send_resets_idle_on_exception(self, monkeypatch):
        """Any exception inside send() must still leave state at idle.

        Observed in prod: the SDK raised "Not connected. Call connect() first."
        during a turn, server caught the error and emitted an error event,
        but state stayed stuck at "working" forever — bell never fired
        because the renderer only raises it when state transitions to
        "idle". The fix is a try/finally in send().
        """
        from distillate.agent_sdk import NicolasClient
        from distillate.nicolas_state import get_nicolas_state
        from distillate.state import State

        client = NicolasClient(State())

        async def _boom(self_):
            raise RuntimeError("Not connected. Call connect() first.")

        # Force the failure at _ensure_connected so we don't need the SDK.
        monkeypatch.setattr(
            NicolasClient, "_ensure_connected", _boom, raising=True,
        )

        async def _drive():
            with pytest.raises(RuntimeError):
                async for _ in client.send("hi"):
                    pass

        asyncio.run(_drive())
        assert get_nicolas_state() == "idle"


@pytest.mark.skipif(
    not importlib.util.find_spec("fastapi"),
    reason="fastapi not installed (desktop-only dependency)",
)
class TestNicolasStateEndpoint:
    """GET /nicolas/state reflects the current Nicolas state."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.config.CONFIG_DIR", tmp_path / "cfg")
        (tmp_path / "cfg").mkdir(parents=True, exist_ok=True)

        from starlette.testclient import TestClient
        from distillate.server import _create_app

        return TestClient(_create_app())

    def test_nicolas_state_endpoint_returns_current_state(self, client):
        """Endpoint returns the current state verbatim, or null when unset."""
        from distillate.nicolas_state import set_nicolas_state

        resp = client.get("/nicolas/state")
        assert resp.status_code == 200
        assert resp.json() == {"status": None}

        set_nicolas_state("working")
        resp = client.get("/nicolas/state")
        assert resp.status_code == 200
        assert resp.json() == {"status": "working"}

        set_nicolas_state("idle")
        resp = client.get("/nicolas/state")
        assert resp.status_code == 200
        assert resp.json() == {"status": "idle"}

    def test_ack_endpoint_clears_state(self, client):
        """POST /nicolas/ack wipes the state store (renderer acknowledges the bell).

        Lets a focused renderer tell the backend "user engaged, stop
        advertising a pending turn" without waiting for the next prompt
        to flip state to working.
        """
        from distillate.nicolas_state import get_nicolas_state, set_nicolas_state

        set_nicolas_state("idle")
        assert get_nicolas_state() == "idle"

        resp = client.post("/nicolas/ack")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert get_nicolas_state() is None

        # Endpoint reflects the cleared state.
        resp = client.get("/nicolas/state")
        assert resp.json() == {"status": None}

    def test_state_transitions_on_turn_lifecycle(self, client):
        """Seed no state, flip to working, then idle; endpoint reflects each step in order."""
        from distillate.nicolas_state import set_nicolas_state

        # No state yet → endpoint returns null.
        assert client.get("/nicolas/state").json() == {"status": None}

        # User submits a prompt → working.
        set_nicolas_state("working")
        assert client.get("/nicolas/state").json() == {"status": "working"}

        # Turn ends → idle.
        set_nicolas_state("idle")
        assert client.get("/nicolas/state").json() == {"status": "idle"}
