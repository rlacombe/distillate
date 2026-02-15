"""Tests for state management: State class, locking, atomic writes."""

import json
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)
    yield tmp_path


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

    def test_atomic_write_creates_valid_json(self, tmp_path):
        from distillate.state import State, STATE_PATH

        s = State()
        s.zotero_library_version = 7
        s.save()

        data = json.loads(STATE_PATH.read_text())
        assert data["zotero_library_version"] == 7


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
