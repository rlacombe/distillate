"""Parsing for experiment artifacts: runs.jsonl, events.jsonl, Claude logs.

Split out of experiments.py to keep concerns separate. Public names are
re-exported from distillate.experiments for backwards compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from distillate.experiments import (
    _SKIP_DIRS,
    _create_run,
    _has_numeric_results,
    _hyperparam_fingerprint,
    _tag_from_config,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# runs.jsonl + events.jsonl ingestion
# ---------------------------------------------------------------------------

# Valid status values in runs.jsonl
_STRUCTURED_STATUSES = {"best", "completed", "keep", "discard", "crash", "running"}

# Regex to fix invalid JSON escapes written by LLM agents (e.g. \! \' \`)
_INVALID_JSON_ESCAPE_RE = re.compile(r'\\([^"\\/bfnrtu])')


def _repair_json_line(line: str) -> str:
    """Fix common invalid JSON escape sequences from LLM output."""
    return _INVALID_JSON_ESCAPE_RE.sub(r'\1', line)


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
                    try:
                        entry = json.loads(_repair_json_line(line))
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
                # Map old keep/discard → completed; new best/completed pass through
                if status in ("keep", "discard"):
                    decision = "completed"
                elif status in _STRUCTURED_STATUSES:
                    decision = status
                else:
                    decision = None
                internal_status = "completed"
                if status == "crash":
                    internal_status = "failed"
                elif status == "running":
                    internal_status = "running"

                ts = entry.get("timestamp", "")
                started = entry.get("started_at", "") or ts
                completed = entry.get("completed_at", "") or ts
                duration_secs = entry.get("duration_seconds", 0)
                duration_mins = int(duration_secs / 60) if duration_secs else 0

                run = _create_run(
                    prefix="sr",
                    name=run_id,
                    hyperparameters=entry.get("hyperparameters", {}),
                    results=entry.get("results", {}),
                    started_at=started,
                    completed_at=completed,
                    duration_minutes=duration_mins,
                    duration_seconds=duration_secs,
                    source="structured",
                    decision=decision,
                    description=entry.get("description", ""),
                    agent_reasoning=entry.get("reasoning", ""),
                    hypothesis=entry.get("hypothesis", ""),
                    changes=entry.get("changes", ""),
                    commit=entry.get("commit", ""),
                    baseline_comparison=entry.get("baseline_comparison"),
                    run_number=entry.get("run_number"),
                    # Pre-registration fields
                    prediction=entry.get("prediction", ""),
                    outcome=entry.get("outcome", ""),
                    predicted_metric=entry.get("predicted_metric", ""),
                    predicted_value=entry.get("predicted_value"),
                    predicted_direction=entry.get("predicted_direction", ""),
                    confidence=entry.get("confidence"),
                    rationale=entry.get("rationale", ""),
                    verdict=entry.get("verdict", ""),
                    prediction_error=entry.get("prediction_error"),
                    prediction_error_pct=entry.get("prediction_error_pct"),
                    belief_update=entry.get("belief_update", ""),
                )
                run["status"] = internal_status
                runs.append(run)
    except OSError:
        pass

    # Deduplicate by run name: prefer terminal status, then latest timestamp
    seen: dict[str, int] = {}
    _terminal = {"best", "completed", "keep", "discard", "crash"}
    for i, run in enumerate(runs):
        name = run["name"]
        if name not in seen:
            seen[name] = i
        else:
            prev = runs[seen[name]]
            prev_terminal = prev.get("decision") in _terminal or prev.get("status") in ("completed", "failed")
            curr_terminal = run.get("decision") in _terminal or run.get("status") in ("completed", "failed")
            prev_ts = prev.get("started_at", "")
            curr_ts = run.get("started_at", "")
            # Prefer terminal over running; among same finality, prefer later timestamp
            if (curr_terminal and not prev_terminal) or \
               (curr_terminal == prev_terminal and curr_ts > prev_ts):
                seen[name] = i
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
                command = event.get("command", "")

                ts = event.get("ts", "")
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

    # Index structured runs by fingerprint and timestamp for correlation
    structured_fps: dict[str, dict] = {}
    structured_timestamps: set[str] = set()
    for run in structured:
        hp = run.get("hyperparameters", {})
        if hp:
            structured_fps[_hyperparam_fingerprint(hp)] = run
        ts = run.get("started_at", "")
        if ts:
            structured_timestamps.add(ts[:16])  # match to minute

    # Correlate hook runs with structured reports
    result = list(structured)  # structured runs are primary
    for hook_run in hook_runs:
        # Skip hook runs already covered by a backfilled structured entry
        # (backfill writes events into runs.jsonl with matching timestamps)
        hook_ts = hook_run.get("started_at", "")
        if hook_ts and hook_ts[:16] in structured_timestamps:
            continue

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


def backfill_runs_from_events(project_path: Path) -> int:
    """Auto-create runs.jsonl entries for training events that weren't logged.

    Reads events.jsonl for ``run_completed`` events that have metrics,
    checks runs.jsonl for existing entries covering those events
    (by timestamp proximity), and appends new entries for unmatched events.

    Returns count of new entries added.
    """
    import hashlib

    project_path = Path(project_path).resolve()
    distillate_dir = project_path / ".distillate"
    events_file = distillate_dir / "events.jsonl"
    runs_file = distillate_dir / "runs.jsonl"

    if not events_file.exists():
        return 0

    # Parse existing completed runs from runs.jsonl
    existing_timestamps: set[str] = set()
    existing_run_ids: set[str] = set()
    next_run_num = 1
    if runs_file.exists():
        for line in runs_file.read_text(encoding="utf-8").strip().splitlines():
            try:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    entry = json.loads(_repair_json_line(line))
                if entry.get("status") in ("best", "completed", "keep", "discard", "crash"):
                    ts = entry.get("timestamp", "")
                    if ts:
                        existing_timestamps.add(ts[:16])  # match to minute
                rid = entry.get("id", "")
                if rid:
                    existing_run_ids.add(rid)
                    # Track highest run number
                    m = re.match(r"run_(\d+)", rid)
                    if m:
                        next_run_num = max(next_run_num, int(m.group(1)) + 1)
            except (json.JSONDecodeError, ValueError):
                continue

    # Parse training events
    new_entries: list[dict] = []
    for line in events_file.read_text(encoding="utf-8").strip().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "run_completed":
            continue
        metrics = event.get("results", {})
        if not metrics:
            continue
        command = event.get("command", "")

        ts = event.get("ts", "")
        # Skip if we already have a runs.jsonl entry near this timestamp
        if ts and ts[:16] in existing_timestamps:
            continue

        # Extract script name from command
        script_name = ""
        m = re.search(r"python[3]?\s+(\S+\.py)", command)
        if m:
            script_name = Path(m.group(1)).stem

        seed = f"{ts}-{next_run_num}"
        slug = hashlib.sha256(seed.encode()).hexdigest()[:6]
        run_id = f"xp-{slug}"
        next_run_num += 1

        entry = {
            "$schema": "distillate/run/v1",
            "id": run_id,
            "timestamp": ts,
            "status": "completed",
            "description": f"Backfilled from {script_name}" if script_name else "Backfilled from training event",
            "hyperparameters": event.get("hyperparameters", {}),
            "results": metrics,
            "reasoning": f"Auto-logged by rescan. Command: {command[:100]}" if command else "Auto-logged by rescan from events.jsonl",
        }
        new_entries.append(entry)
        if ts:
            existing_timestamps.add(ts[:16])

    # Close stale "running" entries — runs that were announced but never
    # concluded (e.g. session died, timeout exit code 144).  A running entry
    # is stale if it has no terminal follow-up and is older than 2 hours.
    _STALE_THRESHOLD_SECS = 2 * 3600
    if runs_file.exists():
        running_entries: dict[str, dict] = {}  # run_id -> latest running entry
        terminal_ids: set[str] = set()
        for line in runs_file.read_text(encoding="utf-8").strip().splitlines():
            try:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    entry = json.loads(_repair_json_line(line))
            except (json.JSONDecodeError, ValueError):
                continue
            rid = entry.get("id", "")
            status = entry.get("status", "")
            if status in ("best", "completed", "keep", "discard", "crash"):
                terminal_ids.add(rid)
            elif status == "running" and rid:
                running_entries[rid] = entry

        now = datetime.now(timezone.utc)
        for rid, entry in running_entries.items():
            if rid in terminal_ids:
                continue
            ts = entry.get("timestamp", "") or entry.get("started_at", "")
            if not ts:
                continue
            try:
                started = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                age_secs = (now - started).total_seconds()
            except (ValueError, TypeError):
                continue
            if age_secs < _STALE_THRESHOLD_SECS:
                continue
            crash_entry = {
                "$schema": "distillate/run/v1",
                "id": rid,
                "timestamp": now.isoformat(),
                "status": "crash",
                "description": entry.get("description", ""),
                "reasoning": f"Auto-closed: running entry from {ts} had no completion after {int(age_secs / 3600)}h",
            }
            new_entries.append(crash_entry)
            log.info("Auto-closing stale running entry %s (age: %dh)", rid, int(age_secs / 3600))

    if not new_entries:
        return 0

    # Append to runs.jsonl
    distillate_dir.mkdir(exist_ok=True)
    with open(runs_file, "a", encoding="utf-8") as f:
        for entry in new_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    log.info("Backfilled %d run(s) from events.jsonl into runs.jsonl", len(new_entries))
    return len(new_entries)


def watch_experiment_artifacts(project_path: Path) -> list[dict]:
    """Check for new data since last scan.

    Polls ``.distillate/scan_state.json`` manifest and watches
    ``runs.jsonl`` / ``events.jsonl`` for new lines.

    Returns list of new events detected.
    """
    from distillate.experiments import _has_changed_files

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
# --key value pairs (argparse style, e.g. --d_model 64 --lr 1e-3)
_ARGPARSE_RE = re.compile(
    r"--(\w+)\s+([\d.eE+-]+|[Tt]rue|[Ff]alse)(?=\s|$)"
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
    r"perplexity|bleu|rouge|auc|best_val_acc|final_loss|"
    r"test_acc|train_acc|val_acc|param_count|n_params|total_params|"
    r"params|mse|rmse|mae|val_bpb|train_bpb|bpb)"
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

    # Extract key=value and --key value pairs
    hyperparams: dict[str, Any] = {}
    for key, val in _CMD_KV_RE.findall(command):
        key_lower = key.lower()
        if key_lower in _KNOWN_HYPERPARAMS or key in _KNOWN_HYPERPARAMS:
            hyperparams[key] = _coerce_value(val)
        elif re.match(r"^[\d.eE+-]+$", val):
            hyperparams[key] = _coerce_value(val)
    for key, val in _ARGPARSE_RE.findall(command):
        key_lower = key.lower()
        if key_lower in _KNOWN_HYPERPARAMS or key in _KNOWN_HYPERPARAMS:
            hyperparams[key] = _coerce_value(val)
        elif re.match(r"^[\d.eE+-]+$", val):
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


def extract_runs_from_claude_logs(project_path: Path) -> list[dict]:
    """Extract experiment runs from Claude Code conversation logs.

    Parses ~/.claude/projects/<encoded-path>/*.jsonl files for bash
    training commands and their stdout output.  Returns a list of
    run dicts compatible with scan_experiment().
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
