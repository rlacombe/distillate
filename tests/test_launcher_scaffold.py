# Covers: distillate/launcher.py — template management and experiment scaffolding

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Template management
# ---------------------------------------------------------------------------

class TestTemplatesDir:
    def test_returns_config_subdir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        from distillate.launcher import templates_dir
        result = templates_dir()
        assert result == tmp_path / "templates"
        assert result.is_dir()

    def test_creates_dir_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        from distillate.launcher import templates_dir
        d = templates_dir()
        assert d.exists()


class TestListTemplates:
    def test_empty_when_no_templates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        from distillate.launcher import list_templates
        assert list_templates() == []

    def test_discovers_templates(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl_dir = tmp_path / "templates" / "my-exp"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "PROMPT.md").write_text("line1\nline2\nline3\n")
        (tmpl_dir / "data").mkdir()

        from distillate.launcher import list_templates
        templates = list_templates()
        assert len(templates) == 1
        assert templates[0]["name"] == "my-exp"
        assert templates[0]["has_data"] is True
        assert templates[0]["prompt_lines"] == 3

    def test_skips_hidden_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl_dir = tmp_path / "templates"
        tmpl_dir.mkdir()
        (tmpl_dir / ".hidden").mkdir()
        (tmpl_dir / "visible").mkdir()
        (tmpl_dir / "visible" / "PROMPT.md").write_text("hello\n")

        from distillate.launcher import list_templates
        templates = list_templates()
        assert len(templates) == 1
        assert templates[0]["name"] == "visible"

    def test_no_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl_dir = tmp_path / "templates" / "simple"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "PROMPT.md").write_text("just a prompt\n")

        from distillate.launcher import list_templates
        templates = list_templates()
        assert templates[0]["has_data"] is False


class TestImportTemplate:
    def test_imports_prompt_and_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        source = tmp_path / "src-experiment"
        source.mkdir()
        (source / "PROMPT.md").write_text("do the thing\n")
        (source / "data").mkdir()
        (source / "data" / "train.csv").write_text("a,b,c\n")
        (source / "evaluate.py").write_text("print('eval')\n")
        (source / "random.txt").write_text("ignored\n")

        from distillate.launcher import import_template
        name = import_template(source)
        assert name == "src-experiment"

        dest = tmp_path / "templates" / "src-experiment"
        assert (dest / "PROMPT.md").exists()
        assert (dest / "data" / "train.csv").exists()
        assert (dest / "evaluate.py").exists()
        assert not (dest / "random.txt").exists()

    def test_custom_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        source = tmp_path / "my_dir"
        source.mkdir()
        (source / "PROMPT.md").write_text("prompt\n")

        from distillate.launcher import import_template
        name = import_template(source, name="Custom Name")
        assert name == "custom-name"
        assert (tmp_path / "templates" / "custom-name" / "PROMPT.md").exists()

    def test_overwrites_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        dest = tmp_path / "templates" / "existing"
        dest.mkdir(parents=True)
        (dest / "old_file.txt").write_text("old\n")

        source = tmp_path / "new_src"
        source.mkdir()
        (source / "PROMPT.md").write_text("new prompt\n")

        from distillate.launcher import import_template
        import_template(source, name="existing")

        assert (dest / "PROMPT.md").read_text() == "new prompt\n"
        assert not (dest / "old_file.txt").exists()

    def test_source_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        from distillate.launcher import import_template
        with pytest.raises(FileNotFoundError):
            import_template(tmp_path / "nonexistent")


class TestScaffoldExperiment:
    def test_scaffold_creates_structure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)

        # Create template
        tmpl = tmp_path / "templates" / "test-tmpl"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("test prompt\n")
        (tmpl / "evaluate.py").write_text("print('eval')\n")

        target = tmp_path / "output" / "my-exp"

        from distillate.launcher import scaffold_experiment
        result = scaffold_experiment("test-tmpl", target)

        assert result == target
        assert (target / "PROMPT.md").read_text() == "test prompt\n"
        assert (target / "evaluate.py").exists()
        assert (target / ".distillate").is_dir()
        assert (target / ".claude").is_dir()
        assert (target / ".claude" / "settings.local.json").exists()

        # Check settings.local.json has permissions
        local_cfg = json.loads((target / ".claude" / "settings.local.json").read_text())
        assert "permissions" in local_cfg
        assert "allow" in local_cfg["permissions"]

    def test_scaffold_template_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        (tmp_path / "templates").mkdir()

        from distillate.launcher import scaffold_experiment
        with pytest.raises(FileNotFoundError, match="Template not found"):
            scaffold_experiment("nonexistent", tmp_path / "out")

    def test_scaffold_target_not_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "t"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("p\n")

        target = tmp_path / "notempty"
        target.mkdir()
        (target / "file.txt").write_text("stuff\n")

        from distillate.launcher import scaffold_experiment
        with pytest.raises(FileExistsError, match="not empty"):
            scaffold_experiment("t", target)

    def test_scaffold_git_init(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "g"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "git-test"

        from distillate.launcher import scaffold_experiment
        scaffold_experiment("g", target)

        assert (target / ".git").exists()

    def test_scaffold_installs_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "h"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "hook-test"

        from distillate.launcher import scaffold_experiment
        scaffold_experiment("h", target)

        settings = target / ".claude" / "settings.json"
        if settings.exists():
            cfg = json.loads(settings.read_text())
            assert "hooks" in cfg

    def test_scaffold_creates_mcp_json(self, tmp_path, monkeypatch):
        """scaffold_experiment creates a .mcp.json file."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "mcp-test"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "mcp-exp"
        from distillate.launcher import scaffold_experiment
        scaffold_experiment("mcp-test", target)

        mcp_json = target / ".mcp.json"
        assert mcp_json.exists()
        cfg = json.loads(mcp_json.read_text())
        assert "mcpServers" in cfg

    def test_scaffold_mcp_json_has_distillate_server(self, tmp_path, monkeypatch):
        """The .mcp.json file references the distillate MCP server."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "mcp-srv"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "mcp-srv-exp"
        from distillate.launcher import scaffold_experiment
        scaffold_experiment("mcp-srv", target)

        cfg = json.loads((target / ".mcp.json").read_text())
        assert "distillate" in cfg["mcpServers"]
        server_cfg = cfg["mcpServers"]["distillate"]
        assert "command" in server_cfg
        assert server_cfg["args"] == ["-m", "distillate.mcp_server"]

    def test_scaffold_settings_local_has_mcp_permissions(self, tmp_path, monkeypatch):
        """settings.local.json includes MCP tool permissions (mcp__distillate__*)."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "perm-test"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "perm-exp"
        from distillate.launcher import scaffold_experiment
        scaffold_experiment("perm-test", target)

        local_cfg = json.loads((target / ".claude" / "settings.local.json").read_text())
        allow_list = local_cfg["permissions"]["allow"]
        assert "mcp__distillate__start_run" in allow_list
        assert "mcp__distillate__conclude_run" in allow_list
        assert "mcp__distillate__save_enrichment" in allow_list
        assert "mcp__distillate__scan_project" in allow_list
        assert "mcp__distillate__annotate_run" in allow_list

    # -- Modal compute scaffold -------------------------------------------
    # Contract: scaffold_experiment accepts optional compute params. When
    # compute="modal", the Modal config is written into the shared
    # .distillate/budget.json alongside the train budget. When compute is
    # left as "local" (default), no modal block appears — so experiments
    # that opt out never see the extra surface.

    def test_scaffold_defaults_to_no_modal_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "local-default"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "local-exp"
        from distillate.launcher import scaffold_experiment
        scaffold_experiment("local-default", target)

        budget_file = target / ".distillate" / "budget.json"
        if budget_file.exists():
            data = json.loads(budget_file.read_text())
            assert "modal" not in data

    def test_scaffold_with_modal_writes_budget_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        tmpl = tmp_path / "templates" / "modal-on"
        tmpl.mkdir(parents=True)
        (tmpl / "PROMPT.md").write_text("prompt\n")

        target = tmp_path / "modal-exp"
        from distillate.launcher import scaffold_experiment
        scaffold_experiment(
            "modal-on", target,
            compute="modal", modal_gpu="A100-80GB", modal_budget_usd=25.0,
        )

        from distillate.budget import read_modal_config
        cfg = read_modal_config(cwd=target)
        assert cfg is not None
        assert cfg["gpu"] == "A100-80GB"
        assert cfg["budget_usd"] == 25.0
