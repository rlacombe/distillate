"""Client-side cloud state sync.

Pushes documents and projects to the cloud API so state is available
across CLI and desktop.  Pulls remote changes on startup to merge
state from other devices.

Requires ``DISTILLATE_AUTH_TOKEN`` and ``DISTILLATE_API_URL`` in config.
When credentials are absent, all functions are silent no-ops.
"""

import logging
from typing import Optional

import requests

from distillate import config
from distillate.state import State

log = logging.getLogger(__name__)

_TIMEOUT = 15  # seconds — generous for ~200 KB payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cloud_sync_available() -> bool:
    """True when cloud sync credentials are configured."""
    return bool(config.DISTILLATE_AUTH_TOKEN and config.DISTILLATE_API_URL)


def push_state(state: State) -> bool:
    """Push local documents and projects to the cloud.

    Returns True when both collections are pushed successfully.
    """
    if not cloud_sync_available():
        return False

    ok_docs = _push_collection("/state/documents", {"documents": state.documents})
    ok_proj = _push_collection("/state/projects", {"projects": state.projects})

    if ok_docs or ok_proj:
        log.info("Cloud push complete (docs=%s, projects=%s)", ok_docs, ok_proj)
    return ok_docs and ok_proj


def pull_state(state: State) -> bool:
    """Pull remote state and merge into local.

    Returns True when at least one collection was pulled successfully.
    """
    if not cloud_sync_available():
        return False

    since = state.last_cloud_sync_at
    any_ok = False

    # Pull documents
    doc_data = _pull_collection("/state/documents", since)
    if doc_data and "documents" in doc_data:
        _merge_documents(state, doc_data["documents"])
        sync_at = doc_data.get("sync_at")
        if sync_at:
            state.last_cloud_sync_at = sync_at
        any_ok = True

    # Pull projects
    proj_data = _pull_collection("/state/projects", since)
    if proj_data and "projects" in proj_data:
        _merge_projects(state, proj_data["projects"])
        sync_at = proj_data.get("sync_at")
        if sync_at:
            state.last_cloud_sync_at = sync_at
        any_ok = True

    if any_ok:
        state.save()
    return any_ok


def sync_state(state: State) -> bool:
    """Full sync: pull remote changes, then push local state."""
    if not cloud_sync_available():
        return False
    pull_state(state)
    return push_state(state)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.DISTILLATE_AUTH_TOKEN}",
        "Content-Type": "application/json",
    }


def _api_url(path: str) -> str:
    return f"{config.DISTILLATE_API_URL.rstrip('/')}{path}"


def _push_collection(path: str, payload: dict) -> bool:
    """PUT a collection to the cloud.  Returns True on success."""
    try:
        resp = requests.put(
            _api_url(path),
            json=payload,
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if resp.ok:
            log.info("Cloud push %s: %s", path, resp.json())
            return True
        log.warning("Cloud push %s failed: %d %s", path, resp.status_code, resp.text[:200])
        return False
    except requests.exceptions.ConnectionError:
        log.warning("Cloud unreachable for push %s", path)
        return False
    except requests.exceptions.Timeout:
        log.warning("Cloud push %s timed out", path)
        return False
    except Exception:
        log.warning("Cloud push %s failed", path, exc_info=True)
        return False


def _pull_collection(path: str, since: Optional[str]) -> Optional[dict]:
    """GET a collection from the cloud.  Returns response JSON or None."""
    params = {}
    if since:
        params["since"] = since
    try:
        resp = requests.get(
            _api_url(path),
            params=params,
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if resp.ok:
            return resp.json()
        log.warning("Cloud pull %s failed: %d", path, resp.status_code)
        return None
    except requests.exceptions.ConnectionError:
        log.warning("Cloud unreachable for pull %s", path)
        return None
    except requests.exceptions.Timeout:
        log.warning("Cloud pull %s timed out", path)
        return None
    except Exception:
        log.warning("Cloud pull %s failed", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

_STATUS_ORDER = {
    "on_remarkable": 0,
    "awaiting_pdf": 1,
    "tracked": 2,
    "processed": 3,
    "deleted": 4,
}


def _merge_documents(state: State, remote: dict) -> None:
    """Merge remote documents into local state (additive, no deletions)."""
    for key, remote_doc in remote.items():
        local_doc = state.get_document(key)
        if local_doc is None:
            # New document from another device — add it
            state.documents[key] = remote_doc
            log.info("Cloud pull: added document '%s'", remote_doc.get("title", key))
        else:
            _merge_single_document(local_doc, remote_doc)


def _merge_single_document(local: dict, remote: dict) -> None:
    """Merge a single remote document into a local one.

    Strategy: remote wins for fields the local doesn't have yet.
    Status only advances forward (never regresses).
    Metadata is merged key-by-key (remote fills gaps).
    """
    local_rank = _STATUS_ORDER.get(local.get("status", ""), 0)
    remote_rank = _STATUS_ORDER.get(remote.get("status", ""), 0)

    # Advance status if remote is further along
    if remote_rank > local_rank:
        local["status"] = remote["status"]

    # processed_at: take whichever exists (prefer local)
    if not local.get("processed_at") and remote.get("processed_at"):
        local["processed_at"] = remote["processed_at"]

    # Summary: prefer local, fill from remote
    if not local.get("summary") and remote.get("summary"):
        local["summary"] = remote["summary"]

    # Merge metadata dict (remote fills gaps)
    local_meta = local.setdefault("metadata", {})
    remote_meta = remote.get("metadata", {})
    for mk, mv in remote_meta.items():
        if mk not in local_meta or local_meta[mk] is None:
            local_meta[mk] = mv

    # Engagement and highlight fields: take whichever is nonzero
    for field in ("engagement", "highlight_count", "highlighted_pages",
                  "highlight_word_count", "page_count"):
        if local.get(field) is None and remote.get(field) is not None:
            local[field] = remote[field]


def _merge_projects(state: State, remote: dict) -> None:
    """Merge remote projects into local state (add-only)."""
    for pid, remote_proj in remote.items():
        if not state.has_project(pid):
            state.projects[pid] = remote_proj
            log.info("Cloud pull: added project '%s'", remote_proj.get("name", pid))
