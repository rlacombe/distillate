"""Kernel-enforced training-budget wrapper for autonomous experiments.

Usage:
    distillate-run [--budget=N] [--grace=N] -- <command...>
    python -m distillate.run [--budget=N] [--grace=N] -- <command...>

Reads ``.distillate/budget.json`` (walking up from CWD), exec's the inner
command, sends SIGTERM at the deadline and SIGKILL after a grace window.
Stdout/stderr stream through unchanged. Exit code 124 (matching GNU
``timeout(1)``) signals a budget kill; otherwise the child's exit code
is forwarded.

Budget precedence: ``--budget`` flag > ``DISTILLATE_TRAIN_BUDGET_SECONDS``
env > ``DISTILLATE_RUN_BUDGET_SECONDS`` env > ``budget.json`` > 300s default.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

DEFAULT_BUDGET_SECONDS = 300
DEFAULT_GRACE_SECONDS = 10
TIMEOUT_EXIT_CODE = 124  # GNU timeout(1) convention


def _find_distillate_dir(start: Path) -> Optional[Path]:
    """Walk up from ``start`` looking for a ``.distillate`` directory."""
    for parent in [start, *start.parents]:
        if (parent / ".distillate").is_dir():
            return parent / ".distillate"
    return None


def resolve_budget(*, cwd: Path, cli_budget: Optional[int]) -> int:
    """Determine the training budget in seconds.

    Precedence:
      1. ``cli_budget`` (the ``--budget=N`` flag)
      2. ``DISTILLATE_TRAIN_BUDGET_SECONDS`` env var
      3. ``DISTILLATE_RUN_BUDGET_SECONDS`` env var (back-compat)
      4. ``train_budget_seconds`` (then ``run_budget_seconds``) from
         ``.distillate/budget.json``
      5. ``DEFAULT_BUDGET_SECONDS`` with a stderr warning
    """
    if cli_budget is not None and cli_budget > 0:
        return cli_budget

    for var in ("DISTILLATE_TRAIN_BUDGET_SECONDS", "DISTILLATE_RUN_BUDGET_SECONDS"):
        v = os.environ.get(var, "").strip()
        if v:
            try:
                n = int(v)
                if n > 0:
                    return n
            except ValueError:
                pass

    distillate_dir = _find_distillate_dir(cwd)
    if distillate_dir is not None:
        budget_path = distillate_dir / "budget.json"
        if budget_path.exists():
            try:
                data = json.loads(budget_path.read_text(encoding="utf-8"))
                for key in ("train_budget_seconds", "run_budget_seconds"):
                    val = data.get(key)
                    if val:
                        try:
                            n = int(val)
                            if n > 0:
                                return n
                        except (TypeError, ValueError):
                            pass
            except (OSError, json.JSONDecodeError):
                pass

    print(
        f"[distillate-run] no budget configured; using default "
        f"{DEFAULT_BUDGET_SECONDS}s",
        file=sys.stderr,
    )
    return DEFAULT_BUDGET_SECONDS


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Argparse with ``--`` separator. Anything after ``--`` is the command."""
    if "--" in argv:
        idx = argv.index("--")
        wrapper_args, command = argv[:idx], argv[idx + 1:]
    else:
        wrapper_args, command = argv, []

    parser = argparse.ArgumentParser(
        prog="distillate-run",
        description="Run a training command with a kernel-enforced budget.",
    )
    parser.add_argument("--budget", type=int, default=None,
                        help="Training budget in seconds (overrides budget.json)")
    parser.add_argument("--grace", type=int, default=DEFAULT_GRACE_SECONDS,
                        help=f"Seconds between SIGTERM and SIGKILL (default {DEFAULT_GRACE_SECONDS})")
    args = parser.parse_args(wrapper_args)
    return args, command


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for both ``python -m distillate.run`` and ``distillate-run``."""
    if argv is None:
        argv = sys.argv[1:]

    args, command = _parse_args(argv)
    if not command:
        print("[distillate-run] no command given (use `-- <cmd>`)", file=sys.stderr)
        sys.exit(2)

    cwd = Path.cwd()
    budget = resolve_budget(cwd=cwd, cli_budget=args.budget)
    grace = max(1, int(args.grace))

    # Spawn child in its own process group so we can signal the whole tree.
    # On macOS/Linux, start_new_session=True calls setsid().
    try:
        proc = subprocess.Popen(command, start_new_session=True)
    except FileNotFoundError as e:
        print(f"[distillate-run] {e}", file=sys.stderr)
        sys.exit(127)

    # Forward SIGINT/SIGTERM from parent to the child group so Ctrl+C works.
    def _forward(signum, _frame):
        try:
            os.killpg(proc.pid, signum)
        except ProcessLookupError:
            pass

    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(s, _forward)
        except (ValueError, OSError):
            pass

    deadline = time.monotonic() + budget
    timed_out = False

    while True:
        try:
            rc = proc.wait(timeout=0.5)
            return rc
        except subprocess.TimeoutExpired:
            pass

        if time.monotonic() >= deadline and not timed_out:
            timed_out = True
            print(
                f"\n[distillate-run] training budget of {budget}s reached; "
                f"sending SIGTERM (grace {grace}s before SIGKILL)",
                file=sys.stderr,
                flush=True,
            )
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            kill_at = time.monotonic() + grace

            # Grace window: child has `grace` seconds to clean up
            while time.monotonic() < kill_at:
                try:
                    proc.wait(timeout=0.2)
                    print(
                        f"[distillate-run] child exited gracefully after SIGTERM",
                        file=sys.stderr,
                        flush=True,
                    )
                    return TIMEOUT_EXIT_CODE
                except subprocess.TimeoutExpired:
                    continue

            # Grace expired; SIGKILL
            print(
                f"[distillate-run] child ignored SIGTERM, sending SIGKILL",
                file=sys.stderr,
                flush=True,
            )
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return TIMEOUT_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())
