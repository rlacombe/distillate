# Covers: distillate/launcher.py, distillate/experiment_tools/run_tools.py (start_run deadlines),
#          distillate/autoresearch/CLAUDE.md, distillate/autoresearch/PI.md,
#          distillate/autoresearch/REPORTING.md, distillate/experiment_tools/init_tools.py
"""L2 — single source of truth for budgets.

The launcher writes ``train_budget_seconds`` / ``wrap_budget_seconds`` into
``.distillate/budget.json``. Protocol files (CLAUDE.md, PI.md, REPORTING.md)
reference the helper instead of hard-coding MAX_SECONDS.

L3 — start_run records deadlines.

``start_run`` reads budget.json and writes ``train_deadline_at`` and
``wrap_deadline_at`` into the run entry so every consumer reads one canonical
timestamp instead of recomputing.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers shared in this file
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path):
    """A bare project directory with .distillate/ ready."""
    p = tmp_path / "proj"
    p.mkdir()
    (p / ".distillate").mkdir()
    return p


def _write_budget(project: Path, *, train: int, wrap: Optional[int] = None) -> Path:
    """Helper: write a budget.json with the new L2 fields."""
    if wrap is None:
        wrap = max(60, int(train * 0.1))
    data = {
        "run_budget_seconds": train,
        "train_budget_seconds": train,
        "wrap_budget_seconds": wrap,
        "session_budget_seconds": None,
        "session_started_at": None,
    }
    path = project / ".distillate" / "budget.json"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return path


def _read_runs(project: Path) -> list[dict]:
    path = project / ".distillate" / "runs.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ===========================================================================
# L2 — single source of truth: protocol docs + launcher agree.
# ===========================================================================


class TestL2ProtocolDocsCleaned:
    """The protocol files must stop telling the agent to hard-code
    ``MAX_SECONDS = 300``. That snippet is the root of agents copying
    300s into a project whose actual budget is 600s.
    """

    PROTOCOL_FILES = [
        "distillate/autoresearch/CLAUDE.md",
        "distillate/autoresearch/PI.md",
        "distillate/autoresearch/REPORTING.md",
    ]

    @pytest.mark.parametrize("rel", PROTOCOL_FILES)
    def test_no_hardcoded_max_seconds(self, rel):
        repo_root = Path(__file__).resolve().parent.parent
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert "MAX_SECONDS = 300" not in text, (
            f"{rel} still hard-codes MAX_SECONDS = 300. Replace with the "
            "distillate-run wrapper or budget.json read."
        )

    @pytest.mark.parametrize("rel", PROTOCOL_FILES)
    def test_references_distillate_run_or_budget_json(self, rel):
        repo_root = Path(__file__).resolve().parent.parent
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert ("distillate-run" in text) or ("budget.json" in text), (
            f"{rel} must point the agent at distillate-run or budget.json "
            "instead of the hand-rolled wall-clock snippet."
        )


class TestL2LauncherWritesNewBudgetFields:
    """``write_budget_json`` must write the new ``train_budget_seconds`` and
    ``wrap_budget_seconds`` fields alongside the legacy ``run_budget_seconds``.
    """

    def test_writes_train_budget(self, tmp_path):
        from distillate.launcher import write_budget_json
        write_budget_json(tmp_path, {"duration_minutes": 10})
        data = json.loads((tmp_path / ".distillate" / "budget.json").read_text())
        assert data["train_budget_seconds"] == 600

    def test_writes_wrap_budget(self, tmp_path):
        from distillate.launcher import write_budget_json
        write_budget_json(tmp_path, {"duration_minutes": 10})
        data = json.loads((tmp_path / ".distillate" / "budget.json").read_text())
        # Default wrap = max(60, 10% of train) -> max(60, 60) = 60
        assert data["wrap_budget_seconds"] == 60

    def test_wrap_budget_floor_is_60s(self, tmp_path):
        """Even tiny train budgets get a 60s wrap-up window — conclude_run
        + commit + push routinely takes 30-45s."""
        from distillate.launcher import write_budget_json
        write_budget_json(tmp_path, {"duration_minutes": 1})  # 60s train
        data = json.loads((tmp_path / ".distillate" / "budget.json").read_text())
        # 10% of 60 = 6, so floor of 60 must apply
        assert data["wrap_budget_seconds"] == 60

    def test_wrap_budget_scales_with_train(self, tmp_path):
        """Long training runs deserve proportionally larger wrap windows."""
        from distillate.launcher import write_budget_json
        write_budget_json(tmp_path, {"duration_minutes": 60})  # 3600s train
        data = json.loads((tmp_path / ".distillate" / "budget.json").read_text())
        # 10% of 3600 = 360 > floor of 60
        assert data["wrap_budget_seconds"] == 360

    def test_run_budget_back_compat(self, tmp_path):
        """Existing consumers read ``run_budget_seconds``; keep it equal to
        train so they keep working without a coordinated upgrade."""
        from distillate.launcher import write_budget_json
        write_budget_json(tmp_path, {"duration_minutes": 10})
        data = json.loads((tmp_path / ".distillate" / "budget.json").read_text())
        assert data["run_budget_seconds"] == data["train_budget_seconds"]


# ===========================================================================
# L3 — start_run records deadlines.
# ===========================================================================


class TestL3StartRunWritesDeadlines:
    """``start_run`` writes ``train_deadline_at`` and ``wrap_deadline_at``
    into the run entry. Consumers (renderer, hooks, on_stop) read these
    instead of recomputing.
    """

    def _make_state_with_project(self, monkeypatch, project_dir):
        """Wire up a state with one project pointing at project_dir."""
        from distillate.state import State
        state = State()
        proj_id = "p1"
        state.add_experiment(proj_id, "test-proj", str(project_dir))
        state.update_experiment(proj_id, duration_minutes=10)
        return state, proj_id

    def test_run_entry_has_train_deadline(self, project_dir, monkeypatch):
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state, _ = self._make_state_with_project(monkeypatch, project_dir)

        start_run(state=state, project="test-proj", description="d")

        runs = _read_runs(project_dir)
        assert runs and "train_deadline_at" in runs[-1], (
            f"Run entry must include train_deadline_at; got keys: {list(runs[-1])}"
        )

    def test_run_entry_has_wrap_deadline(self, project_dir, monkeypatch):
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state, _ = self._make_state_with_project(monkeypatch, project_dir)

        start_run(state=state, project="test-proj", description="d")

        runs = _read_runs(project_dir)
        assert "wrap_deadline_at" in runs[-1]

    def test_train_deadline_equals_started_plus_train_budget(
        self, project_dir, monkeypatch
    ):
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state, _ = self._make_state_with_project(monkeypatch, project_dir)

        start_run(state=state, project="test-proj", description="d")

        run = _read_runs(project_dir)[-1]
        started = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
        train_deadline = datetime.fromisoformat(
            run["train_deadline_at"].replace("Z", "+00:00")
        )
        delta = (train_deadline - started).total_seconds()
        assert abs(delta - 600) < 2, (
            f"train_deadline_at should be started_at + 600s; off by {delta - 600:.1f}s"
        )

    def test_wrap_deadline_equals_train_deadline_plus_wrap_budget(
        self, project_dir, monkeypatch
    ):
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state, _ = self._make_state_with_project(monkeypatch, project_dir)

        start_run(state=state, project="test-proj", description="d")

        run = _read_runs(project_dir)[-1]
        train_deadline = datetime.fromisoformat(
            run["train_deadline_at"].replace("Z", "+00:00")
        )
        wrap_deadline = datetime.fromisoformat(
            run["wrap_deadline_at"].replace("Z", "+00:00")
        )
        delta = (wrap_deadline - train_deadline).total_seconds()
        assert abs(delta - 60) < 1

    def test_response_includes_deadlines(self, project_dir, monkeypatch):
        """The MCP response payload exposes the deadlines so the agent can
        read them directly without re-parsing runs.jsonl."""
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state, _ = self._make_state_with_project(monkeypatch, project_dir)

        result = start_run(state=state, project="test-proj", description="d")
        assert result["success"] is True
        assert "train_deadline_at" in result
        assert "wrap_deadline_at" in result

    def test_falls_back_to_duration_minutes_without_budget_file(
        self, project_dir, monkeypatch
    ):
        """If budget.json is missing (e.g. legacy project), use the project's
        duration_minutes. This must NOT silently use a wrong default."""
        from distillate.experiment_tools.run_tools import start_run
        # No _write_budget -- intentional
        state, _ = self._make_state_with_project(monkeypatch, project_dir)

        start_run(state=state, project="test-proj", description="d")

        run = _read_runs(project_dir)[-1]
        started = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
        train_deadline = datetime.fromisoformat(
            run["train_deadline_at"].replace("Z", "+00:00")
        )
        delta = (train_deadline - started).total_seconds()
        # 10 min from project.duration_minutes
        assert abs(delta - 600) < 2

    def test_existing_fields_preserved(self, project_dir, monkeypatch):
        """Adding deadlines must not regress existing fields the renderer reads."""
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state, _ = self._make_state_with_project(monkeypatch, project_dir)

        start_run(
            state=state, project="test-proj",
            description="d", hypothesis="h", prediction="p",
            predicted_metric="loss", predicted_value=0.5, confidence=70,
        )
        run = _read_runs(project_dir)[-1]
        for field in ("id", "started_at", "status", "description",
                      "hypothesis", "prediction",
                      "predicted_metric", "predicted_value", "confidence"):
            assert field in run, f"Regression: {field} dropped from run entry"
        assert run["status"] == "running"


# ===========================================================================
# Budget protocol — what the agent is told about budget helpers.
# ===========================================================================


class TestProtocolDocsReferenceBudgetHelper:
    """CLAUDE.md and REPORTING.md must point the agent at the
    ``read_train_budget`` helper AND explicitly forbid hardcoding timeout
    values. Without the prohibition in writing, agents have repeatedly
    pasted ``MAX_SECONDS = 300`` into new training scripts even when the
    project's real budget is 60 minutes.
    """

    BUDGET_AWARE_FILES = [
        "distillate/autoresearch/CLAUDE.md",
        "distillate/autoresearch/REPORTING.md",
    ]

    @pytest.mark.parametrize("rel", BUDGET_AWARE_FILES)
    def test_references_budget_helper(self, rel):
        repo_root = Path(__file__).resolve().parent.parent
        text = (repo_root / rel).read_text(encoding="utf-8")
        # Either the import line or the function name is enough -- both
        # point the agent at the canonical helper.
        assert ("read_train_budget" in text) or ("distillate.budget" in text), (
            f"{rel} must reference `read_train_budget` / `distillate.budget` "
            "so agents know where the canonical helper lives."
        )

    @pytest.mark.parametrize("rel", BUDGET_AWARE_FILES)
    def test_forbids_hardcoded_timeouts(self, rel):
        repo_root = Path(__file__).resolve().parent.parent
        text = (repo_root / rel).read_text(encoding="utf-8").lower()
        # Accept any of the natural phrasings a human would write. The
        # important thing is the prohibition is *explicit*, not implicit.
        assert ("never hardcode" in text) or ("do not hardcode" in text), (
            f"{rel} must explicitly prohibit hardcoding timeout values "
            "(look for 'never hardcode' / 'do not hardcode')."
        )


class TestPromptMdSystemInstructsDynamicBudget:
    """The system prompt used by ``init_experiment`` to generate PROMPT.md
    is where the norm gets set for every *new* experiment. If it doesn't
    mention the helper, every new PROMPT.md will silently omit it and the
    agent will reach for the old ``MAX_SECONDS = 300`` habit.
    """

    def test_prompt_system_mentions_budget_helper(self):
        from distillate.experiment_tools.init_tools import _PROMPT_MD_SYSTEM
        text = _PROMPT_MD_SYSTEM
        assert ("read_train_budget" in text) or ("distillate.budget" in text), (
            "_PROMPT_MD_SYSTEM must tell PROMPT.md writers to reference "
            "read_train_budget so new experiments inherit the dynamic budget."
        )

    def test_prompt_system_forbids_hardcoded_timeouts(self):
        from distillate.experiment_tools.init_tools import _PROMPT_MD_SYSTEM
        lower = _PROMPT_MD_SYSTEM.lower()
        assert ("never hardcode" in lower) or ("do not hardcode" in lower), (
            "_PROMPT_MD_SYSTEM must explicitly forbid hardcoding timeouts "
            "in training scripts."
        )
