# Covers: distillate/zotero_client.py (get_recent_papers, get_pdf_attachment, WebDAV fallback)
#         distillate/main.py (_upload_paper — PDF download paths)
"""Tests for zotero_client paper fetching and PDF attachment resolution."""

from unittest.mock import MagicMock

import pytest


# -- Fixtures --

@pytest.fixture(autouse=True)
def isolate_config(monkeypatch):
    """Set required config values for tests."""
    monkeypatch.setenv("ZOTERO_API_KEY", "test_key")
    monkeypatch.setenv("ZOTERO_USER_ID", "12345")
    monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "test_key")
    monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
    monkeypatch.setattr("distillate.config.ZOTERO_TAG_INBOX", "inbox")
    monkeypatch.setattr("distillate.config.ZOTERO_TAG_READ", "read")
    monkeypatch.setattr("distillate.config.ZOTERO_COLLECTION_KEY", "")
    monkeypatch.setattr("distillate.config.RM_FOLDER_INBOX", "Distillate/Inbox")
    monkeypatch.setattr("distillate.config.RM_FOLDER_PAPERS", "Distillate")
    monkeypatch.setattr("distillate.config.KEEP_ZOTERO_PDF", True)
    monkeypatch.setattr("distillate.config.HTTP_TIMEOUT", 10)
    # Default flipped to zotero — test_import covers the legacy rM path.
    monkeypatch.setattr("distillate.config.READING_SOURCE", "remarkable")


def _make_paper(key, title, doi="", item_type="journalArticle", tags=None):
    """Build a minimal Zotero item dict for testing."""
    tag_list = [{"tag": t} for t in (tags or [])]
    return {
        "key": key,
        "version": 1,
        "data": {
            "key": key,
            "itemType": item_type,
            "title": title,
            "DOI": doi,
            "creators": [{"creatorType": "author", "lastName": "Smith"}],
            "tags": tag_list,
            "url": "",
            "abstractNote": "",
            "date": "2026",
            "publicationTitle": "Test Journal",
        },
    }


# -- Tests for get_recent_papers --

class TestGetRecentPapers:
    def test_returns_filtered_papers(self, monkeypatch):
        from distillate import zotero_client

        papers = [
            _make_paper("A1", "Paper A"),
            _make_paper("A2", "Note B", item_type="note"),
            _make_paper("A3", "Paper C"),
            _make_paper("A4", "Tagged D", tags=["inbox"]),
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = papers
        mock_resp.status_code = 200
        mock_resp.headers = {"Last-Modified-Version": "10"}
        mock_resp.raise_for_status = MagicMock()

        monkeypatch.setattr(
            "distillate.zotero_client._request_with_retry",
            lambda *a, **kw: mock_resp,
        )

        result = zotero_client.get_recent_papers(limit=100)

        # Should keep A1 and A3, filter out note (A2) and tagged (A4)
        keys = [r["key"] for r in result]
        assert "A1" in keys
        assert "A3" in keys
        assert "A2" not in keys
        assert "A4" not in keys

    def test_empty_library(self, monkeypatch):
        from distillate import zotero_client

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.status_code = 200
        mock_resp.headers = {"Last-Modified-Version": "5"}
        mock_resp.raise_for_status = MagicMock()

        monkeypatch.setattr(
            "distillate.zotero_client._request_with_retry",
            lambda *a, **kw: mock_resp,
        )

        result = zotero_client.get_recent_papers()
        assert result == []


# -- Tests for WebDAV PDF downloads --

class TestWebDAVAttachment:
    """Regression: get_pdf_attachment must find WebDAV (linked_url) attachments."""

    def test_linked_url_attachment_found(self):
        """WebDAV attachments have linkMode='linked_url' and must be matched."""
        from unittest.mock import MagicMock
        from distillate import zotero_client

        child = {
            "key": "WDAV1",
            "data": {
                "itemType": "attachment",
                "contentType": "application/pdf",
                "linkMode": "linked_url",
                "md5": "abc123",
            },
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = [child]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "distillate.zotero_client._get",
                lambda path, **kw: mock_resp,
            )
            result = zotero_client.get_pdf_attachment("ITEM1")

        assert result is not None
        assert result["key"] == "WDAV1"

    def test_imported_file_still_found(self):
        """Standard Zotero cloud attachments (imported_file) still matched."""
        from unittest.mock import MagicMock
        from distillate import zotero_client

        child = {
            "key": "CLOUD1",
            "data": {
                "itemType": "attachment",
                "contentType": "application/pdf",
                "linkMode": "imported_file",
                "md5": "def456",
            },
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = [child]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "distillate.zotero_client._get",
                lambda path, **kw: mock_resp,
            )
            result = zotero_client.get_pdf_attachment("ITEM2")

        assert result is not None
        assert result["key"] == "CLOUD1"

    def test_linked_file_is_matched(self):
        """linked_file attachments are intentionally included — the sync
        pipeline creates them when KEEP_ZOTERO_PDF is false."""
        from unittest.mock import MagicMock
        from distillate import zotero_client

        child = {
            "key": "LOCAL1",
            "data": {
                "itemType": "attachment",
                "contentType": "application/pdf",
                "linkMode": "linked_file",
            },
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = [child]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "distillate.zotero_client._get",
                lambda path, **kw: mock_resp,
            )
            result = zotero_client.get_pdf_attachment("ITEM3")

        assert result is not None
        assert result["key"] == "LOCAL1"

    def test_webdav_fallback_in_upload(self, monkeypatch):
        """When Zotero cloud returns 404, WebDAV should be tried."""
        import requests as _req
        from distillate.main import _upload_paper
        from distillate.state import State

        state = State()
        paper = _make_paper("WD1", "WebDAV Paper")

        # Simulate a WebDAV attachment (linked_url)
        attachment = {"key": "WDATT1", "data": {"md5": "abc"}}
        monkeypatch.setattr(
            "distillate.zotero_client.get_pdf_attachment",
            lambda k: attachment,
        )

        # Zotero cloud returns 404
        http_err = _req.exceptions.HTTPError(response=MagicMock(status_code=404))
        monkeypatch.setattr(
            "distillate.zotero_client.download_pdf",
            MagicMock(side_effect=http_err),
        )

        # WebDAV returns PDF bytes
        monkeypatch.setattr(
            "distillate.zotero_client.download_pdf_from_webdav",
            lambda k: b"webdav-pdf-bytes",
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.upload_pdf_bytes",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.sanitize_filename",
            lambda n: n,
        )
        monkeypatch.setattr(
            "distillate.obsidian.save_inbox_pdf",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.add_tag",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "distillate.semantic_scholar.lookup_paper",
            lambda **kw: None,
        )

        result = _upload_paper(paper, state, existing_on_rm=set())
        assert result is True
        doc = state.get_document("WD1")
        assert doc["status"] == "on_remarkable"  # not awaiting_pdf
