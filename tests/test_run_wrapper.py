# Covers: distillate/run.py (distillate-run kill wrapper — L1 kernel-enforced deadline)
"""L1 — kernel-enforced kill.

``distillate-run`` reads ``.distillate/budget.json`` for the training budget,
exec's the inner command, sends SIGTERM at the deadline, and SIGKILL after a
grace window. The training subprocess physically stops whether or not the agent
cooperates. CLI lives at ``distillate.run`` (also exposed as the
``distillate-run`` console script).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
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


# ===========================================================================
# L1 — distillate-run wrapper kills training subprocess at the deadline.
# ===========================================================================


class TestL1WrapperModule:
    """The wrapper exists, is importable, and exposes a CLI entry point."""

    def test_module_importable(self):
        import distillate.run  # noqa: F401

    def test_module_has_main(self):
        import distillate.run as mod
        assert callable(getattr(mod, "main", None)), (
            "distillate.run must expose main() so 'python -m distillate.run' works"
        )

    def test_runs_via_python_dash_m(self, tmp_path):
        """Smoke test: invoking the module via `python -m` actually loads
        the module (not 'No module named ...') and exits cleanly when the
        wrapped command does."""
        proc = subprocess.run(
            [sys.executable, "-m", "distillate.run", "--", "true"],
            capture_output=True, text=True, timeout=10, cwd=tmp_path,
        )
        assert "No module named" not in proc.stderr, (
            f"distillate.run module is missing: {proc.stderr}"
        )
        assert proc.returncode == 0, (
            f"`python -m distillate.run -- true` should pass through exit 0; "
            f"got {proc.returncode}, stderr={proc.stderr}"
        )

    def test_console_script_registered(self):
        """``distillate-run`` is registered as a console script in pyproject.toml.

        We don't require the binary to be on PATH (CI may install via a
        different mechanism). We only require pyproject declares it.
        """
        repo_root = Path(__file__).resolve().parent.parent
        text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
        # Tolerate both quoted forms; require the script -> module mapping.
        assert "distillate-run" in text, (
            "Add 'distillate-run = \"distillate.run:main\"' to "
            "[project.scripts] in pyproject.toml"
        )
        assert "distillate.run:main" in text, (
            "console script must point at distillate.run:main"
        )


class TestL1WrapperBudgetResolution:
    """Where does the wrapper get its budget from?

    Precedence (strictest to laxest):
      1. ``--budget=N`` CLI flag
      2. ``DISTILLATE_TRAIN_BUDGET_SECONDS`` env var
      3. ``DISTILLATE_RUN_BUDGET_SECONDS`` env var (back-compat)
      4. ``.distillate/budget.json`` ``train_budget_seconds`` (then ``run_budget_seconds``)
      5. Hard default (300s) with a stderr warning
    """

    def test_resolves_from_budget_json(self, project_dir):
        from distillate.run import resolve_budget
        _write_budget(project_dir, train=42)
        secs = resolve_budget(cwd=project_dir, cli_budget=None)
        assert secs == 42

    def test_resolves_from_train_field_first(self, project_dir):
        """When budget.json has both legacy run_budget and new train_budget,
        the train field wins so a future config can diverge them."""
        from distillate.run import resolve_budget
        path = project_dir / ".distillate" / "budget.json"
        path.write_text(json.dumps({
            "run_budget_seconds": 999,
            "train_budget_seconds": 42,
        }))
        secs = resolve_budget(cwd=project_dir, cli_budget=None)
        assert secs == 42

    def test_resolves_from_legacy_run_budget(self, project_dir):
        """Back-compat: an old budget.json with only run_budget_seconds works."""
        from distillate.run import resolve_budget
        path = project_dir / ".distillate" / "budget.json"
        path.write_text(json.dumps({"run_budget_seconds": 75}))
        assert resolve_budget(cwd=project_dir, cli_budget=None) == 75

    def test_walks_up_to_find_distillate_dir(self, project_dir):
        from distillate.run import resolve_budget
        _write_budget(project_dir, train=33)
        nested = project_dir / "src" / "models"
        nested.mkdir(parents=True)
        assert resolve_budget(cwd=nested, cli_budget=None) == 33

    def test_cli_flag_overrides_file(self, project_dir):
        from distillate.run import resolve_budget
        _write_budget(project_dir, train=600)
        assert resolve_budget(cwd=project_dir, cli_budget=15) == 15

    def test_env_var_overrides_file(self, project_dir, monkeypatch):
        from distillate.run import resolve_budget
        _write_budget(project_dir, train=600)
        monkeypatch.setenv("DISTILLATE_TRAIN_BUDGET_SECONDS", "12")
        assert resolve_budget(cwd=project_dir, cli_budget=None) == 12

    def test_legacy_env_var_back_compat(self, project_dir, monkeypatch):
        from distillate.run import resolve_budget
        # No file, no new var -- fall back to the legacy env var
        monkeypatch.setenv("DISTILLATE_RUN_BUDGET_SECONDS", "8")
        monkeypatch.delenv("DISTILLATE_TRAIN_BUDGET_SECONDS", raising=False)
        assert resolve_budget(cwd=project_dir, cli_budget=None) == 8

    def test_hard_default_when_nothing_configured(self, tmp_path, monkeypatch):
        from distillate.run import resolve_budget
        monkeypatch.delenv("DISTILLATE_TRAIN_BUDGET_SECONDS", raising=False)
        monkeypatch.delenv("DISTILLATE_RUN_BUDGET_SECONDS", raising=False)
        secs = resolve_budget(cwd=tmp_path, cli_budget=None)
        # A sane default exists (training without a budget would defeat the wrapper)
        assert secs > 0
        # And it's documented as the default
        assert secs <= 600  # not absurd


class TestL1WrapperKill:
    """The wrapper kills the training process at the budget. SIGTERM at the
    deadline; if the child ignores SIGTERM, SIGKILL after the grace window.
    """

    def _python_loop_command(self) -> list[str]:
        """A command that prints, sleeps in 0.1s ticks, and ignores SIGTERM
        if asked. Used to verify the wrapper's escalation from TERM to KILL.
        """
        return [
            sys.executable, "-c",
            "import sys, time, signal\n"
            "ignore = '--ignore-term' in sys.argv\n"
            "if ignore: signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "for i in range(600):\n"
            "    print(f'tick {i}', flush=True)\n"
            "    time.sleep(0.1)\n",
        ]

    def test_completes_within_budget_returns_exit_zero(self, project_dir):
        _write_budget(project_dir, train=10)
        proc = subprocess.run(
            [sys.executable, "-m", "distillate.run", "--", "true"],
            capture_output=True, text=True, timeout=15, cwd=project_dir,
        )
        assert proc.returncode == 0, (
            f"Quick command should pass through exit 0; got {proc.returncode}, "
            f"stderr={proc.stderr}"
        )

    def test_forwards_inner_exit_code(self, project_dir):
        _write_budget(project_dir, train=10)
        proc = subprocess.run(
            [sys.executable, "-m", "distillate.run", "--",
             sys.executable, "-c", "import sys; sys.exit(7)"],
            capture_output=True, text=True, timeout=15, cwd=project_dir,
        )
        assert proc.returncode == 7

    def test_kills_overrunning_process(self, project_dir):
        """Process running longer than budget is killed; total wall clock
        stays close to the budget (well within 1.5x)."""
        _write_budget(project_dir, train=2)
        cmd = self._python_loop_command()
        t0 = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-m", "distillate.run", "--budget=2", "--"] + cmd,
            capture_output=True, text=True, timeout=15, cwd=project_dir,
        )
        elapsed = time.monotonic() - t0
        # Wrapper must actually run the child (>= 1s of the 2s budget)
        # -- otherwise the test is meaningless (e.g. module doesn't exist
        # and `python -m` exits immediately).
        assert "tick" in proc.stdout, (
            f"Wrapper didn't run the child or didn't forward stdout. "
            f"stdout={proc.stdout!r}, stderr={proc.stderr!r}"
        )
        assert elapsed >= 1.5, (
            f"Wrapper exited too fast ({elapsed:.2f}s) -- did it actually launch the child?"
        )
        # Strict: must finish in well under unconstrained 60s
        assert elapsed < 6.0, (
            f"Wrapper allowed process to run {elapsed:.1f}s past 2s budget"
        )
        # Conventional timeout exit code (124 from coreutils) or non-zero kill code
        assert proc.returncode != 0, (
            "Killed process must signal failure (non-zero exit)"
        )

    def test_uses_124_exit_code_on_timeout(self, project_dir):
        """Convention: 124 means 'timed out' (matches GNU timeout(1)).
        Tooling can rely on this to distinguish budget-kills from crashes.
        """
        _write_budget(project_dir, train=2)
        proc = subprocess.run(
            [sys.executable, "-m", "distillate.run", "--budget=2", "--"]
            + self._python_loop_command(),
            capture_output=True, text=True, timeout=15, cwd=project_dir,
        )
        assert proc.returncode == 124, (
            f"Expected exit 124 (GNU timeout convention); got {proc.returncode}"
        )

    def test_escalates_to_sigkill_when_child_ignores_term(self, project_dir):
        """A child that ignores SIGTERM must still die — within budget +
        grace window. Otherwise the wrapper is no better than `&`.
        """
        _write_budget(project_dir, train=2)
        cmd = self._python_loop_command() + ["--ignore-term"]
        t0 = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-m", "distillate.run",
             "--budget=2", "--grace=2", "--"] + cmd,
            capture_output=True, text=True, timeout=15, cwd=project_dir,
        )
        elapsed = time.monotonic() - t0
        # Wrapper must actually have run the child past SIGTERM
        assert "tick" in proc.stdout, (
            f"Wrapper didn't run the child. stdout={proc.stdout!r}, "
            f"stderr={proc.stderr!r}"
        )
        # Budget + grace = at least 3s of real time
        assert elapsed >= 3.0, (
            f"Wrapper exited too fast ({elapsed:.2f}s); SIGTERM escalation untested"
        )
        # Budget (2s) + grace (2s) + slack (~1s for fork/exec)
        assert elapsed < 6.5, (
            f"SIGKILL escalation didn't trigger; ran {elapsed:.1f}s"
        )
        assert proc.returncode != 0

    def test_forwards_stdout_during_run(self, project_dir):
        """Output must stream to stdout, not get swallowed. Otherwise the
        agent can't see partial results from a run that hit the budget.
        """
        _write_budget(project_dir, train=10)
        proc = subprocess.run(
            [sys.executable, "-m", "distillate.run", "--",
             sys.executable, "-c", "print('hello-from-child')"],
            capture_output=True, text=True, timeout=15, cwd=project_dir,
        )
        assert "hello-from-child" in proc.stdout

    def test_prints_kill_notice_on_timeout(self, project_dir):
        """When the wrapper kills the child, it tells the agent why so the
        agent can make sense of partial output / non-zero exit.
        """
        _write_budget(project_dir, train=2)
        proc = subprocess.run(
            [sys.executable, "-m", "distillate.run", "--budget=2", "--"]
            + self._python_loop_command(),
            capture_output=True, text=True, timeout=15, cwd=project_dir,
        )
        notice = proc.stderr + proc.stdout
        assert "budget" in notice.lower(), (
            f"Wrapper must print a recognizable budget-kill notice; got: {notice!r}"
        )
