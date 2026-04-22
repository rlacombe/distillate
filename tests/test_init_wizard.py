# Covers: distillate/main.py (_init_wizard — fresh-start wizard steps 1–7)
"""Tests for the init wizard (_init_wizard) — fresh first-run flow."""

import os
from unittest.mock import patch, MagicMock

# Env vars that the wizard reads — must be cleared for clean tests
_WIZARD_ENV_KEYS = [
    "ZOTERO_API_KEY", "ZOTERO_USER_ID", "REMARKABLE_DEVICE_TOKEN",
    "OBSIDIAN_VAULT_PATH", "OUTPUT_PATH", "PDF_SUBFOLDER",
    "KEEP_ZOTERO_PDF", "ANTHROPIC_API_KEY", "RESEND_API_KEY", "DIGEST_TO",
    "READING_SOURCE",
]


def _run_wizard(inputs, tmp_path, monkeypatch):
    """Helper to run the wizard with mocked I/O, rmapi, and Zotero API."""
    from distillate import config

    env_file = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", env_file)

    # Isolate state file so wizard scans don't pollute the real state
    monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")

    # Clear all wizard-related env vars for isolation
    for key in _WIZARD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    input_iter = iter(inputs)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"Last-Modified-Version": "0"}
    mock_resp.json = MagicMock(return_value=[])

    with patch("builtins.input", lambda _: next(input_iter)), \
         patch("requests.get", return_value=mock_resp), \
         patch("requests.post", return_value=mock_resp), \
         patch("requests.request", return_value=mock_resp), \
         patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
         patch("platform.system", return_value="Linux"):
        from distillate.main import _init_wizard
        _init_wizard()

    return env_file


class TestInitWizard:
    """Tests for main._init_wizard() with mocked I/O — fresh first-run."""

    def test_saves_zotero_credentials(self, tmp_path, monkeypatch):
        env_file = _run_wizard([
            "test_api_key",     # API key
            "12345",            # User ID
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable registration
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic (step 5)
            "",                 # Skip HuggingFace (step 6)
            "",                 # Skip Resend (step 7)
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ], tmp_path, monkeypatch)

        # Secrets go to keyring (os.environ in test mode), not .env
        assert os.environ.get("ZOTERO_API_KEY") == "test_api_key"
        assert os.environ.get("ZOTERO_USER_ID") == "12345"

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
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable registration
            "",                 # Use Obsidian (default Y)
            vault_path,         # Vault path
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic
            "",                 # Skip HuggingFace
            "",                 # Skip Resend
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "OBSIDIAN_VAULT_PATH=" in text

    def test_plain_folder_path_saved(self, tmp_path, monkeypatch):
        output_path = str(tmp_path / "notes")
        env_file = _run_wizard([
            "key",              # API key
            "999",              # User ID
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable registration
            "n",                # Don't use Obsidian
            output_path,        # Plain folder path
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic
            "",                 # Skip HuggingFace
            "",                 # Skip Resend
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "OUTPUT_PATH=" in text
        assert (tmp_path / "notes").exists()

    def test_optional_features_saved(self, tmp_path, monkeypatch):
        env_file = _run_wizard([
            "key",                  # API key
            "999",                  # User ID
            "",                     # Skip WebDAV
            "",                     # Reading surface (default: reMarkable)
            "n",                    # Skip reMarkable registration
            "n",                    # Don't use Obsidian
            "",                     # Skip plain folder
            "",                     # PDF subfolder (default pdf)
            "",                     # Keep PDFs (default 1)
            "sk-ant-test123",       # Anthropic key
            "",                     # Skip HuggingFace
            "re_test456",           # Resend key
            "user@example.com",     # Digest email
            "",                     # Decline experiments (empty != y)
            "n",                    # Skip newsletter
        ], tmp_path, monkeypatch)

        # Secrets go to keyring (os.environ in test mode), not .env
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test123"
        assert os.environ.get("RESEND_API_KEY") == "re_test456"
        # Non-secret config still goes to .env
        text = env_file.read_text()
        assert "DIGEST_TO=user@example.com" in text

    def test_delete_zotero_pdf_option(self, tmp_path, monkeypatch):
        env_file = _run_wizard([
            "key",              # API key
            "999",              # User ID
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable registration
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # PDF subfolder (default pdf)
            "2",                # Remove PDFs from Zotero after sync
            "",                 # Skip Anthropic
            "",                 # Skip HuggingFace
            "",                 # Skip Resend
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "KEEP_ZOTERO_PDF=false" in text
