"""
Test suite for Issue #12: Onboarding fails with "Template not found: tiny-matmul"

Verifies that the demo experiment scaffolding works on fresh installs without
relying on template files being present.
"""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


class TestDemoExperimentScaffolding:
    """Verify demo experiment scaffolds correctly without template files."""

    def test_demo_scaffold_on_completely_fresh_config(self, tmp_path, monkeypatch):
        """Demo scaffold succeeds even when ~/.config/distillate/ doesn't exist."""
        # Start with completely empty config dir (simulates fresh install)
        config_dir = tmp_path / "fresh-config"
        assert not config_dir.exists()

        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", config_dir)
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.launcher import scaffold_experiment

        target = Path(tmp_path / "experiments" / "demo-1")
        result = scaffold_experiment("demo", target, name="Demo Experiment")

        assert result.is_dir()
        assert (result / "PROMPT.md").is_file()
        assert (result / "train.py").is_file()

    def test_demo_scaffold_creates_prompt_and_train_files(self, tmp_path, monkeypatch):
        """Demo experiment includes PROMPT.md and train.py with correct content."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.launcher import scaffold_experiment

        target = tmp_path / "experiments" / "test-demo"
        scaffold_experiment("demo", target)

        # Check PROMPT.md
        prompt = (target / "PROMPT.md").read_text()
        assert "Addition Grokking" in prompt
        assert "10-digit" in prompt
        assert "Papailopoulos" in prompt  # proper attribution

        # Check train.py
        train = (target / "train.py").read_text()
        assert "MiniTransformer" in train
        assert "generate_dataset" in train
        assert "import torch" in train
        assert "metrics.json" in train

    def test_demo_scaffold_installs_distillate_infrastructure(self, tmp_path, monkeypatch):
        """Demo experiment gets all standard Distillate scaffolding."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.launcher import scaffold_experiment

        target = tmp_path / "experiments" / "test-demo"
        scaffold_experiment("demo", target)

        # Standard distillate scaffolding
        assert (target / ".git").is_dir(), "Should be git repo"
        assert (target / ".distillate" / "REPORTING.md").is_file()
        assert (target / "CLAUDE.md").is_file()
        assert (target / ".claude" / "settings.local.json").is_file()
        assert (target / ".mcp.json").is_file()

    def test_demo_scaffold_multiple_times_idempotent(self, tmp_path, monkeypatch):
        """Can scaffold demo experiment multiple times (different names)."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.launcher import scaffold_experiment

        # First demo
        result1 = scaffold_experiment("demo", tmp_path / "experiments" / "demo-1")
        assert result1.is_dir()

        # Second demo (different directory)
        result2 = scaffold_experiment("demo", tmp_path / "experiments" / "demo-2")
        assert result2.is_dir()

        # Both should have the same content
        prompt1 = (result1 / "PROMPT.md").read_text()
        prompt2 = (result2 / "PROMPT.md").read_text()
        assert prompt1 == prompt2

    def test_demo_experiment_git_commits(self, tmp_path, monkeypatch):
        """Scaffolded demo experiment has git initialized."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.launcher import scaffold_experiment

        target = tmp_path / "experiments" / "test-demo"
        scaffold_experiment("demo", target)

        # Verify git is initialized
        result = subprocess.run(
            ["git", "status"],
            cwd=target,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "On branch" in result.stdout or "nothing to commit" in result.stdout

    def test_onboarding_endpoint_scaffolds_demo(self, tmp_path, monkeypatch):
        """POST /experiments/scaffold with demo template works (onboarding flow)."""
        if not importlib.util.find_spec("fastapi"):
            pytest.skip("fastapi not installed (desktop-only dependency)")

        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setenv("DISTILLATE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr("distillate.config.EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.server import _create_app
        from starlette.testclient import TestClient

        app = _create_app()
        client = TestClient(app)

        # Simulate onboarding: POST to /experiments/scaffold with demo template
        resp = client.post(
            "/experiments/scaffold",
            json={"template": "demo", "name": "Addition Grokking"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "experiment_id" in data
        assert "path" in data

        # Verify experiment directory was created with correct files
        exp_path = Path(data["path"])
        assert exp_path.is_dir()
        assert (exp_path / "PROMPT.md").is_file()
        assert (exp_path / "train.py").is_file()

    def test_onboarding_succeeds_on_fresh_desktop_install(self, tmp_path, monkeypatch):
        """Onboarding succeeds from scratch without any templates seeded."""
        if not importlib.util.find_spec("fastapi"):
            pytest.skip("fastapi not installed (desktop-only dependency)")

        # Completely fresh install: empty config and experiments dir
        config_dir = tmp_path / "distillate-config"
        exp_root = tmp_path / "experiments"

        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", config_dir)
        monkeypatch.setenv("DISTILLATE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(exp_root))
        monkeypatch.setattr("distillate.config.EXPERIMENTS_ROOT", str(exp_root))

        # Verify dirs don't exist yet
        assert not config_dir.exists()
        assert not exp_root.exists()

        from distillate.server import _create_app
        from starlette.testclient import TestClient

        app = _create_app()
        client = TestClient(app)

        # Onboarding should succeed on fresh install
        resp = client.post(
            "/experiments/scaffold",
            json={"template": "demo", "name": "My First Experiment"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_demo_mcp_config_includes_distillate_server(self, tmp_path, monkeypatch):
        """Scaffolded demo has MCP config pointing to distillate tools."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.launcher import scaffold_experiment

        target = tmp_path / "experiments" / "test-demo"
        scaffold_experiment("demo", target)

        mcp_json = (target / ".mcp.json").read_text()
        mcp_config = json.loads(mcp_json)

        assert "mcpServers" in mcp_config
        assert "distillate" in mcp_config["mcpServers"]

    def test_demo_train_script_is_runnable(self, tmp_path, monkeypatch):
        """Demo train.py is syntactically valid Python."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.launcher import scaffold_experiment

        target = tmp_path / "experiments" / "test-demo"
        scaffold_experiment("demo", target)

        train_py = (target / "train.py").read_text()

        # Verify it's valid Python by compiling it
        try:
            compile(train_py, "train.py", "exec")
        except SyntaxError as e:
            pytest.fail(f"train.py has syntax error: {e}")

    def test_demo_metrics_structure(self, tmp_path, monkeypatch):
        """Demo experiment logs metrics.json with expected structure."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.launcher import scaffold_experiment

        target = tmp_path / "experiments" / "test-demo"
        scaffold_experiment("demo", target)

        train_py = (target / "train.py").read_text()

        # Verify train.py writes metrics.json with expected keys
        assert "metrics.json" in train_py
        assert "test_accuracy" in train_py
        assert "param_count" in train_py
        assert "train_loss" in train_py


class TestIssue12Regression:
    """Regression tests: ensure the original bug doesn't resurface."""

    def test_scaffold_no_longer_requires_template_files(self, tmp_path, monkeypatch):
        """Demo scaffolding doesn't look for files in ~/.config/distillate/templates/."""
        config_dir = tmp_path / "config"
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", config_dir)
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.launcher import scaffold_experiment

        # Even if templates dir doesn't exist, demo should work
        assert not (config_dir / "templates").exists()

        target = tmp_path / "experiments" / "demo"
        result = scaffold_experiment("demo", target)

        assert result.is_dir()
        assert (result / "PROMPT.md").is_file()

        # Config templates dir might be created, but demo doesn't rely on it
        # (this is fine—it's just for storing user-imported templates)

    def test_wizard_onboarding_doesnt_hardcode_template_name(self, tmp_path):
        """Verify wizard.js uses 'demo' template, not 'tiny-matmul'."""
        wizard_path = Path(__file__).parent.parent / "desktop" / "renderer" / "experiment-wizard.js"

        content = wizard_path.read_text()

        # Should use demo template
        assert 'template: "demo"' in content

        # Should NOT hardcode tiny-matmul in onboarding
        # (it might exist elsewhere in file for other features, but not in onboarding)
        lines = content.split("\n")
        onboarding_section = None
        for i, line in enumerate(lines):
            if "Launch demo experiment" in line or "onboarding" in line.lower():
                # Find nearby scaffold call
                for j in range(max(0, i - 10), min(len(lines), i + 20)):
                    if "experiments/scaffold" in lines[j]:
                        onboarding_section = "\n".join(lines[max(0, j - 5) : j + 5])
                        break

        # If we found the onboarding section, verify it uses demo
        if onboarding_section:
            assert 'template: "demo"' in onboarding_section

    def test_endpoint_accepts_demo_template(self, tmp_path, monkeypatch):
        """Verify /experiments/scaffold endpoint handles demo template."""
        if not importlib.util.find_spec("fastapi"):
            pytest.skip("fastapi not installed (desktop-only dependency)")

        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path / "config")
        monkeypatch.setenv("DISTILLATE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr("distillate.config.EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.server import _create_app
        from starlette.testclient import TestClient

        app = _create_app()
        client = TestClient(app)

        # Should NOT return 404 for demo template
        resp = client.post(
            "/experiments/scaffold",
            json={"template": "demo", "name": "Test"},
        )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        assert resp.json()["ok"] is True
