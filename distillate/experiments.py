"""ML experiment tracking for Distillate.

Discovers ML projects, reconstructs experiment history from artifacts
(training logs, configs, checkpoints, results), and generates rich
markdown lab notebooks.  Works with or without git.
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
import logging
import os
import re
import subprocess

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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
_COMMAND_DISPLAY_CHARS = 200     # truncation limit for commands in LLM prompt
_FINGERPRINT_HEX_LENGTH = 16    # hex chars for enrichment cache fingerprint

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

def scan_project(path: Path) -> dict:
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
    for run in ingested:
        # Structured runs (from runs.jsonl) have explicit unique IDs —
        # skip dedup which can merge distinct runs sharing hyperparameters.
        if run.get("source") == "structured" or not _is_duplicate_run(runs, run):
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


# ---------------------------------------------------------------------------
# Structured reporting + hook event ingestion
# ---------------------------------------------------------------------------

# Valid status values in runs.jsonl
_STRUCTURED_STATUSES = {"keep", "discard", "crash", "running"}


def _parse_runs_jsonl(path: Path) -> list[dict]:
    """Parse .distillate/runs.jsonl into run dicts."""
    runs_file = path / ".distillate" / "runs.jsonl"
    if not runs_file.exists():
        return []

    runs = []
    try:
        with open(runs_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("$schema") != "distillate/run/v1":
                    continue

                # Map structured report to internal run schema
                run_id = entry.get("id", "")
                if not run_id:
                    continue

                status = entry.get("status", "completed")
                # Map keep/discard/crash to internal status + decision
                decision = status if status in _STRUCTURED_STATUSES else None
                internal_status = "completed"
                if status == "crash":
                    internal_status = "failed"
                elif status == "running":
                    internal_status = "running"

                ts = entry.get("timestamp", "")
                duration_secs = entry.get("duration_seconds", 0)
                duration_mins = int(duration_secs / 60) if duration_secs else 0

                run = _create_run(
                    prefix="sr",
                    name=run_id,
                    hyperparameters=entry.get("hyperparameters", {}),
                    results=entry.get("results", {}),
                    started_at=ts,
                    completed_at=ts,
                    duration_minutes=duration_mins,
                    source="structured",
                    decision=decision,
                    description=entry.get("description", ""),
                    agent_reasoning=entry.get("reasoning", ""),
                    hypothesis=entry.get("hypothesis", ""),
                    changes=entry.get("changes", ""),
                    commit=entry.get("commit", ""),
                    baseline_comparison=entry.get("baseline_comparison"),
                )
                run["status"] = internal_status
                runs.append(run)
    except OSError:
        pass

    # Deduplicate by run name: last entry wins (append-only log semantics)
    seen: dict[str, int] = {}
    for i, run in enumerate(runs):
        seen[run["name"]] = i
    runs = [runs[i] for i in sorted(seen.values())]

    return runs


def _parse_events_jsonl(path: Path) -> list[dict]:
    """Parse .distillate/events.jsonl (hook events) into run dicts."""
    events_file = path / ".distillate" / "events.jsonl"
    if not events_file.exists():
        return []

    runs = []
    try:
        with open(events_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue

                if event.get("type") != "run_completed":
                    continue

                hp = event.get("hyperparameters", {})
                metrics = event.get("results", {})
                if not hp and not metrics:
                    continue

                ts = event.get("ts", "")
                command = event.get("command", "")
                session_id = event.get("session_id", "")

                # Build name from command or session
                name = ""
                if command:
                    m = re.search(r"python[3]?\s+(\S+\.py)", command)
                    if m:
                        name = Path(m.group(1)).stem

                run = _create_run(
                    prefix="hook",
                    name=name or f"hook-{session_id[:8]}",
                    hyperparameters=hp,
                    results=metrics,
                    started_at=ts,
                    completed_at=ts,
                    source="hooks",
                    command=command,
                    session_id=session_id,
                )
                runs.append(run)
    except OSError:
        pass

    return runs


def ingest_runs(project_path: Path) -> list[dict]:
    """Ingest runs from structured reports + hook events.

    Reads ``.distillate/runs.jsonl`` (structured, primary) and
    ``.distillate/events.jsonl`` (hooks, secondary).  Correlates by
    timestamp proximity + hyperparameter fingerprint.  Structured
    wins on conflicts; hooks fill gaps.

    Returns a list of run dicts.
    """
    structured = _parse_runs_jsonl(project_path)
    hook_runs = _parse_events_jsonl(project_path)

    if not structured and not hook_runs:
        return []

    # Index structured runs by fingerprint for correlation
    structured_fps: dict[str, dict] = {}
    for run in structured:
        hp = run.get("hyperparameters", {})
        if hp:
            structured_fps[_hyperparam_fingerprint(hp)] = run

    # Correlate hook runs with structured reports
    result = list(structured)  # structured runs are primary
    for hook_run in hook_runs:
        hp = hook_run.get("hyperparameters", {})
        if hp:
            fp = _hyperparam_fingerprint(hp)
            if fp in structured_fps:
                # Merge hook data into structured run (fill gaps)
                target = structured_fps[fp]
                if hook_run.get("command") and not target.get("command"):
                    target["command"] = hook_run["command"]
                if hook_run.get("session_id") and not target.get("session_id"):
                    target["session_id"] = hook_run["session_id"]
                continue

        # No match — create run from hook data alone
        hook_run["source"] = "hooks"
        result.append(hook_run)

    return result


def watch_project_artifacts(project_path: Path) -> list[dict]:
    """Check for new data since last scan.

    Polls ``.distillate/scan_state.json`` manifest and watches
    ``runs.jsonl`` / ``events.jsonl`` for new lines.

    Returns list of new events detected.
    """
    project_path = Path(project_path).resolve()
    distillate_dir = project_path / ".distillate"

    new_data: list[dict] = []

    # Check for new JSONL lines (runs.jsonl + events.jsonl)
    watch_state_file = distillate_dir / "watch_state.json"
    watch_state: dict = {}
    if watch_state_file.exists():
        try:
            watch_state = json.loads(watch_state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    for jsonl_name in ("runs.jsonl", "events.jsonl"):
        jsonl_path = distillate_dir / jsonl_name
        if not jsonl_path.exists():
            continue

        last_offset = watch_state.get(f"{jsonl_name}_offset", 0)
        try:
            file_size = jsonl_path.stat().st_size
        except OSError:
            continue

        if file_size <= last_offset:
            continue

        # Read new lines from offset
        try:
            with open(jsonl_path, encoding="utf-8") as f:
                f.seek(last_offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entry["_source_file"] = jsonl_name
                        new_data.append(entry)
                    except json.JSONDecodeError:
                        continue
            watch_state[f"{jsonl_name}_offset"] = file_size
        except OSError:
            continue

    # Check for new artifact files
    artifact_changes = _has_changed_files(project_path)
    if artifact_changes:
        new_data.append({"type": "artifacts_changed", "path": str(project_path)})

    # Save watch state
    if new_data:
        distillate_dir.mkdir(exist_ok=True)
        try:
            watch_state_file.write_text(
                json.dumps(watch_state, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    return new_data


# ---------------------------------------------------------------------------
# Claude Code log extraction
# ---------------------------------------------------------------------------

# Training script indicators in bash commands
# Detect "python ... something.py" invocations
_PYTHON_SCRIPT_RE = re.compile(
    r"python[3]?\s+(\S+\.py)", re.IGNORECASE,
)
# Match training-related script FILENAMES (not directory paths)
_TRAIN_FILENAME_RE = re.compile(
    r"(?:train|run_exp|finetune|sweep)", re.IGNORECASE,
)

# Key=value pairs on the command line (e.g. d_model=8 lr=0.003)
_CMD_KV_RE = re.compile(
    r"(?<![/\w])(\w+)\s*=\s*([\d.eE+-]+|[Tt]rue|[Ff]alse)"
)

# Known hyperparameter names (for filtering noise from key=value extraction)
_KNOWN_HYPERPARAMS = {
    "d_model", "n_heads", "d_ff", "n_layers", "epochs", "lr",
    "learning_rate", "batch_size", "dropout", "warmup_steps",
    "weight_decay", "eval_every", "hidden_dim", "num_layers",
    "num_heads", "embed_dim", "max_len", "val_split", "seed",
    "gradient_clip", "accumulation_steps", "num_epochs", "steps",
}

# Metric patterns in training stdout
_METRIC_RE = re.compile(
    r"(?:^|[|\s,])\s*"
    r"(accuracy|loss|exact_match|val_loss|val_accuracy|test_accuracy|"
    r"train_loss|train_accuracy|val_exact_match|f1|precision|recall|"
    r"perplexity|bleu|rouge|auc|best_val_acc|final_loss)"
    r"\s*[=:]\s*([\d.]+)%?",
    re.IGNORECASE | re.MULTILINE,
)

# Config JSON block in stdout (e.g. "Config: { ... }")
_CONFIG_BLOCK_RE = re.compile(
    r"Config:\s*(\{[^}]+\})", re.DOTALL,
)


def _find_claude_log_dir(project_path: Path) -> Optional[Path]:
    """Find the Claude Code log directory for a project path."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.is_dir():
        return None
    encoded = str(project_path).replace("/", "-")
    log_dir = claude_dir / encoded
    if log_dir.is_dir():
        return log_dir
    return None


def _parse_training_command(command: str) -> Optional[dict]:
    """Parse a bash command and extract hyperparameters if it's a training run.

    Returns {"script": "train.py", "hyperparameters": {...}} or None.
    Matches based on the script FILENAME (not directory path), so
    'python experiment/train.py' matches but 'python experiment/plot.py'
    does not.
    """
    m = _PYTHON_SCRIPT_RE.search(command)
    if not m:
        return None
    full_path = m.group(1)
    # Check the filename only (strip directory prefix)
    filename = full_path.rsplit("/", 1)[-1]
    if not _TRAIN_FILENAME_RE.search(filename):
        return None

    script = full_path

    # Extract key=value pairs
    hyperparams: dict[str, Any] = {}
    for key, val in _CMD_KV_RE.findall(command):
        key_lower = key.lower()
        # Only keep known hyperparameter names or numeric-valued keys
        if key_lower in _KNOWN_HYPERPARAMS or key in _KNOWN_HYPERPARAMS:
            hyperparams[key] = _coerce_value(val)
        elif re.match(r"^[\d.eE+-]+$", val):
            # Keep any numeric key=value — likely a hyperparameter
            hyperparams[key] = _coerce_value(val)

    return {"script": script, "hyperparameters": hyperparams}


def _coerce_value(val: str) -> Any:
    """Coerce a string value to int, float, or bool."""
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    try:
        f = float(val)
        return int(f) if f == int(f) and "." not in val and "e" not in val.lower() else f
    except ValueError:
        return val


def _extract_metrics_from_output(text: str) -> dict:
    """Extract metric values from training stdout text."""
    metrics: dict[str, float] = {}
    # Find all metric=value patterns; keep the LAST occurrence (final value)
    for match in _METRIC_RE.finditer(text):
        name = match.group(1).lower()
        try:
            metrics[name] = float(match.group(2))
        except ValueError:
            pass
    return metrics


def _parse_config_block(text: str) -> dict:
    """Extract hyperparameters from a 'Config: {...}' JSON block in stdout."""
    m = _CONFIG_BLOCK_RE.search(text)
    if not m:
        return {}
    try:
        config = json.loads(m.group(1))
        if isinstance(config, dict):
            return {k: v for k, v in config.items()
                    if isinstance(v, (int, float, str, bool))}
    except json.JSONDecodeError:
        pass
    return {}


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
    """Sort key that orders runs by version number, then by timestamp.

    Version extraction: 'v5' -> 5, 'final' -> 9999, unversioned -> 0.
    Falls back to started_at/completed_at timestamp.
    """
    name = run.get("name", "")
    tags = run.get("tags", [])
    tag = tags[0] if tags else name

    # Extract version number from tag or name
    version = 0
    m = re.search(r"v(\d+)", tag, re.IGNORECASE)
    if m:
        version = int(m.group(1))
    elif "final" in tag.lower():
        version = 9999

    ts = run.get("started_at") or run.get("completed_at") or ""
    return (version, ts)


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
        items.append(f"{k}={v}")
    return "|".join(items)


def extract_runs_from_claude_logs(project_path: Path) -> list[dict]:
    """Extract experiment runs from Claude Code conversation logs.

    Parses ~/.claude/projects/<encoded-path>/*.jsonl files for bash
    training commands and their stdout output.  Returns a list of
    run dicts compatible with scan_project().
    """
    log_dir = _find_claude_log_dir(project_path)
    if not log_dir:
        return []

    runs: list[dict] = []
    for jsonl_file in sorted(log_dir.glob("*.jsonl")):
        try:
            session_runs = _parse_claude_session(jsonl_file)
            runs.extend(session_runs)
        except (json.JSONDecodeError, OSError, KeyError, ValueError, IndexError):
            log.debug("Failed to parse Claude session %s", jsonl_file, exc_info=True)
            continue

    return runs


def _parse_claude_session(jsonl_path: Path) -> list[dict]:
    """Parse a single Claude Code JSONL session file for training runs."""
    messages: list[dict] = []
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    # Walk messages looking for (Bash tool_use, tool_result) pairs
    runs: list[dict] = []
    pending_commands: dict[str, dict] = {}  # tool_use_id -> {command, timestamp, parsed}

    for msg in messages:
        msg_type = msg.get("type")
        timestamp = msg.get("timestamp", "")
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue

        if msg_type == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    command = block.get("input", {}).get("command", "")
                    parsed = _parse_training_command(command)
                    if parsed:
                        tool_id = block.get("id", "")
                        pending_commands[tool_id] = {
                            "command": command,
                            "timestamp": timestamp,
                            "parsed": parsed,
                        }

        elif msg_type == "user":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tool_id = block.get("tool_use_id", "")
                    if tool_id in pending_commands:
                        pending = pending_commands.pop(tool_id)
                        output = block.get("content", "")
                        if not isinstance(output, str):
                            continue

                        # Extract metrics and config from output
                        metrics = _extract_metrics_from_output(output)
                        config_hp = _parse_config_block(output)

                        # Merge hyperparameters: command-line overrides config block
                        hp = {**config_hp, **pending["parsed"]["hyperparameters"]}

                        # Build name from model tag or script
                        name = _tag_from_config(hp)
                        if not name:
                            name = Path(pending["parsed"]["script"]).stem

                        runs.append(_create_run(
                            prefix="claude",
                            name=name,
                            hyperparameters=hp,
                            results=metrics,
                            tags=[name] if name else [],
                            started_at=pending["timestamp"],
                            completed_at=timestamp,
                            source="claude_logs",
                            session_file=jsonl_path.name,
                            command=pending["command"],
                        ))

    return runs


# ---------------------------------------------------------------------------
# LLM enrichment (narrative sections via Claude)
# ---------------------------------------------------------------------------

def _runs_fingerprint(runs: dict) -> str:
    """Hash of sorted run hyperparams+metrics for cache invalidation."""
    items = []
    for rid in sorted(runs.keys()):
        r = runs[rid]
        hp = json.dumps(r.get("hyperparameters", {}), sort_keys=True)
        mt = json.dumps(r.get("results", {}), sort_keys=True)
        items.append(f"{rid}:{hp}:{mt}")
    return hashlib.sha256("|".join(items).encode()).hexdigest()[:_FINGERPRINT_HEX_LENGTH]


def load_enrichment_cache(project_path: Path) -> dict:
    """Load LLM enrichment cache from .distillate/llm_enrichment.json."""
    cache_file = project_path / ".distillate" / "llm_enrichment.json"
    if not cache_file.exists():
        return {}
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_enrichment_cache(project_path: Path, data: dict) -> None:
    """Save LLM enrichment cache to .distillate/llm_enrichment.json."""
    distillate_dir = project_path / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    (distillate_dir / "llm_enrichment.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def _build_enrichment_prompt(runs: dict, project_name: str) -> str:
    """Build the Sonnet prompt for enriching experiment runs."""
    # Sort runs by version number, then chronologically
    sorted_runs = sorted(runs.items(), key=lambda kv: _run_sort_key(kv[1]))

    run_descriptions = []
    prev_hp: dict = {}
    for i, (rid, run) in enumerate(sorted_runs, 1):
        hp = run.get("hyperparameters", {})
        metrics = run.get("results", {})
        command = run.get("command", "")

        # Build diff from previous
        diff_parts = []
        if prev_hp:
            for k in sorted(set(hp.keys()) | set(prev_hp.keys())):
                old = prev_hp.get(k)
                new = hp.get(k)
                if old != new:
                    if old is not None and new is not None:
                        diff_parts.append(f"{k}: {old} -> {new}")
                    elif new is not None:
                        diff_parts.append(f"{k}: (new) {new}")

        hp_str = ", ".join(f"{k}={v}" for k, v in sorted(hp.items()))
        metric_str = ", ".join(
            f"{k}={v}" for k, v in sorted(metrics.items())
            if isinstance(v, (int, float))
        )
        diff_str = "; ".join(diff_parts) if diff_parts else "(first experiment)"

        desc = f"Experiment {i} [{rid}]: {run.get('name', '?')}\n"
        desc += f"  Hyperparameters: {hp_str}\n"
        desc += f"  Metrics: {metric_str}\n"
        desc += f"  Changes from previous: {diff_str}\n"
        if command:
            desc += f"  Command: {command[:_COMMAND_DISPLAY_CHARS]}\n"

        run_descriptions.append(desc)
        prev_hp = hp

    run_ids_json = json.dumps([rid for rid, _ in sorted_runs])

    return f"""You are a research scientist writing a lab notebook for an ML experiment series.

Project: {project_name}

Experiment timeline (chronological order):

{chr(10).join(run_descriptions)}

For each experiment, generate:
1. name: A descriptive human-readable name (e.g. "Baseline Character-Level Transformer", "Scaled-Up Model", "Triplet Tokenization Breakthrough"). Keep it short (3-6 words).
2. hypothesis: Why this experiment was tried (1-2 sentences). For the first experiment, describe the baseline rationale.
3. approach: What was done differently from the previous experiment (1-2 sentences).
4. analysis: What the results mean — interpret the metrics by name, note failure modes, explain surprising outcomes (2-3 sentences). Be specific: say "loss dropped from 13.0 to 0.0" not just "improved". If no metrics were reported, say so explicitly and speculate why (e.g. incomplete run, setup step, utility script).
5. next_steps: What to try next based on these results (1 sentence).
6. params: Estimated total trainable parameter count as a SHORT string (e.g. "~98K", "~1.5K", "~350"). ALWAYS estimate this from the architecture hyperparameters — for a transformer, consider embeddings, attention projections, feedforward layers, and layer norms. If a run's hyperparameters are incomplete, infer the architecture from adjacent experiments in the series (e.g. the first run likely used the same architecture as the next run that has full hyperparameters). NEVER use "N/A" — every training run had a model, estimate its size.
7. validation: The key quality metric result as a SHORT string. Use the most important evaluation metric (e.g. "100%", "99.8%", "converged", "loss=0.0"). If the experiment has no reported results but the architecture was later re-run successfully with the same or similar hyperparameters, infer the likely outcome. If the experiment genuinely failed or was too small to learn, say "failed" or "did not converge". NEVER say "no metrics" — always make a judgment call based on context.

Also generate project-level insights:
8. key_breakthrough: Which experiment was the biggest improvement and why (1-2 sentences). Reference specific metric values.
9. lessons_learned: 3-5 bullets of deeper insights connecting the experiments. Reference concrete numbers, not vague claims.

Output ONLY valid JSON in this exact format (no markdown, no code blocks):
{{"runs": {{{run_ids_json[1:-1].replace('"', '')}: see below}}, "project": {{"key_breakthrough": "...", "lessons_learned": ["..."]}}}}

The "runs" object must have keys matching these exact run IDs: {run_ids_json}
Each run value: {{"name": "...", "hypothesis": "...", "approach": "...", "analysis": "...", "next_steps": "...", "params": "...", "validation": "..."}}"""


def enrich_runs_with_llm(runs: dict, project_name: str,
                          project_path: Path) -> Optional[dict]:
    """Enrich experiment runs with LLM-generated narrative sections.

    Uses Claude Sonnet to add hypothesis, approach, analysis, next_steps,
    and a descriptive name to each run.  Also adds project-level insights.

    Returns enrichment dict or None if unavailable (no API key, error).
    Results are cached in .distillate/llm_enrichment.json.
    """
    from distillate import config

    if not config.ANTHROPIC_API_KEY:
        log.debug("No ANTHROPIC_API_KEY — skipping LLM enrichment")
        return None

    if not runs:
        return None

    # Check cache
    fingerprint = _runs_fingerprint(runs)
    cache = load_enrichment_cache(project_path)
    if cache.get("fingerprint") == fingerprint and cache.get("enrichment"):
        log.info("Using cached LLM enrichment for %s", project_name)
        return cache["enrichment"]

    # Build prompt and call Sonnet
    prompt = _build_enrichment_prompt(runs, project_name)

    # Guard against overly large prompts (rough estimate: ~4 chars per token)
    _MAX_ENRICHMENT_CHARS = 400_000  # ~100K tokens, well under 200K limit
    if len(prompt) > _MAX_ENRICHMENT_CHARS:
        log.warning(
            "Enrichment prompt too large (%d chars) for %s — skipping",
            len(prompt), project_name,
        )
        return None

    try:
        import anthropic
    except ImportError:
        log.error("LLM enrichment requires the 'anthropic' package")
        return None

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.CLAUDE_SMART_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        if not response.content or not hasattr(response.content[0], "text"):
            log.error("Unexpected API response: no content blocks")
            return None
        text = response.content[0].text.strip()
        log.info("LLM enrichment response: %d chars", len(text))
    except (anthropic.APIError, anthropic.APIConnectionError) as e:
        log.error("Claude API error during LLM enrichment: %s", e)
        return None

    # Parse JSON response (handle markdown code blocks)
    if text.startswith("```") and "\n" in text:
        text = text.split("\n", 1)[1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    try:
        enrichment = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                enrichment = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                log.error("Failed to parse LLM enrichment JSON")
                return None
        else:
            log.error("No JSON found in LLM enrichment response")
            return None

    if not isinstance(enrichment, dict) or "runs" not in enrichment:
        log.error("LLM enrichment missing 'runs' key")
        return None

    # Cache the result
    _save_enrichment_cache(project_path, {
        "fingerprint": fingerprint,
        "enrichment": enrichment,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })

    return enrichment


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

def update_project(project: dict, state: Any) -> bool:
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
# Notebook generation
# ---------------------------------------------------------------------------

def _factorize_hyperparams(runs: list[dict]) -> tuple[dict, set]:
    """Find hyperparameters that are identical across all runs that have them.

    Returns (common_params, varying_keys).  A param is "common" if every run
    that defines it uses the same value and at least 2 runs define it.
    """
    # Collect all values per key
    key_values: dict[str, list] = {}
    key_counts: dict[str, int] = {}
    for run in runs:
        for k, v in run.get("hyperparameters", {}).items():
            key_values.setdefault(k, []).append(v)
            key_counts[k] = key_counts.get(k, 0) + 1

    common: dict = {}
    varying: set = set()
    for k, vals in key_values.items():
        if key_counts[k] >= 2 and len(set(str(v) for v in vals)) == 1:
            common[k] = vals[0]
        else:
            varying.add(k)

    return common, varying


def generate_notebook(project: dict, section: str = "main",
                      enrichment: Optional[dict] = None) -> str:
    """Generate a markdown lab notebook for a project.

    Produces a rich document with project overview, experiment timeline,
    per-run detail cards, and diff sections between consecutive runs.

    If ``enrichment`` is provided (from ``enrich_runs_with_llm()``), adds
    narrative sections: hypothesis, approach, analysis, next steps per run,
    plus project-level research insights.
    """
    name = project.get("name", "Untitled Project")
    runs_dict = project.get("runs", {})
    runs = list(runs_dict.values())
    # Unwrap cache format: {fingerprint, enrichment: {runs, project}} → {runs, project}
    _enr = enrichment or {}
    if "enrichment" in _enr and isinstance(_enr["enrichment"], dict):
        _enr = _enr["enrichment"]
    run_enrichments = _enr.get("runs", {})
    project_insights = _enr.get("project", {})

    # Sort runs by version number, then chronologically
    runs.sort(key=_run_sort_key)

    # Factorize hyperparameters
    common_params, varying_keys = _factorize_hyperparams(runs)

    # Helper to get enrichment for a run
    def _enrich(run: dict, field: str) -> str:
        e = run_enrichments.get(run.get("id", ""), {})
        return e.get(field, "")

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

    # Research Insights (from enrichment) — at the top
    if project_insights:
        breakthrough = project_insights.get("key_breakthrough", "")
        lessons = project_insights.get("lessons_learned", [])
        if breakthrough or lessons:
            parts.append("")
            parts.append("## Research Insights")
            if breakthrough:
                parts.append("")
                parts.append("### Key Breakthrough")
                parts.append(f"> {breakthrough}")
            if lessons:
                parts.append("")
                parts.append("### Lessons Learned")
                for idx, lesson in enumerate(lessons, 1):
                    parts.append(f"{idx}. {lesson}")

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
        kept = sum(1 for r in runs if r.get("decision") == "keep")
        discarded = sum(1 for r in runs if r.get("decision") == "discard")
        crashed = sum(1 for r in runs if r.get("decision") == "crash")
        has_decisions = kept + discarded + crashed > 0

        parts.append("")
        parts.append("## Experiment Timeline")
        parts.append("")
        if has_decisions:
            parts.append(
                f"> **{len(runs)}** experiments | "
                f"**{kept}** kept | "
                f"**{discarded}** discarded | "
                f"**{crashed}** crashed"
            )
        else:
            parts.append(
                f"> **{len(runs)}** experiments | "
                f"**{completed}** completed | "
                f"**{running}** running | "
                f"**{failed}** failed"
            )
        parts.append("")
        if has_decisions:
            parts.append("| # | Experiment | Decision | Duration | Result |")
            parts.append("|---|-----------|----------|----------|--------|")
        else:
            parts.append("| # | Experiment | Status | Duration | Result |")
            parts.append("|---|-----------|--------|----------|--------|")

        for i, run in enumerate(runs, 1):
            duration = _fmt_duration(run.get("duration_minutes", 0))
            run_enr = run_enrichments.get(run.get("id", ""), {})
            key_metric = _pick_key_metric(run.get("results", {}), run_enr or None)
            display_name = _enrich(run, "name") or run.get("name", "?")
            if has_decisions:
                decision = run.get("decision", "")
                decision_md = {"keep": "✓", "discard": "✗", "crash": "⚠"}.get(decision, "-")
                parts.append(
                    f"| {i} | {display_name} | {decision_md} {decision} | "
                    f"{duration} | {key_metric} |"
                )
            else:
                status_icon = _status_icon(run.get("status", "planned"))
                parts.append(
                    f"| {i} | {display_name} | {status_icon} | "
                    f"{duration} | {key_metric} |"
                )

    # Common hyperparameters table
    if common_params:
        parts.append("")
        parts.append("## Common Configuration")
        parts.append("")
        parts.append("> Parameters shared across all experiments.")
        parts.append("")
        parts.append("| Parameter | Value |")
        parts.append("|-----------|-------|")
        for k, v in sorted(common_params.items()):
            parts.append(f"| {k} | `{v}` |")

    # Per-run detail cards
    if runs:
        parts.append("")
        parts.append("## Experiment Details")

    for i, run in enumerate(runs):
        display_name = _enrich(run, "name") or run.get("name", "Untitled")
        parts.append("")
        parts.append(f"### {run.get('id', '')}: {display_name}")
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

        # Narrative sections — user-provided hypothesis takes precedence
        hypothesis = run.get("hypothesis", "") or _enrich(run, "hypothesis")
        if hypothesis:
            parts.append("")
            parts.append("#### Hypothesis")
            parts.append(hypothesis)

        approach = _enrich(run, "approach")
        if approach:
            parts.append("")
            parts.append("#### Approach")
            parts.append(approach)

        # Hyperparameters table — only varying params (or all if no common)
        hyperparams = run.get("hyperparameters", {})
        if hyperparams:
            delta = {k: v for k, v in hyperparams.items()
                     if k in varying_keys or not common_params}
            if delta:
                label = "#### Configuration (changes)" if common_params else "#### Hyperparameters"
                parts.append("")
                parts.append(label)
                parts.append("")
                parts.append("| Parameter | Value |")
                parts.append("|-----------|-------|")
                for k, v in delta.items():
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

        # Analysis from enrichment
        analysis = _enrich(run, "analysis")
        if analysis:
            parts.append("")
            parts.append("#### Analysis")
            parts.append(analysis)

        # Agent reasoning (from structured reports)
        reasoning = run.get("agent_reasoning", "")
        if reasoning:
            parts.append("")
            parts.append("#### Agent Reasoning")
            parts.append(f"> {reasoning}")

        if run.get("notes"):
            parts.append("")
            parts.append("#### Notes")
            for note in run["notes"]:
                parts.append(f"- {note}")

        # Next steps from enrichment
        next_steps = _enrich(run, "next_steps")
        if next_steps:
            parts.append("")
            parts.append("#### Next Steps")
            parts.append(next_steps)

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
    """Format a metric value based on its category."""
    if not isinstance(val, (int, float)):
        return str(val)
    cat = classify_metric(name)
    if cat == "ratio":
        if isinstance(val, float) and 0 < val <= 1:
            return f"{val:.2%}"
        return f"{val:.2f}"
    if cat == "loss":
        if isinstance(val, float):
            if abs(val) < 0.001:
                return f"{val:.2e}"
            if abs(val) < 1:
                return f"{val:.4f}"
        return f"{val:.2f}"
    if cat == "count":
        if isinstance(val, int) or (isinstance(val, float) and val == int(val)):
            iv = int(val)
            v = abs(iv)
            if v >= 1e9:
                return f"{iv / 1e9:.2f}B ({iv:,})"
            if v >= 1e6:
                return f"{iv / 1e6:.2f}M ({iv:,})"
            return f"{iv:,}"
        return f"{val:.2f}"
    if cat == "time":
        v = abs(val)
        if v >= 3600:
            h = int(v // 3600)
            m = int((v % 3600) // 60)
            return f"{h}h {m}m"
        if v >= 60:
            m = int(v // 60)
            s = int(v % 60)
            return f"{m}m {s}s"
        return f"{val:.2f}s"
    if cat == "cost":
        return f"${val:.2f}"
    if cat == "hyperparameter":
        if isinstance(val, float) and (abs(val) < 0.01 or abs(val) >= 1000):
            return f"{val:.2e}"
        return f"{val:.4g}"
    # generic
    if isinstance(val, int):
        return f"{val:,}"
    if isinstance(val, float):
        if 0 < val <= 1:
            return f"{val:.2%}"
        if abs(val) < 0.001:
            return f"{val:.2e}"
        if abs(val) < 1:
            return f"{val:.4f}"
    return f"{val:.2f}"


def _pick_key_metric(results: dict, enrichment: dict | None = None) -> str:
    """Pick the most important metric from results for the timeline table.

    If *enrichment* is available, combine params + validation from it.
    Otherwise falls back to heuristic priority ordering.
    Returns a labeled string like 'loss=0.0012' or 'accuracy=98.50%'.
    """
    if enrichment:
        parts = []
        if enrichment.get("params"):
            parts.append(enrichment["params"])
        if enrichment.get("validation"):
            parts.append(enrichment["validation"])
        # Fallback to legacy key_metric if new fields missing
        if not parts and enrichment.get("key_metric"):
            return enrichment["key_metric"]
        if parts:
            return ", ".join(parts)
    if not results:
        return "-"
    # Select best metric by category priority (loss/ratio first, skip counts/hyperparams)
    _CAT_PRIORITY = {"loss": 0, "ratio": 1, "generic": 2, "time": 3, "cost": 4}
    candidates = []
    for k, v in results.items():
        if not isinstance(v, (int, float)):
            continue
        cat = classify_metric(k)
        if cat in ("count", "hyperparameter"):
            continue
        candidates.append((k, v, _CAT_PRIORITY.get(cat, 99)))
    if candidates:
        candidates.sort(key=lambda x: x[2])
        k, v, _ = candidates[0]
        return f"{k}={_fmt_metric(k, v)}"
    # Last resort: first numeric metric
    for k, v in results.items():
        if isinstance(v, (int, float)):
            return f"{k}={_fmt_metric(k, v)}"
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
# HTML notebook generation
# ---------------------------------------------------------------------------

_HTML_NOTEBOOK_CSS = """\
:root {
  --bg: #0f0f23; --surface: #1a1a2e; --border: #2a2a3e;
  --text: #e6edf3; --text-dim: #8b949e;
  --accent: #6366f1; --accent-dim: rgba(99,102,241,0.15);
  --green: #3fb950; --green-dim: rgba(63,185,80,0.15);
  --red: #f85149; --yellow: #d29922; --cyan: #58a6ff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.6;
  padding: 2rem; max-width: 960px; margin: 0 auto;
}
h1 {
  font-size: 2rem; margin-bottom: 0.25rem;
  background: linear-gradient(135deg, #6366f1, #3b82f6);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.subtitle { color: var(--text-dim); font-size: 0.85rem; margin-bottom: 0.75rem; }
.subtitle code { background: var(--surface); padding: 2px 6px; border-radius: 4px; font-size: 0.8rem; }
.project-description { color: var(--text); font-size: 0.95rem; margin-bottom: 1rem; line-height: 1.6; padding: 0.75rem 1rem; background: var(--surface); border-radius: 8px; border-left: 3px solid var(--accent); }
.hero-metric { display: flex; align-items: baseline; gap: 12px; margin-bottom: 1.5rem; }
.hero-value { font-size: 2.25rem; font-weight: 700; color: var(--accent); font-variant-numeric: tabular-nums; }
.hero-label { font-size: 0.85rem; color: var(--text-dim); }
.stats-bar {
  display: flex; gap: 1.5rem; padding: 1rem 1.25rem;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; margin-bottom: 2rem; flex-wrap: wrap;
}
.stat { text-align: center; }
.stat-value { font-size: 1.5rem; font-weight: 700; color: var(--accent); }
.stat-label { font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }
h2 {
  font-size: 1.25rem; color: var(--accent); margin: 2rem 0 1rem;
  padding-bottom: 0.5rem; border-bottom: 1px solid var(--border);
}
.insights {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 1.5rem; margin-bottom: 2rem;
  border-left: 4px solid var(--accent);
}
.insights h3 { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--accent); margin-bottom: 0.5rem; }
.breakthrough { font-size: 1rem; line-height: 1.7; margin-bottom: 1.25rem; }
.lessons { list-style: none; padding: 0; }
.lessons li { position: relative; padding: 0.4rem 0 0.4rem 1.5rem; font-size: 0.9rem; line-height: 1.5; }
.lessons li::before { content: ""; position: absolute; left: 0; top: 0.75rem; width: 8px; height: 8px; background: var(--accent); border-radius: 50%; }
.lessons li + li { border-top: 1px solid var(--border); }
table { width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; font-size: 0.9rem; }
thead th { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 2px solid var(--border); color: var(--text-dim); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em; }
tbody td { padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); }
tbody tr:hover { background: rgba(99,102,241,0.05); }
tbody tr { cursor: pointer; }
.status-ok { color: var(--green); }
.metric-val { font-weight: 600; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.85rem; }
.metric-good { color: var(--green); }
.exp-name-enriched { color: var(--text-dim); font-size: 0.8rem; display: block; }
.config-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 0.5rem; margin-bottom: 1.5rem; }
.config-item { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 0.5rem 0.75rem; display: flex; justify-content: space-between; align-items: center; }
.config-key { color: var(--text-dim); font-size: 0.85rem; }
.config-val { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.85rem; color: var(--accent); }
details.run-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 0.75rem; transition: border-color 0.2s; }
details.run-card[open] { border-color: var(--accent); }
details.run-card summary { padding: 1rem 1.25rem; cursor: pointer; display: flex; justify-content: space-between; align-items: center; list-style: none; user-select: none; }
details.run-card summary::-webkit-details-marker { display: none; }
details.run-card summary::after { content: "\\25B6"; font-size: 0.7rem; color: var(--text-dim); transition: transform 0.2s; flex-shrink: 0; margin-left: 0.75rem; }
details.run-card[open] summary::after { transform: rotate(90deg); }
details.run-card summary:hover { background: rgba(99,102,241,0.04); border-radius: 10px; }
.run-summary-left { display: flex; align-items: center; gap: 0.5rem; flex: 1; min-width: 0; }
.run-index { color: var(--text-dim); font-size: 0.8rem; font-weight: 600; flex-shrink: 0; }
.run-name { font-size: 1rem; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.run-enriched-name { color: var(--text-dim); font-size: 0.82rem; font-weight: 400; margin-left: 0.25rem; }
.run-summary-right { display: flex; align-items: center; gap: 0.75rem; flex-shrink: 0; }
.run-metric-pill { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.78rem; padding: 2px 8px; border-radius: 4px; background: var(--green-dim); color: var(--green); }
.run-metric-pill.none { background: transparent; color: var(--text-dim); }
.run-time { color: var(--text-dim); font-size: 0.78rem; white-space: nowrap; }
.run-body { padding: 0 1.25rem 1.25rem; }
.narrative { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin-bottom: 0.75rem; }
.narrative-block { background: var(--bg); border-radius: 8px; padding: 0.75rem 1rem; border-left: 3px solid var(--border); }
.narrative-block.hypothesis { border-left-color: var(--cyan); }
.narrative-block.approach { border-left-color: var(--accent); }
.narrative-block.analysis { border-left-color: var(--green); }
.narrative-block.next-steps { border-left-color: var(--yellow); }
.narrative-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; margin-bottom: 0.25rem; }
.narrative-block.hypothesis .narrative-label { color: var(--cyan); }
.narrative-block.approach .narrative-label { color: var(--accent); }
.narrative-block.analysis .narrative-label { color: var(--green); }
.narrative-block.next-steps .narrative-label { color: var(--yellow); }
.narrative-text { font-size: 0.85rem; line-height: 1.55; color: var(--text); }
.tag { display: inline-block; background: var(--accent-dim); color: var(--accent); font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; margin-right: 0.25rem; font-weight: 500; }
.run-section-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); margin: 0.75rem 0 0.4rem; }
.params-row, .results-row { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.param-chip, .result-chip { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 3px 10px; font-size: 0.82rem; font-family: 'SF Mono', 'Fira Code', monospace; }
.param-chip .pk { color: var(--text-dim); }
.param-chip .pv { color: var(--text); }
.result-chip .rk { color: var(--text-dim); }
.result-chip .rv { font-weight: 600; }
.diff-section { margin-top: 0.75rem; padding: 0.6rem 0.75rem; background: var(--bg); border-radius: 6px; border-left: 3px solid var(--accent); font-size: 0.82rem; }
.diff-title { color: var(--accent); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; margin-bottom: 0.3rem; }
.diff-item { margin: 0.15rem 0; }
.diff-param { color: var(--yellow); }
.diff-improved { color: var(--green); }
.diff-regressed { color: var(--red); }
.diff-arrow { color: var(--text-dim); }
.notes-block { margin-top: 0.5rem; padding: 0.5rem 0.75rem; background: var(--bg); border-radius: 6px; border-left: 3px solid var(--cyan); font-size: 0.85rem; }
.notes-block .notes-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; color: var(--cyan); margin-bottom: 0.25rem; }
.toolbar { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.toolbar button { background: var(--surface); border: 1px solid var(--border); color: var(--text-dim); padding: 4px 12px; border-radius: 6px; font-size: 0.78rem; cursor: pointer; transition: all 0.15s; }
.toolbar button:hover { border-color: var(--accent); color: var(--text); }
.toolbar button.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
.run-desc { color: var(--text-dim); font-size: 0.8rem; font-style: italic; max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.section-header { display: flex; align-items: center; justify-content: space-between; }
.section-header h2 { border-bottom: none; margin-bottom: 0; flex: 1; }
.section-header .toolbar { margin-bottom: 0; }
.footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--border); color: var(--text-dim); font-size: 0.75rem; text-align: center; }
.decision-keep { color: var(--green); font-weight: 600; }
.decision-discard { color: #888; font-weight: 600; }
.decision-crash { color: var(--yellow); font-weight: 600; }
.reasoning-block { margin-top: 0.5rem; padding: 0.5rem 0.75rem; background: var(--bg); border-radius: 6px; border-left: 3px solid var(--accent); font-size: 0.85rem; }
.reasoning-block .reasoning-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; color: var(--accent); margin-bottom: 0.25rem; }
.metric-chart { margin: 1.5rem 0; }
.metric-chart svg { width: 100%; height: auto; }
.sparkline { vertical-align: middle; margin-left: 6px; opacity: 0.85; }
.result-chip .sparkline { margin-left: 4px; }
.stat .sparkline { display: block; margin: 4px auto 0; opacity: 0.7; }
@media (max-width: 640px) { .narrative { grid-template-columns: 1fr; } .run-enriched-name { display: none; } }
"""


def _h(text: str) -> str:
    """HTML-escape text for safe embedding."""
    return html_mod.escape(str(text))


def _decision_icon(decision: str | None) -> str:
    """Return an HTML-styled decision indicator."""
    if decision == "keep":
        return '<span class="decision-keep">&#10003; keep</span>'
    if decision == "discard":
        return '<span class="decision-discard">&#10007; discard</span>'
    if decision == "crash":
        return '<span class="decision-crash">&#9888; crash</span>'
    return '<span style="color:var(--text-dim)">&mdash;</span>'


def _sparkline_svg(values: list[float], width: int = 60, height: int = 16,
                   color: str = "#6366f1") -> str:
    """Render a tiny inline SVG sparkline from a list of numeric values."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo if hi != lo else 1.0
    # Pad Y slightly so line doesn't touch edges
    pad = 1
    inner_h = height - 2 * pad
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = round(i / (n - 1) * width, 1)
        y = round(pad + inner_h - (v - lo) / span * inner_h, 1)
        pts.append(f"{x},{y}")
    polyline = " ".join(pts)
    # Last point dot
    lx, ly = pts[-1].split(",")
    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">'
        f'<polyline points="{polyline}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{lx}" cy="{ly}" r="1.5" fill="{color}"/>'
        f'</svg>'
    )


def _render_metric_chart(runs: list[dict],
                         key_metric: str = "") -> str:
    """Render an inline SVG polyline chart of the primary metric over time.

    Each point is colored by decision (green=keep, red=discard, yellow=crash).
    Pure SVG — no JS library needed.
    """
    # Use key_metric if provided, otherwise auto-detect
    metric_name = key_metric
    if not metric_name:
        # Auto-detect: prefer loss/ratio metrics (the ones people chart),
        # then any other non-count/non-hyperparameter metric
        all_numeric = {}
        for run in runs:
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)) and k not in all_numeric:
                    all_numeric[k] = classify_metric(k)
        # Priority: loss > ratio > generic > time > cost > count
        _CAT_PRIORITY = {"loss": 0, "ratio": 1, "generic": 2,
                         "time": 3, "cost": 4, "count": 5}
        candidates = [(k, cat) for k, cat in all_numeric.items()
                      if cat not in ("hyperparameter",)]
        if candidates:
            candidates.sort(key=lambda kc: _CAT_PRIORITY.get(kc[1], 99))
            metric_name = candidates[0][0]
    if not metric_name:
        return ""

    # Collect data points
    points = []
    for i, run in enumerate(runs):
        val = run.get("results", {}).get(metric_name)
        if val is not None and isinstance(val, (int, float)):
            decision = run.get("decision", "")
            points.append((i, val, decision))

    if len(points) < 2:
        return ""

    # SVG dimensions
    w, h = 900, 200
    pad_x, pad_y = 50, 25
    chart_w = w - 2 * pad_x
    chart_h = h - 2 * pad_y

    indices = [p[0] for p in points]
    values = [p[1] for p in points]
    min_val = min(values)
    max_val = max(values)
    val_range = max_val - min_val or 1.0
    min_idx = min(indices)
    max_idx = max(indices)
    idx_range = max_idx - min_idx or 1

    def _x(idx: int) -> float:
        return pad_x + (idx - min_idx) / idx_range * chart_w

    def _y(val: float) -> float:
        return pad_y + chart_h - (val - min_val) / val_range * chart_h

    # Build SVG
    svg = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">']

    # Grid lines
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        y = pad_y + chart_h * (1 - frac)
        label_val = min_val + val_range * frac
        svg.append(f'<line x1="{pad_x}" y1="{y}" x2="{w - pad_x}" y2="{y}" '
                   f'stroke="#30363d" stroke-dasharray="4,4"/>')
        svg.append(f'<text x="{pad_x - 8}" y="{y + 4}" text-anchor="end" '
                   f'fill="#8b949e" font-size="11">{_h(_fmt_metric(metric_name, label_val))}</text>')

    # Polyline
    line_points = " ".join(f"{_x(idx)},{_y(val)}" for idx, val, _ in points)
    svg.append(f'<polyline points="{line_points}" fill="none" stroke="#6366f1" '
               f'stroke-width="2"/>')

    # Decision markers with tooltips
    colors = {"keep": "#3fb950", "discard": "#555555", "crash": "#d29922"}
    for idx, val, decision in points:
        color = colors.get(decision, "#8b949e")
        cx, cy = _x(idx), _y(val)
        # Get run description for tooltip
        run = runs[idx] if idx < len(runs) else {}
        desc = run.get("description", "") or run.get("hypothesis", "")
        run_id = run.get("name", run.get("id", f"#{idx + 1}"))
        tip = f"{_h(run_id)}: {_h(_fmt_metric(metric_name, val))} [{_h(decision or '?')}]"
        if desc:
            tip += f" — {_h(desc)}"
        svg.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="{color}" stroke="#0d1117" stroke-width="2">'
                   f'<title>{tip}</title></circle>')

    # Axis label
    svg.append(f'<text x="{w / 2}" y="{h - 2}" text-anchor="middle" '
               f'fill="#8b949e" font-size="12">{_h(metric_name)}</text>')

    svg.append("</svg>")
    return "\n".join(svg)


def generate_html_notebook(project: dict,
                           enrichment: Optional[dict] = None) -> str:
    """Generate a self-contained HTML lab notebook for a project.

    Mirrors ``generate_notebook()`` but outputs rich HTML with a dark theme,
    collapsible experiment cards, and color-coded metrics.
    """
    name = project.get("name", "Untitled Project")
    runs_dict = project.get("runs", {})
    runs = list(runs_dict.values())
    # Unwrap cache format: {fingerprint, enrichment: {runs, project}} → {runs, project}
    _enr = enrichment or {}
    if "enrichment" in _enr and isinstance(_enr["enrichment"], dict):
        _enr = _enr["enrichment"]
    run_enrichments = _enr.get("runs", {})
    project_insights = _enr.get("project", {})

    runs.sort(key=_run_sort_key, reverse=True)  # newest first by default
    common_params, varying_keys = _factorize_hyperparams(runs)

    def _enrich(run: dict, field: str) -> str:
        e = run_enrichments.get(run.get("id", ""), {})
        return e.get(field, "")

    # Pre-compute metric histories for sparklines (metric_name → [values in run order])
    metric_histories: dict[str, list[float]] = {}
    for run in runs:
        for k, v in run.get("results", {}).items():
            if isinstance(v, (int, float)):
                metric_histories.setdefault(k, []).append(float(v))

    parts: list[str] = []

    # --- Header ---
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append(f"<title>{_h(name)} — Lab Notebook</title>")
    parts.append(f"<style>{_HTML_NOTEBOOK_CSS}</style>")
    parts.append("</head><body>")
    parts.append(f"<h1>{_h(name)}</h1>")
    subtitle = (
        f'Lab Notebook &mdash; Generated {datetime.now().strftime("%Y-%m-%d")}'
        f'<br><code>{_h(project.get("path", ""))}</code>'
    )
    parts.append(f'<div class="subtitle">{subtitle}</div>')

    # --- Goal / Description ---
    description = project.get("description", "")
    if description:
        parts.append(f'<div class="project-description">{_h(description)}</div>')

    # --- North star metric ---
    key_metric_name = project.get("key_metric_name", "")
    if key_metric_name:
        # Find best value from kept runs (or all runs if no decisions)
        lower_better = _is_lower_better(key_metric_name)
        best_val = None
        kept_runs = [r for r in runs if r.get("decision") == "keep"]
        search_runs = kept_runs if kept_runs else runs
        for r in search_runs:
            v = r.get("results", {}).get(key_metric_name)
            if isinstance(v, (int, float)):
                if best_val is None or (v < best_val if lower_better else v > best_val):
                    best_val = v
        if best_val is not None:
            arrow = "&darr;" if lower_better else "&uarr;"
            parts.append(f'<div class="hero-metric">'
                         f'<span class="hero-value">{_h(_fmt_metric(key_metric_name, best_val))}</span>'
                         f'<span class="hero-label">{_h(key_metric_name)} {arrow}</span>'
                         f'</div>')

    # --- Research Insights (above stats for prominence) ---
    breakthrough = project_insights.get("key_breakthrough", "")
    lessons = project_insights.get("lessons_learned", [])
    if breakthrough or lessons:
        parts.append("<h2>Research Insights</h2>")
        parts.append('<div class="insights">')
        if breakthrough:
            parts.append('<h3>Key Breakthrough</h3>')
            parts.append(f'<p class="breakthrough">{_h(breakthrough)}</p>')
        if lessons:
            parts.append('<h3>Lessons Learned</h3>')
            parts.append('<ul class="lessons">')
            for lesson in lessons:
                parts.append(f"<li>{_h(lesson)}</li>")
            parts.append("</ul>")
        parts.append("</div>")

    # --- Stats bar ---
    completed = sum(1 for r in runs if r.get("status") == "completed")
    running = sum(1 for r in runs if r.get("status") == "running")
    kept = sum(1 for r in runs if r.get("decision") == "keep")
    discarded = sum(1 for r in runs if r.get("decision") == "discard")
    crashed = sum(1 for r in runs if r.get("decision") == "crash")
    has_decisions = kept + discarded + crashed > 0
    unique_configs = len({
        tuple(sorted(r.get("hyperparameters", {}).items()))
        for r in runs if r.get("hyperparameters")
    })
    parts.append('<div class="stats-bar">')
    parts.append(f'<div class="stat"><div class="stat-value">{len(runs)}</div>'
                 '<div class="stat-label">Experiments</div></div>')
    if has_decisions:
        parts.append(f'<div class="stat"><div class="stat-value" style="color:var(--green)">'
                     f'{kept}</div><div class="stat-label">Kept</div></div>')
        parts.append(f'<div class="stat"><div class="stat-value" style="color:var(--red)">'
                     f'{discarded}</div><div class="stat-label">Discarded</div></div>')
        if crashed:
            parts.append(f'<div class="stat"><div class="stat-value" style="color:var(--yellow)">'
                         f'{crashed}</div><div class="stat-label">Crashed</div></div>')
    else:
        parts.append(f'<div class="stat"><div class="stat-value" style="color:var(--green)">'
                     f'{completed}</div><div class="stat-label">Completed</div></div>')
        parts.append(f'<div class="stat"><div class="stat-value">{running}</div>'
                     '<div class="stat-label">Running</div></div>')
    parts.append(f'<div class="stat"><div class="stat-value">{unique_configs}</div>'
                 '<div class="stat-label">Configs Tested</div></div>')
    # Key metric sparkline in stats bar (use key_metric_name if set)
    spark_key = key_metric_name
    if not spark_key and metric_histories:
        spark_key = max(metric_histories, key=lambda k: len(metric_histories[k]))
    if spark_key and spark_key in metric_histories:
        history = metric_histories[spark_key]
        if len(history) >= 2:
            spark = _sparkline_svg(history, width=80, height=20, color="#6366f1")
            parts.append(f'<div class="stat"><div class="stat-value" '
                         f'style="font-size:0.85rem">{_h(spark_key)}</div>'
                         f'{spark}'
                         f'<div class="stat-label">Trend</div></div>')
    parts.append("</div>")

    # --- Goals ---
    goals = project.get("goals", [])
    if goals:
        parts.append("<h2>Success Criteria</h2>")
        parts.append('<ul class="lessons">')
        for g in goals:
            direction = g.get("direction", "maximize")
            threshold = g.get("threshold")
            label = f"{_h(g['metric'])}: {direction}"
            if threshold is not None:
                label += f" (target: {_h(_fmt_metric(g['metric'], threshold))})"
            parts.append(f"<li>{label}</li>")
        parts.append("</ul>")

    # --- Metric progression chart ---
    if has_decisions and len(runs) >= 2:
        chart_svg = _render_metric_chart(runs, key_metric=key_metric_name)
        if chart_svg:
            parts.append("<h2>Metric Progression</h2>")
            parts.append(f'<div class="metric-chart">{chart_svg}</div>')

    # --- Timeline table ---
    if runs:
        # Sort toggle + header
        parts.append('<div class="section-header">')
        parts.append("<h2>Experiment Timeline</h2>")
        parts.append('<div class="toolbar">')
        parts.append(
            '<button id="sort-toggle" onclick="'
            "var tb=document.getElementById('timeline-body');"
            "var rows=Array.from(tb.rows);"
            "rows.reverse();"
            "rows.forEach(function(r){tb.appendChild(r)});"
            "var btn=document.getElementById('sort-toggle');"
            "btn.textContent=btn.textContent==='↑ Oldest first'?'↓ Newest first':'↑ Oldest first';"
            '">&#8595; Newest first</button>')
        parts.append("</div></div>")

        parts.append("<table><thead><tr>")
        if has_decisions:
            parts.append("<th>#</th><th>Experiment</th><th>What</th>"
                         "<th>Decision</th><th>Params</th><th>Validation</th>")
        else:
            parts.append("<th>#</th><th>Experiment</th><th>What</th>"
                         "<th>Params</th><th>Validation</th>")
        parts.append("</tr></thead><tbody id='timeline-body'>")
        for i, run in enumerate(runs, 1):
            run_enr = run_enrichments.get(run.get("id", ""), {})
            key_metric = _pick_key_metric(run.get("results", {}), run_enr or None)
            raw_name = run.get("name", "?")
            enriched_label = _enrich(run, "name")

            # Short description: enriched name > hypothesis > changes > commit
            desc = (
                _enrich(run, "name")
                or run.get("hypothesis", "")
                or run.get("changes", "")
                or run.get("description", "")
                or ""
            )
            # Truncate to 7 words
            if desc:
                words = desc.split()
                if len(words) > 7:
                    desc = " ".join(words[:7]) + "\u2026"

            # Split key_metric into params/validation columns
            params_col = _h(run_enr.get("params", ""))
            val_col = _h(run_enr.get("validation", ""))
            if not params_col and not val_col:
                params_col = _h(key_metric)

            onclick = (
                f"document.getElementById('run-{i}').open=true;"
                f"document.getElementById('run-{i}').scrollIntoView("
                "{behavior:'smooth',block:'center'})"
            )
            enriched_span = ""
            if enriched_label and enriched_label != raw_name:
                enriched_span = f'<span class="exp-name-enriched">{_h(enriched_label)}</span>'
            status = run.get("status", "planned")
            val_style = ""
            if status == "completed":
                val_style = ' class="metric-val metric-good"'
            elif status == "failed":
                val_style = ' style="color:var(--red)"'

            parts.append(f'<tr onclick="{onclick}">')
            parts.append(f"<td>{i}</td>")
            parts.append(f"<td>{_h(raw_name)}{enriched_span}</td>")
            parts.append(f'<td class="run-desc" title="{_h(desc)}">{_h(desc)}</td>')
            if has_decisions:
                decision = run.get("decision", "")
                decision_html = _decision_icon(decision)
                parts.append(f"<td>{decision_html}</td>")
            parts.append(f'<td class="metric-val">{params_col}</td>')
            parts.append(f"<td{val_style}>{val_col}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")

    # --- Common Configuration ---
    if common_params:
        parts.append("<h2>Common Configuration</h2>")
        parts.append('<div class="config-grid">')
        for k, v in sorted(common_params.items()):
            parts.append(
                f'<div class="config-item">'
                f'<span class="config-key">{_h(k)}</span>'
                f'<span class="config-val">{_h(str(v))}</span></div>'
            )
        parts.append("</div>")

    # --- Experiment Details ---
    if runs:
        parts.append("<h2>Experiment Details</h2>")
        parts.append('<div class="toolbar">')
        parts.append(
            "<button onclick=\"document.querySelectorAll('details.run-card')"
            ".forEach(d=>d.open=true)\">Expand All</button>"
        )
        parts.append(
            "<button onclick=\"document.querySelectorAll('details.run-card')"
            ".forEach(d=>d.open=false)\">Collapse All</button>"
        )
        parts.append("</div>")

    for i, run in enumerate(runs, 1):
        raw_name = run.get("name", "Untitled")
        enriched_label = _enrich(run, "name")
        run_enr = run_enrichments.get(run.get("id", ""), {})
        key_metric = _pick_key_metric(run.get("results", {}), run_enr or None)
        duration = _fmt_duration(run.get("duration_minutes", 0))

        # Timestamp for summary line
        ts = run.get("completed_at") or run.get("started_at") or ""
        ts_short = ""
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                ts_short = dt.strftime("%b %d, %H:%M")
            except (ValueError, TypeError):
                ts_short = ts[:10]

        # Metric pill class
        status = run.get("status", "planned")
        pill_cls = ""
        if status == "failed":
            pill_cls = ' style="background:rgba(248,81,73,0.15);color:var(--red)"'
        elif not run.get("results"):
            pill_cls = ' class="run-metric-pill none"'

        enriched_span = ""
        if enriched_label and enriched_label != raw_name:
            enriched_span = f'<span class="run-enriched-name">&mdash; {_h(enriched_label)}</span>'

        pill_tag = f'<span class="run-metric-pill"{pill_cls}>{_h(key_metric)}</span>' if pill_cls else f'<span class="run-metric-pill">{_h(key_metric)}</span>'

        parts.append(f'<details class="run-card" id="run-{i}">')
        parts.append("<summary>")
        parts.append(f'<div class="run-summary-left">')
        parts.append(f'<span class="run-index">#{i}</span>')
        parts.append(f'<span class="run-name">{_h(raw_name)}</span>')
        parts.append(enriched_span)
        parts.append("</div>")
        parts.append(f'<div class="run-summary-right">')
        parts.append(pill_tag)
        if ts_short:
            parts.append(f'<span class="run-time">{_h(ts_short)}</span>')
        if duration != "-":
            parts.append(f'<span class="run-time">{_h(duration)}</span>')
        parts.append("</div>")
        parts.append("</summary>")
        parts.append('<div class="run-body">')

        # Narrative blocks (2x2 grid)
        hypothesis = run.get("hypothesis", "") or _enrich(run, "hypothesis")
        approach = _enrich(run, "approach")
        analysis = _enrich(run, "analysis")
        next_steps = _enrich(run, "next_steps")
        if any([hypothesis, approach, analysis, next_steps]):
            parts.append('<div class="narrative">')
            for cls, label, text in [
                ("hypothesis", "Hypothesis", hypothesis),
                ("approach", "Approach", approach),
                ("analysis", "Analysis", analysis),
                ("next-steps", "Next Steps", next_steps),
            ]:
                if text:
                    parts.append(f'<div class="narrative-block {cls}">')
                    parts.append(f'<div class="narrative-label">{label}</div>')
                    parts.append(f'<div class="narrative-text">{_h(text)}</div>')
                    parts.append("</div>")
            parts.append("</div>")

        # Tags
        tags = run.get("tags", [])
        if tags:
            for tag in tags:
                parts.append(f'<span class="tag">{_h(tag)}</span>')

        # Config chips (varying params)
        hyperparams = run.get("hyperparameters", {})
        if hyperparams:
            delta = {k: v for k, v in hyperparams.items()
                     if k in varying_keys or not common_params}
            if delta:
                label = "Configuration (changes)" if common_params else "Hyperparameters"
                parts.append(f'<div class="run-section-title">{label}</div>')
                parts.append('<div class="params-row">')
                for k, v in delta.items():
                    parts.append(
                        f'<span class="param-chip">'
                        f'<span class="pk">{_h(k)}=</span>'
                        f'<span class="pv">{_h(str(v))}</span></span>'
                    )
                parts.append("</div>")

        # Result chips (with sparklines showing metric history)
        results = run.get("results", {})
        if results:
            parts.append('<div class="run-section-title">Results</div>')
            parts.append('<div class="results-row">')
            for k, v in results.items():
                spark = ""
                history = metric_histories.get(k, [])
                if len(history) >= 2:
                    spark = _sparkline_svg(history, width=48, height=14)
                parts.append(
                    f'<span class="result-chip">'
                    f'<span class="rk">{_h(k)}=</span>'
                    f'<span class="rv">{_h(_fmt_metric(k, v))}</span>'
                    f'{spark}</span>'
                )
            parts.append("</div>")

        # Agent reasoning
        reasoning = run.get("agent_reasoning", "")
        if reasoning:
            parts.append('<div class="reasoning-block">')
            parts.append('<div class="reasoning-label">Agent Reasoning</div>')
            parts.append(f"<p>{_h(reasoning)}</p>")
            parts.append("</div>")

        # User notes
        notes = run.get("notes", [])
        if notes:
            parts.append('<div class="notes-block">')
            parts.append('<div class="notes-label">Notes</div>')
            for note in notes:
                parts.append(f"<p>{_h(note)}</p>")
            parts.append("</div>")

        # Diff with previous run
        if i > 1:
            prev = runs[i - 2]
            diff = diff_runs(prev, run)
            diff_html = _render_diff_html(diff, prev, run)
            if diff_html:
                parts.append(diff_html)

        parts.append("</div>")  # run-body
        parts.append("</details>")

    # --- Linked papers ---
    linked = project.get("linked_papers", [])
    if linked:
        parts.append("<h2>Linked Papers</h2>")
        parts.append('<ul class="lessons">')
        for citekey in linked:
            parts.append(f"<li><code>{_h(citekey)}</code></li>")
        parts.append("</ul>")

    # --- Footer ---
    parts.append('<div class="footer">')
    parts.append("Generated by <strong>Distillate</strong> &mdash; Lab Notebook")
    parts.append("</div>")
    parts.append("</body></html>")

    return "\n".join(parts)


def _render_diff_html(diff: dict, run_a: dict, run_b: dict) -> str:
    """Render a diff section between two runs as HTML."""
    param_diffs = [d for d in diff.get("param_diffs", []) if d.get("change") == "changed"]
    metric_diffs = [d for d in diff.get("metric_diffs", []) if d.get("change") == "changed"]

    if not param_diffs and not metric_diffs:
        return ""

    lines = ['<div class="diff-section">']
    prev_name = _h(run_a.get("name", "previous"))
    lines.append(f'<div class="diff-title">What Changed (vs {prev_name})</div>')

    for d in param_diffs:
        pct = d.get("pct_change")
        if pct is not None:
            sign = "+" if pct > 0 else ""
            lines.append(
                f'<div class="diff-item"><span class="diff-param">{_h(d["key"])}</span> '
                f'<span class="diff-arrow">{_h(str(d["old"]))} &rarr; '
                f'{_h(str(d["new"]))} ({sign}{pct:.0f}%)</span></div>'
            )
        else:
            lines.append(
                f'<div class="diff-item"><span class="diff-param">{_h(d["key"])}</span> '
                f'<span class="diff-arrow">{_h(str(d["old"]))} &rarr; '
                f'{_h(str(d["new"]))}</span></div>'
            )

    for d in metric_diffs:
        arrow = "improved" if d.get("improved") else "regressed"
        cls = "diff-improved" if d.get("improved") else "diff-regressed"
        delta = d.get("delta")
        if delta is not None:
            sign = "+" if delta > 0 else ""
            lines.append(
                f'<div class="diff-item"><span class="{cls}">'
                f'{_h(d["key"])}: {_h(_fmt_metric(d["key"], d["old"]))} &rarr; '
                f'{_h(_fmt_metric(d["key"], d["new"]))} '
                f'({sign}{delta:.4g}, {arrow})</span></div>'
            )
        else:
            lines.append(
                f'<div class="diff-item"><span class="{cls}">'
                f'{_h(d["key"])}: {_h(str(d["old"]))} &rarr; '
                f'{_h(str(d["new"]))} ({arrow})</span></div>'
            )

    lines.append("</div>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auto-detection: check tracked projects for new commits
# ---------------------------------------------------------------------------

def check_projects_for_updates(projects: dict) -> list[dict]:
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


def _get_logo_image():
    """Load the Distillate logo SVG as a matplotlib-compatible RGBA array."""
    import io
    from pathlib import Path

    import cairosvg
    import numpy as np
    from PIL import Image

    svg_path = Path(__file__).parent.parent / "docs" / "logo.svg"
    if not svg_path.exists():
        return None
    png_bytes = cairosvg.svg2png(
        url=str(svg_path), output_width=64, output_height=64,
    )
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return np.array(img) / 255.0


def generate_export_chart(runs: list[dict], metric: str, title: str = "",
                          log_scale: bool = False, subtitle: str = "") -> bytes:
    """Generate a clean chart PNG for sharing on social media.

    White background, thin left+bottom spines only, dense gridlines,
    dots colored by decision (green=keep, lighter gray=discard, orange=crash).
    Returns PNG bytes.
    """
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.offsetbox import AnnotationBbox, OffsetImage
    from matplotlib.ticker import FuncFormatter, LogLocator, MaxNLocator

    # Filter runs with numeric values for this metric
    points = []
    for i, run in enumerate(runs):
        val = run.get("results", {}).get(metric)
        if isinstance(val, (int, float)):
            points.append({"index": i, "value": val, "run": run})

    if len(points) < 1:
        raise ValueError(f"No data for metric '{metric}'")

    # Truncate subtitle to ~10 words max so it fits on one line with title
    if subtitle:
        words = subtitle.split()
        if len(words) > 10:
            subtitle = " ".join(words[:10]) + "\u2026"

    # ── Figure setup ──
    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # Prefer a clean system font
    for family in ("Inter", "Helvetica Neue", "Helvetica", "Arial", "sans-serif"):
        try:
            from matplotlib.font_manager import findfont, FontProperties
            if findfont(FontProperties(family=family), fallback_to_default=False):
                plt.rcParams["font.family"] = family
                break
        except Exception:
            continue

    # Spines: only left and bottom, thin
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.6)
    ax.spines["bottom"].set_linewidth(0.6)
    ax.spines["left"].set_color("#aaa")
    ax.spines["bottom"].set_color("#aaa")

    # Grid
    ax.yaxis.grid(True, alpha=0.12, linewidth=0.4, color="#999")
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)

    xs = list(range(len(points)))
    ys = [p["value"] for p in points]
    if log_scale:
        ax.set_yscale("log")

    lower_better = _is_lower_better(metric)

    # ── Best-so-far frontier line ──
    best_so_far = None
    frontier_xs, frontier_ys = [], []
    for i, p in enumerate(points):
        v = p["value"]
        decision = p["run"].get("decision", "")
        if decision == "keep":
            if best_so_far is None:
                best_so_far = v
                frontier_xs.append(0)
                frontier_ys.append(best_so_far)
            elif (lower_better and v < best_so_far) or (not lower_better and v > best_so_far):
                best_so_far = v
        if best_so_far is not None:
            frontier_xs.append(i)
            frontier_ys.append(best_so_far)
    if len(frontier_xs) > 1:
        ax.plot(frontier_xs, frontier_ys, color="#4F46E5", linewidth=2.5,
                alpha=0.9, zorder=2, solid_capstyle="round")

    # ── Scatter dots ──
    colors_map = {"keep": "#22c55e", "discard": "#d4d4d4", "crash": "#d29922"}
    colors = [colors_map.get(p["run"].get("decision", ""), "#d4d4d4") for p in points]
    ax.scatter(xs, ys, c=colors, s=36, zorder=3, edgecolors="white", linewidths=0.6)

    # ── Description labels on frontier improvements only ──
    prev_best = None
    frontier_pts = []
    for i, p in enumerate(points):
        if p["run"].get("decision") != "keep":
            continue
        v = p["value"]
        if prev_best is None:
            prev_best = v
            frontier_pts.append((i, p))
        elif (lower_better and v < prev_best) or (not lower_better and v > prev_best):
            prev_best = v
            frontier_pts.append((i, p))

    # Thin to max ~8 labels
    MAX_LABELS = 8
    if len(frontier_pts) > MAX_LABELS:
        step = (len(frontier_pts) - 1) / (MAX_LABELS - 1)
        indices = [round(j * step) for j in range(MAX_LABELS)]
        frontier_pts = [frontier_pts[j] for j in sorted(set(indices))]

    for i, p in frontier_pts:
        desc = p["run"].get("description", "") or p["run"].get("name", "")
        if not desc:
            continue
        if len(desc) > 18:
            desc = desc[:16] + "\u2026"
        ax.annotate(desc, (i, p["value"]),
                    textcoords="offset points", xytext=(4, 6),
                    fontsize=5.5, color="#999", ha="left", va="bottom",
                    rotation=30, zorder=4, annotation_clip=True)

    # ── Axes ──
    arrow = "\u2193" if lower_better else "\u2191"
    hint = "lower is better" if lower_better else "higher is better"
    scale_label = ", log" if log_scale else ""
    ax.set_xlabel("Run", fontsize=9.5, color="#888", labelpad=6)
    # DejaVu Sans has arrow glyphs; use it for the label
    from matplotlib.font_manager import FontProperties
    label_font = FontProperties(family="DejaVu Sans", size=9.5)
    ax.set_ylabel(f"{metric} {arrow} ({hint}{scale_label})", fontproperties=label_font,
                  color="#888", labelpad=6)
    ax.tick_params(colors="#888", labelsize=8.5, length=3, width=0.5)

    # Dense Y-axis ticks
    if log_scale:
        # 1-2-5 sequence within each decade (matches canvas chart)
        ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0), numticks=20))
    else:
        ax.yaxis.set_major_locator(MaxNLocator(nbins=10, steps=[1, 2, 2.5, 5, 10]))

    # Clean tick formatter
    cat = classify_metric(metric)
    def _tick_fmt(v, _pos):
        if cat == "ratio":
            return f"{v * 100:g}%" if 0 <= v <= 1 else f"{v:g}"
        if cat == "loss":
            return f"{v:.2e}" if abs(v) < 0.001 else f"{v:g}"
        if cat == "count":
            iv = int(v) if v == int(v) else v
            if isinstance(iv, int):
                return f"{iv / 1e6:g}M" if abs(iv) >= 1e6 else f"{iv:,}"
            return f"{v:g}"
        return f"{v:g}"
    ax.yaxis.set_major_formatter(FuncFormatter(_tick_fmt))

    if not log_scale and min(ys) >= 0:
        ax.set_ylim(bottom=0)

    # ── Title: "Title — subtitle" on one line, mixed weights ──
    if title:
        if subtitle:
            # Render bold title + lighter subtitle on one line
            # Use a dry render to measure title width, then place subtitle after
            renderer = fig.canvas.get_renderer()
            # Full combined string centered, but with two text objects
            combined = f"{title}  \u2014  {subtitle}"
            # Place as single text for centering, use color trick:
            # Can't mix weights in one text, so place title bold then subtitle
            t1 = fig.text(0.5, 0.965, title + "  ", ha="right", va="top",
                          fontsize=13, fontweight="bold", color="#222",
                          transform=fig.transFigure)
            t2 = fig.text(0.5, 0.965, "  " + subtitle, ha="left", va="top",
                          fontsize=13, fontweight="normal", color="#aaa",
                          transform=fig.transFigure)
            # Measure to re-center: shift both so the pair is centered
            fig.canvas.draw()
            bb1 = t1.get_window_extent(renderer)
            bb2 = t2.get_window_extent(renderer)
            total_w = bb1.width + bb2.width
            fig_w = fig.get_figwidth() * fig.dpi
            offset = (total_w / 2 - bb1.width) / fig_w
            t1.set_position((0.5 + offset, 0.965))
            t2.set_position((0.5 + offset, 0.965))
        else:
            fig.text(0.5, 0.965, title, ha="center", va="top",
                     fontsize=13, fontweight="bold", color="#222")

    plt.tight_layout(rect=[0.01, 0.04, 0.99, 0.92])

    # ── Branding: logo icon + "Distillate" in brand indigo ──
    try:
        logo_arr = _get_logo_image()
        if logo_arr is not None:
            # Place logo as a small icon in bottom-right
            logo_ax = fig.add_axes([0.88, 0.005, 0.025, 0.045],
                                   anchor="SE", zorder=10)
            logo_ax.imshow(logo_arr)
            logo_ax.axis("off")
            # "Distillate" text right of logo
            fig.text(0.912, 0.025, "Distillate", ha="left", va="center",
                     fontsize=7.5, color="#6366f1", fontweight="600")
        else:
            raise FileNotFoundError
    except Exception:
        fig.text(0.97, 0.025, "Distillate", ha="right", va="center",
                 fontsize=7.5, color="#6366f1", fontweight="600")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
