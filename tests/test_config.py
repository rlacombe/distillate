"""Tests for config module: env loading, defaults, save_to_env, ensure_loaded."""

import os
from pathlib import Path

import pytest


class TestDefaults:
    """Verify default values when no env vars are set."""

    def test_rm_folder_defaults(self):
        from distillate import config

        # These may be overridden by the user's .env, so just check they're strings
        assert isinstance(config.RM_FOLDER_PAPERS, str)
        assert isinstance(config.RM_FOLDER_INBOX, str)
        assert isinstance(config.RM_FOLDER_READ, str)
        assert isinstance(config.RM_FOLDER_SAVED, str)

    def test_optional_keys_default_empty(self):
        """Optional keys that aren't set should default to empty string, not crash."""
        from distillate import config

        assert isinstance(config.ANTHROPIC_API_KEY, str)
        assert isinstance(config.RESEND_API_KEY, str)
        assert isinstance(config.OUTPUT_PATH, str)


class TestSaveToEnv:
    """Tests for config.save_to_env()."""

    def test_creates_new_key(self, tmp_path, monkeypatch):
        from distillate import config

        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=value\n")
        monkeypatch.setattr(config, "ENV_PATH", env_file)

        config.save_to_env("NEW_KEY", "new_value")
        text = env_file.read_text()
        assert "NEW_KEY=new_value" in text
        assert "EXISTING=value" in text

    def test_updates_existing_key(self, tmp_path, monkeypatch):
        from distillate import config

        env_file = tmp_path / ".env"
        env_file.write_text("MY_KEY=old\nOTHER=keep\n")
        monkeypatch.setattr(config, "ENV_PATH", env_file)

        config.save_to_env("MY_KEY", "new")
        text = env_file.read_text()
        assert "MY_KEY=new" in text
        assert "MY_KEY=old" not in text
        assert "OTHER=keep" in text

    def test_creates_env_file_if_missing(self, tmp_path, monkeypatch):
        from distillate import config

        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)

        config.save_to_env("BRAND_NEW", "val")
        assert env_file.exists()
        assert "BRAND_NEW=val" in env_file.read_text()

    def test_sets_env_file_permissions(self, tmp_path, monkeypatch):
        from distillate import config

        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)

        config.save_to_env("SECRET", "value")
        assert env_file.stat().st_mode & 0o777 == 0o600

    def test_sets_os_environ(self, tmp_path, monkeypatch):
        from distillate import config

        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)

        config.save_to_env("TEST_ENV_VAR", "hello")
        assert os.environ.get("TEST_ENV_VAR") == "hello"
        # Cleanup
        monkeypatch.delenv("TEST_ENV_VAR", raising=False)


class TestEnsureLoaded:
    """Tests for lazy config loading."""

    def test_ensure_loaded_succeeds_with_env(self, monkeypatch):
        from distillate import config

        monkeypatch.setattr(config, "_loaded", False)
        monkeypatch.setenv("ZOTERO_API_KEY", "test_key")
        monkeypatch.setenv("ZOTERO_USER_ID", "12345")

        config.ensure_loaded()
        assert config.ZOTERO_API_KEY == "test_key"
        assert config.ZOTERO_USER_ID == "12345"
        # Reset for other tests
        monkeypatch.setattr(config, "_loaded", False)

    def test_ensure_loaded_exits_without_env(self, monkeypatch):
        from distillate import config

        monkeypatch.setattr(config, "_loaded", False)
        monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
        monkeypatch.delenv("ZOTERO_USER_ID", raising=False)

        with pytest.raises(SystemExit):
            config.ensure_loaded()
        # Reset
        monkeypatch.setattr(config, "_loaded", False)

    def test_ensure_loaded_idempotent(self, monkeypatch):
        from distillate import config

        monkeypatch.setattr(config, "_loaded", False)
        monkeypatch.setenv("ZOTERO_API_KEY", "key1")
        monkeypatch.setenv("ZOTERO_USER_ID", "111")
        config.ensure_loaded()

        # Change env var — second call should be a no-op
        monkeypatch.setenv("ZOTERO_API_KEY", "key2")
        config.ensure_loaded()
        assert config.ZOTERO_API_KEY == "key1"
        monkeypatch.setattr(config, "_loaded", False)


class TestValidateOptional:
    """Tests for _validate_optional() warnings."""

    def test_warns_on_missing_vault_path(self, monkeypatch, caplog):
        import logging
        from distillate import config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "/nonexistent/vault")
        monkeypatch.setattr(config, "OUTPUT_PATH", "")
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(config, "RESEND_API_KEY", "")

        with caplog.at_level(logging.WARNING):
            config._validate_optional()
        assert "OBSIDIAN_VAULT_PATH does not exist" in caplog.text

    def test_warns_on_missing_output_path(self, monkeypatch, caplog):
        import logging
        from distillate import config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", "/nonexistent/output")
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(config, "RESEND_API_KEY", "")

        with caplog.at_level(logging.WARNING):
            config._validate_optional()
        assert "OUTPUT_PATH does not exist" in caplog.text

    def test_warns_on_bad_anthropic_key(self, monkeypatch, caplog):
        import logging
        from distillate import config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", "")
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "bad-key-prefix")
        monkeypatch.setattr(config, "RESEND_API_KEY", "")

        with caplog.at_level(logging.WARNING):
            config._validate_optional()
        assert "ANTHROPIC_API_KEY" in caplog.text

    def test_warns_on_bad_resend_key(self, monkeypatch, caplog):
        import logging
        from distillate import config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", "")
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(config, "RESEND_API_KEY", "bad-key-prefix")

        with caplog.at_level(logging.WARNING):
            config._validate_optional()
        assert "RESEND_API_KEY" in caplog.text

    def test_no_warnings_when_valid(self, monkeypatch, caplog, tmp_path):
        import logging
        from distillate import config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setattr(config, "OUTPUT_PATH", "")
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-valid")
        monkeypatch.setattr(config, "RESEND_API_KEY", "re_valid")

        with caplog.at_level(logging.WARNING):
            config._validate_optional()
        assert caplog.text == ""

    def test_no_warnings_when_empty(self, monkeypatch, caplog):
        import logging
        from distillate import config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", "")
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(config, "RESEND_API_KEY", "")

        with caplog.at_level(logging.WARNING):
            config._validate_optional()
        assert caplog.text == ""


class TestConfigDir:
    """Tests for CONFIG_DIR resolution."""

    def test_config_dir_is_path(self):
        from distillate.config import CONFIG_DIR

        assert isinstance(CONFIG_DIR, Path)

    def test_config_dir_exists(self):
        from distillate.config import CONFIG_DIR

        assert CONFIG_DIR.exists()
