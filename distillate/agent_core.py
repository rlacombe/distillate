"""Core agent infrastructure for Distillate.

Shared constants (tool labels, verbose tools), system prompt builder,
tool executor, and helpers used by both the CLI REPL (via ``agent_sdk``)
and the MCP server.
"""

import json
import logging

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from distillate import config
from distillate.state import State
from distillate.tools import TOOL_SCHEMAS as _PAPER_TOOL_SCHEMAS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool registry
#
# ``ToolName`` is the single source of truth for tool identifiers used across
# the MCP server, the agent SDK label layer, and the dispatch table inside
# ``execute_tool``. Using a StrEnum means:
#   * typos become AttributeError at import time (ToolName.SEARH_PAPERS)
#   * members compare/hash as strings so existing dict lookups and
#     ``if name in TOOL_LABELS`` checks keep working
#   * the consistency check at the bottom of this module fails loudly
#     if a tool is added to the dispatch table but forgotten in labels
#     (or vice versa).
# ---------------------------------------------------------------------------


class ToolName(StrEnum):
    # Paper library
    SEARCH_PAPERS = "search_papers"
    GET_PAPER_DETAILS = "get_paper_details"
    GET_READING_STATS = "get_reading_stats"
    GET_QUEUE = "get_queue"
    GET_RECENT_READS = "get_recent_reads"
    SUGGEST_NEXT_READS = "suggest_next_reads"
    SYNTHESIZE_ACROSS_PAPERS = "synthesize_across_papers"
    RUN_SYNC = "run_sync"
    REFRESH_METADATA = "refresh_metadata"
    REPROCESS_PAPER = "reprocess_paper"
    PROMOTE_PAPERS = "promote_papers"
    GET_TRENDING_PAPERS = "get_trending_papers"
    SEARCH_HF_MODELS = "search_hf_models"
    SEARCH_HF_DATASETS = "search_hf_datasets"
    FIND_PAPER_ASSOCIATIONS = "find_paper_associations"
    ADD_PAPER_TO_ZOTERO = "add_paper_to_zotero"
    DELETE_PAPER = "delete_paper"
    READING_REPORT = "reading_report"

    # Experiments & runs
    LIST_EXPERIMENTS = "list_experiments"
    GET_EXPERIMENT_DETAILS = "get_experiment_details"
    COMPARE_RUNS = "compare_runs"
    SCAN_EXPERIMENT = "scan_experiment"
    GET_EXPERIMENT_NOTEBOOK = "get_experiment_notebook"
    ADD_EXPERIMENT = "add_experiment"
    RENAME_EXPERIMENT = "rename_experiment"
    RENAME_RUN = "rename_run"
    DELETE_EXPERIMENT = "delete_experiment"
    DELETE_RUN = "delete_run"
    UPDATE_EXPERIMENT = "update_experiment"
    LINK_PAPER = "link_paper"
    UPDATE_GOALS = "update_goals"
    GET_RUN_DETAILS = "get_run_details"
    ANNOTATE_RUN = "annotate_run"
    LAUNCH_EXPERIMENT = "launch_experiment"
    EXPERIMENT_STATUS = "experiment_status"
    STOP_EXPERIMENT = "stop_experiment"
    INIT_EXPERIMENT = "init_experiment"
    CONTINUE_EXPERIMENT = "continue_experiment"
    SWEEP_EXPERIMENT = "sweep_experiment"
    STEER_EXPERIMENT = "steer_experiment"
    ASK_EXPERIMENTALIST = "ask_experimentalist"
    COMPARE_EXPERIMENTS = "compare_experiments"
    QUEUE_SESSIONS = "queue_sessions"
    LIST_TEMPLATES = "list_templates"
    SAVE_TEMPLATE = "save_template"
    CREATE_GITHUB_REPO = "create_github_repo"
    MANAGE_SESSION = "manage_session"
    REPLICATE_PAPER = "replicate_paper"
    SUGGEST_FROM_LITERATURE = "suggest_from_literature"
    EXTRACT_BASELINES = "extract_baselines"
    SAVE_ENRICHMENT = "save_enrichment"
    START_RUN = "start_run"
    CONCLUDE_RUN = "conclude_run"
    PURGE_HOOK_RUNS = "purge_hook_runs"
    DISCOVER_RELEVANT_PAPERS = "discover_relevant_papers"

    # HuggingFace Jobs
    SUBMIT_HF_JOB = "submit_hf_job"
    CHECK_HF_JOB = "check_hf_job"
    TAIL_HF_JOB_LOGS = "tail_hf_job_logs"
    CANCEL_HF_JOB = "cancel_hf_job"
    LIST_HF_JOBS = "list_hf_jobs"

    # Workspaces & coding sessions
    CREATE_WORKSPACE = "create_workspace"
    LIST_WORKSPACES = "list_workspaces"
    GET_WORKSPACE = "get_workspace"
    ADD_WORKSPACE_REPO = "add_workspace_repo"
    LAUNCH_CODING_SESSION = "launch_coding_session"
    LAUNCH_WRITING_SESSION = "launch_writing_session"
    LAUNCH_SURVEY_SESSION = "launch_survey_session"
    STOP_CODING_SESSION = "stop_coding_session"
    RESTART_CODING_SESSION = "restart_coding_session"
    RECOVER_CODING_SESSION = "recover_coding_session"
    RECOVER_ALL_SESSIONS = "recover_all_sessions"
    STOP_ALL_SESSIONS = "stop_all_sessions"

    # Work Sessions (deliverable-oriented workspace primitives)
    CREATE_WORK_ITEM = "create_work_item"
    LIST_WORK_ITEMS = "list_work_items"
    COMPLETE_WORK_ITEM = "complete_work_item"

    # Experiment notes & lab notebook
    GET_EXPERIMENT_NOTES = "get_workspace_notes"
    SAVE_EXPERIMENT_NOTES = "save_workspace_notes"
    APPEND_LAB_BOOK = "append_lab_book"
    READ_LAB_NOTEBOOK = "read_lab_notebook"
    NOTEBOOK_DIGEST = "notebook_digest"

    # Long-lived agents
    LIST_AGENT_TEMPLATES = "list_agent_templates"
    CREATE_AGENT = "create_agent"
    LIST_AGENTS = "list_agents"
    START_AGENT_SESSION = "start_agent_session"
    STOP_AGENT_SESSION = "stop_agent_session"
    UPDATE_AGENT = "update_agent"
    DELETE_AGENT = "delete_agent"

    # Lab REPL (recursive reasoning sandbox)
    LAB_REPL = "lab_repl"

    # Thread management
    SET_THREAD_NAME = "set_thread_name"

    # Experimentalist context management
    DISTILLATE_REPL = "distillate_repl"
    DISTILLATE_SEARCH = "distillate_search"
    DISTILLATE_NOTE = "distillate_note"


# Tools that only exist when config.EXPERIMENTS_ENABLED is true.
_EXPERIMENT_TOOLS: frozenset[ToolName] = frozenset({
    ToolName.LIST_EXPERIMENTS, ToolName.GET_EXPERIMENT_DETAILS, ToolName.COMPARE_RUNS,
    ToolName.SCAN_EXPERIMENT, ToolName.GET_EXPERIMENT_NOTEBOOK, ToolName.ADD_EXPERIMENT,
    ToolName.RENAME_EXPERIMENT, ToolName.RENAME_RUN, ToolName.DELETE_EXPERIMENT,
    ToolName.DELETE_RUN, ToolName.UPDATE_EXPERIMENT, ToolName.LINK_PAPER,
    ToolName.UPDATE_GOALS, ToolName.GET_RUN_DETAILS, ToolName.ANNOTATE_RUN,
    ToolName.LAUNCH_EXPERIMENT, ToolName.EXPERIMENT_STATUS,
    ToolName.STOP_EXPERIMENT, ToolName.INIT_EXPERIMENT,
    ToolName.CONTINUE_EXPERIMENT, ToolName.SWEEP_EXPERIMENT,
    ToolName.STEER_EXPERIMENT, ToolName.ASK_EXPERIMENTALIST,
    ToolName.COMPARE_EXPERIMENTS,
    ToolName.QUEUE_SESSIONS, ToolName.LIST_TEMPLATES, ToolName.SAVE_TEMPLATE,
    ToolName.CREATE_GITHUB_REPO, ToolName.READING_REPORT,
    ToolName.MANAGE_SESSION, ToolName.REPLICATE_PAPER,
    ToolName.SUGGEST_FROM_LITERATURE, ToolName.EXTRACT_BASELINES,
    ToolName.SAVE_ENRICHMENT, ToolName.START_RUN, ToolName.CONCLUDE_RUN,
    ToolName.PURGE_HOOK_RUNS, ToolName.DISCOVER_RELEVANT_PAPERS,
    ToolName.SUBMIT_HF_JOB, ToolName.CHECK_HF_JOB, ToolName.TAIL_HF_JOB_LOGS,
    ToolName.CANCEL_HF_JOB, ToolName.LIST_HF_JOBS,
    ToolName.CREATE_WORKSPACE, ToolName.LIST_WORKSPACES, ToolName.GET_WORKSPACE,
    ToolName.ADD_WORKSPACE_REPO, ToolName.LAUNCH_CODING_SESSION,
    ToolName.LAUNCH_WRITING_SESSION, ToolName.LAUNCH_SURVEY_SESSION,
    ToolName.STOP_CODING_SESSION, ToolName.RESTART_CODING_SESSION,
    ToolName.RECOVER_CODING_SESSION, ToolName.RECOVER_ALL_SESSIONS,
    ToolName.STOP_ALL_SESSIONS,
    ToolName.CREATE_WORK_ITEM, ToolName.LIST_WORK_ITEMS, ToolName.COMPLETE_WORK_ITEM,
    ToolName.GET_EXPERIMENT_NOTES, ToolName.SAVE_EXPERIMENT_NOTES,
    ToolName.APPEND_LAB_BOOK, ToolName.READ_LAB_NOTEBOOK,
    ToolName.NOTEBOOK_DIGEST,
    ToolName.LIST_AGENT_TEMPLATES, ToolName.CREATE_AGENT, ToolName.LIST_AGENTS,
    ToolName.START_AGENT_SESSION, ToolName.STOP_AGENT_SESSION,
    ToolName.UPDATE_AGENT, ToolName.DELETE_AGENT,
    ToolName.LAB_REPL, ToolName.SET_THREAD_NAME,
    ToolName.DISTILLATE_REPL, ToolName.DISTILLATE_SEARCH, ToolName.DISTILLATE_NOTE,
})

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOOL_RESULT_CHARS = 12000

VERBOSE_TOOLS: frozenset[ToolName] = frozenset({
    ToolName.RUN_SYNC, ToolName.REPROCESS_PAPER, ToolName.PROMOTE_PAPERS,
    ToolName.ADD_PAPER_TO_ZOTERO, ToolName.REFRESH_METADATA,
    ToolName.SCAN_EXPERIMENT, ToolName.ADD_EXPERIMENT, ToolName.INIT_EXPERIMENT,
    ToolName.CONTINUE_EXPERIMENT, ToolName.SWEEP_EXPERIMENT,
    ToolName.MANAGE_SESSION, ToolName.REPLICATE_PAPER,
    ToolName.CREATE_WORKSPACE, ToolName.LAUNCH_CODING_SESSION,
    ToolName.STOP_CODING_SESSION, ToolName.RESTART_CODING_SESSION,
    ToolName.RECOVER_CODING_SESSION, ToolName.RECOVER_ALL_SESSIONS,
    ToolName.STOP_ALL_SESSIONS,
    ToolName.LAB_REPL,
    ToolName.DISTILLATE_SEARCH,
})

TOOL_LABELS: dict[ToolName, str] = {
    ToolName.SEARCH_PAPERS: "\U0001F50D Searching the library",
    ToolName.GET_PAPER_DETAILS: "\U0001F4DC Unrolling the manuscript",
    ToolName.GET_READING_STATS: "\U0001F4CA Tallying the ledger",
    ToolName.GET_QUEUE: "\u2697\ufe0f Inspecting the queue",
    ToolName.GET_RECENT_READS: "\U0001F4DA Reviewing recent reads",
    ToolName.SUGGEST_NEXT_READS: "\U0001F52E Consulting the oracle",
    ToolName.SYNTHESIZE_ACROSS_PAPERS: "\u2728 Cross-referencing texts",
    ToolName.RUN_SYNC: "\U0001F525 Firing up the furnace",
    ToolName.REFRESH_METADATA: "\U0001F4DD Refreshing metadata",
    ToolName.REPROCESS_PAPER: "\U0001F9EA Re-extracting the essence",
    ToolName.PROMOTE_PAPERS: "\u2B50 Promoting to the shelf",
    ToolName.GET_TRENDING_PAPERS: "\U0001F4C8 Scanning the latest papers",
    ToolName.SEARCH_HF_MODELS: "\U0001F917 Searching HuggingFace models",
    ToolName.SEARCH_HF_DATASETS: "\U0001F917 Searching HuggingFace datasets",
    ToolName.FIND_PAPER_ASSOCIATIONS: "\U0001F517 Finding paper associations",
    ToolName.ADD_PAPER_TO_ZOTERO: "\U0001F4D6 Adding to the library",
    ToolName.DELETE_PAPER: "\U0001F5D1\uFE0F Removing from the library",
    ToolName.LIST_EXPERIMENTS: "\U0001F9EA Surveying the laboratory",
    ToolName.GET_EXPERIMENT_DETAILS: "\U0001F52C Examining the experiment",
    ToolName.COMPARE_RUNS: "\u2696\ufe0f Weighing the results",
    ToolName.SCAN_EXPERIMENT: "\U0001F50D Scanning for experiments",
    ToolName.GET_EXPERIMENT_NOTEBOOK: "\U0001F4D3 Opening the lab notebook",
    ToolName.ADD_EXPERIMENT: "\U0001F4C1 Adding experiment to the lab",
    ToolName.RENAME_EXPERIMENT: "\u270F\uFE0F Relabeling the experiment",
    ToolName.RENAME_RUN: "\u270F\uFE0F Relabeling the run",
    ToolName.DELETE_EXPERIMENT: "\U0001F5D1\uFE0F Removing from the lab",
    ToolName.DELETE_RUN: "\U0001F5D1\uFE0F Removing the run",
    ToolName.UPDATE_EXPERIMENT: "\U0001F4DD Updating experiment details",
    ToolName.LINK_PAPER: "\U0001F517 Linking paper to experiment",
    ToolName.UPDATE_GOALS: "\U0001F3AF Setting experiment goals",
    ToolName.GET_RUN_DETAILS: "\U0001F52C Examining run details",
    ToolName.ANNOTATE_RUN: "\U0001F4DD Adding note to run",
    ToolName.LAUNCH_EXPERIMENT: "\U0001F3C1 Launching experiment",
    ToolName.EXPERIMENT_STATUS: "\U0001F4CA Checking experiment status",
    ToolName.STOP_EXPERIMENT: "\u23F9\uFE0F Stopping experiment",
    ToolName.INIT_EXPERIMENT: "\u2697\ufe0f Drafting experiment prompt",
    ToolName.CONTINUE_EXPERIMENT: "\U0001F504 Continuing experiment",
    ToolName.SWEEP_EXPERIMENT: "\U0001F9F9 Launching sweep",
    ToolName.STEER_EXPERIMENT: "\U0001F9E7 Steering the experiment",
    ToolName.ASK_EXPERIMENTALIST: "\U0001F4AC Asking the Experimentalist",
    ToolName.COMPARE_EXPERIMENTS: "\u2696\ufe0f Comparing experiments",
    ToolName.QUEUE_SESSIONS: "\U0001F4CB Queuing sessions",
    ToolName.LIST_TEMPLATES: "\U0001F4C4 Listing templates",
    ToolName.SAVE_TEMPLATE: "\U0001F4BE Saving template",
    ToolName.CREATE_GITHUB_REPO: "\U0001F4E4 Creating GitHub repo",
    ToolName.READING_REPORT: "\U0001F4CA Compiling reading report",
    ToolName.MANAGE_SESSION: "\U0001F3AC Managing session",
    ToolName.REPLICATE_PAPER: "\U0001F9EA Scaffolding from paper",
    ToolName.SUGGEST_FROM_LITERATURE: "\U0001F4DA Mining the literature",
    ToolName.EXTRACT_BASELINES: "\U0001F4CF Extracting baselines",
    ToolName.SAVE_ENRICHMENT: "\U0001F4A1 Saving research insights",
    ToolName.START_RUN: "\U0001F3C1 Starting run",
    ToolName.CONCLUDE_RUN: "\U0001F3C1 Concluding run",
    ToolName.PURGE_HOOK_RUNS: "\U0001F9F9 Purging spurious runs",
    ToolName.DISCOVER_RELEVANT_PAPERS: "\U0001F517 Finding related papers",
    # HuggingFace Jobs
    ToolName.SUBMIT_HF_JOB: "\U0001F680 Submitting HuggingFace job",
    ToolName.CHECK_HF_JOB: "\U0001F50D Checking HuggingFace job",
    ToolName.TAIL_HF_JOB_LOGS: "\U0001F4FA Streaming job logs",
    ToolName.CANCEL_HF_JOB: "\u23F9\uFE0F Cancelling HuggingFace job",
    ToolName.LIST_HF_JOBS: "\U0001F4CB Listing HuggingFace jobs",
    # Workspace projects
    ToolName.CREATE_WORKSPACE: "\U0001F4C1 Creating workspace",
    ToolName.LIST_WORKSPACES: "\U0001F4CB Listing workspaces",
    ToolName.GET_WORKSPACE: "\U0001F52C Examining workspace",
    ToolName.ADD_WORKSPACE_REPO: "\U0001F517 Linking repo",
    ToolName.LAUNCH_CODING_SESSION: "\U0001F4BB Launching coding session",
    ToolName.LAUNCH_WRITING_SESSION: "\u270D\uFE0F Launching writing session",
    ToolName.LAUNCH_SURVEY_SESSION: "\U0001F50D Launching survey session",
    ToolName.CREATE_WORK_ITEM: "\U0001F4CB Creating work session",
    ToolName.LIST_WORK_ITEMS: "\U0001F4CB Listing work sessions",
    ToolName.COMPLETE_WORK_ITEM: "\u2713 Completing work session",
    ToolName.STOP_CODING_SESSION: "\u23F9\uFE0F Stopping coding session",
    ToolName.RESTART_CODING_SESSION: "\U0001F504 Restarting coding session",
    ToolName.RECOVER_CODING_SESSION: "\U0001F504 Recovering coding session",
    ToolName.RECOVER_ALL_SESSIONS: "\U0001F504 Recovering all lost sessions",
    ToolName.STOP_ALL_SESSIONS: "\u23F9\uFE0F Stopping all idle sessions",
    ToolName.GET_EXPERIMENT_NOTES: "\U0001F4DD Reading experiment notes",
    ToolName.SAVE_EXPERIMENT_NOTES: "\U0001F4DD Saving experiment notes",
    ToolName.APPEND_LAB_BOOK: "\U0001F4D3 Writing in the lab notebook",
    ToolName.READ_LAB_NOTEBOOK: "\U0001F4D3 Reading the lab notebook",
    ToolName.NOTEBOOK_DIGEST: "\U0001F4CA Generating research digest",
    # Long-lived agents
    ToolName.LIST_AGENT_TEMPLATES: "\U0001F4C4 Listing agent templates",
    ToolName.CREATE_AGENT: "\U0001F916 Conjuring an agent",
    ToolName.LIST_AGENTS: "\U0001F916 Surveying the agents",
    ToolName.START_AGENT_SESSION: "\u25B6\uFE0F Starting agent session",
    ToolName.STOP_AGENT_SESSION: "\u23F9\uFE0F Stopping agent session",
    ToolName.UPDATE_AGENT: "\U0001F4DD Updating agent",
    ToolName.DELETE_AGENT: "\U0001F5D1\uFE0F Dismissing agent",
    # Lab REPL
    ToolName.LAB_REPL: "\U0001F9EA Reasoning in lab sandbox",
    # Thread management
    ToolName.SET_THREAD_NAME: "\U0001F516 Naming the thread",
    # Experimentalist context management
    ToolName.DISTILLATE_REPL: "\U0001F4BB Running isolated REPL",
    ToolName.DISTILLATE_SEARCH: "\U0001F50E Searching literature",
    ToolName.DISTILLATE_NOTE: "\U0001F4DD Writing to scratchpad",
}


# Consistency check: every tool defined in the enum must have a label.
# Catches add-tool-forgot-label drift at import time.
_missing_labels = set(ToolName) - set(TOOL_LABELS.keys())
if _missing_labels:  # pragma: no cover — guarded by test_agents tests
    raise RuntimeError(
        f"ToolName members missing from TOOL_LABELS: "
        f"{sorted(m.value for m in _missing_labels)}"
    )
del _missing_labels


# Tools hidden from Nicolas's MCP schema list — their reads are now served
# by the Lab REPL sandbox (lab.papers.*, lab.experiments.*, lab.notebook.*).
# The dispatch table below still contains them so HTTP routes and other
# callers keep working; we just don't pay the prompt-token tax for schemas
# that duplicate the Lab API.
_HIDDEN_FROM_NICOLAS: frozenset[str] = frozenset({
    ToolName.SEARCH_PAPERS,
    ToolName.GET_PAPER_DETAILS,
    ToolName.GET_RECENT_READS,
    ToolName.GET_QUEUE,
    ToolName.GET_READING_STATS,
    ToolName.SUGGEST_NEXT_READS,
    ToolName.READING_REPORT,
    ToolName.EXPERIMENT_STATUS,
    ToolName.READ_LAB_NOTEBOOK,
    ToolName.NOTEBOOK_DIGEST,
})


def _build_tool_schemas(*, for_nicolas: bool = False) -> list[dict]:
    """Combine paper + experiment tool schemas.

    When ``for_nicolas`` is True, omit schemas that duplicate Lab REPL reads.
    """
    schemas = list(_PAPER_TOOL_SCHEMAS)
    if config.EXPERIMENTS_ENABLED:
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        schemas.extend(EXPERIMENT_TOOL_SCHEMAS)
    if for_nicolas:
        schemas = [s for s in schemas if s.get("name") not in _HIDDEN_FROM_NICOLAS]
    return schemas


TOOL_SCHEMAS = _build_tool_schemas()
NICOLAS_TOOL_SCHEMAS = _build_tool_schemas(for_nicolas=True)


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
    projects = state.experiments
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
        best_count = sum(1 for r in runs.values()
                        if (r.get("decision") or "") == "best")

        # Session status
        sessions = proj.get("sessions", {})
        has_session = any(s.get("status") == "running" for s in sessions.values())
        has_active_run = any(r.get("status") == "running" for r in runs.values())

        parts = [f"{len(runs)} runs"]
        if best_count:
            parts.append(f"{best_count} best")

        line = f"- {proj.get('name', '?')}: {', '.join(parts)}"

        if has_session and has_active_run:
            line += " — running"
        elif has_session:
            line += " — ready"
        else:
            line += " — paused"

        # Linked papers
        linked = proj.get("linked_papers", [])
        if linked:
            line += f" [papers: {len(linked)}]"

        n_new = update_map.get(proj.get("id", ""), 0)
        if n_new:
            line += f" — {n_new} new commit{'s' if n_new != 1 else ''} since last scan"
        lines.append(line)
    return "\n".join(lines) + "\n\n"


def _hf_connected() -> bool:
    from distillate import auth as _auth
    return bool(_auth.hf_token_for("jobs"))


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

    # Queue snapshot for instant answers (no tool call needed)
    queue_lines = []
    if queue:
        # Sort by upload date, newest first
        sorted_q = sorted(queue, key=lambda d: d.get("uploaded_at", ""), reverse=True)
        # Newest 5
        for doc in sorted_q[:5]:
            idx = state.index_of(doc.get("key", ""))
            title = doc.get("title", "?")
            days = (now - datetime.fromisoformat(doc.get("uploaded_at", now.isoformat()).replace("Z", "+00:00"))).days
            queue_lines.append(f"- [{idx}] **{title}** ({days}d ago)")
        # Promoted
        promoted = [d for d in queue if d.get("promoted_at")]
        if promoted:
            queue_lines.append("Promoted:")
            for doc in promoted[:3]:
                idx = state.index_of(doc.get("key", ""))
                queue_lines.append(f"- [{idx}] **{doc.get('title', '?')}**")
        # Oldest
        oldest = sorted_q[-1] if len(sorted_q) > 5 else None
        if oldest:
            idx = state.index_of(oldest.get("key", ""))
            days = (now - datetime.fromisoformat(oldest.get("uploaded_at", now.isoformat()).replace("Z", "+00:00"))).days
            queue_lines.append(f"Oldest: [{idx}] **{oldest.get('title', '?')}** ({days}d)")
    queue_section = "\n".join(queue_lines) if queue_lines else "(empty)"

    if config.EXPERIMENTS_ENABLED:
        role_description = (
            "Your primary purpose is to advance the user\u2019s research agenda \u2014 "
            "you do this by spawning autonomous experiment agents, steering them toward "
            "promising directions, and synthesizing what they discover into a coherent "
            "research trajectory. The paper library, lab notebook, and knowledge base "
            "are instruments in service of that goal.\n\n"
            "You are a Recursive Language Model (RLM): for multi-step reasoning or "
            "questions that span experiments, papers, and the notebook, reach for "
            "lab_repl first. It gives you the full workspace through code \u2014 "
            "lab.experiments, lab.papers, lab.notebook. From inside it, llm_query() "
            "lets you spawn a focused sub-LLM call without polluting your context "
            "window, and delegate() hands off to a specialist. Prefer lab_repl when "
            "the question requires synthesis across two or more sources or needs "
            "computation. Use direct tool calls for simple single-source lookups.\n\n"
            "You also manage their paper library"
            + (
                " \u2014 they read and highlight papers in the Zotero app "
                "(on any device), and Distillate extracts highlights and generates notes."
                if config.is_zotero_reader() else
                " via a Zotero \u2192 reMarkable \u2192 Obsidian workflow."
            )
            + " You have tools to search their library, read highlights and notes, "
            "analyze reading patterns, and synthesize insights across papers."
        )
    else:
        role_description = (
            "You manage their paper library"
            + (
                " \u2014 they read and highlight papers in the Zotero app "
                "(on any device), and Distillate extracts highlights and generates notes."
                if config.is_zotero_reader() else
                " via a Zotero \u2192 reMarkable \u2192 Obsidian workflow."
            )
            + " You have tools to search their library, read highlights and notes, "
            "analyze reading patterns, and synthesize insights across papers."
        )

    is_first_use = len(processed) == 0 and not state.experiments

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
        "You are Nicolas, Chief Alchemist and Orchestrator of the Distillate Lab. "
        + role_description
        + "\n\n"
        + first_use_section
        + f"{_experiments_section(state, updates=experiment_updates)}"
        "## Library\n"
        f"- {len(processed)} papers read, {len(queue)} in queue"
        f", {len(awaiting)} awaiting PDF\n"
        f"- This week: {len(recent)} papers read\n\n"
        "## Queue Snapshot\n"
        f"{queue_section}\n\n"
        "## Recent Reads\n"
        f"{recent_section}\n\n"
        "## Research Interests\n"
        f"{tags_section}\n\n"
        f"{format_past_sessions(past_sessions or [])}"
        "## Personality\n"
        "You're warm, witty, and genuinely curious about the user's research. "
        "The alchemy runs deep \u2014 you transmute raw experimental results "
        "and half-read papers into research insight. A breakthrough might be "
        "\"pure gold\"; a clever technique \"distilled from first principles.\" "
        "Keep the flavor light and natural, not forced. Show enthusiasm when "
        "a result is surprising. Be opinionated \u2014 if a method is clever "
        "or a finding contradicts the prior art, say so.\n\n"
        "## Specialists\n"
        "You have three specialists you can delegate to:\n"
        "- **Librarian** (\U0001F4DA): paper workflows \u2014 highlight extraction, "
        "multi-paper summarization, reading queue, trending search. Delegate "
        "when the user asks about papers and the work involves processing "
        "multiple documents or large highlight sets.\n"
        "- **Knowledge Agent** (\uD83D\uDCDC): notebook & wiki upkeep (coming soon).\n"
        "- **Research Agent** (\uD83D\uDD0D): paper discovery & trending (coming soon).\n\n"
        "## Guidelines\n"
        + (
            "- When asked about experiments, use the experiment tools "
            "(list_experiments, get_experiment_details, compare_runs).\n"
            "- Use manage_session to start, stop, restart, continue, or check "
            "status of experiment sessions.\n"
            "- Use add_experiment or scan_experiment to track a new directory.\n"
            "- Use compare_runs to show what changed between runs.\n"
            "- Use rename_experiment, rename_run, update_experiment, update_goals, "
            "link_paper to manage experiments.\n"
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
            "- Use delete_experiment/delete_run with confirm=false first, then "
            "confirm=true after user approval.\n"
            "- Use replicate_paper when the user wants to reproduce a paper's "
            "results \u2014 it reads the paper, clones its GitHub repo if "
            "available, and scaffolds an experiment.\n"
            "- Use suggest_from_literature to mine recent reads for steering "
            "ideas \u2014 connects paper insights to running experiments.\n"
            "- Use extract_baselines to pull reported metrics from papers "
            "for setting experiment goals.\n"
            "- When a user discusses a paper's technique in an experiment "
            "context, use link_paper to connect them.\n"
            "- Use suggest_from_literature when an experiment is stuck "
            "\u2014 the user's reading may contain relevant techniques.\n"
            "- When concluding a run that implements a paper's idea, set "
            "inspired_by to credit the paper.\n"
            "- Use discover_relevant_papers to find papers in the library "
            "that may be relevant to an experiment's goals or methods.\n"
            if config.EXPERIMENTS_ENABLED else ""
        )
        + (
            "## HuggingFace Jobs (Cloud GPU Compute)\n"
            "HuggingFace is connected — you can submit training scripts to run on cloud GPUs.\n"
            "- Use submit_hf_job to dispatch a training script to a GPU. Key params: "
            "project, script (relative path), gpu_flavor (T4/L4/A100/H200), timeout_minutes, "
            "env (dict of env vars). Returns a job_id.\n"
            "- Right after submitting, call tail_hf_job_logs(job_id) to stream live output "
            "to the terminal — training loss and metrics will appear in real time.\n"
            "- Use check_hf_job(job_id) to poll final status and fetch logs. "
            "Parse METRIC key=value lines from logs to extract results.\n"
            "- Use list_hf_jobs to show recent jobs with status.\n"
            "- Use cancel_hf_job(job_id) to stop a running job.\n"
            "- GPU flavors by cost: T4 ($0.40/hr) → L4 ($0.80/hr) → A100 ($2.50/hr) → H200 ($5/hr).\n"
            "- The Experimentalist agent handles job dispatch autonomously when the experiment "
            "uses hfjobs compute. You can also submit one-off jobs directly.\n"
            "- Users can check jobs from their terminal: `hf jobs ps`, `hf jobs logs <id>`, "
            "`hf jobs cancel <id>` — the `hf` CLI skill is installed globally.\n\n"
            if _hf_connected() else ""
        )
        + "- For quick queue questions (newest, oldest, promoted, count), "
        "use the Queue Snapshot above \u2014 answer instantly, no tool call. "
        "Only call get_queue for full listings or searches.\n"
        "- Show paper [index] numbers for easy reference.\n"
        "- **Bold paper titles** with markdown **title** for readability.\n"
        "- NEVER use markdown tables \u2014 they render poorly. Use bullet "
        "lists instead. Format paper lists as:\n"
        "  - [42] **Paper Title** \u2014 brief note\n"
        "  - [17] **Another Paper** \u2014 brief note\n"
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


def _build_dispatch() -> dict[ToolName, Any]:
    """Build the tool dispatch table, respecting ``EXPERIMENTS_ENABLED``."""
    from distillate import tools

    dispatch: dict[ToolName, Any] = {
        ToolName.SEARCH_PAPERS: tools.search_papers,
        ToolName.GET_PAPER_DETAILS: tools.get_paper_details,
        ToolName.GET_READING_STATS: tools.get_reading_stats,
        ToolName.GET_QUEUE: tools.get_queue,
        ToolName.GET_RECENT_READS: tools.get_recent_reads,
        ToolName.SUGGEST_NEXT_READS: tools.suggest_next_reads,
        ToolName.SYNTHESIZE_ACROSS_PAPERS: tools.synthesize_across_papers,
        ToolName.RUN_SYNC: tools.run_sync,
        ToolName.REFRESH_METADATA: tools.refresh_metadata,
        ToolName.REPROCESS_PAPER: tools.reprocess_paper,
        ToolName.PROMOTE_PAPERS: tools.promote_papers,
        ToolName.GET_TRENDING_PAPERS: tools.get_trending_papers,
        ToolName.SEARCH_HF_MODELS: tools.search_hf_models,
        ToolName.SEARCH_HF_DATASETS: tools.search_hf_datasets,
        ToolName.FIND_PAPER_ASSOCIATIONS: tools.find_paper_associations,
        ToolName.ADD_PAPER_TO_ZOTERO: tools.add_paper_to_zotero,
        ToolName.DELETE_PAPER: tools.delete_paper,
    }

    if config.EXPERIMENTS_ENABLED:
        from distillate import experiment_tools as et
        dispatch.update({
            ToolName.LIST_EXPERIMENTS: et.list_experiments,
            ToolName.GET_EXPERIMENT_DETAILS: et.get_experiment_details,
            ToolName.COMPARE_RUNS: et.compare_runs,
            ToolName.SCAN_EXPERIMENT: et.scan_project_tool,
            ToolName.GET_EXPERIMENT_NOTEBOOK: et.get_experiment_notebook,
            ToolName.ADD_EXPERIMENT: et.add_project_tool,
            ToolName.RENAME_EXPERIMENT: et.rename_experiment_tool,
            ToolName.RENAME_RUN: et.rename_run_tool,
            ToolName.DELETE_EXPERIMENT: et.delete_experiment_tool,
            ToolName.DELETE_RUN: et.delete_run_tool,
            ToolName.UPDATE_EXPERIMENT: et.update_project_tool,
            ToolName.LINK_PAPER: et.link_paper_tool,
            ToolName.UPDATE_GOALS: et.update_goals_tool,
            ToolName.GET_RUN_DETAILS: et.get_run_details_tool,
            ToolName.ANNOTATE_RUN: et.annotate_run_tool,
            ToolName.LAUNCH_EXPERIMENT: et.launch_experiment_tool,
            ToolName.EXPERIMENT_STATUS: et.experiment_status_tool,
            ToolName.STOP_EXPERIMENT: et.stop_experiment_tool,
            ToolName.INIT_EXPERIMENT: et.init_experiment_tool,
            ToolName.CONTINUE_EXPERIMENT: et.continue_experiment_tool,
            ToolName.SWEEP_EXPERIMENT: et.sweep_experiment_tool,
            ToolName.STEER_EXPERIMENT: et.steer_experiment_tool,
            ToolName.ASK_EXPERIMENTALIST: et.ask_experimentalist_tool,
            ToolName.COMPARE_EXPERIMENTS: et.compare_experiments_tool,
            ToolName.QUEUE_SESSIONS: et.queue_sessions_tool,
            ToolName.LIST_TEMPLATES: et.list_templates_tool,
            ToolName.SAVE_TEMPLATE: et.save_template_tool,
            ToolName.CREATE_GITHUB_REPO: et.create_github_repo_tool,
            ToolName.READING_REPORT: et.reading_report_tool,
            ToolName.MANAGE_SESSION: et.manage_session_tool,
            ToolName.REPLICATE_PAPER: et.replicate_paper,
            ToolName.SUGGEST_FROM_LITERATURE: et.suggest_from_literature,
            ToolName.EXTRACT_BASELINES: et.extract_baselines,
            ToolName.SAVE_ENRICHMENT: et.save_enrichment,
            ToolName.START_RUN: et.start_run,
            ToolName.CONCLUDE_RUN: et.conclude_run,
            ToolName.PURGE_HOOK_RUNS: et.purge_hook_runs_tool,
            ToolName.DISCOVER_RELEVANT_PAPERS: et.discover_relevant_papers,
            # HuggingFace Jobs
            ToolName.SUBMIT_HF_JOB: et.submit_hf_job_tool,
            ToolName.CHECK_HF_JOB: et.check_hf_job_tool,
            ToolName.TAIL_HF_JOB_LOGS: et.tail_hf_job_logs_tool,
            ToolName.CANCEL_HF_JOB: et.cancel_hf_job_tool,
            ToolName.LIST_HF_JOBS: et.list_hf_jobs_tool,
            # Workspace projects
            ToolName.CREATE_WORKSPACE: et.create_workspace_tool,
            ToolName.LIST_WORKSPACES: et.list_workspaces_tool,
            ToolName.GET_WORKSPACE: et.get_workspace_tool,
            ToolName.ADD_WORKSPACE_REPO: et.add_workspace_repo_tool,
            ToolName.LAUNCH_CODING_SESSION: et.launch_coding_session_tool,
            ToolName.LAUNCH_WRITING_SESSION: et.launch_writing_session_tool,
            ToolName.LAUNCH_SURVEY_SESSION: et.launch_survey_session_tool,
            ToolName.CREATE_WORK_ITEM: et.create_work_item_tool,
            ToolName.LIST_WORK_ITEMS: et.list_work_items_tool,
            ToolName.COMPLETE_WORK_ITEM: et.complete_work_item_tool,
            ToolName.STOP_CODING_SESSION: et.stop_coding_session_tool,
            ToolName.RESTART_CODING_SESSION: et.restart_coding_session_tool,
            ToolName.RECOVER_CODING_SESSION: et.recover_coding_session_tool,
            ToolName.RECOVER_ALL_SESSIONS: et.recover_all_sessions_tool,
            ToolName.STOP_ALL_SESSIONS: et.stop_all_sessions_tool,
            # Project notes + lab notebook
            ToolName.GET_EXPERIMENT_NOTES: et.get_workspace_notes_tool,
            ToolName.SAVE_EXPERIMENT_NOTES: et.save_workspace_notes_tool,
            ToolName.APPEND_LAB_BOOK: et.append_lab_book_tool,
            ToolName.READ_LAB_NOTEBOOK: et.read_lab_notebook_tool,
            ToolName.NOTEBOOK_DIGEST: et.notebook_digest_tool,
            # Long-lived agents
            ToolName.LIST_AGENT_TEMPLATES: et.list_agent_templates_tool,
            ToolName.CREATE_AGENT: et.create_agent_tool,
            ToolName.LIST_AGENTS: et.list_agents_tool,
            ToolName.START_AGENT_SESSION: et.start_agent_session_tool,
            ToolName.STOP_AGENT_SESSION: et.stop_agent_session_tool,
            ToolName.UPDATE_AGENT: et.update_agent_tool,
            ToolName.DELETE_AGENT: et.delete_agent_tool,
            # Lab REPL
            ToolName.LAB_REPL: et.lab_repl_tool,
            ToolName.SET_THREAD_NAME: et.set_thread_name_tool,
            # Experimentalist context management
            ToolName.DISTILLATE_REPL: et.distillate_repl_tool,
            ToolName.DISTILLATE_SEARCH: et.distillate_search_tool,
            ToolName.DISTILLATE_NOTE: et.distillate_note_tool,
        })

    return dispatch


def execute_tool(name: str, input_data: dict, state: State) -> dict:
    """Execute a tool and return the result dict.

    ``name`` comes from the MCP protocol or the Claude SDK — a raw string
    from the model. We validate it against ``ToolName`` before dispatching
    so that typos and unknown tools produce a clean error.
    """
    try:
        tool = ToolName(name)
    except ValueError:
        return {"error": f"Unknown tool: {name}"}

    if tool in _EXPERIMENT_TOOLS and not config.EXPERIMENTS_ENABLED:
        return {"error": f"Experiment tools disabled: {name}"}

    dispatch = _build_dispatch()
    fn = dispatch.get(tool)
    if fn is None:
        return {"error": f"Tool not registered: {name}"}

    try:
        return fn(state=state, **input_data)
    except Exception as e:
        log.exception("Tool '%s' failed", name)
        return {"error": str(e)}
