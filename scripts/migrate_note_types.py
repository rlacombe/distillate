#!/usr/bin/env python3
"""Collapse legacy entry types into ``note`` and auto-pin former milestones.

The notebook originally offered four manual entry types — note, observation,
decision, milestone. We collapsed all four into a single ``note`` type and
introduced a pin flag that replaces "milestone" as a first-class concept.

This migration walks every day file under ``NOTEBOOK_ROOT`` and rewrites
entry-type prefixes in-place:

  - ``[milestone]`` → ``[note]`` **and** the entry is added to ``pins.json``
  - ``[observation]`` → ``[note]``
  - ``[decision]`` → ``[note]``

Run with:
    .venv/bin/python scripts/migrate_note_types.py [--dry-run]

Idempotent: re-running is a no-op because there will be nothing left to
migrate.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from distillate.lab_notebook import (  # noqa: E402
    NOTEBOOK_ROOT,
    load_pins,
    pin_key,
    save_pins,
)


# ``- **HH:MM** — [type] rest``
_ENTRY_RE = re.compile(r"^(- \*\*(\d{2}:\d{2})\*\* — \[)(\w+)(\] .*)$")

_COLLAPSE_MAP = {
    "milestone": "note",
    "observation": "note",
    "decision": "note",
}


def _iter_day_files() -> list[Path]:
    if not NOTEBOOK_ROOT.exists():
        return []
    return sorted(p for p in NOTEBOOK_ROOT.glob("*/*/*.md") if p.stem != "index")


def _file_date(path: Path) -> str:
    """Day files are named YYYY-MM-DD.md; the stem is the date string."""
    return path.stem


def migrate(dry_run: bool) -> tuple[int, int, int]:
    """Run the migration. Returns (files_touched, lines_rewritten, pins_added)."""
    pins = load_pins()
    pins_before = len(pins)

    files_touched = 0
    lines_rewritten = 0

    for path in _iter_day_files():
        date = _file_date(path)
        text = path.read_text(encoding="utf-8")
        new_lines: list[str] = []
        changed = False

        for raw in text.splitlines(keepends=True):
            m = _ENTRY_RE.match(raw.rstrip("\n"))
            if not m:
                new_lines.append(raw)
                continue
            prefix, time, old_type, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
            if old_type not in _COLLAPSE_MAP:
                new_lines.append(raw)
                continue
            new_type = _COLLAPSE_MAP[old_type]
            new_line = f"{prefix}{new_type}{suffix}\n"
            new_lines.append(new_line)
            lines_rewritten += 1
            changed = True
            # Auto-pin former milestones so they keep their visual distinction
            if old_type == "milestone":
                pins.add(pin_key("notebook", date, time))

        if changed:
            files_touched += 1
            if not dry_run:
                path.write_text("".join(new_lines), encoding="utf-8")

    pins_added = len(pins) - pins_before
    if pins_added and not dry_run:
        save_pins(pins)

    return files_touched, lines_rewritten, pins_added


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files_touched, lines_rewritten, pins_added = migrate(args.dry_run)

    flag = "(dry-run) would rewrite" if args.dry_run else "rewrote"
    print(f"{flag} {lines_rewritten} entry lines across {files_touched} files")
    pin_flag = "(dry-run) would add" if args.dry_run else "added"
    print(f"{pin_flag} {pins_added} new pins")
    print(f"Notebook root: {NOTEBOOK_ROOT}")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
