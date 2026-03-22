"""Experiment-related CLI command handlers.

Extracted from commands.py: experiment launcher, campaign, goals, watch,
chart export, notebooks, templates, etc.
"""

import json
import logging
import os
import re
import sys
from pathlib import Path

from distillate.cli import _bold, _dim, _opt

log = logging.getLogger("distillate")

__all__ = [
    "_resolve_project_or_bail",
    "_require_path",
    "_scan_projects",
    "_install_hooks",
    "_new_experiment",
    "_launch_experiment",
    "_list_experiments",
    "_attach_experiment",
    "_stop_experiment",
    "_campaign",
    "_goals",
    "_show_experiment",
    "_show_runs",
    "_open_notebook",
    "_continue_experiment",
    "_sweep_experiment",
    "_chart_export",
    "_delete_experiment",
    "_edit_prompt",
    "_steer",
    "_sparkline",
    "_tail_jsonl",
    "_format_watch_event",
    "_watch",
    "_update_project",
    "_queue_sessions",
    "_list_templates",
    "_save_template",
    "_compare_projects",
    "_github",
    "_create_experiment",
    "_parallel_campaign",
]


def _resolve_project_or_bail(query, state):
    """Look up a project by name/ID/index, printing an error if not found."""
    proj = state.find_project(query)
    if not proj:
        print(f"  No project found matching '{query}'.")
    return proj


def _require_path(proj, query=""):
    """Return project path string, or print an error and return empty string."""
    path = proj.get("path", "")
    if not path:
        name = proj.get("name", query or proj.get("id", "?"))
        print(f"  Project '{name}' has no path set.")
    return path


def _scan_projects() -> None:
    """Scan all tracked ML projects for new experiments."""
    from distillate import config
    from distillate.state import State

    config.setup_logging()
    state = State()

    if not config.EXPERIMENTS_ENABLED:
        print("Experiments not enabled. Set EXPERIMENTS_ENABLED=true in your .env")
        return

    projects = state.projects
    if not projects:
        print("No projects tracked yet. Use the agent to scan a project:")
        print('  distillate "scan project at ~/Code/Research/my-project"')
        return

    from distillate.experiments import (
        generate_notebook,
        load_enrichment_cache,
        update_project,
    )
    from distillate.obsidian import write_experiment_notebook

    updated = 0
    for proj_id, proj in projects.items():
        print(f"  Scanning {proj.get('name', proj_id)}...")
        if update_project(proj, state):
            proj_path = proj.get("path", "")
            enrichment = load_enrichment_cache(Path(proj_path)) if proj_path else {}
            notebook_md = generate_notebook(proj, enrichment=enrichment)
            write_experiment_notebook(proj, notebook_md)
            updated += 1

    if updated:
        state.save()
        print(f"  Updated {updated} project(s).")
    else:
        print("  No changes detected.")


def _install_hooks(args: list[str]) -> None:
    """Install Claude Code hooks for experiment capture into a project."""
    import json as json_mod
    import shutil

    if not args:
        print("Usage: distillate --install-hooks <path>")
        return

    project_path = Path(args[0]).resolve()
    if not project_path.is_dir():
        print(f"Not a directory: {project_path}")
        return

    # 1. Create .distillate/ directory
    distillate_dir = project_path / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    print(f"  Created {distillate_dir}/")

    # 2. Copy REPORTING.md
    reporting_src = Path(__file__).parent / "autoresearch" / "REPORTING.md"
    if reporting_src.exists():
        reporting_dst = distillate_dir / "REPORTING.md"
        shutil.copy2(reporting_src, reporting_dst)
        print(f"  Copied REPORTING.md to {reporting_dst}")

    # 2b. Install CLAUDE.md (consolidated protocol)
    claude_md_src = Path(__file__).parent / "autoresearch" / "CLAUDE.md"
    if claude_md_src.exists():
        claude_md_dst = project_path / "CLAUDE.md"
        shutil.copy2(claude_md_src, claude_md_dst)
        print(f"  Installed CLAUDE.md (experiment protocol)")

    # 3. Merge hook config into .claude/settings.json
    hooks_src = Path(__file__).parent / "autoresearch" / "hooks.json"
    if not hooks_src.exists():
        print("  Warning: hooks.json template not found")
        return

    hook_config = json_mod.loads(hooks_src.read_text(encoding="utf-8"))

    claude_dir = project_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_file = claude_dir / "settings.json"

    existing: dict = {}
    if settings_file.exists():
        try:
            existing = json_mod.loads(settings_file.read_text(encoding="utf-8"))
        except json_mod.JSONDecodeError:
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
        json_mod.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  Updated {settings_file}")
    print("  Done! Hooks will capture experiments in this directory.")


# ---------------------------------------------------------------------------
# Experiment launcher commands
# ---------------------------------------------------------------------------

def _new_experiment(args: list[str]) -> None:
    """Scaffold a new experiment from a template (interactive wizard)."""
    from distillate import config
    from distillate.experiments import slugify
    from distillate.launcher import (
        import_template,
        list_templates,
        scaffold_experiment,
    )
    from distillate.state import State

    templates = list_templates()

    # If template name given as argument, use it
    template_name = None
    if args and not args[0].startswith("-"):
        template_name = args[0]
        # Check if it exists
        if not any(t["name"] == template_name for t in templates):
            # Maybe it's a path to import as a template
            candidate = Path(args[0]).expanduser().resolve()
            if candidate.is_dir() and (candidate / "PROMPT.md").exists():
                print(f"  Importing {candidate.name} as a template...")
                template_name = import_template(candidate)
                templates = list_templates()
            else:
                print(f"  Template '{template_name}' not found.")
                if templates:
                    print("  Available templates:")
                    for t in templates:
                        data = " (has data/)" if t["has_data"] else ""
                        print(f"    {t['name']}{data} — {t['prompt_lines']} lines")
                else:
                    print("  No templates available. Import one:")
                    print("    distillate --new-experiment /path/to/experiment")
                return

    if not template_name:
        if not templates:
            print("  No templates available yet.")
            print("  Import an experiment directory as a template:")
            print("    distillate --new-experiment /path/to/experiment")
            return

        print("\n  Available templates:")
        for i, t in enumerate(templates, 1):
            data = " (has data/)" if t["has_data"] else ""
            print(f"    {i}. {t['name']}{data} — {t['prompt_lines']} lines")

        try:
            choice = input("\n  Select template (number): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(templates):
                template_name = templates[idx]["name"]
            else:
                print("  Invalid choice.")
                return
        except (ValueError, EOFError, KeyboardInterrupt):
            print()
            return

    # Name
    name = _opt("--name")
    if not name:
        try:
            default = template_name
            name = input(f"  Experiment name [{default}]: ").strip() or default
        except (EOFError, KeyboardInterrupt):
            print()
            return

    # Target directory
    target = _opt("--target")
    if not target:
        if config.EXPERIMENTS_ROOT:
            default_target = str(Path(config.EXPERIMENTS_ROOT) / slugify(name))
        else:
            default_target = str(Path.home() / "experiments" / slugify(name))
        try:
            target = input(f"  Target directory [{default_target}]: ").strip() or default_target
        except (EOFError, KeyboardInterrupt):
            print()
            return

    target_path = Path(target).expanduser().resolve()

    try:
        result = scaffold_experiment(template_name, target_path, name=name)
        print(f"\n  Scaffolded experiment at {result}")
        print(f"  - PROMPT.md copied from template")
        print(f"  - .distillate/ created with REPORTING.md")
        print(f"  - Claude Code hooks installed")
        print(f"  - git initialized")

        # Register in state
        state = State()
        project_id = slugify(name)
        if not state.has_project(project_id):
            state.add_project(
                project_id=project_id,
                name=name.replace("-", " ").title() if name == slugify(name) else name,
                path=str(result),
            )
            state.update_project(project_id, template=template_name)
            state.save()
            print(f"  - Registered as project '{project_id}'")

        print(f"\n  Launch it:")
        print(f"    distillate --launch {project_id}")

    except (FileNotFoundError, FileExistsError) as e:
        print(f"  Error: {e}")


def _launch_experiment(args: list[str]) -> None:
    """Launch an auto-research session for an experiment."""
    from distillate.launcher import launch_experiment
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --launch <name|path> [--host <ssh_host>] [--model <model>] [--turns <N>]")
        return

    query = args[0]
    host = _opt("--host")
    model = _opt("--model") or "claude-sonnet-4-5-20250929"
    turns = int(_opt("--turns") or "100")

    state = State()

    # Resolve: try project name/ID first, then path
    proj = state.find_project(query)
    if proj:
        project_path = Path(proj["path"])
    else:
        project_path = Path(query).expanduser().resolve()
        if not project_path.is_dir():
            print(f"  No project found matching '{query}' and path doesn't exist.")
            return

    try:
        session_data = launch_experiment(
            project_path,
            host=host,
            model=model,
            max_turns=turns,
            project=proj,
        )

        # Save session in state
        if proj:
            state.add_session(proj["id"], session_data["session_id"], session_data)
            state.save()

        tmux_name = session_data["tmux_session"]
        print(f"\n  Launched experiment session: {tmux_name}")
        print(f"  Model: {model} | Max turns: {turns}")
        if host:
            print(f"  Host: {host}")
        print(f"\n  Attach to session:")
        print(f"    distillate --attach {query}")
        print(f"\n  Stop session:")
        print(f"    distillate --stop {query}")

    except (FileNotFoundError, RuntimeError) as e:
        print(f"  Error: {e}")


def _list_experiments() -> None:
    """List all tracked experiments with status and key insights."""
    from distillate.experiments import load_enrichment_cache
    from distillate.launcher import refresh_session_statuses
    from distillate.state import State

    state = State()
    projects = state.projects

    if not projects:
        print("  No experiments tracked yet.")
        print("  Scaffold one: distillate --new-experiment")
        return

    # Refresh session statuses
    changed = refresh_session_statuses(state)
    if changed:
        state.save()

    # Print table header
    print()
    print(f"  {'#':>3}  {'Name':<22} {'Status':<12} {'Runs':>5}  {'Best Metric':<20} {'Sessions'}")
    print(f"  {'─' * 3}  {'─' * 22} {'─' * 12} {'─' * 5}  {'─' * 20} {'─' * 12}")

    insights_by_proj: list[tuple[str, dict]] = []

    for proj_id, proj in projects.items():
        idx = state.project_index_of(proj_id)
        name = proj.get("name", proj_id)[:22]
        status = proj.get("status", "tracking")
        runs = proj.get("runs", {})
        run_count = len(runs)

        # Find best metric
        best_metric = ""
        for run in runs.values():
            results = run.get("results", {})
            for k in ("accuracy", "exact_match", "test_accuracy", "val_accuracy",
                       "best_val_acc", "f1", "loss", "val_bpb", "rmse"):
                if k in results:
                    val = results[k]
                    if isinstance(val, float):
                        best_metric = f"{k}: {val:.4f}"
                    else:
                        best_metric = f"{k}: {val}"
                    break
            if best_metric:
                break

        # Count active sessions
        sessions = proj.get("sessions", {})
        active = sum(1 for s in sessions.values() if s.get("status") == "running")
        sess_str = f"{active} active" if active else "0 active"

        print(f"  {idx:>3}  {name:<22} {status:<12} {run_count:>5}  {best_metric:<20} {sess_str}")

        # Load enrichment for insights
        proj_path = proj.get("path", "")
        if proj_path:
            cache = load_enrichment_cache(Path(proj_path))
            enr = cache.get("enrichment", cache)
            project_insights = enr.get("project", {})
            if project_insights:
                insights_by_proj.append((proj.get("name", proj_id), project_insights))

    # Print research insights below the table
    if insights_by_proj:
        print()
        print(f"  {'─' * 60}")
        for proj_name, insights in insights_by_proj:
            breakthrough = insights.get("key_breakthrough", "")
            lessons = insights.get("lessons_learned", [])
            if breakthrough or lessons:
                print(f"\n  {_bold(proj_name)} — Research Insights")
                if breakthrough:
                    print(f"  {_dim('Breakthrough:')} {breakthrough}")
                if lessons:
                    print(f"  {_dim('Lessons:')}")
                    for i, lesson in enumerate(lessons, 1):
                        print(f"    {i}. {lesson}")

    print()


def _attach_experiment(args: list[str]) -> None:
    """Attach to a running experiment session."""
    from distillate.launcher import attach_session
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --attach <name>")
        return

    query = args[0]
    state = State()

    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    # Find running session
    sessions = proj.get("sessions", {})
    running = [(sid, s) for sid, s in sessions.items() if s.get("status") == "running"]

    if not running:
        print(f"  No running sessions for '{proj.get('name', query)}'.")
        return

    # Attach to the most recent running session
    sess_id, sess = running[-1]
    tmux_name = sess.get("tmux_session", "")
    host = sess.get("host")

    try:
        attach_session(tmux_name, host)
        print(f"  Opened terminal attached to {tmux_name}")
    except RuntimeError as e:
        print(f"  Error: {e}")


def _stop_experiment(args: list[str]) -> None:
    """Stop a running experiment session."""
    from datetime import datetime, timezone

    from distillate.launcher import stop_session
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --stop <name>")
        return

    query = args[0]
    state = State()

    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    # Find running sessions
    sessions = proj.get("sessions", {})
    running = [(sid, s) for sid, s in sessions.items() if s.get("status") == "running"]

    if not running:
        print(f"  No running sessions for '{proj.get('name', query)}'.")
        return

    for sess_id, sess in running:
        tmux_name = sess.get("tmux_session", "")
        host = sess.get("host")
        ok = stop_session(tmux_name, host)
        if ok:
            state.update_session(
                proj["id"], sess_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            print(f"  Stopped session {tmux_name}")
        else:
            print(f"  Failed to stop session {tmux_name}")

    state.save()


def _campaign(args: list[str]) -> None:
    """Manage autonomous campaign loops: start, status, stop."""
    import signal
    import threading
    from datetime import datetime, timezone

    from distillate.cli import _bold, _dim
    from distillate.launcher import run_campaign, should_continue
    from distillate.state import State

    if not args:
        print("Usage: distillate --campaign start|status|stop <project>")
        return

    action = args[0]
    if action not in ("start", "status", "stop"):
        print(f"Unknown campaign action: {action}")
        print("Usage: distillate --campaign start|status|stop <project>")
        return

    if len(args) < 2:
        print(f"Usage: distillate --campaign {action} <project>")
        return

    query = args[1]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_name = proj.get("name", query)

    # --- status ---
    if action == "status":
        campaign = proj.get("campaign", {})
        if not campaign or not campaign.get("status"):
            print(f"  No campaign running for '{proj_name}'.")
            return
        print()
        print(f"  Campaign: {_bold(proj_name)}")
        print(f"  Status:   {campaign.get('status', '?')}")
        print(f"  Sessions: {campaign.get('sessions_launched', 0)}"
              f" / {campaign.get('budget', {}).get('max_sessions', '?')}")
        if campaign.get("objective"):
            print(f"  Objective: {campaign['objective']}")
        stop_reason = campaign.get("stop_reason")
        if stop_reason:
            print(f"  Stopped:  {stop_reason}")
        # Show best metric from kept runs
        runs = proj.get("runs", {})
        best_val = None
        best_name = None
        for run in runs.values():
            if run.get("status") != "keep" and run.get("decision") != "keep":
                continue
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    if best_val is None or v > best_val:
                        best_val = v
                        best_name = k
        if best_val is not None:
            print(f"  Best:     {best_name}={best_val}")
        print()
        return

    # --- stop ---
    if action == "stop":
        campaign = proj.get("campaign", {})
        if not campaign or campaign.get("status") not in ("running", "paused"):
            print(f"  No active campaign for '{proj_name}'.")
            return
        campaign["status"] = "paused"
        campaign["stop_reason"] = "user_stopped"
        campaign["completed_at"] = datetime.now(timezone.utc).isoformat()
        state.update_project(proj["id"], campaign=campaign)
        state.save()
        print(f"  Campaign paused for '{proj_name}'.")
        return

    # --- start ---
    if not proj.get("goals"):
        print(f"  Cannot start campaign: '{proj_name}' has no goals set.")
        print("  Set goals first with the agent REPL (update_goals tool).")
        return

    if not should_continue(proj):
        print(f"  All goals for '{proj_name}' appear to be met already.")
        return

    existing = proj.get("campaign", {})
    if existing.get("status") == "running":
        print(f"  Campaign already running for '{proj_name}'.")
        return

    max_sessions = 10
    model = "claude-sonnet-4-5-20250929"
    max_turns = 100

    # Parse optional flags
    for i, a in enumerate(args[2:], start=2):
        if a == "--model" and i + 1 < len(args):
            model = args[i + 1]
        elif a == "--turns" and i + 1 < len(args):
            max_turns = int(args[i + 1])
        elif a.isdigit():
            max_sessions = int(a)

    campaign = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "objective": "",
        "budget": {"max_sessions": max_sessions, "max_hours": 8},
        "model": model,
        "max_turns": max_turns,
        "sessions_launched": 0,
        "current_session_id": None,
        "completed_at": None,
        "stop_reason": None,
    }
    state.update_project(proj["id"], campaign=campaign, auto_continue=True)
    state.save()

    stop_flag = threading.Event()

    def _on_sigint(sig, frame):
        print("\n  Pausing campaign (finishing current session)...")
        stop_flag.set()

    old_handler = signal.signal(signal.SIGINT, _on_sigint)

    def _on_event(event):
        etype = event.get("type", "")
        ts = event.get("ts", "")[:19]
        if etype == "campaign_run_started":
            n = event.get("sessions_launched", 0)
            remaining = event.get("budget_remaining", "?")
            print(f"  [{ts}] Session #{n} started ({remaining} remaining)")
        elif etype == "goal_reached":
            print(f"  [{ts}] \033[1;32mGoal reached!\033[0m")
        elif etype == "campaign_completed":
            reason = event.get("stop_reason", "?")
            print(f"  [{ts}] Campaign completed: {reason}")

    print()
    print(f"  Starting campaign for {_bold(proj_name)}")
    print(f"  Budget: {max_sessions} sessions, model: {model}")
    print(f"  Press Ctrl+C to pause\n")

    try:
        result = run_campaign(
            proj["id"],
            state,
            max_sessions=max_sessions,
            model=model,
            max_turns=max_turns,
            on_event=_on_event,
            stop_flag=stop_flag,
        )
    finally:
        signal.signal(signal.SIGINT, old_handler)

    reason = result.get("stop_reason", "unknown")
    launched = result.get("sessions_launched", 0)
    print(f"\n  Campaign ended: {reason} ({launched} session(s) launched)")

    # Update campaign status in state
    state.reload()
    p = state.get_project(proj["id"])
    if p:
        c = dict(p.get("campaign", {}))
        c["status"] = "paused"
        c["stop_reason"] = reason
        c["completed_at"] = datetime.now(timezone.utc).isoformat()
        state.update_project(proj["id"], campaign=c)
        state.save()


def _goals(args: list[str]) -> None:
    """View or set metric goals for a project."""
    from distillate.experiment_tools import _parse_goals_from_text
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --goals <project> [\"metric>0.95\" ...]")
        return

    query = args[0]
    goal_strs = [a for a in args[1:] if not a.startswith("-")]

    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_name = proj.get("name", query)

    # --- Set goals ---
    if goal_strs:
        goals: list[dict] = []
        for g in goal_strs:
            # Simple "metric>threshold" / "metric<threshold" syntax
            parsed = _parse_goals_from_text(g)
            if parsed:
                goals.extend(parsed)
            else:
                print(f"  Could not parse goal: '{g}'")
                print("  Expected format: \"accuracy>0.95\" or \"loss<0.05\"")
                return

        state.update_project(proj["id"], goals=goals)
        state.save()
        print(f"\n  Set {len(goals)} goal(s) for {_bold(proj_name)}:\n")
        for g in goals:
            arrow = "↑" if g["direction"] == "maximize" else "↓"
            op = ">" if g["direction"] == "maximize" else "<"
            print(f"    {arrow} {g['metric']} {op} {g['threshold']}")
        print()
        return

    # --- View goals ---
    goals = proj.get("goals", [])
    if not goals:
        print(f"\n  No goals set for '{proj_name}'.")
        print("  Set with: distillate --goals " + query + ' "accuracy>0.95"')
        print()
        return

    # Gather best values from kept runs
    runs = proj.get("runs", {})
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
                # Track best based on goal direction
                goal_dir = next(
                    (g["direction"] for g in goals if g["metric"] == k), "maximize"
                )
                if goal_dir == "maximize":
                    best[k] = max(best[k], v)
                else:
                    best[k] = min(best[k], v)

    print(f"\n  Goals for {_bold(proj_name)}:\n")
    for g in goals:
        metric = g["metric"]
        direction = g["direction"]
        threshold = g["threshold"]
        arrow = "↑" if direction == "maximize" else "↓"
        op = ">" if direction == "maximize" else "<"

        val = best.get(metric)
        if val is not None:
            if direction == "maximize":
                met = val >= threshold
            else:
                met = val <= threshold
            status = "\033[1;32m✓\033[0m" if met else "\033[33m·\033[0m"
            dist = abs(val - threshold)
            dist_str = f"({'+' if met else '-'}{dist:.4f})"
            print(f"    {status} {arrow} {metric} {op} {threshold}  "
                  f"best: {val:.4f} {_dim(dist_str)}")
        else:
            print(f"    · {arrow} {metric} {op} {threshold}  {_dim('(no data)')}")
    print()


def _show_experiment(args: list[str]) -> None:
    """Show detailed experiment dashboard."""
    from datetime import datetime

    from distillate.experiments import load_enrichment_cache
    from distillate.launcher import refresh_session_statuses
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --show <project>")
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    # Refresh sessions
    changed = refresh_session_statuses(state)
    if changed:
        state.save()

    proj_name = proj.get("name", query)
    proj_path = proj.get("path", "")
    runs = proj.get("runs", {})
    sessions = proj.get("sessions", {})
    goals = proj.get("goals", [])
    campaign = proj.get("campaign", {})

    # --- Header ---
    print()
    print(f"  {_bold(proj_name)}")
    if proj.get("description"):
        print(f"  {proj['description']}")
    print(f"  {_dim('Path:')} {proj_path}")
    status = proj.get("status", "tracking")
    print(f"  {_dim('Status:')} {status}  {_dim('Runs:')} {len(runs)}")
    tags = proj.get("tags", [])
    if tags:
        print(f"  {_dim('Tags:')} {', '.join(tags)}")
    added = proj.get("added_at", "")[:10]
    if added:
        print(f"  {_dim('Added:')} {added}")

    # --- Goals ---
    if goals:
        # Gather best values from kept runs
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
                    goal_dir = next(
                        (g["direction"] for g in goals if g["metric"] == k),
                        "maximize",
                    )
                    if goal_dir == "maximize":
                        best[k] = max(best[k], v)
                    else:
                        best[k] = min(best[k], v)

        print(f"\n  {_bold('Goals')}")
        for g in goals:
            metric = g["metric"]
            direction = g["direction"]
            threshold = g["threshold"]
            arrow = "↑" if direction == "maximize" else "↓"
            op = ">" if direction == "maximize" else "<"
            val = best.get(metric)
            if val is not None:
                met = (val >= threshold) if direction == "maximize" else (val <= threshold)
                status_ch = "\033[1;32m✓\033[0m" if met else "\033[33m·\033[0m"
                print(f"    {status_ch} {arrow} {metric} {op} {threshold}  "
                      f"best: {val:.4f}")
            else:
                print(f"    · {arrow} {metric} {op} {threshold}  {_dim('(no data)')}")

    # --- Campaign ---
    if campaign and campaign.get("status"):
        print(f"\n  {_bold('Campaign')}")
        print(f"    Status:   {campaign.get('status', '?')}")
        launched = campaign.get("sessions_launched", 0)
        budget = campaign.get("budget", {}).get("max_sessions", "?")
        print(f"    Sessions: {launched} / {budget}")
        if campaign.get("objective"):
            print(f"    Objective: {campaign['objective']}")
        if campaign.get("stop_reason"):
            print(f"    Stopped:  {campaign['stop_reason']}")

    # --- Active sessions ---
    active_sessions = [
        (sid, s) for sid, s in sessions.items() if s.get("status") == "running"
    ]
    if active_sessions:
        print(f"\n  {_bold('Active Sessions')}")
        for sid, s in active_sessions:
            tmux = s.get("tmux_session", sid)
            model = s.get("model", "?")
            started = s.get("started_at", "")[:16].replace("T", " ")
            print(f"    {tmux}  {_dim(model)}  {_dim(started)}")

    # --- Runs (last 10) ---
    if runs:
        sorted_runs = sorted(
            runs.values(),
            key=lambda r: r.get("started_at", r.get("completed_at", "")),
        )

        # Key metric sparkline
        key_vals: list[float] = []
        key_metric_name = ""
        for r in sorted_runs:
            results = r.get("results", {})
            for k in ("accuracy", "exact_match", "test_accuracy", "val_accuracy",
                       "best_val_acc", "f1", "loss", "val_bpb", "rmse"):
                if k in results and isinstance(results[k], (int, float)):
                    if not key_metric_name:
                        key_metric_name = k
                    if k == key_metric_name:
                        key_vals.append(results[k])
                    break

        print(f"\n  {_bold('Runs')} ({len(runs)} total)")
        if key_vals:
            print(f"    {_dim(key_metric_name + ':')} {_sparkline(key_vals, 20)}")

        # Table: last 10
        recent = sorted_runs[-10:]
        print(f"\n    {'Name':<22} {'Decision':<10} {'Key Metric':<20} {'Duration'}")
        print(f"    {'─' * 22} {'─' * 10} {'─' * 20} {'─' * 10}")
        for r in recent:
            name = (r.get("name") or r.get("id", "?"))[:22]
            decision = r.get("decision", r.get("status", "?"))
            dur = r.get("duration_minutes", 0)
            dur_str = f"{dur}m" if dur else "?"

            # Key metric
            results = r.get("results", {})
            km = ""
            for k in ("accuracy", "exact_match", "test_accuracy", "val_accuracy",
                       "best_val_acc", "f1", "loss", "val_bpb", "rmse"):
                if k in results:
                    v = results[k]
                    km = f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                    break
            if not km and results:
                k2, v2 = next(iter(results.items()))
                if isinstance(v2, (int, float)):
                    km = f"{k2}={v2:.4f}" if isinstance(v2, float) else f"{k2}={v2}"

            print(f"    {name:<22} {decision:<10} {km:<20} {dur_str}")

    # --- Research Insights ---
    if proj_path:
        cache = load_enrichment_cache(Path(proj_path))
        enr = cache.get("enrichment", cache)
        project_insights = enr.get("project", {})
        breakthrough = project_insights.get("key_breakthrough", "")
        lessons = project_insights.get("lessons_learned", [])
        if breakthrough or lessons:
            print(f"\n  {_bold('Research Insights')}")
            if breakthrough:
                print(f"    {_dim('Breakthrough:')} {breakthrough}")
            if lessons:
                print(f"    {_dim('Lessons:')}")
                for i, lesson in enumerate(lessons, 1):
                    print(f"      {i}. {lesson}")

    # --- Steering ---
    if proj_path:
        steering_path = Path(proj_path) / ".distillate" / "steering.md"
        if steering_path.exists():
            content = steering_path.read_text(encoding="utf-8").strip()
            if content:
                print(f"\n  {_bold('Steering')}")
                for line in content.splitlines()[:5]:
                    print(f"    {line}")
                if len(content.splitlines()) > 5:
                    print(f"    {_dim('...')}")

    print()


def _show_runs(args: list[str]) -> None:
    """Show full run history for a project."""
    from distillate.experiments import load_enrichment_cache
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --runs <project>")
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_name = proj.get("name", query)
    proj_path = proj.get("path", "")
    runs = proj.get("runs", {})

    if not runs:
        print(f"\n  No runs recorded for '{proj_name}'.\n")
        return

    sorted_runs = sorted(
        runs.values(),
        key=lambda r: r.get("started_at", r.get("completed_at", "")),
    )

    # Load enrichment for hypothesis/changes
    enrichment: dict = {}
    if proj_path:
        cache = load_enrichment_cache(Path(proj_path))
        enrichment = cache.get("enrichment", cache)
    run_enrichments = enrichment.get("runs", {})

    # Key metric sparkline across all runs
    key_vals: list[float] = []
    key_metric_name = ""
    for r in sorted_runs:
        results = r.get("results", {})
        for k in ("accuracy", "exact_match", "test_accuracy", "val_accuracy",
                   "best_val_acc", "f1", "loss", "val_bpb", "rmse"):
            if k in results and isinstance(results[k], (int, float)):
                if not key_metric_name:
                    key_metric_name = k
                if k == key_metric_name:
                    key_vals.append(results[k])
                break

    print(f"\n  {_bold(proj_name)} — Run History ({len(runs)} runs)")
    if key_vals:
        print(f"  {_dim(key_metric_name + ':')} {_sparkline(key_vals, 30)}")
    print()

    for i, r in enumerate(sorted_runs, 1):
        name = r.get("name") or r.get("id", "?")
        decision = r.get("decision", r.get("status", "?"))
        started = r.get("started_at", "")[:10]
        dur = r.get("duration_minutes", 0)
        dur_str = f"{dur}m" if dur else ""

        # Decision coloring
        if decision == "keep":
            dec_str = "\033[1;32mkeep\033[0m"
        elif decision == "discard":
            dec_str = "\033[2mdiscard\033[0m"
        else:
            dec_str = decision

        print(f"  {_dim(f'#{i:>3}')}  {_bold(name)}  {dec_str}"
              f"  {_dim(started)}  {_dim(dur_str)}")

        # Metrics
        results = r.get("results", {})
        numeric = {k: v for k, v in results.items() if isinstance(v, (int, float))}
        if numeric:
            parts = []
            for k, v in numeric.items():
                parts.append(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}")
            print(f"        {_dim('Metrics:')} {', '.join(parts)}")

        # Hypothesis (from annotation or enrichment)
        hypothesis = r.get("hypothesis", "")
        if not hypothesis:
            run_enr = run_enrichments.get(r.get("id", ""), {})
            hypothesis = run_enr.get("hypothesis", "")
        if hypothesis:
            print(f"        {_dim('Hypothesis:')} {hypothesis[:120]}")

        # Changes (from enrichment)
        run_enr = run_enrichments.get(r.get("id", ""), {})
        changes = run_enr.get("changes", run_enr.get("approach", ""))
        if changes:
            change_str = changes if isinstance(changes, str) else "; ".join(changes)
            print(f"        {_dim('Changes:')} {change_str[:120]}")

        print()

    print()


def _open_notebook(args: list[str]) -> None:
    """Generate HTML+MD notebooks and open in browser."""
    import webbrowser

    from distillate.experiments import (
        generate_html_notebook,
        generate_notebook,
        load_enrichment_cache,
    )
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --notebook <project>")
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_name = proj.get("name", query)
    proj_path = _require_path(proj)
    if not proj_path:
        return

    project_path = Path(proj_path)

    # Load enrichment cache
    cache = load_enrichment_cache(project_path)
    enrichment = cache.get("enrichment", cache)

    # Generate HTML notebook
    html_content = generate_html_notebook(proj, enrichment=enrichment)
    html_path = project_path / ".distillate" / "notebook.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_content, encoding="utf-8")

    # Generate MD notebook
    md_content = generate_notebook(proj, enrichment=enrichment)
    md_path = project_path / ".distillate" / "notebook.md"
    md_path.write_text(md_content, encoding="utf-8")

    print(f"\n  Generated notebooks for {_bold(proj_name)}:")
    print(f"    HTML: {html_path}")
    print(f"    MD:   {md_path}")

    # Open in browser
    try:
        webbrowser.open(f"file://{html_path}")
        print(f"\n  Opened in browser.")
    except Exception:
        print(f"\n  Open manually: file://{html_path}")
    print()


def _continue_experiment(args: list[str]) -> None:
    """Launch a continuation session for an experiment."""
    from distillate.launcher import launch_continuation, should_continue
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --continue <project> [--model <model>] [--turns <N>]")
        return

    query = args[0]
    model = _opt("--model") or "claude-sonnet-4-5-20250929"
    turns = int(_opt("--turns") or "100")

    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_name = proj.get("name", query)
    proj_path = _require_path(proj)
    if not proj_path:
        return

    # Check if goals are met
    if not should_continue(proj):
        if not proj.get("goals"):
            print(f"  No goals set for '{proj_name}'.")
            print(f"  Set goals first: distillate --goals {query} \"accuracy>0.95\"")
        else:
            print(f"  All goals for '{proj_name}' appear to be met.")
        return

    try:
        session_data = launch_continuation(
            Path(proj_path), proj, model=model, max_turns=turns,
        )

        # Save session to state
        state.add_session(proj["id"], session_data["session_id"], session_data)
        state.save()

        tmux_name = session_data["tmux_session"]
        print(f"\n  Continuation session launched: {tmux_name}")
        print(f"  Model: {model} | Max turns: {turns}")
        print(f"\n  Attach:")
        print(f"    distillate --attach {query}")
        print(f"\n  Stop:")
        print(f"    distillate --stop {query}")

    except (FileNotFoundError, RuntimeError) as e:
        print(f"  Error: {e}")


def _sweep_experiment(args: list[str]) -> None:
    """Launch a parallel sweep from a config file."""
    from distillate.launcher import launch_sweep
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --sweep <project> --config <sweep.json>")
        return

    query = args[0]
    config_path = _opt("--config")
    if not config_path:
        print("Usage: distillate --sweep <project> --config <sweep.json>")
        return

    model = _opt("--model") or "claude-sonnet-4-5-20250929"
    turns = int(_opt("--turns") or "100")

    # Load config
    config_file = Path(config_path)
    if not config_file.exists():
        print(f"  Config file not found: {config_path}")
        return

    try:
        configs = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  Invalid JSON in config file: {e}")
        return

    if not isinstance(configs, list) or not configs:
        print("  Config must be a JSON array of hyperparameter dicts.")
        return

    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_name = proj.get("name", query)
    proj_path = _require_path(proj)
    if not proj_path:
        return

    try:
        sessions = launch_sweep(
            Path(proj_path), proj, configs, model=model, max_turns=turns,
        )

        # Save all sessions
        for s in sessions:
            state.add_session(proj["id"], s["session_id"], s)
        state.save()

        print(f"\n  Launched {len(sessions)} sweep variant(s) for {_bold(proj_name)}:\n")
        for s in sessions:
            tmux_name = s["tmux_session"]
            print(f"    {tmux_name}")

        print(f"\n  Attach to any:")
        print(f"    distillate --attach {query}")
        print(f"\n  Stop all:")
        print(f"    distillate --stop {query}")

    except (FileNotFoundError, RuntimeError) as e:
        print(f"  Error: {e}")


def _chart_export(args: list[str]) -> None:
    """Generate and open a chart PNG for an experiment."""
    import webbrowser

    from distillate.experiments import generate_export_chart, infer_key_metric_name
    from distillate.state import State

    if not args:
        print("Usage: distillate --chart <project> [--metric M] [--log-scale]")
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    metric = _opt("--metric") or infer_key_metric_name(proj)
    if not metric:
        print("  No metric found. Specify one with --metric <name>.")
        return

    runs = list(proj.get("runs", {}).values())
    if not runs:
        print(f"  No runs found for '{proj.get('name', query)}'.")
        return

    use_log = "--log-scale" in sys.argv

    # Read subtitle from PROMPT.md first non-heading line
    subtitle = ""
    proj_path_str = proj.get("path", "")
    if proj_path_str:
        prompt_md = Path(proj_path_str) / "PROMPT.md"
        if prompt_md.exists():
            try:
                for line in prompt_md.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("```"):
                        subtitle = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
                        if len(subtitle) > 80:
                            subtitle = subtitle[:78] + "\u2026"
                        break
            except OSError:
                pass

    try:
        png_bytes = generate_export_chart(
            runs, metric, proj.get("name", query),
            log_scale=use_log, subtitle=subtitle,
        )
    except ValueError as e:
        print(f"  {e}")
        return

    # Write to .distillate/chart.png inside the project
    if proj_path_str:
        out_dir = Path(proj_path_str) / ".distillate"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "chart.png"
    else:
        out_path = Path("chart.png")

    out_path.write_bytes(png_bytes)
    print(f"  Chart saved: {out_path}")
    webbrowser.open(f"file://{out_path.resolve()}")


def _delete_experiment(args: list[str]) -> None:
    """Delete an experiment from tracking (keeps files)."""
    from distillate.launcher import _tmux_session_exists
    from distillate.state import State

    if not args:
        print("Usage: distillate --delete-experiment <project> [--yes]")
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_name = proj.get("name", query)
    proj_id = proj["id"]

    # Refuse if sessions are running
    for sess in proj.get("sessions", {}).values():
        if sess.get("status") == "running":
            tmux_name = sess.get("tmux_session", "")
            if tmux_name and _tmux_session_exists(tmux_name):
                print(f"  Session '{tmux_name}' is still running. Stop it first.")
                return

    run_count = len(proj.get("runs", {}))

    if "--yes" not in sys.argv:
        print(f"\n  Will delete '{_bold(proj_name)}' with {run_count} run(s) from tracking.")
        print("  Source files will NOT be deleted.\n")
        answer = input("  Delete? [y/N] ").strip().lower()
        if answer != "y":
            print("  Cancelled.")
            return

    state.remove_project(proj_id)
    state.save()
    print(f"  Deleted '{proj_name}' ({run_count} runs removed from tracking).")


def _edit_prompt(args: list[str]) -> None:
    """Open PROMPT.md in $EDITOR for a project."""
    import subprocess

    from distillate.experiments import detect_primary_metric
    from distillate.state import State

    if not args:
        print("Usage: distillate --edit-prompt <project>")
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_path = _require_path(proj)
    if not proj_path:
        return

    prompt_path = Path(proj_path) / "PROMPT.md"
    if not prompt_path.exists():
        print(f"  No PROMPT.md found at {prompt_path}")
        return

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vim"
    subprocess.run([editor, str(prompt_path)])

    # After editor closes, detect metric and set flag
    try:
        content = prompt_path.read_text(encoding="utf-8")
    except OSError:
        return

    detected = detect_primary_metric(content)
    if detected:
        state.update_project(proj["id"], key_metric_name=detected)
        state.save()
        print(f"  Detected primary metric: {detected}")

    # Write flag for running agents
    distillate_dir = Path(proj_path) / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    from datetime import datetime, timezone
    flag = distillate_dir / "prompt_updated"
    flag.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")
    print("  Prompt updated (running agents will pick up changes).")


def _steer(args: list[str]) -> None:
    """Write steering instructions for the next experiment session."""
    from distillate.launcher import write_steering
    from distillate.state import State

    if len(args) < 2:
        print("Usage: distillate --steer <project> \"text\"")
        return

    query = args[0]
    text = " ".join(args[1:])

    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_path = _require_path(proj)
    if not proj_path:
        return

    path = write_steering(Path(proj_path), text)
    print(f"  Steering written: {path}")
    preview = text[:120] + ("..." if len(text) > 120 else "")
    print(f"  → {preview}")


def _sparkline(values: list[float], width: int = 8) -> str:
    """Render a list of floats as a Unicode sparkline."""
    if not values:
        return ""
    bars = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    span = hi - lo if hi != lo else 1.0
    recent = values[-width:]
    return "".join(bars[min(int((v - lo) / span * (len(bars) - 1)), len(bars) - 1)]
                   for v in recent)


def _tail_jsonl(path: Path, offset: int) -> tuple[list[dict], int]:
    """Read new lines from a JSONL file starting at byte *offset*.

    Returns (parsed_events, new_offset).
    """
    if not path.exists():
        return [], offset
    try:
        size = path.stat().st_size
        if size <= offset:
            return [], offset
        with open(path, "r", encoding="utf-8") as f:
            f.seek(offset)
            lines = f.readlines()
        new_offset = path.stat().st_size
    except OSError:
        return [], offset

    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events, new_offset


def _format_watch_event(event: dict) -> str | None:
    """Format a JSONL event for terminal display. Returns colored string or None."""
    etype = event.get("type", "")
    ts = event.get("ts", "")[:19]

    if etype == "metric_update":
        metric = event.get("metric", "?")
        value = event.get("value", "?")
        history = event.get("history", [])
        spark = _sparkline(history) if history else ""
        return f"  \033[36m[{ts}]\033[0m {metric}={value} {spark}"

    if etype == "run_completed":
        run_id = event.get("run_id", "?")
        results = event.get("results", {})
        status = event.get("status", "?")
        metrics_str = ", ".join(f"{k}={v}" for k, v in results.items()
                                if isinstance(v, (int, float)))
        color = "\033[32m" if status == "keep" else "\033[33m"
        return f"  {color}[{ts}]\033[0m Run {run_id}: {metrics_str} [{status}]"

    if etype == "session_end":
        reason = event.get("stop_reason", event.get("reason", "?"))
        return f"  \033[2m[{ts}]\033[0m Session ended: {reason}"

    if etype == "goal_reached":
        return f"  \033[1;32m[{ts}] Goal reached!\033[0m"

    if etype == "campaign_run_started":
        n = event.get("sessions_launched", 0)
        remaining = event.get("budget_remaining", "?")
        return f"  \033[34m[{ts}]\033[0m Campaign session #{n} started ({remaining} remaining)"

    if etype == "campaign_completed":
        reason = event.get("stop_reason", "?")
        return f"  \033[1m[{ts}]\033[0m Campaign completed: {reason}"

    # Generic fallback for unknown event types
    return f"  \033[2m[{ts}]\033[0m {etype}"


def _watch(args: list[str]) -> None:
    """Watch an experiment repo and regenerate notebooks on changes.

    Also tails events.jsonl, runs.jsonl, and live_metrics.jsonl for
    live event display with sparklines.
    """
    import time
    import webbrowser

    from distillate import config
    from distillate.experiments import (
        generate_html_notebook,
        generate_notebook,
        load_enrichment_cache,
        scan_project,
        watch_project_artifacts,
    )

    config.setup_logging()

    if not args:
        print("Usage: distillate --watch <path|project>")
        return

    # Resolve project name to path
    from distillate.state import State as _WatchState
    _ws = _WatchState()
    _wp = _ws.find_project(args[0])
    if _wp and _wp.get("path"):
        project_path = Path(_wp["path"]).resolve()
    else:
        project_path = Path(args[0]).resolve()
    if not project_path.is_dir():
        print(f"Not a directory: {project_path}")
        return

    print(f"  Watching {project_path}...")

    # Initial scan
    project = scan_project(project_path)
    if "error" in project:
        print(f"  Error: {project['error']}")
        return

    runs_count = len(project.get("runs", {}))
    print(f"  Found {runs_count} experiment(s)")

    # Load LLM enrichment (insights, lessons learned)
    enrichment = load_enrichment_cache(project_path)

    # Generate initial notebook
    html = generate_html_notebook(project, enrichment=enrichment)
    html_path = project_path / ".distillate" / "notebook.html"
    html_path.parent.mkdir(exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    md = generate_notebook(project, enrichment=enrichment)
    md_path = project_path / ".distillate" / "notebook.md"
    md_path.write_text(md, encoding="utf-8")

    print(f"  Generated notebook: {html_path}")
    webbrowser.open(f"file://{html_path}")

    # Initialize JSONL tail offsets
    distillate_dir = project_path / ".distillate"
    tail_files = {
        "events": distillate_dir / "events.jsonl",
        "runs": distillate_dir / "runs.jsonl",
        "metrics": distillate_dir / "live_metrics.jsonl",
    }
    offsets: dict[str, int] = {}
    for key, fpath in tail_files.items():
        offsets[key] = fpath.stat().st_size if fpath.exists() else 0

    # Watch loop
    print("  Watching for changes (Ctrl+C to stop)...")
    try:
        while True:
            time.sleep(5)

            # Tail JSONL files for live events
            for key, fpath in tail_files.items():
                new_events, new_offset = _tail_jsonl(fpath, offsets[key])
                offsets[key] = new_offset
                for evt in new_events:
                    line = _format_watch_event(evt)
                    if line:
                        print(line)

            # Check for artifact changes (notebook regen)
            new_data = watch_project_artifacts(project_path)
            if new_data:
                print(f"  Detected {len(new_data)} new event(s), regenerating...")
                project = scan_project(project_path)
                enrichment = load_enrichment_cache(project_path)
                if "error" not in project:
                    html = generate_html_notebook(project, enrichment=enrichment)
                    html_path.write_text(html, encoding="utf-8")
                    md = generate_notebook(project, enrichment=enrichment)
                    md_path.write_text(md, encoding="utf-8")
                    new_runs = len(project.get("runs", {}))
                    print(f"  Updated: {new_runs} experiment(s)")
    except KeyboardInterrupt:
        print("\n  Stopped watching.")


# ---------------------------------------------------------------------------
# Desktop → CLI bridge commands
# ---------------------------------------------------------------------------

def _update_project(args: list[str]) -> None:
    """Update project metadata (description, key metric)."""
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print('Usage: distillate --update <project> [--key-metric M] [--description "..."]')
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    updates: dict = {}
    key_metric = _opt("--key-metric")
    description = _opt("--description")
    if key_metric:
        updates["key_metric_name"] = key_metric
    if description:
        updates["description"] = description

    if not updates:
        print('  Nothing to update. Use --key-metric or --description.')
        return

    state.update_project(proj["id"], **updates)
    state.save()
    print(f"  Updated {_bold(proj.get('name', query))}:")
    for k, v in updates.items():
        print(f"    {k}: {v}")


def _queue_sessions(args: list[str]) -> None:
    """Queue N continuation sessions for a project."""
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --queue-sessions <project> [--count N] [--model M] [--turns T]")
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    count = int(_opt("--count") or "1")
    model = _opt("--model") or "claude-sonnet-4-5-20250929"
    max_turns = int(_opt("--turns") or "100")

    state.update_project(proj["id"], continuation_queue={
        "count": count,
        "model": model,
        "max_turns": max_turns,
    }, auto_continue=True)
    state.save()

    proj_name = proj.get("name", query)
    print(f"  Queued {_bold(str(count))} continuation session(s) for {_bold(proj_name)}")
    print(f"  Model: {model} | Max turns: {max_turns}")


def _list_templates() -> None:
    """List available experiment templates."""
    from distillate.launcher import list_templates

    templates = list_templates()
    if not templates:
        print("  No templates available yet.")
        print("  Import one: distillate --save-template <project>")
        return

    print()
    print(f"  {'#':>3}  {'Name':<22} {'Lines':>5}  {'Data'}")
    print(f"  {'─' * 3}  {'─' * 22} {'─' * 5}  {'─' * 6}")
    for i, t in enumerate(templates, 1):
        data = "yes" if t.get("has_data") else "no"
        print(f"  {i:>3}  {t['name']:<22} {t.get('prompt_lines', 0):>5}  {data}")
    print()


def _save_template(args: list[str]) -> None:
    """Save a project's config as a reusable template."""
    from distillate.launcher import import_template
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --save-template <project> [--name N]")
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_path = _require_path(proj)
    if not proj_path:
        return

    name = _opt("--name")
    template_name = import_template(Path(proj_path), name=name)
    print(f"  Saved template '{_bold(template_name)}' from {proj.get('name', query)}")


def _compare_projects(args: list[str]) -> None:
    """Side-by-side experiment comparison table."""
    from distillate.state import State

    # Collect project identifiers (stop at flags)
    queries = [a for a in args if not a.startswith("-")]
    if len(queries) < 2:
        print("Usage: distillate --compare <proj1> <proj2> [proj3...]")
        return

    state = State()
    projects: list[dict] = []
    all_metrics: set[str] = set()
    _LOWER_IS_BETTER = {"loss", "val_loss", "rmse", "mae", "perplexity", "val_bpb"}

    for q in queries:
        proj = _resolve_project_or_bail(q, state)
        if not proj:
            return

        best: dict[str, float] = {}
        for run in proj.get("runs", {}).values():
            if run.get("decision") != "keep" and run.get("status") != "keep":
                continue
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    all_metrics.add(k)
                    # Use goal direction if available
                    goal_dirs = {g["metric"]: g["direction"] for g in proj.get("goals", [])}
                    direction = goal_dirs.get(k, "minimize" if k in _LOWER_IS_BETTER else "maximize")
                    if k not in best:
                        best[k] = v
                    elif direction == "maximize":
                        best[k] = max(best[k], v)
                    else:
                        best[k] = min(best[k], v)

        projects.append({"name": proj.get("name", q), "best": best, "goals": proj.get("goals", [])})

    sorted_metrics = sorted(all_metrics)
    if not sorted_metrics:
        print("  No metrics to compare (no kept runs with results).")
        return

    # Build table: metrics as rows, projects as columns
    name_width = max(len(p["name"]) for p in projects)
    col_width = max(name_width, 12)

    # Header
    print()
    header = f"  {'Metric':<22}"
    for p in projects:
        header += f"  {p['name']:>{col_width}}"
    print(header)
    print(f"  {'─' * 22}" + "".join(f"  {'─' * col_width}" for _ in projects))

    # Find global best per metric (for starring)
    for metric in sorted_metrics:
        goal_dirs = {}
        for p in projects:
            for g in p["goals"]:
                if g["metric"] == metric:
                    goal_dirs[metric] = g["direction"]
        direction = goal_dirs.get(metric, "minimize" if metric in _LOWER_IS_BETTER else "maximize")

        vals = [(p["best"].get(metric), p) for p in projects]
        numeric_vals = [v for v, _ in vals if v is not None]
        global_best = None
        if numeric_vals:
            global_best = max(numeric_vals) if direction == "maximize" else min(numeric_vals)

        row = f"  {metric:<22}"
        for val, p in vals:
            if val is None:
                cell = "—"
            else:
                cell = f"{val:.4f}" if isinstance(val, float) else str(val)
                if val == global_best and len(numeric_vals) > 1:
                    cell += " *"
            row += f"  {cell:>{col_width}}"
        print(row)

    print()


def _github(args: list[str]) -> None:
    """Create a GitHub repo for a project."""
    from distillate.launcher import create_github_repo
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print("Usage: distillate --github <project> [--name repo] [--private]")
        return

    query = args[0]
    state = State()
    proj = _resolve_project_or_bail(query, state)
    if not proj:
        return

    proj_path = _require_path(proj)
    if not proj_path:
        return

    repo_name = _opt("--name") or proj.get("id", "experiment")
    private = "--private" in sys.argv

    result = create_github_repo(Path(proj_path), repo_name, private=private)
    if result.get("ok"):
        state.update_project(proj["id"], github_url=result["url"])
        state.save()
        print(f"  Created repo: {_bold(result['url'])}")
    else:
        print(f"  Error: {result.get('reason', 'unknown error')}")


def _create_experiment(args: list[str]) -> None:
    """Create experiment from scratch (non-interactive CLI wizard)."""
    from distillate.experiment_tools import _parse_goals_from_text, init_experiment_tool
    from distillate.state import State

    if not args or args[0].startswith("-"):
        print('Usage: distillate --create-experiment <name> [--goal "..."] [--target /path] '
              '[--metric M] [--direction maximize|minimize]')
        return

    name = args[0]
    goal = _opt("--goal") or ""
    target = _opt("--target") or ""
    metric = _opt("--metric") or ""
    direction = _opt("--direction") or ""

    if not target:
        from distillate import config
        from distillate.experiments import slugify
        if config.EXPERIMENTS_ROOT:
            target = str(Path(config.EXPERIMENTS_ROOT) / slugify(name))
        else:
            target = str(Path.home() / "experiments" / slugify(name))

    state = State()
    print(f"  Creating experiment {_bold(name)} at {target}...")

    result = init_experiment_tool(
        state=state,
        path=target,
        goal=goal,
        name=name,
        primary_metric=metric,
        metric_direction=direction,
    )

    if result.get("success"):
        print(f"  Project registered: {result.get('project_id', '')}")
        if result.get("goals_set"):
            print(f"  Goals: {result['goals_set']}")
        print(f"\n  Launch it:")
        print(f"    distillate --launch {result.get('project_id', name)}")
    else:
        print(f"  Error: {result.get('error', 'unknown')}")


def _parallel_campaign(args: list[str]) -> None:
    """Launch campaigns across multiple projects in parallel."""
    import signal
    import threading
    from datetime import datetime, timezone

    from distillate.launcher import run_campaign, should_continue
    from distillate.state import State

    # Collect project identifiers (stop at flags)
    queries = [a for a in args if not a.startswith("-")]
    if not queries:
        print("Usage: distillate --parallel-campaign <proj1> <proj2> [...] [--budget N] [--model M]")
        return

    budget = int(_opt("--budget") or "10")
    model = _opt("--model") or "claude-sonnet-4-5-20250929"
    max_turns = int(_opt("--turns") or "100")

    state = State()
    resolved: list[dict] = []
    for q in queries:
        proj = _resolve_project_or_bail(q, state)
        if not proj:
            return
        if not proj.get("goals"):
            print(f"  Cannot start campaign: '{proj.get('name', q)}' has no goals.")
            return
        if not should_continue(proj):
            print(f"  All goals for '{proj.get('name', q)}' already met — skipping.")
            continue
        resolved.append(proj)

    if not resolved:
        print("  No projects need campaigning.")
        return

    stop_flag = threading.Event()
    old_handler = signal.signal(signal.SIGINT, lambda s, f: stop_flag.set())

    print(f"\n  Launching parallel campaigns ({len(resolved)} projects):")
    for p in resolved:
        print(f"    - {_bold(p.get('name', p['id']))}")
    print(f"  Budget: {budget} sessions each, model: {model}")
    print(f"  Press Ctrl+C to stop\n")

    # Set campaign state for each project
    now = datetime.now(timezone.utc).isoformat()
    for proj in resolved:
        campaign = {
            "status": "running",
            "started_at": now,
            "objective": "",
            "budget": {"max_sessions": budget, "max_hours": 8},
            "model": model,
            "max_turns": max_turns,
            "sessions_launched": 0,
            "current_session_id": None,
            "completed_at": None,
            "stop_reason": None,
        }
        state.update_project(proj["id"], campaign=campaign, auto_continue=True)
    state.save()

    results: dict[str, dict] = {}
    lock = threading.Lock()

    def _run_one(proj: dict) -> None:
        pid = proj["id"]
        pname = proj.get("name", pid)

        def _on_event(event):
            etype = event.get("type", "")
            ts = event.get("ts", "")[:19]
            if etype == "campaign_run_started":
                n = event.get("sessions_launched", 0)
                print(f"  [{ts}] {pname}: session #{n} started")
            elif etype == "goal_reached":
                print(f"  [{ts}] {pname}: \033[1;32mgoal reached!\033[0m")
            elif etype == "campaign_completed":
                reason = event.get("stop_reason", "?")
                print(f"  [{ts}] {pname}: completed ({reason})")

        r = run_campaign(
            pid, state,
            max_sessions=budget,
            model=model,
            max_turns=max_turns,
            on_event=_on_event,
            stop_flag=stop_flag,
        )
        with lock:
            results[pid] = r

    threads = [threading.Thread(target=_run_one, args=(p,)) for p in resolved]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    signal.signal(signal.SIGINT, old_handler)

    print(f"\n  Parallel campaigns finished:")
    for proj in resolved:
        r = results.get(proj["id"], {})
        reason = r.get("stop_reason", "unknown")
        launched = r.get("sessions_launched", 0)
        print(f"    {proj.get('name', proj['id'])}: {reason} ({launched} sessions)")
