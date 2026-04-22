# Covers: distillate/integrations/remarkable/renderer.py

import pytest


# ---------------------------------------------------------------------------
# _recover_pdf_text handles empty normalized search text
# ---------------------------------------------------------------------------

class TestRecoverPdfTextEmpty:
    def test_empty_search_returns_none(self):
        """Pure hyphens/spaces should return None, not crash."""
        from distillate.integrations.remarkable.renderer import _recover_pdf_text

        result = _recover_pdf_text("Some page text here", "- - -")
        assert result is None

    def test_empty_string_returns_none(self):
        """Empty string search should return None."""
        from distillate.integrations.remarkable.renderer import _recover_pdf_text

        result = _recover_pdf_text("Some page text here", "")
        assert result is None

    def test_whitespace_only_returns_none(self):
        """Whitespace-only search should return None."""
        from distillate.integrations.remarkable.renderer import _recover_pdf_text

        result = _recover_pdf_text("Some page text here", "   ")
        assert result is None

    def test_normal_search_still_works(self):
        """Normal text recovery still functions."""
        from distillate.integrations.remarkable.renderer import _recover_pdf_text

        result = _recover_pdf_text("The quick brown fox", "quick brown")
        assert result == "quick brown"


# ---------------------------------------------------------------------------
# Migrated from test_v032.py
# ---------------------------------------------------------------------------

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from rmscene import scene_items as si
from rmscene.tagged_block_common import CrdtId, LwwValue


def _make_rm_bundle_with_text():
    """Create a minimal .zip bundle with .content and .rm files for testing."""
    buf = io.BytesIO()
    content = {"cPages": {"pages": [{"id": "page-001"}]}}
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.content", json.dumps(content))
        zf.writestr("page-001.rm", b"\x00")
    buf.seek(0)
    return buf


class TestExtractTypedNotes:
    """extract_typed_notes() should parse Text items from .rm files."""

    @patch("distillate.integrations.remarkable.renderer._rmscene_loaded", True)
    @patch("distillate.integrations.remarkable.renderer.si", si)
    @patch("distillate.integrations.remarkable.renderer.read_tree")
    def test_extracts_plain_text(self, mock_read_tree, tmp_path):
        from distillate.integrations.remarkable.renderer import extract_typed_notes

        mock_tree = MagicMock()
        text_item = MagicMock(spec=si.Text)

        mock_para = MagicMock()
        mock_para.__str__ = lambda self: "My handwritten note"
        mock_para.style = LwwValue(CrdtId(0, 0), si.ParagraphStyle.PLAIN)
        mock_para.contents = []

        mock_tree.root_text = text_item
        mock_read_tree.return_value = mock_tree

        bundle = _make_rm_bundle_with_text()
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(bundle.read())

        with patch("rmscene.text.TextDocument") as mock_td_cls:
            mock_doc = MagicMock()
            mock_doc.contents = [mock_para]
            mock_td_cls.from_scene_item.return_value = mock_doc

            result = extract_typed_notes(zip_path)

        assert 0 in result
        assert "My handwritten note" in result[0]

    @patch("distillate.integrations.remarkable.renderer._rmscene_loaded", True)
    @patch("distillate.integrations.remarkable.renderer.si", si)
    @patch("distillate.integrations.remarkable.renderer.read_tree")
    def test_no_text_returns_empty(self, mock_read_tree, tmp_path):
        from distillate.integrations.remarkable.renderer import extract_typed_notes

        mock_tree = MagicMock()
        mock_tree.root_text = None
        mock_read_tree.return_value = mock_tree

        bundle = _make_rm_bundle_with_text()
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(bundle.read())

        result = extract_typed_notes(zip_path)
        assert result == {}

    @patch("distillate.integrations.remarkable.renderer._rmscene_loaded", True)
    @patch("distillate.integrations.remarkable.renderer.si", si)
    @patch("distillate.integrations.remarkable.renderer.read_tree")
    def test_heading_style(self, mock_read_tree, tmp_path):
        from distillate.integrations.remarkable.renderer import extract_typed_notes

        mock_tree = MagicMock()
        text_item = MagicMock(spec=si.Text)
        mock_tree.root_text = text_item
        mock_read_tree.return_value = mock_tree

        mock_para = MagicMock()
        mock_para.__str__ = lambda self: "Important Title"
        mock_para.style = LwwValue(CrdtId(0, 0), si.ParagraphStyle.HEADING)

        bundle = _make_rm_bundle_with_text()
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(bundle.read())

        with patch("rmscene.text.TextDocument") as mock_td_cls:
            mock_doc = MagicMock()
            mock_doc.contents = [mock_para]
            mock_td_cls.from_scene_item.return_value = mock_doc

            result = extract_typed_notes(zip_path)

        assert "### Important Title" in result[0]


class TestExtractInkStrokes:
    """extract_ink_strokes() should filter out highlighters and erasers."""

    @patch("distillate.integrations.remarkable.renderer._rmscene_loaded", True)
    @patch("distillate.integrations.remarkable.renderer.si", si)
    @patch("distillate.integrations.remarkable.renderer._ERASER_TOOLS", {si.Pen.ERASER, si.Pen.ERASER_AREA})
    @patch("distillate.integrations.remarkable.renderer.read_tree")
    def test_extracts_writing_strokes(self, mock_read_tree, tmp_path):
        from distillate.integrations.remarkable.renderer import extract_ink_strokes

        mock_tree = MagicMock()
        mock_tree.root_text = None

        writing_line = si.Line(
            color=si.PenColor.BLACK,
            tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=100, y=100, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=200, y=200, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0,
            starting_length=0,
        )
        highlighter_line = si.Line(
            color=si.PenColor.HIGHLIGHT,
            tool=si.Pen.HIGHLIGHTER_1,
            points=[
                si.Point(x=100, y=100, speed=0, direction=0, width=5, pressure=50),
                si.Point(x=300, y=100, speed=0, direction=0, width=5, pressure=50),
            ],
            thickness_scale=2.0,
            starting_length=0,
        )
        eraser_line = si.Line(
            color=si.PenColor.WHITE,
            tool=si.Pen.ERASER,
            points=[
                si.Point(x=50, y=50, speed=0, direction=0, width=10, pressure=50),
                si.Point(x=150, y=150, speed=0, direction=0, width=10, pressure=50),
            ],
            thickness_scale=3.0,
            starting_length=0,
        )

        mock_tree.walk.return_value = iter([writing_line, highlighter_line, eraser_line])
        mock_read_tree.return_value = mock_tree

        bundle = _make_rm_bundle_with_text()
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(bundle.read())

        result = extract_ink_strokes(zip_path)

        assert 0 in result
        assert len(result[0]) == 1  # only the writing stroke
        assert result[0][0].tool == si.Pen.BALLPOINT_1

    @patch("distillate.integrations.remarkable.renderer._rmscene_loaded", True)
    @patch("distillate.integrations.remarkable.renderer.si", si)
    @patch("distillate.integrations.remarkable.renderer._ERASER_TOOLS", {si.Pen.ERASER, si.Pen.ERASER_AREA})
    @patch("distillate.integrations.remarkable.renderer.read_tree")
    def test_no_strokes_returns_empty(self, mock_read_tree, tmp_path):
        from distillate.integrations.remarkable.renderer import extract_ink_strokes

        mock_tree = MagicMock()
        mock_tree.root_text = None
        mock_tree.walk.return_value = iter([])
        mock_read_tree.return_value = mock_tree

        bundle = _make_rm_bundle_with_text()
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(bundle.read())

        result = extract_ink_strokes(zip_path)
        assert result == {}


class TestOcrFallback:
    """ocr_handwritten_notes() should fail gracefully."""

    def test_no_api_key_returns_empty(self, monkeypatch):
        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "")
        from distillate.integrations.remarkable.renderer import ocr_handwritten_notes

        result = ocr_handwritten_notes(Path("fake.zip"))
        assert result == {}

    @patch("distillate.integrations.remarkable.renderer.extract_ink_strokes")
    def test_no_ink_returns_empty(self, mock_extract, monkeypatch):
        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "sk-test")
        from distillate.integrations.remarkable.renderer import ocr_handwritten_notes

        mock_extract.return_value = {}
        result = ocr_handwritten_notes(Path("fake.zip"))
        assert result == {}

    @patch("distillate.integrations.remarkable.renderer.extract_ink_strokes")
    def test_no_pdf_in_zip_returns_empty(self, mock_extract, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "sk-test")
        from distillate.integrations.remarkable.renderer import ocr_handwritten_notes

        mock_extract.return_value = {0: [MagicMock()]}
        zip_path = tmp_path / "bundle.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("content.rm", b"fake")

        result = ocr_handwritten_notes(zip_path)
        assert result == {}

    @patch("distillate.integrations.remarkable.renderer._ocr_page_claude")
    @patch("distillate.integrations.remarkable.renderer._render_ink_on_pdf")
    @patch("distillate.integrations.remarkable.renderer.extract_ink_strokes")
    def test_claude_ocr_result_included(self, mock_extract, mock_render, mock_ocr, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.config.ANTHROPIC_API_KEY", "sk-test")
        from distillate.integrations.remarkable.renderer import ocr_handwritten_notes
        import pymupdf

        pdf_doc = pymupdf.open()
        for _ in range(3):
            pdf_doc.new_page()
        pdf_bytes = pdf_doc.tobytes()
        pdf_doc.close()

        zip_path = tmp_path / "bundle.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("paper.pdf", pdf_bytes)

        mock_extract.return_value = {0: [MagicMock()], 2: [MagicMock()]}
        mock_ocr.side_effect = ["use REML!", ""]  # page 0 has text, page 2 empty

        result = ocr_handwritten_notes(zip_path)
        assert result == {0: "use REML!"}


class TestLoadRmPages:
    """_load_rm_pages() should parse page IDs and return sorted (idx, data)."""

    def test_loads_pages_from_bundle(self, tmp_path):
        from distillate.integrations.remarkable.renderer import _load_rm_pages

        content = {"cPages": {"pages": [{"id": "p1"}, {"id": "p2"}]}}
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("doc.content", json.dumps(content))
            zf.writestr("p1.rm", b"page1data")
            zf.writestr("p2.rm", b"page2data")

        result = _load_rm_pages(zip_path)
        assert len(result) == 2
        assert result[0] == (0, b"page1data")
        assert result[1] == (1, b"page2data")

    def test_empty_bundle(self, tmp_path):
        from distillate.integrations.remarkable.renderer import _load_rm_pages

        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("doc.content", json.dumps({"cPages": {"pages": []}}))

        result = _load_rm_pages(zip_path)
        assert result == []


# ---------------------------------------------------------------------------
# Migrated from test_highlight_io.py
# ---------------------------------------------------------------------------

pymupdf = pytest.importorskip("pymupdf")
from distillate import highlight_io


@pytest.fixture
def sample_pdf(tmp_path):
    """A 2-page PDF with some text, enough to host highlight annots."""
    doc = pymupdf.open()
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((72, 100), "Page 1 body text.", fontsize=12)
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((72, 100), "Page 2 body text.", fontsize=12)
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()
    return path


class TestConcurrentSaves:
    """Two rapid-fire saves to the same PDF file. PyMuPDF incremental
    saves are not file-system locked; two concurrent opens + writes
    can corrupt the document. This test documents current behaviour;
    if we add a per-file lock later, the test still passes."""

    def test_two_sequential_saves_coexist(self, sample_pdf):
        """Baseline: two back-to-back saves produce two annotations.
        Not actually concurrent — threading-level concurrency is out of
        scope for a unit test, but we pin the serial-save contract."""
        id_a = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 200.0, 712.0]], text="first",
        )
        id_b = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 650.0, 200.0, 662.0]], text="second",
        )
        assert id_a and id_b and id_a != id_b
        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 2


class TestMigrateLocalHighlights:

    def test_migrates_and_reports_count(self, sample_pdf):
        local = [
            {"page_index": 0, "rects": [[72.0, 700.0, 300.0, 712.0]],
             "text": "a", "color": "#ffd400"},
            {"page_index": 1, "rects": [[100.0, 500.0, 400.0, 520.0]],
             "text": "b", "color": "#ffd400"},
        ]
        migrated = highlight_io.migrate_local_highlights(sample_pdf, local)
        assert migrated == 2
        assert len(highlight_io.read_highlights(sample_pdf)) == 2

    def test_empty_input(self, sample_pdf):
        assert highlight_io.migrate_local_highlights(sample_pdf, []) == 0

    def test_missing_file(self, tmp_path):
        missing = tmp_path / "nope.pdf"
        assert highlight_io.migrate_local_highlights(missing, [{"rects": [[0, 0, 1, 1]]}]) == 0
