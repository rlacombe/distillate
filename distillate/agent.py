"""Interactive agent REPL for Distillate.

Provides a conversational interface to the paper library using Claude
with tool use. Launched via ``distillate`` (in a TTY) or
``distillate "question"`` for single-turn mode.
"""

import json
import logging
import os
import random
import sys
import threading

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from distillate import config
from distillate.state import State
from distillate.tools import TOOL_SCHEMAS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation log — persists across sessions
# ---------------------------------------------------------------------------

_CONVERSATION_LOG_PATH = config.CONFIG_DIR / "conversations.json"
_MAX_SESSIONS = 50
_PROMPT_SESSIONS = 3  # how many past sessions to include in system prompt


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


def _format_past_sessions(sessions: list[dict]) -> str:
    """Format recent sessions for inclusion in the system prompt."""
    if not sessions:
        return ""

    now = datetime.now(timezone.utc)
    lines = []
    for s in sessions[-_PROMPT_SESSIONS:]:
        queries = [m["content"] for m in s.get("messages", []) if m["role"] == "user"]
        if not queries:
            continue
        # Human-readable relative date
        try:
            ts = datetime.fromisoformat(s["session_id"])
            delta = (now - ts).days
            if delta == 0:
                when = "Today"
            elif delta == 1:
                when = "Yesterday"
            else:
                when = f"{delta} days ago"
        except (ValueError, KeyError):
            when = "Earlier"
        quoted = ", ".join(f'"{q[:60]}"' for q in queries[:5])
        lines.append(f"- {when}: {quoted}")

    if not lines:
        return ""
    return "## Recent Conversations\n" + "\n".join(lines) + "\n\n"


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


_TOOL_LABELS = {
    "search_papers": "\U0001F50D Searching the library",
    "get_paper_details": "\U0001F4DC Unrolling the manuscript",
    "get_reading_stats": "\U0001F4CA Tallying the ledger",
    "get_queue": "\u2697\ufe0f Inspecting the queue",
    "get_recent_reads": "\U0001F4DA Reviewing recent reads",
    "suggest_next_reads": "\U0001F52E Consulting the oracle",
    "synthesize_across_papers": "\u2728 Cross-referencing texts",
    "run_sync": "\U0001F525 Firing up the furnace",
    "reprocess_paper": "\U0001F9EA Re-extracting the essence",
    "promote_papers": "\u2B50 Promoting to the shelf",
    "get_trending_papers": "\U0001F4C8 Scanning the latest papers",
    "add_paper_to_zotero": "\U0001F4D6 Adding to the library",
}


def _tool_label(name: str) -> str:
    return _TOOL_LABELS.get(name, name.replace("_", " ").title())


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


# Use the configured agent model (Haiku by default — fast + cheap for REPL)
_AGENT_MODEL = None  # resolved lazily after config is loaded

_MAX_TOOL_STEPS = 5
_MAX_TOKENS = 1024
_CONVERSATION_TRIM_THRESHOLD = 20
_CONVERSATION_KEEP = 10
_MAX_TOOL_RESULT_CHARS = 4000  # truncate large tool responses


def _get_model() -> str:
    global _AGENT_MODEL
    if _AGENT_MODEL is None:
        _AGENT_MODEL = config.CLAUDE_AGENT_MODEL
    return _AGENT_MODEL


def _truncate_result(result: dict, max_chars: int) -> dict:
    """Truncate a tool result dict so its JSON stays under max_chars.

    Walks top-level string values and list values, shortening them
    until the serialized size fits. Produces valid JSON unlike naive slicing.
    """
    import json as _json
    if len(_json.dumps(result)) <= max_chars:
        return result

    out = dict(result)
    # First pass: truncate long string values
    for key, val in out.items():
        if isinstance(val, str) and len(val) > 500:
            out[key] = val[:500] + "... (truncated)"
        elif isinstance(val, list) and len(val) > 10:
            out[key] = val[:10] + ["... (truncated)"]
    # Second pass: if still too large, drop the biggest fields
    while len(_json.dumps(out)) > max_chars and out:
        biggest = max(out, key=lambda k: len(_json.dumps(out[k])))
        out[biggest] = "(truncated)"
    return out


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system_prompt(state: State, past_sessions: list[dict] | None = None) -> str:
    """Build a context-rich system prompt from current library state."""
    now = datetime.now(timezone.utc)

    queue = state.documents_with_status("on_remarkable")
    processed = state.documents_with_status("processed")
    awaiting = state.documents_with_status("awaiting_pdf")

    week_ago = (now - timedelta(days=7)).isoformat()
    recent = state.documents_processed_since(week_ago)

    # Recent reads (last 5)
    recent_lines = []
    for doc in list(reversed(recent))[:5]:
        eng = doc.get("engagement", 0)
        hl = doc.get("highlight_count", 0)
        recent_lines.append(
            f"- {doc.get('title', '?')} ({eng}% engaged, {hl} highlights)"
        )

    # Top tags from last 30 days
    month_ago = (now - timedelta(days=30)).isoformat()
    month_papers = state.documents_processed_since(month_ago)
    tag_counts: dict[str, int] = {}
    for doc in month_papers:
        for tag in doc.get("metadata", {}).get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = sorted(tag_counts, key=tag_counts.get, reverse=True)[:8]

    recent_section = "\n".join(recent_lines) if recent_lines else "(none this week)"
    tags_section = ", ".join(top_tags) if top_tags else "(not enough data yet)"

    return (
        "You are Nicolas, a research alchemist \u2014 named after Nicolas "
        "Flamel, the legendary alchemist. You help a researcher distill "
        "the essence from academic papers. The user reads papers through a "
        "Zotero \u2192 reMarkable \u2192 Obsidian workflow powered by "
        "Distillate. You have tools to search their library, read their "
        "highlights and notes, analyze reading patterns, and synthesize "
        "insights across papers.\n\n"
        "## Library\n"
        f"- {len(processed)} papers read, {len(queue)} in queue"
        f", {len(awaiting)} awaiting PDF\n"
        f"- This week: {len(recent)} papers read\n\n"
        "## Recent Reads\n"
        f"{recent_section}\n\n"
        "## Research Interests\n"
        f"{tags_section}\n\n"
        f"{_format_past_sessions(past_sessions or [])}"
        "## Personality\n"
        "You're warm, witty, and genuinely curious about the user's research. "
        "Think of yourself as a fellow scholar who happens to live in an "
        "alchemist's workshop \u2014 you might say a paper's findings are "
        "\"pure gold\" or that you'll \"distill the key insights.\" Keep the "
        "alchemy flavor light and natural, not forced. Show enthusiasm when "
        "a paper is interesting. Be opinionated \u2014 if a result is "
        "surprising or a method is clever, say so.\n\n"
        "## Guidelines\n"
        "- Look up papers with tools before answering \u2014 don't guess "
        "from memory. When the user asks about recent papers, their queue, "
        "or what they added recently, call get_queue \u2014 it's sorted "
        "newest-first with upload timestamps.\n"
        "- Show paper [index] numbers for easy reference.\n"
        "- **Bold paper titles** with markdown **title** for readability.\n"
        "- You may sprinkle one or two chemistry/alchemy emojis "
        "(\u2697\ufe0f \U0001F9EA \U0001F52C \u2728 \U0001F4DC) inline in a response "
        "\u2014 but NEVER start a message with an emoji. Keep them subtle.\n"
        "- If the user says they already added papers to Zotero and need PDFs "
        "loaded, call run_sync \u2014 it picks up new Zotero items and "
        "downloads their PDFs. Use add_paper_to_zotero only when the paper "
        "isn't in Zotero yet.\n"
        "- add_paper_to_zotero works with just an arXiv ID or URL \u2014 it "
        "auto-fetches the title, authors, and abstract. Don't ask the user "
        "for metadata you can look up.\n"
        "- Confirm with the user before write operations (sync, reprocess, "
        "promote).\n"
        "- Keep responses concise \u2014 this is a terminal REPL.\n"
        "- End with a statement, not a question. Don't ask \"Want to know more?\" "
        "or \"Shall I look into X?\" \u2014 just deliver the answer. The user "
        "will ask if they want more.\n"
        "- When asked to compare or synthesize, use synthesize_across_papers.\n"
    )


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

def run_chat(initial_args: Optional[List[str]] = None) -> None:
    """Entry point for interactive chat mode."""
    if not config.ANTHROPIC_API_KEY:
        print(
            "\n  Agent mode requires an Anthropic API key.\n"
            "  Set ANTHROPIC_API_KEY in your .env file or run "
            "'distillate --init'.\n"
            "  To sync papers without AI, use: distillate --sync\n"
        )
        sys.exit(1)

    try:
        import anthropic
    except ImportError:
        print(
            "\n  Agent mode requires the 'anthropic' package.\n"
            "  Install it with: pip install distillate\n"
        )
        sys.exit(1)

    state = State()
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
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
        _handle_turn(
            client, state, conversation, query,
            stream=False, past_sessions=all_sessions,
        )
        return

    # Interactive REPL — clear screen for full-screen feel
    if _is_tty():
        print("\033[2J\033[H", end="", flush=True)
    _print_welcome(state)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower().rstrip(".!") in ("exit", "quit", "/quit", "/exit", "/q"):
            print("  \u2697\ufe0f See you next time!")
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

        _handle_turn(
            client, state, conversation, user_input,
            stream=True, past_sessions=all_sessions,
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


def _print_welcome(state: State) -> None:
    """Print a compact welcome banner."""
    processed = state.documents_with_status("processed")
    queue = state.documents_with_status("on_remarkable")
    n_read = len(processed)
    n_queue = len(queue)

    w = min(_term_width(), 64)
    # "─── ⚗️  Nicolas " = 17 visible chars (emoji is 2 wide)
    dashes = _dim("\u2500\u2500\u2500")
    header_prefix = f"  {dashes} \u2697\ufe0f  {_bold('Nicolas')} "
    header_tail = _dim("\u2500" * max(0, w - 19))
    footer = _dim("  " + "\u2500" * (w - 2))

    print()
    print(header_prefix + header_tail)
    print(f"  \U0001F4DA {n_read} papers read \u00b7 {n_queue} in queue")
    print(f"  {_dim('Your research alchemist. Type /help or /quit.')}")
    print(footer)

    # Contextual suggestions
    hints = []
    if n_queue > 0:
        hints.append("What's in my queue?")
    if n_read > 0:
        hints.append("Summarize my last read")
    hints.append("What's trending in AI?")
    sep = " \u00b7 "
    print(f"\n  {_dim('Try:')} {_dim(sep.join(hints))}")


def _run_init() -> None:
    """Run the setup wizard inline, then reload config."""
    import importlib

    from distillate.main import _init_wizard
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
# Turn handling with multi-step tool use
# ---------------------------------------------------------------------------

def _handle_turn(
    client,
    state: State,
    conversation: list[dict],
    user_input: str,
    stream: bool = True,
    past_sessions: list[dict] | None = None,
) -> None:
    """Handle one user turn, including multi-step tool use."""
    conversation.append({"role": "user", "content": user_input})

    # Refresh state from disk (picks up changes from concurrent sync)
    state.reload()

    system_prompt = _build_system_prompt(state, past_sessions=past_sessions)
    tools = TOOL_SCHEMAS

    # One blank line after the prompt — all spinners reuse this line
    if stream:
        print()

    for _step in range(_MAX_TOOL_STEPS):
        try:
            if stream:
                response = _stream_response(
                    client, system_prompt, conversation, tools,
                )
            else:
                response = client.messages.create(
                    model=_get_model(),
                    max_tokens=_MAX_TOKENS,
                    system=system_prompt,
                    messages=conversation,
                    tools=tools,
                )
                # Print text blocks for single-turn mode
                fmt = _StreamFormatter()
                for block in response.content:
                    if hasattr(block, "text"):
                        print(fmt.feed(block.text), end="")
                print(fmt.flush())
        except KeyboardInterrupt:
            print("\n  (interrupted)")
            return
        except Exception as exc:
            log.exception("Agent API call failed")
            msg = str(exc)
            if "credit balance is too low" in msg:
                print("\n  Anthropic API credits depleted.")
                print("  Add credits at https://console.anthropic.com/settings/billing")
            elif "authentication_error" in msg or "invalid x-api-key" in msg.lower():
                print("\n  Invalid Anthropic API key. Run /init to update it.")
            elif "overloaded" in msg:
                print("\n  Anthropic API is overloaded. Try again in a moment.")
            elif "rate_limit" in msg:
                print("\n  Rate limited. Wait a moment and try again.")
            else:
                print("\n  Something went wrong. Try again.")
            return

        # Append assistant response to conversation
        conversation.append({"role": "assistant", "content": response.content})

        # Check for tool use
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break  # Pure text response, done

        # If text was streamed before tool use, add a blank line so the
        # tool spinner doesn't sit right against the previous text.
        has_text = any(
            hasattr(b, "text") for b in response.content if b.type == "text"
        )
        if stream and has_text:
            print()

        # Execute tools with spinner
        _VERBOSE_TOOLS = {"run_sync", "reprocess_paper", "promote_papers", "add_paper_to_zotero"}
        tool_results = []
        for tool_use in tool_uses:
            spinner = _ThinkingSpinner(_tool_label(tool_use.name))
            verbose = tool_use.name in _VERBOSE_TOOLS
            spinner.start()
            try:
                if verbose:
                    # These tools print progress — freeze spinner label first
                    spinner.stop(keep_label=True)
                result = _execute_tool(tool_use.name, tool_use.input, state)
            finally:
                spinner.stop()  # idempotent — clears line if not already stopped
            result_json = json.dumps(result)
            if len(result_json) > _MAX_TOOL_RESULT_CHARS:
                result_json = json.dumps(_truncate_result(result, _MAX_TOOL_RESULT_CHARS))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_json,
            })
        conversation.append({"role": "user", "content": tool_results})

    # Trim conversation to prevent context overflow
    if len(conversation) > _CONVERSATION_TRIM_THRESHOLD:
        trimmed = conversation[-_CONVERSATION_KEEP:]
        # Ensure conversation starts with a user message (not an orphaned
        # tool_result left over from a mid-sequence trim).
        while trimmed and trimmed[0].get("role") == "assistant":
            trimmed.pop(0)
        conversation[:] = trimmed


def _stream_response(client, system_prompt, conversation, tools):
    """Stream response text to terminal, return complete response."""
    fmt = _StreamFormatter()
    spinner = _ThinkingSpinner()
    spinner.start()
    first_token = True

    with client.messages.stream(
        model=_get_model(),
        max_tokens=_MAX_TOKENS,
        system=system_prompt,
        messages=conversation,
        tools=tools,
    ) as stream:
        for event in stream:
            if hasattr(event, "type") and event.type == "content_block_delta":
                if hasattr(event.delta, "text"):
                    if first_token:
                        spinner.stop()
                        first_token = False
                    print(fmt.feed(event.delta.text), end="", flush=True)
        if first_token:
            spinner.stop()  # tool-only response, no text
        else:
            print(fmt.flush(), end="")
            print()  # newline after streamed text
        return stream.get_final_message()


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def _execute_tool(name: str, input_data: dict, state: State) -> dict:
    """Execute a tool and return the result dict."""
    from distillate import tools

    dispatch = {
        "search_papers": tools.search_papers,
        "get_paper_details": tools.get_paper_details,
        "get_reading_stats": tools.get_reading_stats,
        "get_queue": tools.get_queue,
        "get_recent_reads": tools.get_recent_reads,
        "suggest_next_reads": tools.suggest_next_reads,
        "synthesize_across_papers": tools.synthesize_across_papers,
        "run_sync": tools.run_sync,
        "reprocess_paper": tools.reprocess_paper,
        "promote_papers": tools.promote_papers,
        "get_trending_papers": tools.get_trending_papers,
        "add_paper_to_zotero": tools.add_paper_to_zotero,
    }

    fn = dispatch.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}

    try:
        return fn(state=state, **input_data)
    except Exception as e:
        log.exception("Tool '%s' failed", name)
        return {"error": str(e)}
