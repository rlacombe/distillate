# Covers: distillate/budget.py (read_train_budget + Modal config helpers)
"""Public budget-reader helper for experiment training scripts.

The ``distillate-run`` wrapper enforces the training budget at the kernel
level (SIGTERM -> SIGKILL). But agents still write an in-script guard
(``if time.time() - _start > MAX_SECONDS: break``) so epochs don't get
chopped mid-step by a hard kill. That guard must stay in sync with the
real budget -- if the user bumps ``duration_minutes`` from 10 to 60, the
in-script guard has to track it without a script edit.

Before this helper shipped, agents pasted ``MAX_SECONDS = 300`` into
every training script and drifted out of sync the moment the budget
changed. ``read_train_budget()`` removes the hardcode: it reads
``.distillate/budget.json`` and returns the kernel-side budget minus a
wrap reserve, so every run automatically picks up the current value.

This file tests the *public* contract the agent sees. The internals are
small -- a file read, a couple of fallbacks -- so behavior is easy to
pin down. Any implementation that passes these tests is fine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Importability (principle: public API means importable two ways)
# ---------------------------------------------------------------------------


class TestImportability:
    def test_import_function_directly(self):
        from distillate.budget import read_train_budget  # noqa: F401

    def test_import_module(self):
        from distillate import budget
        assert callable(getattr(budget, "read_train_budget", None)), (
            "distillate.budget must expose read_train_budget as a callable"
        )


# ---------------------------------------------------------------------------
# Core behavior
# ---------------------------------------------------------------------------


@pytest.fixture
def project_cwd(tmp_path):
    """A tmp_path/.distillate dir ready to receive a budget.json."""
    d = tmp_path / ".distillate"
    d.mkdir()
    return tmp_path


def _write_budget(cwd: Path, data: dict) -> None:
    (cwd / ".distillate" / "budget.json").write_text(
        json.dumps(data), encoding="utf-8",
    )


class TestCoreBehavior:
    def test_reads_train_budget_minus_default_reserve(self, project_cwd):
        from distillate.budget import read_train_budget
        _write_budget(project_cwd, {"train_budget_seconds": 3600})
        # default reserve is 300
        assert read_train_budget(cwd=project_cwd) == 3300

    def test_honors_custom_reserve_seconds(self, project_cwd):
        from distillate.budget import read_train_budget
        _write_budget(project_cwd, {"train_budget_seconds": 3600})
        assert read_train_budget(reserve_seconds=60, cwd=project_cwd) == 3540
        assert read_train_budget(reserve_seconds=0, cwd=project_cwd) == 3600

    def test_falls_back_to_run_budget_when_train_missing(self, project_cwd):
        """Back-compat with legacy budget files that only have run_budget_seconds."""
        from distillate.budget import read_train_budget
        _write_budget(project_cwd, {"run_budget_seconds": 1800})
        assert read_train_budget(cwd=project_cwd) == 1500

    def test_prefers_train_budget_over_run_budget_when_both_present(self, project_cwd):
        """train_budget_seconds is the new canonical key and must win."""
        from distillate.budget import read_train_budget
        _write_budget(project_cwd, {
            "train_budget_seconds": 600,
            "run_budget_seconds": 9999,  # intentionally wrong to catch accidental fallback
        })
        assert read_train_budget(cwd=project_cwd) == 300


# ---------------------------------------------------------------------------
# Fallbacks -- no/bad file, missing keys
# ---------------------------------------------------------------------------


class TestFallbacks:
    def test_returns_fallback_when_budget_json_missing(self, tmp_path):
        """No .distillate/budget.json anywhere on the walk-up path."""
        from distillate.budget import read_train_budget
        # tmp_path has no .distillate; its parents won't either (pytest tmp)
        assert read_train_budget(cwd=tmp_path) == 3300

    def test_returns_fallback_when_budget_json_unparseable(self, project_cwd):
        from distillate.budget import read_train_budget
        (project_cwd / ".distillate" / "budget.json").write_text(
            "not valid json {{{", encoding="utf-8",
        )
        assert read_train_budget(cwd=project_cwd) == 3300

    def test_uses_3600_when_both_budget_keys_missing(self, project_cwd):
        """File exists but has neither train_budget_seconds nor run_budget_seconds.
        Spec says: fall back to 3600 -> minus reserve 300 = 3300."""
        from distillate.budget import read_train_budget
        _write_budget(project_cwd, {"something_else": 42})
        assert read_train_budget(cwd=project_cwd) == 3300

    def test_ignores_zero_and_negative_values(self, project_cwd):
        """A zero or negative budget is broken data; fall through rather than
        return a nonsense value to the caller."""
        from distillate.budget import read_train_budget
        _write_budget(project_cwd, {"train_budget_seconds": 0})
        result = read_train_budget(cwd=project_cwd)
        assert result > 0, (
            f"read_train_budget must never return <=0; got {result}"
        )


# ---------------------------------------------------------------------------
# Floor + walk-up
# ---------------------------------------------------------------------------


class TestFloorAndWalkUp:
    def test_floors_at_60_seconds_when_reserve_exceeds_budget(self, project_cwd):
        """Edge case: reserve >= budget would give 0 or negative. The floor
        at 60s guarantees the agent's in-script guard still has *some*
        headroom rather than an immediate break/divide-by-zero."""
        from distillate.budget import read_train_budget
        _write_budget(project_cwd, {"train_budget_seconds": 100})
        assert read_train_budget(reserve_seconds=90, cwd=project_cwd) == 60
        assert read_train_budget(reserve_seconds=500, cwd=project_cwd) == 60

    def test_walks_up_from_nested_cwd(self, project_cwd):
        """Agent scripts often run from `cd project/src/` -- the walk-up
        must find the budget.json at the project root."""
        from distillate.budget import read_train_budget
        _write_budget(project_cwd, {"train_budget_seconds": 600})
        nested = project_cwd / "src" / "models"
        nested.mkdir(parents=True)
        assert read_train_budget(cwd=nested) == 300  # 600 - 300

    def test_defaults_cwd_to_path_cwd(self, project_cwd, monkeypatch):
        """Calling without cwd= uses Path.cwd() -- the common case for
        scripts launched from the project root."""
        from distillate.budget import read_train_budget
        _write_budget(project_cwd, {"train_budget_seconds": 900})
        monkeypatch.chdir(project_cwd)
        assert read_train_budget() == 600  # 900 - 300


# ---------------------------------------------------------------------------
# Modal config -- read_modal_config / write_modal_config
# ---------------------------------------------------------------------------
#
# Shape in .distillate/budget.json:
#   {"modal": {"enabled": true, "gpu": "A100-80GB", "budget_usd": 25.0}}
#
# Behaviour: read_modal_config returns the dict when ``enabled`` is truthy;
# every other case (missing file, missing key, disabled, malformed) returns
# None so callers can gate with a simple ``if cfg is None: ...``.


class TestReadModalConfig:
    def test_returns_none_when_budget_json_missing(self, tmp_path):
        from distillate.budget import read_modal_config
        assert read_modal_config(cwd=tmp_path) is None

    def test_returns_none_when_no_modal_block(self, project_cwd):
        from distillate.budget import read_modal_config
        _write_budget(project_cwd, {"train_budget_seconds": 3600})
        assert read_modal_config(cwd=project_cwd) is None

    def test_returns_none_when_disabled(self, project_cwd):
        from distillate.budget import read_modal_config
        _write_budget(project_cwd, {
            "modal": {"enabled": False, "gpu": "A100-80GB", "budget_usd": 25.0},
        })
        assert read_modal_config(cwd=project_cwd) is None

    def test_returns_config_when_enabled(self, project_cwd):
        from distillate.budget import read_modal_config
        _write_budget(project_cwd, {
            "train_budget_seconds": 3600,
            "modal": {"enabled": True, "gpu": "A100-80GB", "budget_usd": 25.0},
        })
        cfg = read_modal_config(cwd=project_cwd)
        assert cfg is not None
        assert cfg["gpu"] == "A100-80GB"
        assert cfg["budget_usd"] == 25.0

    def test_returns_none_on_malformed_json(self, project_cwd):
        from distillate.budget import read_modal_config
        (project_cwd / ".distillate" / "budget.json").write_text(
            "not valid json", encoding="utf-8",
        )
        assert read_modal_config(cwd=project_cwd) is None

    def test_returns_none_when_modal_is_not_a_dict(self, project_cwd):
        """Defensive against hand-edited files that put a string/list there."""
        from distillate.budget import read_modal_config
        _write_budget(project_cwd, {"modal": "enabled"})
        assert read_modal_config(cwd=project_cwd) is None

    def test_walks_up_from_nested_cwd(self, project_cwd):
        from distillate.budget import read_modal_config
        _write_budget(project_cwd, {
            "modal": {"enabled": True, "gpu": "A100-80GB", "budget_usd": 10.0},
        })
        nested = project_cwd / "src" / "models"
        nested.mkdir(parents=True)
        cfg = read_modal_config(cwd=nested)
        assert cfg is not None
        assert cfg["budget_usd"] == 10.0


class TestWriteModalConfig:
    def test_creates_budget_json_when_absent(self, tmp_path):
        from distillate.budget import read_modal_config, write_modal_config
        write_modal_config(
            cwd=tmp_path, gpu="A100-80GB", budget_usd=25.0,
        )
        cfg = read_modal_config(cwd=tmp_path)
        assert cfg is not None
        assert cfg["gpu"] == "A100-80GB"
        assert cfg["budget_usd"] == 25.0

    def test_preserves_existing_train_budget(self, project_cwd):
        """Writing Modal config must not clobber train_budget_seconds.
        The file is shared state between distillate-run (wall-clock) and
        the Modal watcher ($) -- one side's write has to be a merge, not
        an overwrite."""
        import json
        from distillate.budget import read_train_budget, write_modal_config
        _write_budget(project_cwd, {"train_budget_seconds": 1800})
        write_modal_config(
            cwd=project_cwd, gpu="A100-80GB", budget_usd=50.0,
        )
        # train budget still honored
        assert read_train_budget(cwd=project_cwd) == 1500  # 1800 - 300
        # modal block added alongside
        raw = json.loads(
            (project_cwd / ".distillate" / "budget.json").read_text(),
        )
        assert raw["train_budget_seconds"] == 1800
        assert raw["modal"]["budget_usd"] == 50.0

    def test_overwrites_existing_modal_block(self, project_cwd):
        """Re-launching with a different budget must update, not append."""
        from distillate.budget import read_modal_config, write_modal_config
        _write_budget(project_cwd, {
            "modal": {"enabled": True, "gpu": "A100-80GB", "budget_usd": 25.0},
        })
        write_modal_config(
            cwd=project_cwd, gpu="A100-80GB", budget_usd=100.0,
        )
        cfg = read_modal_config(cwd=project_cwd)
        assert cfg is not None
        assert cfg["budget_usd"] == 100.0


# ---------------------------------------------------------------------------
# write_budget_json — must not clobber compute config written by
# write_compute_budget. These two helpers share the same budget.json file
# but manage different keys; write_budget_json must merge, not overwrite.
# ---------------------------------------------------------------------------


class TestWriteBudgetJsonPreservesComputeBlock:
    def test_preserves_hfjobs_compute_block(self, tmp_path):
        """write_budget_json called after write_compute_budget must leave the
        compute block intact. This guards against the clobber bug where
        write_budget_json created a fresh dict and wrote it, destroying any
        compute config that had just been persisted."""
        from distillate.budget import read_compute_budget, write_compute_budget
        from distillate.launcher import write_budget_json

        write_compute_budget(
            cwd=tmp_path, provider="hfjobs", gpu_type="a100-large", budget_usd=25.0,
        )
        assert read_compute_budget(cwd=tmp_path) is not None

        write_budget_json(tmp_path, {"duration_minutes": 10})

        cfg = read_compute_budget(cwd=tmp_path)
        assert cfg is not None, "write_budget_json must not clobber the compute block"
        assert cfg["provider"] == "hfjobs"
        assert cfg["gpu_type"] == "a100-large"
        assert cfg["budget_usd"] == 25.0

    def test_time_budget_still_written_when_compute_present(self, tmp_path):
        """The time budget keys must be present even when a compute block exists."""
        import json
        from distillate.budget import write_compute_budget
        from distillate.launcher import write_budget_json

        write_compute_budget(
            cwd=tmp_path, provider="hfjobs", gpu_type="a100-large", budget_usd=10.0,
        )
        write_budget_json(tmp_path, {"duration_minutes": 5})

        raw = json.loads((tmp_path / ".distillate" / "budget.json").read_text())
        assert raw["train_budget_seconds"] == 300
        assert raw["compute"]["provider"] == "hfjobs"
