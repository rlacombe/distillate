# Covers: distillate/main.py (_init_wizard — re-run shortcut when config already exists)
"""Tests for the init wizard re-run flow (config already exists)."""

import os
from unittest.mock import patch, MagicMock

# Env vars that the wizard reads — must be cleared for clean tests
_WIZARD_ENV_KEYS = [
    "ZOTERO_API_KEY", "ZOTERO_USER_ID", "REMARKABLE_DEVICE_TOKEN",
    "OBSIDIAN_VAULT_PATH", "OUTPUT_PATH", "PDF_SUBFOLDER",
    "KEEP_ZOTERO_PDF", "ANTHROPIC_API_KEY", "RESEND_API_KEY", "DIGEST_TO",
    "READING_SOURCE",
]


def _mock_resp():
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.status_code = 200
    m.headers = {"Last-Modified-Version": "0"}
    m.json = MagicMock(return_value=[])
    return m


def _run_rerun_wizard(inputs, monkeypatch):
    """Run the wizard with patched I/O + HTTP. Does NOT isolate env_file path."""
    mr = _mock_resp()
    with patch("builtins.input", lambda _: next(inputs)), \
         patch("requests.get", return_value=mr), \
         patch("requests.post", return_value=mr), \
         patch("requests.request", return_value=mr), \
         patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
         patch("platform.system", return_value="Linux"):
        from distillate.main import _init_wizard
        _init_wizard()


class TestInitRerun:
    """Tests for the re-run shortcut when config already exists."""

    def test_shortcut_jumps_to_step5(self, tmp_path, monkeypatch, capsys):
        """Option 2 (default) on re-run skips to AI & extras."""
        from distillate import config
        env_file = tmp_path / ".env"
        env_file.write_text("ZOTERO_API_KEY=existing_key\n")
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ZOTERO_API_KEY", "existing_key")

        _run_rerun_wizard(iter([
            "",                 # Default choice (2 = AI & extras)
            "sk-ant-new123",    # Anthropic key (step 5)
            "",                 # Skip HuggingFace (step 6)
            "",                 # Skip Resend (step 7)
            "",                 # Skip newsletter
            "n",                # Skip experiments
        ]), monkeypatch)

        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-new123"
        assert "ZOTERO_API_KEY=existing_key" in env_file.read_text()
        output = capsys.readouterr().out
        assert "Step 1 of 7" not in output

    def test_full_rerun_shows_existing_values(self, tmp_path, monkeypatch, capsys):
        """Option 1 on re-run shows full wizard with existing values."""
        from distillate import config
        env_file = tmp_path / ".env"
        env_file.write_text("ZOTERO_API_KEY=old_key\nZOTERO_USER_ID=111\n")
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ZOTERO_API_KEY", "old_key")
        monkeypatch.setenv("ZOTERO_USER_ID", "111")

        _run_rerun_wizard(iter([
            "1",    # Full setup
            "",     # Keep existing API key (Enter)
            "",     # Keep existing User ID (Enter)
            "",     # Skip WebDAV
            "",     # Reading surface (default: reMarkable)
            "n",    # Skip reMarkable
            "n",    # Don't use Obsidian
            "",     # Skip plain folder
            "",     # PDF subfolder (default pdf)
            "",     # Keep PDFs (default 1)
            "",     # Skip Anthropic
            "",     # Skip HuggingFace
            "",     # Skip Resend
            "",     # Decline experiments (empty != y)
            "n",    # Skip newsletter
        ]), monkeypatch)

        text = env_file.read_text()
        assert "ZOTERO_API_KEY=old_key" in text
        assert "ZOTERO_USER_ID=111" in text

    def test_existing_values_shown_masked(self, tmp_path, monkeypatch, capsys):
        """Long API keys are masked in the prompt display."""
        from distillate import config
        env_file = tmp_path / ".env"
        env_file.write_text("ZOTERO_API_KEY=sk-very-long-api-key-12345\n")
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ZOTERO_API_KEY", "sk-very-long-api-key-12345")

        _run_rerun_wizard(iter([
            "1",    # Full setup
            "",     # Keep existing API key
            "999",  # New User ID
            "",     # Skip WebDAV
            "",     # Reading surface (default: reMarkable)
            "n",    # Skip reMarkable
            "n",    # Don't use Obsidian
            "",     # Skip plain folder
            "",     # PDF subfolder (default pdf)
            "",     # Keep PDFs
            "",     # Skip Anthropic
            "",     # Skip HuggingFace
            "",     # Skip Resend
            "",     # Decline experiments (empty != y)
            "n",    # Skip newsletter
        ]), monkeypatch)

        assert "ZOTERO_API_KEY=sk-very-long-api-key-12345" in env_file.read_text()

    def test_skip_registration_when_already_registered(self, tmp_path, monkeypatch, capsys):
        """Already-registered reMarkable defaults to skip."""
        from distillate import config
        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("REMARKABLE_DEVICE_TOKEN", "some_token")

        _run_rerun_wizard(iter([
            "key",  # API key
            "999",  # User ID
            "",     # Skip WebDAV
            "",     # Reading surface (default: reMarkable)
            "",     # Keep existing registration (default N)
            "n",    # Don't use Obsidian
            "",     # Skip plain folder
            "",     # PDF subfolder (default pdf)
            "",     # Keep PDFs
            "",     # Skip Anthropic
            "",     # Skip HuggingFace
            "",     # Skip Resend
            "",     # Decline experiments (empty != y)
            "n",    # Skip newsletter
        ]), monkeypatch)

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

        _run_rerun_wizard(iter([
            "key",  # API key
            "999",  # User ID
            "",     # Skip WebDAV
            "",     # Reading surface (default: reMarkable)
            "n",    # Skip reMarkable
            "n",    # Don't use Obsidian
            "",     # Skip plain folder
            "",     # PDF subfolder (default pdf)
            "",     # Keep default (now 2 since existing is false)
            "",     # Skip Anthropic
            "",     # Skip HuggingFace
            "",     # Skip Resend
            "",     # Decline experiments (empty != y)
            "n",    # Skip newsletter
        ]), monkeypatch)

        assert "KEEP_ZOTERO_PDF=false" in env_file.read_text()
