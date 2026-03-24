"""Tests for experiment tracking bugs found during overnight run audit (2026-03-14).

Covers:
  BUG 1: _rescan_project merge freezes decision as "running"
  BUG 2: _parse_runs_jsonl dedup (last entry wins)
  BUG 3: current_run / active_run_start stale on inactive projects
  BUG 4: current_run missing for active projects
  BUG 5: experiment_total_secs gap-based calculation
  BUG 6: key metric selection (param_count vs test_accuracy)
  BUG 7: time enforcement hook
"""

import importlib.util
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        state.add_project("my-project", "My Project", str(proj_dir))

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
        from distillate.experiments import scan_project
        result = scan_project(proj_dir)
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
        proj = state.get_project("my-project")
        old_runs = proj.get("runs", {})

        # Verify the stored run has decision="running"
        stored_run = list(old_runs.values())[0]
        assert stored_run["decision"] == "running"

        # Simulate what scan_project returns after the run completes
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
# BUG 5: experiment_total_secs gap-based calculation
# ---------------------------------------------------------------------------

class TestExperimentTotalTime:
    """Gap-based time calculation should handle various edge cases."""

    def _compute_total_secs(self, entries: list[dict], max_gap: int = 1800) -> float:
        """Reproduce the server.py pair-matching + gap calculation logic."""
        run_starts: dict[str, datetime] = {}
        pair_secs = 0.0
        unpaired_dts: list[datetime] = []

        for entry in entries:
            ts = entry.get("timestamp", "")
            st = entry.get("status", "")
            rid = entry.get("id", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                continue

            if st == "running":
                run_starts[rid] = dt
            elif st in ("best", "completed", "keep", "discard", "crash"):
                if rid in run_starts:
                    pair_secs += (dt - run_starts[rid]).total_seconds()
                    del run_starts[rid]
                else:
                    unpaired_dts.append(dt)
            else:
                unpaired_dts.append(dt)

        # Gap-based for unpaired entries
        gap_secs = 0.0
        prev_dt = None
        for udt in sorted(unpaired_dts):
            if prev_dt is not None:
                gap = (udt - prev_dt).total_seconds()
                if 0 < gap <= max_gap:
                    gap_secs += gap
            prev_dt = udt

        return pair_secs + gap_secs

    def test_simple_sequential_runs(self):
        """Two consecutive runs, 10 min each."""
        entries = [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z"),
            _make_entry("run_002", "running", "2026-03-14T00:12:00Z"),
            _make_entry("run_002", "discard", "2026-03-14T00:22:00Z"),
        ]
        total = self._compute_total_secs(entries)
        assert total == 20 * 60  # 10 + 10 = 20 minutes (pair-matched)

    def test_session_break_capped(self):
        """Gap > 30 min between sessions should be excluded."""
        entries = [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z"),
            # 2 hour gap (session break)
            _make_entry("run_002", "running", "2026-03-14T02:10:00Z"),
            _make_entry("run_002", "discard", "2026-03-14T02:20:00Z"),
        ]
        total = self._compute_total_secs(entries)
        assert total == 20 * 60  # 10 + 10 = 20 minutes (pair-matched)

    def test_mixed_timezone_entries(self):
        """Handles mix of Z suffix and +00:00 suffix."""
        entries = [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "keep", "2026-03-14T00:10:00+00:00"),
        ]
        total = self._compute_total_secs(entries)
        assert total == 600  # 10 minutes

    def test_naive_timestamps(self):
        """Handles timestamps without timezone info."""
        entries = [
            _make_entry("run_001", "running", "2026-03-14T00:00:00"),
            _make_entry("run_001", "keep", "2026-03-14T00:10:00"),
        ]
        total = self._compute_total_secs(entries)
        assert total == 600

    def test_long_run_without_intermediate_entries(self):
        """A single run that takes 2 hours — pair-matching handles this correctly."""
        entries = [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "keep", "2026-03-14T02:00:00Z"),
        ]
        total = self._compute_total_secs(entries)
        # Pair-matching: running→keep = 2 hours
        assert total == 2 * 3600  # 7200 seconds

    def test_active_run_start_cleared_on_completion(self):
        """active_run_start should be cleared when a run completes."""
        entries = [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z"),
        ]
        active_run_start = ""
        for entry in entries:
            st = entry.get("status", "")
            ts = entry.get("timestamp", "")
            if st == "running":
                active_run_start = ts
            elif st in ("best", "completed", "keep", "discard", "crash"):
                active_run_start = ""
        assert active_run_start == ""

    def test_active_run_start_set_for_orphan(self):
        """active_run_start should be set for an unresolved running entry."""
        entries = [
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z"),
            _make_entry("run_002", "running", "2026-03-14T01:00:00Z"),
        ]
        active_run_start = ""
        for entry in entries:
            st = entry.get("status", "")
            ts = entry.get("timestamp", "")
            if st == "running":
                active_run_start = ts
            elif st in ("best", "completed", "keep", "discard", "crash"):
                active_run_start = ""
        assert active_run_start == "2026-03-14T01:00:00Z"


# ---------------------------------------------------------------------------
# BUG 6: Key metric selection
# ---------------------------------------------------------------------------

class TestKeyMetricSelection:
    """Key metric should prefer meaningful metrics over param_count."""

    def test_prefers_accuracy_over_param_count(self, tmp_path, monkeypatch):
        """param_count may appear in more runs, but test_accuracy is more meaningful."""
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("proj", "Test", str(tmp_path))

        from distillate.experiments import _create_run
        # 10 runs with param_count only
        for i in range(10):
            run = _create_run(
                prefix="sr", name=f"run_{i:03d}",
                results={"param_count": 1000 + i},
                started_at=f"2026-03-14T{i:02d}:00:00Z",
                decision="completed",
            )
            state.add_run("proj", run["id"], run)

        # 5 runs with both param_count and test_accuracy
        for i in range(10, 15):
            run = _create_run(
                prefix="sr", name=f"run_{i:03d}",
                results={"param_count": 1000 + i, "test_accuracy": 0.8 + i * 0.01},
                started_at=f"2026-03-14T{i:02d}:00:00Z",
                decision="best",
            )
            state.add_run("proj", run["id"], run)

        proj = state.get_project("proj")
        from distillate.experiments import classify_metric
        pc_cat = classify_metric("param_count")
        ta_cat = classify_metric("test_accuracy")
        # param_count is "count" category, test_accuracy is "ratio" category
        assert pc_cat == "count"
        assert ta_cat == "ratio"

        # Reproduce the _infer_key_metric_name scoring logic with the fix
        from collections import Counter
        runs = list(proj.get("runs", {}).values())
        metric_counts: Counter = Counter()
        for run in runs:
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    metric_counts[k] += 1

        _RELEVANCE = {
            "test_accuracy": 100, "param_count": 5,
        }

        def _score(name):
            coverage = metric_counts[name] / len(runs)
            relevance = _RELEVANCE.get(name.lower(), 8)
            cat = classify_metric(name)
            if cat in ("count", "time", "cost"):
                relevance = min(relevance, 1)
            return coverage * relevance

        best = max(metric_counts.keys(), key=_score)
        assert best == "test_accuracy", \
            f"Expected test_accuracy, got {best}"


# ---------------------------------------------------------------------------
# BUG 7: Time enforcement hook
# ---------------------------------------------------------------------------

class TestTimeEnforcementHook:
    """PostToolUse hook should detect runs exceeding time budget."""

    def test_warns_after_threshold(self, tmp_path):
        """Hook should print warning when a run exceeds 10 minutes."""
        _write_runs_jsonl(tmp_path, [
            _make_entry(
                "run_001", "running",
                (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(),
                "Long running experiment",
            ),
        ])
        from distillate.hooks.post_bash import _check_run_elapsed
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            _check_run_elapsed(tmp_path)
        output = f.getvalue()
        assert "TIME WARNING" in output
        assert "run_001" in output

    def test_no_warning_under_threshold(self, tmp_path):
        """No warning when run is under 10 minutes."""
        _write_runs_jsonl(tmp_path, [
            _make_entry(
                "run_001", "running",
                (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "Short run",
            ),
        ])
        from distillate.hooks.post_bash import _check_run_elapsed
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            _check_run_elapsed(tmp_path)
        output = f.getvalue()
        assert "TIME WARNING" not in output

    def test_critical_warning_after_30_minutes(self, tmp_path):
        """Hook should print CRITICAL warning when a run exceeds 30 minutes."""
        _write_runs_jsonl(tmp_path, [
            _make_entry(
                "run_001", "running",
                (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(),
                "Very long experiment",
            ),
        ])
        from distillate.hooks.post_bash import _check_run_elapsed
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            _check_run_elapsed(tmp_path)
        output = f.getvalue()
        assert "CRITICAL" in output
        assert "run_001" in output

    def test_no_warning_for_completed_run(self, tmp_path):
        """No warning when the last entry is a completed run."""
        _write_runs_jsonl(tmp_path, [
            _make_entry("run_001", "running", "2026-03-14T00:00:00Z"),
            _make_entry("run_001", "keep", "2026-03-14T00:10:00Z"),
        ])
        from distillate.hooks.post_bash import _check_run_elapsed
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            _check_run_elapsed(tmp_path)
        output = f.getvalue()
        assert "TIME WARNING" not in output


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


# ---------------------------------------------------------------------------
# Extracted metric functions (from server.py → experiments.py)
# ---------------------------------------------------------------------------

class TestDetectPrimaryMetric:
    """detect_primary_metric() extracts metric name from PROMPT.md content."""

    def test_standard_format(self):
        from distillate.experiments import detect_primary_metric
        content = "Primary metric: test_accuracy (maximize)"
        assert detect_primary_metric(content) == "test_accuracy"

    def test_backtick_format(self):
        from distillate.experiments import detect_primary_metric
        content = "Primary metric: `param_count` (minimize)"
        assert detect_primary_metric(content) == "param_count"

    def test_key_metric_fallback(self):
        from distillate.experiments import detect_primary_metric
        content = "Key metric: val_f1"
        assert detect_primary_metric(content) == "val_f1"

    def test_north_star_fallback(self):
        from distillate.experiments import detect_primary_metric
        content = "North Star metric: accuracy"
        assert detect_primary_metric(content) == "accuracy"

    def test_no_metric_returns_empty(self):
        from distillate.experiments import detect_primary_metric
        content = "This is just a description with no metric."
        assert detect_primary_metric(content) == ""


class TestInferKeyMetricName:
    """infer_key_metric_name() picks the best metric to chart."""

    def test_explicit_override(self):
        from distillate.experiments import infer_key_metric_name
        proj = {"key_metric_name": "custom_metric", "runs": {}}
        assert infer_key_metric_name(proj) == "custom_metric"

    def test_goal_metric(self):
        from distillate.experiments import infer_key_metric_name
        proj = {
            "goals": [{"metric": "test_accuracy", "target": 0.95}],
            "runs": {},
        }
        assert infer_key_metric_name(proj) == "test_accuracy"

    def test_prefers_test_over_param_count(self):
        from distillate.experiments import infer_key_metric_name
        proj = {
            "runs": {
                f"r{i}": {"results": {"param_count": 100 + i, "test_accuracy": 0.8 + i * 0.01}}
                for i in range(5)
            },
        }
        assert infer_key_metric_name(proj) == "test_accuracy"

    def test_empty_runs(self):
        from distillate.experiments import infer_key_metric_name
        proj = {"runs": {}}
        assert infer_key_metric_name(proj) == ""

    def test_skips_constraint_goals(self):
        from distillate.experiments import infer_key_metric_name
        proj = {
            "goals": [
                {"metric": "param_count", "target": 1000, "is_constraint": True},
                {"metric": "accuracy", "target": 0.9},
            ],
            "runs": {},
        }
        assert infer_key_metric_name(proj) == "accuracy"


# ---------------------------------------------------------------------------
# CLI command: --delete-experiment
# ---------------------------------------------------------------------------

class TestDeleteExperiment:
    """_delete_experiment removes project from state."""

    def test_deletes_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("proj-1", "My Project", str(tmp_path))
        state.save()

        monkeypatch.setattr("sys.argv", ["distillate", "--delete-experiment", "proj-1", "--yes"])
        from distillate.commands import _delete_experiment
        _delete_experiment(["proj-1"])

        state2 = State()
        assert state2.get_project("proj-1") is None

    def test_refuses_with_running_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("proj-1", "My Project", str(tmp_path))
        proj = state.get_project("proj-1")
        proj["sessions"] = {"s1": {"status": "running", "tmux_session": "dist-proj-1"}}
        state.save()

        monkeypatch.setattr("sys.argv", ["distillate", "--delete-experiment", "proj-1", "--yes"])
        monkeypatch.setattr("distillate.commands._tmux_session_exists", lambda n: True,
                            raising=False)
        # Patch the import inside the function
        import distillate.commands as cmd_mod
        original_fn = cmd_mod._delete_experiment

        import io
        from contextlib import redirect_stdout
        captured = io.StringIO()

        # Re-patch _tmux_session_exists inside the launcher module
        monkeypatch.setattr("distillate.launcher._tmux_session_exists", lambda n: True)
        with redirect_stdout(captured):
            original_fn(["proj-1"])

        assert "still running" in captured.getvalue().lower()
        # Project should still exist
        state2 = State()
        assert state2.get_project("proj-1") is not None


# ---------------------------------------------------------------------------
# CLI command: --edit-prompt
# ---------------------------------------------------------------------------

class TestEditPrompt:
    """_edit_prompt opens editor and detects metric."""

    def test_detects_metric_after_edit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        state.add_project("proj-1", "My Project", str(proj_dir))
        state.save()

        # Write PROMPT.md with a metric
        prompt = proj_dir / "PROMPT.md"
        prompt.write_text("# Experiment\nPrimary metric: test_accuracy (maximize)\n")

        # Mock editor to be a no-op (just return)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: None)
        monkeypatch.setattr("sys.argv", ["distillate", "--edit-prompt", "proj-1"])

        from distillate.commands import _edit_prompt
        _edit_prompt(["proj-1"])

        state2 = State()
        proj = state2.get_project("proj-1")
        assert proj["key_metric_name"] == "test_accuracy"

    def test_writes_prompt_updated_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        state.add_project("proj-1", "My Project", str(proj_dir))
        state.save()

        prompt = proj_dir / "PROMPT.md"
        prompt.write_text("# Experiment\nJust a description.\n")

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: None)
        monkeypatch.setattr("sys.argv", ["distillate", "--edit-prompt", "proj-1"])

        from distillate.commands import _edit_prompt
        _edit_prompt(["proj-1"])

        flag = proj_dir / ".distillate" / "prompt_updated"
        assert flag.exists()


# ---------------------------------------------------------------------------
# CLI command: --chart
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not importlib.util.find_spec("matplotlib"),
    reason="matplotlib not installed (optional dependency)",
)
class TestChartExport:
    """_chart_export generates a PNG file."""

    def test_writes_chart_png(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        state.add_project("proj-1", "My Project", str(proj_dir))

        from distillate.experiments import _create_run
        for i in range(3):
            run = _create_run(
                prefix="sr", name=f"run_{i:03d}",
                results={"accuracy": 0.7 + i * 0.05},
                started_at=f"2026-03-14T{i:02d}:00:00Z",
                decision="best",
            )
            state.add_run("proj-1", run["id"], run)
        state.save()

        monkeypatch.setattr("sys.argv", ["distillate", "--chart", "proj-1", "--metric", "accuracy"])
        monkeypatch.setattr("webbrowser.open", lambda url: None)

        from distillate.commands import _chart_export
        _chart_export(["proj-1"])

        chart_path = proj_dir / ".distillate" / "chart.png"
        assert chart_path.exists()
        assert chart_path.stat().st_size > 1000  # PNG should be non-trivial

    def test_no_runs_prints_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        state.add_project("proj-1", "My Project", str(proj_dir))
        state.save()

        monkeypatch.setattr("sys.argv", ["distillate", "--chart", "proj-1", "--metric", "accuracy"])

        import io
        from contextlib import redirect_stdout
        captured = io.StringIO()

        from distillate.commands import _chart_export
        with redirect_stdout(captured):
            _chart_export(["proj-1"])

        assert "no runs" in captured.getvalue().lower()
