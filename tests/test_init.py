"""Tests for the init wizard (_init_wizard)."""

from pathlib import Path
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

    with patch("builtins.input", lambda _: next(input_iter)), \
         patch("requests.get", return_value=mock_resp), \
         patch("requests.post", return_value=mock_resp), \
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
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable registration
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic (step 5)
            "",                 # Skip Resend (step 6)
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
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
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable registration
            "",                 # Use Obsidian (default Y)
            vault_path,         # Vault path
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic
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
            "",                 # Skip Resend
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "OUTPUT_PATH=" in text
        assert Path(output_path).exists()

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
            "re_test456",           # Resend key
            "user@example.com",     # Digest email
            "",                     # Decline experiments (empty != y)
            "n",                    # Skip newsletter
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-test123" in text
        assert "RESEND_API_KEY=re_test456" in text
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
            "",                 # Skip Resend
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ], tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "KEEP_ZOTERO_PDF=false" in text


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

        inputs = iter([
            "",                     # Default choice (2 = AI & extras)
            "sk-ant-new123",        # Anthropic key (step 5)
            "",                     # Skip Resend (step 6)
            "",                     # Skip newsletter
            "n",                    # Skip experiments
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("requests.post", return_value=mock_resp), \
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
        assert "Step 1 of 6" not in output

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

        inputs = iter([
            "1",                # Full setup
            "",                 # Keep existing API key (Enter)
            "",                 # Keep existing User ID (Enter)
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic
            "",                 # Skip Resend
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("requests.post", return_value=mock_resp), \
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
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ZOTERO_API_KEY", "sk-very-long-api-key-12345")

        inputs = iter([
            "1",                # Full setup
            "",                 # Keep existing API key
            "999",              # New User ID
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep PDFs
            "",                 # Skip Anthropic
            "",                 # Skip Resend
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("requests.post", return_value=mock_resp), \
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
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "",                 # Keep existing registration (default N)
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep PDFs
            "",                 # Skip Anthropic
            "",                 # Skip Resend
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("requests.post", return_value=mock_resp), \
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
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep default (now 2 since existing is false)
            "",                 # Skip Anthropic
            "",                 # Skip Resend
            "",                 # Decline experiments (empty != y)
            "n",                # Skip newsletter
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("requests.post", return_value=mock_resp), \
             patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
             patch("platform.system", return_value="Linux"):
            from distillate.main import _init_wizard
            _init_wizard()

        text = env_file.read_text()
        assert "KEEP_ZOTERO_PDF=false" in text


# ---------------------------------------------------------------------------
# Experiment tracking at init (Step 6)
# ---------------------------------------------------------------------------

def _base_inputs_skip_to_experiments():
    """Wizard inputs that skip through steps 1-5 and Resend to reach experiments.

    After these 11 inputs, the next prompt is "Enable experiment tracking? [y/N]".
    Each test must append: experiment input(s) + [""] for newsletter skip.
    """
    return [
        "key",          # Step 1: API key
        "999",          # Step 1: User ID
        "",             # Step 1: Skip WebDAV
        "",             # Step 2: Reading surface (default: reMarkable)
        "n",            # Step 2: Skip reMarkable registration
        "n",            # Step 3: Don't use Obsidian
        "",             # Step 3: Skip plain folder
        "",             # Step 3: PDF subfolder (default pdf)
        "",             # Step 4: Keep PDFs (default 1)
        "",             # Step 5: Skip Anthropic
        "",             # Step 6: Skip Resend
    ]


class TestInitExperiments:
    """Tests for experiment tracking at init (Step 6)."""

    def test_skip_experiments(self, tmp_path, monkeypatch, capsys):
        """Declining experiment tracking writes nothing experiment-related."""
        inputs = _base_inputs_skip_to_experiments() + [
            "n",    # Decline experiments
            "",     # Skip newsletter
        ]
        env_file = _run_wizard(inputs, tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "EXPERIMENTS_ENABLED" not in text
        assert "EXPERIMENTS_ROOT" not in text

    def test_enable_with_valid_root_and_repos(self, tmp_path, monkeypatch, capsys):
        """Enable experiments, provide root, discover repos, scan them."""
        # Create a fake ML repo
        ml_repo = tmp_path / "research" / "my-project"
        ml_repo.mkdir(parents=True)
        (ml_repo / "train.py").write_text("import torch\n")
        (ml_repo / ".git").mkdir()

        inputs = _base_inputs_skip_to_experiments() + [
            "y",                        # Enable experiment tracking
            str(tmp_path / "research"), # Research folder root
            "",                         # Scan now (default Y)
            "",                         # Skip newsletter
        ]

        fake_scan = {
            "name": "my-project",
            "runs": {
                "exp-abc123": {
                    "id": "exp-abc123",
                    "name": "baseline",
                    "status": "completed",
                    "hyperparameters": {"lr": 0.001},
                    "results": {"loss": 0.5},
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T01:00:00Z",
                },
            },
            "head_hash": "abc123",
        }
        with patch("distillate.experiments.scan_project", return_value=fake_scan):
            env_file = _run_wizard(inputs, tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "EXPERIMENTS_ENABLED=true" in text
        assert "EXPERIMENTS_ROOT=" in text

        output = capsys.readouterr().out
        assert "my-project" in output
        assert "1 run(s)" in output

    def test_enable_with_no_repos_found(self, tmp_path, monkeypatch, capsys):
        """Enable experiments with a root that has no ML projects."""
        research_dir = tmp_path / "empty_research"
        research_dir.mkdir()

        inputs = _base_inputs_skip_to_experiments() + [
            "y",                        # Enable experiment tracking
            str(research_dir),          # Research folder root (empty)
            "",                         # Skip newsletter
        ]
        env_file = _run_wizard(inputs, tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "EXPERIMENTS_ENABLED=true" in text
        assert f"EXPERIMENTS_ROOT={research_dir}" in text

        output = capsys.readouterr().out
        assert "No ML projects found" in output

    def test_enable_with_invalid_root(self, tmp_path, monkeypatch, capsys):
        """Enable experiments with a nonexistent root path."""
        inputs = _base_inputs_skip_to_experiments() + [
            "y",                        # Enable experiment tracking
            str(tmp_path / "nope"),      # Nonexistent path
            "",                         # Skip newsletter
        ]
        env_file = _run_wizard(inputs, tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "EXPERIMENTS_ENABLED=true" in text

        output = capsys.readouterr().out
        assert "not found" in output.lower()

    def test_enable_skip_root_folder(self, tmp_path, monkeypatch, capsys):
        """Enable experiments but skip the root folder prompt."""
        inputs = _base_inputs_skip_to_experiments() + [
            "y",                        # Enable experiment tracking
            "",                         # Skip root folder
            "",                         # Skip newsletter
        ]
        env_file = _run_wizard(inputs, tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "EXPERIMENTS_ENABLED=true" in text
        assert "EXPERIMENTS_ROOT" not in text

        output = capsys.readouterr().out
        assert "EXPERIMENTS_ROOT" in output

    def test_enable_repos_found_decline_scan(self, tmp_path, monkeypatch, capsys):
        """Enable experiments, repos found, but decline to scan."""
        ml_repo = tmp_path / "research" / "proj"
        ml_repo.mkdir(parents=True)
        (ml_repo / "train.py").write_text("import torch\n")
        (ml_repo / ".git").mkdir()

        inputs = _base_inputs_skip_to_experiments() + [
            "y",                        # Enable experiment tracking
            str(tmp_path / "research"), # Root
            "n",                        # Don't scan now
            "",                         # Skip newsletter
        ]
        env_file = _run_wizard(inputs, tmp_path, monkeypatch)

        text = env_file.read_text()
        assert "EXPERIMENTS_ENABLED=true" in text

        output = capsys.readouterr().out
        assert "proj" in output

    def test_rerun_shortcut_includes_experiments(self, tmp_path, monkeypatch, capsys):
        """Re-run shortcut (option 2) reaches experiment tracking."""
        from distillate import config

        env_file = tmp_path / ".env"
        env_file.write_text("ZOTERO_API_KEY=existing\n")
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ZOTERO_API_KEY", "existing")

        inputs = iter([
            "",                         # Default choice (2 = AI & extras)
            "",                         # Skip Anthropic (step 5)
            "",                         # Skip Resend (step 6)
            "n",                        # Skip experiments
            "",                         # Skip newsletter
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("requests.post", return_value=mock_resp), \
             patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
             patch("platform.system", return_value="Linux"):
            from distillate.main import _init_wizard
            _init_wizard()

        output = capsys.readouterr().out
        assert "Experiment" in output

    def test_scan_multiple_repos(self, tmp_path, monkeypatch, capsys):
        """Scanning multiple repos adds all of them."""
        research = tmp_path / "research"
        for name in ("proj-a", "proj-b"):
            repo = research / name
            repo.mkdir(parents=True)
            (repo / "train.py").write_text("import torch\n")
            (repo / ".git").mkdir()

        inputs = _base_inputs_skip_to_experiments() + [
            "y",                        # Enable
            str(research),              # Root
            "",                         # Scan now (default Y)
            "",                         # Skip newsletter
        ]

        call_count = {"n": 0}
        def fake_scan(path):
            call_count["n"] += 1
            return {
                "name": path.name,
                "runs": {},
                "head_hash": f"hash{call_count['n']}",
            }

        with patch("distillate.experiments.scan_project", side_effect=fake_scan):
            env_file = _run_wizard(inputs, tmp_path, monkeypatch)

        output = capsys.readouterr().out
        assert "proj-a" in output
        assert "proj-b" in output
        assert "2 project(s)" in output

    def test_scan_saves_state(self, tmp_path, monkeypatch, capsys):
        """Scanning at init creates state entries for discovered projects."""
        ml_repo = tmp_path / "research" / "test-proj"
        ml_repo.mkdir(parents=True)
        (ml_repo / "train.py").write_text("import torch\n")
        (ml_repo / ".git").mkdir()

        inputs = _base_inputs_skip_to_experiments() + [
            "y",
            str(tmp_path / "research"),
            "",  # Scan now
            "",  # Skip newsletter
        ]

        fake_scan = {
            "name": "test-proj",
            "runs": {
                "exp-001": {
                    "id": "exp-001",
                    "name": "run-1",
                    "status": "completed",
                    "hyperparameters": {"epochs": 10},
                    "results": {"loss": 0.1},
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_at": "",
                },
            },
            "head_hash": "deadbeef",
        }

        with patch("distillate.experiments.scan_project", return_value=fake_scan):
            _run_wizard(inputs, tmp_path, monkeypatch)

        # Verify state was saved
        from distillate.state import State
        state = State()
        proj = state.get_project("test-proj")
        if proj:
            assert proj["name"] == "test-proj"
            assert "exp-001" in proj.get("runs", {})

    def test_step6_shows_in_output(self, tmp_path, monkeypatch, capsys):
        """Step 6 header is visible in wizard output."""
        inputs = _base_inputs_skip_to_experiments() + [
            "n",    # Decline experiments
            "",     # Skip newsletter
        ]
        _run_wizard(inputs, tmp_path, monkeypatch)

        output = capsys.readouterr().out
        assert "Step 6 of 6" in output
        assert "Extras" in output
