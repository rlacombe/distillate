# Covers: distillate/experiments.py, distillate/experiment_tools.py

"""Tests for project update detection, git repo discovery, and run lifecycle tools
(start/conclude/save-enrichment/purge/discover-papers)."""

import json
import subprocess


# ---------------------------------------------------------------------------
# Shared state helpers
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
    state.add_experiment("test-proj", "Test Project", str(proj_dir))
    state.save()
    return state, proj_dir


# ---------------------------------------------------------------------------
# Auto-detection tests
# ---------------------------------------------------------------------------


class TestCheckProjectsForUpdates:
    def test_no_projects(self):
        from distillate.experiments import check_experiments_for_updates
        assert check_experiments_for_updates({}) == []

    def test_nonexistent_path(self):
        from distillate.experiments import check_experiments_for_updates
        projects = {
            "p1": {"id": "p1", "path": "/nonexistent/path", "last_commit_hash": "abc123"},
        }
        assert check_experiments_for_updates(projects) == []

    def test_detects_new_commits(self, tmp_path):
        """Test detection when HEAD differs from stored hash."""
        from distillate.experiments import check_experiments_for_updates
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
        updates = check_experiments_for_updates(projects)
        assert len(updates) == 1
        assert updates[0]["new_commits"] == 1
        assert updates[0]["project"]["name"] == "Test"

    def test_no_updates_when_hash_matches(self, tmp_path):
        from distillate.experiments import check_experiments_for_updates
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
        assert check_experiments_for_updates(projects) == []

    def test_first_scan_no_stored_hash(self, tmp_path):
        from distillate.experiments import check_experiments_for_updates
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
        updates = check_experiments_for_updates(projects)
        assert len(updates) == 1
        assert updates[0]["new_commits"] == 1


# ---------------------------------------------------------------------------
# Git repo discovery tests
# ---------------------------------------------------------------------------


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
        assert len(result["experiments"]) == 2
        project_names = {p["name"] for p in result["experiments"]}
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


# ---------------------------------------------------------------------------
# start_run / conclude_run / save_enrichment / purge_hook_runs /
# discover_relevant_papers tool tests
# ---------------------------------------------------------------------------


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
        entries = [json.loads(line) for line in runs_jsonl.read_text().splitlines() if line.strip()]
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
        entries = [json.loads(line) for line in runs_jsonl.read_text().splitlines() if line.strip()]
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
        entries = [json.loads(line) for line in runs_jsonl.read_text().splitlines() if line.strip()]
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
        entries = [json.loads(line) for line in runs_jsonl.read_text().splitlines() if line.strip()]
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
        entries = [json.loads(line) for line in runs_jsonl.read_text().splitlines() if line.strip()]
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
        state.update_experiment("test-proj",
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
        state.update_experiment("test-proj",
                             description="transformer architecture")
        state.save()
        from distillate.experiment_tools import discover_relevant_papers
        result = discover_relevant_papers(state=state, project="test-proj")
        assert result["candidates"] == []
