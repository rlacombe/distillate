# Covers: distillate/experiments.py (classify_metric, detect_primary_metric, infer_key_metric_name)
"""Tests for experiment metric inference, key metric selection, and total-time calculation.

Covers bugs found during overnight run audit (2026-03-14):
  BUG 5: experiment_total_secs gap-based calculation
  BUG 6: key metric selection (param_count vs test_accuracy)
"""

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper: write runs.jsonl
# ---------------------------------------------------------------------------

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
        state.add_experiment("proj", "Test", str(tmp_path))

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

        proj = state.get_experiment("proj")
        from distillate.experiments import classify_metric
        pc_cat = classify_metric("param_count")
        ta_cat = classify_metric("test_accuracy")
        # param_count is "count" category, test_accuracy is "ratio" category
        assert pc_cat == "count"
        assert ta_cat == "ratio"

        # Reproduce the _infer_key_metric_name scoring logic with the fix
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
