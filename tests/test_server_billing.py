# Covers: distillate/server.py
"""Tests for server-side billing endpoints and WebSocket protocol.

Adds to distillate/server.py:
  - HTTP: GET /usage → snapshot
  - WS in:  get_preferences, get_usage, set_model (persists)
  - WS out: preferences, usage, usage_update

RED tests. See docs/research/nicolas-billing-action-plan.md §7.5.
"""
import importlib.util
import json

import pytest


pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("fastapi"),
    reason="fastapi not installed (desktop-only dependency)",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _FakeNicolas:
    """Stand-in for NicolasClient — server is the SUT, not the SDK."""
    def __init__(self, state=None, model=""):
        self.model = model
        self.set_model_calls = []
        self._turn_yields = []

    async def set_model(self, m):
        self.model = m
        self.set_model_calls.append(m)

    async def new_conversation(self): pass
    async def interrupt(self): pass
    def list_sessions(self): return []
    async def switch_session(self, sid): pass
    def rename_session(self, sid, name): return True

    async def send(self, text):
        """Yield whatever events the test queued up."""
        for e in self._turn_yields:
            yield e


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("distillate.config.CONFIG_DIR", tmp_path)
    (tmp_path).mkdir(parents=True, exist_ok=True)

    # Isolate prefs + usage.
    from distillate import preferences
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", tmp_path / "preferences.json")
    if hasattr(preferences, "_reset_cache"):
        preferences._reset_cache()

    from distillate.agent_runtime import usage_tracker
    monkeypatch.setattr(usage_tracker, "USAGE_PATH", tmp_path / "usage.jsonl")
    if hasattr(usage_tracker, "_reset_singleton"):
        usage_tracker._reset_singleton()

    # Replace NicolasClient with the fake so the server never spins up the SDK.
    import distillate.server as server_mod
    fake = _FakeNicolas()
    monkeypatch.setattr(server_mod, "_get_nicolas", lambda: fake)

    from starlette.testclient import TestClient
    app = server_mod._create_app()
    tc = TestClient(app)
    tc.fake_nicolas = fake  # expose for assertions
    return tc


# ---------------------------------------------------------------------------
# HTTP /usage
# ---------------------------------------------------------------------------

class TestHttpUsage:
    def test_get_usage_returns_snapshot_shape(self, client):
        resp = client.get("/usage")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("session", "today", "week", "all", "by_model", "current_model"):
            assert key in body

    def test_get_usage_reflects_recorded_events(self, client, tmp_path):
        from distillate.agent_runtime import usage_tracker
        t = usage_tracker.get_tracker()
        t.record(
            model="claude-opus-4-6",
            role="nicolas_turn",
            session_id="s1",
            tokens={"input_tokens": 1000, "output_tokens": 500,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        )
        body = client.get("/usage").json()
        assert body["all"]["input_tokens"] == 1000
        assert body["all"]["output_tokens"] == 500


# ---------------------------------------------------------------------------
# WebSocket — get_preferences
# ---------------------------------------------------------------------------

class TestWsPreferences:
    def test_get_preferences_returns_default(self, client):
        from distillate import pricing
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "get_preferences"}))
            msg = ws.receive_json()
            assert msg["type"] == "preferences"
            assert msg["nicolas_model"] == pricing.DEFAULT_MODEL
            from distillate import pricing
            assert len(msg["supported_models"]) == len(pricing.supported_models())

    def test_get_preferences_returns_persisted_model(self, client):
        from distillate import preferences
        preferences.set("nicolas_model", "claude-opus-4-6")
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "get_preferences"}))
            msg = ws.receive_json()
            assert msg["nicolas_model"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# WebSocket — set_model persistence
# ---------------------------------------------------------------------------

class TestWsSetModel:
    def test_set_model_persists_to_preferences(self, client, tmp_path):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "set_model", "model": "claude-haiku-4-5-20251001"
            }))
            # Small drain — server handles and continues.
            ws.send_text(json.dumps({"type": "get_preferences"}))
            msg = ws.receive_json()
        assert msg["nicolas_model"] == "claude-haiku-4-5-20251001"
        on_disk = json.loads((tmp_path / "preferences.json").read_text())
        assert on_disk["nicolas_model"] == "claude-haiku-4-5-20251001"

    def test_set_model_calls_nicolas_client(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "set_model", "model": "claude-opus-4-6"
            }))
            ws.send_text(json.dumps({"type": "get_preferences"}))
            ws.receive_json()
        assert "claude-opus-4-6" in client.fake_nicolas.set_model_calls

    def test_set_model_rejects_unknown(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({
                "type": "set_model", "model": "claude-not-real"
            }))
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "model" in msg["message"].lower()
        # Not persisted.
        from distillate import preferences
        from distillate import pricing
        assert preferences.get("nicolas_model") == pricing.DEFAULT_MODEL


# ---------------------------------------------------------------------------
# WebSocket — get_usage
# ---------------------------------------------------------------------------

class TestWsUsage:
    def test_get_usage_returns_snapshot(self, client):
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "get_usage"}))
            msg = ws.receive_json()
            assert msg["type"] == "usage"
            for key in ("session", "today", "week", "all", "by_model", "current_model"):
                assert key in msg


# ---------------------------------------------------------------------------
# WebSocket — usage_update push
# ---------------------------------------------------------------------------

class TestWsUsageUpdate:
    def test_usage_update_pushed_after_turn_end(self, client):
        # Queue a turn_end on the fake Nicolas.
        turn_end = {
            "type": "turn_end",
            "session_id": "s1",
            "model": "claude-opus-4-6",
            "tokens": {"input": 100, "output": 50, "cache_read": 0, "cache_creation": 0},
            "cost_usd": 0.01,
            "sdk_reported_cost_usd": 0.01,
            "num_turns": 1,
        }
        client.fake_nicolas._turn_yields = [turn_end]

        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"text": "hi"}))
            # Expect: turn_end first, then usage_update push.
            seen = []
            for _ in range(2):
                seen.append(ws.receive_json())
        types = [m["type"] for m in seen]
        assert "turn_end" in types
        assert "usage_update" in types
