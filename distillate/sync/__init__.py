"""Cloud sync: state synchronization and email notifications.

This package provides the clean import path for Distillate's cloud sync layer.
Currently a facade that re-exports from the flat module layout; logic will be
extracted here incrementally.
"""

# --- Cloud state sync ---
from distillate.cloud_sync import (
    cloud_sync_available,
    pull_state,
    push_state,
    sync_state,
)

# --- Email notifications ---
from distillate.cloud_email import (
    prompt_for_email_cli,
    send_experiment_event,
    sync_snapshot,
)

__all__ = [
    # cloud_sync
    "cloud_sync_available",
    "pull_state",
    "push_state",
    "sync_state",
    # cloud_email
    "prompt_for_email_cli",
    "send_experiment_event",
    "sync_snapshot",
]
