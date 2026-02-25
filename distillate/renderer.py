"""Extract highlights, typed notes, and ink strokes from document bundles.

Supports two sources:
- reMarkable bundles (.zip/.rmdoc): uses rmscene to parse v6 .rm files
- Zotero annotations: uses Zotero API annotation positions directly

Uses PyMuPDF to render annotations onto the original PDF.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# rmscene is lazy-imported — only needed for reMarkable bundles.
# Zotero-only users don't need it installed.
_rmscene_loaded = False
read_tree: Any = None
si: Any = None


def _ensure_rmscene():
    """Lazy-load rmscene on first use."""
    global _rmscene_loaded, read_tree, si, _ERASER_TOOLS, _PEN_COLOR_MAP
    if _rmscene_loaded:
        return
    from rmscene import read_tree as _rt, scene_items as _si
    read_tree = _rt
    si = _si
    _ERASER_TOOLS = {si.Pen.ERASER, si.Pen.ERASER_AREA}
    _PEN_COLOR_MAP = {
        si.PenColor.BLACK: (0, 0, 0),
        si.PenColor.GRAY: (0.5, 0.5, 0.5),
        si.PenColor.WHITE: (1, 1, 1),
        si.PenColor.YELLOW: (0.9, 0.8, 0),
        si.PenColor.GREEN: (0, 0.6, 0),
        si.PenColor.PINK: (0.9, 0.2, 0.5),
        si.PenColor.BLUE: (0, 0.2, 0.8),
        si.PenColor.RED: (0.8, 0, 0),
        si.PenColor.GREEN_2: (0, 0.6, 0),
        si.PenColor.CYAN: (0, 0.6, 0.7),
        si.PenColor.MAGENTA: (0.7, 0, 0.7),
        si.PenColor.YELLOW_2: (0.9, 0.8, 0),
    }
    _rmscene_loaded = True
    # Suppress rmscene "newer format" warning — benign, data is still extracted
    logging.getLogger("rmscene.tagged_block_reader").setLevel(logging.ERROR)


log = logging.getLogger(__name__)

# GlyphRange items on adjacent lines within this y-gap are merged
_MAX_LINE_GAP = 100.0

# Highlight annotation style
_HIGHLIGHT_COLOR = (1.0, 0.92, 0.3)  # soft yellow
_HIGHLIGHT_OPACITY = 0.35
_HIGHLIGHT_TRIM = 0.20  # shrink quads vertically by this fraction per side


def extract_original_pdf(zip_path: Path) -> Optional[bytes]:
    """Extract the original PDF from a reMarkable bundle with ink strokes.

    Returns PDF bytes with handwritten ink rendered (but no highlight
    annotations — those are added by Zotero's own annotation layer).
    Falls back to the raw PDF if ink rendering fails.
    """
    try:
        import pymupdf
    except ImportError:
        pass  # fall through to raw extraction

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            pdf_names = [n for n in zf.namelist() if n.endswith(".pdf")]
            if not pdf_names:
                return None
            pdf_data = zf.read(pdf_names[0])

        # Render ink strokes onto the original PDF
        try:
            ink_by_page = extract_ink_strokes(zip_path)
            if ink_by_page:
                doc = pymupdf.open(stream=pdf_data, filetype="pdf")
                _render_ink_on_pdf(doc, ink_by_page)
                buf = io.BytesIO()
                doc.save(buf, garbage=3, deflate=True)
                doc.close()
                return buf.getvalue()
        except Exception:
            log.debug("Ink rendering on original PDF failed, using raw", exc_info=True)

        return pdf_data
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
    _ensure_rmscene()
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

    by_page = _recover_from_pdf(by_page, zip_path)
    by_page = _merge_cross_page(by_page)

    total = sum(len(v) for v in by_page.values())
    log.info("Extracted %d highlight(s) from %s", total, zip_path.name)
    return by_page


def _recover_from_pdf(
    by_page: Dict[int, List[str]], zip_path: Path,
) -> Dict[int, List[str]]:
    """Replace highlight passages with properly-spaced text from the PDF.

    The reMarkable OCR often strips spaces at line breaks, producing
    concatenated words like ``howgenotypes``.  This function opens the
    embedded PDF and uses :func:`_recover_pdf_text` to find the correct
    text with proper spacing.
    """
    if not by_page:
        return by_page

    try:
        import pymupdf  # noqa: F811 — may not be installed
    except ImportError:
        # No pymupdf — clean raw text only
        for passages in by_page.values():
            for i, passage in enumerate(passages):
                passages[i] = _clean_highlight_text(passage)
        return by_page

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            pdf_names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
            if not pdf_names:
                return by_page
            pdf_data = zf.read(pdf_names[0])

        doc = pymupdf.open(stream=pdf_data, filetype="pdf")

        for page_num, passages in by_page.items():
            page_idx = page_num - 1  # by_page is 1-based, pymupdf is 0-based
            if page_idx < 0 or page_idx >= len(doc):
                # No PDF page — clean raw text only
                for i, passage in enumerate(passages):
                    passages[i] = _clean_highlight_text(passage)
                continue
            page_text = doc[page_idx].get_text("text")

            for i, passage in enumerate(passages):
                recovered = _recover_pdf_text(page_text, passage)
                passages[i] = _clean_highlight_text(recovered or passage)

        doc.close()
    except Exception:
        log.debug("PDF text recovery failed, using raw glyphs", exc_info=True)
        # Fallback: clean all passages without PDF recovery
        for passages in by_page.values():
            for i, passage in enumerate(passages):
                passages[i] = _clean_highlight_text(passage)

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
    if not search_norm:
        return None

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

        # Render handwritten ink strokes
        ink_count = 0
        try:
            ink_by_page = extract_ink_strokes(zip_path)
            if ink_by_page:
                ink_count = _render_ink_on_pdf(doc, ink_by_page)
        except Exception:
            log.debug("Ink stroke rendering failed, continuing", exc_info=True)

        doc.save(str(output_path), garbage=3, deflate=True)
        doc.close()
        log.info(
            "Rendered annotated PDF with %d highlight(s) and %d ink stroke(s): %s",
            len(positions), ink_count, output_path,
        )
        return True

    except Exception:
        log.warning("Failed to render annotated PDF for %s", zip_path.name, exc_info=True)
        return False


def render_annotated_pdf_from_annotations(
    pdf_bytes: bytes,
    annotations: List[Dict[str, Any]],
    output_path: Path,
) -> bool:
    """Render highlight annotations onto a PDF using Zotero annotation rects.

    Takes raw annotation dicts (from zotero_client.get_raw_annotations())
    with pre-computed page_index and rects in PDF bottom-left coordinates.
    Converts to PyMuPDF top-left coordinates and adds highlight annotations.

    Returns True on success, False on failure.
    """
    try:
        import pymupdf
    except ImportError:
        log.warning("pymupdf not installed, cannot render annotated PDF")
        return False

    if not annotations:
        log.info("No annotations to render")
        return False

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")

        rendered = 0
        for ann in annotations:
            page_idx = ann.get("page_index", 0)
            rects = ann.get("rects", [])
            if page_idx >= len(doc) or not rects:
                continue

            page = doc[page_idx]
            page_h = page.rect.height

            # Convert rects from PDF coords (bottom-left) to PyMuPDF (top-left)
            quads = []
            for rect in rects:
                if len(rect) < 4:
                    continue
                x0, y0_bl, x1, y1_bl = rect[0], rect[1], rect[2], rect[3]
                # PDF bottom-left → PyMuPDF top-left: y_tl = page_h - y_bl
                y0_tl = page_h - y1_bl  # top edge
                y1_tl = page_h - y0_bl  # bottom edge
                quads.append(pymupdf.Rect(x0, y0_tl, x1, y1_tl))

            if not quads:
                continue

            annot = page.add_highlight_annot(quads)
            annot.set_colors(stroke=_HIGHLIGHT_COLOR)
            annot.set_opacity(_HIGHLIGHT_OPACITY)
            annot.update()
            rendered += 1

        doc.save(str(output_path), garbage=3, deflate=True)
        doc.close()
        log.info("Rendered annotated PDF with %d highlight(s): %s",
                 rendered, output_path)
        return True

    except Exception:
        log.warning("Failed to render annotated PDF from annotations",
                    exc_info=True)
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

    # Strip bare citation digits after closing paren: (LDSC)48and → (LDSC) and
    text = re.sub(r'\)\d{1,3}([a-zA-Z])', r') \1', text)

    # Strip bare citation digit runs (with optional en-dash ranges) between
    # a word and punctuation: efficiency28, → efficiency,  learning30–32, → learning,
    text = re.sub(r'([a-z])\d{1,3}(?:[,–\-]\d{1,3})*([,;.])', r'\1\2', text)

    # Strip bare citation digits before a word: estimation34to → estimation to
    text = re.sub(r'([,.)a-z])\d{1,3}(?:[,–\-]\d{1,3})*([a-z])', r'\1 \2', text)

    # Strip superscript digits glued to a word (from PDF text extraction):
    # data12 and → data and,  implementations,27 is → implementations, is
    text = re.sub(r'([a-z])\d{1,3}(\s)', r'\1\2', text)
    text = re.sub(r'([,;])\d{1,3}(\s)', r'\1\2', text)

    # Strip trailing citation digits: "data sets,47" → "data sets"
    text = re.sub(r',\d{1,3}(?:[,–\-]\d{1,3})*$', '', text)

    # Clean punctuation artifacts from citation removal
    text = re.sub(r',\s*,', ',', text)       # ",," → ","
    text = re.sub(r';\s*;', ';', text)       # ";;" → ";"
    text = re.sub(r',\s*;', ';', text)       # ",;" → ";"
    text = re.sub(r',(\s*and\b)', r'\1', text)  # ", and" after removed citation

    # Insert space after sentence-ending punctuation followed by a letter
    text = re.sub(r'([.;!?:])([A-Za-z])', r'\1 \2', text)

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
    _ensure_rmscene()
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
            passages.append(_join_dedup(current_parts))
            current_parts = [text]
        prev_y = y
        prev_color = color

    passages.append(_join_dedup(current_parts))
    return passages


# ---------------------------------------------------------------------------
# Page-ID helpers (shared by highlight, ink, and typed-note extraction)
# ---------------------------------------------------------------------------


def _load_rm_pages(zip_path: Path) -> List[Tuple[int, bytes]]:
    """Load .rm page data from a reMarkable bundle.

    Returns a list of (page_index, rm_bytes) tuples sorted by page order.
    """
    result: List[Tuple[int, bytes]] = []
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
                    result.append((page_idx, zf.read(rm_file)))
                except Exception:
                    log.warning("Failed to read %s in %s", rm_file, zip_path)
    except Exception:
        log.warning("Failed to read zip %s", zip_path, exc_info=True)
    return result


# ---------------------------------------------------------------------------
# Typed notes extraction (keyboard-typed text on reMarkable)
# ---------------------------------------------------------------------------


def extract_typed_notes(zip_path: Path) -> Dict[int, str]:
    """Extract keyboard-typed text notes per page from a reMarkable bundle.

    Returns a dict mapping page index (0-based) to the typed text on that page.
    Only pages with non-empty text are included.
    """
    _ensure_rmscene()
    from rmscene.text import TextDocument

    result: Dict[int, str] = {}
    for page_idx, rm_data in _load_rm_pages(zip_path):
        try:
            tree = read_tree(io.BytesIO(rm_data))
            if tree.root_text is None:
                continue
            doc = TextDocument.from_scene_item(tree.root_text)
            paragraphs = []
            for para in doc.contents:
                style = para.style.value if para.style else si.ParagraphStyle.PLAIN
                text = str(para).strip()
                if not text:
                    continue
                if style == si.ParagraphStyle.HEADING:
                    paragraphs.append(f"### {text}")
                elif style == si.ParagraphStyle.BOLD:
                    paragraphs.append(f"**{text}**")
                elif style in (si.ParagraphStyle.BULLET, si.ParagraphStyle.BULLET2):
                    paragraphs.append(f"- {text}")
                elif style == si.ParagraphStyle.CHECKBOX:
                    paragraphs.append(f"- [ ] {text}")
                elif style == si.ParagraphStyle.CHECKBOX_CHECKED:
                    paragraphs.append(f"- [x] {text}")
                else:
                    paragraphs.append(text)
            if paragraphs:
                result[page_idx] = "\n".join(paragraphs)
        except Exception:
            log.debug("Could not parse typed notes from page %d of %s",
                      page_idx, zip_path.name, exc_info=True)
    if result:
        log.info("Extracted typed notes from %d page(s) of %s",
                 len(result), zip_path.name)
    return result


# ---------------------------------------------------------------------------
# Ink stroke extraction and rendering
# ---------------------------------------------------------------------------

# Eraser tools and pen color map — initialized lazily by _ensure_rmscene()
_ERASER_TOOLS: set = set()
_PEN_COLOR_MAP: dict = {}

# reMarkable page dimensions in device units (classic RM1/RM2)
_RM_WIDTH = 1404.0
_RM_HEIGHT = 1872.0

# reMarkable Paper Pro renders at 227 DPI; stroke coordinates are in
# this native-DPI space (not the classic 1404×1872 viewport).
_RM_PRO_DPI = 227.0
_PDF_DPI = 72.0


def extract_ink_strokes(zip_path: Path) -> Dict[int, List[si.Line]]:
    """Extract handwritten ink strokes (non-highlighter, non-eraser) per page.

    Returns a dict mapping page index (0-based) to a list of Line items.
    Only pages with ink strokes are included.
    """
    _ensure_rmscene()
    result: Dict[int, List[si.Line]] = {}
    for page_idx, rm_data in _load_rm_pages(zip_path):
        try:
            tree = read_tree(io.BytesIO(rm_data))
            strokes = []
            for item in tree.walk():
                if not isinstance(item, si.Line):
                    continue
                if si.Pen.is_highlighter(item.tool):
                    continue
                if item.tool in _ERASER_TOOLS:
                    continue
                if len(item.points) < 2:
                    continue
                strokes.append(item)
            if strokes:
                result[page_idx] = strokes
        except Exception:
            log.debug("Could not parse ink from page %d of %s",
                      page_idx, zip_path.name, exc_info=True)
    if result:
        total = sum(len(v) for v in result.values())
        log.info("Extracted %d ink stroke(s) from %d page(s) of %s",
                 total, len(result), zip_path.name)
    return result


def _is_paper_pro_coords(ink_by_page: Dict[int, List[si.Line]]) -> bool:
    """Detect whether stroke coordinates use Paper Pro native-DPI space.

    Paper Pro coordinates are centered at x=0 with the PDF scaled at
    ~227 DPI, producing stroke x-values far below 0 and y-values well
    above 1872.  Classic RM coordinates stay within 0..1404 / 0..1872
    (with small margins for edge strokes).
    """
    for lines in ink_by_page.values():
        for line in lines:
            for p in line.points:
                if p.x < -200 or p.y > 2100:
                    return True
    return False


def _rm_to_pdf_mapping(
    pdf_w: float, pdf_h: float,
    paper_pro: bool = False,
) -> tuple[float, float, float]:
    """Compute reMarkable → PDF coordinate mapping.

    Returns (rm_scale, x_offset, y_offset) where rm_scale is RM units
    per PDF point and offsets are the RM coordinates of the PDF origin.

    Classic RM (1404×1872 viewport, bestFit):
        pdf_coord = (rm_coord - offset) / rm_scale

    Paper Pro (227 DPI native coordinates, PDF centered at x≈0):
        Same formula with scale=227/72 and PDF horizontally centered.
    """
    if paper_pro:
        rm_scale = _RM_PRO_DPI / _PDF_DPI
        x_off = -(pdf_w * rm_scale) / 2
        y_off = 0.0
    else:
        rm_scale = min(_RM_WIDTH / pdf_w, _RM_HEIGHT / pdf_h)
        rendered_w = pdf_w * rm_scale
        rendered_h = pdf_h * rm_scale
        x_off = (_RM_WIDTH - rendered_w) / 2
        y_off = (_RM_HEIGHT - rendered_h) / 2
    return rm_scale, x_off, y_off


def _render_ink_on_pdf(doc, ink_by_page: Dict[int, List[si.Line]]) -> int:
    """Draw ink strokes onto PDF pages using PyMuPDF's drawing API.

    Detects the coordinate system (classic RM vs Paper Pro) from the
    stroke data, then maps RM coordinates to PDF page coordinates.
    Only renders strokes within the rendered PDF area.
    Returns the number of strokes rendered.
    """
    import pymupdf

    paper_pro = _is_paper_pro_coords(ink_by_page)
    if paper_pro:
        log.debug("Detected Paper Pro coordinate system")

    total_rendered = 0
    for page_idx, lines in ink_by_page.items():
        if page_idx >= len(doc):
            continue
        page = doc[page_idx]
        pdf_w = page.rect.width
        pdf_h = page.rect.height
        rm_scale, x_off, y_off = _rm_to_pdf_mapping(pdf_w, pdf_h, paper_pro)
        rendered_w = pdf_w * rm_scale
        rendered_h = pdf_h * rm_scale

        shape = page.new_shape()
        for line in lines:
            # Skip strokes entirely outside the rendered PDF area
            xs = [p.x for p in line.points]
            ys = [p.y for p in line.points]
            if max(xs) < x_off or min(xs) > x_off + rendered_w:
                continue
            if max(ys) < y_off or min(ys) > y_off + rendered_h:
                continue
            # Map RM coordinates to PDF coordinates, clamping to page
            points = [
                pymupdf.Point(
                    max(0, min((p.x - x_off) / rm_scale, pdf_w)),
                    max(0, min((p.y - y_off) / rm_scale, pdf_h)),
                )
                for p in line.points
            ]
            color = _PEN_COLOR_MAP.get(line.color, (0, 0, 0))
            width = max(0.8, line.thickness_scale / rm_scale)
            shape.draw_polyline(points)
            shape.finish(color=color, width=width, closePath=False)
            total_rendered += 1
        shape.commit()
    return total_rendered


# ---------------------------------------------------------------------------
# Handwritten OCR via Claude Vision
# ---------------------------------------------------------------------------


def _ocr_page_claude(img_bytes: bytes) -> str:
    """OCR handwritten notes from a PDF page image using Claude Vision.

    The image shows a full PDF page with both printed and handwritten text.
    Claude is asked to transcribe ONLY the handwritten annotations,
    ignoring the printed content.

    Args:
        img_bytes: PNG image bytes of the annotated PDF page.

    Returns:
        Recognized handwritten text, or empty string on failure.
    """
    from distillate import config

    if not config.ANTHROPIC_API_KEY:
        return ""

    try:
        import anthropic
    except ImportError:
        log.debug("anthropic package not installed, cannot OCR")
        return ""

    import base64
    image_b64 = base64.b64encode(img_bytes).decode("ascii")

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.CLAUDE_SMART_MODEL,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a research paper page with handwritten "
                            "margin notes overlaid in ink on the printed text. "
                            "Transcribe ONLY the handwritten annotations, "
                            "NOT the printed text underneath. These are a "
                            "reader's notes — expect questions, reactions, "
                            "abbreviations, arrows (→), and shorthand. "
                            "If adjacent words clearly form one thought, "
                            "keep them on a single line. "
                            "Use the printed text to resolve ambiguous "
                            "handwriting — e.g., if the paper discusses "
                            "'trust region optimization' and the note says "
                            "'TRO', output 'TRO [Trust Region Optimization]'. "
                            "Skip any handwriting you cannot read confidently. "
                            "No headers, labels, or commentary — just the "
                            "transcribed notes. "
                            "If there are no legible handwritten notes, "
                            "output exactly: [none]"
                        ),
                    },
                ],
            }],
        )
        text = response.content[0].text.strip()
        # Strip any header/label lines Claude may prepend
        lines = text.split("\n")
        cleaned = [
            ln for ln in lines
            if not re.match(
                r"^(#{1,3}\s|handwritten|annotations?:?\s*$)",
                ln.strip(), re.IGNORECASE,
            )
            and not re.match(
                r"^\[?none\]?[\s\-—.]", ln.strip(), re.IGNORECASE,
            )
            and ln.strip().lower() not in ("[none]", "none")
        ]
        text = "\n".join(cleaned).strip()
        log.debug("Claude page OCR: %d chars", len(text))
        return text
    except Exception:
        log.exception("Claude page OCR failed")
        return ""


def ocr_handwritten_notes(zip_path: Path) -> Dict[int, str]:
    """Extract and OCR handwritten notes from a reMarkable bundle.

    Renders the annotated PDF page (with ink) and sends it to Claude
    Vision (Haiku) for recognition. Claude sees the handwriting in
    context with the printed text, producing much better results.
    Returns a dict mapping page index (0-based) to recognized text.
    Requires ANTHROPIC_API_KEY and pymupdf.
    """
    from distillate import config

    if not config.ANTHROPIC_API_KEY:
        return {}

    ink_by_page = extract_ink_strokes(zip_path)
    if not ink_by_page:
        return {}

    # Render ink onto the PDF so we can grab annotated page images
    try:
        import pymupdf
    except ImportError:
        return {}

    with zipfile.ZipFile(zip_path, "r") as zf:
        pdf_names = [n for n in zf.namelist() if n.endswith(".pdf")]
        if not pdf_names:
            return {}
        pdf_data = zf.read(pdf_names[0])

    doc = pymupdf.open(stream=pdf_data, filetype="pdf")
    _render_ink_on_pdf(doc, ink_by_page)

    results: Dict[int, str] = {}
    for page_idx in ink_by_page:
        if page_idx >= len(doc):
            continue
        if not ink_by_page[page_idx]:
            continue
        # Render page at 2x (144 DPI) — good quality, within 8000px limit
        page = doc[page_idx]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
        img_bytes = pix.tobytes("png")

        text = _ocr_page_claude(img_bytes)
        if text.strip() and text.strip().lower() != "[none]":
            results[page_idx] = text.strip()

    doc.close()

    if results:
        log.info("OCR'd handwritten notes from %d page(s) of %s",
                 len(results), zip_path.name)
    return results
