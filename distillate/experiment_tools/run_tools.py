"""Run lifecycle tools — start_run, conclude_run, paper linking, and enrichment."""

import logging
from pathlib import Path as _Path

from ._helpers import _resolve_project, _run_summary, _sanitize_llm_text, _regen_notebook

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas (sent to Claude)
# ---------------------------------------------------------------------------

SCHEMAS = [
    {
        "name": "replicate_paper",
        "description": (
            "Scaffold a new experiment from a paper in the library. Reads the "
            "paper's abstract, summary, highlights, and GitHub repo (if any), "
            "then creates an experiment with a PROMPT.md that targets "
            "reproducing the paper's key results. Papers with linked GitHub "
            "repos get cloned automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paper": {
                    "type": "string",
                    "description": "Paper identifier (index, citekey, or title)",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Directory for the experiment. Defaults to "
                        "EXPERIMENTS_ROOT/<paper-slug> if not specified."
                    ),
                },
                "goal": {
                    "type": "string",
                    "description": (
                        "Override goal. If not provided, auto-generates a "
                        "replication goal from the paper's reported results."
                    ),
                },
                "constraints": {
                    "type": "string",
                    "description": "Hardware or methodology constraints",
                },
            },
            "required": ["paper"],
        },
    },
    {
        "name": "suggest_from_literature",
        "description": (
            "Suggest experiment steering based on recent paper reads. Scans "
            "papers read in the last 30 days for techniques, methods, or "
            "findings relevant to a given experiment, and suggests concrete "
            "steering instructions. Use when the user asks to apply ideas "
            "from their reading to an experiment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project identifier (index, id, or name)",
                },
                "focus": {
                    "type": "string",
                    "description": (
                        "Optional focus area (e.g., 'regularization', "
                        "'data augmentation'). Narrows the literature search."
                    ),
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "extract_baselines",
        "description": (
            "Extract reported metric baselines from one or more papers. "
            "Reads abstracts, summaries, and highlights to find reported "
            "numbers (accuracy, F1, loss, etc.) that can be used as "
            "experiment goals or comparison points."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "papers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paper identifiers (index, citekey, or title)",
                },
                "metrics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of metric names to look for "
                        "(e.g., ['accuracy', 'F1', 'BLEU']). If not "
                        "specified, extracts all reported metrics."
                    ),
                },
            },
            "required": ["papers"],
        },
    },
    {
        "name": "save_enrichment",
        "description": (
            "Save research insights for an experiment. Writes structured "
            "enrichment data to the project's .distillate/llm_enrichment.json. "
            "Used by the /distill skill after extracting insights from "
            "experimentalist agent session histories. The insights then appear in the "
            "desktop Control Panel."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name or ID",
                },
                "key_breakthrough": {
                    "type": "string",
                    "description": (
                        "One sentence: the metric improvement and what caused it. "
                        "No Greek letters, no parenthetical asides. "
                        "Example: 'F1 improved from 0.42 to 0.76 by adding a 39-bag LDA cascade.'"
                    ),
                },
                "lessons_learned": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "3-5 short sentences. Each starts with the finding, "
                        "then one supporting number. No ALL CAPS headers. "
                        "Write for a smart colleague scanning a dashboard."
                    ),
                },
                "dead_ends": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One sentence each: name the approach and why it failed.",
                },
                "trajectory": {
                    "type": "string",
                    "description": "2-3 sentences: the story arc from baseline to current best.",
                },
                "run_insights": {
                    "type": "object",
                    "description": (
                        "Per-run insights keyed by run ID. Each value has: "
                        "hypothesis, approach, analysis, descriptive_name"
                    ),
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "start_run",
        "description": (
            "Start a new experiment run. Creates a 'running' entry in "
            "runs.jsonl with a timestamp. Call this BEFORE training begins. "
            "Returns the run_id to pass to conclude_run. "
            "If the response contains stop_after_run: true, the user has "
            "requested a graceful stop — do NOT start this run. Exit the "
            "session immediately without training."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, ID, or index number",
                },
                "description": {
                    "type": "string",
                    "description": "One sentence: what you're about to try and why",
                },
                "hypothesis": {
                    "type": "string",
                    "description": "Why you think this approach will work",
                },
                "prediction": {
                    "type": "string",
                    "description": (
                        "What you expect to happen — concrete and falsifiable. "
                        "E.g. 'loss should drop below 0.5 since we doubled "
                        "model capacity'."
                    ),
                },
                "predicted_metric": {
                    "type": "string",
                    "description": (
                        "Name of the metric you're predicting (must match a "
                        "key in results). E.g. 'val_loss', 'f1', 'test_accuracy'."
                    ),
                },
                "predicted_value": {
                    "type": "number",
                    "description": (
                        "Your numeric prediction for the metric. E.g. 0.5, 0.85. "
                        "Enables machine-readable prediction accuracy tracking."
                    ),
                },
                "confidence": {
                    "type": "integer",
                    "description": (
                        "How confident you are in this prediction (0-100). "
                        "Used for calibration tracking — do your 70%-confidence "
                        "predictions come true ~70% of the time?"
                    ),
                    "minimum": 0,
                    "maximum": 100,
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "Evidence for your prediction — reference prior run IDs, "
                        "papers, or data. E.g. 'xp-abc showed lr=0.01 cut loss "
                        "30%; same effect expected here'."
                    ),
                },
            },
            "required": ["project", "description", "prediction"],
        },
    },
    {
        "name": "conclude_run",
        "description": (
            "Conclude an experiment run with results. Appends a completed "
            "entry to runs.jsonl with status, results, timing, and reasoning. "
            "Call this AFTER training finishes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name, ID, or index number",
                },
                "run_id": {
                    "type": "string",
                    "description": "The run_id returned by start_run",
                },
                "status": {
                    "type": "string",
                    "enum": ["crash"],
                    "description": (
                        "Only pass 'crash' for complete failures with no "
                        "results. Omit for normal runs — best/completed "
                        "is auto-detected from the key metric frontier."
                    ),
                },
                "results": {
                    "type": "object",
                    "description": "Metric results as key-value pairs, e.g. {\"f1\": 0.85, \"loss\": 0.12}",
                },
                "reasoning": {
                    "type": "string",
                    "description": "2-3 sentences: what worked, what didn't, what you learned. Be specific with numbers.",
                },
                "hyperparameters": {
                    "type": "object",
                    "description": "Hyperparameters used for this run",
                },
                "changes": {
                    "type": "string",
                    "description": "What changed from the previous run",
                },
                "outcome": {
                    "type": "string",
                    "description": (
                        "One sentence: what actually happened vs your "
                        "prediction. E.g. 'loss hit 0.38, beating the 0.5 "
                        "prediction — extra capacity helped more than expected'."
                    ),
                },
                "inspired_by": {
                    "type": "string",
                    "description": (
                        "Paper citekey or title that inspired this run. "
                        "When set, the run is credited to the paper and the "
                        "paper is auto-linked to the project."
                    ),
                },
                "verdict": {
                    "type": "string",
                    "enum": ["confirmed", "refuted", "inconclusive"],
                    "description": (
                        "Was the prediction confirmed, refuted, or "
                        "inconclusive? Auto-detected from numeric prediction "
                        "if omitted."
                    ),
                },
                "belief_update": {
                    "type": "string",
                    "description": (
                        "What changed in your understanding — feeds your "
                        "next prediction. E.g. 'lr sensitivity is lower than "
                        "expected; batch size matters more'."
                    ),
                },
            },
            "required": ["project", "run_id", "results", "reasoning", "outcome"],
        },
    },
    {
        "name": "discover_relevant_papers",
        "description": (
            "Search the user's paper library for papers relevant to an "
            "experiment project. Uses the project's description, goals, "
            "and latest learnings to find keyword matches in titles, tags, "
            "and summaries. Returns candidate papers with relevance reasons."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project id, name substring, or index number",
                },
            },
            "required": ["project"],
        },
    },
    {
        "name": "purge_hook_runs",
        "description": "Remove all hook-inferred spurious runs from a project, keeping only structured (runs.jsonl) runs. Use when a project has accumulated noise from manual script executions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project identifier"},
                "confirm": {"type": "boolean", "description": "Must be true to execute", "default": False},
            },
            "required": ["project"],
        },
    },
]


# ---------------------------------------------------------------------------
# Paper-experiment integration tools
# ---------------------------------------------------------------------------

def _gather_paper_context(state, identifier: str) -> dict | None:
    """Gather full paper context for experiment scaffolding.

    Returns a dict with title, authors, abstract, summary, highlights,
    github_repo, and citekey — or None if the paper is not found.
    """
    from distillate.tools import (
        _extract_highlights_from_note,
        _find_papers_from_state,
        _read_note_content,
    )

    matches = _find_papers_from_state(identifier, state)
    if not matches:
        return None

    key, doc = matches[0]
    meta = doc.get("metadata", {})
    citekey = meta.get("citekey", "")

    highlights = ""
    note_content = _read_note_content(citekey, doc.get("title", ""))
    if note_content:
        highlights = _extract_highlights_from_note(note_content)

    return {
        "key": key,
        "title": doc.get("title", ""),
        "authors": doc.get("authors", []),
        "abstract": meta.get("abstract", ""),
        "summary": doc.get("summary", ""),
        "highlights": highlights,
        "github_repo": meta.get("github_repo", ""),
        "github_stars": meta.get("github_stars"),
        "citekey": citekey,
        "tags": meta.get("tags", []),
        "citation_count": meta.get("citation_count", 0),
    }


def replicate_paper(*, state, paper: str, path: str = "",
                    goal: str = "", constraints: str = "") -> dict:
    """Scaffold an experiment to replicate a paper's results."""
    import subprocess
    from pathlib import Path as _Path

    from distillate import config
    from distillate.experiments import slugify

    from .init_tools import init_experiment_tool

    ctx = _gather_paper_context(state, paper)
    if ctx is None:
        return {"success": False, "error": f"No paper found matching '{paper}'"}

    # Determine experiment path
    if not path:
        root = config.EXPERIMENTS_ROOT
        if not root:
            return {
                "success": False,
                "error": (
                    "No path specified and EXPERIMENTS_ROOT not set. "
                    "Provide a path or set EXPERIMENTS_ROOT in .env."
                ),
            }
        slug = slugify(ctx["title"][:60])
        path = str(_Path(root) / slug)

    project_path = _Path(path).expanduser().resolve()

    # Clone GitHub repo if available and directory doesn't exist yet
    cloned = False
    if ctx["github_repo"] and not project_path.exists():
        repo_url = ctx["github_repo"]
        if not repo_url.startswith("http"):
            repo_url = f"https://github.com/{repo_url}"
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(project_path)],
                capture_output=True, text=True, timeout=60,
            )
            cloned = result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Build a replication-focused goal
    if not goal:
        parts = [f"Reproduce the key results from '{ctx['title']}'."]
        if ctx["summary"]:
            parts.append(f"Paper summary: {ctx['summary'][:300]}")
        if ctx["highlights"]:
            parts.append(
                "Focus on the methods and findings highlighted by the reader."
            )
        goal = " ".join(parts)

    # Build constraints with paper context
    paper_context = f"Replicating: {ctx['title']}"
    if ctx["authors"]:
        authors_str = ", ".join(ctx["authors"][:3])
        paper_context += f" by {authors_str}"
    if constraints:
        paper_context += f". {constraints}"

    # Call init_experiment with paper-enriched context
    result = init_experiment_tool(
        state=state,
        path=str(project_path),
        goal=goal,
        name=f"Replicate: {ctx['title'][:50]}",
        constraints=paper_context,
    )

    if not result.get("success"):
        return result

    # Auto-link the paper to the new project
    experiment_id = result.get("experiment_id", "")
    if experiment_id:
        link_ref = ctx["citekey"] or ctx["title"]
        proj = state.get_experiment(experiment_id)
        if proj:
            linked = proj.get("linked_papers", [])
            if link_ref not in linked:
                linked.append(link_ref)
                state.update_experiment(experiment_id, linked_papers=linked)
                # Reverse link on the paper
                doc = state.get_document(ctx["key"])
                if doc:
                    doc.setdefault("linked_projects", [])
                    if experiment_id not in doc["linked_projects"]:
                        doc["linked_projects"].append(experiment_id)
                state.save()

    result["paper"] = ctx["title"]
    result["cloned_repo"] = cloned
    if cloned:
        result["message"] = (
            f"Cloned {ctx['github_repo']} and initialized experiment. "
            + result.get("message", "")
        )

    return result


def suggest_from_literature(*, state, project: str,
                            focus: str = "") -> dict:
    """Suggest experiment steering based on recent paper reads."""
    from datetime import datetime, timedelta, timezone

    from distillate.tools import (
        _extract_highlights_from_note,
        _read_note_content,
    )

    proj, err = _resolve_project(state, project)
    if err:
        return err

    # Gather recent reads (last 30 days)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()
    recent = state.documents_processed_since(since)
    if not recent:
        return {
            "suggestions": [],
            "message": "No papers read in the last 30 days to draw from.",
        }

    # Build context for each recent paper
    paper_contexts = []
    for doc in reversed(recent[:10]):  # Most recent first, cap at 10
        meta = doc.get("metadata", {})
        citekey = meta.get("citekey", "")
        title = doc.get("title", "")

        parts = [f"**{title}**"]
        if doc.get("summary"):
            parts.append(doc["summary"][:200])

        note = _read_note_content(citekey, title)
        if note:
            hl = _extract_highlights_from_note(note)
            if hl:
                parts.append(hl[:500])

        paper_contexts.append("\n".join(parts))

    # Build experiment context
    runs = proj.get("runs", {})
    best_run = None
    if runs:
        completed = [r for r in runs.values() if r.get("status") == "completed"]
        if completed:
            best_run = max(
                completed,
                key=lambda r: max(r.get("results", {}).values(), default=0)
                if r.get("results") else 0,
            )

    exp_context = f"Experiment: {proj.get('name', '')}"
    if proj.get("description"):
        exp_context += f"\nDescription: {proj['description']}"
    goals = proj.get("goals", [])
    if goals:
        goal_strs = [
            f"{g['metric']} {g['direction']} {g.get('threshold', '?')}"
            for g in goals
        ]
        exp_context += f"\nGoals: {', '.join(goal_strs)}"
    if best_run:
        exp_context += f"\nBest run: {best_run.get('name', '')} — {best_run.get('results', {})}"

    # Return raw data — Claude Code (the caller) synthesizes suggestions
    return {
        "success": True,
        "project": proj.get("name", ""),
        "experiment_context": exp_context,
        "focus": focus,
        "paper_contexts": paper_contexts,
        "papers_consulted": len(paper_contexts),
        "message": (
            f"Gathered context from {len(paper_contexts)} recent papers "
            f"for experiment '{proj.get('name', '')}'. "
            "Synthesize 2-3 concrete steering suggestions based on "
            "techniques, methods, or findings from these papers."
        ),
    }


def extract_baselines(*, state, papers: list[str],
                      metrics: list[str] | None = None) -> dict:
    """Extract reported metric baselines from papers."""
    from distillate.tools import (
        _extract_highlights_from_note,
        _find_papers_from_state,
        _read_note_content,
    )

    paper_texts = []
    titles_used = []

    for ident in papers:
        matches = _find_papers_from_state(ident, state)
        if not matches:
            continue
        key, doc = matches[0]
        title = doc.get("title", "")
        titles_used.append(title)
        meta = doc.get("metadata", {})

        parts = [f"Title: {title}"]
        if meta.get("abstract"):
            parts.append(f"Abstract: {meta['abstract'][:800]}")
        if doc.get("summary"):
            parts.append(f"Summary: {doc['summary']}")

        citekey = meta.get("citekey", "")
        note = _read_note_content(citekey, title)
        if note:
            hl = _extract_highlights_from_note(note)
            if hl:
                parts.append(hl[:1500])

        paper_texts.append("\n".join(parts))

    if not paper_texts:
        return {"error": "No matching papers found.", "papers_used": []}

    # Return raw data — Claude Code (the caller) extracts baselines
    return {
        "success": True,
        "paper_texts": paper_texts,
        "papers_used": titles_used,
        "target_metrics": metrics or [],
        "message": (
            f"Gathered text from {len(titles_used)} papers. "
            "Extract all reported quantitative results (metrics, baselines, "
            "benchmarks). For each metric, provide: paper_title, metric, "
            "value, context (model/method), and direction (maximize/minimize)."
        ),
    }


def save_enrichment(
    *, state, project: str,
    key_breakthrough: str = "",
    lessons_learned: list[str] | None = None,
    dead_ends: list[str] | None = None,
    trajectory: str = "",
    run_insights: dict | None = None,
) -> dict:
    """Save research insights to a project's enrichment cache.

    Writes to .distillate/llm_enrichment.json so insights appear
    in the desktop Control Panel and lab notebooks.
    """
    import json
    from pathlib import Path as _Path

    from distillate.experiments import _runs_fingerprint

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"success": False, "error": "Project has no path set."}

    path = _Path(proj_path)
    distillate_dir = path / ".distillate"
    distillate_dir.mkdir(parents=True, exist_ok=True)
    cache_path = distillate_dir / "llm_enrichment.json"

    # Build enrichment structure
    runs = proj.get("runs", {})
    fingerprint = _runs_fingerprint(runs)

    enrichment = {
        "project": {},
        "runs": run_insights or {},
    }
    if key_breakthrough:
        enrichment["project"]["key_breakthrough"] = key_breakthrough
    if lessons_learned:
        enrichment["project"]["lessons_learned"] = lessons_learned
    if dead_ends:
        enrichment["project"]["dead_ends"] = dead_ends
    if trajectory:
        enrichment["project"]["trajectory"] = trajectory

    cache = {
        "fingerprint": fingerprint,
        "enrichment": enrichment,
    }

    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "success": True,
        "project": proj.get("name", project),
        "path": str(cache_path),
        "message": (
            f"Saved enrichment for '{proj.get('name', project)}': "
            f"{len(enrichment['project'])} project insights, "
            f"{len(enrichment['runs'])} run insights."
        ),
    }


def _collect_metrics_series(project_path, *, started_at: str, completed_at: str = "") -> list[dict]:
    """Read events.jsonl and return metric_update events for this run.

    Used by ``conclude_run`` to freeze this run's per-epoch training
    metrics onto the completion entry, so per-row sparklines and
    convergence classifiers can render without replaying events.jsonl
    and re-matching timestamps.

    Event attribution: any ``metric_update`` event with ``ts >=
    started_at`` belongs to *this* run. We don't upper-bound by
    ``completed_at`` -- in practice conclude_run is called milliseconds
    after the final event writes, and clock skew between the hook's
    timestamp and the completion's wall clock can push real events
    past the "end" window. The next ``start_run`` naturally partitions
    future events to the next run, so this lower-bound-only semantics
    is both simpler and more correct.

    ``completed_at`` is accepted for API clarity (callers document the
    intended window) but is not actually used to exclude events.
    """
    import json
    from datetime import datetime
    from pathlib import Path as _Path

    events_path = _Path(project_path) / ".distillate" / "events.jsonl"
    if not events_path.exists():
        return []

    try:
        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return []

    series: list[dict] = []
    try:
        text = events_path.read_text(encoding="utf-8")
    except OSError:
        return []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "metric_update":
            continue
        ts = e.get("ts", "")
        if not ts:
            continue
        try:
            e_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if e_dt >= start_dt:
            entry = {"ts": ts, "metrics": e.get("metrics", {})}
            if e.get("epoch") is not None:
                entry["epoch"] = e["epoch"]
            if e.get("step") is not None:
                entry["step"] = e["step"]
            series.append(entry)
    return series


def start_run(
    *, state, project: str, description: str, hypothesis: str = "",
    prediction: str = "",
    predicted_metric: str = "",
    predicted_value: float | None = None,
    confidence: int | None = None,
    rationale: str = "",
) -> dict:
    """Start a new experiment run — creates a 'running' entry in runs.jsonl."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"success": False, "error": "Project has no path set."}

    path = _Path(proj_path)
    distillate_dir = path / ".distillate"
    distillate_dir.mkdir(parents=True, exist_ok=True)
    runs_jsonl = distillate_dir / "runs.jsonl"

    # Graceful stop: if the user requested stop-after-run, consume the flag
    # and return without writing any run entry.
    stop_flag = distillate_dir / "stop_requested"
    if stop_flag.exists():
        try:
            stop_flag.unlink()
        except OSError:
            pass
        return {
            "success": True,
            "stop_after_run": True,
            "message": (
                "Stop requested by user. Do not start this run — "
                "exit the session cleanly."
            ),
        }

    # Determine next run ID
    existing_ids = set()
    max_run_num = 0
    if runs_jsonl.exists():
        for line in runs_jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                existing_ids.add(entry.get("id", ""))
                rn = entry.get("run_number")
                if isinstance(rn, (int, float)):
                    max_run_num = max(max_run_num, int(rn))
            except json.JSONDecodeError:
                pass

    import hashlib
    # Use max(run_number)+1 as the canonical next run number so backfill
    # entries and orphaned "running" entries (which carry no run_number)
    # don't inflate the counter. Falls back to unique-ID count for legacy
    # files predating run_number.
    n = (max_run_num + 1) if max_run_num > 0 else (len(existing_ids) + 1)
    seed = f"{datetime.now(timezone.utc).isoformat()}-{n}"
    slug = hashlib.sha256(seed.encode()).hexdigest()[:6]
    run_id = f"xp-{slug}"
    while run_id in existing_ids:
        seed += "x"
        slug = hashlib.sha256(seed.encode()).hexdigest()[:6]
        run_id = f"xp-{slug}"

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()

    # Resolve train + wrap budgets: prefer .distillate/budget.json, fall back
    # to the project's duration_minutes so legacy projects still get deadlines.
    budget_path = distillate_dir / "budget.json"
    train_secs: int = 0
    wrap_secs: int = 0
    if budget_path.exists():
        try:
            bdata = json.loads(budget_path.read_text(encoding="utf-8"))
            train_secs = int(bdata.get("train_budget_seconds")
                             or bdata.get("run_budget_seconds") or 0)
            wrap_secs = int(bdata.get("wrap_budget_seconds") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    if not train_secs:
        train_secs = int((proj.get("duration_minutes") or 5) * 60)
    if not wrap_secs:
        wrap_secs = max(60, int(train_secs * 0.1))

    from datetime import timedelta as _td
    train_deadline_dt = now_dt + _td(seconds=train_secs)
    wrap_deadline_dt = train_deadline_dt + _td(seconds=wrap_secs)

    entry = {
        "$schema": "distillate/run/v1",
        "id": run_id,
        "run_number": n,
        "timestamp": now,
        "started_at": now,
        "train_deadline_at": train_deadline_dt.isoformat(),
        "wrap_deadline_at": wrap_deadline_dt.isoformat(),
        "status": "running",
        "description": _sanitize_llm_text(description),
    }
    if hypothesis:
        entry["hypothesis"] = _sanitize_llm_text(hypothesis)
    if prediction:
        entry["prediction"] = _sanitize_llm_text(prediction)

    # Structured pre-registration fields
    if predicted_metric:
        entry["predicted_metric"] = predicted_metric
    if predicted_value is not None:
        entry["predicted_value"] = predicted_value
        # Auto-detect direction from metric name
        if predicted_metric:
            from distillate.experiments import _is_lower_better
            entry["predicted_direction"] = (
                "below" if _is_lower_better(predicted_metric) else "above"
            )
    if confidence is not None:
        entry["confidence"] = max(0, min(100, int(confidence)))
    if rationale:
        entry["rationale"] = _sanitize_llm_text(rationale)

    with open(runs_jsonl, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "success": True,
        "run_id": run_id,
        "run_number": n,
        "started_at": now,
        "train_deadline_at": train_deadline_dt.isoformat(),
        "wrap_deadline_at": wrap_deadline_dt.isoformat(),
        "project": proj.get("name", project),
        "message": f"Run {n} ({run_id}) started: {description}",
    }


def conclude_run(
    *, state, project: str, run_id: str, status: str = "",
    results: dict, reasoning: str,
    outcome: str = "",
    hyperparameters: dict | None = None,
    changes: str = "",
    inspired_by: str = "",
    verdict: str = "",
    belief_update: str = "",
) -> dict:
    """Conclude an experiment run — appends completed entry to runs.jsonl.

    Auto-detects ``best`` vs ``completed`` by comparing the key metric
    against the frontier of prior ``best`` runs.  Pass ``status="crash"``
    explicitly for runs that failed with no output.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    proj, err = _resolve_project(state, project)
    if err:
        return err

    proj_path = proj.get("path", "")
    if not proj_path:
        return {"success": False, "error": "Project has no path set."}

    path = _Path(proj_path)
    runs_jsonl = path / ".distillate" / "runs.jsonl"

    # Find the start entry to compute duration and carry forward fields
    started_at = None
    start_prediction = ""
    start_run_number: int | None = None
    start_prereg: dict = {}
    if runs_jsonl.exists():
        for line in runs_jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("id") == run_id and entry.get("status") == "running":
                    started_at = entry.get("started_at", entry.get("timestamp"))
                    start_prediction = entry.get("prediction", "")
                    # Carry forward the canonical run_number so the
                    # completion entry and downstream consumers see the
                    # same integer the agent was told at start_run time.
                    if entry.get("run_number") is not None:
                        try:
                            start_run_number = int(entry["run_number"])
                        except (TypeError, ValueError):
                            pass
                    # Carry forward structured pre-registration fields
                    for fld in ("predicted_metric", "predicted_value",
                                "predicted_direction", "confidence", "rationale"):
                        val = entry.get(fld)
                        if val is not None:
                            start_prereg[fld] = val
            except json.JSONDecodeError:
                pass

    now = datetime.now(timezone.utc).isoformat()

    # ── Auto-detect best vs completed ──
    # Accept explicit "crash" (or legacy "keep") but auto-compute otherwise
    is_best = False
    if status == "crash":
        final_status = "crash"
    elif status == "keep":
        # Legacy caller — treat as auto-detect
        final_status = "completed"
    else:
        final_status = "completed"

    # Captured for metric delta in notebook entry below
    _nb_frontier_val = None
    _nb_metric_key = None
    _nb_metric_val = None

    if final_status != "crash" and results:
        from distillate.experiments import infer_key_metric_name, _is_lower_better
        key_metric = infer_key_metric_name(proj)
        val = results.get(key_metric) if key_metric else None
        if isinstance(val, (int, float)):
            lower_better = _is_lower_better(key_metric)
            # Find frontier value from prior best runs in runs.jsonl
            frontier_val = None
            if runs_jsonl.exists():
                for line in runs_jsonl.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        prev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if prev.get("status") not in ("best", "keep"):
                        continue
                    pv = prev.get("results", {}).get(key_metric)
                    if not isinstance(pv, (int, float)):
                        continue
                    if frontier_val is None:
                        frontier_val = pv
                    elif lower_better:
                        frontier_val = min(frontier_val, pv)
                    else:
                        frontier_val = max(frontier_val, pv)

            if frontier_val is None:
                # First run with this metric → best
                is_best = True
            elif lower_better and val < frontier_val:
                is_best = True
            elif not lower_better and val > frontier_val:
                is_best = True

            _nb_frontier_val = frontier_val
            _nb_metric_key = key_metric
            _nb_metric_val = val

        elif key_metric == "" and final_status != "crash":
            # No key metric at all — first run is best
            has_any_prior = False
            if runs_jsonl.exists():
                for line in runs_jsonl.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        prev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if prev.get("status") in ("best", "completed", "keep", "discard"):
                        has_any_prior = True
                        break
            if not has_any_prior:
                is_best = True

        if is_best:
            final_status = "best"

    entry = {
        "$schema": "distillate/run/v1",
        "id": run_id,
        "timestamp": now,
        "status": final_status,
        "description": _sanitize_llm_text(changes) or f"{run_id} completed",
        "results": results,
        "reasoning": _sanitize_llm_text(reasoning),
        "completed_at": now,
    }
    if start_run_number is not None:
        entry["run_number"] = start_run_number
    if started_at:
        entry["started_at"] = started_at
        try:
            from datetime import datetime as _dt
            start = _dt.fromisoformat(started_at)
            end = _dt.fromisoformat(now)
            entry["duration_seconds"] = round((end - start).total_seconds())
        except Exception:
            log.debug("Failed to calculate run duration", exc_info=True)
    if start_prediction:
        entry["prediction"] = start_prediction
    if outcome:
        entry["outcome"] = _sanitize_llm_text(outcome)
    if hyperparameters:
        entry["hyperparameters"] = hyperparameters
    if changes:
        entry["changes"] = changes
    if inspired_by:
        entry["inspired_by"] = inspired_by

    # Carry forward structured pre-registration fields from start entry
    for fld, val in start_prereg.items():
        entry[fld] = val

    # Auto-compute prediction error and verdict
    pm = entry.get("predicted_metric")
    pv = entry.get("predicted_value")
    if pm and pv is not None:
        actual = results.get(pm)
        if isinstance(actual, (int, float)):
            entry["prediction_error"] = round(abs(pv - actual), 6)
            if actual != 0:
                entry["prediction_error_pct"] = round(
                    abs(pv - actual) / abs(actual), 4
                )
            # Auto-detect verdict if not provided by agent
            if not verdict:
                direction = entry.get("predicted_direction", "below")
                if direction == "below":
                    verdict = "confirmed" if actual <= pv else "refuted"
                else:
                    verdict = "confirmed" if actual >= pv else "refuted"

    if verdict:
        entry["verdict"] = verdict
    if belief_update:
        entry["belief_update"] = _sanitize_llm_text(belief_update)

    # Budget overrun annotation
    if "duration_seconds" in entry:
        run_budget = (proj.get("duration_minutes") or 5) * 60
        overrun = entry["duration_seconds"] - run_budget
        if overrun > 0:
            entry["budget_overrun_seconds"] = overrun

    # Freeze the per-epoch metric_update events that belong to this run
    # (by timestamp window) onto the completion entry. This is the data
    # source for per-row sparklines in the Results tab and the
    # convergence-shape classifier. Done best-effort: no events.jsonl or
    # a parse error just means no series attached.
    if started_at:
        try:
            series = _collect_metrics_series(
                path, started_at=started_at, completed_at=now,
            )
            if series:
                entry["metrics_series"] = series
        except Exception:
            log.debug("Failed to collect metrics_series", exc_info=True)

    with open(runs_jsonl, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Add rich notebook entry
    try:
        from distillate.lab_notebook import append_entry as _append_entry

        n = entry.get("run_number", "?")
        run_status = entry.get("status", "completed")

        # Key metric — try agent-reported results first, then fall back to
        # whatever post_bash.py parsed from the training output in events.jsonl.
        metric_str = None
        if results:
            for k, v in results.items():
                if isinstance(v, (int, float)):
                    metric_str = f"{k}={v:.4g}"
                    break

        if not metric_str and started_at:
            events_path = path / ".distillate" / "events.jsonl"
            if events_path.exists():
                try:
                    start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    for raw in reversed(events_path.read_text(encoding="utf-8").splitlines()):
                        raw = raw.strip()
                        if not raw:
                            continue
                        evt = json.loads(raw)
                        if evt.get("type") != "run_completed":
                            continue
                        ts = evt.get("ts", "")
                        if not ts:
                            continue
                        e_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if e_dt < start_dt:
                            break  # passed our window; event is from a prior run
                        for k, v in (evt.get("results") or {}).items():
                            if isinstance(v, (int, float)):
                                metric_str = f"{k}={v:.4g}"
                                break
                        break  # used the most-recent run_completed in this window
                except Exception:
                    pass

        if not metric_str:
            metric_str = "(no metrics)"

        # Append metric delta vs previous best for "best" runs
        if (
            run_status == "best"
            and _nb_frontier_val is not None
            and _nb_metric_val is not None
        ):
            delta = _nb_metric_val - _nb_frontier_val
            sign = "+" if delta >= 0 else ""
            metric_str += f" ({sign}{delta:.4g})"

        lines = [f"Run {n} [{run_status}]: {metric_str}"]

        # Trim at word boundary, strip dangling em-dash. Generous budgets —
        # the client renders multi-line and wraps; we just want to cap
        # runaway paragraphs.
        def _nb_trim(text: str, max_len: int) -> str:
            text = text.strip().rstrip("—").rstrip()
            if len(text) <= max_len:
                return text
            return text[:max_len].rsplit(" ", 1)[0].rstrip("—").rstrip() + "…"

        # Change — what was tried in this run. The single most-valuable
        # per-run signal after the metric itself, so surface it first.
        description = (entry.get("description") or "").strip()
        if description:
            lines.append(f"Change: {_nb_trim(description, 220)}")

        # Prediction → verdict
        pm = entry.get("predicted_metric")
        pv = entry.get("predicted_value")
        conf = entry.get("confidence")
        verdict = entry.get("verdict")

        if pm and pv is not None:
            dir_sym = "<" if entry.get("predicted_direction") == "below" else ">"
            lines.append(f"Prediction: {dir_sym} {pv} ({conf}%) → {verdict}")

        # Belief update is the distilled learning; keep it full-length.
        if belief_update:
            lines.append(f"Belief: {_nb_trim(belief_update, 280)}")
            
        _append_entry(
            " · ".join(lines),
            entry_type="run_completed",
            project=proj.get("name", project)
        )
    except Exception:
        pass  # Notebook is non-critical

    # Write calibration entry when both confidence and verdict exist
    if entry.get("confidence") is not None and entry.get("verdict"):
        pm_name = entry.get("predicted_metric", "")
        actual_val = results.get(pm_name) if pm_name else None
        if isinstance(actual_val, (int, float)):
            cal_entry = {
                "run_id": run_id,
                "timestamp": now,
                "predicted_metric": pm_name,
                "predicted_value": entry.get("predicted_value"),
                "actual_value": actual_val,
                "confidence": entry["confidence"],
                "verdict": entry["verdict"],
                "prediction_error": entry.get("prediction_error"),
            }
            cal_file = path / ".distillate" / "calibration.jsonl"
            try:
                with open(cal_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(cal_entry, ensure_ascii=False) + "\n")
            except OSError:
                log.debug("Failed to write calibration entry", exc_info=True)

    # Auto-link the inspiring paper to the project
    linked_paper = ""
    if inspired_by:
        try:
            from distillate.experiment_tools import link_paper_tool
            link_result = link_paper_tool(
                state=state, project=project, paper=inspired_by,
            )
            if link_result.get("success"):
                linked_paper = link_result.get("paper", inspired_by)
        except Exception:
            log.debug("Failed to auto-link paper '%s'", inspired_by, exc_info=True)

    duration_str = ""
    overrun_str = ""
    if "duration_seconds" in entry:
        m, s = divmod(entry["duration_seconds"], 60)
        duration_str = f" ({m}m {s}s)"
        if "budget_overrun_seconds" in entry:
            overrun_str = f" (ran {entry['budget_overrun_seconds']}s over budget)"

    # Upload checkpoint on [best] runs if checkpoint files exist
    checkpoint_url = ""
    if is_best and proj_path:
        try:
            from pathlib import Path
            from distillate.checkpoints import upload_checkpoint_if_exists
            storage_type = proj.get("checkpoint_storage", "github")
            url = upload_checkpoint_if_exists(
                Path(proj_path), run_id, storage_type=storage_type,
            )
            if url:
                checkpoint_url = url
        except Exception:
            log.debug("Checkpoint upload failed for %s", run_id, exc_info=True)

    # Build suggested commit message with prediction loop
    desc_for_msg = _sanitize_llm_text(changes) or entry.get("description", run_id)
    metric_str = ""
    if results:
        from distillate.experiments import infer_key_metric_name as _ikmn
        km = _ikmn(proj) or next(
            (k for k, v in results.items() if isinstance(v, (int, float))), ""
        )
        if km and isinstance(results.get(km), (int, float)):
            metric_str = f"{km}={results[km]}"

    pred_str = ""
    if entry.get("predicted_value") is not None and entry.get("verdict"):
        sym = {"below": "<", "above": ">"}.get(
            entry.get("predicted_direction", "below"), "~"
        )
        pred_str = (
            f" (predicted {sym}{entry['predicted_value']}, "
            f"{entry['verdict']})"
        )

    prefix = "[best] " if is_best else ""
    suggested_msg = f"{prefix}{desc_for_msg}: {metric_str}{pred_str}".rstrip(": ")

    # Calibration summary from calibration.jsonl
    calibration_str = ""
    cal_file = path / ".distillate" / "calibration.jsonl"
    if cal_file.exists():
        try:
            cal_total = cal_confirmed = 0
            for line in cal_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    c = json.loads(line)
                    cal_total += 1
                    if c.get("verdict") == "confirmed":
                        cal_confirmed += 1
                except json.JSONDecodeError:
                    pass
            if cal_total >= 3:
                pct = 100 * cal_confirmed // cal_total
                calibration_str = (
                    f"{cal_confirmed}/{cal_total} predictions confirmed ({pct}%)"
                )
        except OSError:
            pass

    result = {
        "success": True,
        "run_id": run_id,
        "status": final_status,
        "is_best": is_best,
        "duration": duration_str.strip(),
        "project": proj.get("name", project),
        "message": (
            f"Run {start_run_number} ({run_id}) concluded: "
            f"{final_status}{duration_str}{overrun_str}"
            if start_run_number is not None
            else f"Run {run_id} concluded: {final_status}{duration_str}{overrun_str}"
        ),
        "suggested_commit_msg": suggested_msg,
    }
    if start_run_number is not None:
        result["run_number"] = start_run_number
    if entry.get("prediction_error") is not None:
        result["prediction_error"] = entry["prediction_error"]
    if entry.get("verdict"):
        result["verdict"] = entry["verdict"]
    if calibration_str:
        result["calibration"] = calibration_str
    if linked_paper:
        result["linked_paper"] = linked_paper
    if checkpoint_url:
        result["checkpoint_url"] = checkpoint_url
    return result


def discover_relevant_papers(*, state, project: str) -> dict:
    """Search the user's paper library for papers relevant to a project."""
    from distillate.experiment_tools._helpers import extract_experiment_keywords

    proj, err = _resolve_project(state, project)
    if err:
        return err

    keywords = extract_experiment_keywords(proj)

    if not keywords:
        return {
            "candidates": [],
            "message": "No project context available to search against. Add a description or goals first.",
        }

    # Search papers for matches
    already_linked = set(p.lower() for p in proj.get("linked_papers", []))
    candidates = []

    for key, doc in state.documents.items():
        if doc.get("status") != "processed":
            continue
        meta = doc.get("metadata", {})
        citekey = meta.get("citekey", "")
        title = doc.get("title", "")

        # Skip already-linked papers
        if citekey.lower() in already_linked or title.lower() in already_linked:
            continue

        # Build searchable text for this paper
        paper_text = " ".join([
            title,
            " ".join(meta.get("tags", [])),
            doc.get("summary", "") or "",
            meta.get("abstract", "") or "",
        ]).lower()

        # Score: count keyword matches
        matched = [kw for kw in keywords if kw in paper_text]
        if len(matched) >= 2:
            candidates.append({
                "citekey": citekey,
                "title": title,
                "index": state.index_of(key),
                "match_count": len(matched),
                "matched_keywords": matched[:5],
                "reason": f"Matches on: {', '.join(matched[:5])}",
            })

    # Sort by match count descending
    candidates.sort(key=lambda c: c["match_count"], reverse=True)
    candidates = candidates[:10]

    return {
        "project": proj.get("name", ""),
        "keywords_used": keywords[:10],
        "candidates": candidates,
        "total_candidates": len(candidates),
    }


def purge_hook_runs_tool(*, state, project: str, confirm: bool = False) -> dict:
    """Remove all hook-inferred runs from a project."""
    proj, err = _resolve_project(state, project)
    if err:
        return err

    runs = proj.get("runs", {})
    hook_run_ids = [rid for rid, r in runs.items() if r.get("source") == "hooks"]

    if not hook_run_ids:
        return {"success": True, "message": "No hook runs found.", "removed": 0}

    if not confirm:
        return {
            "success": False,
            "confirm_required": True,
            "hook_runs": len(hook_run_ids),
            "total_runs": len(runs),
            "message": f"Found {len(hook_run_ids)} hook-inferred runs out of {len(runs)} total. Pass confirm=true to remove them.",
        }

    for rid in hook_run_ids:
        del runs[rid]

    state.update_experiment(proj["id"], runs=runs)
    state.save()

    return {
        "success": True,
        "removed": len(hook_run_ids),
        "remaining": len(runs),
        "message": f"Removed {len(hook_run_ids)} hook runs. {len(runs)} runs remaining.",
    }
