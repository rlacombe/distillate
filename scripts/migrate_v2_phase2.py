#!/usr/bin/env python3
"""v2 Phase 2 migration — backfill agent_id, harness_id, tier fields.

Idempotent. Logs every change. Safe to run multiple times.

Usage:
    python scripts/migrate_v2_phase2.py [--dry-run]
"""

import json
import logging
import shutil
import sys
from datetime import datetime, timezone
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

    # Backup before migration
    backup_path = state_path.with_suffix(f".json.pre-v2-{datetime.now().strftime('%Y%m%d%H%M%S')}")
    if not dry_run:
        shutil.copy2(state_path, backup_path)
        log.info("Backed up state to %s", backup_path)

    from distillate.state import State
    state = State()

    if state._data.get("v2_phase2_migrated"):
        log.info("Already migrated — nothing to do.")
        return

    changes = state.migrate_v2_phase2()

    if dry_run:
        log.info("DRY RUN: would make %d changes", changes)
    else:
        state.save()
        log.info("Migration complete: %d fields updated", changes)


if __name__ == "__main__":
    main()
