# Covers: distillate/experiments.py (ML repo detection, retroactive scan, scan-without-git, metric formatting, git hook installation)

"""Tests for ML repo detection, retroactive scanning, non-git scanning, metric formatting,
and git hook installation."""

import json
import os
import subprocess


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
        from distillate.experiments import scan_experiment
        repo = self._create_mock_ml_repo(tmp_path)
        result = scan_experiment(repo)
        assert "error" not in result
        assert result["name"] == "Test Project"
        assert len(result["runs"]) >= 1
        # Should have found the training log
        run = list(result["runs"].values())[0]
        assert run["status"] == "completed"
        assert "d_model" in run["hyperparameters"]
        assert run["hyperparameters"]["d_model"] == 64

    def test_scan_non_git_dir_succeeds(self, tmp_path):
        from distillate.experiments import scan_experiment
        result = scan_experiment(tmp_path)
        assert "error" not in result
        assert len(result["runs"]) == 0
        assert result["has_git"] is False
        assert result["head_hash"] == ""

    def test_scan_empty_repo(self, tmp_path):
        from distillate.experiments import scan_experiment
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
        result = scan_experiment(repo)
        assert "error" not in result
        assert len(result["runs"]) == 0


# ---------------------------------------------------------------------------
# Non-git scanning tests
# ---------------------------------------------------------------------------


class TestScanWithoutGit:
    def test_scan_discovers_runs_without_git(self, tmp_path):
        """Scan a plain directory (no .git) with ML artifacts."""
        from distillate.experiments import scan_experiment

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

        result = scan_experiment(proj)
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
        from distillate.experiments import scan_experiment

        proj = tmp_path / "my-project"
        proj.mkdir()
        # Put a result file so there's something to scan
        (proj / "results.json").write_text(json.dumps({
            "accuracy": 0.95, "loss": 0.1,
        }))

        scan_experiment(proj)
        scan_state = proj / ".distillate" / "scan_state.json"
        assert scan_state.exists()
        data = json.loads(scan_state.read_text())
        assert "last_scanned_at" in data
        assert "file_manifest" in data

    def test_scan_with_git_enriches_commits(self, tmp_path):
        """Scan a git repo — runs get git_commits attached."""
        from distillate.experiments import scan_experiment

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

        result = scan_experiment(repo)
        assert result["has_git"] is True
        assert result["head_hash"] != ""
        run = list(result["runs"].values())[0]
        assert len(run["git_commits"]) >= 1

    def test_update_project_detects_changes(self, tmp_path):
        """update_experiment re-scans when files change."""
        from distillate.experiments import scan_experiment, update_experiment

        proj = tmp_path / "my-project"
        results_dir = proj / "experiment"
        results_dir.mkdir(parents=True)

        (results_dir / "results_v1.json").write_text(json.dumps({
            "accuracy": 0.9, "loss": 0.1,
        }))

        # Initial scan
        result = scan_experiment(proj)
        project = {
            "name": "My Project",
            "path": str(proj),
            "runs": result["runs"],
            "last_scanned_at": "",
        }

        # No changes → returns False
        assert update_experiment(project, state=None) is False

        # Add a new artifact
        import time
        time.sleep(0.05)  # ensure different mtime
        (results_dir / "results_v2.json").write_text(json.dumps({
            "accuracy": 0.95, "loss": 0.05,
        }))

        # Now it should detect the change
        assert update_experiment(project, state=None) is True

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
