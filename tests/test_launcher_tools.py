# Covers: distillate/experiment_tools.py — experiment tool schemas and tool functions

import pytest


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

class TestExperimentToolSchemas:
    def test_schema_count(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        assert len(EXPERIMENT_TOOL_SCHEMAS) == 70

    def test_new_tool_names(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        names = {s["name"] for s in EXPERIMENT_TOOL_SCHEMAS}
        assert "launch_experiment" in names
        assert "experiment_status" in names
        assert "stop_experiment" in names
        assert "compare_experiments" in names
        assert "queue_sessions" in names
        assert "list_templates" in names
        assert "save_template" in names
        assert "create_github_repo" in names
        assert "reading_report" in names

    def test_all_schemas_have_required_fields(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        for schema in EXPERIMENT_TOOL_SCHEMAS:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema


# ---------------------------------------------------------------------------
# Core experiment tools
# ---------------------------------------------------------------------------

class TestLaunchExperimentTool:
    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import launch_experiment_tool
        from distillate.state import State

        state = State()
        result = launch_experiment_tool(state=state, project="nonexistent")
        assert "error" in result

    def test_no_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import launch_experiment_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Project 1", "")
        result = launch_experiment_tool(state=state, project="p1")
        assert "error" in result
        assert "no path" in result["error"].lower()


class TestExperimentStatusTool:
    def test_all_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.session_status", lambda n, h=None: "running")

        from distillate.experiment_tools import experiment_status_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Exp 1", str(tmp_path))
        state.add_session("p1", "s1", {"status": "running", "tmux_session": "t1"})

        result = experiment_status_tool(state=state)
        assert result["total_active_sessions"] == 1
        assert len(result["experiments"]) == 1

    def test_specific_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.session_status", lambda n, h=None: "completed")

        from distillate.experiment_tools import experiment_status_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Exp 1", str(tmp_path))
        state.add_experiment("p2", "Exp 2", str(tmp_path))

        result = experiment_status_tool(state=state, project="p1")
        assert len(result["experiments"]) == 1
        assert result["experiments"][0]["name"] == "Exp 1"


class TestStopExperimentTool:
    def test_no_running_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")

        from distillate.experiment_tools import stop_experiment_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Exp 1", str(tmp_path))
        result = stop_experiment_tool(state=state, project="p1")
        assert "error" in result

    def test_stops_running(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.stop_session", lambda n, h=None: True)

        from distillate.experiment_tools import stop_experiment_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Exp 1", str(tmp_path))
        state.add_session("p1", "s1", {"status": "running", "tmux_session": "t1"})
        state.save()

        result = stop_experiment_tool(state=state, project="p1")
        assert result["success"] is True
        assert "t1" in result["stopped"]


# ---------------------------------------------------------------------------
# Comparison, queuing, and template tools
# ---------------------------------------------------------------------------

class TestCompareProjectsTool:
    def test_needs_two_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import compare_experiments_tool
        from distillate.state import State

        state = State()
        result = compare_experiments_tool(state=state, projects=["p1"])
        assert "error" in result

    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import compare_experiments_tool
        from distillate.state import State

        state = State()
        result = compare_experiments_tool(state=state, projects=["a", "b"])
        assert "error" in result

    def test_compares_two_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import compare_experiments_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.add_experiment("p2", "Proj 2", str(tmp_path))

        # Add kept runs with metrics
        state._data["experiments"]["p1"]["runs"] = {
            "r1": {"status": "completed", "decision": "best",
                    "results": {"accuracy": 0.85, "loss": 0.3}},
        }
        state._data["experiments"]["p2"]["runs"] = {
            "r1": {"status": "completed", "decision": "best",
                    "results": {"accuracy": 0.92, "loss": 0.15}},
        }
        state.save()

        result = compare_experiments_tool(state=state, projects=["p1", "p2"])
        assert "experiments" in result
        assert len(result["experiments"]) == 2
        assert "metrics" in result
        assert "accuracy" in result["metrics"]
        assert "loss" in result["metrics"]
        assert result["experiments"][0]["name"] == "Proj 1"
        assert result["experiments"][0]["best_metrics"]["accuracy"] == 0.85
        assert result["experiments"][1]["best_metrics"]["accuracy"] == 0.92

    def test_skips_crash_runs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import compare_experiments_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.add_experiment("p2", "Proj 2", str(tmp_path))
        state._data["experiments"]["p1"]["runs"] = {
            "r1": {"status": "completed", "decision": "crash",
                    "results": {"accuracy": 0.99}},
        }
        state._data["experiments"]["p2"]["runs"] = {}
        state.save()

        result = compare_experiments_tool(state=state, projects=["p1", "p2"])
        assert result["experiments"][0]["best_metrics"] == {}
        assert result["metrics"] == []


class TestQueueSessionsTool:
    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import queue_sessions_tool
        from distillate.state import State

        state = State()
        result = queue_sessions_tool(state=state, project="nope")
        assert "error" in result

    def test_queues_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import queue_sessions_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        result = queue_sessions_tool(state=state, project="p1", count=3)
        assert result["success"] is True
        assert result["queued"] == 3
        assert "Proj 1" in result["message"]

        # Verify state was updated
        state.reload()
        proj = state.get_experiment("p1")
        assert proj["continuation_queue"]["count"] == 3
        assert proj["auto_continue"] is True

    def test_custom_model_and_turns(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import queue_sessions_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        result = queue_sessions_tool(
            state=state, project="p1", count=2,
            model="claude-opus-4-20250514", max_turns=50,
        )
        assert result["success"] is True
        assert result["model"] == "claude-opus-4-20250514"
        assert result["max_turns"] == 50


class TestListTemplatesTool:
    def test_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        (tmp_path / "templates").mkdir()
        from distillate.experiment_tools import list_templates_tool
        from distillate.state import State

        result = list_templates_tool(state=State())
        assert result["templates"] == []
        assert result["total"] == 0

    def test_returns_templates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        tmpl_dir = tmp_path / "templates" / "mlp-basic"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "PROMPT.md").write_text("line1\nline2\n")

        from distillate.experiment_tools import list_templates_tool
        from distillate.state import State

        result = list_templates_tool(state=State())
        assert result["total"] == 1
        assert result["templates"][0]["name"] == "mlp-basic"


class TestSaveTemplateTool:
    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import save_template_tool
        from distillate.state import State

        result = save_template_tool(state=State(), project="nope")
        assert "error" in result

    def test_no_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import save_template_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Proj 1", "")
        result = save_template_tool(state=state, project="p1")
        assert "error" in result

    def test_saves_template(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")

        # Create a project directory with PROMPT.md
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        (proj_dir / "PROMPT.md").write_text("# Experiment\nDo the thing.\n")

        from distillate.experiment_tools import save_template_tool
        from distillate.state import State

        state = State()
        state.add_experiment("my-project", "My Project", str(proj_dir))
        result = save_template_tool(state=state, project="my-project", name="my-tmpl")
        assert result["success"] is True
        assert result["template_name"] == "my-tmpl"
        # Verify file was created
        assert (tmp_path / "config" / "templates" / "my-tmpl" / "PROMPT.md").exists()


class TestCreateGithubRepoTool:
    def test_project_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import create_github_repo_tool
        from distillate.state import State

        result = create_github_repo_tool(state=State(), project="nope")
        assert "error" in result

    def test_no_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import create_github_repo_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Proj 1", "")
        result = create_github_repo_tool(state=state, project="p1")
        assert "error" in result

    def test_calls_launcher_and_updates_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr(
            "distillate.launcher.create_github_repo",
            lambda path, name, private=True: {"ok": True, "url": "https://github.com/test/repo"},
        )
        from distillate.experiment_tools import create_github_repo_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        result = create_github_repo_tool(state=state, project="p1", name="repo")
        assert result["ok"] is True
        assert result["url"] == "https://github.com/test/repo"

        # Verify github_url saved to state
        state.reload()
        proj = state.get_experiment("p1")
        assert proj["github_url"] == "https://github.com/test/repo"

    def test_error_from_launcher(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr(
            "distillate.launcher.create_github_repo",
            lambda path, name, private=True: {"ok": False, "reason": "gh not installed"},
        )
        from distillate.experiment_tools import create_github_repo_tool
        from distillate.state import State

        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        result = create_github_repo_tool(state=state, project="p1")
        assert result["ok"] is False
        assert "gh not installed" in result["reason"]


class TestReadingReportTool:
    def test_no_papers(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import reading_report_tool
        from distillate.state import State

        result = reading_report_tool(state=State())
        assert "message" in result

    def test_returns_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.experiment_tools import reading_report_tool
        from distillate.state import State

        state = State()
        # Add some processed papers
        state._data["documents"] = {
            "K1": {
                "title": "Paper One", "status": "processed",
                "zotero_item_key": "K1",
                "page_count": 10, "highlight_word_count": 200,
                "engagement": 75,
                "authors": ["Alice", "Bob"],
                "processed_at": "2026-03-10T12:00:00+00:00",
                "metadata": {
                    "tags": ["transformers", "nlp"],
                    "citation_count": 500,
                },
            },
            "K2": {
                "title": "Paper Two", "status": "processed",
                "zotero_item_key": "K2",
                "page_count": 8, "highlight_word_count": 150,
                "engagement": 50,
                "authors": ["Alice", "Charlie"],
                "processed_at": "2026-03-12T12:00:00+00:00",
                "metadata": {
                    "tags": ["transformers", "vision"],
                    "citation_count": 100,
                },
            },
        }
        state.save()

        result = reading_report_tool(state=state)
        assert result["lifetime"]["papers"] == 2
        assert result["lifetime"]["pages"] == 18
        assert result["lifetime"]["words_highlighted"] == 350
        assert result["lifetime"]["avg_engagement"] == 62  # round((75+50)/2)
        assert len(result["top_topics"]) >= 1
        assert result["top_topics"][0]["topic"] == "transformers"
        assert result["top_topics"][0]["count"] == 2
        assert len(result["most_cited"]) == 2
        assert result["most_cited"][0]["citations"] == 500
        assert len(result["top_authors"]) >= 1
        # Alice appears in 2 papers
        alice = [a for a in result["top_authors"] if a["author"] == "Alice"]
        assert len(alice) == 1
        assert alice[0]["count"] == 2
