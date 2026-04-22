# Covers: distillate/state.py, distillate/experiments.py (CRUD, classification, model tags, run diffing, notebook generation, slugify, project/run helpers)

"""Tests for project/experiment/run create-read-update-delete operations, file classification,
model tags, run diffing, notebook generation, slugify, and State helper methods."""

import json
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
        assert state.experiments == {}

    def test_add_and_get_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("my-project", "My Project", "/tmp/my-project")
        assert state.has_experiment("my-project")
        proj = state.get_experiment("my-project")
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
        state.add_experiment("proj-a", "A", "/a")
        state.add_experiment("proj-b", "B", "/b")
        assert state.experiment_index_of("proj-a") == 1
        assert state.experiment_index_of("proj-b") == 2
        assert state.experiment_index_of("nonexistent") == 0

    def test_find_project_by_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("tiny-gene-code", "Tiny Gene Code", "/path")
        assert state.find_experiment("tiny-gene-code")["name"] == "Tiny Gene Code"

    def test_find_project_by_index(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("proj-a", "A", "/a")
        assert state.find_experiment("1")["name"] == "A"

    def test_find_project_by_name_substring(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("tiny-gene-code", "Tiny Gene Code", "/path")
        assert state.find_experiment("gene")["name"] == "Tiny Gene Code"
        assert state.find_experiment("GENE")["name"] == "Tiny Gene Code"

    def test_find_project_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        assert state.find_experiment("nonexistent") is None
        assert state.find_experiment("") is None

    def test_update_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("my-proj", "My Proj", "/path")
        state.update_experiment("my-proj", description="A cool project", status="archived")
        proj = state.get_experiment("my-proj")
        assert proj["description"] == "A cool project"
        assert proj["status"] == "archived"

    def test_remove_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("my-proj", "My Proj", "/path")
        assert state.remove_experiment("my-proj") is True
        assert state.has_experiment("my-proj") is False
        assert state.remove_experiment("nonexistent") is False

    def test_add_and_get_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("my-proj", "My Proj", "/path")
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
        state.add_experiment("my-proj", "My Proj", "/path")
        state.add_run("my-proj", "exp-1", {"id": "exp-1", "status": "running"})
        state.update_run("my-proj", "exp-1", status="completed")
        assert state.get_run("my-proj", "exp-1")["status"] == "completed"

    def test_get_run_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        assert state.get_run("no-proj", "no-run") is None
        state.add_experiment("my-proj", "My Proj", "/path")
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
        assert state.experiments == {}
        # Should be able to add projects
        state.add_experiment("test", "Test", "/path")
        assert state.has_experiment("test")

    def test_save_and_reload_projects(self, tmp_path, monkeypatch):
        state_path = tmp_path / "state.json"
        monkeypatch.setattr("distillate.state.STATE_PATH", state_path)
        from distillate.state import State
        state = State()
        state.add_experiment("my-proj", "My Proj", "/path")
        state.add_run("my-proj", "exp-1", {"id": "exp-1", "name": "Run 1"})
        state.save()

        state2 = State()
        assert state2.has_experiment("my-proj")
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


