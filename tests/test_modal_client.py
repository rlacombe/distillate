# Covers: distillate/modal_client.py
"""Tests for distillate.modal_client — Modal compute provider helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Tests: app_name_for
# ---------------------------------------------------------------------------

class TestAppNameFor:
    def test_prefixes_experiment_id(self):
        from distillate.modal_client import app_name_for
        assert app_name_for("abc123") == "distillate-xp-abc123"

    def test_does_not_double_prefix(self):
        # Round-tripping the App name through the helper must be a no-op
        # so callers can safely normalise either form.
        from distillate.modal_client import app_name_for
        assert app_name_for("distillate-xp-foo") == "distillate-xp-foo"


# ---------------------------------------------------------------------------
# Tests: is_authed
# ---------------------------------------------------------------------------

class TestIsAuthed:
    def test_returns_false_when_token_file_missing(self, tmp_path):
        from distillate.modal_client import is_authed
        assert is_authed(token_path=tmp_path / "missing.toml") is False

    def test_returns_true_with_valid_default_token(self, tmp_path):
        from distillate.modal_client import is_authed
        token_file = tmp_path / "modal.toml"
        token_file.write_text(
            '[default]\n'
            'token_id = "ak-abc123"\n'
            'token_secret = "as-xyz789"\n'
        )
        assert is_authed(token_path=token_file) is True

    def test_returns_false_when_file_empty(self, tmp_path):
        from distillate.modal_client import is_authed
        token_file = tmp_path / "modal.toml"
        token_file.write_text("")
        assert is_authed(token_path=token_file) is False

    def test_returns_false_when_no_default_section(self, tmp_path):
        from distillate.modal_client import is_authed
        token_file = tmp_path / "modal.toml"
        token_file.write_text(
            '[other]\n'
            'token_id = "ak-abc"\n'
            'token_secret = "as-xyz"\n'
        )
        assert is_authed(token_path=token_file) is False

    def test_returns_false_when_malformed_toml(self, tmp_path):
        from distillate.modal_client import is_authed
        token_file = tmp_path / "modal.toml"
        token_file.write_text("not = valid = toml [[")
        assert is_authed(token_path=token_file) is False

    def test_returns_false_when_token_id_missing(self, tmp_path):
        from distillate.modal_client import is_authed
        token_file = tmp_path / "modal.toml"
        token_file.write_text(
            '[default]\n'
            'token_secret = "as-xyz789"\n'
        )
        assert is_authed(token_path=token_file) is False

    def test_returns_false_when_token_secret_missing(self, tmp_path):
        from distillate.modal_client import is_authed
        token_file = tmp_path / "modal.toml"
        token_file.write_text(
            '[default]\n'
            'token_id = "ak-abc123"\n'
        )
        assert is_authed(token_path=token_file) is False


# ---------------------------------------------------------------------------
# Tests: stop_app
# ---------------------------------------------------------------------------

class TestStopApp:
    @patch("distillate.modal_client.subprocess.run")
    def test_returns_true_on_success(self, mock_run):
        from distillate.modal_client import stop_app
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert stop_app("distillate-xp-abc") is True
        # Verify the actual command shape — these arg positions are the
        # contract with the modal CLI, worth pinning.
        args = mock_run.call_args[0][0]
        assert args[0] == "modal"
        assert "app" in args
        assert "stop" in args
        assert "distillate-xp-abc" in args

    @patch("distillate.modal_client.subprocess.run")
    def test_returns_false_on_nonzero_exit(self, mock_run):
        from distillate.modal_client import stop_app
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="App not found",
        )
        assert stop_app("distillate-xp-missing") is False

    @patch("distillate.modal_client.subprocess.run")
    def test_returns_false_when_modal_cli_not_installed(self, mock_run):
        from distillate.modal_client import stop_app
        mock_run.side_effect = FileNotFoundError("modal not on PATH")
        assert stop_app("distillate-xp-abc") is False

    @patch("distillate.modal_client.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run):
        import subprocess as sp
        from distillate.modal_client import stop_app
        mock_run.side_effect = sp.TimeoutExpired(cmd="modal", timeout=30)
        assert stop_app("distillate-xp-abc") is False
