"""Tests for distillate.cloud_sync — cloud state push/pull."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def cloud_env(monkeypatch):
    """Set cloud credentials."""
    monkeypatch.setattr("distillate.config.DISTILLATE_AUTH_TOKEN", "test-token-123")
    monkeypatch.setattr("distillate.config.DISTILLATE_API_URL", "https://api.example.com")


# ---------------------------------------------------------------------------
# cloud_sync_available
# ---------------------------------------------------------------------------

class TestCloudSyncAvailable:
    def test_available_when_both_set(self, cloud_env):
        from distillate.cloud_sync import cloud_sync_available
        assert cloud_sync_available() is True

    def test_unavailable_when_no_token(self, monkeypatch):
        monkeypatch.setattr("distillate.config.DISTILLATE_AUTH_TOKEN", "")
        monkeypatch.setattr("distillate.config.DISTILLATE_API_URL", "https://api.example.com")
        from distillate.cloud_sync import cloud_sync_available
        assert cloud_sync_available() is False

    def test_unavailable_when_no_url(self, monkeypatch):
        monkeypatch.setattr("distillate.config.DISTILLATE_AUTH_TOKEN", "tok")
        monkeypatch.setattr("distillate.config.DISTILLATE_API_URL", "")
        from distillate.cloud_sync import cloud_sync_available
        assert cloud_sync_available() is False


# ---------------------------------------------------------------------------
# push_state
# ---------------------------------------------------------------------------

class TestPushState:
    def test_push_sends_documents_and_projects(self, cloud_env):
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        state.add_document("K1", "A1", "md5", "doc1", "Title", ["Auth"])

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"ok": True, "upserted": 1}

        with patch("distillate.cloud_sync.requests.put", return_value=mock_resp) as mock_put:
            result = push_state(state)

        assert result is True
        assert mock_put.call_count == 2  # documents + projects

        # Verify auth header on first call
        call_args = mock_put.call_args_list[0]
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer test-token-123"

        # Verify URL
        assert "/state/documents" in call_args.args[0]

    def test_push_url_construction(self, cloud_env):
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        mock_resp = MagicMock(ok=True)
        mock_resp.json.return_value = {"ok": True}

        with patch("distillate.cloud_sync.requests.put", return_value=mock_resp) as mock_put:
            push_state(state)

        urls = [c.args[0] for c in mock_put.call_args_list]
        assert urls[0] == "https://api.example.com/state/documents"
        assert urls[1] == "https://api.example.com/state/projects"

    def test_push_timeout_graceful(self, cloud_env):
        import requests as req_lib
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        with patch("distillate.cloud_sync.requests.put",
                    side_effect=req_lib.exceptions.Timeout):
            result = push_state(state)
        assert result is False

    def test_push_connection_error_graceful(self, cloud_env):
        import requests as req_lib
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        with patch("distillate.cloud_sync.requests.put",
                    side_effect=req_lib.exceptions.ConnectionError):
            result = push_state(state)
        assert result is False

    def test_push_server_error_graceful(self, cloud_env):
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        with patch("distillate.cloud_sync.requests.put", return_value=mock_resp):
            result = push_state(state)
        assert result is False

    def test_push_noop_without_credentials(self, monkeypatch):
        monkeypatch.setattr("distillate.config.DISTILLATE_AUTH_TOKEN", "")
        monkeypatch.setattr("distillate.config.DISTILLATE_API_URL", "")
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        with patch("distillate.cloud_sync.requests.put") as mock_put:
            result = push_state(state)
        assert result is False
        mock_put.assert_not_called()


# ---------------------------------------------------------------------------
# pull_state
# ---------------------------------------------------------------------------

class TestPullState:
    def test_pull_adds_new_documents(self, cloud_env):
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        state.add_document("K1", "A1", "md5", "doc1", "Local Paper", ["A"])

        remote_doc = {
            "zotero_item_key": "K2",
            "title": "Cloud Paper",
            "status": "on_remarkable",
            "authors": ["B"],
            "metadata": {},
            "uploaded_at": "2026-02-25T10:00:00+00:00",
            "processed_at": None,
        }

        doc_resp = MagicMock(ok=True)
        doc_resp.json.return_value = {
            "documents": {"K2": remote_doc},
            "sync_at": "2026-02-25T12:00:00+00:00",
        }
        proj_resp = MagicMock(ok=True)
        proj_resp.json.return_value = {
            "projects": {},
            "sync_at": "2026-02-25T12:00:00+00:00",
        }

        with patch("distillate.cloud_sync.requests.get", side_effect=[doc_resp, proj_resp]):
            result = pull_state(state)

        assert result is True
        assert state.has_document("K2")
        assert state.get_document("K2")["title"] == "Cloud Paper"
        assert state.last_cloud_sync_at == "2026-02-25T12:00:00+00:00"
        # K1 still exists
        assert state.has_document("K1")

    def test_pull_uses_since_param(self, cloud_env):
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        state.last_cloud_sync_at = "2026-02-20T00:00:00+00:00"

        empty_resp = MagicMock(ok=True)
        empty_resp.json.return_value = {"documents": {}, "sync_at": "2026-02-25T12:00:00+00:00"}
        proj_resp = MagicMock(ok=True)
        proj_resp.json.return_value = {"projects": {}, "sync_at": "2026-02-25T12:00:00+00:00"}

        with patch("distillate.cloud_sync.requests.get", side_effect=[empty_resp, proj_resp]) as mock_get:
            pull_state(state)

        # Verify ?since= was passed
        first_call = mock_get.call_args_list[0]
        assert first_call.kwargs["params"]["since"] == "2026-02-20T00:00:00+00:00"

    def test_pull_no_since_on_first_sync(self, cloud_env):
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        assert state.last_cloud_sync_at is None

        empty_resp = MagicMock(ok=True)
        empty_resp.json.return_value = {"documents": {}, "sync_at": "2026-02-25T12:00:00+00:00"}
        proj_resp = MagicMock(ok=True)
        proj_resp.json.return_value = {"projects": {}, "sync_at": "2026-02-25T12:00:00+00:00"}

        with patch("distillate.cloud_sync.requests.get", side_effect=[empty_resp, proj_resp]) as mock_get:
            pull_state(state)

        first_call = mock_get.call_args_list[0]
        assert first_call.kwargs["params"] == {}

    def test_pull_connection_error_graceful(self, cloud_env):
        import requests as req_lib
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        with patch("distillate.cloud_sync.requests.get",
                    side_effect=req_lib.exceptions.ConnectionError):
            result = pull_state(state)
        assert result is False
        assert state.documents == {}

    def test_pull_noop_without_credentials(self, monkeypatch):
        monkeypatch.setattr("distillate.config.DISTILLATE_AUTH_TOKEN", "")
        monkeypatch.setattr("distillate.config.DISTILLATE_API_URL", "")
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        with patch("distillate.cloud_sync.requests.get") as mock_get:
            result = pull_state(state)
        assert result is False
        mock_get.assert_not_called()

    def test_pull_adds_new_projects(self, cloud_env):
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()

        doc_resp = MagicMock(ok=True)
        doc_resp.json.return_value = {"documents": {}, "sync_at": "2026-02-25T12:00:00+00:00"}
        proj_resp = MagicMock(ok=True)
        proj_resp.json.return_value = {
            "projects": {
                "my-project": {
                    "id": "my-project",
                    "name": "My Project",
                    "path": "/tmp/ml",
                    "runs": {},
                },
            },
            "sync_at": "2026-02-25T12:00:00+00:00",
        }

        with patch("distillate.cloud_sync.requests.get", side_effect=[doc_resp, proj_resp]):
            pull_state(state)

        assert state.has_project("my-project")
        assert state.get_project("my-project")["name"] == "My Project"


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

class TestMergeDocument:
    def test_status_advances_forward(self):
        from distillate.cloud_sync import _merge_single_document

        local = {"status": "on_remarkable", "processed_at": None, "metadata": {}}
        remote = {
            "status": "processed",
            "processed_at": "2026-02-25T10:00:00+00:00",
            "summary": "Great paper",
            "metadata": {"doi": "10.1234/test"},
        }

        _merge_single_document(local, remote)

        assert local["status"] == "processed"
        assert local["processed_at"] == "2026-02-25T10:00:00+00:00"
        assert local["summary"] == "Great paper"
        assert local["metadata"]["doi"] == "10.1234/test"

    def test_status_does_not_regress(self):
        from distillate.cloud_sync import _merge_single_document

        local = {
            "status": "processed",
            "processed_at": "2026-02-20T00:00:00+00:00",
            "summary": "My summary",
            "metadata": {"doi": "10.1234/test"},
        }
        remote = {"status": "on_remarkable", "processed_at": None, "metadata": {}}

        _merge_single_document(local, remote)

        assert local["status"] == "processed"
        assert local["processed_at"] == "2026-02-20T00:00:00+00:00"
        assert local["summary"] == "My summary"

    def test_metadata_fills_gaps(self):
        from distillate.cloud_sync import _merge_single_document

        local = {"status": "processed", "metadata": {"doi": "10.1234/test"}}
        remote = {
            "status": "processed",
            "metadata": {"doi": "WRONG", "venue": "NeurIPS", "citations": 42},
        }

        _merge_single_document(local, remote)

        # doi: local wins (already set)
        assert local["metadata"]["doi"] == "10.1234/test"
        # venue + citations: filled from remote
        assert local["metadata"]["venue"] == "NeurIPS"
        assert local["metadata"]["citations"] == 42

    def test_local_summary_not_overwritten(self):
        from distillate.cloud_sync import _merge_single_document

        local = {"status": "processed", "summary": "Local version", "metadata": {}}
        remote = {"status": "processed", "summary": "Remote version", "metadata": {}}

        _merge_single_document(local, remote)
        assert local["summary"] == "Local version"

    def test_engagement_filled_from_remote(self):
        from distillate.cloud_sync import _merge_single_document

        local = {"status": "processed", "metadata": {}}
        remote = {
            "status": "processed",
            "metadata": {},
            "engagement": 85,
            "highlight_count": 12,
            "page_count": 8,
        }

        _merge_single_document(local, remote)
        assert local["engagement"] == 85
        assert local["highlight_count"] == 12
        assert local["page_count"] == 8

    def test_local_engagement_not_overwritten(self):
        from distillate.cloud_sync import _merge_single_document

        local = {"status": "processed", "metadata": {}, "engagement": 90}
        remote = {"status": "processed", "metadata": {}, "engagement": 50}

        _merge_single_document(local, remote)
        assert local["engagement"] == 90


# ---------------------------------------------------------------------------
# sync_state
# ---------------------------------------------------------------------------

class TestSyncState:
    def test_sync_pulls_then_pushes(self, cloud_env):
        from distillate.cloud_sync import sync_state
        from distillate.state import State

        state = State()

        with patch("distillate.cloud_sync.pull_state", return_value=True) as mock_pull, \
             patch("distillate.cloud_sync.push_state", return_value=True) as mock_push:
            result = sync_state(state)

        assert result is True
        mock_pull.assert_called_once_with(state)
        mock_push.assert_called_once_with(state)

    def test_sync_noop_without_credentials(self, monkeypatch):
        monkeypatch.setattr("distillate.config.DISTILLATE_AUTH_TOKEN", "")
        monkeypatch.setattr("distillate.config.DISTILLATE_API_URL", "")
        from distillate.cloud_sync import sync_state
        from distillate.state import State

        state = State()
        assert sync_state(state) is False


# ---------------------------------------------------------------------------
# last_cloud_sync_at property
# ---------------------------------------------------------------------------

class TestLastCloudSyncAt:
    def test_default_none(self):
        from distillate.state import State
        state = State()
        assert state.last_cloud_sync_at is None

    def test_roundtrip(self):
        from distillate.state import State
        state = State()
        state.last_cloud_sync_at = "2026-02-25T12:00:00+00:00"
        state.save()

        state2 = State()
        assert state2.last_cloud_sync_at == "2026-02-25T12:00:00+00:00"
