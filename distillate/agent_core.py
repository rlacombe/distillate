"""Core conversation logic for the Distillate agent.

Yields typed event dicts that any frontend (terminal REPL, WebSocket
server, etc.) can consume for rendering.  No direct I/O — all output
goes through yielded events.
"""

import json
import logging

from datetime import datetime, timedelta, timezone

from distillate import config
from distillate.state import State
from distillate.tools import TOOL_SCHEMAS as _PAPER_TOOL_SCHEMAS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOOL_STEPS = 5
MAX_TOKENS = 2048
CONVERSATION_TRIM_THRESHOLD = 40
CONVERSATION_KEEP = 24
MAX_TOOL_RESULT_CHARS = 12000

VERBOSE_TOOLS = frozenset({
    "run_sync", "reprocess_paper", "promote_papers",
    "add_paper_to_zotero", "refresh_metadata",
    "scan_project", "add_project", "init_experiment",
    "continue_experiment", "sweep_experiment",
})

TOOL_LABELS = {
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
    "delete_paper": "\U0001F5D1\uFE0F Removing from the library",
    "list_projects": "\U0001F9EA Surveying the laboratory",
    "get_project_details": "\U0001F52C Examining the experiment",
    "compare_runs": "\u2696\ufe0f Weighing the results",
    "scan_project": "\U0001F50D Scanning for experiments",
    "get_experiment_notebook": "\U0001F4D3 Opening the lab notebook",
    "add_project": "\U0001F4C1 Adding project to the lab",
    "rename_project": "\u270F\uFE0F Relabeling the project",
    "rename_run": "\u270F\uFE0F Relabeling the run",
    "delete_project": "\U0001F5D1\uFE0F Removing from the lab",
    "delete_run": "\U0001F5D1\uFE0F Removing the run",
    "update_project": "\U0001F4DD Updating project details",
    "link_paper": "\U0001F517 Linking paper to project",
    "update_goals": "\U0001F3AF Setting project goals",
    "annotate_run": "\U0001F4DD Adding note to run",
    "init_experiment": "\u2697\ufe0f Drafting experiment prompt",
    "continue_experiment": "\U0001F504 Continuing experiment",
    "sweep_experiment": "\U0001F9F9 Launching sweep",
    "steer_experiment": "\U0001F9E7 Steering the experiment",
}


def _build_tool_schemas() -> list[dict]:
    """Combine paper + experiment tool schemas."""
    schemas = list(_PAPER_TOOL_SCHEMAS)
    if config.EXPERIMENTS_ENABLED:
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        schemas.extend(EXPERIMENT_TOOL_SCHEMAS)
    return schemas


TOOL_SCHEMAS = _build_tool_schemas()


def tool_label(name: str) -> str:
    """Human-friendly label for a tool invocation."""
    return TOOL_LABELS.get(name, name.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

_AGENT_MODEL = None  # resolved lazily after config is loaded


def get_model() -> str:
    global _AGENT_MODEL
    if _AGENT_MODEL is None:
        _AGENT_MODEL = config.CLAUDE_AGENT_MODEL
    return _AGENT_MODEL


def create_client():
    """Create an Anthropic client based on available credentials.

    Returns ``None`` when no credentials are configured.

    * ``DISTILLATE_AUTH_TOKEN`` + ``DISTILLATE_API_URL`` → cloud proxy
    * ``ANTHROPIC_API_KEY`` → direct Anthropic (CLI power users)
    """
    try:
        import anthropic
    except ImportError:
        return None

    if config.DISTILLATE_AUTH_TOKEN and config.DISTILLATE_API_URL:
        return anthropic.Anthropic(
            api_key=config.DISTILLATE_AUTH_TOKEN,
            base_url=config.DISTILLATE_API_URL,
        )
    if config.ANTHROPIC_API_KEY:
        return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def format_past_sessions(sessions: list[dict]) -> str:
    """Format recent sessions for inclusion in the system prompt."""
    _PROMPT_SESSIONS = 3
    if not sessions:
        return ""

    now = datetime.now(timezone.utc)
    lines = []
    for s in sessions[-_PROMPT_SESSIONS:]:
        queries = [m["content"] for m in s.get("messages", []) if m["role"] == "user"]
        if not queries:
            continue
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


def _experiments_section(state: State, updates: list[dict] | None = None) -> str:
    """Build the experiments section of the system prompt."""
    if not config.EXPERIMENTS_ENABLED:
        return ""
    projects = state.projects
    if not projects:
        return ""

    # Index updates by project id for easy lookup
    update_map: dict[str, int] = {}
    for u in (updates or []):
        pid = u["project"].get("id", "")
        if pid:
            update_map[pid] = u["new_commits"]

    lines = ["## Lab"]
    for proj in projects.values():
        runs = proj.get("runs", {})
        completed = sum(1 for r in runs.values() if r.get("status") == "completed")
        line = (
            f"- {proj.get('name', '?')}: {len(runs)} runs "
            f"({completed} completed)"
        )
        n_new = update_map.get(proj.get("id", ""), 0)
        if n_new:
            line += f" — {n_new} new commit{'s' if n_new != 1 else ''} since last scan"
        lines.append(line)
    return "\n".join(lines) + "\n\n"


def build_system_prompt(
    state: State, past_sessions: list[dict] | None = None,
    experiment_updates: list[dict] | None = None,
) -> str:
    """Build a context-rich system prompt from current library state."""
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

    experiments_identity = ""
    if config.EXPERIMENTS_ENABLED:
        experiments_identity = (
            " Your primary job is helping them design, launch, monitor, and "
            "analyze autonomous research experiments. You can scaffold new "
            "experiments from templates, launch Claude Code sessions in tmux, "
            "track runs, compare results, and generate lab notebooks."
        )

    papers_identity = (
        " You also manage their paper library"
        + (
            " \u2014 they read and highlight papers in the Zotero app "
            "(on any device), and Distillate extracts highlights and "
            "generates notes."
            if config.is_zotero_reader() else
            " via a Zotero \u2192 reMarkable \u2192 Obsidian workflow."
        )
        + " You have tools to search their library, read their "
        "highlights and notes, analyze reading patterns, and synthesize "
        "insights across papers."
    )

    return (
        "You are Nicolas, a research alchemist \u2014 named after Nicolas "
        "Flamel, the legendary alchemist. You are the command and control "
        "center for a researcher's experimental work."
        + experiments_identity
        + papers_identity
        + "\n\n"
        f"{_experiments_section(state, updates=experiment_updates)}"
        "## Library\n"
        f"- {len(processed)} papers read, {len(queue)} in queue"
        f", {len(awaiting)} awaiting PDF\n"
        f"- This week: {len(recent)} papers read\n\n"
        "## Recent Reads\n"
        f"{recent_section}\n\n"
        "## Research Interests\n"
        f"{tags_section}\n\n"
        f"{format_past_sessions(past_sessions or [])}"
        "## Personality\n"
        "You're warm, witty, and genuinely curious about the user's research. "
        "Think of yourself as a fellow scholar who happens to live in an "
        "alchemist's workshop \u2014 you might say a paper's findings are "
        "\"pure gold\" or that you'll \"distill the key insights.\" Keep the "
        "alchemy flavor light and natural, not forced. Show enthusiasm when "
        "a paper is interesting. Be opinionated \u2014 if a result is "
        "surprising or a method is clever, say so.\n\n"
        "## Guidelines\n"
        + (
            "- When asked about experiments or projects, use the experiment "
            "tools (list_projects, get_project_details, compare_runs).\n"
            "- Use launch_experiment to spawn a new auto-research session.\n"
            "- Use experiment_status to check on running sessions.\n"
            "- Use stop_experiment to gracefully stop a session.\n"
            "- Use add_project or scan_project to track a new directory.\n"
            "- Use compare_runs to show what changed between experiments.\n"
            "- Use rename_project, rename_run, update_project, update_goals, "
            "link_paper to manage projects.\n"
            "- Use init_experiment to set up a new experiment from scratch — "
            "it scans the directory, drafts a PROMPT.md with Claude, and "
            "sets up hooks and tracking. Use when the user wants to start "
            "a new experiment or asks how to set one up.\n"
            "- Use continue_experiment to resume an experiment that hasn't "
            "met its goals. It launches a new session with prior-run context.\n"
            "- Use sweep_experiment to launch parallel ablations — provide a "
            "list of config dicts and each runs in its own tmux session.\n"
            "- Use steer_experiment to write steering instructions for "
            "the next session — e.g., 'try lower learning rate' or 'focus "
            "on regularization'. Instructions are auto-injected.\n"
            "- Use annotate_run to add a hypothesis or note to a run — "
            "user-provided hypotheses take precedence over LLM enrichment.\n"
            "- Use delete_project/delete_run with confirm=false first, then "
            "confirm=true after user approval.\n"
            if config.EXPERIMENTS_ENABLED else ""
        )
        + "- Look up papers with tools before answering \u2014 don't guess "
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
        "promote, delete, launch).\n"
        "- Keep responses concise \u2014 this is a terminal REPL.\n"
        "- End with a statement, not a question. Don't ask \"Want to know more?\" "
        "or \"Shall I look into X?\" \u2014 just deliver the answer. The user "
        "will ask if they want more.\n"
        "- When asked to compare or synthesize, use synthesize_across_papers.\n"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def truncate_result(result: dict, max_chars: int) -> dict:
    """Truncate a tool result dict so its JSON stays under *max_chars*."""
    if len(json.dumps(result)) <= max_chars:
        return result

    out = dict(result)
    for key, val in out.items():
        if isinstance(val, str) and len(val) > 500:
            out[key] = val[:500] + "... (truncated)"
        elif isinstance(val, list) and len(val) > 10:
            out[key] = val[:10] + ["... (truncated)"]
    while len(json.dumps(out)) > max_chars and out:
        biggest = max(out, key=lambda k: len(json.dumps(out[k])))
        out[biggest] = "(truncated)"
    return out


def execute_tool(name: str, input_data: dict, state: State) -> dict:
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
        "refresh_metadata": tools.refresh_metadata,
        "reprocess_paper": tools.reprocess_paper,
        "promote_papers": tools.promote_papers,
        "get_trending_papers": tools.get_trending_papers,
        "add_paper_to_zotero": tools.add_paper_to_zotero,
        "delete_paper": tools.delete_paper,
    }

    # Add experiment tools if enabled
    if config.EXPERIMENTS_ENABLED:
        from distillate import experiment_tools as et
        dispatch.update({
            "list_projects": et.list_projects,
            "get_project_details": et.get_project_details,
            "compare_runs": et.compare_runs,
            "scan_project": et.scan_project_tool,
            "get_experiment_notebook": et.get_experiment_notebook,
            "add_project": et.add_project_tool,
            "rename_project": et.rename_project_tool,
            "rename_run": et.rename_run_tool,
            "delete_project": et.delete_project_tool,
            "delete_run": et.delete_run_tool,
            "update_project": et.update_project_tool,
            "link_paper": et.link_paper_tool,
            "update_goals": et.update_goals_tool,
            "annotate_run": et.annotate_run_tool,
            "launch_experiment": et.launch_experiment_tool,
            "experiment_status": et.experiment_status_tool,
            "stop_experiment": et.stop_experiment_tool,
            "init_experiment": et.init_experiment_tool,
            "continue_experiment": et.continue_experiment_tool,
            "sweep_experiment": et.sweep_experiment_tool,
            "steer_experiment": et.steer_experiment_tool,
        })

    fn = dispatch.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}

    try:
        return fn(state=state, **input_data)
    except Exception as e:
        log.exception("Tool '%s' failed", name)
        return {"error": str(e)}


def trim_conversation(conversation: list[dict]) -> None:
    """Trim conversation to prevent context overflow.

    Mutates *conversation* in place.
    """
    if len(conversation) <= CONVERSATION_TRIM_THRESHOLD:
        return

    trimmed = conversation[-CONVERSATION_KEEP:]
    # Ensure conversation starts with a genuine user message — skip
    # assistant messages AND orphaned tool_result messages.
    while trimmed:
        msg = trimmed[0]
        if msg.get("role") == "assistant":
            trimmed.pop(0)
            continue
        content = msg.get("content")
        if (isinstance(content, list) and content
                and isinstance(content[0], dict)
                and content[0].get("type") == "tool_result"):
            trimmed.pop(0)
            continue
        break
    conversation[:] = trimmed


# ---------------------------------------------------------------------------
# Core conversation generator
# ---------------------------------------------------------------------------

def stream_turn(client, state, conversation, user_input, past_sessions=None,
                experiment_updates=None):
    """Yield event dicts for one conversation turn.

    Appends messages to *conversation* in place (user message, assistant
    responses, tool results).  Callers iterate over events to drive their
    UI.

    Events
    ------
    ``{"type": "text_delta", "text": str}``
        A chunk of streamed assistant text.
    ``{"type": "tool_start", "name": str, "input": dict,
       "tool_use_id": str, "verbose": bool}``
        A tool is about to execute.  The generator **pauses** here — the
        caller can set up I/O interception before resuming.
    ``{"type": "tool_done", "name": str, "result": dict,
       "tool_use_id": str}``
        A tool finished executing.
    ``{"type": "turn_end"}``
        The turn completed normally.
    ``{"type": "error", "message": str, "category": str}``
        An unrecoverable error.  Categories: ``credits_depleted``,
        ``invalid_key``, ``overloaded``, ``rate_limited``, ``unknown``.
    """
    conversation.append({"role": "user", "content": user_input})
    state.reload()

    system_prompt = build_system_prompt(
        state, past_sessions=past_sessions,
        experiment_updates=experiment_updates,
    )
    tools = _build_tool_schemas()

    for _step in range(MAX_TOOL_STEPS):
        # --- API call (streaming) ---
        try:
            with client.messages.stream(
                model=get_model(),
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=conversation,
                tools=tools,
            ) as stream:
                for event in stream:
                    if (hasattr(event, "type")
                            and event.type == "content_block_delta"
                            and hasattr(event.delta, "text")):
                        yield {"type": "text_delta", "text": event.delta.text}
                response = stream.get_final_message()
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
            log.exception("Agent API call failed")
            yield {"type": "error", "message": msg, "category": cat}
            return

        # --- Record assistant message ---
        conversation.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break  # pure text response — done

        # --- Execute tools ---
        tool_results = []
        for tool_use in tool_uses:
            yield {
                "type": "tool_start",
                "name": tool_use.name,
                "input": tool_use.input,
                "tool_use_id": tool_use.id,
                "verbose": tool_use.name in VERBOSE_TOOLS,
            }

            result = execute_tool(tool_use.name, tool_use.input, state)

            result_json = json.dumps(result)
            if len(result_json) > MAX_TOOL_RESULT_CHARS:
                result = truncate_result(result, MAX_TOOL_RESULT_CHARS)
                result_json = json.dumps(result)

            yield {
                "type": "tool_done",
                "name": tool_use.name,
                "result": result,
                "tool_use_id": tool_use.id,
            }

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_json,
            })

        conversation.append({"role": "user", "content": tool_results})

    trim_conversation(conversation)
    yield {"type": "turn_end"}
