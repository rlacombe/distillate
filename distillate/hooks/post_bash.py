"""PostToolUse hook for capturing training runs from Bash commands.

Receives Claude Code PostToolUse event JSON on stdin.  Detects training
commands, extracts metrics and hyperparameters from stdout, and appends
structured events to ``.distillate/events.jsonl``.

Must exit 0 immediately — never block the agent.

Usage in ``.claude/settings.json``::

    {
      "hooks": {
        "PostToolUse": [
          {
            "matcher": "Bash",
            "command": "python3 -m distillate.hooks.post_bash"
          }
        ]
      }
    }
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ML-related keywords in commands (broader than _TRAIN_FILENAME_RE)
_ML_COMMAND_RE = re.compile(
    r"(?:train|epoch|loss|lr|batch|finetune|sweep|run_exp)",
    re.IGNORECASE,
)

# Python script invocation
_PYTHON_SCRIPT_RE = re.compile(r"python[3]?\s+(\S+\.py)", re.IGNORECASE)

# Key=value pairs on the command line
_CMD_KV_RE = re.compile(
    r"(?<![/\w])(\w+)\s*=\s*([\d.eE+-]+|[Tt]rue|[Ff]alse)"
)

# --key value pairs (argparse style)
_ARGPARSE_RE = re.compile(
    r"--(\w+)\s+([\d.eE+-]+|[Tt]rue|[Ff]alse)(?=\s|$)"
)

# Metric patterns in training stdout
_METRIC_RE = re.compile(
    r"(?:^|[|\s,])\s*"
    r"(accuracy|loss|exact_match|val_loss|val_accuracy|test_accuracy|"
    r"train_loss|train_accuracy|val_exact_match|f1|precision|recall|"
    r"perplexity|bleu|rouge|auc|best_val_acc|final_loss|val_bpb|"
    r"train_bpb|bpb|mse|rmse|mae|test_acc|train_acc|val_acc|"
    r"param_count|n_params|total_params|params)"
    r"\s*[=:]\s*([\d.]+)%?",
    re.IGNORECASE | re.MULTILINE,
)

# Config JSON block in stdout
_CONFIG_BLOCK_RE = re.compile(r"Config:\s*(\{[^}]+\})", re.DOTALL)

# Epoch/step markers in training output
_EPOCH_RE = re.compile(r"[Ee]poch\s+(\d+)", re.IGNORECASE)
_STEP_RE = re.compile(r"[Ss]tep\s+(\d+)", re.IGNORECASE)

# Result file write detection
_RESULT_FILE_RE = re.compile(
    r"(?:results?|test_results?|metrics).*\.json",
    re.IGNORECASE,
)


def _is_training_command(command: str) -> bool:
    """Check if a command looks like a training run."""
    m = _PYTHON_SCRIPT_RE.search(command)
    if not m:
        return False
    filename = m.group(1).rsplit("/", 1)[-1]
    if re.search(r"(?:train|run_exp|finetune|sweep)", filename, re.IGNORECASE):
        return True
    # Check for ML keywords in the command args (after the python invocation)
    return bool(_ML_COMMAND_RE.search(command))


def _extract_hyperparams(command: str) -> dict:
    """Extract hyperparameters from command-line key=value and --key value pairs."""
    hp = {}
    for key, val in _CMD_KV_RE.findall(command):
        hp[key] = _coerce(val)
    for key, val in _ARGPARSE_RE.findall(command):
        hp[key] = _coerce(val)
    return hp


def _extract_metrics(text: str) -> dict:
    """Extract metric values from stdout text."""
    metrics = {}
    for match in _METRIC_RE.finditer(text):
        try:
            metrics[match.group(1).lower()] = float(match.group(2))
        except ValueError:
            pass
    return metrics


def _extract_config_block(text: str) -> dict:
    """Extract hyperparameters from 'Config: {...}' in stdout."""
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


def _coerce(val: str):
    """Coerce string to int, float, or bool."""
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    try:
        f = float(val)
        return int(f) if f == int(f) and "." not in val and "e" not in val.lower() else f
    except ValueError:
        return val


def _find_project_root() -> Path:
    """Walk up from CWD to find .distillate/ or .git/."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".distillate").is_dir() or (parent / ".git").is_dir():
            return parent
    return cwd


def _append_event(project_root: Path, event: dict) -> None:
    """Append a JSON event to .distillate/events.jsonl."""
    distillate_dir = project_root / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    events_file = distillate_dir / "events.jsonl"
    with open(events_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _emit_epoch_metrics(
    project_root: Path,
    tool_result: str,
    ts: str,
    session_id: str,
) -> None:
    """Emit per-epoch metric_update events from training output.

    Scans each line of stdout for epoch/step markers and metrics.
    Emits one event per line that has both a position marker and metrics.
    """
    for line in tool_result.splitlines():
        # Look for epoch or step marker on this line
        epoch_m = _EPOCH_RE.search(line)
        step_m = _STEP_RE.search(line)
        if not epoch_m and not step_m:
            continue

        # Extract metrics from this specific line
        line_metrics = {}
        for match in _METRIC_RE.finditer(line):
            try:
                line_metrics[match.group(1).lower()] = float(match.group(2))
            except ValueError:
                pass

        if not line_metrics:
            continue

        evt: dict = {
            "type": "metric_update",
            "ts": ts,
            "metrics": line_metrics,
            "session_id": session_id,
        }
        if epoch_m:
            evt["epoch"] = int(epoch_m.group(1))
        if step_m:
            evt["step"] = int(step_m.group(1))

        _append_event(project_root, evt)


def _check_dirty_git(project_root: Path) -> None:
    """Warn if git has uncommitted changes when starting a new training run.

    This catches the case where the agent ran an experiment but forgot to
    commit before starting the next one.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root, capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(
                "\n*** WARNING: You have uncommitted changes. The protocol "
                "requires committing after EACH experiment run before "
                "starting the next one. Run: git add -A && git commit -m "
                "'<description>: <metric>=<value> [keep|discard]' && "
                "git push ***"
            )
    except Exception:
        pass


def _check_running_entry(project_root: Path) -> None:
    """Warn if no 'running' entry in runs.jsonl for the current training run.

    The protocol requires announcing a run before starting training.
    """
    runs_file = project_root / ".distillate" / "runs.jsonl"
    if not runs_file.exists():
        print(
            "\n*** WARNING: No .distillate/runs.jsonl found. You MUST "
            "announce each run by appending a 'running' entry BEFORE "
            "starting training. See .distillate/REPORTING.md ***"
        )
        return
    try:
        lines = runs_file.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            print(
                "\n*** WARNING: runs.jsonl is empty. Append a 'running' "
                "entry before each training run. ***"
            )
            return
        last = json.loads(lines[-1])
        if last.get("status") != "running":
            print(
                "\n*** WARNING: Last entry in runs.jsonl has status "
                f"'{last.get('status')}', not 'running'. You MUST "
                "announce each new run by appending a 'running' entry "
                "BEFORE starting training. ***"
            )
    except Exception:
        pass


_RUN_TIME_WARN_MINUTES = 10  # Warn after this many minutes on a single run


def _check_run_elapsed(project_root: Path) -> None:
    """Print a warning if the current run has been going too long.

    Reads the last entry in runs.jsonl. If it has status="running" and
    its timestamp is older than _RUN_TIME_WARN_MINUTES, print a nudge.
    """
    runs_file = project_root / ".distillate" / "runs.jsonl"
    if not runs_file.exists():
        return
    try:
        # Read last non-empty line
        lines = runs_file.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return
        last = json.loads(lines[-1])
        if last.get("status") != "running":
            return
        ts_str = last.get("timestamp", "")
        if not ts_str:
            return
        # Parse ISO timestamp
        started = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        elapsed_min = elapsed / 60
        if elapsed_min > 30:
            print(
                f"\n*** CRITICAL: Run {last.get('id', '?')} has been going "
                f"for {int(elapsed_min)} min. STOP IMMEDIATELY. Log whatever "
                f"results you have (even partial), commit, and start the next "
                f"run. ***"
            )
        elif elapsed_min > _RUN_TIME_WARN_MINUTES:
            print(
                f"\n*** TIME WARNING: You have been on run {last.get('id', '?')} "
                f"for {int(elapsed_min)} minutes. Wrap up this run NOW — "
                f"log results (even partial), commit, and move on to the next "
                f"experiment. ***"
            )
    except Exception:
        pass


def main() -> None:
    """Entry point: reads PostToolUse event from stdin."""
    if not os.environ.get("DISTILLATE_SESSION"):
        return
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        event = json.loads(raw)

        tool_name = event.get("tool_name", "")
        if tool_name != "Bash":
            return

        tool_input = event.get("tool_input", {})
        command = tool_input.get("command", "")
        if not command:
            return

        tool_result = event.get("tool_result", "")
        if not isinstance(tool_result, str):
            tool_result = str(tool_result)

        session_id = event.get("session_id", "")
        ts = datetime.now(timezone.utc).isoformat()
        project_root = _find_project_root()

        # Detect training commands
        if _is_training_command(command):
            config_hp = _extract_config_block(tool_result)
            cmd_hp = _extract_hyperparams(command)
            hp = {**config_hp, **cmd_hp}

            # Emit per-epoch metric_update events
            _emit_epoch_metrics(
                project_root, tool_result, ts, session_id,
            )

            metrics = _extract_metrics(tool_result)

            _append_event(project_root, {
                "type": "run_completed",
                "ts": ts,
                "command": command,
                "hyperparameters": hp,
                "results": metrics,
                "session_id": session_id,
            })

            # Protocol enforcement: warn about missing announcement and dirty git
            _check_running_entry(project_root)
            _check_dirty_git(project_root)

            # Remind agent to log results
            if metrics:
                metric_str = ", ".join(f"{k}={v}" for k, v in metrics.items())
                print(
                    f"\n*** Training completed. Detected metrics: {metric_str}. "
                    f"Now: 1) append completed entry to runs.jsonl, "
                    f"2) git add -A && git commit && git push ***"
                )
            return

        # Detect result file writes
        if _RESULT_FILE_RE.search(command):
            _append_event(project_root, {
                "type": "result_file_written",
                "ts": ts,
                "command": command,
                "session_id": session_id,
            })

        # Check if PROMPT.md was updated externally (via desktop editor)
        flag = project_root / ".distillate" / "prompt_updated"
        if flag.exists():
            try:
                flag.unlink()
                print(
                    "\n*** PROMPT.md has been updated by the user. "
                    "Re-read PROMPT.md now and adjust your approach accordingly. ***"
                )
            except OSError:
                pass

        # Check for steering instructions from the desktop app
        steering = project_root / ".distillate" / "steering.md"
        if steering.exists():
            try:
                text = steering.read_text(encoding="utf-8").strip()
                steering.unlink()
                if text:
                    print(f"\n*** USER INSTRUCTION ***\n{text}\n*** END INSTRUCTION ***")
            except OSError:
                pass

        # Time-elapsed warning: nudge agent if current run is taking too long
        _check_run_elapsed(project_root)

    except Exception:
        pass  # Never block the agent


if __name__ == "__main__":
    main()
