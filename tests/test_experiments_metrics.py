# Covers: distillate/experiments.py, distillate/experiment_tools.py

"""Tests for metric chart rendering and annotate-run tool."""


# ---------------------------------------------------------------------------
# Shared state helpers
# ---------------------------------------------------------------------------


def _make_state(tmp_path, monkeypatch):
    """Create a State with a project and two runs for tool tests."""
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
