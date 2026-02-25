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

from distillate import config
from distillate.agent_core import stream_turn
from distillate.state import State

log = logging.getLogger(__name__)

_DEFAULT_PORT = 8742
_executor = ThreadPoolExecutor(max_workers=2)


def _create_app():
    """Build the FastAPI application (lazy import so PyPI installs don't need fastapi)."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Nicolas", docs_url=None, redoc_url=None)

    # Shared state
    _state = State()
    _conversation: list[dict] = []
    _all_sessions: list[dict] = []

    @app.get("/status")
    async def status():
        from importlib.metadata import version
        ver = version("distillate")
        return JSONResponse({"ok": True, "version": ver})

    @app.websocket("/ws")
    async def ws_chat(websocket: WebSocket):
        await websocket.accept()
        loop = asyncio.get_event_loop()

        try:
            import anthropic
        except ImportError:
            await websocket.send_json({
                "type": "error",
                "message": "anthropic package not installed",
                "category": "missing_package",
            })
            await websocket.close()
            return

        if not config.ANTHROPIC_API_KEY:
            await websocket.send_json({
                "type": "error",
                "message": "ANTHROPIC_API_KEY not configured",
                "category": "invalid_key",
            })
            await websocket.close()
            return

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    msg = {"text": raw}

                user_input = msg.get("text", "").strip()
                if not user_input:
                    continue

                # Run the synchronous generator in a thread, relay events
                queue: asyncio.Queue = asyncio.Queue()

                def _run_turn():
                    try:
                        for event in stream_turn(
                            client, _state, _conversation, user_input,
                            past_sessions=_all_sessions,
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
        except Exception:
            log.exception("WebSocket error")

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
