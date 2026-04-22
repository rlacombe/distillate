# Covers: distillate/highlight_io.py
"""Tests for distillate.highlight_io — native PDF highlight I/O.

Round-trip: build a PDF → add highlights → read them back → delete one →
read remaining. Verifies coordinate math (bottom-left ↔ top-left flip),
subject tagging, and incremental save correctness.
"""

from pathlib import Path
import pytest

pymupdf = pytest.importorskip("pymupdf")

from distillate import highlight_io


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """A 2-page PDF with some text, enough to host highlight annots."""
    doc = pymupdf.open()
    p1 = doc.new_page(width=612, height=792)  # US Letter
    p1.insert_text((72, 100), "Page 1 body text.", fontsize=12)
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((72, 100), "Page 2 body text.", fontsize=12)
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()
    return path


class TestAddHighlight:

    def test_adds_and_returns_id(self, sample_pdf):
        rects = [[72.0, 700.0, 300.0, 712.0]]  # PDF bottom-left
        annot_id = highlight_io.add_highlight(
            sample_pdf, 0, rects, text="first highlight",
        )
        assert annot_id  # non-empty string

    def test_roundtrip(self, sample_pdf):
        """Write a highlight, read it back — text, page, and approximate
        rect survive the bottom-left ↔ top-left flip."""
        rects_in = [[72.0, 700.0, 300.0, 712.0]]
        annot_id = highlight_io.add_highlight(
            sample_pdf, 0, rects_in, text="hello", color="#ff8800",
        )
        assert annot_id

        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 1
        h = read[0]
        assert h["id"] == annot_id
        assert h["page_index"] == 0
        assert h["text"] == "hello"
        assert h["color"] == "#ff8800"
        assert h["subject"] == "distillate-user"
        # Rect round-trip: coordinates should match within ~1pt.
        assert len(h["rects"]) == 1
        r = h["rects"][0]
        assert abs(r[0] - 72.0) < 1
        assert abs(r[1] - 700.0) < 1
        assert abs(r[2] - 300.0) < 1
        assert abs(r[3] - 712.0) < 1

    def test_rejects_out_of_range_page(self, sample_pdf):
        annot_id = highlight_io.add_highlight(
            sample_pdf, 99, [[72.0, 700.0, 300.0, 712.0]], text="x",
        )
        assert annot_id is None

    def test_rejects_empty_rects(self, sample_pdf):
        annot_id = highlight_io.add_highlight(sample_pdf, 0, [], text="x")
        assert annot_id is None

    def test_multiple_highlights_coexist(self, sample_pdf):
        id1 = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 300.0, 712.0]], text="one",
        )
        id2 = highlight_io.add_highlight(
            sample_pdf, 1, [[100.0, 500.0, 400.0, 520.0]], text="two",
        )
        assert id1 and id2 and id1 != id2

        read = highlight_io.read_highlights(sample_pdf)
        ids = {h["id"] for h in read}
        assert ids == {id1, id2}

        texts_by_page = {h["page_index"]: h["text"] for h in read}
        assert texts_by_page == {0: "one", 1: "two"}


class TestDeleteHighlight:

    def test_deletes_by_id(self, sample_pdf):
        id1 = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 300.0, 712.0]], text="keep",
        )
        id2 = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 650.0, 300.0, 662.0]], text="drop",
        )
        assert id1 and id2

        removed = highlight_io.delete_highlight(sample_pdf, id2)
        assert removed is True

        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 1
        assert read[0]["id"] == id1
        assert read[0]["text"] == "keep"

    def test_unknown_id_is_noop(self, sample_pdf):
        highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 300.0, 712.0]], text="stay",
        )
        removed = highlight_io.delete_highlight(sample_pdf, "NONEXISTENT")
        assert removed is False
        assert len(highlight_io.read_highlights(sample_pdf)) == 1

    def test_empty_id_is_noop(self, sample_pdf):
        assert highlight_io.delete_highlight(sample_pdf, "") is False


class TestReadHighlights:

    def test_empty_pdf(self, sample_pdf):
        assert highlight_io.read_highlights(sample_pdf) == []

    def test_excludes_pipeline_tagged_annotations(self, sample_pdf, tmp_path):
        """Annots with subject == 'distillate' (pipeline-generated) should
        be filtered out. User annots ('distillate-user') should appear."""
        # Write a user highlight.
        uid = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 300.0, 712.0]], text="user",
        )
        # Directly add a pipeline-tagged annot.
        doc = pymupdf.open(str(sample_pdf))
        page = doc[0]
        annot = page.add_highlight_annot(pymupdf.Rect(72, 80, 300, 92))
        info = annot.info
        info["subject"] = "distillate"
        info["content"] = "pipeline"
        annot.set_info(info)
        annot.update()
        doc.save(str(sample_pdf), incremental=True,
                 encryption=pymupdf.PDF_ENCRYPT_KEEP)
        doc.close()

        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 1
        assert read[0]["id"] == uid
        assert read[0]["text"] == "user"


class TestMalformedRects:
    """Guard rails for the coordinate unpacking — these inputs used to
    throw TypeErrors and get silently swallowed into an empty return."""

    def test_returns_none_on_null_rect_values(self, sample_pdf):
        """Rect with None values (what JSON.stringify(NaN) becomes) must
        be caught cleanly, not explode."""
        result = highlight_io.add_highlight(
            sample_pdf, 0, [[None, None, None, None]], text="bad rect",
        )
        assert result is None
        # Verify nothing was written.
        assert highlight_io.read_highlights(sample_pdf) == []

    def test_returns_none_on_short_rect(self, sample_pdf):
        """Rect with <4 elements is filtered out; if it's the only rect
        we end up with no quads and return None."""
        result = highlight_io.add_highlight(
            sample_pdf, 0, [[1.0, 2.0, 3.0]], text="short rect",
        )
        assert result is None


class TestClearUserHighlights:

    def test_removes_only_user_highlights(self, sample_pdf):
        """clear_user_highlights() removes distillate-user annots but
        preserves subject=='distillate' (pipeline) annots."""
        user_id = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 300.0, 712.0]], text="user",
        )
        assert user_id

        # Add a pipeline-tagged annot directly.
        doc = pymupdf.open(str(sample_pdf))
        page = doc[0]
        annot = page.add_highlight_annot(pymupdf.Rect(72, 80, 300, 92))
        info = annot.info
        info["subject"] = "distillate"
        info["content"] = "pipeline"
        annot.set_info(info)
        annot.update()
        doc.save(str(sample_pdf), incremental=True,
                 encryption=pymupdf.PDF_ENCRYPT_KEEP)
        doc.close()

        removed = highlight_io.clear_user_highlights(sample_pdf)
        assert removed == 1  # only user annot

        # Pipeline annot still there (but read_highlights skips it).
        read = highlight_io.read_highlights(sample_pdf)
        assert read == []

    def test_noop_when_no_user_highlights(self, sample_pdf):
        assert highlight_io.clear_user_highlights(sample_pdf) == 0


class TestDedupeAndIdempotency:

    def test_add_highlight_is_idempotent_on_same_text(self, sample_pdf):
        """Calling add_highlight twice with the same text on the same
        page returns the same id and writes only one annotation."""
        id1 = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 300.0, 712.0]], text="dupe me",
        )
        id2 = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 300.0, 712.0]], text="dupe me",
        )
        assert id1 and id2 and id1 == id2
        assert len(highlight_io.read_highlights(sample_pdf)) == 1

    def test_dedupe_removes_stacked_duplicates(self, sample_pdf):
        """If a PDF was previously written with duplicates (pre-guard),
        dedupe_pdf_highlights() collapses them to one per (text, page)."""
        # Simulate the pre-fix stacked state by bypassing the add_highlight
        # idempotency guard — write annotations directly.
        doc = pymupdf.open(str(sample_pdf))
        page = doc[0]
        for _ in range(3):
            annot = page.add_highlight_annot(pymupdf.Rect(72, 80, 300, 92))
            info = annot.info
            info["subject"] = "distillate-user"
            info["content"] = "stacked"
            annot.set_info(info)
            annot.update()
        doc.save(str(sample_pdf), incremental=True,
                 encryption=pymupdf.PDF_ENCRYPT_KEEP)
        doc.close()

        removed = highlight_io.dedupe_pdf_highlights(sample_pdf)
        assert removed == 2
        assert len(highlight_io.read_highlights(sample_pdf)) == 1


class TestOverlapExtends:
    """User-decided behaviour: when a new highlight overlaps an existing
    one on the same page, merge the rects into the existing annotation
    rather than stacking. Same page, no overlap → two distinct highlights.
    Different pages → never merge.

    Definition of overlap: any rect in the new selection has a
    bounding-box intersection with any rect in the existing annotation.
    """

    def test_no_overlap_same_page_keeps_both(self, sample_pdf):
        """Two highlights on the same page with non-overlapping rects
        stay as two distinct annotations."""
        id1 = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 200.0, 712.0]], text="first",
        )
        id2 = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 600.0, 200.0, 612.0]], text="second",
        )
        assert id1 and id2 and id1 != id2
        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 2
        assert {h["text"] for h in read} == {"first", "second"}

    def test_overlap_same_page_merges_into_existing(self, sample_pdf):
        """A new highlight whose rect overlaps with an existing rect on
        the same page should extend the existing annotation rather than
        creating a new one. Returned id is the existing annotation's id;
        rects of both are unioned in the resulting annotation."""
        id1 = highlight_io.add_highlight(
            sample_pdf, 0, [[100.0, 700.0, 300.0, 712.0]],
            text="quick brown",
        )
        # New selection overlaps the existing rect (x range 200-400 overlaps 100-300).
        id2 = highlight_io.add_highlight(
            sample_pdf, 0, [[200.0, 700.0, 400.0, 712.0]],
            text="brown fox jumps",
        )
        assert id1 and id2
        # Same id returned — the new one merged into the old.
        assert id2 == id1

        read = highlight_io.read_highlights(sample_pdf)
        # Single annotation remains.
        assert len(read) == 1
        merged = read[0]
        # Rects from both inputs should be present in the merged annot.
        assert len(merged["rects"]) >= 2 or any(
            r[0] <= 100.0 and r[2] >= 400.0 for r in merged["rects"]
        ), f"expected merged rects to span both inputs, got {merged['rects']}"

    def test_overlap_different_page_keeps_both(self, sample_pdf):
        """Highlights on different pages never merge, even if rect
        coordinates would otherwise overlap."""
        id1 = highlight_io.add_highlight(
            sample_pdf, 0, [[100.0, 700.0, 300.0, 712.0]], text="page 1",
        )
        id2 = highlight_io.add_highlight(
            sample_pdf, 1, [[100.0, 700.0, 300.0, 712.0]], text="page 2",
        )
        assert id1 and id2 and id1 != id2
        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 2

    def test_extending_preserves_existing_text(self, sample_pdf):
        """When merging via overlap, the existing annotation's text is
        kept (not replaced by the new selection's text). Rationale: the
        old annotation came first; the new selection is the user
        extending its bounds, not changing what it covers."""
        highlight_io.add_highlight(
            sample_pdf, 0, [[100.0, 700.0, 300.0, 712.0]],
            text="original text",
        )
        highlight_io.add_highlight(
            sample_pdf, 0, [[200.0, 700.0, 400.0, 712.0]],
            text="extending selection",
        )
        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 1
        # Either text is acceptable for a v1; pin "original" for now.
        assert read[0]["text"] == "original text"

    def test_no_overlap_with_existing_on_same_page_creates_new(self, sample_pdf):
        """Three highlights on same page; new one overlaps only one of
        them. Should merge into that one, leave the other alone. End
        state: two distinct annotations."""
        # Two non-overlapping highlights up front
        id_a = highlight_io.add_highlight(
            sample_pdf, 0, [[100.0, 700.0, 200.0, 712.0]], text="A",
        )
        id_b = highlight_io.add_highlight(
            sample_pdf, 0, [[100.0, 600.0, 200.0, 612.0]], text="B",
        )
        # New highlight overlaps A only (same row 700-712).
        id_c = highlight_io.add_highlight(
            sample_pdf, 0, [[150.0, 700.0, 250.0, 712.0]], text="C overlap A",
        )
        assert id_c == id_a  # merged into A
        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 2
        texts = sorted(h["text"] for h in read)
        assert texts == sorted(["A", "B"])  # text from "A" kept


class TestBridgeMerge:
    """When a new highlight overlaps *two* or more existing annotations,
    it should transitively merge all of them into one.

    Example: existing [A] on left, existing [C] on right. User selects
    [B] spanning both. Expected end state: a single annotation covering
    A+B+C. Currently `add_highlight` returns after the first match and
    leaves later overlapping annotations orphaned."""

    def test_bridge_merge_unites_all_overlapping(self, sample_pdf):
        # Set up two non-overlapping user highlights on same row.
        id_a = highlight_io.add_highlight(
            sample_pdf, 0, [[100.0, 700.0, 200.0, 712.0]], text="A",
        )
        id_c = highlight_io.add_highlight(
            sample_pdf, 0, [[300.0, 700.0, 400.0, 712.0]], text="C",
        )
        assert id_a and id_c and id_a != id_c

        # Bridge selection overlaps both.
        id_bridge = highlight_io.add_highlight(
            sample_pdf, 0, [[150.0, 700.0, 350.0, 712.0]], text="B bridge",
        )
        # Returned id is one of the two originals (order is impl detail).
        assert id_bridge in (id_a, id_c)
        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 1, f"expected single merged annot, got {len(read)}: {[h['text'] for h in read]}"


class TestColorPreservationOnMerge:
    """When merging highlights via overlap, the existing annotation's
    colour should be preserved — the new selection is extending its
    bounds, not re-painting it."""

    def test_overlap_keeps_existing_color(self, sample_pdf):
        highlight_io.add_highlight(
            sample_pdf, 0, [[100.0, 700.0, 300.0, 712.0]],
            text="original", color="#ff6600",  # orange
        )
        # New selection overlaps, passing a different colour.
        highlight_io.add_highlight(
            sample_pdf, 0, [[200.0, 700.0, 400.0, 712.0]],
            text="extending", color="#00ff00",  # green
        )
        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 1
        # Colour should be preserved from the existing annotation.
        assert read[0]["color"] == "#ff6600"


class TestPartialMigrationSafety:
    """migrate_local_highlights should only report entries it successfully
    wrote. Callers use this count to decide whether to clear the source
    list — a silent half-failure would lose the other half."""

    def test_returns_actual_count_when_some_fail(self, sample_pdf):
        local = [
            # First entry is valid.
            {"page_index": 0, "rects": [[72.0, 700.0, 300.0, 712.0]],
             "text": "good", "color": "#ffd400"},
            # Second is invalid (out-of-range page).
            {"page_index": 99, "rects": [[72.0, 500.0, 300.0, 512.0]],
             "text": "bad-page", "color": "#ffd400"},
            # Third is valid.
            {"page_index": 1, "rects": [[72.0, 500.0, 300.0, 512.0]],
             "text": "also-good", "color": "#ffd400"},
        ]
        migrated = highlight_io.migrate_local_highlights(sample_pdf, local)
        # Only the 2 valid entries should count as migrated.
        assert migrated == 2
        read = highlight_io.read_highlights(sample_pdf)
        assert {h["text"] for h in read} == {"good", "also-good"}


class TestEncryptedPdfRoundtrip:
    """PDFs opened from encrypted source files — with or without
    `encryption=PDF_ENCRYPT_KEEP` — should round-trip highlights without
    corrupting the document. Here we make a PDF that Python can open
    without needing a password (most scientific PDFs), to confirm the
    incremental-save path handles the PDF_ENCRYPT_KEEP flag correctly."""

    def test_roundtrip_preserves_encryption_flag(self, sample_pdf):
        # Add then read back — tests the incremental save with
        # encryption=PDF_ENCRYPT_KEEP which is set in add_highlight.
        annot_id = highlight_io.add_highlight(
            sample_pdf, 0, [[72.0, 700.0, 300.0, 712.0]], text="encrypted-test",
        )
        assert annot_id
        # File should still be a valid PDF (magic bytes check).
        head = sample_pdf.read_bytes()[:8]
        assert head.startswith(b"%PDF-")

        read = highlight_io.read_highlights(sample_pdf)
        assert len(read) == 1
        assert read[0]["text"] == "encrypted-test"


class TestRotatedPage:
    """PDFs with rotated pages — coordinate flip math assumes
    ``page.rect.height`` is the visual height. Document the current
    behaviour so future changes to rotation handling are surfaced."""

    def test_rotated_page_highlight_roundtrips(self, tmp_path):
        """Highlight on a 90°-rotated page round-trips text at minimum.
        We don't assert exact rect values because PyMuPDF's rotation
        handling is an upstream concern — but the highlight must exist
        and be readable after write."""
        import pymupdf
        doc = pymupdf.open()
        p = doc.new_page(width=612, height=792)
        p.set_rotation(90)
        p.insert_text((72, 100), "Rotated body text.", fontsize=12)
        path = tmp_path / "rotated.pdf"
        doc.save(str(path))
        doc.close()

        annot_id = highlight_io.add_highlight(
            path, 0, [[72.0, 700.0, 300.0, 712.0]], text="rotated-hl",
        )
        assert annot_id
        read = highlight_io.read_highlights(path)
        assert len(read) == 1
        assert read[0]["text"] == "rotated-hl"


