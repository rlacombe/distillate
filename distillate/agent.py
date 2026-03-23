"""Interactive agent REPL for Distillate.

Provides a conversational interface to the paper library using Claude
Code via the Agent SDK. Launched via ``distillate`` (in a TTY) or
``distillate "question"`` for single-turn mode.

Terminal rendering (spinners, ANSI) lives here.  The Agent SDK
(``agent_sdk.NicolasClient``) manages the Claude Code subprocess
and MCP tool connections.
"""

import asyncio
import json
import logging
import os
import random
import sys
import threading

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from distillate import config
from distillate.agent_core import (
    VERBOSE_TOOLS,
    build_system_prompt as _build_system_prompt,  # noqa: F401 (re-export for tests)
    execute_tool as _execute_tool,  # noqa: F401 (re-export for tests)
    tool_label as _tool_label,
)
from distillate.state import State

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation log — persists across sessions
# ---------------------------------------------------------------------------

_CONVERSATION_LOG_PATH = config.CONFIG_DIR / "conversations.json"
_MAX_SESSIONS = 50

_console = Console(highlight=False)


def _load_conversation_log() -> list[dict]:
    """Load conversation history from disk."""
    try:
        return json.loads(_CONVERSATION_LOG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_conversation_log(sessions: list[dict]) -> None:
    """Save conversation history, keeping the most recent sessions."""
    trimmed = sessions[-_MAX_SESSIONS:]
    _CONVERSATION_LOG_PATH.write_text(
        json.dumps(trimmed, ensure_ascii=False, indent=None),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_DIM = "\033[2m"
_RESET = "\033[0m"


def _is_tty() -> bool:
    return sys.stdout.isatty()


def _is_dark_background() -> bool:
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        try:
            bg = int(colorfgbg.rsplit(";", 1)[-1])
            return bg < 8  # 0-7 are dark ANSI colors
        except ValueError:
            pass
    return True


def _bold(text: str) -> str:
    if _is_tty():
        if _is_dark_background():
            return f"\033[1;97m{text}{_RESET}"
        return f"\033[1m{text}{_RESET}"
    return text


def _dim(text: str) -> str:
    if _is_tty():
        return f"{_DIM}{text}{_RESET}"
    return text


def _bold_ansi() -> str:
    """Return the ANSI escape for bold (background-aware)."""
    if _is_dark_background():
        return "\033[1;97m"
    return "\033[1m"


class _StreamFormatter:
    """Convert **bold** markdown markers to ANSI bold in streamed text.

    Handles ** split across chunk boundaries with a one-char buffer.
    """

    def __init__(self) -> None:
        self._in_bold = False
        self._pending_star = False

    def feed(self, text: str) -> str:
        """Process a chunk, return ANSI-formatted output."""
        if not _is_tty():
            return text
        out: list[str] = []
        for ch in text:
            if self._pending_star:
                self._pending_star = False
                if ch == "*":
                    # Got ** → toggle bold
                    self._in_bold = not self._in_bold
                    out.append(_bold_ansi() if self._in_bold else _RESET)
                    continue
                # Single * → emit the buffered star, then this char
                out.append("*")
                out.append(ch)
                continue
            if ch == "*":
                self._pending_star = True
            else:
                out.append(ch)
        return "".join(out)

    def flush(self) -> str:
        """Flush any buffered character at end of stream."""
        if self._pending_star:
            self._pending_star = False
            return "*"
        if self._in_bold:
            self._in_bold = False
            return _RESET
        return ""


_THINKING_PHRASES = [
    "\U0001F525 Heating the flask",
    "\U0001F9EA Dissolving the precipitate",
    "\u2697\ufe0f Filtering the solution",
    "\U0001F4A7 Distilling the essence",
    "\U0001F52C Reading the residue",
    "\U0001F9EB Decanting the extract",
    "\u2697\ufe0f Measuring the tincture",
    "\U0001F31F Stirring the crucible",
    "\U0001F4DC Consulting the codex",
    "\U0001F9EA Preparing the solvent",
    "\U0001F52C Observing the reaction",
    "\U0001F4A8 Condensing the vapor",
]

_SPINNER_FRAMES = ["\u280b", "\u2819", "\u2838", "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]


class _ThinkingSpinner:
    """Animated spinner shown while waiting for the first token."""

    def __init__(self, phrase: str | None = None) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._phrase = phrase or random.choice(_THINKING_PHRASES)  # noqa: S311

    def start(self) -> None:
        if not _is_tty():
            return
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, keep_label: bool = False) -> None:
        if self._stop.is_set():
            return  # already stopped — idempotent
        self._stop.set()
        if self._thread:
            self._thread.join()
        if _is_tty():
            if keep_label:
                # Freeze the spinner text and move to next line
                print(flush=True)
            else:
                # Erase the spinner line
                print("\r\033[2K", end="", flush=True)

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = _SPINNER_FRAMES[i % len(_SPINNER_FRAMES)]
            text = f"\033[35m{frame}\033[1;35m {self._phrase}{_RESET}"
            print(f"\r\033[2K{text}", end="", flush=True)
            i += 1
            self._stop.wait(0.1)


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

def run_chat(initial_args: Optional[List[str]] = None) -> None:
    """Entry point for interactive chat mode — bridges sync to async."""
    try:
        from claude_agent_sdk import ClaudeSDKClient  # noqa: F401
    except ImportError:
        print(
            "\n  Nicolas requires Claude Code.\n"
            "  Install it from: https://docs.anthropic.com/en/docs/claude-code\n"
            "  Then: pip install claude-agent-sdk\n"
        )
        sys.exit(1)

    asyncio.run(_async_run_chat(initial_args))


async def _async_run_chat(initial_args: Optional[List[str]] = None) -> None:
    """Async REPL loop driven by NicolasClient (Agent SDK)."""
    from distillate.agent_sdk import NicolasClient

    state = State()
    nicolas = NicolasClient(state)

    # Load conversation history for cross-session memory
    all_sessions = _load_conversation_log()
    current_session: dict = {
        "session_id": datetime.now(timezone.utc).isoformat(),
        "messages": [],
    }

    # Single-turn mode: answer one question and exit
    if initial_args:
        query = " ".join(initial_args)
        await _render_turn_sdk(nicolas, query, stream=False)
        await nicolas.disconnect()
        return

    # Interactive REPL — clear screen for full-screen feel
    if _is_tty():
        print("\033[2J\033[H", end="", flush=True)
    _print_welcome(state)

    loop = asyncio.get_event_loop()

    while True:
        try:
            user_input = await loop.run_in_executor(None, _prompt_input)
            if user_input is None:
                # EOFError or KeyboardInterrupt
                print()
                break
        except KeyboardInterrupt:
            print()
            break

        if not user_input:
            continue
        if user_input.lower().rstrip(".!") in ("exit", "quit", "/quit", "/exit", "/q"):
            print("\n  \u2697\ufe0f  See you next time!\n")
            break
        if user_input.lower() in ("/clear",):
            await nicolas.new_conversation()
            print("  Conversation cleared.")
            continue
        if user_input.lower() in ("/help",):
            _print_help()
            continue
        if user_input.lower() in ("/init",):
            _run_init()
            state.reload()
            continue
        if user_input.lower().startswith("/papers"):
            _show_papers(state, user_input)
            continue
        if user_input.lower().startswith("/experiments"):
            _show_experiments(state)
            continue
        if user_input.lower().startswith("/detail"):
            _show_detail(state, user_input)
            continue
        if user_input.lower().startswith("/experiment "):
            _show_experiment_detail(state, user_input)
            continue
        if user_input.lower().startswith("/report"):
            _show_report(state)
            continue

        assistant_text = await _render_turn_sdk(nicolas, user_input, stream=True)

        # Log this exchange
        current_session["messages"].append({"role": "user", "content": user_input})
        if assistant_text:
            current_session["messages"].append(
                {"role": "assistant", "content": assistant_text[:200]}
            )

    # Save session on exit (only if there were messages)
    if current_session["messages"]:
        all_sessions.append(current_session)
        _save_conversation_log(all_sessions)

    await nicolas.disconnect()


def _prompt_input() -> str | None:
    """Blocking input() call — run in executor for async compat."""
    try:
        return input("\n> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None


# ---------------------------------------------------------------------------
# Turn rendering — consumes events from NicolasClient.send()
# ---------------------------------------------------------------------------

async def _render_turn_sdk(nicolas, user_input: str, stream: bool = True) -> str:
    """Handle one user turn by consuming Agent SDK events.

    Returns the accumulated assistant text for conversation logging.
    """
    fmt = _StreamFormatter()
    spinner = _ThinkingSpinner()
    first_token = True
    has_text = False
    assistant_text_parts: list[str] = []
    tool_names_seen: set[str] = set()

    # Blank line before response
    if stream:
        print()

    spinner.start()

    try:
        async for event in nicolas.send(user_input):
            etype = event["type"]

            if etype == "text_delta":
                if first_token:
                    spinner.stop()
                    first_token = False
                    has_text = True
                text = event["text"]
                assistant_text_parts.append(text)
                if stream:
                    print(fmt.feed(text), end="", flush=True)
                else:
                    print(fmt.feed(text), end="")

            elif etype == "tool_start":
                # Stop the thinking spinner before tool execution
                spinner.stop()
                first_token = False
                tool_names_seen.add(event.get("name", ""))

                # If text was streamed before this tool, add spacing
                if has_text:
                    print(fmt.flush(), end="")
                    print()  # newline after streamed text
                    print()  # blank line before tool spinner

                # Start a tool-specific spinner
                label = event.get("label") or _tool_label(event["name"])
                spinner = _ThinkingSpinner(label)

                if event.get("verbose"):
                    # Verbose tools print their own progress —
                    # show the label, then let stdout pass through
                    spinner.start()
                    spinner.stop(keep_label=True)
                    if _is_tty():
                        _orig_write = sys.stdout.write
                        sys.stdout.write = lambda s, _w=_orig_write: (
                            _w(f"\033[2;35m{s}{_RESET}") if s.strip() else _w(s)
                        )
                else:
                    spinner.start()

            elif etype == "tool_done":
                # Detect verbose tool from name if present
                tool_name = event.get("name", "")
                if tool_name in VERBOSE_TOOLS and _is_tty():
                    sys.stdout.write = sys.__stdout__.write
                    print()  # blank line after verbose output
                spinner.stop()
                has_text = False

                # Start a new thinking spinner for the next response
                spinner = _ThinkingSpinner()
                spinner.start()
                first_token = True

            elif etype == "turn_end":
                spinner.stop()
                if has_text:
                    print(fmt.flush(), end="")
                    print()  # final newline
                # Prompt for email after first experiment-related turn
                if not os.environ.get("DISTILLATE_EMAIL") and not os.environ.get("DISTILLATE_EMAIL_ASKED"):
                    if any(t in tool_names_seen for t in (
                        "init_experiment", "launch_experiment", "conclude_run",
                        "manage_session", "scan_project",
                    )):
                        from distillate.cloud_email import prompt_for_email_cli
                        from distillate.state import State
                        prompt_for_email_cli(State())

            elif etype == "session_init":
                # Session ID received — logged for diagnostics
                log.debug("SDK session: %s", event.get("session_id"))

    except KeyboardInterrupt:
        spinner.stop()
        print("\n  (interrupted)")

    return "".join(assistant_text_parts)


def _print_welcome(state: State) -> None:
    """Print a compact welcome banner using rich."""
    processed = state.documents_with_status("processed")
    _q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
    queue = state.documents_with_status(_q_status)
    n_read = len(processed)
    n_queue = len(queue)

    lines = []
    lines.append("[dim]Your research alchemist.[/dim]")

    # Experiments first
    if config.EXPERIMENTS_ENABLED and state.projects:
        n_proj = len(state.projects)
        n_runs = sum(len(p.get("runs", {})) for p in state.projects.values())
        active = sum(
            1 for p in state.projects.values()
            for s in p.get("sessions", {}).values()
            if s.get("status") == "running"
        )
        exp_line = f"{n_proj} experiment{'s' if n_proj != 1 else ''} \u00b7 {n_runs} runs"
        if active:
            exp_line += f" \u00b7 {active} running"
        lines.append(f"\U0001F9EA {exp_line}")

        # Check for new commits in tracked projects
        from distillate.experiments import check_projects_for_updates
        experiment_updates = check_projects_for_updates(state.projects)
        for u in experiment_updates[:3]:
            proj_name = u["project"].get("name", "?")
            slug = u["project"].get("id", proj_name)
            n = u["new_commits"]
            s = "s" if n != 1 else ""
            hint = f'try "scan {slug}"'
            lines.append(f"  [dim]{proj_name} has {n} new commit{s} \u2014 {hint}[/dim]")

    # Papers second
    lines.append(f"\U0001F4DA {n_read} papers read \u00b7 {n_queue} in queue \u00b7 [dim]Type /help or /quit.[/dim]")

    print()
    _console.print(Panel(
        "\n".join(lines),
        title="\u2697\ufe0f  [bold]Nicolas[/bold]",
        border_style="dim",
        padding=(0, 2),
    ))

    # Available commands
    print()
    print(f"  {_dim('/papers')}           {_dim('Browse your library')}")
    print(f"  {_dim('/detail <n>')}       {_dim('View a paper by number')}")
    if config.EXPERIMENTS_ENABLED and state.projects:
        print(f"  {_dim('/experiments')}      {_dim('See all experiments')}")
        print(f"  {_dim('/experiment <n>')}   {_dim('View an experiment')}")
    print(f"  {_dim('/report')}           {_dim('Reading stats')}")
    print(f"  {_dim('/help')}             {_dim('All commands')}")

    # First-use onboarding
    is_first_use = n_read == 0 and not state.projects
    if is_first_use:
        print(f"\n  {_dim('Welcome! Two ways to get started:')}")
        print(f"  {_dim('1.')} Ask me to conjure an experiment {_dim('(works right away)')}")
        print(f"  {_dim('2.')} Run {_bold('/init')} {_dim('to connect your Zotero library')}")
        print()
        return

    # Contextual suggestions
    hints = []
    if config.EXPERIMENTS_ENABLED and state.projects:
        hints.append("How are my experiments?")
    if n_queue > 0:
        hints.append("What's in my queue?")
    if n_read > 0:
        hints.append("Summarize my last read")
    hints.append("What's trending in AI?")
    sep = " \u00b7 "
    print(f"\n  {_dim('Or just ask:')} {_dim(sep.join(hints))}")

    # Rotating tips (one per session)
    import random
    tips = [
        "Tip: /conjure launches an autonomous experiment from a research question.",
        "Tip: /survey scans all experiments for breakthroughs.",
        "Tip: /brew syncs your paper library from Zotero.",
        "Tip: /transmute turns paper insights into experiment ideas.",
        "Tip: /distill extracts key findings from an experiment's history.",
        "Tip: /forage discovers trending papers and reading suggestions.",
        "Tip: /steer lets you redirect a running experiment mid-session.",
        "Tip: /tincture does a deep extraction from a single paper.",
        "Tip: /assay compares experiment runs side-by-side.",
    ]
    print(f"  {_dim(random.choice(tips))}")


def _run_init() -> None:
    """Run the setup wizard inline, then reload config."""
    import importlib

    from distillate.wizard import _init_wizard
    _init_wizard()
    importlib.reload(config)
    print(f"\n  {_dim('Config reloaded. Back to Nicolas.')}\n")


def _print_help() -> None:
    print(
        f"\n  {_bold('Browse')}\n"
        "    /papers             Browse your full paper library\n"
        "    /papers read        Show only papers you've finished reading\n"
        "    /papers unread      Show papers still in your queue\n"
        "    /detail <n>         View a paper's details — summary, highlights, metadata\n"
        "\n"
        f"  {_bold('Experiments')}\n"
        "    /experiments        See all tracked experiments and their status\n"
        "    /experiment <name>  View an experiment — runs, metrics, hypotheses\n"
        "\n"
        f"  {_bold('Insights')}\n"
        "    /report             Your reading stats — velocity, topics, engagement\n"
        "\n"
        f"  {_bold('Session')}\n"
        "    /init               Run the setup wizard\n"
        "    /clear              Clear conversation history\n"
        "    /quit               Exit\n"
        "\n"
        f"  {_bold('Or just ask a question in plain English.')}\n"
        f"    {_dim('What should I read next?')}\n"
        f"    {_dim('Compare my last two ML papers')}\n"
        f"    {_dim('What is trending in AI?')}\n"
    )


# ---------------------------------------------------------------------------
# Slash command renderers — rich tables and panels
# ---------------------------------------------------------------------------

def _show_papers(state: State, cmd: str) -> None:
    """Render papers as a rich table. Supports /papers, /papers read, /papers unread."""
    parts = cmd.strip().split()
    filter_mode = parts[1].lower() if len(parts) > 1 else None

    if filter_mode == "read":
        docs = state.documents_with_status("processed")
        subtitle = "Read"
    elif filter_mode == "unread":
        q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
        docs = state.documents_with_status(q_status)
        subtitle = "Unread"
    else:
        # All papers
        q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
        docs = (
            state.documents_with_status("processed")
            + state.documents_with_status(q_status)
            + state.documents_with_status("processing")
            + state.documents_with_status("awaiting_pdf")
        )
        subtitle = "All"

    if not docs:
        print(f"\n  No papers found ({subtitle.lower()}).\n")
        return

    table = Table(title=f"Papers ({subtitle})", title_style="bold", padding=(0, 1))
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("", width=2)  # status dot
    table.add_column("Title", max_width=50, no_wrap=True)
    table.add_column("Year", width=5, justify="right")
    table.add_column("Cites", width=6, justify="right", style="dim")

    # Sort: unread first (by upload date desc), then read (by processed date desc)
    def _sort_key(d):
        if d.get("status") == "processed":
            return (1, d.get("processed_at", ""))
        return (0, d.get("uploaded_at", ""))

    for doc in sorted(docs, key=_sort_key, reverse=True):
        idx = state.index_of(doc["zotero_item_key"])
        idx_str = str(idx) if idx else ""
        status = doc.get("status", "")
        if status == "processed":
            dot = "[green]\u25cf[/green]"
        elif status in ("on_remarkable", "tracked"):
            dot = "[yellow]\u25cb[/yellow]"
        elif status == "processing":
            dot = "[blue]\u25cb[/blue]"
        else:
            dot = "[dim]\u25cb[/dim]"

        title = doc.get("title", "Untitled")
        if len(title) > 50:
            title = title[:47] + "..."

        meta = doc.get("metadata", {})
        year = ""
        pub_date = meta.get("publication_date", "")
        if pub_date and len(pub_date) >= 4:
            year = pub_date[:4]

        cites = meta.get("citation_count", 0)
        cite_str = f"{cites:,}" if cites else ""

        table.add_row(idx_str, dot, title, year, cite_str)

    print()
    _console.print(table)
    print()


def _show_experiments(state: State) -> None:
    """Render experiments as a rich table."""
    projects = state.projects
    if not projects:
        print("\n  No experiments tracked yet.\n")
        return

    table = Table(title="Experiments", title_style="bold", padding=(0, 1))
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Name", max_width=28, no_wrap=True)
    table.add_column("Status", width=10)
    table.add_column("Runs", width=5, justify="right")
    table.add_column("Best Metric", max_width=22)
    table.add_column("Sessions", width=10)

    for proj_id, proj in projects.items():
        idx = state.project_index_of(proj_id)
        name = proj.get("name", proj_id)
        if len(name) > 28:
            name = name[:25] + "..."
        status = proj.get("status", "tracking")
        runs = proj.get("runs", {})
        run_count = len(runs)

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

        sessions = proj.get("sessions", {})
        active = sum(1 for s in sessions.values() if s.get("status") == "running")
        sess_str = f"[green]{active} active[/green]" if active else "[dim]0 active[/dim]"

        table.add_row(str(idx), name, status, str(run_count), best_metric, sess_str)

    print()
    _console.print(table)
    print()


def _show_detail(state: State, cmd: str) -> None:
    """Render paper detail as a rich panel."""
    parts = cmd.strip().split()
    if len(parts) < 2:
        print("\n  Usage: /detail <number>\n")
        return

    try:
        idx = int(parts[1])
    except ValueError:
        print(f"\n  Invalid paper number: {parts[1]}\n")
        return

    key = state.key_for_index(idx)
    if not key:
        print(f"\n  No paper with index {idx}.\n")
        return

    doc = state.get_document(key)
    if not doc:
        print(f"\n  Paper {idx} not found.\n")
        return

    title = doc.get("title", "Untitled")
    authors = doc.get("authors", [])
    meta = doc.get("metadata", {})
    status = doc.get("status", "unknown")
    summary = doc.get("summary", "")

    lines = []
    lines.append(f"[bold]{title}[/bold]")
    if authors:
        lines.append(f"[dim]{', '.join(authors[:5])}{'...' if len(authors) > 5 else ''}[/dim]")
    lines.append("")

    # Metadata row
    meta_parts = []
    pub_date = meta.get("publication_date", "")
    if pub_date:
        meta_parts.append(pub_date[:10])
    venue = meta.get("venue", "") or meta.get("publication_venue", "")
    if venue:
        meta_parts.append(venue)
    cites = meta.get("citation_count", 0)
    if cites:
        meta_parts.append(f"{cites:,} citations")
    pages = doc.get("page_count", 0)
    if pages:
        meta_parts.append(f"{pages} pages")
    engagement = doc.get("engagement", 0)
    if engagement:
        meta_parts.append(f"{engagement}% engagement")
    if meta_parts:
        lines.append("[dim]" + " \u00b7 ".join(meta_parts) + "[/dim]")
        lines.append("")

    # Status
    status_display = {
        "processed": "[green]Read[/green]",
        "on_remarkable": "[yellow]On reMarkable[/yellow]",
        "tracked": "[yellow]In Queue[/yellow]",
        "processing": "[blue]Processing[/blue]",
        "awaiting_pdf": "[red]Awaiting PDF[/red]",
    }
    lines.append(f"Status: {status_display.get(status, status)}")

    # Summary
    if summary:
        lines.append("")
        lines.append("[bold]Summary[/bold]")
        lines.append(summary[:500])

    # Highlights
    word_count = doc.get("highlight_word_count", 0)
    highlight_count = doc.get("highlight_count", 0)
    if word_count or highlight_count:
        lines.append("")
        lines.append(f"[bold]Highlights[/bold]: {highlight_count} passages, {word_count:,} words")

    # Tags
    tags = meta.get("tags", [])
    if tags:
        lines.append("")
        lines.append("[dim]Tags: " + ", ".join(tags) + "[/dim]")

    print()
    _console.print(Panel(
        "\n".join(lines),
        title=f"[dim]#{idx}[/dim]",
        border_style="dim",
        padding=(1, 2),
    ))
    print()


def _show_experiment_detail(state: State, cmd: str) -> None:
    """Render experiment detail as a rich panel."""
    # Parse: "/experiment <name or index>"
    query = cmd.strip().split(maxsplit=1)
    if len(query) < 2:
        print("\n  Usage: /experiment <name or index>\n")
        return

    proj = state.find_project(query[1])
    if not proj:
        print(f"\n  No experiment found matching '{query[1]}'.\n")
        return

    name = proj.get("name", proj.get("id", "?"))
    idx = state.project_index_of(proj.get("id", ""))
    runs = proj.get("runs", {})
    status = proj.get("status", "tracking")
    description = proj.get("description", "")
    path = proj.get("path", "")
    tags = proj.get("tags", [])
    goals = proj.get("goals", [])

    lines = []
    lines.append(f"[bold]{name}[/bold]")
    if description:
        lines.append(f"[dim]{description}[/dim]")
    lines.append("")

    meta_parts = [f"Status: {status}", f"{len(runs)} runs"]
    if path:
        meta_parts.append(f"[dim]{path}[/dim]")
    lines.append(" \u00b7 ".join(meta_parts))

    if tags:
        lines.append("[dim]Tags: " + ", ".join(tags) + "[/dim]")

    if goals:
        lines.append("")
        lines.append("[bold]Goals[/bold]")
        for g in goals[:5]:
            text = g if isinstance(g, str) else g.get("description", str(g))
            lines.append(f"  \u2022 {text}")

    # Runs table
    if runs:
        lines.append("")
        lines.append("[bold]Runs[/bold]")
        # Show last 10 runs sorted by date
        sorted_runs = sorted(
            runs.values(),
            key=lambda r: r.get("started_at", ""),
            reverse=True,
        )
        for run in sorted_runs[:10]:
            run_name = run.get("name", run.get("id", "?"))
            run_status = run.get("status", "")
            hypothesis = run.get("hypothesis", "")
            results = run.get("results", {})

            # Format result metrics
            metric_str = ""
            for k in ("accuracy", "exact_match", "test_accuracy", "loss", "f1", "val_bpb"):
                if k in results:
                    v = results[k]
                    metric_str = f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                    break

            parts = [run_name]
            if run_status:
                parts.append(run_status)
            if metric_str:
                parts.append(metric_str)
            if hypothesis:
                h = hypothesis[:40] + "..." if len(hypothesis) > 40 else hypothesis
                parts.append(f'"{h}"')

            lines.append(f"  \u2022 {' \u00b7 '.join(parts)}")

        if len(runs) > 10:
            lines.append(f"  [dim]... and {len(runs) - 10} more[/dim]")

    print()
    _console.print(Panel(
        "\n".join(lines),
        title=f"[dim]#{idx}[/dim]" if idx else None,
        border_style="dim",
        padding=(1, 2),
    ))
    print()


def _show_report(state: State) -> None:
    """Render reading stats as a rich panel."""
    processed = state.documents_with_status("processed")
    if not processed:
        print("\n  No processed papers yet. Read some papers first!\n")
        return

    total_papers = len(processed)
    total_pages = sum(d.get("page_count", 0) for d in processed)
    total_words = sum(d.get("highlight_word_count", 0) for d in processed)
    engagements = [d.get("engagement", 0) for d in processed if d.get("engagement")]
    avg_engagement = round(sum(engagements) / len(engagements)) if engagements else 0

    lines = []
    lines.append("[bold]Lifetime[/bold]")
    lines.append(f"  {total_papers} papers \u00b7 {total_pages:,} pages \u00b7 {total_words:,} words highlighted")
    lines.append(f"  Avg engagement: {avg_engagement}%")

    # Reading velocity (last 8 weeks)
    now = datetime.now(timezone.utc)
    week_counts: Counter = Counter()
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
                week_counts[label] += 1
        except (ValueError, TypeError):
            pass

    if week_counts:
        lines.append("")
        lines.append("[bold]Reading Velocity[/bold] (last 8 weeks)")
        max_count = max(week_counts.values())
        for label in list(week_counts.keys())[::-1][:8]:
            count = week_counts[label]
            bar_len = round(count / max(max_count, 1) * 20)
            bar = "\u2588" * bar_len
            lines.append(f"  {label}  {bar} {count}")

    # Top topics
    topic_counter: Counter = Counter()
    for doc in processed:
        tags = doc.get("metadata", {}).get("tags") or []
        for tag in tags:
            topic_counter[tag] += 1

    if topic_counter:
        lines.append("")
        lines.append("[bold]Top Topics[/bold]")
        for topic, count in topic_counter.most_common(5):
            display = topic[:30] if len(topic) > 30 else topic
            lines.append(f"  {display:<32} {count} papers")

    # Engagement distribution
    buckets = {"0-25%": 0, "25-50%": 0, "50-75%": 0, "75-100%": 0}
    for doc in processed:
        eng = doc.get("engagement", 0)
        if eng <= 25:
            buckets["0-25%"] += 1
        elif eng <= 50:
            buckets["25-50%"] += 1
        elif eng <= 75:
            buckets["50-75%"] += 1
        else:
            buckets["75-100%"] += 1

    max_bucket = max(buckets.values()) if buckets else 1
    lines.append("")
    lines.append("[bold]Engagement Distribution[/bold]")
    for label, count in buckets.items():
        bar_len = round(count / max(max_bucket, 1) * 20)
        bar = "\u2588" * bar_len
        lines.append(f"  {label:<8} {bar} {count}")

    # Most-cited papers
    cited = sorted(
        [d for d in processed if d.get("metadata", {}).get("citation_count", 0) > 0],
        key=lambda d: d.get("metadata", {}).get("citation_count", 0),
        reverse=True,
    )
    if cited:
        lines.append("")
        lines.append("[bold]Most-Cited Papers Read[/bold]")
        for doc in cited[:5]:
            idx = state.index_of(doc["zotero_item_key"])
            cites = doc["metadata"]["citation_count"]
            short = doc["title"][:45]
            if len(doc["title"]) > 45:
                short += "..."
            lines.append(f"  [dim]\\[{idx}][/dim] {short} ({cites:,} citations)")

    print()
    _console.print(Panel(
        "\n".join(lines),
        title="[bold]Reading Report[/bold]",
        border_style="dim",
        padding=(1, 2),
    ))
    print()
