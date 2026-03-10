"""Experiment launcher — scaffold, launch, monitor, and record experiments.

Manages the full experiment lifecycle: templates, scaffolding from templates,
tmux-based session management (local + SSH), and state tracking.
"""

import json
import logging
import os
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from distillate.config import CONFIG_DIR

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Template management
# ---------------------------------------------------------------------------

def templates_dir() -> Path:
    """Return templates directory (~/.config/distillate/templates/)."""
    d = CONFIG_DIR / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_templates() -> list[dict]:
    """List available templates.

    Returns [{"name": str, "path": Path, "has_data": bool, "prompt_lines": int}].
    """
    root = templates_dir()
    results = []
    if not root.is_dir():
        return results

    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        prompt = child / "PROMPT.md"
        prompt_lines = 0
        if prompt.exists():
            try:
                prompt_lines = len(prompt.read_text(encoding="utf-8").splitlines())
            except OSError:
                pass
        results.append({
            "name": child.name,
            "path": child,
            "has_data": (child / "data").is_dir(),
            "prompt_lines": prompt_lines,
        })
    return results


def import_template(source: Path, name: str | None = None) -> str:
    """Import an experiment directory as a reusable template.

    Copies PROMPT.md + data/ + *.py files. Returns template name.
    """
    source = source.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source}")

    template_name = name or source.name
    template_name = _slugify(template_name)
    dest = templates_dir() / template_name

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    # Copy PROMPT.md
    prompt = source / "PROMPT.md"
    if prompt.exists():
        shutil.copy2(prompt, dest / "PROMPT.md")

    # Copy data/ directory
    data_dir = source / "data"
    if data_dir.is_dir():
        shutil.copytree(data_dir, dest / "data")

    # Copy *.py files
    for py_file in source.glob("*.py"):
        shutil.copy2(py_file, dest / py_file.name)

    return template_name


def scaffold_experiment(
    template: str,
    target: Path,
    name: str | None = None,
) -> Path:
    """Create a new experiment directory from a template.

    - Copies template contents to target/
    - git init
    - Creates .distillate/ with REPORTING.md
    - Installs Claude Code hooks (.claude/settings.json)
    - Creates .claude/settings.local.json with safe Bash permissions
    Returns experiment path.
    """
    tmpl_dir = templates_dir() / template
    if not tmpl_dir.is_dir():
        raise FileNotFoundError(f"Template not found: {template}")

    target = target.resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Target directory is not empty: {target}")

    target.mkdir(parents=True, exist_ok=True)

    # Copy template contents
    for item in tmpl_dir.iterdir():
        if item.name.startswith("."):
            continue
        dst = target / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)

    # git init (if not already a repo)
    if not (target / ".git").exists():
        subprocess.run(
            ["git", "init"],
            cwd=target,
            capture_output=True,
        )

    # Create .distillate/ with REPORTING.md
    distillate_dir = target / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    reporting_src = Path(__file__).parent / "autoresearch" / "REPORTING.md"
    if reporting_src.exists():
        shutil.copy2(reporting_src, distillate_dir / "REPORTING.md")

    # Install hooks via the shared function
    _install_hooks_into(target)

    # Create .claude/settings.local.json with safe Bash permissions
    claude_dir = target / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_local = claude_dir / "settings.local.json"
    if not settings_local.exists():
        local_config = {
            "permissions": {
                "allow": [
                    "Bash(python3:*)",
                    "Bash(tail:*)",
                    "Bash(ls:*)",
                    "Bash(cat:*)",
                    "Bash(head:*)",
                    "Bash(wc:*)",
                    "Bash(mkdir:*)",
                    "Read",
                    "Write",
                    "Edit",
                    "Glob",
                    "Grep",
                ],
            },
        }
        settings_local.write_text(
            json.dumps(local_config, indent=2) + "\n",
            encoding="utf-8",
        )

    return target


def _install_hooks_into(project_path: Path) -> None:
    """Install Claude Code hooks into a project (shared with main.py --install-hooks)."""
    hooks_src = Path(__file__).parent / "autoresearch" / "hooks.json"
    if not hooks_src.exists():
        return

    hook_config = json.loads(hooks_src.read_text(encoding="utf-8"))

    claude_dir = project_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_file = claude_dir / "settings.json"

    existing: dict = {}
    if settings_file.exists():
        try:
            existing = json.loads(settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # Merge hooks (don't overwrite existing hooks)
    existing_hooks = existing.setdefault("hooks", {})
    for event_type, hook_list in hook_config.get("hooks", {}).items():
        existing_entries = existing_hooks.setdefault(event_type, [])
        existing_commands = {e.get("command", "") for e in existing_entries}
        for hook in hook_list:
            if hook.get("command", "") not in existing_commands:
                existing_entries.append(hook)

    settings_file.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Session management (tmux-based)
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")


def _session_name(project_name: str, session_num: int) -> str:
    """Generate a tmux session name: distillate-<slug>-<NNN>."""
    return f"distillate-{_slugify(project_name)}-{session_num:03d}"


def _next_session_id(project: dict) -> str:
    """Generate the next session ID for a project."""
    sessions = project.get("sessions", {})
    existing = [int(k.split("_")[-1]) for k in sessions if k.startswith("session_")]
    n = max(existing, default=0) + 1
    return f"session_{n:03d}"


def _build_claude_command(
    prompt_path: Path,
    *,
    model: str = "claude-sonnet-4-5-20250929",
    max_turns: int = 100,
    effort: str = "high",
) -> str:
    """Build the claude CLI invocation string."""
    parts = [
        "claude",
        "-p",
        f'"$(cat {prompt_path.name})"',
        "--allowedTools",
        "'Bash(python3:*)'",
        "'Bash(tail:*)'",
        "'Bash(ls:*)'",
        "'Bash(cat:*)'",
        "'Bash(head:*)'",
        "'Bash(wc:*)'",
        "'Bash(mkdir:*)'",
        "'Read'",
        "'Write'",
        "'Edit'",
        "'Glob'",
        "'Grep'",
        "--model",
        model,
        "--max-turns",
        str(max_turns),
        "--output-format",
        "stream-json",
    ]
    return " ".join(parts)


def launch_experiment(
    project_path: Path,
    *,
    host: str | None = None,
    model: str = "claude-sonnet-4-5-20250929",
    max_turns: int = 100,
    effort: str = "high",
    project: dict | None = None,
) -> dict:
    """Launch a Claude Code session for the experiment.

    1. Verify PROMPT.md exists
    2. Ensure hooks installed (always)
    3. Build claude command
    4. Spawn tmux session (local or via SSH)
    5. Return session dict for state tracking
    """
    project_path = project_path.resolve()
    prompt = project_path / "PROMPT.md"
    if not prompt.exists():
        raise FileNotFoundError(f"No PROMPT.md found in {project_path}")

    # Ensure hooks are always installed
    _install_hooks_into(project_path)

    # Build command
    cmd = _build_claude_command(
        prompt, model=model, max_turns=max_turns, effort=effort,
    )

    # Determine session name
    proj_name = project.get("name", project_path.name) if project else project_path.name
    session_id = _next_session_id(project) if project else "session_001"
    session_num = int(session_id.split("_")[-1])
    tmux_name = _session_name(proj_name, session_num)

    # Count current runs
    runs_at_start = len(project.get("runs", {})) if project else 0

    # Spawn tmux session
    if host:
        _spawn_ssh(tmux_name, host, str(project_path), cmd)
    else:
        _spawn_local(tmux_name, project_path, cmd)

    now = datetime.now(timezone.utc).isoformat()
    return {
        "session_id": session_id,
        "tmux_session": tmux_name,
        "started_at": now,
        "status": "running",
        "host": host,
        "model": model,
        "max_turns": max_turns,
        "runs_at_start": runs_at_start,
    }


def _spawn_local(session_name: str, work_dir: Path, command: str) -> int:
    """Spawn a local tmux session. Returns tmux server PID."""
    result = subprocess.run(
        [
            "tmux", "new-session", "-d",
            "-s", session_name,
            "-c", str(work_dir),
            command,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create tmux session '{session_name}': {result.stderr.strip()}"
        )

    # Get tmux server PID
    pid_result = subprocess.run(
        ["tmux", "display-message", "-p", "#{pid}"],
        capture_output=True,
        text=True,
    )
    try:
        return int(pid_result.stdout.strip())
    except ValueError:
        return 0


def _spawn_ssh(
    session_name: str, host: str, remote_dir: str, command: str,
) -> None:
    """Spawn a remote tmux session via SSH."""
    ssh_cmd = f"cd {remote_dir} && tmux new-session -d -s {session_name} {command!r}"
    result = subprocess.run(
        ["ssh", host, ssh_cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create remote tmux session on {host}: {result.stderr.strip()}"
        )


def session_status(session_name: str, host: str | None = None) -> str:
    """Check if tmux session is alive. Returns 'running' | 'completed' | 'unknown'."""
    cmd = ["tmux", "has-session", "-t", session_name]
    if host:
        cmd = ["ssh", host, " ".join(cmd)]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0:
        return "running"
    return "completed"


def attach_session(session_name: str, host: str | None = None) -> None:
    """Open a new terminal window attached to the tmux session.

    Spawns a separate Terminal.app window (macOS) so it works from both
    CLI and the desktop app's "Attach" button.
    """
    system = platform.system()

    if host:
        attach_cmd = f"ssh -t {host} tmux attach -t {session_name}"
    else:
        attach_cmd = f"tmux attach -t {session_name}"

    if system == "Darwin":
        # macOS: use osascript to open a new Terminal.app window
        script = f'tell application "Terminal" to do script "{attach_cmd}"'
        subprocess.run(["osascript", "-e", script], capture_output=True)
    elif system == "Linux":
        # Linux: try common terminal emulators
        for term in ("x-terminal-emulator", "gnome-terminal", "xterm"):
            if shutil.which(term):
                if term == "gnome-terminal":
                    subprocess.Popen([term, "--", "bash", "-c", attach_cmd])
                else:
                    subprocess.Popen([term, "-e", attach_cmd])
                return
        raise RuntimeError("No terminal emulator found. Install x-terminal-emulator.")
    else:
        raise RuntimeError(f"Unsupported platform: {system}. Attach manually: {attach_cmd}")


def stop_session(session_name: str, host: str | None = None) -> bool:
    """Send C-c to tmux session to stop gracefully. Returns success."""
    cmd = ["tmux", "send-keys", "-t", session_name, "C-c", ""]
    if host:
        cmd = ["ssh", host, " ".join(cmd)]

    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0


def list_sessions() -> list[dict]:
    """List all distillate-* tmux sessions with status.

    Returns [{"name": str, "activity": str}].
    """
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    sessions = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(" ", 1)
        name = parts[0]
        if not name.startswith("distillate-"):
            continue
        activity = parts[1] if len(parts) > 1 else ""
        sessions.append({"name": name, "activity": activity})
    return sessions


# ---------------------------------------------------------------------------
# Refresh session statuses in state
# ---------------------------------------------------------------------------

def refresh_session_statuses(state) -> int:
    """Check all running sessions and update their status in state.

    Returns count of sessions that changed from running to completed.
    """
    changed = 0
    for proj_id, proj in state.projects.items():
        sessions = proj.get("sessions", {})
        for sess_id, sess in sessions.items():
            if sess.get("status") != "running":
                continue
            tmux_name = sess.get("tmux_session", "")
            host = sess.get("host")
            actual = session_status(tmux_name, host)
            if actual != "running":
                sess["status"] = "completed"
                sess["completed_at"] = datetime.now(timezone.utc).isoformat()
                changed += 1
    return changed
