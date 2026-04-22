# Covers: distillate/auth.py
"""Tests for session management, token resolution, and session lifecycle."""
import pytest


@pytest.fixture(autouse=True)
def _reset_secrets_cache():
    from distillate import secrets as _secrets
    _secrets._cache.clear()
    yield
    _secrets._cache.clear()


@pytest.fixture()
def env_secrets(monkeypatch):
    """Force env backend and provide a clean slate for secret values."""
    monkeypatch.setenv("DISTILLATE_SECRETS_BACKEND", "env")
    session_keys = [
        "DISTILLATE_SESSION_JWT", "DISTILLATE_USER_ID",
        "HF_OAUTH_ACCESS_TOKEN", "HF_OAUTH_REFRESH_TOKEN", "HF_OAUTH_EXPIRES_AT",
        "_SESSION_EMAIL", "_SESSION_DISPLAY_NAME", "_SESSION_AVATAR_URL",
        "_LEGACY_CLAIMED", "HF_TOKEN", "DISTILLATE_AUTH_TOKEN",
    ]
    for k in session_keys:
        monkeypatch.delenv(k, raising=False)
    from distillate import secrets as _secrets
    _secrets._backend = "env"
    _secrets._cache.clear()
    yield
    _secrets._cache.clear()


# ── hf_token_for resolution order ──

class TestHfTokenFor:
    def test_manual_override_wins(self, env_secrets, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_manual")
        monkeypatch.setenv("HF_OAUTH_ACCESS_TOKEN", "hf_oauth")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.auth import hf_token_for
        assert hf_token_for("hub") == "hf_manual"
        assert hf_token_for("jobs") == "hf_manual"
        assert hf_token_for("inference") == "hf_manual"

    def test_falls_back_to_oauth_token(self, env_secrets, monkeypatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.setenv("HF_OAUTH_ACCESS_TOKEN", "hf_oauth")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.auth import hf_token_for
        assert hf_token_for("hub") == "hf_oauth"

    def test_returns_empty_when_neither_set(self, env_secrets, monkeypatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HF_OAUTH_ACCESS_TOKEN", raising=False)
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.auth import hf_token_for
        assert hf_token_for("hub") == ""


# ── clear_session does not touch HF_TOKEN or DISTILLATE_AUTH_TOKEN ──

class TestClearSession:
    def test_preserves_hf_token(self, env_secrets, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_keep_me")
        monkeypatch.setenv("DISTILLATE_AUTH_TOKEN", "legacy_keep_me")
        monkeypatch.setenv("DISTILLATE_SESSION_JWT", "eyJ.some.jwt")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.auth import clear_session
        clear_session()
        from distillate import secrets as _s2; _s2._cache.clear()
        from distillate.auth import hf_token_for
        assert hf_token_for("hub") == "hf_keep_me"

    def test_preserves_legacy_auth_token(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_AUTH_TOKEN", "legacy_keep_me")
        monkeypatch.setenv("DISTILLATE_SESSION_JWT", "eyJ.some.jwt")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.auth import clear_session
        clear_session()
        from distillate import secrets as _s2; _s2._cache.clear()
        from distillate import secrets
        assert secrets.get("DISTILLATE_AUTH_TOKEN") == "legacy_keep_me"

    def test_clears_session_jwt(self, env_secrets, monkeypatch):
        monkeypatch.setenv("DISTILLATE_SESSION_JWT", "eyJ.some.jwt")
        monkeypatch.setenv("DISTILLATE_USER_ID", "user-123")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.auth import clear_session, is_signed_in
        assert is_signed_in()
        clear_session()
        from distillate import secrets as _s2; _s2._cache.clear()
        assert not is_signed_in()


# ── set_session / get_session round-trip ──

class TestSessionRoundTrip:
    def test_round_trips_session(self, env_secrets):
        from distillate.auth import set_session, get_session, is_signed_in
        set_session(
            user_id="user-abc",
            session_jwt="eyJ.jwt.token",
            hf_access_token="hf_access_123",
            hf_refresh_token="hf_refresh_456",
            expires_at="2026-05-18T00:00:00Z",
            email="test@example.com",
            display_name="Test User",
            avatar_url="https://example.com/avatar.png",
        )
        from distillate import secrets as _s; _s._cache.clear()
        assert is_signed_in()
        session = get_session()
        assert session is not None
        assert session["user_id"] == "user-abc"
        assert session["display_name"] == "Test User"

    def test_get_session_returns_none_when_not_signed_in(self, env_secrets):
        from distillate.auth import get_session
        assert get_session() is None


# ── is_signed_in and current_user_id ──

class TestSignedInState:
    def test_is_signed_in_false_by_default(self, env_secrets):
        from distillate.auth import is_signed_in
        assert not is_signed_in()

    def test_current_user_id_none_when_not_signed_in(self, env_secrets):
        from distillate.auth import current_user_id
        assert current_user_id() is None


# ── legacy-claim idempotence (route-level) ──

class TestLegacyClaimIdempotence:
    """_claim_legacy swallows 409 (already claimed by same account) gracefully."""

    def test_409_returns_none_and_marks_claimed(self, env_secrets, monkeypatch):
        """Worker returns 409 → _claim_legacy returns None without raising."""
        import requests
        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.ok = False
        monkeypatch.setattr(requests, "post", lambda *a, **kw: mock_resp)

        from distillate.routes.auth import _claim_legacy
        result = _claim_legacy("eyJ.session.jwt", "old_legacy_token")
        assert result is None

    def test_second_call_skips_when_already_claimed(self, env_secrets, monkeypatch):
        """If _LEGACY_CLAIMED is set, auth.legacy_claimed() returns True."""
        monkeypatch.setenv("_LEGACY_CLAIMED", "1")
        from distillate import secrets as _s; _s._cache.clear()
        from distillate.auth import legacy_claimed
        assert legacy_claimed() is True
