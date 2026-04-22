# Covers: distillate/cloud_sync.py (auth header and 401 handling)
"""Tests for cloud sync authentication header and 401 behavior."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _reset_cache():
    from distillate import secrets as _secrets
    _secrets._cache.clear()
    yield
    _secrets._cache.clear()


@pytest.fixture()
def env_secrets(monkeypatch):
    monkeypatch.setenv("DISTILLATE_SECRETS_BACKEND", "env")
    for k in ["DISTILLATE_SESSION_JWT", "DISTILLATE_AUTH_TOKEN", "DISTILLATE_CLOUD_URL",
              "HF_OAUTH_ACCESS_TOKEN", "DISTILLATE_USER_ID", "HF_OAUTH_REFRESH_TOKEN",
              "HF_OAUTH_EXPIRES_AT"]:
        monkeypatch.delenv(k, raising=False)
    from distillate import secrets as _s
    _s._backend = "env"
    _s._cache.clear()
    yield
    _s._cache.clear()


# ── cloud_sync_available ──

class TestCloudSyncAvailable:
    def test_true_with_session_jwt(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_SESSION_JWT", "eyJ.jwt.token")
        monkeypatch.setenv("DISTILLATE_CLOUD_URL", "https://api.distillate.dev")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.cloud_sync import cloud_sync_available
        assert cloud_sync_available()

    def test_true_with_legacy_token(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_AUTH_TOKEN", "legacy-opaque-token")
        monkeypatch.setenv("DISTILLATE_CLOUD_URL", "https://api.distillate.dev")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.cloud_sync import cloud_sync_available
        assert cloud_sync_available()

    def test_false_with_no_credentials(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_CLOUD_URL", "https://api.distillate.dev")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.cloud_sync import cloud_sync_available
        assert not cloud_sync_available()

    def test_false_with_no_cloud_url(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_SESSION_JWT", "eyJ.jwt.token")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.cloud_sync import cloud_sync_available
        assert not cloud_sync_available()


# ── _headers ──

class TestHeaders:
    def test_jwt_session_sends_bearer(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_SESSION_JWT", "eyJ.jwt.token")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.cloud_sync import _headers
        h = _headers()
        assert h.get("Authorization") == "Bearer eyJ.jwt.token"
        assert "x-auth-token" not in h

    def test_legacy_token_sends_xauthtoken(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_AUTH_TOKEN", "legacy-opaque-token")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.cloud_sync import _headers
        h = _headers()
        assert h.get("x-auth-token") == "legacy-opaque-token"
        assert "Authorization" not in h

    def test_jwt_wins_over_legacy(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_SESSION_JWT", "eyJ.jwt.token")
        monkeypatch.setenv("DISTILLATE_AUTH_TOKEN", "legacy-opaque-token")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.cloud_sync import _headers
        h = _headers()
        assert h.get("Authorization") == "Bearer eyJ.jwt.token"


# ── 401 clears session and returns False ──

class TestUnauthorizedHandling:
    def _mock_401(self):
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 401
        resp.text = "Unauthorized"
        return resp

    def test_push_401_clears_session_and_returns_false(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_SESSION_JWT", "eyJ.jwt.token")
        monkeypatch.setenv("DISTILLATE_CLOUD_URL", "https://api.distillate.dev")
        from distillate import secrets as _s; _s._cache.clear()

        state = MagicMock()
        mock_resp = self._mock_401()

        with patch("distillate.state_sqlite.get_meta", return_value=None), \
             patch("distillate.state_sqlite.changed_documents_since", return_value={"key": {"title": "Paper"}}), \
             patch("distillate.cloud_sync.requests.put", return_value=mock_resp):
            from distillate.cloud_sync import push_state
            result = push_state(state)

        assert result is False
        from distillate import secrets as _s2; _s2._cache.clear()
        from distillate.auth import is_signed_in
        assert not is_signed_in()

    def test_pull_401_clears_session_and_returns_false(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_SESSION_JWT", "eyJ.jwt.token")
        monkeypatch.setenv("DISTILLATE_CLOUD_URL", "https://api.distillate.dev")
        from distillate import secrets as _s; _s._cache.clear()

        state = MagicMock()
        state.last_cloud_sync_at = None
        mock_resp = self._mock_401()

        with patch("distillate.cloud_sync.requests.get", return_value=mock_resp):
            from distillate.cloud_sync import pull_state
            result = pull_state(state)

        assert result is False
        from distillate import secrets as _s2; _s2._cache.clear()
        from distillate.auth import is_signed_in
        assert not is_signed_in()
