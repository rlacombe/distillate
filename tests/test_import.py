"""Tests for --import, get_recent_papers, _upload_paper, and init seed."""

from unittest.mock import MagicMock

import pytest


# -- Fixtures --

@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)
    yield tmp_path


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch):
    """Set required config values for tests."""
    monkeypatch.setenv("ZOTERO_API_KEY", "test_key")
    monkeypatch.setenv("ZOTERO_USER_ID", "12345")
    monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "test_key")
    monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
    monkeypatch.setattr("distillate.config.ZOTERO_TAG_INBOX", "inbox")
    monkeypatch.setattr("distillate.config.ZOTERO_TAG_READ", "read")
    monkeypatch.setattr("distillate.config.RM_FOLDER_INBOX", "Distillate/Inbox")
    monkeypatch.setattr("distillate.config.RM_FOLDER_PAPERS", "Distillate")
    monkeypatch.setattr("distillate.config.KEEP_ZOTERO_PDF", True)
    monkeypatch.setattr("distillate.config.HTTP_TIMEOUT", 10)


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


# -- Tests for get_recent_papers --

class TestGetRecentPapers:
    def test_returns_filtered_papers(self, monkeypatch):
        from distillate import zotero_client

        papers = [
            _make_paper("A1", "Paper A"),
            _make_paper("A2", "Note B", item_type="note"),
            _make_paper("A3", "Paper C"),
            _make_paper("A4", "Tagged D", tags=["inbox"]),
        ]

        mock_resp = MagicMock()
        mock_resp.json.return_value = papers
        mock_resp.status_code = 200
        mock_resp.headers = {"Last-Modified-Version": "10"}
        mock_resp.raise_for_status = MagicMock()

        monkeypatch.setattr(
            "distillate.zotero_client._request_with_retry",
            lambda *a, **kw: mock_resp,
        )

        result = zotero_client.get_recent_papers(limit=100)

        # Should keep A1 and A3, filter out note (A2) and tagged (A4)
        keys = [r["key"] for r in result]
        assert "A1" in keys
        assert "A3" in keys
        assert "A2" not in keys
        assert "A4" not in keys

    def test_empty_library(self, monkeypatch):
        from distillate import zotero_client

        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.status_code = 200
        mock_resp.headers = {"Last-Modified-Version": "5"}
        mock_resp.raise_for_status = MagicMock()

        monkeypatch.setattr(
            "distillate.zotero_client._request_with_retry",
            lambda *a, **kw: mock_resp,
        )

        result = zotero_client.get_recent_papers()
        assert result == []


# -- Tests for _upload_paper --

class TestUploadPaper:
    def test_upload_paper_success(self, monkeypatch):
        from distillate.main import _upload_paper
        from distillate.state import State

        state = State()

        paper = _make_paper("K1", "Test Paper", doi="10.1234/test")

        attachment = {"key": "ATT1", "data": {"md5": "abc123"}}
        monkeypatch.setattr(
            "distillate.zotero_client.get_pdf_attachment",
            lambda k: attachment,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.download_pdf",
            lambda k: b"fake-pdf-bytes",
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.upload_pdf_bytes",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.sanitize_filename",
            lambda n: n,
        )
        monkeypatch.setattr(
            "distillate.obsidian.save_inbox_pdf",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.add_tag",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "distillate.semantic_scholar.lookup_paper",
            lambda **kw: None,
        )

        result = _upload_paper(paper, state, existing_on_rm=set())
        assert result is True
        assert state.has_document("K1")
        doc = state.get_document("K1")
        assert doc["title"] == "Test Paper"
        assert doc["status"] == "on_remarkable"

    def test_upload_paper_duplicate_by_doi(self, monkeypatch):
        from distillate.main import _upload_paper
        from distillate.state import State

        state = State()
        # Pre-add a doc with same DOI
        state.add_document(
            zotero_item_key="OLD",
            zotero_attachment_key="",
            zotero_attachment_md5="",
            remarkable_doc_name="Old Paper",
            title="Old Paper",
            authors=["Smith"],
            metadata={"doi": "10.1234/dup"},
        )

        paper = _make_paper("K2", "New Paper Same DOI", doi="10.1234/dup")
        monkeypatch.setattr(
            "distillate.zotero_client.add_tag",
            lambda *a: None,
        )

        result = _upload_paper(paper, state, existing_on_rm=set())
        assert result is False
        assert not state.has_document("K2")

    def test_upload_paper_duplicate_by_title(self, monkeypatch):
        from distillate.main import _upload_paper
        from distillate.state import State

        state = State()
        state.add_document(
            zotero_item_key="OLD",
            zotero_attachment_key="",
            zotero_attachment_md5="",
            remarkable_doc_name="Existing Paper",
            title="Existing Paper",
            authors=["Jones"],
        )

        paper = _make_paper("K3", "Existing Paper")
        monkeypatch.setattr(
            "distillate.zotero_client.add_tag",
            lambda *a: None,
        )

        result = _upload_paper(paper, state, existing_on_rm=set())
        assert result is False

    def test_upload_paper_no_pdf_marks_awaiting(self, monkeypatch):
        from distillate.main import _upload_paper
        from distillate.state import State

        state = State()
        paper = _make_paper("K4", "No PDF Paper")

        monkeypatch.setattr(
            "distillate.zotero_client.get_pdf_attachment",
            lambda k: None,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.sanitize_filename",
            lambda n: n,
        )

        result = _upload_paper(paper, state, existing_on_rm=set())
        assert result is True
        doc = state.get_document("K4")
        assert doc["status"] == "awaiting_pdf"

    def test_upload_paper_skip_remarkable(self, monkeypatch):
        from distillate.main import _upload_paper
        from distillate.state import State

        state = State()
        paper = _make_paper("K5", "Skip RM Paper")

        monkeypatch.setattr(
            "distillate.zotero_client.get_pdf_attachment",
            lambda k: None,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.sanitize_filename",
            lambda n: n,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.add_tag",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "distillate.semantic_scholar.lookup_paper",
            lambda **kw: None,
        )

        # With skip_remarkable=True and no PDF, it should still mark awaiting_pdf
        result = _upload_paper(paper, state, existing_on_rm=set(), skip_remarkable=True)
        assert result is True
        doc = state.get_document("K5")
        # No PDF available → awaiting_pdf regardless of skip_remarkable
        assert doc["status"] == "awaiting_pdf"


# -- Tests for _import --

class TestImport:
    def test_import_noninteractive(self, monkeypatch, capsys):
        from distillate.main import _import

        papers = [
            _make_paper("I1", "Import Paper 1"),
            _make_paper("I2", "Import Paper 2"),
            _make_paper("I3", "Import Paper 3"),
        ]

        monkeypatch.setattr(
            "distillate.config.setup_logging", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.config.ensure_loaded", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_recent_papers",
            lambda limit=100: papers,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_library_version",
            lambda: 42,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.ensure_folders",
            lambda: None,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.list_folder",
            lambda f: [],
        )

        uploaded = []

        def fake_upload(paper, state, existing, skip_remarkable=False):
            uploaded.append(paper["key"])
            state.add_document(
                zotero_item_key=paper["key"],
                zotero_attachment_key="",
                zotero_attachment_md5="",
                remarkable_doc_name=paper["data"]["title"],
                title=paper["data"]["title"],
                authors=["Smith"],
            )
            return True

        monkeypatch.setattr("distillate.main._upload_paper", fake_upload)

        _import(["2"])

        output = capsys.readouterr().out
        assert "Imported 2 paper" in output
        assert len(uploaded) == 2

    def test_import_no_papers(self, monkeypatch, capsys):
        from distillate.main import _import

        monkeypatch.setattr(
            "distillate.config.setup_logging", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.config.ensure_loaded", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_recent_papers",
            lambda limit=100: [],
        )

        _import([])

        output = capsys.readouterr().out
        assert "No untracked papers" in output

    def test_import_interactive_all(self, monkeypatch, capsys):
        from distillate.main import _import

        papers = [
            _make_paper("I4", "Interactive Paper 1"),
            _make_paper("I5", "Interactive Paper 2"),
        ]

        monkeypatch.setattr(
            "distillate.config.setup_logging", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.config.ensure_loaded", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_recent_papers",
            lambda limit=100: papers,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_library_version",
            lambda: 50,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.ensure_folders",
            lambda: None,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.list_folder",
            lambda f: [],
        )

        uploaded = []

        def fake_upload(paper, state, existing, skip_remarkable=False):
            uploaded.append(paper["key"])
            return True

        monkeypatch.setattr("distillate.main._upload_paper", fake_upload)
        monkeypatch.setattr("builtins.input", lambda _: "all")

        _import([])

        output = capsys.readouterr().out
        assert "Found 2 untracked" in output
        assert len(uploaded) == 2

    def test_import_interactive_none(self, monkeypatch, capsys):
        from distillate.main import _import

        papers = [_make_paper("I6", "Skip Paper")]

        monkeypatch.setattr(
            "distillate.config.setup_logging", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.config.ensure_loaded", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_recent_papers",
            lambda limit=100: papers,
        )

        monkeypatch.setattr("builtins.input", lambda _: "none")

        _import([])

        output = capsys.readouterr().out
        assert "Skipped" in output

    def test_import_excludes_tracked(self, monkeypatch, capsys):
        from distillate.main import _import
        from distillate.state import State

        # Pre-track I7
        state = State()
        state.add_document(
            zotero_item_key="I7",
            zotero_attachment_key="",
            zotero_attachment_md5="",
            remarkable_doc_name="Already Tracked",
            title="Already Tracked",
            authors=["Smith"],
        )
        state.save()

        papers = [
            _make_paper("I7", "Already Tracked"),
            _make_paper("I8", "New Paper"),
        ]

        monkeypatch.setattr(
            "distillate.config.setup_logging", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.config.ensure_loaded", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_recent_papers",
            lambda limit=100: papers,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_library_version",
            lambda: 60,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.ensure_folders",
            lambda: None,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.list_folder",
            lambda f: [],
        )

        uploaded = []

        def fake_upload(paper, state, existing, skip_remarkable=False):
            uploaded.append(paper["key"])
            return True

        monkeypatch.setattr("distillate.main._upload_paper", fake_upload)

        _import(["10"])

        # Should only import I8, not I7
        assert uploaded == ["I8"]


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
            lambda limit=100: papers,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_library_version",
            lambda: 99,
        )

        uploaded = []

        def fake_upload(paper, state, existing, skip_remarkable=False):
            uploaded.append(paper["key"])
            return True

        monkeypatch.setattr("distillate.main._upload_paper", fake_upload)
        monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/rmapi")
        monkeypatch.setenv("REMARKABLE_DEVICE_TOKEN", "tok")
        monkeypatch.setattr(
            "distillate.remarkable_client.ensure_folders", lambda: None,
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.list_folder", lambda f: [],
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
            lambda limit=100: [],
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
            lambda limit=100: papers,
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

        monkeypatch.setattr("distillate.main._schedule_macos", mock_macos)
        monkeypatch.setattr("distillate.main._schedule_linux", mock_linux)

        monkeypatch.setattr("platform.system", lambda: "Darwin")
        _schedule()
        assert called == ["macos"]

        called.clear()
        monkeypatch.setattr("platform.system", lambda: "Linux")
        _schedule()
        assert called == ["linux"]
