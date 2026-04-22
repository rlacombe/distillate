# Covers: distillate/experiments.py

"""Tests for runs.jsonl parsing, events.jsonl parsing, dual-source ingestion, and file watching."""

import json


# ---------------------------------------------------------------------------
# Structured reporting (runs.jsonl) tests
# ---------------------------------------------------------------------------


class TestRunsJsonlParsing:
    """Test parsing of .distillate/runs.jsonl structured reports."""

    def test_parse_empty_file(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        (distillate_dir / "runs.jsonl").write_text("", encoding="utf-8")
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert runs == []

    def test_parse_no_file(self, tmp_path):
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert runs == []

    def test_parse_single_keep(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        entry = json.dumps({
            "$schema": "distillate/run/v1",
            "id": "run_001",
            "timestamp": "2026-03-09T04:23:17Z",
            "status": "keep",
            "hypothesis": "Larger d_model improves val_bpb",
            "hyperparameters": {"d_model": 128, "lr": 0.003},
            "results": {"val_bpb": 0.912},
            "reasoning": "val_bpb improved from 0.934 to 0.912",
            "duration_seconds": 312,
        })
        (distillate_dir / "runs.jsonl").write_text(entry + "\n", encoding="utf-8")
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 1
        run = runs[0]
        assert run["id"].startswith("sr-")
        assert run["decision"] == "completed"
        assert run["status"] == "completed"
        assert run["source"] == "structured"
        assert run["hyperparameters"]["d_model"] == 128
        assert run["results"]["val_bpb"] == 0.912
        assert run["agent_reasoning"] == "val_bpb improved from 0.934 to 0.912"
        assert run["hypothesis"] == "Larger d_model improves val_bpb"
        assert run["duration_minutes"] == 5  # 312s / 60 -> 5

    def test_parse_crash_status(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        entry = json.dumps({
            "$schema": "distillate/run/v1",
            "id": "run_002",
            "timestamp": "2026-03-09T05:00:00Z",
            "status": "crash",
            "hypothesis": "Very small model",
            "results": {},
        })
        (distillate_dir / "runs.jsonl").write_text(entry + "\n", encoding="utf-8")
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 1
        assert runs[0]["decision"] == "crash"
        assert runs[0]["status"] == "failed"

    def test_parse_running_status(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        entry = json.dumps({
            "$schema": "distillate/run/v1",
            "id": "run_003",
            "timestamp": "2026-03-09T05:00:00Z",
            "status": "running",
            "hypothesis": "Testing",
            "results": {},
        })
        (distillate_dir / "runs.jsonl").write_text(entry + "\n", encoding="utf-8")
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 1
        assert runs[0]["decision"] == "running"
        assert runs[0]["status"] == "running"

    def test_skips_invalid_schema(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        lines = [
            json.dumps({"$schema": "distillate/run/v1", "id": "run_001",
                        "timestamp": "2026-03-09T04:00:00Z", "status": "keep",
                        "hypothesis": "a", "results": {"x": 1}}),
            json.dumps({"$schema": "wrong", "id": "run_002",
                        "timestamp": "2026-03-09T05:00:00Z", "status": "keep",
                        "hypothesis": "b", "results": {"x": 2}}),
            "not json at all",
            json.dumps({"$schema": "distillate/run/v1", "id": "run_003",
                        "timestamp": "2026-03-09T06:00:00Z", "status": "discard",
                        "hypothesis": "c", "results": {"x": 3}}),
        ]
        (distillate_dir / "runs.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 2

    def test_skips_entry_without_id(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        entry = json.dumps({
            "$schema": "distillate/run/v1",
            "timestamp": "2026-03-09T04:00:00Z",
            "status": "keep",
            "hypothesis": "a",
            "results": {"x": 1},
        })
        (distillate_dir / "runs.jsonl").write_text(entry + "\n", encoding="utf-8")
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert runs == []

    def test_multiple_runs(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        lines = []
        for i in range(5):
            lines.append(json.dumps({
                "$schema": "distillate/run/v1",
                "id": f"run_{i:03d}",
                "timestamp": f"2026-03-09T0{i}:00:00Z",
                "status": "keep" if i % 2 == 0 else "discard",
                "hypothesis": f"Test {i}",
                "results": {"metric": i * 0.1},
            }))
        (distillate_dir / "runs.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        from distillate.experiments import _parse_runs_jsonl
        runs = _parse_runs_jsonl(tmp_path)
        assert len(runs) == 5
        assert sum(1 for r in runs if r["decision"] == "completed") == 5


# ---------------------------------------------------------------------------
# Hook event parsing tests
# ---------------------------------------------------------------------------


class TestEventsJsonlParsing:
    """Test parsing of .distillate/events.jsonl hook events."""

    def test_parse_empty_file(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        (distillate_dir / "events.jsonl").write_text("", encoding="utf-8")
        from distillate.experiments import _parse_events_jsonl
        runs = _parse_events_jsonl(tmp_path)
        assert runs == []

    def test_parse_no_file(self, tmp_path):
        from distillate.experiments import _parse_events_jsonl
        runs = _parse_events_jsonl(tmp_path)
        assert runs == []

    def test_parse_run_completed(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        event = json.dumps({
            "type": "run_completed",
            "ts": "2026-03-09T04:23:17Z",
            "command": "python3 train.py d_model=64 lr=0.001",
            "hyperparameters": {"d_model": 64, "lr": 0.001},
            "results": {"loss": 0.045, "accuracy": 0.99},
            "session_id": "abc12345",
        })
        (distillate_dir / "events.jsonl").write_text(event + "\n", encoding="utf-8")
        from distillate.experiments import _parse_events_jsonl
        runs = _parse_events_jsonl(tmp_path)
        assert len(runs) == 1
        run = runs[0]
        assert run["source"] == "hooks"
        assert run["hyperparameters"]["d_model"] == 64
        assert run["results"]["loss"] == 0.045
        assert run["command"] == "python3 train.py d_model=64 lr=0.001"

    def test_skips_session_end(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        lines = [
            json.dumps({"type": "run_completed", "ts": "2026-03-09T04:00:00Z",
                        "command": "python3 train.py", "hyperparameters": {"lr": 0.01},
                        "results": {"loss": 0.1}, "session_id": "abc"}),
            json.dumps({"type": "session_end", "ts": "2026-03-09T05:00:00Z",
                        "session_id": "abc", "stop_reason": "user"}),
        ]
        (distillate_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        from distillate.experiments import _parse_events_jsonl
        runs = _parse_events_jsonl(tmp_path)
        assert len(runs) == 1

    def test_skips_empty_metrics_and_hp(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        event = json.dumps({
            "type": "run_completed",
            "ts": "2026-03-09T04:00:00Z",
            "command": "echo hello",
            "hyperparameters": {},
            "results": {},
            "session_id": "abc",
        })
        (distillate_dir / "events.jsonl").write_text(event + "\n", encoding="utf-8")
        from distillate.experiments import _parse_events_jsonl
        runs = _parse_events_jsonl(tmp_path)
        assert runs == []


# ---------------------------------------------------------------------------
# Dual-source ingestion tests
# ---------------------------------------------------------------------------


class TestIngestRuns:
    """Test dual-source ingestion (structured + hooks)."""

    def test_empty_project(self, tmp_path):
        from distillate.experiments import ingest_runs
        runs = ingest_runs(tmp_path)
        assert runs == []

    def test_structured_only(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        entry = json.dumps({
            "$schema": "distillate/run/v1",
            "id": "run_001",
            "timestamp": "2026-03-09T04:00:00Z",
            "status": "keep",
            "hypothesis": "Test",
            "results": {"val_bpb": 0.912},
        })
        (distillate_dir / "runs.jsonl").write_text(entry + "\n", encoding="utf-8")
        from distillate.experiments import ingest_runs
        runs = ingest_runs(tmp_path)
        assert len(runs) == 1
        assert runs[0]["source"] == "structured"

    def test_hooks_only(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        event = json.dumps({
            "type": "run_completed",
            "ts": "2026-03-09T04:00:00Z",
            "command": "python3 train.py d_model=64",
            "hyperparameters": {"d_model": 64},
            "results": {"loss": 0.01},
            "session_id": "abc",
        })
        (distillate_dir / "events.jsonl").write_text(event + "\n", encoding="utf-8")
        from distillate.experiments import ingest_runs
        runs = ingest_runs(tmp_path)
        assert len(runs) == 1
        assert runs[0]["source"] == "hooks"

    def test_merge_by_fingerprint(self, tmp_path):
        """When both sources report same hyperparams, structured wins."""
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        structured = json.dumps({
            "$schema": "distillate/run/v1",
            "id": "run_001",
            "timestamp": "2026-03-09T04:00:00Z",
            "status": "keep",
            "hypothesis": "Test",
            "hyperparameters": {"d_model": 64, "lr": 0.001},
            "results": {"val_bpb": 0.912},
        })
        (distillate_dir / "runs.jsonl").write_text(structured + "\n", encoding="utf-8")
        # Hook event in a different minute so timestamp dedup doesn't skip it
        hook = json.dumps({
            "type": "run_completed",
            "ts": "2026-03-09T04:05:00Z",
            "command": "python3 train.py d_model=64 lr=0.001",
            "hyperparameters": {"d_model": 64, "lr": 0.001},
            "results": {"loss": 0.045},
            "session_id": "abc",
        })
        (distillate_dir / "events.jsonl").write_text(hook + "\n", encoding="utf-8")
        from distillate.experiments import ingest_runs
        runs = ingest_runs(tmp_path)
        # Should merge into one run (structured wins)
        assert len(runs) == 1
        assert runs[0]["source"] == "structured"
        # Hook command should be merged in
        assert runs[0].get("command") == "python3 train.py d_model=64 lr=0.001"

    def test_no_merge_different_hp(self, tmp_path):
        """Different hyperparams → two separate runs."""
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        structured = json.dumps({
            "$schema": "distillate/run/v1",
            "id": "run_001",
            "timestamp": "2026-03-09T04:00:00Z",
            "status": "keep",
            "hypothesis": "Test",
            "hyperparameters": {"d_model": 64},
            "results": {"val_bpb": 0.912},
        })
        (distillate_dir / "runs.jsonl").write_text(structured + "\n", encoding="utf-8")
        hook = json.dumps({
            "type": "run_completed",
            "ts": "2026-03-09T04:30:00Z",
            "command": "python3 train.py d_model=128",
            "hyperparameters": {"d_model": 128},
            "results": {"loss": 0.01},
            "session_id": "abc",
        })
        (distillate_dir / "events.jsonl").write_text(hook + "\n", encoding="utf-8")
        from distillate.experiments import ingest_runs
        runs = ingest_runs(tmp_path)
        assert len(runs) == 2


# ---------------------------------------------------------------------------
# Watch artifacts tests
# ---------------------------------------------------------------------------


class TestWatchArtifacts:
    """Test file watching for experiment changes."""

    def test_watch_empty_project(self, tmp_path):
        from distillate.experiments import watch_experiment_artifacts
        changes = watch_experiment_artifacts(tmp_path)
        # No .distillate dir → artifacts_changed (first scan)
        assert any(c.get("type") == "artifacts_changed" for c in changes)

    def test_watch_detects_new_jsonl_lines(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()

        # Write initial event
        events_file = distillate_dir / "events.jsonl"
        event1 = json.dumps({"type": "run_completed", "ts": "2026-03-09T04:00:00Z",
                            "command": "python3 train.py", "hyperparameters": {"lr": 0.01},
                            "results": {"loss": 0.1}, "session_id": "abc"})
        events_file.write_text(event1 + "\n", encoding="utf-8")

        from distillate.experiments import watch_experiment_artifacts

        # First watch — picks up everything
        changes = watch_experiment_artifacts(tmp_path)
        event_changes = [c for c in changes if c.get("_source_file") == "events.jsonl"]
        assert len(event_changes) == 1

        # No changes → empty
        changes = watch_experiment_artifacts(tmp_path)
        event_changes = [c for c in changes if c.get("_source_file") == "events.jsonl"]
        assert len(event_changes) == 0

        # Append new event
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "run_completed", "ts": "2026-03-09T05:00:00Z",
                               "command": "python3 train.py lr=0.02",
                               "hyperparameters": {"lr": 0.02},
                               "results": {"loss": 0.05}, "session_id": "def"}) + "\n")

        # Should detect the new line
        changes = watch_experiment_artifacts(tmp_path)
        event_changes = [c for c in changes if c.get("_source_file") == "events.jsonl"]
        assert len(event_changes) == 1
