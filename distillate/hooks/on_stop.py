"""Stop hook for capturing session end events.

Receives Claude Code Stop event JSON on stdin.  Appends a session_end
event to ``.distillate/events.jsonl``.

Must exit 0 immediately — never block the agent.

Usage in ``.claude/settings.json``::

    {
      "hooks": {
        "Stop": [
          {
            "command": "python3 -m distillate.hooks.on_stop"
          }
        ]
      }
    }
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


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
    """Entry point: reads Stop event from stdin."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        event = json.loads(raw)

        session_id = event.get("session_id", "")
        stop_reason = event.get("stop_reason", "user")
        ts = datetime.now(timezone.utc).isoformat()
        project_root = _find_project_root()

        _append_event(project_root, {
            "type": "session_end",
            "ts": ts,
            "session_id": session_id,
            "stop_reason": stop_reason,
            "project_path": str(project_root),
        })

    except Exception:
        pass  # Never block the agent


if __name__ == "__main__":
    main()
