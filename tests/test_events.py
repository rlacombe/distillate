# Covers: distillate/events.py
"""Tests for the unified event stream (Phase 5)."""

import json
import pytest
from unittest.mock import patch

import distillate.state as state_mod
from distillate.state import State
from distillate.events import (
    Event,
    create_event,
    emit_event,
    query_events,
    MANUAL_NOTE,
    RUN_COMPLETED,
    EXPERIMENT_LAUNCHED,
    PAPER_ADDED,
)


@pytest.fixture
def state(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"zotero_library_version": 0, "documents": {}}))
    with patch.object(state_mod, "STATE_PATH", state_file), \
         patch.object(state_mod, "LOCK_PATH", state_file.with_suffix(".lock")):
        s = State()
    yield s


class TestEventCreation:

    def test_create_event_has_id_and_timestamp(self):
        evt = create_event("test_event")
        assert evt.id
        assert evt.timestamp
        assert evt.event_type == "test_event"

    def test_create_event_with_context(self):
        evt = create_event(
            EXPERIMENT_LAUNCHED,
            experiment_id="exp-1",
            payload={"name": "my experiment"},
        )
        assert evt.experiment_id == "exp-1"
        assert evt.payload["name"] == "my experiment"

    def test_to_dict_roundtrip(self):
        evt = create_event(
            MANUAL_NOTE,
            payload={"text": "Hello world"},
            tags=["test"],
        )
        d = evt.to_dict()
        assert d["event_type"] == MANUAL_NOTE
        assert d["payload"]["text"] == "Hello world"
        assert "test" in d["tags"]


class TestEventPersistence:

    def test_emit_event_persists(self, state):
        assert len(state.events) == 0
        evt = emit_event(state, MANUAL_NOTE, payload={"text": "note 1"})
        assert len(state.events) == 1
        assert state.events[0]["event_type"] == MANUAL_NOTE

    def test_multiple_events(self, state):
        emit_event(state, EXPERIMENT_LAUNCHED, experiment_id="e1")
        emit_event(state, RUN_COMPLETED, experiment_id="e1")
        emit_event(state, MANUAL_NOTE, payload={"text": "thoughts"})
        assert len(state.events) == 3

    def test_event_stream_bounded(self, state):
        for i in range(10005):
            state.add_event(create_event("test", payload={"i": i}))
        assert len(state.events) <= 10000


class TestEventQuery:

    def test_query_all(self, state):
        emit_event(state, MANUAL_NOTE, payload={"text": "a"})
        emit_event(state, RUN_COMPLETED, experiment_id="e1")
        events = state.query_events()
        assert len(events) == 2

    def test_query_by_type(self, state):
        emit_event(state, MANUAL_NOTE, payload={"text": "a"})
        emit_event(state, RUN_COMPLETED, experiment_id="e1")
        events = state.query_events(event_types=[MANUAL_NOTE])
        assert len(events) == 1
        assert events[0]["event_type"] == MANUAL_NOTE

    def test_query_by_project(self, state):
        emit_event(state, MANUAL_NOTE, experiment_id="p1", payload={"text": "a"})
        emit_event(state, MANUAL_NOTE, experiment_id="p2", payload={"text": "b"})
        events = state.query_events(experiment_id="p1")
        assert len(events) == 1
        assert events[0]["payload"]["text"] == "a"

    def test_query_limit(self, state):
        for i in range(10):
            emit_event(state, MANUAL_NOTE, payload={"text": str(i)})
        events = state.query_events(limit=3)
        assert len(events) == 3

    def test_query_newest_first(self, state):
        emit_event(state, MANUAL_NOTE, payload={"text": "first"})
        emit_event(state, MANUAL_NOTE, payload={"text": "second"})
        events = state.query_events()
        assert events[0]["payload"]["text"] == "second"
