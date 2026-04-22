"""SQLite persistence backend for Distillate state.

Provides ``load_all()`` and ``save_all()`` to round-trip between the
dict-based ``State._data`` format and SQLite rows.  The ``State`` class
keeps its dict-based API — this module only handles storage.

Per-row ``updated_at`` is maintained automatically: rows are only written
when their JSON data actually changes, preserving timestamps for unchanged
entities (foundation for PR #3 delta sync).
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from distillate import db as _db

log = logging.getLogger(__name__)

_CURRENT_SCHEMA_VERSION = 3  # matches state.py's schema_version

# Serialize ALL access to the shared module-level sqlite connection.
# The connection is opened with check_same_thread=False so it can be used
# from FastAPI's threadpool, but Python sqlite3 raises SQLITE_MISUSE
# (InterfaceError) when two threads hit the same connection's prepared
# statements concurrently, and even concurrent reads can return garbage
# (e.g. a NOT NULL column coming back as None) during a writer's
# transaction. Every load_all() and save_all() must hold this lock.
_db_lock = threading.RLock()
# Backward-compat alias (external callers may import this name).
_save_lock = _db_lock


# ---------------------------------------------------------------------------
# Load: SQLite -> dict  (same shape as state.json)
# ---------------------------------------------------------------------------


def _parse_row_data(raw, table: str, row_id: str) -> dict | None:
    """Parse the JSON ``data`` column for an entity row.

    Returns the parsed dict, or None if the column is missing/corrupt. Does
    not raise — a single bad row must not take down the entire reload. A
    transient ``None`` here usually means a concurrent-connection read race
    on the shared sqlite connection; the row's real data is still on disk.
    """
    if raw is None or raw == "":
        log.error(
            "Corrupt/empty data column for %s row '%s' — skipping. "
            "This is usually a concurrent-read race on the shared sqlite "
            "connection; restart picks up the real row.",
            table, row_id,
        )
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        log.error(
            "Failed to parse JSON for %s row '%s': %s — skipping",
            table, row_id, exc,
        )
        return None


def load_all(conn: sqlite3.Connection | None = None) -> Dict[str, Any]:
    """Read all state from SQLite and return a dict identical to _load_raw().

    If the database is empty, returns a default state dict.
    """
    if conn is None:
        conn = _db.get_connection()

    with _db_lock:
        return _load_all_locked(conn)


def _load_all_locked(conn: sqlite3.Connection) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "schema_version": _CURRENT_SCHEMA_VERSION,
        "zotero_library_version": 0,
        "last_poll_timestamp": None,
        "documents": {},
        "promoted_papers": [],
        "experiments": {},
    }

    # Meta (scalar values)
    for row in conn.execute("SELECT key, value FROM meta").fetchall():
        key, value = row["key"], row["value"]
        if key == "zotero_library_version":
            data[key] = int(value) if value else 0
        elif key == "schema_version":
            data[key] = int(value) if value else _CURRENT_SCHEMA_VERSION
        else:
            data[key] = value

    # Documents
    for row in conn.execute(
        "SELECT zotero_item_key, data FROM documents WHERE deleted_at IS NULL"
    ).fetchall():
        doc = _parse_row_data(row["data"], "documents", row["zotero_item_key"])
        if doc is not None:
            data["documents"][row["zotero_item_key"]] = doc

    # Promoted papers
    data["promoted_papers"] = [
        row["zotero_item_key"]
        for row in conn.execute("SELECT zotero_item_key FROM promoted_papers").fetchall()
    ]

    # Pending promotions
    data["pending_promotions"] = [
        row["zotero_item_key"]
        for row in conn.execute("SELECT zotero_item_key FROM pending_promotions").fetchall()
    ]

    # Workspaces
    data["workspaces"] = {}
    for row in conn.execute(
        "SELECT id, data FROM workspaces WHERE deleted_at IS NULL"
    ).fetchall():
        ws = _parse_row_data(row["data"], "workspaces", row["id"])
        if ws is not None:
            data["workspaces"][row["id"]] = ws

    # Experiments
    data["experiments"] = {}
    for row in conn.execute(
        "SELECT id, data FROM experiments WHERE deleted_at IS NULL"
    ).fetchall():
        exp = _parse_row_data(row["data"], "experiments", row["id"])
        if exp is not None:
            data["experiments"][row["id"]] = exp

    # Agents
    data["agents"] = {}
    for row in conn.execute(
        "SELECT id, data FROM agents WHERE deleted_at IS NULL"
    ).fetchall():
        agent = _parse_row_data(row["data"], "agents", row["id"])
        if agent is not None:
            data["agents"][row["id"]] = agent

    # Harnesses
    data["harnesses"] = {}
    for row in conn.execute("SELECT id, data FROM harnesses").fetchall():
        h = _parse_row_data(row["data"], "harnesses", row["id"])
        if h is not None:
            data["harnesses"][row["id"]] = h

    return data


# ---------------------------------------------------------------------------
# Save: dict -> SQLite  (smart diff — only write changed rows)
# ---------------------------------------------------------------------------


def save_all(data: Dict[str, Any], conn: sqlite3.Connection | None = None) -> None:
    """Write the full state dict to SQLite.

    Only rows whose JSON data has changed are written, preserving
    ``updated_at`` for unchanged entities.
    """
    if conn is None:
        conn = _db.get_connection()

    now = datetime.now(timezone.utc).isoformat()

    with _db_lock, conn:  # serialize threads; auto-commit on success, rollback on error
        _save_meta(conn, data)
        _save_documents(conn, data.get("documents", {}), now)
        _save_list_table(conn, "promoted_papers", data.get("promoted_papers", []))
        _save_list_table(conn, "pending_promotions", data.get("pending_promotions", []))
        _save_entities(conn, "workspaces", data.get("workspaces", {}), now,
                       extract=_extract_workspace)
        _save_entities(conn, "experiments", data.get("experiments", {}), now,
                       extract=_extract_project)
        _save_entities(conn, "agents", data.get("agents", {}), now,
                       extract=_extract_agent)
        _save_harnesses(conn, data.get("harnesses", {}))


# ---------------------------------------------------------------------------
# Import: state.json -> SQLite  (one-shot migration)
# ---------------------------------------------------------------------------


def import_from_json(json_path: Path, conn: sqlite3.Connection | None = None) -> int:
    """Migrate state.json into SQLite. Returns the number of entities imported."""
    if not json_path.exists():
        return 0

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    save_all(raw, conn=conn)

    count = (
        len(raw.get("documents", {}))
        + len(raw.get("experiments", {}))
        + len(raw.get("workspaces", {}))
        + len(raw.get("agents", {}))
    )
    log.info("Imported %d entities from %s into SQLite", count, json_path)
    return count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _save_meta(conn: sqlite3.Connection, data: dict) -> None:
    """Upsert scalar meta values."""
    meta_keys = {
        "schema_version", "zotero_library_version", "last_poll_timestamp",
        "last_cloud_sync_at", "last_pushed_at",
    }
    for key in meta_keys:
        if key in data:
            value = str(data[key]) if data[key] is not None else None
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def _save_documents(conn: sqlite3.Connection, documents: dict, now: str) -> None:
    """Upsert documents, only writing rows that changed."""
    # Build set of current keys for deletion detection
    existing = {
        row["zotero_item_key"]
        for row in conn.execute(
            "SELECT zotero_item_key FROM documents WHERE deleted_at IS NULL"
        )
    }

    for key, doc in documents.items():
        doc_json = json.dumps(doc, sort_keys=True, default=str)
        status = doc.get("status", "")
        title = doc.get("title", "")
        conn.execute(
            "INSERT INTO documents (zotero_item_key, status, title, data, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(zotero_item_key) DO UPDATE SET "
            "  status = excluded.status, "
            "  title = excluded.title, "
            "  data = excluded.data, "
            "  updated_at = CASE WHEN data != excluded.data "
            "    THEN excluded.updated_at ELSE updated_at END, "
            "  deleted_at = NULL "
            "WHERE data != excluded.data OR deleted_at IS NOT NULL",
            (key, status, title, doc_json, now),
        )
        existing.discard(key)

    # Soft-delete removed documents
    for key in existing:
        conn.execute(
            "UPDATE documents SET deleted_at = ?, updated_at = ? "
            "WHERE zotero_item_key = ? AND deleted_at IS NULL",
            (now, now, key),
        )


def _save_list_table(conn: sqlite3.Connection, table: str, items: list) -> None:
    """Replace a simple list table (promoted_papers, pending_promotions)."""
    conn.execute(f"DELETE FROM {table}")  # noqa: S608 — table name is hardcoded
    for item in items:
        conn.execute(f"INSERT OR IGNORE INTO {table} (zotero_item_key) VALUES (?)", (item,))


def _save_entities(
    conn: sqlite3.Connection,
    table: str,
    entities: dict,
    now: str,
    extract: callable,
) -> None:
    """Generic upsert for entity tables (workspaces, projects, agents)."""
    existing = {
        row["id"]
        for row in conn.execute(
            f"SELECT id FROM {table} WHERE deleted_at IS NULL"  # noqa: S608
        )
    }

    for entity_id, entity in entities.items():
        entity_json = json.dumps(entity, sort_keys=True, default=str)
        cols = extract(entity)  # dict of extracted column values
        col_names = ", ".join(cols.keys())
        placeholders = ", ".join(["?"] * len(cols))

        # Build the upsert
        set_clauses = ", ".join(f"{c} = excluded.{c}" for c in cols)
        conn.execute(
            f"INSERT INTO {table} (id, {col_names}, data, updated_at) "  # noqa: S608
            f"VALUES (?, {placeholders}, ?, ?) "
            f"ON CONFLICT(id) DO UPDATE SET "
            f"  {set_clauses}, "
            f"  data = excluded.data, "
            f"  updated_at = CASE WHEN data != excluded.data "
            f"    THEN excluded.updated_at ELSE updated_at END, "
            f"  deleted_at = NULL "
            f"WHERE data != excluded.data OR deleted_at IS NOT NULL",
            (entity_id, *cols.values(), entity_json, now),
        )
        existing.discard(entity_id)

    # Safety net: refuse catastrophic cascade deletes. The caller contract
    # is "passed dict == full intended state" — but a stale/partial
    # state._data (e.g. from a threading race on the shared SQLite
    # connection, or a caller that forgot to reload before save) can
    # silently tombstone every missing entity. On 2026-04-21 this wiped 5
    # workspaces in a single save. Block only at a threshold that catches
    # the observed failure modes (many rows gone at once) without blocking
    # legitimate single-entity deletes — which are indistinguishable from
    # a stale-state bug that drops one row.
    _MAX_CASCADE = 3
    if len(existing) >= _MAX_CASCADE:
        log.error(
            "Refusing cascade soft-delete on '%s': would tombstone %d row(s) "
            "in one save (passed dict has %d) — likely a stale/partial state "
            "dict. Delete explicitly if intentional.",
            table, len(existing), len(entities),
        )
        return

    # Soft-delete removed entities
    for entity_id in existing:
        log.info("Soft-deleting '%s' row: %s", table, entity_id)
        conn.execute(
            f"UPDATE {table} SET deleted_at = ?, updated_at = ? "  # noqa: S608
            f"WHERE id = ? AND deleted_at IS NULL",
            (now, now, entity_id),
        )


def _save_harnesses(conn: sqlite3.Connection, harnesses: dict) -> None:
    """Upsert harnesses (no updated_at / deleted_at needed)."""
    for h_id, harness in harnesses.items():
        h_json = json.dumps(harness, sort_keys=True, default=str)
        conn.execute(
            "INSERT INTO harnesses (id, data) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (h_id, h_json),
        )


# ---------------------------------------------------------------------------
# Delta queries (for PR #3 cloud sync)
# ---------------------------------------------------------------------------


def changed_documents_since(
    since: str | None, conn: sqlite3.Connection | None = None,
) -> dict[str, dict]:
    """Return documents changed since *since* (ISO-8601), including tombstones.

    If *since* is None, returns ALL non-deleted documents (first sync).
    Tombstoned documents have a ``deleted_at`` key in their data dict.
    """
    if conn is None:
        conn = _db.get_connection()

    if since:
        rows = conn.execute(
            "SELECT zotero_item_key, data, deleted_at FROM documents "
            "WHERE updated_at > ? OR (deleted_at IS NOT NULL AND deleted_at > ?)",
            (since, since),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT zotero_item_key, data, deleted_at FROM documents"
        ).fetchall()

    result = {}
    for row in rows:
        doc = json.loads(row["data"])
        if row["deleted_at"]:
            doc["deleted_at"] = row["deleted_at"]
        result[row["zotero_item_key"]] = doc
    return result


def changed_experiments_since(
    since: str | None, conn: sqlite3.Connection | None = None,
) -> dict[str, dict]:
    """Return experiments changed since *since*, including tombstones."""
    if conn is None:
        conn = _db.get_connection()

    if since:
        rows = conn.execute(
            "SELECT id, data, deleted_at FROM experiments "
            "WHERE updated_at > ? OR (deleted_at IS NOT NULL AND deleted_at > ?)",
            (since, since),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, data, deleted_at FROM experiments"
        ).fetchall()

    result = {}
    for row in rows:
        proj = json.loads(row["data"])
        if row["deleted_at"]:
            proj["deleted_at"] = row["deleted_at"]
        result[row["id"]] = proj
    return result


def get_meta(key: str, conn: sqlite3.Connection | None = None) -> str | None:
    """Read a single meta value."""
    if conn is None:
        conn = _db.get_connection()
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str | None, conn: sqlite3.Connection | None = None) -> None:
    """Write a single meta value."""
    if conn is None:
        conn = _db.get_connection()
    with conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def hard_delete_before(cutoff: str, conn: sqlite3.Connection | None = None) -> int:
    """Permanently remove tombstoned rows older than *cutoff*. Returns count."""
    if conn is None:
        conn = _db.get_connection()
    total = 0
    with conn:
        for table, pk in [
            ("documents", "zotero_item_key"),
            ("experiments", "id"),
            ("workspaces", "id"),
            ("agents", "id"),
        ]:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE deleted_at IS NOT NULL AND deleted_at < ?",  # noqa: S608
                (cutoff,),
            )
            total += cur.rowcount
    return total


def _extract_workspace(ws: dict) -> dict:
    return {
        "name": ws.get("name", ""),
        "status": ws.get("status", "active"),
    }


def _extract_project(proj: dict) -> dict:
    return {
        "name": proj.get("name", ""),
        "status": proj.get("status", "tracking"),
        "workspace_id": proj.get("workspace_id", ""),
    }


def _extract_agent(agent: dict) -> dict:
    return {
        "name": agent.get("name", ""),
        "session_status": agent.get("session_status", "stopped"),
    }
