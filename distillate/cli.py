"""CLI entry point and dispatcher.

Parses flags from sys.argv and routes to the appropriate command handler.
Terminal formatting helpers used by other modules also live here.
"""

import logging
import os
import sys
from pathlib import Path

import requests

log = logging.getLogger("distillate")

_DIM = "\033[2m"
_RESET = "\033[0m"


def _is_dark_background() -> bool:
    """Guess if the terminal has a dark background.

    Checks COLORFGBG (set by many terminals: 'fg;bg', bg>=8 is dark)
    and common dark-theme env hints. Defaults to True (most terminals).
    """
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        try:
            bg = int(colorfgbg.rsplit(";", 1)[-1])
            return bg < 8  # 0-7 are dark ANSI colors
        except (ValueError, IndexError):
            pass
    # Common dark terminal indicators
    if os.environ.get("TERM_PROGRAM") in ("iTerm.app", "Hyper", "Alacritty"):
        return True
    return True  # most terminals default dark


def _bold(text: str) -> str:
    """Wrap text in bold, bright white on dark backgrounds."""
    if sys.stdout.isatty():
        if _is_dark_background():
            return f"\033[1;97m{text}{_RESET}"
        return f"\033[1m{text}{_RESET}"
    return text


def _dim(text: str) -> str:
    """Wrap text in ANSI dim, only when stdout is a TTY."""
    if sys.stdout.isatty():
        return f"{_DIM}{text}{_RESET}"
    return text


def _opt(flag: str) -> str | None:
    """Extract the value for a --flag <value> pair from sys.argv, or None."""
    if flag not in sys.argv:
        return None
    idx = sys.argv.index(flag)
    if idx + 1 < len(sys.argv):
        return sys.argv[idx + 1]
    return None


try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("distillate")
except Exception:
    _VERSION = "0.0.0"

_HELP = """\
Usage: distillate [question]

  distillate              Open the interactive agent (requires Claude Code)
  distillate "question"   Ask a single question, then exit

Experiments:

  Setup:
  --new-experiment [tmpl] Scaffold a new experiment from a template
  --create-experiment <name> [--goal "..."] [--target /path] [--metric M]
                          Create experiment from scratch (non-interactive)
  --install-hooks <path>  Install Claude Code hooks for experiment capture
  --scan-projects         Scan tracked projects for new experiments

  Run:
  --launch <name>         Launch an auto-research session (tmux)
  --continue <project>    Launch continuation session (checks goals first)
  --sweep <project> --config <sweep.json>
                          Launch parallel sweep from config file
  --attach <name>         Attach to a running experiment session
  --stop <name>           Stop a running experiment session
  --edit-prompt <project> Edit PROMPT.md in $EDITOR
  --steer <project> "text"  Write steering instructions for next session

  Campaign:
  --campaign start|status|stop <project>
                          Run an autonomous campaign loop
  --parallel-campaign <proj1> <proj2> [...] [--budget N] [--model M]
                          Launch campaigns across multiple projects
  --goals <project> ["metric>0.95" ...]
                          View or set metric goals for a project
  --queue-sessions <project> [--count N] [--model M] [--turns T]
                          Queue N continuation sessions

  Inspect:
  --experiments           List all tracked experiments with status
  --show <project>        Detailed experiment dashboard
  --runs <project>        Full run history with metrics
  --notebook <project>    Generate and open HTML notebook
  --chart <project>       Export metric chart as PNG and open it
  --compare <proj1> <proj2> [proj3...]
                          Side-by-side experiment comparison
  --watch <path>          Watch an experiment repo and regenerate notebooks
  --update <project> [--key-metric M] [--description "..."]
                          Update project metadata
  --templates             List available experiment templates
  --save-template <project> [--name N]
                          Save a project config as a reusable template
  --github <project> [--name repo] [--private]
                          Create GitHub repo for a project
  --delete-experiment <project>
                          Remove experiment from tracking (keeps files)

Papers:
  --sync                  Sync papers: Zotero -> reMarkable -> notes
  --import                Import existing papers from Zotero
  --status                Show experiment and reading status
  --list                  List all tracked papers (summary table)
  --queue                 Browse all papers interactively (paged, press space)
  --suggest               Pick papers for your reading queue (interactive)
  --suggest-email         Email today's suggestions (non-interactive)
  --digest                Show your reading digest
  --report                Show reading insights dashboard
  --schedule              Set up automatic syncing (launchd/cron)
  --init                  Run the setup wizard
  --remove "Title"        Remove a paper from tracking
  --reprocess "Title"     Re-extract highlights and regenerate note

Advanced:
  --backfill-s2           Refresh Semantic Scholar data for all papers
  --backfill-highlights [N]  Back-propagate highlights to Zotero (last N papers)
  --refresh-metadata [Q]  Re-fetch metadata from Zotero + Semantic Scholar
  --sync-state            Push state.json to a GitHub Gist
  --export-state <path>   Export state.json to a file
  --import-state <path>   Import state.json from a file (backs up existing)

Options:
  -v, --verbose           Show INFO-level logs on console
  -h, --help              Show this help
  -V, --version           Show version
"""

_KNOWN_FLAGS = {
    "--help", "-h", "--version", "-V", "--verbose", "-v", "--init", "--register",
    "--status", "--list", "--queue", "--remove", "--import", "--reprocess",
    "--digest", "--schedule", "--send-digest", "--sync",
    "--backfill-s2", "--backfill-highlights", "--refresh-metadata",
    "--suggest", "--suggest-email", "--sync-state",
    "--export-state", "--import-state", "--report",
    "--scan-projects", "--install-hooks", "--watch",
    "--new-experiment", "--launch", "--experiments", "--attach", "--stop",
    "--campaign", "--steer", "--show", "--runs", "--notebook",
    "--continue", "--sweep", "--goals", "--config",
    "--host", "--model", "--turns", "--target", "--name",
    "--update", "--queue-sessions", "--templates", "--save-template",
    "--compare", "--github", "--create-experiment", "--parallel-campaign",
    "--key-metric", "--description", "--count", "--private",
    "--direction", "--metric", "--budget", "--goal",
    "--chart", "--delete-experiment", "--edit-prompt", "--yes", "--log-scale",
}


def main():
    from distillate import commands
    from distillate import pipeline
    from distillate import wizard

    if "--verbose" in sys.argv or "-v" in sys.argv:
        from distillate import config
        config.VERBOSE = True

    if "--help" in sys.argv or "-h" in sys.argv:
        print(_HELP)
        return

    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"distillate {_VERSION}")
        return

    if "--init" in sys.argv:
        wizard._init_wizard()
        return

    if "--register" in sys.argv:
        from distillate.remarkable_auth import register_interactive
        register_interactive()
        return

    # Commands that only need local state (no Zotero credentials)
    if "--status" in sys.argv:
        commands._status()
        return

    if "--list" in sys.argv:
        commands._list()
        return

    if "--queue" in sys.argv:
        commands._queue()
        return

    if "--remove" in sys.argv:
        idx = sys.argv.index("--remove")
        commands._remove(sys.argv[idx + 1:])
        return

    from distillate import config
    config.ensure_loaded()

    if "--import" in sys.argv:
        idx = sys.argv.index("--import")
        commands._import(sys.argv[idx + 1:])
        return

    if "--reprocess" in sys.argv:
        idx = sys.argv.index("--reprocess")
        pipeline._reprocess(sys.argv[idx + 1:])
        return

    if "--digest" in sys.argv:
        commands._print_digest()
        return

    if "--schedule" in sys.argv:
        wizard._schedule()
        return

    if "--send-digest" in sys.argv:
        from distillate import digest
        digest.send_weekly_digest()
        return

    if "--backfill-s2" in sys.argv:
        commands._backfill_s2()
        return

    if "--backfill-highlights" in sys.argv:
        idx = sys.argv.index("--backfill-highlights")
        commands._backfill_highlights(sys.argv[idx + 1:])
        return

    if "--refresh-metadata" in sys.argv:
        idx = sys.argv.index("--refresh-metadata")
        commands._refresh_metadata(sys.argv[idx + 1:] or None)
        return

    if "--suggest" in sys.argv:
        commands._suggest()
        return

    if "--suggest-email" in sys.argv:
        from distillate import digest
        digest.send_suggestion()
        return

    if "--export-state" in sys.argv:
        idx = sys.argv.index("--export-state")
        if idx + 1 >= len(sys.argv):
            print("Usage: distillate --export-state <path>")
            sys.exit(1)
        commands._export_state(sys.argv[idx + 1])
        return

    if "--import-state" in sys.argv:
        idx = sys.argv.index("--import-state")
        if idx + 1 >= len(sys.argv):
            print("Usage: distillate --import-state <path>")
            sys.exit(1)
        commands._import_state(sys.argv[idx + 1])
        return

    if "--report" in sys.argv:
        commands._report()
        return

    if "--sync-state" in sys.argv:
        from distillate.cloud_sync import cloud_sync_available, sync_state as cloud_sync
        from distillate.state import State
        if cloud_sync_available():
            cloud_sync(State())
        else:
            commands._sync_state()
        return

    if "--new-experiment" in sys.argv:
        idx = sys.argv.index("--new-experiment")
        commands._new_experiment(sys.argv[idx + 1:])
        return

    if "--launch" in sys.argv:
        idx = sys.argv.index("--launch")
        commands._launch_experiment(sys.argv[idx + 1:])
        return

    if "--experiments" in sys.argv:
        commands._list_experiments()
        return

    if "--attach" in sys.argv:
        idx = sys.argv.index("--attach")
        commands._attach_experiment(sys.argv[idx + 1:])
        return

    if "--stop" in sys.argv:
        idx = sys.argv.index("--stop")
        commands._stop_experiment(sys.argv[idx + 1:])
        return

    if "--goals" in sys.argv:
        idx = sys.argv.index("--goals")
        commands._goals(sys.argv[idx + 1:])
        return

    if "--show" in sys.argv:
        idx = sys.argv.index("--show")
        commands._show_experiment(sys.argv[idx + 1:])
        return

    if "--runs" in sys.argv:
        idx = sys.argv.index("--runs")
        commands._show_runs(sys.argv[idx + 1:])
        return

    if "--notebook" in sys.argv:
        idx = sys.argv.index("--notebook")
        commands._open_notebook(sys.argv[idx + 1:])
        return

    if "--continue" in sys.argv:
        idx = sys.argv.index("--continue")
        commands._continue_experiment(sys.argv[idx + 1:])
        return

    if "--sweep" in sys.argv:
        idx = sys.argv.index("--sweep")
        commands._sweep_experiment(sys.argv[idx + 1:])
        return

    if "--campaign" in sys.argv:
        idx = sys.argv.index("--campaign")
        commands._campaign(sys.argv[idx + 1:])
        return

    if "--chart" in sys.argv:
        idx = sys.argv.index("--chart")
        commands._chart_export(sys.argv[idx + 1:])
        return

    if "--delete-experiment" in sys.argv:
        idx = sys.argv.index("--delete-experiment")
        commands._delete_experiment(sys.argv[idx + 1:])
        return

    if "--edit-prompt" in sys.argv:
        idx = sys.argv.index("--edit-prompt")
        commands._edit_prompt(sys.argv[idx + 1:])
        return

    if "--steer" in sys.argv:
        idx = sys.argv.index("--steer")
        commands._steer(sys.argv[idx + 1:])
        return

    if "--scan-projects" in sys.argv:
        commands._scan_projects()
        return

    if "--install-hooks" in sys.argv:
        idx = sys.argv.index("--install-hooks")
        commands._install_hooks(sys.argv[idx + 1:])
        return

    if "--watch" in sys.argv:
        idx = sys.argv.index("--watch")
        commands._watch(sys.argv[idx + 1:])
        return

    if "--update" in sys.argv:
        idx = sys.argv.index("--update")
        commands._update_project(sys.argv[idx + 1:])
        return

    if "--queue-sessions" in sys.argv:
        idx = sys.argv.index("--queue-sessions")
        commands._queue_sessions(sys.argv[idx + 1:])
        return

    if "--templates" in sys.argv:
        commands._list_templates()
        return

    if "--save-template" in sys.argv:
        idx = sys.argv.index("--save-template")
        commands._save_template(sys.argv[idx + 1:])
        return

    if "--compare" in sys.argv:
        idx = sys.argv.index("--compare")
        commands._compare_projects(sys.argv[idx + 1:])
        return

    if "--github" in sys.argv:
        idx = sys.argv.index("--github")
        commands._github(sys.argv[idx + 1:])
        return

    if "--create-experiment" in sys.argv:
        idx = sys.argv.index("--create-experiment")
        commands._create_experiment(sys.argv[idx + 1:])
        return

    if "--parallel-campaign" in sys.argv:
        idx = sys.argv.index("--parallel-campaign")
        commands._parallel_campaign(sys.argv[idx + 1:])
        return

    # Catch unknown flags before falling through
    unknown = [a for a in sys.argv[1:] if a.startswith("-") and a not in _KNOWN_FLAGS]
    if unknown:
        print(f"Unknown option: {unknown[0]}")
        print("Run 'distillate --help' for available commands.")
        sys.exit(1)

    # Positional args (not flags) → single-turn agent query
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]
    if positional:
        from distillate.agent import run_chat
        run_chat(positional)
        return

    # No flags, no positional args → agent (TTY) or sync (non-TTY / --sync)
    if "--sync" not in sys.argv and sys.stdin.isatty() and sys.stdout.isatty():
        from distillate.agent import run_chat
        run_chat()
        return

    pipeline.run_sync()


def _main_wrapper():
    """Entry point with top-level error handling."""
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as _exc:
        log.debug("Unhandled exception", exc_info=True)
        print(
            f"\n  Unexpected error: {_exc}"
            "\n  Please report at: https://github.com/rlacombe/distillate/issues\n"
        )
        sys.exit(1)
