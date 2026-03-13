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

  distillate              Open the interactive agent (requires API key)
  distillate "question"   Ask a single question, then exit

Experiments:
  --new-experiment [tmpl] Scaffold a new experiment from a template
  --launch <name>         Launch an auto-research session (tmux)
  --campaign start|status|stop <project>
                          Run an autonomous campaign loop
  --steer <project> "text"  Write steering instructions for next session
  --experiments           List all tracked experiments with status
  --attach <name>         Attach to a running experiment session
  --stop <name>           Stop a running experiment session
  --scan-projects         Scan tracked projects for new experiments
  --install-hooks <path>  Install Claude Code hooks for experiment capture
  --watch <path>          Watch an experiment repo and regenerate notebooks

Papers:
  --sync                  Sync papers: Zotero -> reMarkable -> notes
  --import                Import existing papers from Zotero
  --status                Show experiment and reading status
  --list                  List all tracked papers
  --queue                 Browse all papers (paged, press space to scroll)
  --suggest               Pick papers for your queue and promote to tablet
  --digest                Show your reading digest
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
  --report                Show reading insights dashboard

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
    "--campaign", "--steer",
    "--host", "--model", "--turns", "--target", "--name",
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

    if "--campaign" in sys.argv:
        idx = sys.argv.index("--campaign")
        commands._campaign(sys.argv[idx + 1:])
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
