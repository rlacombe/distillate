"""Persistent state management for Distillate.

Tracks Zotero library version, document mappings between Zotero and reMarkable,
processing status, and ML experiment projects. State is stored as JSON and
written atomically to prevent corruption if the script is interrupted mid-write.
"""

import copy
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from distillate.config import CONFIG_DIR

log = logging.getLogger(__name__)

# State file: prefer CWD (for dev installs), then config dir
STATE_PATH = CONFIG_DIR / "state.json"
if not STATE_PATH.exists() and (Path.cwd() / "state.json").exists():
    STATE_PATH = Path.cwd() / "state.json"
LOCK_PATH = STATE_PATH.with_suffix(".lock")

_CURRENT_SCHEMA_VERSION = 2

_DEFAULT_STATE = {
    "schema_version": _CURRENT_SCHEMA_VERSION,
    "zotero_library_version": 0,
    "last_poll_timestamp": None,
    "documents": {},
    "promoted_papers": [],
    "projects": {},
}


def _migrate_0_to_1(data: Dict[str, Any]) -> Dict[str, Any]:
    """Add schema_version field to legacy state files."""
    data["schema_version"] = 1
    return data


def _migrate_1_to_2(data: Dict[str, Any]) -> Dict[str, Any]:
    """Replace keep/discard decisions with best/completed.

    For each project, walk runs chronologically and mark frontier-improving
    runs as ``best`` and all others as ``completed``.
    """
    from distillate.experiments import infer_key_metric_name, _is_lower_better

    import json as _json
    import re as _re

    projects = data.get("projects", {})
    total_runs = sum(len(p.get("runs", {})) for p in projects.values())
    processed = 0
    total_best = 0

    log.info("Migrating %d projects (%d runs) to best/completed schema...",
             len(projects), total_runs)

    for _pid, proj in projects.items():
        runs = proj.get("runs", {})
        if not runs:
            continue

        proj_name = proj.get("name", _pid)
        key_metric = infer_key_metric_name(proj)

        # Read runs.jsonl to get (1) original statuses and (2) file
        # order — the append order IS the true chronological sequence.
        # Timestamps can be wrong (midnight sessions, backfills) but
        # file order is always correct.
        original_statuses: Dict[str, str] = {}  # run_name → keep/discard/crash
        file_order: List[str] = []  # run names in chronological order
        proj_path = proj.get("path", "")
        if proj_path:
            runs_file = Path(proj_path) / ".distillate" / "runs.jsonl"
            if runs_file.exists():
                try:
                    for line in runs_file.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = _json.loads(line)
                        except (ValueError, _json.JSONDecodeError):
                            continue
                        rid = entry.get("id", "")
                        st = entry.get("status", "")
                        if st in ("keep", "discard", "crash"):
                            original_statuses[rid] = st
                            # First terminal entry per run = chronological position
                            if rid not in [n for n in file_order]:
                                file_order.append(rid)
                except OSError:
                    pass

        # Build name→state_id map for lookup
        name_to_sid: Dict[str, str] = {}
        for sid, r in runs.items():
            name_to_sid[r.get("name", sid)] = sid

        # Walk in runs.jsonl file order (true chronological).
        # Runs only in state.json (no runs.jsonl) get appended at the end.
        seen_names: set = set()
        ordered_sids: List[str] = []
        for run_name in file_order:
            sid = name_to_sid.get(run_name)
            if sid and sid not in seen_names:
                ordered_sids.append(sid)
                seen_names.add(sid)
        # Append any state-only runs (sorted by timestamp as fallback)
        for sid in sorted(
            runs.keys(),
            key=lambda s: runs[s].get("started_at", ""),
        ):
            if sid not in seen_names:
                ordered_sids.append(sid)
                seen_names.add(sid)

        frontier_val = None
        lower_better = _is_lower_better(key_metric) if key_metric else False
        proj_best = 0

        for sid in ordered_sids:
            run = runs[sid]
            run_name = run.get("name", sid)
            decision = run.get("decision", "")
            status = run.get("status", "")
            orig = original_statuses.get(run_name, "")

            # Crash / failed stays crash
            if decision == "crash" or status == "failed" or orig == "crash":
                run["decision"] = "crash"
                processed += 1
                continue

            # Only process terminal runs
            if decision not in ("keep", "discard", "best", "completed", "") \
                    and status != "completed":
                processed += 1
                continue

            # Only old "keep" runs can be "best". Discards were
            # rejected by the agent (e.g. didn't meet accuracy threshold)
            # and never participate in the frontier.
            if orig == "discard":
                run["decision"] = "completed"
                processed += 1
                continue

            val = run.get("results", {}).get(key_metric) if key_metric else None
            if isinstance(val, (int, float)):
                improved = False
                if frontier_val is None:
                    improved = True
                elif lower_better and val < frontier_val:
                    improved = True
                elif not lower_better and val > frontier_val:
                    improved = True

                if improved:
                    run["decision"] = "best"
                    frontier_val = val
                    proj_best += 1
                else:
                    run["decision"] = "completed"
            else:
                run["decision"] = "completed"
            processed += 1

        total_best += proj_best
        log.info("  %s: %d runs → %d best (key metric: %s)",
                 proj_name, len(runs), proj_best, key_metric or "none")

    log.info("Migration complete: %d runs processed, %d marked as best",
             processed, total_best)

    data["schema_version"] = 2
    return data


_MIGRATIONS: Dict[int, Any] = {0: _migrate_0_to_1, 1: _migrate_1_to_2}

# Set by _run_migrations when a migration executes — consumed once by the
# server to show a UI toast.
last_migration_message: Optional[str] = None


def _run_migrations(data: Dict[str, Any]) -> Dict[str, Any]:
    """Apply pending schema migrations in order."""
    global last_migration_message
    version = data.get("schema_version", 0)
    if version > _CURRENT_SCHEMA_VERSION:
        log.warning(
            "State file has schema_version %d (newer than %d) — loading as-is",
            version, _CURRENT_SCHEMA_VERSION,
        )
        return data
    if version < _CURRENT_SCHEMA_VERSION:
        log.info("State schema %d → %d: running migrations...",
                 version, _CURRENT_SCHEMA_VERSION)
    while version < _CURRENT_SCHEMA_VERSION:
        migrate = _MIGRATIONS.get(version)
        if migrate is None:
            log.warning("No migration for schema version %d", version)
            break
        data = migrate(data)
        old_version = version
        version = data.get("schema_version", version + 1)
        if old_version == 1 and version == 2:
            # Count best for the toast message
            total_best = sum(
                sum(1 for r in p.get("runs", {}).values()
                    if r.get("decision") == "best")
                for p in data.get("projects", {}).values()
            )
            last_migration_message = (
                f"Migrated run decisions: {total_best} frontier-improving "
                f"runs marked as best"
            )
    return data


def _load_raw() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return copy.deepcopy(_DEFAULT_STATE)
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return _run_migrations(data)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Corrupt state file, backing up and starting fresh: %s", exc)
        backup = STATE_PATH.with_suffix(".json.bak")
        STATE_PATH.rename(backup)
        log.warning("Backed up corrupt state to %s", backup)
        return copy.deepcopy(_DEFAULT_STATE)


def _save_raw(data: Dict[str, Any]) -> None:
    """Write state atomically: write to temp file, then rename."""
    fd, tmp = tempfile.mkstemp(
        dir=STATE_PATH.parent, prefix=".state_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, STATE_PATH)
    except BaseException:
        os.unlink(tmp)
        raise


class State:
    """Interface for reading and writing persistent workflow state."""

    def __init__(self) -> None:
        self._data = _load_raw()

    def reload(self) -> None:
        """Re-read state from disk (picks up changes from concurrent sync)."""
        self._data = _load_raw()

    def save(self) -> None:
        _save_raw(self._data)

    # -- Schema version --

    @property
    def schema_version(self) -> int:
        return self._data.get("schema_version", 0)

    # -- Zotero library version --

    @property
    def zotero_library_version(self) -> int:
        return self._data["zotero_library_version"]

    @zotero_library_version.setter
    def zotero_library_version(self, version: int) -> None:
        self._data["zotero_library_version"] = version

    # -- Poll timestamp --

    @property
    def last_poll_timestamp(self) -> Optional[str]:
        return self._data["last_poll_timestamp"]

    def touch_poll_timestamp(self) -> None:
        self._data["last_poll_timestamp"] = (
            datetime.now(timezone.utc).isoformat()
        )

    # -- Cloud sync watermark --

    @property
    def last_cloud_sync_at(self) -> Optional[str]:
        return self._data.get("last_cloud_sync_at")

    @last_cloud_sync_at.setter
    def last_cloud_sync_at(self, ts: str) -> None:
        self._data["last_cloud_sync_at"] = ts

    # -- Document tracking --

    @property
    def documents(self) -> Dict[str, Any]:
        return self._data["documents"]

    def get_document(self, zotero_item_key: str) -> Optional[Dict[str, Any]]:
        return self._data["documents"].get(zotero_item_key)

    def has_document(self, zotero_item_key: str) -> bool:
        return zotero_item_key in self._data["documents"]

    def index_of(self, zotero_item_key: str) -> int:
        """Return the 1-based index of a document (insertion order)."""
        for i, key in enumerate(self._data["documents"], 1):
            if key == zotero_item_key:
                return i
        return 0

    def key_for_index(self, index: int) -> Optional[str]:
        """Return the zotero_item_key for a 1-based index, or None."""
        keys = list(self._data["documents"].keys())
        if 1 <= index <= len(keys):
            return keys[index - 1]
        return None

    def find_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Find a tracked document by DOI. Returns None if not found."""
        if not doi:
            return None
        for doc in self._data["documents"].values():
            if doc.get("metadata", {}).get("doi", "") == doi:
                return doc
        return None

    def find_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Find a tracked document by exact title (case-insensitive)."""
        if not title:
            return None
        title_lower = title.lower().strip()
        for doc in self._data["documents"].values():
            if doc.get("title", "").lower().strip() == title_lower:
                return doc
        return None

    def find_by_citekey(self, citekey: str) -> Optional[Dict[str, Any]]:
        """Find a tracked document by citekey. Returns None if not found."""
        if not citekey:
            return None
        for doc in self._data["documents"].values():
            if doc.get("metadata", {}).get("citekey", "") == citekey:
                return doc
        return None

    def add_document(
        self,
        zotero_item_key: str,
        zotero_attachment_key: str,
        zotero_attachment_md5: str,
        remarkable_doc_name: str,
        title: str,
        authors: List[str],
        status: str = "on_remarkable",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._data["documents"][zotero_item_key] = {
            "zotero_item_key": zotero_item_key,
            "zotero_attachment_key": zotero_attachment_key,
            "zotero_attachment_md5": zotero_attachment_md5,
            "remarkable_doc_name": remarkable_doc_name,
            "title": title,
            "authors": authors,
            "status": status,
            "metadata": metadata or {},
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "processed_at": None,
        }

    def set_status(self, zotero_item_key: str, status: str) -> None:
        doc = self._data["documents"].get(zotero_item_key)
        if doc:
            doc["status"] = status

    def mark_processed(
        self, zotero_item_key: str,
        summary: str = "",
    ) -> None:
        doc = self._data["documents"].get(zotero_item_key)
        if doc:
            doc["status"] = "processed"
            if not doc.get("processed_at"):
                doc["processed_at"] = datetime.now(timezone.utc).isoformat()
            if summary:
                doc["summary"] = summary
            # Remove from promoted list if present
            promoted = self._data.get("promoted_papers", [])
            if zotero_item_key in promoted:
                promoted.remove(zotero_item_key)
                self._data["promoted_papers"] = promoted

    def mark_deleted(self, zotero_item_key: str) -> None:
        doc = self._data["documents"].get(zotero_item_key)
        if doc:
            doc["status"] = "deleted"

    def remove_document(self, zotero_item_key: str) -> bool:
        """Remove a document from tracking. Returns True if found and removed."""
        if zotero_item_key not in self._data["documents"]:
            return False
        del self._data["documents"][zotero_item_key]
        # Clean up from promoted and pending lists
        for list_key in ("promoted_papers", "pending_promotions"):
            lst = self._data.get(list_key, [])
            if zotero_item_key in lst:
                lst.remove(zotero_item_key)
        return True

    def documents_with_status(self, status: str) -> List[Dict[str, Any]]:
        return [
            doc
            for doc in self._data["documents"].values()
            if doc["status"] == status
        ]

    # -- Project tracking (experiments) --

    @property
    def projects(self) -> Dict[str, Any]:
        return self._data.setdefault("projects", {})

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        return self.projects.get(project_id)

    def has_project(self, project_id: str) -> bool:
        return project_id in self.projects

    def project_index_of(self, project_id: str) -> int:
        """Return the 1-based index of a project (insertion order)."""
        for i, key in enumerate(self.projects, 1):
            if key == project_id:
                return i
        return 0

    def find_project(self, query: str) -> Optional[Dict[str, Any]]:
        """Find a project by id, 1-based index, or name substring.

        Returns the first match.  Use ``find_all_projects`` when you need
        to detect ambiguous queries.
        """
        matches = self.find_all_projects(query)
        return matches[0] if matches else None

    def find_all_projects(self, query: str) -> List[Dict[str, Any]]:
        """Return all projects matching *query* (id, index, or name substring)."""
        if not query:
            return []
        # Try index number (always unique)
        try:
            idx = int(query)
            keys = list(self.projects.keys())
            if 1 <= idx <= len(keys):
                return [self.projects[keys[idx - 1]]]
        except ValueError:
            pass
        # Try exact id (always unique)
        if query in self.projects:
            return [self.projects[query]]
        # Substring search (may match multiple)
        query_lower = query.lower()
        matches: List[Dict[str, Any]] = []
        for proj in self.projects.values():
            if (query_lower in proj.get("name", "").lower()
                    or query_lower in proj.get("id", "").lower()):
                matches.append(proj)
        return matches

    def add_project(
        self,
        project_id: str,
        name: str,
        path: str,
        description: str = "",
        status: str = "tracking",
        tags: Optional[List[str]] = None,
        goals: Optional[List[Dict[str, Any]]] = None,
        notebook_sections: Optional[List[str]] = None,
    ) -> None:
        resolved_path = str(Path(path).expanduser().resolve()) if path else path
        self.projects[project_id] = {
            "id": project_id,
            "name": name,
            "path": resolved_path,
            "description": description,
            "status": status,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "last_scanned_at": None,
            "last_commit_hash": "",
            "tags": tags or [],
            "linked_papers": [],
            "goals": goals or [],
            "runs": {},
            "notebook_sections": notebook_sections or ["main"],
        }

    def update_project(self, project_id: str, **kwargs: Any) -> None:
        proj = self.projects.get(project_id)
        if proj:
            for key, val in kwargs.items():
                proj[key] = val

    def remove_project(self, project_id: str) -> bool:
        if project_id not in self.projects:
            return False
        del self.projects[project_id]
        return True

    def get_run(self, project_id: str, run_id: str) -> Optional[Dict[str, Any]]:
        proj = self.projects.get(project_id)
        if not proj:
            return None
        return proj.get("runs", {}).get(run_id)

    def add_run(self, project_id: str, run_id: str, run_data: Dict[str, Any]) -> None:
        proj = self.projects.get(project_id)
        if proj:
            proj.setdefault("runs", {})[run_id] = run_data

    def update_run(self, project_id: str, run_id: str, **kwargs: Any) -> None:
        proj = self.projects.get(project_id)
        if not proj:
            return
        run = proj.get("runs", {}).get(run_id)
        if run:
            for key, val in kwargs.items():
                run[key] = val

    def remove_run(self, project_id: str, run_id: str) -> bool:
        """Remove a run from a project. Returns True if found and removed."""
        proj = self.projects.get(project_id)
        if not proj:
            return False
        runs = proj.get("runs", {})
        if run_id not in runs:
            return False
        del runs[run_id]
        return True

    # -- Session tracking (experiment launcher) --

    def add_session(self, project_id: str, session_id: str, session_data: dict) -> None:
        """Add a launcher session to a project."""
        proj = self.projects.get(project_id)
        if proj:
            proj.setdefault("sessions", {})[session_id] = session_data

    def update_session(self, project_id: str, session_id: str, **kwargs) -> None:
        """Update fields on a launcher session."""
        proj = self.projects.get(project_id)
        if not proj:
            return
        sess = proj.get("sessions", {}).get(session_id)
        if sess:
            for key, val in kwargs.items():
                sess[key] = val

    def active_sessions(self) -> list[tuple[str, str, dict]]:
        """Return [(project_id, session_id, session_dict)] for all running sessions."""
        result = []
        for proj_id, proj in self.projects.items():
            for sess_id, sess in proj.get("sessions", {}).items():
                if sess.get("status") == "running":
                    result.append((proj_id, sess_id, sess))
        return result

    # -- Promoted papers --

    @property
    def promoted_papers(self) -> List[str]:
        """Return list of Zotero item keys currently promoted to Papers root."""
        return self._data.get("promoted_papers", [])

    @promoted_papers.setter
    def promoted_papers(self, keys: List[str]) -> None:
        self._data["promoted_papers"] = keys

    @property
    def pending_promotions(self) -> List[str]:
        """Return Zotero item keys picked by --suggest, awaiting promotion."""
        return self._data.get("pending_promotions", [])

    @pending_promotions.setter
    def pending_promotions(self, keys: List[str]) -> None:
        self._data["pending_promotions"] = keys

    def documents_processed_since(self, since_iso: str) -> List[Dict[str, Any]]:
        """Return documents processed on or after the given ISO timestamp."""
        return sorted(
            [
                doc
                for doc in self._data["documents"].values()
                if doc["status"] == "processed" and (doc.get("processed_at") or "") >= since_iso
            ],
            key=lambda d: d.get("processed_at", ""),
        )


def _try_create_lock() -> bool:
    """Attempt to create the lock file. Returns True if successful."""
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def acquire_lock() -> bool:
    """Try to acquire a file lock. Returns True if acquired, False if already held.

    If the lock is held by a dead process (stale lock), it is automatically
    removed and re-acquired.
    """
    if _try_create_lock():
        return True

    # Lock exists — check if the holding process is still alive
    try:
        pid = int(LOCK_PATH.read_text().strip())
        os.kill(pid, 0)  # signal 0: check existence only
    except (ValueError, OSError):
        # Stale lock: PID is dead or lock file is malformed
        log.warning("Removing stale lock (previous process died)")
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
        return _try_create_lock()

    # Process is alive — lock is valid
    return False


def release_lock() -> None:
    """Release the file lock."""
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass
