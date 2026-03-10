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

# Metric patterns in training stdout
_METRIC_RE = re.compile(
    r"(?:^|[|\s,])\s*"
    r"(accuracy|loss|exact_match|val_loss|val_accuracy|test_accuracy|"
    r"train_loss|train_accuracy|val_exact_match|f1|precision|recall|"
    r"perplexity|bleu|rouge|auc|best_val_acc|final_loss|val_bpb|"
    r"train_bpb|bpb|mse|rmse|mae)"
    r"\s*[=:]\s*([\d.]+)%?",
    re.IGNORECASE | re.MULTILINE,
)

# Config JSON block in stdout
_CONFIG_BLOCK_RE = re.compile(r"Config:\s*(\{[^}]+\})", re.DOTALL)

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
    """Extract hyperparameters from command-line key=value pairs."""
    hp = {}
    for key, val in _CMD_KV_RE.findall(command):
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


def main() -> None:
    """Entry point: reads PostToolUse event from stdin."""
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
            metrics = _extract_metrics(tool_result)

            _append_event(project_root, {
                "type": "run_completed",
                "ts": ts,
                "command": command,
                "hyperparameters": hp,
                "results": metrics,
                "session_id": session_id,
            })
            return

        # Detect result file writes
        if _RESULT_FILE_RE.search(command):
            _append_event(project_root, {
                "type": "result_file_written",
                "ts": ts,
                "command": command,
                "session_id": session_id,
            })

    except Exception:
        pass  # Never block the agent


if __name__ == "__main__":
    main()
