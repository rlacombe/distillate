"""Project and run CRUD tools."""

import logging
import shlex
from pathlib import Path as _Path

from ._helpers import (
    _compute_time_info, _find_run, _resolve_project, _resolve_run,
    _regen_notebook, _remove_notebook, _run_summary, _run_summary_full,
    _sanitize_llm_text, _find_all_runs,
)

log = logging.getLogger(__name__)

SCHEMAS = [
    {
        "name": "list_experiments",
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
        "name": "get_experiment_details",
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
        "name": "scan_experiment",
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
        "name": "add_experiment",
        "description": (
            "Add a directory as a tracked ML project and scan it for "
            "experiments. Superset of scan_experiment — also lets you set a "
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
        "name": "rename_experiment",
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
        "name": "delete_experiment",
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
        "name": "update_experiment",
        "description": (
            "Update a project's description, tags, status, or primary metric. "
            "Only provided fields are changed. Use key_metric_name to set "
            "the north star metric displayed in the experiment dashboard."
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
                "key_metric_name": {
                    "type": "string",
                    "description": "Primary metric name for the project (e.g. 'param_count', 'test_accuracy'). Shown as the hero metric in the dashboard.",
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
        "name": "get_run_details",
        "description": (
            "Get full details for a single experiment run including all "
            "hyperparameters, results/metrics, hypothesis, reasoning, "
            "decision, tags, and timing. Use when the user asks about "
            "a specific run's parameters or results."
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
                    "description": "Run id, name substring, or run number (e.g. '3' for run #003)",
                },
            },
            "required": ["project", "run"],
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
]


# ---------------------------------------------------------------------------
# Implementation functions
# ---------------------------------------------------------------------------

def list_experiments(*, state) -> dict:
    """List all tracked ML projects."""
    projects = state.experiments
    if not projects:
        return {"experiments": [], "total": 0, "message": "No projects tracked yet."}

    results = []
    for proj_id, proj in projects.items():
        runs = proj.get("runs", {})
        best_count = 0
        running = 0
        for r in runs.values():
            decision = r.get("decision") or r.get("status", "")
            if decision == "best":
                best_count += 1
            elif decision == "running":
                running += 1

        results.append({
            "index": state.experiment_index_of(proj_id),
            "id": proj_id,
            "name": proj.get("name", ""),
            "status": proj.get("status", ""),
            "path": proj.get("path", ""),
            "run_count": len(runs),
            "best_runs": best_count,
            "running_runs": running,
            "tags": proj.get("tags", []),
            "last_scanned_at": proj.get("last_scanned_at", ""),
            "linked_papers": proj.get("linked_papers", []),
        })

    return {"experiments": results, "total": len(results)}


def get_experiment_details(
    *, state, identifier: str, include_discarded: bool = False,
) -> dict:
    """Get full details for a project including runs.

    Crash runs are hidden by default. Pass ``include_discarded=True``
    to see them.
    """
    proj, err = _resolve_project(state, identifier)
    if err:
        return {"found": False, **err}

    all_runs = proj.get("runs", {})
    crash_count = 0
    visible_runs = []
    for r in all_runs.values():
        decision = r.get("decision") or r.get("status", "")
        if decision == "crash":
            crash_count += 1
            if not include_discarded:
                continue
        visible_runs.append(r)

    run_summaries = [_run_summary(r) for r in visible_runs]
    run_summaries.sort(
        key=lambda r: r.get("completed_at", "") or "", reverse=True
    )

    # Resolve linked paper titles to brief details
    linked_paper_details = []
    for paper_ref in proj.get("linked_papers", []):
        detail = {"title": paper_ref}
        # Search for the paper in state by citekey or title
        doc = state.find_by_citekey(paper_ref)
        if not doc:
            ref_lower = paper_ref.lower()
            for _k, _d in state.documents.items():
                ck = _d.get("metadata", {}).get("citekey", "")
                if (ref_lower in ck.lower()
                        or ref_lower in _d.get("title", "").lower()):
                    doc = _d
                    break
        if doc:
            meta = doc.get("metadata", {})
            detail["citekey"] = meta.get("citekey", "")
            detail["title"] = doc.get("title", paper_ref)
            detail["status"] = doc.get("status", "")
        linked_paper_details.append(detail)

    # Canonical run count: read runs.jsonl directly, deduplicate by ID,
    # count only terminal entries. This is the authoritative number —
    # state can include backfill entries or orphaned running entries that
    # inflate len(all_runs). If the two counts diverge, surface a warning.
    canonical_run_count: int | None = None
    run_count_diverged = False
    proj_path = proj.get("path", "")
    if proj_path:
        from pathlib import Path as _Path
        import json as _json
        runs_jsonl = _Path(proj_path) / ".distillate" / "runs.jsonl"
        if runs_jsonl.exists():
            _terminal = {"best", "completed", "keep", "discard", "crash", "timeout"}
            _terminal_ids: set[str] = set()
            try:
                for _line in runs_jsonl.read_text(encoding="utf-8").splitlines():
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _e = _json.loads(_line)
                        if _e.get("status") in _terminal:
                            _terminal_ids.add(_e.get("id", ""))
                    except _json.JSONDecodeError:
                        pass
                canonical_run_count = len(_terminal_ids)
                run_count_diverged = canonical_run_count != len(all_runs)
            except OSError:
                pass

    result = {
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
        },
        "runs": run_summaries,
        "total_runs": len(visible_runs),
        "canonical_run_count": canonical_run_count,
        "run_count_source": "runs.jsonl (terminal entries, deduplicated by ID)",
    }
    if run_count_diverged:
        result["run_count_warning"] = (
            f"State shows {len(all_runs)} total entries but runs.jsonl has "
            f"{canonical_run_count} canonical terminal runs. Backfill entries "
            f"or orphaned 'running' entries may be inflating the state count. "
            f"runs.jsonl is authoritative."
        )
    # Live time budget info
    time_info = _compute_time_info(proj)
    if time_info:
        result["time"] = time_info
    if linked_paper_details:
        result["linked_paper_details"] = linked_paper_details
    if crash_count:
        result["crash_runs"] = crash_count
    return result


def compare_runs(
    *, state, project: str, run_a: str, run_b: str,
    include_discarded: bool = False,
) -> dict:
    """Compare two experiment runs within a project.

    Discarded runs are excluded by default unless ``include_discarded=True``.
    """
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

    if not include_discarded:
        for label, run in [("run_a", a), ("run_b", b)]:
            decision = run.get("decision") or run.get("status", "")
            if decision == "crash":
                return {"error": f"{label} ({run.get('name', run.get('id', '?'))}) crashed. Pass include_discarded=true to compare anyway."}

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
        generate_html_notebook, generate_notebook, load_enrichment_cache,
        scan_experiment, slugify,
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
                        "experiments": [
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

    result = scan_experiment(repo_path)
    if "error" in result:
        return {"success": False, "error": result["error"]}

    experiment_id = slugify(result["name"])

    # Lock state for the mutation section
    acquire_lock()
    try:
        state.reload()
        # Add or update in state
        if state.has_experiment(experiment_id):
            # Merge new runs into existing project
            existing = state.get_experiment(experiment_id)
            existing_names = {str(r.get("name", "")) for r in existing.get("runs", {}).values()}
            new_runs = 0
            for run_id, run_data in result.get("runs", {}).items():
                if run_data["name"] not in existing_names:
                    state.add_run(experiment_id, run_id, run_data)
                    new_runs += 1
            update_kw = dict(
                last_scanned_at=datetime.now(timezone.utc).isoformat(),
                last_commit_hash=result["head_hash"],
            )
            if result.get("goals"):
                update_kw["goals"] = result["goals"]
            state.update_experiment(experiment_id, **update_kw)
            state.save()
            message = f"Rescanned '{result['name']}': found {new_runs} new run(s)."
        else:
            state.add_experiment(
                experiment_id=experiment_id,
                name=result["name"],
                path=str(repo_path),
            )
            for run_id, run_data in result.get("runs", {}).items():
                state.add_run(experiment_id, run_id, run_data)
            update_kw = dict(
                last_scanned_at=datetime.now(timezone.utc).isoformat(),
                last_commit_hash=result["head_hash"],
            )
            if result.get("goals"):
                update_kw["goals"] = result["goals"]
            state.update_experiment(experiment_id, **update_kw)
            state.save()
            message = (
                f"Now tracking '{result['name']}' with "
                f"{len(result.get('runs', {}))} discovered run(s)."
            )
    finally:
        release_lock()

    # Generate notebooks (MD + HTML) with cached enrichment
    proj = state.get_experiment(experiment_id)
    if proj:
        enrichment = load_enrichment_cache(repo_path)
        if enrichment:
            enrichment = enrichment.get("enrichment", enrichment)
        notebook_md = generate_notebook(proj, enrichment=enrichment)
        write_experiment_notebook(proj, notebook_md)
        notebook_html = generate_html_notebook(proj, enrichment=enrichment)
        write_experiment_html_notebook(proj, notebook_html)

    return {
        "success": True,
        "experiment_id": experiment_id,
        "name": result["name"],
        "runs_discovered": len(result.get("runs", {})),
        "message": message,
    }


def get_experiment_notebook(*, state, project: str, section: str = "main") -> dict:
    """Get or regenerate the lab notebook for a project.

    Uses cached enrichment only — enrichment is produced by the
    experiment agents themselves, not by server-side API calls.
    """
    from pathlib import Path as _Path

    from distillate.experiments import (
        generate_notebook,
        load_enrichment_cache,
    )
    from distillate.obsidian import write_experiment_notebook

    proj, err = _resolve_project(state, project)
    if err:
        return err

    enrichment = None
    proj_path = proj.get("path", "")
    if proj_path:
        enrichment = load_enrichment_cache(_Path(proj_path))
        if enrichment:
            enrichment = enrichment.get("enrichment", enrichment)

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

    from distillate.experiments import scan_experiment, slugify
    from distillate.state import acquire_lock, release_lock

    repo_path = _Path(path).expanduser().resolve()
    if not repo_path.is_dir():
        return {"success": False, "error": f"Directory not found: {path}"}

    result = scan_experiment(repo_path)
    if "error" in result:
        return {"success": False, "error": result["error"]}

    display_name = name or result["name"]
    experiment_id = slugify(display_name)

    acquire_lock()
    try:
        state.reload()
        if state.has_experiment(experiment_id):
            return {"success": False, "error": f"Project '{display_name}' already tracked. Use scan_experiment to rescan."}

        state.add_experiment(
            experiment_id=experiment_id,
            name=display_name,
            path=str(repo_path),
            description=description,
            tags=tags,
        )
        for run_id, run_data in result.get("runs", {}).items():
            state.add_run(experiment_id, run_id, run_data)
        state.update_experiment(
            experiment_id,
            last_scanned_at=datetime.now(timezone.utc).isoformat(),
            last_commit_hash=result.get("head_hash", ""),
        )
        state.save()
    finally:
        release_lock()

    # Generate notebook
    proj = state.get_experiment(experiment_id)
    if proj:
        _regen_notebook(proj)

    return {
        "success": True,
        "experiment_id": experiment_id,
        "name": display_name,
        "runs_discovered": len(result.get("runs", {})),
        "message": (
            f"Now tracking '{display_name}' with "
            f"{len(result.get('runs', {}))} discovered run(s)."
        ),
    }


def rename_experiment_tool(*, state, identifier: str, new_name: str) -> dict:
    """Rename a tracked ML project."""
    from distillate.experiments import slugify

    proj, err = _resolve_project(state, identifier)
    if err:
        return err

    old_id = proj["id"]
    old_name = proj.get("name", "")
    new_id = slugify(new_name)

    if new_id == old_id and new_name == old_name:
        return {"success": False, "error": "New name is the same as current name."}

    if new_id != old_id and state.has_experiment(new_id):
        return {"success": False, "error": f"A project with slug '{new_id}' already exists."}

    # If slug changed, we need to re-key the project in state
    if new_id != old_id:
        proj_data = state.experiments.pop(old_id)
        proj_data["id"] = new_id
        proj_data["name"] = new_name
        state.experiments[new_id] = proj_data

        # Remove old notebook, write new one
        _remove_notebook(old_id)
        _regen_notebook(proj_data)
    else:
        state.update_experiment(old_id, name=new_name)
        proj["name"] = new_name
        _regen_notebook(proj)

    state.save()

    return {
        "success": True,
        "old_name": old_name,
        "new_name": new_name,
        "old_id": old_id,
        "new_id": new_id,
        "message": f"Renamed '{old_name}' \u2192 '{new_name}'.",
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
        "message": f"Renamed run '{old_name}' \u2192 '{new_name}'.",
    }


def delete_experiment_tool(*, state, identifier: str, confirm: bool = False) -> dict:
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
    state.remove_experiment(proj_id)
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
                        status: str | None = None,
                        key_metric_name: str | None = None) -> dict:
    """Update a project's description, tags, status, or primary metric."""
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
    if key_metric_name is not None:
        updates["key_metric_name"] = key_metric_name

    if not updates:
        return {"error": "No fields to update. Provide description, tags, status, or key_metric_name."}

    state.update_experiment(proj["id"], **updates)
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
    state.update_experiment(proj["id"], linked_papers=linked)

    # Reverse link: store project reference on the paper
    if found_key:
        doc = state.get_document(found_key)
        if doc:
            doc.setdefault("linked_projects", [])
            if proj["id"] not in doc["linked_projects"]:
                doc["linked_projects"].append(proj["id"])

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

    state.update_experiment(proj["id"], goals=goals)
    state.save()

    _regen_notebook(proj)

    return {
        "success": True,
        "project": proj.get("name", ""),
        "goals_count": len(goals),
        "message": f"Set {len(goals)} goal(s) for '{proj.get('name', '')}'.",
    }


def get_run_details_tool(*, state, project: str, run: str) -> dict:
    """Get full details for a single experiment run."""
    proj, err = _resolve_project(state, project)
    if err:
        return err

    runs = proj.get("runs", {})
    run_obj, err = _resolve_run(runs, run, proj.get("name", ""))
    if err:
        return err

    return {
        "found": True,
        "project": proj.get("name", ""),
        "run": {
            "id": run_obj.get("id", ""),
            "name": run_obj.get("name", ""),
            "status": run_obj.get("status", ""),
            "decision": run_obj.get("decision", ""),
            "hypothesis": run_obj.get("hypothesis", ""),
            "reasoning": run_obj.get("reasoning", ""),
            "changes": run_obj.get("changes", ""),
            "hyperparameters": run_obj.get("hyperparameters", {}),
            "results": run_obj.get("results", {}),
            "tags": run_obj.get("tags", []),
            "notes": run_obj.get("notes", []),
            "started_at": run_obj.get("started_at", ""),
            "completed_at": run_obj.get("completed_at", ""),
            "duration_minutes": run_obj.get("duration_minutes", 0),
            "baseline_comparison": run_obj.get("baseline_comparison", {}),
        },
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
