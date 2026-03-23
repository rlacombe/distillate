"""Cloud state sync via Supabase Storage.

Pushes/pulls state.json to Supabase so state is available across devices
and the cloud email system can generate content from real data.

Requires ``DISTILLATE_AUTH_TOKEN`` and ``DISTILLATE_CLOUD_URL`` in config.
When credentials are absent, all functions are silent no-ops.
"""

import json
import logging
import os

import requests

from distillate import config
from distillate.state import State

log = logging.getLogger(__name__)

_TIMEOUT = 30  # seconds — state.json can be ~500 KB


def cloud_sync_available() -> bool:
    """True when Supabase cloud sync credentials are configured."""
    token = os.environ.get("DISTILLATE_AUTH_TOKEN", "").strip()
    cloud_url = os.environ.get("DISTILLATE_CLOUD_URL", "").strip()
    return bool(token and cloud_url)


def _sync_url() -> str:
    cloud_url = os.environ.get("DISTILLATE_CLOUD_URL", "").strip().rstrip("/")
    return f"{cloud_url}/state-sync"


def _token() -> str:
    return os.environ.get("DISTILLATE_AUTH_TOKEN", "").strip()


def push_state(state: State) -> bool:
    """Push local state.json to Supabase Storage. Returns True on success."""
    if not cloud_sync_available():
        return False

    try:
        data = json.dumps(state._data, default=str)
        resp = requests.post(
            _sync_url(),
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-auth-token": _token(),
            },
            timeout=_TIMEOUT,
        )
        if resp.ok:
            result = resp.json()
            log.info("Cloud push: %d bytes uploaded", result.get("size", 0))
            return True
        log.warning("Cloud push failed: %d %s", resp.status_code, resp.text[:200])
        return False
    except requests.exceptions.ConnectionError:
        log.warning("Cloud unreachable for push")
        return False
    except requests.exceptions.Timeout:
        log.warning("Cloud push timed out")
        return False
    except Exception:
        log.warning("Cloud push failed", exc_info=True)
        return False


def pull_state(state: State) -> bool:
    """Pull remote state.json and merge into local. Returns True on success."""
    if not cloud_sync_available():
        return False

    try:
        resp = requests.get(
            _sync_url(),
            headers={"x-auth-token": _token()},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            log.info("No remote state found (first sync?)")
            return False
        if not resp.ok:
            log.warning("Cloud pull failed: %d", resp.status_code)
            return False

        remote = resp.json()
        _merge_documents(state, remote.get("documents", {}))
        _merge_projects(state, remote.get("projects", {}))
        state.save()
        log.info("Cloud pull: merged remote state")
        return True
    except requests.exceptions.ConnectionError:
        log.warning("Cloud unreachable for pull")
        return False
    except requests.exceptions.Timeout:
        log.warning("Cloud pull timed out")
        return False
    except Exception:
        log.warning("Cloud pull failed", exc_info=True)
        return False


def sync_state(state: State) -> bool:
    """Full sync: pull remote changes, then push local state."""
    if not cloud_sync_available():
        return False
    pull_state(state)
    return push_state(state)


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

    if remote_rank > local_rank:
        local["status"] = remote["status"]

    if not local.get("processed_at") and remote.get("processed_at"):
        local["processed_at"] = remote["processed_at"]

    if not local.get("summary") and remote.get("summary"):
        local["summary"] = remote["summary"]

    local_meta = local.setdefault("metadata", {})
    remote_meta = remote.get("metadata", {})
    for mk, mv in remote_meta.items():
        if mk not in local_meta or local_meta[mk] is None:
            local_meta[mk] = mv

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
