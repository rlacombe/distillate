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


def _resolve_project(state, identifier: str) -> tuple[dict | None, dict | None]:
    """Resolve a project by identifier, returning (project, error_dict).

    Returns (project_dict, None) on success, or (None, error_dict) on
    failure (not found or ambiguous match).
    """
    matches = state.find_all_projects(identifier)
    if not matches:
        return None, {"error": f"No project found matching '{identifier}'"}
    if len(matches) > 1:
        names = [m.get("name", m.get("id", "")) for m in matches[:5]]
        return None, {
            "error": f"Multiple projects match '{identifier}'. Be more specific.",
            "matches": names,
        }
    return matches[0], None


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
        enrich_runs_with_llm, generate_html_notebook, generate_notebook,
    )
    from distillate.obsidian import (
        write_experiment_html_notebook, write_experiment_notebook,
    )

    proj_path = proj.get("path", "")
    enrichment = None
    if proj_path:
        enrichment = enrich_runs_with_llm(
            proj.get("runs", {}), proj.get("name", ""), _Path(proj_path),
        )
    notebook_md = generate_notebook(proj, enrichment=enrichment)
    write_experiment_notebook(proj, notebook_md)
    notebook_html = generate_html_notebook(proj, enrichment=enrichment)
    write_experiment_html_notebook(proj, notebook_html)


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
    # -- CRUD tools --
    {
        "name": "add_project",
        "description": (
            "Add a directory as a tracked ML project and scan it for "
            "experiments. Superset of scan_project — also lets you set a "
            "custom name, description, and tags. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the project directory",
                },
                "name": {
                    "type": "string",
                    "description": "Display name (default: directory name, title-cased)",
                },
                "description": {
                    "type": "string",
                    "description": "Short description of the project",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization (e.g. 'nlp', 'vision')",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "rename_project",
        "description": (
            "Rename a tracked ML project. Updates the display name, slug, "
            "and Obsidian notebook filename. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "new_name": {
                    "type": "string",
                    "description": "The new display name for the project",
                },
            },
            "required": ["identifier", "new_name"],
        },
    },
    {
        "name": "rename_run",
        "description": (
            "Rename an experiment run within a project. Updates the "
            "display name and regenerates the lab notebook."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "run": {
                    "type": "string",
                    "description": "Run id or name substring",
                },
                "new_name": {
                    "type": "string",
                    "description": "The new display name for the run",
                },
            },
            "required": ["project", "run", "new_name"],
        },
    },
    {
        "name": "delete_project",
        "description": (
            "Delete a tracked ML project and its Obsidian notebook. "
            "Two-phase: call with confirm=false to preview, then "
            "confirm=true to execute. Does NOT delete source files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Set to true to actually delete (default: false = preview)",
                },
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "delete_run",
        "description": (
            "Delete an experiment run from a project. "
            "Two-phase: call with confirm=false to preview, then "
            "confirm=true to execute. Does NOT delete source files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "run": {
                    "type": "string",
                    "description": "Run id or name substring",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Set to true to actually delete (default: false = preview)",
                },
            },
            "required": ["project", "run"],
        },
    },
    {
        "name": "update_project",
        "description": (
            "Update a project's description, tags, or status. "
            "Only provided fields are changed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "description": {
                    "type": "string",
                    "description": "New project description",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New tags list (replaces existing)",
                },
                "status": {
                    "type": "string",
                    "description": "New status (tracking, paused, archived, completed)",
                },
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "link_paper",
        "description": (
            "Link a paper from the library to an ML project. "
            "Use when the user mentions a paper is related to a project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "paper": {
                    "type": "string",
                    "description": "Paper citekey, title substring, or index number",
                },
            },
            "required": ["project", "paper"],
        },
    },
    {
        "name": "update_goals",
        "description": (
            "Set metric goals on a project. Each goal has a metric name, "
            "direction (maximize/minimize), and threshold value."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "goals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "metric": {"type": "string"},
                            "direction": {
                                "type": "string",
                                "enum": ["maximize", "minimize"],
                            },
                            "threshold": {"type": "number"},
                        },
                        "required": ["metric", "direction", "threshold"],
                    },
                    "description": "List of metric goals",
                },
            },
            "required": ["project", "goals"],
        },
    },
    {
        "name": "annotate_run",
        "description": (
            "Add a note or hypothesis to an experiment run. User-provided "
            "hypotheses take precedence over LLM-generated ones in notebooks. "
            "Notes are appended (not replaced)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
                "run": {
                    "type": "string",
                    "description": "Run id or name substring",
                },
                "hypothesis": {
                    "type": "string",
                    "description": "Hypothesis for this run (replaces any existing)",
                },
                "note": {
                    "type": "string",
                    "description": "A note to append to this run's notes list",
                },
            },
            "required": ["project", "run"],
        },
    },
    # -- Launcher tools --
    {
        "name": "launch_experiment",
        "description": (
            "Launch an auto-research experiment session in a tmux window. "
            "Spawns a Claude Code session with the project's PROMPT.md. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "model": {
                    "type": "string",
                    "description": "Claude model to use (default: claude-sonnet-4-5-20250929)",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Max turns for the session (default: 100)",
                },
                "host": {
                    "type": "string",
                    "description": "SSH host for remote launch (optional — local by default)",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "experiment_status",
        "description": (
            "Check status of running experiment sessions. Shows active "
            "tmux sessions, run counts, and how long they've been running. "
            "If no project specified, shows all experiments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index (optional — shows all if omitted)",
                },
            },
        },
    },
    {
        "name": "stop_experiment",
        "description": (
            "Stop a running experiment session by sending C-c to its tmux window. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "init_experiment",
        "description": (
            "Initialize an experiment project — scan the directory, draft a "
            "PROMPT.md with Claude, set up hooks and tracking. The user "
            "describes what they want to research and the tool produces a "
            "complete, ready-to-launch experiment. Returns the draft PROMPT.md "
            "for review. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the project directory",
                },
                "goal": {
                    "type": "string",
                    "description": (
                        "What the experiment should achieve — the research "
                        "question, target metric, or objective. Be specific."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Display name for the experiment "
                        "(default: directory name, title-cased)"
                    ),
                },
                "constraints": {
                    "type": "string",
                    "description": (
                        "Hardware, time, or methodology constraints "
                        "(e.g. 'MacBook M3, no GPU', 'must use PyTorch', "
                        "'2 hour budget')"
                    ),
                },
            },
            "required": ["path", "goal"],
        },
    },
    {
        "name": "sweep_experiment",
        "description": (
            "Launch a parallel hyperparameter sweep. Spawns one tmux session "
            "per configuration variant, each with a modified PROMPT.md that "
            "injects the specific hyperparameters. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "configs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "description": "Hyperparameter dict for this variant",
                    },
                    "description": (
                        "List of config dicts, one per variant. "
                        "Example: [{\"lr\": 0.001}, {\"lr\": 0.01}]"
                    ),
                },
                "model": {
                    "type": "string",
                    "description": "Claude model to use (default: claude-sonnet-4-5-20250929)",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Max turns per session (default: 100)",
                },
            },
            "required": ["project", "configs"],
        },
    },
    {
        "name": "continue_experiment",
        "description": (
            "Continue an experiment that hasn't met its goals yet. "
            "Launches a new session with prior-run context appended so "
            "the agent builds on previous results. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "model": {
                    "type": "string",
                    "description": "Claude model to use (default: claude-sonnet-4-5-20250929)",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "Max turns for the session (default: 100)",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "steer_experiment",
        "description": (
            "Write steering instructions for the next experiment session. "
            "The text is saved to .distillate/steering.md and automatically "
            "injected into the next session's prompt. Use when the user wants "
            "to guide the experiment in a specific direction (e.g., 'try lower "
            "learning rate', 'focus on regularization')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, id, or index number",
                },
                "text": {
                    "type": "string",
                    "description": "Steering instructions for the next session",
                },
            },
            "required": ["project", "text"],
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
    proj, err = _resolve_project(state, identifier)
    if err:
        return {"found": False, **err}

    runs = proj.get("runs", {})
    run_summaries = [_run_summary(r) for r in runs.values()]
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

    proj, err = _resolve_project(state, project)
    if err:
        return err

    runs = proj.get("runs", {})
    proj_name = proj.get("name", "")

    a, err = _resolve_run(runs, run_a, proj_name)
    if err:
        return err

    b, err = _resolve_run(runs, run_b, proj_name)
    if err:
        return err

    return diff_runs(a, b)


def _discover_git_repos(root) -> list:
    """Find git repos under root (1 level deep). Returns list of Paths."""
    from pathlib import Path as _Path

    root = _Path(root)
    repos = []
    try:
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if (child / ".git").exists():
                repos.append(child)
    except PermissionError:
        pass
    return repos


def scan_project_tool(*, state, path: str) -> dict:
    """Scan a git repo and track it as a project."""
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    from distillate.experiments import (
        enrich_runs_with_llm, generate_html_notebook, generate_notebook,
        scan_project, slugify,
    )
    from distillate.obsidian import (
        write_experiment_html_notebook, write_experiment_notebook,
    )
    from distillate.state import acquire_lock, release_lock

    repo_path = _Path(path).expanduser().resolve()
    if not repo_path.is_dir():
        return {"success": False, "error": f"Directory not found: {path}"}

    # If path has no .git, discover individual repos in subdirectories
    if not (repo_path / ".git").exists():
        sub_repos = _discover_git_repos(repo_path)
        if not sub_repos:
            return {
                "success": False,
                "error": (
                    f"No git repository found at '{path}'. "
                    "Point to a directory with a .git folder, or a parent "
                    "directory containing git repos."
                ),
            }
        # Scan each discovered repo as a separate project
        results = []
        for sub in sub_repos:
            r = scan_project_tool(state=state, path=str(sub))
            if r.get("success"):
                results.append(r)
        if not results:
            return {
                "success": False,
                "error": f"Found {len(sub_repos)} repo(s) under '{path}' but none had ML experiments.",
            }
        return {
            "success": True,
            "multi": True,
            "projects": [
                {"name": r["name"], "runs": r["runs_discovered"]}
                for r in results
            ],
            "message": (
                f"Discovered {len(results)} project(s) under '{path}': "
                + ", ".join(
                    f"{r['name']} ({r['runs_discovered']} runs)"
                    for r in results
                )
            ),
        }

    result = scan_project(repo_path)
    if "error" in result:
        return {"success": False, "error": result["error"]}

    project_id = slugify(result["name"])

    # Lock state for the mutation section
    acquire_lock()
    try:
        state.reload()
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
            update_kw = dict(
                last_scanned_at=datetime.now(timezone.utc).isoformat(),
                last_commit_hash=result["head_hash"],
            )
            if result.get("goals"):
                update_kw["goals"] = result["goals"]
            state.update_project(project_id, **update_kw)
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
            update_kw = dict(
                last_scanned_at=datetime.now(timezone.utc).isoformat(),
                last_commit_hash=result["head_hash"],
            )
            if result.get("goals"):
                update_kw["goals"] = result["goals"]
            state.update_project(project_id, **update_kw)
            state.save()
            message = (
                f"Now tracking '{result['name']}' with "
                f"{len(result.get('runs', {}))} discovered run(s)."
            )
    finally:
        release_lock()

    # LLM enrichment + generate notebooks (MD + HTML)
    proj = state.get_project(project_id)
    if proj:
        enrichment = enrich_runs_with_llm(
            proj.get("runs", {}), proj.get("name", ""), repo_path,
        )
        notebook_md = generate_notebook(proj, enrichment=enrichment)
        write_experiment_notebook(proj, notebook_md)
        notebook_html = generate_html_notebook(proj, enrichment=enrichment)
        write_experiment_html_notebook(proj, notebook_html)

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

    proj, err = _resolve_project(state, project)
    if err:
        return err

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
# CRUD tool implementations
# ---------------------------------------------------------------------------

def add_project_tool(*, state, path: str, name: str = "",
                     description: str = "", tags: list[str] | None = None) -> dict:
    """Add a directory as a tracked ML project and scan it."""
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    from distillate.experiments import scan_project, slugify
    from distillate.state import acquire_lock, release_lock

    repo_path = _Path(path).expanduser().resolve()
    if not repo_path.is_dir():
        return {"success": False, "error": f"Directory not found: {path}"}

    result = scan_project(repo_path)
    if "error" in result:
        return {"success": False, "error": result["error"]}

    display_name = name or result["name"]
    project_id = slugify(display_name)

    acquire_lock()
    try:
        state.reload()
        if state.has_project(project_id):
            return {"success": False, "error": f"Project '{display_name}' already tracked. Use scan_project to rescan."}

        state.add_project(
            project_id=project_id,
            name=display_name,
            path=str(repo_path),
            description=description,
            tags=tags,
        )
        for run_id, run_data in result.get("runs", {}).items():
            state.add_run(project_id, run_id, run_data)
        state.update_project(
            project_id,
            last_scanned_at=datetime.now(timezone.utc).isoformat(),
            last_commit_hash=result.get("head_hash", ""),
        )
        state.save()
    finally:
        release_lock()

    # Generate notebook
    proj = state.get_project(project_id)
    if proj:
        _regen_notebook(proj)

    return {
        "success": True,
        "project_id": project_id,
        "name": display_name,
        "runs_discovered": len(result.get("runs", {})),
        "message": (
            f"Now tracking '{display_name}' with "
            f"{len(result.get('runs', {}))} discovered run(s)."
        ),
    }


def rename_project_tool(*, state, identifier: str, new_name: str) -> dict:
    """Rename a tracked ML project."""
    from distillate.experiments import slugify
    from distillate.obsidian import write_experiment_notebook

    proj, err = _resolve_project(state, identifier)
    if err:
        return err

    old_id = proj["id"]
    old_name = proj.get("name", "")
    new_id = slugify(new_name)

    if new_id == old_id and new_name == old_name:
        return {"success": False, "error": "New name is the same as current name."}

    if new_id != old_id and state.has_project(new_id):
        return {"success": False, "error": f"A project with slug '{new_id}' already exists."}

    # If slug changed, we need to re-key the project in state
    if new_id != old_id:
        proj_data = state.projects.pop(old_id)
        proj_data["id"] = new_id
        proj_data["name"] = new_name
        state.projects[new_id] = proj_data

        # Remove old notebook, write new one
        _remove_notebook(old_id)
        _regen_notebook(proj_data)
    else:
        state.update_project(old_id, name=new_name)
        proj["name"] = new_name
        _regen_notebook(proj)

    state.save()

    return {
        "success": True,
        "old_name": old_name,
        "new_name": new_name,
        "old_id": old_id,
        "new_id": new_id,
        "message": f"Renamed '{old_name}' → '{new_name}'.",
    }


def rename_run_tool(*, state, project: str, run: str, new_name: str) -> dict:
    """Rename an experiment run within a project."""
    proj, err = _resolve_project(state, project)
    if err:
        return err

    runs = proj.get("runs", {})
    run_obj, err = _resolve_run(runs, run, proj.get("name", ""))
    if err:
        return err

    old_name = run_obj.get("name", "")
    run_obj["name"] = new_name
    state.save()

    _regen_notebook(proj)

    return {
        "success": True,
        "old_name": old_name,
        "new_name": new_name,
        "message": f"Renamed run '{old_name}' → '{new_name}'.",
    }


def delete_project_tool(*, state, identifier: str, confirm: bool = False) -> dict:
    """Delete a tracked ML project (two-phase)."""
    proj, err = _resolve_project(state, identifier)
    if err:
        return err

    proj_name = proj.get("name", "")
    run_count = len(proj.get("runs", {}))

    if not confirm:
        return {
            "confirm_required": True,
            "project": proj_name,
            "run_count": run_count,
            "message": (
                f"Will delete project '{proj_name}' with {run_count} run(s) "
                f"from tracking. Source files will NOT be deleted. "
                f"Call again with confirm=true to proceed."
            ),
        }

    proj_id = proj["id"]
    _remove_notebook(proj_id)
    state.remove_project(proj_id)
    state.save()

    return {
        "success": True,
        "message": f"Deleted project '{proj_name}' ({run_count} runs removed from tracking).",
    }


def delete_run_tool(*, state, project: str, run: str, confirm: bool = False) -> dict:
    """Delete an experiment run from a project (two-phase)."""
    proj, err = _resolve_project(state, project)
    if err:
        return err

    runs = proj.get("runs", {})
    run_obj, err = _resolve_run(runs, run, proj.get("name", ""))
    if err:
        return err

    run_name = run_obj.get("name", "")
    run_id = run_obj.get("id", "")

    if not confirm:
        return {
            "confirm_required": True,
            "project": proj.get("name", ""),
            "run": run_name,
            "message": (
                f"Will delete run '{run_name}' from project "
                f"'{proj.get('name', '')}'. Source files will NOT be "
                f"deleted. Call again with confirm=true to proceed."
            ),
        }

    state.remove_run(proj["id"], run_id)
    state.save()

    _regen_notebook(proj)

    return {
        "success": True,
        "message": f"Deleted run '{run_name}' from '{proj.get('name', '')}'.",
    }


def update_project_tool(*, state, identifier: str,
                        description: str | None = None,
                        tags: list[str] | None = None,
                        status: str | None = None) -> dict:
    """Update a project's description, tags, or status."""
    proj, err = _resolve_project(state, identifier)
    if err:
        return err

    updates = {}
    if description is not None:
        updates["description"] = description
    if tags is not None:
        updates["tags"] = tags
    if status is not None:
        valid_statuses = ("tracking", "paused", "archived", "completed")
        if status not in valid_statuses:
            return {"error": f"Invalid status '{status}'. Must be one of: {', '.join(valid_statuses)}"}
        updates["status"] = status

    if not updates:
        return {"error": "No fields to update. Provide description, tags, or status."}

    state.update_project(proj["id"], **updates)
    state.save()

    return {
        "success": True,
        "project": proj.get("name", ""),
        "updated": list(updates.keys()),
        "message": f"Updated {', '.join(updates.keys())} for '{proj.get('name', '')}'.",
    }


def link_paper_tool(*, state, project: str, paper: str) -> dict:
    """Link a paper from the library to an ML project."""
    proj, err = _resolve_project(state, project)
    if err:
        return err

    # Resolve paper — try index, citekey, title substring
    paper_query = paper.strip()
    found_key = None
    found_title = None

    # Try index number
    if paper_query.isdigit():
        key = state.key_for_index(int(paper_query))
        if key:
            doc = state.get_document(key)
            if doc:
                found_key = key
                ck = doc.get("metadata", {}).get("citekey", "")
                found_title = ck or doc.get("title", "")

    # Try citekey or title
    if not found_key:
        doc = state.find_by_citekey(paper_query)
        if doc:
            found_key = doc.get("zotero_item_key", "")
            found_title = paper_query
        else:
            # Title substring search
            query_lower = paper_query.lower()
            for key, doc in state.documents.items():
                ck = doc.get("metadata", {}).get("citekey", "")
                if (query_lower in ck.lower()
                        or query_lower in doc.get("title", "").lower()):
                    found_key = key
                    found_title = ck or doc.get("title", "")
                    break

    if not found_key:
        return {"error": f"No paper found matching '{paper_query}'"}

    linked = proj.get("linked_papers", [])
    if found_title in linked:
        return {"success": False, "error": f"Paper '{found_title}' is already linked to this project."}

    linked.append(found_title)
    state.update_project(proj["id"], linked_papers=linked)
    state.save()

    _regen_notebook(proj)

    return {
        "success": True,
        "project": proj.get("name", ""),
        "paper": found_title,
        "message": f"Linked '{found_title}' to project '{proj.get('name', '')}'.",
    }


def update_goals_tool(*, state, project: str, goals: list[dict]) -> dict:
    """Set metric goals on a project."""
    proj, err = _resolve_project(state, project)
    if err:
        return err

    # Validate goals
    for g in goals:
        if not g.get("metric"):
            return {"error": "Each goal must have a 'metric' field."}
        if g.get("direction") not in ("maximize", "minimize"):
            return {"error": f"Invalid direction '{g.get('direction')}'. Must be 'maximize' or 'minimize'."}
        if not isinstance(g.get("threshold"), (int, float)):
            return {"error": f"Threshold must be a number, got '{g.get('threshold')}'."}

    state.update_project(proj["id"], goals=goals)
    state.save()

    _regen_notebook(proj)

    return {
        "success": True,
        "project": proj.get("name", ""),
        "goals_count": len(goals),
        "message": f"Set {len(goals)} goal(s) for '{proj.get('name', '')}'.",
    }


def annotate_run_tool(*, state, project: str, run: str,
                      hypothesis: str = "", note: str = "") -> dict:
    """Add a note or hypothesis to an experiment run."""
    proj, err = _resolve_project(state, project)
    if err:
        return err

    runs = proj.get("runs", {})
    run_obj, err = _resolve_run(runs, run, proj.get("name", ""))
    if err:
        return err

    if not hypothesis and not note:
        return {"error": "Provide at least one of 'hypothesis' or 'note'."}

    changes = []
    if hypothesis:
        run_obj["hypothesis"] = hypothesis
        changes.append("hypothesis")
    if note:
        if "notes" not in run_obj:
            run_obj["notes"] = []
        run_obj["notes"].append(note)
        changes.append("note")

    state.save()
    _regen_notebook(proj)

    return {
        "success": True,
        "run": run_obj.get("name", ""),
        "updated": changes,
        "message": (
            f"Updated {' and '.join(changes)} on run "
            f"'{run_obj.get('name', '')}' in '{proj.get('name', '')}'."
        ),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def launch_experiment_tool(*, state, project: str,
                           model: str = "claude-sonnet-4-5-20250929",
                           max_turns: int = 100,
                           host: str | None = None) -> dict:
    """Launch an auto-research experiment session."""
    from pathlib import Path as _Path

    from distillate.launcher import launch_experiment
    from distillate.state import acquire_lock, release_lock

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    try:
        session_data = launch_experiment(
            _Path(proj_path),
            host=host,
            model=model,
            max_turns=max_turns,
            project=proj,
        )
    except (FileNotFoundError, RuntimeError) as e:
        return {"error": str(e)}

    # Save session to state
    acquire_lock()
    try:
        state.reload()
        state.add_session(proj["id"], session_data["session_id"], session_data)
        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "tmux_session": session_data["tmux_session"],
        "model": model,
        "max_turns": max_turns,
        "host": host,
        "message": (
            f"Launched session '{session_data['tmux_session']}' for "
            f"'{proj.get('name', '')}'. Use experiment_status to monitor."
        ),
    }


def experiment_status_tool(*, state, project: str = "") -> dict:
    """Check status of running experiment sessions."""
    from distillate.launcher import refresh_session_statuses

    changed = refresh_session_statuses(state)
    if changed:
        state.save()

    if project:
        proj, err = _resolve_project(state, project)
        if err:
            return err
        projects = {proj["id"]: proj}
    else:
        projects = state.projects

    results = []
    for proj_id, proj in projects.items():
        sessions = proj.get("sessions", {})
        runs = proj.get("runs", {})
        active = [s for s in sessions.values() if s.get("status") == "running"]

        proj_info = {
            "name": proj.get("name", ""),
            "status": proj.get("status", ""),
            "total_runs": len(runs),
            "active_sessions": len(active),
            "sessions": [],
        }

        for sess in sessions.values():
            started = sess.get("started_at", "")
            proj_info["sessions"].append({
                "tmux_session": sess.get("tmux_session", ""),
                "status": sess.get("status", ""),
                "started_at": started,
                "model": sess.get("model", ""),
                "host": sess.get("host"),
            })

        results.append(proj_info)

    total_active = sum(p["active_sessions"] for p in results)
    return {
        "experiments": results,
        "total_active_sessions": total_active,
    }


def stop_experiment_tool(*, state, project: str) -> dict:
    """Stop a running experiment session."""
    from datetime import datetime, timezone

    from distillate.launcher import stop_session
    from distillate.state import acquire_lock, release_lock

    proj, err = _resolve_project(state, project)
    if err:
        return err

    sessions = proj.get("sessions", {})
    running = [(sid, s) for sid, s in sessions.items() if s.get("status") == "running"]

    if not running:
        return {"error": f"No running sessions for '{proj.get('name', '')}'."}

    stopped = []
    failed = []
    for sess_id, sess in running:
        tmux_name = sess.get("tmux_session", "")
        host = sess.get("host")
        ok = stop_session(tmux_name, host)
        if ok:
            stopped.append(tmux_name)
        else:
            failed.append(tmux_name)

    # Update state
    acquire_lock()
    try:
        state.reload()
        now = datetime.now(timezone.utc).isoformat()
        for sess_id, sess in running:
            tmux_name = sess.get("tmux_session", "")
            if tmux_name in stopped:
                state.update_session(proj["id"], sess_id,
                                     status="completed", completed_at=now)
        state.save()
    finally:
        release_lock()

    if failed:
        return {
            "success": False,
            "stopped": stopped,
            "failed": failed,
            "message": f"Stopped {len(stopped)}, failed {len(failed)} session(s).",
        }

    return {
        "success": True,
        "stopped": stopped,
        "message": f"Stopped {len(stopped)} session(s) for '{proj.get('name', '')}'.",
    }


def sweep_experiment_tool(*, state, project: str,
                          configs: list[dict],
                          model: str = "claude-sonnet-4-5-20250929",
                          max_turns: int = 100) -> dict:
    """Launch a parallel hyperparameter sweep."""
    from pathlib import Path as _Path

    from distillate.launcher import launch_sweep
    from distillate.state import acquire_lock, release_lock

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    if not configs or len(configs) < 2:
        return {"error": "Provide at least 2 config variants for a sweep."}

    try:
        sessions = launch_sweep(
            _Path(proj_path), proj, configs,
            model=model, max_turns=max_turns,
        )
    except (FileNotFoundError, RuntimeError) as e:
        return {"error": str(e)}

    # Save all sessions to state
    acquire_lock()
    try:
        state.reload()
        for sd in sessions:
            state.add_session(proj["id"], sd["session_id"], sd)
        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "variants": len(sessions),
        "sessions": [s["tmux_session"] for s in sessions],
        "model": model,
        "message": (
            f"Launched {len(sessions)}-variant sweep for "
            f"'{proj.get('name', '')}'. Use experiment_status to monitor."
        ),
    }


def continue_experiment_tool(*, state, project: str,
                             model: str = "claude-sonnet-4-5-20250929",
                             max_turns: int = 100) -> dict:
    """Launch a continuation session with prior-run context."""
    from pathlib import Path as _Path

    from distillate.launcher import launch_continuation, should_continue
    from distillate.state import acquire_lock, release_lock

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    if not should_continue(proj):
        return {
            "success": False,
            "message": (
                f"All goals for '{proj.get('name', '')}' appear to be met. "
                "No continuation needed."
            ),
        }

    try:
        session_data = launch_continuation(
            _Path(proj_path), proj, model=model, max_turns=max_turns,
        )
    except (FileNotFoundError, RuntimeError) as e:
        return {"error": str(e)}

    acquire_lock()
    try:
        state.reload()
        state.add_session(proj["id"], session_data["session_id"], session_data)
        state.save()
    finally:
        release_lock()

    return {
        "success": True,
        "tmux_session": session_data["tmux_session"],
        "model": model,
        "max_turns": max_turns,
        "message": (
            f"Launched continuation session '{session_data['tmux_session']}' "
            f"for '{proj.get('name', '')}' with prior-run context."
        ),
    }


def steer_experiment_tool(*, state, project: str, text: str) -> dict:
    """Write steering instructions for the next experiment session."""
    from pathlib import Path as _Path

    from distillate.launcher import write_steering

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"error": f"Project '{project}' has no path set."}

    path = write_steering(_Path(proj_path), text)
    preview = text[:200] + ("..." if len(text) > 200 else "")
    return {
        "success": True,
        "path": str(path),
        "preview": preview,
        "message": (
            f"Steering instructions written for '{proj.get('name', '')}'. "
            "They'll be injected into the next session's prompt."
        ),
    }


# ---------------------------------------------------------------------------
# Goal auto-parsing from free-form text
# ---------------------------------------------------------------------------

# Metrics that should default to "minimize" direction
_MINIMIZE_METRICS = frozenset({
    "loss", "val_loss", "train_loss", "mse", "rmse", "mae",
    "error", "perplexity",
})

# Known metric names we can recognise in text
_KNOWN_METRICS = [
    "test_accuracy", "val_accuracy", "val_loss", "train_loss",
    "best_val_acc", "exact_match", "f1", "accuracy", "precision",
    "recall", "perplexity", "bleu", "rouge", "auc", "mse", "rmse",
    "mae", "error", "loss",
]

# Metrics whose thresholds are typically expressed as percentages (95% → 0.95)
_PERCENT_METRICS = frozenset({
    "accuracy", "test_accuracy", "val_accuracy", "best_val_acc",
    "exact_match", "f1", "precision", "recall", "auc",
})


def _infer_direction(metric: str) -> str:
    """Return 'minimize' or 'maximize' based on metric name."""
    return "minimize" if metric in _MINIMIZE_METRICS else "maximize"


def _normalise_threshold(value: float, metric: str, was_percent: bool) -> float:
    """Convert percentage thresholds to decimals for accuracy-like metrics."""
    if was_percent and metric in _PERCENT_METRICS:
        return value / 100.0
    # Heuristic: raw number > 1 for a percent-like metric is probably a %
    if not was_percent and value > 1.0 and metric in _PERCENT_METRICS:
        return value / 100.0
    return value


def _parse_goals_from_text(goal: str) -> list[dict]:
    """Extract structured goals from a free-form goal string.

    Supports patterns like:
      - "accuracy > 95%"
      - "loss < 0.1"
      - "maximize accuracy to 90%"
      - "minimize perplexity below 20"
      - "f1 score above 0.85"
    """
    import re

    if not goal:
        return []

    text = goal.lower()
    results: list[dict] = []
    seen: set[str] = set()

    # Build a regex alternation for known metrics (longest first to avoid
    # partial matches like "loss" matching inside "val_loss")
    sorted_metrics = sorted(_KNOWN_METRICS, key=len, reverse=True)
    metric_pattern = "|".join(re.escape(m).replace("_", r"[\s_]") for m in sorted_metrics)

    # Pattern 1: "metric_name >/>=/</<= threshold"
    p1 = re.compile(
        rf"({metric_pattern})\s*(?:score\s+)?([><]=?)\s*(\d+(?:\.\d+)?)\s*(%)?",
    )
    for m in p1.finditer(text):
        metric = re.sub(r"\s+", "_", m.group(1).strip())
        op = m.group(2)
        value = float(m.group(3))
        pct = m.group(4) is not None
        direction = "maximize" if op.startswith(">") else "minimize"
        threshold = _normalise_threshold(value, metric, pct)
        if metric not in seen:
            seen.add(metric)
            results.append({"metric": metric, "direction": direction, "threshold": threshold})

    # Pattern 2: "maximize/minimize metric_name to/above/below threshold"
    p2 = re.compile(
        rf"(maximize|minimize)\s+({metric_pattern})"
        rf"(?:\s+score)?\s+(?:to|above|over|below|under)\s+(\d+(?:\.\d+)?)\s*(%)?",
    )
    for m in p2.finditer(text):
        direction = m.group(1)
        metric = re.sub(r"\s+", "_", m.group(2).strip())
        value = float(m.group(3))
        pct = m.group(4) is not None
        threshold = _normalise_threshold(value, metric, pct)
        if metric not in seen:
            seen.add(metric)
            results.append({"metric": metric, "direction": direction, "threshold": threshold})

    # Pattern 3: "metric_name above/over/exceeding/below/under threshold"
    p3 = re.compile(
        rf"({metric_pattern})\s*(?:score\s+)?"
        rf"(above|over|exceeding|below|under)\s+(\d+(?:\.\d+)?)\s*(%)?",
    )
    for m in p3.finditer(text):
        metric = re.sub(r"\s+", "_", m.group(1).strip())
        word = m.group(2)
        value = float(m.group(3))
        pct = m.group(4) is not None
        direction = "minimize" if word in ("below", "under") else "maximize"
        threshold = _normalise_threshold(value, metric, pct)
        if metric not in seen:
            seen.add(metric)
            results.append({"metric": metric, "direction": direction, "threshold": threshold})

    return results


def init_experiment_tool(*, state, path: str, goal: str,
                         name: str = "", constraints: str = "",
                         duration_minutes: int = 5) -> dict:
    """Initialize an experiment project with LLM-drafted PROMPT.md."""
    import json as _json
    import subprocess
    from pathlib import Path as _Path

    from distillate import config
    from distillate.experiments import slugify
    from distillate.launcher import _install_hooks_into
    from distillate.state import acquire_lock, release_lock

    project_path = _Path(path).expanduser().resolve()

    # Create directory if it doesn't exist
    if not project_path.exists():
        project_path.mkdir(parents=True, exist_ok=True)

    if not project_path.is_dir():
        return {"success": False, "error": f"Path is not a directory: {path}"}

    # Reject if PROMPT.md already exists
    prompt_file = project_path / "PROMPT.md"
    if prompt_file.exists():
        return {
            "success": False,
            "error": (
                f"PROMPT.md already exists in {path}. "
                "Edit it directly or delete it first."
            ),
        }

    # --- Step 1: Scan directory ---
    scan = _scan_directory_for_init(project_path)

    # --- Step 2: Call Claude to draft PROMPT.md ---
    prompt_md = _generate_prompt_md(goal, scan, name, constraints, duration_minutes)
    if prompt_md is None:
        return {
            "success": False,
            "error": (
                "Failed to generate PROMPT.md — no API credentials configured. "
                "Set ANTHROPIC_API_KEY in your .env file."
            ),
        }

    # --- Step 3: Write PROMPT.md ---
    prompt_file.write_text(prompt_md, encoding="utf-8")

    # --- Step 4: Set up infrastructure ---
    # git init if not already a repo
    if not (project_path / ".git").exists():
        subprocess.run(
            ["git", "init"],
            cwd=project_path,
            capture_output=True,
        )

    # Create .distillate/ with REPORTING.md
    distillate_dir = project_path / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    reporting_src = _Path(__file__).parent / "autoresearch" / "REPORTING.md"
    if reporting_src.exists():
        import shutil
        shutil.copy2(reporting_src, distillate_dir / "REPORTING.md")

    # Install hooks
    _install_hooks_into(project_path)

    # Create .claude/settings.local.json with safe Bash permissions
    claude_dir = project_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_local = claude_dir / "settings.local.json"
    if not settings_local.exists():
        local_config = {
            "permissions": {
                "allow": [
                    "Bash(python3:*)",
                    "Bash(tail:*)",
                    "Bash(ls:*)",
                    "Bash(cat:*)",
                    "Bash(head:*)",
                    "Bash(wc:*)",
                    "Bash(mkdir:*)",
                    "Read",
                    "Write",
                    "Edit",
                    "Glob",
                    "Grep",
                ],
            },
        }
        settings_local.write_text(
            _json.dumps(local_config, indent=2) + "\n",
            encoding="utf-8",
        )

    # --- Step 5: Register in state ---
    display_name = name or project_path.name.replace("-", " ").replace("_", " ").title()
    project_id = slugify(display_name)

    if not state.has_project(project_id):
        from datetime import datetime, timezone
        acquire_lock()
        try:
            state.reload()
            state.add_project(
                project_id=project_id,
                name=display_name,
                path=str(project_path),
            )
            state.update_project(
                project_id,
                last_scanned_at=datetime.now(timezone.utc).isoformat(),
            )
            # Auto-parse goals from the free-form goal string
            parsed_goals = _parse_goals_from_text(goal)
            if parsed_goals:
                state.update_project(project_id, goals=parsed_goals)
            state.save()
        finally:
            release_lock()

    return {
        "success": True,
        "project_id": project_id,
        "name": display_name,
        "path": str(project_path),
        "prompt_md": prompt_md,
        "message": (
            f"Initialized '{display_name}' with a draft PROMPT.md. "
            "Review it above — tell me what to change, or say 'launch it' "
            "when ready."
        ),
    }


def _scan_directory_for_init(project_path) -> dict:
    """Scan a directory for context to feed the PROMPT.md generator."""
    scan: dict = {
        "files": [],
        "readme": "",
        "code_snippets": {},
        "data_files": [],
    }

    # List files (2 levels deep)
    try:
        for item in sorted(project_path.rglob("*")):
            rel = item.relative_to(project_path)
            if any(p.startswith(".") for p in rel.parts):
                continue
            if len(rel.parts) > 2:
                continue
            if item.is_file():
                scan["files"].append(str(rel))
    except PermissionError:
        pass

    # Read README
    for readme_name in ("README.md", "README.txt", "README"):
        readme = project_path / readme_name
        if readme.exists():
            try:
                text = readme.read_text(encoding="utf-8")
                scan["readme"] = text[:3000]
            except OSError:
                pass
            break

    # Detect data files
    data_exts = {".csv", ".json", ".jsonl", ".parquet", ".tsv", ".npy", ".npz", ".h5", ".hdf5"}
    for f in scan["files"]:
        from pathlib import Path as _P
        if _P(f).suffix.lower() in data_exts:
            scan["data_files"].append(f)

    # Read key code files (first 50 lines)
    key_names = {"train.py", "model.py", "main.py", "config.py", "config.yaml",
                 "config.yml", "requirements.txt", "pyproject.toml", "setup.py"}
    for f in scan["files"]:
        from pathlib import Path as _P
        if _P(f).name.lower() in key_names:
            try:
                lines = (project_path / f).read_text(encoding="utf-8").splitlines()[:50]
                scan["code_snippets"][f] = "\n".join(lines)
            except OSError:
                pass

    return scan


_PROMPT_MD_SYSTEM = """\
You are an expert ML researcher writing an autonomous experiment prompt. \
Write a PROMPT.md that is precise, thorough, and gives an autonomous agent \
everything it needs to run experiments independently.

The PROMPT.md must follow this exact structure:

# Task: <Title that captures the objective>

**Objective:** <One sentence with a specific, measurable target>

## The Task

<Problem definition — what the model/system must do, input/output format, \
what success looks like>

## Data

<What data exists, file paths relative to the project root, format, \
train/test splits. If no data exists yet, specify how to obtain or generate it.>

## Rules & Constraints

<Hardware constraints, compute budget, time budget, no internet access, \
autonomy requirements, no reward hacking, allowed tools and libraries. \
IMPORTANT: include the time budget the user specified (default: 5 minutes per \
experiment iteration). Each iteration should fit within this budget.>

**CRITICAL: File Size Limit.** When using the Read tool, tool results must \
not exceed 51,200 bytes. For files longer than ~400 lines, always use \
`offset` and `limit` parameters to read in chunks. When writing code, \
keep individual Python files under 400 lines — split large scripts into \
separate modules.

## Experiment Tracking (Distillate)

### Prior Runs
Before starting, **read `.distillate/runs.jsonl`** if it exists. It contains \
the history of all prior experiment iterations. Build on what worked, avoid \
repeating failed approaches. Reference prior run IDs in your reasoning. \
If `.distillate/context.md` exists, read it for a formatted summary.

### Recording Results
After each experiment iteration, you MUST append one JSON line to \
`.distillate/runs.jsonl`:

```json
{"$schema":"distillate/run/v1", "id":"run_NNN", "timestamp":"ISO8601", \
"status":"keep|discard|crash", "hypothesis":"...", "changes":"...", \
"hyperparameters":{...}, "results":{...}, "reasoning":"..."}
```

Set `status` to `keep` if results improved, `discard` if not, `crash` on \
failure. Include `reasoning` to explain your decision. Create the \
`.distillate/` directory if it doesn't exist.

## What You Must Deliver

<Numbered list of deliverables — model, training curves, evaluation, \
written log of decisions>

## Evaluation Criteria

<Primary metric with threshold, secondary criteria like methodology quality>

Write in second person ("you must..."). Be direct and specific. Include \
concrete numbers for targets where the user provided them. The prompt should \
be self-contained — an agent reading only this file should know exactly what \
to do without asking questions.

IMPORTANT: Always include the "Experiment Tracking (Distillate)" section \
exactly as shown above — this is how experiment data is recorded and tracked.

Do NOT include any meta-commentary, preamble, or explanation outside the \
PROMPT.md content. Output ONLY the markdown content of the PROMPT.md file."""


def _generate_prompt_md(goal: str, scan: dict, name: str,
                        constraints: str,
                        duration_minutes: int = 5) -> str | None:
    """Call Claude to generate PROMPT.md content."""
    from distillate.agent_core import create_client

    client = create_client()
    if client is None:
        return None

    # Build the user message with all context
    parts = [f"**Goal:** {goal}"]

    if name:
        parts.append(f"**Project name:** {name}")

    if constraints:
        parts.append(f"**Constraints:** {constraints}")

    parts.append(f"**Time budget per iteration:** {duration_minutes} minutes")

    if scan["files"]:
        file_list = "\n".join(f"- {f}" for f in scan["files"][:50])
        parts.append(f"**Directory contents:**\n{file_list}")

    if scan["readme"]:
        parts.append(f"**README:**\n```\n{scan['readme']}\n```")

    if scan["data_files"]:
        parts.append(f"**Data files:** {', '.join(scan['data_files'])}")

    if scan["code_snippets"]:
        for fname, snippet in scan["code_snippets"].items():
            parts.append(f"**{fname}** (first 50 lines):\n```\n{snippet}\n```")

    user_msg = "\n\n".join(parts)

    try:
        from distillate import config
        response = client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=4096,
            system=_PROMPT_MD_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()
    except Exception:
        log.exception("Failed to generate PROMPT.md via Claude API")
        return None


def _remove_notebook(project_id: str) -> None:
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
    for md_file in nb_dir.glob(f"{project_id}*.md"):
        md_file.unlink(missing_ok=True)

    # Remove HTML notebook
    html_dir = nb_dir / "html"
    if html_dir.is_dir():
        html_file = html_dir / f"{project_id}.html"
        html_file.unlink(missing_ok=True)
