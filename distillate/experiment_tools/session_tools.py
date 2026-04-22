"""Session management tools — launch, stop, sweep, steer, continue."""

import logging
from pathlib import Path as _Path

from ._helpers import _resolve_project, _run_summary, _compute_time_info, _regen_notebook

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

SCHEMAS = [
    {
        "name": "launch_experiment",
        "description": (
            "USE WHEN the user has approved a scaffolded experiment "
            "(after init_experiment showed them the PROMPT.md) and asks "
            "to launch / start / kick off / run it. Spawns a Claude Code "
            "session in a tmux window with the project's PROMPT.md. "
            "Always confirm with the user before calling — this starts "
            "an autonomous agent that will run for an extended time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "model": {
                    "type": "string",
                    "description": "Claude model to use (default: claude-sonnet-4-5-20250929)",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Max turns for the session (default: 100)",
                },
                "host": {
                    "type": "string",
                    "description": "SSH host for remote launch (optional — local by default)",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "experiment_status",
        "description": (
            "Check status of running experiment sessions. Shows active "
            "tmux sessions, run counts, and how long they've been running. "
            "If no project specified, shows all experiments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index (optional — shows all if omitted)",
                },
            },
        },
    },
    {
        "name": "stop_experiment",
        "description": (
            "Stop a running experiment session by sending C-c to its tmux window. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "sweep_experiment",
        "description": (
            "Launch a parallel hyperparameter sweep. Spawns one tmux session "
            "per configuration variant, each with a modified PROMPT.md that "
            "injects the specific hyperparameters. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "configs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "description": "Hyperparameter dict for this variant",
                    },
                    "description": (
                        "List of config dicts, one per variant. "
                        "Example: [{\"lr\": 0.001}, {\"lr\": 0.01}]"
                    ),
                },
                "model": {
                    "type": "string",
                    "description": "Claude model to use (default: claude-sonnet-4-5-20250929)",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Max turns per session (default: 100)",
                },
            },
            "required": ["project", "configs"],
        },
    },
    {
        "name": "continue_experiment",
        "description": (
            "Continue an experiment that hasn't met its goals yet. "
            "Launches a new session with prior-run context appended so "
            "the agent builds on previous results. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "model": {
                    "type": "string",
                    "description": "Claude model to use (default: claude-sonnet-4-5-20250929)",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Max turns for the session (default: 100)",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "steer_experiment",
        "description": (
            "Steer a running (or about-to-launch) experiment. If a tmux "
            "session is live, the text is typed into its Claude Code TUI "
            "and submitted immediately — it becomes the agent's next user "
            "turn. Always also saved to .distillate/steering.md as a "
            "durable record (inlined into the launch prompt if the session "
            "is restarted). Use when the user wants to guide the "
            "experiment in a specific direction (e.g., 'try lower learning "
            "rate', 'focus on regularization')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "text": {
                    "type": "string",
                    "description": "Steering instructions for the next session",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Optionally adjust per-run time budget (minutes). 0 = no change.",
                },
            },
            "required": ["project", "text"],
        },
    },
    {
        "name": "ask_experimentalist",
        "description": (
            "Ask a quick question to a running Experimentalist agent without "
            "redirecting its research flow. Captures the current terminal "
            "output (often enough to answer passively) and, if the session "
            "is live, injects '/btw <question>' into the Claude Code TUI — "
            "Claude Code's built-in /btw command spawns a read-only ephemeral "
            "sub-agent that answers the question without disrupting the main "
            "research loop or persisting in chat history. "
            "Use for queries like 'what's the current loss?', 'what are you "
            "testing next?', 'why did run 3 crash?'. "
            "DIFFERENT from steer_experiment: steer is a directive (change "
            "direction), ask is a query (answer and keep going)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "question": {
                    "type": "string",
                    "description": "The question to ask the running Experimentalist",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Seconds to wait for the /btw response (default 20). "
                        "If the agent is mid-bash, /btw buffers and fires "
                        "between turns — use a longer timeout or call again later."
                    ),
                },
            },
            "required": ["project", "question"],
        },
    },
    {
        "name": "compare_experiments",
        "description": (
            "Compare best metrics across multiple experiment projects. "
            "Shows a side-by-side comparison grid. Different from "
            "compare_runs which compares runs within a single project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "experiments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of project identifiers (id, name, or index)",
                },
            },
            "required": ["experiments"],
        },
    },
    {
        "name": "queue_sessions",
        "description": (
            "Queue N continuation sessions for a project. Sessions run "
            "sequentially, each checking goals before launching the next."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of sessions to queue (default 1)",
                },
                "model": {
                    "type": "string",
                    "description": "Model to use (default: claude-sonnet-4-5-20250929)",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Max turns per session (default 100)",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "list_templates",
        "description": (
            "List available experiment templates that can be used to "
            "scaffold new experiments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "save_template",
        "description": (
            "Save a project's configuration as a reusable experiment "
            "template. Copies PROMPT.md, data files, and scripts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "name": {
                    "type": "string",
                    "description": "Template name (defaults to project slug)",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "create_github_repo",
        "description": (
            "Create a GitHub repository for an experiment project. "
            "Uses the gh CLI to create the repo and push initial code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "name": {
                    "type": "string",
                    "description": "Repository name (defaults to distillate-xp-<project-slug>)",
                },
                "private": {
                    "type": "boolean",
                    "description": "Whether the repo should be private (default false — public for adoption tracking)",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "reading_report",
        "description": (
            "Get reading insights and statistics. Returns lifetime stats, "
            "reading velocity, top topics, engagement distribution, "
            "most-cited papers, and top authors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "manage_session",
        "description": (
            "USE WHEN the user asks to control the lifecycle of an "
            "EXISTING experiment — stop, restart, continue (resume from "
            "prior runs), or check what's running. Preferred over "
            "individual launch/stop/status tools. Actions: 'start' "
            "launches a new session, 'stop' stops running sessions, "
            "'restart' stops then starts, 'continue' launches a "
            "continuation with prior-run context, 'status' checks what's "
            "running. Confirm with the user before any write action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "restart", "continue", "status"],
                    "description": "What to do with the session",
                },
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Claude model to use (default: claude-sonnet-4-5-20250929). "
                        "Only for start/restart/continue."
                    ),
                },
                "max_turns": {
                    "type": "integer",
                    "description": (
                        "Max turns for the session (default: 100). "
                        "Only for start/restart/continue."
                    ),
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": (
                        "Override the project's time budget per iteration "
                        "(in minutes). Only for start/restart/continue."
                    ),
                },
            },
            "required": ["action", "project"],
        },
    },
]

# ---------------------------------------------------------------------------
# Implementation functions
# ---------------------------------------------------------------------------


def launch_experiment_tool(*, state, project: str,
                           model: str = "claude-sonnet-4-5-20250929",
                           max_turns: int = 100,
                           host: str | None = None) -> dict:
    """Launch an auto-research experiment session."""
    from pathlib import Path as _Path

    from distillate.launcher import launch_experiment
    from distillate.state import acquire_lock, release_lock

    proj, err = _resolve_project(state, project)
    if err:
        return err

    # v2 rule: "1 Agent, N Runs sequentially." Refuse to spawn a second
    # session while one is already running — double-stacked sessions are
    # how phantom runs and run_number drift accumulate.
    active_sessions = [
        (sid, s) for sid, s in proj.get("sessions", {}).items()
        if s.get("status") == "running"
    ]
    if active_sessions:
        active_names = [s.get("tmux_session", sid) for sid, s in active_sessions]
        return {
            "success": False,
            "error": (
                f"Project '{proj.get('name', project)}' already has an active "
                f"session ({', '.join(active_names)}). Stop or conclude it "
                "before launching another."
            ),
            "active_sessions": active_names,
        }

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    try:
        session_data = launch_experiment(
            _Path(proj_path),
            host=host,
            model=model,
            max_turns=max_turns,
            project=proj,
        )
    except (FileNotFoundError, RuntimeError) as e:
        return {"error": str(e)}

    # Save session to state
    acquire_lock()
    try:
        state.reload()
        state.add_session(proj["id"], session_data["session_id"], session_data)
        state.save()
    finally:
        release_lock()

    # Log to lab notebook
    try:
        from distillate.lab_notebook import append_entry
        append_entry(
            f"Experiment launched on '{proj.get('name', '')}' ({model})",
            entry_type="experiment",
            project=proj.get("name", ""),
        )
    except Exception:
        pass

    return {
        "success": True,
        "tmux_session": session_data["tmux_session"],
        "model": model,
        "max_turns": max_turns,
        "host": host,
        "message": (
            f"Launched session '{session_data['tmux_session']}' for "
            f"'{proj.get('name', '')}'. Use experiment_status to monitor."
        ),
        # Hint to the WebSocket layer: branch to a fresh Nicolas thread
        # named after this experiment, so the setup chatter stays in the
        # current thread and the running-experiment discussion gets its
        # own clean surface. Consumed in server.ws_chat.
        "_thread_branch": {"name": proj.get("name", "New Experiment")},
    }


def experiment_status_tool(*, state, project: str = "") -> dict:
    """Check status of running experiment sessions."""
    from distillate.launcher import refresh_session_statuses

    changed = refresh_session_statuses(state)
    if changed:
        state.save()

    if project:
        proj, err = _resolve_project(state, project)
        if err:
            return err
        projects = {proj["id"]: proj}
    else:
        projects = state.experiments

    results = []
    for proj_id, proj in projects.items():
        sessions = proj.get("sessions", {})
        runs = proj.get("runs", {})
        active = [s for s in sessions.values() if s.get("status") == "running"]

        proj_info = {
            "name": proj.get("name", ""),
            "status": proj.get("status", ""),
            "total_runs": len(runs),
            "active_sessions": len(active),
            "sessions": [],
        }

        for sess in sessions.values():
            started = sess.get("started_at", "")
            proj_info["sessions"].append({
                "tmux_session": sess.get("tmux_session", ""),
                "status": sess.get("status", ""),
                "started_at": started,
                "model": sess.get("model", ""),
                "host": sess.get("host"),
            })

        # Time budget info for projects with active sessions
        if active:
            time_info = _compute_time_info(proj)
            if time_info:
                proj_info["time"] = time_info

        results.append(proj_info)

    total_active = sum(p["active_sessions"] for p in results)
    return {
        "experiments": results,
        "total_active_sessions": total_active,
    }


def stop_experiment_tool(*, state, project: str) -> dict:
    """Stop a running experiment session."""
    from datetime import datetime, timezone

    from distillate.launcher import stop_session
    from distillate.state import acquire_lock, release_lock

    proj, err = _resolve_project(state, project)
    if err:
        return err

    sessions = proj.get("sessions", {})
    running = [(sid, s) for sid, s in sessions.items() if s.get("status") == "running"]

    if not running:
        return {"error": f"No running sessions for '{proj.get('name', '')}'."}

    stopped = []
    failed = []
    for sess_id, sess in running:
        tmux_name = sess.get("tmux_session", "")
        host = sess.get("host")
        ok = stop_session(tmux_name, host)
        if ok:
            stopped.append(tmux_name)
        else:
            failed.append(tmux_name)

    # Update state
    acquire_lock()
    try:
        state.reload()
        now = datetime.now(timezone.utc).isoformat()
        for sess_id, sess in running:
            tmux_name = sess.get("tmux_session", "")
            if tmux_name in stopped:
                state.update_session(proj["id"], sess_id,
                                     status="completed", completed_at=now)
        state.save()
    finally:
        release_lock()

    if failed:
        return {
            "success": False,
            "stopped": stopped,
            "failed": failed,
            "message": f"Stopped {len(stopped)}, failed {len(failed)} session(s).",
        }

    # Log to lab notebook
    try:
        from distillate.lab_notebook import append_entry
        append_entry(
            f"Experiment stopped on '{proj.get('name', '')}' ({len(stopped)} session(s))",
            entry_type="experiment",
            project=proj.get("name", ""),
        )
    except Exception:
        pass

    return {
        "success": True,
        "stopped": stopped,
        "message": f"Stopped {len(stopped)} session(s) for '{proj.get('name', '')}'.",
    }


def sweep_experiment_tool(*, state, project: str,
                          configs: list[dict],
                          model: str = "claude-sonnet-4-5-20250929",
                          max_turns: int = 100) -> dict:
    """Launch a parallel hyperparameter sweep."""
    from pathlib import Path as _Path

    from distillate.launcher import launch_sweep
    from distillate.state import acquire_lock, release_lock

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    if not configs or len(configs) < 2:
        return {"error": "Provide at least 2 config variants for a sweep."}

    try:
        sessions = launch_sweep(
            _Path(proj_path), proj, configs,
            model=model, max_turns=max_turns,
        )
    except (FileNotFoundError, RuntimeError) as e:
        return {"error": str(e)}

    # Save all sessions to state
    acquire_lock()
    try:
        state.reload()
        for sd in sessions:
            state.add_session(proj["id"], sd["session_id"], sd)
        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "variants": len(sessions),
        "sessions": [s["tmux_session"] for s in sessions],
        "model": model,
        "message": (
            f"Launched {len(sessions)}-variant sweep for "
            f"'{proj.get('name', '')}'. Use experiment_status to monitor."
        ),
    }


def continue_experiment_tool(*, state, project: str,
                             model: str = "claude-sonnet-4-5-20250929",
                             max_turns: int = 100) -> dict:
    """Launch a continuation session with prior-run context."""
    from pathlib import Path as _Path

    from distillate.launcher import launch_continuation, should_continue
    from distillate.state import acquire_lock, release_lock

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    if not should_continue(proj):
        return {
            "success": False,
            "message": (
                f"All goals for '{proj.get('name', '')}' appear to be met. "
                "No continuation needed."
            ),
        }

    try:
        session_data = launch_continuation(
            _Path(proj_path), proj, model=model, max_turns=max_turns,
        )
    except (FileNotFoundError, RuntimeError) as e:
        return {"error": str(e)}

    acquire_lock()
    try:
        state.reload()
        state.add_session(proj["id"], session_data["session_id"], session_data)
        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "tmux_session": session_data["tmux_session"],
        "model": model,
        "max_turns": max_turns,
        "message": (
            f"Launched continuation session '{session_data['tmux_session']}' "
            f"for '{proj.get('name', '')}' with prior-run context."
        ),
    }


def steer_experiment_tool(*, state, project: str, text: str,
                          duration_minutes: int = 0) -> dict:
    """Write steering instructions and inject live into any running session.

    Always writes ``.distillate/steering.md`` (durable record + fallback
    for sessions not yet running). If the project has a running tmux
    session, also types the text into its Claude Code TUI and submits it
    — the steering becomes the agent's next user turn, no wait for a
    bash command to fire the post_bash hook.
    """
    from pathlib import Path as _Path

    from distillate.launcher import (
        inject_into_tmux, write_budget_json, write_steering,
    )

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    path = write_steering(_Path(proj_path), text)
    preview = text[:200] + ("..." if len(text) > 200 else "")

    # Live injection — type + Enter into the running session's Claude Code
    # TUI. Falls back silently to the steering.md path (above) if nothing
    # is running or the inject fails.
    injected_into: list[str] = []
    for sess in proj.get("sessions", {}).values():
        if sess.get("status") != "running":
            continue
        tmux_name = sess.get("tmux_session", "")
        host = sess.get("host")
        if tmux_name and inject_into_tmux(tmux_name, text, host=host)["ok"]:
            injected_into.append(tmux_name)
            break  # 1 agent per experiment — stop after first live session

    # Mid-session budget adjustment
    budget_msg = ""
    if duration_minutes > 0:
        state.update_experiment(proj["id"], duration_minutes=duration_minutes)
        state.save()
        proj = state.get_experiment(proj["id"]) or proj
        write_budget_json(_Path(proj_path), proj)
        budget_msg = f" Run budget updated to {duration_minutes} min."

    if injected_into:
        delivery = (
            f"delivered live to running session '{injected_into[0]}' "
            "(typed + submitted)."
        )
    else:
        delivery = (
            "queued in .distillate/steering.md — picked up at next "
            "session launch or on the running session's next tool use."
        )

    return {
        "success": True,
        "path": str(path),
        "preview": preview,
        "injected_live": bool(injected_into),
        "message": (
            f"Steering for '{proj.get('name', '')}' {delivery}{budget_msg}"
        ),
    }


def _extract_btw_response(pre_lines: list[str], post_pane: str, question: str) -> str:
    """Extract the /btw sub-agent's response from the post-injection pane.

    The /btw overlay shows:
      - A header with our question (echoed, possibly with ❯ prefix)
      - Possibly previous /btw questions from this session's history
      - The sub-agent's actual answer
      - A footer: "↑/↓ to scroll · f to fork · x to clear history · Esc to dismiss"

    Strategy: collect lines new vs the pre-injection snapshot, then strip
    the echoed question, any /btw history lines, the footer, and UI chrome.
    Returns clean response text or "".
    """
    pre_set = set(pre_lines)

    _FOOTER_FRAGMENTS = ("↑/↓ to scroll", "Esc to dismiss", "to fork", "to clear history")
    _SPINNERS = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    new_lines = []
    for line in post_pane.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in pre_set:
            continue
        # Skip any /btw command line (our question or history entries)
        bare = stripped.lstrip("❯ ❯").strip()
        if bare.startswith("/btw ") or bare == "/btw":
            continue
        # Skip the overlay footer
        if any(f in stripped for f in _FOOTER_FRAGMENTS):
            continue
        # Skip Claude Code spinner frames
        if stripped.startswith(_SPINNERS):
            continue
        new_lines.append(stripped)

    return "\n".join(new_lines).strip()


def ask_experimentalist_tool(*, state, project: str, question: str,
                             timeout: int = 20) -> dict:
    """Inject /btw into the Experimentalist's Claude Code TUI and capture the reply.

    Uses Claude Code's built-in /btw slash command, which spawns a read-only
    ephemeral sub-agent — cheap (reuses prompt cache), non-disruptive (main
    research loop resumes automatically), and off-record (not saved to history).

    If the agent is mid-bash, /btw buffers in the PTY and fires between turns.
    We poll capture-pane for up to `timeout` seconds; if nothing appears in
    that window, we return the current pane with a note so Nicolas can retry.

    Different from steer_experiment: steer is a directive (change course),
    ask is a query (answer and continue).
    """
    import time
    from datetime import datetime, timezone

    from distillate.launcher import capture_pane, inject_into_tmux

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    # Find the running tmux session
    running_session = None
    host = None
    for sess in proj.get("sessions", {}).values():
        if sess.get("status") == "running":
            tmux_name = sess.get("tmux_session", "")
            if tmux_name:
                running_session = tmux_name
                host = sess.get("host")
                break

    if not running_session:
        return {
            "success": False,
            "running": False,
            "error": (
                f"No active session for '{proj.get('name', project)}'. "
                "The Experimentalist is not currently running. "
                "Use steer_experiment to queue instructions for the next session."
            ),
        }

    # Snapshot the pane before injection so we can diff the response
    pre_pane = capture_pane(running_session, lines=100, escapes=False)
    pre_lines = [l.strip() for l in pre_pane.splitlines() if l.strip()]

    # Log the question to .distillate/btw.md for traceability
    try:
        btw_path = _Path(proj_path) / ".distillate" / "btw.md"
        btw_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(btw_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] Nicolas asks: {question}\n")
    except OSError:
        pass

    # Inject /btw — Claude Code's native slash command for ephemeral queries
    btw_text = f"/btw {question}"
    injected = inject_into_tmux(running_session, btw_text, host=host)["ok"]

    if not injected:
        return {
            "success": False,
            "running": True,
            "session": running_session,
            "injected": False,
            "current_pane": pre_pane.strip(),
            "error": "Failed to inject into tmux session.",
        }

    # Poll capture-pane until new content stabilises or timeout expires.
    # We consider the response "done" when the pane hasn't changed for 1.5s.
    deadline = time.monotonic() + timeout
    last_pane = pre_pane
    stable_since = None
    response = ""

    while time.monotonic() < deadline:
        time.sleep(0.5)
        current_pane = capture_pane(running_session, lines=100, escapes=False)

        if current_pane == last_pane:
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= 1.5:
                # Pane stable for 1.5s — extract response and stop polling
                candidate = _extract_btw_response(pre_lines, current_pane, question)
                if candidate:
                    response = candidate
                break
        else:
            stable_since = None
            last_pane = current_pane

    # Try extraction one last time from whatever the pane shows now
    if not response:
        response = _extract_btw_response(
            pre_lines, capture_pane(running_session, lines=100, escapes=False), question
        )

    # Dismiss the /btw overlay with Escape so the Experimentalist resumes.
    # Space / Enter also work; Escape is cleanest (won't accidentally submit
    # anything if the overlay has already auto-closed).
    if response:
        time.sleep(0.2)
        inject_into_tmux(running_session, keys=["Escape"], host=host)
        time.sleep(0.3)

    # Capture the restored pane state so Nicolas can see the agent is back
    final_pane = capture_pane(running_session, lines=100, escapes=False)

    # Append the answer to btw.md for traceability
    if response:
        try:
            with open(btw_path, "a", encoding="utf-8") as f:
                f.write(f"  → {response[:500]}\n")
        except OSError:
            pass

    timed_out = not response
    return {
        "success": True,
        "running": True,
        "session": running_session,
        "injected": True,
        "answer": response or None,
        "current_pane": final_pane.strip(),
        "timed_out": timed_out,
        "message": (
            f"btw answered by '{proj.get('name', project)}'."
            if response
            else (
                f"No response within {timeout}s — agent may be mid-task. "
                "The /btw is buffered and will fire between turns. "
                "Call again to check current_pane for the answer."
            )
        ),
    }


def manage_session_tool(*, state, action: str, project: str,
                        model: str = "claude-sonnet-4-5-20250929",
                        max_turns: int = 100,
                        duration_minutes: int = 0) -> dict:
    """Unified session management: start, stop, restart, continue, status."""
    # If duration_minutes override is provided, persist and rewrite budget.json
    if duration_minutes > 0 and action in ("start", "restart", "continue"):
        from pathlib import Path as _Path
        from distillate.launcher import write_budget_json
        proj, _ = _resolve_project(state, project)
        if proj:
            state.update_experiment(proj["id"], duration_minutes=duration_minutes)
            state.save()
            proj = state.get_experiment(proj["id"]) or proj
            proj_path = proj.get("path", "")
            if proj_path:
                write_budget_json(_Path(proj_path), proj)
    if action == "status":
        return experiment_status_tool(state=state, project=project)
    elif action == "stop":
        return stop_experiment_tool(state=state, project=project)
    elif action == "start":
        return launch_experiment_tool(
            state=state, project=project, model=model, max_turns=max_turns,
        )
    elif action == "continue":
        return continue_experiment_tool(
            state=state, project=project, model=model, max_turns=max_turns,
        )
    elif action == "restart":
        # Stop first, then start
        stop_result = stop_experiment_tool(state=state, project=project)
        if stop_result.get("error"):
            # No running session to stop — just start fresh
            pass
        start_result = launch_experiment_tool(
            state=state, project=project, model=model, max_turns=max_turns,
        )
        if stop_result.get("stopped"):
            start_result["previously_stopped"] = stop_result["stopped"]
        return start_result
    else:
        return {"error": f"Unknown action '{action}'. Use: start, stop, restart, continue, status."}


def compare_experiments_tool(*, state, projects: list[str]) -> dict:
    """Compare best metrics across multiple projects."""
    if len(projects) < 2:
        return {"error": "Need at least 2 projects to compare."}

    comparison = []
    all_metrics: set[str] = set()

    for identifier in projects:
        proj, err = _resolve_project(state, identifier)
        if err:
            return err

        best: dict[str, float] = {}
        for run in proj.get("runs", {}).values():
            decision = run.get("decision") or run.get("status", "")
            if decision == "crash":
                continue
            for k, v in run.get("results", {}).items():
                if not isinstance(v, (int, float)):
                    continue
                lower = any(t in k.lower() for t in ("loss", "error", "perplexity", "mse", "mae"))
                if k not in best:
                    best[k] = v
                elif lower:
                    best[k] = min(best[k], v)
                else:
                    best[k] = max(best[k], v)
                all_metrics.add(k)

        comparison.append({
            "id": proj.get("id", ""),
            "name": proj.get("name", ""),
            "run_count": len(proj.get("runs", {})),
            "best_metrics": best,
            "goals": proj.get("goals", []),
        })

    return {
        "experiments": comparison,
        "metrics": sorted(all_metrics),
    }


def queue_sessions_tool(*, state, project: str, count: int = 1,
                        model: str = "claude-sonnet-4-5-20250929",
                        max_turns: int = 100) -> dict:
    """Queue N continuation sessions for a project."""
    proj, err = _resolve_project(state, project)
    if err:
        return err

    state.update_experiment(proj["id"], continuation_queue={
        "count": count,
        "model": model,
        "max_turns": max_turns,
    }, auto_continue=True)
    state.save()

    return {
        "success": True,
        "project": proj.get("name", ""),
        "queued": count,
        "model": model,
        "max_turns": max_turns,
        "message": f"Queued {count} continuation session(s) for '{proj.get('name', '')}'.",
    }


def list_templates_tool(*, state) -> dict:
    """List available experiment templates."""
    from distillate.launcher import list_templates

    templates = list_templates()
    return {
        "templates": templates,
        "total": len(templates),
    }


def save_template_tool(*, state, project: str, name: str = "") -> dict:
    """Save a project config as a reusable template."""
    from pathlib import Path as _Path

    from distillate.launcher import import_template

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    template_name = import_template(_Path(proj_path), name=name or None)
    return {
        "success": True,
        "template_name": template_name,
        "message": f"Saved template '{template_name}' from project '{proj.get('name', '')}'.",
    }


def create_github_repo_tool(*, state, project: str, name: str = "",
                             private: bool = False) -> dict:
    """Create a GitHub repo for a project."""
    from pathlib import Path as _Path

    from distillate.launcher import create_github_repo

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    repo_name = name or f"distillate-xp-{proj.get('id', 'experiment')}"
    result = create_github_repo(_Path(proj_path), repo_name, private=private)

    if result.get("ok"):
        state.update_experiment(proj["id"], github_url=result["url"])
        state.save()

    return result


def reading_report_tool(*, state) -> dict:
    """Get reading insights and statistics."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    processed = state.documents_with_status("processed")
    if not processed:
        return {"message": "No processed papers yet."}

    total_papers = len(processed)
    total_pages = sum(d.get("page_count", 0) for d in processed)
    total_words = sum(d.get("highlight_word_count", 0) for d in processed)
    engagements = [d.get("engagement", 0) for d in processed if d.get("engagement")]
    avg_engagement = round(sum(engagements) / len(engagements)) if engagements else 0

    now = datetime.now(timezone.utc)

    # Reading velocity (last 8 weeks)
    week_counts: dict[str, int] = {}
    for doc in processed:
        ts = doc.get("processed_at", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            weeks_ago = (now - dt).days // 7
            if weeks_ago < 8:
                monday = dt - timedelta(days=dt.weekday())
                label = monday.strftime("%b %d")
                week_counts[label] = week_counts.get(label, 0) + 1
        except (ValueError, TypeError):
            pass

    # Top topics
    topic_counter: Counter = Counter()
    for doc in processed:
        for tag in doc.get("metadata", {}).get("tags") or []:
            topic_counter[tag] += 1
    top_topics = [{"topic": t, "count": c} for t, c in topic_counter.most_common(5)]

    # Most-cited
    cited = sorted(
        [d for d in processed if d.get("metadata", {}).get("citation_count", 0) > 0],
        key=lambda d: d.get("metadata", {}).get("citation_count", 0),
        reverse=True,
    )
    top_cited = [
        {"title": d["title"][:60], "citations": d["metadata"]["citation_count"]}
        for d in cited[:5]
    ]

    # Top authors
    author_counter: Counter = Counter()
    for doc in processed:
        for author in doc.get("authors", []):
            if author and author.lower() != "unknown":
                author_counter[author] += 1
    top_authors = [
        {"author": a, "count": c}
        for a, c in author_counter.most_common(5) if c >= 2
    ]

    return {
        "lifetime": {
            "papers": total_papers,
            "pages": total_pages,
            "words_highlighted": total_words,
            "avg_engagement": avg_engagement,
        },
        "velocity": week_counts,
        "top_topics": top_topics,
        "most_cited": top_cited,
        "top_authors": top_authors,
    }
