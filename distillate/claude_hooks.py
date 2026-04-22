"""Claude Code hooks integration.

Claude Code can be configured to fire hooks on key events (turn end,
permission prompt, user prompt submit, ...). We register HTTP hooks that
POST to the local Distillate server so the desktop sidebar can reflect
agent state instantly — no tmux content-polling round-trip.

Schema + semantics: https://code.claude.com/docs/en/hooks.md

This module owns:
- The hook config writer (`write_hook_config`) that creates
  `.claude/settings.local.json` inside a project's repo on launch.
- The session resolver (`resolve_session`) that maps a hook payload's
  `session_id` / `cwd` to a Distillate (workspace_id, session_id).
- A tiny in-memory state store (`get_hook_state`, `set_hook_state`,
  `clear_hook_state`) keyed by (workspace_id, session_id). Not persisted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# In-memory hook state store
# ---------------------------------------------------------------------------

# (workspace_id, session_id) → "working" | "idle" | "waiting"
_HOOK_STATE: Dict[Tuple[str, str], str] = {}


def get_hook_state(key: Tuple[str, str]) -> Optional[str]:
    """Return the last hook-reported state for a session, or None if unknown."""
    return _HOOK_STATE.get(key)


def set_hook_state(key: Tuple[str, str], status: str) -> None:
    """Record a session's hook-reported state."""
    _HOOK_STATE[key] = status


def clear_hook_state() -> None:
    """Wipe the in-memory store (used by tests and on server restart)."""
    _HOOK_STATE.clear()


def snapshot_hook_state() -> Dict[Tuple[str, str], str]:
    """Return a shallow copy of the current state map."""
    return dict(_HOOK_STATE)


def merge_with_hook_state(sessions: Dict[str, dict]) -> Dict[str, dict]:
    """Overlay hook-reported status on a sessions dict from tmux polling.

    Sessions dict is keyed by ``"workspace_id/session_id"``. For each key that
    has a value in the hook state store, ``info["status"]`` is overridden.
    Other fields (name, etc.) are preserved. Returns the mutated input.

    Hook state is authoritative when present: once Claude Code signals
    via ``Stop`` or ``Notification``, we trust that over the tmux-content
    classifier (which can be fooled by user typing, scrollback quirks, etc.).
    """
    for key_str, info in sessions.items():
        if "/" not in key_str:
            continue
        ws_id, sid = key_str.split("/", 1)
        hook_status = _HOOK_STATE.get((ws_id, sid))
        if hook_status:
            info["status"] = hook_status
    return sessions


# ---------------------------------------------------------------------------
# Server port (set at startup, read by the launcher)
# ---------------------------------------------------------------------------

_SERVER_PORT: int = 0


def set_server_port(port: int) -> None:
    """Record the port the Distillate server is listening on.

    Set once at server startup (from ``server.main``). The session launcher
    reads this when writing ``.claude/settings.local.json`` so Claude Code's
    hooks POST to the right URL. When unset (0), the launcher skips hook
    config writing — useful for CLI / test contexts without a running server.
    """
    global _SERVER_PORT
    _SERVER_PORT = int(port)


def get_server_port() -> int:
    """Return the currently-set server port, or 0 if not set."""
    return _SERVER_PORT


# ---------------------------------------------------------------------------
# Hook config writer
# ---------------------------------------------------------------------------

def write_hook_config(project_dir: Path, server_port: int, agent_type: str = "claude") -> None:
    """Write `.agent/settings.local.json` in a project dir with our hooks.

    Merges with any pre-existing file: the user's non-Distillate hooks and
    unrelated settings (permissions, env, etc.) are preserved. Our three
    events (Stop, Notification, UserPromptSubmit) are overwritten on each
    call — idempotent, port-aware.

    Skipped if the existing file contains ``"_distillate_no_http_hooks": true`` —
    lets workspace sessions (coding/writing/research) opt out so these hooks
    only fire in auto-research experiment sessions.

    `.claude/settings.local.json` (or `.gemini/...`) is gitignored by CLI convention,
    so writing into checked-out repos is safe.
    """
    project_dir = Path(project_dir)
    cfg_dir = project_dir / f".{agent_type}"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "settings.local.json"

    existing: dict = {}
    if cfg_path.exists():
        try:
            loaded = json.loads(cfg_path.read_text())
            if isinstance(loaded, dict):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Opt-out marker: if set, don't write the HTTP lifecycle hooks.
    if existing.get("_distillate_no_http_hooks"):
        return

    existing_hooks = existing.get("hooks", {})
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}

    # Map agent_type to the hook base URL slug
    url_slug = "claude-code" if agent_type == "claude" else "gemini"
    base_url = f"http://127.0.0.1:{server_port}/hooks/{url_slug}"
    our_hooks = {
        "Stop": [{
            "hooks": [{"type": "http", "url": f"{base_url}/stop"}],
        }],
        "Notification": [{
            "matcher": "permission_prompt",
            "hooks": [{"type": "http", "url": f"{base_url}/notification"}],
        }],
        "UserPromptSubmit": [{
            "hooks": [{"type": "http", "url": f"{base_url}/user-prompt-submit"}],
        }],
    }
    for event, blocks in our_hooks.items():
        existing_hooks[event] = blocks

    existing["hooks"] = existing_hooks

    # Pre-approve MCP servers declared in this project's .mcp.json so Claude
    # Code doesn't interrupt the experiment with a "New MCP server found"
    # approval modal on every launch. Experiment repos are scaffolded by us,
    # and the declared servers (distillate, optionally huggingface) are ours.
    if agent_type == "claude":
        existing["enableAllProjectMcpServers"] = True

    cfg_path.write_text(json.dumps(existing, indent=2))


# ---------------------------------------------------------------------------
# Session resolver: hook payload → (workspace_id, session_id)
# ---------------------------------------------------------------------------

def resolve_session(
    state,
    *,
    claude_session_id: str,
    cwd: str,
) -> Optional[Tuple[str, str]]:
    """Map a hook payload to a Distillate (workspace_id, session_id).

    Primary lookup: exact match of the payload's `session_id` against each
    coding session's stored `claude_session_id`. This is the reliable path
    once the status poll has discovered the Claude session UUID.

    Fallback: if no session_id match, match against active sessions whose
    `repo_path` equals `cwd` or is a prefix of it (user cd'd into a subdir).
    Returns the hit only if exactly one candidate matches — ambiguity ⇒ None.
    """
    # Primary: claude_session_id match (non-empty only)
    if claude_session_id:
        for ws_id, ws in state.workspaces.items():
            for sid, sess in ws.get("coding_sessions", {}).items():
                stored = sess.get("claude_session_id", "")
                if stored and stored == claude_session_id:
                    return (ws_id, sid)

    if not cwd:
        return None

    try:
        cwd_resolved = str(Path(cwd).expanduser().resolve())
    except (OSError, ValueError):
        return None

    candidates = []
    for ws_id, ws in state.workspaces.items():
        for sid, sess in ws.get("coding_sessions", {}).items():
            if sess.get("status") != "running":
                continue
            repo_path = sess.get("repo_path", "")
            if not repo_path:
                continue
            if cwd_resolved == repo_path or cwd_resolved.startswith(repo_path + "/"):
                candidates.append((ws_id, sid))

    if len(candidates) == 1:
        return candidates[0]
    return None
