# Covers: distillate/agents.py
"""Tests for HarnessAdapter pattern (Phase 4)."""

import pytest
from unittest.mock import patch

from distillate.agents import (
    HarnessAdapter,
    ClaudeCodeAdapter,
    CodexAdapter,
    GeminiCLIAdapter,
    OpenHandsAdapter,
    HARNESS_ADAPTERS,
    get_harness_adapter,
    list_harness_adapters,
)


class TestHarnessAdapters:

    def test_claude_code_adapter(self):
        adapter = ClaudeCodeAdapter()
        assert adapter.id == "claude-code"
        assert adapter.binary == "claude"
        assert adapter.mcp_support is True
        cmd = adapter.build_command("test prompt", effort="high")
        assert "claude" in cmd
        assert "--permission-mode" in cmd

    def test_codex_adapter(self):
        adapter = CodexAdapter()
        assert adapter.id == "codex"
        assert adapter.binary == "codex"
        assert adapter.mcp_support is False
        cmd = adapter.build_command("test prompt")
        assert "codex" in cmd

    def test_gemini_adapter(self):
        adapter = GeminiCLIAdapter()
        assert adapter.id == "gemini-cli"
        assert adapter.context_file == "GEMINI.md"

    def test_openhands_adapter(self):
        adapter = OpenHandsAdapter()
        assert adapter.id == "openhands"

    def test_registry_has_all_adapters(self):
        assert "claude-code" in HARNESS_ADAPTERS
        assert "codex" in HARNESS_ADAPTERS
        assert "gemini-cli" in HARNESS_ADAPTERS
        assert "openhands" in HARNESS_ADAPTERS

    def test_get_harness_adapter_default(self):
        adapter = get_harness_adapter("nonexistent")
        assert adapter.id == "claude-code"

    def test_get_harness_adapter_by_id(self):
        adapter = get_harness_adapter("codex")
        assert adapter.id == "codex"

    def test_list_harness_adapters(self):
        adapters = list_harness_adapters()
        assert isinstance(adapters, list)
        assert len(adapters) >= 4
        ids = {a["id"] for a in adapters}
        assert "claude-code" in ids
        assert "codex" in ids

    def test_adapter_to_dict(self):
        adapter = ClaudeCodeAdapter()
        d = adapter.to_dict()
        assert d["id"] == "claude-code"
        assert d["label"] == "Claude Code"
        assert d["mcp_support"] is True
        assert "available" in d

    def test_build_command_with_model(self):
        adapter = CodexAdapter()
        cmd = adapter.build_command("test", model="gpt-5")
        assert "--model" in cmd
        assert "gpt-5" in cmd

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_available_when_binary_found(self, mock_which):
        adapter = ClaudeCodeAdapter()
        assert adapter.available is True

    @patch("shutil.which", return_value=None)
    def test_unavailable_when_binary_missing(self, mock_which):
        adapter = CodexAdapter()
        assert adapter.available is False
