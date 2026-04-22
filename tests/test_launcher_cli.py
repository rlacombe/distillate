# Covers: distillate/main.py, distillate/commands.py — CLI commands for experiment management

import pytest


# ---------------------------------------------------------------------------
# CLI command tests — main.py
# ---------------------------------------------------------------------------

class TestNewExperimentCLI:
    def test_no_templates_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("distillate.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        (tmp_path / "templates").mkdir()

        from distillate.main import _new_experiment
        _new_experiment([])

        output = capsys.readouterr().out
        assert "No templates available" in output


class TestListExperimentsCLI:
    def test_no_projects(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.session_status", lambda n, h=None: "completed")

        from distillate.main import _list_experiments
        _list_experiments()

        output = capsys.readouterr().out
        assert "No experiments tracked" in output

    def test_shows_projects(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("distillate.launcher.session_status", lambda n, h=None: "completed")

        from distillate.state import State

        state = State()
        state.add_experiment("tiny-gene-code", "Tiny Gene Code", str(tmp_path))
        state.add_session("tiny-gene-code", "s1", {"status": "running", "tmux_session": "t1"})
        state.save()

        from distillate.main import _list_experiments
        _list_experiments()

        output = capsys.readouterr().out
        assert "Tiny Gene Code" in output


# ---------------------------------------------------------------------------
# CLI command tests — commands.py
# ---------------------------------------------------------------------------

class TestUpdateProjectCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _update_project
        _update_project([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _update_project
        _update_project(["nonexistent"])
        assert "No project found" in capsys.readouterr().out

    def test_updates_description(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--update", "p1", "--description", "New desc",
        ])
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _update_project
        _update_project(["p1"])
        output = capsys.readouterr().out
        assert "Updated" in output
        assert "description" in output

        state.reload()
        assert state.get_experiment("p1")["description"] == "New desc"

    def test_updates_key_metric(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--update", "p1", "--key-metric", "f1",
        ])
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _update_project
        _update_project(["p1"])

        state.reload()
        assert state.get_experiment("p1")["key_metric_name"] == "f1"

    def test_nothing_to_update(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--update", "p1"])
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _update_project
        _update_project(["p1"])
        assert "Nothing to update" in capsys.readouterr().out


class TestQueueSessionsCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _queue_sessions
        _queue_sessions([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _queue_sessions
        _queue_sessions(["nope"])
        assert "No project found" in capsys.readouterr().out

    def test_queues_default(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--queue-sessions", "p1"])
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _queue_sessions
        _queue_sessions(["p1"])
        output = capsys.readouterr().out
        assert "Queued" in output
        assert "1" in output

        state.reload()
        proj = state.get_experiment("p1")
        assert proj["continuation_queue"]["count"] == 1
        assert proj["auto_continue"] is True

    def test_queues_custom_count(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--queue-sessions", "p1", "--count", "5",
        ])
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _queue_sessions
        _queue_sessions(["p1"])

        state.reload()
        assert state.get_experiment("p1")["continuation_queue"]["count"] == 5


class TestListTemplatesCLI:
    def test_no_templates(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        (tmp_path / "templates").mkdir()
        from distillate.commands import _list_templates
        _list_templates()
        assert "No templates available" in capsys.readouterr().out

    def test_shows_templates(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl_dir = tmp_path / "templates" / "my-exp"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "PROMPT.md").write_text("line1\nline2\n")

        from distillate.commands import _list_templates
        _list_templates()
        output = capsys.readouterr().out
        assert "my-exp" in output


class TestSaveTemplateCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _save_template
        _save_template([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _save_template
        _save_template(["nope"])
        assert "No project found" in capsys.readouterr().out

    def test_saves(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--save-template", "p1"])

        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        (proj_dir / "PROMPT.md").write_text("experiment\n")

        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Proj 1", str(proj_dir))
        state.save()

        from distillate.commands import _save_template
        _save_template(["p1"])
        output = capsys.readouterr().out
        assert "Saved template" in output


class TestCompareProjectsCLI:
    def test_needs_two(self, capsys):
        from distillate.commands import _compare_experiments
        _compare_experiments(["one"])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _compare_experiments
        _compare_experiments(["a", "b"])
        assert "No project found" in capsys.readouterr().out

    def test_comparison_table(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Alpha", str(tmp_path))
        state.add_experiment("p2", "Beta", str(tmp_path))
        state._data["experiments"]["p1"]["runs"] = {
            "r1": {"decision": "best", "results": {"accuracy": 0.80}},
        }
        state._data["experiments"]["p2"]["runs"] = {
            "r1": {"decision": "best", "results": {"accuracy": 0.95}},
        }
        state.save()

        from distillate.commands import _compare_experiments
        _compare_experiments(["p1", "p2"])
        output = capsys.readouterr().out
        assert "Alpha" in output
        assert "Beta" in output
        assert "accuracy" in output
        assert "*" in output  # best value starred

    def test_no_metrics(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Alpha", str(tmp_path))
        state.add_experiment("p2", "Beta", str(tmp_path))
        state.save()

        from distillate.commands import _compare_experiments
        _compare_experiments(["p1", "p2"])
        assert "No metrics" in capsys.readouterr().out


class TestGithubCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _github
        _github([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _github
        _github(["nope"])
        assert "No project found" in capsys.readouterr().out

    def test_success(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--github", "p1"])
        monkeypatch.setattr(
            "distillate.launcher.create_github_repo",
            lambda path, name, private=True: {"ok": True, "url": "https://github.com/u/r"},
        )
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _github
        _github(["p1"])
        output = capsys.readouterr().out
        assert "https://github.com/u/r" in output

        state.reload()
        assert state.get_experiment("p1")["github_url"] == "https://github.com/u/r"

    def test_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", ["distillate", "--github", "p1"])
        monkeypatch.setattr(
            "distillate.launcher.create_github_repo",
            lambda path, name, private=True: {"ok": False, "reason": "no gh"},
        )
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.save()

        from distillate.commands import _github
        _github(["p1"])
        assert "no gh" in capsys.readouterr().out


class TestCreateExperimentCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _create_experiment
        _create_experiment([])
        assert "Usage" in capsys.readouterr().out

    def test_calls_init_experiment_tool(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--create-experiment", "test-exp",
            "--target", str(tmp_path / "exp"),
            "--goal", "Maximize accuracy",
        ])

        # Mock init_experiment_tool to avoid actual Claude calls
        monkeypatch.setattr(
            "distillate.experiment_tools.init_experiment_tool",
            lambda **kwargs: {
                "success": True,
                "experiment_id": "test-exp",
                "goals_set": [{"metric": "accuracy", "direction": "maximize"}],
            },
        )

        from distillate.commands import _create_experiment
        _create_experiment(["test-exp"])
        output = capsys.readouterr().out
        assert "test-exp" in output
        assert "Launch it" in output


class TestParallelCampaignCLI:
    def test_no_args(self, capsys):
        from distillate.commands import _parallel_campaign
        _parallel_campaign([])
        assert "Usage" in capsys.readouterr().out

    def test_project_not_found(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        from distillate.commands import _parallel_campaign
        _parallel_campaign(["nope1", "nope2"])
        assert "No project found" in capsys.readouterr().out

    def test_no_goals(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setattr("sys.argv", [
            "distillate", "--parallel-campaign", "p1", "p2",
        ])
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "Proj 1", str(tmp_path))
        state.add_experiment("p2", "Proj 2", str(tmp_path))
        state.save()

        from distillate.commands import _parallel_campaign
        _parallel_campaign(["p1", "p2"])
        assert "no goals" in capsys.readouterr().out.lower()


class TestWatchProjectNameResolution:
    def test_resolves_project_name_to_path(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")

        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        (proj_dir / ".git").mkdir()

        from distillate.state import State
        state = State()
        state.add_experiment("my-project", "My Project", str(proj_dir))
        state.save()

        # _watch will fail at scan_experiment (no actual experiment), but we can
        # verify the path resolution worked by checking it accesses the right dir
        from distillate.commands import _watch
        # Mock scan_experiment to return error (avoids infinite loop)
        monkeypatch.setattr(
            "distillate.experiments.scan_experiment",
            lambda p: {"error": f"test_path={p}"},
        )
        monkeypatch.setattr("distillate.config.setup_logging", lambda: None)

        _watch(["my-project"])
        output = capsys.readouterr().out
        assert str(proj_dir) in output  # "Watching /path..."
        assert "Error" in output  # scan_experiment returns error
