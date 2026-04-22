#!/usr/bin/env python3
"""Backfill the Lab Notebook from existing project state.

Walks ``state.json`` and synthesises historical notebook entries from:
  - Project runs (started_at/completed_at)
  - Project creation (added_at)
  - Processed papers (uploaded_at)

Entries are written directly into the per-day markdown files at
``KNOWLEDGE_DIR/notebook/YYYY/MM/YYYY-MM-DD.md`` in the same line format
the live notebook produces. Existing entries are preserved; duplicates
(same time + same text) are skipped, so the script is idempotent.

Workspace coding sessions are intentionally skipped — they're already
merged into the notebook view at read-time from ``notes.md`` files and
would double-appear if backfilled.

Run with:
    .venv/bin/python scripts/backfill_notebook.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Make the repo importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from distillate.lab_notebook import (  # noqa: E402
    NOTEBOOK_ROOT,
    _day_path,
    _ensure_header,
    _format_entry_line,
    _slug_tag,
    load_pins,
    pin_key,
    save_pins,
)
from distillate import config  # noqa: E402


# ---------------------------------------------------------------------------
# Timestamp parsing — state.json uses several ISO8601 flavours
# ---------------------------------------------------------------------------


def _parse_ts(raw: str | None) -> datetime | None:
    """Parse an ISO8601 timestamp in any of the formats state.json uses.

    Returns a UTC-aware ``datetime`` or ``None`` if the input is empty /
    unparseable. Accepts trailing ``Z``, explicit ``+00:00``, and naive
    strings (treated as UTC).
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    # Normalise trailing Z → +00:00 for fromisoformat
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Entry collection
# ---------------------------------------------------------------------------


_BEST_METRIC_KEYS = ("accuracy", "val_accuracy", "f1", "score", "val_loss", "loss")


def _best_metric(runs: list[dict]) -> tuple[str, float] | None:
    """Return the best (metric_name, value) across a group of runs.

    Prefers accuracy/score (higher is better) over loss (lower is better).
    Returns ``None`` if no run has a readable numeric metric.
    """
    for key in ("accuracy", "val_accuracy", "f1", "score"):
        vals = [
            r["results"][key]
            for r in runs
            if isinstance(r.get("results"), dict)
            and isinstance(r["results"].get(key), (int, float))
        ]
        if vals:
            return key, max(vals)
    for key in ("val_loss", "loss"):
        vals = [
            r["results"][key]
            for r in runs
            if isinstance(r.get("results"), dict)
            and isinstance(r["results"].get(key), (int, float))
        ]
        if vals:
            return key, min(vals)
    return None


def collect_entries(state: dict) -> list[dict]:
    """Walk state.json and return a list of entry dicts.

    Runs are grouped by (project, UTC date) into a single summary entry per
    project per day, because structured-log ingestion often dumps many runs
    with identical timestamps which would otherwise flood the feed. Papers
    and project creations are emitted one-to-one.

    Each dict has keys: ``dt`` (UTC datetime), ``text``, ``entry_type``,
    ``project`` (display name, will be slugified by the writer).
    """
    entries: list[dict] = []

    projects = state.get("projects", {}) or {}
    for pid, proj in projects.items():
        name = proj.get("name") or pid

        # Project creation — single entry at the time the project was tracked.
        # These are high-signal moments worth pinning by default; the writer
        # will add them to pins.json below.
        added = _parse_ts(proj.get("added_at"))
        if added:
            entries.append({
                "dt": added,
                "text": f'Project tracked: "{name}"',
                "entry_type": "note",
                "project": name,
                "pin": True,
            })

        # Group runs by UTC date
        runs_raw = proj.get("runs") or {}
        if isinstance(runs_raw, dict):
            run_iter = runs_raw.values()
        elif isinstance(runs_raw, list):
            run_iter = runs_raw
        else:
            run_iter = []

        by_day: dict[str, list[dict]] = defaultdict(list)
        latest_on_day: dict[str, datetime] = {}
        for run in run_iter:
            if not isinstance(run, dict):
                continue
            ts = _parse_ts(run.get("completed_at")) or _parse_ts(run.get("started_at"))
            if not ts:
                continue
            day = ts.strftime("%Y-%m-%d")
            by_day[day].append(run)
            if day not in latest_on_day or ts > latest_on_day[day]:
                latest_on_day[day] = ts

        for day, group in by_day.items():
            count = len(group)
            ts = latest_on_day[day]
            metric = _best_metric(group)
            if count == 1:
                run = group[0]
                rname = run.get("name") or run.get("id") or "run"
                metric_str = ""
                if metric:
                    metric_str = f" — best {metric[0]}={metric[1]:.4g}"
                text = f'Run: "{rname}" on {name}{metric_str}'
            else:
                metric_str = ""
                if metric:
                    metric_str = f" — best {metric[0]}={metric[1]:.4g}"
                text = f"{count} runs on {name}{metric_str}"
            entries.append({
                "dt": ts,
                "text": text,
                "entry_type": "run_completed",
                "project": name,
            })

    # Processed papers
    docs = state.get("documents", {}) or {}
    for key, doc in docs.items():
        if doc.get("status") != "processed":
            continue
        ts = _parse_ts(doc.get("processed_at")) or _parse_ts(doc.get("uploaded_at"))
        if not ts:
            continue
        title = (doc.get("title") or "").strip() or key
        # Trim very long titles to keep the notebook readable
        if len(title) > 120:
            title = title[:117] + "…"
        entries.append({
            "dt": ts,
            "text": f'Paper processed: "{title}"',
            "entry_type": "paper",
            "project": "",
        })

    return entries


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

def _existing_lines(path: Path) -> set[str]:
    """Return the set of raw entry lines already present in a day file.

    Used as an idempotency key so re-running backfill is a no-op. We compare
    the fully-formatted line (time + type + text + tags) verbatim, which is
    robust against stray ``#`` characters inside entry text.
    """
    if not path.exists():
        return set()
    return {
        line.rstrip("\n")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("- **")
    }


def _write_day(
    day: str,
    entries: list[dict],
    dry_run: bool,
    pins: set[str],
) -> int:
    """Write *entries* for a UTC *day* to the notebook file.

    Sorts by time ascending, skips duplicates by exact-line match, and appends
    in chronological order. Mutates *pins* in place with any entry flagged
    ``pin=True`` so the caller can persist the pin set once at the end.
    Returns the number of new lines written.
    """
    # Parse "YYYY-MM-DD" into a UTC datetime for path resolution
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    path = _day_path(d)

    existing = _existing_lines(path)

    # Sort by time, format each line
    entries.sort(key=lambda e: e["dt"])
    new_lines: list[str] = []
    for e in entries:
        line = _format_entry_line(
            e["dt"].strftime("%H:%M"),
            e["text"],
            entry_type=e["entry_type"],
            project=e["project"],
        )
        stripped = line.rstrip("\n")
        if stripped not in existing:
            new_lines.append(line)
            existing.add(stripped)
        # Auto-pin regardless of whether the line was freshly written, so a
        # re-run of backfill still seeds pins for entries migrated earlier.
        if e.get("pin"):
            pins.add(pin_key("notebook", day, e["dt"].strftime("%H:%M")))

    if not new_lines:
        return 0
    if dry_run:
        return len(new_lines)

    _ensure_header(path, d)
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(new_lines)
    return len(new_lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Report what would be written without touching files.")
    ap.add_argument(
        "--state",
        default=str(config.CONFIG_DIR / "state.json"),
        help="Path to state.json (default: ~/.config/distillate/state.json)",
    )
    args = ap.parse_args()

    state_path = Path(args.state)
    if not state_path.exists():
        print(f"state.json not found at {state_path}", file=sys.stderr)
        return 1

    state = json.loads(state_path.read_text(encoding="utf-8"))
    entries = collect_entries(state)
    if not entries:
        print("No historical entries found.")
        return 0

    # Group by UTC day
    by_day: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_day[e["dt"].strftime("%Y-%m-%d")].append(e)

    pins = load_pins()
    pins_before = len(pins)

    total_new = 0
    affected_days = 0
    for day in sorted(by_day):
        written = _write_day(day, by_day[day], args.dry_run, pins)
        if written:
            affected_days += 1
            total_new += written
            flag = "(dry-run) would add" if args.dry_run else "added"
            print(f"  {day}: {flag} {written:>3} entries")

    pins_added = len(pins) - pins_before
    if pins_added and not args.dry_run:
        save_pins(pins)
    if pins_added:
        print(f"  + {pins_added} entries auto-pinned")

    print()
    print(f"Total new entries: {total_new} across {affected_days} days")
    print(f"Notebook root: {NOTEBOOK_ROOT}")
    if args.dry_run:
        print("(dry run — no files were modified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
