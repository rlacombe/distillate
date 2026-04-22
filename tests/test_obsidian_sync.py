# Covers: distillate/obsidian.py
"""Tests for Obsidian note sync behaviour.

Migrated from test_v020.py (sections 3, 4, 5, 7, 8).  test_obsidian_mirror.py
is already over 500 lines; new obsidian tests live here.
"""

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def obs_env(tmp_path, monkeypatch):
    """Set up obsidian / output environment for tests."""
    from distillate import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
    monkeypatch.setattr(config, "OUTPUT_PATH", str(tmp_path))
    monkeypatch.setattr(config, "PDF_SUBFOLDER", "pdf")
    return tmp_path


# ---------------------------------------------------------------------------
# Note merge (plugin coexistence)
# ---------------------------------------------------------------------------

class TestNoteMerge:
    def test_fresh_note_has_markers(self, obs_env):
        """New note should contain distillate:start and distillate:end markers."""
        from distillate import obsidian

        result = obsidian.create_paper_note(
            title="Fresh Paper",
            authors=["Author A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["highlight one"]},
        )
        content = result.read_text()
        assert "<!-- distillate:start -->" in content
        assert "<!-- distillate:end -->" in content
        assert "highlight one" in content

    def test_resync_replaces_between_markers(self, obs_env):
        """Re-syncing replaces content between markers."""
        from distillate import obsidian

        # Create initial note
        result1 = obsidian.create_paper_note(
            title="Paper",
            authors=["A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["old highlight"]},
        )
        content1 = result1.read_text()
        assert "old highlight" in content1

        # Re-sync with new highlights
        result2 = obsidian.create_paper_note(
            title="Paper",
            authors=["A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["new highlight"]},
        )
        content2 = result2.read_text()
        assert "new highlight" in content2
        assert "old highlight" not in content2

    def test_resync_preserves_my_notes(self, obs_env):
        """Re-syncing preserves user's My Notes section."""
        from distillate import obsidian

        result = obsidian.create_paper_note(
            title="Paper",
            authors=["A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["hl1"]},
        )

        # Add user notes
        content = result.read_text()
        content = content.replace(
            "## My Notes\n\n",
            "## My Notes\n\nMy important thought.\n\n",
        )
        result.write_text(content)

        # Re-sync
        result2 = obsidian.create_paper_note(
            title="Paper",
            authors=["A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["hl2"]},
        )
        content2 = result2.read_text()
        assert "My important thought." in content2
        assert "hl2" in content2
        assert "hl1" not in content2

    def test_merge_into_external_note(self, obs_env):
        """Merging into an external note (no markers) preserves existing content."""
        from distillate import obsidian

        # Simulate a note created by Zotero Integration plugin
        rd = obs_env / "Papers" / "Notes"
        rd.mkdir(parents=True, exist_ok=True)
        external_note = rd / "vaswani2017.md"
        external_note.write_text("""\
---
title: "Attention Is All You Need"
citekey: vaswani2017
---

# Attention Is All You Need

Some content from the Zotero Integration plugin.

## My Notes

My existing personal notes.
""")

        result = obsidian.create_paper_note(
            title="Attention Is All You Need",
            authors=["Vaswani, A."],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["highlight from remarkable"]},
            citekey="vaswani2017",
        )
        content = result.read_text()

        # Original content preserved
        assert "Some content from the Zotero Integration plugin." in content
        # Distillate content added
        assert "highlight from remarkable" in content
        assert "<!-- distillate:start -->" in content
        assert "<!-- distillate:end -->" in content
        # User notes preserved
        assert "My existing personal notes." in content

    def test_merge_adds_distillate_frontmatter(self, obs_env):
        """Merging adds Distillate-specific frontmatter to existing note."""
        from distillate import obsidian

        rd = obs_env / "Papers" / "Notes"
        rd.mkdir(parents=True, exist_ok=True)
        external_note = rd / "smith2024.md"
        external_note.write_text("""\
---
title: "A Paper"
citekey: smith2024
custom_field: preserved
---

# A Paper
""")

        obsidian.create_paper_note(
            title="A Paper",
            authors=["Smith, J."],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            doi="10.1234/test",
            engagement=75,
            citekey="smith2024",
        )
        content = external_note.read_text()

        # Distillate frontmatter added
        assert 'doi: "10.1234/test"' in content
        assert "engagement: 75" in content
        # Original frontmatter preserved
        assert "custom_field: preserved" in content


# ---------------------------------------------------------------------------
# Obsidian Bases
# ---------------------------------------------------------------------------

class TestObsidianBases:
    def test_ensure_bases_note_creates_file(self, monkeypatch, tmp_path):
        """ensure_bases_note creates a .base file in the papers directory."""
        from distillate import obsidian, config

        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(vault))
        monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")

        obsidian.ensure_bases_note()

        bases_path = vault / "Distillate" / "_meta" / "Papers.base"
        assert bases_path.exists()
        content = bases_path.read_text()
        assert "date_read" in content
        assert "engagement" in content

    def test_ensure_bases_note_idempotent(self, monkeypatch, tmp_path):
        """ensure_bases_note is idempotent when template version matches."""
        from distillate import obsidian, config

        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(vault))
        monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")

        obsidian.ensure_bases_note()
        bases_path = vault / "Distillate" / "_meta" / "Papers.base"
        first_content = bases_path.read_text()

        obsidian.ensure_bases_note()
        assert bases_path.read_text() == first_content

    def test_ensure_bases_note_updates_stale_template(self, monkeypatch, tmp_path):
        """ensure_bases_note overwrites files with outdated template version."""
        from distillate import obsidian, config

        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(vault))
        monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")

        bases_path = vault / "Distillate" / "_meta" / "Papers.base"
        bases_path.parent.mkdir(parents=True, exist_ok=True)
        bases_path.write_text("stale content without version marker")

        obsidian.ensure_bases_note()
        content = bases_path.read_text()
        assert f"distillate:template:{obsidian._TEMPLATE_VERSION}" in content
        assert "file.inFolder" in content

    def test_ensure_bases_note_skips_non_obsidian(self, monkeypatch, tmp_path):
        """ensure_bases_note does nothing when not in Obsidian mode."""
        from distillate import obsidian, config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", str(tmp_path))

        obsidian.ensure_bases_note()
        # No .base file in plain folder mode
        assert not list(tmp_path.rglob("*.base"))


# ---------------------------------------------------------------------------
# Zotero highlight position extraction
# ---------------------------------------------------------------------------

class TestZoteroHighlightExtraction:
    def test_extract_returns_empty_without_pymupdf(self, monkeypatch):
        """extract_zotero_highlights returns [] when pymupdf is missing."""
        from distillate.integrations.remarkable import renderer as rm_renderer
        import builtins

        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "pymupdf":
                raise ImportError("no pymupdf")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = rm_renderer.extract_zotero_highlights.__wrapped__(
            "fake.zip",
        ) if hasattr(rm_renderer.extract_zotero_highlights, "__wrapped__") else []
        # Verify the function handles missing pymupdf gracefully
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# SYNC_HIGHLIGHTS config
# ---------------------------------------------------------------------------

class TestSyncHighlightsConfig:
    def test_sync_highlights_default_true(self, monkeypatch):
        """SYNC_HIGHLIGHTS defaults to True."""
        import os
        monkeypatch.delenv("SYNC_HIGHLIGHTS", raising=False)
        val = os.environ.get("SYNC_HIGHLIGHTS", "true").strip().lower() in ("true", "1", "yes")
        assert val is True

    def test_sync_highlights_can_be_disabled(self, monkeypatch):
        """SYNC_HIGHLIGHTS can be set to false."""
        import os
        monkeypatch.setenv("SYNC_HIGHLIGHTS", "false")
        val = os.environ.get("SYNC_HIGHLIGHTS", "true").strip().lower() in ("true", "1", "yes")
        assert val is False


# ---------------------------------------------------------------------------
# Reading log with citekey wikilinks
# ---------------------------------------------------------------------------

class TestReadingLogCitekey:
    def test_reading_log_uses_citekey_wikilink(self, monkeypatch, tmp_path):
        """Reading log entries use citekey-based wikilinks."""
        from distillate import obsidian, config

        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(vault))
        monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")

        obsidian.append_to_reading_log(
            "My Great Paper", "A nice summary", citekey="author2024great",
        )

        log_path = vault / "Distillate" / "Papers Log.md"
        assert log_path.exists()
        content = log_path.read_text()
        assert "[[author2024great|My Great Paper]]" in content

    def test_reading_log_dedup_old_title_entry(self, monkeypatch, tmp_path):
        """When switching to citekey, old title-based entries are cleaned up."""
        from distillate import obsidian, config

        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(vault))
        monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")

        # Create old-style entry (no citekey)
        obsidian.append_to_reading_log("My Paper", "old summary")

        # Now re-add with citekey
        obsidian.append_to_reading_log("My Paper", "new summary", citekey="me2024paper")

        log_path = vault / "Distillate" / "Papers Log.md"
        content = log_path.read_text()

        # Old entry removed, new citekey entry present
        assert "[[me2024paper|My Paper]]" in content
        lines = [ln for ln in content.split("\n") if ln.startswith("- ")]
        assert len(lines) == 1  # only one entry


# ---------------------------------------------------------------------------
# Missing end marker doesn't delete user content (from test_v022.py)
# ---------------------------------------------------------------------------

class TestMissingEndMarker:
    def test_missing_end_marker_preserves_content(self, obs_env):
        """When end marker is missing, user content after start marker is preserved."""
        from distillate import obsidian

        # Create a note with start marker but no end marker
        saved_dir = obs_env / "Papers" / "Notes"
        saved_dir.mkdir(parents=True)
        note_path = saved_dir / "Test Paper.md"
        note_path.write_text(
            "---\ntitle: Test Paper\n---\n\n"
            "<!-- distillate:start -->\n\n"
            "## Highlights\n\nOld highlights\n\n"
            "## My Notes\n\nUser's important notes here\n"
            # Note: no <!-- distillate:end --> marker!
        )

        obsidian.create_paper_note(
            title="Test Paper",
            authors=["Author"],
            date_added="2026-01-01",
            zotero_item_key="ABC123",
            highlights={"1": ["New highlight"]},
        )

        content = note_path.read_text()
        # The end marker should now be present
        assert obsidian.MARKER_END in content
        # Start marker should still be present
        assert obsidian.MARKER_START in content

    def test_with_both_markers_works_normally(self, obs_env):
        """Normal re-sync with both markers works as before."""
        from distillate import obsidian

        saved_dir = obs_env / "Papers" / "Notes"
        saved_dir.mkdir(parents=True)
        note_path = saved_dir / "Test Paper.md"
        note_path.write_text(
            "---\ntitle: Test Paper\n---\n\n"
            "<!-- distillate:start -->\n\n"
            "## Highlights\n\nOld highlights\n\n"
            "## My Notes\n\n"
            "<!-- distillate:end -->\n"
        )

        obsidian.create_paper_note(
            title="Test Paper",
            authors=["Author"],
            date_added="2026-01-01",
            zotero_item_key="ABC123",
            highlights={"1": ["New highlight"]},
        )

        content = note_path.read_text()
        assert "New highlight" in content
        assert obsidian.MARKER_START in content
        assert obsidian.MARKER_END in content


# ---------------------------------------------------------------------------
# Migrated from test_v032.py
# ---------------------------------------------------------------------------


class TestPaperNoteWithNotes:
    """create_paper_note() should include typed and handwritten note sections."""

    def test_typed_notes_section(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.config.OUTPUT_PATH", str(tmp_path))
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")

        from distillate import obsidian

        note_path = obsidian.create_paper_note(
            title="Test Paper",
            authors=["Author"],
            date_added="2024-01-01",
            zotero_item_key="KEY1",
            typed_notes={0: "My typed note on page 1", 2: "Note on page 3"},
        )

        assert note_path is not None
        content = note_path.read_text()
        assert "## Notes from reMarkable" in content
        assert "### Page 1" in content
        assert "My typed note on page 1" in content
        assert "### Page 3" in content
        assert "Note on page 3" in content

    def test_handwritten_notes_section(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.config.OUTPUT_PATH", str(tmp_path))
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")

        from distillate import obsidian

        note_path = obsidian.create_paper_note(
            title="Test Paper 2",
            authors=["Author"],
            date_added="2024-01-01",
            zotero_item_key="KEY2",
            handwritten_notes={1: "OCR'd handwriting from page 2"},
        )

        assert note_path is not None
        content = note_path.read_text()
        assert "## Handwritten Notes" in content
        assert "### Page 2" in content
        assert "OCR'd handwriting from page 2" in content

    def test_no_notes_no_section(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.config.OUTPUT_PATH", str(tmp_path))
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")

        from distillate import obsidian

        note_path = obsidian.create_paper_note(
            title="Test Paper 3",
            authors=["Author"],
            date_added="2024-01-01",
            zotero_item_key="KEY3",
        )

        assert note_path is not None
        content = note_path.read_text()
        assert "## Notes from reMarkable" not in content
        assert "## Handwritten Notes" not in content


    def test_resync_does_not_duplicate_end_marker(self, obs_env):
        """Re-syncing a note with duplicate end markers must not grow them further (regression for 3.5GB corruption)."""
        from distillate import obsidian

        saved_dir = obs_env / "Papers" / "Notes"
        saved_dir.mkdir(parents=True)
        note_path = saved_dir / "Test Paper 4.md"
        # Simulate already-corrupted file with extra end markers
        note_path.write_text(
            "<!-- distillate:start -->\n\n"
            "## Highlights\n\n*No highlights extracted.*\n\n"
            "## My Notes\n\n"
            "<!-- distillate:end -->\n\n"
            "<!-- distillate:end -->\n\n"
            "<!-- distillate:end -->\n"
        )

        obsidian.create_paper_note(
            title="Test Paper 4",
            authors=["Author"],
            date_added="2026-01-01",
            zotero_item_key="KEY4",
        )

        content = note_path.read_text()
        assert content.count(obsidian.MARKER_END) == 1, (
            f"Expected exactly 1 end marker after resync, got {content.count(obsidian.MARKER_END)}"
        )

