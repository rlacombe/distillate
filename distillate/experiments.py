"""ML experiment tracking for Distillate.

Discovers ML projects in git repos, reconstructs experiment history from
artifacts (training logs, configs, checkpoints, results), and generates
rich markdown lab notebooks.

Ported and adapted from rlacombe/ml-notebook.
"""

import json
import logging
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Directories to skip when walking repos
_SKIP_DIRS = {
    "__pycache__", ".git", ".mlnotebook", "node_modules",
    "venv", "env", ".venv", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", "egg-info",
}

# File patterns that indicate an ML project
_ML_FILE_PATTERNS = [
    r"train.*\.py$", r"run.*\.py$", r"experiment.*\.py$",
    r"finetune.*\.py$", r"sweep.*\.py$",
    r"config.*\.(yaml|yml|json|toml)$",
    r".*\.(pt|pth|ckpt|safetensors|bin)$",
    r"wandb/", r"mlruns/", r"tensorboard/",
]

# Keywords in Python files suggesting ML code
_ML_IMPORT_KEYWORDS = {
    "torch", "tensorflow", "keras", "jax", "flax",
    "transformers", "lightning", "sklearn",
}

# Lower-is-better metric names
_LOWER_BETTER_KEYWORDS = (
    "loss", "error", "mae", "rmse", "mse",
    "time", "duration", "seconds", "minutes",
    "latency", "perplexity",
)


# ---------------------------------------------------------------------------
# Repo detection
# ---------------------------------------------------------------------------

def detect_ml_repos(root: Path, max_depth: int = 2) -> list[Path]:
    """Walk subdirectories of root and return paths that look like ML projects.

    A directory qualifies if it has a .git folder and contains ML-like
    artifacts (training scripts, configs, checkpoints, wandb, etc.)
    """
    root = Path(root).resolve()
    if not root.is_dir():
        return []

    repos: list[Path] = []

    def _walk(path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if path.name in _SKIP_DIRS or path.name.startswith("."):
            return

        git_dir = path / ".git"
        if git_dir.exists():
            if _is_ml_repo(path):
                repos.append(path)
            return  # don't recurse into nested git repos

        try:
            for child in sorted(path.iterdir()):
                if child.is_dir():
                    _walk(child, depth + 1)
        except PermissionError:
            pass

    _walk(root, 0)
    return repos


def _is_ml_repo(path: Path) -> bool:
    """Check if a git repo contains ML-like artifacts."""
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            rel = os.path.relpath(os.path.join(root, fname), path)
            for pattern in _ML_FILE_PATTERNS:
                if re.search(pattern, rel):
                    return True
            # Check Python files for ML imports
            if fname.endswith(".py"):
                try:
                    text = Path(root, fname).read_text(encoding="utf-8", errors="ignore")[:2000]
                    for kw in _ML_IMPORT_KEYWORDS:
                        if f"import {kw}" in text or f"from {kw}" in text:
                            return True
                except OSError:
                    pass
    return False


# ---------------------------------------------------------------------------
# File classification (heuristic, ported from ml-notebook)
# ---------------------------------------------------------------------------

def _classify_json(data: dict) -> str:
    """Classify a JSON dict as training_log, results, config, history, or other."""
    if not isinstance(data, dict):
        return "other"
    if _is_training_log(data):
        return "training_log"
    if _is_training_history(data):
        return "training_history"
    if _is_result_file(data):
        return "results"
    if _is_config_file(data):
        return "config"
    return "other"


def _is_training_log(data: dict) -> bool:
    """A training log has a config dict + steps or epochs list."""
    has_config = isinstance(data.get("config"), dict)
    has_steps = isinstance(data.get("steps"), list)
    has_epochs = isinstance(data.get("epochs"), list)
    if not has_config or not (has_steps or has_epochs):
        return False
    config = data["config"]
    numeric_count = sum(1 for v in config.values() if isinstance(v, (int, float)))
    return numeric_count >= 2


def _is_training_history(data: dict) -> bool:
    """Column-oriented: {"epoch": [1,2,...], "loss": [6.4, 3.9,...]}."""
    if "config" in data:
        return False
    index_key = None
    for k in ("epoch", "step", "epochs", "steps"):
        if isinstance(data.get(k), list) and len(data[k]) >= 2:
            index_key = k
            break
    if not index_key:
        return False
    n = len(data[index_key])
    return any(
        isinstance(v, list) and len(v) == n and v and isinstance(v[0], (int, float))
        for k, v in data.items() if k != index_key
    )


def _is_result_file(data: dict) -> bool:
    """Flat dict with metric-like keys (possibly alongside metadata)."""
    # Reject list-valued epochs/steps (that's a training log/history)
    if isinstance(data.get("steps"), list) or isinstance(data.get("epochs"), list):
        return False
    metric_keywords = {"accuracy", "loss", "f1", "precision", "recall",
                       "bleu", "rouge", "auc", "mrr", "exact_match",
                       "correct", "total", "perplexity", "mse", "rmse"}
    keys_lower = {k.lower() for k in data.keys()}
    if any(any(kw in k for kw in metric_keywords) for k in keys_lower):
        return True
    # Check nested dicts too
    for v in data.values():
        if isinstance(v, dict):
            nested = {k.lower() for k in v.keys()}
            if any(any(kw in nk for kw in metric_keywords) for nk in nested):
                return True
    return False


def _is_config_file(data: dict) -> bool:
    """Standalone ML config file."""
    if "config" in data or "steps" in data or "epochs" in data:
        return False
    numeric_count = sum(1 for v in data.values() if isinstance(v, (int, float)))
    if numeric_count < 2:
        return False
    config_keywords = {"dim", "hidden", "layer", "head", "lr", "batch",
                       "epoch", "embed", "input", "output", "dropout"}
    keys_lower = {k.lower() for k in data.keys()}
    return any(any(kw in k for kw in config_keywords) for k in keys_lower)


# ---------------------------------------------------------------------------
# Model tag extraction (from filenames and configs)
# ---------------------------------------------------------------------------

def _extract_model_tag(filename: str) -> str:
    """Extract model identifier from filename (d8_h1_ff16_L1, v3, etc.)."""
    m = re.search(r"d(\d+)_h(\d+)_ff(\d+)_L(\d+)", filename)
    if m:
        return m.group(0)
    m = re.search(r"[_\-.]v(\d+)", filename, re.IGNORECASE)
    if m:
        return f"v{m.group(1)}"
    if "_final" in filename or filename.startswith("final"):
        return "final"
    return ""


def _tag_from_config(config: dict) -> str:
    """Build a model tag from config values."""
    parts = []
    for key, prefix in [("d_model", "d"), ("n_heads", "h"),
                         ("d_ff", "ff"), ("n_layers", "L")]:
        if key in config:
            parts.append(f"{prefix}{config[key]}")
    return "_".join(parts)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git_log(repo_path: Path, since_hash: str = "") -> list[dict]:
    """Get git commits, optionally since a given hash."""
    cmd = ["git", "-C", str(repo_path), "log", "--format=%H|%s|%an|%aI",
           "--no-merges"]
    if since_hash:
        cmd.append(f"{since_hash}..HEAD")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    commits = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "hash": parts[0],
                "message": parts[1],
                "author": parts[2],
                "date": parts[3],
            })
    return commits


def _git_head_hash(repo_path: Path) -> str:
    """Get the current HEAD commit hash."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _git_changed_files(repo_path: Path, commit_hash: str) -> list[str]:
    """Get files changed in a specific commit."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "diff-tree", "--no-commit-id",
             "--name-only", "-r", commit_hash],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip().splitlines() if result.returncode == 0 else []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


# ---------------------------------------------------------------------------
# Retroactive scanning
# ---------------------------------------------------------------------------

def scan_project(path: Path) -> dict:
    """Retroactively scan a git repo and reconstruct experiment runs.

    Returns a dict with project metadata and discovered runs.
    """
    path = Path(path).resolve()
    if not (path / ".git").exists():
        return {"error": f"Not a git repo: {path}"}

    # Collect JSON files
    classified: list[dict] = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if not fname.endswith(".json"):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, path)
            try:
                with open(full, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, dict) or len(data) < 2:
                continue
            file_type = _classify_json(data)
            if file_type == "other":
                continue
            tag = _extract_model_tag(fname)
            classified.append({
                "path": rel,
                "type": file_type,
                "tag": tag,
                "data": data,
            })

    # Group by type
    training_logs = [f for f in classified if f["type"] == "training_log"]
    result_files = [f for f in classified if f["type"] == "results"]
    config_files = [f for f in classified if f["type"] == "config"]
    history_files = [f for f in classified if f["type"] == "training_history"]

    # Build runs from training logs
    runs = {}
    for tl in training_logs:
        data = tl["data"]
        config = data.get("config", {})
        tag = tl["tag"] or _tag_from_config(config)

        # Extract metrics from last epoch/step
        metrics: dict[str, Any] = {}
        for key in ("best_val_acc", "final_loss", "best_accuracy"):
            if key in data and isinstance(data[key], (int, float)):
                metrics[key] = data[key]
        epochs = data.get("epochs", data.get("steps", []))
        if epochs and isinstance(epochs[-1], dict):
            last = epochs[-1]
            for k, v in last.items():
                if isinstance(v, (int, float)) and k not in ("epoch", "step"):
                    metrics[k] = v
        if data.get("n_params"):
            metrics["n_params"] = data["n_params"]

        # Extract hyperparameters
        hyperparams = {k: v for k, v in config.items()
                       if isinstance(v, (int, float, str, bool))}

        run_id = f"exp-{uuid.uuid4().hex[:6]}"
        name = tag if tag else Path(tl["path"]).stem
        runs[run_id] = {
            "id": run_id,
            "name": name,
            "status": "completed",
            "hypothesis": "",
            "hyperparameters": hyperparams,
            "results": metrics,
            "tags": [tag] if tag else [],
            "git_commits": [],
            "files_created": [tl["path"]],
            "started_at": "",
            "completed_at": "",
            "duration_minutes": int(data.get("total_time", 0) / 60)
                               if data.get("total_time") else 0,
            "notes": [],
        }

        # Try matching result files by tag
        for rf in result_files:
            if rf["tag"] and rf["tag"] == tag:
                flat: dict[str, Any] = {}
                for k, v in rf["data"].items():
                    if isinstance(v, (int, float)):
                        flat[k] = v
                runs[run_id]["results"].update(flat)
                runs[run_id]["files_created"].append(rf["path"])

    # Build runs from training histories (no training_log)
    for hf in history_files:
        data = hf["data"]
        tag = hf["tag"]
        # Skip if already covered by a training log with same tag
        if any(r.get("tags", [None])[0] == tag for r in runs.values() if tag):
            continue

        # Extract last-row metrics
        index_key = None
        for k in ("epoch", "step", "epochs", "steps"):
            if isinstance(data.get(k), list):
                index_key = k
                break
        if not index_key:
            continue
        n = len(data[index_key])
        metrics = {}
        for k, v in data.items():
            if k == index_key:
                continue
            if isinstance(v, list) and len(v) == n and v and isinstance(v[-1], (int, float)):
                metrics[k] = v[-1]

        run_id = f"exp-{uuid.uuid4().hex[:6]}"
        runs[run_id] = {
            "id": run_id,
            "name": tag if tag else Path(hf["path"]).stem,
            "status": "completed",
            "hypothesis": "",
            "hyperparameters": {},
            "results": metrics,
            "tags": [tag] if tag else [],
            "git_commits": [],
            "files_created": [hf["path"]],
            "started_at": "",
            "completed_at": "",
            "duration_minutes": 0,
            "notes": [],
        }

    # Build runs from standalone result files (not yet covered by a training log)
    used_result_paths = {f for r in runs.values() for f in r["files_created"]}
    for rf in result_files:
        if rf["path"] in used_result_paths:
            continue
        tag = rf["tag"]
        # Skip if already covered by a run with the same tag
        if tag and any(tag in r.get("tags", []) for r in runs.values()):
            continue

        metrics: dict[str, Any] = {}
        for k, v in rf["data"].items():
            if isinstance(v, (int, float)):
                metrics[k] = v

        # Try to pair with a config file by matching tag
        hyperparams: dict[str, Any] = {}
        config_path = None
        for cf in config_files:
            if cf["tag"] and cf["tag"] == tag:
                hyperparams = {k: v for k, v in cf["data"].items()
                               if isinstance(v, (int, float, str, bool))}
                config_path = cf["path"]
                break

        run_id = f"exp-{uuid.uuid4().hex[:6]}"
        name = tag if tag else Path(rf["path"]).stem
        files = [rf["path"]]
        if config_path:
            files.append(config_path)
        runs[run_id] = {
            "id": run_id,
            "name": name,
            "status": "completed",
            "hypothesis": "",
            "hyperparameters": hyperparams,
            "results": metrics,
            "tags": [tag] if tag else [],
            "git_commits": [],
            "files_created": files,
            "started_at": "",
            "completed_at": "",
            "duration_minutes": 0,
            "notes": [],
        }

    # Attach git commits to runs
    commits = _git_log(path)
    for commit in commits:
        changed = _git_changed_files(path, commit["hash"])
        for run in runs.values():
            if any(f in changed for f in run["files_created"]):
                run["git_commits"].append(commit["hash"])
                if not run["completed_at"]:
                    run["completed_at"] = commit["date"]
                if not run["started_at"]:
                    run["started_at"] = commit["date"]

    # Derive project name from repo directory
    project_name = path.name.replace("-", " ").replace("_", " ").title()

    return {
        "name": project_name,
        "path": str(path),
        "runs": runs,
        "head_hash": _git_head_hash(path),
        "total_commits": len(commits),
        "artifact_files": len(classified),
    }


# ---------------------------------------------------------------------------
# Incremental update (medium tier)
# ---------------------------------------------------------------------------

def update_project_from_git(project: dict, state: Any) -> bool:
    """Check for new commits since last scan and detect new runs.

    Returns True if the project was updated.
    """
    path = Path(project["path"])
    if not path.is_dir():
        log.warning("Project path not found: %s", path)
        return False

    last_hash = project.get("last_commit_hash", "")
    if not last_hash:
        return False  # needs a full scan first

    new_commits = _git_log(path, since_hash=last_hash)
    if not new_commits:
        return False

    # Check if any new commits touch ML artifact files
    ml_files_changed = []
    for commit in new_commits:
        changed = _git_changed_files(path, commit["hash"])
        for f in changed:
            if f.endswith(".json") or f.endswith(".log") or f.endswith(".csv"):
                ml_files_changed.append(f)

    if not ml_files_changed:
        # Update the hash even if no ML files changed
        project["last_commit_hash"] = _git_head_hash(path)
        project["last_scanned_at"] = datetime.now(timezone.utc).isoformat()
        return True

    # Re-scan to pick up new experiments
    scan_result = scan_project(path)
    if "error" in scan_result:
        return False

    # Merge new runs (don't overwrite existing ones)
    existing_names = {r["name"] for r in project.get("runs", {}).values()}
    new_count = 0
    for run_id, run_data in scan_result.get("runs", {}).items():
        if run_data["name"] not in existing_names:
            project.setdefault("runs", {})[run_id] = run_data
            new_count += 1

    project["last_commit_hash"] = scan_result["head_hash"]
    project["last_scanned_at"] = datetime.now(timezone.utc).isoformat()

    if new_count:
        log.info("Found %d new run(s) in %s", new_count, project["name"])

    return True


# ---------------------------------------------------------------------------
# Run diffing
# ---------------------------------------------------------------------------

def _is_lower_better(metric_name: str) -> bool:
    """Return True if lower values are better for this metric."""
    name = metric_name.lower()
    return any(kw in name for kw in _LOWER_BETTER_KEYWORDS)


def diff_runs(run_a: dict, run_b: dict) -> dict:
    """Structured diff between two runs (a=baseline, b=new).

    Returns param_diffs and metric_diffs with deltas and direction awareness.
    """
    # Diff hyperparameters
    params_a = run_a.get("hyperparameters", {})
    params_b = run_b.get("hyperparameters", {})
    param_diffs = _diff_dicts(params_a, params_b)

    # Diff metrics (only numeric values)
    metrics_a = {k: v for k, v in run_a.get("results", {}).items()
                 if isinstance(v, (int, float))}
    metrics_b = {k: v for k, v in run_b.get("results", {}).items()
                 if isinstance(v, (int, float))}
    metric_diffs = _diff_dicts(metrics_a, metrics_b)

    # Add improvement direction to metric diffs
    for d in metric_diffs:
        if d.get("delta") is not None:
            lower_better = _is_lower_better(d["key"])
            raw_delta = d["delta"]
            d["improved"] = (raw_delta < 0) if lower_better else (raw_delta > 0)

    return {
        "run_a": run_a.get("name", run_a.get("id", "")),
        "run_b": run_b.get("name", run_b.get("id", "")),
        "param_diffs": param_diffs,
        "metric_diffs": metric_diffs,
    }


def _diff_dicts(a: dict, b: dict) -> list[dict]:
    """Diff two flat dicts, producing a list of change records."""
    diffs = []
    all_keys = sorted(set(a.keys()) | set(b.keys()))
    for key in all_keys:
        if key in a and key not in b:
            diffs.append({"key": key, "old": a[key], "new": None, "change": "removed"})
        elif key not in a and key in b:
            diffs.append({"key": key, "old": None, "new": b[key], "change": "added"})
        elif a[key] != b[key]:
            entry: dict[str, Any] = {
                "key": key, "old": a[key], "new": b[key], "change": "changed",
            }
            if isinstance(a[key], (int, float)) and isinstance(b[key], (int, float)):
                entry["delta"] = b[key] - a[key]
                if a[key] != 0:
                    entry["pct_change"] = (b[key] - a[key]) / abs(a[key]) * 100
            diffs.append(entry)
    return diffs


# ---------------------------------------------------------------------------
# Notebook generation
# ---------------------------------------------------------------------------

def generate_notebook(project: dict, section: str = "main") -> str:
    """Generate a markdown lab notebook for a project.

    Produces a rich document with project overview, experiment timeline,
    per-run detail cards, and diff sections between consecutive runs.
    """
    name = project.get("name", "Untitled Project")
    runs_dict = project.get("runs", {})
    runs = list(runs_dict.values())

    # Sort runs by completion date, then by name
    runs.sort(key=lambda r: r.get("completed_at") or r.get("started_at") or "")

    parts = [
        f"# {name}",
        "",
        f"> {project.get('description', '')}".rstrip() if project.get("description") else "",
        "",
        "---",
        "",
        f"**Generated by** Distillate  ",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Repository:** `{project.get('path', '')}`  ",
    ]

    # Goals
    goals = project.get("goals", [])
    if goals:
        parts.append("")
        parts.append("## Success Criteria")
        for g in goals:
            direction = "maximize" if g.get("direction") == "maximize" else "minimize"
            threshold = g.get("threshold")
            if threshold is not None:
                parts.append(f"- **{g['metric']}:** {direction} (target: {_fmt_metric(g['metric'], threshold)})")
            else:
                parts.append(f"- **{g['metric']}:** {direction}")

    # Timeline table
    if runs:
        completed = sum(1 for r in runs if r.get("status") == "completed")
        running = sum(1 for r in runs if r.get("status") == "running")
        failed = sum(1 for r in runs if r.get("status") == "failed")

        parts.append("")
        parts.append("## Experiment Timeline")
        parts.append("")
        parts.append(
            f"> **{len(runs)}** experiments | "
            f"**{completed}** completed | "
            f"**{running}** running | "
            f"**{failed}** failed"
        )
        parts.append("")
        parts.append("| # | Experiment | Status | Duration | Key Metric |")
        parts.append("|---|-----------|--------|----------|------------|")

        for i, run in enumerate(runs, 1):
            status_icon = _status_icon(run.get("status", "planned"))
            duration = _fmt_duration(run.get("duration_minutes", 0))
            key_metric = _pick_key_metric(run.get("results", {}))
            parts.append(
                f"| {i} | {run.get('name', '?')} | {status_icon} | "
                f"{duration} | {key_metric} |"
            )

    # Per-run detail cards
    if runs:
        parts.append("")
        parts.append("## Experiment Details")

    for i, run in enumerate(runs):
        parts.append("")
        parts.append(f"### {run.get('id', '')}: {run.get('name', 'Untitled')}")
        parts.append("")

        status = run.get("status", "planned").capitalize()
        duration = _fmt_duration(run.get("duration_minutes", 0))
        tags = ", ".join(f"`{t}`" for t in run.get("tags", []))
        parts.append(f"**Status:** {status} | **Duration:** {duration}")
        if tags:
            parts[-1] += f" | **Tags:** {tags}"

        if run.get("started_at"):
            parts.append(f"**Started:** {run['started_at']}")
        if run.get("completed_at"):
            parts.append(f"**Completed:** {run['completed_at']}")

        if run.get("hypothesis"):
            parts.append("")
            parts.append("#### Hypothesis")
            parts.append(run["hypothesis"])

        # Hyperparameters table
        hyperparams = run.get("hyperparameters", {})
        if hyperparams:
            parts.append("")
            parts.append("#### Hyperparameters")
            parts.append("")
            parts.append("| Parameter | Value |")
            parts.append("|-----------|-------|")
            for k, v in hyperparams.items():
                parts.append(f"| {k} | `{v}` |")

        # Results table
        results = run.get("results", {})
        if results:
            parts.append("")
            parts.append("#### Results")
            parts.append("")
            parts.append("| Metric | Value |")
            parts.append("|--------|-------|")
            for k, v in results.items():
                parts.append(f"| {k} | **{_fmt_metric(k, v)}** |")

        if run.get("notes"):
            parts.append("")
            parts.append("#### Notes")
            for note in run["notes"]:
                parts.append(f"- {note}")

        # Diff with previous run
        if i > 0:
            diff = diff_runs(runs[i - 1], run)
            diff_section = _render_diff(diff, runs[i - 1], run)
            if diff_section:
                parts.append("")
                parts.append(diff_section)

        parts.append("")
        parts.append("---")

    # Linked papers
    linked = project.get("linked_papers", [])
    if linked:
        parts.append("")
        parts.append("## Linked Papers")
        for citekey in linked:
            parts.append(f"- `{citekey}`")

    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Notebook formatting helpers
# ---------------------------------------------------------------------------

def _status_icon(status: str) -> str:
    return {
        "planned": "[ ]",
        "running": "[~]",
        "completed": "[x]",
        "failed": "[!]",
        "abandoned": "[-]",
    }.get(status, "[ ]")


def _fmt_duration(minutes: int) -> str:
    if not minutes:
        return "-"
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _fmt_metric(name: str, val: Any) -> str:
    """Format a metric value with adaptive precision."""
    if isinstance(val, float):
        nl = name.lower()
        no_pct = ("loss", "mae", "rmse", "mse", "error", "time", "seconds")
        if 0 < val <= 1 and not any(k in nl for k in no_pct):
            return f"{val:.1%}" if val < 0.9995 else f"{val:.2%}"
        if val == int(val) and abs(val) >= 1:
            return f"{int(val):,}"
        return f"{val:.6f}" if abs(val) < 0.01 else f"{val:.4f}"
    if isinstance(val, int):
        return f"{val:,}"
    return str(val)


def _pick_key_metric(results: dict) -> str:
    """Pick the most important metric from results for the timeline table."""
    if not results:
        return "-"
    # Priority order for key metrics
    priority = ["exact_match", "accuracy", "test_accuracy", "val_accuracy",
                 "best_val_acc", "f1", "loss", "final_loss", "val_loss"]
    for key in priority:
        if key in results and isinstance(results[key], (int, float)):
            return _fmt_metric(key, results[key])
    # Fallback: first numeric metric
    for k, v in results.items():
        if isinstance(v, (int, float)) and k not in ("n_params", "duration_minutes"):
            return _fmt_metric(k, v)
    return "-"


def _render_diff(diff: dict, run_a: dict, run_b: dict) -> str:
    """Render a diff section between two runs as markdown."""
    param_diffs = [d for d in diff.get("param_diffs", []) if d.get("change") == "changed"]
    metric_diffs = [d for d in diff.get("metric_diffs", []) if d.get("change") == "changed"]

    if not param_diffs and not metric_diffs:
        return ""

    lines = [f"#### What Changed (vs {run_a.get('name', 'previous')})"]

    if param_diffs:
        changes = []
        for d in param_diffs:
            pct = d.get("pct_change")
            if pct is not None:
                direction = "+" if pct > 0 else ""
                changes.append(f"**{d['key']}** {d['old']} -> {d['new']} ({direction}{pct:.0f}%)")
            else:
                changes.append(f"**{d['key']}** `{d['old']}` -> `{d['new']}`")
        lines.append("- Parameters: " + ", ".join(changes))

    if metric_diffs:
        for d in metric_diffs:
            arrow = "improved" if d.get("improved") else "regressed"
            delta = d.get("delta")
            if delta is not None:
                sign = "+" if delta > 0 else ""
                lines.append(
                    f"- **{d['key']}**: {_fmt_metric(d['key'], d['old'])} -> "
                    f"{_fmt_metric(d['key'], d['new'])} ({sign}{delta:.4g}, {arrow})"
                )
            else:
                lines.append(
                    f"- **{d['key']}**: `{d['old']}` -> `{d['new']}` ({arrow})"
                )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Git hook installation
# ---------------------------------------------------------------------------

def install_git_hook(project_path: Path, project_id: str) -> bool:
    """Install a post-commit hook that triggers distillate --scan-project.

    Appends to existing hook if present. Returns True on success.
    """
    hooks_dir = project_path / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return False

    hook_file = hooks_dir / "post-commit"
    marker = "# distillate experiment tracking"
    hook_cmd = (
        f"\n{marker}\n"
        f"distillate --scan-project {project_id} 2>/dev/null &\n"
    )

    if hook_file.exists():
        existing = hook_file.read_text(encoding="utf-8")
        if marker in existing:
            return True  # already installed
        hook_file.write_text(existing + hook_cmd, encoding="utf-8")
    else:
        hook_file.write_text(f"#!/bin/sh\n{hook_cmd}", encoding="utf-8")

    # Make executable
    os.chmod(hook_file, 0o755)
    log.info("Installed post-commit hook in %s", project_path)
    return True


def slugify(name: str) -> str:
    """Convert a project name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")
    return slug
