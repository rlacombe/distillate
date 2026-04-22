"""Workspaces — project management, repos, sessions, notes, write-ups."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from distillate.routes import _context

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/workspaces")
async def list_workspaces():
    """List all workspace projects."""
    _state = _context._state
    from distillate.experiment_tools import list_workspaces_tool
    return JSONResponse(list_workspaces_tool(state=_state))


@router.get("/workspaces/agent-status")
async def agent_status():
    """Fast status-only poll for sidebar dots. Runs in thread to avoid blocking event loop."""
    _state = _context._state
    from distillate.experiment_tools import agent_status_tool
    result = await asyncio.to_thread(agent_status_tool, state=_state)
    return JSONResponse(result)


@router.post("/workspaces")
async def create_workspace(request: Request):
    """Create a workspace project. Body: {"name": "...", "description": "...", "repos": [...]}"""
    _state = _context._state
    from distillate.experiment_tools import create_workspace_tool
    body = await request.json()
    return JSONResponse(create_workspace_tool(state=_state, **body))


@router.get("/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str):
    """Get workspace details."""
    _state = _context._state
    from distillate.experiment_tools import get_workspace_tool
    return JSONResponse(get_workspace_tool(state=_state, workspace=workspace_id))


@router.post("/workspaces/{workspace_id}/repos")
async def add_workspace_repo(workspace_id: str, request: Request):
    """Link a repo. Body: {"path": "/abs/path", "name": "optional"}"""
    _state = _context._state
    from distillate.experiment_tools import add_workspace_repo_tool
    body = await request.json()
    return JSONResponse(add_workspace_repo_tool(
        state=_state, workspace=workspace_id, **body))


@router.delete("/workspaces/{workspace_id}/repos")
async def remove_workspace_repo(workspace_id: str, request: Request):
    """Unlink a repo. Body: {"path": "/abs/path"}"""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    body = await request.json()
    path = body.get("path", "")
    if not path:
        return JSONResponse({"success": False, "error": "path required."})
    acquire_lock()
    try:
        _state.reload()
        ok = _state.remove_workspace_repo(workspace_id, path)
        if not ok:
            return JSONResponse({"success": False, "error": "Repo not found."})
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True})


@router.post("/workspaces/{workspace_id}/experiments")
async def link_workspace_experiment(workspace_id: str, request: Request):
    """Link an experiment to a workspace. Body: {"experiment_id": "..."}"""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    body = await request.json()
    experiment_id = body.get("experiment_id", "")
    if not experiment_id:
        return JSONResponse({"success": False, "error": "experiment_id required."})
    acquire_lock()
    try:
        _state.reload()
        if not _state.get_workspace(workspace_id):
            return JSONResponse({"success": False, "error": "Project not found."})
        exp = _state.experiments.get(experiment_id)
        if not exp:
            return JSONResponse({"success": False, "error": "Experiment not found."})
        _state.update_experiment(experiment_id, workspace_id=workspace_id)
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True})


@router.delete("/workspaces/{workspace_id}/experiments")
async def unlink_workspace_experiment(workspace_id: str, request: Request):
    """Unlink an experiment from a workspace. Body: {"experiment_id": "..."}"""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    body = await request.json()
    experiment_id = body.get("experiment_id", "")
    if not experiment_id:
        return JSONResponse({"success": False, "error": "experiment_id required."})
    acquire_lock()
    try:
        _state.reload()
        exp = _state.experiments.get(experiment_id)
        if not exp:
            return JSONResponse({"success": False, "error": "Experiment not found."})
        if exp.get("workspace_id") != workspace_id:
            return JSONResponse({"success": False, "error": "Experiment not linked to this project."})
        _state.update_experiment(experiment_id, workspace_id="")
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True})


@router.delete("/workspaces/{workspace_id}/papers")
async def remove_workspace_paper(workspace_id: str, request: Request):
    """Unlink a paper. Body: {"citekey": "..."}"""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    body = await request.json()
    citekey = body.get("citekey", "")
    if not citekey:
        return JSONResponse({"success": False, "error": "citekey required."})
    acquire_lock()
    try:
        _state.reload()
        ok = _state.remove_workspace_paper(workspace_id, citekey)
        if not ok:
            return JSONResponse({"success": False, "error": "Paper not found."})
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True})


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str):
    """Delete a project from tracking."""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    # Guard: the Workbench (default) project cannot be deleted.
    ws = _state.get_workspace(workspace_id)
    if ws and ws.get("default"):
        return JSONResponse({"success": False, "error": "The Workbench project cannot be deleted."})
    acquire_lock()
    try:
        _state.reload()
        ok = _state.remove_workspace(workspace_id)
        if not ok:
            return JSONResponse({"success": False, "error": "Project not found."})
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True})


@router.post("/workspaces/{workspace_id}/sessions")
async def launch_coding_session(workspace_id: str, request: Request):
    """Launch a coding session. Body: {"repo": "name", "prompt": "...", "agent": "...", "model": "..."}"""
    _state = _context._state
    from distillate.experiment_tools import launch_coding_session_tool
    body = await request.json()
    return JSONResponse(launch_coding_session_tool(
        state=_state, workspace=workspace_id,
        repo=body.get("repo", ""),
        prompt=body.get("prompt", ""),
        agent=body.get("agent", "claude"),
        model=body.get("model", "")
    ))


@router.post("/workspaces/{workspace_id}/sessions/{session_id}/inject-prompt")
async def inject_session_prompt(
    workspace_id: str, session_id: str, request: Request
):
    """Inject a prompt into a live Claude Code session's TUI and press Enter.

    Body: ``{"prompt": "..."}``. Used by the write-up ⌘K inline-edit flow to
    hand the agent an instruction without the user needing to click into the
    terminal. Reuses the same tmux send-keys mechanism as the session wrap-up
    helper (``_inject_wrapup_prompt`` in workspace_tools.py).
    """
    import subprocess
    import time
    _state = _context._state
    ws = _state.get_workspace(workspace_id)
    if not ws:
        return JSONResponse({"success": False, "error": "Project not found."})
    session = (ws.get("coding_sessions") or {}).get(session_id)
    if not session:
        return JSONResponse({"success": False, "error": "Session not found."})
    tmux_name = session.get("tmux_name", "")
    if not tmux_name:
        return JSONResponse({"success": False, "error": "Session has no tmux name."})

    body = await request.json() if await request.body() else {}
    prompt = body.get("prompt", "") or ""
    if not prompt.strip():
        return JSONResponse({"success": False, "error": "prompt_required"})

    try:
        # -l flag sends literal text (no keybinding parsing) so braces,
        # backslashes etc. in the prompt don't get interpreted by tmux.
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_name, "-l", prompt],
            capture_output=True, timeout=3,
        )
        time.sleep(0.2)  # give Claude Code a beat to render the typed buffer
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_name, "Enter"],
            capture_output=True, timeout=3,
        )
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)})
    return JSONResponse({"success": True})


@router.post("/workspaces/{workspace_id}/canvases/{canvas_id}/sessions")
async def launch_canvas_session(
    workspace_id: str, canvas_id: str, request: Request
):
    """Launch a Claude Code session cwd'd into the canvas directory.

    Reuses ``launch_coding_session_tool`` with ``cwd_override=canvas.dir``
    and ``canvas_id=canvas_id``. The resulting session is linked on the
    canvas record (``canvas.session_id``) and tagged on the session
    record (``session.canvas_id``) so the UI can group them.

    Also sends an agent priming prompt so Claude arrives knowing exactly
    which file the user is editing and what role to take.
    """
    _state = _context._state
    from distillate.experiment_tools import launch_coding_session_tool

    cv = _state.get_workspace_canvas(workspace_id, canvas_id)
    if not cv:
        return JSONResponse({"success": False, "error": "Canvas not found."})

    body = await request.json() if await request.body() else {}
    user_prompt = body.get("prompt", "")

    # Build the priming prompt — this is Claude's FIRST user message and
    # frames the whole session around the single file it's there to help edit.
    entry = cv.get("entry", "")
    cv_type = cv.get("type", "plain")
    title = cv.get("title", "")
    type_label = {
        "latex": "LaTeX source",
        "markdown": "Markdown document",
        "plain": "text document",
        "code": "code project",
        "survey": "literature survey",
        "data": "data analysis",
    }.get(cv_type, "document")
    session_type_map = {
        "latex": "writing", "markdown": "writing", "plain": "writing",
        "code": "coding", "survey": "survey", "data": "data",
    }
    session_type = session_type_map.get(cv_type, "coding")
    if cv_type in ("code", "survey", "data"):
        priming = (
            f"You are opening a {type_label} session called \"{title}\". "
            f"Your working directory is set to this session's folder. "
            + (f"The main file is `{entry}`." if entry else "")
        )
    else:
        priming = (
            f"You are helping me edit a {type_label} called \"{title}\". "
            f"The main file is `{entry}` in your current working directory. "
            f"When I ask for edits, use your Read and Edit tools against that file directly. "
            f"Keep your replies brief — I can see your changes in the editor above this terminal, "
            f"so confirmations of one or two sentences are plenty unless I ask for explanation."
        )
    full_prompt = f"{priming}\n\n{user_prompt}" if user_prompt else priming

    result = launch_coding_session_tool(
        state=_state,
        workspace=workspace_id,
        prompt=full_prompt,
        cwd_override=cv["dir"],
        canvas_id=canvas_id,
        session_type=session_type,
    )
    return JSONResponse(result)


@router.post("/workspaces/{workspace_id}/sessions/{session_id}/stop")
async def stop_coding_session(workspace_id: str, session_id: str):
    """Stop a coding session."""
    _state = _context._state
    from distillate.experiment_tools import stop_coding_session_tool
    result = await asyncio.get_event_loop().run_in_executor(
        _context._executor, lambda: stop_coding_session_tool(
            state=_state, workspace=workspace_id, session=session_id))
    return JSONResponse(result)


@router.post("/workspaces/{workspace_id}/sessions/{session_id}/complete")
async def complete_coding_session(workspace_id: str, session_id: str):
    """Start a session wrap-up by asking the live Claude agent to summarize
    itself.

    Injects a summary prompt into the tmux Claude Code TUI, waits for its
    reply to stabilise, extracts the title + bullets, and returns them as
    ``draft_summary``. The tmux session stays running until the user saves
    via PATCH /summary; calling wrapup/discard clears the draft so the user
    can keep coding.
    """
    _state = _context._state
    from distillate.experiment_tools import complete_coding_session_tool
    result = await asyncio.get_event_loop().run_in_executor(
        _context._executor, lambda: complete_coding_session_tool(
            state=_state, workspace=workspace_id, session=session_id))
    return JSONResponse(result)


@router.post("/workspaces/{workspace_id}/sessions/{session_id}/wrapup/discard")
async def discard_session_wrapup(workspace_id: str, session_id: str):
    """Discard a drafted wrap-up so the user can keep the session running."""
    _state = _context._state
    from distillate.experiment_tools import discard_session_wrapup_tool
    result = await asyncio.get_event_loop().run_in_executor(
        _context._executor, lambda: discard_session_wrapup_tool(
            state=_state, workspace=workspace_id, session=session_id))
    return JSONResponse(result)


@router.patch("/workspaces/{workspace_id}/sessions/{session_id}/summary")
async def save_session_summary(workspace_id: str, session_id: str, request: Request):
    """Save the edited session summary and end the session.

    Kills the tmux session, marks it ``completed``, persists the edited
    summary, and appends entries to the lab notebook + project notes. Body:
    ``{"summary": "..."}``.
    """
    _state = _context._state
    from distillate.experiment_tools import save_session_summary_tool
    body = await request.json()
    result = await asyncio.get_event_loop().run_in_executor(
        _context._executor, lambda: save_session_summary_tool(
            state=_state, workspace=workspace_id, session=session_id,
            summary=body.get("summary", "")))
    return JSONResponse(result)


@router.post("/workspaces/{workspace_id}/sessions/{session_id}/restart")
async def restart_coding_session(workspace_id: str, session_id: str):
    """Restart a coding session (kill + resume)."""
    _state = _context._state
    from distillate.experiment_tools import restart_coding_session_tool
    result = await asyncio.get_event_loop().run_in_executor(
        _context._executor, lambda: restart_coding_session_tool(
            state=_state, workspace=workspace_id, session=session_id))
    return JSONResponse(result)


@router.post("/workspaces/{workspace_id}/sessions/{session_id}/recover")
async def recover_coding_session(workspace_id: str, session_id: str):
    """Recover a lost coding session."""
    _state = _context._state
    from distillate.experiment_tools import recover_coding_session_tool
    return JSONResponse(recover_coding_session_tool(
        state=_state, workspace=workspace_id, session=session_id))


@router.post("/workspaces/recover")
async def recover_all_sessions():
    """Recover ALL lost coding sessions across all workspaces."""
    _state = _context._state
    from distillate.experiment_tools import recover_all_sessions_tool
    result = await asyncio.get_event_loop().run_in_executor(
        _context._executor, lambda: recover_all_sessions_tool(state=_state))
    return JSONResponse(result)


@router.post("/workspaces/{workspace_id}/sessions/stop-all")
async def stop_all_sessions(workspace_id: str):
    """Stop all non-working sessions in a workspace."""
    _state = _context._state
    from distillate.experiment_tools import stop_all_sessions_tool
    result = await asyncio.get_event_loop().run_in_executor(
        _context._executor, lambda: stop_all_sessions_tool(
            state=_state, workspace=workspace_id))
    return JSONResponse(result)


@router.get("/workspaces/{workspace_id}/notes")
async def get_workspace_notes(workspace_id: str):
    """Get project notes."""
    _state = _context._state
    from distillate.experiment_tools import get_workspace_notes_tool
    return JSONResponse(get_workspace_notes_tool(state=_state, workspace=workspace_id))


@router.put("/workspaces/{workspace_id}/notes")
async def save_workspace_notes(workspace_id: str, request: Request):
    """Save project notes. Body: {"content": "markdown..."}"""
    _state = _context._state
    from distillate.experiment_tools import save_workspace_notes_tool
    body = await request.json()
    return JSONResponse(save_workspace_notes_tool(
        state=_state, workspace=workspace_id, content=body.get("content", "")))


@router.post("/workspaces/{workspace_id}/log")
async def append_workspace_log(workspace_id: str, request: Request):
    """Append to lab notebook. Body: {"entry": "...", "entry_type": "note"}"""
    _state = _context._state
    from distillate.experiment_tools import append_lab_book_tool
    body = await request.json()
    return JSONResponse(append_lab_book_tool(state=_state, workspace=workspace_id, **body))


@router.get("/workspaces/{workspace_id}/sessions")
async def list_coding_sessions(workspace_id: str):
    """List all coding sessions for a workspace."""
    _state = _context._state
    from distillate.experiment_tools import get_workspace_tool
    result = get_workspace_tool(state=_state, workspace=workspace_id)
    if not result.get("success"):
        return JSONResponse(result)
    return JSONResponse({
        "sessions": result["workspace"].get("sessions", []),
    })


@router.put("/workspaces/{workspace_id}/sessions/reorder")
async def reorder_sessions(workspace_id: str, request: Request):
    """Reorder coding sessions. Body: {"session_ids": ["id1", "id2", ...]}"""
    _state = _context._state
    from distillate.experiment_tools import reorder_sessions_tool
    body = await request.json()
    return JSONResponse(reorder_sessions_tool(
        state=_state, workspace=workspace_id, session_ids=body.get("session_ids", [])))


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(workspace_id: str, request: Request):
    """Update project fields. Body: any of {name, description, tags, status}"""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    body = await request.json()
    allowed = {"name", "description", "tags", "status", "root_path"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        return JSONResponse({"success": False, "error": "No valid fields to update."})
    acquire_lock()
    try:
        _state.reload()
        ws = _state.get_workspace(workspace_id)
        if not ws:
            return JSONResponse({"success": False, "error": f"Project not found: {workspace_id}"})
        _state.update_workspace(workspace_id, **updates)
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True})


@router.post("/workspaces/{workspace_id}/resources")
async def add_workspace_resource(workspace_id: str, request: Request):
    """Add a resource link. Body: {"type": "...", "name": "...", "url": "..."}"""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    body = await request.json()
    acquire_lock()
    try:
        _state.reload()
        ok = _state.add_workspace_resource(workspace_id, body)
        if not ok:
            return JSONResponse({"success": False, "error": "Project not found."})
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True})


@router.delete("/workspaces/{workspace_id}/resources/{index}")
async def remove_workspace_resource(workspace_id: str, index: int):
    """Remove a resource by index."""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    acquire_lock()
    try:
        _state.reload()
        ok = _state.remove_workspace_resource(workspace_id, index)
        if not ok:
            return JSONResponse({"success": False, "error": "Not found."})
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True})


@router.post("/workspaces/{workspace_id}/papers")
async def link_workspace_paper(workspace_id: str, request: Request):
    """Link a paper. Body: {"citekey": "..."}"""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    body = await request.json()
    citekey = body.get("citekey", "")
    if not citekey:
        return JSONResponse({"success": False, "error": "citekey required."})
    acquire_lock()
    try:
        _state.reload()
        ok = _state.add_workspace_paper(workspace_id, citekey)
        if not ok:
            return JSONResponse({"success": False, "error": "Project not found or paper already linked."})
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True})


@router.get("/workspaces/{workspace_id}/writeup")
async def get_workspace_writeup(workspace_id: str):
    """Get write-up content and metadata."""
    _state = _context._state
    ws = _state.get_workspace(workspace_id)
    if not ws:
        return JSONResponse({"success": False, "error": "Project not found."})
    writeup = ws.get("writeup")
    if not writeup or not writeup.get("path"):
        return JSONResponse({"success": True, "exists": False, "content": "", "format": ""})
    from pathlib import Path
    path = Path(writeup["path"])
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return JSONResponse({
        "success": True,
        "exists": True,
        "format": writeup.get("format", "markdown"),
        "content": content,
        "path": str(path),
    })


@router.put("/workspaces/{workspace_id}/writeup")
async def save_workspace_writeup(workspace_id: str, request: Request):
    """Save write-up. Body: {"content": "...", "format": "markdown"|"latex"}"""
    _state = _context._state
    from distillate.state import acquire_lock, release_lock
    from pathlib import Path
    body = await request.json()
    content = body.get("content", "")
    fmt = body.get("format", "markdown")
    ext = ".md" if fmt == "markdown" else ".tex"

    ws = _state.get_workspace(workspace_id)
    if not ws:
        return JSONResponse({"success": False, "error": "Project not found."})

    # Determine write-up path — prefer root_path, fallback to notes dir
    writeup_info = ws.get("writeup")
    if writeup_info and writeup_info.get("path"):
        path = Path(writeup_info["path"])
    else:
        root = ws.get("root_path") or ws.get("notes_path", "")
        if not root:
            # Create in knowledge dir as fallback
            from distillate.lab_notebook import _KB_DIR
            root = str(_KB_DIR / "wiki" / "projects" / ws["id"])
            Path(root).mkdir(parents=True, exist_ok=True)
        path = Path(root) / f"WRITEUP{ext}"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    acquire_lock()
    try:
        _state.reload()
        _state.set_workspace_writeup(workspace_id, fmt, str(path))
        _state.save()
    finally:
        release_lock()
    return JSONResponse({"success": True, "path": str(path)})


@router.post("/workspaces/{workspace_id}/writeup/generate")
async def generate_workspace_writeup(workspace_id: str, request: Request):
    """Generate a write-up draft from project data. Body: {"format": "markdown"|"latex", "sections": [...]}"""
    _state = _context._state
    import anthropic
    body = await request.json()
    fmt = body.get("format", "markdown")
    sections = body.get("sections", ["abstract", "introduction", "methods", "results", "discussion"])

    ws = _state.get_workspace(workspace_id)
    if not ws:
        return JSONResponse({"success": False, "error": "Project not found."})

    # Gather project context
    context_parts = [f"# Project: {ws.get('name', '')}"]
    if ws.get("description"):
        context_parts.append(f"Description: {ws['description']}")
    if ws.get("tags"):
        context_parts.append(f"Tags: {', '.join(ws['tags'])}")

    # Linked experiments
    experiments = _state.experiments_for_workspace(workspace_id)
    if experiments:
        context_parts.append("\n## Experiments")
        for exp in experiments:
            context_parts.append(f"\n### {exp.get('name', exp['id'])}")
            if exp.get("description"):
                context_parts.append(f"Description: {exp['description']}")
            for run_id, run in exp.get("runs", {}).items():
                status = run.get("decision", run.get("status", ""))
                results = run.get("results", {})
                hyp = run.get("hypothesis", "")
                context_parts.append(
                    f"- Run {run.get('name', run_id)} [{status}]: "
                    f"{'hypothesis: ' + hyp + ' | ' if hyp else ''}"
                    f"results: {results}"
                )

    # Linked papers
    papers = ws.get("linked_papers", [])
    if papers:
        context_parts.append("\n## Linked Papers")
        for ck in papers:
            doc = _state.find_document(ck) if hasattr(_state, "find_document") else None
            if doc:
                context_parts.append(f"- {doc.get('title', ck)} ({doc.get('authors', '')})")
            else:
                context_parts.append(f"- {ck}")

    context = "\n".join(context_parts)

    format_instruction = (
        "Write in Markdown format with ## headings."
        if fmt == "markdown" else
        "Write in LaTeX format suitable for a conference paper. "
        "Use \\section{}, \\subsection{}, etc. Include a \\begin{document} preamble."
    )

    prompt = (
        f"You are a scientific writing assistant. Based on the following project data, "
        f"generate a draft write-up covering these sections: {', '.join(sections)}.\n\n"
        f"{format_instruction}\n\n"
        f"Project context:\n{context}\n\n"
        f"Generate a well-structured draft. Be specific about methods and results where data is available. "
        f"For sections without data, add placeholder text with [TODO] markers."
    )

    async def stream():
        client = anthropic.Anthropic()
        with client.messages.stream(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        ) as stream_resp:
            for text in stream_resp.text_stream:
                yield text

    return StreamingResponse(stream(), media_type="text/plain")
