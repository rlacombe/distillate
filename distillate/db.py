"""SQLite database for Distillate state.

Replaces state.json with a WAL-mode SQLite database that stores entities
as JSON blobs with extracted columns for indexed queries and per-row
``updated_at`` timestamps (foundation for delta sync in PR #3).
"""

import json
import logging
import sqlite3
from pathlib import Path

from distillate.config import DB_PATH

log = logging.getLogger(__name__)

_conn: sqlite3.Connection | None = None

_SCHEMA_VERSION = 6

_SCHEMA_SQL = """\
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    zotero_item_key TEXT PRIMARY KEY,
    status          TEXT,
    title           TEXT,
    data            TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_doc_status  ON documents(status);
CREATE INDEX IF NOT EXISTS idx_doc_updated ON documents(updated_at);

CREATE TABLE IF NOT EXISTS promoted_papers (
    zotero_item_key TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS pending_promotions (
    zotero_item_key TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS workspaces (
    id         TEXT PRIMARY KEY,
    name       TEXT,
    status     TEXT DEFAULT 'active',
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at TEXT
);

-- Canonical experiments table (research directions / auto-research trackers).
-- Runs are embedded in data['runs'] and mirrored in the runs table.
CREATE TABLE IF NOT EXISTS experiments (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    status       TEXT DEFAULT 'tracking',
    workspace_id TEXT,
    data         TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_exp_workspace ON experiments(workspace_id);
CREATE INDEX IF NOT EXISTS idx_exp_updated   ON experiments(updated_at);

-- Individual runs extracted from experiments.data['runs'] for indexed queries.
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT NOT NULL,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    name          TEXT,
    status        TEXT,
    decision      TEXT,
    started_at    TEXT,
    completed_at  TEXT,
    data          TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (id, experiment_id)
);

-- Agent sessions extracted from experiments.data['sessions'] for indexed queries.
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT NOT NULL,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    status        TEXT,
    started_at    TEXT,
    data          TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (id, experiment_id)
);

CREATE TABLE IF NOT EXISTS agents (
    id             TEXT PRIMARY KEY,
    name           TEXT,
    session_status TEXT DEFAULT 'stopped',
    data           TEXT NOT NULL,
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    deleted_at     TEXT
);

CREATE TABLE IF NOT EXISTS harnesses (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    key             TEXT PRIMARY KEY,
    encrypted_value TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    source          TEXT
);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Return a module-level SQLite connection (created once, reused)."""
    global _conn
    if _conn is not None:
        return _conn

    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _init_schema(_conn)
    return _conn


def close() -> None:
    """Close the module-level connection (for tests / shutdown)."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create tables if missing and run any pending migrations."""
    conn.executescript(_SCHEMA_SQL)

    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < _SCHEMA_VERSION:
        _migrate(conn, version)
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    """Apply sequential schema migrations."""
    # Version 0 -> 1: initial schema (tables created by _SCHEMA_SQL above)
    if from_version < 1:
        log.info("SQLite schema initialized (version %d)", _SCHEMA_VERSION)
    # Version 1 -> 2: rename 'projects' table to 'experiments'
    if from_version < 2:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "projects" in tables and "experiments" not in tables:
            conn.executescript("""
                ALTER TABLE projects RENAME TO experiments;
                DROP INDEX IF EXISTS idx_proj_workspace;
                DROP INDEX IF EXISTS idx_proj_updated;
                CREATE INDEX IF NOT EXISTS idx_exp_workspace ON experiments(workspace_id);
                CREATE INDEX IF NOT EXISTS idx_exp_updated   ON experiments(updated_at);
            """)
            log.info("SQLite migration v2: renamed 'projects' table to 'experiments'")
    # Version 2 -> 3: add credentials table for encrypted secret storage
    if from_version < 3:
        # Credentials table is created by _SCHEMA_SQL, nothing to migrate
        log.info("SQLite migration v3: added credentials table for encrypted storage")
    # Version 3 -> 4: copy any projects rows orphaned in the 'projects' table into 'experiments'
    # This handles DBs where both tables were created simultaneously (v2 migration only ran
    # ALTER TABLE RENAME when 'experiments' didn't exist yet, so data could get stranded).
    if from_version < 4:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "projects" in tables:
            conn.execute("""
                INSERT OR IGNORE INTO experiments (id, name, status, workspace_id, data, updated_at, deleted_at)
                SELECT id, name, status, workspace_id, data, updated_at, deleted_at
                FROM projects
            """)
            moved = conn.execute("SELECT changes()").fetchone()[0]
            if moved:
                log.info("SQLite migration v4: copied %d orphaned rows from 'projects' → 'experiments'", moved)

    # Version 4 -> 5: Primitives v2 schema migration
    # - Ensure projects table has all experiment data (copy from experiments if empty)
    # - Populate normalized runs and sessions tables from projects.data JSON
    if from_version < 5:
        import json as _json

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        # Bootstrap projects from experiments if projects is empty
        if "experiments" in tables and "projects" in tables:
            proj_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            exp_count = conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
            if proj_count == 0 and exp_count > 0:
                conn.execute("""
                    INSERT OR IGNORE INTO projects
                        (id, name, status, workspace_id, data, updated_at, deleted_at)
                    SELECT id, name, status, workspace_id, data, updated_at, deleted_at
                    FROM experiments
                """)
                copied = conn.execute("SELECT changes()").fetchone()[0]
                log.info("SQLite migration v5: bootstrapped %d rows from experiments → projects", copied)

        # Populate runs/sessions from projects.data (only if projects table exists)
        run_count = session_count = 0
        if "projects" in tables:
            rows = conn.execute("SELECT id, data FROM projects WHERE deleted_at IS NULL").fetchall()
            for row in rows:
                proj_id = row[0]
                try:
                    proj = _json.loads(row[1])
                except (ValueError, TypeError):
                    continue
                for run_id, run_data in (proj.get("runs") or {}).items():
                    run_json = _json.dumps(run_data, sort_keys=True, default=str)
                    conn.execute(
                        "INSERT OR IGNORE INTO runs "
                        "(id, project_id, name, status, decision, started_at, completed_at, data) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (run_id, proj_id,
                         run_data.get("name", ""),
                         run_data.get("status", ""),
                         run_data.get("decision", ""),
                         run_data.get("started_at", ""),
                         run_data.get("completed_at", ""),
                         run_json),
                    )
                    run_count += 1
                for sess_id, sess_data in (proj.get("sessions") or {}).items():
                    sess_json = _json.dumps(sess_data, sort_keys=True, default=str)
                    conn.execute(
                        "INSERT OR IGNORE INTO sessions (id, project_id, status, started_at, data) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (sess_id, proj_id,
                         sess_data.get("status", ""),
                         sess_data.get("started_at", ""),
                         sess_json),
                    )
                    session_count += 1

        log.info(
            "SQLite migration v5: populated runs (%d rows) and sessions (%d rows)",
            run_count, session_count,
        )

    # Version 5 -> 6: make experiments the canonical table; rebuild runs/sessions with experiment_id FK
    if from_version < 6:
        import json as _json

        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        # Copy any projects rows not yet in experiments
        if "projects" in tables:
            conn.execute("""
                INSERT OR IGNORE INTO experiments (id, name, status, workspace_id, data, updated_at, deleted_at)
                SELECT id, name, status, workspace_id, data, updated_at, deleted_at
                FROM projects
            """)
            moved = conn.execute("SELECT changes()").fetchone()[0]
            if moved:
                log.info("SQLite migration v6: copied %d rows from projects → experiments", moved)

        # Rebuild runs table with experiment_id column (was project_id referencing projects)
        conn.executescript("""
            PRAGMA foreign_keys = OFF;
            DROP TABLE IF EXISTS runs;
            CREATE TABLE runs (
                id            TEXT NOT NULL,
                experiment_id TEXT NOT NULL REFERENCES experiments(id),
                name          TEXT,
                status        TEXT,
                decision      TEXT,
                started_at    TEXT,
                completed_at  TEXT,
                data          TEXT NOT NULL,
                updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                PRIMARY KEY (id, experiment_id)
            );
            CREATE INDEX IF NOT EXISTS idx_runs_experiment ON runs(experiment_id);
            CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);
            DROP TABLE IF EXISTS sessions;
            CREATE TABLE sessions (
                id            TEXT NOT NULL,
                experiment_id TEXT NOT NULL REFERENCES experiments(id),
                status        TEXT,
                started_at    TEXT,
                data          TEXT NOT NULL,
                updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                PRIMARY KEY (id, experiment_id)
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_experiment ON sessions(experiment_id);
            PRAGMA foreign_keys = ON;
        """)

        # Re-populate runs and sessions from experiments.data JSON
        rows = conn.execute("SELECT id, data FROM experiments WHERE deleted_at IS NULL").fetchall()
        run_count = session_count = 0
        for row in rows:
            exp_id = row[0]
            try:
                exp = _json.loads(row[1])
            except (ValueError, TypeError):
                continue
            for run_id, run_data in (exp.get("runs") or {}).items():
                conn.execute(
                    "INSERT OR IGNORE INTO runs "
                    "(id, experiment_id, name, status, decision, started_at, completed_at, data) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, exp_id,
                     run_data.get("name", ""), run_data.get("status", ""),
                     run_data.get("decision", ""), run_data.get("started_at", ""),
                     run_data.get("completed_at", ""),
                     _json.dumps(run_data, sort_keys=True, default=str)),
                )
                run_count += 1
            for sess_id, sess_data in (exp.get("sessions") or {}).items():
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (id, experiment_id, status, started_at, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (sess_id, exp_id,
                     sess_data.get("status", ""), sess_data.get("started_at", ""),
                     _json.dumps(sess_data, sort_keys=True, default=str)),
                )
                session_count += 1

        # Drop the legacy projects table now that experiments is canonical
        conn.executescript("""
            PRAGMA foreign_keys = OFF;
            DROP TABLE IF EXISTS projects;
            DROP INDEX IF EXISTS idx_proj_workspace;
            DROP INDEX IF EXISTS idx_proj_updated;
            PRAGMA foreign_keys = ON;
        """)

        log.info(
            "SQLite migration v6: experiments canonical, runs (%d) and sessions (%d) rebuilt, projects table dropped",
            run_count, session_count,
        )


# ---------------------------------------------------------------------------
# Credential storage (encrypted)
# ---------------------------------------------------------------------------


def get_credential(key: str) -> str | None:
    """Get encrypted credential value from database.

    Returns the base64-encoded Fernet ciphertext, or None if not found.
    Caller is responsible for decryption via credential_store.decrypt().
    """
    conn = get_connection()
    row = conn.execute("SELECT encrypted_value FROM credentials WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_credential(key: str, encrypted_value: str, source: str = "app") -> None:
    """Store encrypted credential value in database.

    Args:
        key: Credential name (e.g., "HF_OAUTH_ACCESS_TOKEN")
        encrypted_value: Base64-encoded Fernet ciphertext
        source: Metadata ("oauth", "manual", "env", etc.)
    """
    conn = get_connection()
    now = _now_iso()
    conn.execute(
        "INSERT OR REPLACE INTO credentials (key, encrypted_value, updated_at, source) VALUES (?, ?, ?, ?)",
        (key, encrypted_value, now, source)
    )
    conn.commit()


def delete_credential(key: str) -> None:
    """Delete a credential from database."""
    conn = get_connection()
    conn.execute("DELETE FROM credentials WHERE key = ?", (key,))
    conn.commit()


def _now_iso() -> str:
    """Return current time as ISO-8601 string with UTC timezone."""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
