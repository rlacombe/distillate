"""Agent tool definitions for ML experiment tracking.

Each tool is a pure function that takes `state` as a keyword argument
(injected by the dispatcher, invisible to Claude) and returns a
JSON-serializable dict. Same pattern as tools.py.
"""

import logging
from typing import Any

log = logging.getLogger(__name__)


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

    return {
        "id": run.get("id", ""),
        "name": run.get("name", ""),
        "status": run.get("status", ""),
        "key_metric": key_metric,
        "duration_minutes": run.get("duration_minutes", 0),
        "tags": run.get("tags", []),
    }


# ---------------------------------------------------------------------------
# Tool schemas (sent to Claude)
# ---------------------------------------------------------------------------

EXPERIMENT_TOOL_SCHEMAS = [
    {
        "name": "list_projects",
        "description": (
            "List all tracked ML experiment projects with their status, "
            "run count, and last activity. Use when the user asks about "
            "their experiments or projects."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_project_details",
        "description": (
            "Get full details for an ML project including all experiment "
            "runs with metrics, hyperparameters, linked papers, and goals. "
            "Use when the user asks about a specific project or experiment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "compare_runs",
        "description": (
            "Compare two experiment runs within a project. Shows parameter "
            "deltas, metric improvements/regressions, and what changed. "
            "Use when the user asks to compare experiments or see what changed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "run_a": {
                    "type": "string",
                    "description": "First run id or name (baseline)",
                },
                "run_b": {
                    "type": "string",
                    "description": "Second run id or name (comparison)",
                },
            },
            "required": ["project", "run_a", "run_b"],
        },
    },
    {
        "name": "scan_project",
        "description": (
            "Scan a directory to discover ML experiments. Finds training "
            "logs, configs, checkpoints, and results, then reconstructs "
            "experiment runs. Works with any directory (git optional). "
            "Use when the user wants to track a new project or rescan "
            "an existing one. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the project directory",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_experiment_notebook",
        "description": (
            "Get the generated lab notebook for a project. Shows the full "
            "experiment timeline, run details, and diffs between runs. "
            "If the notebook doesn't exist yet, it will be generated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "section": {
                    "type": "string",
                    "description": "Notebook section (default: 'main')",
                },
            },
            "required": ["project"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def list_projects(*, state) -> dict:
    """List all tracked ML projects."""
    projects = state.projects
    if not projects:
        return {"projects": [], "total": 0, "message": "No projects tracked yet."}

    results = []
    for proj_id, proj in projects.items():
        runs = proj.get("runs", {})
        completed = sum(1 for r in runs.values() if r.get("status") == "completed")
        running = sum(1 for r in runs.values() if r.get("status") == "running")

        results.append({
            "index": state.project_index_of(proj_id),
            "id": proj_id,
            "name": proj.get("name", ""),
            "status": proj.get("status", ""),
            "path": proj.get("path", ""),
            "run_count": len(runs),
            "completed_runs": completed,
            "running_runs": running,
            "tags": proj.get("tags", []),
            "last_scanned_at": proj.get("last_scanned_at", ""),
            "linked_papers": proj.get("linked_papers", []),
        })

    return {"projects": results, "total": len(results)}


def get_project_details(*, state, identifier: str) -> dict:
    """Get full details for a project including all runs."""
    proj = state.find_project(identifier)
    if not proj:
        return {"found": False, "error": f"No project found matching '{identifier}'"}

    runs = proj.get("runs", {})
    run_summaries = [_run_summary(r) for r in runs.values()]
    # Sort by completion date
    run_summaries.sort(
        key=lambda r: r.get("completed_at", "") or "", reverse=True
    )

    return {
        "found": True,
        "project": {
            "id": proj.get("id", ""),
            "name": proj.get("name", ""),
            "path": proj.get("path", ""),
            "description": proj.get("description", ""),
            "status": proj.get("status", ""),
            "tags": proj.get("tags", []),
            "goals": proj.get("goals", []),
            "linked_papers": proj.get("linked_papers", []),
            "added_at": proj.get("added_at", ""),
            "last_scanned_at": proj.get("last_scanned_at", ""),
            "notebook_sections": proj.get("notebook_sections", ["main"]),
        },
        "runs": run_summaries,
        "total_runs": len(runs),
    }


def compare_runs(*, state, project: str, run_a: str, run_b: str) -> dict:
    """Compare two experiment runs within a project."""
    from distillate.experiments import diff_runs

    proj = state.find_project(project)
    if not proj:
        return {"error": f"No project found matching '{project}'"}

    runs = proj.get("runs", {})

    # Find run_a
    a = _find_run(runs, run_a)
    if not a:
        return {"error": f"No run found matching '{run_a}' in project '{proj.get('name', '')}'"}

    # Find run_b
    b = _find_run(runs, run_b)
    if not b:
        return {"error": f"No run found matching '{run_b}' in project '{proj.get('name', '')}'"}

    return diff_runs(a, b)


def scan_project_tool(*, state, path: str) -> dict:
    """Scan a git repo and track it as a project."""
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    from distillate.experiments import (
        enrich_runs_with_llm, generate_notebook, scan_project, slugify,
    )
    from distillate.obsidian import write_experiment_notebook

    repo_path = _Path(path).expanduser().resolve()
    if not repo_path.is_dir():
        return {"success": False, "error": f"Directory not found: {path}"}

    result = scan_project(repo_path)
    if "error" in result:
        return {"success": False, "error": result["error"]}

    project_id = slugify(result["name"])

    # Add or update in state
    if state.has_project(project_id):
        # Merge new runs into existing project
        existing = state.get_project(project_id)
        existing_names = {r["name"] for r in existing.get("runs", {}).values()}
        new_runs = 0
        for run_id, run_data in result.get("runs", {}).items():
            if run_data["name"] not in existing_names:
                state.add_run(project_id, run_id, run_data)
                new_runs += 1
        state.update_project(
            project_id,
            last_scanned_at=datetime.now(timezone.utc).isoformat(),
            last_commit_hash=result["head_hash"],
        )
        state.save()
        message = f"Rescanned '{result['name']}': found {new_runs} new run(s)."
    else:
        state.add_project(
            project_id=project_id,
            name=result["name"],
            path=str(repo_path),
        )
        for run_id, run_data in result.get("runs", {}).items():
            state.add_run(project_id, run_id, run_data)
        state.update_project(
            project_id,
            last_scanned_at=datetime.now(timezone.utc).isoformat(),
            last_commit_hash=result["head_hash"],
        )
        state.save()
        message = (
            f"Now tracking '{result['name']}' with "
            f"{len(result.get('runs', {}))} discovered run(s)."
        )

    # LLM enrichment + generate notebook
    proj = state.get_project(project_id)
    if proj:
        enrichment = enrich_runs_with_llm(
            proj.get("runs", {}), proj.get("name", ""), repo_path,
        )
        notebook_md = generate_notebook(proj, enrichment=enrichment)
        write_experiment_notebook(proj, notebook_md)

    return {
        "success": True,
        "project_id": project_id,
        "name": result["name"],
        "runs_discovered": len(result.get("runs", {})),
        "message": message,
    }


def get_experiment_notebook(*, state, project: str, section: str = "main") -> dict:
    """Get or regenerate the lab notebook for a project."""
    from pathlib import Path as _Path

    from distillate.experiments import enrich_runs_with_llm, generate_notebook
    from distillate.obsidian import write_experiment_notebook

    proj = state.find_project(project)
    if not proj:
        return {"error": f"No project found matching '{project}'"}

    enrichment = None
    proj_path = proj.get("path", "")
    if proj_path:
        enrichment = enrich_runs_with_llm(
            proj.get("runs", {}), proj.get("name", ""), _Path(proj_path),
        )

    notebook_md = generate_notebook(proj, section=section, enrichment=enrichment)

    # Write to disk
    write_experiment_notebook(proj, notebook_md, section=section)

    # Truncate for tool result
    if len(notebook_md) > 8000:
        notebook_md = notebook_md[:8000] + "\n\n... (truncated)"

    return {
        "project": proj.get("name", ""),
        "section": section,
        "notebook": notebook_md,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_run(runs: dict, query: str) -> Any:
    """Find a run by id or name substring."""
    if not query:
        return None
    # Exact id
    if query in runs:
        return runs[query]
    # Name substring
    query_lower = query.lower()
    for run in runs.values():
        if query_lower in run.get("name", "").lower():
            return run
        if query_lower in run.get("id", "").lower():
            return run
    return None
