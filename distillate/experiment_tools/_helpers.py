"""Shared helpers for experiment tools.

Internal module — not part of the public API. Imported by tool sub-modules.
"""

import logging
import re
from pathlib import Path as _Path
from typing import Any

log = logging.getLogger(__name__)

# LLMs sometimes produce invalid JSON escape sequences like \! or \'
_INVALID_ESCAPE_RE = re.compile(r'\\([^"\\/bfnrtu])')


def _sanitize_llm_text(s: str) -> str:
    """Strip invalid JSON escapes from LLM-generated text."""
    return _INVALID_ESCAPE_RE.sub(r'\1', s) if isinstance(s, str) else s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_summary(run: dict) -> dict:
    """Build a concise run summary for tool results."""
    results = run.get("results", {})
    # Pick best metric for display
    key_metric = ""
    for k in ("accuracy", "exact_match", "test_accuracy", "val_accuracy",
              "best_val_acc", "f1", "loss"):
        if k in results:
            key_metric = f"{k}={results[k]}"
            break
    if not key_metric and results:
        k, v = next(iter(results.items()))
        if isinstance(v, (int, float)):
            key_metric = f"{k}={v}"

    summary = {
        "id": run.get("id", ""),
        "name": run.get("name", ""),
        "status": run.get("status", ""),
        "key_metric": key_metric,
        "duration_minutes": run.get("duration_minutes", 0),
        "tags": run.get("tags", []),
    }

    # Live elapsed for running runs
    if run.get("status") == "running" and run.get("started_at"):
        try:
            from datetime import datetime as _dt, timezone as _tz
            st = _dt.fromisoformat(run["started_at"].replace("Z", "+00:00"))
            summary["elapsed_seconds"] = round((_dt.now(_tz.utc) - st).total_seconds())
        except (ValueError, TypeError):
            pass

    return summary


def _run_summary_full(run: dict, run_number: int = 0, run_suffix: str = "") -> dict:
    """Build a full run summary with all fields (for server/desktop API).

    Superset of ``_run_summary`` — includes hyperparameters, hypothesis,
    reasoning, baseline comparison, etc.
    """
    results = run.get("results", {})
    key_metric = ""
    for k in ("accuracy", "exact_match", "test_accuracy", "val_accuracy",
              "best_val_acc", "f1", "loss"):
        if k in results:
            key_metric = f"{k}={results[k]}"
            break
    if not key_metric and results:
        k, v = next(iter(results.items()))
        if isinstance(v, (int, float)):
            key_metric = f"{k}={v}"

    # Always include core fields
    summary = {
        "id": run.get("id", ""),
        "run_number": run_number,
        "status": run.get("status", ""),
        "decision": run.get("decision", ""),
        "key_metric": key_metric,
        "results": {k: v for k, v in results.items() if isinstance(v, (int, float))},
        "started_at": run.get("started_at", ""),
    }
    # Only include non-empty optional fields
    for field in ("name", "run_suffix", "description", "hypothesis",
                   "prediction", "outcome", "reasoning", "agent_reasoning",
                   "predicted_metric", "predicted_direction",
                   "rationale", "verdict", "belief_update"):
        val = run.get(field, "") if field != "run_suffix" else run_suffix
        if val:
            summary[field] = val
    # Numeric pre-registration fields (0 is a valid value, only skip None)
    for field in ("predicted_value", "confidence",
                  "prediction_error", "prediction_error_pct"):
        val = run.get(field)
        if val is not None:
            summary[field] = val
    if run.get("hyperparameters"):
        summary["hyperparameters"] = run["hyperparameters"]
    if run.get("baseline_comparison") is not None:
        summary["baseline_comparison"] = run["baseline_comparison"]
    if run.get("duration_minutes"):
        summary["duration_minutes"] = run["duration_minutes"]
    if run.get("duration_seconds"):
        summary["duration_seconds"] = run["duration_seconds"]
    if run.get("tags"):
        summary["tags"] = run["tags"]
    if run.get("checkpoint_url"):
        summary["checkpoint_url"] = run["checkpoint_url"]
    return summary


def _resolve_project(state, identifier: str) -> tuple[dict | None, dict | None]:
    """Resolve a project by identifier, returning (project, error_dict).

    Returns (project_dict, None) on success, or (None, error_dict) on
    failure (not found or ambiguous match).
    """
    matches = state.find_all_experiments(identifier)
    if not matches:
        return None, {"error": f"No project found matching '{identifier}'"}
    if len(matches) > 1:
        names = [m.get("name", m.get("id", "")) for m in matches[:5]]
        return None, {
            "error": f"Multiple projects match '{identifier}'. Be more specific.",
            "matches": names,
        }
    return matches[0], None


def _compute_time_info(proj: dict) -> dict:
    """Compute live time budget info for a project.

    Reads .distillate/budget.json and combines with run/session state
    to return elapsed, remaining, and budget fields.
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    from pathlib import Path as _Path

    info: dict = {}
    proj_path = proj.get("path", "")
    if not proj_path:
        return info

    # Read budget.json
    budget_path = _Path(proj_path) / ".distillate" / "budget.json"
    budget: dict = {}
    try:
        budget = _json.loads(budget_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        pass

    now = _dt.now(_tz.utc)
    run_budget = budget.get("run_budget_seconds") or (proj.get("duration_minutes") or 5) * 60
    info["run_budget_seconds"] = run_budget

    # Session budget info
    session_budget = budget.get("session_budget_seconds") or proj.get("session_budget_seconds")
    if session_budget:
        info["session_budget_seconds"] = session_budget
        session_started = budget.get("session_started_at")
        if session_started:
            try:
                st = _dt.fromisoformat(session_started.replace("Z", "+00:00"))
                elapsed = (now - st).total_seconds()
                info["session_elapsed_seconds"] = round(elapsed)
                info["session_remaining_seconds"] = max(0, round(session_budget - elapsed))
            except (ValueError, TypeError):
                pass

    # Find running run for live elapsed
    runs = proj.get("runs", {})
    for run in runs.values():
        if run.get("status") == "running":
            started_at = run.get("started_at", "")
            if started_at:
                try:
                    st = _dt.fromisoformat(started_at.replace("Z", "+00:00"))
                    elapsed = (now - st).total_seconds()
                    info["run_elapsed_seconds"] = round(elapsed)
                    info["run_remaining_seconds"] = max(0, round(run_budget - elapsed))
                except (ValueError, TypeError):
                    pass
            break

    # Total training time across completed runs
    total = sum(r.get("duration_seconds", 0) for r in runs.values()
                if r.get("status") not in ("running", ""))
    if total:
        info["total_training_seconds"] = round(total)

    return info


def _find_run(runs: dict, query: str) -> Any:
    """Find a run by id or name substring. Returns first match."""
    if not query:
        return None
    if query in runs:
        return runs[query]
    query_lower = query.lower()
    for run in runs.values():
        if query_lower in run.get("name", "").lower():
            return run
        if query_lower in run.get("id", "").lower():
            return run
    return None


def _find_all_runs(runs: dict, query: str) -> list[dict]:
    """Find all runs matching query (id or name substring)."""
    if not query:
        return []
    # Exact id match is always unique
    if query in runs:
        return [runs[query]]
    query_lower = query.lower()
    matches = []
    for run in runs.values():
        if (query_lower in run.get("name", "").lower()
                or query_lower in run.get("id", "").lower()):
            matches.append(run)
    return matches


def _resolve_run(runs: dict, query: str, project_name: str) -> tuple[dict | None, dict | None]:
    """Resolve a run by query, returning (run, error_dict)."""
    matches = _find_all_runs(runs, query)
    if not matches:
        return None, {"error": f"No run found matching '{query}' in project '{project_name}'"}
    if len(matches) > 1:
        names = [m.get("name", m.get("id", "")) for m in matches[:5]]
        return None, {
            "error": f"Multiple runs match '{query}'. Be more specific.",
            "matches": names,
        }
    return matches[0], None


def _regen_notebook(proj: dict) -> None:
    """Regenerate the Obsidian lab notebook (MD + HTML) for a project."""
    from pathlib import Path as _Path

    from distillate.experiments import (
        generate_html_notebook, generate_notebook, load_enrichment_cache,
    )
    from distillate.obsidian import (
        write_experiment_html_notebook, write_experiment_notebook,
    )

    proj_path = proj.get("path", "")
    enrichment = None
    if proj_path:
        enrichment = load_enrichment_cache(_Path(proj_path))
        if enrichment:
            enrichment = enrichment.get("enrichment", enrichment)
    notebook_md = generate_notebook(proj, enrichment=enrichment)
    write_experiment_notebook(proj, notebook_md)
    notebook_html = generate_html_notebook(proj, enrichment=enrichment)
    write_experiment_html_notebook(proj, notebook_html)


def _remove_notebook(experiment_id: str) -> None:
    """Remove the Obsidian notebook file for a project."""
    from distillate import config

    vault = config.OBSIDIAN_VAULT_PATH
    output = config.OUTPUT_PATH if not vault else ""
    base = vault or output
    if not base:
        return

    from pathlib import Path as _Path
    folder = config.OBSIDIAN_PAPERS_FOLDER if vault else ""
    nb_dir = _Path(base) / folder / "Projects" if folder else _Path(base) / "Projects"

    # Remove main notebook and any section notebooks
    for md_file in nb_dir.glob(f"{experiment_id}*.md"):
        md_file.unlink(missing_ok=True)

    # Remove HTML notebook
    html_dir = nb_dir / "html"
    if html_dir.is_dir():
        html_file = html_dir / f"{experiment_id}.html"
        html_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Keyword extraction for paper-experiment matching
# ---------------------------------------------------------------------------

EXPERIMENT_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "is", "it", "as", "be", "was",
    "are", "were", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "shall", "should", "may", "might", "can",
    "could", "not", "no", "so", "if", "then", "than", "that", "this",
    "these", "those", "which", "what", "who", "how", "when", "where",
    "why", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "only", "same", "very", "just", "also",
    "now", "new", "use", "using", "used", "run", "try", "tried",
})


def extract_experiment_keywords(proj: dict, *, max_keywords: int = 30) -> list[str]:
    """Extract search keywords from a project's context.

    Gathers text from the project's description, goal metrics, tags, and
    latest run reasoning (from ``runs.jsonl``).  Returns a deduplicated
    list of lowercase keyword tokens (3+ chars, no stopwords).
    """
    import json as _json

    context_parts: list[str] = []
    if proj.get("description"):
        context_parts.append(proj["description"])
    for goal in proj.get("goals", []):
        if goal.get("metric"):
            context_parts.append(goal["metric"])
    if proj.get("tags"):
        context_parts.extend(proj["tags"])

    # Pull latest learnings from runs.jsonl
    proj_path = proj.get("path", "")
    if proj_path:
        runs_jsonl = _Path(proj_path) / ".distillate" / "runs.jsonl"
        if runs_jsonl.exists():
            try:
                lines = runs_jsonl.read_text(encoding="utf-8").splitlines()
                for line in reversed(lines[-20:]):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rr = _json.loads(line)
                        if rr.get("reasoning"):
                            context_parts.append(rr["reasoning"])
                            break  # just the latest
                    except _json.JSONDecodeError:
                        pass
            except OSError:
                pass

    if not context_parts:
        return []

    raw_text = " ".join(context_parts).lower()
    tokens = re.findall(r"[a-z][a-z0-9_]{2,}", raw_text)
    return list(dict.fromkeys(t for t in tokens if t not in EXPERIMENT_STOPWORDS))[:max_keywords]
