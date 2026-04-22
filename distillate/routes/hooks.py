"""Claude Code hook receiver endpoints.

Claude Code POSTs to these when configured via
`.claude/settings.local.json` (see `distillate.claude_hooks.write_hook_config`).

We receive three events:
  POST /hooks/claude-code/stop                  → session becomes idle
  POST /hooks/claude-code/notification          → waiting (only permission_prompt)
  POST /hooks/claude-code/user-prompt-submit    → working

All handlers return 200 with `{"matched": bool}`. Claude Code treats non-2xx
as a non-blocking failure that still logs a warning — returning 200 with an
explicit matched=false for unknown sessions keeps its debug log clean.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from distillate.routes import _context
from distillate.claude_hooks import resolve_session, set_hook_state

log = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_from_body(request: Request):
    """Parse JSON body and resolve to (workspace_id, session_id) or None."""
    try:
        body = await request.json()
    except Exception:
        return None, {}
    if not isinstance(body, dict):
        return None, {}

    _context._cached_reload()
    key = resolve_session(
        _context._state,
        claude_session_id=body.get("session_id", "") or "",
        cwd=body.get("cwd", "") or "",
    )
    return key, body


@router.post("/hooks/claude-code/stop")
@router.post("/hooks/gemini/stop")
async def hook_stop(request: Request):
    """Agent finished a turn → session is idle at the prompt."""
    key, _body = await _resolve_from_body(request)
    if key is None:
        return JSONResponse({"matched": False})
    set_hook_state(key, "idle")
    return JSONResponse({"matched": True})


@router.post("/hooks/claude-code/notification")
@router.post("/hooks/gemini/notification")
async def hook_notification(request: Request):
    """Agent notification → waiting, but only for permission prompts."""
    key, body = await _resolve_from_body(request)
    if key is None:
        return JSONResponse({"matched": False})
    notif_type = body.get("notification_type", "")
    if notif_type == "permission_prompt":
        set_hook_state(key, "waiting")
        return JSONResponse({"matched": True})
    # Other notification types (idle_prompt, auth_success) are acknowledged
    # but don't flip the state — they're not "needs user attention" signals.
    return JSONResponse({"matched": True, "acted": False})


@router.post("/hooks/claude-code/user-prompt-submit")
@router.post("/hooks/gemini/user-prompt-submit")
async def hook_user_prompt_submit(request: Request):
    """User submitted a prompt → session is working on a response."""
    key, _body = await _resolve_from_body(request)
    if key is None:
        return JSONResponse({"matched": False})
    set_hook_state(key, "working")
    return JSONResponse({"matched": True})
