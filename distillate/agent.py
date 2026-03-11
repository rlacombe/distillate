"""Interactive agent REPL for Distillate.

Provides a conversational interface to the paper library using Claude
with tool use. Launched via ``distillate`` (in a TTY) or
``distillate "question"`` for single-turn mode.

Terminal rendering (spinners, ANSI) lives here.  Core conversation logic
lives in :mod:`distillate.agent_core`.
"""

import json
import logging
import os
import random
import sys
import threading

from datetime import datetime, timezone
from typing import List, Optional

from distillate import config
from distillate.agent_core import (
    CONVERSATION_KEEP,
    CONVERSATION_TRIM_THRESHOLD,
    VERBOSE_TOOLS,
    build_system_prompt as _build_system_prompt,  # noqa: F401 (re-export for tests)
    create_client,
    execute_tool as _execute_tool,  # noqa: F401 (re-export for tests)
    stream_turn,
    tool_label as _tool_label,
)
from distillate.state import State

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation log — persists across sessions
# ---------------------------------------------------------------------------

_CONVERSATION_LOG_PATH = config.CONFIG_DIR / "conversations.json"
_MAX_SESSIONS = 50

# Re-export constants so existing tests that import from agent still work
_CONVERSATION_TRIM_THRESHOLD = CONVERSATION_TRIM_THRESHOLD
_CONVERSATION_KEEP = CONVERSATION_KEEP


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
    """Entry point for interactive chat mode."""
    client = create_client()
    if client is None:
        print(
            "\n  Agent mode requires an Anthropic API key.\n"
            "  Set ANTHROPIC_API_KEY in your .env file or run "
            "'distillate --init'.\n"
            "  To sync papers without AI, use: distillate --sync\n"
        )
        sys.exit(1)

    state = State()
    conversation: list[dict] = []

    # Load conversation history for cross-session memory
    all_sessions = _load_conversation_log()
    current_session: dict = {
        "session_id": datetime.now(timezone.utc).isoformat(),
        "messages": [],
    }

    # Single-turn mode: answer one question and exit
    if initial_args:
        query = " ".join(initial_args)
        _render_turn(
            client, state, conversation, query,
            past_sessions=all_sessions, stream=False,
        )
        return

    # Interactive REPL — clear screen for full-screen feel
    if _is_tty():
        print("\033[2J\033[H", end="", flush=True)
    experiment_updates = _print_welcome(state)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower().rstrip(".!") in ("exit", "quit", "/quit", "/exit", "/q"):
            print("\n  \u2697\ufe0f  See you next time!\n")
            break
        if user_input.lower() in ("/clear",):
            conversation.clear()
            print("  Conversation cleared.")
            continue
        if user_input.lower() in ("/help",):
            _print_help()
            continue
        if user_input.lower() in ("/init",):
            _run_init()
            state.reload()
            continue

        _render_turn(
            client, state, conversation, user_input,
            past_sessions=all_sessions, stream=True,
            experiment_updates=experiment_updates,
        )

        # Log this exchange
        current_session["messages"].append({"role": "user", "content": user_input})
        # Extract assistant text from the last assistant message
        for msg in reversed(conversation):
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                texts = [b.text for b in content if hasattr(b, "text")]
                if texts:
                    current_session["messages"].append(
                        {"role": "assistant", "content": " ".join(texts)[:200]}
                    )
                break

    # Save session on exit (only if there were messages)
    if current_session["messages"]:
        all_sessions.append(current_session)
        _save_conversation_log(all_sessions)


def _term_width() -> int:
    """Return terminal width, defaulting to 60."""
    try:
        return os.get_terminal_size().columns
    except (ValueError, OSError):
        return 60


def _print_welcome(state: State) -> list[dict]:
    """Print a compact welcome banner.

    Returns experiment update dicts (for later use in the system prompt).
    """
    processed = state.documents_with_status("processed")
    _q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
    queue = state.documents_with_status(_q_status)
    n_read = len(processed)
    n_queue = len(queue)

    w = min(_term_width(), 64)
    dashes = _dim("\u2500\u2500\u2500")
    header_prefix = f"  {dashes} \u2697\ufe0f  {_bold('Nicolas')} "
    header_tail = _dim("\u2500" * max(0, w - 19))
    footer = _dim("  " + "\u2500" * (w - 2))

    print()
    print(header_prefix + header_tail)
    print(f"  {_dim('Your research command center.')}")

    experiment_updates: list[dict] = []

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
        print(f"  \U0001F9EA {exp_line}")

        # Check for new commits in tracked projects
        from distillate.experiments import check_projects_for_updates
        experiment_updates = check_projects_for_updates(state.projects)
        for u in experiment_updates[:3]:
            proj_name = u["project"].get("name", "?")
            slug = u["project"].get("id", proj_name)
            n = u["new_commits"]
            s = "s" if n != 1 else ""
            hint = f'try "scan {slug}"'
            line = f"  \u21b3 {proj_name} has {n} new commit{s} \u2014 {hint}"
            print(f"  {_dim(line)}")

    # Papers second
    print(f"  \U0001F4DA {n_read} papers read \u00b7 {n_queue} in queue \u00b7 {_dim('Type /help or /quit.')}")

    print(footer)

    # Contextual suggestions — experiments first
    hints = []
    if config.EXPERIMENTS_ENABLED and state.projects:
        hints.append("How are my experiments?")
    if n_queue > 0:
        hints.append("What's in my queue?")
    if n_read > 0:
        hints.append("Summarize my last read")
    hints.append("What's trending in AI?")
    sep = " \u00b7 "
    print(f"\n  {_dim('Try:')} {_dim(sep.join(hints))}")

    return experiment_updates


def _run_init() -> None:
    """Run the setup wizard inline, then reload config."""
    import importlib

    from distillate.wizard import _init_wizard
    _init_wizard()
    importlib.reload(config)
    print(f"\n  {_dim('Config reloaded. Back to Nicolas.')}\n")


def _print_help() -> None:
    print(
        f"\n  {_bold('Commands')}\n"
        "    /init    Run the setup wizard\n"
        "    /clear   Clear conversation history\n"
        "    /quit    Exit the agent\n"
        "    /help    Show this help\n"
        "\n"
        f"  {_bold('Try asking')}\n"
        f"    {_dim('What is in my queue?')}\n"
        f"    {_dim('Tell me about paper 42')}\n"
        f"    {_dim('Compare my last two ML papers')}\n"
        f"    {_dim('What should I read next?')}\n"
        f"    {_dim('How many papers have I read this month?')}\n"
    )


# ---------------------------------------------------------------------------
# Turn rendering — consumes events from agent_core.stream_turn
# ---------------------------------------------------------------------------

def _render_turn(
    client,
    state: State,
    conversation: list[dict],
    user_input: str,
    past_sessions: list[dict] | None = None,
    stream: bool = True,
    experiment_updates: list[dict] | None = None,
) -> None:
    """Handle one user turn by consuming agent_core events."""
    fmt = _StreamFormatter()
    spinner = _ThinkingSpinner()
    first_token = True
    has_text = False

    # Blank line before response
    if stream:
        print()

    spinner.start()

    try:
        for event in stream_turn(
            client, state, conversation, user_input,
            past_sessions=past_sessions,
            experiment_updates=experiment_updates,
        ):
            etype = event["type"]

            if etype == "text_delta":
                if first_token:
                    spinner.stop()
                    first_token = False
                    has_text = True
                if stream:
                    print(fmt.feed(event["text"]), end="", flush=True)
                else:
                    print(fmt.feed(event["text"]), end="")

            elif etype == "tool_start":
                # Stop the thinking spinner before tool execution
                if first_token:
                    spinner.stop()
                    first_token = False
                else:
                    spinner.stop()

                # If text was streamed before this tool, add spacing
                if has_text:
                    print(fmt.flush(), end="")
                    print()  # newline after streamed text
                    print()  # blank line before tool spinner

                # Start a tool-specific spinner
                spinner = _ThinkingSpinner(_tool_label(event["name"]))

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
                if event.get("name") in VERBOSE_TOOLS and _is_tty():
                    # Restore stdout after verbose tool
                    sys.stdout.write = sys.__stdout__.write
                    print()  # blank line after verbose output
                spinner.stop()
                has_text = False

                # Start a new thinking spinner for the next API call
                spinner = _ThinkingSpinner()
                spinner.start()
                first_token = True

            elif etype == "turn_end":
                spinner.stop()
                if has_text:
                    print(fmt.flush(), end="")
                    print()  # final newline

            elif etype == "error":
                spinner.stop()
                cat = event.get("category", "unknown")
                if cat == "credits_depleted":
                    print("\n  Anthropic API credits depleted.")
                    print("  Add credits at https://console.anthropic.com/settings/billing")
                elif cat == "invalid_key":
                    print("\n  Invalid Anthropic API key. Run /init to update it.")
                elif cat == "overloaded":
                    print("\n  Anthropic API is overloaded. Try again in a moment.")
                elif cat == "rate_limited":
                    print("\n  Rate limited. Wait a moment and try again.")
                else:
                    print("\n  Something went wrong. Try again.")

    except KeyboardInterrupt:
        spinner.stop()
        print("\n  (interrupted)")
