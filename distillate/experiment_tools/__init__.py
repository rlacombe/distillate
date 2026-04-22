"""Agent tool definitions for ML experiment tracking.

Each tool is a pure function that takes `state` as a keyword argument
(injected by the dispatcher, invisible to Claude) and returns a
JSON-serializable dict. Same pattern as tools.py.

This package splits tools by domain. The facade re-exports everything
so that ``from distillate.experiment_tools import X`` continues to work.
"""

# --- Aggregate schemas from all sub-modules -----------------------------------

from .experiment_crud import SCHEMAS as _PROJECT_SCHEMAS
from .session_tools import SCHEMAS as _SESSION_SCHEMAS
from .init_tools import SCHEMAS as _INIT_SCHEMAS
from .run_tools import SCHEMAS as _PAPER_BRIDGE_SCHEMAS
from .hf_tools import SCHEMAS as _HF_SCHEMAS
from .workspace_tools import SCHEMAS as _WORKSPACE_SCHEMAS
from .agent_tools import SCHEMAS as _AGENT_SCHEMAS
from .repl_tools import SCHEMAS as _REPL_SCHEMAS
from .xp_tools import SCHEMAS as _XP_SCHEMAS

EXPERIMENT_TOOL_SCHEMAS = (
    _PROJECT_SCHEMAS
    + _SESSION_SCHEMAS
    + _INIT_SCHEMAS
    + _PAPER_BRIDGE_SCHEMAS
    + _HF_SCHEMAS
    + _WORKSPACE_SCHEMAS
    + _AGENT_SCHEMAS
    + _REPL_SCHEMAS
    + _XP_SCHEMAS
)

# --- Re-export helpers (used by tests, server, other modules) -----------------

from ._helpers import (  # noqa: F401
    _compute_time_info,
    _find_all_runs,
    _find_run,
    _resolve_project,
    _resolve_run,
    _regen_notebook,
    _run_summary,
    _run_summary_full,
    _sanitize_llm_text,
)

# --- Re-export all public tool functions --------------------------------------

from .experiment_crud import (  # noqa: F401
    list_experiments,
    get_experiment_details,
    compare_runs,
    _discover_git_repos,
    scan_project_tool,
    get_experiment_notebook,
    add_project_tool,
    rename_experiment_tool,
    rename_run_tool,
    delete_experiment_tool,
    delete_run_tool,
    update_project_tool,
    link_paper_tool,
    update_goals_tool,
    get_run_details_tool,
    annotate_run_tool,
)

from .session_tools import (  # noqa: F401
    launch_experiment_tool,
    experiment_status_tool,
    stop_experiment_tool,
    sweep_experiment_tool,
    continue_experiment_tool,
    steer_experiment_tool,
    ask_experimentalist_tool,
    manage_session_tool,
    compare_experiments_tool,
    queue_sessions_tool,
    list_templates_tool,
    save_template_tool,
    create_github_repo_tool,
    reading_report_tool,
)

from .init_tools import (  # noqa: F401
    _parse_goals_from_text,
    init_experiment_tool,
)

from .run_tools import (  # noqa: F401
    replicate_paper,
    suggest_from_literature,
    extract_baselines,
    save_enrichment,
    start_run,
    conclude_run,
    discover_relevant_papers,
    purge_hook_runs_tool,
)

from .hf_tools import (  # noqa: F401
    submit_hf_job_tool,
    check_hf_job_tool,
    tail_hf_job_logs_tool,
    cancel_hf_job_tool,
    list_hf_jobs_tool,
)

from .workspace_tools import (  # noqa: F401
    create_workspace_tool,
    agent_status_tool,
    reorder_sessions_tool,
    list_workspaces_tool,
    get_workspace_tool,
    add_workspace_repo_tool,
    launch_coding_session_tool,
    launch_writing_session_tool,
    launch_survey_session_tool,
    create_work_item_tool,
    list_work_items_tool,
    complete_work_item_tool,
    stop_coding_session_tool,
    complete_coding_session_tool,
    discard_session_wrapup_tool,
    save_session_summary_tool,
    restart_coding_session_tool,
    recover_coding_session_tool,
    recover_all_sessions_tool,
    stop_all_sessions_tool,
    get_workspace_notes_tool,
    save_workspace_notes_tool,
    append_lab_book_tool,
    read_lab_notebook_tool,
    notebook_digest_tool,
)

from .agent_tools import (  # noqa: F401
    list_agent_templates_tool,
    create_agent_tool,
    list_agents_tool,
    get_agent_details_tool,
    start_agent_session_tool,
    stop_agent_session_tool,
    update_agent_tool,
    delete_agent_tool,
)

from .repl_tools import (  # noqa: F401
    lab_repl_tool,
    set_thread_name_tool,
)

from .xp_tools import (  # noqa: F401
    distillate_repl_tool,
    distillate_search_tool,
    distillate_note_tool,
)
