# Covers: distillate/agent_sdk.py — _touch_session upsert, data-loss guards, legacy migration
"""Session upsert, history preservation, and legacy migration tests.

Behaviors guarded:
  _touch_session
    - New session_id appends a new entry
    - Existing session_id updates in-place (no duplicates)
    - last_activity is always refreshed
    - preview is set ONCE (first user message rule)
    - name updates when provided, preserved when not
    - New entry's default name derived from preview

  Multi-session sequences (data-loss guards)
    - Two sessions both persist after save→load
    - Clearing active_session_id does not drop sessions
    - new_conversation then new message keeps old session
    - switch_session preserves all other sessions
    - Rapid sequential touches never drop sessions

  Legacy migration
    - Legacy single-session file migrated to registry
    - Legacy file removed after successful migration
    - Migration is idempotent
    - Malformed legacy file doesn't crash
    - Legacy with no session_id does not migrate
    - Migration skipped when registry already exists
"""

from __future__ import annotations

import json

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
# _touch_session — upsert semantics
# ---------------------------------------------------------------------------

class TestTouchSession:
    def test_new_session_appends_entry(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "new-id", preview="hello")
        assert len(reg["sessions"]) == 1
        s = reg["sessions"][0]
        assert s["session_id"] == "new-id"
        assert s["preview"] == "hello"
        assert s["created_at"]
        assert s["last_activity"]

    def test_existing_session_updates_in_place(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "id-1", preview="first")
        first_created = reg["sessions"][0]["created_at"]

        # Touch again with new preview attempt
        agent_sdk._touch_session(reg, "id-1", preview="second")

        assert len(reg["sessions"]) == 1, "Should update in place, not duplicate"
        assert reg["sessions"][0]["created_at"] == first_created, "created_at preserved"

    def test_preview_set_only_once(self):
        """The first user message wins. Later previews must not overwrite."""
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="first message")
        agent_sdk._touch_session(reg, "sid", preview="second message")
        agent_sdk._touch_session(reg, "sid", preview="third message")
        assert reg["sessions"][0]["preview"] == "first message"

    def test_preview_truncated_to_120(self):
        reg = agent_sdk._default_registry()
        long = "x" * 500
        agent_sdk._touch_session(reg, "sid", preview=long)
        assert len(reg["sessions"][0]["preview"]) == 120

    def test_last_activity_refreshed_on_each_touch(self):
        import time
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="x")
        first = reg["sessions"][0]["last_activity"]
        time.sleep(1.05)  # ISO seconds resolution
        agent_sdk._touch_session(reg, "sid")
        assert reg["sessions"][0]["last_activity"] > first

    def test_name_updates_when_provided(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="hi")
        agent_sdk._touch_session(reg, "sid", name="Renamed")
        assert reg["sessions"][0]["name"] == "Renamed"

    def test_name_preserved_when_not_provided(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", name="Original")
        agent_sdk._touch_session(reg, "sid", preview="x")
        assert reg["sessions"][0]["name"] == "Original"

    def test_new_entry_default_name_from_preview(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="A meaningful first line")
        assert reg["sessions"][0]["name"] == "A meaningful first line"

    def test_new_entry_default_name_truncated(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid", preview="x" * 500)
        assert len(reg["sessions"][0]["name"]) <= 50

    def test_new_entry_default_name_when_no_preview(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "sid")
        assert reg["sessions"][0]["name"] == "New conversation"


# ---------------------------------------------------------------------------
# Multi-session sequences — the data-loss-guards that matter most
# ---------------------------------------------------------------------------

class TestNoDataLoss:
    """These tests are the heart of the suite — they reproduce the
    'previous conversation disappeared' bug class."""

    def test_two_sessions_both_persist(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "A", preview="msg A")
        agent_sdk._touch_session(reg, "B", preview="msg B")
        agent_sdk._save_registry(reg)

        loaded = agent_sdk._load_registry()
        ids = {s["session_id"] for s in loaded["sessions"]}
        assert ids == {"A", "B"}

    def test_clearing_active_does_not_drop_sessions(self):
        """The flow: A is active → user starts new conversation →
        active_session_id := None. A must remain in the list."""
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "A", preview="msg A")
        reg["active_session_id"] = "A"
        agent_sdk._save_registry(reg)

        # Simulate new_conversation()
        reg = agent_sdk._load_registry()
        reg["active_session_id"] = None
        agent_sdk._save_registry(reg)

        loaded = agent_sdk._load_registry()
        assert loaded["active_session_id"] is None
        assert len(loaded["sessions"]) == 1
        assert loaded["sessions"][0]["session_id"] == "A"

    def test_new_conversation_then_new_message_keeps_old(self):
        """End-to-end: A finishes, user clicks New, sends a message that
        creates B. A and B both appear in list."""
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "A", preview="first conv")
        reg["active_session_id"] = "A"
        agent_sdk._save_registry(reg)

        # User clicks New
        reg = agent_sdk._load_registry()
        reg["active_session_id"] = None
        agent_sdk._save_registry(reg)

        # User sends message → session_init for new id "B"
        reg = agent_sdk._load_registry()
        agent_sdk._touch_session(reg, "B", preview="second conv")
        reg["active_session_id"] = "B"
        agent_sdk._save_registry(reg)

        loaded = agent_sdk._load_registry()
        ids = {s["session_id"] for s in loaded["sessions"]}
        assert ids == {"A", "B"}, f"Lost a session! Got: {ids}"
        assert loaded["active_session_id"] == "B"

    def test_switch_session_preserves_others(self):
        reg = agent_sdk._default_registry()
        agent_sdk._touch_session(reg, "A", preview="a")
        agent_sdk._touch_session(reg, "B", preview="b")
        agent_sdk._touch_session(reg, "C", preview="c")
        reg["active_session_id"] = "A"
        agent_sdk._save_registry(reg)

        # Switch to B
        reg = agent_sdk._load_registry()
        reg["active_session_id"] = "B"
        agent_sdk._save_registry(reg)

        # Switch to C
        reg = agent_sdk._load_registry()
        reg["active_session_id"] = "C"
        agent_sdk._save_registry(reg)

        loaded = agent_sdk._load_registry()
        assert {s["session_id"] for s in loaded["sessions"]} == {"A", "B", "C"}

    def test_rapid_touches_no_data_loss(self):
        """20 sequential touches across 5 session ids must end with all 5
        present — no drops from race-window write/load races."""
        reg = agent_sdk._default_registry()
        for i in range(20):
            sid = f"s{i % 5}"
            reg = agent_sdk._load_registry()
            agent_sdk._touch_session(reg, sid, preview=f"msg {i}")
            agent_sdk._save_registry(reg)

        loaded = agent_sdk._load_registry()
        ids = {s["session_id"] for s in loaded["sessions"]}
        assert ids == {"s0", "s1", "s2", "s3", "s4"}


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

class TestLegacyMigration:
    def test_legacy_file_migrated(self, isolate_registry):
        agent_sdk._LEGACY_SESSION_FILE.write_text(
            json.dumps({"session_id": "legacy-123"})
        )
        reg = agent_sdk._load_registry()
        ids = [s["session_id"] for s in reg["sessions"]]
        assert "legacy-123" in ids
        assert reg["active_session_id"] == "legacy-123"

    def test_legacy_file_removed_after_migration(self, isolate_registry):
        agent_sdk._LEGACY_SESSION_FILE.write_text(
            json.dumps({"session_id": "legacy-123"})
        )
        agent_sdk._load_registry()
        assert not agent_sdk._LEGACY_SESSION_FILE.exists()

    def test_migration_idempotent(self, isolate_registry):
        """Once migrated, subsequent loads must not re-migrate or duplicate."""
        agent_sdk._LEGACY_SESSION_FILE.write_text(
            json.dumps({"session_id": "legacy-123"})
        )
        reg1 = agent_sdk._load_registry()
        reg2 = agent_sdk._load_registry()
        assert len(reg1["sessions"]) == len(reg2["sessions"]) == 1

    def test_malformed_legacy_does_not_crash(self, isolate_registry):
        agent_sdk._LEGACY_SESSION_FILE.write_text("not json")
        reg = agent_sdk._load_registry()
        assert reg["sessions"] == []

    def test_legacy_with_no_session_id_does_not_migrate(self, isolate_registry):
        agent_sdk._LEGACY_SESSION_FILE.write_text(json.dumps({"other": "field"}))
        reg = agent_sdk._load_registry()
        assert reg["sessions"] == []

    def test_migration_skipped_when_registry_exists(self, isolate_registry):
        """If the new registry already exists, never touch the legacy file."""
        agent_sdk._SESSIONS_FILE.write_text(
            json.dumps(agent_sdk._default_registry())
        )
        agent_sdk._LEGACY_SESSION_FILE.write_text(
            json.dumps({"session_id": "legacy-123"})
        )
        reg = agent_sdk._load_registry()
        assert reg["sessions"] == []
        # Legacy file untouched
        assert agent_sdk._LEGACY_SESSION_FILE.exists()
