# Covers: distillate/main.py (_init_seed, _schedule_macos, _schedule_linux, _schedule)
#         distillate/wizard.py
"""Tests for init seed and platform-specific scheduler commands (launchd and cron)."""

from unittest.mock import MagicMock

import pytest


def _make_paper(key, title, doi="", item_type="journalArticle", tags=None):
    """Build a minimal Zotero item dict for testing."""
    tag_list = [{"tag": t} for t in (tags or [])]
    return {
        "key": key,
        "version": 1,
        "data": {
            "key": key,
            "itemType": item_type,
            "title": title,
            "DOI": doi,
            "creators": [{"creatorType": "author", "lastName": "Smith"}],
            "tags": tag_list,
            "url": "",
            "abstractNote": "",
            "date": "2026",
            "publicationTitle": "Test Journal",
        },
    }


# -- Tests for init seed --

class TestInitSeed:
    def test_init_seed_imports_papers(self, monkeypatch, capsys):
        from distillate.main import _init_seed

        papers = [
            _make_paper("S1", "Seed Paper 1"),
            _make_paper("S2", "Seed Paper 2"),
        ]

        monkeypatch.setattr(
            "distillate.config.ensure_loaded", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_recent_papers",
            lambda limit=100, collection_key="": papers,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_library_version",
            lambda: 99,
        )

        uploaded = []

        def fake_upload(paper, state, existing, skip_remarkable=False):
            uploaded.append(paper["key"])
            return True

        monkeypatch.setattr("distillate.pipeline._upload_paper", fake_upload)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/rmapi")
        monkeypatch.setenv("REMARKABLE_DEVICE_TOKEN", "tok")
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.ensure_folders", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.list_folder", lambda f: [],
        )
        monkeypatch.setattr("builtins.input", lambda _: "all")

        _init_seed()

        output = capsys.readouterr().out
        assert "Found 2 untracked" in output
        assert len(uploaded) == 2

    def test_init_seed_no_papers(self, monkeypatch, capsys):
        from distillate.main import _init_seed

        monkeypatch.setattr(
            "distillate.config.ensure_loaded", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_recent_papers",
            lambda limit=100, collection_key="": [],
        )

        _init_seed()

        output = capsys.readouterr().out
        # No output when no papers — just returns
        assert "untracked" not in output

    def test_init_seed_skip(self, monkeypatch, capsys):
        from distillate.main import _init_seed
        from distillate.state import State

        papers = [_make_paper("S3", "Skip Me")]

        monkeypatch.setattr(
            "distillate.config.ensure_loaded", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_recent_papers",
            lambda limit=100, collection_key="": papers,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_library_version",
            lambda: 77,
        )
        monkeypatch.setattr("builtins.input", lambda _: "none")

        _init_seed()

        output = capsys.readouterr().out
        assert "Skipped" in output

        # Watermark should still be set
        state = State()
        assert state.zotero_library_version == 77

    def test_init_seed_api_error_graceful(self, monkeypatch, capsys):
        from distillate.main import _init_seed

        monkeypatch.setattr(
            "distillate.config.ensure_loaded", lambda: None,
        )

        def explode(*a, **kw):
            raise ConnectionError("no internet")

        monkeypatch.setattr(
            "distillate.zotero_client.get_recent_papers", explode,
        )

        _init_seed()

        output = capsys.readouterr().out
        assert "distillate --import" in output


# -- Tests for _schedule --

class TestSchedule:
    def test_schedule_macos_active_keep(self, tmp_path, monkeypatch, capsys):
        """Plist exists, user chooses to keep — shows Active status."""
        import plistlib
        from distillate.main import _schedule_macos

        plist_path = tmp_path / "com.distillate.sync.plist"
        plist_data = {"Label": "com.distillate.sync", "StartInterval": 900}
        with open(plist_path, "wb") as f:
            plistlib.dump(plist_data, f)

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        (tmp_path / "Library" / "LaunchAgents").mkdir(parents=True)
        real_plist = tmp_path / "Library" / "LaunchAgents" / "com.distillate.sync.plist"
        with open(real_plist, "wb") as f:
            plistlib.dump(plist_data, f)

        monkeypatch.setattr("builtins.input", lambda _: "3")

        _schedule_macos()

        output = capsys.readouterr().out
        assert "Active (launchd)" in output
        assert "every 15 minutes" in output

    def test_schedule_macos_not_active(self, tmp_path, monkeypatch, capsys):
        """No plist, user skips — shows Not scheduled."""
        from distillate.main import _schedule_macos

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        (tmp_path / "Library" / "LaunchAgents").mkdir(parents=True)

        monkeypatch.setattr("builtins.input", lambda _: "n")

        _schedule_macos()

        output = capsys.readouterr().out
        assert "Not scheduled" in output
        assert "Skipped" in output

    def test_schedule_macos_remove(self, tmp_path, monkeypatch, capsys):
        """Plist exists, user removes — unlinks plist."""
        import plistlib
        from distillate.main import _schedule_macos

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        (tmp_path / "Library" / "LaunchAgents").mkdir(parents=True)
        real_plist = tmp_path / "Library" / "LaunchAgents" / "com.distillate.sync.plist"
        plist_data = {"Label": "com.distillate.sync", "StartInterval": 1800}
        with open(real_plist, "wb") as f:
            plistlib.dump(plist_data, f)

        # Mock subprocess.run so launchctl doesn't actually run
        calls = []
        def mock_run(*args, **kwargs):
            calls.append(args)
            return MagicMock(returncode=0)

        monkeypatch.setattr("subprocess.run", mock_run)
        monkeypatch.setattr("builtins.input", lambda _: "2")

        _schedule_macos()

        output = capsys.readouterr().out
        assert "Schedule removed" in output
        assert not real_plist.exists()

    def test_schedule_linux_no_cron(self, monkeypatch, capsys):
        """No crontab entry — shows instructions."""
        from distillate.main import _schedule_linux

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "# empty crontab\n"
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        _schedule_linux()

        output = capsys.readouterr().out
        assert "Not scheduled" in output
        assert "crontab -e" in output

    def test_schedule_linux_with_cron(self, monkeypatch, capsys):
        """Crontab has distillate entry — shows Active."""
        from distillate.main import _schedule_linux

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "*/15 * * * * /usr/local/bin/distillate\n"
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)

        _schedule_linux()

        output = capsys.readouterr().out
        assert "Active (cron)" in output
        assert "distillate" in output

    def test_schedule_dispatches_by_platform(self, monkeypatch, capsys):
        """_schedule() dispatches to macos or linux based on platform."""
        from distillate.main import _schedule

        called = []

        def mock_macos():
            called.append("macos")

        def mock_linux():
            called.append("linux")

        monkeypatch.setattr("distillate.wizard._schedule_macos", mock_macos)
        monkeypatch.setattr("distillate.wizard._schedule_linux", mock_linux)

        monkeypatch.setattr("platform.system", lambda: "Darwin")
        _schedule()
        assert called == ["macos"]

        called.clear()
        monkeypatch.setattr("platform.system", lambda: "Linux")
        _schedule()
        assert called == ["linux"]
