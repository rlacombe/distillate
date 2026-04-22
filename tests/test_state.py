# Covers: distillate/state.py
"""Tests for state management: State class, locking, atomic writes."""

import json
import os
from unittest.mock import patch


class TestCorruptStateRecovery:
    def test_corrupt_json_recovers_to_defaults(self, tmp_path):
        from distillate.state import STATE_PATH, State

        STATE_PATH.write_text("{invalid json!!!")
        s = State()
        assert s.zotero_library_version == 0
        assert s.documents == {}

        # Corrupt file should be backed up
        backup = STATE_PATH.with_suffix(".json.bak")
        assert backup.exists()
        assert backup.read_text() == "{invalid json!!!"

    def test_empty_file_recovers_to_defaults(self, tmp_path):
        from distillate.state import STATE_PATH, State

        STATE_PATH.write_text("")
        s = State()
        assert s.zotero_library_version == 0


class TestStateBasics:
    def test_fresh_state_has_defaults(self):
        from distillate.state import State

        s = State()
        assert s.zotero_library_version == 0
        assert s.last_poll_timestamp is None
        assert s.documents == {}
        assert s.promoted_papers == []

    def test_save_and_reload(self, tmp_path):
        from distillate.state import State

        s = State()
        s.zotero_library_version = 42
        s.touch_poll_timestamp()
        s.save()

        s2 = State()
        assert s2.zotero_library_version == 42
        assert s2.last_poll_timestamp is not None

    def test_save_persists_data(self, tmp_path):
        from distillate.state import State

        s = State()
        s.zotero_library_version = 7
        s.save()

        # Verify round-trip via a fresh State (regardless of backend)
        s2 = State()
        assert s2.zotero_library_version == 7


class TestDocumentCRUD:
    def test_add_and_get_document(self):
        from distillate.state import State

        s = State()
        s.add_document(
            zotero_item_key="ABC123",
            zotero_attachment_key="ATT456",
            zotero_attachment_md5="md5hash",
            remarkable_doc_name="Test Paper",
            title="Test Paper Title",
            authors=["Author A"],
        )
        assert s.has_document("ABC123")
        doc = s.get_document("ABC123")
        assert doc["title"] == "Test Paper Title"
        assert doc["status"] == "on_remarkable"
        assert doc["processed_at"] is None

    def test_set_status(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        s.set_status("K1", "awaiting_pdf")
        assert s.get_document("K1")["status"] == "awaiting_pdf"

    def test_mark_processed(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        s.mark_processed("K1", summary="Great paper")
        doc = s.get_document("K1")
        assert doc["status"] == "processed"
        assert doc["summary"] == "Great paper"
        assert doc["processed_at"] is not None

    def test_mark_processed_preserves_date(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        s.mark_processed("K1", summary="v1")
        first_date = s.get_document("K1")["processed_at"]

        # Second mark_processed should NOT overwrite the date
        s.mark_processed("K1", summary="v2")
        assert s.get_document("K1")["processed_at"] == first_date
        assert s.get_document("K1")["summary"] == "v2"

    def test_mark_processed_removes_from_promoted(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        s.promoted_papers = ["K1", "K2"]
        s.mark_processed("K1")
        assert "K1" not in s.promoted_papers
        assert "K2" in s.promoted_papers

    def test_mark_deleted(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        s.mark_deleted("K1")
        assert s.get_document("K1")["status"] == "deleted"

    def test_documents_with_status(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "T1", ["A"])
        s.add_document("K2", "A2", "md5", "doc2", "T2", ["A"])
        s.mark_processed("K1")
        assert len(s.documents_with_status("processed")) == 1
        assert len(s.documents_with_status("on_remarkable")) == 1

    def test_documents_processed_since(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "T1", ["A"])
        s.add_document("K2", "A2", "md5", "doc2", "T2", ["A"])
        s.mark_processed("K1")
        s.mark_processed("K2")

        # All should be returned with a very old cutoff
        result = s.documents_processed_since("2000-01-01")
        assert len(result) == 2

        # None with a future cutoff
        result = s.documents_processed_since("2099-01-01")
        assert len(result) == 0


class TestDuplicateDetection:
    def test_find_by_doi_found(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "Title One", ["A"],
                        metadata={"doi": "10.1234/test"})
        result = s.find_by_doi("10.1234/test")
        assert result is not None
        assert result["title"] == "Title One"

    def test_find_by_doi_not_found(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "Title", ["A"],
                        metadata={"doi": "10.1234/test"})
        assert s.find_by_doi("10.9999/other") is None

    def test_find_by_doi_empty(self):
        from distillate.state import State

        s = State()
        assert s.find_by_doi("") is None
        assert s.find_by_doi(None) is None

    def test_find_by_title_found(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "My Great Paper", ["A"])
        result = s.find_by_title("my great paper")
        assert result is not None
        assert result["zotero_item_key"] == "K1"

    def test_find_by_title_case_insensitive(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "Attention Is All You Need", ["A"])
        assert s.find_by_title("ATTENTION IS ALL YOU NEED") is not None
        assert s.find_by_title("attention is all you need") is not None

    def test_find_by_title_not_found(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "Paper A", ["A"])
        assert s.find_by_title("Paper B") is None

    def test_find_by_title_empty(self):
        from distillate.state import State

        s = State()
        assert s.find_by_title("") is None
        assert s.find_by_title(None) is None

    def test_find_by_title_strips_whitespace(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "  Spaced Title  ", ["A"])
        assert s.find_by_title("Spaced Title") is not None


class TestRemoveDocument:
    def test_remove_existing(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "Title One", ["A"])
        assert s.has_document("K1")
        assert s.remove_document("K1") is True
        assert not s.has_document("K1")

    def test_remove_nonexistent(self):
        from distillate.state import State

        s = State()
        assert s.remove_document("NOPE") is False

    def test_remove_cleans_promoted_list(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "Title One", ["A"])
        s.promoted_papers = ["K1", "K2"]
        s.pending_promotions = ["K1"]

        s.remove_document("K1")
        assert "K1" not in s.promoted_papers
        assert "K2" in s.promoted_papers
        assert "K1" not in s.pending_promotions

    def test_remove_persists(self):
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc1", "Title", ["A"])
        s.remove_document("K1")
        s.save()

        s2 = State()
        assert not s2.has_document("K1")


class TestPromotedPapers:
    def test_promoted_papers_roundtrip(self):
        from distillate.state import State

        s = State()
        s.promoted_papers = ["A", "B"]
        s.save()

        s2 = State()
        assert s2.promoted_papers == ["A", "B"]

    def test_pending_promotions(self):
        from distillate.state import State

        s = State()
        s.pending_promotions = ["X", "Y"]
        assert s.pending_promotions == ["X", "Y"]


# ---------------------------------------------------------------------------
# Migrated from test_v016.py
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture()
def populated_state_v016():
    """Create a state with papers in various statuses (from test_v016)."""
    from distillate.state import State

    s = State()
    s.add_document("K1", "A1", "md5", "paper_one", "Attention Is All You Need",
                   ["Vaswani, A.", "Shazeer, N."], status="on_remarkable")
    s.add_document("K2", "A2", "md5", "paper_two", "Scaling Laws for Neural LMs",
                   ["Kaplan, J."], status="processed",
                   metadata={"doi": "10.1234/test"})
    s.add_document("K3", "A3", "md5", "paper_three", "BERT: Pre-training",
                   ["Devlin, J."], status="awaiting_pdf")
    s.add_document("K4", "A4", "md5", "paper_four", "GPT-4 Technical Report",
                   ["OpenAI"], status="on_remarkable")
    s.promoted_papers = ["K1"]
    s.pending_promotions = ["K4"]
    s.save()
    return s


class TestRemoveDocumentExtended:
    def test_remove_from_find_by_doi(self, populated_state_v016):
        """After removal, find_by_doi should not find the document."""
        s = populated_state_v016
        assert s.find_by_doi("10.1234/test") is not None
        s.remove_document("K2")
        assert s.find_by_doi("10.1234/test") is None

    def test_remove_from_find_by_title(self, populated_state_v016):
        """After removal, find_by_title should not find the document."""
        s = populated_state_v016
        assert s.find_by_title("Attention Is All You Need") is not None
        s.remove_document("K1")
        assert s.find_by_title("Attention Is All You Need") is None

    def test_remove_from_documents_with_status(self, populated_state_v016):
        """After removal, documents_with_status should not include it."""
        s = populated_state_v016
        on_rm_before = s.documents_with_status("on_remarkable")
        assert len(on_rm_before) == 2

        s.remove_document("K1")
        on_rm_after = s.documents_with_status("on_remarkable")
        assert len(on_rm_after) == 1
        assert on_rm_after[0]["title"] == "GPT-4 Technical Report"

    def test_remove_cleans_only_targeted_lists(self, populated_state_v016):
        """Removing K1 cleans promoted but not pending (K4 is pending)."""
        s = populated_state_v016
        s.remove_document("K1")
        assert "K1" not in s.promoted_papers
        assert "K4" in s.pending_promotions  # untouched

    def test_remove_nonexistent_key_returns_false(self):
        from distillate.state import State
        s = State()
        assert s.remove_document("DOES_NOT_EXIST") is False


class TestProcessingState:
    def test_processing_status_set_and_found(self):
        """Verify 'processing' is a valid status the state machine handles."""
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        s.set_status("K1", "processing")

        processing = s.documents_with_status("processing")
        assert len(processing) == 1
        assert processing[0]["title"] == "Title"

    def test_processing_then_mark_processed(self):
        """Paper goes on_remarkable -> processing -> processed."""
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        assert s.get_document("K1")["status"] == "on_remarkable"

        s.set_status("K1", "processing")
        assert s.get_document("K1")["status"] == "processing"

        s.mark_processed("K1", summary="Done")
        assert s.get_document("K1")["status"] == "processed"

    def test_processing_persists_across_reload(self):
        """Processing status survives save/reload cycle."""
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        s.set_status("K1", "processing")
        s.save()

        s2 = State()
        assert s2.get_document("K1")["status"] == "processing"
        assert len(s2.documents_with_status("processing")) == 1


class TestLocking:
    def test_acquire_and_release(self):
        from distillate.state import acquire_lock, release_lock

        assert acquire_lock() is True
        # Second acquire should fail (same process is alive)
        assert acquire_lock() is False
        release_lock()

    def test_release_idempotent(self):
        from distillate.state import release_lock

        # Should not raise even if no lock exists
        release_lock()

    def test_stale_lock_removed(self, tmp_path):
        from distillate.state import acquire_lock, release_lock, LOCK_PATH

        # Write a lock file with a dead PID
        LOCK_PATH.write_text("999999999")
        with patch("os.kill", side_effect=OSError("No such process")):
            assert acquire_lock() is True
        release_lock()

    def test_valid_lock_respected(self, tmp_path):
        from distillate.state import acquire_lock, LOCK_PATH

        # Write a lock with our own PID (alive process)
        LOCK_PATH.write_text(str(os.getpid()))
        assert acquire_lock() is False
        LOCK_PATH.unlink()


# ---------------------------------------------------------------------------
# Migrated from test_v070.py
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod
    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)
    return tmp_path


class TestSchemaVersioning:
    def test_fresh_state_has_schema_version(self, isolate_state):
        from distillate.state import State, _CURRENT_SCHEMA_VERSION
        s = State()
        assert s.schema_version == _CURRENT_SCHEMA_VERSION

    def test_legacy_state_loads_as_is(self, isolate_state):
        """Pre-v2 state files load as-is; migrations have been removed."""
        from distillate.state import STATE_PATH, State
        legacy = {
            "zotero_library_version": 42,
            "last_poll_timestamp": None,
            "documents": {},
            "promoted_papers": [],
        }
        STATE_PATH.write_text(json.dumps(legacy))
        s = State()
        assert s.zotero_library_version == 42

    def test_migration_is_idempotent(self, isolate_state):
        from distillate.state import STATE_PATH, State, _CURRENT_SCHEMA_VERSION
        data = {
            "schema_version": _CURRENT_SCHEMA_VERSION,
            "zotero_library_version": 10,
            "last_poll_timestamp": None,
            "documents": {},
            "promoted_papers": [],
        }
        STATE_PATH.write_text(json.dumps(data))
        s = State()
        assert s.schema_version == _CURRENT_SCHEMA_VERSION
        assert s.zotero_library_version == 10

    def test_future_version_loads_safely(self, isolate_state):
        from distillate.state import STATE_PATH, State
        data = {
            "schema_version": 999,
            "zotero_library_version": 50,
            "documents": {},
        }
        STATE_PATH.write_text(json.dumps(data))
        s = State()
        assert s.schema_version == 999
        assert s.zotero_library_version == 50

    def test_saved_state_preserves_schema_version(self, isolate_state):
        from distillate.state import State, _CURRENT_SCHEMA_VERSION
        s = State()
        s.zotero_library_version = 99
        s.save()
        # Verify via round-trip (works with both JSON and SQLite backends)
        s2 = State()
        assert s2.schema_version == _CURRENT_SCHEMA_VERSION
        assert s2.zotero_library_version == 99
