# Covers: distillate/sync.py, distillate/state_sqlite.py
"""Tests for delta sync: changed_since queries, tombstones, last_pushed_at."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from distillate import db, state_sqlite
from distillate.state import State


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.close()
    c = db.get_connection(tmp_path / "test.db")
    yield c
    db.close()


class TestChangedDocumentsSince:
    def test_returns_all_when_since_is_none(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["A"] = {"title": "Paper A", "status": "tracked"}
        data["documents"]["B"] = {"title": "Paper B", "status": "processed"}
        state_sqlite.save_all(data, conn)

        changed = state_sqlite.changed_documents_since(None, conn)
        assert len(changed) == 2
        assert "A" in changed
        assert "B" in changed

    def test_returns_only_changed_after_timestamp(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["OLD"] = {"title": "Old Paper", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        # Record the timestamp after first save
        ts = conn.execute(
            "SELECT updated_at FROM documents WHERE zotero_item_key = 'OLD'"
        ).fetchone()["updated_at"]

        # Add a new document
        data["documents"]["NEW"] = {"title": "New Paper", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        changed = state_sqlite.changed_documents_since(ts, conn)
        assert "NEW" in changed
        # OLD should NOT be in changed (its data didn't change)
        assert "OLD" not in changed

    def test_includes_tombstones(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["DEL"] = {"title": "To Delete", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        ts = conn.execute(
            "SELECT updated_at FROM documents WHERE zotero_item_key = 'DEL'"
        ).fetchone()["updated_at"]

        # Remove the document (soft-delete)
        del data["documents"]["DEL"]
        state_sqlite.save_all(data, conn)

        changed = state_sqlite.changed_documents_since(ts, conn)
        assert "DEL" in changed
        assert "deleted_at" in changed["DEL"]


class TestChangedProjectsSince:
    def test_returns_all_when_since_is_none(self, conn):
        data = state_sqlite.load_all(conn)
        data["experiments"]["P1"] = {"id": "P1", "name": "Proj 1", "status": "tracking"}
        state_sqlite.save_all(data, conn)

        changed = state_sqlite.changed_experiments_since(None, conn)
        assert "P1" in changed

    def test_includes_tombstones(self, conn):
        data = state_sqlite.load_all(conn)
        data["experiments"]["P1"] = {"id": "P1", "name": "Proj 1", "status": "tracking"}
        state_sqlite.save_all(data, conn)

        ts = conn.execute(
            "SELECT updated_at FROM experiments WHERE id = 'P1'"
        ).fetchone()["updated_at"]

        del data["experiments"]["P1"]
        state_sqlite.save_all(data, conn)

        changed = state_sqlite.changed_experiments_since(ts, conn)
        assert "P1" in changed
        assert "deleted_at" in changed["P1"]


class TestMetaHelpers:
    def test_get_set_roundtrip(self, conn):
        state_sqlite.set_meta("last_pushed_at", "2026-01-01T00:00:00Z", conn)
        assert state_sqlite.get_meta("last_pushed_at", conn) == "2026-01-01T00:00:00Z"

    def test_get_returns_none_for_missing(self, conn):
        assert state_sqlite.get_meta("nonexistent", conn) is None

    def test_set_overwrites(self, conn):
        state_sqlite.set_meta("key", "v1", conn)
        state_sqlite.set_meta("key", "v2", conn)
        assert state_sqlite.get_meta("key", conn) == "v2"


class TestHardDelete:
    def test_removes_old_tombstones(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["OLD"] = {"title": "Old", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        # Manually set deleted_at to 60 days ago
        old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        conn.execute(
            "UPDATE documents SET deleted_at = ?, updated_at = ? WHERE zotero_item_key = 'OLD'",
            (old_ts, old_ts),
        )
        conn.commit()

        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        n = state_sqlite.hard_delete_before(cutoff, conn)
        assert n == 1

        row = conn.execute(
            "SELECT * FROM documents WHERE zotero_item_key = 'OLD'"
        ).fetchone()
        assert row is None

    def test_preserves_recent_tombstones(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["RECENT"] = {"title": "Recent", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        # Delete it (recent tombstone)
        del data["documents"]["RECENT"]
        state_sqlite.save_all(data, conn)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        n = state_sqlite.hard_delete_before(cutoff, conn)
        assert n == 0  # too recent to hard-delete


class TestMergeTombstones:
    """Test that cloud pull merge handles remote tombstones correctly."""

    def test_merge_document_tombstone_marks_deleted(self):
        s = State()
        s.add_document(
            zotero_item_key="DOC1",
            zotero_attachment_key="ATT1",
            zotero_attachment_md5="md5",
            remarkable_doc_name="Paper",
            title="Paper Title",
            authors=["Author"],
        )

        from distillate.cloud_sync import _merge_documents
        _merge_documents(s, {"DOC1": {"deleted_at": "2026-01-01T00:00:00Z"}})

        doc = s.get_document("DOC1")
        assert doc["status"] == "deleted"

    def test_merge_document_tombstone_ignores_unknown(self):
        s = State()
        from distillate.cloud_sync import _merge_documents
        # Should not crash on unknown document
        _merge_documents(s, {"UNKNOWN": {"deleted_at": "2026-01-01T00:00:00Z"}})
        assert not s.has_document("UNKNOWN")

    def test_merge_project_tombstone_removes(self):
        s = State()
        s._data.setdefault("experiments", {})
        s._data["experiments"]["P1"] = {"id": "P1", "name": "Proj", "status": "tracking"}

        from distillate.cloud_sync import _merge_experiments
        _merge_experiments(s, {"P1": {"deleted_at": "2026-01-01T00:00:00Z"}})

        assert not s.has_experiment("P1")

    def test_merge_project_tombstone_ignores_unknown(self):
        s = State()
        from distillate.cloud_sync import _merge_experiments
        _merge_experiments(s, {"UNKNOWN": {"deleted_at": "2026-01-01T00:00:00Z"}})
        assert not s.has_experiment("UNKNOWN")
