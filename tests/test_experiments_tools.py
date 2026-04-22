# Covers: distillate/experiment_tools.py, distillate/obsidian.py (tool schemas, rename/delete/update/link/goals/annotate tools, Obsidian notebook writing)

"""Tests for experiment tool schemas and tool-level operations (rename, delete, update,
link paper, update goals) plus Obsidian notebook writing."""


# ---------------------------------------------------------------------------
# Tool schema tests
# ---------------------------------------------------------------------------


class TestExperimentToolSchemas:
    def test_all_schemas_valid(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        assert len(EXPERIMENT_TOOL_SCHEMAS) >= 37
        for schema in EXPERIMENT_TOOL_SCHEMAS:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"

    def test_schema_names(self):
        from distillate.experiment_tools import EXPERIMENT_TOOL_SCHEMAS
        names = {s["name"] for s in EXPERIMENT_TOOL_SCHEMAS}
        assert names == {
            "list_experiments", "get_experiment_details", "compare_runs",
            "scan_experiment", "get_experiment_notebook",
            "add_experiment", "rename_experiment", "rename_run",
            "delete_experiment", "delete_run", "update_experiment",
            "get_run_details", "link_paper", "update_goals", "annotate_run",
            "launch_experiment", "experiment_status", "stop_experiment",
            "init_experiment", "continue_experiment", "sweep_experiment",
            "steer_experiment", "ask_experimentalist",
            "compare_experiments", "queue_sessions", "list_templates",
            "save_template", "create_github_repo", "reading_report",
            "manage_session",
            "replicate_paper", "suggest_from_literature", "extract_baselines",
            "save_enrichment", "start_run", "conclude_run",
            "purge_hook_runs", "discover_relevant_papers",
            "submit_hf_job", "check_hf_job", "list_hf_jobs", "cancel_hf_job",
            # Workspace tools
            "create_workspace", "list_workspaces", "get_workspace",
            "add_workspace_repo", "launch_coding_session",
            "launch_writing_session", "launch_survey_session",
            "create_work_item", "list_work_items", "complete_work_item",
            "get_workspace_notes", "save_workspace_notes", "append_lab_book",
            "stop_coding_session", "restart_coding_session",
            "recover_coding_session", "recover_all_sessions",
            "stop_all_sessions",
            # Agent tools
            "create_agent", "list_agents", "list_agent_templates",
            "start_agent_session", "stop_agent_session",
            "update_agent", "delete_agent",
            # Lab notebook tools
            "read_lab_notebook", "notebook_digest",
            # Lab REPL + thread management
            "lab_repl", "set_thread_name",
        }


# ---------------------------------------------------------------------------
# Obsidian notebook writing tests
# ---------------------------------------------------------------------------


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
# State remove_run tests
# ---------------------------------------------------------------------------


class TestRemoveRun:
    def test_remove_existing_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("proj", "Proj", "/path")
        state.add_run("proj", "run-1", {"id": "run-1", "name": "Run 1"})
        assert state.remove_run("proj", "run-1") is True
        assert state.get_run("proj", "run-1") is None

    def test_remove_nonexistent_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        state.add_experiment("proj", "Proj", "/path")
        assert state.remove_run("proj", "nope") is False

    def test_remove_run_nonexistent_project(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        state = State()
        assert state.remove_run("nope", "run-1") is False


# ---------------------------------------------------------------------------
# Resolve-project and find-run helpers
# ---------------------------------------------------------------------------


class TestResolveProject:
    def test_resolve_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        from distillate.state import State
        from distillate.experiment_tools import _resolve_project
        state = State()
        state.add_experiment("my-proj", "My Proj", "/path")
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
        state.add_experiment("ml-proj-a", "ML Project A", "/a")
        state.add_experiment("ml-proj-b", "ML Project B", "/b")
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
    state.add_experiment("test-proj", "Test Project", str(tmp_path / "fake-dir"))
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
        from distillate.experiment_tools import rename_experiment_tool
        result = rename_experiment_tool(state=state, identifier="test-proj", new_name="Better Name")
        assert result["success"] is True
        assert result["old_name"] == "Test Project"
        assert result["new_name"] == "Better Name"
        assert state.has_experiment("better-name")
        assert not state.has_experiment("test-proj")

    def test_rename_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import rename_experiment_tool
        result = rename_experiment_tool(state=state, identifier="nope", new_name="X")
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
        state.add_experiment("test-project", "Test Project", str(tmp_path))
        state.save()
        from distillate.experiment_tools import rename_experiment_tool
        result = rename_experiment_tool(state=state, identifier="test-project", new_name="Test Project")
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
        from distillate.experiment_tools import delete_experiment_tool
        result = delete_experiment_tool(state=state, identifier="test-proj", confirm=False)
        assert result["confirm_required"] is True
        assert state.has_experiment("test-proj")  # not deleted yet

    def test_delete_confirm(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import delete_experiment_tool
        result = delete_experiment_tool(state=state, identifier="test-proj", confirm=True)
        assert result["success"] is True
        assert not state.has_experiment("test-proj")

    def test_delete_not_found(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import delete_experiment_tool
        result = delete_experiment_tool(state=state, identifier="nope")
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
        assert state.get_experiment("test-proj")["description"] == "New desc"

    def test_update_tags(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_project_tool
        result = update_project_tool(state=state, identifier="test-proj", tags=["nlp", "transformers"])
        assert result["success"] is True
        assert state.get_experiment("test-proj")["tags"] == ["nlp", "transformers"]

    def test_update_status(self, tmp_path, monkeypatch):
        state = _make_state(tmp_path, monkeypatch)
        from distillate.experiment_tools import update_project_tool
        result = update_project_tool(state=state, identifier="test-proj", status="archived")
        assert result["success"] is True
        assert state.get_experiment("test-proj")["status"] == "archived"

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
        proj = state.get_experiment("test-proj")
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
        state.update_experiment("test-proj", linked_papers=["smith2026"])
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
        proj = state.get_experiment("test-proj")
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
