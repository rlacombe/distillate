"""ML experiment tracking for Distillate.

Discovers ML projects, reconstructs experiment history from artifacts
(training logs, configs, checkpoints, results), and generates rich
markdown lab notebooks.  Works with or without git.
"""

import hashlib
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
    "__pycache__", ".git", ".distillate", "node_modules",
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

    return {
        "name": project_name,
        "path": str(path),
        "runs": runs,
        "has_git": has_git,
        "head_hash": _git_head_hash(path) if has_git else "",
        "total_commits": len(commits),
        "artifact_files": len(classified),
    }


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
        except Exception:
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

                        run_id = f"claude-{uuid.uuid4().hex[:6]}"
                        runs.append({
                            "id": run_id,
                            "name": name,
                            "status": "completed",
                            "hypothesis": "",
                            "hyperparameters": hp,
                            "results": metrics,
                            "tags": [name] if name else [],
                            "git_commits": [],
                            "files_created": [],
                            "started_at": pending["timestamp"],
                            "completed_at": timestamp,
                            "duration_minutes": 0,
                            "notes": [],
                            "source": "claude_logs",
                            "session_file": jsonl_path.name,
                            "command": pending["command"],
                        })

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
    return hashlib.sha256("|".join(items).encode()).hexdigest()[:16]


def _load_enrichment_cache(project_path: Path) -> dict:
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
            desc += f"  Command: {command[:200]}\n"

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

Also generate project-level insights:
6. key_breakthrough: Which experiment was the biggest improvement and why (1-2 sentences). Reference specific metric values.
7. lessons_learned: 3-5 bullets of deeper insights connecting the experiments. Reference concrete numbers, not vague claims.

Output ONLY valid JSON in this exact format (no markdown, no code blocks):
{{"runs": {{{run_ids_json[1:-1].replace('"', '')}: see below}}, "project": {{"key_breakthrough": "...", "lessons_learned": ["..."]}}}}

The "runs" object must have keys matching these exact run IDs: {run_ids_json}
Each run value: {{"name": "...", "hypothesis": "...", "approach": "...", "analysis": "...", "next_steps": "..."}}"""


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
    cache = _load_enrichment_cache(project_path)
    if cache.get("fingerprint") == fingerprint and cache.get("enrichment"):
        log.info("Using cached LLM enrichment for %s", project_name)
        return cache["enrichment"]

    # Build prompt and call Sonnet
    prompt = _build_enrichment_prompt(runs, project_name)

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
        text = response.content[0].text.strip()
        log.info("LLM enrichment response: %d chars", len(text))
    except Exception:
        log.exception("Failed to call Claude for LLM enrichment")
        return None

    # Parse JSON response (handle markdown code blocks)
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
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
    run_enrichments = (enrichment or {}).get("runs", {})
    project_insights = (enrichment or {}).get("project", {})

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
        parts.append("| # | Experiment | Status | Duration | Result |")
        parts.append("|---|-----------|--------|----------|--------|")

        for i, run in enumerate(runs, 1):
            status_icon = _status_icon(run.get("status", "planned"))
            duration = _fmt_duration(run.get("duration_minutes", 0))
            key_metric = _pick_key_metric(run.get("results", {}))
            display_name = _enrich(run, "name") or run.get("name", "?")
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

        # Narrative sections from enrichment
        hypothesis = _enrich(run, "hypothesis") or run.get("hypothesis", "")
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
    """Pick the most important metric from results for the timeline table.

    Returns a labeled string like 'loss=0.0012' or 'accuracy=98.50%'.
    """
    if not results:
        return "-"
    # Priority order for key metrics
    priority = ["exact_match", "val_exact_match", "accuracy", "test_accuracy",
                 "val_accuracy", "best_val_acc", "f1", "bleu", "rouge",
                 "loss", "final_loss", "val_loss"]
    for key in priority:
        if key in results and isinstance(results[key], (int, float)):
            return f"{key}={_fmt_metric(key, results[key])}"
    # Fallback: first numeric metric (skip metadata-like fields)
    skip = {"n_params", "duration_minutes", "num_examples", "n_samples",
            "num_classes", "vocab_size", "num_papers"}
    for k, v in results.items():
        if isinstance(v, (int, float)) and k not in skip:
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
