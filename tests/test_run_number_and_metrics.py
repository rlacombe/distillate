# Covers: distillate/experiment_tools/run_tools.py (start_run/conclude_run run_number),
#          distillate/autoresearch/CLAUDE.md, distillate/autoresearch/PI.md
"""Canonical run_number from start_run and conclude_run.

The agent must have a stable, monotonically increasing run counter from the
backend (not self-counted per session) so commit messages, save_enrichment,
and the renderer all agree on "Run N".
"""

from __future__ import annotations

import json
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
# Canonical run_number from start_run.
# ===========================================================================


class TestStartRunCanonicalRunNumber:
    """``start_run`` must return and persist a canonical ``run_number`` so
    the agent has a stable, monotonically increasing position to reference
    (in commit messages, save_enrichment, etc.) instead of maintaining its
    own counter -- which resets on every session restart or phase pivot.
    """

    def _state(self, project_dir):
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "test-proj", str(project_dir))
        state.update_experiment("p1", duration_minutes=10)
        return state

    def test_response_contains_run_number(self, project_dir):
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        result = start_run(state=state, project="test-proj", description="first")
        assert "run_number" in result, (
            f"start_run payload must include run_number; got keys: {sorted(result)}"
        )
        assert result["run_number"] == 1

    def test_run_entry_persists_run_number(self, project_dir):
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        start_run(state=state, project="test-proj", description="first")
        runs = _read_runs(project_dir)
        assert runs[-1].get("run_number") == 1

    def test_run_number_increments_monotonically(self, project_dir):
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        r1 = start_run(state=state, project="test-proj", description="first")
        r2 = start_run(state=state, project="test-proj", description="second")
        r3 = start_run(state=state, project="test-proj", description="third")
        assert [r1["run_number"], r2["run_number"], r3["run_number"]] == [1, 2, 3]

    def test_run_number_reflects_entire_project_history(self, project_dir):
        """Even if an old session concluded runs that are only present in
        runs.jsonl (no state refresh yet), new runs pick up from that count.
        """
        # Simulate a legacy project with 4 pre-existing run entries
        existing = project_dir / ".distillate" / "runs.jsonl"
        for i in range(4):
            existing.open("a").write(json.dumps({
                "id": f"xp-old{i:04d}",
                "status": "completed" if i < 3 else "crash",
                "timestamp": "2026-03-01T00:00:00+00:00",
                "results": {"loss": 0.1 * i},
            }) + "\n")

        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        r = start_run(state=state, project="test-proj", description="fresh")
        assert r["run_number"] == 5, (
            f"New run after 4 historical entries should be #5; got {r['run_number']}"
        )

    def test_run_number_matches_id_generation_counter(self, project_dir):
        """run_number must increment by 1 per start_run call so the agent can
        say "run 2" and the system knows it means xp-<whatever>."""
        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        r1 = start_run(state=state, project="test-proj", description="a")
        r2 = start_run(state=state, project="test-proj", description="b")
        assert r2["run_number"] - r1["run_number"] == 1

    def test_backfill_entries_do_not_inflate_run_number(self, project_dir):
        """Backfill entries (written by backfill_runs_from_events) have no
        run_number field. They must NOT push the canonical counter forward.

        Regression: Glyco DFM v1 had 32 canonical runs but the harness
        reported 'Run 61' because backfill added 28 extra unique IDs.
        """
        runs_jsonl = project_dir / ".distillate" / "runs.jsonl"

        # 5 canonical runs: each has a running entry + completed entry (same ID)
        for i in range(1, 6):
            runs_jsonl.open("a").write(json.dumps({
                "$schema": "distillate/run/v1",
                "id": f"xp-canon{i:04d}",
                "run_number": i,
                "status": "running",
                "timestamp": f"2026-04-{i:02d}T00:00:00+00:00",
            }) + "\n")
            runs_jsonl.open("a").write(json.dumps({
                "$schema": "distillate/run/v1",
                "id": f"xp-canon{i:04d}",
                "run_number": i,
                "status": "completed",
                "timestamp": f"2026-04-{i:02d}T01:00:00+00:00",
                "results": {"loss": 0.1},
            }) + "\n")

        # 3 backfill entries: unique IDs, no run_number (as written by backfill_runs_from_events)
        for j in range(3):
            runs_jsonl.open("a").write(json.dumps({
                "$schema": "distillate/run/v1",
                "id": f"xp-backfill{j:04d}",
                "status": "completed",
                "timestamp": f"2026-04-10T{j:02d}:00:00+00:00",
                "results": {"loss": 0.2},
            }) + "\n")

        from distillate.experiment_tools.run_tools import start_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        r = start_run(state=state, project="test-proj", description="next")
        assert r["run_number"] == 6, (
            f"After 5 canonical runs + 3 backfill entries, next should be #6; "
            f"got #{r['run_number']}. Backfill entries must not inflate run_number."
        )


class TestProtocolDocsMentionRunNumber:
    """The agent has to KNOW to use run_number. The protocol files are the
    place to teach that -- otherwise the field is invisible.
    """

    PROTOCOL_FILES = [
        "distillate/autoresearch/CLAUDE.md",
        "distillate/autoresearch/PI.md",
    ]

    @pytest.mark.parametrize("rel", PROTOCOL_FILES)
    def test_protocol_mentions_run_number(self, rel):
        repo_root = Path(__file__).resolve().parent.parent
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert "run_number" in text, (
            f"{rel} must mention `run_number` so the agent uses the canonical "
            "counter in summaries/commits instead of self-counting."
        )


class TestConcludeRunCarriesRunNumber:
    """``start_run`` writes ``run_number`` into the running entry. If
    ``conclude_run`` drops that field when it appends the completion
    entry, every downstream consumer (``/experiments/list``, the
    renderer, the agent's own commit prose) falls back to recomputing
    the count — which drifts from the canonical sequence the moment
    state.runs gets stale or duplicated. Carry it forward.
    """

    def _state(self, project_dir):
        from distillate.state import State
        state = State()
        state.add_experiment("p1", "test-proj", str(project_dir))
        state.update_experiment("p1", duration_minutes=10)
        return state

    def test_completion_entry_has_same_run_number_as_start(self, project_dir):
        from distillate.experiment_tools.run_tools import start_run, conclude_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        start_result = start_run(state=state, project="test-proj", description="r1")
        run_id = start_result["run_id"]
        expected = start_result["run_number"]

        conclude_run(
            state=state, project="test-proj", run_id=run_id,
            results={"loss": 0.5}, reasoning="done",
        )

        runs = _read_runs(project_dir)
        # Last entry for this run_id should be the completion, with run_number
        completion = [r for r in runs if r["id"] == run_id and r.get("status") != "running"][-1]
        assert completion.get("run_number") == expected, (
            f"conclude_run must preserve run_number={expected}; "
            f"got {completion.get('run_number')}"
        )

    def test_crash_conclusion_also_carries_run_number(self, project_dir):
        from distillate.experiment_tools.run_tools import start_run, conclude_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        start_result = start_run(state=state, project="test-proj", description="r1")
        conclude_run(
            state=state, project="test-proj", run_id=start_result["run_id"],
            status="crash", results={}, reasoning="oops",
        )

        runs = _read_runs(project_dir)
        crash = [r for r in runs if r.get("status") == "crash"][-1]
        assert crash.get("run_number") == start_result["run_number"]

    def test_run_number_still_in_response_on_conclude(self, project_dir):
        """Agents use the run_number from conclude_run's response to
        compose commit messages ("Run 13: ..."). Drop-and-recompute
        breaks that chain; the number must be in the payload."""
        from distillate.experiment_tools.run_tools import start_run, conclude_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        start_result = start_run(state=state, project="test-proj", description="r1")
        conclude_result = conclude_run(
            state=state, project="test-proj", run_id=start_result["run_id"],
            results={"loss": 0.5}, reasoning="done",
        )
        assert conclude_result.get("run_number") == start_result["run_number"]

    def test_multiple_runs_keep_their_own_numbers(self, project_dir):
        from distillate.experiment_tools.run_tools import start_run, conclude_run
        _write_budget(project_dir, train=600, wrap=60)
        state = self._state(project_dir)

        pairs = []
        for _ in range(3):
            r = start_run(state=state, project="test-proj", description="x")
            conclude_run(state=state, project="test-proj", run_id=r["run_id"],
                         results={"loss": 0.5}, reasoning="done")
            pairs.append(r["run_number"])

        runs = _read_runs(project_dir)
        completions = [r for r in runs if r.get("status") != "running"]
        seen_numbers = [r.get("run_number") for r in completions]
        # Each completion carries the right number; no off-by-one or drift
        assert seen_numbers == pairs, (
            f"expected {pairs}, got {seen_numbers}"
        )
