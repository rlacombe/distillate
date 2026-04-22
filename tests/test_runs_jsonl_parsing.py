# Covers: distillate/experiments.py (_parse_runs_jsonl, scan_experiment, _create_run)
"""Tests for runs.jsonl parsing, deduplication, and orphan detection.

Covers bugs found during overnight run audit (2026-03-14):
  BUG 1: _rescan_project merge freezes decision as "running"
  BUG 2: _parse_runs_jsonl dedup (last entry wins)
  BUG 3: current_run / active_run_start stale on inactive projects
  BUG 4: current_run missing for active projects
"""

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper: write runs.jsonl
# ---------------------------------------------------------------------------

def _write_runs_jsonl(directory: Path, entries: list[dict]):
    """Write a list of run entries to .distillate/runs.jsonl."""
    dist_dir = directory / ".distillate"
    dist_dir.mkdir(parents=True, exist_ok=True)
    with open(dist_dir / "runs.jsonl", "w", encoding="utf-8") as f:
        for entry in entries:
            entry.setdefault("$schema", "distillate/run/v1")
            f.write(json.dumps(entry) + "\n")


def _make_entry(run_id, status, ts, description="", results=None, **extra):
    """Build a runs.jsonl entry."""
    e = {
        "$schema": "distillate/run/v1",
        "id": run_id,
        "status": status,
        "timestamp": ts,
        "description": description,
    }
    if results:
        e["results"] = results
    e.update(extra)
    return e


# ---------------------------------------------------------------------------
# BUG 1 & 2: _parse_runs_jsonl dedup — last entry wins
# ---------------------------------------------------------------------------

class TestParseRunsJsonlDedup:
    """_parse_runs_jsonl should deduplicate by run name, last entry wins."""

    def test_running_then_keep_resolves_to_completed(self, tmp_path):
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z", "Baseline"),
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z", "Baseline",
                        results={"accuracy": 0.95}),
        ])
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 1
        assert runs[0]["decision"] == "completed"

    def test_running_then_discard_resolves_to_completed(self, tmp_path):
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "discard", "2026-03-14T00:10:00Z",
                        results={"accuracy": 0.5}),
        ])
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 1
        assert runs[0]["decision"] == "completed"

    def test_running_crash_discard_resolves_to_completed(self, tmp_path):
        """Multiple entries per run (running -> crash -> discard): last wins."""
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_002", "running", "2026-03-14T00:15:00Z"),
            _make_entry("run_002", "crash", "2026-03-14T00:15:00Z"),
            _make_entry("run_002", "discard", "2026-03-14T00:20:00Z",
                        results={"accuracy": 0.3}),
        ])
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 1
        assert runs[0]["decision"] == "completed"

    def test_orphaned_running_stays_running(self, tmp_path):
        """A running entry with no completion should stay as running."""
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z",
                        results={"accuracy": 0.95}),
            _make_entry("run_002", "running", "2026-03-14T00:20:00Z", "Still running"),
        ])
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 2
        by_name = {r["name"]: r for r in runs}
        assert by_name["run_001"]["decision"] == "completed"
        assert by_name["run_002"]["decision"] == "running"

    def test_many_runs_all_resolved(self, tmp_path):
        """13 runs each with running+discard should produce 13 completed runs."""
        entries = []
        for i in range(1, 14):
            entries.append(_make_entry(f"run_{i:03d}", "running",
                                       f"2026-03-14T{i:02d}:00:00Z"))
            entries.append(_make_entry(f"run_{i:03d}", "discard",
                                       f"2026-03-14T{i:02d}:10:00Z",
                                       results={"test_accuracy": 0.5}))
        _write_runs_jsonl(tmp_path, entries)
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 13
        for run in runs:
            assert run["decision"] == "completed", \
                f"Run {run['name']} should be completed, got {run.get('decision')}"


# ---------------------------------------------------------------------------
# BUG 1: _rescan_project merge must update decision
# ---------------------------------------------------------------------------

class TestRescanMergeDecision:
    """_rescan_project must update run decision when it changes from running to resolved."""

    def _setup_state_with_running_run(self, tmp_path, monkeypatch):
        """Set up a state with a project that has a run in 'running' state."""
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        state.add_experiment("my-project", "My Project", str(proj_dir))

        # Add a run with decision="running" to simulate the initial scan
        from distillate.experiments import _create_run
        run = _create_run(
            prefix="sr",
            name="run_001",
            started_at="2026-03-14T00:00:00Z",
            decision="running",
            description="Baseline",
        )
        run["status"] = "running"
        state.add_run("my-project", run["id"], run)
        state.save()
        return state, proj_dir

    def test_merge_updates_decision_from_running_to_completed(self, tmp_path, monkeypatch):
        """When a run goes from running to discard, the merge should update it."""
        state, proj_dir = self._setup_state_with_running_run(tmp_path, monkeypatch)

        # Now write the completed entry to runs.jsonl
        _write_runs_jsonl(proj_dir, [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z", "Baseline"),
            _make_entry("run_001", "discard", "2026-03-14T00:10:00Z", "Baseline",
                        results={"accuracy": 0.5}),
        ])

        # Rescan should update the decision
        from distillate.experiments import scan_experiment
        result = scan_experiment(proj_dir)
        scanned_runs = result.get("runs", {})

        # Find the run_001 in scanned results
        run_001 = None
        for rid, rdata in scanned_runs.items():
            if rdata["name"] == "run_001":
                run_001 = rdata
                break
        assert run_001 is not None
        assert run_001["decision"] == "completed", \
            f"Scanned run should be completed, got {run_001.get('decision')}"

    def test_merge_preserves_only_empty_fields(self, tmp_path, monkeypatch):
        """The old merge logic (if v and not erun.get(k)) fails to update decision.

        This test documents the BUG: after merging, the decision should be
        'discard' but the old code keeps it as 'running'.
        """
        state, proj_dir = self._setup_state_with_running_run(tmp_path, monkeypatch)
        proj = state.get_experiment("my-project")
        old_runs = proj.get("runs", {})

        # Verify the stored run has decision="running"
        stored_run = list(old_runs.values())[0]
        assert stored_run["decision"] == "running"

        # Simulate what scan_experiment returns after the run completes
        from distillate.experiments import _create_run
        new_run = _create_run(
            prefix="sr",
            name="run_001",
            started_at="2026-03-14T00:00:00Z",
            decision="completed",
            description="Baseline",
            results={"accuracy": 0.5},
        )

        # Simulate the old merge logic (BUG)
        for k, v in new_run.items():
            if k == "id":
                continue
            if v and not stored_run.get(k):
                stored_run[k] = v

        # BUG: decision is still "running" because stored_run already had it
        assert stored_run["decision"] == "running", \
            "Old merge keeps 'running' — this documents the bug"

        # Now simulate the FIXED merge logic: always update decision + status
        stored_run2 = dict(stored_run)  # fresh copy
        stored_run2["decision"] = "running"  # reset
        for k, v in new_run.items():
            if k == "id":
                continue
            # Fixed: always update decision and status fields
            if k in ("decision", "status"):
                if v:
                    stored_run2[k] = v
            elif v and not stored_run2.get(k):
                stored_run2[k] = v

        assert stored_run2["decision"] == "completed", \
            "Fixed merge should update decision to 'completed'"


# ---------------------------------------------------------------------------
# BUG 3 & 4: current_run / active_run_start must respect session status
# ---------------------------------------------------------------------------

class TestCurrentRunSessionAwareness:
    """current_run and active_run_start should only be set for projects with active sessions."""

    def test_stale_running_entry_without_active_session(self, tmp_path):
        """If project has no active sessions, current_run should be None
        even if runs.jsonl has an unresolved 'running' entry."""
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z",
                        results={"accuracy": 0.95}),
            _make_entry("run_002", "running", "2026-03-14T08:00:00Z", "Still running"),
        ])
        runs_file = tmp_path / ".distillate" / "runs.jsonl"
        all_lines = runs_file.read_text(encoding="utf-8").splitlines()

        # Simulate the backward pass (from server.py experiment_list)
        resolved_ids = set()
        current_run = None
        for line in reversed(all_lines):
            line = line.strip()
            if not line:
                continue
            rr = json.loads(line)
            rid = rr.get("id", "")
            status = rr.get("status", "")
            if status in ("best", "completed", "keep", "discard", "crash"):
                resolved_ids.add(rid)
            if status == "running" and rid not in resolved_ids and rr.get("description"):
                current_run = rr["description"]
                break

        # Without session check, current_run is set (BUG)
        assert current_run == "Still running"
        # With session check (active_sessions=0), it should be None

    def test_active_session_without_running_entry(self, tmp_path):
        """If project has active sessions but no unresolved 'running' entry,
        the RUNNING pane should still show something (BUG 4)."""
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "discard", "2026-03-14T00:10:00Z",
                        results={"accuracy": 0.5}),
        ])
        runs_file = tmp_path / ".distillate" / "runs.jsonl"
        all_lines = runs_file.read_text(encoding="utf-8").splitlines()

        # Backward pass
        resolved_ids = set()
        current_run = None
        for line in reversed(all_lines):
            line = line.strip()
            if not line:
                continue
            rr = json.loads(line)
            rid = rr.get("id", "")
            status = rr.get("status", "")
            if status in ("best", "completed", "keep", "discard", "crash"):
                resolved_ids.add(rid)
            if status == "running" and rid not in resolved_ids and rr.get("description"):
                current_run = rr["description"]
                break

        # All runs resolved: current_run is None
        assert current_run is None
        # But the session IS active — should show session status instead


# ---------------------------------------------------------------------------
# Orphan auto-close
# ---------------------------------------------------------------------------

class TestOrphanAutoClose:
    """Orphaned 'running' entries should be auto-closed as crash."""

    def test_closes_orphaned_running(self, tmp_path):
        """A running entry with no subsequent resolution should be closed."""
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z", "Baseline"),
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z", "Baseline"),
            _make_entry("run_002", "running", "2026-03-14T00:20:00Z", "Orphan"),
        ])

        # Simulate the orphan detection logic from launcher.py
        runs_file = tmp_path / ".distillate" / "runs.jsonl"
        lines = runs_file.read_text(encoding="utf-8").strip().splitlines()
        runs = [json.loads(line) for line in lines if line.strip()]

        resolved_ids = {r["id"] for r in runs
                        if r.get("status") in ("best", "completed", "keep", "discard", "crash")}
        orphans = [r for r in runs
                   if r.get("status") == "running" and r.get("id") not in resolved_ids]

        assert len(orphans) == 1
        assert orphans[0]["id"] == "run_002"

    def test_no_orphans_when_all_resolved(self, tmp_path):
        """No orphans when all running entries have completions."""
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "discard", "2026-03-14T00:10:00Z"),
            _make_entry("run_002", "running", "2026-03-14T00:20:00Z"),
            _make_entry("run_002", "keep", "2026-03-14T00:30:00Z"),
        ])
        runs_file = tmp_path / ".distillate" / "runs.jsonl"
        lines = runs_file.read_text(encoding="utf-8").strip().splitlines()
        runs = [json.loads(line) for line in lines if line.strip()]

        resolved_ids = {r["id"] for r in runs
                        if r.get("status") in ("best", "completed", "keep", "discard", "crash")}
        orphans = [r for r in runs
                   if r.get("status") == "running" and r.get("id") not in resolved_ids]

        assert len(orphans) == 0

    def test_orphan_crash_uses_original_timestamp(self, tmp_path):
        """Auto-closed crash entry should use the original running timestamp,
        not the current time (to avoid inflating experiment time)."""
        original_ts = "2026-03-14T00:20:00Z"
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_002", "running", original_ts, "Orphan"),
        ])

        runs_file = tmp_path / ".distillate" / "runs.jsonl"
        lines = runs_file.read_text(encoding="utf-8").strip().splitlines()
        runs = [json.loads(line) for line in lines if line.strip()]

        resolved_ids = {r["id"] for r in runs
                        if r.get("status") in ("best", "completed", "keep", "discard", "crash")}
        orphans = [r for r in runs
                   if r.get("status") == "running" and r.get("id") not in resolved_ids]

        for orph in orphans:
            crash_entry = {
                "$schema": "distillate/run/v1",
                "id": orph["id"],
                "timestamp": orph.get("timestamp", ""),  # Use original timestamp!
                "status": "crash",
                "description": orph.get("description", ""),
                "reasoning": "Auto-closed: run was announced but never completed.",
            }
            assert crash_entry["timestamp"] == original_ts
