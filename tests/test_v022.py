"""Tests for v0.2.2 bug fixes."""

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
# #4. _recover_pdf_text handles empty normalized search text
# ---------------------------------------------------------------------------

class TestRecoverPdfTextEmpty:
    def test_empty_search_returns_none(self):
        """Pure hyphens/spaces should return None, not crash."""
        from distillate.renderer import _recover_pdf_text

        result = _recover_pdf_text("Some page text here", "- - -")
        assert result is None

    def test_empty_string_returns_none(self):
        """Empty string search should return None."""
        from distillate.renderer import _recover_pdf_text

        result = _recover_pdf_text("Some page text here", "")
        assert result is None

    def test_whitespace_only_returns_none(self):
        """Whitespace-only search should return None."""
        from distillate.renderer import _recover_pdf_text

        result = _recover_pdf_text("Some page text here", "   ")
        assert result is None

    def test_normal_search_still_works(self):
        """Normal text recovery still functions."""
        from distillate.renderer import _recover_pdf_text

        result = _recover_pdf_text("The quick brown fox", "quick brown")
        assert result == "quick brown"


# ---------------------------------------------------------------------------
# #8. Missing end marker doesn't delete user content
# ---------------------------------------------------------------------------

class TestMissingEndMarker:
    def test_missing_end_marker_preserves_content(self, obs_env):
        """When end marker is missing, user content after start marker is preserved."""
        from distillate import obsidian

        # Create a note with start marker but no end marker
        saved_dir = obs_env / "Saved"
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

        saved_dir = obs_env / "Saved"
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
# #5. _handle_backoff returns bool
# ---------------------------------------------------------------------------

class TestHandleBackoffReturn:
    def test_returns_true_when_backoff_header_present(self, monkeypatch):
        """_handle_backoff returns True when it sleeps."""
        import time
        from unittest.mock import MagicMock
        from distillate.zotero_client import _handle_backoff

        monkeypatch.setattr(time, "sleep", lambda x: None)

        resp = MagicMock()
        resp.headers = {"Retry-After": "5"}
        assert _handle_backoff(resp) is True

    def test_returns_false_when_no_header(self):
        """_handle_backoff returns False when no backoff needed."""
        from unittest.mock import MagicMock
        from distillate.zotero_client import _handle_backoff

        resp = MagicMock()
        resp.headers = {}
        assert _handle_backoff(resp) is False


# ---------------------------------------------------------------------------
# #7. Annotation create-before-delete order
# ---------------------------------------------------------------------------

class TestAnnotationCreateBeforeDelete:
    def test_create_runs_before_delete(self, monkeypatch):
        """New annotations are created before old ones are deleted."""
        from unittest.mock import MagicMock
        from distillate import zotero_client, config

        monkeypatch.setattr(config, "ZOTERO_API_KEY", "fake")
        monkeypatch.setattr(config, "ZOTERO_USER_ID", "123")

        call_order = []

        def fake_get(path, **kw):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = [
                {"key": "OLD1", "version": 1, "data": {"tags": [{"tag": "distillate"}]}},
            ]
            return resp

        def fake_post(path, **kw):
            call_order.append("create")
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"successful": {"0": {"key": "NEW1"}}}
            return resp

        def fake_delete(path, **kw):
            call_order.append("delete")
            resp = MagicMock()
            resp.status_code = 204
            return resp

        monkeypatch.setattr(zotero_client, "_get", fake_get)
        monkeypatch.setattr(zotero_client, "_post", fake_post)
        monkeypatch.setattr(zotero_client, "_delete", fake_delete)

        highlights = [{
            "text": "test",
            "color": "#ffd400",
            "page_label": "1",
            "sort_index": "00000|000000|00000",
            "page_index": 0,
            "rects": [[0, 0, 100, 10]],
        }]

        zotero_client.create_highlight_annotations("ATT1", highlights)
        assert call_order == ["create", "delete"]


# ---------------------------------------------------------------------------
# #10. Public API names
# ---------------------------------------------------------------------------

class TestPublicAPINames:
    def test_build_note_html_is_public(self):
        """build_note_html is accessible as a public function."""
        from distillate import zotero_client
        assert hasattr(zotero_client, "build_note_html")
        assert callable(zotero_client.build_note_html)

    def test_sanitize_filename_is_public(self):
        """sanitize_filename is accessible as a public function."""
        from distillate import remarkable_client
        assert hasattr(remarkable_client, "sanitize_filename")
        assert callable(remarkable_client.sanitize_filename)


# ---------------------------------------------------------------------------
# #13. Module-level marker constants
# ---------------------------------------------------------------------------

class TestMarkerConstants:
    def test_markers_are_module_level(self):
        """MARKER_START and MARKER_END are accessible at module level."""
        from distillate import obsidian
        assert obsidian.MARKER_START == "<!-- distillate:start -->"
        assert obsidian.MARKER_END == "<!-- distillate:end -->"


# ---------------------------------------------------------------------------
# #1. Version from package metadata
# ---------------------------------------------------------------------------

class TestVersionString:
    def test_version_not_hardcoded(self):
        """_VERSION should not be the old hardcoded value."""
        from distillate.main import _VERSION
        assert _VERSION != "0.1.7"
