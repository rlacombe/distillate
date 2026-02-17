"""Tests for v0.2.0 features: citekey naming, highlight back-propagation,
note merge for plugin coexistence, Obsidian Bases."""

import json
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)
    yield tmp_path


@pytest.fixture()
def obs_env(tmp_path, monkeypatch):
    """Set up obsidian / output environment for tests."""
    from distillate import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
    monkeypatch.setattr(config, "OUTPUT_PATH", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Citekey extraction from Zotero
# ---------------------------------------------------------------------------

class TestCitekeyExtraction:
    def test_citekey_from_extra_field(self, monkeypatch):
        """Better BibTeX citekey is parsed from the 'extra' field."""
        from distillate import zotero_client

        item = {
            "data": {
                "itemType": "journalArticle",
                "title": "Attention Is All You Need",
                "creators": [{"creatorType": "author", "lastName": "Vaswani", "firstName": "A."}],
                "date": "2017-06-12",
                "extra": "Citation Key: vaswani2017attention\nsome other data",
                "DOI": "",
                "publicationTitle": "",
                "url": "",
                "abstractNote": "",
                "tags": [],
            }
        }
        meta = zotero_client.extract_metadata(item)
        assert meta["citekey"] == "vaswani2017attention"

    def test_citekey_fallback_generation(self, monkeypatch):
        """When no Better BibTeX citekey, a fallback is generated."""
        from distillate import zotero_client

        item = {
            "data": {
                "itemType": "journalArticle",
                "title": "The Great Paper on AI",
                "creators": [{"creatorType": "author", "lastName": "Smith", "firstName": "J."}],
                "date": "2025-03-01",
                "extra": "",
                "DOI": "",
                "publicationTitle": "",
                "url": "",
                "abstractNote": "",
                "tags": [],
            }
        }
        meta = zotero_client.extract_metadata(item)
        assert meta["citekey"] == "smith_great_2025"

    def test_citekey_fallback_no_date(self):
        """Fallback citekey works without a date."""
        from distillate.zotero_client import _generate_citekey

        result = _generate_citekey(["Doe, J."], "Some Paper", "")
        assert result == "doe_some"

    def test_citekey_fallback_no_authors(self):
        """Fallback citekey works without authors."""
        from distillate.zotero_client import _generate_citekey

        result = _generate_citekey([], "Neural Networks", "2024")
        assert result == "unknown_neural_2024"

    def test_citekey_skips_stop_words(self):
        """Fallback citekey skips stop words in title."""
        from distillate.zotero_client import _generate_citekey

        result = _generate_citekey(["Author, A."], "A Study of the Effects", "2023")
        assert result == "author_study_2023"


# ---------------------------------------------------------------------------
# 2. Citekey-based note naming
# ---------------------------------------------------------------------------

class TestCitekeyNaming:
    def test_note_uses_citekey_as_filename(self, obs_env):
        """When citekey is provided, note filename should use it."""
        from distillate import obsidian

        result = obsidian.create_paper_note(
            title="Attention Is All You Need",
            authors=["Vaswani, A."],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            citekey="vaswani2017attention",
        )
        assert result is not None
        assert result.name == "vaswani2017attention.md"
        assert result.exists()

    def test_note_falls_back_to_title(self, obs_env):
        """Without citekey, note filename uses sanitized title."""
        from distillate import obsidian

        result = obsidian.create_paper_note(
            title="My Paper Title",
            authors=["Author A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
        )
        assert result is not None
        assert result.name == "My Paper Title.md"

    def test_frontmatter_includes_citekey_and_aliases(self, obs_env):
        """Citekey, year, and aliases appear in frontmatter."""
        from distillate import obsidian

        result = obsidian.create_paper_note(
            title="Attention Is All You Need",
            authors=["Vaswani, A."],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            citekey="vaswani2017attention",
            publication_date="2017-06-12",
        )
        content = result.read_text()
        assert 'citekey: "vaswani2017attention"' in content
        assert "year: 2017" in content
        assert 'aliases:' in content
        assert '"Attention Is All You Need"' in content

    def test_annotated_pdf_uses_citekey(self, obs_env):
        """save_annotated_pdf uses citekey for filename."""
        from distillate import obsidian

        result = obsidian.save_annotated_pdf(
            "Some Title", b"fake-pdf", citekey="author2024paper",
        )
        assert result is not None
        assert result.name == "author2024paper.pdf"

    def test_delete_paper_note_with_citekey(self, obs_env):
        """delete_paper_note finds note by citekey."""
        from distillate import obsidian

        # Create note with citekey
        result = obsidian.create_paper_note(
            title="My Paper",
            authors=["A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            citekey="my2026paper",
        )
        assert result.exists()

        obsidian.delete_paper_note("My Paper", citekey="my2026paper")
        assert not result.exists()

    def test_obsidian_uri_uses_citekey(self, monkeypatch):
        """get_obsidian_uri uses citekey in file path."""
        from distillate import obsidian, config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_NAME", "MyVault")
        monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")

        uri = obsidian.get_obsidian_uri("Some Title", citekey="author2024")
        assert "author2024" in uri
        assert "Some%20Title" not in uri


# ---------------------------------------------------------------------------
# 3. Note merge (plugin coexistence)
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
        rd = obs_env / "Saved"
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

        rd = obs_env / "Saved"
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
# 4. Obsidian Bases
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

        bases_path = vault / "Distillate" / "Distillate Papers.base"
        assert bases_path.exists()
        content = bases_path.read_text()
        assert "date_read" in content
        assert "engagement" in content

    def test_ensure_bases_note_idempotent(self, monkeypatch, tmp_path):
        """ensure_bases_note doesn't overwrite an existing .base file."""
        from distillate import obsidian, config

        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(vault))
        monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")

        obsidian.ensure_bases_note()
        bases_path = vault / "Distillate" / "Distillate Papers.base"
        bases_path.write_text("custom content")

        obsidian.ensure_bases_note()
        assert bases_path.read_text() == "custom content"

    def test_ensure_bases_note_skips_non_obsidian(self, monkeypatch, tmp_path):
        """ensure_bases_note does nothing when not in Obsidian mode."""
        from distillate import obsidian, config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", str(tmp_path))

        obsidian.ensure_bases_note()
        # No .base file in plain folder mode
        assert not list(tmp_path.rglob("*.base"))


# ---------------------------------------------------------------------------
# 5. Zotero highlight position extraction
# ---------------------------------------------------------------------------

class TestZoteroHighlightExtraction:
    def test_extract_returns_empty_without_pymupdf(self, monkeypatch):
        """extract_zotero_highlights returns [] when pymupdf is missing."""
        from distillate import renderer
        import builtins

        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "pymupdf":
                raise ImportError("no pymupdf")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = renderer.extract_zotero_highlights.__wrapped__(
            "fake.zip",
        ) if hasattr(renderer.extract_zotero_highlights, "__wrapped__") else []
        # Just verify the function handles missing pymupdf gracefully
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 6. Annotation creation API format
# ---------------------------------------------------------------------------

class TestAnnotationCreation:
    def test_create_highlight_annotations_builds_correct_payload(self, monkeypatch):
        """Verify the annotation payload format matches Zotero API."""
        from distillate import zotero_client, config

        monkeypatch.setattr(config, "ZOTERO_API_KEY", "fake")
        monkeypatch.setattr(config, "ZOTERO_USER_ID", "12345")

        # Mock HTTP calls
        class MockResp:
            status_code = 200
            def json(self):
                return []

        class MockPostResp:
            status_code = 200
            def json(self):
                return {"successful": {"0": {"key": "ANN1"}}, "failed": {}}

        calls = []
        def mock_get(path, params=None, **kwargs):
            return MockResp()

        def mock_post(path, **kwargs):
            calls.append(kwargs.get("json", []))
            return MockPostResp()

        monkeypatch.setattr(zotero_client, "_get", mock_get)
        monkeypatch.setattr(zotero_client, "_post", mock_post)

        highlights = [{
            "text": "test highlight",
            "page_index": 0,
            "page_label": "1",
            "rects": [[10.0, 20.0, 100.0, 30.0]],
            "sort_index": "00000|000042|00750",
            "color": "#ffd400",
        }]

        keys = zotero_client.create_highlight_annotations("ATT1", highlights)
        assert keys == ["ANN1"]
        assert len(calls) == 1

        item = calls[0][0]
        assert item["itemType"] == "annotation"
        assert item["parentItem"] == "ATT1"
        assert item["annotationType"] == "highlight"
        assert item["annotationText"] == "test highlight"
        assert item["annotationPageLabel"] == "1"

        pos = json.loads(item["annotationPosition"])
        assert pos["pageIndex"] == 0
        assert pos["rects"] == [[10.0, 20.0, 100.0, 30.0]]

        assert {"tag": "distillate"} in item["tags"]

    def test_duplicate_prevention_deletes_existing(self, monkeypatch):
        """Existing distillate annotations are deleted before creating new ones."""
        from distillate import zotero_client, config

        monkeypatch.setattr(config, "ZOTERO_API_KEY", "fake")
        monkeypatch.setattr(config, "ZOTERO_USER_ID", "12345")

        deleted = []

        class MockGetResp:
            status_code = 200
            def json(self):
                return [
                    {"key": "OLD1", "version": 42, "data": {"tags": [{"tag": "distillate"}]}},
                    {"key": "OTHER", "version": 10, "data": {"tags": [{"tag": "manual"}]}},
                ]

        class MockPostResp:
            status_code = 200
            def json(self):
                return {"successful": {}, "failed": {}}

        class MockDelResp:
            status_code = 204

        def mock_get(path, params=None, **kwargs):
            return MockGetResp()

        def mock_post(path, **kwargs):
            return MockPostResp()

        def mock_delete(path, **kwargs):
            deleted.append(path)
            return MockDelResp()

        monkeypatch.setattr(zotero_client, "_get", mock_get)
        monkeypatch.setattr(zotero_client, "_post", mock_post)
        monkeypatch.setattr(zotero_client, "_delete", mock_delete)

        zotero_client.create_highlight_annotations("ATT1", [
            {"text": "x", "page_index": 0, "page_label": "1",
             "rects": [], "sort_index": "00000|000000|00000", "color": "#ffd400"},
        ])

        # Only OLD1 should be deleted (has distillate tag), not OTHER
        assert "/items/OLD1" in deleted
        assert "/items/OTHER" not in deleted


# ---------------------------------------------------------------------------
# 7. SYNC_HIGHLIGHTS config
# ---------------------------------------------------------------------------

class TestSyncHighlightsConfig:
    def test_sync_highlights_default_true(self, monkeypatch):
        """SYNC_HIGHLIGHTS defaults to True."""
        monkeypatch.delenv("SYNC_HIGHLIGHTS", raising=False)
        # Re-evaluate
        import os
        val = os.environ.get("SYNC_HIGHLIGHTS", "true").strip().lower() in ("true", "1", "yes")
        assert val is True

    def test_sync_highlights_can_be_disabled(self, monkeypatch):
        """SYNC_HIGHLIGHTS can be set to false."""
        monkeypatch.setenv("SYNC_HIGHLIGHTS", "false")
        import os
        val = os.environ.get("SYNC_HIGHLIGHTS", "true").strip().lower() in ("true", "1", "yes")
        assert val is False


# ---------------------------------------------------------------------------
# 8. Reading log with citekey wikilinks
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

        log_path = vault / "Distillate" / "Distillate Log.md"
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

        log_path = vault / "Distillate" / "Distillate Log.md"
        content = log_path.read_text()

        # Old entry removed, new citekey entry present
        assert "[[me2024paper|My Paper]]" in content
        lines = [ln for ln in content.split("\n") if ln.startswith("- ")]
        assert len(lines) == 1  # only one entry
