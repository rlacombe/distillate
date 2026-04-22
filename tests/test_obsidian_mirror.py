# Covers: distillate/obsidian.py
"""Tests for the Obsidian vault integration.

Phase 1: curated digest (firehose → filtered vault, managed markers).
Phase 2: wikilinks (paper titles, project cross-references).
Phase 3: vault wiki (schema.md, index.md, lint).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Reload ``distillate.lab_notebook`` with KB + vault rooted in *tmp_path*.

    Sets ``DISTILLATE_KNOWLEDGE_DIR`` before import so ``NOTEBOOK_ROOT``
    picks up the temp location. Points ``config.OBSIDIAN_VAULT_PATH`` at
    a sibling temp directory and enables ``OBSIDIAN_PAPERS_FOLDER``.
    """
    kb_dir = tmp_path / "kb"
    vault_dir = tmp_path / "vault"
    kb_dir.mkdir()
    vault_dir.mkdir()
    monkeypatch.setenv("DISTILLATE_KNOWLEDGE_DIR", str(kb_dir))

    import distillate.lab_notebook as mod
    mod = importlib.reload(mod)

    from distillate import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(vault_dir))
    monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_NAME", "testvault")

    yield mod, vault_dir, kb_dir


def _vault_file(vault_dir: Path, date: str) -> Path:
    return vault_dir / "Distillate" / "Lab Notebook" / f"{date}.md"


# ---------------------------------------------------------------------------
# R1 — no vault configured means zero writes outside the KB
# ---------------------------------------------------------------------------

def test_no_vault_no_writes_outside_kb(tmp_path, monkeypatch):
    kb_dir = tmp_path / "kb"
    vault_dir = tmp_path / "vault"
    kb_dir.mkdir()
    vault_dir.mkdir()
    monkeypatch.setenv("DISTILLATE_KNOWLEDGE_DIR", str(kb_dir))

    import distillate.lab_notebook as mod
    mod = importlib.reload(mod)

    from distillate import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")

    result = mod.append_entry("hello world", entry_type="note")
    assert result["success"] is True

    # KB file exists
    assert Path(result["path"]).exists()
    # Vault dir is untouched — no Distillate subfolder was created
    assert not (vault_dir / "Distillate").exists()


# ---------------------------------------------------------------------------
# R2 — vault-worthy entries create exactly one vault file per day
# ---------------------------------------------------------------------------

def test_note_entry_writes_vault_file(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry("Brainstormed approach for X", entry_type="note")
    out = _vault_file(vault_dir, result["date"])
    assert out.exists()
    content = out.read_text()
    assert "# Lab Notebook" in content
    assert "<!-- distillate:managed -->" in content
    assert "<!-- /distillate:managed -->" in content
    assert "## Notes" in content
    assert "Brainstormed approach for X" in content


def test_paper_entry_writes_vault_file(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry(
        'Paper processed: "Attention is All You Need", engagement 85%, 1200 words highlighted',
        entry_type="paper",
    )
    content = _vault_file(vault_dir, result["date"]).read_text()
    assert "## Papers" in content
    assert "Attention is All You Need" in content


# ---------------------------------------------------------------------------
# R3 — run_completed triggers zero vault writes
# ---------------------------------------------------------------------------

def test_run_completed_entry_no_vault_write(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry(
        "Run completed: loss=0.42, accuracy=0.87",
        entry_type="run_completed",
        project="myproj",
    )
    out = _vault_file(vault_dir, result["date"])
    assert not out.exists()


# ---------------------------------------------------------------------------
# R4 — noise-pattern entries trigger zero vault writes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entry,entry_type",
    [
        ("Agent 'nicolas' session started", "session"),
        ("Agent 'nicolas' session stopped", "session"),
        ("Experiment session ended (user)", "session"),
        ("Experiment launched on 'myproj' (claude-sonnet-4-5-20250929)", "experiment"),
        ("Experiment stopped on 'myproj' (2 session(s))", "experiment"),
        ('Paper processed: "Title"', "paper"),
        ('Paper processed: "T1"', "paper"),
        ('Paper processed: "Tracked Paper"', "paper"),
        ('Paper processed: "Some Real Paper"', "paper"),  # no metadata → noise
    ],
)
def test_noise_pattern_entries_skipped(lab, entry, entry_type):
    mod, vault_dir, _ = lab
    result = mod.append_entry(entry, entry_type=entry_type)
    out = _vault_file(vault_dir, result["date"])
    assert not out.exists(), f"noise entry leaked to vault: {entry!r}"


# ---------------------------------------------------------------------------
# R5 — sections render in the declared order
# ---------------------------------------------------------------------------

def test_section_order(lab):
    mod, vault_dir, _ = lab
    mod.append_entry("Finished the session summary — bullets go here", entry_type="session", project="myproj")
    mod.append_entry(
        'Paper processed: "Scaling Laws", engagement 70%, 800 words highlighted',
        entry_type="paper",
    )
    mod.append_entry("Key insight about transformers", entry_type="note")
    result = mod.append_entry(
        "Milestone: completed training run 14 with best accuracy 0.91",
        entry_type="experiment",
        project="myproj",
    )

    content = _vault_file(vault_dir, result["date"]).read_text()

    i_notes = content.index("## Notes")
    i_papers = content.index("## Papers")
    i_exp = content.index("## Experiments")
    i_sess = content.index("## Sessions")
    assert i_notes < i_papers < i_exp < i_sess


# ---------------------------------------------------------------------------
# R6 — user content outside markers is preserved byte-for-byte
# ---------------------------------------------------------------------------

def test_user_content_outside_markers_preserved(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry("First thought", entry_type="note")
    out = _vault_file(vault_dir, result["date"])
    existing = out.read_text()

    # User hand-edits the file, adding content *below* the managed block
    user_tail = "\n## My own notes\n\nSome hand-written text with details.\n"
    out.write_text(existing + user_tail, encoding="utf-8", newline="\n")

    # Another note triggers regeneration
    mod.append_entry("Second thought", entry_type="note")
    after = out.read_text()
    assert "## My own notes" in after
    assert "Some hand-written text with details." in after
    assert "First thought" in after
    assert "Second thought" in after


def test_user_content_above_markers_preserved(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry("First thought", entry_type="note")
    out = _vault_file(vault_dir, result["date"])
    existing = out.read_text()

    # User prepends content above everything (before the header)
    user_head = "## Prefixed by me\n\nHeader content I typed.\n\n"
    out.write_text(user_head + existing, encoding="utf-8", newline="\n")

    mod.append_entry("Second thought", entry_type="note")
    after = out.read_text()
    assert "## Prefixed by me" in after
    assert "Header content I typed." in after
    assert "Second thought" in after


# ---------------------------------------------------------------------------
# R7 — first entry of a new day creates a clean file
# ---------------------------------------------------------------------------

def test_first_entry_of_day_creates_clean_file(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry("Hello world", entry_type="note")
    content = _vault_file(vault_dir, result["date"]).read_text()

    assert content.startswith("# Lab Notebook — ")
    assert content.count("<!-- distillate:managed -->") == 1
    assert content.count("<!-- /distillate:managed -->") == 1
    assert "## Notes" in content
    assert "Hello world" in content


# ---------------------------------------------------------------------------
# Shared filter — is_noise_entry / is_noise_text still work for feed code
# ---------------------------------------------------------------------------

def test_shared_noise_filter_exports(lab):
    mod, _, _ = lab
    assert mod.is_noise_text("Agent 'foo' session started") is True
    assert mod.is_noise_text("Key insight about X") is False
    assert mod.is_noise_entry({"text": "Experiment session ended"}) is True
    assert mod.is_noise_entry({"text": "Finished reading the paper"}) is False


# ---------------------------------------------------------------------------
# Legacy vault files without markers get replaced with a clean digest
# ---------------------------------------------------------------------------

def test_legacy_file_without_markers_replaced(lab):
    mod, vault_dir, _ = lab
    # Simulate a legacy auto-mirror file: header plus flat bullet stream,
    # no markers anywhere.
    date = "2025-01-15"
    day_dir = vault_dir / "Distillate" / "Lab Notebook"
    day_dir.mkdir(parents=True, exist_ok=True)
    legacy = day_dir / f"{date}.md"
    legacy.write_text(
        "# Lab Notebook — Wednesday, January 15, 2025\n\n"
        "- **08:01** — [session] Agent 'nicolas' session started #foo\n"
        "- **08:02** — [run_completed] Run completed: loss=0.5 #foo\n"
        "- **08:03** — [session] Agent 'nicolas' session stopped #foo\n",
        encoding="utf-8",
    )

    # Write a single high-signal KB entry for the same date, then regenerate
    from datetime import datetime, timezone
    dt = datetime(2025, 1, 15, 9, 30, tzinfo=timezone.utc)
    mod.append_entry("Something meaningful", entry_type="note", when=dt)

    after = legacy.read_text()
    # Clean managed file — no trace of the legacy bullet stream
    assert "<!-- distillate:managed -->" in after
    assert "Something meaningful" in after
    assert "Run completed" not in after
    assert "session started" not in after


# ---------------------------------------------------------------------------
# Cleanup — migrate all legacy vault notebook files in bulk
# ---------------------------------------------------------------------------

def test_cleanup_legacy_with_kb_source(lab):
    """Legacy file with a KB source → regenerated with managed markers."""
    mod, vault_dir, _ = lab
    from datetime import datetime, timezone

    # Write a KB entry for Jan 20
    dt = datetime(2025, 1, 20, 10, 0, tzinfo=timezone.utc)
    mod.append_entry("Important note", entry_type="note", when=dt)

    # Simulate a legacy vault file for the same date (no markers)
    nb_dir = vault_dir / "Distillate" / "Lab Notebook"
    legacy = nb_dir / "2025-01-20.md"
    legacy.write_text(
        "# Lab Notebook\n\n- **08:00** — [session] junk\n",
        encoding="utf-8",
    )

    result = mod.cleanup_legacy_notebook()
    assert result["cleaned"] >= 1

    after = legacy.read_text()
    assert "<!-- distillate:managed -->" in after
    assert "Important note" in after


def test_cleanup_deletes_orphans(lab):
    """Legacy file with no KB source → deleted."""
    mod, vault_dir, _ = lab

    nb_dir = vault_dir / "Distillate" / "Lab Notebook"
    nb_dir.mkdir(parents=True, exist_ok=True)
    orphan = nb_dir / "2020-01-01.md"
    orphan.write_text("# Lab Notebook\n\n- **08:00** — [session] old noise\n")

    result = mod.cleanup_legacy_notebook()
    assert result["deleted"] >= 1
    assert not orphan.exists()


def test_cleanup_skips_managed_files(lab):
    """Already-managed file → skipped (not touched)."""
    mod, vault_dir, _ = lab

    nb_dir = vault_dir / "Distillate" / "Lab Notebook"
    nb_dir.mkdir(parents=True, exist_ok=True)
    managed = nb_dir / "2025-03-01.md"
    managed.write_text(
        "# Lab Notebook\n\n<!-- distillate:managed -->\n## Notes\n<!-- /distillate:managed -->\n",
    )

    result = mod.cleanup_legacy_notebook()
    assert result["skipped"] >= 1


# ===========================================================================
# Phase 2 — Wikilinks
# ===========================================================================

def test_paper_title_wikified(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry(
        'Paper processed: "Scaling Laws for Neural Language Models", engagement 70%, 800 words highlighted',
        entry_type="paper",
    )
    content = _vault_file(vault_dir, result["date"]).read_text()
    assert "[[Scaling Laws for Neural Language Models]]" in content


def test_project_tag_becomes_wikilink(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry(
        "Finished the session with good results",
        entry_type="session",
        project="my-cool-project",
    )
    content = _vault_file(vault_dir, result["date"]).read_text()
    assert "[[Projects/my-cool-project]]" in content


def test_note_without_project_has_no_wikilink(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry("Just a thought", entry_type="note")
    content = _vault_file(vault_dir, result["date"]).read_text()
    assert "[[Projects/" not in content


def test_paper_without_title_pattern_not_wikified(lab):
    mod, vault_dir, _ = lab
    result = mod.append_entry(
        'Paper processed: "Attention is All You Need", engagement 85%, 1200 words highlighted',
        entry_type="paper",
    )
    content = _vault_file(vault_dir, result["date"]).read_text()
    # The title IS wikified
    assert "[[Attention is All You Need]]" in content
    # But only one occurrence of [[
    assert content.count("[[Attention") == 1

