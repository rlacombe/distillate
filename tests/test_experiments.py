"""Tests for the experiments (ML project tracking) feature."""

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# State CRUD tests
# ---------------------------------------------------------------------------


class TestStateProjects:
    """Test project/run CRUD methods on State."""

    def test_default_state_has_projects_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        assert state.projects == {}

    def test_add_and_get_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("my-project", "My Project", "/tmp/my-project")
        assert state.has_project("my-project")
        proj = state.get_project("my-project")
        assert proj["name"] == "My Project"
        assert proj["path"] == "/tmp/my-project"
        assert proj["status"] == "tracking"
        assert proj["runs"] == {}
        assert proj["notebook_sections"] == ["main"]
        assert proj["linked_papers"] == []
        assert proj["added_at"]  # should be set

    def test_project_index_of(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("proj-a", "A", "/a")
        state.add_project("proj-b", "B", "/b")
        assert state.project_index_of("proj-a") == 1
        assert state.project_index_of("proj-b") == 2
        assert state.project_index_of("nonexistent") == 0

    def test_find_project_by_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("tiny-gene-code", "Tiny Gene Code", "/path")
        assert state.find_project("tiny-gene-code")["name"] == "Tiny Gene Code"

    def test_find_project_by_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("proj-a", "A", "/a")
        assert state.find_project("1")["name"] == "A"

    def test_find_project_by_name_substring(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("tiny-gene-code", "Tiny Gene Code", "/path")
        assert state.find_project("gene")["name"] == "Tiny Gene Code"
        assert state.find_project("GENE")["name"] == "Tiny Gene Code"

    def test_find_project_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        assert state.find_project("nonexistent") is None
        assert state.find_project("") is None

    def test_update_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("my-proj", "My Proj", "/path")
        state.update_project("my-proj", description="A cool project", status="archived")
        proj = state.get_project("my-proj")
        assert proj["description"] == "A cool project"
        assert proj["status"] == "archived"

    def test_remove_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("my-proj", "My Proj", "/path")
        assert state.remove_project("my-proj") is True
        assert state.has_project("my-proj") is False
        assert state.remove_project("nonexistent") is False

    def test_add_and_get_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("my-proj", "My Proj", "/path")
        run_data = {
            "id": "exp-abc123",
            "name": "Baseline",
            "status": "completed",
            "hyperparameters": {"lr": 0.001},
            "results": {"accuracy": 0.95},
        }
        state.add_run("my-proj", "exp-abc123", run_data)
        run = state.get_run("my-proj", "exp-abc123")
        assert run["name"] == "Baseline"
        assert run["results"]["accuracy"] == 0.95

    def test_update_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("my-proj", "My Proj", "/path")
        state.add_run("my-proj", "exp-1", {"id": "exp-1", "status": "running"})
        state.update_run("my-proj", "exp-1", status="completed")
        assert state.get_run("my-proj", "exp-1")["status"] == "completed"

    def test_get_run_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        assert state.get_run("no-proj", "no-run") is None
        state.add_project("my-proj", "My Proj", "/path")
        assert state.get_run("my-proj", "no-run") is None

    def test_existing_state_without_projects_key(self, tmp_path, monkeypatch):
        """state.json from before experiments feature loads cleanly."""
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({
            "zotero_library_version": 42,
            "last_poll_timestamp": None,
            "documents": {"ABC": {"title": "Test"}},
            "promoted_papers": [],
        }))
        monkeypatch.setattr("distillate.state.STATE_PATH", state_path)
        from distillate.state import State
        state = State()
        # Should have documents from old state
        assert "ABC" in state.documents
        # projects should default to empty dict
        assert state.projects == {}
        # Should be able to add projects
        state.add_project("test", "Test", "/path")
        assert state.has_project("test")

    def test_save_and_reload_projects(self, tmp_path, monkeypatch):
        state_path = tmp_path / "state.json"
        monkeypatch.setattr("distillate.state.STATE_PATH", state_path)
        from distillate.state import State
        state = State()
        state.add_project("my-proj", "My Proj", "/path")
        state.add_run("my-proj", "exp-1", {"id": "exp-1", "name": "Run 1"})
        state.save()

        state2 = State()
        assert state2.has_project("my-proj")
        assert state2.get_run("my-proj", "exp-1")["name"] == "Run 1"


# ---------------------------------------------------------------------------
# File classification tests
# ---------------------------------------------------------------------------


class TestFileClassification:
    def test_classify_training_log(self):
        from distillate.experiments import _classify_json
        data = {
            "config": {"lr": 0.001, "batch_size": 32, "epochs": 10},
            "epochs": [{"loss": 1.0}, {"loss": 0.5}],
        }
        assert _classify_json(data) == "training_log"

    def test_classify_training_history(self):
        from distillate.experiments import _classify_json
        data = {
            "epoch": [1, 2, 3, 4, 5],
            "loss": [1.0, 0.8, 0.6, 0.4, 0.2],
            "accuracy": [0.5, 0.6, 0.7, 0.8, 0.9],
        }
        assert _classify_json(data) == "training_history"

    def test_classify_result_file(self):
        from distillate.experiments import _classify_json
        data = {"accuracy": 0.95, "f1": 0.93, "precision": 0.94, "recall": 0.92}
        assert _classify_json(data) == "results"

    def test_classify_config_file(self):
        from distillate.experiments import _classify_json
        data = {"hidden_dim": 64, "num_layers": 3, "lr": 0.001, "batch_size": 32}
        assert _classify_json(data) == "config"

    def test_classify_other(self):
        from distillate.experiments import _classify_json
        assert _classify_json({"name": "hello", "version": "1.0"}) == "other"
        assert _classify_json({}) == "other"

    def test_classify_non_dict(self):
        from distillate.experiments import _classify_json
        assert _classify_json([1, 2, 3]) == "other"


# ---------------------------------------------------------------------------
# Model tag extraction tests
# ---------------------------------------------------------------------------


class TestModelTagExtraction:
    def test_full_tag(self):
        from distillate.experiments import _extract_model_tag
        assert _extract_model_tag("train_d8_h1_ff16_L1.json") == "d8_h1_ff16_L1"

    def test_version_tag(self):
        from distillate.experiments import _extract_model_tag
        assert _extract_model_tag("results_v3.json") == "v3"

    def test_final_tag(self):
        from distillate.experiments import _extract_model_tag
        assert _extract_model_tag("final_results.json") == "final"

    def test_no_tag(self):
        from distillate.experiments import _extract_model_tag
        assert _extract_model_tag("data.json") == ""

    def test_tag_from_config(self):
        from distillate.experiments import _tag_from_config
        assert _tag_from_config({"d_model": 64, "n_heads": 4}) == "d64_h4"
        assert _tag_from_config({"d_model": 8, "n_heads": 1, "d_ff": 16, "n_layers": 1}) == "d8_h1_ff16_L1"
        assert _tag_from_config({"lr": 0.001}) == ""


# ---------------------------------------------------------------------------
# Run diffing tests
# ---------------------------------------------------------------------------


class TestRunDiffing:
    def test_diff_param_changes(self):
        from distillate.experiments import diff_runs
        run_a = {
            "name": "v1",
            "hyperparameters": {"lr": 0.001, "batch_size": 32},
            "results": {},
        }
        run_b = {
            "name": "v2",
            "hyperparameters": {"lr": 0.01, "batch_size": 64},
            "results": {},
        }
        d = diff_runs(run_a, run_b)
        assert d["run_a"] == "v1"
        assert d["run_b"] == "v2"
        assert len(d["param_diffs"]) == 2
        lr_diff = next(p for p in d["param_diffs"] if p["key"] == "lr")
        assert lr_diff["old"] == 0.001
        assert lr_diff["new"] == 0.01

    def test_diff_metric_direction(self):
        from distillate.experiments import diff_runs
        run_a = {"name": "v1", "hyperparameters": {},
                 "results": {"accuracy": 0.8, "loss": 0.5}}
        run_b = {"name": "v2", "hyperparameters": {},
                 "results": {"accuracy": 0.9, "loss": 0.3}}
        d = diff_runs(run_a, run_b)
        acc_diff = next(m for m in d["metric_diffs"] if m["key"] == "accuracy")
        loss_diff = next(m for m in d["metric_diffs"] if m["key"] == "loss")
        assert acc_diff["improved"] is True   # accuracy up = good
        assert loss_diff["improved"] is True  # loss down = good

    def test_diff_metric_regression(self):
        from distillate.experiments import diff_runs
        run_a = {"name": "v1", "hyperparameters": {},
                 "results": {"accuracy": 0.9}}
        run_b = {"name": "v2", "hyperparameters": {},
                 "results": {"accuracy": 0.7}}
        d = diff_runs(run_a, run_b)
        acc_diff = next(m for m in d["metric_diffs"] if m["key"] == "accuracy")
        assert acc_diff["improved"] is False

    def test_diff_added_removed_params(self):
        from distillate.experiments import diff_runs
        run_a = {"name": "v1", "hyperparameters": {"lr": 0.001},
                 "results": {}}
        run_b = {"name": "v2", "hyperparameters": {"batch_size": 64},
                 "results": {}}
        d = diff_runs(run_a, run_b)
        assert any(p["change"] == "removed" for p in d["param_diffs"])
        assert any(p["change"] == "added" for p in d["param_diffs"])

    def test_diff_no_changes(self):
        from distillate.experiments import diff_runs
        run = {"name": "v1", "hyperparameters": {"lr": 0.001},
               "results": {"accuracy": 0.9}}
        d = diff_runs(run, run)
        assert d["param_diffs"] == []
        assert d["metric_diffs"] == []


# ---------------------------------------------------------------------------
# Notebook generation tests
# ---------------------------------------------------------------------------


class TestNotebookGeneration:
    def test_basic_notebook(self):
        from distillate.experiments import generate_notebook
        project = {
            "name": "Test Project",
            "path": "/tmp/test",
            "description": "A test project",
            "goals": [],
            "linked_papers": [],
            "runs": {
                "exp-1": {
                    "id": "exp-1",
                    "name": "Baseline",
                    "status": "completed",
                    "hypothesis": "",
                    "hyperparameters": {"lr": 0.001, "batch_size": 32},
                    "results": {"accuracy": 0.85, "loss": 0.4},
                    "tags": ["baseline"],
                    "git_commits": [],
                    "files_created": [],
                    "started_at": "2026-02-01T10:00:00",
                    "completed_at": "2026-02-01T12:00:00",
                    "duration_minutes": 120,
                    "notes": [],
                },
            },
        }
        md = generate_notebook(project)
        assert "# Test Project" in md
        assert "Baseline" in md
        assert "| lr | `0.001` |" in md
        assert "accuracy" in md
        assert "[x]" in md  # completed status

    def test_notebook_with_diffs(self):
        from distillate.experiments import generate_notebook
        project = {
            "name": "Diff Test",
            "path": "/tmp/test",
            "description": "",
            "goals": [],
            "linked_papers": [],
            "runs": {
                "exp-1": {
                    "id": "exp-1", "name": "v1", "status": "completed",
                    "hypothesis": "", "hyperparameters": {"lr": 0.001},
                    "results": {"accuracy": 0.8},
                    "tags": [], "git_commits": [], "files_created": [],
                    "started_at": "2026-02-01T10:00:00",
                    "completed_at": "2026-02-01T12:00:00",
                    "duration_minutes": 120, "notes": [],
                },
                "exp-2": {
                    "id": "exp-2", "name": "v2", "status": "completed",
                    "hypothesis": "", "hyperparameters": {"lr": 0.01},
                    "results": {"accuracy": 0.9},
                    "tags": [], "git_commits": [], "files_created": [],
                    "started_at": "2026-02-02T10:00:00",
                    "completed_at": "2026-02-02T12:00:00",
                    "duration_minutes": 120, "notes": [],
                },
            },
        }
        md = generate_notebook(project)
        assert "What Changed" in md
        assert "lr" in md.split("What Changed")[1]  # lr should appear in diff

    def test_notebook_timeline_table(self):
        from distillate.experiments import generate_notebook
        project = {
            "name": "Timeline Test",
            "path": "/tmp/test",
            "description": "",
            "goals": [{"metric": "accuracy", "direction": "maximize", "threshold": 0.99}],
            "linked_papers": ["smith2026"],
            "runs": {},
        }
        md = generate_notebook(project)
        assert "Success Criteria" in md
        assert "accuracy" in md
        assert "Linked Papers" in md
        assert "smith2026" in md

    def test_empty_project_notebook(self):
        from distillate.experiments import generate_notebook
        project = {
            "name": "Empty Project",
            "path": "/tmp/test",
            "description": "",
            "goals": [],
            "linked_papers": [],
            "runs": {},
        }
        md = generate_notebook(project)
        assert "# Empty Project" in md
        assert "Experiment Timeline" not in md  # no runs = no timeline


# ---------------------------------------------------------------------------
# ML repo detection tests
# ---------------------------------------------------------------------------


class TestMLRepoDetection:
    def test_detect_ml_repo(self, tmp_path):
        from distillate.experiments import detect_ml_repos
        # Create a fake ML repo
        repo = tmp_path / "my-ml-project"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "train.py").write_text("import torch\nmodel = torch.nn.Linear(10, 10)")

        repos = detect_ml_repos(tmp_path)
        assert len(repos) == 1
        assert repos[0].name == "my-ml-project"

    def test_skip_non_ml_repo(self, tmp_path):
        from distillate.experiments import detect_ml_repos
        # Create a non-ML repo
        repo = tmp_path / "web-app"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / "index.js").write_text("console.log('hello')")

        repos = detect_ml_repos(tmp_path)
        assert len(repos) == 0

    def test_detect_by_checkpoint(self, tmp_path):
        from distillate.experiments import detect_ml_repos
        repo = tmp_path / "experiment"
        repo.mkdir()
        (repo / ".git").mkdir()
        ckpt_dir = repo / "checkpoints"
        ckpt_dir.mkdir()
        (ckpt_dir / "best_model.pt").write_text("")

        repos = detect_ml_repos(tmp_path)
        assert len(repos) == 1

    def test_nonexistent_root(self, tmp_path):
        from distillate.experiments import detect_ml_repos
        repos = detect_ml_repos(tmp_path / "nonexistent")
        assert repos == []


# ---------------------------------------------------------------------------
# Retroactive scan tests (with a mock git repo)
# ---------------------------------------------------------------------------


class TestRetroactiveScan:
    def _create_mock_ml_repo(self, tmp_path):
        """Create a minimal git repo with ML artifacts."""
        repo = tmp_path / "test-project"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            capture_output=True,
        )

        # Create a training log JSON
        log_data = {
            "config": {"d_model": 64, "n_heads": 2, "lr": 0.001, "batch_size": 32},
            "n_params": 28416,
            "total_time": 6300,
            "best_val_acc": 0.672,
            "epochs": [
                {"epoch": 1, "loss": 1.0, "val_acc": 0.3},
                {"epoch": 10, "loss": 0.1, "val_acc": 0.672},
            ],
        }
        results_dir = repo / "experiment"
        results_dir.mkdir()
        (results_dir / "train_v1.json").write_text(json.dumps(log_data))

        # Create a result file
        results_data = {"exact_match": 0.672, "total_params": 28416}
        (results_dir / "results_v1.json").write_text(json.dumps(results_data))

        # Commit
        subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "Add baseline experiment"],
            capture_output=True,
        )

        return repo

    def test_scan_discovers_runs(self, tmp_path):
        from distillate.experiments import scan_project
        repo = self._create_mock_ml_repo(tmp_path)
        result = scan_project(repo)
        assert "error" not in result
        assert result["name"] == "Test Project"
        assert len(result["runs"]) >= 1
        # Should have found the training log
        run = list(result["runs"].values())[0]
        assert run["status"] == "completed"
        assert "d_model" in run["hyperparameters"]
        assert run["hyperparameters"]["d_model"] == 64

    def test_scan_non_git_dir_succeeds(self, tmp_path):
        from distillate.experiments import scan_project
        result = scan_project(tmp_path)
        assert "error" not in result
        assert len(result["runs"]) == 0
        assert result["has_git"] is False
        assert result["head_hash"] == ""

    def test_scan_empty_repo(self, tmp_path):
        from distillate.experiments import scan_project
        repo = tmp_path / "empty"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            capture_output=True,
        )
        # Need at least one commit for git log
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "init"],
            capture_output=True,
        )
        result = scan_project(repo)
        assert "error" not in result
        assert len(result["runs"]) == 0


# ---------------------------------------------------------------------------
# Non-git scanning tests
# ---------------------------------------------------------------------------


class TestScanWithoutGit:
    def test_scan_discovers_runs_without_git(self, tmp_path):
        """Scan a plain directory (no .git) with ML artifacts."""
        from distillate.experiments import scan_project

        proj = tmp_path / "my-project"
        results_dir = proj / "experiment"
        results_dir.mkdir(parents=True)

        # Create a training log
        (results_dir / "train_v1.json").write_text(json.dumps({
            "config": {"d_model": 64, "n_heads": 2, "lr": 0.001},
            "total_time": 3600,
            "best_val_acc": 0.85,
            "epochs": [
                {"epoch": 1, "loss": 1.0, "val_acc": 0.5},
                {"epoch": 10, "loss": 0.2, "val_acc": 0.85},
            ],
        }))

        # Create a result file with matching tag
        (results_dir / "results_v1.json").write_text(json.dumps({
            "exact_match": 0.85, "total_params": 28416,
        }))

        result = scan_project(proj)
        assert "error" not in result
        assert result["has_git"] is False
        assert result["head_hash"] == ""
        assert len(result["runs"]) >= 1

        run = list(result["runs"].values())[0]
        assert run["status"] == "completed"
        assert "d_model" in run["hyperparameters"]
        assert run["git_commits"] == []
        # Timestamps from file mtime
        assert run["started_at"] != ""
        assert run["completed_at"] != ""

    def test_distillate_dir_created(self, tmp_path):
        """Scanning creates .distillate/scan_state.json."""
        from distillate.experiments import scan_project

        proj = tmp_path / "my-project"
        proj.mkdir()
        # Put a result file so there's something to scan
        (proj / "results.json").write_text(json.dumps({
            "accuracy": 0.95, "loss": 0.1,
        }))

        scan_project(proj)
        scan_state = proj / ".distillate" / "scan_state.json"
        assert scan_state.exists()
        data = json.loads(scan_state.read_text())
        assert "last_scanned_at" in data
        assert "file_manifest" in data

    def test_scan_with_git_enriches_commits(self, tmp_path):
        """Scan a git repo — runs get git_commits attached."""
        from distillate.experiments import scan_project

        repo = tmp_path / "git-project"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            capture_output=True,
        )

        # Initial commit (diff-tree needs a parent to list files)
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "init"],
            capture_output=True,
        )

        # Second commit with ML artifact
        results_dir = repo / "experiment"
        results_dir.mkdir()
        (results_dir / "results_v1.json").write_text(json.dumps({
            "accuracy": 0.9, "loss": 0.1,
        }))
        subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "Add results"],
            capture_output=True,
        )

        result = scan_project(repo)
        assert result["has_git"] is True
        assert result["head_hash"] != ""
        run = list(result["runs"].values())[0]
        assert len(run["git_commits"]) >= 1

    def test_update_project_detects_changes(self, tmp_path):
        """update_project re-scans when files change."""
        from distillate.experiments import scan_project, update_project

        proj = tmp_path / "my-project"
        results_dir = proj / "experiment"
        results_dir.mkdir(parents=True)

        (results_dir / "results_v1.json").write_text(json.dumps({
            "accuracy": 0.9, "loss": 0.1,
        }))

        # Initial scan
        result = scan_project(proj)
        project = {
            "name": "My Project",
            "path": str(proj),
            "runs": result["runs"],
            "last_scanned_at": "",
        }

        # No changes → returns False
        assert update_project(project, state=None) is False

        # Add a new artifact
        import time
        time.sleep(0.05)  # ensure different mtime
        (results_dir / "results_v2.json").write_text(json.dumps({
            "accuracy": 0.95, "loss": 0.05,
        }))

        # Now it should detect the change
        assert update_project(project, state=None) is True

    def test_detect_ml_repos_without_git(self, tmp_path):
        """detect_ml_repos finds non-git directories with ML files."""
        from distillate.experiments import detect_ml_repos

        # Create a non-git ML project
        proj = tmp_path / "ml-project"
        proj.mkdir()
        (proj / "train.py").write_text("import torch\nmodel = torch.nn.Linear(10, 10)")
        (proj / "config.yaml").write_text("lr: 0.001")

        repos = detect_ml_repos(tmp_path)
        assert proj in repos


# ---------------------------------------------------------------------------
# Slugify tests
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        from distillate.experiments import slugify
        assert slugify("My Project") == "my-project"
        assert slugify("tiny_gene_code") == "tiny-gene-code"

    def test_special_chars(self):
        from distillate.experiments import slugify
        assert slugify("Hello! World?") == "hello-world"

    def test_dashes(self):
        from distillate.experiments import slugify
        assert slugify("already-slugged") == "already-slugged"


# ---------------------------------------------------------------------------
# Git hook installation tests
# ---------------------------------------------------------------------------


class TestGitHook:
    def test_install_hook_new(self, tmp_path):
        from distillate.experiments import install_git_hook
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        assert install_git_hook(tmp_path, "my-project") is True
        hook = hooks_dir / "post-commit"
        assert hook.exists()
        content = hook.read_text()
        assert "distillate --scan-project my-project" in content
        assert os.access(hook, os.X_OK)

    def test_install_hook_idempotent(self, tmp_path):
        from distillate.experiments import install_git_hook
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        install_git_hook(tmp_path, "my-project")
        install_git_hook(tmp_path, "my-project")  # second call
        hook = hooks_dir / "post-commit"
        content = hook.read_text()
        assert content.count("distillate --scan-project") == 1

    def test_install_hook_appends(self, tmp_path):
        from distillate.experiments import install_git_hook
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook = hooks_dir / "post-commit"
        hook.write_text("#!/bin/sh\necho 'existing hook'\n")
        os.chmod(hook, 0o755)
        install_git_hook(tmp_path, "my-project")
        content = hook.read_text()
        assert "existing hook" in content
        assert "distillate --scan-project" in content

    def test_install_hook_no_git_dir(self, tmp_path):
        from distillate.experiments import install_git_hook
        assert install_git_hook(tmp_path, "my-project") is False


# ---------------------------------------------------------------------------
# Metric formatting tests
# ---------------------------------------------------------------------------


class TestMetricFormatting:
    def test_percentage_metric(self):
        from distillate.experiments import _fmt_metric
        assert _fmt_metric("accuracy", 0.95) == "95.0%"
        assert _fmt_metric("accuracy", 0.9999) == "99.99%"

    def test_loss_metric(self):
        from distillate.experiments import _fmt_metric
        # Loss should NOT be formatted as percentage
        result = _fmt_metric("loss", 0.0089)
        assert "%" not in result

    def test_integer_metric(self):
        from distillate.experiments import _fmt_metric
        assert _fmt_metric("n_params", 28416) == "28,416"

    def test_pick_key_metric(self):
        from distillate.experiments import _pick_key_metric
        assert _pick_key_metric({"accuracy": 0.95, "loss": 0.1}) == "95.0%"
        assert _pick_key_metric({"loss": 0.1}) == "0.1000"
        assert _pick_key_metric({}) == "-"


# ---------------------------------------------------------------------------
# Obsidian notebook writing tests
# ---------------------------------------------------------------------------


class TestExperimentToolSchemas:
    def test_all_schemas_valid(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        assert len(EXPERIMENT_TOOL_SCHEMAS) == 5
        for schema in EXPERIMENT_TOOL_SCHEMAS:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"

    def test_schema_names(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        names = {s["name"] for s in EXPERIMENT_TOOL_SCHEMAS}
        assert names == {
            "list_projects", "get_project_details", "compare_runs",
            "scan_project", "get_experiment_notebook",
        }


class TestObsidianNotebook:
    def test_write_fresh_notebook(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setattr("distillate.config.OBSIDIAN_PAPERS_FOLDER", "Papers")
        from distillate.obsidian import write_experiment_notebook
        project = {"id": "my-project"}
        result = write_experiment_notebook(project, "# My Notebook\n\nContent here.")
        assert result is not None
        assert result.exists()
        content = result.read_text()
        assert "<!-- distillate:start -->" in content
        assert "<!-- distillate:end -->" in content
        assert "# My Notebook" in content

    def test_regenerate_notebook(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setattr("distillate.config.OBSIDIAN_PAPERS_FOLDER", "Papers")
        from distillate.obsidian import write_experiment_notebook
        project = {"id": "my-project"}
        # First write
        write_experiment_notebook(project, "Version 1")
        # Second write (regenerate)
        result = write_experiment_notebook(project, "Version 2")
        content = result.read_text()
        assert "Version 2" in content
        assert "Version 1" not in content

    def test_section_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setattr("distillate.config.OBSIDIAN_PAPERS_FOLDER", "Papers")
        from distillate.obsidian import write_experiment_notebook
        project = {"id": "my-project"}
        result = write_experiment_notebook(project, "Train content", section="train")
        assert "my-project-train.md" in str(result)

    def test_no_vault_returns_none(self, monkeypatch):
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr("distillate.config.OUTPUT_PATH", "")
        from distillate.obsidian import write_experiment_notebook
        result = write_experiment_notebook({"id": "test"}, "content")
        assert result is None


# ---------------------------------------------------------------------------
# Claude Code log extraction tests
# ---------------------------------------------------------------------------


def _make_jsonl_session(path: Path, messages: list[dict]) -> Path:
    """Write a fake JSONL session file."""
    jsonl_file = path / "abc12345-fake-session.jsonl"
    with open(jsonl_file, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return jsonl_file


def _assistant_bash(tool_id: str, command: str, ts: str = "2026-01-15T10:00:00Z") -> dict:
    """Build an assistant message with a Bash tool_use block."""
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "Bash",
                    "input": {"command": command},
                }
            ]
        },
    }


def _user_tool_result(tool_id: str, output: str, ts: str = "2026-01-15T10:05:00Z") -> dict:
    """Build a user message with a tool_result block."""
    return {
        "type": "user",
        "timestamp": ts,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": output,
                }
            ]
        },
    }


class TestClaudeLogExtraction:
    """Tests for extracting experiment runs from Claude Code JSONL logs."""

    def test_find_claude_log_dir(self, tmp_path, monkeypatch):
        from distillate.experiments import _find_claude_log_dir

        # Create a fake .claude/projects directory
        claude_projects = tmp_path / ".claude" / "projects"
        encoded = str(tmp_path / "my-project").replace("/", "-")
        log_dir = claude_projects / encoded
        log_dir.mkdir(parents=True)

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = _find_claude_log_dir(tmp_path / "my-project")
        assert result == log_dir

    def test_find_claude_log_dir_missing(self, tmp_path, monkeypatch):
        from distillate.experiments import _find_claude_log_dir

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        result = _find_claude_log_dir(tmp_path / "nonexistent")
        assert result is None

    def test_parse_training_command(self):
        from distillate.experiments import _parse_training_command

        result = _parse_training_command(
            "python3 train.py d_model=8 n_heads=1 d_ff=16 epochs=20 lr=0.005"
        )
        assert result is not None
        assert result["script"] == "train.py"
        hp = result["hyperparameters"]
        assert hp["d_model"] == 8
        assert hp["n_heads"] == 1
        assert hp["d_ff"] == 16
        assert hp["epochs"] == 20
        assert hp["lr"] == 0.005

    def test_parse_training_command_with_path(self):
        from distillate.experiments import _parse_training_command

        result = _parse_training_command(
            "cd /some/path && python3 train.py epochs=5 batch_size=32 2>&1"
        )
        assert result is not None
        assert result["hyperparameters"]["epochs"] == 5
        assert result["hyperparameters"]["batch_size"] == 32

    def test_parse_training_command_not_training(self):
        from distillate.experiments import _parse_training_command

        # evaluate.py — not a training script
        assert _parse_training_command("python3 evaluate.py") is None
        # git command
        assert _parse_training_command("git status") is None
        # pip install
        assert _parse_training_command("pip install torch") is None

    def test_extract_metrics_from_output(self):
        from distillate.experiments import _extract_metrics_from_output

        output = (
            "Epoch 1: loss=6.432 accuracy=0.01\n"
            "Epoch 2: loss=3.210 accuracy=0.45\n"
            "Epoch 3: loss=1.050 accuracy=0.82\n"
            "Final: loss=0.320 exact_match=0.95\n"
        )
        metrics = _extract_metrics_from_output(output)
        # Should keep the last occurrence of each metric
        assert metrics["loss"] == 0.320
        assert metrics["accuracy"] == 0.82
        assert metrics["exact_match"] == 0.95

    def test_parse_config_block(self):
        from distillate.experiments import _parse_config_block

        output = (
            'Some warning text\n'
            'Config: {\n'
            '  "d_model": 64,\n'
            '  "n_heads": 2,\n'
            '  "lr": 0.003\n'
            '}\n'
            'Device: mps\n'
        )
        config = _parse_config_block(output)
        assert config["d_model"] == 64
        assert config["n_heads"] == 2
        assert config["lr"] == 0.003

    def test_parse_config_block_no_config(self):
        from distillate.experiments import _parse_config_block

        assert _parse_config_block("just some output text") == {}

    def test_extract_runs_from_session(self, tmp_path, monkeypatch):
        """Full integration: write fake JSONL, extract runs."""
        from distillate.experiments import extract_runs_from_claude_logs

        # Set up fake claude log directory
        project_path = tmp_path / "my-project"
        project_path.mkdir()
        claude_projects = tmp_path / ".claude" / "projects"
        encoded = str(project_path).replace("/", "-")
        log_dir = claude_projects / encoded
        log_dir.mkdir(parents=True)

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        # Write a session with two training runs
        _make_jsonl_session(log_dir, [
            _assistant_bash("t1", "python3 train.py d_model=8 n_heads=1 epochs=5 lr=0.01",
                           ts="2026-01-15T10:00:00Z"),
            _user_tool_result("t1",
                             "Config: {\"d_model\": 8, \"n_heads\": 1}\n"
                             "Epoch 5: loss=1.23 accuracy=0.85\n",
                             ts="2026-01-15T10:05:00Z"),
            _assistant_bash("t2", "python3 train.py d_model=16 n_heads=2 epochs=10 lr=0.005",
                           ts="2026-01-15T11:00:00Z"),
            _user_tool_result("t2",
                             "Epoch 10: loss=0.45 accuracy=0.95 exact_match=0.92\n",
                             ts="2026-01-15T11:30:00Z"),
        ])

        runs = extract_runs_from_claude_logs(project_path)
        assert len(runs) == 2

        # First run
        r1 = runs[0]
        assert r1["hyperparameters"]["d_model"] == 8
        assert r1["hyperparameters"]["n_heads"] == 1
        assert r1["results"]["accuracy"] == 0.85
        assert r1["started_at"] == "2026-01-15T10:00:00Z"
        assert r1["completed_at"] == "2026-01-15T10:05:00Z"
        assert r1["source"] == "claude_logs"
        assert r1["id"].startswith("claude-")

        # Second run
        r2 = runs[1]
        assert r2["hyperparameters"]["d_model"] == 16
        assert r2["results"]["exact_match"] == 0.92
        assert r2["results"]["loss"] == 0.45

    def test_non_training_commands_skipped(self, tmp_path, monkeypatch):
        """Non-training bash commands should not produce runs."""
        from distillate.experiments import extract_runs_from_claude_logs

        project_path = tmp_path / "my-project"
        project_path.mkdir()
        claude_projects = tmp_path / ".claude" / "projects"
        encoded = str(project_path).replace("/", "-")
        log_dir = claude_projects / encoded
        log_dir.mkdir(parents=True)

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        _make_jsonl_session(log_dir, [
            _assistant_bash("t1", "git status"),
            _user_tool_result("t1", "On branch main\nnothing to commit"),
            _assistant_bash("t2", "python3 evaluate.py"),
            _user_tool_result("t2", "accuracy=0.95"),
            _assistant_bash("t3", "cat train.py"),
            _user_tool_result("t3", "import torch\n..."),
        ])

        runs = extract_runs_from_claude_logs(project_path)
        assert len(runs) == 0

    def test_duplicate_run_skipped_in_scan(self, tmp_path, monkeypatch):
        """Claude log runs with same hyperparams as artifact runs are skipped."""
        from distillate.experiments import _is_duplicate_run

        existing_runs = {
            "exp-abc123": {
                "id": "exp-abc123",
                "name": "d8_h1",
                "hyperparameters": {"d_model": 8, "n_heads": 1, "lr": 0.01},
                "results": {"accuracy": 0.85},
            }
        }

        # Same hyperparams → duplicate
        candidate = {
            "id": "claude-xyz789",
            "hyperparameters": {"d_model": 8, "n_heads": 1, "lr": 0.01},
        }
        assert _is_duplicate_run(existing_runs, candidate) is True

        # Different hyperparams → not duplicate
        candidate2 = {
            "id": "claude-xyz790",
            "hyperparameters": {"d_model": 16, "n_heads": 2, "lr": 0.005},
        }
        assert _is_duplicate_run(existing_runs, candidate2) is False

    def test_claude_runs_integrated_in_scan(self, tmp_path, monkeypatch):
        """scan_project() should include Claude log runs alongside artifact runs."""
        from distillate.experiments import scan_project

        # Create a project dir with no artifacts
        project_path = tmp_path / "my-project"
        project_path.mkdir()

        # Set up Claude logs
        claude_projects = tmp_path / ".claude" / "projects"
        encoded = str(project_path).replace("/", "-")
        log_dir = claude_projects / encoded
        log_dir.mkdir(parents=True)

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        _make_jsonl_session(log_dir, [
            _assistant_bash("t1", "python3 train.py d_model=32 epochs=10",
                           ts="2026-01-15T10:00:00Z"),
            _user_tool_result("t1", "loss=0.5 accuracy=0.90\n",
                             ts="2026-01-15T10:10:00Z"),
        ])

        result = scan_project(project_path)
        assert len(result["runs"]) == 1
        run = list(result["runs"].values())[0]
        assert run["source"] == "claude_logs"
        assert run["hyperparameters"]["d_model"] == 32

    def test_coerce_value(self):
        from distillate.experiments import _coerce_value

        assert _coerce_value("42") == 42
        assert isinstance(_coerce_value("42"), int)
        assert _coerce_value("0.005") == 0.005
        assert isinstance(_coerce_value("0.005"), float)
        assert _coerce_value("True") is True
        assert _coerce_value("false") is False
        assert _coerce_value("1e-4") == 1e-4
