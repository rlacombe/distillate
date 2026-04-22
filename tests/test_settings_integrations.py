# Covers: distillate/routes/settings.py
"""Covers: distillate/routes/settings.py — /integrations endpoint.

Targeted at the compute[] portion: whether cloud-GPU tiles light up
correctly based on auth detection. The auth functions themselves are
already covered in their own modules (test_modal_client etc.); these
tests verify the *wiring* from detection to UI payload.
"""

from __future__ import annotations

import importlib.util

import pytest


pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("fastapi"),
    reason="fastapi not installed (desktop-only dependency)",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A FastAPI TestClient wired to an isolated state + config dir."""
    monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
    monkeypatch.setattr("distillate.config.CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir(parents=True, exist_ok=True)

    from starlette.testclient import TestClient
    from distillate.server import _create_app
    app = _create_app()
    return TestClient(app)




class TestIntegrationsHealth:
    def test_health_no_integrations(self, client, monkeypatch):
        """With no integrations configured, health returns ok=True and empty health dict."""
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr("distillate.secrets.get", lambda key: "")
        resp = client.post("/integrations/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "health" in data
        assert data["health"] == {}

    def test_health_obsidian_ok(self, client, monkeypatch, tmp_path):
        """Obsidian status is 'ok' when vault path exists on disk."""
        vault = tmp_path / "vault"
        vault.mkdir()
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", str(vault))
        monkeypatch.setattr("distillate.secrets.get", lambda key: "")
        resp = client.post("/integrations/health")
        assert resp.status_code == 200
        assert resp.json()["health"]["obsidian"] == "ok"

    def test_health_obsidian_error(self, client, monkeypatch, tmp_path):
        """Obsidian status is 'error' when vault path does not exist."""
        missing = tmp_path / "nonexistent"
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", str(missing))
        monkeypatch.setattr("distillate.secrets.get", lambda key: "")
        resp = client.post("/integrations/health")
        assert resp.status_code == 200
        assert resp.json()["health"]["obsidian"] == "error"


class TestHfjobsFlavors:
    def test_hfjobs_flavors_returns_pricing(self, client):
        """GET /compute/hfjobs/flavors returns GPU options with pricing."""
        resp = client.get("/compute/hfjobs/flavors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["flavors"]) >= 5
        for f in data["flavors"]:
            assert "id" in f
            assert "label" in f
            assert "cost_per_hour" in f
            assert f["cost_per_hour"] > 0
            assert "vram_gb" in f

    def test_hfjobs_flavors_includes_key_gpus(self, client):
        """Key GPU tiers (T4, A100, H200) are always present."""
        resp = client.get("/compute/hfjobs/flavors")
        data = resp.json()
        ids = {f["id"] for f in data["flavors"]}
        for expected in ("t4-small", "a100-large", "h200"):
            assert expected in ids, f"{expected} missing from flavors"
