"""Cloud state sync via the Distillate API (Cloudflare Worker).

Pushes/pulls documents and projects to ``DISTILLATE_CLOUD_URL``
(default ``https://api.distillate.dev``) so state is available across
devices and the cloud email system can generate content from real data.

Requires ``DISTILLATE_AUTH_TOKEN`` and ``DISTILLATE_CLOUD_URL`` in config.
When credentials are absent, all functions are silent no-ops.
"""

import json
import logging
import os

import requests

from distillate.state import State

log = logging.getLogger(__name__)

_TIMEOUT = 30  # seconds


def cloud_sync_available() -> bool:
    """True when cloud sync credentials are configured.

    Accepts either a session JWT (OAuth sign-in) or the legacy opaque token.
    """
    from distillate import secrets as _secrets
    cloud_url = os.environ.get("DISTILLATE_CLOUD_URL", "").strip()
    if not cloud_url:
        return False
    return bool(_secrets.get("DISTILLATE_SESSION_JWT") or _secrets.get("DISTILLATE_AUTH_TOKEN"))


def _base_url() -> str:
    return os.environ.get("DISTILLATE_CLOUD_URL", "").strip().rstrip("/")


def _headers() -> dict:
    """Build auth headers. Prefers session JWT; falls back to legacy opaque token."""
    from distillate import secrets as _secrets
    jwt = _secrets.get("DISTILLATE_SESSION_JWT")
    if jwt:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {jwt}"}
    token = _secrets.get("DISTILLATE_AUTH_TOKEN")
    if token:
        return {"Content-Type": "application/json", "x-auth-token": token}
    return {"Content-Type": "application/json"}


def push_state(state: State) -> bool:
    """Push changed documents and projects to the cloud.

    Uses delta sync: only rows with ``updated_at > last_pushed_at`` are sent.
    On the first push (no watermark), all rows are sent.  Includes tombstones
    (rows with ``deleted_at`` set) so the remote can soft-delete.

    Returns True on success.
    """
    if not cloud_sync_available():
        return False

    from distillate import state_sqlite

    base = _base_url()
    headers = _headers()
    ok_docs = ok_projs = False

    last_pushed = state_sqlite.get_meta("last_pushed_at")

    # Delta: only changed documents since last push
    changed_docs = state_sqlite.changed_documents_since(last_pushed)
    if changed_docs:
        try:
            resp = requests.put(
                f"{base}/state/documents",
                data=json.dumps({"documents": changed_docs}, default=str),
                headers=headers,
                timeout=_TIMEOUT,
            )
            if resp.ok:
                n = resp.json().get("upserted", 0)
                log.info("Cloud push: %d document(s) synced (delta)", n)
                ok_docs = True
            elif resp.status_code == 401:
                log.warning("Cloud push: session expired or invalid, pausing sync")
                from distillate import auth as _auth
                _auth.clear_session()
                return False
            else:
                log.warning("Cloud push documents failed: %d %s", resp.status_code, resp.text[:200])
        except requests.exceptions.ConnectionError:
            log.warning("Cloud unreachable for document push")
        except requests.exceptions.Timeout:
            log.warning("Cloud document push timed out")
        except Exception:
            log.warning("Cloud document push failed", exc_info=True)
    else:
        log.debug("Cloud push: no document changes to sync")
        ok_docs = True

    # Delta: only changed experiments since last push
    changed_exps = state_sqlite.changed_experiments_since(last_pushed)
    if changed_exps:
        try:
            resp = requests.put(
                f"{base}/state/experiments",
                data=json.dumps({"experiments": changed_exps}, default=str),
                headers=headers,
                timeout=_TIMEOUT,
            )
            if resp.ok:
                n = resp.json().get("upserted", 0)
                log.info("Cloud push: %d experiment(s) synced (delta)", n)
                ok_projs = True
            elif resp.status_code == 401:
                log.warning("Cloud push: session expired or invalid, pausing sync")
                from distillate import auth as _auth
                _auth.clear_session()
                return False
            else:
                log.warning("Cloud push experiments failed: %d %s", resp.status_code, resp.text[:200])
        except requests.exceptions.ConnectionError:
            log.warning("Cloud unreachable for experiment push")
        except requests.exceptions.Timeout:
            log.warning("Cloud experiment push timed out")
        except Exception:
            log.warning("Cloud experiment push failed", exc_info=True)
    else:
        log.debug("Cloud push: no experiment changes to sync")
        ok_projs = True

    # Advance the watermark on success
    if ok_docs and ok_projs:
        from datetime import datetime, timezone
        state_sqlite.set_meta(
            "last_pushed_at",
            datetime.now(timezone.utc).isoformat(),
        )

    return ok_docs and ok_projs


def pull_state(state: State) -> bool:
    """Pull remote documents and projects, merge into local. Returns True on success."""
    if not cloud_sync_available():
        return False

    base = _base_url()
    headers = _headers()
    since = state.last_cloud_sync_at
    params = {"since": since} if since else {}
    sync_at = None

    # Pull documents
    try:
        resp = requests.get(
            f"{base}/state/documents",
            headers=headers,
            params=params,
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            log.info("No remote documents found (first sync?)")
        elif resp.status_code == 401:
            log.warning("Cloud pull: session expired or invalid, pausing sync")
            from distillate import auth as _auth
            _auth.clear_session()
            return False
        elif not resp.ok:
            log.warning("Cloud pull documents failed: %d", resp.status_code)
            return False
        else:
            data = resp.json()
            _merge_documents(state, data.get("documents", {}))
            sync_at = data.get("sync_at")
    except requests.exceptions.ConnectionError:
        log.warning("Cloud unreachable for document pull")
        return False
    except requests.exceptions.Timeout:
        log.warning("Cloud document pull timed out")
        return False
    except Exception:
        log.warning("Cloud document pull failed", exc_info=True)
        return False

    # Pull experiments
    try:
        resp = requests.get(
            f"{base}/state/experiments",
            headers=headers,
            params=params,
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            log.info("No remote experiments found (first sync?)")
        elif not resp.ok:
            log.warning("Cloud pull experiments failed: %d", resp.status_code)
            return False
        else:
            data = resp.json()
            _merge_experiments(state, data.get("experiments", {}))
            # Use the later sync_at as watermark
            proj_sync_at = data.get("sync_at")
            if proj_sync_at and (not sync_at or proj_sync_at > sync_at):
                sync_at = proj_sync_at
    except requests.exceptions.ConnectionError:
        log.warning("Cloud unreachable for experiment pull")
        return False
    except requests.exceptions.Timeout:
        log.warning("Cloud project pull timed out")
        return False
    except Exception:
        log.warning("Cloud project pull failed", exc_info=True)
        return False

    if sync_at:
        state.last_cloud_sync_at = sync_at
    state.save()
    log.info("Cloud pull: merged remote state")
    return True


def sync_state(state: State) -> bool:
    """Full sync: pull remote changes, then push local state.

    After a successful push, refreshes the Supabase snapshot so the
    email functions have current experiment data.  Also cleans up
    tombstoned rows older than 30 days.
    """
    if not cloud_sync_available():
        return False

    # Clean up old tombstones before syncing
    _cleanup_tombstones()

    pull_state(state)
    ok = push_state(state)
    if ok:
        _refresh_snapshot(state)
    return ok


def _cleanup_tombstones() -> None:
    """Remove tombstoned rows older than 30 days."""
    try:
        from datetime import datetime, timedelta, timezone
        from distillate import state_sqlite
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        n = state_sqlite.hard_delete_before(cutoff)
        if n:
            log.info("Cleaned up %d tombstoned row(s) older than 30 days", n)
    except Exception:
        log.debug("Tombstone cleanup failed (non-critical)", exc_info=True)


def _refresh_snapshot(state: State) -> None:
    """Push a lightweight snapshot to Supabase for email rendering."""
    try:
        from distillate.cloud_email import sync_snapshot, _cloud_configured
        if _cloud_configured():
            sync_snapshot(state)
            log.debug("Cloud snapshot refreshed after sync")
    except Exception:
        log.debug("Snapshot refresh failed (non-critical)", exc_info=True)


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

# Run decisions advance monotonically: a "best" run stays best.
_DECISION_ORDER = {
    "running": 0,
    "crash": 1,
    "completed": 2,
    "best": 3,
}

_RUN_STATUS_ORDER = {
    "running": 0,
    "failed": 1,
    "completed": 2,
}


def _merge_documents(state: State, remote: dict) -> None:
    """Merge remote documents into local state.

    Handles tombstones: if a remote document has ``deleted_at`` set,
    the local copy is marked deleted.
    """
    for key, remote_doc in remote.items():
        # Tombstone: remote says this document was deleted
        if remote_doc.get("deleted_at"):
            if state.has_document(key):
                state.mark_deleted(key)
                log.info("Cloud pull: marked document '%s' as deleted (remote tombstone)", key)
            continue

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


def _merge_experiments(state: State, remote: dict) -> None:
    """Merge remote projects into local state.

    Handles tombstones: if a remote project has ``deleted_at`` set,
    the local copy is removed.

    New projects are added wholesale.  Existing projects get run-level
    merge: new runs are added, existing runs have their fields filled
    and decisions advanced.
    """
    for pid, remote_proj in remote.items():
        # Tombstone: remote says this project was deleted
        if remote_proj.get("deleted_at"):
            if state.has_experiment(pid):
                state.remove_experiment(pid)
                log.info("Cloud pull: removed project '%s' (remote tombstone)", pid)
            continue

        if not state.has_experiment(pid):
            state.experiments[pid] = remote_proj
            log.info("Cloud pull: added project '%s'", remote_proj.get("name", pid))
        else:
            _merge_single_project(state.experiments[pid], remote_proj)


def _merge_single_project(local: dict, remote: dict) -> None:
    """Merge a remote project into a local one.

    Project metadata: remote fills gaps (local wins ties).
    Runs: union by run_id, field-level merge for shared runs.
    """
    for field in ("name", "path", "key_metric", "key_metric_direction",
                  "github_url", "goals", "linked_papers", "added_at",
                  "template", "description"):
        if not local.get(field) and remote.get(field):
            local[field] = remote[field]

    local_runs = local.setdefault("runs", {})
    remote_runs = remote.get("runs", {})
    added = 0
    for run_id, remote_run in remote_runs.items():
        if run_id not in local_runs:
            local_runs[run_id] = remote_run
            added += 1
        else:
            _merge_single_run(local_runs[run_id], remote_run)
    if added:
        log.info("Cloud pull: added %d run(s) to project '%s'",
                 added, local.get("name", "?"))


def _merge_single_run(local: dict, remote: dict) -> None:
    """Merge a remote run into a local one.

    Decision and status only advance forward (monotonic).
    Scalar fields: remote fills gaps.
    Dict fields (results, hyperparameters): merge key-by-key.
    """
    # Decision rank: running < crash < completed < best
    local_rank = _DECISION_ORDER.get(local.get("decision", ""), -1)
    remote_rank = _DECISION_ORDER.get(remote.get("decision", ""), -1)
    if remote_rank > local_rank:
        local["decision"] = remote["decision"]

    # Status rank: running < failed < completed
    local_sr = _RUN_STATUS_ORDER.get(local.get("status", ""), -1)
    remote_sr = _RUN_STATUS_ORDER.get(remote.get("status", ""), -1)
    if remote_sr > local_sr:
        local["status"] = remote["status"]

    # Scalar fields: remote fills gaps
    for field in ("completed_at", "duration_minutes", "description",
                  "hypothesis", "reasoning", "baseline_comparison",
                  "name", "started_at", "tags"):
        if not local.get(field) and remote.get(field):
            local[field] = remote[field]

    # Dict fields: merge key-by-key (local wins ties)
    for dict_field in ("results", "hyperparameters"):
        remote_dict = remote.get(dict_field)
        if remote_dict and isinstance(remote_dict, dict):
            local_dict = local.setdefault(dict_field, {})
            for k, v in remote_dict.items():
                if k not in local_dict:
                    local_dict[k] = v
