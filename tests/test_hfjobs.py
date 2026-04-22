"""Tests for HuggingFace Jobs integration.

All HF API calls are mocked — no real network traffic.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_project(tmp_path):
    """Minimal experiment directory with budget and hf_jobs registry."""
    distillate_dir = tmp_path / ".distillate"
    distillate_dir.mkdir()
    (distillate_dir / "budget.json").write_text(
        json.dumps({
            "train_budget_seconds": 300,
            "compute": {
                "provider": "hfjobs",
                "gpu_type": "a100-large",
                "budget_usd": 10.0,
                "cost_per_hour": 2.50,
            },
        }) + "\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def mock_hf_api():
    """Patch HFJobsProvider so no real HF calls are made."""
    with patch("distillate.compute_hfjobs.HFJobsProvider.__init__", return_value=None):
        yield


@pytest.fixture
def train_script(tmp_project):
    """Write a minimal train.py with requirements header."""
    script = tmp_project / "train.py"
    script.write_text(
        textwrap.dedent("""\
            # requirements: torch
            import sys
            print("METRIC val_loss=0.42 train_loss=0.31", flush=True)
        """),
        encoding="utf-8",
    )
    return script


# ---------------------------------------------------------------------------
# _parse_requirements_comment
# ---------------------------------------------------------------------------


def test_parse_requirements_comment(tmp_path):
    from distillate.experiment_tools.hf_tools import _parse_requirements_comment

    script = tmp_path / "train.py"
    script.write_text("# requirements: torch transformers datasets\n\nprint('hi')\n")
    assert _parse_requirements_comment(str(script)) == ["torch", "transformers", "datasets"]


def test_parse_requirements_comment_missing(tmp_path):
    from distillate.experiment_tools.hf_tools import _parse_requirements_comment

    script = tmp_path / "train.py"
    script.write_text("import torch\n")
    assert _parse_requirements_comment(str(script)) == []


def test_parse_requirements_comment_nonexistent():
    from distillate.experiment_tools.hf_tools import _parse_requirements_comment

    assert _parse_requirements_comment("/nonexistent/train.py") == []


# ---------------------------------------------------------------------------
# _register_job / _lookup_job_project
# ---------------------------------------------------------------------------


def test_register_and_lookup_job(tmp_project):
    from distillate.experiment_tools.hf_tools import _lookup_job_project, _register_job

    _register_job(tmp_project, "job-abc123", script="train.py", flavor="a100-large")

    registry = tmp_project / ".distillate" / "hf_jobs.json"
    assert registry.exists()
    data = json.loads(registry.read_text())
    assert "job-abc123" in data
    assert data["job-abc123"]["script"] == "train.py"
    assert data["job-abc123"]["flavor"] == "a100-large"
    assert data["job-abc123"]["project_path"] == str(tmp_project)


def test_register_job_multiple(tmp_project):
    from distillate.experiment_tools.hf_tools import _register_job

    _register_job(tmp_project, "job-1", script="train.py", flavor="t4-small")
    _register_job(tmp_project, "job-2", script="train_v2.py", flavor="a100-large")

    data = json.loads((tmp_project / ".distillate" / "hf_jobs.json").read_text())
    assert "job-1" in data and "job-2" in data


# ---------------------------------------------------------------------------
# JobInfo dataclass
# ---------------------------------------------------------------------------


def test_job_info_has_duration_seconds():
    from distillate.compute_hfjobs import JobInfo

    info = JobInfo(id="j1")
    assert info.duration_seconds == 0.0
    info.duration_seconds = 120.0
    assert info.duration_seconds == 120.0


# ---------------------------------------------------------------------------
# HFJobsProvider.submit_job — command building
# ---------------------------------------------------------------------------


def _make_provider(namespace="myorg"):
    """Build a HFJobsProvider instance without calling __init__."""
    from distillate.compute_hfjobs import HFJobsProvider
    provider = HFJobsProvider.__new__(HFJobsProvider)
    provider._namespace = namespace
    provider._token = "hf_fake"
    provider._api = MagicMock()
    return provider


def test_submit_job_command_with_code_repo():
    """submit_job mounts code_repo as /workspace volume and runs from there."""
    captured = {}

    def fake_run_job(**kwargs):
        captured.update(kwargs)
        job = MagicMock()
        job.id = "job-xyz"
        return job

    provider = _make_provider()

    with patch("huggingface_hub.run_job", fake_run_job):
        # The import inside submit_job does `from huggingface_hub import Volume, run_job`
        # so we also need Volume to be importable — patch at the right level
        import huggingface_hub as _hfhub
        orig_run_job = getattr(_hfhub, "run_job", None)
        _hfhub.run_job = fake_run_job
        try:
            info = provider.submit_job(
                "train.py",
                gpu_flavor="a100-large",
                code_repo_id="myorg/distillate-xp-proj",
            )
        finally:
            if orig_run_job is not None:
                _hfhub.run_job = orig_run_job
            else:
                delattr(_hfhub, "run_job")

    assert info.id == "job-xyz"
    assert "/workspace/train.py" in captured["command"]
    vol = captured["volumes"][0]
    assert vol.mount_path == "/workspace"
    assert vol.source == "myorg/distillate-xp-proj"


def test_submit_job_with_dependencies():
    """Dependencies are passed as --with flags to uv run."""
    captured = {}

    def fake_run_job(**kwargs):
        captured.update(kwargs)
        job = MagicMock()
        job.id = "job-dep"
        return job

    provider = _make_provider(namespace="")

    import huggingface_hub as _hfhub
    orig = getattr(_hfhub, "run_job", None)
    _hfhub.run_job = fake_run_job
    try:
        provider.submit_job(
            "train.py",
            gpu_flavor="t4-small",
            dependencies=["torch", "transformers"],
            code_repo_id="user/code-repo",
        )
    finally:
        if orig is not None:
            _hfhub.run_job = orig
        else:
            delattr(_hfhub, "run_job")

    cmd = captured["command"]
    assert cmd[0] == "uv"
    assert "--with=torch" in cmd
    assert "--with=transformers" in cmd
    assert "/workspace/train.py" in cmd


def test_submit_job_no_code_repo_uses_bare_script():
    """Without code_repo_id, the script name is used as-is in the command."""
    captured = {}

    def fake_run_job(**kwargs):
        captured.update(kwargs)
        job = MagicMock()
        job.id = "job-bare"
        return job

    provider = _make_provider(namespace="")

    import huggingface_hub as _hfhub
    orig = getattr(_hfhub, "run_job", None)
    _hfhub.run_job = fake_run_job
    try:
        provider.submit_job("train.py", gpu_flavor="t4-small")
    finally:
        if orig is not None:
            _hfhub.run_job = orig
        else:
            delattr(_hfhub, "run_job")

    assert captured["command"] == ["python3", "train.py"]
    assert not captured.get("volumes")


# ---------------------------------------------------------------------------
# submit_hf_job_tool — budget gate
# ---------------------------------------------------------------------------


def test_submit_hf_job_tool_budget_exhausted(tmp_project, train_script):
    """Tool returns error when cumulative spend >= budget."""
    # Fake prior spend that exhausts the $10 budget
    spend_path = tmp_project / ".distillate" / "compute_spend.json"
    spend_path.write_text(
        json.dumps({"total_usd": 10.5, "jobs": [{"job_id": "old", "cost_usd": 10.5}]}),
        encoding="utf-8",
    )

    state = MagicMock()
    state.experiments = {"my-proj": {"id": "my-proj", "name": "my-proj", "path": str(tmp_project)}}
    state.get_experiment = lambda eid: state.experiments.get(eid)

    from distillate.experiment_tools.hf_tools import submit_hf_job_tool

    with patch("distillate.experiment_tools._helpers._resolve_project") as mock_resolve:
        mock_resolve.return_value = (
            {"id": "my-proj", "name": "my-proj", "path": str(tmp_project)},
            None,
        )
        result = submit_hf_job_tool(
            state=state,
            project="my-proj",
            script="train.py",
        )

    assert result["success"] is False
    assert "exhausted" in result["error"]


def test_submit_hf_job_tool_missing_script(tmp_project):
    """Tool returns error when script file doesn't exist."""
    from distillate.experiment_tools.hf_tools import submit_hf_job_tool

    state = MagicMock()

    with patch("distillate.experiment_tools._helpers._resolve_project") as mock_resolve:
        mock_resolve.return_value = (
            {"id": "my-proj", "name": "my-proj", "path": str(tmp_project)},
            None,
        )
        result = submit_hf_job_tool(
            state=state,
            project="my-proj",
            script="nonexistent.py",
        )

    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_submit_hf_job_tool_parses_requirements(tmp_project, train_script):
    """Tool reads # requirements: from script header and passes deps."""
    from distillate.compute_hfjobs import JobInfo
    from distillate.experiment_tools.hf_tools import submit_hf_job_tool

    state = MagicMock()

    fake_job = JobInfo(id="job-123", status="starting", flavor="a100-large", cost_per_hour=2.5)

    with (
        patch("distillate.experiment_tools._helpers._resolve_project") as mock_resolve,
        patch("distillate.compute_hfjobs.HFJobsProvider") as MockProvider,
    ):
        mock_resolve.return_value = (
            {"id": "my-proj", "name": "my-proj", "path": str(tmp_project)},
            None,
        )
        mock_provider_instance = MockProvider.return_value
        mock_provider_instance.upload_script_for_job.return_value = (
            "user/distillate-xp-my-proj", "train.py"
        )
        mock_provider_instance.submit_job.return_value = fake_job

        result = submit_hf_job_tool(
            state=state,
            project="my-proj",
            script="train.py",
        )

    assert result["success"] is True
    assert result["job_id"] == "job-123"
    assert "torch" in result["dependencies"]

    # Verify submit_job was called with code_repo_id and dependencies
    call_kwargs = mock_provider_instance.submit_job.call_args
    assert call_kwargs.kwargs["code_repo_id"] == "user/distillate-xp-my-proj"
    assert "torch" in call_kwargs.kwargs["dependencies"]


# ---------------------------------------------------------------------------
# check_hf_job_tool — metrics extraction + spend recording
# ---------------------------------------------------------------------------


def test_check_hf_job_tool_extracts_metrics():
    """check_hf_job extracts METRIC key=value lines from logs."""
    from distillate.compute_hfjobs import JobInfo
    from distillate.experiment_tools.hf_tools import check_hf_job_tool

    state = MagicMock()

    fake_info = JobInfo(
        id="job-abc",
        status="completed",
        flavor="a100-large",
        cost_per_hour=2.5,
        duration_seconds=600.0,
    )
    fake_logs = "Epoch 1\nMETRIC val_loss=0.42 train_loss=0.31\nDone"

    with patch("distillate.compute_hfjobs.HFJobsProvider") as MockProvider:
        mock_provider_instance = MockProvider.return_value
        mock_provider_instance.get_job.return_value = fake_info
        mock_provider_instance.get_logs.return_value = fake_logs

        result = check_hf_job_tool(state=state, job_id="job-abc")

    assert result["status"] == "completed"
    assert result["duration_seconds"] == 600.0
    assert abs(result["cost_usd"] - 600 / 3600 * 2.5) < 0.001
    metrics = result.get("metrics_from_logs", {})
    assert metrics.get("val_loss") == pytest.approx(0.42)
    assert metrics.get("train_loss") == pytest.approx(0.31)


def test_check_hf_job_tool_records_spend(tmp_project):
    """check_hf_job records spend in compute_spend.json when project given."""
    from distillate.compute_hfjobs import JobInfo
    from distillate.experiment_tools.hf_tools import check_hf_job_tool, _register_job

    state = MagicMock()

    # Register job so lookup works
    _register_job(tmp_project, "job-spend", script="train.py", flavor="a100-large")

    fake_info = JobInfo(
        id="job-spend",
        status="completed",
        flavor="a100-large",
        cost_per_hour=2.5,
        duration_seconds=3600.0,
    )

    with (
        patch("distillate.compute_hfjobs.HFJobsProvider") as MockProvider,
        patch("distillate.experiment_tools._helpers._resolve_project") as mock_resolve,
    ):
        mock_resolve.return_value = (
            {"id": "proj", "name": "proj", "path": str(tmp_project)},
            None,
        )
        mock_provider_instance = MockProvider.return_value
        mock_provider_instance.get_job.return_value = fake_info
        mock_provider_instance.get_logs.return_value = ""

        check_hf_job_tool(state=state, job_id="job-spend", project="proj")

    spend_path = tmp_project / ".distillate" / "compute_spend.json"
    assert spend_path.exists()
    spend = json.loads(spend_path.read_text())
    assert spend["total_usd"] == pytest.approx(2.5)  # 1 hour * $2.50/hr


# ---------------------------------------------------------------------------
# get_job — duration extraction
# ---------------------------------------------------------------------------


def test_get_job_extracts_duration_from_field():
    from distillate.compute_hfjobs import HFJobsProvider

    # Use SimpleNamespace so getattr works without MagicMock spec issues
    from types import SimpleNamespace
    raw_job = SimpleNamespace(
        status="completed",
        flavor="a100-large",
        duration_seconds=1200.0,
        duration=None,
        started_at=None,
        completed_at=None,
        created_at="",
    )

    provider = HFJobsProvider.__new__(HFJobsProvider)
    provider._api = MagicMock()
    provider._api.get_job.return_value = raw_job

    info = provider.get_job("job-123")
    assert info is not None
    assert info.duration_seconds == 1200.0


def test_get_job_extracts_duration_from_timestamps():
    from distillate.compute_hfjobs import HFJobsProvider
    from types import SimpleNamespace

    raw_job = SimpleNamespace(
        status="completed",
        flavor="t4-small",
        duration=None,
        duration_seconds=None,
        started_at="2026-04-21T10:00:00+00:00",
        completed_at="2026-04-21T10:05:00+00:00",
        created_at="2026-04-21T10:00:00+00:00",
    )

    provider = HFJobsProvider.__new__(HFJobsProvider)
    provider._api = MagicMock()
    provider._api.get_job.return_value = raw_job

    info = provider.get_job("job-ts")
    assert info is not None
    assert info.duration_seconds == pytest.approx(300.0)
