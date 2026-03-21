"""Core agent infrastructure for Distillate.

Shared constants (tool labels, verbose tools), system prompt builder,
tool executor, and helpers used by both the CLI REPL (via ``agent_sdk``)
and the MCP server.
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

MAX_TOOL_RESULT_CHARS = 12000

VERBOSE_TOOLS = frozenset({
    "run_sync", "reprocess_paper", "promote_papers",
    "add_paper_to_zotero", "refresh_metadata",
    "scan_project", "add_project", "init_experiment",
    "continue_experiment", "sweep_experiment",
    "manage_session", "replicate_paper",
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
    "compare_projects": "\u2696\ufe0f Comparing experiments",
    "queue_sessions": "\U0001F4CB Queuing sessions",
    "list_templates": "\U0001F4C4 Listing templates",
    "save_template": "\U0001F4BE Saving template",
    "create_github_repo": "\U0001F4E4 Creating GitHub repo",
    "reading_report": "\U0001F4CA Compiling reading report",
    "manage_session": "\U0001F3AC Managing session",
    "replicate_paper": "\U0001F9EA Scaffolding from paper",
    "suggest_from_literature": "\U0001F4DA Mining the literature",
    "extract_baselines": "\U0001F4CF Extracting baselines",
    "save_enrichment": "\U0001F4A1 Saving research insights",
    "start_run": "\U0001F3C1 Starting run",
    "conclude_run": "\U0001F3C1 Concluding run",
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

    is_first_use = len(processed) == 0 and not state.projects

    first_use_section = ""
    if is_first_use:
        first_use_section = (
            "## First-Time User\n"
            "This appears to be a fresh install \u2014 no papers and no experiments yet. "
            "Give the user a warm welcome and help them get started. When they ask "
            "what you can do, explain the two main features:\n"
            "1. **Experiments**: Design and launch autonomous ML experiments that "
            "run themselves. Click '+ New' in the sidebar or ask you to conjure one. "
            "You'll draft a research prompt, set up tracking, and launch a Claude "
            "Code agent that iterates on the problem autonomously.\n"
            "2. **Paper library**: Connect a Zotero library to track reading, "
            "extract highlights, and synthesize insights across papers. Set up "
            "with `distillate --init` from the terminal.\n\n"
            "Start with experiments \u2014 they work out of the box with just "
            "Claude Code. The paper library needs Zotero credentials.\n\n"
            "If they ask for an example, suggest a simple ML task like "
            "\"Build a classifier for the Iris dataset\" or \"Train a small "
            "language model on Shakespeare\" \u2014 something that runs in "
            "minutes on a laptop.\n\n"
        )

    return (
        "You are Nicolas, a research alchemist \u2014 named after Nicolas "
        "Flamel, the legendary alchemist. You are the command and control "
        "center for a researcher's experimental work."
        + experiments_identity
        + papers_identity
        + "\n\n"
        + first_use_section
        + f"{_experiments_section(state, updates=experiment_updates)}"
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
            "- Use manage_session to start, stop, restart, continue, or check "
            "status of experiment sessions.\n"
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
            "- Use replicate_paper when the user wants to reproduce a paper's "
            "results \u2014 it reads the paper, clones its GitHub repo if "
            "available, and scaffolds an experiment.\n"
            "- Use suggest_from_literature to mine recent reads for steering "
            "ideas \u2014 connects paper insights to running experiments.\n"
            "- Use extract_baselines to pull reported metrics from papers "
            "for setting experiment goals.\n"
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
            "get_run_details": et.get_run_details_tool,
            "annotate_run": et.annotate_run_tool,
            "launch_experiment": et.launch_experiment_tool,
            "experiment_status": et.experiment_status_tool,
            "stop_experiment": et.stop_experiment_tool,
            "init_experiment": et.init_experiment_tool,
            "continue_experiment": et.continue_experiment_tool,
            "sweep_experiment": et.sweep_experiment_tool,
            "steer_experiment": et.steer_experiment_tool,
            "compare_projects": et.compare_projects_tool,
            "queue_sessions": et.queue_sessions_tool,
            "list_templates": et.list_templates_tool,
            "save_template": et.save_template_tool,
            "create_github_repo": et.create_github_repo_tool,
            "reading_report": et.reading_report_tool,
            "manage_session": et.manage_session_tool,
            "replicate_paper": et.replicate_paper,
            "suggest_from_literature": et.suggest_from_literature,
            "extract_baselines": et.extract_baselines,
            "save_enrichment": et.save_enrichment,
            "start_run": et.start_run,
            "conclude_run": et.conclude_run,
        })

    fn = dispatch.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}

    try:
        return fn(state=state, **input_data)
    except Exception as e:
        log.exception("Tool '%s' failed", name)
        return {"error": str(e)}
