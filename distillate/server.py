"""Local WebSocket server for the Nicolas desktop app.

Bridges the synchronous ``agent_core.stream_turn`` generator to an
async WebSocket so the Electron renderer can consume events.

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

Chat:
    WS   /ws                                  Agent chat (Nicolas REPL over WebSocket).
"""

import asyncio
import json
import logging
import sys

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from distillate.agent_core import stream_turn
from distillate.state import State

log = logging.getLogger(__name__)

_DEFAULT_PORT = 8742
_executor = ThreadPoolExecutor(max_workers=2)


def _create_app():
    """Build the FastAPI application (lazy import so PyPI installs don't need fastapi)."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse

    from distillate import config
    config.ensure_loaded()

    app = FastAPI(title="Nicolas", docs_url=None, redoc_url=None)

    # Shared state
    _state = State()

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

        _state.reload()
        proj = _state.find_project(project_query)
        if not proj:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

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
        _state.reload()
        proj = _state.find_project(project_id)
        if not proj:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

        proj_path = Path(proj.get("path", ""))
        if not proj_path.exists():
            return JSONResponse({"ok": False, "reason": "path_not_found"}, status_code=404)

        model = body.get("model", "claude-sonnet-4-6")
        max_turns = body.get("max_turns", 100)

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _executor,
                lambda: launch_experiment(
                    proj_path, model=model, max_turns=max_turns, project=proj,
                ),
            )
            # Persist session to state
            sessions = proj.setdefault("sessions", {})
            sessions[result["session_id"]] = result
            _state.save()

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
        _state.reload()
        proj = _state.find_project(project_id)
        if not proj:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

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

    @app.get("/experiments/stream")
    async def experiments_stream():
        """SSE endpoint that tails experiment events from tracked projects."""
        from pathlib import Path

        from starlette.responses import StreamingResponse

        async def _event_generator():
            """Yield SSE events from .distillate/events.jsonl files."""
            # Track file offsets per project
            offsets: dict[str, int] = {}

            _state.reload()
            projects = _state.projects

            while True:
                for proj in projects.values():
                    proj_path = proj.get("path", "")
                    if not proj_path:
                        continue
                    events_file = Path(proj_path) / ".distillate" / "events.jsonl"
                    if not events_file.exists():
                        continue

                    key = str(events_file)
                    last_offset = offsets.get(key, 0)
                    try:
                        file_size = events_file.stat().st_size
                    except OSError:
                        continue

                    if file_size <= last_offset:
                        continue

                    try:
                        with open(events_file, encoding="utf-8") as f:
                            f.seek(last_offset)
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                yield f"data: {line}\n\n"
                        offsets[key] = file_size
                    except OSError:
                        continue

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

    def _run_summary(run: dict) -> dict:
        """Build a concise run summary dict."""
        results = run.get("results", {})
        key_metric = ""
        for k in ("accuracy", "exact_match", "test_accuracy", "val_accuracy",
                  "best_val_acc", "f1", "loss"):
            if k in results:
                key_metric = f"{k}={results[k]}"
                break
        if not key_metric and results:
            k, v = next(iter(results.items()))
            if isinstance(v, (int, float)):
                key_metric = f"{k}={v}"
        return {
            "id": run.get("id", ""),
            "name": run.get("name", ""),
            "status": run.get("status", ""),
            "decision": run.get("decision", ""),
            "key_metric": key_metric,
            "results": {k: v for k, v in results.items() if isinstance(v, (int, float))},
            "hyperparameters": run.get("hyperparameters", {}),
            "hypothesis": run.get("hypothesis", ""),
            "reasoning": run.get("reasoning", ""),
            "baseline_comparison": run.get("baseline_comparison"),
            "started_at": run.get("started_at", ""),
            "duration_minutes": run.get("duration_minutes", 0),
            "tags": run.get("tags", []),
        }

    def _infer_key_metric_name(proj: dict) -> str:
        """Infer the primary metric name from goals or most common result key."""
        goals = proj.get("goals", [])
        if goals and isinstance(goals[0], dict) and goals[0].get("metric"):
            return goals[0]["metric"]
        # Fall back to most common numeric result key across kept runs
        from collections import Counter
        key_counts: Counter = Counter()
        for run in proj.get("runs", {}).values():
            if run.get("decision") != "keep":
                continue
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    key_counts[k] += 1
        if key_counts:
            return key_counts.most_common(1)[0][0]
        # Fall back to any numeric result key
        for run in proj.get("runs", {}).values():
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    return k
        return ""

    @app.get("/experiments/list")
    async def list_experiments():
        _state.reload()
        projects = _state.projects
        result = []
        for proj_id, proj in projects.items():
            runs = proj.get("runs", {})
            sessions = proj.get("sessions", {})
            active = sum(1 for s in sessions.values() if s.get("status") == "running")
            result.append({
                "id": proj_id,
                "name": proj.get("name", ""),
                "path": proj.get("path", ""),
                "status": proj.get("status", ""),
                "description": proj.get("description", ""),
                "tags": proj.get("tags", []),
                "run_count": len(runs),
                "active_sessions": active,
                "key_metric_name": _infer_key_metric_name(proj),
                "last_scanned_at": proj.get("last_scanned_at", ""),
                "runs": [_run_summary(r) for r in sorted(
                    runs.values(), key=lambda r: r.get("started_at", ""),
                )],
            })
        return JSONResponse({"ok": True, "projects": result})

    @app.get("/experiments/{project_id}/notebook")
    async def experiment_notebook(project_id: str):
        from pathlib import Path

        from starlette.responses import HTMLResponse

        from distillate.experiments import generate_html_notebook, load_enrichment_cache

        _state.reload()
        proj = _state.find_project(project_id)
        if not proj:
            return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

        proj_path = Path(proj.get("path", ""))
        enrichment = load_enrichment_cache(proj_path) if proj_path.exists() else {}

        html = generate_html_notebook(proj, enrichment=enrichment)
        return HTMLResponse(html)

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
        loop = asyncio.get_event_loop()

        from distillate.agent_core import create_client

        client = create_client()
        if client is None:
            await websocket.send_json({
                "type": "error",
                "message": "No API credentials configured",
                "category": "invalid_key",
            })
            await websocket.close()
            return

        # Per-connection conversation state
        conversation: list[dict] = []
        all_sessions: list[dict] = _load_sessions()

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    msg = {"text": raw}

                # Handle new conversation request
                if msg.get("type") == "new_conversation":
                    if conversation:
                        _save_session(all_sessions, conversation)
                    conversation = []
                    continue

                # Handle model change — update the module-level default
                if msg.get("type") == "set_model":
                    new_model = msg.get("model")
                    if new_model:
                        from distillate import agent_core
                        agent_core._AGENT_MODEL = new_model
                        log.info("Model changed to %s", new_model)
                    continue

                user_input = msg.get("text", "").strip()
                if not user_input:
                    continue

                # Run the synchronous generator in a thread, relay events
                queue: asyncio.Queue = asyncio.Queue()

                def _run_turn():
                    try:
                        for event in stream_turn(
                            client, _state, conversation, user_input,
                            past_sessions=all_sessions,
                        ):
                            loop.call_soon_threadsafe(queue.put_nowait, event)
                    except Exception as exc:
                        log.exception("stream_turn crashed")
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            {"type": "error", "message": str(exc), "category": "unknown"},
                        )
                    finally:
                        loop.call_soon_threadsafe(
                            queue.put_nowait, None,  # sentinel
                        )

                _executor.submit(_run_turn)

                # Relay events to WebSocket
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    await websocket.send_json(event)

        except WebSocketDisconnect:
            log.info("WebSocket client disconnected")
            if conversation:
                _save_session(all_sessions, conversation)
        except Exception:
            log.exception("WebSocket error")
            if conversation:
                _save_session(all_sessions, conversation)

    return app


# ---------------------------------------------------------------------------
# Conversation persistence — reuses the CLI's conversations.json format
# ---------------------------------------------------------------------------

_MAX_SESSIONS = 50


def _conversations_path():
    """Return the path to the shared conversations log."""
    from distillate import config
    return config.CONFIG_DIR / "conversations.json"


def _load_sessions() -> list[dict]:
    """Load past sessions from the shared conversation log."""
    try:
        return json.loads(_conversations_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_session(all_sessions: list[dict], conversation: list[dict]) -> None:
    """Append a session to the conversation log."""
    # Build a summary from the first user message
    first_user = ""
    for msg in conversation:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                first_user = content[:120]
            break

    session = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": first_user,
        "turns": len(conversation),
    }
    all_sessions.append(session)

    # Trim and save
    trimmed = all_sessions[-_MAX_SESSIONS:]
    try:
        _conversations_path().write_text(
            json.dumps(trimmed, ensure_ascii=False, indent=None),
            encoding="utf-8",
        )
    except OSError:
        log.warning("Could not save conversation log")


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
