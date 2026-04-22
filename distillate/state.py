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
from typing import Any, Dict, List, Optional, TypedDict

from distillate.config import CONFIG_DIR, DB_PATH


# ---------------------------------------------------------------------------
# TypedDicts: document the shapes stored in state.json.
#
# `total=False` matches the defensive `.get()` style used throughout the
# codebase — every key is effectively optional at the type level, and the
# TypedDicts serve as documentation and IDE assistance rather than strict
# validation. Runtime data still flows through plain dicts.
# ---------------------------------------------------------------------------


class DocumentDict(TypedDict, total=False):
    """A Zotero paper tracked by Distillate."""
    zotero_item_key: str
    zotero_attachment_key: str
    zotero_attachment_md5: str
    remarkable_doc_name: str
    title: str
    authors: List[str]
    status: str  # on_remarkable | processed | deleted | ...
    metadata: Dict[str, Any]  # doi, citekey, year, venue, etc.
    uploaded_at: str
    processed_at: Optional[str]
    summary: str
    engagement: float
    highlight_word_count: int
    # Desktop reader state — persisted so the reader re-opens where you left off.
    last_read_page: int       # 1-based page number
    last_read_at: str         # ISO-8601 timestamp


class RepoDict(TypedDict, total=False):
    """A repo linked to a workspace."""
    path: str
    name: str


class ResourceDict(TypedDict, total=False):
    """An external resource linked to a workspace (HF model, W&B run, etc.)."""
    type: str  # huggingface_model | huggingface_dataset | wandb | link | ...
    name: str
    url: str
    id: str


class WriteupDict(TypedDict, total=False):
    """A workspace write-up. Markdown is always flat; LaTeX may be dir+entry."""
    format: str  # "markdown" | "latex"
    path: str
    dir: str
    entry: str


class CodingSessionDict(TypedDict, total=False):
    """A non-experiment CLI agent coding session on a workspace."""
    id: str
    repo_path: str
    tmux_name: str
    agent_session_id: str
    agent_type: str
    model: str
    status: str  # running | stopped
    sort_order: int
    started_at: str
    ended_at: Optional[str]
    canvas_id: str


class WorkspaceDict(TypedDict, total=False):
    """A workspace project — container for repos, experiments, papers, resources."""
    id: str
    name: str
    emoji: str     # v2: visual character (displayed in sidebar, detail views)
    description: str
    status: str
    default: bool  # True for the Workbench project (auto-created, pinned last, can't delete)
    root_path: str
    repos: List[RepoDict]
    tags: List[str]
    linked_papers: List[str]
    resources: List[ResourceDict]
    writeup: Optional[WriteupDict]
    coding_sessions: Dict[str, CodingSessionDict]
    notes_path: str
    created_at: str
    updated_at: str


class RunDict(TypedDict, total=False):
    """A single experiment run. Most fields are managed by experiments.py."""
    id: str
    name: str
    started_at: str
    results: Dict[str, Any]
    decision: str  # best | completed | crash
    status: str
    produced_by_agent_id: str  # v2: FK to the agent that produced this run


class SessionDict(TypedDict, total=False):
    """A launcher session (running experiment agent)."""
    id: str
    status: str  # running | stopped | failed
    started_at: str


class ExperimentDict(TypedDict, total=False):
    """An experiment project tracked by the launcher."""
    id: str
    name: str
    path: str
    description: str
    status: str  # tracking | archived | ...
    added_at: str
    last_scanned_at: Optional[str]
    last_commit_hash: str
    tags: List[str]
    linked_papers: List[str]
    goals: List[Dict[str, Any]]
    runs: Dict[str, RunDict]
    notebook_sections: List[str]
    agent_type: str  # legacy — maps to harness_id
    agent_id: str    # v2: FK to the agent identity running this experiment
    harness_id: str  # v2: FK to the harness (claude-code, codex, etc.)
    workspace_id: Optional[str]
    session_budget_seconds: int
    sister_of: str
    compute: Dict[str, Any]
    sessions: Dict[str, SessionDict]


class HarnessDict(TypedDict, total=False):
    """A CLI/SDK runtime that wraps a model to run experiments."""
    id: str          # e.g. "claude-code", "codex", "gemini-cli"
    label: str
    binary: str      # CLI binary name
    available: bool  # detected via shutil.which
    install_hint: str
    description: str
    context_file: str  # e.g. "CLAUDE.md", "AGENTS.md"
    mcp_support: bool


class EventDict(TypedDict, total=False):
    """A unified event in the timeline stream."""
    id: str
    timestamp: str
    event_type: str
    experiment_id: Optional[str]
    experiment_id: Optional[str]
    agent_id: Optional[str]
    paper_id: Optional[str]
    payload: Dict[str, Any]
    tags: List[str]


class AgentDict(TypedDict, total=False):
    """A long-lived agent (Nicolas, custom assistants, experiment agents)."""
    id: str
    name: str
    emoji: str       # v2: visual character (displayed in sidebar, detail views, welcome)
    agent_type: str  # claude | nicolas | ...
    tier: str        # v2: "shell" | "agent" (Tier 2 sub-agents not in this table)
    model: str
    harness_id: str  # v2: FK to Integrations.harnesses
    builtin: bool
    config_dir: str
    workspace_id: Optional[str]
    working_dir: str
    command: str
    tmux_name: str
    session_status: str  # running | stopped
    created_at: str
    updated_at: str
    last_active_at: Optional[str]

log = logging.getLogger(__name__)

# State file: prefer CWD (for dev installs), then config dir
STATE_PATH = CONFIG_DIR / "state.json"
if not STATE_PATH.exists() and (Path.cwd() / "state.json").exists():
    STATE_PATH = Path.cwd() / "state.json"
LOCK_PATH = STATE_PATH.with_suffix(".lock")

_CURRENT_SCHEMA_VERSION = 3

_DEFAULT_STATE = {
    "schema_version": _CURRENT_SCHEMA_VERSION,
    "zotero_library_version": 0,
    "last_poll_timestamp": None,
    "documents": {},
    "promoted_papers": [],
    "experiments": {},
}

# Backend selection: "sqlite" (default) or "json" (emergency fallback).
_STATE_BACKEND = os.environ.get("DISTILLATE_STATE_BACKEND", "sqlite").strip().lower()


def _load_raw() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return copy.deepcopy(_DEFAULT_STATE)
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        version = data.get("schema_version", 0)
        if version < 3 and "projects" in data:
            data["experiments"] = data.pop("projects")
            data["schema_version"] = 3
            log.info("Migrated state: renamed 'projects' → 'experiments' (schema v3)")
        elif version != _CURRENT_SCHEMA_VERSION:
            log.warning(
                "State file has schema_version %d (current is %d) — "
                "loading as-is.",
                version, _CURRENT_SCHEMA_VERSION,
            )
        return data
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


def _use_sqlite() -> bool:
    return _STATE_BACKEND == "sqlite"


def _load_sqlite() -> Dict[str, Any]:
    """Load state from SQLite, auto-migrating from JSON on first run."""
    from distillate import state_sqlite

    conn = _db_connection()

    # One-shot migration: if SQLite is truly empty but state.json exists, import it.
    # Check both meta AND experiments — meta is empty after a fresh migration (v6
    # doesn't write meta), so checking meta alone would falsely trigger the import
    # and wipe experiments that the migration just inserted (state.json uses the old
    # "projects" key, so import_from_json would save experiments={} and soft-delete
    # everything).
    meta_count = conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
    exp_count = conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
    if meta_count == 0 and exp_count == 0 and STATE_PATH.exists():
        log.info("Migrating state.json → state.db")
        try:
            state_sqlite.import_from_json(STATE_PATH, conn=conn)
        except Exception as exc:
            log.warning("Failed to import state.json: %s — starting fresh", exc)
            # Back up the corrupt file so the user can inspect it
            backup = STATE_PATH.with_suffix(".json.bak")
            try:
                STATE_PATH.rename(backup)
                log.warning("Backed up corrupt state to %s", backup)
            except OSError:
                pass

    return state_sqlite.load_all(conn=conn)


def _save_sqlite(data: Dict[str, Any]) -> None:
    """Write state to SQLite."""
    from distillate import state_sqlite
    state_sqlite.save_all(data, conn=_db_connection())


def _db_connection():
    """Get or create the SQLite connection."""
    from distillate import db
    return db.get_connection()


class State:
    """Interface for reading and writing persistent workflow state.

    Backed by SQLite (default) or JSON (``DISTILLATE_STATE_BACKEND=json``).
    The dict-based API is identical regardless of backend.
    """

    def __init__(self) -> None:
        if _use_sqlite():
            self._data = _load_sqlite()
        else:
            self._data = _load_raw()

    def reload(self) -> None:
        """Re-read state from disk (picks up changes from concurrent sync)."""
        if _use_sqlite():
            self._data = _load_sqlite()
        else:
            self._data = _load_raw()

    def save(self) -> None:
        if _use_sqlite():
            _save_sqlite(self._data)
        else:
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
    def documents(self) -> Dict[str, DocumentDict]:
        return self._data["documents"]

    def get_document(self, zotero_item_key: str) -> Optional[DocumentDict]:
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

    def find_by_doi(self, doi: str) -> Optional[DocumentDict]:
        """Find a tracked document by DOI. Returns None if not found."""
        if not doi:
            return None
        for doc in self._data["documents"].values():
            if doc.get("metadata", {}).get("doi", "") == doi:
                return doc
        return None

    def find_by_title(self, title: str) -> Optional[DocumentDict]:
        """Find a tracked document by exact title (case-insensitive)."""
        if not title:
            return None
        title_lower = title.lower().strip()
        for doc in self._data["documents"].values():
            if doc.get("title", "").lower().strip() == title_lower:
                return doc
        return None

    def find_by_citekey(self, citekey: str) -> Optional[DocumentDict]:
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
            "zotero_date_added": (metadata or {}).get("zotero_date_added", ""),
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

            # Log to lab notebook
            try:
                from distillate.lab_notebook import append_entry
                title = doc.get("title", "Untitled")
                engagement = doc.get("engagement", "")
                hl = doc.get("highlight_word_count", 0)
                eng_str = f", engagement {engagement}%" if engagement else ""
                hl_str = f", {hl} words highlighted" if hl else ""
                append_entry(
                    f'Paper processed: "{title}"{eng_str}{hl_str}',
                    entry_type="paper",
                )
            except Exception:
                pass

    def mark_deleted(self, zotero_item_key: str) -> None:
        doc = self._data["documents"].get(zotero_item_key)
        if doc:
            doc["status"] = "deleted"

    def set_read_position(self, zotero_item_key: str, page: int) -> bool:
        """Persist the desktop reader's scroll position for a paper.

        ``page`` is 1-based. Returns True if the paper exists and was updated.
        """
        doc = self._data["documents"].get(zotero_item_key)
        if not doc:
            return False
        try:
            page = int(page)
        except (TypeError, ValueError):
            return False
        if page < 1:
            page = 1
        doc["last_read_page"] = page
        doc["last_read_at"] = datetime.now(timezone.utc).isoformat()
        return True

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

    def documents_with_status(self, status: str) -> List[DocumentDict]:
        return [
            doc
            for doc in self._data["documents"].values()
            if doc["status"] == status
        ]

    # -- Workspace projects (IAE) --

    @property
    def workspaces(self) -> Dict[str, WorkspaceDict]:
        """Return all workspace-type projects (non-experiment)."""
        return self._data.setdefault("workspaces", {})

    def get_workspace(self, workspace_id: str) -> Optional[WorkspaceDict]:
        return self.workspaces.get(workspace_id)

    def add_workspace(
        self,
        workspace_id: str,
        name: str,
        description: str = "",
        repos: Optional[List[RepoDict]] = None,
        root_path: str = "",
        tags: Optional[List[str]] = None,
    ) -> WorkspaceDict:
        """Create a workspace project — the container for repos, experiments, papers, and resources.

        Each repo is a dict: {"path": "/abs/path", "name": "repo-name"}.
        """
        ws: WorkspaceDict = {
            "id": workspace_id,
            "name": name,
            "description": description,
            "status": "active",
            "root_path": str(Path(root_path).expanduser().resolve()) if root_path else "",
            "repos": repos or [],
            "tags": tags or [],
            "linked_papers": [],
            "resources": [],          # [{type, id/url, name, ...}]
            "writeup": None,          # legacy singular — Markdown textarea path
            "canvases": {},           # plural keyed dict: {cv_id: {id, title, type, dir, entry, session_id, ...}}
            "coding_sessions": {},
            "notes_path": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.workspaces[workspace_id] = ws
        return ws

    def update_workspace(self, workspace_id: str, **kwargs: Any) -> None:
        ws = self.workspaces.get(workspace_id)
        if ws:
            for key, val in kwargs.items():
                ws[key] = val
            ws["updated_at"] = datetime.now(timezone.utc).isoformat()

    def get_default_workspace(self) -> Optional[WorkspaceDict]:
        """Return the default (Workbench) project, if it exists."""
        for ws in self.workspaces.values():
            if ws.get("default"):
                return ws
        return None

    def ensure_workbench(self) -> WorkspaceDict:
        """Ensure the Workbench default project exists. Idempotent."""
        existing = self.get_default_workspace()
        if existing:
            return existing
        ws = self.add_workspace(
            "workbench",
            name="Workbench",
            description="Default home for unfiled experiments and sessions",
        )
        ws["default"] = True
        return ws

    def remove_workspace(self, workspace_id: str) -> bool:
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        # The Workbench (default) project cannot be deleted.
        if ws.get("default"):
            return False
        del self.workspaces[workspace_id]
        return True

    def add_workspace_repo(self, workspace_id: str, path: str, name: str = "") -> bool:
        """Link a repo to a workspace project."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        resolved = str(Path(path).expanduser().resolve())
        repo_name = name or Path(resolved).name
        # Avoid duplicates
        for r in ws.get("repos", []):
            if r.get("path") == resolved:
                return False
        ws.setdefault("repos", []).append({"path": resolved, "name": repo_name})
        ws["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True

    def remove_workspace_repo(self, workspace_id: str, path: str) -> bool:
        """Unlink a repo from a workspace project."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        resolved = str(Path(path).expanduser().resolve())
        repos = ws.get("repos", [])
        before = len(repos)
        ws["repos"] = [r for r in repos if r.get("path") != resolved]
        if len(ws["repos"]) < before:
            ws["updated_at"] = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def add_workspace_resource(self, workspace_id: str, resource: ResourceDict) -> bool:
        """Add a resource link to a workspace (HF model, dataset, W&B, external URL, etc.)."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        ws.setdefault("resources", []).append(resource)
        ws["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True

    def remove_workspace_resource(self, workspace_id: str, index: int) -> bool:
        """Remove a resource by index."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        resources = ws.get("resources", [])
        if 0 <= index < len(resources):
            resources.pop(index)
            ws["updated_at"] = datetime.now(timezone.utc).isoformat()
            return True
        return False

    # -- Canvases (plural resource collection on a workspace) --
    #
    # A "canvas" is any editable document or work item the user is working on
    # with an agent. Each workspace carries a dict ``ws["canvases"]`` keyed by
    # canvas id (e.g. "cv_001"). Each entry looks like:
    #
    #   {
    #     "id": "cv_001",
    #     "title": "Main paper",
    #     "type": "latex" | "markdown" | "plain" | "code" | "survey" | "data",
    #     "dir": "/abs/path/to/canvas/parent",   # parent folder of entry
    #     "entry": "paper.tex",                   # file within dir
    #     "session_id": "",                       # currently active session id
    #     "sessions": [],                         # all session ids that worked on this
    #     "status": "active" | "review" | "done" | "archived",
    #     "branch": "",                           # git branch (code type)
    #     "template": "",                         # template id (survey/data types)
    #     "completed_at": None,
    #     "last_compile": {ok, at, duration_ms, error_count} | None,
    #     "created_at": "...",
    #     "updated_at": "...",
    #   }
    #
    # Helper: _canvas_category() maps type → category ("write"/"code"/"survey"/"data")
    #
    # On reload, legacy ``ws["writeups"]`` entries are migrated in place to
    # ``ws["canvases"]`` (format → type field rename, ids get rewritten).
    # The legacy singular ``ws["writeup"]`` is also folded into a canvas.

    @staticmethod
    def _canvas_category(canvas_type: str) -> str:
        """Map canvas type to its display category."""
        if canvas_type in ("latex", "markdown", "plain"):
            return "write"
        return canvas_type  # "code", "survey", "data"

    @staticmethod
    def _infer_canvas_type(filename: str) -> str:
        """Map a filename to the canvas type used by the renderer registry."""
        name = (filename or "").lower()
        if name.endswith(".tex"):
            return "latex"
        if name.endswith(".md") or name.endswith(".markdown") or name.endswith(".mdx"):
            return "markdown"
        return "plain"

    @staticmethod
    def _canvas_slug(title: str) -> str:
        """Turn a title into a filesystem-safe slug."""
        import re
        s = re.sub(r"[^\w\s-]", "", (title or "").strip().lower(), flags=re.UNICODE)
        s = re.sub(r"[\s_]+", "-", s).strip("-")
        return s or "canvas"

    def _ensure_canvases_migrated(self, workspace_id: str) -> None:
        """Migrate legacy writeup state shapes into ``ws["canvases"]``.

        Three incoming shapes handled:
          1. Plural ``ws["writeups"]`` dict (from the previous refactor).
          2. Legacy singular ``ws["writeup"]`` (pre-plural shape).
          3. Already-canvas workspaces — no-op.
        The legacy fields are preserved so any stray reader still sees data.
        """
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return
        if isinstance(ws.get("canvases"), dict) and ws["canvases"]:
            return
        ws.setdefault("canvases", {})

        now = datetime.now(timezone.utc).isoformat()

        # Shape (1): promote the plural writeups dict.
        old_writeups = ws.get("writeups") or {}
        if isinstance(old_writeups, dict) and old_writeups:
            for wu_id, wu in old_writeups.items():
                entry = wu.get("entry", "main.tex")
                cv_type = self._infer_canvas_type(entry)
                # Preserve ids where possible, prefix wu_ → cv_ for clarity.
                cv_id = wu_id.replace("wu_", "cv_") if wu_id.startswith("wu_") else wu_id
                ws["canvases"][cv_id] = {
                    "id": cv_id,
                    "title": wu.get("title") or "Canvas",
                    "type": cv_type,
                    "dir": wu.get("dir", ""),
                    "entry": entry,
                    "session_id": wu.get("session_id", ""),
                    "last_compile": wu.get("last_compile"),
                    "created_at": wu.get("created_at", now),
                    "updated_at": now,
                }
            return

        # Shape (2): legacy singular writeup record.
        legacy = ws.get("writeup")
        if not legacy:
            return
        fmt = legacy.get("format", "markdown")
        if legacy.get("dir"):
            directory = legacy["dir"]
            entry = legacy.get("entry", "main.tex")
        else:
            legacy_path = legacy.get("path", "")
            if not legacy_path:
                return
            p = Path(legacy_path)
            if fmt == "latex" and p.name.startswith("WRITEUP"):
                directory = str(p.parent / "canvases" / "main")
                entry = "main.tex"
            else:
                directory = str(p.parent)
                entry = p.name
        cv_type = "latex" if fmt == "latex" else self._infer_canvas_type(entry)
        cv_id = "cv_001"
        ws["canvases"][cv_id] = {
            "id": cv_id,
            "title": "Canvas",
            "type": cv_type,
            "dir": directory,
            "entry": entry,
            "session_id": "",
            "last_compile": None,
            "created_at": legacy.get("created_at", now),
            "updated_at": now,
        }

    def _next_canvas_id(self, workspace_id: str) -> str:
        """Return the next sequential cv_NNN id for a workspace."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return "cv_001"
        existing = ws.get("canvases") or {}
        n = 1
        while f"cv_{n:03d}" in existing:
            n += 1
        return f"cv_{n:03d}"

    def list_workspace_canvases(self, workspace_id: str) -> list:
        """Return canvases for a workspace, sorted by created_at ascending."""
        self._ensure_canvases_migrated(workspace_id)
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return []
        canvases = list((ws.get("canvases") or {}).values())
        canvases.sort(key=lambda c: c.get("created_at", ""))
        return canvases

    def get_workspace_canvas(
        self, workspace_id: str, canvas_id: str
    ) -> Optional[Dict[str, Any]]:
        """Look up a single canvas by id."""
        self._ensure_canvases_migrated(workspace_id)
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return None
        return (ws.get("canvases") or {}).get(canvas_id)

    def find_workspace_canvas_by_path(
        self, workspace_id: str, abs_path: str
    ) -> Optional[Dict[str, Any]]:
        """Return a canvas whose dir+entry resolves to ``abs_path``, or None."""
        self._ensure_canvases_migrated(workspace_id)
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return None
        target = str(Path(abs_path).expanduser().resolve())
        for cv in (ws.get("canvases") or {}).values():
            cv_path = str(Path(cv.get("dir", "")) / cv.get("entry", ""))
            try:
                if str(Path(cv_path).resolve()) == target:
                    return cv
            except OSError:
                continue
        return None

    def add_workspace_canvas(
        self,
        workspace_id: str,
        title: str,
        canvas_type: str,
        directory: str,
        entry: str,
        branch: str = "",
        template: str = "",
        description: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Create a new canvas entry on a workspace.

        ``directory`` is the on-disk folder containing ``entry`` — caller
        is responsible for actually creating or importing the file.
        ``canvas_type`` is ``latex``, ``markdown``, ``plain``, ``code``,
        ``survey``, or ``data``.
        """
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return None
        self._ensure_canvases_migrated(workspace_id)
        cv_id = self._next_canvas_id(workspace_id)
        now = datetime.now(timezone.utc).isoformat()
        cv: Dict[str, Any] = {
            "id": cv_id,
            "title": title or "Untitled",
            "type": canvas_type,
            "dir": str(Path(directory).expanduser().resolve()) if directory else "",
            "entry": entry,
            "session_id": "",
            "sessions": [],
            "status": "active",
            "branch": branch,
            "template": template,
            "description": description,
            "completed_at": None,
            "last_compile": None,
            "created_at": now,
            "updated_at": now,
        }
        ws.setdefault("canvases", {})[cv_id] = cv
        ws["updated_at"] = now
        return cv

    def complete_workspace_canvas(
        self, workspace_id: str, canvas_id: str
    ) -> Optional[Dict[str, Any]]:
        """Mark a work item as done."""
        cv = self.get_workspace_canvas(workspace_id, canvas_id)
        if not cv:
            return None
        now = datetime.now(timezone.utc).isoformat()
        cv["status"] = "done"
        cv["completed_at"] = now
        cv["updated_at"] = now
        ws = self.workspaces.get(workspace_id)
        if ws:
            ws["updated_at"] = now
        return cv

    def update_workspace_canvas(
        self, workspace_id: str, canvas_id: str, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        """Merge fields into an existing canvas record."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return None
        cv = (ws.get("canvases") or {}).get(canvas_id)
        if not cv:
            return None
        fields.pop("id", None)
        fields.pop("created_at", None)
        cv.update(fields)
        cv["updated_at"] = datetime.now(timezone.utc).isoformat()
        ws["updated_at"] = cv["updated_at"]
        return cv

    def remove_workspace_canvas(
        self, workspace_id: str, canvas_id: str
    ) -> bool:
        """Remove a canvas from state. Files on disk are left untouched."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        canvases = ws.get("canvases") or {}
        if canvas_id not in canvases:
            return False
        del canvases[canvas_id]
        ws["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True

    def set_workspace_canvas_session(
        self, workspace_id: str, canvas_id: str, session_id: str
    ) -> bool:
        """Link a coding session to a canvas."""
        cv = self.get_workspace_canvas(workspace_id, canvas_id)
        if not cv:
            return False
        cv["session_id"] = session_id
        cv["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True

    # Legacy compat: kept for the Markdown textarea editor path which still
    # calls these. New canvas flow uses the plural API above.
    def set_workspace_writeup(self, workspace_id: str, fmt: str, path: str) -> bool:
        """Legacy singular setter — used only by the Markdown textarea flow."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        ws["writeup"] = {"format": fmt, "path": str(Path(path).expanduser().resolve())}
        ws["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True

    def add_workspace_paper(self, workspace_id: str, citekey: str) -> bool:
        """Link a paper to a workspace."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        papers = ws.setdefault("linked_papers", [])
        if citekey not in papers:
            papers.append(citekey)
            ws["updated_at"] = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def remove_workspace_paper(self, workspace_id: str, citekey: str) -> bool:
        """Unlink a paper from a workspace."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        papers = ws.get("linked_papers", [])
        if citekey in papers:
            papers.remove(citekey)
            ws["updated_at"] = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def add_coding_session(
        self,
        workspace_id: str,
        session_id: str,
        repo_path: str,
        tmux_name: str = "",
        agent_session_id: str = "",
        canvas_id: str = "",
        agent_type: str = "claude",
        model: str = "",
        session_type: str = "coding",
    ) -> Optional[CodingSessionDict]:
        """Record a workspace session (coding/writing/research CLI agent) on a workspace.

        ``canvas_id`` is set when the session was launched to work on a
        specific canvas (``cwd`` = canvas directory). Used by the UI to
        group sessions under their parent canvas.
        ``session_type`` is "coding" | "writing" | "research".
        """
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return None
        existing = ws.get("coding_sessions", {})
        max_order = max((s.get("sort_order", 0) for s in existing.values()), default=0)
        session: CodingSessionDict = {
            "id": session_id,
            "repo_path": str(Path(repo_path).expanduser().resolve()),
            "tmux_name": tmux_name,
            "agent_session_id": agent_session_id,
            "agent_type": agent_type,
            "model": model,
            "session_type": session_type,
            "status": "running",
            "sort_order": max_order + 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "canvas_id": canvas_id,
        }
        ws.setdefault("coding_sessions", {})[session_id] = session
        ws["updated_at"] = datetime.now(timezone.utc).isoformat()
        return session

    def reorder_coding_sessions(self, workspace_id: str, session_ids: list) -> bool:
        """Set the sort order of coding sessions based on the provided ID list."""
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return False
        sessions = ws.get("coding_sessions", {})
        for i, sid in enumerate(session_ids):
            if sid in sessions:
                sessions[sid]["sort_order"] = i
        ws["updated_at"] = datetime.now(timezone.utc).isoformat()
        return True

    def update_coding_session(self, workspace_id: str, session_id: str, **kwargs) -> None:
        ws = self.workspaces.get(workspace_id)
        if not ws:
            return
        sess = ws.get("coding_sessions", {}).get(session_id)
        if sess:
            for key, val in kwargs.items():
                sess[key] = val
            ws["updated_at"] = datetime.now(timezone.utc).isoformat()

    def active_coding_sessions(self) -> list[tuple[str, str, dict]]:
        """Return [(workspace_id, session_id, session_dict)] for running coding sessions."""
        result = []
        for ws_id, ws in self.workspaces.items():
            for sess_id, sess in ws.get("coding_sessions", {}).items():
                if sess.get("status") == "running":
                    result.append((ws_id, sess_id, sess))
        return result

    # -- Long-lived agents --

    @property
    def agents(self) -> Dict[str, AgentDict]:
        """Return all long-lived agents."""
        return self._data.setdefault("agents", {})

    def get_agent(self, agent_id: str) -> Optional[AgentDict]:
        return self.agents.get(agent_id)

    # -- Harness registry --

    @property
    def harnesses(self) -> Dict[str, HarnessDict]:
        """Return the harness registry (experiment CLI runtimes)."""
        return self._data.setdefault("harnesses", {})

    def get_harness(self, harness_id: str) -> Optional[HarnessDict]:
        return self.harnesses.get(harness_id)

    def add_agent(
        self,
        agent_id: str,
        name: str,
        agent_type: str = "claude",
        builtin: bool = False,
        model: str = "",
        emoji: str = "",
        tier: str = "agent",
        harness_id: str = "",
        experiment_id: Optional[str] = None,
        working_dir: str = "",
        command: str = "",
    ) -> AgentDict:
        """Create a long-lived agent."""
        from distillate.config import CONFIG_DIR
        agent: AgentDict = {
            "id": agent_id,
            "name": name,
            "emoji": emoji,
            "agent_type": agent_type,
            "tier": tier,
            "model": model,
            "harness_id": harness_id or ("distillate-sdk" if agent_type == "nicolas" else "claude-code"),
            "builtin": builtin,
            "config_dir": str(CONFIG_DIR / "agents" / agent_id),
            "experiment_id": experiment_id,
            "working_dir": str(Path(working_dir).expanduser().resolve()) if working_dir else "",
            "command": command,
            "tmux_name": "",
            "session_status": "stopped",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_active_at": None,
        }
        self.agents[agent_id] = agent
        return agent

    def update_agent(self, agent_id: str, **kwargs: Any) -> None:
        agent = self.agents.get(agent_id)
        if agent:
            for key, val in kwargs.items():
                agent[key] = val
            agent["updated_at"] = datetime.now(timezone.utc).isoformat()

    def remove_agent(self, agent_id: str) -> bool:
        if agent_id not in self.agents:
            return False
        del self.agents[agent_id]
        return True

    # -- Project tracking (experiments) --

    @property
    def experiments(self) -> Dict[str, ExperimentDict]:
        return self._data.setdefault("experiments", {})

    def get_experiment(self, experiment_id: str) -> Optional[ExperimentDict]:
        return self.experiments.get(experiment_id)

    def has_experiment(self, experiment_id: str) -> bool:
        return experiment_id in self.experiments

    def experiment_index_of(self, experiment_id: str) -> int:
        """Return the 1-based index of a project (insertion order)."""
        for i, key in enumerate(self.experiments, 1):
            if key == experiment_id:
                return i
        return 0

    def find_experiment(self, query: str) -> Optional[ExperimentDict]:
        """Find a project by id, 1-based index, or name substring.

        Returns the first match.  Use ``find_all_experiments`` when you need
        to detect ambiguous queries.
        """
        matches = self.find_all_experiments(query)
        return matches[0] if matches else None

    def find_all_experiments(self, query: str) -> List[ExperimentDict]:
        """Return all projects matching *query* (id, index, or name substring)."""
        if not query:
            return []
        # Try index number (always unique)
        try:
            idx = int(query)
            keys = list(self.experiments.keys())
            if 1 <= idx <= len(keys):
                return [self.experiments[keys[idx - 1]]]
        except ValueError:
            pass
        # Try exact id (always unique)
        if query in self.experiments:
            return [self.experiments[query]]
        # Substring search (may match multiple)
        query_lower = query.lower()
        matches: List[ExperimentDict] = []
        for proj in self.experiments.values():
            if (query_lower in proj.get("name", "").lower()
                    or query_lower in proj.get("id", "").lower()):
                matches.append(proj)
        return matches

    def add_experiment(
        self,
        experiment_id: str,
        name: str,
        path: str,
        description: str = "",
        status: str = "tracking",
        tags: Optional[List[str]] = None,
        goals: Optional[List[Dict[str, Any]]] = None,
        notebook_sections: Optional[List[str]] = None,
        agent_type: str = "claude",
        session_budget_seconds: Optional[int] = None,
        sister_of: Optional[str] = None,
        compute: Optional[Dict[str, Any]] = None,
        workspace_id: Optional[str] = None,
    ) -> None:
        resolved_path = str(Path(path).expanduser().resolve()) if path else path
        proj: ExperimentDict = {
            "id": experiment_id,
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
            "agent_type": agent_type,
            "workspace_id": workspace_id,
        }
        if session_budget_seconds is not None:
            proj["session_budget_seconds"] = session_budget_seconds
        if sister_of is not None:
            proj["sister_of"] = sister_of
        if compute is not None:
            proj["compute"] = compute
        self.experiments[experiment_id] = proj

    def update_experiment(self, experiment_id: str, **kwargs: Any) -> None:
        proj = self.experiments.get(experiment_id)
        if proj:
            for key, val in kwargs.items():
                proj[key] = val

    def experiments_for_workspace(self, workspace_id: str) -> List[ExperimentDict]:
        """Return all experiments linked to a workspace (optional relationship)."""
        return [
            p for p in self.experiments.values()
            if p.get("workspace_id") == workspace_id
        ]

    def remove_experiment(self, experiment_id: str) -> bool:
        if experiment_id not in self.experiments:
            return False
        del self.experiments[experiment_id]
        return True

    def get_run(self, experiment_id: str, run_id: str) -> Optional[RunDict]:
        proj = self.experiments.get(experiment_id)
        if not proj:
            return None
        return proj.get("runs", {}).get(run_id)

    def add_run(self, experiment_id: str, run_id: str, run_data: RunDict) -> None:
        proj = self.experiments.get(experiment_id)
        if proj:
            proj.setdefault("runs", {})[run_id] = run_data

    def update_run(self, experiment_id: str, run_id: str, **kwargs: Any) -> None:
        proj = self.experiments.get(experiment_id)
        if not proj:
            return
        run = proj.get("runs", {}).get(run_id)
        if run:
            for key, val in kwargs.items():
                run[key] = val

    def remove_run(self, experiment_id: str, run_id: str) -> bool:
        """Remove a run from a project. Returns True if found and removed."""
        proj = self.experiments.get(experiment_id)
        if not proj:
            return False
        runs = proj.get("runs", {})
        if run_id not in runs:
            return False
        del runs[run_id]
        return True

    # -- Session tracking (experiment launcher) --

    def add_session(self, experiment_id: str, session_id: str, session_data: dict) -> None:
        """Add a launcher session to a project."""
        proj = self.experiments.get(experiment_id)
        if proj:
            proj.setdefault("sessions", {})[session_id] = session_data

    def update_session(self, experiment_id: str, session_id: str, **kwargs) -> None:
        """Update fields on a launcher session."""
        proj = self.experiments.get(experiment_id)
        if not proj:
            return
        sess = proj.get("sessions", {}).get(session_id)
        if sess:
            for key, val in kwargs.items():
                sess[key] = val

    def active_sessions(self) -> list[tuple[str, str, dict]]:
        """Return [(experiment_id, session_id, session_dict)] for all running sessions."""
        result = []
        for proj_id, proj in self.experiments.items():
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

    # -- Events stream (v2 Phase 5) --

    @property
    def events(self) -> List[Dict[str, Any]]:
        """Return the unified event stream."""
        return self._data.setdefault("events", [])

    def add_event(self, event) -> None:
        """Append an event to the stream. Accepts an Event dataclass or dict."""
        if hasattr(event, "to_dict"):
            evt_dict = event.to_dict()
        else:
            evt_dict = dict(event) if not isinstance(event, dict) else event
        self.events.append(evt_dict)
        # Keep stream bounded — trim oldest events beyond 10,000
        if len(self.events) > 10000:
            self._data["events"] = self.events[-10000:]

    def query_events(
        self,
        *,
        experiment_id: str = "",
        event_types: Optional[List[str]] = None,
        since: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query events with optional filters. Returns newest-first."""
        results = []
        for evt in reversed(self.events):
            if experiment_id and evt.get("experiment_id") != experiment_id:
                continue
            if event_types and evt.get("event_type") not in event_types:
                continue
            if since and (evt.get("timestamp", "") < since):
                continue
            results.append(evt)
            if len(results) >= limit:
                break
        return results

    # -- v2 Migration --

    def migrate_v2_phase2(self) -> int:
        """Backfill v2 fields on experiments and agents. Idempotent. Returns change count."""
        import shutil as _shutil

        changes = 0

        # 1. Bootstrap harness registry if empty
        if not self.harnesses:
            self.harnesses["claude-code"] = {
                "id": "claude-code",
                "label": "Claude Code",
                "binary": "claude",
                "available": _shutil.which("claude") is not None,
                "install_hint": "npm install -g @anthropic-ai/claude-code",
                "description": "Anthropic's Claude Code CLI",
                "context_file": "CLAUDE.md",
                "mcp_support": True,
            }
            self.harnesses["distillate-sdk"] = {
                "id": "distillate-sdk",
                "label": "Distillate Agent SDK",
                "binary": "",
                "available": True,
                "install_hint": "",
                "description": "Powers Nicolas and Tier 2 sub-agents",
                "context_file": "",
                "mcp_support": True,
            }
            changes += 2
            log.info("v2 migration: bootstrapped harness registry")

        # 2. Backfill agent_id and harness_id on experiments
        for proj_id, proj in self.experiments.items():
            if not proj.get("agent_id"):
                proj["agent_id"] = "claude-code"
                changes += 1
            if not proj.get("harness_id"):
                # Map legacy agent_type to harness_id
                legacy = proj.get("agent_type", "claude")
                proj["harness_id"] = legacy if legacy != "claude" else "claude-code"
                changes += 1
            # Backfill orphan experiments → Workbench
            if not proj.get("workspace_id"):
                default_ws = self.get_default_workspace()
                if default_ws:
                    proj["workspace_id"] = default_ws["id"]
                    changes += 1
            # Backfill produced_by_agent_id on runs
            for run_id, run in (proj.get("runs") or {}).items():
                if not run.get("produced_by_agent_id"):
                    run["produced_by_agent_id"] = proj.get("agent_id", "claude-code")
                    changes += 1

        # 3. Backfill tier + harness_id on agents
        for agent_id, agent in self.agents.items():
            if not agent.get("tier"):
                agent["tier"] = "shell" if agent.get("agent_type") == "nicolas" else "agent"
                changes += 1
            if not agent.get("harness_id"):
                agent["harness_id"] = "distillate-sdk" if agent.get("agent_type") == "nicolas" else "claude-code"
                changes += 1
            if not agent.get("emoji"):
                agent["emoji"] = "\u2697\uFE0F" if agent.get("agent_type") == "nicolas" else ""
                changes += 1

        # 4. Mark migration as done
        self._data["v2_phase2_migrated"] = True

        if changes:
            log.info("v2 migration: %d fields backfilled", changes)
        return changes


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
