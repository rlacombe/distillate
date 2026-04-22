# Covers: distillate/secrets.py
"""Tests for the secrets module: keyring backend, env fallback, migration."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from distillate import secrets


class TestSecretKeys:
    """Verify the set of secret keys."""

    def test_secret_keys_are_frozenset(self):
        assert isinstance(secrets.SECRET_KEYS, frozenset)

    def test_expected_keys_present(self):
        expected = {
            "ZOTERO_API_KEY", "ZOTERO_USER_ID", "REMARKABLE_DEVICE_TOKEN",
            "ANTHROPIC_API_KEY", "HF_TOKEN", "DISTILLATE_AUTH_TOKEN",
            "RESEND_API_KEY", "ZOTERO_WEBDAV_PASSWORD",
            # HF OAuth session keys
            "DISTILLATE_SESSION_JWT", "DISTILLATE_USER_ID",
            "HF_OAUTH_ACCESS_TOKEN", "HF_OAUTH_REFRESH_TOKEN", "HF_OAUTH_EXPIRES_AT",
            "_SESSION_EMAIL", "_SESSION_DISPLAY_NAME", "_SESSION_AVATAR_URL",
            "_LEGACY_CLAIMED",
        }
        assert secrets.SECRET_KEYS == expected


class TestEnvBackend:
    """Tests with env backend (the default in tests via conftest)."""

    def test_get_returns_env_var(self, monkeypatch):
        monkeypatch.setattr(secrets, "_cache", {})
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
        assert secrets.get("ANTHROPIC_API_KEY") == "sk-test-123"

    def test_get_returns_empty_when_unset(self, monkeypatch):
        monkeypatch.setattr(secrets, "_cache", {})
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert secrets.get("ANTHROPIC_API_KEY") == ""

    def test_set_updates_environ(self, monkeypatch):
        monkeypatch.setattr(secrets, "_cache", {})
        secrets.set("HF_TOKEN", "hf_test_456")
        assert os.environ.get("HF_TOKEN") == "hf_test_456"
        monkeypatch.delenv("HF_TOKEN", raising=False)

    def test_set_updates_cache(self, monkeypatch):
        monkeypatch.setattr(secrets, "_cache", {})
        secrets.set("HF_TOKEN", "hf_cached")
        assert secrets._cache["HF_TOKEN"] == "hf_cached"

    def test_get_uses_cache(self, monkeypatch):
        monkeypatch.setattr(secrets, "_cache", {"MY_KEY": "cached_val"})
        assert secrets.get("MY_KEY") == "cached_val"

    def test_delete_clears_environ_and_cache(self, monkeypatch):
        monkeypatch.setattr(secrets, "_cache", {"HF_TOKEN": "old"})
        monkeypatch.setenv("HF_TOKEN", "old")
        secrets.delete("HF_TOKEN")
        assert "HF_TOKEN" not in secrets._cache
        assert "HF_TOKEN" not in os.environ

    def test_using_keyring_always_false(self):
        assert not secrets.using_keyring()


class TestMigrationStub:
    """Verify migrate_from_env() is a no-op stub (keyring removed, encrypted DB used instead)."""

    def test_migrate_returns_zero(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-test\n")
        assert secrets.migrate_from_env(env_path=env_file) == 0


class TestConfigIntegration:
    """Test that config.save_to_env() rejects secret keys."""

    def test_save_to_env_rejects_secret_keys(self, tmp_path, monkeypatch):
        from distillate import config
        monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")

        with pytest.raises(ValueError, match="secret"):
            config.save_to_env("ANTHROPIC_API_KEY", "sk-bad")

    def test_save_to_env_accepts_config_keys(self, tmp_path, monkeypatch):
        from distillate import config
        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)

        config.save_to_env("LOG_LEVEL", "DEBUG")
        assert "LOG_LEVEL=DEBUG" in env_file.read_text()
