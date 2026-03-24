"""Tests for distillate.cloud_sync — granular cloud state push/pull."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def cloud_env(monkeypatch):
    """Set cloud credentials via os.environ (matching cloud_sync_available)."""
    monkeypatch.setenv("DISTILLATE_AUTH_TOKEN", "test-token-123")
    monkeypatch.setenv("DISTILLATE_CLOUD_URL", "https://api.distillate.dev")


@pytest.fixture
def no_cloud_env(monkeypatch):
    """Ensure cloud credentials are absent."""
    monkeypatch.delenv("DISTILLATE_AUTH_TOKEN", raising=False)
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

        mock_resp = MagicMock(ok=True)
        mock_resp.json.return_value = {"ok": True, "upserted": 1}

        with patch("distillate.cloud_sync.requests.put", return_value=mock_resp) as mock_put:
            result = push_state(state)

        assert result is True
        assert mock_put.call_count == 2

        # First call: documents
        doc_call = mock_put.call_args_list[0]
        assert doc_call.args[0] == "https://api.distillate.dev/state/documents"
        assert doc_call.kwargs["headers"]["x-auth-token"] == "test-token-123"

        # Second call: projects
        proj_call = mock_put.call_args_list[1]
        assert proj_call.args[0] == "https://api.distillate.dev/state/projects"

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

    def test_push_partial_failure(self, cloud_env):
        """If documents succeeds but projects fails, push returns False."""
        from distillate.cloud_sync import push_state
        from distillate.state import State

        state = State()
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
            "projects": {
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
        assert state.has_project("my-proj")
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
        proj_resp.json.return_value = {"projects": {}, "sync_at": "2026-02-25T12:00:00+00:00"}

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
        proj_resp.json.return_value = {"projects": {}, "sync_at": "2026-02-25T12:00:00+00:00"}

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
        proj_resp.json.return_value = {"projects": {}, "sync_at": "2026-02-25T12:00:00+00:00"}

        with patch("distillate.cloud_sync.requests.get", side_effect=[empty_resp, proj_resp]) as mock_get:
            pull_state(state)

        urls = [c.args[0] for c in mock_get.call_args_list]
        assert urls[0] == "https://api.distillate.dev/state/documents"
        assert urls[1] == "https://api.distillate.dev/state/projects"

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
            "projects": {"p1": {"id": "p1", "name": "P1", "path": "/tmp", "runs": {}}},
            "sync_at": "2026-02-25T12:00:00+00:00",
        }

        with patch("distillate.cloud_sync.requests.get", side_effect=[doc_resp, proj_resp]):
            result = pull_state(state)

        assert result is True
        assert state.has_project("p1")


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

        assert local["metadata"]["doi"] == "10.1234/test"
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

class TestMergeProject:
    """Test run-level merge within existing projects."""

    def test_new_runs_added(self):
        from distillate.cloud_sync import _merge_single_project

        local = {
            "name": "My ML Project",
            "runs": {
                "xp-aaa": {"name": "baseline", "decision": "best", "results": {"f1": 0.42}},
            },
        }
        remote = {
            "name": "My ML Project",
            "runs": {
                "xp-aaa": {"name": "baseline", "decision": "best", "results": {"f1": 0.42}},
                "xp-bbb": {"name": "dropout", "decision": "completed", "results": {"f1": 0.38}},
            },
        }

        _merge_single_project(local, remote)

        assert "xp-bbb" in local["runs"]
        assert local["runs"]["xp-bbb"]["name"] == "dropout"
        assert local["runs"]["xp-bbb"]["results"]["f1"] == 0.38

    def test_metadata_fills_gaps(self):
        from distillate.cloud_sync import _merge_single_project

        local = {"name": "Project", "runs": {}}
        remote = {"name": "Project", "github_url": "https://github.com/foo/bar", "runs": {}}

        _merge_single_project(local, remote)
        assert local["github_url"] == "https://github.com/foo/bar"

    def test_local_metadata_not_overwritten(self):
        from distillate.cloud_sync import _merge_single_project

        local = {"name": "Local Name", "github_url": "https://local", "runs": {}}
        remote = {"name": "Remote Name", "github_url": "https://remote", "runs": {}}

        _merge_single_project(local, remote)
        assert local["name"] == "Local Name"
        assert local["github_url"] == "https://local"

    def test_pull_merges_runs_into_existing_project(self, cloud_env):
        """End-to-end: pull adds runs to an existing local project."""
        from distillate.cloud_sync import pull_state
        from distillate.state import State

        state = State()
        state.projects["proj-1"] = {
            "id": "proj-1", "name": "ML Proj", "path": "/tmp",
            "runs": {
                "xp-local": {"name": "run A", "decision": "best", "results": {"acc": 0.9}},
            },
        }

        doc_resp = MagicMock(ok=True, status_code=200)
        doc_resp.json.return_value = {"documents": {}, "sync_at": "2026-03-23T12:00:00+00:00"}
        proj_resp = MagicMock(ok=True, status_code=200)
        proj_resp.json.return_value = {
            "projects": {
                "proj-1": {
                    "id": "proj-1", "name": "ML Proj", "path": "/tmp",
                    "runs": {
                        "xp-local": {"name": "run A", "decision": "best", "results": {"acc": 0.9}},
                        "xp-remote": {"name": "run B", "decision": "completed", "results": {"acc": 0.85}},
                    },
                },
            },
            "sync_at": "2026-03-23T12:00:00+00:00",
        }

        with patch("distillate.cloud_sync.requests.get", side_effect=[doc_resp, proj_resp]):
            result = pull_state(state)

        assert result is True
        runs = state.projects["proj-1"]["runs"]
        assert len(runs) == 2
        assert "xp-remote" in runs
        assert runs["xp-local"]["decision"] == "best"  # preserved


class TestMergeRun:
    """Test field-level merge for individual runs."""

    def test_decision_advances_forward(self):
        from distillate.cloud_sync import _merge_single_run

        local = {"decision": "completed", "results": {"f1": 0.5}}
        remote = {"decision": "best", "results": {"f1": 0.5}}

        _merge_single_run(local, remote)
        assert local["decision"] == "best"

    def test_decision_does_not_regress(self):
        from distillate.cloud_sync import _merge_single_run

        local = {"decision": "best", "results": {"f1": 0.5}}
        remote = {"decision": "completed", "results": {"f1": 0.5}}

        _merge_single_run(local, remote)
        assert local["decision"] == "best"

    def test_status_advances_forward(self):
        from distillate.cloud_sync import _merge_single_run

        local = {"status": "running"}
        remote = {"status": "completed"}

        _merge_single_run(local, remote)
        assert local["status"] == "completed"

    def test_status_does_not_regress(self):
        from distillate.cloud_sync import _merge_single_run

        local = {"status": "completed"}
        remote = {"status": "running"}

        _merge_single_run(local, remote)
        assert local["status"] == "completed"

    def test_scalar_fields_fill_gaps(self):
        from distillate.cloud_sync import _merge_single_run

        local = {"decision": "best", "description": "baseline CNN"}
        remote = {
            "decision": "best",
            "description": "should not overwrite",
            "hypothesis": "CNN should work well",
            "completed_at": "2026-03-23T14:00:00+00:00",
        }

        _merge_single_run(local, remote)
        assert local["description"] == "baseline CNN"  # local wins
        assert local["hypothesis"] == "CNN should work well"  # filled
        assert local["completed_at"] == "2026-03-23T14:00:00+00:00"  # filled

    def test_results_merged_key_by_key(self):
        from distillate.cloud_sync import _merge_single_run

        local = {"results": {"f1": 0.42, "accuracy": 0.91}}
        remote = {"results": {"f1": 0.99, "loss": 0.08}}

        _merge_single_run(local, remote)
        assert local["results"]["f1"] == 0.42  # local wins
        assert local["results"]["accuracy"] == 0.91  # preserved
        assert local["results"]["loss"] == 0.08  # filled from remote

    def test_hyperparameters_merged_key_by_key(self):
        from distillate.cloud_sync import _merge_single_run

        local = {"hyperparameters": {"lr": 0.001}}
        remote = {"hyperparameters": {"lr": 0.01, "batch_size": 32}}

        _merge_single_run(local, remote)
        assert local["hyperparameters"]["lr"] == 0.001  # local wins
        assert local["hyperparameters"]["batch_size"] == 32  # filled

    def test_crash_to_completed_advances(self):
        from distillate.cloud_sync import _merge_single_run

        local = {"decision": "crash"}
        remote = {"decision": "completed", "results": {"f1": 0.3}}

        _merge_single_run(local, remote)
        assert local["decision"] == "completed"
        assert local["results"]["f1"] == 0.3


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
