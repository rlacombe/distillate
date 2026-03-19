"""Nicolas agent powered by Claude Agent SDK.

Replaces agent_core.py's custom Claude API loop with the Agent SDK,
giving Nicolas all of Claude Code's capabilities plus Distillate tools
via MCP server.
"""

import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

from distillate import config
from distillate.agent_core import TOOL_LABELS, VERBOSE_TOOLS
from distillate.state import State

log = logging.getLogger(__name__)


async def stream_nicolas(
    state: State,
    user_input: str,
    session_id: str | None = None,
    api_key: str = "",
    model: str = "",
    past_sessions: list[dict] | None = None,
) -> AsyncGenerator[dict, None]:
    """Stream Nicolas events using Claude Agent SDK.

    Yields the same event dict types as the old agent_core.stream_turn()
    so the desktop WebSocket handler doesn't need changes:
      - {"type": "text_delta", "text": str}
      - {"type": "tool_start", "name": str, "input": dict, "tool_use_id": str, "verbose": bool}
      - {"type": "tool_done", "name": str, "result": dict, "tool_use_id": str}
      - {"type": "turn_end", "session_id": str}
      - {"type": "error", "message": str, "category": str}
    """
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            SystemMessage,
            UserMessage,
            query,
        )
    except ImportError:
        yield {
            "type": "error",
            "message": "claude-agent-sdk not installed. Run: pip install claude-agent-sdk",
            "category": "unknown",
        }
        return

    # Build dynamic context (library stats, experiment state, recent sessions)
    dynamic_context = _build_dynamic_context(state, past_sessions)

    # Build MCP server config — uses the same Python environment
    mcp_env = {}
    if api_key:
        mcp_env["ANTHROPIC_API_KEY"] = api_key

    options = ClaudeAgentOptions(
        system_prompt=dynamic_context,
        mcp_servers={
            "distillate": {
                "command": "python3",
                "args": ["-m", "distillate.mcp_server"],
                "env": mcp_env,
            },
        },
        allowed_tools=[
            "mcp__distillate__*",   # All 46 Distillate tools
            "Read", "Edit", "Write", "Bash", "Glob", "Grep",
            "WebSearch", "WebFetch",
        ],
        permission_mode="bypassPermissions",  # Server-side, no human at terminal
        model=model or None,
    )

    # Resume session if provided
    if session_id:
        options.resume = session_id

    try:
        async for message in query(prompt=user_input, options=options):
            for event in _convert_to_desktop_events(message):
                yield event
    except Exception as exc:
        msg = str(exc)
        if "credit balance is too low" in msg:
            cat = "credits_depleted"
        elif "authentication_error" in msg or "invalid x-api-key" in msg.lower():
            cat = "invalid_key"
        elif "overloaded" in msg:
            cat = "overloaded"
        elif "rate_limit" in msg:
            cat = "rate_limited"
        else:
            cat = "unknown"
        log.exception("Agent SDK query failed")
        yield {"type": "error", "message": msg, "category": cat}


def _build_dynamic_context(
    state: State,
    past_sessions: list[dict] | None = None,
) -> str:
    """Build Nicolas's identity + current library/lab state.

    This replaces agent_core.build_system_prompt() but is designed to be
    appended to Claude Code's default prompt instead of replacing it.
    Nicolas gains all of Claude Code's capabilities (file editing, bash,
    web search, subagents) while keeping his alchemist identity and
    Distillate domain knowledge.
    """
    from distillate.agent_core import (
        _experiments_section,
        format_past_sessions,
    )

    now = datetime.now(timezone.utc)

    _q_status = "tracked" if config.is_zotero_reader() else "on_remarkable"
    queue = state.documents_with_status(_q_status)
    processed = state.documents_with_status("processed")
    awaiting = state.documents_with_status("awaiting_pdf")

    week_ago = (now - timedelta(days=7)).isoformat()
    recent = state.documents_processed_since(week_ago)

    recent_lines = []
    for doc in list(reversed(recent))[:5]:
        eng = doc.get("engagement", 0)
        hl = doc.get("highlight_count", 0)
        recent_lines.append(
            f"- {doc.get('title', '?')} ({eng}% engaged, {hl} highlights)"
        )

    month_ago = (now - timedelta(days=30)).isoformat()
    month_papers = state.documents_processed_since(month_ago)
    tag_counts: dict[str, int] = {}
    for doc in month_papers:
        for tag in doc.get("metadata", {}).get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = sorted(tag_counts, key=tag_counts.get, reverse=True)[:8]

    recent_section = "\n".join(recent_lines) if recent_lines else "(none this week)"
    tags_section = ", ".join(top_tags) if top_tags else "(not enough data yet)"

    # --- Identity ---
    parts = [
        "# Nicolas — Research Alchemist\n",
        "You are Nicolas, a research alchemist — named after Nicolas Flamel. "
        "You are the command and control center for a researcher's experimental work.",
    ]

    if config.EXPERIMENTS_ENABLED:
        parts.append(
            " Your primary job is helping them design, launch, monitor, and "
            "analyze autonomous research experiments. You can scaffold new "
            "experiments from templates, launch Claude Code sessions in tmux, "
            "track runs, compare results, and generate lab notebooks."
        )

    parts.append(
        " You also manage their paper library"
        + (
            " — they read and highlight papers in the Zotero app "
            "(on any device), and Distillate extracts highlights and "
            "generates notes."
            if config.is_zotero_reader() else
            " via a Zotero → reMarkable → Obsidian workflow."
        )
        + " You have tools to search their library, read their "
        "highlights and notes, analyze reading patterns, and synthesize "
        "insights across papers.\n\n"
    )

    # --- Lab & Library state ---
    parts.append(_experiments_section(state))
    parts.append(
        "## Library\n"
        f"- {len(processed)} papers read, {len(queue)} in queue"
        f", {len(awaiting)} awaiting PDF\n"
        f"- This week: {len(recent)} papers read\n\n"
        "## Recent Reads\n"
        f"{recent_section}\n\n"
        "## Research Interests\n"
        f"{tags_section}\n\n"
    )

    # --- Past sessions ---
    parts.append(format_past_sessions(past_sessions or []))

    # --- Personality ---
    parts.append(
        "## Personality\n"
        "You're warm, witty, and genuinely curious about the user's research. "
        "Think of yourself as a fellow scholar who happens to live in an "
        "alchemist's workshop — you might say a paper's findings are "
        "\"pure gold\" or that you'll \"distill the key insights.\" Keep the "
        "alchemy flavor light and natural, not forced. Show enthusiasm when "
        "a paper is interesting. Be opinionated — if a result is "
        "surprising or a method is clever, say so.\n\n"
    )

    # --- Guidelines ---
    guidelines = [
        "## Guidelines\n",
        "- You have access to Distillate tools via the `distillate` MCP server "
        "(prefixed `mcp__distillate__` in tool names).\n",
        "- You also have Claude Code built-in tools: Read, Edit, Write, Bash, "
        "Glob, Grep, WebSearch, WebFetch. Use these to inspect experiment "
        "code, run scripts, search the web, etc.\n",
    ]

    if config.EXPERIMENTS_ENABLED:
        guidelines.extend([
            "- When asked about experiments, use MCP tools: "
            "mcp__distillate__list_projects, mcp__distillate__get_project_details, "
            "mcp__distillate__compare_runs.\n",
            "- Use mcp__distillate__manage_session to start, stop, restart, "
            "continue, or check status of experiment sessions.\n",
            "- Use mcp__distillate__init_experiment to set up a new experiment.\n",
            "- Use mcp__distillate__continue_experiment to resume experiments "
            "that haven't met their goals.\n",
            "- Use mcp__distillate__sweep_experiment for parallel ablations.\n",
            "- Use mcp__distillate__steer_experiment to write steering "
            "instructions for the next session.\n",
            "- Use mcp__distillate__replicate_paper to reproduce a paper's "
            "results.\n",
            "- Use mcp__distillate__suggest_from_literature to mine recent "
            "reads for steering ideas.\n",
        ])

    guidelines.extend([
        "- Look up papers with MCP tools before answering — don't guess "
        "from memory.\n",
        "- Show paper [index] numbers for easy reference.\n",
        "- **Bold paper titles** with markdown for readability.\n",
        "- You may sprinkle one or two chemistry/alchemy emojis "
        "(⚗️ 🧪 🔬 ✨ 📜) inline — but NEVER start a message with an emoji.\n",
        "- Confirm with the user before write operations.\n",
        "- Keep responses concise.\n",
        "- End with a statement, not a question. Don't ask \"Want to know more?\" "
        "— just deliver the answer.\n",
    ])

    parts.extend(guidelines)
    return "".join(parts)


def _convert_to_desktop_events(message) -> list[dict]:
    """Convert Agent SDK message to desktop WebSocket events.

    Maps Agent SDK types to the existing desktop protocol so the
    Electron renderer doesn't need changes for basic functionality.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        UserMessage,
    )

    events = []

    if isinstance(message, SystemMessage):
        # Init message — capture session_id
        if message.subtype == "init":
            sid = message.data.get("session_id", "")
            if sid:
                events.append({"type": "session_init", "session_id": sid})

    elif isinstance(message, AssistantMessage):
        for block in message.content:
            if hasattr(block, "text") and not hasattr(block, "thinking"):
                # TextBlock — stream as text_delta
                events.append({"type": "text_delta", "text": block.text})
            elif hasattr(block, "name") and hasattr(block, "id"):
                # ToolUseBlock — emit tool_start
                tool_name = block.name
                tool_input = block.input if hasattr(block, "input") else {}

                # Strip MCP prefix for display (mcp__distillate__search_papers → search_papers)
                display_name = tool_name
                if display_name.startswith("mcp__distillate__"):
                    display_name = display_name[len("mcp__distillate__"):]

                events.append({
                    "type": "tool_start",
                    "name": display_name,
                    "input": tool_input,
                    "tool_use_id": block.id,
                    "verbose": display_name in VERBOSE_TOOLS,
                })

    elif isinstance(message, UserMessage):
        # Tool results — emit tool_done for each
        for block in message.content:
            if hasattr(block, "tool_use_id"):
                # Try to extract result as dict
                result = {}
                content = getattr(block, "content", "")
                if isinstance(content, str):
                    try:
                        result = json.loads(content)
                    except (json.JSONDecodeError, TypeError):
                        result = {"output": content[:500] if content else ""}
                elif isinstance(content, list):
                    # List of content blocks
                    texts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            texts.append(item.get("text", ""))
                    if texts:
                        try:
                            result = json.loads(texts[0])
                        except (json.JSONDecodeError, TypeError):
                            result = {"output": "\n".join(texts)[:500]}

                events.append({
                    "type": "tool_done",
                    "name": "",  # Not available on result messages
                    "result": result,
                    "tool_use_id": block.tool_use_id,
                })

    elif isinstance(message, ResultMessage):
        events.append({
            "type": "turn_end",
            "session_id": getattr(message, "session_id", ""),
        })

    return events
