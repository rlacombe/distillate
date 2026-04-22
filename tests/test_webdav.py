# Covers: distillate/zotero_client.py
"""Tests for Zotero WebDAV PDF download and collection filtering.

Migrated from test_v032.py. Placed here because adding to test_zotero_reader.py
would exceed the 500-line limit.
"""

import io
import zipfile
from unittest.mock import MagicMock, patch

import requests


# ---------------------------------------------------------------------------
# WebDAV PDF download fallback
# ---------------------------------------------------------------------------


class TestDownloadPdfFromWebdav:
    """download_pdf_from_webdav() should fetch PDFs from WebDAV storage."""

    def test_no_webdav_url_returns_none(self, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "")
        from distillate.zotero_client import download_pdf_from_webdav

        assert download_pdf_from_webdav("ABC123") is None

    @patch("distillate.zotero_client.requests.get")
    def test_downloads_pdf_from_zip(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("paper.pdf", b"%PDF-1.4 fake content")
        zip_bytes = buf.getvalue()

        mock_resp = MagicMock(status_code=200, content=zip_bytes)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = download_pdf_from_webdav("XYZ789")
        assert result == b"%PDF-1.4 fake content"

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert "zotero/XYZ789.zip" in call_kwargs[0][0] or "zotero/XYZ789.zip" in str(call_kwargs)
        assert call_kwargs[1]["auth"] == ("user", "pass")

    @patch("distillate.zotero_client.requests.get")
    def test_404_returns_none(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        mock_resp = MagicMock(status_code=404)
        mock_get.return_value = mock_resp

        assert download_pdf_from_webdav("MISSING") is None

    @patch("distillate.zotero_client.requests.get")
    def test_bad_zip_returns_none(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        mock_resp = MagicMock(status_code=200, content=b"not a zip")
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        assert download_pdf_from_webdav("BADZIP") is None

    @patch("distillate.zotero_client.requests.get")
    def test_zip_without_pdf_returns_none(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("notes.txt", b"just notes")
        zip_bytes = buf.getvalue()

        mock_resp = MagicMock(status_code=200, content=zip_bytes)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        assert download_pdf_from_webdav("NOPDF") is None

    @patch("distillate.zotero_client.requests.get")
    def test_connection_error_returns_none(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        mock_get.side_effect = requests.exceptions.ConnectionError("refused")

        assert download_pdf_from_webdav("OFFLINE") is None

    @patch("distillate.zotero_client.requests.get")
    def test_http_error_returns_none(self, mock_get, monkeypatch):
        """HTTPError (401, 403, 500 etc.) should be caught, not propagated."""
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        resp = MagicMock(status_code=401)
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "401 Unauthorized", response=resp,
        )
        mock_get.return_value = resp

        assert download_pdf_from_webdav("AUTHFAIL") is None

    @patch("distillate.zotero_client.requests.get")
    def test_html_error_page_returns_none(self, mock_get, monkeypatch):
        """Server returning HTML instead of zip should be handled gracefully."""
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        mock_resp = MagicMock(status_code=200, content=b"<html>Error</html>")
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        assert download_pdf_from_webdav("HTMLERR") is None

    @patch("distillate.zotero_client.requests.get")
    def test_no_auth_when_username_empty(self, mock_get, monkeypatch):
        """When ZOTERO_WEBDAV_USERNAME is empty, auth should be None."""
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "")
        from distillate.zotero_client import download_pdf_from_webdav

        mock_resp = MagicMock(status_code=404)
        mock_get.return_value = mock_resp

        download_pdf_from_webdav("NOAUTH")
        _, kwargs = mock_get.call_args
        assert kwargs.get("auth") is None


# ---------------------------------------------------------------------------
# Zotero collection filtering
# ---------------------------------------------------------------------------


class TestCollectionFiltering:
    """Collection-scoped paper discovery."""

    @patch("distillate.zotero_client._get")
    def test_get_changed_keys_with_collection(self, mock_get):
        """When collection_key is set, should hit /collections/{key}/items/top."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"KEY1": 5}
        mock_resp.headers = {"Last-Modified-Version": "10"}
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_changed_item_keys
        keys, version = get_changed_item_keys(1, collection_key="ABCD1234")

        assert keys == {"KEY1": 5}
        assert version == 10
        call_path = mock_get.call_args[0][0]
        assert "/collections/ABCD1234/items/top" in call_path

    @patch("distillate.zotero_client._get")
    def test_get_changed_keys_without_collection(self, mock_get):
        """Without collection_key, should hit /items/top (default)."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.headers = {"Last-Modified-Version": "5"}
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_changed_item_keys
        get_changed_item_keys(1)

        call_path = mock_get.call_args[0][0]
        assert "/items/top" in call_path
        assert "/collections/" not in call_path

    @patch("distillate.zotero_client._get")
    def test_get_recent_papers_with_collection(self, mock_get, monkeypatch):
        """get_recent_papers with collection_key should scope the query."""
        monkeypatch.setattr("distillate.config.ZOTERO_TAG_INBOX", "inbox")
        monkeypatch.setattr("distillate.config.ZOTERO_TAG_READ", "read")

        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"key": "A", "data": {"itemType": "journalArticle", "title": "P1", "tags": []}},
        ]
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_recent_papers
        papers = get_recent_papers(limit=10, collection_key="COLL1234")

        assert len(papers) == 1
        call_path = mock_get.call_args[0][0]
        assert "/collections/COLL1234/items/top" in call_path

    @patch("distillate.zotero_client._get")
    def test_list_collections(self, mock_get):
        """list_collections should return collection data."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"key": "ABC", "data": {"name": "To Read"}},
            {"key": "DEF", "data": {"name": "ML Papers"}},
        ]
        mock_get.return_value = mock_resp

        from distillate.zotero_client import list_collections
        colls = list_collections()

        assert len(colls) == 2
        assert colls[0]["data"]["name"] == "To Read"
