"""Local WebSocket server for the Nicolas desktop app.

Bridges ``agent_sdk.NicolasClient`` to an async WebSocket so the
Electron renderer can consume events.

Not included in PyPI dependencies — ``fastapi`` and ``uvicorn`` are
only installed in the bundled Electron venv.

Usage::

    python -m distillate.server [port]

REST API Endpoints
------------------

General:
    GET  /status                              App version, paper/experiment counts.
    POST /sync                                Trigger cloud sync (pull + push).
    GET  /report                              Reading insights dashboard (lifetime stats,
                                              velocity, topics, engagement, citations).
    GET  /state/export                        Download current state.json for backup.
    POST /state/import                        Validate and import a state backup.
                                              Body: ``{"state": {...}}``.

Papers (Papers tab):
    GET  /papers                              List all papers (truncated summaries).
                                              Optional ``?status=processed`` filter.
                                              Includes ``promoted`` boolean per paper.
    GET  /papers/{paper_key}                  Full paper details: all authors, full
                                              summary, highlights, venue, DOI, etc.
    POST /papers/{paper_key}/promote          Add paper to promoted list.
    POST /papers/{paper_key}/unpromote        Remove paper from promoted list.
    POST /papers/{paper_key}/refresh-metadata Re-fetch metadata from Zotero + S2.
    GET  /papers/{paper_key}/pdf              PDF bytes for the desktop reader
                                              (local cache → Zotero fallback).
    GET  /papers/{paper_key}/annotations      Raw Zotero highlight annotations
                                              with position data for overlay.
    GET  /papers/{paper_key}/read-position    Last-read page for the reader.
    POST /papers/{paper_key}/read-position    Persist current page.  Body:
                                              ``{"page": <int>}``.

Experiments (Live tab):
    GET  /experiments/list                    All tracked projects with run summaries.
    GET  /experiments/{experiment_id}/notebook   Self-contained HTML lab notebook.
    GET  /experiments/stream                  SSE stream of experiment events.
    POST /experiments/attach                  Open terminal attached to tmux session.
                                              Body: ``{"project": "id"}``.

Campaign Orchestration:
    POST /experiments/{id}/campaign/start     Start autonomous campaign loop.
                                              Body: ``{"objective", "max_sessions",
                                              "max_hours", "model", "max_turns"}``.
    POST /experiments/{id}/campaign/pause     Pause campaign (finishes current session).
    POST /experiments/{id}/campaign/resume    Resume a paused campaign.
    POST /experiments/{id}/campaign/stop      Stop campaign permanently.
    POST /experiments/{id}/steer              Write steering instructions for next session.
                                              Body: ``{"text": "..."}``.

Multi-Agent Research Lab (M4 stubs):
    GET  /experiments/compare                 Compare metrics across experiments.
                                              Query: ``?ids=proj1,proj2,proj3``.
    POST /experiments/{id}/save-template      Save experiment as reusable template.
                                              Body: ``{"name": "..."}``.
    POST /experiments/campaign/parallel       Launch campaigns across multiple projects.
                                              Body: ``{"project_ids": [...],
                                              "max_sessions": 5, "model": "..."}``.

Chat:
    WS   /ws                                  Agent chat (Nicolas REPL over WebSocket).
"""

import asyncio
import json
import logging
import sys

from concurrent.futures import ThreadPoolExecutor
from distillate.agent_sdk import NicolasClient, _classify_error
from distillate.state import State

log = logging.getLogger(__name__)

_DEFAULT_PORT = 8742
_executor = ThreadPoolExecutor(max_workers=8)

# Module-level singletons for the Nicolas client + its shared State.
# Hoisted out of _create_app so tests can monkey-patch _get_nicolas.
_nicolas_singleton: "NicolasClient | None" = None
_app_state: "State | None" = None


def _register_app_state(state: "State") -> None:
    """Called once by _create_app to hand the shared State to _get_nicolas."""
    global _app_state, _nicolas_singleton
    _app_state = state
    _nicolas_singleton = None  # reset so the next _get_nicolas() uses this state


def _get_nicolas() -> "NicolasClient":
    global _nicolas_singleton
    if _nicolas_singleton is None:
        _nicolas_singleton = NicolasClient(_app_state)
    return _nicolas_singleton


def _find_ui_dir():
    """Resolve the renderer files directory.

    Production (installed from wheel): distillate/ui/ next to this file.
    Development (monorepo): desktop/renderer/ at the repo root.
    Returns ``None`` if neither exists.
    """
    from pathlib import Path

    # Production: installed wheel puts files in distillate/ui/
    prod = Path(__file__).parent / "ui"
    if prod.is_dir() and (prod / "index.html").exists():
        return prod

    # Development: monorepo layout
    dev = Path(__file__).parent.parent / "desktop" / "renderer"
    if dev.is_dir() and (dev / "index.html").exists():
        return dev

    return None


def _create_app():
    """Build the FastAPI application (lazy import so PyPI installs don't need fastapi)."""

    from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse

    from distillate import config
    config.ensure_loaded(required=False)

    # Shared state (created before lifespan so it's available everywhere)
    _state = State()
    _last_reload = 0.0
    _RELOAD_TTL = 2.0  # seconds — skip re-reading disk if recently loaded

    # Bootstrap Nicolas as the default long-lived agent
    if not _state.get_agent("nicolas"):
        _state.add_agent("nicolas", "Nicolas", agent_type="nicolas", builtin=True)
        _state.save()

    # Bootstrap Workbench default project (idempotent)
    if not _state.get_default_workspace():
        _state.ensure_workbench()
        _state.save()

    # v2 Phase 2 migration: backfill agent_id, harness_id, tier fields
    if not _state._data.get("v2_phase2_migrated"):
        changes = _state.migrate_v2_phase2()
        if changes:
            _state.save()

    _initial_sync_done = False

    # Initialize shared context for router modules
    from distillate.routes import _context
    _context.init(_state)

    # Auto-recover lost coding sessions on startup
    try:
        from distillate.experiment_tools import recover_all_sessions_tool
        result = recover_all_sessions_tool(state=_state)
        if result.get("recovered", 0) > 0:
            log.info("Startup: recovered %d coding session(s)", result["recovered"])
    except Exception:
        log.debug("Startup session recovery failed", exc_info=True)

    app = FastAPI(title="Nicolas", docs_url=None, redoc_url=None)

    # Allow cross-origin requests from the Electron renderer
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _cached_reload():
        """Reload state from disk at most once per _RELOAD_TTL seconds."""
        nonlocal _last_reload
        import time
        now = time.monotonic()
        if now - _last_reload >= _RELOAD_TTL:
            _state.reload()
            _last_reload = now

    def _require_local_auth(request: Request) -> None:
        """Guard for sensitive endpoints: require auth token or Electron origin.

        Accepts the request if ANY of these hold:
        - x-auth-token header matches DISTILLATE_AUTH_TOKEN
        - Origin is http://127.0.0.1 or http://localhost (Electron renderer)
        - Referer starts with http://127.0.0.1 or http://localhost

        Raises 403 otherwise.
        """
        import os as _os
        token = request.headers.get("x-auth-token", "")
        expected = _os.environ.get("DISTILLATE_AUTH_TOKEN", "").strip()
        if expected and token == expected:
            return

        origin = request.headers.get("origin", "")
        referer = request.headers.get("referer", "")
        trusted = ("http://127.0.0.1", "http://localhost")
        if any(origin.startswith(t) for t in trusted):
            return
        if any(referer.startswith(t) for t in trusted):
            return

        raise HTTPException(status_code=403, detail="Authentication required")

    def _get_project_or_404(experiment_id: str):
        """Reload state and look up a project, raising 404 if not found."""
        _cached_reload()
        proj = _state.find_experiment(experiment_id)
        if not proj:
            raise HTTPException(404, detail="not_found")
        return proj

    # Shared Nicolas singleton — one per server process. The WebSocket
    # handler and the /nicolas/sessions HTTP endpoints all route through
    # the same instance so switching sessions from the sidebar affects
    # the next WS send. Disconnect happens only at app shutdown.
    #
    # Hand the state to the module-level _get_nicolas factory so tests can
    # monkeypatch it to a fake.
    _register_app_state(_state)

    @app.get("/usage")
    async def usage_snapshot():
        """Billing snapshot: session/today/week/all/by_model + current_model."""
        from distillate.agent_runtime import usage_tracker as _ut
        nicolas = _get_nicolas()
        sid = getattr(nicolas, "session_id", None)
        return _ut.get_tracker().snapshot(session_id=sid)

    @app.post("/preferences/budget")
    async def set_budget(request: Request):
        """Persist budget thresholds without requiring an open WebSocket."""
        body = await request.json()
        nicolas = _get_nicolas()
        nicolas.set_budget_thresholds(
            compact_suggest=body.get("compact_suggest"),
            session_hard=body.get("session_hard"),
        )
        return {"ok": True}

    @app.get("/status")
    async def status():
        from importlib.metadata import version
        ver = version("distillate")
        _cached_reload()
        processed = _state.documents_with_status("processed")
        from distillate import config
        q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
        queue = _state.documents_with_status(q_status)

        # Experiment stats
        experiment_stats = None
        projects = _state.experiments
        if projects:
            total_runs = 0
            runs_best = 0
            active_sessions = 0
            session_details = []
            for proj in projects.values():
                for run in proj.get("runs", {}).values():
                    total_runs += 1
                    decision = run.get("decision", "")
                    if decision == "best":
                        runs_best += 1
                    if run.get("status") == "running":
                        active_sessions += 1
                # Launcher sessions
                for sess in proj.get("sessions", {}).values():
                    if sess.get("status") == "running":
                        session_details.append({
                            "name": proj.get("name", proj.get("id", "")),
                            "status": "running",
                            "runs": len(proj.get("runs", {})),
                            "since": sess.get("started_at", ""),
                        })
            if total_runs > 0 or session_details:
                experiment_stats = {
                    "total_projects": len(projects),
                    "active_sessions": active_sessions + len(session_details),
                    "total_runs": total_runs,
                    "runs_best": runs_best,
                }
                if session_details:
                    experiment_stats["sessions"] = session_details

        resp = {
            "ok": True,
            "version": ver,
            "papers_read": len(processed),
            "papers_queued": len(queue),
            "library_configured": bool(config.ZOTERO_API_KEY and config.ZOTERO_USER_ID),
            "reading_source": config.READING_SOURCE,
        }
        if experiment_stats:
            resp["experiments"] = experiment_stats

        return JSONResponse(resp)

    @app.get("/connectors")
    async def list_connectors():
        """Return status of all available connectors."""
        import os
        import shutil

        connectors = []

        # Papers — Zotero
        connectors.append({
            "id": "zotero",
            "label": "Papers",
            "service": "Zotero library",
            "connected": bool(config.ZOTERO_API_KEY and config.ZOTERO_USER_ID),
            "setup": "library",
        })

        # Notifications — Email
        email = os.environ.get("DISTILLATE_EMAIL", "").strip()
        connectors.append({
            "id": "email",
            "label": "Updates",
            "service": "Email",
            "connected": bool(email),
            "detail": None,
            "setup": "email",
        })

        # Notes — Obsidian
        has_obsidian = bool(config.OBSIDIAN_VAULT_PATH)
        connectors.append({
            "id": "obsidian",
            "label": "Notes",
            "service": "Obsidian vault",
            "connected": has_obsidian,
            "detail": None,
            "setup": "obsidian",
            "icon": "obsidian",
            "vault_name": config.OBSIDIAN_VAULT_NAME if has_obsidian else "",
            "papers_folder": config.OBSIDIAN_PAPERS_FOLDER if has_obsidian else "",
        })

        # Tablet — reMarkable (only show if reading source is remarkable or rmapi is available)
        has_rmapi = shutil.which("rmapi") is not None
        has_token = bool(config.REMARKABLE_DEVICE_TOKEN)
        if config.READING_SOURCE == "remarkable" or has_rmapi:
            connectors.append({
                "id": "remarkable",
                "label": "Tablet",
                "service": "reMarkable",
                "connected": has_rmapi and has_token,
                "setup": "remarkable",
            })

        # HuggingFace — inference, compute, models
        hf_token = os.environ.get("HF_TOKEN", "").strip()
        connectors.append({
            "id": "huggingface",
            "label": "HuggingFace",
            "service": "Models, compute & inference",
            "connected": bool(hf_token),
            "setup": "huggingface",
        })

        return JSONResponse({"ok": True, "connectors": connectors})

    @app.get("/init")
    async def init_bundle():
        """Return status + workspaces + experiments + papers + connectors in one call.

        Replaces the five parallel fetches on app startup with a single
        round-trip, cutting initial load latency.  After the first call,
        triggers a background cloud sync (deferred to avoid racing with
        the initial UI load).
        """
        nonlocal _initial_sync_done
        import asyncio as _aio
        from distillate.routes.experiments import list_experiments as _list_experiments
        from distillate.routes.papers import list_papers as _list_papers
        from distillate.routes.settings import list_integrations as _list_integrations
        from distillate.experiment_tools import list_workspaces_tool as _list_workspaces_tool

        def _get_workspaces():
            return _list_workspaces_tool(state=_state)

        status_resp, experiments_resp, papers_resp, integrations_resp, workspaces_data = await _aio.gather(
            status(),
            _list_experiments(),
            _list_papers(),
            _list_integrations(),
            _aio.to_thread(_get_workspaces),
        )
        # Each returns a JSONResponse — extract the bodies
        import json as _json
        integrations_data = _json.loads(integrations_resp.body)
        resp = JSONResponse({
            "ok": True,
            "status": _json.loads(status_resp.body),
            "workspaces": workspaces_data,
            "experiments": _json.loads(experiments_resp.body),
            "papers": _json.loads(papers_resp.body),
            "connectors": {"ok": True, "connectors": integrations_data.get("library", [])},
            "integrations": integrations_data,
        })

        # Deferred cloud sync: run once after the UI has loaded
        if not _initial_sync_done:
            _initial_sync_done = True
            from distillate.cloud_sync import cloud_sync_available, sync_state
            if cloud_sync_available():
                loop = asyncio.get_event_loop()
                loop.run_in_executor(_executor, sync_state, _state)

        return resp

    @app.get("/welcome/state")
    async def welcome_state():
        """Return the welcome screen state blob (7-state fallback chain)."""
        _cached_reload()
        from distillate.welcome_state import synthesize_welcome_state
        result = synthesize_welcome_state(_state)
        return JSONResponse(result)

    # --- Nicolas sessions ---------------------------------------------------

    @app.get("/nicolas/state")
    async def get_nicolas_state_endpoint():
        """Authoritative "working" | "idle" | null for the Nicolas chat turn.

        Renderer polls this alongside the session status endpoint so the
        tray + activity-bar bell mirror backend state (set from inside
        NicolasClient.send) instead of relying on a renderer-local flag.
        """
        from distillate.nicolas_state import get_nicolas_state
        return JSONResponse({"status": get_nicolas_state()})

    @app.post("/nicolas/ack")
    async def ack_nicolas_state():
        """Renderer acknowledges the pending-turn bell — clear backend state.

        Called when the user focuses the Nicolas sidebar view. Without
        this, backend would stay in "idle" until the next prompt, and
        a second window polling the state would re-raise the bell the
        first window just dismissed.
        """
        from distillate.nicolas_state import clear_nicolas_state
        clear_nicolas_state()
        return JSONResponse({"ok": True})

    @app.get("/nicolas/sessions")
    async def list_nicolas_sessions():
        nicolas = _get_nicolas()
        return JSONResponse({
            "sessions": nicolas.list_sessions(),
            "active_session_id": nicolas.session_id,
        })

    @app.post("/nicolas/sessions")
    async def create_nicolas_session():
        """Disconnect the active session and clear active_session_id.

        The new session_id is minted by Claude Code on the next /ws send
        (via the session_init event), which then appears in the registry.
        """
        nicolas = _get_nicolas()
        await nicolas.new_conversation()
        return JSONResponse({"ok": True, "active_session_id": None})

    @app.post("/nicolas/sessions/{session_id}/activate")
    async def activate_nicolas_session(session_id: str):
        nicolas = _get_nicolas()
        await nicolas.switch_session(session_id)
        return JSONResponse({"ok": True, "active_session_id": session_id})

    @app.patch("/nicolas/sessions/{session_id}")
    async def update_nicolas_session(session_id: str, request: Request):
        body = await request.json() if request.content_length else {}
        nicolas = _get_nicolas()
        updated = False

        # Update name if provided
        name = (body or {}).get("name", "").strip()
        if name:
            if not nicolas.rename_session(session_id, name):
                raise HTTPException(404, detail="session_not_found")
            updated = True

        # Update status if provided
        status = (body or {}).get("status", "").strip()
        if status:
            if not nicolas.set_session_status(session_id, status):
                raise HTTPException(404, detail="session_not_found")
            updated = True

        if not updated:
            raise HTTPException(400, detail="name_or_status_required")
        return JSONResponse({"ok": True})

    @app.delete("/nicolas/sessions/{session_id}")
    async def delete_nicolas_session(session_id: str):
        nicolas = _get_nicolas()
        if not await nicolas.delete_session(session_id):
            raise HTTPException(404, detail="session_not_found")
        return JSONResponse({"ok": True})

    @app.get("/nicolas/sessions/{session_id}/history")
    async def nicolas_session_history(session_id: str):
        """Replay past turns for a session from Claude Code's .jsonl store.

        Returns ``{"turns": [...]}`` where each turn has a ``role`` field:
          - ``user``:       {"role": "user", "text": str}
          - ``assistant``:  {"role": "assistant", "text": str}
          - ``tool``:       {"role": "tool", "name": str, "input": dict,
                             "tool_use_id": str, "is_error": bool}

        Tool turns preserve the original inline ordering: an assistant
        message like [text, tool_use, text] becomes three consecutive
        turns. This lets the renderer replay tool indicators inline with
        text just like they appeared live, so switching back to a session
        shows the full trajectory instead of a stripped-down text log.
        """
        from pathlib import Path
        projects_dir = Path.home() / ".claude" / "projects"
        history_path: Path | None = None
        if projects_dir.is_dir():
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                candidate = project_dir / f"{session_id}.jsonl"
                if candidate.exists():
                    history_path = candidate
                    break
        if history_path is None:
            return JSONResponse({"turns": []})

        turns: list[dict] = []
        # Track tool_use_ids that received an error so we can mark them
        # on replay (tool_result.is_error lives in the subsequent user msg).
        errored_tool_ids: set[str] = set()
        try:
            # First pass: scan for error tool_results so we can annotate
            # the matching tool_use turns from the assistant side.
            for line in history_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = entry.get("message") or {}
                if msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if (
                        isinstance(c, dict)
                        and c.get("type") == "tool_result"
                        and c.get("is_error")
                    ):
                        tid = c.get("tool_use_id")
                        if tid:
                            errored_tool_ids.add(tid)

            # Second pass: build the turn list preserving inline order.
            for line in history_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = entry.get("message") or {}
                role = msg.get("role")
                content = msg.get("content")

                if role == "user":
                    # Skip tool_result-only messages; they're already
                    # paired with their tool_use on the assistant side.
                    # Also filter out system-injected XML that Claude Code
                    # inserts as user-role messages (<task-notification>,
                    # <system-reminder>, etc.) — these are internal metadata
                    # and must not render as user chat turns.
                    def _is_system_xml(text: str) -> bool:
                        t = text.strip()
                        return t.startswith("<") and any(
                            t.startswith(f"<{tag}")
                            for tag in (
                                "task-notification", "system-reminder",
                                "command-name", "local-command",
                            )
                        )

                    if isinstance(content, str) and content.strip():
                        if not _is_system_xml(content):
                            turns.append({"role": "user", "text": content})
                    elif isinstance(content, list):
                        texts = [
                            c.get("text", "")
                            for c in content
                            if isinstance(c, dict)
                            and c.get("type") == "text"
                            and not _is_system_xml(c.get("text", ""))
                        ]
                        joined = "\n".join(t for t in texts if t)
                        if joined.strip():
                            turns.append({"role": "user", "text": joined})

                elif role == "assistant" and isinstance(content, list):
                    # Preserve inline order of text + tool_use blocks.
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        ctype = c.get("type")
                        if ctype == "text":
                            text = c.get("text", "")
                            if text.strip():
                                turns.append({"role": "assistant", "text": text})
                        elif ctype == "tool_use":
                            name = c.get("name", "") or ""
                            # Strip the MCP server prefix the renderer uses too.
                            if name.startswith("mcp__distillate__"):
                                name = name[len("mcp__distillate__"):]
                            tid = c.get("id", "")
                            turns.append({
                                "role": "tool",
                                "name": name,
                                "input": c.get("input") or {},
                                "tool_use_id": tid,
                                "is_error": tid in errored_tool_ids,
                            })
        except OSError:
            pass
        return JSONResponse({"turns": turns})

    @app.get("/connectors/{connector_id}")
    async def connector_detail(connector_id: str):
        """Return full config for a connector."""
        import os

        if connector_id == "zotero":
            return JSONResponse({"ok": True, "connector": {
                "id": "zotero",
                "label": "Papers",
                "service": "Zotero library",
                "connected": bool(config.ZOTERO_API_KEY and config.ZOTERO_USER_ID),
                "settings": [
                    {"key": "ZOTERO_API_KEY", "label": "API key", "value": config.ZOTERO_API_KEY[:8] + "..." if config.ZOTERO_API_KEY else "", "sensitive": True},
                    {"key": "ZOTERO_USER_ID", "label": "User ID", "value": config.ZOTERO_USER_ID},
                    {"key": "ZOTERO_COLLECTION_KEY", "label": "Collection", "value": config.ZOTERO_COLLECTION_KEY or "(whole library)"},
                    {"key": "READING_SOURCE", "label": "Reading surface", "value": config.READING_SOURCE},
                ],
            }})
        elif connector_id == "email":
            email = os.environ.get("DISTILLATE_EMAIL", "").strip()
            verified = os.environ.get("DISTILLATE_EMAIL_VERIFIED", "").strip().lower() in ("true", "1", "yes")
            exp_reports = os.environ.get("DISTILLATE_EMAIL_EXPERIMENT_REPORTS", "true").strip().lower() in ("true", "1", "yes")
            daily_papers = os.environ.get("DISTILLATE_EMAIL_DAILY_PAPERS", "true").strip().lower() in ("true", "1", "yes")
            weekly_digest = os.environ.get("DISTILLATE_EMAIL_WEEKLY_DIGEST", "true").strip().lower() in ("true", "1", "yes")
            return JSONResponse({"ok": True, "connector": {
                "id": "email",
                "label": "Updates",
                "service": "Email",
                "connected": bool(email),
                "verified": verified,
                "settings": [
                    {"key": "DISTILLATE_EMAIL", "label": "Email address", "value": email},
                    {"key": "experiment_reports", "label": "Experiment reports", "value": "on" if exp_reports else "off"},
                    {"key": "daily_papers", "label": "Daily paper suggestions", "value": "on" if daily_papers else "off"},
                    {"key": "weekly_digest", "label": "Weekly digest", "value": "on" if weekly_digest else "off"},
                ],
            }})
        elif connector_id == "obsidian":
            return JSONResponse({"ok": True, "connector": {
                "id": "obsidian",
                "label": "Notes",
                "service": "Obsidian vault",
                "connected": bool(config.OBSIDIAN_VAULT_PATH),
                "settings": [
                    {"key": "OBSIDIAN_VAULT_PATH", "label": "Vault path", "value": config.OBSIDIAN_VAULT_PATH or "(not set)"},
                    {"key": "OBSIDIAN_VAULT_NAME", "label": "Vault name", "value": config.OBSIDIAN_VAULT_NAME or "(auto)"},
                    {"key": "OBSIDIAN_PAPERS_FOLDER", "label": "Papers folder", "value": config.OBSIDIAN_PAPERS_FOLDER},
                ],
            }})
        elif connector_id == "remarkable":
            import shutil
            has_rmapi = shutil.which("rmapi") is not None
            return JSONResponse({"ok": True, "connector": {
                "id": "remarkable",
                "label": "Tablet",
                "service": "reMarkable",
                "connected": has_rmapi and bool(config.REMARKABLE_DEVICE_TOKEN),
                "settings": [
                    {"key": "rmapi", "label": "rmapi CLI", "value": "installed" if has_rmapi else "not found"},
                    {"key": "REMARKABLE_DEVICE_TOKEN", "label": "Device token", "value": "configured" if config.REMARKABLE_DEVICE_TOKEN else "(not set)", "sensitive": True},
                    {"key": "RM_FOLDER_PAPERS", "label": "Papers folder", "value": config.RM_FOLDER_PAPERS},
                ],
            }})
        elif connector_id == "huggingface":
            hf_token = os.environ.get("HF_TOKEN", "").strip()
            hf_connected = bool(hf_token)
            settings = [
                {"key": "HF_TOKEN", "label": "API token", "value": (hf_token[:8] + "...") if hf_token else "(not set)", "sensitive": True},
                {"key": "HF_INFERENCE_ROUTING", "label": "Inference routing", "value": config.HF_INFERENCE_ROUTING},
                {"key": "HF_DEFAULT_GPU_FLAVOR", "label": "Default GPU", "value": config.HF_DEFAULT_GPU_FLAVOR},
            ]
            # If connected, fetch account info
            account = {}
            if hf_connected:
                from distillate.huggingface import validate_token
                info = validate_token(hf_token)
                if info.get("ok"):
                    account = {
                        "username": info.get("username", ""),
                        "plan": info.get("plan", "free"),
                        "can_pay": info.get("can_pay", False),
                    }
                    settings.insert(1, {"key": "username", "label": "Account", "value": info.get("username", "")})
                    settings.insert(2, {"key": "plan", "label": "Plan", "value": info.get("plan", "free")})
            return JSONResponse({"ok": True, "connector": {
                "id": "huggingface",
                "label": "HuggingFace",
                "service": "Models, compute & inference",
                "connected": hf_connected,
                "account": account,
                "settings": settings,
                "features": {
                    "inference": hf_connected,
                    "compute": hf_connected and account.get("can_pay", False),
                    "hub_search": True,
                    "mcp_server": hf_connected,
                },
            }})
        else:
            return JSONResponse({"ok": False, "reason": "Unknown connector"}, status_code=404)

    # Experiment endpoints moved to distillate/routes/experiments.py

    @app.get("/terminal/{tmux_name}/capture")
    async def terminal_capture(tmux_name: str, lines: int = 5000):
        """Capture tmux pane scrollback for client-side scroll+select.

        Returns the last ``lines`` lines of tmux output including ANSI
        escape sequences so the client can populate xterm.js scrollback.
        """
        from distillate.launcher import capture_pane as _capture_pane
        text = _capture_pane(tmux_name, lines=lines)
        if not text:
            return JSONResponse({"ok": False, "error": "capture failed"}, status_code=404)
        return JSONResponse({"ok": True, "content": text})

    @app.websocket("/ws/terminal/{tmux_name}")
    async def ws_terminal(websocket: WebSocket, tmux_name: str):
        """WebSocket terminal proxy: xterm.js ↔ tmux session.

        The client sends raw keystrokes, the server pipes them to tmux and
        streams output back. Works in any browser — no Electron required.
        """
        import asyncio as _aio
        import os as _term_os
        import pty as _pty
        import select as _select
        import subprocess as _sp
        import struct as _struct
        import fcntl as _fcntl
        import termios as _termios

        # Verify tmux session exists
        try:
            _sp.run(["tmux", "has-session", "-t", tmux_name],
                     check=True, capture_output=True, timeout=5)
        except (_sp.CalledProcessError, FileNotFoundError):
            await websocket.close(code=4004, reason=f"tmux session '{tmux_name}' not found")
            return

        await websocket.accept()

        # Fork a PTY running `tmux attach -t <name>`
        master_fd, slave_fd = _pty.openpty()
        env = _term_os.environ.copy()
        env["TERM"] = "xterm-256color"

        proc = _sp.Popen(
            ["tmux", "attach-session", "-t", tmux_name],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            env=env, preexec_fn=_term_os.setsid,
        )
        _term_os.close(slave_fd)  # parent keeps only the master

        # Make master_fd non-blocking so reads don't hang
        import fcntl as _fcntl2
        flags = _fcntl2.fcntl(master_fd, _fcntl2.F_GETFL)
        _fcntl2.fcntl(master_fd, _fcntl2.F_SETFL, flags | _term_os.O_NONBLOCK)

        def _set_winsize(rows, cols):
            try:
                winsize = _struct.pack("HHHH", rows, cols, 0, 0)
                _fcntl.ioctl(master_fd, _termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

        _set_winsize(24, 80)

        _alive = True

        async def _read_pty():
            """Read from PTY master and send to WebSocket.

            Uses a dedicated reader thread to avoid per-read thread pool
            scheduling overhead from run_in_executor.
            """
            nonlocal _alive
            import threading as _threading
            loop = _aio.get_event_loop()
            queue = _aio.Queue()

            def _reader_thread():
                while _alive:
                    try:
                        ready = _select.select([master_fd], [], [], 0.005)[0]
                    except (ValueError, OSError):
                        break
                    if not ready:
                        continue
                    try:
                        data = _term_os.read(master_fd, 65536)
                        if not data:
                            break
                        loop.call_soon_threadsafe(queue.put_nowait, data)
                    except (BlockingIOError, OSError):
                        break
                loop.call_soon_threadsafe(queue.put_nowait, None)

            t = _threading.Thread(target=_reader_thread, daemon=True)
            t.start()

            try:
                while _alive:
                    data = await queue.get()
                    if data is None:
                        break
                    await websocket.send_bytes(data)
            except Exception:
                pass
            finally:
                _alive = False

        async def _write_pty():
            """Read from WebSocket and write to PTY master."""
            nonlocal _alive
            try:
                while _alive:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    if "bytes" in msg and msg["bytes"]:
                        _term_os.write(master_fd, msg["bytes"])
                    elif "text" in msg and msg["text"]:
                        text = msg["text"]
                        # Handle resize messages
                        if text.startswith("{"):
                            try:
                                cmd = json.loads(text)
                                if cmd.get("type") == "resize":
                                    _set_winsize(cmd.get("rows", 24), cmd.get("cols", 80))
                                    continue
                            except (json.JSONDecodeError, KeyError):
                                pass
                        _term_os.write(master_fd, text.encode("utf-8"))
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                _alive = False

        try:
            await _aio.gather(_read_pty(), _write_pty())
        finally:
            proc.terminate()
            try:
                _term_os.close(master_fd)
            except OSError:
                pass
            proc.wait()

    @app.websocket("/ws")
    async def ws_chat(websocket: WebSocket):
        """Single-receive WebSocket handler.

        All incoming messages flow through one ``receive_text()`` call in
        the main loop. Streaming work runs in a spawned task so cancels,
        model switches, and other control messages can be processed
        without contention. This is the fix for the concurrency bug where
        the outer loop's ``receive_text`` raced with a background cancel
        listener's ``receive_text`` ("cannot call recv while another
        coroutine is already waiting for the next message").
        """
        await websocket.accept()

        # Reuse the shared singleton so session state is consistent with the
        # /nicolas/sessions HTTP endpoints.
        nicolas = _get_nicolas()
        cancel_flag = asyncio.Event()
        # Serialize streaming tasks: Nicolas processes one user query at a
        # time. A second message sent while the first is streaming queues
        # behind the lock instead of racing.
        stream_lock = asyncio.Lock()

        async def _stream_turn(user_input: str) -> None:
            async with stream_lock:
                cancel_flag.clear()
                # Collect assistant text for post-turn auto-naming.
                _assistant_chunks: list[str] = []
                # Track whether a tool in this turn requested a thread branch
                # (e.g. launch_experiment → new thread named after the experiment).
                _pending_branch: dict | None = None
                try:
                    async for event in nicolas.send(user_input):
                        if cancel_flag.is_set():
                            await websocket.send_json({"type": "cancelled"})
                            return
                        await websocket.send_json(event)

                        # Capture streamed assistant text so we can feed
                        # it to the Haiku-powered thread-namer.
                        if event.get("type") == "text_delta":
                            _assistant_chunks.append(event.get("text", ""))

                        # Harvest the thread_branch hint from any tool_done
                        # whose result carries one (only launch_experiment
                        # does, today). The branch fires AFTER turn_end so
                        # we don't truncate Nicolas's final response.
                        if event.get("type") == "tool_done":
                            res = event.get("result") or {}
                            if isinstance(res, dict) and res.get("_thread_branch"):
                                _pending_branch = res["_thread_branch"]
                            # Push usage snapshot after every tool call so the
                            # thinking-indicator token counter reflects real
                            # API usage (including lab_repl delegate calls)
                            # without waiting for turn_end.
                            try:
                                from distillate.agent_runtime import usage_tracker as _ut
                                _sid = getattr(nicolas, "session_id", None)
                                _snap = _ut.get_tracker().snapshot(session_id=_sid)
                                await websocket.send_json({"type": "usage_update", **_snap})
                            except Exception:
                                pass

                        # Push a fresh billing snapshot right after every
                        # turn so the renderer's cost pill updates live.
                        if event.get("type") == "turn_end":
                            try:
                                from distillate.agent_runtime import usage_tracker as _ut
                                sid = getattr(nicolas, "session_id", None) or event.get("session_id")
                                snap = _ut.get_tracker().snapshot(session_id=sid)
                                await websocket.send_json({
                                    "type": "usage_update", **snap,
                                })
                            except Exception:
                                log.debug("usage_update push failed", exc_info=True)

                            # After the turn completes, fire the two
                            # post-turn workflows. Both run as tasks so we
                            # don't block further message handling.
                            end_sid = event.get("session_id") or getattr(nicolas, "session_id", None)
                            assistant_text = "".join(_assistant_chunks)
                            if _pending_branch:
                                asyncio.create_task(_handle_thread_branch(
                                    websocket, nicolas, _pending_branch,
                                ))
                            else:
                                asyncio.create_task(_maybe_auto_name(
                                    websocket, end_sid, user_input, assistant_text,
                                ))
                except Exception as exc:
                    log.exception("Nicolas query failed")
                    try:
                        await websocket.send_json({
                            "type": "error",
                            "message": str(exc),
                            "category": _classify_error(str(exc)),
                        })
                    except Exception:
                        # WebSocket likely closed; nothing we can do.
                        pass

        async def _maybe_auto_name(
            ws: WebSocket, session_id: str | None,
            user_msg: str, assistant_msg: str,
        ) -> None:
            """Call Haiku in a worker thread to generate a 3-5 word name
            for the thread if it doesn't already have a meaningful one.
            Emits `session_renamed` so the sidebar refreshes."""
            if not session_id:
                return
            try:
                from distillate.agent_sdk import (
                    _load_registry, _needs_auto_name,
                    _generate_thread_name, _apply_auto_name,
                )
                reg = _load_registry()
                entry = next(
                    (s for s in reg.get("sessions", [])
                     if s.get("session_id") == session_id),
                    None,
                )
                if not entry or not _needs_auto_name(entry):
                    return
                name = await asyncio.to_thread(
                    _generate_thread_name, user_msg, assistant_msg,
                )
                if not name:
                    # Fallback: first ~6 words of user message
                    words = (user_msg or "").strip().split()
                    name = " ".join(words[:6])
                    if not name:
                        return
                if _apply_auto_name(session_id, name):
                    try:
                        await ws.send_json({
                            "type": "session_renamed",
                            "session_id": session_id,
                            "name": name,
                        })
                    except Exception:
                        pass
            except Exception:
                log.debug("auto-name failed (non-critical)", exc_info=True)

        async def _handle_thread_branch(
            ws: WebSocket, nicolas_client, branch: dict,
        ) -> None:
            """After launch_experiment, start a fresh Nicolas thread
            named after the experiment. The current thread stays in
            the sidebar as the setup conversation."""
            try:
                name = (branch or {}).get("name") or "New Experiment"
                # Clear the active session; the next user send creates a
                # new Claude Code session via _ensure_connected. The
                # pending_name is applied in session_init so the new
                # entry lands with the experiment's name from the start.
                await nicolas_client.new_conversation(pending_name=name)
                try:
                    await ws.send_json({
                        "type": "thread_branched",
                        "suggested_name": name,
                    })
                except Exception:
                    pass
            except Exception:
                log.debug("thread branch failed (non-critical)", exc_info=True)

        active_streams: set[asyncio.Task] = set()

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    msg = {"text": raw}

                if msg.get("type") == "new_conversation":
                    await nicolas.new_conversation()
                    continue

                if msg.get("type") == "unblock_budget":
                    nicolas.unblock_budget()
                    await websocket.send_json({"type": "budget_unblocked"})
                    continue

                if msg.get("type") == "set_model":
                    new_model = msg.get("model")
                    if not new_model:
                        continue
                    from distillate import pricing as _pricing
                    from distillate import preferences as _prefs
                    if new_model not in _pricing.MODEL_PRICES:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Unknown model: {new_model}",
                            "category": "invalid_model",
                        })
                        continue
                    # Persist first so a crash in nicolas.set_model() still
                    # reflects the user's choice on the next restart.
                    _prefs.set("nicolas_model", new_model)
                    await nicolas.set_model(new_model)
                    log.info("Model changed to %s", new_model)
                    continue

                if msg.get("type") == "get_preferences":
                    from distillate import preferences as _prefs
                    from distillate import pricing as _pricing
                    await websocket.send_json({
                        "type": "preferences",
                        "nicolas_model": _prefs.get("nicolas_model", _pricing.DEFAULT_MODEL),
                        "supported_models": _pricing.supported_models(),
                        "budget_compact_suggest_usd": nicolas._compact_suggest_usd,
                        "budget_session_hard_usd": nicolas._session_hard_warn_usd,
                    })
                    continue

                if msg.get("type") == "set_budget_thresholds":
                    nicolas.set_budget_thresholds(
                        compact_suggest=msg.get("compact_suggest"),
                        session_hard=msg.get("session_hard"),
                        day_hard=msg.get("day_hard"),
                    )
                    await websocket.send_json({"type": "budget_thresholds_saved"})
                    continue

                if msg.get("type") == "get_usage":
                    from distillate.agent_runtime import usage_tracker as _ut
                    sid = getattr(nicolas, "session_id", None)
                    snap = _ut.get_tracker().snapshot(session_id=sid)
                    await websocket.send_json({"type": "usage", **snap})
                    continue

                if msg.get("type") == "cancel":
                    # NOTE: We do NOT call nicolas.interrupt() — that sends
                    # SIGINT to the Claude Code process which can disrupt
                    # running tools (e.g. tmux commands checking on
                    # experiments). The flag stops forwarding events; the
                    # underlying turn completes in the background.
                    cancel_flag.set()
                    continue

                user_input = msg.get("text", "").strip()
                if not user_input:
                    continue

                # Prepend context hints so Nicolas can resolve "this project" /
                # "here" without extra tool calls, and knows what the user is
                # currently looking at (active_project_id = auto from selection;
                # focus = explicitly-pinned items from the "+ add context" chip).
                ctx = msg.get("context") or {}
                hints: list[str] = []
                active_pid = ctx.get("active_project_id")
                if active_pid:
                    try:
                        proj = _state.get_workspace(active_pid)
                        if proj and proj.get("name"):
                            hints.append(
                                f"user is currently viewing project "
                                f"\"{proj['name']}\" (id={active_pid})"
                            )
                    except Exception:
                        log.debug("active project lookup failed", exc_info=True)
                for fi in (ctx.get("focus") or []):
                    ftype = fi.get("type", "")
                    fid = fi.get("id", "")
                    flabel = fi.get("label", "")
                    if not (ftype and flabel):
                        continue
                    if ftype == "paper":
                        hints.append(f"user has pinned paper \"{flabel}\" for context")
                    elif ftype == "experiment":
                        hints.append(f"user is focused on experiment \"{flabel}\" (id={fid})")
                    elif ftype == "project":
                        hints.append(f"user is focused on project \"{flabel}\" (id={fid})")
                if hints:
                    user_input = f"[Context: {'; '.join(hints)}]\n\n{user_input}"

                # Spawn streaming as a task so the main loop can keep
                # handling control messages (esp. cancel) while Nicolas
                # processes. The stream_lock ensures only one turn streams
                # at a time; extra text messages queue behind it.
                task = asyncio.create_task(_stream_turn(user_input))
                active_streams.add(task)
                task.add_done_callback(active_streams.discard)

        except WebSocketDisconnect:
            log.info("WebSocket client disconnected")
        except Exception:
            log.exception("WebSocket error")
        finally:
            # Cancel any in-flight streaming tasks on disconnect.
            for t in list(active_streams):
                t.cancel()
        # Do NOT disconnect the singleton on WS close — other requests
        # (session list, switches) still need it. Disconnect at app shutdown.

    # --- Include domain routers ---
    from distillate.routes.experiments import router as experiments_router
    from distillate.routes.papers import router as papers_router
    from distillate.routes.settings import router as settings_router
    from distillate.routes.workspaces import router as workspaces_router
    from distillate.routes.agents import router as agents_router
    from distillate.routes.notebook import router as notebook_router
    from distillate.routes.canvas import router as canvas_router
    from distillate.routes.hooks import router as hooks_router
    from distillate.routes.auth import router as auth_router
    from distillate.routes.hf_metrics import router as hf_metrics_router
    app.include_router(experiments_router)
    app.include_router(papers_router)
    app.include_router(settings_router)
    app.include_router(workspaces_router)
    app.include_router(agents_router)
    app.include_router(notebook_router)
    app.include_router(canvas_router)
    app.include_router(hooks_router)
    app.include_router(auth_router)
    app.include_router(hf_metrics_router)

    # Mount desktop UI static files at /ui (served from wheel or dev monorepo)
    ui_dir = _find_ui_dir()
    if ui_dir:
        from starlette.staticfiles import StaticFiles
        app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")
        log.info("Serving UI from %s", ui_dir)

    return app


def main():
    """Entry point: ``python -m distillate.server [port] [--no-open]``."""
    import uvicorn

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    port = int(args[0]) if args else _DEFAULT_PORT
    no_open = "--no-open" in flags

    # Make the port visible to the session launcher, which writes it into
    # Claude Code's .claude/settings.local.json hook URLs on launch.
    from distillate.claude_hooks import set_server_port
    set_server_port(port)

    if not no_open:
        import threading
        import webbrowser

        def _open():
            import time
            time.sleep(1.0)
            webbrowser.open(f"http://127.0.0.1:{port}/ui/")

        threading.Thread(target=_open, daemon=True).start()

    # Load all credentials from encrypted database into memory cache.
    # This happens once at startup (no OS keychain prompts).
    from distillate import secrets, db
    db.get_connection()  # Ensure DB is initialized with credentials table
    for key in secrets.SECRET_KEYS:
        secrets.get(key)  # Populate in-memory cache from encrypted DB
    log.info("Credentials loaded from encrypted storage")

    log.info("Starting Nicolas server on 127.0.0.1:%d", port)
    uvicorn.run(
        _create_app(),
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
