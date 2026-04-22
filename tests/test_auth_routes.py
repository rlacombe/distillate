# Covers: distillate/routes/auth.py
"""Tests for HF OAuth auth routes: /auth/status, /auth/signin-hf-start, /auth/logout."""
import importlib.util
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("fastapi"),
    reason="fastapi not installed (desktop-only dependency)",
)

# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from distillate.routes.auth import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /auth/status
# ---------------------------------------------------------------------------

def test_auth_status_signed_out(client):
    with patch("distillate.auth.get_session", return_value=None):
        r = client.get("/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["signed_in"] is False
    assert body["user"] is None


def test_auth_status_signed_in(client):
    session = {
        "user_id": "uid-abc",
        "email": "test@hf.co",
        "display_name": "Test User",
        "avatar_url": None,
    }
    with patch("distillate.auth.get_session", return_value=session):
        r = client.get("/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["signed_in"] is True
    assert body["user"]["user_id"] == "uid-abc"
    assert body["user"]["display_name"] == "Test User"
    assert body["user"]["email"] == "test@hf.co"
    assert "hf_username" not in body["user"]


# ---------------------------------------------------------------------------
# /auth/signin-hf-start
# ---------------------------------------------------------------------------

def test_signin_hf_start_returns_authorize_url(client):
    r = client.post("/auth/signin-hf-start")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "authorize_url" in body
    assert "url" not in body  # field must be authorize_url, not url
    assert "desktop_nonce=" in body["authorize_url"]


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------

def test_auth_logout_clears_session(client):
    with patch("distillate.auth.clear_session") as mock_clear:
        r = client.post("/auth/logout")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    mock_clear.assert_called_once()
