"""Interactive agent REPL for Distillate.

Provides a conversational interface to the paper library using Claude
with tool use. Launched via ``distillate`` (in a TTY) or
``distillate "question"`` for single-turn mode.
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from distillate import config
from distillate.state import State
from distillate.tools import TOOL_SCHEMAS

log = logging.getLogger(__name__)

# Use the configured smart model (Sonnet by default)
_AGENT_MODEL = None  # resolved lazily after config is loaded

_MAX_TOOL_STEPS = 5
_MAX_TOKENS = 2048
_CONVERSATION_TRIM_THRESHOLD = 40
_CONVERSATION_KEEP = 20


def _get_model() -> str:
    global _AGENT_MODEL
    if _AGENT_MODEL is None:
        _AGENT_MODEL = config.CLAUDE_SMART_MODEL
    return _AGENT_MODEL


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def _build_system_prompt(state: State) -> str:
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
        "You are Distillate, a research paper assistant. The user reads "
        "academic papers through a Zotero \u2192 reMarkable \u2192 Obsidian "
        "workflow. You have tools to search their library, read their "
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
        "## Guidelines\n"
        "- Look up papers with tools before answering \u2014 don't guess "
        "from memory.\n"
        "- Show paper [index] numbers for easy reference.\n"
        "- Confirm with the user before write operations (sync, reprocess).\n"
        "- Keep responses concise \u2014 this is a terminal REPL.\n"
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
            "  Install it with: pip install distillate[ai]\n"
        )
        sys.exit(1)

    state = State()
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    conversation: list[dict] = []

    # Single-turn mode: answer one question and exit
    if initial_args:
        query = " ".join(initial_args)
        _handle_turn(client, state, conversation, query, stream=False)
        return

    # Interactive REPL
    _print_welcome(state)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/quit", "/exit"):
            break
        if user_input.lower() in ("/clear",):
            conversation.clear()
            print("  Conversation cleared.")
            continue
        if user_input.lower() in ("/help",):
            _print_help()
            continue

        _handle_turn(client, state, conversation, user_input, stream=True)


def _print_welcome(state: State) -> None:
    """Print a compact welcome banner."""
    processed = state.documents_with_status("processed")
    queue = state.documents_with_status("on_remarkable")
    print()
    print(f"  Distillate \u00b7 {len(processed)} papers read, {len(queue)} in queue")
    print("  Ask anything about your papers, or type /quit to exit.")


def _print_help() -> None:
    print(
        "\n  Commands:\n"
        "    /clear   Clear conversation history\n"
        "    /quit    Exit the agent\n"
        "    /help    Show this help\n"
        "\n"
        "  You can ask things like:\n"
        '    "What\'s in my queue?"\n'
        '    "Tell me about paper 42"\n'
        '    "Compare my last two ML papers"\n'
        '    "What should I read next?"\n'
        '    "How many papers have I read this month?"\n'
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
) -> None:
    """Handle one user turn, including multi-step tool use."""
    conversation.append({"role": "user", "content": user_input})

    # Refresh state from disk (picks up changes from concurrent sync)
    state.reload()

    system_prompt = _build_system_prompt(state)
    tools = TOOL_SCHEMAS

    for _step in range(_MAX_TOOL_STEPS):
        try:
            if stream:
                response = _stream_response(
                    client, system_prompt, conversation, tools
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
                for block in response.content:
                    if hasattr(block, "text"):
                        print(block.text)
        except KeyboardInterrupt:
            print("\n  (interrupted)")
            return
        except Exception:
            log.exception("Agent API call failed")
            print("\n  Something went wrong. Try again.")
            return

        # Append assistant response to conversation
        conversation.append({"role": "assistant", "content": response.content})

        # Check for tool use
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break  # Pure text response, done

        # Execute tools and append results
        tool_results = []
        for tool_use in tool_uses:
            result = _execute_tool(tool_use.name, tool_use.input, state)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": json.dumps(result),
            })
        conversation.append({"role": "user", "content": tool_results})

    # Trim conversation to prevent context overflow
    if len(conversation) > _CONVERSATION_TRIM_THRESHOLD:
        conversation[:] = conversation[-_CONVERSATION_KEEP:]


def _stream_response(client, system_prompt, conversation, tools):
    """Stream response text to terminal, return complete response."""
    print()  # blank line before response

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
                    print(event.delta.text, end="", flush=True)
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
    }

    fn = dispatch.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}

    try:
        return fn(state=state, **input_data)
    except Exception as e:
        log.exception("Tool '%s' failed", name)
        return {"error": str(e)}
