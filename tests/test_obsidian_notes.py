# Covers: distillate/vault_wiki.py

"""Tests for Obsidian vault wiki — schema, index, and lint (Phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    """Set up a vault directory for vault_wiki tests."""
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "Distillate").mkdir()

    from distillate import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(vault_dir))
    monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_NAME", "testvault")
    monkeypatch.setattr(config, "PDF_SUBFOLDER", "pdf")

    import distillate.vault_wiki as mod
    return mod, vault_dir


# -- Schema -----------------------------------------------------------------

def test_schema_created(wiki):
    mod, vault_dir = wiki
    path = mod.generate_schema()
    assert path is not None
    content = path.read_text()
    assert "# Distillate Vault Schema" in content
    assert "distillate_schema" in content
    assert "Papers/Notes" in content


def test_schema_not_overwritten_when_current(wiki):
    mod, vault_dir = wiki
    mod.generate_schema()
    schema = vault_dir / "Distillate" / "_meta" / "schema.md"
    # Append user content
    schema.write_text(schema.read_text() + "\n## My custom section\n")
    mod.generate_schema()
    content = schema.read_text()
    # User content preserved because version hasn't changed
    assert "## My custom section" in content


def test_schema_no_vault_returns_none(tmp_path, monkeypatch):
    from distillate import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
    import distillate.vault_wiki as mod
    assert mod.generate_schema() is None


# -- Index ------------------------------------------------------------------

def _make_paper_note(saved_dir: Path, citekey: str, title: str, date_read: str = ""):
    """Create a minimal paper note with frontmatter."""
    saved_dir.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f'title: "{title}"\n'
        f"citekey: {citekey}\n"
        f"date_read: {date_read}\n"
        "engagement: 75\n"
        "---\n"
        f"# {title}\n"
    )
    (saved_dir / f"{citekey}.md").write_text(content, encoding="utf-8")


def test_index_lists_papers(wiki):
    mod, vault_dir = wiki
    saved = vault_dir / "Distillate" / "Papers" / "Notes"
    _make_paper_note(saved, "smith2024", "Smith et al. 2024", "2024-03-15")
    _make_paper_note(saved, "jones2023", "Jones et al. 2023", "2023-11-01")

    path = mod.regenerate_index()
    assert path is not None
    content = path.read_text()
    assert "## Papers" in content
    assert "[[Papers/Notes/smith2024|Smith et al. 2024]]" in content
    assert "[[Papers/Notes/jones2023|Jones et al. 2023]]" in content


def test_index_lists_projects(wiki):
    mod, vault_dir = wiki
    experiments = vault_dir / "Distillate" / "Experiments"
    experiments.mkdir(parents=True, exist_ok=True)
    (experiments / "llm-benchmark.md").write_text("# Benchmark\n")

    path = mod.regenerate_index()
    content = path.read_text()
    assert "## Experiments (1)" in content
    assert "[[Experiments/llm-benchmark|llm-benchmark]]" in content


def test_index_lists_notebook_days(wiki):
    mod, vault_dir = wiki
    nb = vault_dir / "Distillate" / "Lab Notebook"
    nb.mkdir(parents=True, exist_ok=True)
    (nb / "2026-04-10.md").write_text("# Lab Notebook\n")
    (nb / "2026-04-11.md").write_text("# Lab Notebook\n")

    path = mod.regenerate_index()
    content = path.read_text()
    assert "## Lab Notebook" in content
    assert "[[Lab Notebook/2026-04-11|2026-04-11]]" in content


def test_index_preserves_user_content(wiki):
    mod, vault_dir = wiki
    index_path = vault_dir / "Distillate" / "index.md"
    # Pre-populate with managed block + user content
    index_path.write_text(
        "# Distillate Index\n\n"
        "<!-- distillate:index -->\n"
        "old content\n"
        "<!-- /distillate:index -->\n"
        "\n## My Custom Section\n\nUser notes here.\n"
    )

    saved = vault_dir / "Distillate" / "Papers" / "Notes"
    _make_paper_note(saved, "test2024", "Test Paper", "2024-01-01")
    mod.regenerate_index()

    content = index_path.read_text()
    assert "## My Custom Section" in content
    assert "User notes here." in content
    assert "[[Papers/Notes/test2024|Test Paper]]" in content
    assert "old content" not in content


def test_index_no_vault_returns_none(tmp_path, monkeypatch):
    from distillate import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
    import distillate.vault_wiki as mod
    assert mod.regenerate_index() is None


# -- Lint -------------------------------------------------------------------

def test_lint_detects_missing_schema_and_index(wiki):
    mod, vault_dir = wiki
    result = mod.vault_lint()
    assert result["configured"] is True
    issues = result["issues"]
    assert any("schema.md" in i for i in issues)
    assert any("index.md" in i for i in issues)


def test_lint_detects_orphan_pdfs(wiki):
    mod, vault_dir = wiki
    saved = vault_dir / "Distillate" / "Papers" / "Notes"
    saved.mkdir(parents=True, exist_ok=True)
    pdf_dir = saved / "pdf"
    pdf_dir.mkdir()
    (pdf_dir / "orphan2024.pdf").write_bytes(b"%PDF-1.4")
    # No matching orphan2024.md in Papers/Notes/

    result = mod.vault_lint()
    assert any("orphan2024.pdf" in i for i in result["issues"])


def test_lint_detects_missing_frontmatter(wiki):
    mod, vault_dir = wiki
    saved = vault_dir / "Distillate" / "Papers" / "Notes"
    saved.mkdir(parents=True, exist_ok=True)
    (saved / "bad-note.md").write_text("# No frontmatter here\n")

    result = mod.vault_lint()
    assert any("bad-note.md" in i for i in result["issues"])


def test_lint_clean_vault(wiki):
    mod, vault_dir = wiki
    mod.generate_schema()
    mod.regenerate_index()
    # Create the log file (current name is "Papers Log.md")
    d = vault_dir / "Distillate"
    (d / "Papers Log.md").write_text("# Papers Log\n")

    result = mod.vault_lint()
    # Only structural issues should be template staleness (if any)
    for issue in result["issues"]:
        assert "schema" not in issue.lower()
        assert "index" not in issue.lower()
        assert "Log" not in issue


def test_lint_not_configured(tmp_path, monkeypatch):
    from distillate import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
    import distillate.vault_wiki as mod
    result = mod.vault_lint()
    assert result["configured"] is False


# ---------------------------------------------------------------------------
# Migrated from test_obsidian_sync.py
# ---------------------------------------------------------------------------


class TestExtractYear:
    """_extract_year() should handle all Zotero date formats."""

    def test_iso_date(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("2024-10-15") == "2024"

    def test_day_month_year(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("8 September 2024") == "2024"

    def test_month_year(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("10/2024") == "2024"

    def test_year_only(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("2024") == "2024"

    def test_empty(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("") == ""

    def test_none(self):
        from distillate.obsidian import _extract_year
        assert _extract_year(None) == ""

    def test_no_year(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("in press") == ""
