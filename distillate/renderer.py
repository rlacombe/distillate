"""Extract highlights and render annotated PDFs from reMarkable document bundles.

Uses rmscene to parse v6 .rm files for highlighted text (GlyphRange items),
and PyMuPDF to search for that text in the original PDF and add highlight
annotations.
"""

import io
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

from rmscene import read_tree, scene_items as si

log = logging.getLogger(__name__)

# GlyphRange items on adjacent lines within this y-gap are merged
_MAX_LINE_GAP = 100.0

# Highlight annotation style
_HIGHLIGHT_COLOR = (1.0, 0.92, 0.3)  # soft yellow
_HIGHLIGHT_OPACITY = 0.35
_HIGHLIGHT_TRIM = 0.20  # shrink quads vertically by this fraction per side


def get_page_count(zip_path: Path) -> int:
    """Return the total page count from a reMarkable document bundle.

    Parses the .content file to count pages. Returns 0 on failure.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            content_files = [n for n in zf.namelist() if n.endswith(".content")]
            if not content_files:
                return 0
            content_data = json.loads(zf.read(content_files[0]))
            page_ids = content_data.get("cPages", {}).get("pages", [])
            if not page_ids:
                page_ids = content_data.get("pages", [])
            return len(page_ids)
    except Exception:
        log.debug("Could not get page count from %s", zip_path, exc_info=True)
        return 0


def extract_highlights(zip_path: Path) -> Dict[int, List[str]]:
    """Extract highlighted text strings from a reMarkable document bundle.

    Returns a dict mapping page numbers (1-based) to lists of merged
    highlight passages for that page. Empty dict if no highlights found.
    """
    by_page: Dict[int, List[str]] = {}

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            content_files = [n for n in zf.namelist() if n.endswith(".content")]
            if not content_files:
                return by_page

            content_data = json.loads(zf.read(content_files[0]))
            page_ids = content_data.get("cPages", {}).get("pages", [])
            if not page_ids:
                page_ids = content_data.get("pages", [])

            ordered_ids = []
            for page in page_ids:
                if isinstance(page, dict):
                    ordered_ids.append(page.get("id", ""))
                else:
                    ordered_ids.append(str(page))

            rm_files = [n for n in zf.namelist() if n.endswith(".rm")]

            # Build (page_index, rm_file) pairs and sort by page order
            indexed = []
            for rm_file in rm_files:
                stem = Path(rm_file).stem
                try:
                    idx = ordered_ids.index(stem)
                except ValueError:
                    continue
                indexed.append((idx, rm_file))
            indexed.sort()

            for page_idx, rm_file in indexed:
                try:
                    rm_data = zf.read(rm_file)
                    raw = _extract_raw_glyphs(rm_data)
                    merged = _merge_glyphs(raw)
                    if merged:
                        by_page[page_idx + 1] = merged  # 1-based page numbers
                except Exception:
                    log.warning("Failed to parse %s in %s", rm_file, zip_path, exc_info=True)

    except Exception:
        log.warning("Failed to read zip %s", zip_path, exc_info=True)

    total = sum(len(v) for v in by_page.values())
    log.info("Extracted %d highlight(s) from %s", total, zip_path.name)
    return by_page


def render_annotated_pdf(zip_path: Path, output_path: Path) -> bool:
    """Render highlight annotations onto the original PDF using PyMuPDF.

    Extracts the original PDF and highlight text from the zip bundle,
    searches for each text line on the correct page, uses the RM
    y-coordinate to pick the right match when there are duplicates,
    and deduplicates overlapping regions.

    Returns True on success, False on failure.
    """
    try:
        import pymupdf
    except ImportError:
        log.warning("pymupdf not installed, cannot render annotated PDF")
        return False

    try:
        highlights_by_page = _extract_highlights_by_page(zip_path)
        if not highlights_by_page:
            log.info("No highlights to render for %s", zip_path.name)

        # Extract the original PDF from the zip
        with zipfile.ZipFile(zip_path, "r") as zf:
            pdf_names = [n for n in zf.namelist() if n.endswith(".pdf")]
            if not pdf_names:
                log.warning("No PDF found in %s", zip_path)
                return False
            pdf_data = zf.read(pdf_names[0])

        doc = pymupdf.open(stream=pdf_data, filetype="pdf")

        # RM display height in pixels — used to compute expected y-fraction
        _RM_HEIGHT = 1872.0
        # Empirical scale from RM y-fraction to PDF y-fraction
        _RM_TO_PDF_SCALE = 0.70

        total_hits = 0
        for page_idx, glyph_list in highlights_by_page.items():
            if page_idx >= len(doc):
                continue
            page = doc[page_idx]
            page_h = page.rect.height

            # Track highlighted regions to prevent overlaps
            highlighted: List = []  # list of pymupdf.Rect

            for text, rm_y in glyph_list:
                quads = page.search_for(text, quads=True)
                if not quads:
                    continue

                # Group quads into match clusters — a single match
                # that spans multiple text runs / lines returns
                # several consecutive, vertically-close quads.
                groups = _group_quads(quads, page_h)

                # Pick the best match group using RM y-coordinate
                if len(groups) > 1 and rm_y is not None:
                    expected_frac = (rm_y / _RM_HEIGHT) * _RM_TO_PDF_SCALE
                    groups = [min(
                        groups,
                        key=lambda g: abs(g[0].ul.y / page_h - expected_frac),
                    )]

                selected = groups[0]

                # Deduplicate: skip quads whose center is inside an
                # already-highlighted region
                new_quads = []
                for q in selected:
                    r = q.rect
                    center = pymupdf.Point(
                        (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2,
                    )
                    if any(hr.contains(center) for hr in highlighted):
                        continue
                    new_quads.append(q)
                    highlighted.append(r)

                if new_quads:
                    slimmed = [_slim_quad(pymupdf, q, _HIGHLIGHT_TRIM)
                               for q in new_quads]
                    annot = page.add_highlight_annot(slimmed)
                    annot.set_colors(stroke=_HIGHLIGHT_COLOR)
                    annot.set_opacity(_HIGHLIGHT_OPACITY)
                    annot.update()
                    total_hits += 1

        doc.save(str(output_path), garbage=3, deflate=True)
        doc.close()
        log.info(
            "Rendered annotated PDF with %d highlight(s): %s",
            total_hits, output_path,
        )
        return True

    except Exception:
        log.warning("Failed to render annotated PDF for %s", zip_path.name, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _group_quads(quads, page_h: float):
    """Group consecutive quads that belong to the same match.

    PyMuPDF returns one quad per text-run/line for a single match.
    Consecutive quads whose vertical distance is small (< 3% of page
    height) are treated as part of the same match.
    """
    if not quads:
        return []
    threshold = page_h * 0.03
    groups: list = [[quads[0]]]
    for q in quads[1:]:
        prev = groups[-1][-1]
        if abs(q.ul.y - prev.ul.y) < threshold or q.ul.y - prev.lr.y < threshold:
            groups[-1].append(q)
        else:
            groups.append([q])
    return groups


def _slim_quad(pymupdf, q, trim_frac: float):
    """Shrink a quad vertically by *trim_frac* on each side."""
    h = q.ll.y - q.ul.y
    trim = h * trim_frac
    return pymupdf.Quad(
        pymupdf.Point(q.ul.x, q.ul.y + trim),
        pymupdf.Point(q.ur.x, q.ur.y + trim),
        pymupdf.Point(q.ll.x, q.ll.y - trim),
        pymupdf.Point(q.lr.x, q.lr.y - trim),
    )


def _extract_highlights_by_page(
    zip_path: Path,
) -> Dict[int, List[Tuple[str, float | None]]]:
    """Return {page_index: [(line_text, rm_y), ...]} from the zip bundle.

    Each entry is a single GlyphRange line with its RM y-coordinate
    for position-aware matching in the PDF.
    """
    result: Dict[int, List[Tuple[str, float | None]]] = {}

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            content_files = [n for n in zf.namelist() if n.endswith(".content")]
            if not content_files:
                return result

            content_data = json.loads(zf.read(content_files[0]))
            page_ids = content_data.get("cPages", {}).get("pages", [])
            if not page_ids:
                page_ids = content_data.get("pages", [])

            ordered_ids = []
            for page in page_ids:
                if isinstance(page, dict):
                    ordered_ids.append(page.get("id", ""))
                else:
                    ordered_ids.append(str(page))

            rm_files = [n for n in zf.namelist() if n.endswith(".rm")]

            for rm_file in rm_files:
                stem = Path(rm_file).stem
                try:
                    page_idx = ordered_ids.index(stem)
                except ValueError:
                    continue

                try:
                    rm_data = zf.read(rm_file)
                    raw_glyphs = _extract_raw_glyphs(rm_data)
                    if raw_glyphs:
                        result[page_idx] = [
                            (text, y) for text, y, _color in raw_glyphs
                        ]
                except Exception:
                    log.warning(
                        "Failed to parse %s in %s",
                        rm_file, zip_path, exc_info=True,
                    )

    except Exception:
        log.warning("Failed to read zip %s", zip_path, exc_info=True)

    return result


def _join_dedup(parts: List[str]) -> str:
    """Join text parts, removing overlapping words at boundaries.

    When two GlyphRange items overlap (e.g. one ends with 'species' and the
    next starts with 'species or'), the duplicate words are removed.
    """
    if not parts:
        return ""
    result = parts[0]
    for part in parts[1:]:
        result_words = result.split()
        part_words = part.split()
        # Check for 1-3 word overlap at boundary
        overlap = 0
        for n in range(min(3, len(result_words), len(part_words)), 0, -1):
            if result_words[-n:] == part_words[:n]:
                overlap = n
                break
        if overlap:
            result += " " + " ".join(part_words[overlap:])
        else:
            result += " " + part
    return result


def _clean_highlight_text(text: str) -> str:
    """Clean up OCR artifacts from reMarkable highlight text."""
    # Strip superscript citation markers: (p1), (p2), (1), (23), etc.
    text = re.sub(r'\(p?\d+\)', '', text)

    # Clean punctuation artifacts from citation removal
    text = re.sub(r',\s*,', ',', text)       # ",," → ","
    text = re.sub(r';\s*;', ';', text)       # ";;" → ";"
    text = re.sub(r',(\s*and\b)', r'\1', text)  # ", and" after removed citation

    # Insert space after sentence-ending punctuation followed by a letter
    text = re.sub(r'([.;!?])([A-Za-z])', r'\1 \2', text)

    # Insert space after comma followed directly by a letter
    text = re.sub(r',([A-Za-z])', r', \1', text)

    # Fix broken line-wrap joins: lowercase followed by uppercase mid-sentence
    # e.g. "operationsWe" → "operations We" (but preserve acronyms like "GenAI")
    text = re.sub(r'([a-z]{2})([A-Z][a-z])', r'\1 \2', text)

    # Collapse multiple spaces
    text = re.sub(r' {2,}', ' ', text)

    return text.strip()


def _extract_raw_glyphs(
    rm_data: bytes,
) -> List[Tuple[str, float | None, int]]:
    """Parse a .rm file and return [(text, y, color), ...] per GlyphRange."""
    raw_glyphs: List[Tuple[str, float | None, int]] = []
    try:
        tree = read_tree(io.BytesIO(rm_data))
        for item in tree.walk():
            if isinstance(item, si.GlyphRange) and item.text:
                text = item.text.strip()
                if not text:
                    continue
                y = item.rectangles[0].y if item.rectangles else None
                raw_glyphs.append((text, y, item.color))
    except Exception:
        log.debug("Could not parse .rm data as v6 scene tree", exc_info=True)
    return raw_glyphs


def _merge_glyphs(
    raw_glyphs: List[Tuple[str, float | None, int]],
) -> List[str]:
    """Merge consecutive GlyphRange items on adjacent lines into passages.

    Sorts glyphs by y-coordinate first so that items from different columns
    (which have distinct y-ranges) are grouped correctly instead of
    being interleaved.
    """
    if not raw_glyphs:
        return []

    # Sort by y so items from the same column are adjacent
    sorted_glyphs = sorted(raw_glyphs, key=lambda g: g[1] if g[1] is not None else 0)

    passages: List[str] = []
    current_parts: List[str] = [sorted_glyphs[0][0]]
    prev_y = sorted_glyphs[0][1]
    prev_color = sorted_glyphs[0][2]

    for text, y, color in sorted_glyphs[1:]:
        same_passage = (
            color == prev_color
            and prev_y is not None
            and y is not None
            and abs(y - prev_y) < _MAX_LINE_GAP
        )
        if same_passage:
            current_parts.append(text)
        else:
            passages.append(_clean_highlight_text(_join_dedup(current_parts)))
            current_parts = [text]
        prev_y = y
        prev_color = color

    passages.append(_clean_highlight_text(_join_dedup(current_parts)))
    return passages
