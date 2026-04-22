#!/usr/bin/env python3
"""v2 Phase 5 migration — backfill events from existing notebook entries,
experiments, runs, and papers.

Idempotent. Logs every change. Safe to run multiple times.

Usage:
    python scripts/migrate_v2_phase5.py [--dry-run]
"""

import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def main():
    dry_run = "--dry-run" in sys.argv

    from distillate.config import CONFIG_DIR
    state_path = CONFIG_DIR / "state.json"

    if not state_path.exists():
        log.error("No state.json found at %s", state_path)
        sys.exit(1)

    if not dry_run:
        backup = state_path.with_suffix(f".json.pre-v2p5-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(state_path, backup)
        log.info("Backed up state to %s", backup)

    from distillate.state import State
    from distillate.events import create_event

    state = State()

    if state._data.get("v2_phase5_migrated"):
        log.info("Already migrated — nothing to do.")
        return

    count = 0

    # 1. Backfill from experiments and runs
    for proj_id, proj in state.experiments.items():
        evt = create_event(
            "experiment_launched",
            experiment_id=proj_id,
            project_id=proj.get("workspace_id"),
            payload={"name": proj.get("name", ""), "status": proj.get("status", "")},
        )
        evt.timestamp = proj.get("added_at", evt.timestamp)
        state.add_event(evt)
        count += 1

        for run_id, run in (proj.get("runs") or {}).items():
            evt = create_event(
                "run_completed",
                experiment_id=proj_id,
                project_id=proj.get("workspace_id"),
                payload={
                    "run_id": run_id,
                    "decision": run.get("decision", ""),
                    "results": run.get("results", {}),
                },
            )
            evt.timestamp = run.get("started_at", evt.timestamp)
            state.add_event(evt)
            count += 1

    # 2. Backfill from papers
    for key, doc in state._data.get("documents", {}).items():
        if doc.get("status") == "processed":
            evt = create_event(
                "paper_added",
                paper_id=key,
                payload={
                    "title": doc.get("title", ""),
                    "status": doc.get("status", ""),
                },
            )
            evt.timestamp = doc.get("processed_at") or doc.get("uploaded_at", evt.timestamp)
            state.add_event(evt)
            count += 1

    # 3. Backfill from existing notebook entries
    for entry in state._data.get("notebook_entries", []):
        evt = create_event(
            "manual_note",
            project_id=entry.get("project_id"),
            payload={
                "text": entry.get("text", ""),
                "entry_type": entry.get("entry_type", "note"),
                "source": entry.get("source", ""),
            },
            tags=entry.get("tags", []),
        )
        evt.timestamp = entry.get("timestamp", evt.timestamp)
        state.add_event(evt)
        count += 1

    state._data["v2_phase5_migrated"] = True

    if dry_run:
        log.info("DRY RUN: would create %d events", count)
    else:
        state.save()
        log.info("Migration complete: %d events created", count)


if __name__ == "__main__":
    main()
