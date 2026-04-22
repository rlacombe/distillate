# Covers: distillate/cloud_sync.py (cloud_sync_available, push_state, pull_state, sync_state)
#         distillate/state.py (last_cloud_sync_at property)
"""Tests for cloud sync HTTP operations: availability check, push, pull, and orchestration."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def cloud_env(monkeypatch):
    """Set cloud credentials via os.environ (matching cloud_sync_available)."""
    monkeypatch.setenv("DISTILLATE_AUTH_TOKEN", "test-token-123")
    monkeypatch.setenv("DISTILLATE_CLOUD_URL", "https://api.distillate.dev")
    monkeypatch.delenv("DISTILLATE_SESSION_JWT", raising=False)


@pytest.fixture
def no_cloud_env(monkeypatch):
    """Ensure cloud credentials are absent."""
    monkeypatch.delenv("DISTILLATE_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DISTILLATE_SESSION_JWT", raising=False)
    monkeypatch.delenv("DISTILLATE_CLOUD_URL", raising=False)


# ---------------------------------------------------------------------------
# cloud_sync_available
# ---------------------------------------------------------------------------

class TestCloudSyncAvailable:
    def test_available_when_both_set(self, cloud_env):
        from distillate.cloud_sync import cloud_sync_available
        assert cloud_sync_available() is True

    def test_unavailable_when_no_token(self, monkeypatch):
        monkeypatch.delenv("DISTILLATE_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("DISTILLATE_SESSION_JWT", raising=False)
        monkeypatch.setenv("DISTILLATE_CLOUD_URL", "https://api.distillate.dev")
        from distillate.cloud_sync import cloud_sync_available
        assert cloud_sync_available() is False

    def test_unavailable_when_no_url(self, monkeypatch):
        monkeypatch.setenv("DISTILLATE_AUTH_TOKEN", "tok")
        monkeypatch.delenv("DISTILLATE_CLOUD_URL", raising=False)
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
        state.save()  # persist to SQLite so delta push picks it up

        mock_resp = MagicMock(ok=True)
        mock_resp.json.return_value = {"ok": True, "upserted": 1}

        with patch("distillate.cloud_sync.requests.put", return_value=mock_resp) as mock_put:
            result = push_state(state)

        assert result is True
        # Delta push only sends collections that have changes
        assert mock_put.call_count >= 1

        # Documents call
        doc_call = mock_put.call_args_list[0]
        assert doc_call.args[0] == "https://api.distillate.dev/state/documents"
        assert doc_call.kwargs["headers"]["x-auth-token"] == "test-token-123"

    def test_push_timeout_graceful(self, cloud_env):
        import requests as req_lib
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        state.add_document("K1", "A1", "md5", "doc1", "Title", ["Auth"])
        state.save()
        with patch("distillate.cloud_sync.requests.put",
                    side_effect=req_lib.exceptions.Timeout):
            result = push_state(state)
        assert result is False

    def test_push_connection_error_graceful(self, cloud_env):
        import requests as req_lib
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        state.add_document("K1", "A1", "md5", "doc1", "Title", ["Auth"])
        state.save()
        with patch("distillate.cloud_sync.requests.put",
                    side_effect=req_lib.exceptions.ConnectionError):
            result = push_state(state)
        assert result is False

    def test_push_partial_failure(self, cloud_env):
        """If documents succeeds but projects fails, push returns False."""
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        state.add_document("K1", "A1", "md5", "doc1", "Title", ["Auth"])
        state._data.setdefault("experiments", {})["P1"] = {"id": "P1", "name": "Proj"}
        state.save()
        ok_resp = MagicMock(ok=True)
        ok_resp.json.return_value = {"ok": True, "upserted": 0}
        fail_resp = MagicMock(ok=False, status_code=500, text="error")

        with patch("distillate.cloud_sync.requests.put",
                    side_effect=[ok_resp, fail_resp]):
            result = push_state(state)
        assert result is False

    def test_push_server_error_graceful(self, cloud_env):
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
        state.add_document("K1", "A1", "md5", "doc1", "Title", ["Auth"])
        state.save()
        mock_resp = MagicMock(ok=False, status_code=500, text="Internal Server Error")

        with patch("distillate.cloud_sync.requests.put", return_value=mock_resp):
            result = push_state(state)
        assert result is False

    def test_push_noop_without_credentials(self, no_cloud_env):
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
    def test_pull_adds_new_documents_and_projects(self, cloud_env):
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        state.add_document("K1", "A1", "md5", "doc1", "Local Paper", ["A"])

        doc_resp = MagicMock(ok=True, status_code=200)
        doc_resp.json.return_value = {
            "documents": {
                "K2": {
                    "title": "Cloud Paper",
                    "status": "on_remarkable",
                    "authors": ["B"],
                    "metadata": {},
                    "processed_at": None,
                },
            },
            "sync_at": "2026-02-25T12:00:00+00:00",
        }
        proj_resp = MagicMock(ok=True, status_code=200)
        proj_resp.json.return_value = {
            "experiments": {
                "my-proj": {"id": "my-proj", "name": "My Project", "path": "/tmp/ml", "runs": {}},
            },
            "sync_at": "2026-02-25T12:00:01+00:00",
        }

        with patch("distillate.cloud_sync.requests.get", side_effect=[doc_resp, proj_resp]):
            result = pull_state(state)

        assert result is True
        assert state.has_document("K2")
        assert state.get_document("K2")["title"] == "Cloud Paper"
        assert state.has_document("K1")  # local doc preserved
        assert state.has_experiment("my-proj")
        # Uses the later sync_at
        assert state.last_cloud_sync_at == "2026-02-25T12:00:01+00:00"

    def test_pull_passes_since_param(self, cloud_env):
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        state.last_cloud_sync_at = "2026-02-20T00:00:00+00:00"

        empty_resp = MagicMock(ok=True, status_code=200)
        empty_resp.json.return_value = {"documents": {}, "sync_at": "2026-02-25T12:00:00+00:00"}
        proj_resp = MagicMock(ok=True, status_code=200)
        proj_resp.json.return_value = {"experiments": {}, "sync_at": "2026-02-25T12:00:00+00:00"}

        with patch("distillate.cloud_sync.requests.get", side_effect=[empty_resp, proj_resp]) as mock_get:
            pull_state(state)

        # Both calls should pass ?since=
        for c in mock_get.call_args_list:
            assert c.kwargs["params"]["since"] == "2026-02-20T00:00:00+00:00"

    def test_pull_no_since_on_first_sync(self, cloud_env):
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        assert state.last_cloud_sync_at is None

        empty_resp = MagicMock(ok=True, status_code=200)
        empty_resp.json.return_value = {"documents": {}, "sync_at": "2026-02-25T12:00:00+00:00"}
        proj_resp = MagicMock(ok=True, status_code=200)
        proj_resp.json.return_value = {"experiments": {}, "sync_at": "2026-02-25T12:00:00+00:00"}

        with patch("distillate.cloud_sync.requests.get", side_effect=[empty_resp, proj_resp]) as mock_get:
            pull_state(state)

        for c in mock_get.call_args_list:
            assert c.kwargs["params"] == {}

    def test_pull_uses_correct_urls(self, cloud_env):
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        empty_resp = MagicMock(ok=True, status_code=200)
        empty_resp.json.return_value = {"documents": {}, "sync_at": "2026-02-25T12:00:00+00:00"}
        proj_resp = MagicMock(ok=True, status_code=200)
        proj_resp.json.return_value = {"experiments": {}, "sync_at": "2026-02-25T12:00:00+00:00"}

        with patch("distillate.cloud_sync.requests.get", side_effect=[empty_resp, proj_resp]) as mock_get:
            pull_state(state)

        urls = [c.args[0] for c in mock_get.call_args_list]
        assert urls[0] == "https://api.distillate.dev/state/documents"
        assert urls[1] == "https://api.distillate.dev/state/experiments"

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

    def test_pull_noop_without_credentials(self, no_cloud_env):
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        with patch("distillate.cloud_sync.requests.get") as mock_get:
            result = pull_state(state)
        assert result is False
        mock_get.assert_not_called()

    def test_pull_404_continues(self, cloud_env):
        """404 on documents doesn't abort — continues to pull projects."""
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        doc_resp = MagicMock(ok=False, status_code=404)
        proj_resp = MagicMock(ok=True, status_code=200)
        proj_resp.json.return_value = {
            "experiments": {"p1": {"id": "p1", "name": "P1", "path": "/tmp", "runs": {}}},
            "sync_at": "2026-02-25T12:00:00+00:00",
        }

        with patch("distillate.cloud_sync.requests.get", side_effect=[doc_resp, proj_resp]):
            result = pull_state(state)

        assert result is True
        assert state.has_experiment("p1")


# ---------------------------------------------------------------------------
# sync_state
# ---------------------------------------------------------------------------

class TestSyncState:
    def test_sync_pulls_then_pushes(self, cloud_env):
        from distillate.cloud_sync import sync_state
        from distillate.state import State

        state = State()

        with patch("distillate.cloud_sync.pull_state", return_value=True) as mock_pull, \
             patch("distillate.cloud_sync.push_state", return_value=True) as mock_push, \
             patch("distillate.cloud_sync._refresh_snapshot"):
            result = sync_state(state)

        assert result is True
        mock_pull.assert_called_once_with(state)
        mock_push.assert_called_once_with(state)

    def test_sync_refreshes_snapshot_on_success(self, cloud_env):
        from distillate.cloud_sync import sync_state
        from distillate.state import State

        state = State()

        with patch("distillate.cloud_sync.pull_state", return_value=True), \
             patch("distillate.cloud_sync.push_state", return_value=True), \
             patch("distillate.cloud_sync._refresh_snapshot") as mock_refresh:
            sync_state(state)

        mock_refresh.assert_called_once_with(state)

    def test_sync_skips_snapshot_on_push_failure(self, cloud_env):
        from distillate.cloud_sync import sync_state
        from distillate.state import State

        state = State()

        with patch("distillate.cloud_sync.pull_state", return_value=True), \
             patch("distillate.cloud_sync.push_state", return_value=False), \
             patch("distillate.cloud_sync._refresh_snapshot") as mock_refresh:
            sync_state(state)

        mock_refresh.assert_not_called()

    def test_sync_noop_without_credentials(self, no_cloud_env):
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
