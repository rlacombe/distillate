# Covers: distillate/experiments.py, distillate/obsidian.py, distillate/agent_core.py

"""Tests for HTML notebook generation, decision notebooks, Obsidian output, and experiments section."""


# ---------------------------------------------------------------------------
# Shared constants
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


# ---------------------------------------------------------------------------
# Markdown notebook tests (enrichment + factorize)
# ---------------------------------------------------------------------------


class TestMarkdownNotebook:
    """Tests for generate_notebook (Markdown output)."""

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
# Experiments section (agent_core) tests
# ---------------------------------------------------------------------------


class TestExperimentsSection:
    """Test _experiments_section in agent_core."""

    def test_includes_new_commits(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.config.EXPERIMENTS_ENABLED", True)
        from distillate.state import State
        state = State()
        state.add_experiment("proj-1", "Test Project", str(tmp_path))
        from distillate.agent_core import _experiments_section
        updates = [{"project": {"id": "proj-1", "name": "Test Project"}, "new_commits": 3, "current_hash": "abc"}]
        section = _experiments_section(state, updates=updates)
        assert "3 new commits" in section

    def test_no_updates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.config.EXPERIMENTS_ENABLED", True)
        from distillate.state import State
        state = State()
        state.add_experiment("proj-1", "Test Project", str(tmp_path))
        from distillate.agent_core import _experiments_section
        section = _experiments_section(state)
        assert "new commit" not in section
        assert "Test Project" in section
