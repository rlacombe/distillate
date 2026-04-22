"""Shared server context — replaces closure-scoped state from _create_app().

Module-level state initialized by ``init()``, called from ``server._create_app()``.
Router modules import from here to access shared state and helpers.

Usage in routers::

    from distillate.routes import _context

    @router.get("/example")
    async def example():
        _context._cached_reload()
        _state = _context._state
        # ... use _state as before
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from distillate.state import State

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state (set by init())
# ---------------------------------------------------------------------------

_state: State | None = None
_last_reload: float = 0.0
_RELOAD_TTL: float = 2.0
_initial_sync_done: bool = False
_campaign_tasks: dict = {}  # experiment_id → asyncio.Task
_executor = ThreadPoolExecutor(max_workers=4)


def init(state: State) -> None:
    """Initialize shared context. Called once from _create_app()."""
    global _state
    _state = state


# ---------------------------------------------------------------------------
# Shared helpers (same signatures as the old closure functions)
# ---------------------------------------------------------------------------

def _cached_reload() -> None:
    """Reload state from disk at most once per _RELOAD_TTL seconds."""
    global _last_reload
    now = time.monotonic()
    if now - _last_reload >= _RELOAD_TTL:
        _state.reload()
        _last_reload = now


def _require_local_auth(request) -> None:
    """Guard for sensitive endpoints: require auth token or Electron origin."""
    from fastapi import HTTPException

    token = request.headers.get("x-auth-token", "")
    expected = os.environ.get("DISTILLATE_AUTH_TOKEN", "").strip()
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
    from fastapi import HTTPException

    _cached_reload()
    proj = _state.find_experiment(experiment_id)
    if not proj:
        raise HTTPException(404, detail="not_found")
    return proj
