# Covers: distillate/launcher.py (scaffold_experiment, _refresh_protocol_files)
"""Config-file invariants for autonomous experimentalist projects.

Sections covered:
  4. ``.mcp.json`` wiring: both scaffold and refresh point at the MCP server.
  5. ``.claude/settings.local.json`` allowlist matches the whitelist exactly.
  6. Scaffold and refresh write-paths agree with each other and with the
     single source-of-truth constant.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Expected whitelist -- duplicated here so the test fails loudly if the
# source-of-truth constant is renamed or its contents change silently.
# ---------------------------------------------------------------------------

EXPECTED_EXPERIMENTALIST_TOOLS = frozenset({
    "start_run",
    "conclude_run",
    "save_enrichment",
    "annotate_run",
    "submit_hf_job",
    "check_hf_job",
    "cancel_hf_job",
    "list_hf_jobs",
})

FORBIDDEN_EXPERIMENTALIST_TOOLS = frozenset({
    # Cross-project introspection -- Nicolas territory
    "list_experiments", "get_experiment_details", "compare_runs",
    "compare_experiments", "get_experiment_notebook",
    # Orchestration -- only Nicolas launches/stops other experiments
    "launch_experiment", "stop_experiment", "init_experiment",
    "continue_experiment", "sweep_experiment", "steer_experiment",
    "manage_session", "queue_sessions",
    # Destructive CRUD -- an autonomous loop must not be able to delete
    "delete_experiment", "delete_run", "rename_experiment", "rename_run",
    "update_experiment", "update_goals", "purge_hook_runs",
    # Registry / creation
    "add_experiment", "create_github_repo", "link_paper",
    "save_template", "list_templates",
    # Paper library -- not in the experiment loop
    "search_papers", "get_paper_details", "get_reading_stats",
    "get_queue", "get_recent_reads", "suggest_next_reads",
    "synthesize_across_papers", "run_sync", "refresh_metadata",
    "reprocess_paper", "promote_papers", "get_trending_papers",
    "search_hf_models", "search_hf_datasets",
    "find_paper_associations", "add_paper_to_zotero",
    "delete_paper", "reading_report",
    "discover_relevant_papers", "suggest_from_literature",
    "extract_baselines", "replicate_paper",
    # Scanning/status -- orchestrator territory, not experimentalist
    "scan_experiment", "experiment_status", "get_run_details",
    # Workspace / coding sessions -- different primitive entirely
    "create_workspace", "list_workspaces", "get_workspace",
    "add_workspace_repo", "launch_coding_session",
    "stop_coding_session", "restart_coding_session",
    "recover_coding_session", "recover_all_sessions",
    "stop_all_sessions",
    # Project notes / notebook -- Nicolas surface
    "get_workspace_notes", "save_workspace_notes", "append_lab_book",
    "read_lab_notebook", "notebook_digest",
    # Long-lived agents -- Spirit management, definitely not
    "list_agent_templates", "create_agent", "list_agents",
    "start_agent_session", "stop_agent_session",
    "update_agent", "delete_agent",
    # Recursive reasoning sandbox -- Nicolas only
    "lab_repl",
})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scaffold_target(tmp_path, monkeypatch):
    """Isolate CONFIG_DIR + templates dir; create a minimal template."""
    import distillate.config as config_mod
    import distillate.launcher as launcher_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(launcher_mod, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.delenv("HF_TOKEN", raising=False)

    tmpl_root = tmp_path / "config" / "templates" / "mini"
    tmpl_root.mkdir(parents=True)
    (tmpl_root / "PROMPT.md").write_text("do the thing\n", encoding="utf-8")

    target = tmp_path / "proj"

    def _scaffold():
        from distillate.launcher import scaffold_experiment
        return scaffold_experiment("mini", target)

    return _scaffold, target


@pytest.fixture
def refresh_target(tmp_path, monkeypatch):
    """Return (callable, target) for exercising ``_refresh_protocol_files``."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    target = tmp_path / "existing_proj"
    target.mkdir()

    def _refresh():
        from distillate.launcher import _refresh_protocol_files
        _refresh_protocol_files(target, agent_type="claude")
        return target

    return _refresh, target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _mcp_distillate_entry(mcp_json_path: Path) -> dict:
    data = _read_json(mcp_json_path)
    servers = data.get("mcpServers") or {}
    assert "distillate" in servers, f".mcp.json missing 'distillate' entry: {data}"
    return servers["distillate"]


def _allowlist(settings_local_path: Path) -> list[str]:
    return _read_json(settings_local_path)["permissions"]["allow"]


# ===========================================================================
# 4. .mcp.json wiring -- points at the MCP server.
# ===========================================================================


class TestMcpJsonWiring:
    """Both write paths (scaffold + refresh) wire the project's
    ``.mcp.json`` to the experiment-scoped server.
    """

    def test_scaffold_writes_mcp_json(self, scaffold_target):
        scaffold, target = scaffold_target
        scaffold()
        assert (target / ".mcp.json").exists()

    def test_scaffold_mcp_json_points_at_mcp_server(self, scaffold_target):
        scaffold, target = scaffold_target
        scaffold()
        entry = _mcp_distillate_entry(target / ".mcp.json")
        args = entry.get("args") or []
        assert "-m" in args
        idx = args.index("-m")
        assert args[idx + 1] == "distillate.mcp_server", (
            f"scaffold .mcp.json must invoke the MCP server, got: {args}"
        )

    def test_refresh_writes_mcp_json(self, refresh_target):
        refresh, target = refresh_target
        refresh()
        assert (target / ".mcp.json").exists()

    def test_refresh_mcp_json_points_at_mcp_server(self, refresh_target):
        refresh, target = refresh_target
        refresh()
        entry = _mcp_distillate_entry(target / ".mcp.json")
        args = entry.get("args") or []
        assert "-m" in args
        idx = args.index("-m")
        assert args[idx + 1] == "distillate.mcp_server"

    def test_refresh_overwrites_stale_mcp_json(self, refresh_target):
        """If an older version left a stale ``.mcp.json``, the next refresh
        must overwrite it with current Python path.
        """
        refresh, target = refresh_target
        (target / ".mcp.json").write_text(json.dumps({
            "mcpServers": {
                "distillate": {
                    "command": "/usr/bin/python3",
                    "args": ["-m", "distillate.mcp_server"],
                },
            },
        }), encoding="utf-8")

        refresh()

        entry = _mcp_distillate_entry(target / ".mcp.json")
        assert entry["command"] == sys.executable

    def test_scaffold_mcp_uses_current_python(self, scaffold_target):
        scaffold, target = scaffold_target
        scaffold()
        entry = _mcp_distillate_entry(target / ".mcp.json")
        assert entry["command"] == sys.executable

    def test_hf_mcp_server_preserved_when_token_set(self, scaffold_target, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_test_token_not_real")
        scaffold, target = scaffold_target
        scaffold()
        servers = _read_json(target / ".mcp.json")["mcpServers"]
        assert "huggingface" in servers, (
            "HF_TOKEN set -> huggingface MCP server must be wired in .mcp.json"
        )
        args = servers["distillate"]["args"]
        assert "distillate.mcp_server" in args

    def test_hf_mcp_server_absent_without_token(self, scaffold_target, monkeypatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        scaffold, target = scaffold_target
        scaffold()
        servers = _read_json(target / ".mcp.json")["mcpServers"]
        assert "huggingface" not in servers


# ===========================================================================
# 5. .claude/settings.local.json allowlist matches the whitelist exactly.
# ===========================================================================


class TestSettingsLocalAllowlist:
    """The allowlist in ``.claude/settings.local.json`` is the last line of
    defense: if the MCP server ever exposed an extra tool, Claude Code would
    still refuse to call it without a prompt.
    """

    def test_scaffold_writes_settings_local(self, scaffold_target):
        scaffold, target = scaffold_target
        scaffold()
        assert (target / ".claude" / "settings.local.json").exists()

    def test_scaffold_allowlist_includes_every_whitelist_tool(self, scaffold_target):
        scaffold, target = scaffold_target
        scaffold()
        allow = _allowlist(target / ".claude" / "settings.local.json")
        for tool in EXPECTED_EXPERIMENTALIST_TOOLS:
            entry = f"mcp__distillate__{tool}"
            assert entry in allow, (
                f"Allowlist missing {entry} -- agent would be prompted"
            )

    @pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_EXPERIMENTALIST_TOOLS))
    def test_scaffold_allowlist_excludes_forbidden(self, scaffold_target, forbidden):
        scaffold, target = scaffold_target
        scaffold()
        allow = _allowlist(target / ".claude" / "settings.local.json")
        entry = f"mcp__distillate__{forbidden}"
        assert entry not in allow, (
            f"Allowlist leaked forbidden MCP tool: {entry}"
        )

    def test_scaffold_allowlist_has_baseline_tools(self, scaffold_target):
        """The agent needs Read/Write/Edit/Bash for the training loop."""
        scaffold, target = scaffold_target
        scaffold()
        allow = _allowlist(target / ".claude" / "settings.local.json")
        for baseline in ("Read", "Write", "Edit", "Glob", "Grep"):
            assert baseline in allow
        bash_entries = [a for a in allow if a.startswith("Bash(")]
        assert any("python3" in a for a in bash_entries)
        assert any("git" in a for a in bash_entries)

    def test_refresh_allowlist_includes_every_whitelist_tool(self, refresh_target):
        refresh, target = refresh_target
        refresh()
        allow = _allowlist(target / ".claude" / "settings.local.json")
        for tool in EXPECTED_EXPERIMENTALIST_TOOLS:
            entry = f"mcp__distillate__{tool}"
            assert entry in allow

    @pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_EXPERIMENTALIST_TOOLS))
    def test_refresh_allowlist_excludes_forbidden(self, refresh_target, forbidden):
        refresh, target = refresh_target
        refresh()
        allow = _allowlist(target / ".claude" / "settings.local.json")
        entry = f"mcp__distillate__{forbidden}"
        assert entry not in allow

    def test_refresh_syncs_drifted_allowlist(self, refresh_target):
        """If a stale allowlist is missing entries or has forbidden ones,
        refresh must bring it into line.
        """
        refresh, target = refresh_target
        claude_dir = target / ".claude"
        claude_dir.mkdir(exist_ok=True)
        drifted = claude_dir / "settings.local.json"
        drifted.write_text(json.dumps({
            "permissions": {
                "allow": [
                    "Read",
                    "mcp__distillate__list_experiments",
                    "mcp__distillate__launch_experiment",
                ],
            },
        }), encoding="utf-8")

        refresh()

        allow = _allowlist(drifted)
        assert "mcp__distillate__list_experiments" not in allow
        assert "mcp__distillate__launch_experiment" not in allow
        for tool in EXPECTED_EXPERIMENTALIST_TOOLS:
            assert f"mcp__distillate__{tool}" in allow

    def test_hf_mcp_tools_conditional_on_token(self, scaffold_target, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_test_token_not_real")
        scaffold, target = scaffold_target
        scaffold()
        allow = _allowlist(target / ".claude" / "settings.local.json")
        for hf_tool in ("search_models", "search_datasets",
                        "search_papers", "search_spaces"):
            assert f"mcp__huggingface__{hf_tool}" in allow

    def test_hf_mcp_tools_absent_without_token(self, scaffold_target, monkeypatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        scaffold, target = scaffold_target
        scaffold()
        allow = _allowlist(target / ".claude" / "settings.local.json")
        for entry in allow:
            assert not entry.startswith("mcp__huggingface__"), (
                f"HF allowlist entry leaked without HF_TOKEN: {entry}"
            )


# ===========================================================================
# 6. Scaffold and refresh paths agree with the source of truth.
# ===========================================================================


class TestWritePathsAgreeOnSourceOfTruth:
    """The scaffold-time and refresh-time writers must never drift."""

    def test_scaffold_and_refresh_write_same_allowlist(
        self, tmp_path, monkeypatch, scaffold_target, refresh_target
    ):
        scaffold, scaffold_tgt = scaffold_target
        refresh, refresh_tgt = refresh_target

        scaffold()
        refresh()

        scaffold_allow = set(_allowlist(scaffold_tgt / ".claude" / "settings.local.json"))
        refresh_allow = set(_allowlist(refresh_tgt / ".claude" / "settings.local.json"))

        assert scaffold_allow == refresh_allow, (
            f"Scaffold vs refresh drift: only in scaffold {scaffold_allow - refresh_allow}; "
            f"only in refresh {refresh_allow - scaffold_allow}"
        )

    def test_scaffold_and_refresh_write_same_mcp_json(
        self, scaffold_target, refresh_target
    ):
        scaffold, scaffold_tgt = scaffold_target
        refresh, refresh_tgt = refresh_target

        scaffold()
        refresh()

        scaffold_entry = _mcp_distillate_entry(scaffold_tgt / ".mcp.json")
        refresh_entry = _mcp_distillate_entry(refresh_tgt / ".mcp.json")
        assert scaffold_entry == refresh_entry


class TestAllowlistCompletenessMatchesServer:
    """The allowlist MUST cover every tool the experimentalist needs."""

    def test_every_expected_tool_is_allowlisted(self, scaffold_target):
        scaffold, target = scaffold_target
        scaffold()

        allow = set(_allowlist(target / ".claude" / "settings.local.json"))
        mcp_allow = {
            a[len("mcp__distillate__"):]
            for a in allow
            if a.startswith("mcp__distillate__")
        }

        missing = EXPECTED_EXPERIMENTALIST_TOOLS - mcp_allow
        assert not missing, (
            f"Expected experimentalist tools NOT in allowlist -- these "
            f"would trigger permission prompts mid-run: {sorted(missing)}"
        )

    def test_no_forbidden_tool_is_allowlisted(self, scaffold_target):
        """The allowlist must not contain any forbidden tools."""
        scaffold, target = scaffold_target
        scaffold()

        allow = set(_allowlist(target / ".claude" / "settings.local.json"))
        mcp_allow = {
            a[len("mcp__distillate__"):]
            for a in allow
            if a.startswith("mcp__distillate__")
        }

        leaked = FORBIDDEN_EXPERIMENTALIST_TOOLS & mcp_allow
        assert not leaked, (
            f"Allowlist contains forbidden tools: {sorted(leaked)}"
        )
