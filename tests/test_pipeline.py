# Covers: distillate/pipeline.py, distillate/main.py

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests


@pytest.fixture()
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod
    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)
    return tmp_path


@pytest.fixture()
def sync_mocks(isolate_state, monkeypatch):
    """Provide all common mocks for run_sync tests.

    Uses sys.modules patching to intercept lazy imports inside run_sync().
    """
    import sys
    from distillate.state import STATE_PATH

    # Base empty state
    data = {
        "schema_version": 1,
        "zotero_library_version": 10,
        "last_poll_timestamp": None,
        "documents": {},
        "promoted_papers": [],
    }
    STATE_PATH.write_text(json.dumps(data))

    mocks = {}
    import distillate as _pkg
    modules_to_mock = [
        "config", "zotero_client", "obsidian", "renderer",
        "summarizer", "notify",
        "semantic_scholar", "huggingface",
    ]
    for mod_name in modules_to_mock:
        m = MagicMock()
        mocks[mod_name] = m
        # Patch both sys.modules AND the package attribute so lazy imports get the mock
        monkeypatch.setitem(sys.modules, f"distillate.{mod_name}", m)
        monkeypatch.setattr(_pkg, mod_name, m, raising=False)

    # reMarkable client lives at distillate.integrations.remarkable.client
    rm_mock = MagicMock()
    mocks["remarkable_client"] = rm_mock
    monkeypatch.setitem(sys.modules, "distillate.integrations.remarkable.client", rm_mock)

    # Default config values
    mocks["config"].is_zotero_reader.return_value = True
    mocks["config"].ZOTERO_TAG_INBOX = "inbox"
    mocks["config"].ZOTERO_TAG_READ = "read"
    mocks["config"].ZOTERO_COLLECTION_KEY = ""
    mocks["config"].SYNC_HIGHLIGHTS = False
    mocks["config"].KEEP_ZOTERO_PDF = True
    mocks["config"].STATE_GIST_ID = ""
    mocks["config"].EXPERIMENTS_ENABLED = False
    mocks["config"].setup_logging = MagicMock()
    mocks["config"].PDF_SUBFOLDER = "pdf"

    # Zotero client defaults
    mocks["zotero_client"].get_library_version.return_value = 10
    mocks["zotero_client"].get_changed_item_keys.return_value = ([], 10)
    mocks["zotero_client"].get_deleted_item_keys.return_value = []
    mocks["zotero_client"].filter_new_papers.return_value = []

    # Obsidian defaults
    mocks["obsidian"].ensure_dataview_note = MagicMock()
    mocks["obsidian"].ensure_stats_note = MagicMock()
    mocks["obsidian"].ensure_bases_note = MagicMock()
    mocks["obsidian"].migrate_pdfs_to_subdir.return_value = []

    # Cloud sync
    mock_cloud = MagicMock()
    mock_cloud.cloud_sync_available.return_value = False
    mock_cloud.push_state = MagicMock()
    monkeypatch.setitem(sys.modules, "distillate.cloud_sync", mock_cloud)
    monkeypatch.setattr(_pkg, "cloud_sync", mock_cloud, raising=False)

    # State module — keep real but with isolate_state already applied
    from distillate import state as state_mod
    monkeypatch.setattr(state_mod, "acquire_lock", lambda: True)
    monkeypatch.setattr(state_mod, "release_lock", lambda: None)

    return mocks


class TestFetchPdfBytes:
    @pytest.fixture(autouse=True)
    def _mock_zotero(self):
        self.mock_zc = MagicMock()
        import distillate
        with patch.object(distillate, "zotero_client", self.mock_zc, create=True), \
             patch.dict("sys.modules", {"distillate.zotero_client": self.mock_zc}):
            yield

    def test_zotero_cloud_success(self):
        from distillate.pipeline import _fetch_pdf_bytes
        self.mock_zc.download_pdf.return_value = b"%PDF-data"
        pdf_bytes, key = _fetch_pdf_bytes("ATT1", title="Test")
        assert pdf_bytes == b"%PDF-data"
        assert key == "ATT1"

    def test_webdav_fallback(self):
        from distillate.pipeline import _fetch_pdf_bytes
        resp = MagicMock()
        resp.status_code = 404
        self.mock_zc.download_pdf.side_effect = requests.exceptions.HTTPError(response=resp)
        self.mock_zc.download_pdf_from_webdav.return_value = b"%PDF-webdav"
        pdf_bytes, _ = _fetch_pdf_bytes("ATT1", title="Test")
        assert pdf_bytes == b"%PDF-webdav"

    def test_url_fallback(self):
        from distillate.pipeline import _fetch_pdf_bytes
        resp = MagicMock()
        resp.status_code = 404
        self.mock_zc.download_pdf.side_effect = requests.exceptions.HTTPError(response=resp)
        self.mock_zc.download_pdf_from_webdav.return_value = None
        self.mock_zc.download_pdf_from_url.return_value = b"%PDF-url"
        pdf_bytes, _ = _fetch_pdf_bytes("ATT1", paper_url="https://example.com/paper.pdf", title="Test")
        assert pdf_bytes == b"%PDF-url"

    def test_all_fallbacks_fail(self):
        from distillate.pipeline import _fetch_pdf_bytes
        resp = MagicMock()
        resp.status_code = 404
        self.mock_zc.download_pdf.side_effect = requests.exceptions.HTTPError(response=resp)
        self.mock_zc.download_pdf_from_webdav.return_value = None
        self.mock_zc.download_pdf_from_url.return_value = None
        pdf_bytes, _ = _fetch_pdf_bytes("ATT1", paper_url="", title="Test")
        assert pdf_bytes is None

    def test_fresh_attachment_check(self):
        from distillate.pipeline import _fetch_pdf_bytes
        resp = MagicMock()
        resp.status_code = 404
        self.mock_zc.download_pdf.side_effect = [
            requests.exceptions.HTTPError(response=resp),
            b"%PDF-fresh",
        ]
        self.mock_zc.get_pdf_attachment.return_value = {"key": "ATT2"}
        self.mock_zc.download_pdf_from_webdav.return_value = None
        pdf_bytes, key = _fetch_pdf_bytes(
            "ATT1", item_key="ITEM1", title="Test",
            check_fresh_attachment=True,
        )
        assert pdf_bytes == b"%PDF-fresh"
        assert key == "ATT2"

    def test_no_att_key(self):
        from distillate.pipeline import _fetch_pdf_bytes
        self.mock_zc.download_pdf_from_url.return_value = b"%PDF-url"
        pdf_bytes, key = _fetch_pdf_bytes(
            "", paper_url="https://example.com/paper.pdf", title="Test",
        )
        assert pdf_bytes == b"%PDF-url"
        assert key == ""


class TestProcessPaperBundle:
    @pytest.fixture(autouse=True)
    def _mock_deps(self):
        """Mock all lazy-imported modules used by _process_paper_bundle."""
        import distillate as _pkg
        # Ensure parent package is imported so we can attach the mock attribute.
        import distillate.integrations.remarkable as _rm_pkg
        self.mock_config = MagicMock()
        self.mock_obsidian = MagicMock()
        self.mock_renderer = MagicMock()
        self.mock_rm_renderer = MagicMock()
        self.mock_summarizer = MagicMock()
        self.mock_zc = MagicMock()
        self.mock_rm = MagicMock()

        mocks = {
            "distillate.config": self.mock_config,
            "distillate.obsidian": self.mock_obsidian,
            "distillate.renderer": self.mock_renderer,
            "distillate.summarizer": self.mock_summarizer,
            "distillate.zotero_client": self.mock_zc,
            "distillate.integrations.remarkable.client": self.mock_rm,
            "distillate.integrations.remarkable.renderer": self.mock_rm_renderer,
        }
        with patch.dict("sys.modules", mocks), \
             patch.object(_pkg, "config", self.mock_config, create=True), \
             patch.object(_pkg, "obsidian", self.mock_obsidian, create=True), \
             patch.object(_pkg, "renderer", self.mock_renderer, create=True), \
             patch.object(_pkg, "summarizer", self.mock_summarizer, create=True), \
             patch.object(_pkg, "zotero_client", self.mock_zc, create=True), \
             patch.object(_rm_pkg, "client", self.mock_rm, create=True), \
             patch.object(_rm_pkg, "renderer", self.mock_rm_renderer, create=True):
            # Default config
            self.mock_config.is_zotero_reader.return_value = True
            self.mock_config.SYNC_HIGHLIGHTS = False
            self.mock_config.ZOTERO_TAG_READ = "read"
            self.mock_config.RM_FOLDER_SAVED = "Saved"

            # Default returns
            self.mock_zc.download_pdf.return_value = b"%PDF-test"
            self.mock_zc.get_raw_annotations.return_value = []
            self.mock_zc.get_linked_attachment.return_value = None
            self.mock_zc.create_linked_attachment.return_value = None
            self.mock_zc.get_highlight_annotations.return_value = {1: ["A highlight"]}
            self.mock_zc.build_note_html.return_value = "<p>Note</p>"
            self.mock_zc.set_note.return_value = "NOTE1"
            self.mock_zc.get_pdf_attachment.return_value = None

            self.mock_renderer.render_annotated_pdf_from_annotations.return_value = False
            self.mock_summarizer.extract_insights.return_value = ["Insight"]
            self.mock_summarizer.summarize_read_paper.return_value = ("Summary", "One liner")
            self.mock_obsidian.save_annotated_pdf.return_value = None
            self.mock_obsidian.get_obsidian_uri.return_value = None
            yield

    def _make_doc(self, **overrides):
        doc = {
            "title": "Test Paper",
            "remarkable_doc_name": "test",
            "zotero_item_key": "KEY1",
            "zotero_attachment_key": "ATT1",
            "authors": ["Alice"],
            "status": "tracked",
            "uploaded_at": "2025-01-01T00:00:00",
            "processed_at": None,
            "metadata": {"citekey": "alice_test_2025", "tags": ["ML"],
                         "abstract": "Abstract", "hf_summary": "", "s2_tldr": ""},
        }
        doc.update(overrides)
        return doc

    def test_zotero_mode_success(self):
        from distillate.pipeline import _process_paper_bundle
        doc = self._make_doc()
        state = MagicMock()
        ok = _process_paper_bundle(doc, state)
        assert ok is True
        state.mark_processed.assert_called_once()
        state.save.assert_called_once()
        self.mock_obsidian.create_paper_note.assert_called_once()
        self.mock_zc.set_note.assert_called_once()

    def test_zotero_mode_no_pdf_returns_false(self):
        from distillate.pipeline import _process_paper_bundle
        self.mock_zc.download_pdf.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=404)
        )
        self.mock_zc.download_pdf_from_webdav.return_value = None
        self.mock_zc.download_pdf_from_url.return_value = None
        doc = self._make_doc()
        state = MagicMock()
        ok = _process_paper_bundle(doc, state)
        assert ok is False
        state.mark_processed.assert_not_called()

    def test_refresh_metadata_fetches_from_zotero(self):
        from distillate.pipeline import _process_paper_bundle
        self.mock_zc.get_items_by_keys.return_value = [
            {"key": "KEY1", "data": {"tags": [{"tag": "read"}]}}
        ]
        self.mock_zc.extract_metadata.return_value = {"citekey": "new_ck", "tags": ["AI"]}
        doc = self._make_doc()
        state = MagicMock()
        _process_paper_bundle(doc, state, refresh_metadata=True)
        self.mock_zc.get_items_by_keys.assert_called_once_with(["KEY1"])
        assert doc["metadata"] == {"citekey": "new_ck", "tags": ["AI"]}

    def test_ensure_read_tag_calls_add_tag(self):
        from distillate.pipeline import _process_paper_bundle
        doc = self._make_doc()
        state = MagicMock()
        _process_paper_bundle(doc, state, ensure_read_tag=True)
        self.mock_zc.add_tag.assert_called_once_with("KEY1", "read")

    def test_recreate_note_deletes_existing(self):
        from distillate.pipeline import _process_paper_bundle
        doc = self._make_doc()
        state = MagicMock()
        _process_paper_bundle(doc, state, recreate_note=True)
        self.mock_obsidian.ensure_dataview_note.assert_called_once()
        self.mock_obsidian.delete_paper_note.assert_called_once()

    def test_delete_inbox_pdf_flag(self):
        from distillate.pipeline import _process_paper_bundle
        doc = self._make_doc()
        state = MagicMock()
        _process_paper_bundle(doc, state, delete_inbox_pdf=True)
        self.mock_obsidian.delete_inbox_pdf.assert_called_once()

    def test_move_on_rm_flag(self):
        from distillate.pipeline import _process_paper_bundle
        self.mock_config.is_zotero_reader.return_value = False
        self.mock_rm.download_document_bundle_to.return_value = True
        self.mock_rm_renderer.extract_highlights.return_value = {1: ["h"]}
        self.mock_rm_renderer.extract_typed_notes.return_value = {}
        self.mock_rm_renderer.ocr_handwritten_notes.return_value = {}
        self.mock_rm_renderer.get_page_count.return_value = 5
        self.mock_rm_renderer.render_annotated_pdf.return_value = False
        self.mock_rm.download_annotated_pdf_to.return_value = False
        doc = self._make_doc()
        state = MagicMock()
        # We need the zip_path to exist for the test
        with patch("distillate.pipeline.Path") as mock_path_cls:
            # Let tempdir work normally but mock zip existence
            mock_path_cls.side_effect = Path
            _process_paper_bundle(
                doc, state,
                rm_folder="Read",
                move_on_rm=True,
                use_rm_geta_fallback=True,
            )
        self.mock_rm.move_document.assert_called_once_with(
            "test", "Read", "Saved",
        )

    def test_state_updated_with_highlight_stats(self):
        from distillate.pipeline import _process_paper_bundle
        self.mock_zc.get_highlight_annotations.return_value = {
            1: ["word1 word2", "word3"],
            3: ["word4 word5 word6"],
        }
        doc = self._make_doc()
        state = MagicMock()
        _process_paper_bundle(doc, state)
        assert doc["highlight_count"] == 3
        assert doc["highlighted_pages"] == 2
        assert doc["highlight_word_count"] == 6
        assert doc["engagement"] > 0


class TestReport:
    def _make_state_with_papers(self, isolate_state):
        from distillate.state import STATE_PATH
        papers = {}
        for i in range(5):
            key = f"KEY{i}"
            papers[key] = {
                "zotero_item_key": key,
                "title": f"Paper {i}",
                "authors": ["Alice", "Bob"] if i < 3 else ["Charlie"],
                "status": "processed",
                "page_count": 10 + i,
                "highlight_word_count": 100 + i * 50,
                "engagement": 20 + i * 20,
                "uploaded_at": f"2025-01-0{i+1}T00:00:00",
                "processed_at": f"2025-01-1{i}T00:00:00",
                "metadata": {
                    "tags": ["ML", "NLP"] if i < 3 else ["CV"],
                    "citation_count": (5 - i) * 100,
                    "citekey": f"author_paper{i}_2025",
                },
            }
        data = {
            "schema_version": 1,
            "zotero_library_version": 1,
            "last_poll_timestamp": None,
            "documents": papers,
            "promoted_papers": [],
        }
        STATE_PATH.write_text(json.dumps(data))

    def test_report_outputs_sections(self, isolate_state, capsys):
        self._make_state_with_papers(isolate_state)
        from distillate.commands import _report
        _report()
        out = capsys.readouterr().out
        assert "Reading Report" in out
        assert "Lifetime" in out
        assert "papers" in out
        assert "Engagement Distribution" in out

    def test_report_shows_topics(self, isolate_state, capsys):
        self._make_state_with_papers(isolate_state)
        from distillate.commands import _report
        _report()
        out = capsys.readouterr().out
        assert "Top Topics" in out
        assert "ML" in out

    def test_report_shows_cited_papers(self, isolate_state, capsys):
        self._make_state_with_papers(isolate_state)
        from distillate.commands import _report
        _report()
        out = capsys.readouterr().out
        assert "Most-Cited" in out
        assert "citations" in out

    def test_report_no_papers(self, isolate_state, capsys):
        from distillate.state import STATE_PATH
        STATE_PATH.write_text(json.dumps({
            "schema_version": 1,
            "documents": {},
            "promoted_papers": [],
        }))
        from distillate.commands import _report
        _report()
        out = capsys.readouterr().out
        assert "No processed papers" in out


class TestSyncIntegration:
    def test_no_changes_clean_exit(self, sync_mocks, capsys):
        from distillate.pipeline import run_sync
        run_sync()
        out = capsys.readouterr().out
        assert "Nothing to do" in out

    def test_lock_held_exits(self, sync_mocks, monkeypatch, capsys):
        from distillate.pipeline import run_sync
        from distillate import state as state_mod
        monkeypatch.setattr(state_mod, "acquire_lock", lambda: False)
        run_sync()
        out = capsys.readouterr().out
        assert "Nothing to do" not in out

    def test_zotero_reader_processes_read_paper(self, sync_mocks, isolate_state, capsys, monkeypatch):
        """Zotero reader mode: tracked paper with read tag gets processed."""
        from distillate.state import STATE_PATH, State
        from distillate import config as real_config
        from distillate.pipeline import run_sync

        mc = sync_mocks

        # Force Zotero reader mode on the real config module
        monkeypatch.setattr(real_config, "READING_SOURCE", "zotero")
        monkeypatch.setattr(real_config, "ZOTERO_TAG_INBOX", "inbox")
        monkeypatch.setattr(real_config, "ZOTERO_TAG_READ", "read")
        monkeypatch.setattr(real_config, "SYNC_HIGHLIGHTS", False)
        monkeypatch.setattr(real_config, "KEEP_ZOTERO_PDF", True)
        monkeypatch.setattr(real_config, "STATE_GIST_ID", "")
        monkeypatch.setattr(real_config, "EXPERIMENTS_ENABLED", False)
        monkeypatch.setattr(real_config, "ZOTERO_COLLECTION_KEY", "")
        monkeypatch.setattr(real_config, "PDF_SUBFOLDER", "pdf")

        # Set up state with a tracked paper
        data = {
            "schema_version": 1,
            "zotero_library_version": 10,
            "last_poll_timestamp": None,
            "documents": {
                "TRACK1": {
                    "zotero_item_key": "TRACK1",
                    "zotero_attachment_key": "ATT1",
                    "zotero_attachment_md5": "",
                    "remarkable_doc_name": "test",
                    "title": "Tracked Paper",
                    "authors": ["Alice"],
                    "status": "tracked",
                    "metadata": {"citekey": "alice_tracked_2025", "tags": ["ML"],
                                 "abstract": "Test abstract", "hf_summary": "",
                                 "s2_tldr": ""},
                    "uploaded_at": "2025-01-01T00:00:00",
                    "processed_at": None,
                }
            },
            "promoted_papers": [],
        }
        STATE_PATH.write_text(json.dumps(data))

        # This paper has the "read" tag
        mc["zotero_client"].get_items_by_keys.return_value = [
            {"key": "TRACK1", "data": {"tags": [{"tag": "read"}]}},
        ]
        mc["zotero_client"].get_library_version.return_value = 10
        mc["zotero_client"].get_highlight_annotations.return_value = {1: ["A highlight"]}
        mc["zotero_client"].get_raw_annotations.return_value = []
        mc["zotero_client"].download_pdf.return_value = b"%PDF-test"
        mc["zotero_client"].get_linked_attachment.return_value = None
        mc["zotero_client"].build_note_html.return_value = "<p>Note</p>"
        mc["zotero_client"].set_note.return_value = "NOTE1"
        mc["zotero_client"].get_pdf_attachment.return_value = None
        mc["zotero_client"].replace_tag = MagicMock()

        mc["renderer"].render_annotated_pdf_from_annotations.return_value = False

        mc["summarizer"].extract_insights.return_value = ["Insight 1"]
        mc["summarizer"].summarize_read_paper.return_value = ("Summary", "One liner")

        mc["obsidian"].save_annotated_pdf.return_value = None
        mc["obsidian"].get_obsidian_uri.return_value = None
        mc["obsidian"].delete_inbox_pdf = MagicMock()
        mc["obsidian"].create_paper_note = MagicMock()
        mc["obsidian"].append_to_reading_log = MagicMock()

        run_sync()

        state = State()
        doc = state.get_document("TRACK1")
        assert doc["status"] == "processed"
