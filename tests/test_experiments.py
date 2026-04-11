"""Tests for the experiments (ML project tracking) feature."""

import json
import os
import subprocess
from pathlib import Path



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
        from pathlib import Path
        assert proj["path"] == str(Path("/tmp/my-project").resolve())
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
        assert _fmt_metric("accuracy", 0.95) == "95.00%"
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
        # loss is prioritized over accuracy (category priority: loss=0, ratio=1)
        assert _pick_key_metric({"accuracy": 0.95, "loss": 0.1}) == "loss=0.1000"
        assert _pick_key_metric({"loss": 0.1}) == "loss=0.1000"
        assert _pick_key_metric({}) == "-"


# ---------------------------------------------------------------------------
# Obsidian notebook writing tests
# ---------------------------------------------------------------------------


class TestExperimentToolSchemas:
    def test_all_schemas_valid(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        assert len(EXPERIMENT_TOOL_SCHEMAS) == 37
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
            "add_project", "rename_project", "rename_run",
            "delete_project", "delete_run", "update_project",
            "get_run_details", "link_paper", "update_goals", "annotate_run",
            "launch_experiment", "experiment_status", "stop_experiment",
            "init_experiment", "continue_experiment", "sweep_experiment",
            "steer_experiment",
            "compare_projects", "queue_sessions", "list_templates",
            "save_template", "create_github_repo", "reading_report",
            "manage_session",
            "replicate_paper", "suggest_from_literature", "extract_baselines",
            "save_enrichment", "start_run", "conclude_run",
            "purge_hook_runs", "discover_relevant_papers",
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


# ---------------------------------------------------------------------------
# LLM enrichment tests
# ---------------------------------------------------------------------------


_SAMPLE_RUNS = {
    "exp-001": {
        "id": "exp-001",
        "name": "d8_h1",
        "status": "completed",
        "hypothesis": "",
        "hyperparameters": {"d_model": 8, "n_heads": 1, "lr": 0.01},
        "results": {"accuracy": 0.65, "loss": 2.1},
        "tags": ["d8_h1"],
        "git_commits": [],
        "files_created": [],
        "started_at": "2026-01-15T10:00:00Z",
        "completed_at": "2026-01-15T10:30:00Z",
        "duration_minutes": 30,
        "notes": [],
    },
    "exp-002": {
        "id": "exp-002",
        "name": "d16_h2",
        "status": "completed",
        "hypothesis": "",
        "hyperparameters": {"d_model": 16, "n_heads": 2, "lr": 0.005},
        "results": {"accuracy": 0.92, "loss": 0.4},
        "tags": ["d16_h2"],
        "git_commits": [],
        "files_created": [],
        "started_at": "2026-01-15T11:00:00Z",
        "completed_at": "2026-01-15T11:45:00Z",
        "duration_minutes": 45,
        "notes": [],
    },
}

_SAMPLE_ENRICHMENT = {
    "runs": {
        "exp-001": {
            "name": "Baseline Small Transformer",
            "hypothesis": "A minimal transformer should learn basic patterns.",
            "approach": "Start with the smallest viable model to establish a baseline.",
            "analysis": "65% accuracy shows the model learns some patterns but lacks capacity.",
            "next_steps": "Double the model dimensions to test if capacity is the bottleneck.",
        },
        "exp-002": {
            "name": "Scaled-Up Model",
            "hypothesis": "Doubling dimensions should improve accuracy if capacity was the issue.",
            "approach": "Increased d_model from 8 to 16, added a second attention head.",
            "analysis": "92% accuracy confirms capacity was the main bottleneck. Loss dropped 5x.",
            "next_steps": "Try reducing learning rate further or adding regularization.",
        },
    },
    "project": {
        "key_breakthrough": "Scaling from d_model=8 to d_model=16 jumped accuracy from 65% to 92%.",
        "lessons_learned": [
            "Model capacity was the primary bottleneck, not training procedure.",
            "Halving the learning rate alongside scaling helped stability.",
        ],
    },
}


class TestLLMEnrichment:
    """Tests for LLM-based experiment enrichment."""

    def test_runs_fingerprint_stable(self):
        from distillate.experiments import _runs_fingerprint

        fp1 = _runs_fingerprint(_SAMPLE_RUNS)
        fp2 = _runs_fingerprint(_SAMPLE_RUNS)
        assert fp1 == fp2

    def test_runs_fingerprint_changes(self):
        from distillate.experiments import _runs_fingerprint

        fp1 = _runs_fingerprint(_SAMPLE_RUNS)
        modified = json.loads(json.dumps(_SAMPLE_RUNS))
        modified["exp-001"]["results"]["accuracy"] = 0.70
        fp2 = _runs_fingerprint(modified)
        assert fp1 != fp2

    def test_enrichment_cache_round_trip(self, tmp_path):
        from distillate.experiments import (
            load_enrichment_cache,
            _save_enrichment_cache,
        )

        _save_enrichment_cache(tmp_path, {
            "fingerprint": "abc123",
            "enrichment": _SAMPLE_ENRICHMENT,
        })
        loaded = load_enrichment_cache(tmp_path)
        assert loaded["fingerprint"] == "abc123"
        assert loaded["enrichment"]["project"]["key_breakthrough"].startswith("Scaling")

    def test_enrichment_cache_missing(self, tmp_path):
        from distillate.experiments import load_enrichment_cache

        assert load_enrichment_cache(tmp_path) == {}

    def test_build_enrichment_prompt(self):
        from distillate.experiments import _build_enrichment_prompt

        prompt = _build_enrichment_prompt(_SAMPLE_RUNS, "Test Project")
        assert "Test Project" in prompt
        assert "d_model=8" in prompt
        assert "d_model=16" in prompt
        assert "accuracy" in prompt
        assert "exp-001" in prompt
        assert "exp-002" in prompt
        assert "(first experiment)" in prompt  # first run has no diff

    def test_notebook_with_enrichment(self):
        from distillate.experiments import generate_notebook

        project = {
            "name": "Test Project",
            "path": "/tmp/test",
            "runs": _SAMPLE_RUNS,
        }
        md = generate_notebook(project, enrichment=_SAMPLE_ENRICHMENT)

        # Check enriched names in timeline
        assert "Baseline Small Transformer" in md
        assert "Scaled-Up Model" in md

        # Check narrative sections
        assert "#### Hypothesis" in md
        assert "minimal transformer should learn basic patterns" in md
        assert "#### Approach" in md
        assert "smallest viable model" in md
        assert "#### Analysis" in md
        assert "65% accuracy shows" in md
        assert "#### Next Steps" in md

        # Research insights should be near the top (before Experiment Timeline)
        insights_pos = md.index("## Research Insights")
        timeline_pos = md.index("## Experiment Timeline")
        assert insights_pos < timeline_pos

        assert "### Key Breakthrough" in md
        assert "d_model=8 to d_model=16" in md
        assert "### Lessons Learned" in md
        assert "capacity was the primary bottleneck" in md

    def test_notebook_without_enrichment(self):
        """generate_notebook still works fine without enrichment."""
        from distillate.experiments import generate_notebook

        project = {
            "name": "Test Project",
            "path": "/tmp/test",
            "runs": _SAMPLE_RUNS,
        }
        md = generate_notebook(project)

        # Should still have the basic structure
        assert "# Test Project" in md
        assert "## Experiment Timeline" in md
        assert "d8_h1" in md  # original name, not enriched

        # Should NOT have research insights
        assert "## Research Insights" not in md

    def test_factorize_hyperparams(self):
        from distillate.experiments import _factorize_hyperparams

        runs = [
            {"hyperparameters": {"lr": 0.01, "batch_size": 32, "d_model": 8}},
            {"hyperparameters": {"lr": 0.005, "batch_size": 32, "d_model": 16}},
            {"hyperparameters": {"lr": 0.001, "batch_size": 32, "d_model": 32}},
        ]
        common, varying = _factorize_hyperparams(runs)
        assert common == {"batch_size": 32}
        assert "lr" in varying
        assert "d_model" in varying
        assert "batch_size" not in varying

    def test_notebook_factorizes_hyperparams(self):
        """Common hyperparams appear once; per-run cards show only changes."""
        from distillate.experiments import generate_notebook

        runs = {
            "r1": {
                "id": "r1", "name": "run1", "status": "completed",
                "hyperparameters": {"lr": 0.01, "batch_size": 32, "n_layers": 1},
                "results": {}, "tags": [], "git_commits": [],
                "files_created": [], "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "", "duration_minutes": 0, "notes": [],
                "hypothesis": "",
            },
            "r2": {
                "id": "r2", "name": "run2", "status": "completed",
                "hyperparameters": {"lr": 0.005, "batch_size": 32, "n_layers": 1},
                "results": {}, "tags": [], "git_commits": [],
                "files_created": [], "started_at": "2026-01-02T00:00:00Z",
                "completed_at": "", "duration_minutes": 0, "notes": [],
                "hypothesis": "",
            },
        }
        md = generate_notebook({"name": "Test", "path": "/tmp", "runs": runs})

        # Common config section should exist with shared params
        assert "## Common Configuration" in md
        assert "| batch_size | `32` |" in md
        assert "| n_layers | `1` |" in md

        # Per-run cards should show "Configuration (changes)" not full table
        assert "#### Configuration (changes)" in md
        # lr varies so it should appear in per-run cards
        assert "| lr | `0.01` |" in md
        assert "| lr | `0.005` |" in md

    def test_enrich_skips_without_api_key(self, monkeypatch):
        from distillate.experiments import enrich_runs_with_llm

        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "")
        result = enrich_runs_with_llm(_SAMPLE_RUNS, "Test", Path("/tmp"))
        assert result is None

    def test_enrich_uses_cache(self, tmp_path, monkeypatch):
        from distillate.experiments import (
            _runs_fingerprint,
            _save_enrichment_cache,
            enrich_runs_with_llm,
        )

        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "sk-test-key")

        # Pre-populate cache
        fp = _runs_fingerprint(_SAMPLE_RUNS)
        _save_enrichment_cache(tmp_path, {
            "fingerprint": fp,
            "enrichment": _SAMPLE_ENRICHMENT,
        })

        # Should return cached enrichment without calling API
        result = enrich_runs_with_llm(_SAMPLE_RUNS, "Test", tmp_path)
        assert result is not None
        assert result["project"]["key_breakthrough"].startswith("Scaling")

    def test_enrich_calls_api(self, tmp_path, monkeypatch):
        """When cache misses, enrich_runs_with_llm calls Claude API."""
        from distillate.experiments import enrich_runs_with_llm

        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "sk-test-key")
        monkeypatch.setattr("distillate.config.CLAUDE_SMART_MODEL", "claude-sonnet-4-5-20250929")

        # Mock the anthropic client
        api_response = json.dumps(_SAMPLE_ENRICHMENT)

        class FakeContent:
            text = api_response

        class FakeResponse:
            content = [FakeContent()]
            stop_reason = "end_turn"

        class FakeMessages:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeClient:
            def __init__(self, **kwargs):
                self.messages = FakeMessages()

        monkeypatch.setattr("anthropic.Anthropic", FakeClient)
        # Ensure anthropic is "importable" by pre-importing mock
        import types
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = FakeClient
        fake_anthropic.APIError = type("APIError", (Exception,), {})
        fake_anthropic.APIConnectionError = type("APIConnectionError", (Exception,), {})
        monkeypatch.setitem(__import__("sys").modules, "anthropic", fake_anthropic)

        result = enrich_runs_with_llm(_SAMPLE_RUNS, "Test Project", tmp_path)
        assert result is not None
        assert "runs" in result
        assert "project" in result
        assert result["runs"]["exp-001"]["name"] == "Baseline Small Transformer"

        # Check cache was written
        from distillate.experiments import load_enrichment_cache
        cache = load_enrichment_cache(tmp_path)
        assert cache.get("enrichment") is not None


# ---------------------------------------------------------------------------
# State remove_run tests
# ---------------------------------------------------------------------------


class TestRemoveRun:
    def test_remove_existing_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("proj", "Proj", "/path")
        state.add_run("proj", "run-1", {"id": "run-1", "name": "Run 1"})
        assert state.remove_run("proj", "run-1") is True
        assert state.get_run("proj", "run-1") is None

    def test_remove_nonexistent_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_project("proj", "Proj", "/path")
        assert state.remove_run("proj", "nope") is False

    def test_remove_run_nonexistent_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        assert state.remove_run("nope", "run-1") is False


# ---------------------------------------------------------------------------
# Helpers tests
# ---------------------------------------------------------------------------


class TestResolveProject:
    def test_resolve_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        from distillate.experiment_tools import _resolve_project
        state = State()
        state.add_project("my-proj", "My Proj", "/path")
        proj, err = _resolve_project(state, "my-proj")
        assert proj is not None
        assert err is None
        assert proj["name"] == "My Proj"

    def test_resolve_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        from distillate.experiment_tools import _resolve_project
        state = State()
        proj, err = _resolve_project(state, "nope")
        assert proj is None
        assert "error" in err

    def test_resolve_ambiguous(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        from distillate.experiment_tools import _resolve_project
        state = State()
        state.add_project("ml-proj-a", "ML Project A", "/a")
        state.add_project("ml-proj-b", "ML Project B", "/b")
        proj, err = _resolve_project(state, "ML Project")
        assert proj is None
        assert "Multiple" in err["error"]


class TestFindAllRuns:
    def test_find_all_by_substring(self):
        from distillate.experiment_tools import _find_all_runs
        runs = {
            "r1": {"id": "r1", "name": "baseline v1"},
            "r2": {"id": "r2", "name": "baseline v2"},
            "r3": {"id": "r3", "name": "final"},
        }
        matches = _find_all_runs(runs, "baseline")
        assert len(matches) == 2

    def test_find_all_exact_id(self):
        from distillate.experiment_tools import _find_all_runs
        runs = {"r1": {"id": "r1", "name": "run 1"}}
        assert len(_find_all_runs(runs, "r1")) == 1

    def test_find_all_empty(self):
        from distillate.experiment_tools import _find_all_runs
        assert _find_all_runs({}, "x") == []
        assert _find_all_runs({"r1": {"id": "r1", "name": "a"}}, "") == []


# ---------------------------------------------------------------------------
# CRUD tool tests
# ---------------------------------------------------------------------------


def _make_state(tmp_path, monkeypatch):
    """Create a State with a project and two runs for CRUD tool tests."""
    monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")
    monkeypatch.setattr("distillate.config.OUTPUT_PATH", "")
    monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "")
    from distillate.state import State
    state = State()
    state.add_project("test-proj", "Test Project", str(tmp_path / "fake-dir"))
    state.add_run("test-proj", "run-1", {
        "id": "run-1", "name": "Baseline", "status": "completed",
        "hyperparameters": {"lr": 0.01}, "results": {"accuracy": 0.8},
        "tags": [], "git_commits": [], "files_created": [],
        "started_at": "", "completed_at": "", "duration_minutes": 30, "notes": [],
    })
    state.add_run("test-proj", "run-2", {
        "id": "run-2", "name": "Improved", "status": "completed",
        "hyperparameters": {"lr": 0.005}, "results": {"accuracy": 0.9},
        "tags": [], "git_commits": [], "files_created": [],
        "started_at": "", "completed_at": "", "duration_minutes": 45, "notes": [],
    })
    state.save()
    return state


class TestRenameProjectTool:
    def test_rename_success(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import rename_project_tool
        result = rename_project_tool(state=state, identifier="test-proj", new_name="Better Name")
        assert result["success"] is True
        assert result["old_name"] == "Test Project"
        assert result["new_name"] == "Better Name"
        assert state.has_project("better-name")
        assert not state.has_project("test-proj")

    def test_rename_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import rename_project_tool
        result = rename_project_tool(state=state, identifier="nope", new_name="X")
        assert "error" in result

    def test_rename_same_slug_and_name(self, tmp_path, monkeypatch):
        """Renaming to the exact same name (same slug) is a no-op error."""
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr("distillate.config.OUTPUT_PATH", "")
        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "")
        from distillate.state import State
        state = State()
        # Create project where id matches slugify(name)
        state.add_project("test-project", "Test Project", str(tmp_path))
        state.save()
        from distillate.experiment_tools import rename_project_tool
        result = rename_project_tool(state=state, identifier="test-project", new_name="Test Project")
        assert result.get("success") is False


class TestRenameRunTool:
    def test_rename_run_success(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import rename_run_tool
        result = rename_run_tool(state=state, project="test-proj", run="run-1", new_name="New Baseline")
        assert result["success"] is True
        assert result["old_name"] == "Baseline"
        assert state.get_run("test-proj", "run-1")["name"] == "New Baseline"

    def test_rename_run_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import rename_run_tool
        result = rename_run_tool(state=state, project="test-proj", run="nope", new_name="X")
        assert "error" in result


class TestDeleteProjectTool:
    def test_delete_preview(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import delete_project_tool
        result = delete_project_tool(state=state, identifier="test-proj", confirm=False)
        assert result["confirm_required"] is True
        assert state.has_project("test-proj")  # not deleted yet

    def test_delete_confirm(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import delete_project_tool
        result = delete_project_tool(state=state, identifier="test-proj", confirm=True)
        assert result["success"] is True
        assert not state.has_project("test-proj")

    def test_delete_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import delete_project_tool
        result = delete_project_tool(state=state, identifier="nope")
        assert "error" in result


class TestDeleteRunTool:
    def test_delete_run_preview(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import delete_run_tool
        result = delete_run_tool(state=state, project="test-proj", run="run-1", confirm=False)
        assert result["confirm_required"] is True
        assert state.get_run("test-proj", "run-1") is not None

    def test_delete_run_confirm(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import delete_run_tool
        result = delete_run_tool(state=state, project="test-proj", run="run-1", confirm=True)
        assert result["success"] is True
        assert state.get_run("test-proj", "run-1") is None
        # Other run still exists
        assert state.get_run("test-proj", "run-2") is not None

    def test_delete_run_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import delete_run_tool
        result = delete_run_tool(state=state, project="test-proj", run="nope")
        assert "error" in result


class TestUpdateProjectTool:
    def test_update_description(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_project_tool
        result = update_project_tool(state=state, identifier="test-proj", description="New desc")
        assert result["success"] is True
        assert state.get_project("test-proj")["description"] == "New desc"

    def test_update_tags(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_project_tool
        result = update_project_tool(state=state, identifier="test-proj", tags=["nlp", "transformers"])
        assert result["success"] is True
        assert state.get_project("test-proj")["tags"] == ["nlp", "transformers"]

    def test_update_status(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_project_tool
        result = update_project_tool(state=state, identifier="test-proj", status="archived")
        assert result["success"] is True
        assert state.get_project("test-proj")["status"] == "archived"

    def test_update_invalid_status(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_project_tool
        result = update_project_tool(state=state, identifier="test-proj", status="bad")
        assert "error" in result

    def test_update_no_fields(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_project_tool
        result = update_project_tool(state=state, identifier="test-proj")
        assert "error" in result

    def test_update_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_project_tool
        result = update_project_tool(state=state, identifier="nope", description="x")
        assert "error" in result


class TestLinkPaperTool:
    def test_link_by_citekey(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        # Add a paper to state
        state.add_document("ZOT123", "ATT123", "md5", "doc", "Deep RL Paper",
                          ["Author"], metadata={"citekey": "smith2026"})
        state.save()
        from distillate.experiment_tools import link_paper_tool
        result = link_paper_tool(state=state, project="test-proj", paper="smith2026")
        assert result["success"] is True
        proj = state.get_project("test-proj")
        assert "smith2026" in proj["linked_papers"]

    def test_link_by_title_substring(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        state.add_document("ZOT123", "ATT123", "md5", "doc", "Deep RL Paper",
                          ["Author"], metadata={"citekey": "smith2026"})
        from distillate.experiment_tools import link_paper_tool
        result = link_paper_tool(state=state, project="test-proj", paper="Deep RL")
        assert result["success"] is True

    def test_link_paper_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import link_paper_tool
        result = link_paper_tool(state=state, project="test-proj", paper="nonexistent")
        assert "error" in result

    def test_link_already_linked(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        state.add_document("ZOT123", "ATT123", "md5", "doc", "Paper",
                          ["Author"], metadata={"citekey": "smith2026"})
        state.update_project("test-proj", linked_papers=["smith2026"])
        from distillate.experiment_tools import link_paper_tool
        result = link_paper_tool(state=state, project="test-proj", paper="smith2026")
        assert result.get("success") is False

    def test_link_project_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import link_paper_tool
        result = link_paper_tool(state=state, project="nope", paper="x")
        assert "error" in result


class TestUpdateGoalsTool:
    def test_set_goals(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_goals_tool
        goals = [
            {"metric": "accuracy", "direction": "maximize", "threshold": 0.95},
            {"metric": "loss", "direction": "minimize", "threshold": 0.1},
        ]
        result = update_goals_tool(state=state, project="test-proj", goals=goals)
        assert result["success"] is True
        assert result["goals_count"] == 2
        proj = state.get_project("test-proj")
        assert len(proj["goals"]) == 2

    def test_invalid_direction(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_goals_tool
        goals = [{"metric": "acc", "direction": "up", "threshold": 0.9}]
        result = update_goals_tool(state=state, project="test-proj", goals=goals)
        assert "error" in result

    def test_goals_project_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_goals_tool
        result = update_goals_tool(state=state, project="nope", goals=[])
        assert "error" in result


# ---------------------------------------------------------------------------
# HTML notebook generation tests
# ---------------------------------------------------------------------------


class TestHtmlNotebook:
    def _make_project(self):
        return {
            "id": "test-proj",
            "name": "Test Project",
            "path": "/tmp/test",
            "description": "A test project",
            "status": "tracking",
            "goals": [{"metric": "accuracy", "direction": "maximize", "threshold": 0.95}],
            "linked_papers": ["smith2026"],
            "runs": {
                "run-1": {
                    "id": "run-1", "name": "Baseline",
                    "status": "completed",
                    "hyperparameters": {"lr": 0.01, "batch_size": 32},
                    "results": {"accuracy": 0.8, "loss": 0.5},
                    "tags": ["v1"], "notes": ["initial run"],
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T01:00:00+00:00",
                    "duration_minutes": 60,
                },
                "run-2": {
                    "id": "run-2", "name": "Improved",
                    "status": "completed",
                    "hyperparameters": {"lr": 0.005, "batch_size": 32},
                    "results": {"accuracy": 0.9, "loss": 0.3},
                    "tags": ["v2"], "notes": [],
                    "started_at": "2026-01-02T00:00:00+00:00",
                    "completed_at": "2026-01-02T01:00:00+00:00",
                    "duration_minutes": 45,
                },
            },
        }

    def test_html_contains_structure(self):
        from distillate.experiments import generate_html_notebook
        proj = self._make_project()
        html = generate_html_notebook(proj)
        assert "<!DOCTYPE html>" in html
        assert "<title>Test Project" in html
        assert "stats-bar" in html
        assert "run-card" in html
        assert "Baseline" in html
        assert "Improved" in html
        assert "</html>" in html

    def test_html_escapes_special_chars(self):
        from distillate.experiments import generate_html_notebook
        proj = self._make_project()
        proj["name"] = "Test <script>alert('xss')</script>"
        html = generate_html_notebook(proj)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_includes_stats(self):
        from distillate.experiments import generate_html_notebook
        proj = self._make_project()
        html = generate_html_notebook(proj)
        assert "Experiments" in html
        assert "Completed" in html
        assert "stat-value" in html

    def test_html_includes_common_config(self):
        from distillate.experiments import generate_html_notebook
        proj = self._make_project()
        html = generate_html_notebook(proj)
        # batch_size=32 is shared across both runs
        assert "config-grid" in html
        assert "batch_size" in html

    def test_html_includes_diff(self):
        from distillate.experiments import generate_html_notebook
        proj = self._make_project()
        html = generate_html_notebook(proj)
        assert "diff-section" in html
        assert "What Changed" in html

    def test_html_includes_notes(self):
        from distillate.experiments import generate_html_notebook
        proj = self._make_project()
        html = generate_html_notebook(proj)
        assert "initial run" in html
        assert "notes-block" in html

    def test_html_includes_enrichment(self):
        from distillate.experiments import generate_html_notebook
        proj = self._make_project()
        enrichment = {
            "runs": {
                "run-1": {
                    "hypothesis": "Lower LR should help",
                    "approach": "Standard training",
                    "analysis": "Good results",
                    "next_steps": "Try more epochs",
                    "name": "Baseline Experiment",
                },
            },
            "project": {
                "key_breakthrough": "Found optimal LR",
                "lessons_learned": ["Batch size matters", "LR decay helps"],
            },
        }
        html = generate_html_notebook(proj, enrichment=enrichment)
        assert "Research Insights" in html
        assert "Found optimal LR" in html
        assert "Batch size matters" in html
        assert "narrative-block" in html
        assert "Lower LR should help" in html

    def test_html_includes_goals(self):
        from distillate.experiments import generate_html_notebook
        proj = self._make_project()
        html = generate_html_notebook(proj)
        assert "Success Criteria" in html
        assert "accuracy" in html

    def test_html_includes_linked_papers(self):
        from distillate.experiments import generate_html_notebook
        proj = self._make_project()
        html = generate_html_notebook(proj)
        assert "Linked Papers" in html
        assert "smith2026" in html

    def test_html_empty_project(self):
        from distillate.experiments import generate_html_notebook
        proj = {"id": "empty", "name": "Empty", "path": "", "runs": {}}
        html = generate_html_notebook(proj)
        assert "<!DOCTYPE html>" in html
        assert "Empty" in html


class TestWriteHtmlNotebook:
    def test_writes_to_html_subdir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setattr("distillate.config.OBSIDIAN_PAPERS_FOLDER", "Distillate")
        monkeypatch.setattr("distillate.config.OUTPUT_PATH", "")
        from distillate.obsidian import write_experiment_html_notebook
        proj = {"id": "my-project", "name": "My Project"}
        path = write_experiment_html_notebook(proj, "<html>test</html>")
        assert path is not None
        assert path.exists()
        assert path.name == "my-project.html"
        assert "html" in str(path.parent.name)
        assert path.read_text(encoding="utf-8") == "<html>test</html>"

    def test_returns_none_unconfigured(self, monkeypatch):
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr("distillate.config.OUTPUT_PATH", "")
        from distillate.obsidian import write_experiment_html_notebook
        proj = {"id": "my-project"}
        assert write_experiment_html_notebook(proj, "html") is None


# ---------------------------------------------------------------------------
# annotate_run tool tests
# ---------------------------------------------------------------------------


class TestAnnotateRunTool:
    def test_add_hypothesis(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import annotate_run_tool
        result = annotate_run_tool(
            state=state, project="test-proj", run="run-1",
            hypothesis="Smaller LR converges better",
        )
        assert result["success"] is True
        assert "hypothesis" in result["updated"]
        run = state.get_run("test-proj", "run-1")
        assert run["hypothesis"] == "Smaller LR converges better"

    def test_add_note(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import annotate_run_tool
        result = annotate_run_tool(
            state=state, project="test-proj", run="run-1",
            note="Ran on A100 GPU",
        )
        assert result["success"] is True
        assert "note" in result["updated"]
        run = state.get_run("test-proj", "run-1")
        assert "Ran on A100 GPU" in run["notes"]

    def test_add_both(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import annotate_run_tool
        result = annotate_run_tool(
            state=state, project="test-proj", run="run-1",
            hypothesis="Test hypothesis", note="Test note",
        )
        assert result["success"] is True
        assert "hypothesis" in result["updated"]
        assert "note" in result["updated"]

    def test_requires_at_least_one_field(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import annotate_run_tool
        result = annotate_run_tool(
            state=state, project="test-proj", run="run-1",
        )
        assert "error" in result

    def test_project_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import annotate_run_tool
        result = annotate_run_tool(
            state=state, project="nope", run="run-1",
            hypothesis="Test",
        )
        assert "error" in result

    def test_run_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import annotate_run_tool
        result = annotate_run_tool(
            state=state, project="test-proj", run="nope",
            hypothesis="Test",
        )
        assert "error" in result

    def test_hypothesis_precedence_in_notebook(self, tmp_path, monkeypatch):
        """User-provided hypothesis should appear in the notebook."""
        from distillate.experiments import generate_notebook
        proj = {
            "id": "test", "name": "Test", "path": "",
            "runs": {
                "r1": {
                    "id": "r1", "name": "Run 1", "status": "completed",
                    "hypothesis": "User's own hypothesis",
                    "hyperparameters": {}, "results": {},
                    "tags": [], "notes": [],
                    "started_at": "", "completed_at": "", "duration_minutes": 0,
                },
            },
        }
        enrichment = {
            "runs": {"r1": {"hypothesis": "LLM generated hypothesis"}},
            "project": {},
        }
        md = generate_notebook(proj, enrichment=enrichment)
        # User hypothesis takes precedence
        assert "User's own hypothesis" in md

    def test_hypothesis_precedence_in_html(self, tmp_path, monkeypatch):
        """User-provided hypothesis should appear in HTML notebook too."""
        from distillate.experiments import generate_html_notebook
        proj = {
            "id": "test", "name": "Test", "path": "",
            "runs": {
                "r1": {
                    "id": "r1", "name": "Run 1", "status": "completed",
                    "hypothesis": "User hypothesis here",
                    "hyperparameters": {}, "results": {},
                    "tags": [], "notes": [],
                    "started_at": "", "completed_at": "", "duration_minutes": 0,
                },
            },
        }
        enrichment = {
            "runs": {"r1": {"hypothesis": "LLM hypothesis"}},
            "project": {},
        }
        html = generate_html_notebook(proj, enrichment=enrichment)
        assert "User hypothesis here" in html

    def test_notes_append(self, tmp_path, monkeypatch):
        """Multiple annotate calls should accumulate notes."""
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import annotate_run_tool
        annotate_run_tool(state=state, project="test-proj", run="run-1", note="First")
        annotate_run_tool(state=state, project="test-proj", run="run-1", note="Second")
        run = state.get_run("test-proj", "run-1")
        assert len(run["notes"]) == 2
        assert run["notes"][0] == "First"
        assert run["notes"][1] == "Second"


# ---------------------------------------------------------------------------
# Auto-detection tests
# ---------------------------------------------------------------------------


class TestCheckProjectsForUpdates:
    def test_no_projects(self):
        from distillate.experiments import check_projects_for_updates
        assert check_projects_for_updates({}) == []

    def test_nonexistent_path(self):
        from distillate.experiments import check_projects_for_updates
        projects = {
            "p1": {"id": "p1", "path": "/nonexistent/path", "last_commit_hash": "abc123"},
        }
        assert check_projects_for_updates(projects) == []

    def test_detects_new_commits(self, tmp_path):
        """Test detection when HEAD differs from stored hash."""
        from distillate.experiments import check_projects_for_updates
        # Create a git repo with one commit
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
        (repo / "file.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        # Get initial hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True,
        )
        first_hash = result.stdout.strip()

        # Add another commit
        (repo / "file2.txt").write_text("world")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "second"], cwd=repo, capture_output=True)

        projects = {
            "test": {
                "id": "test", "name": "Test",
                "path": str(repo),
                "last_commit_hash": first_hash,
            },
        }
        updates = check_projects_for_updates(projects)
        assert len(updates) == 1
        assert updates[0]["new_commits"] == 1
        assert updates[0]["project"]["name"] == "Test"

    def test_no_updates_when_hash_matches(self, tmp_path):
        from distillate.experiments import check_projects_for_updates
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
        (repo / "file.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True,
        )
        current_hash = result.stdout.strip()

        projects = {
            "test": {
                "id": "test", "path": str(repo),
                "last_commit_hash": current_hash,
            },
        }
        assert check_projects_for_updates(projects) == []

    def test_first_scan_no_stored_hash(self, tmp_path):
        from distillate.experiments import check_projects_for_updates
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
        (repo / "file.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        projects = {
            "test": {"id": "test", "path": str(repo), "last_commit_hash": ""},
        }
        updates = check_projects_for_updates(projects)
        assert len(updates) == 1
        assert updates[0]["new_commits"] == 1


class TestDiscoverGitRepos:
    """Test _discover_git_repos and multi-repo scan_project_tool."""

    def test_discovers_child_repos(self, tmp_path):
        from distillate.experiment_tools import _discover_git_repos

        # Create two child repos and one non-repo dir
        (tmp_path / "repo-a" / ".git").mkdir(parents=True)
        (tmp_path / "repo-b" / ".git").mkdir(parents=True)
        (tmp_path / "not-a-repo").mkdir()
        (tmp_path / ".hidden" / ".git").mkdir(parents=True)

        repos = _discover_git_repos(tmp_path)
        names = [r.name for r in repos]
        assert "repo-a" in names
        assert "repo-b" in names
        assert "not-a-repo" not in names
        assert ".hidden" not in names  # hidden dirs skipped

    def test_returns_empty_for_no_repos(self, tmp_path):
        from distillate.experiment_tools import _discover_git_repos

        (tmp_path / "plain-dir").mkdir()
        assert _discover_git_repos(tmp_path) == []

    def test_scan_tool_no_git_no_subrepos(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.experiment_tools import scan_project_tool
        from distillate.state import State

        plain = tmp_path / "empty"
        plain.mkdir()
        result = scan_project_tool(state=State(), path=str(plain))
        assert not result["success"]
        assert "No git repository" in result["error"]

    def test_scan_tool_multi_repo(self, tmp_path, monkeypatch):
        """Scanning a parent dir discovers child git repos."""
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr("distillate.config.OUTPUT_PATH", "")
        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "")

        parent = tmp_path / "projects"
        parent.mkdir()

        # Create two git repos with ML artifacts
        for name in ("alpha", "beta"):
            repo = parent / name
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
            # Write a training log JSON
            log_data = {
                "config": {"lr": 0.01, "epochs": 10, "batch_size": 32},
                "epochs": [{"epoch": 1, "loss": 0.5}],
            }
            (repo / "training_log.json").write_text(json.dumps(log_data))
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        from distillate.experiment_tools import scan_project_tool
        from distillate.state import State

        result = scan_project_tool(state=State(), path=str(parent))
        assert result["success"]
        assert result.get("multi")
        assert len(result["projects"]) == 2
        project_names = {p["name"] for p in result["projects"]}
        assert "Alpha" in project_names
        assert "Beta" in project_names

    def test_scan_tool_single_git_repo_still_works(self, tmp_path, monkeypatch):
        """A path with .git at root is scanned directly (no discovery)."""
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr("distillate.config.OUTPUT_PATH", "")
        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "")

        repo = tmp_path / "my-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, capture_output=True)
        log_data = {
            "config": {"lr": 0.01, "epochs": 10, "batch_size": 32},
            "epochs": [{"epoch": 1, "loss": 0.5}],
        }
        (repo / "training_log.json").write_text(json.dumps(log_data))
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

        from distillate.experiment_tools import scan_project_tool
        from distillate.state import State

        result = scan_project_tool(state=State(), path=str(repo))
        assert result["success"]
        assert "multi" not in result
        assert result["runs_discovered"] >= 1


class TestExperimentsSection:
    """Test _experiments_section in agent_core."""

    def test_includes_new_commits(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.config.EXPERIMENTS_ENABLED", True)
        from distillate.state import State
        state = State()
        state.add_project("proj-1", "Test Project", str(tmp_path))
        from distillate.agent_core import _experiments_section
        updates = [{"project": {"id": "proj-1", "name": "Test Project"}, "new_commits": 3, "current_hash": "abc"}]
        section = _experiments_section(state, updates=updates)
        assert "3 new commits" in section

    def test_no_updates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.config.EXPERIMENTS_ENABLED", True)
        from distillate.state import State
        state = State()
        state.add_project("proj-1", "Test Project", str(tmp_path))
        from distillate.agent_core import _experiments_section
        section = _experiments_section(state)
        assert "new commit" not in section
        assert "Test Project" in section


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
        from distillate.experiments import watch_project_artifacts
        changes = watch_project_artifacts(tmp_path)
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

        from distillate.experiments import watch_project_artifacts

        # First watch — picks up everything
        changes = watch_project_artifacts(tmp_path)
        event_changes = [c for c in changes if c.get("_source_file") == "events.jsonl"]
        assert len(event_changes) == 1

        # No changes → empty
        changes = watch_project_artifacts(tmp_path)
        event_changes = [c for c in changes if c.get("_source_file") == "events.jsonl"]
        assert len(event_changes) == 0

        # Append new event
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "run_completed", "ts": "2026-03-09T05:00:00Z",
                               "command": "python3 train.py lr=0.02",
                               "hyperparameters": {"lr": 0.02},
                               "results": {"loss": 0.05}, "session_id": "def"}) + "\n")

        # Should detect the new line
        changes = watch_project_artifacts(tmp_path)
        event_changes = [c for c in changes if c.get("_source_file") == "events.jsonl"]
        assert len(event_changes) == 1


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
# Decision-aware notebook tests
# ---------------------------------------------------------------------------


class TestDecisionNotebook:
    """Test decision column and reasoning in generated notebooks."""

    def _make_project_with_decisions(self):
        return {
            "name": "Test Project",
            "path": "/tmp/test",
            "description": "A test project",
            "goals": [],
            "linked_papers": [],
            "runs": {
                "sr-aaa": {
                    "id": "sr-aaa", "name": "run_001", "status": "completed",
                    "decision": "best", "hypothesis": "Larger model",
                    "hyperparameters": {"d_model": 128},
                    "results": {"val_bpb": 0.912},
                    "agent_reasoning": "val_bpb improved significantly",
                    "tags": [], "notes": [], "started_at": "2026-03-09T04:00:00Z",
                    "completed_at": "2026-03-09T04:05:00Z", "duration_minutes": 5,
                    "git_commits": [], "files_created": [],
                },
                "sr-bbb": {
                    "id": "sr-bbb", "name": "run_002", "status": "completed",
                    "decision": "completed", "hypothesis": "Even larger model",
                    "hyperparameters": {"d_model": 256},
                    "results": {"val_bpb": 0.950},
                    "agent_reasoning": "val_bpb regressed, reverting",
                    "tags": [], "notes": [], "started_at": "2026-03-09T05:00:00Z",
                    "completed_at": "2026-03-09T05:10:00Z", "duration_minutes": 10,
                    "git_commits": [], "files_created": [],
                },
                "sr-ccc": {
                    "id": "sr-ccc", "name": "run_003", "status": "failed",
                    "decision": "crash", "hypothesis": "Tiny model",
                    "hyperparameters": {"d_model": 4},
                    "results": {},
                    "agent_reasoning": "OOM error",
                    "tags": [], "notes": [], "started_at": "2026-03-09T06:00:00Z",
                    "completed_at": "2026-03-09T06:01:00Z", "duration_minutes": 1,
                    "git_commits": [], "files_created": [],
                },
            },
        }

    def test_md_notebook_has_decision_column(self):
        from distillate.experiments import generate_notebook
        project = self._make_project_with_decisions()
        md = generate_notebook(project)
        assert "Decision" in md
        assert "★ best" in md
        assert "✓ completed" in md
        assert "⚠ crash" in md
        assert "**1** best" in md
        assert "**1** crashed" in md

    def test_md_notebook_has_reasoning(self):
        from distillate.experiments import generate_notebook
        project = self._make_project_with_decisions()
        md = generate_notebook(project)
        assert "Agent Reasoning" in md
        assert "val_bpb improved significantly" in md

    def test_html_notebook_has_decision_column(self):
        from distillate.experiments import generate_html_notebook
        project = self._make_project_with_decisions()
        html = generate_html_notebook(project)
        assert "Decision" in html
        assert "decision-best" in html
        assert "decision-completed" in html
        assert "decision-crash" in html
        assert "Best" in html

    def test_html_notebook_has_reasoning_block(self):
        from distillate.experiments import generate_html_notebook
        project = self._make_project_with_decisions()
        html = generate_html_notebook(project)
        assert "reasoning-block" in html
        assert "val_bpb improved significantly" in html

    def test_html_notebook_has_metric_chart(self):
        from distillate.experiments import generate_html_notebook
        project = self._make_project_with_decisions()
        html = generate_html_notebook(project)
        assert "Metric Progression" in html
        assert "<svg" in html
        assert "polyline" in html
        # Green for best, gray for completed
        assert "#3fb950" in html
        assert "#555555" in html

    def test_no_decision_column_without_decisions(self):
        """Projects without decisions should use the original status column."""
        from distillate.experiments import generate_notebook, generate_html_notebook
        project = {
            "name": "Plain Project",
            "path": "/tmp/test",
            "goals": [], "linked_papers": [],
            "runs": {
                "exp-aaa": {
                    "id": "exp-aaa", "name": "run_1", "status": "completed",
                    "hyperparameters": {"lr": 0.01},
                    "results": {"loss": 0.1},
                    "tags": [], "notes": [], "started_at": "2026-03-09T04:00:00Z",
                    "completed_at": "2026-03-09T04:05:00Z", "duration_minutes": 5,
                    "git_commits": [], "files_created": [],
                },
            },
        }
        md = generate_notebook(project)
        assert "| Status |" in md
        assert "| Decision |" not in md
        html = generate_html_notebook(project)
        assert ">Decision<" not in html


# ---------------------------------------------------------------------------
# Metric chart rendering tests
# ---------------------------------------------------------------------------


class TestMetricChart:
    """Test the SVG metric chart renderer."""

    def test_render_chart_with_decisions(self):
        from distillate.experiments import _render_metric_chart
        runs = [
            {"results": {"val_bpb": 0.95}, "decision": "best"},
            {"results": {"val_bpb": 0.91}, "decision": "best"},
            {"results": {"val_bpb": 0.93}, "decision": "completed"},
        ]
        svg = _render_metric_chart(runs)
        assert "<svg" in svg
        assert "polyline" in svg
        assert "#3fb950" in svg  # green for best
        assert "#555555" in svg  # gray for completed

    def test_no_chart_with_single_run(self):
        from distillate.experiments import _render_metric_chart
        runs = [{"results": {"val_bpb": 0.95}, "decision": "best"}]
        svg = _render_metric_chart(runs)
        assert svg == ""

    def test_no_chart_without_metrics(self):
        from distillate.experiments import _render_metric_chart
        runs = [
            {"results": {}, "decision": "best"},
            {"results": {}, "decision": "completed"},
        ]
        svg = _render_metric_chart(runs)
        assert svg == ""


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
    """Test that scan_project() integrates structured + hook runs."""

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

        from distillate.experiments import scan_project
        result = scan_project(tmp_path)
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

        from distillate.experiments import scan_project
        result = scan_project(tmp_path)
        assert "error" not in result
        runs = result["runs"]
        hook_runs = [r for r in runs.values() if r.get("source") == "hooks"]
        assert len(hook_runs) == 1

    def test_structured_run_deduplicates_artifact_scanned(self, tmp_path):
        """Structured runs from runs.jsonl should replace artifact-scanned
        duplicates that share the same hyperparameters (double-curve bug).

        Without dedup, scan_project() returns two runs for the same experiment
        — one from artifact scanning (hash-based ID) and one from runs.jsonl
        (sr- prefixed ID) — causing a double curve on the chart.
        """
        # Create an artifact that scan_project will discover
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

        from distillate.experiments import scan_project
        result = scan_project(tmp_path)
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

        from distillate.experiments import scan_project
        result = scan_project(tmp_path)
        assert "error" not in result

        structured = [r for r in result["runs"].values()
                      if r.get("source") == "structured"]
        assert len(structured) == 3, (
            f"Expected 3 structured runs but got {len(structured)}: "
            f"{[r['name'] for r in structured]}"
        )


# ---------------------------------------------------------------------------
# start_run / conclude_run / save_enrichment / purge_hook_runs /
# discover_relevant_papers tool tests
# ---------------------------------------------------------------------------


def _make_state_with_path(tmp_path, monkeypatch):
    """Create a State with a project whose path is a real tmp_path dir."""
    monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")
    monkeypatch.setattr("distillate.config.OUTPUT_PATH", "")
    monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "")
    from distillate.state import State
    state = State()
    proj_dir = tmp_path / "my-project"
    proj_dir.mkdir()
    state.add_project("test-proj", "Test Project", str(proj_dir))
    state.save()
    return state, proj_dir


class TestStartRun:
    def test_start_run_creates_jsonl_entry(self, tmp_path, monkeypatch):
        state, proj_dir = _make_state_with_path(tmp_path, monkeypatch)
        from distillate.experiment_tools import start_run
        result = start_run(state=state, project="test-proj",
                           description="Baseline training")
        assert result["success"] is True
        assert result["run_id"].startswith("xp-")
        assert "started_at" in result
        # Verify the jsonl file was created with correct content
        runs_jsonl = proj_dir / ".distillate" / "runs.jsonl"
        assert runs_jsonl.exists()
        entries = [json.loads(l) for l in runs_jsonl.read_text().splitlines() if l.strip()]
        assert len(entries) == 1
        assert entries[0]["id"] == result["run_id"]
        assert entries[0]["status"] == "running"
        assert entries[0]["description"] == "Baseline training"
        assert entries[0]["$schema"] == "distillate/run/v1"

    def test_start_run_auto_increments_id(self, tmp_path, monkeypatch):
        state, proj_dir = _make_state_with_path(tmp_path, monkeypatch)
        from distillate.experiment_tools import start_run
        r1 = start_run(state=state, project="test-proj", description="Run 1")
        assert r1["run_id"].startswith("xp-")
        r2 = start_run(state=state, project="test-proj", description="Run 2")
        assert r2["run_id"].startswith("xp-")
        assert r1["run_id"] != r2["run_id"]
        # Verify both entries in the file
        runs_jsonl = proj_dir / ".distillate" / "runs.jsonl"
        entries = [json.loads(l) for l in runs_jsonl.read_text().splitlines() if l.strip()]
        assert len(entries) == 2
        assert entries[0]["id"] == r1["run_id"]
        assert entries[1]["id"] == r2["run_id"]

    def test_start_run_invalid_project(self, tmp_path, monkeypatch):
        state, _ = _make_state_with_path(tmp_path, monkeypatch)
        from distillate.experiment_tools import start_run
        result = start_run(state=state, project="nonexistent",
                           description="Should fail")
        assert "error" in result


class TestConcludeRun:
    def test_conclude_run_appends_completed_entry(self, tmp_path, monkeypatch):
        state, proj_dir = _make_state_with_path(tmp_path, monkeypatch)
        from distillate.experiment_tools import start_run, conclude_run
        start_run(state=state, project="test-proj", description="Baseline")
        result = conclude_run(
            state=state, project="test-proj", run_id="run_001",
            status="keep", results={"accuracy": 0.95},
            reasoning="Good convergence",
        )
        assert result["success"] is True
        assert result["run_id"] == "run_001"
        assert result["status"] == "best"
        # Verify both entries exist in the jsonl
        runs_jsonl = proj_dir / ".distillate" / "runs.jsonl"
        entries = [json.loads(l) for l in runs_jsonl.read_text().splitlines() if l.strip()]
        assert len(entries) == 2
        concluded = entries[1]
        assert concluded["status"] == "best"
        assert concluded["results"] == {"accuracy": 0.95}
        assert concluded["reasoning"] == "Good convergence"
        assert "completed_at" in concluded

    def test_conclude_run_computes_duration(self, tmp_path, monkeypatch):
        state, proj_dir = _make_state_with_path(tmp_path, monkeypatch)
        from distillate.experiment_tools import start_run, conclude_run
        start_result = start_run(state=state, project="test-proj", description="Timed run")
        run_id = start_result["run_id"]
        # The start and conclude happen almost instantly, so duration_seconds
        # should be 0 or a small integer (within the same second).
        result = conclude_run(
            state=state, project="test-proj", run_id=run_id,
            results={"loss": 0.01}, reasoning="Fast run",
        )
        assert result["success"] is True
        # Check the jsonl entry has duration_seconds
        runs_jsonl = proj_dir / ".distillate" / "runs.jsonl"
        entries = [json.loads(l) for l in runs_jsonl.read_text().splitlines() if l.strip()]
        concluded = entries[1]
        assert "duration_seconds" in concluded
        assert "started_at" in concluded
        assert isinstance(concluded["duration_seconds"], int)
        assert concluded["duration_seconds"] >= 0

    def test_conclude_run_invalid_run_id(self, tmp_path, monkeypatch):
        """Conclude works gracefully even without a matching start entry."""
        state, proj_dir = _make_state_with_path(tmp_path, monkeypatch)
        # Create .distillate dir (normally start_run creates it)
        (proj_dir / ".distillate").mkdir(parents=True, exist_ok=True)
        from distillate.experiment_tools import conclude_run
        # No start_run call — conclude a run that was never started
        result = conclude_run(
            state=state, project="test-proj", run_id="run_999",
            status="keep", results={"accuracy": 0.5},
            reasoning="Orphan conclude",
        )
        assert result["success"] is True
        assert result["run_id"] == "run_999"
        # Entry written but without duration_seconds (no start entry found)
        runs_jsonl = proj_dir / ".distillate" / "runs.jsonl"
        entries = [json.loads(l) for l in runs_jsonl.read_text().splitlines() if l.strip()]
        assert len(entries) == 1
        assert "duration_seconds" not in entries[0]


class TestSaveEnrichment:
    def test_save_enrichment_writes_json(self, tmp_path, monkeypatch):
        state, proj_dir = _make_state_with_path(tmp_path, monkeypatch)
        from distillate.experiment_tools import save_enrichment
        result = save_enrichment(
            state=state, project="test-proj",
            key_breakthrough="Found optimal LR schedule",
            trajectory="Loss decreasing steadily",
        )
        assert result["success"] is True
        assert "path" in result
        # Verify the file contents
        cache_path = proj_dir / ".distillate" / "llm_enrichment.json"
        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert "fingerprint" in data
        assert "enrichment" in data
        assert data["enrichment"]["project"]["key_breakthrough"] == "Found optimal LR schedule"
        assert data["enrichment"]["project"]["trajectory"] == "Loss decreasing steadily"

    def test_save_enrichment_project_insights(self, tmp_path, monkeypatch):
        state, proj_dir = _make_state_with_path(tmp_path, monkeypatch)
        from distillate.experiment_tools import save_enrichment
        result = save_enrichment(
            state=state, project="test-proj",
            key_breakthrough="Batch norm helps convergence",
            lessons_learned=["Smaller LR better", "Warmup crucial"],
            dead_ends=["SGD diverged"],
            run_insights={"run_001": {"quality": "good"}},
        )
        assert result["success"] is True
        cache_path = proj_dir / ".distillate" / "llm_enrichment.json"
        data = json.loads(cache_path.read_text())
        proj_insights = data["enrichment"]["project"]
        assert proj_insights["key_breakthrough"] == "Batch norm helps convergence"
        assert proj_insights["lessons_learned"] == ["Smaller LR better", "Warmup crucial"]
        assert proj_insights["dead_ends"] == ["SGD diverged"]
        assert data["enrichment"]["runs"] == {"run_001": {"quality": "good"}}


class TestPurgeHookRuns:
    def test_purge_hook_runs_requires_confirm(self, tmp_path, monkeypatch):
        state, _ = _make_state_with_path(tmp_path, monkeypatch)
        # Add hook-sourced runs
        state.add_run("test-proj", "hook-1", {
            "id": "hook-1", "name": "Hook run", "status": "completed",
            "source": "hooks", "results": {},
        })
        state.save()
        from distillate.experiment_tools import purge_hook_runs_tool
        result = purge_hook_runs_tool(state=state, project="test-proj", confirm=False)
        assert result["confirm_required"] is True
        assert result["hook_runs"] == 1
        # Run should still exist
        assert state.get_run("test-proj", "hook-1") is not None

    def test_purge_hook_runs_deletes_with_confirm(self, tmp_path, monkeypatch):
        state, _ = _make_state_with_path(tmp_path, monkeypatch)
        # Add a hook run and a normal run
        state.add_run("test-proj", "hook-1", {
            "id": "hook-1", "name": "Hook run", "status": "completed",
            "source": "hooks", "results": {},
        })
        state.add_run("test-proj", "manual-1", {
            "id": "manual-1", "name": "Manual run", "status": "completed",
            "source": "manual", "results": {},
        })
        state.save()
        from distillate.experiment_tools import purge_hook_runs_tool
        result = purge_hook_runs_tool(state=state, project="test-proj", confirm=True)
        assert result["success"] is True
        assert result["removed"] == 1
        assert result["remaining"] == 1
        # Hook run gone, manual run stays
        assert state.get_run("test-proj", "hook-1") is None
        assert state.get_run("test-proj", "manual-1") is not None


class TestDiscoverRelevantPapers:
    def test_discover_finds_matching_papers(self, tmp_path, monkeypatch):
        state, proj_dir = _make_state_with_path(tmp_path, monkeypatch)
        # Set project description with searchable keywords
        state.update_project("test-proj",
                             description="transformer architecture attention mechanism")
        # Add a processed paper that matches
        state.add_document("ZOT001", "ATT001", "md5a", "doc1",
                           "Attention Is All You Need",
                           ["Vaswani"], status="processed",
                           metadata={"citekey": "vaswani2017",
                                     "tags": ["transformer", "attention"]})
        # Add a paper that doesn't match
        state.add_document("ZOT002", "ATT002", "md5b", "doc2",
                           "ImageNet Classification with Deep CNNs",
                           ["Krizhevsky"], status="processed",
                           metadata={"citekey": "krizhevsky2012",
                                     "tags": ["cnn", "vision"]})
        state.save()
        from distillate.experiment_tools import discover_relevant_papers
        result = discover_relevant_papers(state=state, project="test-proj")
        assert "candidates" in result
        assert len(result["candidates"]) >= 1
        # The transformer/attention paper should be a candidate
        citekeys = [c["citekey"] for c in result["candidates"]]
        assert "vaswani2017" in citekeys
        # Check candidate structure
        match = [c for c in result["candidates"] if c["citekey"] == "vaswani2017"][0]
        assert "match_count" in match
        assert match["match_count"] >= 2
        assert "matched_keywords" in match

    def test_discover_empty_library(self, tmp_path, monkeypatch):
        state, _ = _make_state_with_path(tmp_path, monkeypatch)
        state.update_project("test-proj",
                             description="transformer architecture")
        state.save()
        from distillate.experiment_tools import discover_relevant_papers
        result = discover_relevant_papers(state=state, project="test-proj")
        assert result["candidates"] == []
