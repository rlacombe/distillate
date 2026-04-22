"""Generic PDF annotation rendering.

This module only contains the Zotero-native annotation renderer. The
reMarkable .rmscene parsing path lives in
``distillate.integrations.remarkable.renderer`` and is loaded lazily.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger(__name__)

# Highlight annotation style
_HIGHLIGHT_COLOR = (1.0, 0.92, 0.3)  # soft yellow
_HIGHLIGHT_OPACITY = 0.35


def render_annotated_pdf_from_annotations(
    pdf_bytes: bytes,
    annotations: List[Dict[str, Any]],
    output_path: Path,
) -> bool:
    """Render highlight annotations onto a PDF using Zotero annotation rects.

    Takes raw annotation dicts (from ``zotero_client.get_raw_annotations()``)
    with pre-computed ``page_index`` and ``rects`` in PDF bottom-left
    coordinates. Converts to PyMuPDF top-left coordinates and adds
    highlight annotations.
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

            quads = []
            for rect in rects:
                if len(rect) < 4:
                    continue
                x0, y0_bl, x1, y1_bl = rect[0], rect[1], rect[2], rect[3]
                # PDF bottom-left → PyMuPDF top-left
                y0_tl = page_h - y1_bl
                y1_tl = page_h - y0_bl
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
