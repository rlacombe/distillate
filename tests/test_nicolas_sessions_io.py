# Covers: distillate/agent_sdk.py — registry load/save, atomic write, concurrent writes
"""Registry I/O tests for the Nicolas sessions registry.

Behaviors guarded:
  - Missing file → default registry, no exception
  - Malformed JSON → default registry, no exception
  - Wrong-type JSON (list, str) → default registry
  - Save→Load roundtrips data faithfully
  - Atomic write: never publishes a partial file
  - Concurrent writes don't corrupt the file
  - Write failures don't crash the process
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

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
# Registry I/O — defensive load
# ---------------------------------------------------------------------------

class TestLoadRegistry:
    def test_missing_file_returns_default(self):
        reg = agent_sdk._load_registry()
        assert reg == {"version": 1, "active_session_id": None, "sessions": []}

    def test_malformed_json_returns_default(self, isolate_registry):
        agent_sdk._SESSIONS_FILE.write_text("{broken json")
        reg = agent_sdk._load_registry()
        assert reg["sessions"] == []
        assert reg["active_session_id"] is None

    def test_empty_file_returns_default(self, isolate_registry):
        agent_sdk._SESSIONS_FILE.write_text("")
        reg = agent_sdk._load_registry()
        assert reg["sessions"] == []

    def test_wrong_type_list_returns_default(self, isolate_registry):
        """A list at top level is not a valid registry — default it."""
        agent_sdk._SESSIONS_FILE.write_text(json.dumps([1, 2, 3]))
        reg = agent_sdk._load_registry()
        assert reg["sessions"] == []

    def test_wrong_type_string_returns_default(self, isolate_registry):
        agent_sdk._SESSIONS_FILE.write_text(json.dumps("not a registry"))
        reg = agent_sdk._load_registry()
        assert reg["sessions"] == []

    def test_missing_sessions_key_returns_default(self, isolate_registry):
        """A dict without sessions list is not valid."""
        agent_sdk._SESSIONS_FILE.write_text(json.dumps({"version": 1}))
        reg = agent_sdk._load_registry()
        assert reg["sessions"] == []


# ---------------------------------------------------------------------------
# Registry I/O — save roundtrip
# ---------------------------------------------------------------------------

class TestSaveRegistry:
    def test_save_then_load_roundtrip(self):
        reg = {
            "version": 1,
            "active_session_id": "abc",
            "sessions": [{
                "session_id": "abc",
                "name": "Test",
                "created_at": "2026-01-01T00:00:00Z",
                "last_activity": "2026-01-01T00:00:00Z",
                "preview": "hello",
            }],
        }
        agent_sdk._save_registry(reg)
        loaded = agent_sdk._load_registry()
        assert loaded == reg

    def test_save_failure_does_not_raise(self, monkeypatch):
        """If write fails (e.g. permission denied), don't crash the caller."""
        def boom(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(Path, "write_text", boom)
        # Must not raise
        agent_sdk._save_registry(agent_sdk._default_registry())

    def test_save_is_atomic_no_partial_publish(self, isolate_registry, monkeypatch):
        """A write that fails mid-way must not leave a partial file in place
        of the previous good one. This is the root-cause guard for the
        'previous conversation has disappeared' bug.

        We simulate a real-world crash by overriding ``write_text`` so it
        opens the target with mode 'w' (truncates) then raises before any
        bytes land. Without atomic write (write-temp-rename), the live
        target file is now empty/zero-bytes — a subsequent load returns
        the default registry and all sessions are gone.
        """
        # Seed a known-good registry.
        good = {
            "version": 1,
            "active_session_id": "good",
            "sessions": [{
                "session_id": "good", "name": "Good", "preview": "g",
                "created_at": "2026-01-01T00:00:00Z",
                "last_activity": "2026-01-01T00:00:00Z",
            }],
        }
        agent_sdk._save_registry(good)
        assert agent_sdk._load_registry()["sessions"][0]["session_id"] == "good"

        # Simulate a write failure. With a non-atomic implementation
        # (write_text directly to target), the failure would truncate the
        # live registry file. With atomic write (write tmp + rename), the
        # failure must leave the original target file untouched.
        original_write_text = Path.write_text
        def failing_write(self, *args, **kwargs):
            # Truncate the path (simulates partial write), then raise.
            try:
                with open(self, "w") as f:
                    f.write("")
            except OSError:
                pass
            raise OSError("simulated crash mid-write")
        monkeypatch.setattr(Path, "write_text", failing_write)

        bad = {"version": 1, "active_session_id": None, "sessions": []}
        agent_sdk._save_registry(bad)

        # Restore for clean read.
        monkeypatch.setattr(Path, "write_text", original_write_text)

        # The original registry must still be loadable.
        loaded = agent_sdk._load_registry()
        assert loaded["sessions"], (
            "Atomic-write invariant violated: a failed write destroyed the "
            "live registry. _save_registry must write to a tempfile so the "
            "live target is only touched by an atomic rename."
        )
        assert loaded["sessions"][0]["session_id"] == "good"

    def test_save_uses_atomic_replace_under_the_hood(self, monkeypatch):
        """The implementation should write to a temp file then atomically
        replace the target — never write_text() directly to the live target.
        We assert this by tracking whether os.replace is called as part of
        any successful save.
        """
        import os as os_mod
        replace_calls: list[tuple] = []
        original_replace = os_mod.replace
        def tracked_replace(src, dst, *args, **kwargs):
            replace_calls.append((str(src), str(dst)))
            return original_replace(src, dst, *args, **kwargs)
        monkeypatch.setattr(os_mod, "replace", tracked_replace)
        # Also monkey-patch Path.replace which is the OO equivalent.
        original_path_replace = Path.replace
        def tracked_path_replace(self, target, *args, **kwargs):
            replace_calls.append((str(self), str(target)))
            return original_path_replace(self, target, *args, **kwargs)
        monkeypatch.setattr(Path, "replace", tracked_path_replace)

        agent_sdk._save_registry(agent_sdk._default_registry())

        assert replace_calls, (
            "_save_registry must use atomic replace (write tmp + rename). "
            "Found no os.replace or Path.replace calls."
        )
        # And the rename must target our registry file.
        assert any(
            str(agent_sdk._SESSIONS_FILE) in dst for _src, dst in replace_calls
        ), f"Expected a rename to {agent_sdk._SESSIONS_FILE}, got {replace_calls}"

    def test_concurrent_writes_do_not_corrupt(self, isolate_registry):
        """Two threads writing simultaneously must leave the file readable
        as one of the two values (last-write-wins is OK; corruption is not).
        """
        good_a = {
            "version": 1, "active_session_id": "a",
            "sessions": [{"session_id": "a", "name": "A",
                          "created_at": "x", "last_activity": "x", "preview": ""}],
        }
        good_b = {
            "version": 1, "active_session_id": "b",
            "sessions": [{"session_id": "b", "name": "B",
                          "created_at": "y", "last_activity": "y", "preview": ""}],
        }

        def writer(reg, n):
            for _ in range(n):
                agent_sdk._save_registry(reg)

        t1 = threading.Thread(target=writer, args=(good_a, 50))
        t2 = threading.Thread(target=writer, args=(good_b, 50))
        t1.start(); t2.start(); t1.join(); t2.join()

        loaded = agent_sdk._load_registry()
        # Final state must be one of the two values, never corrupted.
        assert loaded["active_session_id"] in {"a", "b"}
        assert len(loaded["sessions"]) == 1
        assert loaded["sessions"][0]["session_id"] in {"a", "b"}
