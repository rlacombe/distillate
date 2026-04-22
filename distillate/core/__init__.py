"""Core primitives: projects, experiments, metrics, state, configuration.

This package provides the clean import path for Distillate's core functionality.
Currently a facade that re-exports from the flat module layout; logic will be
extracted here incrementally.
"""

# --- Experiments & project scanning ---
from distillate.experiments import (
    backfill_runs_from_events,
    check_experiments_for_updates,
    classify_metric,
    detect_ml_repos,
    detect_primary_metric,
    diff_runs,
    enrich_runs_with_llm,
    extract_runs_from_claude_logs,
    generate_export_chart,
    generate_html_notebook,
    generate_notebook,
    infer_key_metric_name,
    ingest_runs,
    install_git_hook,
    load_enrichment_cache,
    scan_experiment,
    slugify,
    update_experiment,
    watch_experiment_artifacts,
)

# --- Persistent state ---
from distillate.state import State, acquire_lock, release_lock

# --- Configuration ---
from distillate.config import ensure_loaded, is_zotero_reader, save_to_env, setup_logging

__all__ = [
    # experiments
    "backfill_runs_from_events",
    "check_experiments_for_updates",
    "classify_metric",
    "detect_ml_repos",
    "detect_primary_metric",
    "diff_runs",
    "enrich_runs_with_llm",
    "extract_runs_from_claude_logs",
    "generate_export_chart",
    "generate_html_notebook",
    "generate_notebook",
    "infer_key_metric_name",
    "ingest_runs",
    "install_git_hook",
    "load_enrichment_cache",
    "scan_experiment",
    "slugify",
    "update_experiment",
    "watch_experiment_artifacts",
    # state
    "State",
    "acquire_lock",
    "release_lock",
    # config
    "ensure_loaded",
    "is_zotero_reader",
    "save_to_env",
    "setup_logging",
]
