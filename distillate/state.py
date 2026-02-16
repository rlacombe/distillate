"""Persistent state management for Distillate.

Tracks Zotero library version, document mappings between Zotero and reMarkable,
and processing status. State is stored as JSON and written atomically to prevent
corruption if the script is interrupted mid-write.
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

_DEFAULT_STATE = {
    "zotero_library_version": 0,
    "last_poll_timestamp": None,
    "documents": {},
    "promoted_papers": [],
}


def _load_raw() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return copy.deepcopy(_DEFAULT_STATE)
    try:
        return json.loads(STATE_PATH.read_text())
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
        with os.fdopen(fd, "w") as f:
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

    def save(self) -> None:
        _save_raw(self._data)

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

    # -- Document tracking --

    @property
    def documents(self) -> Dict[str, Any]:
        return self._data["documents"]

    def get_document(self, zotero_item_key: str) -> Optional[Dict[str, Any]]:
        return self._data["documents"].get(zotero_item_key)

    def has_document(self, zotero_item_key: str) -> bool:
        return zotero_item_key in self._data["documents"]

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
