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
from typing import Any, Dict, List, Optional, Tuple

from rmscene import read_tree, scene_items as si

log = logging.getLogger(__name__)

# Suppress rmscene "newer format" warning — benign, data is still extracted
logging.getLogger("rmscene.tagged_block_reader").setLevel(logging.ERROR)

# GlyphRange items on adjacent lines within this y-gap are merged
_MAX_LINE_GAP = 100.0

# Highlight annotation style
_HIGHLIGHT_COLOR = (1.0, 0.92, 0.3)  # soft yellow
_HIGHLIGHT_OPACITY = 0.35
_HIGHLIGHT_TRIM = 0.20  # shrink quads vertically by this fraction per side


def extract_original_pdf(zip_path: Path) -> Optional[bytes]:
    """Extract the original (un-annotated) PDF from a reMarkable bundle.

    Returns the raw PDF bytes, or None if no PDF is found.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            pdf_names = [n for n in zf.namelist() if n.endswith(".pdf")]
            if not pdf_names:
                return None
            return zf.read(pdf_names[0])
    except Exception:
        log.warning("Failed to extract original PDF from %s", zip_path.name, exc_info=True)
        return None


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

    by_page = _merge_cross_page(by_page)

    total = sum(len(v) for v in by_page.values())
    log.info("Extracted %d highlight(s) from %s", total, zip_path.name)
    return by_page


def _merge_cross_page(by_page: Dict[int, List[str]]) -> Dict[int, List[str]]:
    """Merge highlight passages that span page breaks.

    If the last passage on page N ends without terminal punctuation and the
    first passage on page N+1 starts lowercase, they are likely the same
    sentence split across pages. Merge them into page N.
    """
    if len(by_page) < 2:
        return by_page

    pages = sorted(by_page.keys())
    for i in range(len(pages) - 1):
        page_n = pages[i]
        page_next = pages[i + 1]

        if page_next != page_n + 1:
            continue

        # Pages may have been deleted by a prior merge in this loop
        if page_n not in by_page or page_next not in by_page:
            continue

        passages_n = by_page[page_n]
        passages_next = by_page[page_next]

        if not passages_n or not passages_next:
            continue

        last = passages_n[-1]
        first = passages_next[0]

        ends_mid = not last.rstrip().endswith((".", "!", "?", ":", '"'))
        starts_lower = first[:1].islower()

        if ends_mid and starts_lower:
            passages_n[-1] = last.rstrip() + " " + first.lstrip()
            passages_next.pop(0)
            if not passages_next:
                del by_page[page_next]

    return by_page


def _recover_pdf_text(page_text: str, search_text: str) -> Optional[str]:
    """Find search_text in page_text via whitespace/hyphen-normalized matching.

    reMarkable OCR often strips spaces and hyphens at line breaks, producing
    concatenated text like ``proofs,standard`` or ``math`` (from ``math-\\n
    ematics``).  This function normalises both texts, locates the match, then
    returns the original PDF substring with line-break hyphens rejoined and
    remaining newlines collapsed to spaces so that PyMuPDF ``search_for`` can
    find it.
    """
    norm_chars: List[str] = []
    norm_to_orig: List[int] = []
    for i, ch in enumerate(page_text):
        if not ch.isspace() and ch not in ("-", "\u00ad"):
            norm_to_orig.append(i)
            norm_chars.append(ch.lower())
    page_norm = "".join(norm_chars)

    search_norm = re.sub(r"[\s\-\u00ad]+", "", search_text.lower())

    pos = page_norm.find(search_norm)
    if pos < 0:
        return None

    orig_start = norm_to_orig[pos]
    orig_end = norm_to_orig[pos + len(search_norm) - 1] + 1
    raw = page_text[orig_start:orig_end]
    # Rejoin hyphenated words, collapse remaining newlines to spaces
    return raw.replace("-\n", "").replace("\n", " ")


def _search_highlight_positions(
    doc: Any,
    highlights_by_page: Dict[int, List[Tuple[str, Optional[float]]]],
) -> List[Dict[str, Any]]:
    """Search for highlight text in a PyMuPDF document and return positions.

    Shared helper used by both render_annotated_pdf() and
    extract_zotero_highlights().  Returns a list of dicts with keys:
    text, page_index (0-based), quads (list of pymupdf.Quad), page_height.
    """
    import pymupdf

    _RM_HEIGHT = 1872.0
    _RM_TO_PDF_SCALE = 0.70

    results: List[Dict[str, Any]] = []

    for page_idx, glyph_list in highlights_by_page.items():
        if page_idx >= len(doc):
            continue
        page = doc[page_idx]
        page_h = page.rect.height

        highlighted: List = []  # list of pymupdf.Rect

        for text, rm_y in glyph_list:
            quads = page.search_for(text, quads=True)
            if not quads:
                # Fallback: recover actual PDF text via normalized matching
                recovered = _recover_pdf_text(page.get_text("text"), text)
                if recovered:
                    quads = page.search_for(recovered, quads=True)
            if not quads:
                continue

            groups = _group_quads(quads, page_h)

            if len(groups) > 1 and rm_y is not None:
                expected_frac = (rm_y / _RM_HEIGHT) * _RM_TO_PDF_SCALE
                groups = [min(
                    groups,
                    key=lambda g: abs(g[0].ul.y / page_h - expected_frac),
                )]

            selected = groups[0]

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
                results.append({
                    "text": text,
                    "page_index": page_idx,
                    "quads": new_quads,
                    "page_height": page_h,
                })

    return results


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

        with zipfile.ZipFile(zip_path, "r") as zf:
            pdf_names = [n for n in zf.namelist() if n.endswith(".pdf")]
            if not pdf_names:
                log.warning("No PDF found in %s", zip_path)
                return False
            pdf_data = zf.read(pdf_names[0])

        doc = pymupdf.open(stream=pdf_data, filetype="pdf")
        positions = _search_highlight_positions(doc, highlights_by_page)

        for pos in positions:
            page = doc[pos["page_index"]]
            slimmed = [_slim_quad(pymupdf, q, _HIGHLIGHT_TRIM)
                       for q in pos["quads"]]
            annot = page.add_highlight_annot(slimmed)
            annot.set_colors(stroke=_HIGHLIGHT_COLOR)
            annot.set_opacity(_HIGHLIGHT_OPACITY)
            annot.update()

        doc.save(str(output_path), garbage=3, deflate=True)
        doc.close()
        log.info(
            "Rendered annotated PDF with %d highlight(s): %s",
            len(positions), output_path,
        )
        return True

    except Exception:
        log.warning("Failed to render annotated PDF for %s", zip_path.name, exc_info=True)
        return False


def extract_zotero_highlights(
    zip_path: Path,
    pdf_bytes: Optional[bytes] = None,
) -> List[Dict[str, Any]]:
    """Extract highlight positions suitable for Zotero annotation API.

    Returns list of dicts with keys: text, page_index (0-based),
    page_label (1-based str), rects (list of [x0, y0, x1, y1] in PDF
    bottom-left coordinates), sort_index, color.
    """
    try:
        import pymupdf
    except ImportError:
        log.warning("pymupdf not installed, cannot extract Zotero highlights")
        return []

    try:
        highlights_by_page = _extract_highlights_by_page(zip_path)
        if not highlights_by_page:
            return []

        if pdf_bytes is None:
            with zipfile.ZipFile(zip_path, "r") as zf:
                pdf_names = [n for n in zf.namelist() if n.endswith(".pdf")]
                if not pdf_names:
                    return []
                pdf_bytes = zf.read(pdf_names[0])

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        positions = _search_highlight_positions(doc, highlights_by_page)

        results: List[Dict[str, Any]] = []
        for pos in positions:
            page_idx = pos["page_index"]
            page_h = pos["page_height"]
            page = doc[page_idx]
            page_text = page.get_text("text")

            # Convert quads from PyMuPDF (top-left origin) to PDF (bottom-left)
            rects = []
            for q in pos["quads"]:
                r = q.rect
                rects.append([
                    round(r.x0, 3),
                    round(page_h - r.y1, 3),
                    round(r.x1, 3),
                    round(page_h - r.y0, 3),
                ])

            # Compute sort_index: PPPPP|CCCCCC|TTTTT
            char_offset = page_text.find(pos["text"])
            top_y = min(q.rect.y0 for q in pos["quads"])
            sort_index = (
                f"{page_idx:05d}"
                f"|{max(char_offset, 0):06d}"
                f"|{int(page_h - top_y):05d}"
            )

            results.append({
                "text": pos["text"],
                "page_index": page_idx,
                "page_label": str(page_idx + 1),
                "rects": rects,
                "sort_index": sort_index,
                "color": "#ffd400",
            })

        doc.close()
        log.info("Extracted %d Zotero highlight position(s)", len(results))
        return results

    except Exception:
        log.warning("Failed to extract Zotero highlights", exc_info=True)
        return []


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
