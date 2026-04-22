# Covers: distillate/launcher.py, distillate/agents.py
"""Permission-mode invariants for autonomous experimentalists.

Every Claude Code invocation for a Tier 3a experimentalist must use
``--permission-mode auto``. Never ``bypassPermissions`` — that flag triggers
an interactive confirmation dialog that halts the autonomous loop.

Sections covered:
  1. Command-builder entry points emit auto.
  2. Source-code grep: no residual ``--permission-mode bypassPermissions`` in
     launch paths.
  7. End-to-end: shlex-tokenised command includes auto as a properly
     separated token.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest


# ===========================================================================
# 1. Command builders -- auto for every launch path
# ===========================================================================


class TestExperimentCommandBuilders:
    """Both command-builder entry points (``distillate.launcher`` and
    ``distillate.agents``) must emit ``--permission-mode auto``.
    """

    def test_launcher_build_claude_command_uses_auto(self):
        from distillate.launcher import _build_claude_command
        cmd = _build_claude_command(Path("/proj/PROMPT.md"))
        assert "--permission-mode auto" in cmd, (
            f"Launcher must emit auto, got: {cmd}"
        )

    def test_launcher_build_claude_command_never_uses_bypass(self):
        from distillate.launcher import _build_claude_command
        cmd = _build_claude_command(Path("/proj/PROMPT.md"))
        assert "--permission-mode bypassPermissions" not in cmd

    def test_launcher_build_claude_command_with_override_uses_auto(self):
        from distillate.launcher import _build_claude_command
        cmd = _build_claude_command(
            Path("/proj/PROMPT.md"),
            prompt_override="custom prompt",
        )
        assert "--permission-mode auto" in cmd

    def test_launcher_build_claude_command_with_model_uses_auto(self):
        from distillate.launcher import _build_claude_command
        cmd = _build_claude_command(
            Path("/proj/PROMPT.md"),
            model="claude-opus-4-6",
        )
        assert "--permission-mode auto" in cmd

    def test_agents_build_claude_command_uses_auto(self):
        from distillate.agents import _build_claude_command
        cmd = _build_claude_command("do the thing")
        assert "--permission-mode auto" in cmd

    def test_agents_build_claude_command_never_uses_bypass(self):
        from distillate.agents import _build_claude_command
        cmd = _build_claude_command("do the thing")
        assert "--permission-mode bypassPermissions" not in cmd

    def test_agents_build_claude_command_with_effort_uses_auto(self):
        from distillate.agents import _build_claude_command
        cmd = _build_claude_command("do the thing", effort="medium")
        assert "--permission-mode auto" in cmd
        assert "--effort medium" in cmd

    def test_harness_adapter_claude_code_uses_auto(self):
        from distillate.agents import ClaudeCodeAdapter
        cmd = ClaudeCodeAdapter().build_command("prompt")
        assert "--permission-mode auto" in cmd

    def test_build_agent_command_claude_type_uses_auto(self):
        from distillate.agents import build_agent_command
        cmd = build_agent_command("claude", "go")
        assert "--permission-mode auto" in cmd


class TestPermissionModeNotBypass:
    """Regression shield: no experimentalist launch path may emit
    ``--permission-mode bypassPermissions``. That flag shows an interactive
    confirmation dialog that blocks the autonomous agent loop.
    """

    _EXPERIMENTALIST_FILES = [
        "distillate/launcher.py",
        "distillate/agents.py",
    ]

    def test_no_permission_mode_bypass_in_experimentalist_paths(self):
        repo_root = Path(__file__).resolve().parent.parent
        offending: list[str] = []
        for rel in self._EXPERIMENTALIST_FILES:
            text = (repo_root / rel).read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), start=1):
                stripped = line.split("#", 1)[0]
                if re.search(r"permission-mode[\"'\s,]+bypassPermissions\b", stripped):
                    offending.append(f"{rel}:{i}: {line.strip()}")
        assert not offending, (
            "Experimentalist launch code must use auto (bypassPermissions shows "
            "an interactive dialog):\n  " + "\n  ".join(offending)
        )


# ===========================================================================
# 7. End-to-end: the full command string is autonomous-compatible
# ===========================================================================


class TestEndToEndLaunchCommand:

    def test_launcher_command_shlex_tokens_include_auto(self):
        from distillate.launcher import _build_claude_command
        cmd = _build_claude_command(Path("/proj/PROMPT.md"))
        tokens = shlex.split(cmd)
        assert "--permission-mode" in tokens
        idx = tokens.index("--permission-mode")
        assert tokens[idx + 1] == "auto"

    def test_agents_command_shlex_tokens_include_auto(self):
        from distillate.agents import _build_claude_command
        cmd = _build_claude_command("hi")
        tokens = shlex.split(cmd)
        assert "--permission-mode" in tokens
        idx = tokens.index("--permission-mode")
        assert tokens[idx + 1] == "auto"

    def test_launcher_command_has_no_disallowed_permission_values(self):
        from distillate.launcher import _build_claude_command
        cmd = _build_claude_command(Path("/proj/PROMPT.md"))
        for bad in ("bypassPermissions", "default", "acceptEdits", "dontAsk", "plan"):
            assert f"--permission-mode {bad}" not in cmd, (
                f"Experimentalist must use auto, not {bad}"
            )
