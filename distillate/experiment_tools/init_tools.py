"""Experiment initialization and scaffolding tools."""

import logging
import re
from pathlib import Path as _Path

from ._helpers import _resolve_project, _sanitize_llm_text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas (sent to Claude)
# ---------------------------------------------------------------------------

SCHEMAS = [
    {
        "name": "init_experiment",
        "description": (
            "USE WHEN the user asks to start, scaffold, set up, or design a "
            "new experiment (e.g. 'start a new experiment about X', "
            "'let's set up an ablation on Y'). Scans the directory, drafts "
            "a PROMPT.md with Claude, sets up hooks and tracking. Returns "
            "the draft PROMPT.md for review — show it to the user wrapped "
            "in a `> [!experiment]` callout block, then call "
            "launch_experiment after they approve."
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
                        "(default: directory name, title-cased). "
                        "If the directory name is generic — 'test', 'tmp', 'project', "
                        "'experiment', 'research', 'untitled', 'demo', 'scratch', "
                        "'work', 'code', or similar — derive a meaningful name from "
                        "the goal text instead of using the directory name. "
                        "Good names are short (2-4 words) and describe what the "
                        "experiment is actually doing, e.g. 'Attention Ablation', "
                        "'LoRA Fine-Tuning', 'TinyMatMul Baseline'."
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
                "duration_minutes": {
                    "type": "integer",
                    "description": (
                        "Time budget per iteration in minutes (default: 5). "
                        "Written to .distillate/budget.json; training scripts "
                        "read it at runtime via `from distillate.budget import "
                        "read_train_budget`."
                    ),
                },
            },
            "required": ["path", "goal"],
        },
    },
]


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
                         duration_minutes: int = 5,
                         primary_metric: str = "",
                         metric_direction: str = "",
                         metric_constraint: str = "",
                         workspace_id: str = "") -> dict:
    """Initialize an experiment project with LLM-drafted PROMPT.md."""
    import json as _json
    import subprocess
    from pathlib import Path as _Path

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
    prompt_md = _generate_prompt_md(goal, scan, name, constraints, duration_minutes,
                                    primary_metric, metric_direction, metric_constraint)
    if prompt_md is None:
        # No API key — return context so Claude Code can generate PROMPT.md
        context_parts = [f"**Goal:** {goal}"]
        if name:
            context_parts.append(f"**Project name:** {name}")
        if primary_metric:
            dir_str = metric_direction or "maximize"
            context_parts.append(f"**Primary metric:** `{primary_metric}` ({dir_str})")
        if constraints:
            context_parts.append(f"**Constraints:** {constraints}")
        context_parts.append(f"**Time budget:** {duration_minutes} minutes")
        if scan["files"]:
            context_parts.append(f"**Files:** {', '.join(scan['files'][:30])}")
        if scan["readme"]:
            context_parts.append(f"**README excerpt:**\n{scan['readme'][:500]}")
        return {
            "success": True,
            "needs_prompt_generation": True,
            "prompt_path": str(prompt_file),
            "project_path": str(project_path),
            "context": "\n\n".join(context_parts),
            "template": _PROMPT_MD_SYSTEM,
            "message": (
                "No API key available — please generate PROMPT.md content "
                "using the provided context and template, then write it to "
                f"{prompt_file}. After writing, call init_experiment again "
                "or proceed with the remaining setup steps."
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

    # Create .distillate/ with REPORTING.md. The budget flows through
    # .distillate/budget.json at runtime (scripts read it via
    # distillate.budget.read_train_budget), so no MAX_SECONDS patching here.
    distillate_dir = project_path / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    reporting_src = _Path(__file__).parent.parent / "autoresearch" / "REPORTING.md"
    if reporting_src.exists():
        import shutil
        shutil.copy2(reporting_src, distillate_dir / "REPORTING.md")

    # Install CLAUDE.md (consolidated protocol — auto-loaded by Claude Code)
    claude_md_src = _Path(__file__).parent.parent / "autoresearch" / "CLAUDE.md"
    if claude_md_src.exists():
        import shutil
        shutil.copy2(claude_md_src, project_path / "CLAUDE.md")

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
                    "WebFetch",
                    "WebSearch",
                ],
            },
        }
        settings_local.write_text(
            _json.dumps(local_config, indent=2) + "\n",
            encoding="utf-8",
        )

    # --- Step 5: Register in state ---
    display_name = name or project_path.name.replace("-", " ").replace("_", " ").title()
    experiment_id = slugify(display_name)

    if not state.has_experiment(experiment_id):
        from datetime import datetime, timezone
        acquire_lock()
        try:
            state.reload()
            state.add_experiment(
                experiment_id=experiment_id,
                name=display_name,
                path=str(project_path),
                workspace_id=workspace_id if workspace_id else None,
            )
            state.update_experiment(
                experiment_id,
                last_scanned_at=datetime.now(timezone.utc).isoformat(),
            )
            # Auto-parse goals from the free-form goal string
            parsed_goals = _parse_goals_from_text(goal)
            if parsed_goals:
                state.update_experiment(experiment_id, goals=parsed_goals)
            # Store primary metric name for hero display
            if primary_metric:
                state.update_experiment(experiment_id, key_metric_name=primary_metric)
            if duration_minutes and duration_minutes != 5:
                state.update_experiment(experiment_id, duration_minutes=duration_minutes)
            state.save()
        finally:
            release_lock()

    return {
        "success": True,
        "experiment_id": experiment_id,
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
experiment iteration). Each iteration should fit within this budget. \
**Never hardcode MAX_SECONDS, timeout values, or run durations in training \
scripts.** Always derive them from `.distillate/budget.json` via \
`from distillate.budget import read_train_budget`:

```python
from distillate.budget import read_train_budget
MAX_SECONDS = read_train_budget()  # train_budget_seconds minus a 300s reserve
```

This keeps the in-script guard in sync with the budget when the user \
updates it in the desktop UI -- no script edit required.>

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

## Primary Metric

**You MUST explicitly declare the primary metric in this section.** State:
1. The metric name (e.g. `test_accuracy`, `param_count`, `val_loss`)
2. The optimization direction: minimize or maximize
3. Any conditional constraints (e.g. "minimize param_count, subject to \
test_accuracy >= 99%")

Use this exact format:
```
Primary metric: <metric_name> (minimize|maximize)
Constraints: <metric> >= <threshold> (if any)
```

This is what Distillate uses as the north star metric for charts and \
progress tracking. Getting this wrong means the agent optimizes in the \
wrong direction.

## Evaluation Criteria

<Secondary criteria like methodology quality, code quality, reproducibility>

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
                        duration_minutes: int = 5,
                        primary_metric: str = "",
                        metric_direction: str = "",
                        metric_constraint: str = "") -> str | None:
    """Generate PROMPT.md content, via Claude API if available.

    Returns the generated content, or None if no API credentials are
    available. When called as an MCP tool from Claude Code, the caller
    can generate PROMPT.md itself using the returned context.
    """
    # Build the user message with all context
    parts = [f"**Goal:** {goal}"]

    if name:
        parts.append(f"**Project name:** {name}")

    if primary_metric:
        direction = metric_direction or "maximize"
        metric_line = f"**Primary metric:** `{primary_metric}` ({direction})"
        if metric_constraint:
            metric_line += f"\n**Metric constraint:** {metric_constraint}"
        parts.append(metric_line)

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

    # Try Claude API if credentials are available (sync pipeline use)
    try:
        import anthropic
        from distillate import config
        if config.ANTHROPIC_API_KEY:
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=config.CLAUDE_FAST_MODEL,
                max_tokens=4096,
                system=_PROMPT_MD_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            return response.content[0].text.strip()
    except Exception:
        log.debug("Claude API not available for PROMPT.md generation")

    return None




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
