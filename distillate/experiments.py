"""ML experiment tracking for Distillate.

Discovers ML projects, reconstructs experiment history from artifacts
(training logs, configs, checkpoints, results), and generates rich
markdown lab notebooks.  Works with or without git.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Directories to skip when walking repos
_SKIP_DIRS = {
    "__pycache__", ".git", ".distillate", "node_modules",
    "venv", "env", ".venv", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", "egg-info",
}

# Constants
_RUN_ID_HEX_LENGTH = 6          # hex chars in auto-generated run IDs
_ML_IMPORT_SCAN_CHARS = 2000     # max bytes to read when checking for ML imports
_GIT_LOG_TIMEOUT = 30            # seconds for git log subprocess
_GIT_SUBPROCESS_TIMEOUT = 10     # seconds for short git commands

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

# Metric classification — checked in priority order (first match wins)
_METRIC_CATEGORIES = (
    ("ratio", ("accuracy", "precision", "recall", "f1", "auc", "map", "ap",
               "iou", "dice", "bleu", "rouge", "meteor", "exact_match", "score")),
    ("loss", ("loss", "error", "mae", "rmse", "mse", "perplexity", "nll",
              "cross_entropy", "bpb")),
    ("count", ("param", "count", "num_", "flops", "size", "steps", "epochs",
               "samples", "vocab")),
    ("time", ("time", "duration", "seconds", "minutes", "latency")),
    ("cost", ("cost", "price")),
    ("hyperparameter", ("lr", "learning_rate", "weight_decay", "dropout",
                        "momentum", "beta", "epsilon", "warmup")),
)

_LOWER_BETTER_CATEGORIES = {"loss", "count", "time", "cost"}


def classify_metric(name: str) -> str:
    """Classify a metric name into a category."""
    nl = name.lower()
    for category, keywords in _METRIC_CATEGORIES:
        if any(kw in nl for kw in keywords):
            return category
    return "generic"


def _create_run(
    *,
    prefix: str = "exp",
    name: str = "",
    hyperparameters: Optional[dict] = None,
    results: Optional[dict] = None,
    tags: Optional[list] = None,
    files_created: Optional[list] = None,
    started_at: str = "",
    completed_at: str = "",
    duration_minutes: int = 0,
    duration_seconds: float = 0,
    **extra: Any,
) -> dict:
    """Build a run dict with consistent schema.  Extra kwargs are merged in."""
    # Deterministic ID from name + started_at so scans are idempotent.
    id_seed = f"{name}|{started_at}"
    run_id = f"{prefix}-{hashlib.sha256(id_seed.encode()).hexdigest()[:_RUN_ID_HEX_LENGTH]}"
    run: dict[str, Any] = {
        "id": run_id,
        "name": name,
        "status": "completed",
        "hypothesis": "",
        "hyperparameters": hyperparameters or {},
        "results": results or {},
        "tags": tags or [],
        "git_commits": [],
        "files_created": files_created or [],
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_minutes": duration_minutes,
        "duration_seconds": duration_seconds,
        "notes": [],
    }
    run.update(extra)
    return run


# ---------------------------------------------------------------------------
# Repo detection
# ---------------------------------------------------------------------------

def detect_ml_repos(root: Path, max_depth: int = 2) -> list[Path]:
    """Walk subdirectories of root and return paths that look like ML projects.

    A directory qualifies if it contains ML-like artifacts (training scripts,
    configs, checkpoints, wandb, etc.).  Git is not required.
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

        # Don't check root itself — only its subdirectories
        if depth > 0 and _is_ml_repo(path):
            repos.append(path)
            return  # don't recurse into nested ML projects

        try:
            for child in sorted(path.iterdir()):
                if child.is_dir():
                    _walk(child, depth + 1)
        except PermissionError:
            pass

    _walk(root, 0)
    return repos


def _is_ml_repo(path: Path) -> bool:
    """Check if a directory contains ML-like artifacts."""
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
                    text = Path(root, fname).read_text(encoding="utf-8", errors="ignore")[:_ML_IMPORT_SCAN_CHARS]
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_GIT_LOG_TIMEOUT)
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
            capture_output=True, text=True, timeout=_GIT_SUBPROCESS_TIMEOUT,
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
            capture_output=True, text=True, timeout=_GIT_SUBPROCESS_TIMEOUT,
        )
        return result.stdout.strip().splitlines() if result.returncode == 0 else []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


# ---------------------------------------------------------------------------
# Retroactive scanning
# ---------------------------------------------------------------------------

def scan_experiment(path: Path) -> dict:
    """Scan a directory and reconstruct experiment runs from artifacts.

    Works with or without git.  Returns a dict with project metadata
    and discovered runs.
    """
    path = Path(path).resolve()
    if not path.is_dir():
        return {"error": f"Not a directory: {path}"}

    # Collect JSON files
    classified: list[dict] = []
    file_mtimes: dict[str, float] = {}
    for root_dir, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if not fname.endswith(".json"):
                continue
            full = os.path.join(root_dir, fname)
            rel = os.path.relpath(full, path)
            try:
                mtime = os.path.getmtime(full)
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
            file_mtimes[rel] = mtime
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

        run = _create_run(
            name=tag if tag else Path(tl["path"]).stem,
            hyperparameters=hyperparams,
            results=metrics,
            tags=[tag] if tag else [],
            files_created=[tl["path"]],
            duration_minutes=(int(data.get("total_time", 0) / 60)
                              if data.get("total_time") else 0),
        )
        run_id = run["id"]
        runs[run_id] = run

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
        if tag and any(
            (r.get("tags") or [None])[0] == tag for r in runs.values()
        ):
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

        run = _create_run(
            name=tag if tag else Path(hf["path"]).stem,
            results=metrics,
            tags=[tag] if tag else [],
            files_created=[hf["path"]],
        )
        runs[run["id"]] = run

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

        files = [rf["path"]]
        if config_path:
            files.append(config_path)
        run = _create_run(
            name=tag if tag else Path(rf["path"]).stem,
            hyperparameters=hyperparams,
            results=metrics,
            tags=[tag] if tag else [],
            files_created=files,
        )
        runs[run["id"]] = run

    # Assign timestamps from file mtimes
    for run in runs.values():
        mtimes = [file_mtimes[f] for f in run["files_created"] if f in file_mtimes]
        if mtimes:
            earliest = datetime.fromtimestamp(min(mtimes), tz=timezone.utc)
            latest = datetime.fromtimestamp(max(mtimes), tz=timezone.utc)
            if not run["started_at"]:
                run["started_at"] = earliest.isoformat()
            if not run["completed_at"]:
                run["completed_at"] = latest.isoformat()

    # Opportunistic git enrichment
    has_git = (path / ".git").exists()
    commits: list[dict] = []
    if has_git:
        commits = _git_log(path)
        for commit in commits:
            changed = _git_changed_files(path, commit["hash"])
            for run in runs.values():
                if any(f in changed for f in run["files_created"]):
                    run["git_commits"].append(commit["hash"])

    # Extract runs from Claude Code conversation logs
    from distillate.experiments_parser import (
        extract_runs_from_claude_logs,
        ingest_runs,
    )
    claude_runs = extract_runs_from_claude_logs(path)
    for run in claude_runs:
        if not _is_duplicate_run(runs, run):
            runs[run["id"]] = run

    # Filter out non-experiment runs (analysis files, metadata-only, etc.)
    runs = {rid: r for rid, r in runs.items() if _is_experiment_run(r)}

    # Derive project name from directory
    project_name = path.name.replace("-", " ").replace("_", " ").title()

    # Save scan state for incremental updates
    _save_scan_state(path, file_mtimes)

    # Ingest structured reports + hook events
    ingested = ingest_runs(path)
    has_structured = any(r.get("source") == "structured" for r in ingested)
    if has_structured:
        # Structured runs (runs.jsonl) are the canonical experiment log.
        # Drop ALL artifact-scanned runs — they're redundant noise when
        # the agent is actively logging via the protocol.
        artifact_keys = [k for k, v in runs.items()
                         if v.get("source") != "structured" and v.get("source") != "hooks"]
        for k in artifact_keys:
            del runs[k]
    for run in ingested:
        # Every run needs at least one numeric result to be displayable.
        # "running" entries are kept (they haven't produced results yet).
        if run.get("status") != "running" and not _has_numeric_results(run):
            continue
        if run.get("source") == "structured":
            runs[run["id"]] = run
        elif not _is_duplicate_run(runs, run):
            runs[run["id"]] = run

    # Ingest .mlnotebook/state.json (structured experiment tracker)
    goals: list[dict] = []
    mlnb_state = path / ".mlnotebook" / "state.json"
    if mlnb_state.exists():
        try:
            with open(mlnb_state, encoding="utf-8") as f:
                mlnb = json.load(f)
            # Extract goals
            proj_meta = mlnb.get("project", {})
            goals = proj_meta.get("goals", [])
            if proj_meta.get("description"):
                project_name = proj_meta.get("name", project_name) or project_name

            # Extract experiments
            for exp in mlnb.get("experiments", []):
                hp = exp.get("hyperparameters", {})
                results = exp.get("results", {})

                # Promote metric-like HP keys into results
                _METRIC_HP_KEYS = {"total_parameters", "total_params", "n_params",
                                   "param_count", "model_size", "flops"}
                for k in _METRIC_HP_KEYS:
                    if k in hp and isinstance(hp[k], (int, float)) and k not in results:
                        results[k] = hp[k]

                run = _create_run(
                    prefix="mlnb",
                    name=exp.get("name", exp.get("id", "")),
                    hyperparameters={k: v for k, v in hp.items()
                                     if isinstance(v, (int, float, str, bool))
                                     and k not in _METRIC_HP_KEYS},
                    results={k: v for k, v in results.items()
                             if isinstance(v, (int, float))},
                    tags=exp.get("tags", []),
                    files_created=exp.get("files_created", []),
                    started_at=exp.get("started_at", ""),
                    completed_at=exp.get("completed_at", ""),
                    duration_minutes=exp.get("duration_minutes", 0),
                    hypothesis=exp.get("hypothesis", ""),
                    decision=exp.get("decision", ""),
                )
                if not _is_duplicate_run(runs, run):
                    runs[run["id"]] = run
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    return {
        "name": project_name,
        "path": str(path),
        "runs": runs,
        "goals": goals,
        "has_git": has_git,
        "head_hash": _git_head_hash(path) if has_git else "",
        "total_commits": len(commits),
        "artifact_files": len(classified),
    }


def _is_duplicate_run(existing_runs: dict, candidate: dict) -> bool:
    """Check if a candidate run duplicates an existing run.

    Matches by exact hyperparameter fingerprint OR by normalized tag
    (e.g. Claude log run 'train_v5' matches JSON run 'v5').
    When a match is found by tag, merges the candidate's data into
    the existing run (hyperparams, metrics, timestamps, command).
    """
    # Match by hyperparameter fingerprint
    cand_hp = candidate.get("hyperparameters", {})
    if cand_hp:
        cand_key = _hyperparam_fingerprint(cand_hp)
        for run in existing_runs.values():
            existing_hp = run.get("hyperparameters", {})
            if existing_hp and _hyperparam_fingerprint(existing_hp) == cand_key:
                _merge_into_run(run, candidate)
                return True

    # Match by normalized tag (strip common prefixes like "train_")
    cand_tag = _normalize_run_tag(candidate.get("name", ""))
    if cand_tag:
        for run in existing_runs.values():
            existing_tag = _normalize_run_tag(run.get("name", ""))
            if existing_tag and existing_tag == cand_tag:
                _merge_into_run(run, candidate)
                return True

    return False


def _normalize_run_tag(name: str) -> str:
    """Normalize a run name for dedup matching.

    'train_v5' -> 'v5', 'train_model' -> 'model', 'v5' -> 'v5',
    'scibert_finetune_results' -> 'scibert_finetune'
    """
    if not name:
        return ""
    # Strip common prefixes
    for prefix in ("train_", "run_", "experiment_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    # Strip common suffixes
    for suffix in ("_results", "_result", "_output", "_log"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name.lower()


def _merge_into_run(target: dict, source: dict) -> None:
    """Merge source run data into target run (in place)."""
    # Merge hyperparameters (source overrides for new keys only)
    for k, v in source.get("hyperparameters", {}).items():
        target.setdefault("hyperparameters", {}).setdefault(k, v)
    # Merge results (source overrides for new keys only)
    for k, v in source.get("results", {}).items():
        target.setdefault("results", {}).setdefault(k, v)
    # Use source timestamps if target has none
    if source.get("started_at") and not target.get("started_at"):
        target["started_at"] = source["started_at"]
    if source.get("completed_at") and not target.get("completed_at"):
        target["completed_at"] = source["completed_at"]
    # Keep the command from Claude logs
    if source.get("command") and not target.get("command"):
        target["command"] = source["command"]


def _run_sort_key(run: dict) -> tuple:
    """Sort key that orders runs by run number extracted from the ID.

    Extracts the number from run_NNN patterns (e.g. run_042 -> 42,
    run_004a -> (4, 'a')).  Non-numeric IDs sort first (number=0)
    with timestamp as tiebreaker.
    """
    name = run.get("name", "")
    m = re.match(r"(?:run_?)(\d+)([a-z]?)", name)
    if m:
        return (int(m.group(1)), m.group(2), "")
    # Non-numeric IDs sort first, ordered by timestamp
    ts = run.get("started_at") or run.get("completed_at") or ""
    return (0, "", ts)


def _has_numeric_results(run: dict) -> bool:
    """True if the run has at least one numeric value in results."""
    return any(isinstance(v, (int, float)) for v in run.get("results", {}).values())


def _is_experiment_run(run: dict) -> bool:
    """Return True if a run looks like an actual training experiment.

    Filters out analysis files, metadata-only results, and empty runs.
    A run is kept if it:
    - Came from Claude logs (explicit training commands), OR
    - Has hyperparameters (from config files or CLI flags), OR
    - Has training-related metrics (loss, accuracy, recall, etc.)
    """
    # Claude log runs are always real experiments (matched training regex)
    if run.get("source") == "claude_logs":
        return True
    hp = run.get("hyperparameters", {})
    if hp:
        return True
    metrics = run.get("results", {})
    if not metrics:
        return False
    # Check for training-related metric names
    training_patterns = (
        "loss", "accuracy", "acc", "recall", "precision", "f1",
        "bleu", "rouge", "auc", "perplexity", "exact_match", "mrr",
    )
    return any(
        any(pat in k.lower() for pat in training_patterns)
        for k in metrics
    )


def _hyperparam_fingerprint(hp: dict) -> str:
    """Create a comparable fingerprint from hyperparameters."""
    # Normalize: sort keys, round floats
    items = []
    for k in sorted(hp.keys()):
        v = hp[k]
        if isinstance(v, float):
            v = round(v, 8)
        elif isinstance(v, (list, tuple)):
            v = str(v)
        items.append(f"{k}={v}")
    return "|".join(items)


# ---------------------------------------------------------------------------
# Scan state (.distillate/scan_state.json)
# ---------------------------------------------------------------------------

def _load_scan_state(path: Path) -> dict:
    """Load the file manifest from .distillate/scan_state.json."""
    state_file = path / ".distillate" / "scan_state.json"
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_scan_state(path: Path, file_mtimes: dict[str, float]) -> None:
    """Save the file manifest to .distillate/scan_state.json."""
    distillate_dir = path / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    state = {
        "last_scanned_at": datetime.now(timezone.utc).isoformat(),
        "file_manifest": {
            rel: {"mtime": mtime, "size": _safe_size(path / rel)}
            for rel, mtime in file_mtimes.items()
        },
    }
    (distillate_dir / "scan_state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _has_changed_files(path: Path) -> bool:
    """Check if any artifact files changed since the last scan."""
    prev = _load_scan_state(path)
    manifest = prev.get("file_manifest", {})
    if not manifest:
        return True  # no previous scan → needs a full scan

    for root_dir, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if not fname.endswith(".json"):
                continue
            full = os.path.join(root_dir, fname)
            rel = os.path.relpath(full, path)
            try:
                mtime = os.path.getmtime(full)
                size = os.path.getsize(full)
            except OSError:
                continue
            entry = manifest.get(rel)
            if entry is None:
                return True  # new file
            if mtime != entry.get("mtime") or size != entry.get("size"):
                return True  # changed file
    return False


# ---------------------------------------------------------------------------
# Incremental update
# ---------------------------------------------------------------------------

def update_experiment(project: dict, state: Any) -> bool:
    """Check for changed artifact files and re-scan if needed.

    Works with or without git.  Uses .distillate/scan_state.json to
    detect changes.  Returns True if the project was updated.
    """
    path = Path(project["path"])
    if not path.is_dir():
        log.warning("Project path not found: %s", path)
        return False

    if not _has_changed_files(path):
        return False

    # Re-scan to pick up new experiments
    scan_result = scan_experiment(path)
    if "error" in scan_result:
        return False

    # Merge new runs (don't overwrite existing ones)
    existing_names = {r["name"] for r in project.get("runs", {}).values()}
    new_count = 0
    for run_id, run_data in scan_result.get("runs", {}).items():
        if run_data["name"] not in existing_names:
            project.setdefault("runs", {})[run_id] = run_data
            new_count += 1

    project["last_commit_hash"] = scan_result.get("head_hash", "")
    project["last_scanned_at"] = datetime.now(timezone.utc).isoformat()

    if new_count:
        log.info("Found %d new run(s) in %s", new_count, project["name"])

    return True


# ---------------------------------------------------------------------------
# Run diffing
# ---------------------------------------------------------------------------

def _is_lower_better(metric_name: str) -> bool:
    """Return True if lower values are better for this metric."""
    return classify_metric(metric_name) in _LOWER_BETTER_CATEGORIES


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
# Re-exports (moved to dedicated modules)
# ---------------------------------------------------------------------------

from distillate.notebook import (  # noqa: F401,E402
    generate_notebook,
    generate_html_notebook,
    _factorize_hyperparams,
    _status_icon,
    _fmt_duration,
    _fmt_metric,
    _pick_key_metric,
    _render_diff,
    _render_metric_chart,
    _render_diff_html,
    _h,
    _decision_icon,
    _sparkline_svg,
)

from distillate.chart_export import generate_export_chart  # noqa: F401,E402

from distillate.experiments_parser import (  # noqa: F401,E402
    _STRUCTURED_STATUSES,
    _INVALID_JSON_ESCAPE_RE,
    _repair_json_line,
    _parse_runs_jsonl,
    _parse_events_jsonl,
    ingest_runs,
    backfill_runs_from_events,
    watch_experiment_artifacts,
    _PYTHON_SCRIPT_RE,
    _TRAIN_FILENAME_RE,
    _CMD_KV_RE,
    _ARGPARSE_RE,
    _KNOWN_HYPERPARAMS,
    _METRIC_RE,
    _CONFIG_BLOCK_RE,
    _find_claude_log_dir,
    _parse_training_command,
    _coerce_value,
    _extract_metrics_from_output,
    _parse_config_block,
    extract_runs_from_claude_logs,
    _parse_claude_session,
)

from distillate.experiments_enrichment import (  # noqa: F401,E402
    _FINGERPRINT_HEX_LENGTH,
    _COMMAND_DISPLAY_CHARS,
    _runs_fingerprint,
    load_enrichment_cache,
    _save_enrichment_cache,
    _build_enrichment_prompt,
    enrich_runs_with_llm,
)


# ---------------------------------------------------------------------------
# Auto-detection: check tracked projects for new commits
# ---------------------------------------------------------------------------

def check_experiments_for_updates(projects: dict) -> list[dict]:
    """Check tracked projects for new commits since last scan.

    For each project with a valid git repo, compares HEAD against the
    stored ``last_commit_hash``.  Returns a list of dicts:
    ``{"project": proj, "new_commits": N, "current_hash": hash}``.

    Read-only — no scanning or state mutation.
    """
    updates = []
    for proj in projects.values():
        proj_path = proj.get("path", "")
        if not proj_path:
            continue
        p = Path(proj_path)
        if not p.is_dir() or not (p / ".git").exists():
            continue

        current_hash = _git_head_hash(p)
        if not current_hash:
            continue

        last_hash = proj.get("last_commit_hash", "")
        if current_hash == last_hash:
            continue

        # Count new commits
        new_commits = 0
        if last_hash:
            commits = _git_log(p, since_hash=last_hash)
            new_commits = len(commits)
        else:
            new_commits = 1  # first scan — just flag it

        if new_commits > 0:
            updates.append({
                "project": proj,
                "new_commits": new_commits,
                "current_hash": current_hash,
            })

    return updates


# ---------------------------------------------------------------------------
# Git hook installation
# ---------------------------------------------------------------------------

def install_git_hook(project_path: Path, experiment_id: str) -> bool:
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
        f"distillate --scan-project {experiment_id} 2>/dev/null &\n"
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


def detect_primary_metric(content: str) -> str:
    """Extract primary metric name from PROMPT.md content.

    Looks for patterns like:
      Primary metric: param_count (minimize)
      Primary metric: `test_accuracy` (maximize)
    """
    # Match "Primary metric: <name> (direction)" pattern
    m = re.search(
        r'[Pp]rimary\s+[Mm]etric\s*:\s*`?(\w+)`?\s*\(',
        content,
    )
    if m:
        return m.group(1)
    # Fallback: look for "key metric" or "north star" mentions
    m = re.search(
        r'(?:[Kk]ey|[Nn]orth\s*[Ss]tar)\s+[Mm]etric\s*:\s*`?(\w+)`?',
        content,
    )
    return m.group(1) if m else ""


def prune_orphan_state_runs(state_runs: dict, jsonl_ids: set) -> dict:
    """Drop ``state.runs`` entries whose ``name`` isn't in ``runs.jsonl``.

    ``state.runs`` is a cache rebuilt by the scanner from ``runs.jsonl``.
    When the file is rewritten (crash+restart reseeds the tail, an older
    parser used a different id scheme, etc.) the cache can retain
    entries whose ``name`` no longer appears in the file. Those
    phantoms inflate ``displayRuns.length`` in the UI and mask real run
    state — hence this pure helper, called from the list route before
    serving.

    :param state_runs: current ``proj.runs`` dict (state-internal ids ->
        run data, where each run dict has a ``name`` keying it back to
        the ``runs.jsonl`` entry's ``id``).
    :param jsonl_ids: set of run ids actually present in ``runs.jsonl``.
    :returns: a new dict containing only the entries whose ``name`` is
        in ``jsonl_ids``. Entries without a ``name`` field are dropped
        (junk state).
    """
    if not state_runs:
        return {}
    return {
        sid: run
        for sid, run in state_runs.items()
        if run.get("name") and run["name"] in jsonl_ids
    }


def infer_key_metric_name(proj: dict) -> str:
    """Pick the best metric to chart by default.

    Priority order:
    1. Explicit goal metric (user told us what matters)
    2. Test-set performance metric present in most runs
    3. Validation-set performance metric
    4. Any performance metric (accuracy, f1, auc, etc.)
    5. Most common numeric metric across runs

    We prefer metrics that have data in ALL (or most) runs so the
    chart is meaningful, and favour "test > val > train" and
    "accuracy/f1/auc > loss/error" for relevance.
    """
    from collections import Counter

    # --- 0. Explicit user override (from PATCH or wizard) ---
    explicit = proj.get("key_metric_name", "")
    if explicit:
        # Check if the explicit name actually exists in run data
        runs_check = list(proj.get("runs", {}).values())
        all_metrics = set()
        for r in runs_check:
            for k, v in r.get("results", {}).items():
                if isinstance(v, (int, float)):
                    all_metrics.add(k)
        if explicit in all_metrics:
            return explicit
        # Fuzzy match: find metrics containing the explicit name or vice versa
        for m in sorted(all_metrics):
            if explicit in m or m in explicit:
                return m
        # No match in data — return explicit anyway (may be set before first run)
        return explicit

    # --- 1. Goal metric ---
    goals = proj.get("goals", [])
    if goals:
        for g in goals:
            if isinstance(g, dict) and g.get("metric") and not g.get("is_constraint"):
                return g["metric"]
        if isinstance(goals[0], dict) and goals[0].get("metric"):
            return goals[0]["metric"]

    # --- 2-5. Score-based ranking ---
    runs = list(proj.get("runs", {}).values())
    if not runs:
        return ""

    # Count how many runs have each numeric metric
    metric_counts: Counter = Counter()
    for run in runs:
        for k, v in run.get("results", {}).items():
            if isinstance(v, (int, float)):
                metric_counts[k] += 1

    if not metric_counts:
        return ""

    total_runs = len(runs)

    # Score each metric: coverage * relevance
    _RELEVANCE = {
        # Test-set performance (highest priority)
        "test_accuracy": 100, "test_acc": 100, "test_f1": 95,
        "test_auc": 90, "test_precision": 85, "test_recall": 85,
        "test_score": 80, "test_r2": 80, "test_rmse": 75,
        "test_loss": 70, "test_error": 70, "test_mae": 70,
        # Validation-set
        "val_accuracy": 60, "val_acc": 60, "val_f1": 55,
        "val_auc": 50, "val_loss": 45, "val_error": 45,
        "val_score": 50, "val_r2": 50, "val_rmse": 45,
        # Generic performance
        "accuracy": 40, "f1": 38, "f1_score": 38,
        "auc": 35, "precision": 30, "recall": 30,
        "rmse": 25, "mae": 25, "r2": 25, "r2_score": 25,
        "loss": 20, "error": 20,
        # Meta (low priority — usually not what you want to chart)
        "param_count": 5, "train_time_sec": 3, "epochs": 2,
    }

    def _score(metric_name: str) -> float:
        coverage = metric_counts[metric_name] / total_runs
        name_lower = metric_name.lower().replace("-", "_")
        # Exact match first
        relevance = _RELEVANCE.get(name_lower, 0)
        if relevance == 0:
            # Fuzzy: check if name contains key patterns
            for pattern, score in [
                ("test", 30), ("accuracy", 25), ("acc", 25),
                ("f1", 20), ("auc", 20), ("score", 15),
                ("val", 12), ("loss", 10), ("error", 10),
                ("rmse", 10), ("mae", 10),
            ]:
                if pattern in name_lower:
                    relevance = max(relevance, score)
            if relevance == 0:
                relevance = 8  # unknown metric baseline
        # Penalize non-optimization metrics (counts, times, costs)
        cat = classify_metric(metric_name)
        if cat in ("count", "time", "cost"):
            relevance = min(relevance, 1)
        return coverage * relevance

    best = max(metric_counts.keys(), key=_score)
    return best
