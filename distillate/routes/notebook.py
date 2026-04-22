"""Lab Notebook — REST endpoints for reading and writing notebook entries."""

import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from distillate.routes import _context

log = logging.getLogger(__name__)

router = APIRouter()


_SESSION_HEADER_RE = re.compile(
    r"^###\s+Session:\s+(.+?)\s+\(completed\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\)\s*$",
    re.MULTILINE,
)

# The noise filter used for the in-app feed is shared with the Obsidian
# vault regenerator — both surfaces show the same curated set. See
# ``distillate.lab_notebook._NOISE_PATTERNS`` for the pattern list.
from distillate.lab_notebook import is_noise_entry as _is_noise  # noqa: E402


def _find_workspace_session_block(
    date: str, time: str, session_name: str = ""
) -> tuple | None:
    """Locate a workspace session block by date+time across all workspaces.

    Returns ``(notes_file, header_match, body_start, body_end)`` for the matching
    block, or ``None`` if not found. Used by the edit/delete endpoints.
    """
    from distillate.experiment_tools.workspace_tools import _get_notes_dir

    state = getattr(_context, "_state", None)
    if state is None or not getattr(state, "workspaces", None):
        return None

    for ws_id in state.workspaces:
        try:
            _, notes_dir, err = _get_notes_dir(state, ws_id)
            if err or not notes_dir:
                continue
            notes_file = notes_dir / "notes.md"
            if not notes_file.exists():
                continue
            content = notes_file.read_text(encoding="utf-8")
            matches = list(_SESSION_HEADER_RE.finditer(content))
            for i, m in enumerate(matches):
                if m.group(2) != date or m.group(3) != time:
                    continue
                if session_name and m.group(1).strip() != session_name:
                    continue
                body_start = m.end()
                body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                return (notes_file, m, body_start, body_end, content)
        except Exception:
            log.exception("Failed scanning workspace notes for %s", ws_id)
    return None


def _collect_workspace_session_entries() -> list[dict]:
    """Parse session blocks from every workspace's notes.md file.

    Returns entries in the same shape as ``read_recent_parsed`` so they can be
    merged with lab-notebook entries.
    """
    from distillate.experiment_tools.workspace_tools import _get_notes_dir

    state = getattr(_context, "_state", None)
    if state is None or not getattr(state, "workspaces", None):
        return []

    entries: list[dict] = []
    for ws_id, ws in state.workspaces.items():
        try:
            _, notes_dir, err = _get_notes_dir(state, ws_id)
            if err or not notes_dir:
                continue
            notes_file = notes_dir / "notes.md"
            if not notes_file.exists():
                continue
            content = notes_file.read_text(encoding="utf-8")
            matches = list(_SESSION_HEADER_RE.finditer(content))
            for i, m in enumerate(matches):
                session_name = m.group(1).strip()
                date = m.group(2)
                time = m.group(3)
                body_start = m.end()
                body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                body = content[body_start:body_end].strip()
                # Use the body as the main text, with the session name as a prefix
                text = f'Completed "{session_name}": {body}' if body else f'Completed "{session_name}"'
                project_name = ws.get("name") or ws_id
                entries.append({
                    "date": date,
                    "time": time,
                    "type": "session",
                    "text": text,
                    "tags": [project_name] if project_name else [],
                    "source": "workspace",
                    "session_name": session_name,
                    "workspace_id": ws_id,
                })
        except Exception:
            log.exception("Failed to parse workspace notes for %s", ws_id)
    return entries


@router.get("/notebook")
async def get_notebook(n: int = 20, date: str = "", project: str = ""):
    """Read recent lab notebook entries."""
    from distillate.lab_notebook import read_recent

    result = read_recent(n=n, date=date, project=project)
    return JSONResponse(result)


@router.post("/notebook")
async def post_notebook(request: Request):
    """Append an entry to the lab notebook.

    Body: {"entry": "...", "entry_type": "note", "project": "..."}
    """
    from distillate.lab_notebook import append_entry

    body = await request.json()
    entry = body.get("entry", "")
    if not entry:
        return JSONResponse({"success": False, "error": "entry is required"}, status_code=400)

    result = append_entry(
        entry=entry,
        entry_type=body.get("entry_type", "note"),
        project=body.get("project", ""),
        tags=body.get("tags"),
    )
    return JSONResponse(result)


@router.get("/notebook/entries")
async def get_notebook_entries(n: int = 100, date: str = "", project: str = ""):
    """Read recent lab notebook entries as parsed structured dicts.

    Merges entries from two sources:
      1. Lab notebook daily files (auto-captured + manual notes)
      2. Per-workspace ``notes.md`` session blocks (rich session summaries)

    Returns entries in reverse-chronological order. Filters out noisy
    auto-captured entries like agent session start/stop pairs.
    """
    from distillate.lab_notebook import read_recent_parsed

    # Source 1: lab notebook entries (over-fetch, we'll filter noise next)
    nb_result = read_recent_parsed(n=n * 4, date=date, project=project)
    nb_entries = [e for e in nb_result.get("entries", []) if not _is_noise(e)]
    for e in nb_entries:
        e["source"] = "notebook"

    # Source 2: workspace notes session blocks (always include for unified view)
    ws_entries = _collect_workspace_session_entries()

    # Filter workspace entries by date if requested
    if date:
        ws_entries = [e for e in ws_entries if e["date"] == date]

    # Filter workspace entries by project if requested
    if project:
        from distillate.lab_notebook import _slug_tag
        proj_slug = _slug_tag(project)
        ws_entries = [
            e for e in ws_entries
            if project in e.get("tags", [])
            or proj_slug in [_slug_tag(t) for t in e.get("tags", [])]
        ]

    # De-dupe: notebook auto-capture writes truncated session summaries that
    # also appear (in full) in workspace notes. Drop the notebook copy when
    # a workspace entry exists at the same date+time.
    ws_keys = {(e["date"], e["time"]) for e in ws_entries}
    nb_filtered = []
    for e in nb_entries:
        if e.get("type") == "session" and (e.get("date"), e.get("time")) in ws_keys:
            continue
        nb_filtered.append(e)

    merged = nb_filtered + ws_entries

    # Sort newest first by (date, time)
    merged.sort(key=lambda e: (e.get("date", ""), e.get("time", "")), reverse=True)

    # Trim to n
    merged = merged[:n]

    # Stamp each entry with its current pin state so the frontend can render
    # the pin toggle without a second round-trip.
    from distillate.lab_notebook import load_pins, pin_key
    pins = load_pins()
    for e in merged:
        key = pin_key(
            e.get("source", "notebook"),
            e.get("date", ""),
            e.get("time", ""),
            e.get("session_name", ""),
        )
        e["pinned"] = key in pins

    # Compute combined dates_covered
    dates_covered = sorted({e["date"] for e in merged}, reverse=True)

    return JSONResponse({
        "entries": merged,
        "dates_covered": dates_covered,
        "total": len(merged),
    })


@router.post("/notebook/pin")
async def toggle_notebook_pin(request: Request):
    """Toggle the pin state of an entry.

    Body: ``{"source": "notebook"|"workspace", "date": "YYYY-MM-DD",
             "time": "HH:MM", "session_name": "..."}`` — session_name is only
    required for workspace entries. Returns ``{"success": True, "pinned": bool}``
    with the new pin state.
    """
    from distillate.lab_notebook import toggle_pin as _toggle_pin

    body = await request.json()
    date = body.get("date", "")
    time = body.get("time", "")
    if not date or not time:
        return JSONResponse({"success": False, "error": "date and time required"}, status_code=400)
    state = _toggle_pin(
        source=body.get("source", "notebook"),
        date=date,
        time=time,
        session_name=body.get("session_name", ""),
    )
    return JSONResponse({"success": True, "pinned": state})


@router.get("/notebook/dates")
async def notebook_dates():
    """List dates that have notebook entries (for calendar navigation).

    Includes dates from both lab notebook daily files AND workspace notes
    session blocks, so navigation works for either source.
    """
    from distillate.lab_notebook import NOTEBOOK_ROOT

    dates: set[str] = set()
    if NOTEBOOK_ROOT.exists():
        for md_file in NOTEBOOK_ROOT.glob("*/*/*.md"):
            if md_file.stem != "index":
                dates.add(md_file.stem)

    for entry in _collect_workspace_session_entries():
        dates.add(entry["date"])

    sorted_dates = sorted(dates, reverse=True)
    return JSONResponse({"dates": sorted_dates[:90]})  # last ~3 months


@router.patch("/notebook/entry")
async def patch_notebook_entry(request: Request):
    """Edit an existing entry.

    Body shape:
      {
        "source": "notebook" | "workspace",
        "date": "YYYY-MM-DD",     # in UTC (matches what /notebook/entries returned)
        "time": "HH:MM",          # in UTC
        "session_name": "...",    # workspace source only — disambiguates blocks
        # Lab notebook update fields:
        "text": "new entry text",
        "entry_type": "note",
        "project": "project name",
        "tags": ["tag1", "tag2"],
        # Workspace update fields:
        "title": "new session title",
        "body": "new session body markdown",
      }
    """
    body = await request.json()
    source = body.get("source", "notebook")
    date = body.get("date", "")
    time = body.get("time", "")
    if not date or not time:
        return JSONResponse({"success": False, "error": "date and time required"}, status_code=400)

    if source == "notebook":
        from distillate.lab_notebook import update_entry

        text = body.get("text", "")
        if not text:
            return JSONResponse({"success": False, "error": "text required"}, status_code=400)
        result = update_entry(
            date=date,
            time=time,
            new_text=text,
            entry_type=body.get("entry_type", "note"),
            project=body.get("project", ""),
            tags=body.get("tags"),
        )
        return JSONResponse(result)

    if source == "workspace":
        found = _find_workspace_session_block(date, time, body.get("session_name", ""))
        if not found:
            return JSONResponse({"success": False, "error": "Session block not found"}, status_code=404)
        notes_file, header_match, body_start, body_end, content = found

        new_title = body.get("title", header_match.group(1).strip())
        new_body = body.get("body", "")
        new_header = f"### Session: {new_title} (completed {date} {time})"
        # Preserve a blank line between header and body
        new_block = f"{new_header}\n\n{new_body.strip()}\n\n"
        new_content = content[: header_match.start()] + new_block + content[body_end:].lstrip("\n")
        notes_file.write_text(new_content, encoding="utf-8")
        return JSONResponse({"success": True, "path": str(notes_file)})

    return JSONResponse({"success": False, "error": f"Unknown source: {source}"}, status_code=400)


@router.delete("/notebook/entry")
async def delete_notebook_entry(request: Request):
    """Delete an entry.

    Body shape:
      {
        "source": "notebook" | "workspace",
        "date": "YYYY-MM-DD",
        "time": "HH:MM",
        "session_name": "...",  # workspace only
      }
    """
    body = await request.json()
    source = body.get("source", "notebook")
    date = body.get("date", "")
    time = body.get("time", "")
    if not date or not time:
        return JSONResponse({"success": False, "error": "date and time required"}, status_code=400)

    if source == "notebook":
        from distillate.lab_notebook import delete_entry

        result = delete_entry(date=date, time=time)
        return JSONResponse(result)

    if source == "workspace":
        found = _find_workspace_session_block(date, time, body.get("session_name", ""))
        if not found:
            return JSONResponse({"success": False, "error": "Session block not found"}, status_code=404)
        notes_file, header_match, _body_start, body_end, content = found
        new_content = content[: header_match.start()] + content[body_end:].lstrip("\n")
        notes_file.write_text(new_content, encoding="utf-8")
        return JSONResponse({"success": True, "path": str(notes_file)})

    return JSONResponse({"success": False, "error": f"Unknown source: {source}"}, status_code=400)


# ---------------------------------------------------------------------------
# Events stream (v2 Phase 5) — unified timeline powering the Notebook view
# ---------------------------------------------------------------------------

@router.get("/events")
async def list_events(
    experiment_id: str = "",
    event_type: str = "",
    since: str = "",
    limit: int = 100,
):
    """Query the unified event stream. Powers the Notebook timeline view."""
    _state = _context._state
    event_types = [t.strip() for t in event_type.split(",") if t.strip()] if event_type else None
    events = _state.query_events(
        experiment_id=experiment_id,
        event_types=event_types,
        since=since,
        limit=min(limit, 500),
    )
    return JSONResponse({"events": events, "total": len(events)})


@router.post("/events")
async def create_manual_note_event(request: Request):
    """Create a manual note event (the "+ Note" button in the Notebook view)."""
    from distillate.events import emit_event, MANUAL_NOTE
    _state = _context._state
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"success": False, "error": "text required"})
    event = emit_event(
        _state,
        MANUAL_NOTE,
        experiment_id=body.get("experiment_id"),
        payload={"text": text, "entry_type": body.get("entry_type", "note")},
        tags=body.get("tags", []),
    )
    _state.save()
    return JSONResponse({"success": True, "event": event.to_dict()})
