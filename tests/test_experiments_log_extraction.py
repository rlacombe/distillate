# Covers: distillate/experiments.py

"""Tests for Claude Code log extraction and LLM enrichment."""

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Claude Code log extraction helpers
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


# ---------------------------------------------------------------------------
# LLM enrichment constants
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
# Claude Code log extraction tests
# ---------------------------------------------------------------------------


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
        """scan_experiment() should include Claude log runs alongside artifact runs."""
        from distillate.experiments import scan_experiment

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

        result = scan_experiment(project_path)
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
