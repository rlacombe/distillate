"""Breadcrumb emission for sub-agent work.

Breadcrumbs are short status messages emitted while a sub-agent works.
They appear in the chat UI as lightweight progress indicators:

    📚 Librarian is reading 12 highlights from Smith et al. 2024…
    📚 Found 47 highlights across the top 5 papers
    📚 Done — synthesized summary in 8s

Breadcrumbs are pushed to the renderer via WebSocket events.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class Breadcrumb:
    """A single breadcrumb event."""
    agent_name: str      # e.g. "librarian"
    agent_icon: str      # e.g. "📚"
    message: str         # e.g. "reading 12 highlights..."
    timestamp: str = ""  # ISO-8601, set on emit


# In-memory queue for the current request — flushed to WebSocket by the caller
_pending: list[Breadcrumb] = []
_listeners: list[Callable[[Breadcrumb], None]] = []


def add_listener(fn: Callable[[Breadcrumb], None]) -> None:
    """Register a breadcrumb listener (e.g. the WebSocket broadcaster)."""
    _listeners.append(fn)


def remove_listener(fn: Callable[[Breadcrumb], None]) -> None:
    """Remove a breadcrumb listener."""
    try:
        _listeners.remove(fn)
    except ValueError:
        pass


def emit(agent_name: str, agent_icon: str, message: str) -> Breadcrumb:
    """Emit a breadcrumb — notify all listeners."""
    from datetime import datetime, timezone
    bc = Breadcrumb(
        agent_name=agent_name,
        agent_icon=agent_icon,
        message=message,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    _pending.append(bc)
    for fn in _listeners:
        try:
            fn(bc)
        except Exception:
            log.debug("Breadcrumb listener error", exc_info=True)
    return bc


def make_emitter(agent_name: str, agent_icon: str) -> Callable[[str], None]:
    """Create a bound emitter function for a specific sub-agent."""
    def _emit(message: str) -> None:
        emit(agent_name, agent_icon, message)
    return _emit


def flush() -> list[Breadcrumb]:
    """Return and clear all pending breadcrumbs."""
    result = list(_pending)
    _pending.clear()
    return result
