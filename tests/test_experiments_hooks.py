# Covers: distillate/hooks/post_bash.py, distillate/hooks/on_stop.py, distillate/main.py, distillate/experiments.py

"""Tests for post-bash hook, on-stop hook, install-hooks CLI, and scan-with-ingestion integration."""

import json


# ---------------------------------------------------------------------------
# Hook parsing tests
# ---------------------------------------------------------------------------


class TestPostBashHook:
    """Test the post_bash hook's detection and extraction logic."""

    def test_is_training_command(self):
        from distillate.hooks.post_bash import _is_training_command
        assert _is_training_command("python3 train.py d_model=64 lr=0.001")
        assert _is_training_command("python train_model.py")
        assert _is_training_command("python3 experiment/train.py")
        assert _is_training_command("python3 run_experiment.py")
        assert not _is_training_command("python3 plot.py")
        assert not _is_training_command("ls -la")
        assert not _is_training_command("cat train.py")  # no python invocation

    def test_is_training_command_ml_keywords(self):
        from distillate.hooks.post_bash import _is_training_command
        assert _is_training_command("python3 main.py --epochs 10 --lr 0.001")
        assert _is_training_command("python3 run.py --loss cross_entropy --batch 32")

    def test_extract_hyperparams(self):
        from distillate.hooks.post_bash import _extract_hyperparams
        hp = _extract_hyperparams("python3 train.py d_model=64 lr=0.001 epochs=10")
        assert hp == {"d_model": 64, "lr": 0.001, "epochs": 10}

    def test_extract_metrics(self):
        from distillate.hooks.post_bash import _extract_metrics
        text = "Epoch 10: loss=0.045, accuracy=0.99, val_bpb=0.912"
        metrics = _extract_metrics(text)
        assert metrics["loss"] == 0.045
        assert metrics["accuracy"] == 0.99
        assert metrics["val_bpb"] == 0.912

    def test_extract_config_block(self):
        from distillate.hooks.post_bash import _extract_config_block
        text = 'Config: {"d_model": 64, "lr": 0.001, "layers": 4}'
        config = _extract_config_block(text)
        assert config == {"d_model": 64, "lr": 0.001, "layers": 4}

    def test_extract_config_block_no_match(self):
        from distillate.hooks.post_bash import _extract_config_block
        assert _extract_config_block("no config here") == {}

    def test_coerce_values(self):
        from distillate.hooks.post_bash import _coerce
        assert _coerce("42") == 42
        assert _coerce("0.001") == 0.001
        assert _coerce("True") is True
        assert _coerce("false") is False
        assert _coerce("3e-4") == 3e-4

    def test_find_project_root(self, tmp_path, monkeypatch):
        # Create a .git directory
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        from distillate.hooks.post_bash import _find_project_root
        root = _find_project_root()
        assert root == tmp_path

    def test_find_project_root_distillate_dir(self, tmp_path, monkeypatch):
        (tmp_path / ".distillate").mkdir()
        monkeypatch.chdir(tmp_path)
        from distillate.hooks.post_bash import _find_project_root
        root = _find_project_root()
        assert root == tmp_path

    def test_append_event(self, tmp_path):
        from distillate.hooks.post_bash import _append_event
        _append_event(tmp_path, {"type": "test", "ts": "2026-03-09T04:00:00Z"})
        events_file = tmp_path / ".distillate" / "events.jsonl"
        assert events_file.exists()
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["type"] == "test"

        # Append another
        _append_event(tmp_path, {"type": "test2", "ts": "2026-03-09T05:00:00Z"})
        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2


class TestOnStopHook:
    """Test the on_stop hook."""

    def test_find_project_root(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        from distillate.hooks.on_stop import _find_project_root
        root = _find_project_root()
        assert root == tmp_path

    def test_append_event(self, tmp_path):
        from distillate.hooks.on_stop import _append_event
        _append_event(tmp_path, {"type": "session_end", "ts": "2026-03-09T04:00:00Z"})
        events_file = tmp_path / ".distillate" / "events.jsonl"
        assert events_file.exists()
        data = json.loads(events_file.read_text(encoding="utf-8").strip())
        assert data["type"] == "session_end"


# ---------------------------------------------------------------------------
# Install hooks CLI test
# ---------------------------------------------------------------------------


class TestInstallHooks:
    """Test the --install-hooks CLI command."""

    def test_install_creates_distillate_dir(self, tmp_path):
        from distillate.main import _install_hooks
        _install_hooks([str(tmp_path)])
        assert (tmp_path / ".distillate").is_dir()

    def test_install_creates_claude_settings(self, tmp_path):
        from distillate.main import _install_hooks
        _install_hooks([str(tmp_path)])
        settings_file = tmp_path / ".claude" / "settings.json"
        assert settings_file.exists()
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert "hooks" in settings
        assert "PostToolUse" in settings["hooks"]
        assert "Stop" in settings["hooks"]

    def test_install_merges_existing_settings(self, tmp_path):
        # Create existing settings
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {"permissions": {"allow": ["Bash"]},
                    "hooks": {"PostToolUse": [{"matcher": "Bash", "command": "echo test"}]}}
        (claude_dir / "settings.json").write_text(
            json.dumps(existing), encoding="utf-8")

        from distillate.main import _install_hooks
        _install_hooks([str(tmp_path)])

        settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
        # Should have both the original and new hooks
        assert len(settings["hooks"]["PostToolUse"]) == 2
        assert settings["permissions"] == {"allow": ["Bash"]}

    def test_install_idempotent(self, tmp_path):
        from distillate.main import _install_hooks
        _install_hooks([str(tmp_path)])
        _install_hooks([str(tmp_path)])  # second time
        settings = json.loads(
            (tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
        # Should not duplicate hooks
        assert len(settings["hooks"]["PostToolUse"]) == 1


# ---------------------------------------------------------------------------
# Scan project integration with ingestion
# ---------------------------------------------------------------------------


class TestScanProjectWithIngestion:
    """Test that scan_experiment() integrates structured + hook runs."""

    def test_scan_picks_up_runs_jsonl(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        entry = json.dumps({
            "$schema": "distillate/run/v1",
            "id": "run_001",
            "timestamp": "2026-03-09T04:00:00Z",
            "status": "keep",
            "hypothesis": "Test structured",
            "hyperparameters": {"d_model": 64},
            "results": {"accuracy": 0.99},
        })
        (distillate_dir / "runs.jsonl").write_text(entry + "\n", encoding="utf-8")

        from distillate.experiments import scan_experiment
        result = scan_experiment(tmp_path)
        assert "error" not in result
        # Should find the structured run
        runs = result["runs"]
        structured_runs = [r for r in runs.values() if r.get("source") == "structured"]
        assert len(structured_runs) == 1
        assert structured_runs[0]["decision"] == "completed"

    def test_scan_picks_up_events_jsonl(self, tmp_path):
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        event = json.dumps({
            "type": "run_completed",
            "ts": "2026-03-09T04:00:00Z",
            "command": "python3 train.py d_model=32",
            "hyperparameters": {"d_model": 32},
            "results": {"loss": 0.01},
            "session_id": "abc",
        })
        (distillate_dir / "events.jsonl").write_text(event + "\n", encoding="utf-8")

        from distillate.experiments import scan_experiment
        result = scan_experiment(tmp_path)
        assert "error" not in result
        runs = result["runs"]
        hook_runs = [r for r in runs.values() if r.get("source") == "hooks"]
        assert len(hook_runs) == 1

    def test_structured_run_deduplicates_artifact_scanned(self, tmp_path):
        """Structured runs from runs.jsonl should replace artifact-scanned
        duplicates that share the same hyperparameters (double-curve bug).

        Without dedup, scan_experiment() returns two runs for the same experiment
        — one from artifact scanning (hash-based ID) and one from runs.jsonl
        (sr- prefixed ID) — causing a double curve on the chart.
        """
        # Create an artifact that scan_experiment will discover
        results_dir = tmp_path / "experiment"
        results_dir.mkdir()
        (results_dir / "train_v1.json").write_text(json.dumps({
            "config": {"d_model": 64, "n_heads": 2, "lr": 0.001},
            "total_time": 3600,
            "best_val_acc": 0.85,
            "epochs": [
                {"epoch": 1, "loss": 1.0, "val_acc": 0.5},
                {"epoch": 10, "loss": 0.2, "val_acc": 0.85},
            ],
        }))

        # Create a structured run with the SAME hyperparameters
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        entry = json.dumps({
            "$schema": "distillate/run/v1",
            "id": "run_001",
            "timestamp": "2026-03-09T04:00:00Z",
            "status": "keep",
            "hypothesis": "Test dedup",
            "hyperparameters": {"d_model": 64, "n_heads": 2, "lr": 0.001},
            "results": {"val_acc": 0.85},
        })
        (distillate_dir / "runs.jsonl").write_text(entry + "\n", encoding="utf-8")

        from distillate.experiments import scan_experiment
        result = scan_experiment(tmp_path)
        assert "error" not in result

        runs = result["runs"]
        # Should have exactly ONE run, not two
        runs_with_hp = [
            r for r in runs.values()
            if r.get("hyperparameters", {}).get("d_model") == 64
        ]
        assert len(runs_with_hp) == 1, (
            f"Expected 1 run with d_model=64 but got {len(runs_with_hp)}: "
            f"{[r['id'] for r in runs_with_hp]}"
        )
        # The surviving run should be the structured one
        assert runs_with_hp[0]["source"] == "structured"
        assert runs_with_hp[0]["name"] == "run_001"

    def test_structured_runs_with_same_hyperparameters_all_kept(self, tmp_path):
        """Multiple structured runs sharing identical hyperparameters must all
        survive — the dedup should only remove artifact-scanned duplicates,
        never other structured runs."""
        distillate_dir = tmp_path / ".distillate"
        distillate_dir.mkdir()
        lines = []
        for i in range(1, 4):
            lines.append(json.dumps({
                "$schema": "distillate/run/v1",
                "id": f"run_{i:03d}",
                "timestamp": f"2026-03-09T0{i}:00:00Z",
                "status": "keep" if i % 2 else "discard",
                "hyperparameters": {"d_model": 64, "n_heads": 2, "lr": 0.001},
                "results": {"accuracy": 0.5 + i * 0.1},
            }))
        (distillate_dir / "runs.jsonl").write_text("\n".join(lines) + "\n",
                                                   encoding="utf-8")

        from distillate.experiments import scan_experiment
        result = scan_experiment(tmp_path)
        assert "error" not in result

        structured = [r for r in result["runs"].values()
                      if r.get("source") == "structured"]
        assert len(structured) == 3, (
            f"Expected 3 structured runs but got {len(structured)}: "
            f"{[r['name'] for r in structured]}"
        )
