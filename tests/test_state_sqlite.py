# Covers: distillate/state_sqlite.py, distillate/db.py
"""Tests for the SQLite state backend: schema, round-trip, migration, updated_at."""

import json
import sqlite3

import pytest

from distillate import db, state_sqlite


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Fresh in-memory-like SQLite connection for each test."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.close()
    c = db.get_connection(tmp_path / "test.db")
    yield c
    db.close()


class TestSchema:
    def test_tables_created(self, conn):
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "meta" in tables
        assert "documents" in tables
        assert "workspaces" in tables
        assert "experiments" in tables
        assert "agents" in tables
        assert "harnesses" in tables
        assert "promoted_papers" in tables
        assert "pending_promotions" in tables

    def test_wal_mode(self, conn):
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


class TestRoundTrip:
    def test_empty_state_roundtrips(self, conn):
        data = state_sqlite.load_all(conn)
        assert data["schema_version"] == 3
        assert data["documents"] == {}
        assert data["experiments"] == {}

    def test_meta_roundtrips(self, conn):
        data = state_sqlite.load_all(conn)
        data["zotero_library_version"] = 42
        data["last_poll_timestamp"] = "2026-01-01T00:00:00Z"
        data["last_cloud_sync_at"] = "2026-01-02T12:00:00Z"
        state_sqlite.save_all(data, conn)

        loaded = state_sqlite.load_all(conn)
        assert loaded["zotero_library_version"] == 42
        assert loaded["last_poll_timestamp"] == "2026-01-01T00:00:00Z"
        assert loaded["last_cloud_sync_at"] == "2026-01-02T12:00:00Z"

    def test_documents_roundtrip(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["ABC123"] = {
            "zotero_item_key": "ABC123",
            "title": "Test Paper",
            "authors": ["Author A", "Author B"],
            "status": "on_remarkable",
            "metadata": {"doi": "10.1234/test"},
            "summary": "",
            "engagement": 0.75,
        }
        state_sqlite.save_all(data, conn)

        loaded = state_sqlite.load_all(conn)
        doc = loaded["documents"]["ABC123"]
        assert doc["title"] == "Test Paper"
        assert doc["authors"] == ["Author A", "Author B"]
        assert doc["status"] == "on_remarkable"
        assert doc["metadata"]["doi"] == "10.1234/test"
        assert doc["engagement"] == 0.75

    def test_projects_roundtrip(self, conn):
        data = state_sqlite.load_all(conn)
        data["experiments"]["proj-1"] = {
            "id": "proj-1",
            "name": "Test Project",
            "status": "tracking",
            "workspace_id": "ws-1",
            "runs": {"run-1": {"id": "run-1", "status": "completed"}},
        }
        state_sqlite.save_all(data, conn)

        loaded = state_sqlite.load_all(conn)
        proj = loaded["experiments"]["proj-1"]
        assert proj["name"] == "Test Project"
        assert proj["runs"]["run-1"]["status"] == "completed"

    def test_workspaces_roundtrip(self, conn):
        data = state_sqlite.load_all(conn)
        data["workspaces"]["ws-1"] = {
            "id": "ws-1",
            "name": "My Workspace",
            "status": "active",
            "repos": [{"path": "/code/repo", "name": "repo"}],
            "canvases": {},
        }
        state_sqlite.save_all(data, conn)

        loaded = state_sqlite.load_all(conn)
        ws = loaded["workspaces"]["ws-1"]
        assert ws["name"] == "My Workspace"
        assert ws["repos"][0]["path"] == "/code/repo"

    def test_agents_roundtrip(self, conn):
        data = state_sqlite.load_all(conn)
        data["agents"]["nicolas"] = {
            "id": "nicolas",
            "name": "Nicolas",
            "agent_type": "claude",
            "session_status": "stopped",
        }
        state_sqlite.save_all(data, conn)

        loaded = state_sqlite.load_all(conn)
        agent = loaded["agents"]["nicolas"]
        assert agent["name"] == "Nicolas"
        assert agent["session_status"] == "stopped"

    def test_promoted_papers_roundtrip(self, conn):
        data = state_sqlite.load_all(conn)
        data["promoted_papers"] = ["A", "B", "C"]
        state_sqlite.save_all(data, conn)

        loaded = state_sqlite.load_all(conn)
        assert loaded["promoted_papers"] == ["A", "B", "C"]

    def test_pending_promotions_roundtrip(self, conn):
        data = state_sqlite.load_all(conn)
        data["pending_promotions"] = ["X", "Y"]
        state_sqlite.save_all(data, conn)

        loaded = state_sqlite.load_all(conn)
        assert loaded["pending_promotions"] == ["X", "Y"]


class TestUpdatedAt:
    def test_new_document_gets_updated_at(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["DOC1"] = {"title": "Paper 1", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        row = conn.execute(
            "SELECT updated_at FROM documents WHERE zotero_item_key = 'DOC1'"
        ).fetchone()
        assert row["updated_at"] is not None

    def test_unchanged_document_preserves_updated_at(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["DOC1"] = {"title": "Paper 1", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        ts1 = conn.execute(
            "SELECT updated_at FROM documents WHERE zotero_item_key = 'DOC1'"
        ).fetchone()["updated_at"]

        # Save again without changes
        state_sqlite.save_all(data, conn)

        ts2 = conn.execute(
            "SELECT updated_at FROM documents WHERE zotero_item_key = 'DOC1'"
        ).fetchone()["updated_at"]

        assert ts1 == ts2  # unchanged — timestamp preserved

    def test_changed_document_updates_updated_at(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["DOC1"] = {"title": "Paper 1", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        ts1 = conn.execute(
            "SELECT updated_at FROM documents WHERE zotero_item_key = 'DOC1'"
        ).fetchone()["updated_at"]

        # Modify the document
        data["documents"]["DOC1"]["status"] = "processed"
        state_sqlite.save_all(data, conn)

        ts2 = conn.execute(
            "SELECT updated_at FROM documents WHERE zotero_item_key = 'DOC1'"
        ).fetchone()["updated_at"]

        assert ts2 >= ts1  # timestamp advanced (or equal if sub-ms)


class TestSoftDelete:
    def test_removed_document_gets_deleted_at(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["DEL1"] = {"title": "To Delete", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        # Remove from data dict
        del data["documents"]["DEL1"]
        state_sqlite.save_all(data, conn)

        row = conn.execute(
            "SELECT deleted_at FROM documents WHERE zotero_item_key = 'DEL1'"
        ).fetchone()
        assert row is not None
        assert row["deleted_at"] is not None

    def test_deleted_document_not_in_load(self, conn):
        data = state_sqlite.load_all(conn)
        data["documents"]["DEL2"] = {"title": "Deleted", "status": "tracked"}
        state_sqlite.save_all(data, conn)

        del data["documents"]["DEL2"]
        state_sqlite.save_all(data, conn)

        loaded = state_sqlite.load_all(conn)
        assert "DEL2" not in loaded["documents"]


class TestCascadeSafetyNet:
    """Guards against catastrophic cascade soft-deletes from stale/partial state.

    Regression coverage for the 2026-04-21 incident where a stale state._data
    with a reduced ``workspaces`` dict silently tombstoned 5 workspaces in
    a single save_all() call.
    """

    def test_mass_reduction_refused(self, conn):
        """The 2026-04-21 incident shape: 5 workspaces wiped in one save."""
        data = state_sqlite.load_all(conn)
        data["workspaces"] = {
            f"w{i}": {"id": f"w{i}", "name": f"W{i}"} for i in range(6)
        }
        state_sqlite.save_all(data, conn)

        # Drop all but one — should be refused (5 cascade deletes)
        data["workspaces"] = {"w0": data["workspaces"]["w0"]}
        state_sqlite.save_all(data, conn)

        alive = conn.execute(
            "SELECT COUNT(*) FROM workspaces WHERE deleted_at IS NULL"
        ).fetchone()[0]
        assert alive == 6, "safety net should refuse mass-cascade"

    def test_empty_dict_from_populated_db_refused_at_threshold(self, conn):
        """Empty dict against many DB rows is the clearest stale-state signal."""
        data = state_sqlite.load_all(conn)
        data["workspaces"] = {
            f"w{i}": {"id": f"w{i}", "name": f"W{i}"} for i in range(5)
        }
        state_sqlite.save_all(data, conn)

        data["workspaces"] = {}
        state_sqlite.save_all(data, conn)

        alive = conn.execute(
            "SELECT COUNT(*) FROM workspaces WHERE deleted_at IS NULL"
        ).fetchone()[0]
        assert alive == 5

    def test_corrupt_row_does_not_crash_load(self, conn):
        """A NULL/empty data column must not break the whole reload.

        Regression: a concurrent-connection race caused ``row["data"]`` to
        come back as None mid-iteration, raising TypeError out of
        json.loads() and aborting the entire reload — leaving callers with
        a stale state dict whose next save cascade-deleted real rows.
        """
        data = state_sqlite.load_all(conn)
        data["workspaces"] = {
            "good1": {"id": "good1", "name": "Good 1"},
            "good2": {"id": "good2", "name": "Good 2"},
        }
        state_sqlite.save_all(data, conn)

        # Simulate corruption by setting one row's data column to NULL.
        # Bypass the NOT NULL constraint by temporarily dropping it.
        conn.execute(
            "CREATE TABLE workspaces_tmp AS SELECT * FROM workspaces"
        )
        conn.execute("DROP TABLE workspaces")
        conn.execute("""
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY, name TEXT, status TEXT DEFAULT 'active',
                data TEXT, updated_at TEXT, deleted_at TEXT
            )
        """)
        conn.execute("INSERT INTO workspaces SELECT * FROM workspaces_tmp")
        conn.execute("UPDATE workspaces SET data = NULL WHERE id = 'good1'")
        conn.commit()

        # Load must not raise, and must return the non-corrupt row
        loaded = state_sqlite.load_all(conn)
        assert "good2" in loaded["workspaces"]
        assert "good1" not in loaded["workspaces"]  # skipped, not crashed

    def test_single_deletion_still_works(self, conn):
        data = state_sqlite.load_all(conn)
        data["workspaces"] = {
            f"w{i}": {"id": f"w{i}", "name": f"W{i}"} for i in range(4)
        }
        state_sqlite.save_all(data, conn)

        # Legitimate single delete: keep 3, drop 1
        del data["workspaces"]["w3"]
        state_sqlite.save_all(data, conn)

        alive = conn.execute(
            "SELECT COUNT(*) FROM workspaces WHERE deleted_at IS NULL"
        ).fetchone()[0]
        assert alive == 3


class TestJsonImport:
    def test_import_from_json(self, conn, tmp_path):
        state_json = {
            "schema_version": 2,
            "zotero_library_version": 10,
            "last_poll_timestamp": "2026-01-01T00:00:00Z",
            "documents": {
                "K1": {"title": "Paper 1", "status": "processed"},
                "K2": {"title": "Paper 2", "status": "on_remarkable"},
            },
            "promoted_papers": ["K1"],
            "experiments": {
                "P1": {"id": "P1", "name": "Proj 1", "status": "tracking"},
            },
        }
        json_path = tmp_path / "state.json"
        json_path.write_text(json.dumps(state_json))

        count = state_sqlite.import_from_json(json_path, conn)
        assert count == 3  # 2 documents + 1 project

        loaded = state_sqlite.load_all(conn)
        assert loaded["zotero_library_version"] == 10
        assert len(loaded["documents"]) == 2
        assert loaded["documents"]["K1"]["title"] == "Paper 1"
        assert loaded["experiments"]["P1"]["name"] == "Proj 1"
        assert loaded["promoted_papers"] == ["K1"]

    def test_import_nonexistent_file(self, conn, tmp_path):
        count = state_sqlite.import_from_json(tmp_path / "nope.json", conn)
        assert count == 0
