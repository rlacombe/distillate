"""Tests for the init wizard (_init_wizard)."""

from pathlib import Path
from unittest.mock import patch, MagicMock

# Env vars that the wizard reads — must be cleared for clean tests
_WIZARD_ENV_KEYS = [
    "ZOTERO_API_KEY", "ZOTERO_USER_ID", "REMARKABLE_DEVICE_TOKEN",
    "OBSIDIAN_VAULT_PATH", "OUTPUT_PATH", "KEEP_ZOTERO_PDF",
    "ANTHROPIC_API_KEY", "RESEND_API_KEY", "DIGEST_TO",
]


def _run_wizard(inputs, tmp_path, monkeypatch):
    """Helper to run the wizard with mocked I/O, rmapi, and Zotero API."""
    from distillate import config

    env_file = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", env_file)

    # Clear all wizard-related env vars for isolation
    for key in _WIZARD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    input_iter = iter(inputs)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("builtins.input", lambda _: next(input_iter)), \
         patch("requests.get", return_value=mock_resp), \
         patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
         patch("platform.system", return_value="Linux"):
        from distillate.main import _init_wizard
        _init_wizard()

    return env_file


class TestInitWizard:
    """Tests for main._init_wizard() with mocked I/O."""

    def test_saves_zotero_credentials(self, tmp_path, monkeypatch):
        env_file = _run_wizard([
            "test_api_key",     # API key
            "12345",            # User ID
            "n",                # Skip reMarkable registration
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic
            "",                 # Skip Resend
            "n",                # Skip automatic syncing (Linux shows crontab)
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "ZOTERO_API_KEY=test_api_key" in text
        assert "ZOTERO_USER_ID=12345" in text

    def test_empty_api_key_aborts(self, tmp_path, monkeypatch, capsys):
        from distillate import config
        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

        inputs = iter([""])
        with patch("builtins.input", lambda _: next(inputs)):
            from distillate.main import _init_wizard
            _init_wizard()

        output = capsys.readouterr().out
        assert "required" in output.lower()

    def test_obsidian_vault_path_saved(self, tmp_path, monkeypatch):
        vault_path = str(tmp_path / "my_vault")
        env_file = _run_wizard([
            "key",              # API key
            "999",              # User ID
            "n",                # Skip reMarkable registration
            "",                 # Use Obsidian (default Y)
            vault_path,         # Vault path
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic
            "",                 # Skip Resend
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "OBSIDIAN_VAULT_PATH=" in text

    def test_plain_folder_path_saved(self, tmp_path, monkeypatch):
        output_path = str(tmp_path / "notes")
        env_file = _run_wizard([
            "key",              # API key
            "999",              # User ID
            "n",                # Skip reMarkable registration
            "n",                # Don't use Obsidian
            output_path,        # Plain folder path
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic
            "",                 # Skip Resend
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "OUTPUT_PATH=" in text
        assert Path(output_path).exists()

    def test_optional_features_saved(self, tmp_path, monkeypatch):
        env_file = _run_wizard([
            "key",                  # API key
            "999",                  # User ID
            "n",                    # Skip reMarkable registration
            "n",                    # Don't use Obsidian
            "",                     # Skip plain folder
            "",                     # Keep PDFs (default 1)
            "sk-ant-test123",       # Anthropic key
            "re_test456",           # Resend key
            "user@example.com",     # Email
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-test123" in text
        assert "RESEND_API_KEY=re_test456" in text
        assert "DIGEST_TO=user@example.com" in text

    def test_delete_zotero_pdf_option(self, tmp_path, monkeypatch):
        env_file = _run_wizard([
            "key",              # API key
            "999",              # User ID
            "n",                # Skip reMarkable registration
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "2",                # Remove PDFs from Zotero after sync
            "",                 # Skip Anthropic
            "",                 # Skip Resend
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "KEEP_ZOTERO_PDF=false" in text


class TestInitRerun:
    """Tests for the re-run shortcut when config already exists."""

    def test_shortcut_jumps_to_step5(self, tmp_path, monkeypatch, capsys):
        """Option 2 (default) on re-run skips to optional features."""
        from distillate import config

        env_file = tmp_path / ".env"
        env_file.write_text("ZOTERO_API_KEY=existing_key\n")
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ZOTERO_API_KEY", "existing_key")

        inputs = iter([
            "",                     # Default choice (2 = optional features)
            "sk-ant-new123",        # Anthropic key
            "",                     # Skip Resend
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
             patch("platform.system", return_value="Linux"):
            from distillate.main import _init_wizard
            _init_wizard()

        text = env_file.read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-new123" in text
        # Original key preserved
        assert "ZOTERO_API_KEY=existing_key" in text
        # Should NOT show Step 1
        output = capsys.readouterr().out
        assert "Step 1 of 5" not in output

    def test_full_rerun_shows_existing_values(self, tmp_path, monkeypatch, capsys):
        """Option 1 on re-run shows full wizard with existing values."""
        from distillate import config

        env_file = tmp_path / ".env"
        env_file.write_text("ZOTERO_API_KEY=old_key\nZOTERO_USER_ID=111\n")
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ZOTERO_API_KEY", "old_key")
        monkeypatch.setenv("ZOTERO_USER_ID", "111")

        inputs = iter([
            "1",                # Full setup
            "",                 # Keep existing API key (Enter)
            "",                 # Keep existing User ID (Enter)
            "n",                # Skip reMarkable
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic
            "",                 # Skip Resend
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
             patch("platform.system", return_value="Linux"):
            from distillate.main import _init_wizard
            _init_wizard()

        text = env_file.read_text()
        # Existing values preserved via Enter
        assert "ZOTERO_API_KEY=old_key" in text
        assert "ZOTERO_USER_ID=111" in text

    def test_existing_values_shown_masked(self, tmp_path, monkeypatch, capsys):
        """Long API keys are masked in the prompt display."""
        from distillate import config

        env_file = tmp_path / ".env"
        env_file.write_text("ZOTERO_API_KEY=sk-very-long-api-key-12345\n")
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ZOTERO_API_KEY", "sk-very-long-api-key-12345")

        inputs = iter([
            "1",                # Full setup
            "",                 # Keep existing API key
            "999",              # New User ID
            "n",                # Skip reMarkable
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # Keep PDFs
            "",                 # Skip Anthropic
            "",                 # Skip Resend
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
             patch("platform.system", return_value="Linux"):
            from distillate.main import _init_wizard
            _init_wizard()

        text = env_file.read_text()
        assert "ZOTERO_API_KEY=sk-very-long-api-key-12345" in text

    def test_skip_registration_when_already_registered(self, tmp_path, monkeypatch, capsys):
        """Already-registered reMarkable defaults to skip."""
        from distillate import config

        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("REMARKABLE_DEVICE_TOKEN", "some_token")

        inputs = iter([
            "key",              # API key
            "999",              # User ID
            "",                 # Keep existing registration (default N)
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # Keep PDFs
            "",                 # Skip Anthropic
            "",                 # Skip Resend
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
             patch("platform.system", return_value="Linux"):
            from distillate.main import _init_wizard
            _init_wizard()

        output = capsys.readouterr().out
        assert "already registered" in output.lower()
        assert "Keeping existing registration" in output

    def test_pdf_storage_default_from_existing(self, tmp_path, monkeypatch):
        """Step 4 defaults to option 2 when KEEP_ZOTERO_PDF=false."""
        from distillate import config

        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("KEEP_ZOTERO_PDF", "false")

        inputs = iter([
            "key",              # API key
            "999",              # User ID
            "n",                # Skip reMarkable
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # Keep default (now 2 since existing is false)
            "",                 # Skip Anthropic
            "",                 # Skip Resend
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
             patch("platform.system", return_value="Linux"):
            from distillate.main import _init_wizard
            _init_wizard()

        text = env_file.read_text()
        assert "KEEP_ZOTERO_PDF=false" in text
