"""Workspace and coding session tools."""

import logging
import os
import re
import shlex
from pathlib import Path as _Path

from ._helpers import _resolve_project

log = logging.getLogger(__name__)


def _evict_stale_session_lock(session_id: str) -> None:
    """Remove stale Claude Code session lock files for a given session ID.

    Claude Code writes ~/.claude/sessions/<pid>.json when a session is active.
    If the process crashes, the file persists and any subsequent --session-id
    or --resume for that ID fails with "Session ID X is already in use".
    """
    import glob as _glob
    import json as _json
    import signal as _signal

    sessions_dir = os.path.expanduser("~/.claude/sessions")
    try:
        for path in _glob.glob(os.path.join(sessions_dir, "*.json")):
            try:
                data = _json.loads(open(path, "rb").read())
                if data.get("sessionId") == session_id:
                    pid = data.get("pid")
                    if pid:
                        try:
                            os.kill(pid, _signal.SIGTERM)
                        except (ProcessLookupError, PermissionError):
                            pass
                    os.unlink(path)
                    log.info("Evicted stale session lock %s (pid=%s)", path, pid)
                    return
            except (OSError, ValueError, KeyError):
                continue
    except OSError:
        pass


def _start_transcript_logging(tmux_name: str, repo_path: str, session_id: str) -> str | None:
    """Start continuous pipe-pane logging of the tmux session to a file.

    Writes all terminal output (including ANSI escapes) to
    ``$CONFIG_DIR/transcripts/<session_id>.log`` (typically
    ``~/.config/distillate/transcripts/``) via tmux's native pipe-pane
    mechanism — no polling, zero data loss.

    Kept outside the user's repo so we don't pollute their git tree.
    The ``repo_path`` argument is unused today but retained for a future
    manifest that maps session_id → repo.

    Returns the transcript path, or None if setup failed.
    """
    import subprocess
    from distillate.config import CONFIG_DIR
    try:
        transcript_dir = CONFIG_DIR / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / f"{session_id}.log"
        subprocess.run(
            ["tmux", "pipe-pane", "-t", tmux_name,
             f"cat >> {shlex.quote(str(transcript_path))}"],
            capture_output=True, timeout=3,
        )
        return str(transcript_path)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("transcript logging setup failed for %s: %s", tmux_name, e)
        return None

SCHEMAS = [
    {
        "name": "create_workspace",
        "description": (
            "Create a workspace project — a top-level container for repos, "
            "coding sessions, and notes. Unlike experiments, workspaces don't "
            "have PROMPT.md or auto-research loops. Use for software projects, "
            "writing, or any work that isn't a tracked experiment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Project name",
                },
                "description": {
                    "type": "string",
                    "description": "What this project is about",
                },
                "repos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Absolute paths to linked repositories",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_workspaces",
        "description": (
            "List all workspace projects with their repos, active sessions, "
            "and status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_workspace",
        "description": "Get details of a workspace project including repos and sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
            },
            "required": ["workspace"],
        },
    },
    {
        "name": "add_workspace_repo",
        "description": "Link a repository to a workspace project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute path to the repository",
                },
                "name": {
                    "type": "string",
                    "description": "Display name for the repo (default: directory name)",
                },
            },
            "required": ["workspace", "path"],
        },
    },
    {
        "name": "launch_coding_session",
        "description": (
            "USE WHEN the user asks to start, open, or launch a coding "
            "session in a project / workspace (e.g. 'start a coding "
            "session in [project]', 'let's code on X'). Opens an "
            "interactive CLI agent (Claude Code or Gemini CLI) in a "
            "tmux session. Default agent is 'claude'. "
            "If the user's message includes a [Context: ...] block "
            "with an active project ID, use that as the workspace "
            "parameter directly — no need to call list_workspaces first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
                "repo": {
                    "type": "string",
                    "description": "Repo name or path (must be linked to the workspace)",
                },
                "prompt": {
                    "type": "string",
                    "description": "Initial prompt/task for the coding session",
                },
                "agent": {
                    "type": "string",
                    "enum": ["claude", "gemini"],
                    "description": "Agent to use (default: 'claude')",
                },
                "work_item_id": {
                    "type": "string",
                    "description": "Work item (canvas) id to attach this session to",
                },
            },
            "required": ["workspace"],
        },
    },
    {
        "name": "launch_writing_session",
        "description": (
            "USE WHEN the user asks to open a writing session: drafting documents, "
            "papers, reports, or any prose work in a workspace. Same infrastructure "
            "as a coding session but tagged as 'writing' for Canvas integration."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
                "repo": {
                    "type": "string",
                    "description": "Repo name or path (must be linked to the workspace)",
                },
                "prompt": {
                    "type": "string",
                    "description": "Initial writing task or document to work on",
                },
                "agent": {
                    "type": "string",
                    "enum": ["claude", "gemini"],
                    "description": "Agent to use (default: 'claude')",
                },
                "canvas_id": {
                    "type": "string",
                    "description": "Canvas (work item) id to attach this session to",
                },
            },
            "required": ["workspace"],
        },
    },
    {
        "name": "launch_survey_session",
        "description": (
            "USE WHEN the user asks to open a survey or literature review session: "
            "surveying papers, datasets, web sources, or synthesizing prior work. "
            "Same infrastructure as a coding session but tagged as 'survey'. "
            "Prefer this over launch_coding_session for literature / knowledge work."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
                "repo": {
                    "type": "string",
                    "description": "Repo name or path (must be linked to the workspace)",
                },
                "prompt": {
                    "type": "string",
                    "description": "Initial survey question or topic",
                },
                "agent": {
                    "type": "string",
                    "enum": ["claude", "gemini"],
                    "description": "Agent to use (default: 'claude')",
                },
                "work_item_id": {
                    "type": "string",
                    "description": "Work item id to attach this session to",
                },
            },
            "required": ["workspace"],
        },
    },
    {
        "name": "create_work_item",
        "description": (
            "Create a persistent Work Session in a workspace — a deliverable-oriented "
            "work item of type code, write, survey, or data. Use this before launching "
            "a session when the work should produce a tracked, named deliverable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
                "title": {
                    "type": "string",
                    "description": "Work item title (e.g. 'ICML paper draft', 'batch-norm sweep')",
                },
                "type": {
                    "type": "string",
                    "enum": ["code", "write", "survey", "data"],
                    "description": "Type of work: code (feature/script), write (document/paper), survey (literature review), data (dataset/analysis)",
                },
                "artifact_path": {
                    "type": "string",
                    "description": "Directory where the deliverable will live (defaults to workspace root or first repo)",
                },
                "artifact_entry": {
                    "type": "string",
                    "description": "Primary filename within artifact_path (e.g. 'paper.tex', 'main.py', 'survey.md')",
                },
                "branch": {
                    "type": "string",
                    "description": "Git branch name (code type)",
                },
                "template": {
                    "type": "string",
                    "description": "Template identifier (survey/data types)",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of the work item",
                },
            },
            "required": ["workspace", "title", "type"],
        },
    },
    {
        "name": "list_work_items",
        "description": "List work sessions (deliverable-oriented work items) in a workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
                "type": {
                    "type": "string",
                    "description": "Filter by type: code, write, survey, data",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status (default: active). Use 'all' to see everything.",
                },
            },
            "required": ["workspace"],
        },
    },
    {
        "name": "complete_work_item",
        "description": "Mark a work session as done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
                "work_item_id": {
                    "type": "string",
                    "description": "Work item (canvas) id to mark as done",
                },
            },
            "required": ["workspace", "work_item_id"],
        },
    },
    {
        "name": "stop_coding_session",
        "description": "Stop a running coding session by killing its tmux session.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Workspace id or name substring"},
                "session": {"type": "string", "description": "Coding session id (e.g. coding_001)"},
            },
            "required": ["workspace", "session"],
        },
    },
    {
        "name": "restart_coding_session",
        "description": "Restart a coding session: kill old tmux, spawn new with --resume to preserve context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Workspace id or name substring"},
                "session": {"type": "string", "description": "Coding session id (e.g. coding_001)"},
            },
            "required": ["workspace", "session"],
        },
    },
    {
        "name": "recover_coding_session",
        "description": (
            "Recover a lost coding session whose tmux died. "
            "Relaunches the same tmux session and resumes the Claude conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Workspace id or name substring"},
                "session": {"type": "string", "description": "Coding session id (e.g. coding_001)"},
            },
            "required": ["workspace", "session"],
        },
    },
    {
        "name": "recover_all_sessions",
        "description": "Recover ALL lost coding sessions across all workspaces. Idempotent — skips sessions whose tmux is still alive.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "stop_all_sessions",
        "description": "Stop ALL non-working coding sessions in a workspace. Kills tmux and marks them ended. Skips actively working sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Workspace id or name substring"},
            },
            "required": ["workspace"],
        },
    },
    {
        "name": "get_workspace_notes",
        "description": (
            "Read the notes for a workspace. Returns the markdown "
            "content of the workspace's notes.md file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
            },
            "required": ["workspace"],
        },
    },
    {
        "name": "save_workspace_notes",
        "description": (
            "Save updated notes for a workspace. Overwrites the "
            "notes.md file with the provided content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or name substring",
                },
                "content": {
                    "type": "string",
                    "description": "Full markdown content to save",
                },
            },
            "required": ["workspace", "content"],
        },
    },
    {
        "name": "append_lab_book",
        "description": (
            "Append an entry to the lab notebook (chronological research journal). "
            "Entries are timestamped automatically and written to today's daily page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entry": {
                    "type": "string",
                    "description": "The log entry text",
                },
                "entry_type": {
                    "type": "string",
                    "enum": [
                        "note", "observation", "decision", "milestone",
                        "session", "experiment", "paper", "run_completed",
                    ],
                    "description": "Type of entry (default: note)",
                },
                "project": {
                    "type": "string",
                    "description": "Project or workspace name to tag the entry with",
                },
                "workspace": {
                    "type": "string",
                    "description": "Alias for project (backward compat)",
                },
            },
            "required": ["entry"],
        },
    },
    {
        "name": "read_lab_notebook",
        "description": (
            "Read recent lab notebook entries. Returns timestamped entries "
            "from the chronological research journal, optionally filtered by "
            "date or project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Number of entries to return (default: 20)",
                },
                "date": {
                    "type": "string",
                    "description": "Specific date to read (YYYY-MM-DD). If omitted, reads most recent.",
                },
                "project": {
                    "type": "string",
                    "description": "Filter entries by project tag",
                },
            },
        },
    },
    {
        "name": "notebook_digest",
        "description": (
            "Generate a weekly research digest from lab notebook entries. "
            "Summarizes activity by type and project over the last N days."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to include (default: 7)",
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Implementation functions
# ---------------------------------------------------------------------------

def _find_workspace(state, query: str):
    """Find a workspace by id or name substring."""
    ws = state.get_workspace(query)
    if ws:
        return ws
    for ws_id, ws in state.workspaces.items():
        if query.lower() in ws.get("name", "").lower():
            return ws
    return None


def create_workspace_tool(*, state, name: str, description: str = "",
                          repos: list[str] | None = None,
                          root_path: str = "",
                          tags: list[str] | None = None) -> dict:
    """Create a workspace project."""
    from distillate.experiments import slugify
    from distillate.state import acquire_lock, release_lock
    from pathlib import Path as _Path

    workspace_id = slugify(name)

    acquire_lock()
    try:
        state.reload()
        if state.get_workspace(workspace_id):
            return {"success": False, "error": f"Workspace '{name}' already exists."}

        repo_list = []
        for rp in (repos or []):
            p = _Path(rp).expanduser().resolve()
            if p.is_dir():
                repo_list.append({"path": str(p), "name": p.name})

        state.add_workspace(
            workspace_id=workspace_id,
            name=name,
            description=description,
            repos=repo_list,
            root_path=root_path,
            tags=tags,
        )

        # Create project notes directory
        from distillate.lab_notebook import _KB_DIR
        kb_root = _KB_DIR / "wiki" / "projects"
        notes_dir = kb_root / workspace_id
        notes_dir.mkdir(parents=True, exist_ok=True)
        notes_file = notes_dir / "notes.md"
        log_file = notes_dir / "log.md"
        if not notes_file.exists():
            notes_file.write_text(f"# {name}\n\n", encoding="utf-8")
        if not log_file.exists():
            log_file.write_text(f"# {name} — Lab Notebook\n\n", encoding="utf-8")
        state.update_workspace(workspace_id, notes_path=str(notes_dir))

        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "workspace_id": workspace_id,
        "name": name,
        "repos": len(repo_list),
        "message": f"Created workspace '{name}' with {len(repo_list)} repo(s).",
    }


import re as _re

_STATUS_BAR_RE = _re.compile(r'shift\+tab|ctrl\+|esc to|to cycle|to expand|to interrupt|to hide|to approve')
_QUESTION_RE = _re.compile(r'\?\s*$')
# Direct question phrases — agent asking user for a decision (can appear mid-line)
_ASK_PHRASE_RE = _re.compile(r'(?i)\b(want me to|should I|do you want|would you like|shall I|ready to)\b')
# A real idle prompt: ❯ alone on a line with only trailing whitespace.
# NOT a user message (❯ Fantastic...) or option selector (❯ 1. Yes...)
_IDLE_PROMPT_RE = _re.compile(r'^\s*\u276f\s*$')
# Loosened variant: ❯ alone OR ❯ followed by text that is NOT an option
# selector (rules out "❯ 1. Yes", "❯ 2) No"). Used by `_has_idle_prompt` on
# the LAST ❯ line only, so history user messages in scrollback don't confuse
# the classifier — only the current prompt's state matters.
_IDLE_OR_TYPING_RE = _re.compile(r'^\s*[\u276f>$](\s*$|\s+(?!\d+[.)]))')
# Strip leading non-printable / formatting chars before detecting spinner
_TITLE_PREFIX_RE = _re.compile(r'^[\s\x00-\x1f]*')


def _detect_spinner(title: str) -> str:
    """Identify the leading spinner glyph in a tmux pane title.

    Returns one of: "working" (braille), "idle_or_waiting" (✳ or similar), or "" (none).
    Robust to leading whitespace and control characters.
    """
    if not title:
        return ""
    # Strip leading whitespace/control chars
    stripped = _TITLE_PREFIX_RE.sub("", title)
    if not stripped:
        return ""
    first = stripped[0]
    # Braille patterns (U+2800 to U+28FF)
    if "\u2800" <= first <= "\u28ff":
        return "working"
    # Claude/Gemini idle symbols: ✳ (\u2733), ❯ (\u276f), or similar
    if first in ("\u2733", "\u276f", "\u2b24", "\u25cf"):
        return "idle_or_waiting"
    return ""


def _has_idle_prompt(pane_text: str) -> bool:
    """True if the CURRENT prompt line is idle (empty) or the user is typing.

    Examines the LAST line containing common prompt characters (❯, >, $).
    """
    last_prompt_line = None
    prompt_chars = ("\u276f", "> ", "$ ")
    for line in pane_text.splitlines():
        if any(c in line for c in prompt_chars):
            last_prompt_line = line
    if last_prompt_line is None:
        return False
    # If it ends with a prompt char and nothing but whitespace, it's idle
    for c in prompt_chars:
        if last_prompt_line.strip().endswith(c.strip()):
            return True
    return bool(_IDLE_OR_TYPING_RE.search(last_prompt_line))


def _has_pending_question(pane_text: str) -> bool:
    """Check if the agent's LAST output ends with a real question to the user.

    Only the LAST 5 meaningful lines of the agent's most recent turn matter:
    a real "I'm waiting on you" question is the last thing the agent says.
    Rhetorical questions or code-comment questions earlier in a long reply
    should NOT mark the session as waiting.
    """
    lines = pane_text.splitlines()
    # Find the last ❯ prompt — content above it is the agent's last reply
    last_prompt = -1
    second_last_prompt = -1
    for i, line in enumerate(lines):
        if "\u276f" in line:
            second_last_prompt = last_prompt
            last_prompt = i
    if last_prompt < 0 or second_last_prompt < 0:
        return False
    # Collect meaningful lines from the agent's last turn
    meaningful = []
    for line in lines[second_last_prompt + 1:last_prompt]:
        stripped = line.strip()
        if not stripped or all(c in '─━═' for c in stripped):
            continue
        if _STATUS_BAR_RE.search(stripped):
            continue
        meaningful.append(stripped)
    if not meaningful:
        return False
    # Only the LAST 5 lines count — that's where a real question would be.
    # Skip code-like lines (start with //, #, /*, *) so code comments
    # containing ? don't trigger false positives.
    for line in meaningful[-5:]:
        if line.startswith(("//", "#", "/*", "*")):
            continue
        if _QUESTION_RE.search(line):
            return True
        if _ASK_PHRASE_RE.search(line):
            return True
    return False


def _resolve_agent_session_id(tmux_name: str) -> str:
    """Get the CLI agent session UUID from a running tmux session.

    Checks both ~/.claude/sessions and ~/.gemini/sessions. The tmux pane
    PID is typically a shell (zsh); claude/gemini runs as a child, so we
    also check child PIDs via ``pgrep -P``.
    """
    import subprocess, json
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-t", tmux_name, "-F", "#{pane_pid}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
        pane_pid = result.stdout.strip().splitlines()[0]

        # Collect candidate PIDs: pane itself + its children.
        pids = [pane_pid]
        cr = subprocess.run(
            ["pgrep", "-P", pane_pid],
            capture_output=True, text=True, timeout=3,
        )
        if cr.returncode == 0:
            pids.extend(cr.stdout.strip().splitlines())

        for pid in pids:
            for base in (".claude", ".gemini"):
                sf = _Path.home() / base / "sessions" / f"{pid}.json"
                if sf.exists():
                    data = json.loads(sf.read_text())
                    sid = data.get("sessionId", "")
                    if sid:
                        return sid
    except Exception:
        pass
    return ""


def _resolve_agent_session_info(tmux_name: str) -> dict:
    """Get the CLI agent session name and activity status for a tmux session.

    Returns {"name": str, "agent_status": "working"|"idle"|"waiting"|"unknown",
             "agent_session_id": str}.
    """
    import subprocess, json, re
    info = {"name": "", "agent_status": "unknown", "agent_session_id": ""}
    try:
        # Get pane PID
        result = subprocess.run(
            ["tmux", "list-panes", "-t", tmux_name, "-F", "#{pane_pid}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return info
        pid = result.stdout.strip().splitlines()[0]

        # Try Claude session JSON
        claude_file = _Path.home() / ".claude" / "sessions" / f"{pid}.json"
        if claude_file.exists():
            data = json.loads(claude_file.read_text())
            info["name"] = data.get("name", "")
            info["agent_session_id"] = data.get("sessionId", "")
        
        # Try Gemini session JSON
        gemini_file = _Path.home() / ".gemini" / "sessions" / f"{pid}.json"
        if gemini_file.exists():
            data = json.loads(gemini_file.read_text())
            info["name"] = data.get("name", "")
            info["agent_session_id"] = data.get("sessionId", "")

        # Pane title: spinner prefix for status
        # Also check for bell flag (attention needed)
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", tmux_name, "#{pane_title}|#{pane_bell_flag}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout:
            parts = result.stdout.rstrip("\n").split("|")
            title = parts[0]
            bell = parts[1] == "1" if len(parts) > 1 else False

            spinner = _detect_spinner(title)

            if bell:
                info["agent_status"] = "waiting"
            elif spinner == "working":
                info["agent_status"] = "working"
            elif spinner == "idle_or_waiting":
                # ✳ = not working. Check pane content for prompt + questions
                cr = subprocess.run(
                    ["tmux", "capture-pane", "-t", tmux_name, "-p", "-S", "-30"],
                    capture_output=True, text=True, timeout=3,
                )
                pane = cr.stdout if cr.returncode == 0 else ""
                has_idle_prompt = _has_idle_prompt(pane)
                if not has_idle_prompt:
                    info["agent_status"] = "waiting"  # no real prompt = blocked/option selector
                elif _has_pending_question(pane):
                    info["agent_status"] = "waiting"  # question asked
                else:
                    info["agent_status"] = "idle"

            # Fallback name from title (strip spinner prefix).
            # Only trust the pane title as an agent name if Claude Code is
            # actually running (spinner detected). Without a spinner the pane
            # title is typically the machine hostname (e.g.
            # "Romains-MacBook-Pro.local") which would clobber the real name.
            if not info["name"] and spinner:
                cleaned = re.sub(r'^[^a-zA-Z0-9]+', '', title)
                if cleaned and cleaned != "Claude Code":
                    info["name"] = cleaned
    except Exception:
        pass
    return info


def _fast_agent_info(tmux_name: str) -> dict:
    """Lightweight status + name check from tmux pane title."""
    import subprocess
    info = {"status": "unknown", "name": ""}
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", tmux_name, "#{pane_title}"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0:
            return info
        title = r.stdout.rstrip("\n")
        spinner = _detect_spinner(title)

        if spinner == "working":
            info["status"] = "working"
            return info
        if spinner == "idle_or_waiting":
            # Capture pane content for prompt + question detection
            cr = subprocess.run(
                ["tmux", "capture-pane", "-t", tmux_name, "-p", "-S", "-30"],
                capture_output=True, text=True, timeout=2,
            )
            if cr.returncode != 0:
                return info
            pane = cr.stdout
            has_idle_prompt = _has_idle_prompt(pane)
            if not has_idle_prompt:
                info["status"] = "waiting"  # no real prompt = blocked/option selector
            elif _has_pending_question(pane):
                info["status"] = "waiting"  # question asked
            else:
                info["status"] = "idle"

        # Only trust pane title as agent name when Claude Code is running
        # (spinner detected). Without a spinner the title is typically the
        # hostname, which would clobber the real stored name.
        if spinner:
            cleaned = _re.sub(r'^[^a-zA-Z0-9]+', '', title.strip())
            if cleaned and cleaned != "Claude Code":
                info["name"] = cleaned
    except Exception:
        pass
    return info


def reorder_sessions_tool(*, state, workspace: str, session_ids: list) -> dict:
    """Reorder coding sessions within a workspace."""
    from distillate.state import acquire_lock, release_lock
    acquire_lock()
    try:
        state.reload()
        ok = state.reorder_coding_sessions(workspace, session_ids)
        if not ok:
            return {"success": False, "error": "Workspace not found"}
        state.save()
    finally:
        release_lock()
    return {"success": True}


def _batch_pane_titles() -> tuple[dict, bool]:
    """Fetch every tmux pane title in ONE subprocess call.

    Returns ``(titles, probe_ok)``. ``probe_ok`` is ``False`` when the
    subprocess failed, timed out, or tmux reported an error — in which case
    callers MUST NOT treat an empty ``titles`` dict as evidence that sessions
    are dead. Treating a failed probe as "every session is gone" used to
    trigger ``_auto_archive_session`` for every live session, which killed
    their tmux: completing one session would cascade-kill every other
    running session. Trust state over a failed probe.

    ~30ms for 20+ sessions vs ~85ms per session if you call
    ``tmux display-message`` individually.
    """
    import subprocess
    titles: dict = {}
    try:
        r = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{session_name}|#{pane_title}"],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return titles, False
    # Non-zero return typically means "no server running" (exit 1) or a
    # transient server error mid-kill — NOT "every session in state is dead".
    if r.returncode != 0:
        return titles, False
    for line in r.stdout.splitlines():
        if "|" in line:
            name, _, title = line.partition("|")
            # Keep the FIRST pane's title per session (Claude Code = pane 0)
            if name not in titles:
                titles[name] = title
    return titles, True


def _capture_pane(tmux_name: str) -> str:
    """Capture the last 30 lines of a pane (history + visible)."""
    import subprocess
    try:
        cr = subprocess.run(
            ["tmux", "capture-pane", "-t", tmux_name, "-p", "-S", "-30"],
            capture_output=True, text=True, timeout=2,
        )
        return cr.stdout if cr.returncode == 0 else ""
    except Exception:
        return ""


def _classify_from_title_and_pane(title: str, get_pane) -> str:
    """Decide status from a pane title (cheap) and lazy pane content (expensive).

    `get_pane` is a callable so we only run capture-pane when needed.
    """
    spinner = _detect_spinner(title)
    if spinner == "working":
        return "working"
    if spinner == "idle_or_waiting":
        pane = get_pane()
        if not pane:
            return "unknown"
        has_idle_prompt = _has_idle_prompt(pane)
        if not has_idle_prompt:
            return "waiting"
        if _has_pending_question(pane):
            return "waiting"
        return "idle"
    return "unknown"


def agent_status_tool(*, state) -> dict:
    """Fast status + name poll for running coding sessions and long-lived agents.

    Performance:
      1. Single batched `tmux list-panes` for all titles (~30ms).
      2. Working tmux panes are classified from title alone (no extra calls).
      3. Panes showing ✳ need pane content — those `capture-pane` calls
         run in parallel via a thread pool.

    Returns `{sessions, agents}`:
      - `sessions` is keyed by `"{ws_id}/{sid}"` for coding sessions.
      - `agents` is keyed by agent id for long-lived agents with a tmux pane.
    """
    from concurrent.futures import ThreadPoolExecutor

    sessions: dict = {}
    agents: dict = {}
    need_save = False

    # Single batched fetch — ~30ms vs ~85ms*N
    titles, probe_ok = _batch_pane_titles()

    # Pass 1: collect every running session, classify titles, identify
    # which ones need a pane content check. `bucket` tags each pending item
    # so pass 2 routes the result to the right output dict.
    pending = []  # list of (bucket, key, tmux_name, name)
    for ws_id, ws in state.workspaces.items():
        for sid, s in ws.get("coding_sessions", {}).items():
            if s.get("status") != "running":
                continue
            tmux_name = s.get("tmux_name", "")
            if not tmux_name:
                continue
            key = f"{ws_id}/{sid}"

            # Tmux session missing from batch → dead or recently created.
            # If the probe itself failed (tmux transiently unresponsive during
            # another session's kill), do NOT auto-archive — that cascade-kills
            # every running session. Trust state and report unknown.
            if tmux_name not in titles:
                if not probe_ok:
                    sessions[key] = {"status": "unknown", "name": s.get("agent_name", "")}
                    continue
                if _recover_lost_session(tmux_name, s):
                    need_save = True
                    titles, probe_ok = _batch_pane_titles()
                    if tmux_name not in titles:
                        sessions[key] = {"status": "unknown", "name": s.get("agent_name", "")}
                        continue
                else:
                    # Recovery failed — archive to notebook
                    _auto_archive_session(state, ws, sid, s, tmux_name)
                    continue

            title = titles[tmux_name]
            cleaned = _re.sub(r'^[^a-zA-Z0-9]+', '', title.strip())
            name = cleaned if cleaned and cleaned != "Claude Code" else s.get("agent_name", "")

            spinner = _detect_spinner(title)
            if spinner == "working":
                sessions[key] = {"status": "working", "name": name, "tmux_name": tmux_name}
            elif spinner == "idle_or_waiting":
                pending.append(("session", key, tmux_name, name))
            else:
                sessions[key] = {"status": "unknown", "name": name, "tmux_name": tmux_name}

    # Long-lived agents: same tmux-title heuristics, keyed by agent id.
    # Nicolas has no tmux pane (lives in the chat panel) so we skip it.
    for aid, agent in state.agents.items():
        if agent.get("agent_type") == "nicolas":
            continue
        if agent.get("session_status") != "running":
            continue
        tmux_name = agent.get("tmux_name", "")
        if not tmux_name:
            continue
        name = agent.get("name", "")
        if tmux_name not in titles:
            # Same defensive move: a failed probe is not proof the agent died.
            agents[aid] = {"status": "unknown" if not probe_ok else "lost", "name": name}
            continue
        title = titles[tmux_name]
        spinner = _detect_spinner(title)
        if spinner == "working":
            agents[aid] = {"status": "working", "name": name}
        elif spinner == "idle_or_waiting":
            pending.append(("agent", aid, tmux_name, name))
        else:
            agents[aid] = {"status": "unknown", "name": name}

    # Pass 2: parallel capture-pane for the ones that need content analysis
    if pending:
        def _check(item):
            bucket, key, tmux_name, name = item
            pane = _capture_pane(tmux_name)
            if not pane:
                return bucket, key, {"status": "unknown", "name": name, "tmux_name": tmux_name}
            has_idle_prompt = _has_idle_prompt(pane)
            if not has_idle_prompt:
                return bucket, key, {"status": "waiting", "name": name, "tmux_name": tmux_name}
            if _has_pending_question(pane):
                return bucket, key, {"status": "waiting", "name": name, "tmux_name": tmux_name}
            return bucket, key, {"status": "idle", "name": name, "tmux_name": tmux_name}

        with ThreadPoolExecutor(max_workers=min(len(pending), 16)) as ex:
            for bucket, key, info in ex.map(_check, pending):
                if bucket == "session":
                    sessions[key] = info
                else:
                    agents[key] = info

    # Overlay hook-reported status — authoritative when Claude Code has
    # signaled via Stop / Notification / UserPromptSubmit. Falls back to
    # tmux-content classifier for sessions we haven't heard from via hooks.
    from distillate.claude_hooks import merge_with_hook_state
    merge_with_hook_state(sessions)

    if need_save:
        state.save()
    return {"sessions": sessions, "agents": agents}


def _auto_archive_session(state, ws: dict, sid: str, s: dict,
                          tmux_name: str) -> None:
    """Auto-archive a zombie session: extract summary, save to notebook, kill tmux.

    Called when Claude Code has exited but the ``;zsh`` fallback keeps tmux
    alive. Extracts the last assistant message from the JSONL log as a
    summary, writes it to the lab notebook via ``save_session_summary_tool``,
    then kills the tmux session.
    """
    import subprocess

    session_name = s.get("agent_name") or tmux_name or sid
    repo_path = s.get("repo_path", "")
    claude_session_id = s.get("claude_session_id", "")

    # Extract summary from JSONL log
    summary = _extract_last_summary(repo_path, claude_session_id)
    if not summary:
        summary = f"Session \"{session_name}\" ended (auto-archived)."

    # Save via the full summary tool (notebook + project notes + status update)
    try:
        save_session_summary_tool(
            state=state, workspace=ws["id"],
            session=sid, summary=summary)
    except Exception:
        log.warning("Auto-archive: save_session_summary_tool failed for %s, "
                     "falling back to simple end", sid)
        from datetime import datetime, timezone
        from distillate.state import acquire_lock, release_lock
        acquire_lock()
        try:
            state.reload()
            state.update_coding_session(
                ws["id"], sid, status="ended",
                ended_at=datetime.now(timezone.utc).isoformat())
            state.save()
        finally:
            release_lock()
        # Kill tmux manually since save_session_summary_tool didn't
        if tmux_name:
            try:
                subprocess.run(["tmux", "kill-session", "-t", tmux_name],
                               capture_output=True, timeout=5)
            except Exception:
                pass


def _recover_lost_session(tmux_name: str, session: dict) -> bool:
    """Attempt to recover a lost coding session by resuming the agent session.

    Uses ``agent --resume <session_id>`` in a fresh tmux session with the
    same name and working directory. Returns True if recovery succeeded.
    """
    import subprocess

    agent_session_id = session.get("agent_session_id") or session.get("claude_session_id", "")
    if not agent_session_id:
        return False

    repo_path = session.get("repo_path", "")
    if not repo_path or not _Path(repo_path).is_dir():
        return False

    agent_type = session.get("agent_type", "claude")
    model = session.get("model", "")
    binary = "gemini" if agent_type == "gemini" else "claude"
    
    # Build the resume command
    if binary == "gemini":
        cmd_parts = ["gemini", "--resume", shlex.quote(agent_session_id), "--approval-mode", "default"]
        if model:
            cmd_parts.extend(["--model", shlex.quote(model)])
        cmd = " ".join(cmd_parts) + "; zsh -f"
    else:
        cmd_parts = ["claude", "--resume", shlex.quote(agent_session_id), "--permission-mode", "auto"]
        if model:
            cmd_parts.extend(["--model", shlex.quote(model)])
        cmd = " ".join(cmd_parts) + "; zsh -f"

    # Evict stale session lock files before resuming — prevents
    # "Session ID X is already in use" from a previous crashed process.
    _evict_stale_session_lock(agent_session_id)

    tmux_cmd = (
        f"tmux new-session -d -x 220 -y 50 -s {shlex.quote(tmux_name)}"
        f" -c {shlex.quote(repo_path)} {shlex.quote(cmd)}"
    )
    try:
        subprocess.run(tmux_cmd, shell=True, check=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        log.warning("Recovery failed for tmux session %s", tmux_name)
        return False

    # Configure for embedded xterm.js
    subprocess.run(["tmux", "set", "-t", tmux_name, "status", "off"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "mouse", "on"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "escape-time", "0"], capture_output=True)
    sid = session.get("id", "")
    if sid:
        _start_transcript_logging(tmux_name, repo_path, sid)

    log.info("Recovered lost session %s via claude --resume %s", tmux_name, agent_session_id)
    return True


def list_workspaces_tool(*, state) -> dict:
    """List all workspace projects."""
    workspaces = []
    need_save = False

    # ONE batch tmux call for all titles (~30ms vs ~85ms × N per-session calls)
    titles, probe_ok = _batch_pane_titles()

    for ws_id, ws in state.workspaces.items():
        running_sessions = []
        for sid, s in ws.get("coding_sessions", {}).items():
            if s.get("status") == "running":
                tmux_name = s.get("tmux_name", "")
                # Check if tmux session is alive using the batch title dict
                if tmux_name and tmux_name not in titles:
                    if not probe_ok:
                        # Probe itself failed — don't archive, just classify as unknown.
                        pass
                    elif not s.get("recovery_failed") and _recover_lost_session(tmux_name, s):
                        # Recovery succeeded — re-fetch titles and continue
                        s.pop("recovery_failed", None)
                        need_save = True
                        titles, probe_ok = _batch_pane_titles()
                    else:
                        # Recovery failed — archive to notebook
                        _auto_archive_session(state, ws, sid, s, tmux_name)
                        continue
                # Classify status from batch title (no extra subprocess per session)
                agent_status = "unknown"
                live_name = ""
                if tmux_name and tmux_name in titles:
                    title = titles[tmux_name]
                    spinner = _detect_spinner(title)
                    if spinner == "working":
                        agent_status = "working"
                    elif spinner == "idle_or_waiting":
                        agent_status = "idle"
                    if spinner:
                        cleaned = _re.sub(r'^[^a-zA-Z0-9]+', '', title.strip())
                        if cleaned and cleaned != "Claude Code":
                            live_name = cleaned
                agent_name = live_name or s.get("agent_name", "")
                if live_name and s.get("agent_name") != live_name:
                    s["agent_name"] = live_name
                    need_save = True
                running_sessions.append({
                    "id": sid,
                    "tmux_name": tmux_name,
                    "agent_name": agent_name,
                    "agent_status": agent_status,
                    "repo": _Path(s.get("repo_path", "")).name if s.get("repo_path") else "",
                    "started_at": s.get("started_at", ""),
                    "sort_order": s.get("sort_order", 0),
                    "canvas_id": s.get("canvas_id", ""),
                    "session_type": s.get("session_type", "coding"),
                    # Surface any pending wrapup draft so the frontend drafts
                    # dock can repopulate after an app reload.
                    "draft_summary": s.get("draft_summary", ""),
                })
        running_sessions.sort(key=lambda x: x.get("sort_order", 0))

        # Experiment summaries linked to this workspace
        experiments = []
        for exp in state.experiments_for_workspace(ws_id):
            exp_sessions = exp.get("sessions", {})
            active = sum(1 for s in exp_sessions.values() if s.get("status") == "running")
            experiments.append({
                "id": exp["id"],
                "name": exp.get("name", ""),
                "run_count": len(exp.get("runs", {})),
                "active_sessions": active,
                "status": exp.get("status", ""),
            })

        # Slim canvas summary for the sidebar — just what's needed to
        # render rows + session dots. The full records ship via /workspaces/{id}.
        canvas_summaries = [
            {
                "id": c.get("id", ""),
                "title": c.get("title", ""),
                "type": c.get("type", "plain"),
                "status": c.get("status", "active"),
                "session_id": c.get("session_id", ""),
            }
            for c in state.list_workspace_canvases(ws_id)
        ]

        workspaces.append({
            "id": ws_id,
            "name": ws.get("name", ws_id),
            "description": ws.get("description", ""),
            "status": ws.get("status", "active"),
            "root_path": ws.get("root_path", ""),
            "repos": [r.get("name", r.get("path", "")) for r in ws.get("repos", [])],
            "tags": ws.get("tags", []),
            "linked_papers": ws.get("linked_papers", []),
            "resources": ws.get("resources", []),
            "writeup": ws.get("writeup"),
            "canvases": canvas_summaries,
            "default": bool(ws.get("default")),
            "active_sessions": len(running_sessions),
            "running_sessions": running_sessions,
            "experiments": experiments,
            "total_sessions": len(ws.get("coding_sessions", {})),
        })
    if need_save:
        state.save()
    return {"workspaces": workspaces, "total": len(workspaces)}


def get_workspace_tool(*, state, workspace: str) -> dict:
    """Get workspace details."""
    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}

    from datetime import datetime, timezone

    sessions = []
    need_save = False

    # ONE batch tmux call for all titles (~30ms) instead of N×3 subprocess calls
    titles, probe_ok = _batch_pane_titles()

    for sid, s in ws.get("coding_sessions", {}).items():
        tmux_name = s.get("tmux_name", "")
        agent_name = s.get("agent_name", "")
        agent_status = "unknown"
        if s.get("status") == "running" and tmux_name:
            if tmux_name not in titles:
                if not probe_ok:
                    # Probe failed — don't auto-archive, show as unknown.
                    title = None
                else:
                    # Tmux session gone — auto-archive
                    s["status"] = "ended"
                    s["ended_at"] = datetime.now(timezone.utc).isoformat()
                    need_save = True
                    continue
            else:
                title = titles[tmux_name]
            spinner = _detect_spinner(title) if title else None
            if spinner == "working":
                agent_status = "working"
            elif spinner == "idle_or_waiting":
                agent_status = "idle"  # fast path; precise waiting detection not needed here
            if not agent_name and spinner:
                cleaned = _re.sub(r'^[^a-zA-Z0-9]+', '', title.strip())
                if cleaned and cleaned != "Claude Code":
                    agent_name = cleaned
                    s["agent_name"] = agent_name
                    need_save = True

        attention = (agent_status == "waiting") or s.get("attention_needed", False)
        sessions.append({
            "id": sid,
            "repo": _Path(s.get("repo_path", "")).name if s.get("repo_path") else "",
            "tmux_name": tmux_name,
            "agent_name": agent_name,
            "agent_status": agent_status,
            "attention_needed": attention,
            "status": s.get("status", "unknown"),
            "started_at": s.get("started_at", ""),
            "ended_at": s.get("ended_at", ""),
            "summary": s.get("summary", ""),
            "canvas_id": s.get("canvas_id", ""),
            "session_type": s.get("session_type", "coding"),
        })
    if need_save:
        state.save()

    # Include linked experiments with enriched data
    experiments = []
    total_runs = 0
    total_running = 0
    runs_improving = 0  # runs with decision == "best"

    for e in state.experiments_for_workspace(ws["id"]):
        runs = e.get("runs", {})
        exp_sessions = e.get("sessions", {})
        active = sum(1 for s in exp_sessions.values() if s.get("status") == "running")
        run_count = len(runs)
        total_runs += run_count
        total_running += active

        # Find best metric and last activity
        key_metric = e.get("key_metric_name", "")
        best_value = None
        last_activity = e.get("added_at", "")
        # Build metric history in run-order for sparkline rendering.
        ordered_runs = sorted(
            runs.values(),
            key=lambda r: (r.get("run_number") or 0, r.get("started_at") or ""),
        )
        metric_history = []
        for run in ordered_runs:
            results = run.get("results", {})
            if key_metric and key_metric in results:
                val = results[key_metric]
                if isinstance(val, (int, float)):
                    metric_history.append(val)
                if best_value is None or (isinstance(val, (int, float)) and isinstance(best_value, (int, float)) and val > best_value):
                    best_value = val
            # Track last activity
            for ts_field in ("completed_at", "started_at"):
                ts = run.get(ts_field, "")
                if ts and ts > last_activity:
                    last_activity = ts
            if run.get("decision") == "best":
                runs_improving += 1

        experiments.append({
            "id": e["id"],
            "name": e.get("name", ""),
            "status": e.get("status", ""),
            "active_sessions": active,
            "run_count": run_count,
            "key_metric_name": key_metric,
            "best_metric_value": best_value,
            "metric_history": metric_history,
            "last_activity": last_activity,
        })

    # Project-level summary
    project_summary = {
        "total_experiments": len(experiments),
        "total_runs": total_runs,
        "total_running": total_running,
        "runs_improving": runs_improving,
    }

    return {
        "success": True,
        "workspace": {
            "id": ws["id"],
            "name": ws.get("name", ""),
            "description": ws.get("description", ""),
            "status": ws.get("status", "active"),
            "root_path": ws.get("root_path", ""),
            "repos": ws.get("repos", []),
            "tags": ws.get("tags", []),
            "linked_papers": ws.get("linked_papers", []),
            "resources": ws.get("resources", []),
            "writeup": ws.get("writeup"),
            "canvases": state.list_workspace_canvases(ws["id"]),
            "sessions": sessions,
            "experiments": experiments,
            "summary": project_summary,
            "created_at": ws.get("created_at", ""),
        },
    }


def add_workspace_repo_tool(*, state, workspace: str, path: str,
                            name: str = "") -> dict:
    """Link a repo to a workspace."""
    from distillate.state import acquire_lock, release_lock
    from pathlib import Path as _Path

    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}

    p = _Path(path).expanduser().resolve()
    if not p.is_dir():
        return {"success": False, "error": f"Directory not found: {path}"}

    acquire_lock()
    try:
        state.reload()
        added = state.add_workspace_repo(ws["id"], str(p), name=name or p.name)
        if not added:
            return {"success": False, "error": "Repo already linked or workspace not found."}
        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "message": f"Linked {p.name} to workspace '{ws.get('name', ws['id'])}'.",
    }


def launch_coding_session_tool(*, state, workspace: str, repo: str = "",
                               prompt: str = "", cwd_override: str = "",
                               canvas_id: str = "", agent: str = "claude",
                               model: str = "",
                               session_type: str = "coding",
                               work_item_id: str = "") -> dict:
    """Launch a workspace session (coding/writing/survey/data) on a workspace.

    Normally starts in the selected repo's directory. When ``cwd_override``
    is provided (e.g. by the canvas editor launching an agent in the
    canvas directory), that path is used as the session's cwd instead
    and no linked repo is required. ``canvas_id`` is stored on the
    session record so the UI can group canvas-attached sessions under
    their parent canvas card.
    ``session_type`` is "coding" | "writing" | "survey" | "data".
    ``work_item_id`` attaches this session to an existing work item (canvas).
    """
    import subprocess
    from datetime import datetime, timezone
    from distillate.state import acquire_lock, release_lock
    from distillate.agents import get_agent

    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}

    # Map generic agent name to specific binary/harness
    agent_info = get_agent(agent)
    binary = agent_info.get("binary", "claude")

    repos = ws.get("repos", [])

    # Canvas sessions don't need a linked repo — the canvas directory
    # is the session's working folder.
    target = None
    if cwd_override:
        repo_path = cwd_override
        repo_name = _Path(cwd_override).name
    else:
        if not repos:
            return {"success": False, "error": "No repos linked to this workspace."}
        if repo:
            for r in repos:
                if repo.lower() in r.get("name", "").lower() or repo in r.get("path", ""):
                    target = r
                    break
        if not target:
            target = repos[0]  # Default to first repo
        repo_path = target["path"]
        repo_name = target.get("name", _Path(repo_path).name)

    # Generate session id + tmux name. The session number needs to avoid
    # collisions with BOTH state and live tmux: a stale tmux session from
    # a previous run (never cleaned up, or from crash recovery) can claim
    # a number that our state doesn't know about, and `tmux new-session`
    # will fail with "duplicate session" when we try to reuse it.
    existing = ws.get("coding_sessions", {})
    _slug = re.sub(r'[\s_]+', '-', re.sub(r'[^\w\s-]', '', ws.get('name', ws['id']).lower().strip())).strip('-')

    # Collect numbers already in use, from state and live tmux.
    used_nums = set()
    for sid in existing.keys():
        m = re.search(r'(\d+)$', sid)
        if m:
            used_nums.add(int(m.group(1)))
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            prefix = f"{_slug}-"
            for line in result.stdout.splitlines():
                if line.startswith(prefix):
                    suffix = line[len(prefix):]
                    if suffix.isdigit():
                        used_nums.add(int(suffix))
    except Exception:
        pass

    # First unused number starting from 1
    session_num = 1
    while session_num in used_nums:
        session_num += 1

    session_id = f"{session_type}_{session_num:03d}"
    tmux_name = f"{_slug}-{session_num:03d}"

    # Resolve work_item_id → canvas_id + cwd_override
    if work_item_id and not canvas_id:
        canvas_id = work_item_id
    if canvas_id and not cwd_override:
        cv = state.get_workspace_canvas(ws["id"], canvas_id)
        if cv and cv.get("dir"):
            cwd_override = cv["dir"]

    # For writing sessions: auto-attach to the most recent canvas if none specified
    if session_type == "writing" and not canvas_id:
        canvases = state.list_workspace_canvases(ws["id"])
        if canvases:
            canvas_id = canvases[-1]["id"]

    # Inject type-specific context into the initial prompt so the agent
    # understands its role and expected output format from the first message.
    _TYPE_CONTEXT = {
        "writing": (
            "You are in a writing session. Your goal is a polished, publishable document. "
            "Focus on prose quality, structure, and clean formatting (.tex, .md, or .html). "
            "Draft iteratively, invite review, and keep iterating until the output is publication-ready."
        ),
        "survey": (
            "You are in a survey session. Your goal is an actionable knowledge report (.md). "
            "Search web sources and papers, synthesize findings, identify gaps and opportunities. "
            "Structure your report: Background, Key Findings, Open Questions, Recommendations."
        ),
        "data": (
            "You are in a data session. Your goal is a clean, well-documented dataset or analysis. "
            "Focus on data quality, reproducibility, and clear provenance. "
            "Document your transformations and produce a summary of what was built."
        ),
    }
    type_context = _TYPE_CONTEXT.get(session_type, "")
    if type_context:
        prompt = f"{type_context}\n\n{prompt}" if prompt else type_context

    # Generate a stable Claude session ID for resume support
    import uuid
    claude_session_id = str(uuid.uuid4())

    # Build command (with ; zsh to keep tmux alive after exit)
    if binary == "gemini":
        parts = ["gemini", "--approval-mode", "default"]
        if model:
            parts.extend(["--model", shlex.quote(model)])
        # Gemini CLI --resume only works for existing sessions.
        # For new sessions, we let it create its own ID.
        if prompt:
            parts.append(shlex.quote(prompt))
        cmd = " ".join(parts) + "; zsh -f"
    else:
        parts = ["claude", "--permission-mode", "auto"]
        if model:
            parts.extend(["--model", shlex.quote(model)])
        parts.extend(["--session-id", claude_session_id])
        if prompt:
            parts.append(shlex.quote(prompt))
        base_cmd = " ".join(parts)
        # Retry wrapper: if claude fails with "Session ID already in use",
        # evict the stale lock file and resume the session automatically.
        resume_parts = ["claude", "--resume", claude_session_id, "--permission-mode", "auto"]
        if model:
            resume_parts.extend(["--model", shlex.quote(model)])
        resume_cmd = " ".join(resume_parts)
        evict = (
            f"for f in ~/.claude/sessions/*.json; do "
            f"grep -q {shlex.quote(claude_session_id)} \"$f\" 2>/dev/null "
            f"&& rm -f \"$f\"; done"
        )
        cmd = f"{base_cmd} || {{ {evict}; sleep 1; {resume_cmd}; }}; zsh -f"

    # Workspace coding sessions do NOT install stop/notification hooks.
    # Hooks are reserved for Experimentalist sessions where agent lifecycle
    # events drive run tracking and status dots on the experiments chart.
    # Coding sessions are interactive — the user drives them directly and
    # we don't need HTTP roundtrips to our server on every turn.

    # Launch in tmux
    tmux_cmd = f"tmux new-session -d -x 220 -y 50 -s {shlex.quote(tmux_name)} -c {shlex.quote(repo_path)} {shlex.quote(cmd)}"
    try:
        subprocess.run(tmux_cmd, shell=True, check=True, timeout=10)
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": f"Failed to start tmux session: {e}"}

    # Configure for embedded xterm.js (same as experiment launcher)
    subprocess.run(["tmux", "set", "-t", tmux_name, "status", "off"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "mouse", "on"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "escape-time", "0"], capture_output=True)
    _start_transcript_logging(tmux_name, repo_path, session_id)

    # Record session
    acquire_lock()
    try:
        state.reload()
        state.add_coding_session(ws["id"], session_id, repo_path, tmux_name,
                                 agent_session_id=claude_session_id,
                                 canvas_id=canvas_id,
                                 agent_type=agent,
                                 model=model,
                                 session_type=session_type)
        if canvas_id:
            state.set_workspace_canvas_session(ws["id"], canvas_id, session_id)
            cv = state.get_workspace_canvas(ws["id"], canvas_id)
            if cv is not None:
                sessions_list = cv.setdefault("sessions", [])
                if session_id not in sessions_list:
                    sessions_list.append(session_id)
        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "session_id": session_id,
        "tmux_name": tmux_name,
        "repo": repo_name,
        "canvas_id": canvas_id,
        "message": f"Launched {session_type} session on {repo_name}. Attach with: tmux attach -t {tmux_name}",
    }


def launch_writing_session_tool(*, state, workspace: str, repo: str = "",
                                prompt: str = "", agent: str = "claude",
                                model: str = "", canvas_id: str = "") -> dict:
    """Launch a writing session (drafting documents, papers, reports) on a workspace."""
    return launch_coding_session_tool(state=state, workspace=workspace, repo=repo,
                                      prompt=prompt, agent=agent, model=model,
                                      canvas_id=canvas_id,
                                      session_type="writing")


def launch_survey_session_tool(*, state, workspace: str, repo: str = "",
                                prompt: str = "", agent: str = "claude",
                                model: str = "", work_item_id: str = "") -> dict:
    """Launch a survey session (literature review, paper synthesis, knowledge work)."""
    return launch_coding_session_tool(state=state, workspace=workspace, repo=repo,
                                      prompt=prompt, agent=agent, model=model,
                                      work_item_id=work_item_id,
                                      session_type="survey")


def create_work_item_tool(*, state, workspace: str, title: str, type: str,
                           artifact_path: str = "", artifact_entry: str = "",
                           branch: str = "", template: str = "",
                           description: str = "") -> dict:
    """Create a persistent work item (canvas) in a workspace."""
    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}

    # Derive entry filename from type if not provided
    if not artifact_entry:
        defaults = {"code": "main.py", "write": "draft.md", "survey": "survey.md", "data": "analysis.py"}
        artifact_entry = defaults.get(type, "main.md")

    # Map "write" → "markdown" for legacy canvas type compatibility
    canvas_type = {"write": "markdown"}.get(type, type)

    directory = artifact_path or ws.get("root_path", "")
    cv = state.add_workspace_canvas(
        ws["id"], title, canvas_type, directory, artifact_entry,
        branch=branch, template=template, description=description,
    )
    if not cv:
        return {"success": False, "error": "Failed to create work item"}
    state.save()
    return {
        "success": True,
        "work_item_id": cv["id"],
        "type": type,
        "title": title,
        "dir": cv.get("dir", ""),
        "entry": cv.get("entry", ""),
    }


def list_work_items_tool(*, state, workspace: str, type: str = "",
                          status: str = "active") -> dict:
    """List work items (canvas records) in a workspace."""
    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}

    canvases = state.list_workspace_canvases(ws["id"])
    items = []
    for cv in canvases:
        cv_type = cv.get("type", "")
        category = state._canvas_category(cv_type)
        if type and category != type:
            continue
        cv_status = cv.get("status", "active")
        if status != "all" and status and cv_status != status:
            continue
        items.append({
            "id": cv["id"],
            "title": cv.get("title", "Untitled"),
            "type": category,
            "status": cv_status,
            "dir": cv.get("dir", ""),
            "entry": cv.get("entry", ""),
            "session_id": cv.get("session_id", ""),
            "sessions": cv.get("sessions", []),
            "created_at": cv.get("created_at", ""),
        })
    return {"success": True, "work_items": items, "count": len(items)}


def complete_work_item_tool(*, state, workspace: str, work_item_id: str) -> dict:
    """Mark a work item as done."""
    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}
    cv = state.complete_workspace_canvas(ws["id"], work_item_id)
    if not cv:
        return {"success": False, "error": f"Work item not found: {work_item_id}"}
    state.save()
    return {"success": True, "work_item_id": work_item_id, "title": cv.get("title", ""), "status": "done"}


def stop_coding_session_tool(*, state, workspace: str, session: str) -> dict:
    """Stop a coding session by killing its tmux session."""
    import subprocess
    from datetime import datetime, timezone
    from distillate.state import acquire_lock, release_lock

    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}

    sess = ws.get("coding_sessions", {}).get(session)
    if not sess:
        return {"success": False, "error": f"Session not found: {session}"}

    tmux_name = sess.get("tmux_name", "")
    if tmux_name:
        # Send C-c first, then kill
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_name, "C-c", ""],
            capture_output=True, timeout=3,
        )
        import time
        time.sleep(0.5)
        subprocess.run(
            ["tmux", "kill-session", "-t", tmux_name],
            capture_output=True, timeout=5,
        )

    acquire_lock()
    try:
        state.reload()
        state.update_coding_session(
            ws["id"], session,
            status="ended",
            ended_at=datetime.now(timezone.utc).isoformat(),
        )
        state.save()
    finally:
        release_lock()

    return {"success": True, "message": f"Session {session} stopped."}


# ---------------------------------------------------------------------------
# Session completion — capture, summarize, log, then stop
# ---------------------------------------------------------------------------

_ANSI_RE = _re.compile(r'(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|\x1b\].*?(\x07|\x1b\\)|\x1b[()][A-B012]')


def _clean_scrollback(text: str) -> str:
    """Strip ANSI escapes, agent TUI chrome, and blank lines from scrollback."""
    text = _ANSI_RE.sub("", text)
    lines = []
    # Chrome patterns that are definitely not markdown content
    chrome_keywords = (
        "shift+tab", "ctrl+", "esc to", "to cycle", "to expand",
        "to interrupt", "to hide", "to approve", "to submit",
        "press enter", "arrow keys", "page up/down",
    )
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines that look like TUI status bars or key hint bars
        if any(kw in stripped.lower() for kw in chrome_keywords):
            continue
        # Skip braille-only lines (spinners)
        if all(0x2800 <= ord(c) <= 0x28FF or c == " " for c in stripped):
            continue
        # Skip lines that are just prompt symbols or boxes
        if stripped in ("❯", "❯ ", "❯  ", "│", "┃", "└", "┛", "┌", "┓"):
            continue
        lines.append(line)
    result = "\n".join(lines)
    return result[-8000:] if len(result) > 8000 else result


_WRAPUP_PROMPT = (
    "Wrap up this session with a brief summary. Do NOT run any tools or "
    "git commands — the user will handle commit and push themselves. "
    "Reply with ONLY the summary below, no preamble or closing "
    "remarks:\n\n"
    "# Short Title (Title Case)\n\n"
    "One or two sentences on what changed and why it matters."
)


def _inject_wrapup_prompt(tmux_name: str) -> bool:
    """Type the wrap-up instruction into the live Claude Code TUI and submit it.

    Sends C-u first to clear any pending text in the input box. Without
    this, our wrap-up prompt would be appended to whatever the user had
    typed but not submitted, and the resulting multi-line input fails to
    submit on Enter in Claude Code's Ink-based TUI — the session stalls
    with the prompt typed but never sent. C-u (kill-line) is honored by
    Ink's text input.
    """
    import subprocess
    import time
    try:
        # Clear the input box first so any pending user text doesn't
        # collide with our injected prompt.
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_name, "C-u"],
            capture_output=True, timeout=3,
        )
        time.sleep(0.1)
        r1 = subprocess.run(
            ["tmux", "send-keys", "-t", tmux_name, "-l", _WRAPUP_PROMPT],
            capture_output=True, timeout=3,
        )
        if r1.returncode != 0:
            log.warning("tmux send-keys failed for %s (rc=%d): %s",
                        tmux_name, r1.returncode, r1.stderr.decode(errors="replace").strip())
            return False
        time.sleep(0.2)
        r2 = subprocess.run(
            ["tmux", "send-keys", "-t", tmux_name, "Enter"],
            capture_output=True, timeout=3,
        )
        if r2.returncode != 0:
            log.warning("tmux send-keys Enter failed for %s (rc=%d)",
                        tmux_name, r2.returncode)
            return False
        return True
    except Exception:
        log.exception("Failed to inject wrap-up prompt into %s", tmux_name)
        return False


def _resolve_claude_jsonl(repo_path: str, claude_session_id: str) -> _Path | None:
    """Locate the Claude Code session log on disk.

    Each Claude Code session writes one assistant turn per line to
    ``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``. The directory
    name is the absolute repo path with ``/`` replaced by ``-``. If the
    stored session id doesn't match a file (e.g. the user resumed with a
    fresh id), fall back to the most-recently-modified ``*.jsonl`` in the
    project directory.
    """
    if not repo_path:
        return None
    try:
        encoded = str(_Path(repo_path).expanduser().resolve()).replace("/", "-")
        log_dir = _Path.home() / ".claude" / "projects" / encoded
        if not log_dir.is_dir():
            return None
        if claude_session_id:
            candidate = log_dir / f"{claude_session_id}.jsonl"
            if candidate.is_file():
                return candidate
        files = sorted(log_dir.glob("*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0] if files else None
    except Exception:
        log.debug("Failed to resolve JSONL for %s", repo_path, exc_info=True)
        return None


def _jsonl_line_count(path: _Path) -> int:
    """Number of lines in a JSONL file (0 if unreadable)."""
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _wait_for_jsonl_reply(
    path: _Path,
    baseline_lines: int,
    *,
    timeout: float = 15.0,
    poll: float = 0.1,
    tmux_name: str | None = None,
) -> str | None:
    """Wait for Claude's wrap-up reply to land in the JSONL log.

    Polls ``path`` for new content past ``baseline_lines``. Two signals:

    - **end_turn with text** — the summary is ready; return its text.
    - **stop_hook_summary** — Claude Code wrote the Stop-hook marker for
      this turn. That marker is emitted *after* the final assistant turn
      of the request, so if we see it without text we know Claude
      finished without producing any; return ``None`` fast.

    Uses ``stat().st_mtime`` to skip the JSONL parse when the file is
    unchanged — keeps polling cheap on a 1M+-line log.

    ``tmux_name`` is accepted for API compatibility but no longer
    consulted: the old tmux-status short-circuit fired prematurely
    during Claude's extended-thinking mode (brief idle between the
    thinking turn and the text turn) and also lied during streaming.
    """
    import json
    import time

    def _scan() -> tuple[str | None, bool]:
        """Return (latest_end_turn_text, saw_stop_hook) past baseline."""
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return None, False
        result: str | None = None
        saw_stop = False
        for raw in lines[baseline_lines:]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "assistant":
                inner = msg.get("message", {})
                if not isinstance(inner, dict):
                    continue
                if inner.get("stop_reason") != "end_turn":
                    continue
                content = inner.get("content", [])
                if not isinstance(content, list):
                    continue
                text_parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                    and block.get("text")
                ]
                if text_parts:
                    result = "\n".join(text_parts).strip()
            elif mtype == "system" and msg.get("subtype") == "stop_hook_summary":
                saw_stop = True
        return result, saw_stop

    deadline = time.monotonic() + timeout
    last_mtime: float = -1.0

    while time.monotonic() < deadline:
        try:
            cur_mtime = path.stat().st_mtime
        except OSError:
            cur_mtime = 0.0
        if cur_mtime != last_mtime:
            last_mtime = cur_mtime
            text, saw_stop = _scan()
            if text:
                return text
            if saw_stop:
                return None
        time.sleep(poll)
    return None


def _extract_summary_from_pane(session_name: str, raw_pane: str) -> str | None:
    """Try to extract a structured summary from the pane after prompt injection.

    The wrapup prompt asks for ``# Title`` + paragraph. If we can
    find that structure in the cleaned pane, return it directly. Otherwise
    return ``None`` so the caller falls through to the raw-pane fallback.
    """
    import re
    cleaned = _clean_scrollback(raw_pane)
    # Look for a markdown heading line — that's the start of the summary
    match = re.search(r'^(#\s+.+)$', cleaned, re.MULTILINE)
    if not match:
        return None
    summary_start = match.start()
    candidate = cleaned[summary_start:].strip()
    # Must have at least a title + some body
    lines = [ln for ln in candidate.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    return candidate


def _fallback_draft_from_pane(session_name: str, raw_pane: str) -> str:
    """Build a recoverable draft from the raw pane when extraction fails.

    Dumps the last ~40 non-empty cleaned lines as a quoted block so the
    user has concrete content to edit rather than the useless
    ``Session completed.`` string. Also writes the raw + cleaned captures
    to /tmp for post-mortem diagnosis.
    """
    import tempfile
    import time as _time

    cleaned = _clean_scrollback(raw_pane)
    try:
        ts = int(_time.time())
        dump = _Path(tempfile.gettempdir()) / f"distillate-wrapup-{ts}.txt"
        dump.write_text(
            f"# session_name: {session_name}\n"
            f"# timestamp: {ts}\n"
            "# --- RAW PANE ---\n"
            f"{raw_pane}\n"
            "# --- CLEANED PANE ---\n"
            f"{cleaned}\n",
            encoding="utf-8",
        )
        log.warning("Wrap-up extraction failed; pane dump at %s", dump)
    except Exception:
        log.debug("Failed to write wrap-up debug dump", exc_info=True)

    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    tail = lines[-40:]
    if not tail:
        body = "_(no captured terminal output)_"
    else:
        body = "\n".join(f"> {ln}" for ln in tail)
    return (
        f"# {session_name}\n\n"
        "_Automatic extraction of Claude's summary failed. The raw pane "
        "tail is shown below — edit it into a proper summary before saving._\n\n"
        f"{body}"
    )


def _last_claude_activity(repo_path: str, claude_session_id: str) -> str | None:
    """Timestamp of the latest pre-wrap-up Claude activity, as ISO-8601 UTC.

    Returns the ``timestamp`` field of the last ``assistant`` message in the
    Claude Code JSONL log. Falls back to the file's mtime, or ``None`` if no
    log can be located.

    Call this BEFORE injecting the wrap-up prompt so the injection's own
    assistant reply doesn't overwrite the "real" last-activity timestamp.
    """
    import json
    from datetime import datetime, timezone

    jsonl = _resolve_claude_jsonl(repo_path, claude_session_id)
    if jsonl is None:
        return None
    try:
        last_ts: str | None = None
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "assistant":
                    ts = msg.get("timestamp")
                    if isinstance(ts, str) and ts:
                        last_ts = ts
        if last_ts:
            return last_ts
        mtime = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc)
        return mtime.isoformat()
    except Exception:
        log.debug("Failed to read Claude log for %s", repo_path, exc_info=True)
        return None


def _read_latest_recap(path: _Path) -> str | None:
    """Read the most recent ``away_summary`` from a Claude Code JSONL log,
    but only if no substantive assistant turn happened after it.

    Claude Code writes these automatically when a session goes idle —
    they're short (2-3 sentences) and already describe what happened.
    Reading one is instant: no prompt injection, no waiting.

    If the agent produced meaningful output *after* the last recap, the
    recap is stale and we return ``None`` so the caller falls through to
    the slow path (inject prompt → wait for fresh summary).
    """
    import json
    try:
        with open(path, "rb") as f:
            try:
                f.seek(-200_000, 2)
            except OSError:
                f.seek(0)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    recap: str | None = None
    recap_idx: int = -1
    last_assistant_idx: int = -1
    for idx, line in enumerate(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if (msg.get("type") == "system"
                and msg.get("subtype") == "away_summary"
                and msg.get("content")):
            text = msg["content"].strip()
            # Claude Code appends a UI hint we don't want surfaced in Distillate
            text = _re.sub(r'\s*\(disable recaps in /config\)\s*', '', text).strip()
            recap = text or None
            recap_idx = idx
        elif msg.get("type") == "assistant":
            # Only count turns with real text content (skip tool-only turns)
            content = msg.get("message", {}).get("content", [])
            for block in (content if isinstance(content, list) else []):
                if isinstance(block, dict) and block.get("type") == "text" and len(block.get("text", "")) > 20:
                    last_assistant_idx = idx
                    break
                elif isinstance(block, str) and len(block) > 20:
                    last_assistant_idx = idx
                    break
    if recap and last_assistant_idx > recap_idx:
        log.info("Wrap-up: away_summary is stale (assistant activity after recap), forcing fresh summary")
        return None
    return recap


def _wait_for_pane_reply(tmux_name: str, pre_inject_pane: str,
                         *, timeout: float = 15.0, poll: float = 0.5) -> str | None:
    """Poll the tmux pane until new substantial content appears after prompt injection.

    Used for non-Claude agents (Gemini, Codex, etc.) that don't write JSONL logs.
    Compares the pane against ``pre_inject_pane`` and returns the new content
    once it stabilises (two consecutive identical captures).
    """
    import time
    from distillate.launcher import capture_pane

    deadline = time.monotonic() + timeout
    prev_capture = ""
    while time.monotonic() < deadline:
        time.sleep(poll)
        try:
            current = capture_pane(tmux_name, lines=500)
        except Exception:
            continue
        if not current or current.strip() == pre_inject_pane.strip():
            continue
        # Wait for output to stabilise (agent finished typing)
        if current == prev_capture:
            return current
        prev_capture = current
    return prev_capture if prev_capture and prev_capture.strip() != pre_inject_pane.strip() else None


def complete_coding_session_tool(*, state, workspace: str, session: str) -> dict:
    """Draft a session wrap-up summary for the user to review.

    Strategy varies by agent type:

    **Claude Code** (has JSONL logs):
    1. Read the latest ``away_summary`` recap — instant, no injection.
       Skipped if stale (assistant activity after the recap).
    2. Inject wrap-up prompt → poll JSONL for the reply.
    3. Last resort: pane capture.

    **Gemini / other agents** (no JSONL):
    1. Inject wrap-up prompt → poll the tmux pane for the reply.
    2. Last resort: raw pane capture.

    Does NOT kill tmux. The draft is stored as ``draft_summary``; the
    user then Saves (persists + kills) or Discards (clears + continues).
    """
    from distillate.launcher import _tmux_session_exists, capture_pane
    from distillate.state import acquire_lock, release_lock

    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}
    sess = ws.get("coding_sessions", {}).get(session)
    if not sess:
        return {"success": False, "error": f"Session not found: {session}"}
    if sess.get("status") != "running":
        return {"success": False, "error": "Session is not running"}

    tmux_name = sess.get("tmux_name", "")
    session_name = sess.get("agent_name") or tmux_name or session
    if not tmux_name:
        return {"success": False, "error": "No tmux session attached"}
    if not _tmux_session_exists(tmux_name):
        return {"success": False, "error": "Session's tmux has died — stop it instead"}

    agent_type = sess.get("agent_type", "claude")
    is_claude = agent_type == "claude"

    repo_path = sess.get("repo_path", "")
    last_activity_at: str | None = None
    jsonl_path: _Path | None = None

    if is_claude:
        claude_session_id = (sess.get("claude_session_id", "")
                             or sess.get("agent_session_id", ""))
        if not claude_session_id:
            claude_session_id = _resolve_agent_session_id(tmux_name)
        jsonl_path = _resolve_claude_jsonl(repo_path, claude_session_id)
        # Capture the true "last activity" timestamp BEFORE any injection so
        # the wrap-up prompt's own reply doesn't overwrite the real one.
        last_activity_at = _last_claude_activity(repo_path, claude_session_id)

    # --- Fast path (Claude only): grab the existing session recap ---
    draft_summary: str | None = None
    if is_claude and jsonl_path is not None:
        draft_summary = _read_latest_recap(jsonl_path)

    # --- Slow path: inject wrap-up prompt and wait for response ---
    if not draft_summary:
        # Capture pane before injection so we can detect new output
        try:
            pre_inject_pane = capture_pane(tmux_name, lines=500)
        except Exception:
            pre_inject_pane = ""

        if not _inject_wrapup_prompt(tmux_name):
            return {"success": False, "error": "Failed to inject wrap-up prompt"}

        if is_claude and jsonl_path is not None:
            baseline_lines = _jsonl_line_count(jsonl_path)
            draft_summary = _wait_for_jsonl_reply(
                jsonl_path, baseline_lines,
            )
            if not draft_summary:
                log.warning("Wrap-up: no new assistant turn in %s", jsonl_path)
        else:
            # Non-Claude agents: poll the pane for the reply
            pane_reply = _wait_for_pane_reply(tmux_name, pre_inject_pane)
            if pane_reply:
                draft_summary = _extract_summary_from_pane(session_name, pane_reply)

    # --- Last resort: pane capture / transcript log ---
    if not draft_summary:
        try:
            raw_pane = capture_pane(tmux_name, lines=500)
        except Exception:
            raw_pane = ""
        if not raw_pane.strip():
            try:
                from distillate.config import CONFIG_DIR
                tlog = CONFIG_DIR / "transcripts" / f"{session}.log"
                if tlog.exists():
                    with open(tlog, "rb") as f:
                        try:
                            f.seek(-100_000, 2)
                        except OSError:
                            f.seek(0)
                        raw_pane = f.read().decode("utf-8", errors="replace")
            except Exception:
                pass
        draft_summary = _fallback_draft_from_pane(session_name, raw_pane)

    acquire_lock()
    try:
        state.reload()
        update: dict = {"draft_summary": draft_summary}
        if last_activity_at:
            update["last_activity_at"] = last_activity_at
        state.update_coding_session(ws["id"], session, **update)
        state.save()
    finally:
        release_lock()

    return {"success": True, "summary": draft_summary,
            "session_name": session_name,
            "last_activity_at": last_activity_at,
            "message": "Wrap-up drafted. Save to commit or Discard to keep session running."}


def discard_session_wrapup_tool(*, state, workspace: str, session: str) -> dict:
    """Discard a drafted session wrap-up; the tmux session keeps running.

    Clears ``draft_summary`` from state so the user can change their mind and
    continue coding as if they had never clicked Complete. The tmux session
    and its Claude agent are untouched.
    """
    from distillate.state import acquire_lock, release_lock

    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}
    sess = ws.get("coding_sessions", {}).get(session)
    if not sess:
        return {"success": False, "error": f"Session not found: {session}"}

    acquire_lock()
    try:
        state.reload()
        state.update_coding_session(ws["id"], session, draft_summary=None)
        state.save()
    finally:
        release_lock()

    return {"success": True, "message": "Wrap-up discarded; session still running."}


def save_session_summary_tool(*, state, workspace: str, session: str,
                               summary: str) -> dict:
    """Persist the final session summary and end the session.

    This is the point of no return: kills the tmux session, marks the session
    ``completed``, persists the edited summary, and appends entries to the
    lab notebook and project notes. The lab notebook entry is back-dated to
    ``last_activity_at`` (captured pre-wrap-up) so idle sessions land on the
    day the work actually happened.
    """
    import subprocess
    import time
    from datetime import datetime
    from distillate.state import acquire_lock, release_lock

    summary = (summary or "").strip()
    if not summary:
        return {"success": False, "error": "Summary cannot be empty"}

    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}
    sess = ws.get("coding_sessions", {}).get(session)
    if not sess:
        return {"success": False, "error": f"Session not found: {session}"}

    # Idempotent: a second save on an already-completed session is a no-op.
    # Returning success keeps the UI happy (dock clears, toasts fire) without
    # re-killing tmux or double-appending to the notebook / project notes.
    if sess.get("status") == "completed":
        return {"success": True,
                "summary": sess.get("summary", ""),
                "session_name": sess.get("agent_name") or sess.get("tmux_name") or session,
                "completed_at": sess.get("completed_at") or sess.get("ended_at", "")}

    session_name = sess.get("agent_name") or sess.get("tmux_name") or session
    tmux_name = sess.get("tmux_name", "")

    # Use the pre-wrap-up Claude activity timestamp so a session that was
    # idle for days gets written to the right day in the lab notebook. Falls
    # back to "now" only if we never recorded one. Convert UTC ISO strings
    # to local naive datetimes so the notebook file lands on the correct
    # local calendar day.
    effective_when = datetime.now()
    raw_last = sess.get("last_activity_at")
    if raw_last:
        try:
            parsed = datetime.fromisoformat(str(raw_last).replace("Z", "+00:00"))
            effective_when = parsed.astimezone().replace(tzinfo=None)
        except ValueError:
            log.debug("Bad last_activity_at on %s: %r", session, raw_last)

    if tmux_name:
        try:
            subprocess.run(["tmux", "send-keys", "-t", tmux_name, "C-c", ""],
                           capture_output=True, timeout=3)
            time.sleep(0.5)
            subprocess.run(["tmux", "kill-session", "-t", tmux_name],
                           capture_output=True, timeout=5)
        except Exception:
            log.warning("Failed to kill tmux session %s on save", tmux_name)

    acquire_lock()
    try:
        state.reload()
        state.update_coding_session(ws["id"], session,
                                    status="completed",
                                    summary=summary,
                                    draft_summary=None,
                                    completed_at=effective_when.isoformat(),
                                    ended_at=effective_when.isoformat())
        state.save()
    finally:
        release_lock()

    try:
        append_lab_book_tool(state=state,
                             entry=f'Completed "{session_name}": {summary}',
                             entry_type="session",
                             project=ws.get("name", ws["id"]),
                             when=effective_when)
    except Exception:
        log.warning("Failed to append session completion to lab notebook")

    try:
        notes_result = get_workspace_notes_tool(state=state, workspace=ws["id"])
        existing = notes_result.get("content", "") if notes_result.get("success") else ""
        date_str = effective_when.strftime("%Y-%m-%d %H:%M")
        section = f"\n\n### Session: {session_name} (completed {date_str})\n\n{summary}\n"
        save_workspace_notes_tool(state=state, workspace=ws["id"],
                                content=existing + section)
    except Exception:
        log.warning("Failed to append session completion to project notes")

    return {"success": True, "summary": summary,
            "session_name": session_name,
            "completed_at": effective_when.isoformat()}


def restart_coding_session_tool(*, state, workspace: str, session: str) -> dict:
    """Restart a coding session: kill old tmux, spawn new with --resume."""
    import subprocess
    from distillate.launcher import _tmux_session_exists
    from distillate.state import acquire_lock, release_lock

    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}

    sess = ws.get("coding_sessions", {}).get(session)
    if not sess:
        return {"success": False, "error": f"Session not found: {session}"}

    tmux_name = sess.get("tmux_name", "")
    repo_path = sess.get("repo_path", "")
    agent_session_id = sess.get("agent_session_id") or sess.get("claude_session_id", "")
    agent_type = sess.get("agent_type", "claude")
    model = sess.get("model", "")
    binary = "gemini" if agent_type == "gemini" else "claude"

    # Kill old tmux if it exists
    if tmux_name and _tmux_session_exists(tmux_name):
        subprocess.run(
            ["tmux", "kill-session", "-t", tmux_name],
            capture_output=True, timeout=5,
        )

    if not repo_path:
        return {"success": False, "error": "No repo_path on session record."}

    # Evict stale session locks before resuming
    if agent_session_id:
        _evict_stale_session_lock(agent_session_id)

    # Build resume command — use --resume (not --session-id) to continue
    # an existing conversation rather than trying to create a new one.
    if binary == "gemini":
        cmd_parts = ["gemini", "--approval-mode", "default"]
        if agent_session_id:
            cmd_parts.extend(["--resume", shlex.quote(agent_session_id)])
        if model:
            cmd_parts.extend(["--model", shlex.quote(model)])
        cmd = " ".join(cmd_parts) + "; zsh -f"
    else:
        cmd_parts = ["claude", "--permission-mode", "auto"]
        if agent_session_id:
            cmd_parts.extend(["--resume", shlex.quote(agent_session_id)])
        if model:
            cmd_parts.extend(["--model", shlex.quote(model)])
        cmd = " ".join(cmd_parts) + "; zsh -f"

    tmux_cmd = (
        f"tmux new-session -d -x 220 -y 50 -s {shlex.quote(tmux_name)} "
        f"-c {shlex.quote(repo_path)} {shlex.quote(cmd)}"
    )
    try:
        subprocess.run(tmux_cmd, shell=True, check=True, timeout=10)
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": f"Failed to restart: {e}"}

    subprocess.run(["tmux", "set", "-t", tmux_name, "status", "off"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "mouse", "on"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "escape-time", "0"], capture_output=True)
    _start_transcript_logging(tmux_name, repo_path, session)

    acquire_lock()
    try:
        state.reload()
        state.update_coding_session(ws["id"], session, status="running")
        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "tmux_name": tmux_name,
        "resumed": bool(agent_session_id),
        "message": f"Session restarted{' with context' if agent_session_id else ' (fresh)'}.",
    }


def recover_all_sessions_tool(*, state) -> dict:
    """Recover ALL lost coding sessions across all workspaces. Idempotent.

    For each coding_session with status=running where tmux is dead:
    spawn a new tmux with `claude --resume <id>; zsh`.
    Skip sessions whose tmux is alive.
    """
    import subprocess
    from distillate.launcher import _tmux_session_exists
    from distillate.state import acquire_lock, release_lock

    recovered = 0
    already_running = 0
    failed = 0

    for ws_id, ws in state.workspaces.items():
        for sess_id, sess in ws.get("coding_sessions", {}).items():
            if sess.get("status") != "running":
                continue
            tmux_name = sess.get("tmux_name", "")
            if not tmux_name:
                continue

            if _tmux_session_exists(tmux_name):
                already_running += 1
                continue

            repo_path = sess.get("repo_path", "")
            agent_session_id = sess.get("agent_session_id") or sess.get("claude_session_id", "")
            agent_type = sess.get("agent_type", "claude")
            model = sess.get("model", "")
            binary = "gemini" if agent_type == "gemini" else "claude"

            if not repo_path:
                failed += 1
                continue

            # Evict stale session locks before resuming
            if agent_session_id:
                _evict_stale_session_lock(agent_session_id)

            # Build resume command (--resume, not --session-id)
            if binary == "gemini":
                cmd_parts = ["gemini", "--approval-mode", "default"]
                if agent_session_id:
                    cmd_parts.extend(["--resume", shlex.quote(agent_session_id)])
                if model:
                    cmd_parts.extend(["--model", shlex.quote(model)])
                cmd = " ".join(cmd_parts) + "; zsh -f"
            else:
                cmd_parts = ["claude", "--permission-mode", "auto"]
                if agent_session_id:
                    cmd_parts.extend(["--resume", shlex.quote(agent_session_id)])
                if model:
                    cmd_parts.extend(["--model", shlex.quote(model)])
                cmd = " ".join(cmd_parts) + "; zsh -f"

            tmux_cmd = (
                f"tmux new-session -d -x 220 -y 50 -s {shlex.quote(tmux_name)} "
                f"-c {shlex.quote(repo_path)} {shlex.quote(cmd)}"
            )
            try:
                subprocess.run(tmux_cmd, shell=True, check=True, timeout=10)
                subprocess.run(["tmux", "set", "-t", tmux_name, "status", "off"], capture_output=True)
                subprocess.run(["tmux", "set", "-t", tmux_name, "mouse", "on"], capture_output=True)
                subprocess.run(["tmux", "set", "-t", tmux_name, "escape-time", "0"], capture_output=True)
                _start_transcript_logging(tmux_name, repo_path, sess_id)
                recovered += 1
            except Exception:
                log.exception("Failed to recover session %s/%s", ws_id, sess_id)
                failed += 1

    if recovered > 0:
        acquire_lock()
        try:
            state.save()
        finally:
            release_lock()

    return {
        "success": True,
        "recovered": recovered,
        "already_running": already_running,
        "failed": failed,
    }


def stop_all_sessions_tool(*, state, workspace: str) -> dict:
    """Stop all non-working sessions in a workspace.

    For each session: extracts a summary from the Claude Code JSONL log (or
    pane fallback), saves it to the lab notebook and project notes via
    ``save_session_summary_tool``, then kills the tmux session. Sessions
    that are actively working are left untouched.
    """
    import subprocess
    from datetime import datetime, timezone
    from distillate.launcher import _tmux_session_exists

    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}

    stopped = 0
    skipped_working = 0

    sessions_to_stop = []
    for sid, s in ws.get("coding_sessions", {}).items():
        if s.get("status") != "running":
            continue
        tmux_name = s.get("tmux_name", "")
        tmux_alive = tmux_name and _tmux_session_exists(tmux_name)
        if tmux_alive:
            info = _fast_agent_info(tmux_name)
            if info.get("status") == "working":
                skipped_working += 1
                continue
        sessions_to_stop.append((sid, s, tmux_alive))

    for sid, s, tmux_alive in sessions_to_stop:
        session_name = s.get("agent_name") or s.get("tmux_name") or sid
        repo_path = s.get("repo_path", "")
        claude_session_id = s.get("claude_session_id", "")

        # Extract a summary from the JSONL log
        summary = _extract_last_summary(repo_path, claude_session_id)

        # Fallback: capture pane content if tmux is alive
        if not summary and tmux_alive:
            try:
                from distillate.launcher import capture_pane
                raw_pane = capture_pane(s.get("tmux_name", ""), lines=500)
                cleaned = _clean_scrollback(raw_pane)
                lines = [ln for ln in cleaned.splitlines() if ln.strip()]
                if lines:
                    summary = f"# {session_name}\n\n" + "\n".join(
                        f"- {ln}" for ln in lines[-5:])
            except Exception:
                pass

        if not summary:
            summary = f"Session ended (no summary available)."

        # Save via the existing summary tool (writes notebook + project notes)
        try:
            save_session_summary_tool(
                state=state, workspace=ws["id"],
                session=sid, summary=summary)
        except Exception:
            log.warning("Bulk stop: failed to save summary for %s", sid)
            # Fall back to simple stop
            tmux_name = s.get("tmux_name", "")
            if tmux_alive and tmux_name:
                try:
                    subprocess.run(
                        ["tmux", "kill-session", "-t", tmux_name],
                        capture_output=True, timeout=5)
                except Exception:
                    pass
            from distillate.state import acquire_lock, release_lock
            acquire_lock()
            try:
                state.reload()
                state.update_coding_session(
                    ws["id"], sid, status="ended",
                    ended_at=datetime.now(timezone.utc).isoformat())
                state.save()
            finally:
                release_lock()
        stopped += 1

    return {
        "success": True,
        "stopped": stopped,
        "skipped_working": skipped_working,
    }


def _extract_last_summary(repo_path: str, claude_session_id: str) -> str | None:
    """Extract the last meaningful assistant message from a Claude Code JSONL log.

    Reads backward through the log to find the last assistant turn with
    substantial text content, suitable as an auto-generated session summary.
    """
    import json

    jsonl = _resolve_claude_jsonl(repo_path, claude_session_id)
    if jsonl is None:
        return None
    try:
        last_text = None
        with open(jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") != "assistant":
                    continue
                # Extract text from message content
                content = msg.get("message", {}).get("content", [])
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block["text"])
                    elif isinstance(block, str):
                        texts.append(block)
                text = "\n".join(texts).strip()
                if len(text) > 20:  # skip trivial replies
                    last_text = text
        # Truncate to a reasonable summary length
        if last_text and len(last_text) > 2000:
            last_text = last_text[:2000] + "\n\n_(truncated)_"
        return last_text
    except Exception:
        log.debug("Failed to extract summary from JSONL", exc_info=True)
        return None


def recover_coding_session_tool(*, state, workspace: str,
                                session: str) -> dict:
    """Recover a lost coding session by resuming its Claude conversation in a new tmux."""
    import subprocess
    from distillate.launcher import _tmux_session_exists
    from distillate.state import acquire_lock, release_lock

    ws = _find_workspace(state, workspace)
    if not ws:
        return {"success": False, "error": f"Workspace not found: {workspace}"}

    sess = ws.get("coding_sessions", {}).get(session)
    if not sess:
        return {"success": False, "error": f"Session not found: {session}"}
    if sess.get("status") != "running":
        return {"success": False, "error": "Session is not in running state."}

    tmux_name = sess.get("tmux_name", "")
    if tmux_name and _tmux_session_exists(tmux_name):
        return {"success": False, "error": "Tmux session is still alive — nothing to recover."}

    claude_session_id = (sess.get("claude_session_id", "")
                         or sess.get("agent_session_id", ""))
    if not claude_session_id:
        return {"success": False,
                "error": "No claude_session_id stored — session was created before recovery support."}

    repo_path = sess.get("repo_path", "")
    if not repo_path:
        return {"success": False, "error": "No repo_path on session record."}

    # Resume: claude --resume <id> in a fresh tmux; zsh keeps window alive
    cmd = f"claude --resume {claude_session_id} --permission-mode auto; zsh -f"
    tmux_cmd = (f"tmux new-session -d -x 220 -y 50 -s {shlex.quote(tmux_name)} "
                f"-c {shlex.quote(repo_path)} {shlex.quote(cmd)}")
    try:
        subprocess.run(tmux_cmd, shell=True, check=True, timeout=10)
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": f"Failed to start tmux session: {e}"}

    # Configure for embedded xterm.js
    subprocess.run(["tmux", "set", "-t", tmux_name, "status", "off"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "mouse", "on"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "escape-time", "0"], capture_output=True)
    _start_transcript_logging(tmux_name, repo_path, session)

    return {
        "success": True,
        "session_id": session,
        "tmux_name": tmux_name,
        "message": f"Recovered session — resumed Claude conversation {claude_session_id[:8]}…",
    }


# ---------------------------------------------------------------------------
# Project notes + lab notebook
# ---------------------------------------------------------------------------

def _get_notes_dir(state, workspace_query: str):
    """Resolve the notes directory for a workspace."""
    ws = _find_workspace(state, workspace_query)
    if not ws:
        return None, None, {"success": False, "error": f"Workspace not found: {workspace_query}"}
    notes_path = ws.get("notes_path", "")
    if not notes_path:
        # Create it if missing (backward compat)
        from distillate.lab_notebook import _KB_DIR
        kb_root = _KB_DIR / "wiki" / "projects"
        notes_path = str(kb_root / ws["id"])
        _Path(notes_path).mkdir(parents=True, exist_ok=True)
    return ws, _Path(notes_path), None


def get_workspace_notes_tool(*, state, workspace: str) -> dict:
    """Read workspace notes."""
    ws, notes_dir, err = _get_notes_dir(state, workspace)
    if err:
        return err
    notes_file = notes_dir / "notes.md"
    if not notes_file.exists():
        return {"success": True, "content": "", "message": "No notes yet."}
    return {
        "success": True,
        "content": notes_file.read_text(encoding="utf-8"),
        "path": str(notes_file),
    }


def save_workspace_notes_tool(*, state, workspace: str, content: str) -> dict:
    """Save workspace notes."""
    ws, notes_dir, err = _get_notes_dir(state, workspace)
    if err:
        return err
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes_file = notes_dir / "notes.md"
    notes_file.write_text(content, encoding="utf-8")
    return {
        "success": True,
        "path": str(notes_file),
        "message": f"Notes saved ({len(content)} chars).",
    }


def append_lab_book_tool(*, state, entry: str, entry_type: str = "note",
                         project: str = "", workspace: str = "",
                         when=None) -> dict:
    """Append an entry to the lab notebook (daily research journal).

    ``when`` — optional ``datetime``/ISO string to back-date the entry, e.g.
    when completing a session that's been idle for days.
    """
    from datetime import datetime
    from distillate.lab_notebook import append_entry

    proj = project or workspace  # backward compat
    dt = None
    if when is not None:
        if isinstance(when, str):
            try:
                dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
            except ValueError:
                dt = None
        elif isinstance(when, datetime):
            dt = when
    return append_entry(entry=entry, entry_type=entry_type, project=proj, when=dt)


def read_lab_notebook_tool(*, state, n: int = 20, date: str = "",
                           project: str = "") -> dict:
    """Read recent lab notebook entries."""
    from distillate.lab_notebook import read_recent

    return read_recent(n=n, date=date, project=project)


def notebook_digest_tool(*, state, days: int = 7) -> dict:
    """Generate a weekly digest from lab notebook entries."""
    from distillate.lab_notebook import generate_digest

    return generate_digest(days=days)
