"""Nicolas agent powered by Claude Agent SDK.

Replaces agent_core.py's custom Claude API loop with the Agent SDK,
giving Nicolas all of Claude Code's capabilities plus Distillate tools
via MCP server.

Architecture:
    Desktop WebSocket ─► NicolasClient (wraps ClaudeSDKClient)
                              │
                         Claude Code process (persistent)
                              │
                    ┌─────────┴──────────┐
                    │                    │
              MCP: distillate      Built-in tools
              (46 tools)           (Read, Edit, Bash, …)
"""

import json
import logging
import sys
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

from distillate import config
from distillate.agent_core import TOOL_LABELS, VERBOSE_TOOLS
from distillate.state import State

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NicolasClient — persistent wrapper around ClaudeSDKClient
# ---------------------------------------------------------------------------

class NicolasClient:
    """Persistent Claude Code connection for one WebSocket session.

    Keeps the Claude Code subprocess and MCP server connections alive
    between messages.  Supports model switching, new conversations,
    and streaming responses as desktop-protocol events.
    """

    def __init__(self, state: State, model: str = ""):
        self._state = state
        self._model = model
        self._client = None  # lazily created on first query
        self._session_id: str | None = None

    async def _ensure_connected(self) -> None:
        """Create and connect the ClaudeSDKClient if not already connected."""
        if self._client is not None:
            return

        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        # Find the MCP server python — same venv as the server
        python_path = sys.executable

        options = ClaudeAgentOptions(
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": _build_dynamic_context(self._state),
            },
            mcp_servers={
                "distillate": {
                    "command": python_path,
                    "args": ["-m", "distillate.mcp_server"],
                },
            },
            allowed_tools=[
                "mcp__distillate__*",
                "Read", "Edit", "Write", "Bash", "Glob", "Grep",
                "WebSearch", "WebFetch",
            ],
            permission_mode="bypassPermissions",
            model=self._model or None,
            resume=self._session_id,
        )

        self._client = ClaudeSDKClient(options)
        await self._client.connect()
        log.info("NicolasClient connected (model=%s, resume=%s)",
                 self._model or "default", self._session_id)

    async def send(self, user_input: str) -> AsyncGenerator[dict, None]:
        """Send a message and yield desktop-protocol events."""
        await self._ensure_connected()

        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )

        await self._client.query(user_input)

        async for message in self._client.receive_response():
            # --- AssistantMessage: text + tool calls ---
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield {"type": "text_delta", "text": block.text}
                    elif isinstance(block, ToolUseBlock):
                        display = block.name
                        if display.startswith("mcp__distillate__"):
                            display = display[len("mcp__distillate__"):]
                        yield {
                            "type": "tool_start",
                            "name": display,
                            "input": block.input,
                            "tool_use_id": block.id,
                            "verbose": display in VERBOSE_TOOLS,
                            "label": TOOL_LABELS.get(display, ""),
                        }

            # --- UserMessage: tool results ---
            elif isinstance(message, UserMessage):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        result = _parse_tool_result(block.content)
                        yield {
                            "type": "tool_done",
                            "tool_use_id": block.tool_use_id,
                            "result": result,
                            "is_error": block.is_error or False,
                        }

            # --- SystemMessage: init, task events ---
            elif isinstance(message, SystemMessage):
                if message.subtype == "init":
                    sid = message.data.get("session_id", "")
                    if sid:
                        self._session_id = sid
                        yield {"type": "session_init", "session_id": sid}

            # --- ResultMessage: turn complete ---
            elif isinstance(message, ResultMessage):
                self._session_id = message.session_id
                yield {
                    "type": "turn_end",
                    "session_id": message.session_id,
                    "cost_usd": message.total_cost_usd,
                    "num_turns": message.num_turns,
                }

    async def interrupt(self) -> None:
        """Interrupt a running query (sends SIGINT to Claude Code)."""
        if self._client:
            await self._client.interrupt()

    async def set_model(self, model: str) -> None:
        """Change model for subsequent queries."""
        self._model = model
        if self._client:
            await self._client.set_model(model)

    async def new_conversation(self) -> None:
        """Start a fresh conversation (disconnect + reconnect)."""
        await self.disconnect()
        self._session_id = None

    async def disconnect(self) -> None:
        """Clean up the Claude Code subprocess."""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                log.debug("Disconnect error (non-critical)", exc_info=True)
            self._client = None

    @property
    def session_id(self) -> str | None:
        return self._session_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_tool_result(content) -> dict:
    """Extract a JSON-serializable dict from a ToolResultBlock's content."""
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {"output": content[:500] if content else ""}
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if texts:
            try:
                return json.loads(texts[0])
            except (json.JSONDecodeError, TypeError):
                return {"output": "\n".join(texts)[:500]}
    return {}


def _classify_error(msg: str) -> str:
    """Map error message to a UI-friendly category."""
    if "credit balance is too low" in msg:
        return "credits_depleted"
    if "authentication_error" in msg or "invalid x-api-key" in msg.lower():
        return "invalid_key"
    if "overloaded" in msg:
        return "overloaded"
    if "rate_limit" in msg:
        return "rate_limited"
    return "unknown"


def _build_dynamic_context(state: State) -> str:
    """Build Nicolas's identity + current library/lab state.

    Appended to Claude Code's default system prompt.  Nicolas gains all
    of Claude Code's capabilities (file editing, bash, web search,
    subagents) while keeping his alchemist identity and Distillate
    domain knowledge.
    """
    from distillate.agent_core import _experiments_section

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

    # --- Identity override ---
    parts = [
        "# IDENTITY OVERRIDE\n"
        "You are **Nicolas**, a research alchemist — named after Nicolas Flamel. "
        "Do NOT identify as Claude Code or Claude. Your name is Nicolas. "
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
