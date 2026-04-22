"""Unified event stream — the backbone of the Notebook view.

Every primitive emits events when it changes. The Notebook view is a
read-only timeline over this event stream. Manual notes materialize
as events with type "manual_note".

Phase 5 introduces the events table and emission helpers. The existing
notebook table stays as a compatibility layer.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

# Projects
PROJECT_CREATED = "project_created"
PROJECT_UPDATED = "project_updated"
PROJECT_ARCHIVED = "project_archived"

# Experiments
EXPERIMENT_LAUNCHED = "experiment_launched"
EXPERIMENT_PAUSED = "experiment_paused"
EXPERIMENT_COMPLETED = "experiment_completed"

# Runs
RUN_STARTED = "run_started"
RUN_COMPLETED = "run_completed"
RUN_METRIC_EMITTED = "run_metric_emitted"
RUN_FAILED = "run_failed"

# Papers
PAPER_ADDED = "paper_added"
PAPER_SUMMARIZED = "paper_summarized"
HIGHLIGHT_EXTRACTED = "highlight_extracted"
PAPER_LINKED = "paper_linked_to_project"

# Sessions
SESSION_STARTED = "session_started"
SESSION_ENDED = "session_ended"

# Agents
AGENT_INVOKED = "agent_invoked"
AGENT_COMPLETED = "agent_completed"

# User
MANUAL_NOTE = "manual_note"


# ---------------------------------------------------------------------------
# Event data structures
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """A single event in the unified stream."""
    id: str = ""
    timestamp: str = ""
    event_type: str = ""
    experiment_id: Optional[str] = None
    agent_id: Optional[str] = None
    paper_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "experiment_id": self.experiment_id,
            "agent_id": self.agent_id,
            "paper_id": self.paper_id,
            "payload": self.payload,
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------

def create_event(
    event_type: str,
    *,
    experiment_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    paper_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> Event:
    """Create an event (does NOT persist — call state.add_event() for that)."""
    return Event(
        id=str(uuid.uuid4())[:8],
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_type=event_type,
        experiment_id=experiment_id,
        agent_id=agent_id,
        paper_id=paper_id,
        payload=payload or {},
        tags=tags or [],
    )


def emit_event(state, event_type: str, **kwargs) -> Event:
    """Create and persist an event to the state's event stream."""
    event = create_event(event_type, **kwargs)
    state.add_event(event)
    return event


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def query_events(
    state,
    *,
    experiment_id: Optional[str] = None,
    event_types: Optional[List[str]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
) -> List[Event]:
    """Query the event stream with optional filters."""
    events_data = state.events
    results = []

    for evt_dict in events_data:
        if experiment_id and evt_dict.get("experiment_id") != experiment_id:
            continue
        if event_types and evt_dict.get("event_type") not in event_types:
            continue
        ts = evt_dict.get("timestamp", "")
        if since and ts < since:
            continue
        if until and ts > until:
            continue

        results.append(Event(**{k: evt_dict.get(k) for k in Event.__dataclass_fields__}))

    # Sort by timestamp descending (newest first)
    results.sort(key=lambda e: e.timestamp, reverse=True)
    return results[:limit]
