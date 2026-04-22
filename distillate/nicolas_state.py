"""In-memory chat-turn state for Nicolas (the Tier 1 shell agent).

Nicolas is a singleton in-process ClaudeSDKClient, not a Claude Code
subprocess, so the (workspace_id, session_id) store in
``distillate.claude_hooks`` doesn't fit. Nicolas gets its own single
module-level variable tracking "working" vs "idle", flipped from inside
``NicolasClient.send()`` (see ``distillate/agent_sdk.py``).

Surfaced via ``GET /nicolas/state`` so the renderer can treat it as the
authoritative source (replacing the previous renderer-local flag).

Transient — wiped on server restart, just like ``HookStateStore``.
"""
from __future__ import annotations

from typing import Optional


# "working" | "idle" | None (None = never seen a turn yet)
_NICOLAS_STATE: Optional[str] = None


def get_nicolas_state() -> Optional[str]:
    """Return Nicolas's last-reported turn state, or None if unknown."""
    return _NICOLAS_STATE


def set_nicolas_state(status: str) -> None:
    """Record Nicolas's current turn state (``working`` or ``idle``)."""
    global _NICOLAS_STATE
    _NICOLAS_STATE = status


def clear_nicolas_state() -> None:
    """Wipe the in-memory state (used by tests and on server restart)."""
    global _NICOLAS_STATE
    _NICOLAS_STATE = None
