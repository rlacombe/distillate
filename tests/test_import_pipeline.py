# Covers: distillate/main.py (_upload_paper, _import)
#         distillate/pipeline.py
"""Tests for import pipeline: upload paper and _import command."""

from unittest.mock import MagicMock

import pytest


# -- Fixtures --

@pytest.fixture(autouse=True)
def isolate_config(monkeypatch):
    """Set required config values for tests."""
    monkeypatch.setenv("ZOTERO_API_KEY", "test_key")
    monkeypatch.setenv("ZOTERO_USER_ID", "12345")
    monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "test_key")
    monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
    monkeypatch.setattr("distillate.config.ZOTERO_TAG_INBOX", "inbox")
    monkeypatch.setattr("distillate.config.ZOTERO_TAG_READ", "read")
    monkeypatch.setattr("distillate.config.ZOTERO_COLLECTION_KEY", "")
    monkeypatch.setattr("distillate.config.RM_FOLDER_INBOX", "Distillate/Inbox")
    monkeypatch.setattr("distillate.config.RM_FOLDER_PAPERS", "Distillate")
    monkeypatch.setattr("distillate.config.KEEP_ZOTERO_PDF", True)
    monkeypatch.setattr("distillate.config.HTTP_TIMEOUT", 10)
    # Default flipped to zotero — test_import covers the legacy rM path.
    monkeypatch.setattr("distillate.config.READING_SOURCE", "remarkable")


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
            "distillate.integrations.remarkable.client.upload_pdf_bytes",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.sanitize_filename",
            lambda n: n,
        )
        monkeypatch.setattr(
            "distillate.obsidian.save_inbox_pdf",
            lambda *a, **kw: None,
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
            "distillate.integrations.remarkable.client.sanitize_filename",
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
            "distillate.integrations.remarkable.client.sanitize_filename",
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
            lambda limit=100, collection_key="": papers,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_library_version",
            lambda: 42,
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.ensure_folders",
            lambda: None,
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.list_folder",
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

        monkeypatch.setattr("distillate.pipeline._upload_paper", fake_upload)

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
            lambda limit=100, collection_key="": [],
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
            lambda limit=100, collection_key="": papers,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_library_version",
            lambda: 50,
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.ensure_folders",
            lambda: None,
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.list_folder",
            lambda f: [],
        )

        uploaded = []

        def fake_upload(paper, state, existing, skip_remarkable=False):
            uploaded.append(paper["key"])
            return True

        monkeypatch.setattr("distillate.pipeline._upload_paper", fake_upload)
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
            lambda limit=100, collection_key="": papers,
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
            lambda limit=100, collection_key="": papers,
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_library_version",
            lambda: 60,
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.ensure_folders",
            lambda: None,
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.list_folder",
            lambda f: [],
        )

        uploaded = []

        def fake_upload(paper, state, existing, skip_remarkable=False):
            uploaded.append(paper["key"])
            return True

        monkeypatch.setattr("distillate.pipeline._upload_paper", fake_upload)

        _import(["10"])

        # Should only import I8, not I7
        assert uploaded == ["I8"]
