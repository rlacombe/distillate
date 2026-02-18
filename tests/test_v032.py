"""Tests for v0.3.2 features: suggestion bug fix, tag abbreviations,
typed notes, ink extraction, and OCR fallback."""

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from rmscene import scene_items as si
from rmscene.tagged_block_common import CrdtId, LwwValue
from rmscene.crdt_sequence import CrdtSequence, CrdtSequenceItem


# ---------------------------------------------------------------------------
# Suggestion email: already-read paper detection
# ---------------------------------------------------------------------------


class TestSyncTagsMarkRead:
    """_sync_tags() should mark papers as processed when Zotero tag is 'read'."""

    def _make_state_and_items(self, zotero_tags, doc_status="on_remarkable"):
        """Helper to build mock state and Zotero items for _sync_tags tests."""
        state = MagicMock()
        state.zotero_library_version = 0
        state.has_document.return_value = True
        doc = {
            "title": "Test Paper",
            "status": doc_status,
            "authors": ["Author"],
            "metadata": {},
        }
        state.get_document.return_value = doc
        state.save = MagicMock()

        item = {
            "key": "ABC123",
            "data": {
                "title": "Test Paper",
                "tags": [{"tag": t} for t in zotero_tags],
                "creators": [{"creatorType": "author", "lastName": "Author"}],
                "date": "2024",
            },
        }
        return state, doc, item

    def _run_sync_tags(self, state, item, monkeypatch):
        """Run _sync_tags with mocked zotero_client."""
        monkeypatch.setenv("ZOTERO_LIBRARY_ID", "123")
        monkeypatch.setenv("ZOTERO_API_KEY", "key")

        mock_zc = MagicMock()
        mock_zc.get_library_version.return_value = 1
        mock_zc.get_changed_item_keys.return_value = (["ABC123"], 1)
        mock_zc.get_items_by_keys.return_value = [item]
        mock_zc.extract_metadata.return_value = {"authors": ["Author"], "tags": []}

        with patch("distillate.zotero_client.get_library_version", mock_zc.get_library_version), \
             patch("distillate.zotero_client.get_changed_item_keys", mock_zc.get_changed_item_keys), \
             patch("distillate.zotero_client.get_items_by_keys", mock_zc.get_items_by_keys), \
             patch("distillate.zotero_client.extract_metadata", mock_zc.extract_metadata):
            from distillate.digest import _sync_tags
            _sync_tags(state)

    def test_marks_read_paper_as_processed(self, monkeypatch):
        state, doc, item = self._make_state_and_items(["read"])
        self._run_sync_tags(state, item, monkeypatch)

        assert doc["status"] == "processed"
        assert "processed_at" in doc

    def test_ignores_inbox_tag(self, monkeypatch):
        state, doc, item = self._make_state_and_items(["inbox"])
        self._run_sync_tags(state, item, monkeypatch)

        assert doc["status"] == "on_remarkable"

    def test_skips_already_processed(self, monkeypatch):
        state, doc, item = self._make_state_and_items(["read"], doc_status="processed")
        self._run_sync_tags(state, item, monkeypatch)

        # Should not have set processed_at since it was already processed
        assert "processed_at" not in doc


# ---------------------------------------------------------------------------
# Tag pill abbreviations
# ---------------------------------------------------------------------------


class TestAbbreviateTag:
    """_abbreviate_tag() should produce short human-readable labels."""

    def test_arxiv_cs_to_words(self):
        from distillate.digest import _abbreviate_tag
        assert _abbreviate_tag("Computer Science - Artificial Intelligence") == "AI"
        assert _abbreviate_tag("Computer Science - Machine Learning") == "ML"
        assert _abbreviate_tag("Computer Science - Computation and Language") == "NLP"
        assert _abbreviate_tag("Computer Science - Computer Vision and Pattern Recognition") == "Vision"

    def test_arxiv_qbio_to_words(self):
        from distillate.digest import _abbreviate_tag
        assert _abbreviate_tag("Quantitative Biology - Genomics") == "Genomics"
        assert _abbreviate_tag("Quantitative Biology - Biomolecules") == "Biomolecules"

    def test_arxiv_stat(self):
        from distillate.digest import _abbreviate_tag
        assert _abbreviate_tag("Statistics - Machine Learning") == "StatML"

    def test_s2_broad_fields(self):
        from distillate.digest import _abbreviate_tag
        assert _abbreviate_tag("Computer Science") == "CS"
        assert _abbreviate_tag("Materials Science") == "MatSci"

    def test_common_research_tags(self):
        from distillate.digest import _abbreviate_tag
        assert _abbreviate_tag("reinforcement learning") == "RL"
        assert _abbreviate_tag("Gene regulatory networks") == "GRN"
        assert _abbreviate_tag("Computational biology and bioinformatics") == "CompBio"

    def test_short_tag_passthrough(self):
        from distillate.digest import _abbreviate_tag
        assert _abbreviate_tag("Biology") == "Biology"
        assert _abbreviate_tag("NLP") == "NLP"
        assert _abbreviate_tag("Medicine") == "Medicine"

    def test_no_dots_in_abbreviations(self):
        """Abbreviated tags should never contain dots (look like links)."""
        from distillate.digest import _TAG_ABBREV
        for tag, abbrev in _TAG_ABBREV.items():
            assert "." not in abbrev, f"{tag} -> {abbrev} contains a dot"


class TestTagPillsHtml:
    """_tag_pills_html() should render abbreviated, smaller pills."""

    def test_renders_abbreviated_arxiv(self):
        from distillate.digest import _tag_pills_html
        html = _tag_pills_html(["Computer Science - Machine Learning"])
        assert "ML" in html
        assert "cs.LG" not in html  # no dotted codes
        assert "font-size:10px" in html

    def test_empty_tags(self):
        from distillate.digest import _tag_pills_html
        assert _tag_pills_html([]) == ""

    def test_max_3_tags(self):
        from distillate.digest import _tag_pills_html
        tags = ["tag1", "tag2", "tag3", "tag4", "tag5"]
        html = _tag_pills_html(tags)
        assert "tag1" in html
        assert "tag3" in html
        assert "tag4" not in html


class TestRankTags:
    """_rank_tags() should sort by user reading frequency."""

    def test_ranks_by_frequency(self):
        from distillate.digest import _rank_tags
        user_top = ["ML", "NLP", "CV"]
        tags = ["CV", "NLP", "Robotics"]
        result = _rank_tags(tags, user_top)
        assert result == ["NLP", "CV", "Robotics"]

    def test_empty_user_top(self):
        from distillate.digest import _rank_tags
        assert _rank_tags(["a", "b"], []) == ["a", "b"]


class TestS2FieldsOfStudy:
    """enrich_metadata() should populate tags from S2 fieldsOfStudy."""

    def test_fills_empty_tags(self):
        from distillate.semantic_scholar import enrich_metadata
        meta = {"tags": []}
        s2 = {
            "citation_count": 10, "influential_citation_count": 1,
            "s2_url": "", "fields_of_study": ["Computer Science", "Medicine"],
        }
        result = enrich_metadata(meta, s2)
        assert result["tags"] == ["Computer Science", "Medicine"]

    def test_merges_into_existing_tags(self):
        from distillate.semantic_scholar import enrich_metadata
        meta = {"tags": ["existing-tag"]}
        s2 = {
            "citation_count": 10, "influential_citation_count": 1,
            "s2_url": "", "fields_of_study": ["Physics"],
        }
        result = enrich_metadata(meta, s2)
        assert "existing-tag" in result["tags"]
        assert "Physics" in result["tags"]

    def test_no_duplicate_merge(self):
        from distillate.semantic_scholar import enrich_metadata
        meta = {"tags": ["Biology"]}
        s2 = {
            "citation_count": 5, "influential_citation_count": 0,
            "s2_url": "", "fields_of_study": ["Biology", "Medicine"],
        }
        result = enrich_metadata(meta, s2)
        assert result["tags"].count("Biology") == 1
        assert "Medicine" in result["tags"]


# ---------------------------------------------------------------------------
# Typed notes extraction
# ---------------------------------------------------------------------------


def _make_rm_bundle_with_text(typed_text_items=None, glyphs=None):
    """Create a minimal .zip bundle with .content and .rm files for testing.

    This creates a valid zip that can be parsed by renderer functions.
    Note: Since rmscene write API requires specific block construction,
    we test at the function level by mocking read_tree.
    """
    buf = io.BytesIO()
    content = {"cPages": {"pages": [{"id": "page-001"}]}}
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.content", json.dumps(content))
        # Write a minimal .rm file (empty — we'll mock read_tree)
        zf.writestr("page-001.rm", b"\x00")
    buf.seek(0)
    return buf


class TestExtractTypedNotes:
    """extract_typed_notes() should parse Text items from .rm files."""

    @patch("distillate.renderer.read_tree")
    def test_extracts_plain_text(self, mock_read_tree, tmp_path):
        from distillate.renderer import extract_typed_notes

        # Build a mock scene tree with a Text item
        mock_tree = MagicMock()
        text_item = MagicMock(spec=si.Text)

        # Mock TextDocument parsing
        mock_para = MagicMock()
        mock_para.__str__ = lambda self: "My handwritten note"
        mock_para.style = LwwValue(CrdtId(0, 0), si.ParagraphStyle.PLAIN)
        mock_para.contents = []

        mock_tree.root_text = text_item
        mock_read_tree.return_value = mock_tree

        # Write bundle to tmp
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

    @patch("distillate.renderer.read_tree")
    def test_no_text_returns_empty(self, mock_read_tree, tmp_path):
        from distillate.renderer import extract_typed_notes

        mock_tree = MagicMock()
        mock_tree.root_text = None
        mock_read_tree.return_value = mock_tree

        bundle = _make_rm_bundle_with_text()
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(bundle.read())

        result = extract_typed_notes(zip_path)
        assert result == {}

    @patch("distillate.renderer.read_tree")
    def test_heading_style(self, mock_read_tree, tmp_path):
        from distillate.renderer import extract_typed_notes

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


# ---------------------------------------------------------------------------
# Ink stroke extraction
# ---------------------------------------------------------------------------


class TestExtractInkStrokes:
    """extract_ink_strokes() should filter out highlighters and erasers."""

    @patch("distillate.renderer.read_tree")
    def test_extracts_writing_strokes(self, mock_read_tree, tmp_path):
        from distillate.renderer import extract_ink_strokes

        mock_tree = MagicMock()
        mock_tree.root_text = None

        # Create mock Line items
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

    @patch("distillate.renderer.read_tree")
    def test_no_strokes_returns_empty(self, mock_read_tree, tmp_path):
        from distillate.renderer import extract_ink_strokes

        mock_tree = MagicMock()
        mock_tree.root_text = None
        mock_tree.walk.return_value = iter([])
        mock_read_tree.return_value = mock_tree

        bundle = _make_rm_bundle_with_text()
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(bundle.read())

        result = extract_ink_strokes(zip_path)
        assert result == {}


# ---------------------------------------------------------------------------
# RM → PDF coordinate mapping
# ---------------------------------------------------------------------------


class TestIsPaperProCoords:
    """_is_paper_pro_coords() should detect Paper Pro coordinate space."""

    def test_classic_rm_coordinates(self):
        from distillate.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=100, y=200, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=500, y=800, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        assert _is_paper_pro_coords({0: [line]}) is False

    def test_classic_rm_near_viewport_edge(self):
        """Strokes near the classic viewport boundary should NOT trigger Pro."""
        from distillate.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=-50, y=1850, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=1400, y=1870, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        # Slightly negative x and y near 1872 — still classic RM
        assert _is_paper_pro_coords({0: [line]}) is False

    def test_classic_rm_scrolled_below_page(self):
        """Classic RM user scrolled below page (y~2000) should NOT trigger."""
        from distillate.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=300, y=1950, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=600, y=2050, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        # y up to 2050 is within scroll range, NOT Paper Pro
        assert _is_paper_pro_coords({0: [line]}) is False

    def test_paper_pro_negative_x(self):
        from distillate.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=-742, y=200, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=-500, y=800, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        assert _is_paper_pro_coords({0: [line]}) is True

    def test_paper_pro_high_y(self):
        from distillate.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=100, y=200, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=500, y=2400, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        assert _is_paper_pro_coords({0: [line]}) is True

    def test_empty_ink(self):
        from distillate.renderer import _is_paper_pro_coords

        assert _is_paper_pro_coords({}) is False


class TestRmToPdfMapping:
    """_rm_to_pdf_mapping() should compute correct coordinate mapping."""

    def test_classic_letter_y_offset(self):
        """Classic RM: US Letter fills width, has vertical centering offset."""
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=False)
        assert abs(x_off) < 0.1
        assert 25 < y_off < 30
        assert abs(rm_scale - 1404.0 / 612.0) < 0.01

    def test_classic_a4_x_offset(self):
        """Classic RM: A4 fills height, has horizontal centering offset."""
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(595.0, 842.0, paper_pro=False)
        assert abs(y_off) < 0.1
        assert x_off > 30
        assert abs(rm_scale - 1872.0 / 842.0) < 0.01

    def test_classic_maps_center_to_center(self):
        """Classic RM: center of viewport maps to center of page."""
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=False)
        # RM viewport center = (702, 936)
        pdf_x = (702 - x_off) / rm_scale
        pdf_y = (936 - y_off) / rm_scale
        # Should map to approximately center of PDF (306, 396)
        assert abs(pdf_x - 306) < 1
        assert abs(pdf_y - 396) < 1

    def test_paper_pro_scale(self):
        """Paper Pro uses 227 DPI native coordinates."""
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=True)
        assert abs(rm_scale - 227.0 / 72.0) < 0.01
        assert abs(x_off - (-612.0 * rm_scale / 2)) < 0.1
        assert abs(y_off) < 0.1

    def test_paper_pro_maps_origin_to_center_x(self):
        """Paper Pro: rm_x=0 maps to pdf_x=pdf_w/2 (PDF centered at x=0)."""
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=True)
        pdf_x = (0 - x_off) / rm_scale
        assert abs(pdf_x - 306) < 2  # center of 612pt page

    def test_paper_pro_coordinate_accuracy(self):
        """Paper Pro mapping should place ink within 2pt of correct position.

        Calibrated from 54 highlight positions on an actual Paper Pro document.
        """
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=True)
        # Known calibration points (rm_y → expected pdf_y)
        calibration = [
            (232, 73.2), (333, 105.3), (362, 114.4),
            (1082, 342.9), (1324, 419.7), (1454, 461.0),
        ]
        for rm_y, expected_pdf_y in calibration:
            pdf_y = (rm_y - y_off) / rm_scale
            assert abs(pdf_y - expected_pdf_y) < 2.0, (
                f"rm_y={rm_y}: got {pdf_y:.1f}, expected {expected_pdf_y}"
            )

    def test_paper_pro_a4(self):
        """Paper Pro mapping works for A4 pages too."""
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(595.0, 842.0, paper_pro=True)
        # Same scale regardless of page size
        assert abs(rm_scale - 227.0 / 72.0) < 0.01
        # x_off shifts for narrower page
        assert abs(x_off - (-595.0 * rm_scale / 2)) < 0.1

    def test_classic_and_pro_produce_different_results(self):
        """The two mappings give meaningfully different positions."""
        from distillate.renderer import _rm_to_pdf_mapping

        classic = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=False)
        pro = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=True)
        # Scales should be very different
        assert classic[0] < 2.5  # ~2.29
        assert pro[0] > 3.0     # ~3.15


# ---------------------------------------------------------------------------
# OCR graceful fallback
# ---------------------------------------------------------------------------


class TestOcrFallback:
    """ocr_handwritten_notes() should fail gracefully without Pillow/Vision."""

    @patch("distillate.renderer.extract_ink_strokes")
    def test_no_ink_returns_empty(self, mock_extract):
        from distillate.renderer import ocr_handwritten_notes

        mock_extract.return_value = {}
        result = ocr_handwritten_notes(Path("fake.zip"))
        assert result == {}

    @patch("distillate.renderer._render_strokes_to_image")
    @patch("distillate.renderer.extract_ink_strokes")
    def test_no_pillow_returns_empty(self, mock_extract, mock_render):
        from distillate.renderer import ocr_handwritten_notes

        mock_extract.return_value = {0: [MagicMock()]}
        mock_render.return_value = None  # Pillow not available

        result = ocr_handwritten_notes(Path("fake.zip"))
        assert result == {}


# ---------------------------------------------------------------------------
# Obsidian note with typed + handwritten notes
# ---------------------------------------------------------------------------


class TestPaperNoteWithNotes:
    """create_paper_note() should include typed and handwritten note sections."""

    def test_typed_notes_section(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
        monkeypatch.delenv("OBSIDIAN_VAULT_NAME", raising=False)

        from distillate import obsidian

        note_path = obsidian.create_paper_note(
            title="Test Paper",
            authors=["Author"],
            date_added="2024-01-01",
            zotero_item_key="KEY1",
            typed_notes={0: "My typed note on page 1", 2: "Note on page 3"},
        )

        assert note_path is not None
        content = note_path.read_text()
        assert "## Notes from reMarkable" in content
        assert "### Page 1" in content
        assert "My typed note on page 1" in content
        assert "### Page 3" in content
        assert "Note on page 3" in content

    def test_handwritten_notes_section(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
        monkeypatch.delenv("OBSIDIAN_VAULT_NAME", raising=False)

        from distillate import obsidian

        note_path = obsidian.create_paper_note(
            title="Test Paper 2",
            authors=["Author"],
            date_added="2024-01-01",
            zotero_item_key="KEY2",
            handwritten_notes={1: "OCR'd handwriting from page 2"},
        )

        assert note_path is not None
        content = note_path.read_text()
        assert "## Handwritten Notes" in content
        assert "### Page 2" in content
        assert "OCR'd handwriting from page 2" in content

    def test_no_notes_no_section(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OUTPUT_PATH", str(tmp_path))
        monkeypatch.delenv("OBSIDIAN_VAULT_NAME", raising=False)

        from distillate import obsidian

        note_path = obsidian.create_paper_note(
            title="Test Paper 3",
            authors=["Author"],
            date_added="2024-01-01",
            zotero_item_key="KEY3",
        )

        assert note_path is not None
        content = note_path.read_text()
        assert "## Notes from reMarkable" not in content
        assert "## Handwritten Notes" not in content


# ---------------------------------------------------------------------------
# --dry-run removal
# ---------------------------------------------------------------------------


class TestDryRunRemoved:
    """--dry-run should no longer be recognized."""

    def test_dry_run_not_in_known_flags(self):
        from distillate.main import _KNOWN_FLAGS
        assert "--dry-run" not in _KNOWN_FLAGS


# ---------------------------------------------------------------------------
# _load_rm_pages helper
# ---------------------------------------------------------------------------


class TestLoadRmPages:
    """_load_rm_pages() should parse page IDs and return sorted (idx, data)."""

    def test_loads_pages_from_bundle(self, tmp_path):
        from distillate.renderer import _load_rm_pages

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
        from distillate.renderer import _load_rm_pages

        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("doc.content", json.dumps({"cPages": {"pages": []}}))

        result = _load_rm_pages(zip_path)
        assert result == []


# ---------------------------------------------------------------------------
# Ink color and pen filtering
# ---------------------------------------------------------------------------


class TestPenColorMap:
    """Verify pen color mapping covers all common colors."""

    def test_all_basic_colors_mapped(self):
        from distillate.renderer import _PEN_COLOR_MAP
        for color in [si.PenColor.BLACK, si.PenColor.GRAY, si.PenColor.BLUE,
                      si.PenColor.RED, si.PenColor.GREEN]:
            assert color in _PEN_COLOR_MAP


class TestEraserFiltering:
    """Verify eraser tools are in the exclusion set."""

    def test_erasers_excluded(self):
        from distillate.renderer import _ERASER_TOOLS
        assert si.Pen.ERASER in _ERASER_TOOLS
        assert si.Pen.ERASER_AREA in _ERASER_TOOLS


# ---------------------------------------------------------------------------
# Windows rmapi path leak fix
# ---------------------------------------------------------------------------


class TestUploadPathLeak:
    """upload_pdf_bytes() should rename docs when rmapi uses temp path."""

    @patch("distillate.remarkable_client._run")
    @patch("distillate.remarkable_client.list_folder")
    def test_no_rename_when_name_correct(self, mock_list, mock_run):
        """Normal case: doc name is correct, no rename needed."""
        from distillate.remarkable_client import upload_pdf_bytes

        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_list.return_value = ["My Paper"]

        upload_pdf_bytes(b"%PDF-1.4 test", "Distillate/Inbox", "My Paper")

        # _run called once for put, never for mv
        assert mock_run.call_count == 1
        assert mock_run.call_args_list[0][0][0][0] == "put"

    @patch("distillate.remarkable_client._run")
    @patch("distillate.remarkable_client.list_folder")
    def test_rename_when_temp_path_leaked(self, mock_list, mock_run):
        r"""Windows path leak: doc named 'C:\Users\...\tmpXXXX\My Paper'."""
        from distillate.remarkable_client import upload_pdf_bytes

        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_list.return_value = [r"C:\Users\foo\Temp\tmp123\My Paper"]

        upload_pdf_bytes(b"%PDF-1.4 test", "Distillate/Inbox", "My Paper")

        # _run called twice: put + mv
        assert mock_run.call_count == 2
        mv_args = mock_run.call_args_list[1][0][0]
        assert mv_args[0] == "mv"
        assert mv_args[1] == r"/Distillate/Inbox/C:\Users\foo\Temp\tmp123\My Paper"
        assert mv_args[2] == "/Distillate/Inbox/My Paper"

    @patch("distillate.remarkable_client._run")
    @patch("distillate.remarkable_client.list_folder")
    def test_skip_when_already_exists(self, mock_list, mock_run):
        """Already-existing doc should skip without rename."""
        from distillate.remarkable_client import upload_pdf_bytes

        mock_run.return_value = MagicMock(
            returncode=1, stderr="entry already exists",
        )

        upload_pdf_bytes(b"%PDF-1.4 test", "Distillate/Inbox", "My Paper")

        # Only the put call, no list_folder or mv
        assert mock_run.call_count == 1
        mock_list.assert_not_called()


# ---------------------------------------------------------------------------
# Year extraction from date strings
# ---------------------------------------------------------------------------


class TestExtractYear:
    """_extract_year() should handle all Zotero date formats."""

    def test_iso_date(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("2024-10-15") == "2024"

    def test_day_month_year(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("8 September 2024") == "2024"

    def test_month_year(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("10/2024") == "2024"

    def test_year_only(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("2024") == "2024"

    def test_empty(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("") == ""

    def test_none(self):
        from distillate.obsidian import _extract_year
        assert _extract_year(None) == ""

    def test_no_year(self):
        from distillate.obsidian import _extract_year
        assert _extract_year("in press") == ""


# ---------------------------------------------------------------------------
# WebDAV PDF download fallback
# ---------------------------------------------------------------------------


class TestDownloadPdfFromWebdav:
    """download_pdf_from_webdav() should fetch PDFs from WebDAV storage."""

    def test_no_webdav_url_returns_none(self, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "")
        from distillate.zotero_client import download_pdf_from_webdav

        assert download_pdf_from_webdav("ABC123") is None

    @patch("distillate.zotero_client.requests.get")
    def test_downloads_pdf_from_zip(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        # Build a zip containing a PDF
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("paper.pdf", b"%PDF-1.4 fake content")
        zip_bytes = buf.getvalue()

        mock_resp = MagicMock(status_code=200, content=zip_bytes)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = download_pdf_from_webdav("XYZ789")
        assert result == b"%PDF-1.4 fake content"

        # Verify correct URL and auth
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert "zotero/XYZ789.zip" in call_kwargs[0][0] or "zotero/XYZ789.zip" in str(call_kwargs)
        assert call_kwargs[1]["auth"] == ("user", "pass")

    @patch("distillate.zotero_client.requests.get")
    def test_404_returns_none(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        mock_resp = MagicMock(status_code=404)
        mock_get.return_value = mock_resp

        assert download_pdf_from_webdav("MISSING") is None

    @patch("distillate.zotero_client.requests.get")
    def test_bad_zip_returns_none(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        mock_resp = MagicMock(status_code=200, content=b"not a zip")
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        assert download_pdf_from_webdav("BADZIP") is None

    @patch("distillate.zotero_client.requests.get")
    def test_zip_without_pdf_returns_none(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("notes.txt", b"just notes")
        zip_bytes = buf.getvalue()

        mock_resp = MagicMock(status_code=200, content=zip_bytes)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        assert download_pdf_from_webdav("NOPDF") is None

    @patch("distillate.zotero_client.requests.get")
    def test_connection_error_returns_none(self, mock_get, monkeypatch):
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_URL", "https://dav.example.com")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_USERNAME", "user")
        monkeypatch.setattr("distillate.config.ZOTERO_WEBDAV_PASSWORD", "pass")
        from distillate.zotero_client import download_pdf_from_webdav

        mock_get.side_effect = requests.exceptions.ConnectionError("refused")

        assert download_pdf_from_webdav("OFFLINE") is None
