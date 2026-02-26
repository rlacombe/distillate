"""Local WebSocket server for the Nicolas desktop app.

Bridges the synchronous ``agent_core.stream_turn`` generator to an
async WebSocket so the Electron renderer can consume events.

Not included in PyPI dependencies — ``fastapi`` and ``uvicorn`` are
only installed in the bundled Electron venv.

Usage::

    python -m distillate.server [port]
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
        return JSONResponse({
            "ok": True,
            "version": ver,
            "papers_read": len(processed),
            "papers_queued": len(queue),
        })

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
