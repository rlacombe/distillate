"""Long-lived agent management tools."""

import logging
import os
import shlex
from pathlib import Path as _Path

log = logging.getLogger(__name__)

SCHEMAS = [
    {
        "name": "create_agent",
        "description": (
            "Create a new long-lived agent. Agents are persistent, interactive "
            "AI assistants with names and personalities (e.g. a research librarian, "
            "a code reviewer). Each gets its own CLAUDE.md and terminal session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Display name for the agent",
                },
                "personality": {
                    "type": "string",
                    "description": "Personality / system prompt written to the agent's CLAUDE.md",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model override (e.g. 'claude-sonnet-4-6')",
                },
                "agent_type": {
                    "type": "string",
                    "description": "Agent backend: 'claude' (default) or a Pi variant id",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Directory where the agent runs (e.g. a project repo path)",
                },
                "command": {
                    "type": "string",
                    "description": "Custom shell command to run instead of Claude Code",
                },
                "template": {
                    "type": "string",
                    "description": "Template id (e.g. 'librarian', 'read-papers'). Pre-fills personality from the template.",
                },
                "experiment_id": {
                    "type": "string",
                    "description": "Link agent to a workspace project. Uses project repo as working_dir if none given.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_agent_templates",
        "description": (
            "List available agent templates — specialist personas for research, "
            "analysis, writing, and monitoring tasks. Use with create_agent's template "
            "parameter to spawn a specialist agent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional filter: research, analysis, writing, monitoring",
                },
            },
        },
    },
    {
        "name": "list_agents",
        "description": (
            "List all long-lived agents with their status (running/stopped). "
            "Use when the user asks about their agents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "start_agent_session",
        "description": (
            "Start a long-lived agent's terminal session. Spawns a Claude Code "
            "process in tmux using the agent's CLAUDE.md personality. "
            "Pass initial_task to give the agent a specific mission."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent id or name substring",
                },
                "initial_task": {
                    "type": "string",
                    "description": "Initial task/prompt for the agent (e.g. 'Read the paper X and extract key findings')",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "stop_agent_session",
        "description": "Stop a running agent's terminal session.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent id or name substring",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "update_agent",
        "description": "Update a long-lived agent's name, personality, model, working directory, or command.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent id or name substring",
                },
                "name": {
                    "type": "string",
                    "description": "New display name",
                },
                "personality": {
                    "type": "string",
                    "description": "New personality text (overwrites CLAUDE.md)",
                },
                "model": {
                    "type": "string",
                    "description": "New model override",
                },
                "working_dir": {
                    "type": "string",
                    "description": "New working directory path",
                },
                "command": {
                    "type": "string",
                    "description": "New custom shell command",
                },
            },
            "required": ["agent"],
        },
    },
    {
        "name": "delete_agent",
        "description": (
            "Delete a long-lived agent and its config directory. "
            "Cannot delete built-in agents like Nicolas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent id or name substring",
                },
            },
            "required": ["agent"],
        },
    },
]


# ---------------------------------------------------------------------------
# Implementation functions
# ---------------------------------------------------------------------------

def _find_agent(state, query: str):
    """Find an agent by id or name substring."""
    agent = state.get_agent(query)
    if agent:
        return agent
    for _aid, a in state.agents.items():
        if query.lower() in a.get("name", "").lower():
            return a
    return None


def _check_tmux_alive(tmux_name: str) -> bool:
    """Return True if a tmux session with the given name exists."""
    import subprocess
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", tmux_name],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def list_agent_templates_tool(*, state, category: str = "") -> dict:
    """List available agent templates."""
    from distillate.agent_templates import list_all_templates
    templates = list_all_templates()
    if category:
        templates = [t for t in templates if t["category"] == category]
    # Return summary (no full personality text — keep response small)
    return {
        "success": True,
        "templates": [
            {k: v for k, v in t.items() if k != "personality"}
            for t in templates
        ],
    }


def create_agent_tool(*, state, name: str, personality: str = "",
                      model: str = "", agent_type: str = "claude",
                      working_dir: str = "", command: str = "",
                      template: str = "", experiment_id: str = "") -> dict:
    """Create a long-lived agent, optionally from a template."""
    from distillate.experiments import slugify
    from distillate.state import acquire_lock, release_lock
    from distillate.agents import ensure_agent_dir

    agent_id = slugify(name)

    # Resolve template if provided
    if template:
        from distillate.agent_templates import get_template
        tmpl = get_template(template)
        if not tmpl:
            return {"success": False, "error": f"Unknown template: {template}"}
        # Template fills in personality if not explicitly provided
        if not personality:
            personality = tmpl.personality
        # Template suggests working_dir if not explicitly provided
        if not working_dir and tmpl.suggested_working_dir != "config_dir":
            working_dir = _resolve_template_dir(tmpl.suggested_working_dir, state, experiment_id)

    # Validate working_dir if provided
    if working_dir:
        wd = _Path(working_dir).expanduser().resolve()
        if not wd.is_dir():
            return {"success": False, "error": f"Directory not found: {working_dir}"}

    acquire_lock()
    try:
        state.reload()
        if state.get_agent(agent_id):
            return {"success": False, "error": f"Agent already exists: {agent_id}"}
        agent = state.add_agent(agent_id, name, agent_type=agent_type, model=model,
                                working_dir=working_dir, command=command,
                                experiment_id=experiment_id or None)
        state.save()
    finally:
        release_lock()

    ensure_agent_dir(agent_id, name, personality)

    return {
        "success": True,
        "agent": agent,
        "message": f"Created agent '{name}' ({agent_id}).",
    }


def _resolve_template_dir(pattern: str, state, experiment_id: str) -> str:
    """Resolve a template's suggested_working_dir pattern to an actual path."""
    from distillate.config import CONFIG_DIR
    if pattern == "project_repo" and experiment_id:
        ws = state.get_workspace(experiment_id)
        if ws:
            repos = ws.get("repos", [])
            if repos:
                return repos[0].get("path", "")
            if ws.get("root_path"):
                return ws["root_path"]
    elif pattern == "knowledge_dir":
        d = CONFIG_DIR / "knowledge"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    return ""


def list_agents_tool(*, state) -> dict:
    """List all long-lived agents with live session status."""
    agents = []
    dirty = False
    for _aid, agent in state.agents.items():
        a = dict(agent)
        tmux = a.get("tmux_name", "")
        expected_tmux = f"agent-{a['id']}"

        if a.get("session_status") == "running" and tmux:
            # State says running — verify tmux is still alive
            if not _check_tmux_alive(tmux):
                a["session_status"] = "stopped"
                a["tmux_name"] = ""
                state.update_agent(a["id"], session_status="stopped", tmux_name="")
                dirty = True
        elif a.get("session_status") != "running" or not tmux:
            # State says stopped or tmux_name is empty — check if a session
            # survived a server restart (tmux outlives the Python process)
            if _check_tmux_alive(expected_tmux):
                a["session_status"] = "running"
                a["tmux_name"] = expected_tmux
                state.update_agent(a["id"], session_status="running", tmux_name=expected_tmux)
                dirty = True

        agents.append(a)
    if dirty:
        state.save()
    return {"success": True, "agents": agents}


def get_agent_details_tool(*, state, agent: str) -> dict:
    """Get full details of a long-lived agent including CLAUDE.md content."""
    a = _find_agent(state, agent)
    if not a:
        return {"success": False, "error": f"Agent not found: {agent}"}

    result = dict(a)

    # Check tmux liveness
    tmux = result.get("tmux_name", "")
    if tmux and result.get("session_status") == "running":
        if not _check_tmux_alive(tmux):
            result["session_status"] = "stopped"
            result["tmux_name"] = ""
            state.update_agent(a["id"], session_status="stopped", tmux_name="")
            state.save()

    # Read CLAUDE.md if it exists
    config_dir = _Path(result.get("config_dir", "")).expanduser()
    claude_md = config_dir / "CLAUDE.md"
    if claude_md.exists():
        result["claude_md"] = claude_md.read_text(encoding="utf-8")

    return {"success": True, "agent": result}


def start_agent_session_tool(*, state, agent: str, initial_task: str = "") -> dict:
    """Start a tmux session for a long-lived agent."""
    import subprocess
    from distillate.state import acquire_lock, release_lock
    from distillate.agents import ensure_agent_dir

    a = _find_agent(state, agent)
    if not a:
        return {"success": False, "error": f"Agent not found: {agent}"}

    if a.get("agent_type") == "nicolas":
        return {"success": False, "error": "Nicolas uses the chat panel, not a terminal session."}

    # Check if already running
    tmux = a.get("tmux_name", "")
    if tmux and _check_tmux_alive(tmux):
        return {
            "success": True,
            "already_running": True,
            "tmux_name": tmux,
            "message": f"Agent '{a['name']}' is already running.",
        }

    # Ensure config dir exists
    config_dir = ensure_agent_dir(a["id"], a["name"])
    tmux_name = f"agent-{a['id']}"

    # Determine working directory: explicit > config dir
    working_dir = a.get("working_dir", "")
    if working_dir:
        cwd = str(_Path(working_dir).expanduser().resolve())
    else:
        cwd = str(config_dir)

    # Build command: custom command > claude CLI
    custom_cmd = a.get("command", "")
    if custom_cmd:
        cmd = custom_cmd
    else:
        parts = ["claude", "--permission-mode", "auto"]
        model = a.get("model", "")
        if model:
            parts.extend(["--model", model])
        if initial_task:
            parts.append(shlex.quote(initial_task))
        cmd = " ".join(parts)

    # Spawn tmux session — login shell for PATH from .zprofile; skip -i so
    # .zshrc is not sourced (avoids startup banners/ASCII art).
    shell = os.environ.get("SHELL", "/bin/zsh")
    shell_cmd = f"{shell} -l -c {shlex.quote(cmd)}"
    tmux_cmd = f"tmux new-session -d -x 220 -y 50 -s {shlex.quote(tmux_name)} -c {shlex.quote(cwd)} {shlex.quote(shell_cmd)}"
    try:
        subprocess.run(tmux_cmd, shell=True, check=True, timeout=10)
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": f"Failed to start tmux session: {e}"}

    # Configure for embedded xterm.js
    subprocess.run(["tmux", "set", "-t", tmux_name, "status", "off"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "mouse", "on"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", tmux_name, "escape-time", "0"], capture_output=True)

    # Update state
    acquire_lock()
    try:
        state.reload()
        state.update_agent(a["id"],
                           tmux_name=tmux_name,
                           session_status="running",
                           last_active_at=__import__("datetime").datetime.now(
                               __import__("datetime").timezone.utc).isoformat())
        state.save()
    finally:
        release_lock()

    # Log to lab notebook
    try:
        from distillate.lab_notebook import append_entry
        append_entry(
            f"Agent '{a['name']}' session started",
            entry_type="session",
            project=a.get("name", ""),
        )
    except Exception:
        pass

    return {
        "success": True,
        "tmux_name": tmux_name,
        "message": f"Started agent '{a['name']}'. Attach with: tmux attach -t {tmux_name}",
    }


def stop_agent_session_tool(*, state, agent: str) -> dict:
    """Stop a running agent's tmux session."""
    import subprocess
    from distillate.state import acquire_lock, release_lock

    a = _find_agent(state, agent)
    if not a:
        return {"success": False, "error": f"Agent not found: {agent}"}

    tmux = a.get("tmux_name", "")
    if tmux and _check_tmux_alive(tmux):
        try:
            subprocess.run(["tmux", "kill-session", "-t", tmux], capture_output=True, timeout=10)
        except Exception:
            pass

    acquire_lock()
    try:
        state.reload()
        state.update_agent(a["id"], tmux_name="", session_status="stopped")
        state.save()
    finally:
        release_lock()

    # Log to lab notebook
    try:
        from distillate.lab_notebook import append_entry
        append_entry(
            f"Agent '{a['name']}' session stopped",
            entry_type="session",
            project=a.get("name", ""),
        )
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Stopped agent '{a['name']}'.",
    }


def update_agent_tool(*, state, agent: str, name: str = "",
                      personality: str = "", model: str = "",
                      working_dir: str = "", command: str = "") -> dict:
    """Update a long-lived agent's name, personality, model, working dir, or command."""
    from distillate.state import acquire_lock, release_lock

    a = _find_agent(state, agent)
    if not a:
        return {"success": False, "error": f"Agent not found: {agent}"}

    updates = {}
    if name:
        updates["name"] = name
    if model:
        updates["model"] = model
    if working_dir:
        wd = _Path(working_dir).expanduser().resolve()
        if not wd.is_dir():
            return {"success": False, "error": f"Directory not found: {working_dir}"}
        updates["working_dir"] = str(wd)
    if command:
        updates["command"] = command

    if updates:
        acquire_lock()
        try:
            state.reload()
            state.update_agent(a["id"], **updates)
            state.save()
        finally:
            release_lock()

    # Update CLAUDE.md if personality provided
    if personality:
        config_dir = _Path(a.get("config_dir", "")).expanduser()
        config_dir.mkdir(parents=True, exist_ok=True)
        claude_md = config_dir / "CLAUDE.md"
        display_name = name or a.get("name", a["id"])
        claude_md.write_text(f"# {display_name}\n\n{personality}\n", encoding="utf-8")

    return {
        "success": True,
        "message": f"Updated agent '{a.get('name', a['id'])}'.",
    }


def delete_agent_tool(*, state, agent: str) -> dict:
    """Delete a long-lived agent."""
    import shutil as _shutil
    from distillate.state import acquire_lock, release_lock

    a = _find_agent(state, agent)
    if not a:
        return {"success": False, "error": f"Agent not found: {agent}"}

    if a.get("builtin"):
        return {"success": False, "error": f"Cannot delete built-in agent '{a['name']}'."}

    # Stop session if running
    tmux = a.get("tmux_name", "")
    if tmux and _check_tmux_alive(tmux):
        import subprocess
        try:
            subprocess.run(["tmux", "kill-session", "-t", tmux], capture_output=True, timeout=10)
        except Exception:
            pass

    # Remove config dir
    config_dir = _Path(a.get("config_dir", "")).expanduser()
    if config_dir.is_dir():
        _shutil.rmtree(config_dir, ignore_errors=True)

    # Remove from state
    acquire_lock()
    try:
        state.reload()
        state.remove_agent(a["id"])
        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "message": f"Deleted agent '{a['name']}'.",
    }
