"""Stop hook for capturing session end events.

Receives Claude Code Stop event JSON on stdin.  Appends a session_end
event to ``.distillate/events.jsonl`` AND auto-concludes any ``running``
runs that overran their wrap deadline (so the UI doesn't show stuck
runs forever and the next session opens to a clean slate).

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
import os
import sys
from datetime import datetime, timedelta, timezone
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


def _wrap_deadline_for(entry: dict, project_root: Path) -> datetime | None:
    """Return the wrap deadline for a run entry, or None if undeterminable.

    Prefers the entry's own ``wrap_deadline_at`` (written by start_run since
    L3). Falls back to ``started_at + train_budget + wrap_budget`` from
    ``.distillate/budget.json`` for legacy entries that pre-date L3.
    """
    wrap_str = entry.get("wrap_deadline_at")
    if wrap_str:
        try:
            return datetime.fromisoformat(wrap_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    started_str = entry.get("started_at") or entry.get("timestamp")
    if not started_str:
        return None
    try:
        started = datetime.fromisoformat(started_str.replace("Z", "+00:00"))
    except ValueError:
        return None

    budget_path = project_root / ".distillate" / "budget.json"
    train = 300
    wrap = 60
    if budget_path.exists():
        try:
            data = json.loads(budget_path.read_text(encoding="utf-8"))
            train = int(data.get("train_budget_seconds")
                        or data.get("run_budget_seconds") or train)
            wrap = int(data.get("wrap_budget_seconds") or max(60, int(train * 0.1)))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return started + timedelta(seconds=train + wrap)


def _autoconclude_overdue(project_root: Path, session_id: str) -> int:
    """Append synthetic ``timeout`` completions for any overdue running runs.

    A run is overdue when the wall clock has passed its wrap deadline AND
    no later entry for the same id has already concluded it. Returns the
    count of runs auto-concluded (for callers that want to log it).
    """
    runs_path = project_root / ".distillate" / "runs.jsonl"
    if not runs_path.exists():
        return 0

    try:
        lines = runs_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0

    # First pass: find latest status per run_id
    latest_status: dict[str, str] = {}
    running_entries: dict[str, dict] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = rec.get("id")
        if not rid:
            continue
        latest_status[rid] = rec.get("status", "")
        if rec.get("status") == "running":
            running_entries[rid] = rec
        else:
            # A non-running entry supersedes a prior running one
            running_entries.pop(rid, None)

    if not running_entries:
        return 0

    now = datetime.now(timezone.utc)
    concluded = 0
    with open(runs_path, "a", encoding="utf-8") as f:
        for rid, entry in running_entries.items():
            # Sanity: skip if the latest status for this id isn't "running"
            if latest_status.get(rid) != "running":
                continue
            wrap_deadline = _wrap_deadline_for(entry, project_root)
            if wrap_deadline is None or now <= wrap_deadline:
                continue
            overdue_secs = int((now - wrap_deadline).total_seconds())
            synthetic = {
                "id": rid,
                "timestamp": now.isoformat(),
                "status": "timeout",
                "auto_concluded": True,
                "auto_concluded_reason": (
                    f"session ended past wrap deadline ({overdue_secs}s overdue)"
                ),
                "results": {},
                "session_id": session_id,
            }
            f.write(json.dumps(synthetic, ensure_ascii=False) + "\n")
            concluded += 1
    return concluded


def _locate_transcript(session_id: str, cwd: str = "") -> Path | None:
    """Return the path to a session JSONL given its session_id.

    Tries a direct derivation first (cwd → encoded project dir), then
    falls back to a glob across all ~/.claude/projects/ sub-directories.
    Claude Code encodes the project CWD as an absolute path with every
    '/' replaced by '-' (leading slash becomes leading '-').
    """
    if not session_id:
        return None
    projects_dir = Path.home() / ".claude" / "projects"
    if cwd:
        encoded = cwd.replace("/", "-")  # /Users/foo/bar → -Users-foo-bar
        candidate = projects_dir / encoded / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    # Fallback: search all project dirs (cheap — O(num_projects))
    if projects_dir.exists():
        for match in projects_dir.glob(f"*/{session_id}.jsonl"):
            return match
    return None


def _record_session_tokens(session_id: str, transcript: Path) -> None:
    """Parse transcript JSONL and append per-session token totals to the usage tracker.

    Each distinct Anthropic API call (identified by message.id) appears once in
    the accounting even though Claude Code logs it once per content block.
    """
    seen: set[str] = set()
    by_model: dict[str, dict] = {}

    for line in transcript.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") != "assistant":
            continue
        msg = row.get("message") or {}
        msg_id = msg.get("id", "")
        if not msg_id or msg_id in seen:
            continue
        seen.add(msg_id)
        usage = msg.get("usage") or {}
        if not usage:
            continue
        model = msg.get("model", "")
        bucket = by_model.setdefault(model, {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        })
        bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
        bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
        bucket["cache_read_input_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
        bucket["cache_creation_input_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)

    if not by_model:
        return

    try:
        from distillate import pricing
        from distillate.agent_runtime import usage_tracker
        tracker = usage_tracker.get_tracker()

        # Build cumulative sum of all prior deltas for this session so we can
        # subtract the running total and only append the incremental difference.
        _zero = lambda: {"input_tokens": 0, "output_tokens": 0,
                         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        prev: dict[str, dict] = {}
        for row in tracker._iter_rows():
            if row.get("session_id") == session_id and row.get("role") == "experimentalist_run":
                mk = row.get("model", "")
                if mk not in prev:
                    prev[mk] = _zero()
                for k in ("input_tokens", "output_tokens",
                          "cache_read_input_tokens", "cache_creation_input_tokens"):
                    prev[mk][k] += int((row.get("tokens") or {}).get(k) or 0)

        for model, tokens in by_model.items():
            if not any(tokens.values()):
                continue
            p = prev.get(model, {})
            delta = {
                "input_tokens":               max(0, tokens["input_tokens"] - int(p.get("input_tokens") or 0)),
                "output_tokens":              max(0, tokens["output_tokens"] - int(p.get("output_tokens") or 0)),
                "cache_read_input_tokens":    max(0, tokens["cache_read_input_tokens"] - int(p.get("cache_read_input_tokens") or 0)),
                "cache_creation_input_tokens": max(0, tokens["cache_creation_input_tokens"] - int(p.get("cache_creation_input_tokens") or 0)),
            }
            if not any(delta.values()):
                continue  # Nothing new since the last stop for this model
            tracker.record(
                model=model or pricing.DEFAULT_MODEL,
                role="experimentalist_run",
                session_id=session_id,
                tokens=delta,
                billing_source="subscription",
            )
    except Exception:
        pass  # Never block the agent


def _check_and_write_alerts(project_root: Path, timeout_count: int) -> None:
    """Detect alert conditions at session end, write to .distillate/alerts.json."""
    from distillate.budget import write_experiment_alert

    budget_path = project_root / ".distillate" / "budget.json"
    spend_path = project_root / ".distillate" / "compute_spend.json"

    # Wrong platform: HF Jobs configured but zero jobs submitted, session ≥5 min
    if budget_path.exists():
        try:
            budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
            compute_cfg = budget_data.get("compute") or {}
            if compute_cfg.get("provider") == "hfjobs":
                elapsed_min = 0.0
                sess_started_str = budget_data.get("session_started_at", "")
                if sess_started_str:
                    try:
                        sess_started = datetime.fromisoformat(
                            sess_started_str.replace("Z", "+00:00")
                        )
                        elapsed_min = (
                            datetime.now(timezone.utc) - sess_started
                        ).total_seconds() / 60
                    except ValueError:
                        pass

                if elapsed_min >= 5:
                    spend_data: dict = {}
                    if spend_path.exists():
                        try:
                            spend_data = json.loads(spend_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            pass
                    if not (spend_data.get("jobs") or []):
                        gpu = compute_cfg.get("gpu_type", "a100-large")
                        write_experiment_alert(
                            cwd=project_root,
                            kind="wrong_platform",
                            message=(
                                f"Ran locally instead of HF Jobs ({gpu}). "
                                "DISTILLATE_COMPUTE may not have reached the agent"
                                " — check compute config and re-launch."
                            ),
                        )
        except (OSError, json.JSONDecodeError):
            pass

    # Time budget exhausted: ≥1 run auto-concluded as timeout
    if timeout_count > 0:
        write_experiment_alert(
            cwd=project_root,
            kind="time_budget_exhausted",
            message=(
                f"{timeout_count} run{'s' if timeout_count != 1 else ''} "
                "timed out before completing. Consider increasing "
                "duration_minutes or simplifying the training script."
            ),
        )


def main() -> None:
    """Entry point: reads Stop event from stdin."""
    if not os.environ.get("DISTILLATE_SESSION"):
        return
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        event = json.loads(raw)

        session_id = event.get("session_id", "")
        stop_reason = event.get("stop_reason", "user")
        ts = datetime.now(timezone.utc).isoformat()
        project_root = _find_project_root()

        # Clean up any unconsumed graceful-stop flag so the next session
        # doesn't inherit a stale signal (e.g. if the agent crashed mid-run).
        stop_flag = project_root / ".distillate" / "stop_requested"
        if stop_flag.exists():
            try:
                stop_flag.unlink()
            except OSError:
                pass

        # Persist the Claude Code session ID so the next launch can resume
        # the same conversation via --resume.
        if session_id:
            try:
                (project_root / ".distillate" / "last_session_id").write_text(
                    session_id, encoding="utf-8"
                )
            except OSError:
                pass

        _append_event(project_root, {
            "type": "session_end",
            "ts": ts,
            "session_id": session_id,
            "stop_reason": stop_reason,
            "project_path": str(project_root),
        })

        # L4: auto-conclude any runs that overran their wrap deadline so the
        # UI doesn't show stuck "running" entries indefinitely.
        timeout_count = 0
        try:
            timeout_count = _autoconclude_overdue(project_root, session_id)
        except Exception:
            pass  # Best-effort; never block the agent

        # Alert detection: wrong compute platform, time budget exhausted.
        try:
            _check_and_write_alerts(project_root, timeout_count)
        except Exception:
            pass  # Best-effort; never block the agent

        # Record token usage for this experimentalist session so the billing
        # UI includes experiment subprocess spend alongside Nicolas turns.
        try:
            transcript_path = event.get("transcript_path", "")
            if not transcript_path:
                cwd_str = event.get("cwd", "")
                found = _locate_transcript(session_id, cwd_str)
                transcript_path = str(found) if found else ""
            if transcript_path:
                _record_session_tokens(session_id, Path(transcript_path))
        except Exception:
            pass  # Best-effort; never block the agent

        # Session-end experiment-entry writing removed: per-run notebook
        # entries from run_tools already cover the run stream. Duplicating
        # them as a prose summary clogged the Notebook with redundant data.

    except Exception:
        pass  # Never block the agent


if __name__ == "__main__":
    main()
