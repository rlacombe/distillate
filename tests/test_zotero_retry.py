"""Tests for Zotero API retry logic and item type filtering."""

from unittest.mock import MagicMock, patch

import pytest
import requests


class TestRetryLogic:
    """Tests for _request_with_retry() in zotero_client."""

    @patch("distillate.zotero_client.requests.request")
    @patch("distillate.zotero_client.time.sleep")
    def test_success_on_first_try(self, mock_sleep, mock_request):
        from distillate.zotero_client import _request_with_retry

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.raise_for_status = MagicMock()
        mock_request.return_value = mock_resp

        result = _request_with_retry("GET", "https://example.com")
        assert result == mock_resp
        mock_sleep.assert_not_called()

    @patch("distillate.zotero_client.requests.request")
    @patch("distillate.zotero_client.time.sleep")
    def test_retries_on_500(self, mock_sleep, mock_request):
        from distillate.zotero_client import _request_with_retry

        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.headers = {}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.headers = {}
        ok_resp.raise_for_status = MagicMock()

        mock_request.side_effect = [fail_resp, ok_resp]
        result = _request_with_retry("GET", "https://example.com")
        assert result == ok_resp
        assert mock_sleep.call_count == 1

    @patch("distillate.zotero_client.requests.request")
    @patch("distillate.zotero_client.time.sleep")
    def test_retries_on_429(self, mock_sleep, mock_request):
        from distillate.zotero_client import _request_with_retry

        fail_resp = MagicMock()
        fail_resp.status_code = 429
        fail_resp.headers = {}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.headers = {}
        ok_resp.raise_for_status = MagicMock()

        mock_request.side_effect = [fail_resp, ok_resp]
        result = _request_with_retry("GET", "https://example.com")
        assert result == ok_resp

    @patch("distillate.zotero_client.requests.request")
    @patch("distillate.zotero_client.time.sleep")
    def test_retries_on_connection_error(self, mock_sleep, mock_request):
        from distillate.zotero_client import _request_with_retry

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.headers = {}
        ok_resp.raise_for_status = MagicMock()

        mock_request.side_effect = [
            requests.exceptions.ConnectionError("fail"),
            ok_resp,
        ]
        result = _request_with_retry("GET", "https://example.com")
        assert result == ok_resp

    @patch("distillate.zotero_client.requests.request")
    @patch("distillate.zotero_client.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep, mock_request):
        from distillate.zotero_client import _request_with_retry

        mock_request.side_effect = requests.exceptions.ConnectionError("fail")

        with pytest.raises(requests.exceptions.ConnectionError):
            _request_with_retry("GET", "https://example.com")
        # 3 retries + 1 initial = 4 calls
        assert mock_request.call_count == 4

    @patch("distillate.zotero_client.requests.request")
    @patch("distillate.zotero_client.time.sleep")
    def test_no_retry_on_4xx(self, mock_sleep, mock_request):
        from distillate.zotero_client import _request_with_retry

        fail_resp = MagicMock()
        fail_resp.status_code = 404
        fail_resp.headers = {}
        fail_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=fail_resp
        )

        mock_request.return_value = fail_resp

        with pytest.raises(requests.exceptions.HTTPError):
            _request_with_retry("GET", "https://example.com")
        # Should NOT retry on 404
        assert mock_request.call_count == 1
        mock_sleep.assert_not_called()

    @patch("distillate.zotero_client.requests.request")
    @patch("distillate.zotero_client.time.sleep")
    def test_exponential_backoff_delays(self, mock_sleep, mock_request):
        from distillate.zotero_client import _request_with_retry

        fail_resp = MagicMock()
        fail_resp.status_code = 503
        fail_resp.headers = {}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.headers = {}
        ok_resp.raise_for_status = MagicMock()

        mock_request.side_effect = [fail_resp, fail_resp, ok_resp]
        _request_with_retry("GET", "https://example.com")

        # Delays: 2*2^0=2, 2*2^1=4
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0][0][0] == 2
        assert mock_sleep.call_args_list[1][0][0] == 4

    @patch("distillate.zotero_client.requests.request")
    @patch("distillate.zotero_client.time.sleep")
    def test_handles_backoff_header(self, mock_sleep, mock_request):
        from distillate.zotero_client import _request_with_retry

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Backoff": "5"}
        resp.raise_for_status = MagicMock()

        mock_request.return_value = resp
        _request_with_retry("GET", "https://example.com")

        # Should sleep for the backoff period
        mock_sleep.assert_called_once_with(5)


class TestItemTypeFiltering:
    """Tests for filter_new_papers() item type skipping."""

    def _make_item(self, item_type, title="Test", tags=None):
        return {
            "data": {
                "itemType": item_type,
                "title": title,
                "tags": [{"tag": t} for t in (tags or [])],
            }
        }

    def test_keeps_journal_articles(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("journalArticle")]
        assert len(filter_new_papers(items)) == 1

    def test_keeps_conference_papers(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("conferencePaper")]
        assert len(filter_new_papers(items)) == 1

    def test_keeps_preprints(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("preprint")]
        assert len(filter_new_papers(items)) == 1

    def test_keeps_thesis(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("thesis")]
        assert len(filter_new_papers(items)) == 1

    def test_skips_book(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("book")]
        assert len(filter_new_papers(items)) == 0

    def test_skips_webpage(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("webpage")]
        assert len(filter_new_papers(items)) == 0

    def test_skips_attachment(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("attachment")]
        assert len(filter_new_papers(items)) == 0

    def test_skips_note(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("note")]
        assert len(filter_new_papers(items)) == 0

    def test_skips_patent(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("patent")]
        assert len(filter_new_papers(items)) == 0

    def test_skips_blog_post(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("blogPost")]
        assert len(filter_new_papers(items)) == 0

    def test_skips_items_with_workflow_tags(self):
        from distillate.zotero_client import filter_new_papers

        items = [self._make_item("journalArticle", tags=["inbox"])]
        assert len(filter_new_papers(items)) == 0

    def test_mixed_types(self):
        from distillate.zotero_client import filter_new_papers

        items = [
            self._make_item("journalArticle", "Good Paper"),
            self._make_item("book", "A Book"),
            self._make_item("conferencePaper", "Conference Paper"),
            self._make_item("webpage", "A Web Page"),
        ]
        result = filter_new_papers(items)
        assert len(result) == 2
        titles = [i["data"]["title"] for i in result]
        assert "Good Paper" in titles
        assert "Conference Paper" in titles
