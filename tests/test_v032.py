"""Tests for v0.3.2 features: suggestion bug fix, tag abbreviations,
typed notes, ink extraction, and OCR fallback."""

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
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


class TestRmToPdfMapping:
    """_rm_to_pdf_mapping() should compute correct bestFit offset."""

    def test_letter_paper_y_offset(self):
        """US Letter (612x792) fills width, has vertical centering offset."""
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0)
        # PDF fills width → x_off ≈ 0
        assert abs(x_off) < 0.1
        # Vertical offset ≈ 27.5 (1872 - 792*2.294)/2
        assert 25 < y_off < 30
        # Scale ≈ 2.294
        assert abs(rm_scale - 1404.0 / 612.0) < 0.01

    def test_a4_paper_x_offset(self):
        """A4 (595x842) fills height, has horizontal centering offset."""
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(595.0, 842.0)
        # PDF fills height → y_off ≈ 0
        assert abs(y_off) < 0.1
        # Horizontal offset > 0 (page narrower than RM)
        assert x_off > 30
        # Scale = 1872/842 ≈ 2.223
        assert abs(rm_scale - 1872.0 / 842.0) < 0.01

    def test_square_page(self):
        """Square page should center in both directions."""
        from distillate.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(500.0, 500.0)
        # Fills width (1404/500=2.808 < 1872/500=3.744)
        assert abs(rm_scale - 1404.0 / 500.0) < 0.01
        assert abs(x_off) < 0.1  # fills width
        assert y_off > 0  # vertical padding


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
