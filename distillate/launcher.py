"""Experiment launcher — scaffold, launch, monitor, and record experiments.

Manages the full experiment lifecycle: templates, scaffolding from templates,
tmux-based session management (local + SSH), and state tracking.
"""

import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from distillate.config import CONFIG_DIR

log = logging.getLogger(__name__)


def _ensure_path():
    """Augment PATH so tmux/claude are found from minimal-PATH environments (Electron)."""
    extra = ["/usr/local/bin", "/opt/homebrew/bin",
             str(Path.home() / ".local" / "bin")]
    path = os.environ.get("PATH", "")
    for p in extra:
        if p not in path:
            path = p + ":" + path
    os.environ["PATH"] = path


def ensure_tmux():
    """Check that tmux is installed; install via brew if missing (macOS)."""
    _ensure_path()
    if shutil.which("tmux"):
        return True
    system = platform.system()
    if system == "Darwin" and shutil.which("brew"):
        log.info("tmux not found, installing via Homebrew...")
        result = subprocess.run(
            ["brew", "install", "tmux"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            log.info("tmux installed successfully")
            return True
        log.error("Failed to install tmux: %s", result.stderr.strip())
    elif system == "Linux":
        for mgr, args in [("apt-get", ["-y"]), ("yum", ["-y"]), ("pacman", ["-S", "--noconfirm"])]:
            if shutil.which(mgr):
                log.info("tmux not found, installing via %s...", mgr)
                result = subprocess.run(
                    ["sudo", mgr, "install", *args, "tmux"],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    return True
                break
    raise RuntimeError(
        "tmux is required but not installed. "
        "Install it with: brew install tmux (macOS) or apt install tmux (Linux)"
    )


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
                    "Bash(git:*)",
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
        # Collect all commands already registered for dedup
        existing_commands = set()
        for entry in existing_entries:
            for h in entry.get("hooks", []):
                existing_commands.add(h.get("command", ""))
        for hook in hook_list:
            new_cmds = {h.get("command", "") for h in hook.get("hooks", [])}
            if not new_cmds & existing_commands:
                existing_entries.append(hook)

    settings_file.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Session management (tmux-based)
# ---------------------------------------------------------------------------


def _tmux_session_exists(session_name: str) -> bool:
    """Check if a tmux session with the given name is currently running."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")


def create_github_repo(project_path: Path, name: str, private: bool = True) -> dict:
    """Create a GitHub repo and push initial commit.

    Uses `gh` CLI (must be installed and authenticated).
    Returns {"ok": True, "url": "..."} or {"ok": False, "reason": "..."}.
    """
    _ensure_path()

    if not shutil.which("gh"):
        return {"ok": False, "reason": "gh CLI not installed. Install: brew install gh"}

    # Check gh auth
    auth_check = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True, text=True,
    )
    if auth_check.returncode != 0:
        return {"ok": False, "reason": "gh not authenticated. Run: gh auth login"}

    # Initial commit if needed
    subprocess.run(["git", "add", "-A"], cwd=project_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial experiment setup"],
        cwd=project_path, capture_output=True,
    )

    # Create repo
    visibility = "--private" if private else "--public"
    result = subprocess.run(
        ["gh", "repo", "create", name, visibility, "--source", str(project_path), "--push"],
        cwd=project_path,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"ok": False, "reason": result.stderr.strip() or "Failed to create repo"}

    # Extract URL from output
    url = result.stdout.strip()
    if not url:
        # Try to get it from git remote
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_path, capture_output=True, text=True,
        )
        url = remote.stdout.strip()

    return {"ok": True, "url": url}


def _session_name(project_name: str, session_num: int) -> str:
    """Generate a tmux session name: distillate-<slug>-<NNN>."""
    return f"distillate-{_slugify(project_name)}-{session_num:03d}"


def _next_session_id(project: dict) -> str:
    """Generate the next session ID for a project."""
    sessions = project.get("sessions", {})
    existing = [int(k.split("_")[-1]) for k in sessions if k.startswith("session_")]
    n = max(existing, default=0) + 1
    return f"session_{n:03d}"


def _generate_run_context(project_path: Path) -> Path | None:
    """Read runs.jsonl and generate .distillate/context.md with prior-run history.

    Returns the path to context.md if written, None otherwise.
    Caps at last 20 runs to keep context manageable.
    """
    runs_file = project_path / ".distillate" / "runs.jsonl"
    if not runs_file.exists():
        return None

    runs = []
    try:
        for line in runs_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return None

    if not runs:
        return None

    # Cap at last 20 runs
    recent = runs[-20:]

    lines = [
        "# Prior Run History",
        "",
        f"This experiment has **{len(runs)} prior run(s)**. "
        f"Below are the most recent {len(recent)}.",
        "",
        "**IMPORTANT:** Review this history before starting. Build on what worked, "
        "avoid repeating failed approaches. Reference specific run IDs when explaining "
        "your reasoning.",
        "",
    ]

    # Summarize each run
    best_metric_val = None
    best_metric_name = None
    best_run_id = None

    for run in recent:
        run_id = run.get("id", "?")
        status = run.get("status", "?")
        hypothesis = run.get("hypothesis", "")
        reasoning = run.get("reasoning", "")
        results = run.get("results", {})
        changes = run.get("changes", "")

        results_str = ", ".join(f"{k}={v}" for k, v in results.items()
                                if isinstance(v, (int, float)))

        lines.append(f"### {run_id} [{status}]")
        if hypothesis:
            lines.append(f"**Hypothesis:** {hypothesis}")
        if changes:
            lines.append(f"**Changes:** {changes}")
        if results_str:
            lines.append(f"**Results:** {results_str}")
        if reasoning:
            lines.append(f"**Reasoning:** {reasoning}")
        lines.append("")

        # Track best metric (from kept runs)
        if status == "keep":
            for k, v in results.items():
                if isinstance(v, (int, float)):
                    if best_metric_val is None or v > best_metric_val:
                        best_metric_val = v
                        best_metric_name = k
                        best_run_id = run_id

    # --- Key learnings from kept runs ---
    learnings: list[str] = []
    for run in recent:
        if run.get("status") != "keep":
            continue
        reasoning = run.get("reasoning", "")
        if reasoning:
            learnings.append(reasoning)
        for lr in run.get("learnings", []):
            if isinstance(lr, str) and lr:
                learnings.append(lr)

    if learnings:
        lines.append("## Key Learnings So Far")
        lines.append("")
        # Deduplicate while preserving order, cap at 10
        seen_lr: set[str] = set()
        for lr in learnings:
            if lr not in seen_lr:
                seen_lr.add(lr)
                lines.append(f"- {lr}")
            if len(seen_lr) >= 10:
                break
        lines.append("")

    # --- Infer key metric and optimization direction ---
    key_metric, key_direction = _infer_key_metric(runs)
    if key_metric:
        # Find best value among kept runs
        best_val = None
        best_rid = None
        for run in recent:
            if run.get("status") != "keep":
                continue
            val = run.get("results", {}).get(key_metric)
            if not isinstance(val, (int, float)):
                continue
            if best_val is None:
                best_val, best_rid = val, run.get("id", "?")
            elif key_direction == "lower" and val < best_val:
                best_val, best_rid = val, run.get("id", "?")
            elif key_direction == "higher" and val > best_val:
                best_val, best_rid = val, run.get("id", "?")

        direction_word = "minimize" if key_direction == "lower" else "maximize"
        best_str = ""
        if best_val is not None:
            best_str = f" Current best: **{key_metric}={best_val}** (from {best_rid})."
        lines.insert(7, f"**Key metric to {direction_word}:** `{key_metric}`.{best_str}")
        lines.insert(8, f"Your goal is to {direction_word} `{key_metric}` across runs. "
                        f"Report this metric for every run.")
        lines.insert(9, "")
    elif best_metric_val is not None:
        lines.insert(7, f"**Current best:** {best_metric_name}={best_metric_val} "
                        f"(from {best_run_id})")
        lines.insert(8, "")

    context_path = project_path / ".distillate" / "context.md"
    context_path.parent.mkdir(exist_ok=True)
    context_path.write_text("\n".join(lines), encoding="utf-8")
    return context_path


def _infer_key_metric(runs: list[dict]) -> tuple[str, str]:
    """Infer the key metric to optimize from run history.

    Returns (metric_name, direction) where direction is "higher" or "lower".
    Returns ("", "") if no metric can be inferred.

    Uses ``classify_metric()`` from experiments.py for category-aware scoring:
    ratio > loss > generic, and prefers metrics present in most runs.
    """
    if not runs:
        return ("", "")

    from collections import Counter

    from distillate.experiments import classify_metric

    metric_counts: Counter = Counter()
    for run in runs:
        for k, v in run.get("results", {}).items():
            if isinstance(v, (int, float)):
                metric_counts[k] += 1
    if not metric_counts:
        return ("", "")

    total = len(runs)

    # Category-based relevance scores
    _CATEGORY_RELEVANCE = {
        "ratio": 50, "loss": 30, "count": 10,
        "time": 5, "cost": 5, "hyperparameter": 1, "generic": 15,
    }
    # Prefix boosts (test > val > train)
    _PREFIX_BOOST = {"test_": 40, "val_": 20, "train_": 5}

    def _score(name: str) -> float:
        coverage = metric_counts[name] / total
        cat = classify_metric(name)
        rel = _CATEGORY_RELEVANCE.get(cat, 15)
        lower_name = name.lower()
        for prefix, boost in _PREFIX_BOOST.items():
            if lower_name.startswith(prefix):
                rel += boost
                break
        return coverage * rel

    best = max(metric_counts.keys(), key=_score)
    cat = classify_metric(best)
    direction = "lower" if cat in ("loss", "count", "time", "cost") else "higher"
    return (best, direction)


def _build_claude_command(
    prompt_path: Path,
    *,
    model: str = "claude-sonnet-4-5-20250929",
    effort: str = "high",
    has_context: bool = False,
) -> str:
    """Build the claude CLI invocation string.

    Runs claude interactively (no -p) so the full TUI is visible in
    the tmux session / xterm.js. The prompt just tells claude to read
    PROMPT.md — all experiment logic lives in that file.
    """
    prompt = (
        "Read PROMPT.md and follow it precisely. "
        "You are fully autonomous. Do NOT pause to ask the human anything. "
        "The human may be asleep. Work indefinitely until manually stopped.\n\n"
        "TIME DISCIPLINE: Every training script MUST include a wall-clock "
        "time check (see .distillate/REPORTING.md). Use time.time() to break "
        "the training loop when the budget from PROMPT.md is reached, then "
        "evaluate and log results normally. Never let a run exceed its budget. "
        "Do not spend more than 2 minutes debugging a single error — try a "
        "different approach instead.\n\n"
        "CRITICAL: For EVERY experiment run, follow this exact sequence:\n"
        "0. BEFORE implementing: append a 'running' entry to .distillate/runs.jsonl "
        "with a one-sentence description of what you're about to try and why\n"
        "1. After results: append a completed entry to .distillate/runs.jsonl "
        "(see .distillate/REPORTING.md) with 'reasoning' and 'description'\n"
        "2. git add -A && git commit -m '<shortest change desc>: <metric>=<value> [keep|discard]'\n"
        "3. git push\n"
        "4. /clear (frees context for the next run)\n\n"
        "Your commit history IS the experiment tracker. Each commit = one run."
    )
    parts = [
        "claude",
        "--permission-mode", "auto",
        shlex.quote(prompt),
    ]
    return " ".join(parts)


def launch_experiment(
    project_path: Path,
    *,
    host: str | None = None,
    model: str = "claude-sonnet-4-5-20250929",
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
    ensure_tmux()

    # Check if a session is already running for this project
    if project:
        for sess in project.get("sessions", {}).values():
            if sess.get("status") == "running":
                tmux_name = sess.get("tmux_session", "")
                if tmux_name and _tmux_session_exists(tmux_name):
                    raise RuntimeError(
                        f"Session '{tmux_name}' is already running. Stop it first."
                    )

    prompt = project_path / "PROMPT.md"
    if not prompt.exists():
        raise FileNotFoundError(f"No PROMPT.md found in {project_path}")

    # Ensure hooks are always installed
    _install_hooks_into(project_path)

    # Generate run context from prior runs (if any)
    context_path = _generate_run_context(project_path)

    # Build command
    cmd = _build_claude_command(
        prompt, model=model, effort=effort,
        has_context=context_path is not None,
    )

    # Determine session name
    proj_name = project.get("name", project_path.name) if project else project_path.name
    session_id = _next_session_id(project) if project else "session_001"
    session_num = int(session_id.split("_")[-1])
    tmux_name = _session_name(proj_name, session_num)

    # Count current runs
    runs_at_start = len(project.get("runs", {})) if project else 0

    # Session output log file (stream-json piped via tee)
    log_dir = project_path / ".distillate"
    log_dir.mkdir(exist_ok=True)
    session_log = log_dir / f"{session_id}.jsonl"

    # Spawn tmux session (interactive claude — no tee needed)
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
        "runs_at_start": runs_at_start,
        "session_log": str(session_log),
    }


def _spawn_local(session_name: str, work_dir: Path, command: str) -> int:
    """Spawn a local tmux session. Returns tmux server PID."""
    # Prepend PATH setup into the command so claude/python3 are found
    # regardless of how tmux was started (Electron has minimal PATH,
    # and tmux server may have been started with a different environment).
    # Also unset CLAUDECODE so Claude Code doesn't refuse to start
    # (it blocks nested sessions, but tmux sessions are independent).
    # Unset ANTHROPIC_API_KEY so Claude Code uses SSO auth (Max/Pro
    # subscription) instead of billing against the raw API key.
    extra_paths = "/usr/local/bin:/opt/homebrew/bin:" + str(Path.home() / ".local" / "bin")
    # Source bash login profile for SSO auth, then run the command
    bash_profile = Path.home() / ".bash_profile"
    source_line = f"source {shlex.quote(str(bash_profile))} >/dev/null 2>&1; " if bash_profile.exists() else ""
    full_command = f'{source_line}export PATH="{extra_paths}:$PATH"; unset CLAUDECODE; unset ANTHROPIC_API_KEY; {command}'

    print(f"[launch] tmux new-session -d -s {session_name} -c {work_dir}")
    print(f"[launch] command: {full_command}")

    # Set tmux options before creating the session to avoid green bar flash
    # -g sets global defaults that apply to new sessions
    subprocess.run(["tmux", "set-option", "-g", "status", "off"], capture_output=True)

    result = subprocess.run(
        [
            "tmux", "new-session", "-d",
            "-s", session_name,
            "-c", str(work_dir),
            full_command,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create tmux session '{session_name}': {result.stderr.strip()}"
        )

    # Configure tmux session for embedded use (xterm.js)
    subprocess.run(["tmux", "set", "-t", session_name, "status", "off"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", session_name, "mouse", "on"], capture_output=True)

    # Auto-confirm workspace trust dialog (Enter after brief delay)
    import time
    time.sleep(3)
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        capture_output=True,
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
    ssh_cmd = f"cd {shlex.quote(remote_dir)} && tmux new-session -d -s {shlex.quote(session_name)} {shlex.quote(command)}"
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
        cmd = ["ssh", host, f"tmux has-session -t {shlex.quote(session_name)}"]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0:
        return "running"
    return "completed"


def capture_pane(session_name: str, lines: int = 200) -> str:
    """Capture the last N lines of output from a tmux session pane."""
    _ensure_path()
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", str(-lines)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def attach_session(session_name: str, host: str | None = None) -> None:
    """Open a new terminal window attached to the tmux session.

    Spawns a separate Terminal.app window (macOS) so it works from both
    CLI and the desktop app's "Attach" button.
    """
    system = platform.system()

    if host:
        attach_cmd = f"ssh -t {shlex.quote(host)} tmux attach -t {shlex.quote(session_name)}"
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
    """Stop a tmux session: send C-c, wait briefly, then kill the session."""
    import time

    if host:
        subprocess.run(
            ["ssh", host, f"tmux send-keys -t {shlex.quote(session_name)} C-c ''"],
            capture_output=True,
        )
        time.sleep(2)
        subprocess.run(
            ["ssh", host, f"tmux kill-session -t {shlex.quote(session_name)}"],
            capture_output=True,
        )
        return True

    # Local: send C-c, wait, then kill the session
    subprocess.run(["tmux", "send-keys", "-t", session_name, "C-c", ""], capture_output=True)
    time.sleep(2)
    result = subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)
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

# ---------------------------------------------------------------------------
# Auto-continuation
# ---------------------------------------------------------------------------

def should_continue(project: dict) -> bool:
    """Check if project goals are unmet and another session should run.

    Compares the best result from kept runs against each goal's threshold.
    Returns True if any goal is not yet met.
    """
    goals = project.get("goals", [])
    if not goals:
        return False

    runs = project.get("runs", {})
    # Collect best metric values from kept runs
    best: dict[str, float] = {}
    for run in runs.values():
        if run.get("status") != "keep" and run.get("decision") != "keep":
            continue
        for k, v in run.get("results", {}).items():
            if not isinstance(v, (int, float)):
                continue
            if k not in best:
                best[k] = v
            else:
                best[k] = max(best[k], v)  # will compare per-goal direction below

    for goal in goals:
        metric = goal.get("metric", "")
        direction = goal.get("direction", "maximize")
        threshold = goal.get("threshold")
        if threshold is None or not metric:
            continue

        val = best.get(metric)
        if val is None:
            return True  # metric never measured → not met

        if direction == "maximize" and val < threshold:
            return True
        if direction == "minimize" and val > threshold:
            return True

    return False


def build_continuation_prompt(project: dict, original_prompt: str) -> str:
    """Append prior-run context to the original PROMPT.md for a continuation session."""
    runs = project.get("runs", {})
    if not runs:
        return original_prompt

    lines = [
        "",
        "## Context from Previous Sessions",
        "",
        f"This experiment has **{len(runs)} prior run(s)**.",
        "",
    ]

    # Summarize recent kept/discarded runs
    sorted_runs = sorted(
        runs.values(),
        key=lambda r: r.get("started_at", "") or r.get("timestamp", ""),
    )
    for run in sorted_runs[-10:]:
        run_id = run.get("id", "?")
        status = run.get("status", run.get("decision", "?"))
        hypothesis = run.get("hypothesis", "")
        results = run.get("results", {})
        reasoning = run.get("reasoning", "")

        results_str = ", ".join(
            f"{k}={v}" for k, v in results.items()
            if isinstance(v, (int, float))
        )

        lines.append(f"### {run_id} [{status}]")
        if hypothesis:
            lines.append(f"**Hypothesis:** {hypothesis}")
        if results_str:
            lines.append(f"**Results:** {results_str}")
        if reasoning:
            lines.append(f"**Reasoning:** {reasoning}")
        lines.append("")

    # Append goals reminder
    goals = project.get("goals", [])
    if goals:
        lines.append("### Goals Still to Meet")
        for g in goals:
            lines.append(
                f"- {g.get('metric', '?')}: {g.get('direction', '?')} "
                f"threshold {g.get('threshold', '?')}"
            )
        lines.append("")

    lines.append(
        "**Build on what worked, avoid repeating failed approaches. "
        "Reference prior run IDs in your reasoning.**"
    )

    return original_prompt + "\n".join(lines)


def launch_continuation(
    project_path: Path,
    project: dict,
    *,
    model: str = "claude-sonnet-4-5-20250929",
    max_turns: int = 100,
) -> dict:
    """Launch a continuation session with enriched context.

    Writes goal reminders into context.md (appended after the standard
    run history that ``_generate_run_context`` produces), then delegates
    to ``launch_experiment`` which already concatenates context.md with
    PROMPT.md.
    """
    project_path = project_path.resolve()

    # _generate_run_context is called inside launch_experiment, but we
    # want to append goal reminders.  Generate it first, then append.
    context_path = _generate_run_context(project_path)

    goals = project.get("goals", [])
    if goals and context_path:
        extra = ["\n## Goals Still to Meet\n"]
        for g in goals:
            extra.append(
                f"- {g.get('metric', '?')}: {g.get('direction', '?')} "
                f"threshold {g.get('threshold', '?')}"
            )
        extra.append("")
        extra.append(
            "**Focus on meeting these goals. Build on what worked, "
            "avoid repeating failed approaches.**"
        )
        with open(context_path, "a", encoding="utf-8") as f:
            f.write("\n".join(extra) + "\n")

    return launch_experiment(
        project_path,
        model=model,
        max_turns=max_turns,
        project=project,
    )


def launch_sweep(
    project_path: Path,
    project: dict,
    configs: list[dict],
    *,
    model: str = "claude-sonnet-4-5-20250929",
    max_turns: int = 100,
) -> list[dict]:
    """Launch parallel ablation sessions, one per config variant.

    Each config dict is injected into the PROMPT.md as a "## Sweep
    Configuration" section.  Each session runs in its own tmux window.

    Returns a list of session dicts (same shape as ``launch_experiment``).
    """
    project_path = project_path.resolve()
    prompt_file = project_path / "PROMPT.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"No PROMPT.md found in {project_path}")

    original_prompt = prompt_file.read_text(encoding="utf-8")
    results = []

    for i, cfg in enumerate(configs):
        # Build variant PROMPT.md with config injected
        variant_lines = [
            original_prompt,
            "",
            "## Sweep Configuration",
            "",
            f"This is variant **{i + 1}** of a {len(configs)}-config sweep.",
            "Use exactly these hyperparameters:",
            "",
        ]
        for k, v in cfg.items():
            variant_lines.append(f"- **{k}**: `{v}`")
        variant_lines.append("")
        variant_lines.append(
            "Record results with a run ID prefixed with "
            f"`sweep_{i + 1:02d}_` in `.distillate/runs.jsonl`."
        )

        variant_prompt = "\n".join(variant_lines)

        # Write variant PROMPT.md to a temp location
        variant_path = project_path / ".distillate" / f"PROMPT_sweep_{i + 1:02d}.md"
        variant_path.parent.mkdir(exist_ok=True)
        variant_path.write_text(variant_prompt, encoding="utf-8")

        # Temporarily swap PROMPT.md for this launch
        backup = prompt_file.read_text(encoding="utf-8")
        try:
            prompt_file.write_text(variant_prompt, encoding="utf-8")
            session_data = launch_experiment(
                project_path,
                model=model,
                max_turns=max_turns,
                project=project,
            )
            results.append(session_data)
        finally:
            prompt_file.write_text(backup, encoding="utf-8")

    return results


def write_steering(project_path: Path, text: str) -> Path:
    """Write steering instructions to .distillate/steering.md.

    Already read by ``_build_claude_command()`` via
    ``$(cat ... .distillate/steering.md 2>/dev/null)``.
    Returns the path written.
    """
    project_path = Path(project_path).resolve()
    steering_path = project_path / ".distillate" / "steering.md"
    steering_path.parent.mkdir(exist_ok=True)
    steering_path.write_text(
        f"# Steering Instructions\n\n{text}\n",
        encoding="utf-8",
    )
    return steering_path


def _rescan_after_session(project_id: str, state) -> dict | None:
    """Rescan a project after a session completes, adding new runs to state.

    Shared by ``run_campaign()`` (CLI foreground) and server SSE loop.
    Returns ``{"new_runs": int, "total_runs": int, "best_metric": dict|None}``
    or None on failure.
    """
    from distillate.experiments import scan_project
    from distillate.state import acquire_lock, release_lock

    proj = state.get_project(project_id)
    if not proj:
        return None

    proj_path = Path(proj.get("path", ""))
    if not proj_path.is_dir():
        return None

    result = scan_project(proj_path)
    if "error" in result:
        return None

    acquire_lock()
    try:
        state.reload()
        existing = state.get_project(project_id)
        if not existing:
            return None
        old_runs = existing.get("runs", {})
        old_count = len(old_runs)
        existing_names = {r["name"] for r in old_runs.values()}
        new_runs = 0
        for run_id, run_data in result.get("runs", {}).items():
            if run_data["name"] not in existing_names:
                state.add_run(project_id, run_id, run_data)
                new_runs += 1
        state.update_project(
            project_id,
            last_scanned_at=datetime.now(timezone.utc).isoformat(),
            last_commit_hash=result.get("head_hash", ""),
        )
        state.save()
    finally:
        release_lock()

    # Find best metric across all kept runs
    best_metric = None
    updated_proj = state.get_project(project_id)
    if updated_proj:
        for run in updated_proj.get("runs", {}).values():
            if run.get("decision") != "keep" and run.get("status") != "keep":
                continue
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    if best_metric is None or v > best_metric.get(list(best_metric.keys())[0], 0):
                        best_metric = {k: v}

    return {
        "new_runs": new_runs,
        "total_runs": old_count + new_runs,
        "best_metric": best_metric,
    }


def run_campaign(
    project_id: str,
    state,
    *,
    max_sessions: int = 10,
    model: str = "claude-sonnet-4-5-20250929",
    max_turns: int = 100,
    poll_interval: int = 10,
    on_event: Optional[callable] = None,
    stop_flag: Optional["threading.Event"] = None,
) -> dict:
    """Synchronous campaign loop: launch → poll → rescan → check goals → repeat.

    Called by both the CLI (foreground) and server (via run_in_executor).
    Returns ``{"sessions_launched": N, "stop_reason": "goal_reached|budget_exhausted|user_stopped"}``.
    """
    import threading
    import time

    from distillate.state import acquire_lock, release_lock

    if stop_flag is None:
        stop_flag = threading.Event()

    sessions_launched = 0

    def _emit(event: dict):
        if on_event:
            on_event(event)
        # Also append to events.jsonl
        proj = state.get_project(project_id)
        if proj:
            proj_path = Path(proj.get("path", ""))
            events_file = proj_path / ".distillate" / "events.jsonl"
            events_file.parent.mkdir(exist_ok=True)
            try:
                with open(events_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
            except OSError:
                pass

    while not stop_flag.is_set():
        state.reload()
        proj = state.get_project(project_id)
        if not proj:
            break

        campaign = proj.get("campaign", {})
        if campaign.get("status") not in ("running", None):
            # Externally paused/stopped
            return {"sessions_launched": sessions_launched, "stop_reason": "user_stopped"}

        # Budget check
        total = campaign.get("sessions_launched", 0)
        if total >= max_sessions:
            _emit({
                "type": "campaign_completed",
                "ts": datetime.now(timezone.utc).isoformat(),
                "project_id": project_id,
                "sessions_launched": total,
                "stop_reason": "budget_exhausted",
            })
            return {"sessions_launched": sessions_launched, "stop_reason": "budget_exhausted"}

        # Goal check
        if not should_continue(proj):
            _emit({
                "type": "goal_reached",
                "ts": datetime.now(timezone.utc).isoformat(),
                "project_id": project_id,
                "sessions_launched": total,
            })
            return {"sessions_launched": sessions_launched, "stop_reason": "goal_reached"}

        # Launch session
        proj_path = Path(proj.get("path", ""))
        if not proj_path.is_dir():
            break

        try:
            session_data = launch_continuation(
                proj_path, proj, model=model, max_turns=max_turns,
            )
        except Exception:
            log.exception("Campaign launch failed for %s", project_id)
            time.sleep(30)
            continue

        sessions_launched += 1

        # Save session + update campaign counters
        acquire_lock()
        try:
            state.reload()
            state.add_session(project_id, session_data["session_id"], session_data)
            p = state.get_project(project_id)
            c = dict(p.get("campaign", {}))
            c["sessions_launched"] = c.get("sessions_launched", 0) + 1
            c["current_session_id"] = session_data["session_id"]
            state.update_project(project_id, campaign=c)
            state.save()
        finally:
            release_lock()

        _emit({
            "type": "campaign_run_started",
            "ts": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "session_id": session_data["session_id"],
            "sessions_launched": c["sessions_launched"],
            "budget_remaining": max_sessions - c["sessions_launched"],
        })

        # Poll for session completion
        tmux_name = session_data.get("tmux_session", "")
        while not stop_flag.is_set():
            time.sleep(poll_interval)
            state.reload()
            p = state.get_project(project_id)
            c = p.get("campaign", {}) if p else {}
            if c.get("status") not in ("running", None):
                return {"sessions_launched": sessions_launched, "stop_reason": "user_stopped"}
            try:
                actual = session_status(tmux_name, None)
            except Exception:
                actual = "unknown"
            if actual != "running":
                # Rescan
                try:
                    _rescan_after_session(project_id, state)
                except Exception:
                    log.exception("Campaign rescan failed for %s", project_id)
                break

        # Small delay before next iteration
        time.sleep(5)

    # If we exited due to stop_flag
    if stop_flag.is_set():
        return {"sessions_launched": sessions_launched, "stop_reason": "user_stopped"}

    return {"sessions_launched": sessions_launched, "stop_reason": "unknown"}


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
