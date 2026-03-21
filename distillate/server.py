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

Experiments (Live tab):
    GET  /experiments/list                    All tracked projects with run summaries.
    GET  /experiments/{project_id}/notebook   Self-contained HTML lab notebook.
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
from datetime import datetime, timezone

from distillate.agent_sdk import NicolasClient, _classify_error
from distillate.server_helpers import (
    _summarize_tool_result,
    _parse_stream_json,
)
from distillate.state import State, acquire_lock, release_lock

log = logging.getLogger(__name__)

_DEFAULT_PORT = 8742
_executor = ThreadPoolExecutor(max_workers=2)


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

    app = FastAPI(title="Nicolas", docs_url=None, redoc_url=None)

    # Allow cross-origin requests from the Electron renderer
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Shared state
    _state = State()

    def _get_project_or_404(project_id: str):
        """Reload state and look up a project, raising 404 if not found."""
        _state.reload()
        proj = _state.find_project(project_id)
        if not proj:
            raise HTTPException(404, detail="not_found")
        return proj

    @app.get("/status")
    async def status():
        from importlib.metadata import version
        ver = version("distillate")
        _state.reload()
        processed = _state.documents_with_status("processed")
        from distillate import config
        q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
        queue = _state.documents_with_status(q_status)

        # Experiment stats
        experiment_stats = None
        projects = _state.projects
        if projects:
            total_runs = 0
            runs_kept = 0
            runs_discarded = 0
            active_sessions = 0
            session_details = []
            for proj in projects.values():
                for run in proj.get("runs", {}).values():
                    total_runs += 1
                    decision = run.get("decision", "")
                    if decision == "keep":
                        runs_kept += 1
                    elif decision == "discard":
                        runs_discarded += 1
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
                    "runs_kept": runs_kept,
                    "runs_discarded": runs_discarded,
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

    @app.post("/sync")
    async def sync_to_cloud():
        from distillate.cloud_sync import cloud_sync_available, sync_state
        if not cloud_sync_available():
            return JSONResponse(
                {"ok": False, "reason": "no_credentials"}, status_code=501,
            )
        _state.reload()
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(_executor, sync_state, _state)
        return JSONResponse({"ok": ok})

    @app.post("/library/setup")
    async def library_setup(body: dict):
        """Validate Zotero credentials, save config, and optionally set reading surface."""
        from distillate.config import save_to_env

        api_key = body.get("zotero_api_key", "").strip()
        user_id = body.get("zotero_user_id", "").strip()
        reading_source = body.get("reading_source", "").strip().lower()

        if not api_key or not user_id:
            return JSONResponse(
                {"ok": False, "reason": "Both zotero_api_key and zotero_user_id are required"},
                status_code=400,
            )

        # Validate credentials against Zotero API
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(
                f"https://api.zotero.org/users/{user_id}/items?limit=1",
                headers={
                    "Zotero-API-Version": "3",
                    "Zotero-API-Key": api_key,
                },
            )
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                _executor,
                lambda: urllib.request.urlopen(req, timeout=10),
            )
            if resp.status != 200:
                return JSONResponse(
                    {"ok": False, "reason": f"Zotero API returned {resp.status}"},
                    status_code=422,
                )
        except urllib.error.HTTPError as e:
            reason = "Invalid API key" if e.code == 403 else f"Zotero API error ({e.code})"
            return JSONResponse({"ok": False, "reason": reason}, status_code=422)
        except Exception as e:
            return JSONResponse(
                {"ok": False, "reason": f"Could not reach Zotero: {e}"},
                status_code=502,
            )

        # Save to .env
        save_to_env("ZOTERO_API_KEY", api_key)
        save_to_env("ZOTERO_USER_ID", user_id)

        # Update in-memory config
        config.ZOTERO_API_KEY = api_key
        config.ZOTERO_USER_ID = user_id

        if reading_source in ("remarkable", "zotero"):
            save_to_env("READING_SOURCE", reading_source)
            config.READING_SOURCE = reading_source
            if reading_source == "zotero":
                save_to_env("SYNC_HIGHLIGHTS", "false")
                config.SYNC_HIGHLIGHTS = False
            else:
                save_to_env("SYNC_HIGHLIGHTS", "true")
                config.SYNC_HIGHLIGHTS = True

        return JSONResponse({"ok": True, "message": "Library configured successfully"})

    @app.get("/experiments/templates")
    async def list_experiment_templates():
        """List available experiment templates."""
        from distillate.launcher import list_templates

        templates = list_templates()
        return JSONResponse({
            "ok": True,
            "templates": [
                {
                    "name": t["name"],
                    "has_data": t["has_data"],
                    "prompt_lines": t["prompt_lines"],
                }
                for t in templates
            ],
        })

    @app.post("/experiments/create")
    async def create_experiment(body: dict):
        """Create a new experiment: scan directory, draft PROMPT.md with Claude,
        install hooks, register, and launch a Claude Code session.

        Uses the CLI's init_experiment_tool for steps 1-4, then launches.

        Body: {"name": "experiment-name", "goal": "what to optimize",
               "target": "/path" (optional), "constraints": "..." (optional),
               "launch": true (optional)}

        Returns progress via streaming NDJSON so the desktop can update a
        flowchart in real time.
        """
        from pathlib import Path

        from starlette.responses import StreamingResponse

        from distillate import config
        from distillate.experiments import slugify

        name = body.get("name", "").strip()
        goal = body.get("goal", "").strip()
        if not name:
            return JSONResponse(
                {"ok": False, "reason": "name is required"}, status_code=400,
            )

        target = body.get("target", "")
        if not target:
            project_id = slugify(name)
            root = config.EXPERIMENTS_ROOT or str(Path.home() / "experiments")
            target = str(Path(root) / project_id)
        target_path = Path(target).expanduser().resolve()

        async def generate():
            from distillate.experiment_tools import init_experiment_tool
            from distillate.launcher import launch_experiment

            # Step 1: Create project directory
            yield json.dumps({"step": 1, "label": "Create project directory", "status": "active"}) + "\n"
            try:
                target_path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                yield json.dumps({"step": 1, "status": "error", "detail": str(e)}) + "\n"
                return
            yield json.dumps({"step": 1, "status": "done", "detail": str(target_path)}) + "\n"

            # Steps 2-4: init_experiment_tool handles scanning, PROMPT.md
            # generation (via Claude), hooks, reporting, and registration
            yield json.dumps({"step": 2, "label": "Draft PROMPT.md with Claude", "status": "active"}) + "\n"

            _state.reload()
            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    _executor,
                    lambda: init_experiment_tool(
                        state=_state,
                        path=str(target_path),
                        goal=goal,
                        name=name,
                        constraints=body.get("constraints", ""),
                        duration_minutes=body.get("duration_minutes", 5),
                        primary_metric=body.get("primary_metric", ""),
                        metric_direction=body.get("metric_direction", ""),
                        metric_constraint=body.get("metric_constraint", ""),
                    ),
                )
            except Exception as e:
                yield json.dumps({"step": 2, "status": "error", "detail": str(e)}) + "\n"
                return

            if not result.get("success"):
                error_msg = result.get("error", "Unknown error")
                # If PROMPT.md already exists, that's okay — use the existing one
                if "already exists" in error_msg:
                    yield json.dumps({"step": 2, "status": "done", "detail": "Using existing PROMPT.md"}) + "\n"
                    # Still need to ensure hooks and registration
                    from distillate.launcher import _install_hooks_into
                    yield json.dumps({"step": 3, "label": "Install hooks & reporting", "status": "active"}) + "\n"
                    _install_hooks_into(target_path)
                    yield json.dumps({"step": 3, "status": "done"}) + "\n"

                    yield json.dumps({"step": 4, "label": "Register experiment", "status": "active"}) + "\n"
                    from distillate.experiments import slugify
                    from distillate.experiment_tools import _parse_goals_from_text
                    project_id = slugify(name)
                    if not _state.has_project(project_id):
                        display_name = name.replace("-", " ").title() if name == project_id else name
                        _state.add_project(
                            project_id=project_id,
                            name=display_name,
                            path=str(target_path),
                            description=goal,
                        )
                        _state.save()
                    # Auto-parse goals from free-form goal text
                    parsed_goals = _parse_goals_from_text(goal)
                    if parsed_goals:
                        _state.update_project(project_id, goals=parsed_goals)
                    primary_metric = body.get("primary_metric", "")
                    if primary_metric:
                        _state.update_project(project_id, key_metric_name=primary_metric)
                    _state.save()
                    yield json.dumps({"step": 4, "status": "done", "project_id": project_id}) + "\n"
                else:
                    yield json.dumps({"step": 2, "status": "error", "detail": error_msg}) + "\n"
                    return
            else:
                project_id = result["project_id"]
                yield json.dumps({"step": 2, "status": "done"}) + "\n"

                # Step 3: Hooks & reporting (already done by init_experiment_tool)
                yield json.dumps({"step": 3, "label": "Install hooks & reporting", "status": "done"}) + "\n"

                # Step 4: Register (already done by init_experiment_tool)
                yield json.dumps({"step": 4, "label": "Register experiment", "status": "done", "project_id": project_id}) + "\n"

            # Step 5: Launch Claude Code session
            if body.get("launch", True):
                yield json.dumps({"step": 5, "label": "Launch Claude Code session", "status": "active"}) + "\n"
                try:
                    _state.reload()
                    proj = _state.find_project(project_id)
                    launch_result = await loop.run_in_executor(
                        _executor,
                        lambda: launch_experiment(
                            target_path, model="claude-sonnet-4-6",
                            max_turns=100, project=proj,
                        ),
                    )
                    acquire_lock()
                    try:
                        sessions = proj.setdefault("sessions", {})
                        sessions[launch_result["session_id"]] = launch_result
                        _state.save()
                    finally:
                        release_lock()
                    yield json.dumps({
                        "step": 5, "status": "done",
                        "tmux_session": launch_result.get("tmux_session", ""),
                    }) + "\n"
                except Exception as e:
                    yield json.dumps({"step": 5, "status": "error", "detail": str(e)}) + "\n"
                    return

            yield json.dumps({"done": True, "project_id": project_id, "path": str(target_path)}) + "\n"

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.post("/experiments/scaffold")
    async def scaffold_from_template(body: dict):
        """Scaffold an experiment from a built-in template (no Claude API call)."""
        from pathlib import Path

        from distillate import config
        from distillate.experiments import slugify
        from distillate.launcher import scaffold_experiment

        template = body.get("template", "").strip()
        name = body.get("name", "").strip() or template.replace("-", " ").title()
        if not template:
            return JSONResponse({"ok": False, "reason": "template required"}, status_code=400)

        project_id = slugify(name)
        root = config.EXPERIMENTS_ROOT or str(Path.home() / "experiments")
        target = Path(root) / project_id

        # If already scaffolded and registered, just return the existing project
        existing = _state.projects.get(project_id)
        if existing:
            return JSONResponse({"ok": True, "project_id": project_id, "path": existing.get("path", str(target)), "already_exists": True})

        try:
            loop = asyncio.get_event_loop()
            result_path = await loop.run_in_executor(
                _executor, lambda: scaffold_experiment(template, target, name=name)
            )
        except FileNotFoundError as e:
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=404)
        except FileExistsError as e:
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=409)
        except Exception as e:
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

        # Register in state with appropriate metadata
        _state.add_project(project_id, name, str(result_path))
        _state.update_project(project_id,
            key_metric_name="param_count",
            duration_minutes=5,
            goals=[
                {"metric": "test_accuracy", "threshold": 0.99, "direction": "maximize"},
                {"metric": "param_count", "threshold": 100000, "direction": "minimize"},
            ],
        )
        _state.save()

        return JSONResponse({"ok": True, "project_id": project_id, "path": str(result_path)})

    @app.post("/experiments/{project_id}/github")
    async def create_github_repo_endpoint(project_id: str, body: dict = None):
        """Create a GitHub repo for the experiment and push initial commit."""
        from pathlib import Path

        from distillate.launcher import create_github_repo

        proj = _get_project_or_404(project_id)

        proj_path = Path(proj.get("path", ""))
        body = body or {}
        repo_name = body.get("name", f"distillate-xp-{project_id}")
        private = body.get("private", False)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor,
            lambda: create_github_repo(proj_path, repo_name, private=private),
        )

        if result.get("ok"):
            _state.update_project(project_id, github_url=result.get("url", ""))
            _state.save()

        return JSONResponse(result)

    @app.get("/experiments/{project_id}/prompt")
    async def get_experiment_prompt(project_id: str):
        """Get the PROMPT.md content for a project."""
        from pathlib import Path

        proj = _get_project_or_404(project_id)

        prompt_path = Path(proj.get("path", "")) / "PROMPT.md"
        if not prompt_path.exists():
            return JSONResponse({"ok": False, "reason": "no_prompt"})

        content = prompt_path.read_text(encoding="utf-8")
        return JSONResponse({"ok": True, "content": content, "path": str(prompt_path)})

    @app.put("/experiments/{project_id}/prompt")
    async def update_experiment_prompt(project_id: str, body: dict):
        """Update the PROMPT.md content for a project."""
        from pathlib import Path

        proj = _get_project_or_404(project_id)

        content = body.get("content", "")
        project_path = Path(proj.get("path", ""))
        prompt_path = project_path / "PROMPT.md"
        prompt_path.write_text(content, encoding="utf-8")

        # Signal running agent that PROMPT.md changed
        distillate_dir = project_path / ".distillate"
        distillate_dir.mkdir(exist_ok=True)
        flag = distillate_dir / "prompt_updated"
        flag.write_text(
            datetime.now(timezone.utc).isoformat() + "\n",
            encoding="utf-8",
        )

        # Auto-detect primary metric from PROMPT.md content
        detected_metric = _detect_primary_metric(content)
        if detected_metric:
            _state.update_project(project_id, key_metric_name=detected_metric)
            _state.save()

        return JSONResponse({"ok": True, "detected_metric": detected_metric})

    @app.get("/experiments/{project_id}/results")
    async def get_experiment_results(project_id: str):
        """Get the RESULTS.md content for a project."""
        from pathlib import Path

        proj = _get_project_or_404(project_id)

        results_path = Path(proj.get("path", "")) / "RESULTS.md"
        if not results_path.exists():
            return JSONResponse({"ok": False, "reason": "no_results"})

        content = results_path.read_text(encoding="utf-8")
        return JSONResponse({"ok": True, "content": content, "path": str(results_path)})

    @app.get("/experiments/{project_id}/claude-md")
    async def get_experiment_claude_md(project_id: str):
        """Get the CLAUDE.md (agent protocol) for a project."""
        from pathlib import Path

        proj = _get_project_or_404(project_id)

        claude_path = Path(proj.get("path", "")) / "CLAUDE.md"
        if not claude_path.exists():
            return JSONResponse({"ok": False, "reason": "no_claude_md"})

        content = claude_path.read_text(encoding="utf-8")
        return JSONResponse({"ok": True, "content": content, "path": str(claude_path)})

    @app.put("/experiments/{project_id}/claude-md")
    async def update_experiment_claude_md(project_id: str, body: dict):
        """Update the CLAUDE.md content for a project."""
        from pathlib import Path

        proj = _get_project_or_404(project_id)

        content = body.get("content", "")
        claude_path = Path(proj.get("path", "")) / "CLAUDE.md"
        claude_path.write_text(content, encoding="utf-8")
        return JSONResponse({"ok": True})

    @app.get("/experiments/{project_id}/session")
    async def get_session_output(project_id: str):
        """Get parsed session output for the project.

        Reads the session log file (.distillate/<session_id>.jsonl) which
        contains stream-json output piped via tee.  Falls back to
        capture_pane for sessions started before the log-file change.
        """
        from pathlib import Path
        from distillate.launcher import _ensure_path, capture_pane

        _ensure_path()
        proj = _get_project_or_404(project_id)

        sessions = proj.get("sessions", {})
        # Find the most recent running session
        running = [s for s in sessions.values() if s.get("status") == "running"]
        if not running:
            return JSONResponse({"ok": False, "reason": "no_running_session", "output": ""}, status_code=404)

        sess = running[-1]
        tmux_name = sess.get("tmux_session", "")
        session_log = sess.get("session_log", "")

        try:
            raw = ""
            # Prefer log file (reliable, survives scrollback limits)
            if session_log:
                log_path = Path(session_log)
                if log_path.exists():
                    # Read last ~50KB of the log file
                    size = log_path.stat().st_size
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        if size > 50_000:
                            f.seek(size - 50_000)
                            f.readline()  # skip partial line
                        raw = f.read()

            # Fallback to capture_pane
            if not raw.strip() and tmux_name:
                loop = asyncio.get_event_loop()
                raw = await loop.run_in_executor(
                    _executor,
                    lambda: capture_pane(tmux_name, lines=500),
                )

            output = _parse_stream_json(raw)
            return JSONResponse({
                "ok": True,
                "session": tmux_name,
                "output": output,
            })
        except Exception as e:
            return JSONResponse({"ok": False, "reason": str(e), "output": ""}, status_code=500)

    @app.post("/experiments/attach")
    async def attach_experiment(body: dict):
        """Open a new Terminal window attached to the experiment's tmux session.

        Called by desktop app's 'Attach' button in Lab tab.
        Body: {"project": "tiny-gene-code"}
        """
        from distillate.launcher import attach_session

        project_query = body.get("project", "")
        if not project_query:
            return JSONResponse({"ok": False, "reason": "missing_project"}, status_code=400)

        proj = _get_project_or_404(project_query)

        sessions = proj.get("sessions", {})
        running = [s for s in sessions.values() if s.get("status") == "running"]
        if not running:
            return JSONResponse({"ok": False, "reason": "no_running_session"}, status_code=404)

        sess = running[-1]
        tmux_name = sess.get("tmux_session", "")
        host = sess.get("host")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, attach_session, tmux_name, host)
            return JSONResponse({"ok": True, "session": tmux_name})
        except RuntimeError as e:
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

    @app.post("/experiments/{project_id}/launch")
    async def launch_experiment_endpoint(project_id: str, body: dict = None):
        """Launch a new experiment session for the project."""
        from pathlib import Path

        from distillate.launcher import launch_experiment

        body = body or {}
        proj = _get_project_or_404(project_id)

        proj_path = Path(proj.get("path", ""))
        if not proj_path.exists():
            return JSONResponse({"ok": False, "reason": "path_not_found"}, status_code=404)

        model = body.get("model", "claude-sonnet-4-6")
        prompt_override = body.get("prompt_override")

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _executor,
                lambda: launch_experiment(
                    proj_path, model=model, project=proj,
                    prompt_override=prompt_override,
                ),
            )
            # Persist session to state
            acquire_lock()
            try:
                sessions = proj.setdefault("sessions", {})
                sessions[result["session_id"]] = result
                _state.save()
            finally:
                release_lock()

            return JSONResponse({
                "ok": True,
                "session_id": result.get("session_id", ""),
                "tmux_session": result.get("tmux_session", ""),
            })
        except Exception as e:
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

    @app.post("/experiments/{project_id}/stop")
    async def stop_experiment_endpoint(project_id: str):
        """Stop all running sessions for the project."""
        from distillate.launcher import _ensure_path, stop_session

        _ensure_path()
        proj = _get_project_or_404(project_id)

        sessions = proj.get("sessions", {})
        running = [s for s in sessions.values() if s.get("status") == "running"]
        if not running:
            return JSONResponse({"ok": False, "reason": "no_running_session"}, status_code=404)

        stopped = []
        for sess in running:
            tmux_name = sess.get("tmux_session", "")
            host = sess.get("host")
            if tmux_name:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        _executor, stop_session, tmux_name, host,
                    )
                    sess["status"] = "completed"
                    stopped.append(tmux_name)
                except Exception:
                    pass
        if stopped:
            _state.save()
        return JSONResponse({"ok": True, "stopped": stopped})

    # ---------------------------------------------------------------
    # Rescan helper (shared by /scan endpoint and SSE auto-rescan)
    # ---------------------------------------------------------------

    def _rescan_project(proj_id: str, proj: dict) -> dict | None:
        """Rescan a project, update state, return summary or None."""
        from pathlib import Path

        from distillate.experiments import (
            backfill_runs_from_events,
            scan_project,
            slugify,
        )

        proj_path = Path(proj.get("path", ""))
        if not proj_path.is_dir():
            return None

        # Backfill runs.jsonl from events.jsonl before scanning
        backfilled = backfill_runs_from_events(proj_path)
        if backfilled:
            log.info("Backfilled %d run(s) for %s", backfilled, proj_id)

        result = scan_project(proj_path)
        if "error" in result:
            return None

        acquire_lock()
        try:
            _state.reload()
            existing = _state.get_project(proj_id)
            if not existing:
                return None
            old_runs = existing.get("runs", {})
            old_count = len(old_runs)
            scan_names = {r["name"] for r in result.get("runs", {}).values()}
            existing_names = {r["name"] for r in old_runs.values()}

            # Remove stale runs no longer in scan results (e.g. artifact-
            # scanned duplicates superseded by structured runs)
            stale_keys = [
                eid for eid, erun in old_runs.items()
                if erun["name"] not in scan_names
            ]
            for k in stale_keys:
                del old_runs[k]

            new_runs = 0
            for run_id, run_data in result.get("runs", {}).items():
                if run_data["name"] not in existing_names:
                    _state.add_run(proj_id, run_id, run_data)
                    new_runs += 1
                else:
                    # Merge scan data into existing run (preserves state,
                    # picks up new fields like backfilled descriptions)
                    for eid, erun in old_runs.items():
                        if erun["name"] == run_data["name"]:
                            for k, v in run_data.items():
                                if k == "id":
                                    continue
                                if k in ("decision", "status", "results",
                                         "agent_reasoning", "description",
                                         "hypothesis", "reasoning"):
                                    # Always take latest value for mutable fields
                                    if v:
                                        erun[k] = v
                                elif v and not erun.get(k):
                                    erun[k] = v
                            break
            _state.update_project(
                proj_id,
                last_scanned_at=datetime.now(timezone.utc).isoformat(),
                last_commit_hash=result.get("head_hash", ""),
            )
            _state.save()
        finally:
            release_lock()

        # Find best metric across all kept runs
        best_metric = None
        updated_proj = _state.get_project(proj_id)
        if updated_proj:
            for run in updated_proj.get("runs", {}).values():
                if run.get("decision") != "keep" and run.get("status") != "keep":
                    continue
                for k, v in run.get("results", {}).items():
                    if isinstance(v, (int, float)):
                        if best_metric is None or v > next(iter(best_metric.values())):
                            best_metric = {k: v}

        return {
            "new_runs": new_runs,
            "total_runs": old_count + new_runs,
            "best_metric": best_metric,
            "backfilled": backfilled,
        }

    @app.post("/experiments/{project_id}/scan")
    async def scan_experiment(project_id: str, full: str = ""):
        """Manually trigger a rescan for a project."""
        proj = _get_project_or_404(project_id)

        # Full rescan: clear watch state + scan state to force re-read
        if full:
            from pathlib import Path
            proj_path = Path(proj.get("path", ""))
            for state_file in ("watch_state.json", "scan_state.json"):
                sf = proj_path / ".distillate" / state_file
                if sf.exists():
                    sf.unlink()

        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            _executor, _rescan_project, project_id, proj,
        )
        if summary is None:
            return JSONResponse({"ok": False, "reason": "scan_failed"}, status_code=500)
        return JSONResponse({"ok": True, **summary})

    # ---------------------------------------------------------------
    # Auto-continuation helper
    # ---------------------------------------------------------------

    async def _maybe_auto_continue(
        proj_id: str, proj: dict, loop,
    ) -> dict | None:
        """If goals unmet (or queue remains), launch a continuation session.

        Returns a ``session_continued`` SSE event dict, or None.
        """
        from pathlib import Path

        from distillate.launcher import launch_continuation, should_continue
        from distillate.state import acquire_lock, release_lock

        # Check queue first (decrement count if present)
        queue = proj.get("continuation_queue", {})
        queue_remaining = queue.get("count", 0)
        if queue_remaining <= 0 and not should_continue(proj):
            return None

        proj_path = Path(proj.get("path", ""))
        if not proj_path.is_dir():
            return None

        model = queue.get("model") or "claude-sonnet-4-5-20250929"
        max_turns = queue.get("max_turns", 100)

        try:
            session_data = await loop.run_in_executor(
                _executor,
                lambda: launch_continuation(
                    proj_path, proj, model=model, max_turns=max_turns,
                ),
            )
        except Exception:
            log.exception("Auto-continue failed for %s", proj_id)
            return None

        # Save session + decrement queue
        acquire_lock()
        try:
            _state.reload()
            _state.add_session(proj_id, session_data["session_id"], session_data)
            if queue_remaining > 0:
                _state.update_project(
                    proj_id,
                    continuation_queue={
                        **queue,
                        "count": queue_remaining - 1,
                    },
                )
            _state.save()
        finally:
            release_lock()

        return {
            "type": "session_continued",
            "project_id": proj_id,
            "tmux_session": session_data["tmux_session"],
            "model": model,
            "queue_remaining": max(0, queue_remaining - 1),
        }

    @app.post("/experiments/{project_id}/queue")
    async def queue_continuation(project_id: str, request: Request):
        """Queue N continuation sessions for a project.

        Body: ``{"count": int, "model": str (optional), "max_turns": int (optional)}``
        """
        body = await request.json()
        count = body.get("count", 1)
        model = body.get("model", "claude-sonnet-4-5-20250929")
        max_turns = body.get("max_turns", 100)

        proj = _get_project_or_404(project_id)

        _state.update_project(project_id, continuation_queue={
            "count": count,
            "model": model,
            "max_turns": max_turns,
        }, auto_continue=True)
        _state.save()

        return JSONResponse({
            "ok": True,
            "queued": count,
            "model": model,
        })

    @app.post("/experiments/{project_id}/sweep")
    async def sweep_experiment(project_id: str, request: Request):
        """Launch a parallel hyperparameter sweep.

        Body: ``{"configs": [{"lr": 0.001}, ...], "model": str, "max_turns": int}``
        """
        from pathlib import Path

        from distillate.launcher import launch_sweep
        from distillate.state import acquire_lock, release_lock

        body = await request.json()
        configs = body.get("configs", [])
        model = body.get("model", "claude-sonnet-4-5-20250929")
        max_turns = body.get("max_turns", 100)

        if not configs or len(configs) < 2:
            return JSONResponse(
                {"ok": False, "reason": "Provide at least 2 config variants."},
                status_code=400,
            )

        proj = _get_project_or_404(project_id)

        proj_path = Path(proj.get("path", ""))
        if not proj_path.is_dir():
            return JSONResponse(
                {"ok": False, "reason": "project_path_missing"},
                status_code=400,
            )

        loop = asyncio.get_event_loop()
        try:
            sessions = await loop.run_in_executor(
                _executor,
                lambda: launch_sweep(
                    proj_path, proj, configs,
                    model=model, max_turns=max_turns,
                ),
            )
        except Exception as e:
            return JSONResponse(
                {"ok": False, "reason": str(e)}, status_code=500,
            )

        acquire_lock()
        try:
            _state.reload()
            for sd in sessions:
                _state.add_session(project_id, sd["session_id"], sd)
            _state.save()
        finally:
            release_lock()

        return JSONResponse({
            "ok": True,
            "variants": len(sessions),
            "sessions": [s["tmux_session"] for s in sessions],
        })

    # ---------------------------------------------------------------
    # Campaign orchestration (M3)
    # ---------------------------------------------------------------

    _campaign_tasks: dict[str, asyncio.Task] = {}

    async def _campaign_loop(project_id: str):
        """Background campaign loop — delegates to shared run_campaign()."""
        import threading

        from distillate.launcher import run_campaign

        _state.reload()
        proj = _state.get_project(project_id)
        if not proj:
            _campaign_tasks.pop(project_id, None)
            return

        campaign = proj.get("campaign", {})
        budget = campaign.get("budget", {})
        max_sessions = budget.get("max_sessions", 10)
        model = campaign.get("model", "claude-sonnet-4-5-20250929")
        max_turns = campaign.get("max_turns", 100)

        # The stop flag is checked by run_campaign; we set it on cancel
        stop_flag = threading.Event()

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                _executor,
                lambda: run_campaign(
                    project_id,
                    _state,
                    max_sessions=max_sessions,
                    model=model,
                    max_turns=max_turns,
                    stop_flag=stop_flag,
                ),
            )
        except Exception:
            log.exception("Campaign loop failed for %s", project_id)
        finally:
            _campaign_tasks.pop(project_id, None)

    @app.post("/experiments/{project_id}/campaign/start")
    async def start_campaign(project_id: str, request: Request):
        """Start an autonomous campaign loop for a project."""
        body = await request.json()

        proj = _get_project_or_404(project_id)

        # Validate goals exist
        if not proj.get("goals"):
            return JSONResponse(
                {"ok": False, "reason": "Set goals first with update_goals."},
                status_code=400,
            )

        # Don't start if already running
        existing = proj.get("campaign", {})
        if existing.get("status") == "running":
            return JSONResponse(
                {"ok": False, "reason": "Campaign already running."},
                status_code=409,
            )

        objective = body.get("objective", "")
        max_sessions = body.get("max_sessions", 10)
        max_hours = body.get("max_hours", 8)
        model = body.get("model", "claude-sonnet-4-5-20250929")
        max_turns = body.get("max_turns", 100)

        campaign = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "objective": objective,
            "budget": {"max_sessions": max_sessions, "max_hours": max_hours},
            "model": model,
            "max_turns": max_turns,
            "sessions_launched": 0,
            "current_session_id": None,
            "completed_at": None,
            "stop_reason": None,
        }

        _state.update_project(
            project_id, campaign=campaign, auto_continue=True,
        )
        _state.save()

        # Start background campaign loop
        task = asyncio.create_task(_campaign_loop(project_id))
        _campaign_tasks[project_id] = task

        return JSONResponse({"ok": True, "campaign": campaign})

    @app.post("/experiments/{project_id}/campaign/pause")
    async def pause_campaign(project_id: str):
        """Pause a running campaign (finishes current session, stops launching)."""
        proj = _get_project_or_404(project_id)
        campaign = proj.get("campaign", {})
        if campaign.get("status") != "running":
            return JSONResponse(
                {"ok": False, "reason": "Campaign not running."},
                status_code=400,
            )
        campaign["status"] = "paused"
        campaign["stop_reason"] = "user_paused"
        _state.update_project(project_id, campaign=campaign)
        _state.save()
        return JSONResponse({"ok": True})

    @app.post("/experiments/{project_id}/campaign/resume")
    async def resume_campaign(project_id: str):
        """Resume a paused campaign."""
        proj = _get_project_or_404(project_id)
        campaign = proj.get("campaign", {})
        if campaign.get("status") != "paused":
            return JSONResponse(
                {"ok": False, "reason": "Campaign not paused."},
                status_code=400,
            )
        campaign["status"] = "running"
        campaign["stop_reason"] = None
        _state.update_project(project_id, campaign=campaign)
        _state.save()
        # Restart the background loop
        task = asyncio.create_task(_campaign_loop(project_id))
        _campaign_tasks[project_id] = task
        return JSONResponse({"ok": True})

    @app.post("/experiments/{project_id}/campaign/stop")
    async def stop_campaign(project_id: str):
        """Stop a campaign permanently."""
        proj = _get_project_or_404(project_id)
        campaign = proj.get("campaign", {})
        campaign["status"] = "stopped"
        campaign["stop_reason"] = "user_stopped"
        campaign["completed_at"] = datetime.now(timezone.utc).isoformat()
        _state.update_project(project_id, campaign=campaign)
        _state.save()
        # Cancel the background task
        task = _campaign_tasks.pop(project_id, None)
        if task:
            task.cancel()
        return JSONResponse({"ok": True})

    @app.patch("/experiments/{project_id:path}")
    async def patch_experiment(project_id: str, request: Request):
        """Update experiment fields (key_metric_name, description, etc.)."""
        proj = _get_project_or_404(project_id)
        actual_id = proj.get("id", project_id)
        body = await request.json()
        updates = {}
        if "name" in body:
            updates["name"] = body["name"]
        if "key_metric_name" in body:
            updates["key_metric_name"] = body["key_metric_name"]
        if "description" in body:
            updates["description"] = body["description"]
        if "goals" in body:
            updates["goals"] = body["goals"]
        if updates:
            _state.update_project(actual_id, **updates)
            _state.save()
        return JSONResponse({"ok": True, "updated": list(updates.keys())})

    @app.delete("/experiments/{project_id:path}")
    async def delete_experiment(project_id: str):
        """Delete experiment from tracking. Does NOT delete files or remote repo."""
        from distillate.launcher import _tmux_session_exists

        proj = _get_project_or_404(project_id)

        # Use the actual state key, not the URL param
        actual_id = proj.get("id", project_id)

        # Refuse if sessions are running
        for sess in proj.get("sessions", {}).values():
            if sess.get("status") == "running":
                tmux_name = sess.get("tmux_session", "")
                if tmux_name and _tmux_session_exists(tmux_name):
                    return JSONResponse(
                        {"ok": False, "reason": f"Session '{tmux_name}' is still running. Stop it first."},
                        status_code=409,
                    )

        name = proj.get("name", actual_id)
        run_count = len(proj.get("runs", {}))
        _state.remove_project(actual_id)
        _state.save()
        return JSONResponse({"ok": True, "message": f"Deleted '{name}' ({run_count} runs). Files and remote repo untouched."})

    @app.post("/experiments/{project_id}/steer")
    async def steer_experiment(project_id: str, request: Request):
        """Write steering instructions for the next session."""
        from pathlib import Path

        from distillate.launcher import write_steering

        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return JSONResponse(
                {"ok": False, "reason": "No steering text provided."},
                status_code=400,
            )

        proj = _get_project_or_404(project_id)

        proj_path = Path(proj.get("path", ""))
        write_steering(proj_path, text)

        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # M4 stubs: Multi-Agent Research Lab
    # ------------------------------------------------------------------

    @app.get("/experiments/compare")
    async def compare_experiments(ids: str = ""):
        """Compare metrics across multiple experiments.

        Query param: ?ids=proj1,proj2,proj3
        Returns a grid of metrics for side-by-side comparison.
        """
        if not ids:
            return JSONResponse(
                {"ok": False, "reason": "No project IDs provided."},
                status_code=400,
            )

        project_ids = [i.strip() for i in ids.split(",") if i.strip()]
        _state.reload()

        comparison = []
        all_metrics: set[str] = set()

        for pid in project_ids:
            proj = _state.find_project(pid)
            if not proj:
                continue

            # Find best results across kept runs
            best: dict[str, float] = {}
            for run in proj.get("runs", {}).values():
                if run.get("decision") != "keep" and run.get("status") != "keep":
                    continue
                for k, v in run.get("results", {}).items():
                    if isinstance(v, (int, float)):
                        if k not in best or v > best[k]:
                            best[k] = v
                        all_metrics.add(k)

            comparison.append({
                "id": proj.get("id", pid),
                "name": proj.get("name", pid),
                "run_count": len(proj.get("runs", {})),
                "best_metrics": best,
                "goals": proj.get("goals", []),
                "campaign": proj.get("campaign"),
            })

        return JSONResponse({
            "ok": True,
            "projects": comparison,
            "metrics": sorted(all_metrics),
        })

    @app.post("/experiments/{project_id}/save-template")
    async def save_template(project_id: str, request: Request):
        """Save a successful experiment as a reusable template."""
        from pathlib import Path

        from distillate.launcher import import_template

        body = await request.json()
        proj = _get_project_or_404(project_id)

        proj_path = Path(proj.get("path", ""))
        if not proj_path.is_dir():
            return JSONResponse(
                {"ok": False, "reason": "path_not_found"}, status_code=404,
            )

        template_name = body.get("name", proj.get("name", project_id))

        loop = asyncio.get_event_loop()
        try:
            result_name = await loop.run_in_executor(
                _executor,
                lambda: import_template(proj_path, template_name),
            )
        except Exception as e:
            return JSONResponse(
                {"ok": False, "reason": str(e)}, status_code=500,
            )

        return JSONResponse({
            "ok": True,
            "template_name": result_name,
            "message": f"Saved as template '{result_name}'.",
        })

    @app.post("/experiments/campaign/parallel")
    async def start_parallel_campaigns(request: Request):
        """Launch campaigns across multiple projects in parallel.

        Body: {"project_ids": ["proj1", "proj2"], "max_sessions": 5, "model": "..."}
        """
        body = await request.json()
        project_ids = body.get("project_ids", [])
        max_sessions = body.get("max_sessions", 5)
        model = body.get("model", "claude-sonnet-4-5-20250929")
        max_turns = body.get("max_turns", 100)

        if len(project_ids) < 2:
            return JSONResponse(
                {"ok": False, "reason": "Provide at least 2 project IDs."},
                status_code=400,
            )

        _state.reload()
        launched = []
        errors = []

        for pid in project_ids:
            proj = _state.find_project(pid)
            if not proj:
                errors.append({"id": pid, "reason": "not_found"})
                continue
            if not proj.get("goals"):
                errors.append({"id": pid, "reason": "no_goals"})
                continue
            if proj.get("campaign", {}).get("status") == "running":
                errors.append({"id": pid, "reason": "already_running"})
                continue

            campaign = {
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "objective": "",
                "budget": {"max_sessions": max_sessions},
                "model": model,
                "max_turns": max_turns,
                "sessions_launched": 0,
                "current_session_id": None,
                "completed_at": None,
                "stop_reason": None,
            }
            _state.update_project(pid, campaign=campaign, auto_continue=True)
            _state.save()

            task = asyncio.create_task(_campaign_loop(pid))
            _campaign_tasks[pid] = task
            launched.append(pid)

        return JSONResponse({
            "ok": True,
            "launched": launched,
            "errors": errors,
        })

    @app.get("/experiments/stream")
    async def experiments_stream():
        """SSE endpoint that tails experiment events and runs.jsonl."""
        from pathlib import Path

        from starlette.responses import StreamingResponse

        async def _event_generator():
            """Yield SSE events from .distillate/events.jsonl, runs.jsonl, and live_metrics.jsonl."""
            # Track file offsets: events + runs + live metrics per project
            event_offsets: dict[str, int] = {}
            run_offsets: dict[str, int] = {}
            metric_offsets: dict[str, int] = {}

            _state.reload()
            projects = _state.projects

            while True:
                for proj_id, proj in projects.items():
                    proj_path = proj.get("path", "")
                    if not proj_path:
                        continue
                    base = Path(proj_path) / ".distillate"

                    # --- Tail events.jsonl ---
                    events_file = base / "events.jsonl"
                    if events_file.exists():
                        ekey = str(events_file)
                        last_offset = event_offsets.get(ekey, 0)
                        try:
                            file_size = events_file.stat().st_size
                        except OSError:
                            file_size = 0

                        if file_size > last_offset:
                            try:
                                with open(events_file, encoding="utf-8") as f:
                                    f.seek(last_offset)
                                    for line in f:
                                        line = line.strip()
                                        if not line:
                                            continue
                                        # Check for session_end → auto-rescan
                                        try:
                                            evt = json.loads(line)
                                        except json.JSONDecodeError:
                                            yield f"data: {line}\n\n"
                                            continue
                                        yield f"data: {line}\n\n"
                                        if evt.get("type") == "session_end":
                                            # Auto-rescan in background
                                            loop = asyncio.get_event_loop()
                                            summary = await loop.run_in_executor(
                                                _executor,
                                                _rescan_project, proj_id, proj,
                                            )
                                            if summary:
                                                completed_evt = {
                                                    "type": "session_completed",
                                                    "project_id": proj_id,
                                                    "new_runs": summary["new_runs"],
                                                    "total_runs": summary["total_runs"],
                                                    "best_metric": summary["best_metric"],
                                                }
                                                yield f"data: {json.dumps(completed_evt)}\n\n"

                                            # Auto-continue if goals unmet
                                            _state.reload()
                                            fresh_proj = _state.get_project(proj_id)
                                            if fresh_proj and fresh_proj.get("auto_continue"):
                                                cont_evt = await _maybe_auto_continue(
                                                    proj_id, fresh_proj, loop,
                                                )
                                                if cont_evt:
                                                    yield f"data: {json.dumps(cont_evt)}\n\n"
                                event_offsets[ekey] = events_file.stat().st_size
                            except OSError:
                                pass

                    # --- Tail runs.jsonl ---
                    runs_file = base / "runs.jsonl"
                    if runs_file.exists():
                        rkey = str(runs_file)
                        last_offset = run_offsets.get(rkey, 0)
                        try:
                            file_size = runs_file.stat().st_size
                        except OSError:
                            file_size = 0

                        if file_size > last_offset:
                            try:
                                with open(runs_file, encoding="utf-8") as f:
                                    f.seek(last_offset)
                                    for line in f:
                                        line = line.strip()
                                        if not line:
                                            continue
                                        try:
                                            run_data = json.loads(line)
                                        except json.JSONDecodeError:
                                            continue
                                        run_evt = {
                                            "type": "run_update",
                                            "project_id": proj_id,
                                            "run": run_data,
                                        }
                                        yield f"data: {json.dumps(run_evt)}\n\n"

                                        # --- Goal checker ---
                                        if run_data.get("status") == "keep" or run_data.get("decision") == "keep":
                                            _state.reload()
                                            fresh_proj = _state.get_project(proj_id)
                                            if fresh_proj and fresh_proj.get("goals"):
                                                from distillate.launcher import should_continue, stop_session
                                                if not should_continue(fresh_proj):
                                                    # Find which goal was met
                                                    run_results = run_data.get("results", {})
                                                    for g in fresh_proj["goals"]:
                                                        gmetric = g.get("metric", "")
                                                        gthresh = g.get("threshold")
                                                        if gmetric and gthresh is not None and gmetric in run_results:
                                                            val = run_results[gmetric]
                                                            if isinstance(val, (int, float)):
                                                                met = False
                                                                if g.get("direction") == "maximize" and val >= gthresh:
                                                                    met = True
                                                                elif g.get("direction") == "minimize" and val <= gthresh:
                                                                    met = True
                                                                if met:
                                                                    goal_evt = {
                                                                        "type": "goal_reached",
                                                                        "project_id": proj_id,
                                                                        "metric": gmetric,
                                                                        "value": val,
                                                                        "target": gthresh,
                                                                    }
                                                                    yield f"data: {json.dumps(goal_evt)}\n\n"
                                                                    break

                                                    # Auto-stop running sessions
                                                    loop = asyncio.get_event_loop()
                                                    sessions = fresh_proj.get("sessions", {})
                                                    stopped_any = False
                                                    for sess in sessions.values():
                                                        if sess.get("status") == "running":
                                                            tmux = sess.get("tmux_session", "")
                                                            if tmux:
                                                                await loop.run_in_executor(
                                                                    _executor, stop_session, tmux, None,
                                                                )
                                                                sess["status"] = "completed"
                                                                stopped_any = True
                                                    if stopped_any:
                                                        _state.save()

                                run_offsets[rkey] = runs_file.stat().st_size
                            except OSError:
                                pass

                    # --- Tail live_metrics.jsonl ---
                    metrics_file = base / "live_metrics.jsonl"
                    if metrics_file.exists():
                        mkey = str(metrics_file)
                        last_offset = metric_offsets.get(mkey, 0)
                        try:
                            file_size = metrics_file.stat().st_size
                        except OSError:
                            file_size = 0

                        if file_size > last_offset:
                            try:
                                with open(metrics_file, encoding="utf-8") as f:
                                    f.seek(last_offset)
                                    for line in f:
                                        line = line.strip()
                                        if not line:
                                            continue
                                        try:
                                            metric_data = json.loads(line)
                                        except json.JSONDecodeError:
                                            continue
                                        metric_evt = {
                                            "type": "metric_update",
                                            "project_id": proj_id,
                                            **metric_data,
                                        }
                                        yield f"data: {json.dumps(metric_evt)}\n\n"
                                metric_offsets[mkey] = metrics_file.stat().st_size
                            except OSError:
                                pass

                await asyncio.sleep(2)

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # -------------------------------------------------------------------
    # Data endpoints for desktop app tabs
    # -------------------------------------------------------------------

    def _extract_run_number(name: str) -> tuple:
        """Extract sortable (number, suffix) from run name like 'run_122' or 'run_004a'.

        Returns (number, suffix) for run_NNN patterns, or (0, "") for others
        so non-numeric IDs sort first by timestamp fallback.
        """
        import re
        m = re.match(r"(?:run_?)(\d+)([a-z]?)", name)
        if m:
            return (int(m.group(1)), m.group(2))
        return (0, "")

    from distillate.experiment_tools import _run_summary_full
    from distillate.experiments import detect_primary_metric as _detect_primary_metric
    from distillate.experiments import infer_key_metric_name as _infer_key_metric_name

    @app.get("/experiments/list")
    async def list_experiments():
        from distillate.launcher import refresh_session_statuses

        _state.reload()

        # Refresh tmux session statuses so the UI doesn't show stale "running"
        loop = asyncio.get_event_loop()
        changed = await loop.run_in_executor(
            _executor, refresh_session_statuses, _state,
        )
        if changed:
            _state.save()

        # Auto-rescan projects to pick up new runs from runs.jsonl
        for pid, p in list(_state.projects.items()):
            try:
                await loop.run_in_executor(
                    _executor, _rescan_project, pid, p,
                )
            except Exception:
                pass

        _state.reload()
        projects = _state.projects
        result = []
        for proj_id, proj in projects.items():
            runs = proj.get("runs", {})
            sessions = proj.get("sessions", {})
            active_sessions = {
                sid: s for sid, s in sessions.items()
                if s.get("status") == "running"
            }
            active = len(active_sessions)
            entry = {
                "id": proj_id,
                "name": proj.get("name", ""),
                "path": proj.get("path", ""),
                "status": proj.get("status", ""),
                "description": proj.get("description", ""),
                "tags": proj.get("tags", []),
                "goals": proj.get("goals", []),
                "run_count": len(runs),
                "active_sessions": active,
                "sessions": {
                    sid: {
                        "tmux_session": s.get("tmux_session", ""),
                        "started_at": s.get("started_at", ""),
                    }
                    for sid, s in active_sessions.items()
                    if s.get("tmux_session")
                },
                "key_metric_name": _infer_key_metric_name(proj),
                "duration_minutes": proj.get("duration_minutes", 5),
                "added_at": proj.get("added_at", ""),
                "last_scanned_at": proj.get("last_scanned_at", ""),
                "runs": [_run_summary_full(r, *_extract_run_number(r.get("name", ""))) for r in sorted(
                    runs.values(),
                    key=lambda r: (_extract_run_number(r.get("name", "")), r.get("started_at", "")),
                )],
            }
            github_url = proj.get("github_url", "")
            if github_url:
                entry["github_url"] = github_url
            campaign = proj.get("campaign")
            if campaign:
                entry["campaign"] = campaign
            # Include research insights from LLM enrichment or RESULTS.md fallback
            proj_path_str = proj.get("path", "")
            if proj_path_str:
                from pathlib import Path as _P
                from distillate.experiments import load_enrichment_cache
                proj_p = _P(proj_path_str)
                cache = load_enrichment_cache(proj_p)
                enr = cache.get("enrichment", cache)
                project_insights = enr.get("project", {})
                if project_insights:
                    entry["insights"] = project_insights
                elif (proj_p / "RESULTS.md").exists():
                    # Fallback: use RESULTS.md as key_breakthrough
                    try:
                        results_text = (proj_p / "RESULTS.md").read_text(
                            encoding="utf-8"
                        ).strip()
                        if results_text:
                            entry["insights"] = {
                                "key_breakthrough": results_text[:2000],
                            }
                    except Exception:
                        pass

                # Latest learning + current run from runs.jsonl
                runs_jsonl = proj_p / ".distillate" / "runs.jsonl"
                if runs_jsonl.exists():
                    try:
                        all_lines = runs_jsonl.read_text(
                            encoding="utf-8"
                        ).splitlines()
                        found_learning = False
                        found_current = False
                        resolved_ids: set[str] = set()
                        for line in reversed(all_lines):
                            if found_learning and found_current:
                                break
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rr = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            rid = rr.get("id", "")
                            status = rr.get("status", "")
                            # Track completed runs so we skip stale
                            # "running" announcements
                            if status in ("keep", "discard", "crash"):
                                resolved_ids.add(rid)
                            # Surface what the agent is currently attempting
                            if (not found_current
                                    and status == "running"
                                    and rid not in resolved_ids
                                    and rr.get("description")):
                                entry["current_run"] = rr["description"]
                                entry["current_run_started"] = rr.get(
                                    "timestamp", "")
                                found_current = True
                            if (not found_learning
                                    and status == "keep"
                                    and rr.get("reasoning")):
                                entry["latest_learning"] = rr["reasoning"]
                                found_learning = True

                        # Total experiment time: pair-matching for
                        # running→completed, gap-based for remainder
                        MAX_GAP = 1800  # 30 min = session break
                        run_starts: dict[str, datetime] = {}
                        pair_secs = 0.0
                        unpaired_dts: list[datetime] = []
                        active_run_start = ""

                        for fwd_line in all_lines:
                            fwd_line = fwd_line.strip()
                            if not fwd_line:
                                continue
                            try:
                                rr = json.loads(fwd_line)
                            except json.JSONDecodeError:
                                continue
                            ts = rr.get("timestamp", "")
                            st = rr.get("status", "")
                            rid = rr.get("id", "")
                            if not ts:
                                continue
                            try:
                                dt = datetime.fromisoformat(
                                    ts.replace("Z", "+00:00"))
                                if dt.tzinfo is not None:
                                    dt = dt.replace(tzinfo=None)
                            except (ValueError, TypeError):
                                continue

                            if st == "running":
                                active_run_start = ts
                                run_starts[rid] = dt
                            elif st in ("keep", "discard", "crash"):
                                active_run_start = ""
                                if rid in run_starts:
                                    pair_secs += (
                                        dt - run_starts[rid]
                                    ).total_seconds()
                                    del run_starts[rid]
                                else:
                                    unpaired_dts.append(dt)
                            else:
                                unpaired_dts.append(dt)

                        # Gap-based for unpaired entries
                        gap_secs = 0.0
                        prev_dt = None
                        for udt in sorted(unpaired_dts):
                            if prev_dt is not None:
                                gap = (udt - prev_dt).total_seconds()
                                if 0 < gap <= MAX_GAP:
                                    gap_secs += gap
                            prev_dt = udt

                        entry["experiment_total_secs"] = (
                            pair_secs + gap_secs
                        )
                        if active_run_start:
                            # Only send if the announcement is recent
                            # (stale = agent logged "running" but never
                            # completed — don't show a ticking timer)
                            try:
                                ar_dt = datetime.fromisoformat(
                                    active_run_start.replace("Z", "+00:00"))
                                if ar_dt.tzinfo is not None:
                                    ar_dt = ar_dt.replace(tzinfo=None)
                                budget = (proj.get("duration_minutes") or 5) * 60
                                age = (datetime.utcnow() - ar_dt).total_seconds()
                                if age < budget * 3:
                                    entry["active_run_start"] = active_run_start
                            except (ValueError, TypeError):
                                pass
                    except OSError:
                        pass

                # Only report current_run/active_run_start for active sessions
                if active == 0:
                    entry.pop("current_run", None)
                    entry.pop("current_run_started", None)
                    entry["active_run_start"] = ""
                elif active > 0 and not entry.get("current_run"):
                    # Active session but no unresolved running entry
                    sess = next(iter(active_sessions.values()), {})
                    entry["current_run"] = "Session active"
                    entry["current_run_started"] = sess.get("started_at", "")

                # Experiment summary from PROMPT.md first meaningful line
                prompt_md = proj_p / "PROMPT.md"
                if prompt_md.exists():
                    try:
                        for pline in prompt_md.read_text(
                            encoding="utf-8"
                        ).splitlines():
                            pline = pline.strip()
                            if (pline and not pline.startswith("#")
                                    and not pline.startswith("```")):
                                entry["experiment_summary"] = pline[:500]
                                break
                    except OSError:
                        pass

            result.append(entry)
        return JSONResponse({"ok": True, "projects": result})

    @app.get("/experiments/{project_id}/notebook")
    async def experiment_notebook(project_id: str):
        from pathlib import Path

        from starlette.responses import HTMLResponse

        from distillate.experiments import (
            generate_html_notebook,
            load_enrichment_cache,
        )

        proj = _get_project_or_404(project_id)

        proj_path = Path(proj.get("path", ""))
        enrichment = load_enrichment_cache(proj_path) if proj_path.exists() else {}

        html = generate_html_notebook(proj, enrichment=enrichment)
        return HTMLResponse(html)

    @app.get("/experiments/{project_id}/chart/export")
    async def export_chart(project_id: str, metric: str = "", format: str = "png",
                           log_scale: str = ""):
        """Generate a Karpathy-style clean chart PNG for sharing."""
        proj = _get_project_or_404(project_id)

        from distillate.experiments import generate_export_chart
        runs = list(proj.get("runs", {}).values())
        if not metric:
            metric = _infer_key_metric_name(proj)
        if not metric:
            return JSONResponse({"ok": False, "reason": "no_metric"}, status_code=400)

        try:
            use_log = log_scale in ("1", "true", "yes")
            # Get experiment summary for chart subtitle
            subtitle = ""
            proj_path_str = proj.get("path", "")
            if proj_path_str:
                from pathlib import Path as _PP
                prompt_md = _PP(proj_path_str) / "PROMPT.md"
                if prompt_md.exists():
                    try:
                        for line in prompt_md.read_text(encoding="utf-8").splitlines():
                            line = line.strip()
                            if line and not line.startswith("#") and not line.startswith("```"):
                                # Strip markdown bold
                                import re
                                subtitle = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
                                if len(subtitle) > 80:
                                    subtitle = subtitle[:78] + "\u2026"
                                break
                    except OSError:
                        pass
            png_bytes = generate_export_chart(runs, metric, proj.get("name", project_id),
                                              log_scale=use_log, subtitle=subtitle)
            from starlette.responses import Response
            return Response(content=png_bytes, media_type="image/png")
        except Exception as e:
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

    @app.get("/papers")
    async def list_papers(status: str = None):
        _state.reload()
        docs = _state.documents
        promoted_set = set(_state.promoted_papers)
        results = []
        for key, doc in docs.items():
            if status and doc.get("status") != status:
                continue
            meta = doc.get("metadata", {})
            idx = _state.index_of(key)
            summary_text = doc.get("summary", "") or ""
            results.append({
                "index": idx,
                "key": key,
                "title": doc.get("title", ""),
                "citekey": meta.get("citekey", ""),
                "status": doc.get("status", ""),
                "authors": doc.get("authors", [])[:3],
                "summary": summary_text[:200] + ("..." if len(summary_text) > 200 else ""),
                "engagement": doc.get("engagement", 0),
                "promoted": key in promoted_set,
                "promoted_at": doc.get("promoted_at", ""),
                "tags": meta.get("tags", [])[:5],
                "citation_count": meta.get("citation_count", 0),
                "publication_date": meta.get("publication_date", ""),
                "uploaded_at": doc.get("uploaded_at", ""),
                "processed_at": doc.get("processed_at", ""),
                "page_count": meta.get("numPages") or meta.get("page_count", 0),
            })
        return JSONResponse({"ok": True, "papers": results, "total": len(results)})

    @app.post("/papers/{paper_key}/promote")
    async def promote_paper(paper_key: str):
        """Add a paper to the promoted list."""
        _state.reload()
        doc = _state.documents.get(paper_key)
        if not doc:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
        promoted = _state.promoted_papers
        if paper_key not in promoted:
            promoted.append(paper_key)
            doc["promoted_at"] = datetime.now(timezone.utc).isoformat()
            _state.save()
        return JSONResponse({"ok": True, "promoted": True})

    @app.post("/papers/{paper_key}/unpromote")
    async def unpromote_paper(paper_key: str):
        """Remove a paper from the promoted list."""
        _state.reload()
        doc = _state.documents.get(paper_key)
        if not doc:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
        promoted = _state.promoted_papers
        if paper_key in promoted:
            promoted.remove(paper_key)
            doc.pop("promoted_at", None)
            _state.save()
        return JSONResponse({"ok": True, "promoted": False})

    @app.post("/papers/{paper_key}/refresh-metadata")
    async def refresh_paper_metadata(paper_key: str):
        """Re-fetch metadata from Zotero + Semantic Scholar for a single paper."""
        _state.reload()
        doc = _state.documents.get(paper_key)
        if not doc:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
        from distillate.tools import refresh_metadata
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor, lambda: refresh_metadata(state=_state, identifier=paper_key)
        )
        return JSONResponse({"ok": True, "result": result})

    @app.get("/papers/{paper_key}")
    async def paper_detail(paper_key: str):
        _state.reload()
        doc = _state.get_document(paper_key)
        if not doc:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
        meta = doc.get("metadata", {})
        idx = _state.index_of(paper_key)

        # Read highlights from Obsidian note if available
        highlights = ""
        try:
            from distillate.tools import _read_note_content, _extract_highlights_from_note
            note = _read_note_content(meta.get("citekey", ""), doc.get("title", ""))
            if note:
                highlights = _extract_highlights_from_note(note)
        except Exception:
            pass

        return JSONResponse({"ok": True, "paper": {
            "index": idx,
            "key": paper_key,
            "title": doc.get("title", ""),
            "citekey": meta.get("citekey", ""),
            "status": doc.get("status", ""),
            "authors": doc.get("authors", []),
            "summary": doc.get("summary", "") or "",
            "s2_tldr": meta.get("s2_tldr", ""),
            "engagement": doc.get("engagement", 0),
            "tags": meta.get("tags", []),
            "citation_count": meta.get("citation_count", 0),
            "publication_date": meta.get("publication_date", ""),
            "venue": meta.get("venue", ""),
            "doi": meta.get("doi", ""),
            "arxiv_id": meta.get("arxiv_id", ""),
            "url": meta.get("url", ""),
            "uploaded_at": doc.get("uploaded_at", ""),
            "processed_at": doc.get("processed_at", ""),
            "promoted_at": doc.get("promoted_at", ""),
            "highlights": highlights,
        }})

    # -------------------------------------------------------------------
    # Insights & state management
    # -------------------------------------------------------------------

    @app.get("/report")
    async def report():
        """Reading insights dashboard data."""
        from collections import Counter
        from datetime import timedelta

        _state.reload()
        processed = _state.documents_with_status("processed")

        if not processed:
            return JSONResponse({"ok": True, "empty": True})

        # Lifetime stats
        total_papers = len(processed)
        total_pages = sum(
            d.get("page_count", 0)
            or d.get("metadata", {}).get("numPages", 0)
            or 0
            for d in processed
        )
        total_words = sum(d.get("highlight_word_count", 0) for d in processed)
        engagements = [d.get("engagement", 0) for d in processed if d.get("engagement")]
        avg_engagement = round(sum(engagements) / len(engagements)) if engagements else 0

        # Reading velocity (last 8 weeks)
        velocity = []
        week_counts: Counter = Counter()
        now = datetime.now(timezone.utc)
        for doc in processed:
            ts = doc.get("processed_at", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
                weeks_ago = (now - dt).days // 7
                if weeks_ago < 8:
                    monday = dt - timedelta(days=dt.weekday())
                    label = monday.strftime("%Y-%m-%d")
                    week_counts[label] += 1
            except (ValueError, TypeError):
                pass
        for label in sorted(week_counts.keys()):
            velocity.append({"week": label, "count": week_counts[label]})

        # Top topics
        topic_counter: Counter = Counter()
        for doc in processed:
            tags = doc.get("metadata", {}).get("tags") or []
            for tag in tags:
                topic_counter[tag] += 1
        topics = [{"topic": t, "count": c} for t, c in topic_counter.most_common(8)]

        # Engagement distribution
        buckets = {"0-25%": 0, "25-50%": 0, "50-75%": 0, "75-100%": 0}
        for doc in processed:
            eng = doc.get("engagement", 0)
            if eng <= 25:
                buckets["0-25%"] += 1
            elif eng <= 50:
                buckets["25-50%"] += 1
            elif eng <= 75:
                buckets["50-75%"] += 1
            else:
                buckets["75-100%"] += 1
        engagement_dist = [{"range": k, "count": v} for k, v in buckets.items()]

        # Most-cited papers
        cited = sorted(
            [d for d in processed if d.get("metadata", {}).get("citation_count", 0) > 0],
            key=lambda d: d.get("metadata", {}).get("citation_count", 0),
            reverse=True,
        )
        cited_papers = []
        for doc in cited[:5]:
            key = doc.get("zotero_item_key", "")
            cited_papers.append({
                "title": doc.get("title", "")[:80],
                "citations": doc["metadata"]["citation_count"],
                "index": _state.index_of(key) if key else 0,
            })

        # Most-read authors
        author_counter: Counter = Counter()
        for doc in processed:
            for author in doc.get("authors", []):
                if author and author.lower() != "unknown":
                    author_counter[author] += 1
        top_authors = [
            {"name": a, "count": c}
            for a, c in author_counter.most_common(5)
            if c >= 2
        ]

        return JSONResponse({
            "ok": True,
            "lifetime": {
                "papers": total_papers,
                "pages": total_pages,
                "words": total_words,
                "avg_engagement": avg_engagement,
            },
            "velocity": velocity,
            "topics": topics,
            "engagement": engagement_dist,
            "cited_papers": cited_papers,
            "top_authors": top_authors,
        })

    @app.get("/state/export")
    async def export_state():
        """Return current state as JSON for backup."""
        from distillate.state import STATE_PATH
        if not STATE_PATH.exists():
            return JSONResponse({"ok": False, "reason": "no_state"}, status_code=404)
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return JSONResponse({"ok": True, "state": data})

    @app.post("/state/import")
    async def import_state(body: dict):
        """Validate and import a state backup."""
        import shutil
        from distillate.state import STATE_PATH, _run_migrations

        state_data = body.get("state")
        if not state_data or not isinstance(state_data, dict):
            return JSONResponse({"ok": False, "reason": "invalid_body"}, status_code=400)
        if "documents" not in state_data:
            return JSONResponse({"ok": False, "reason": "missing_documents"}, status_code=400)

        _run_migrations(state_data)

        # Backup existing
        if STATE_PATH.exists():
            backup = STATE_PATH.with_suffix(".json.bak")
            shutil.copy2(STATE_PATH, backup)

        STATE_PATH.write_text(
            json.dumps(state_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _state.reload()
        n_papers = len(state_data.get("documents", {}))
        return JSONResponse({"ok": True, "papers": n_papers})

    @app.websocket("/ws")
    async def ws_chat(websocket: WebSocket):
        await websocket.accept()

        nicolas = NicolasClient(_state)

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

                if msg.get("type") == "set_model":
                    new_model = msg.get("model")
                    if new_model:
                        await nicolas.set_model(new_model)
                        log.info("Model changed to %s", new_model)
                    continue

                user_input = msg.get("text", "").strip()
                if not user_input:
                    continue

                try:
                    async for event in nicolas.send(user_input):
                        await websocket.send_json(event)
                except Exception as exc:
                    log.exception("Nicolas query failed")
                    await websocket.send_json({
                        "type": "error",
                        "message": str(exc),
                        "category": _classify_error(str(exc)),
                    })

        except WebSocketDisconnect:
            log.info("WebSocket client disconnected")
        except Exception:
            log.exception("WebSocket error")
        finally:
            await nicolas.disconnect()

    # Mount desktop UI static files at /ui (served from wheel or dev monorepo)
    ui_dir = _find_ui_dir()
    if ui_dir:
        from starlette.staticfiles import StaticFiles
        app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")
        log.info("Serving UI from %s", ui_dir)

    return app


def main():
    """Entry point: ``python -m distillate.server [port]``."""
    import uvicorn

    port = int(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_PORT
    log.info("Starting Nicolas server on 127.0.0.1:%d", port)
    uvicorn.run(
        _create_app(),
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
