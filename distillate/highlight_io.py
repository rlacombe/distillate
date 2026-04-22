"""In-place PDF highlight I/O using PyMuPDF.

Reads, writes, and deletes native ``/Highlight`` PDF annotations on a
disk-backed PDF. Writes are **incremental** — they append a new xref + annot
object rather than rewriting the whole file, so saves take milliseconds and
the original bytes are preserved.

Used by the in-app paper reader. Coordinates match the existing renderer
convention (Zotero-style: PDF bottom-left origin). Every user-created
annotation gets ``info["subject"] = "distillate-user"`` so we can
distinguish them from pipeline-generated annotations (which use
``"distillate"``). The returned id is PDF's ``/NM`` field, which PDF.js
surfaces as ``annotation.id`` on the client side.

Pipeline interaction: ``renderer.render_annotated_pdf_from_annotations()``
rewrites the whole PDF and does NOT preserve user highlights. That's an
existing reMarkable-path concern and out of scope here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Match renderer.py styling so pipeline + in-app highlights look identical.
_HIGHLIGHT_COLOR = (1.0, 0.92, 0.3)
_HIGHLIGHT_OPACITY = 0.35
_USER_SUBJECT = "distillate-user"


def _hex_to_rgb(color: str) -> tuple[float, float, float]:
    """Convert ``#rrggbb`` to a 0..1 RGB tuple. Falls back to the default
    soft yellow if parsing fails."""
    if not color or not color.startswith("#") or len(color) != 7:
        return _HIGHLIGHT_COLOR
    try:
        r = int(color[1:3], 16) / 255.0
        g = int(color[3:5], 16) / 255.0
        b = int(color[5:7], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return _HIGHLIGHT_COLOR


def _rgb_to_hex(rgb: Optional[tuple]) -> str:
    """Convert a 0..1 RGB tuple to ``#rrggbb``. Missing → default."""
    if not rgb or len(rgb) < 3:
        return "#ffd400"
    r = max(0, min(255, int(round(rgb[0] * 255))))
    g = max(0, min(255, int(round(rgb[1] * 255))))
    b = max(0, min(255, int(round(rgb[2] * 255))))
    return f"#{r:02x}{g:02x}{b:02x}"


def _norm_text(s: str) -> str:
    """Whitespace-collapsed text, for dedup comparisons."""
    return " ".join((s or "").split())


def _rects_intersect(a, b) -> bool:
    """Bounding-box intersection test for two PyMuPDF Rects.
    Touching edges (zero-area overlap) doesn't count as intersection."""
    return (
        a.x0 < b.x1 and a.x1 > b.x0
        and a.y0 < b.y1 and a.y1 > b.y0
    )


def add_highlight(
    pdf_path: Path,
    page_index: int,
    rects: List[List[float]],
    text: str = "",
    color: str = "#ffd400",
) -> Optional[str]:
    """Append a ``/Highlight`` annotation to a PDF on disk.

    ``rects`` must be in PDF bottom-left coordinates (what PDF.js's
    ``convertToPdfPoint()`` returns). Each rect is ``[x0, y0, x1, y1]``.
    Returns the annotation's ``/NM`` id (string) on success, or ``None``.

    **Idempotent**: if the same text is already highlighted on the same
    page (whitespace-normalized match), the existing annotation's id is
    returned and no duplicate is written. This prevents the "repeated
    save" darkening that happens when multiple identical highlights
    stack at 0.35 opacity.

    Uses ``incremental=True`` so the operation is fast (~ms) and only
    appends bytes to the file.
    """
    try:
        import pymupdf
    except ImportError:
        log.warning("pymupdf not installed — cannot add highlight")
        return None

    if not rects:
        return None

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:
        log.warning("Could not open %s for highlight write: %s", pdf_path, exc)
        return None

    try:
        if page_index < 0 or page_index >= len(doc):
            log.warning(
                "add_highlight: page_index %d out of range (doc has %d pages) for %s",
                page_index, len(doc), pdf_path,
            )
            return None

        page = doc[page_index]
        page_h = page.rect.height

        # Convert the new rects to PyMuPDF top-left space first; we need
        # them for both overlap detection and the actual annot creation.
        new_quads = []
        for rect in rects:
            if len(rect) < 4:
                continue
            x0, y0_bl, x1, y1_bl = rect[0], rect[1], rect[2], rect[3]
            # PDF bottom-left → PyMuPDF top-left (same flip as renderer.py).
            y0_tl = page_h - y1_bl
            y1_tl = page_h - y0_bl
            new_quads.append(pymupdf.Rect(x0, y0_tl, x1, y1_tl))

        if not new_quads:
            log.warning(
                "add_highlight: no valid rects after conversion (input: %r)",
                rects,
            )
            return None

        # Idempotency + overlap-extends pass over existing annotations.
        # Three outcomes:
        #   (a) same normalized text → no-op, return existing id
        #   (b) any rect intersects 1+ existing annots → BRIDGE MERGE:
        #       collect every overlapping annot, union all their quads
        #       with the new quads, delete all originals, write one new
        #       annot. Preserves the FIRST overlapping annot's text,
        #       colour, and id. Handles the A/C-bridged-by-B case.
        #   (c) no overlap → write a fresh annotation (below).
        normalized = _norm_text(text)
        overlapping: List[tuple] = []  # (annot, existing_quads)
        for a in page.annots(types=(pymupdf.PDF_ANNOT_HIGHLIGHT,)):
            info = a.info
            if (info.get("subject") or "") == "distillate":
                continue  # pipeline-generated — don't dedup against

            # Idempotent same-text case short-circuits.
            existing_text = _norm_text(info.get("content", ""))
            if normalized and existing_text == normalized:
                existing_id = (
                    info.get("name") or info.get("id")
                    or f"existing-{page_index}"
                )
                log.info("Duplicate highlight on page %d: returning existing %s",
                         page_index, existing_id)
                return existing_id

            # Collect quads for overlap testing. annot.vertices is a flat
            # list of (x, y) corner points, 4 per quad.
            verts = list(a.vertices or [])
            existing_a_quads: List[Any] = []
            for i in range(0, len(verts), 4):
                quad = verts[i:i + 4]
                if len(quad) < 4:
                    continue
                xs = [p[0] for p in quad]
                ys = [p[1] for p in quad]
                existing_a_quads.append(
                    pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
                )
            if any(_rects_intersect(nq, eq) for nq in new_quads
                                            for eq in existing_a_quads):
                overlapping.append((a, existing_a_quads))

        if overlapping:
            # Bridge merge: preserve the first annot's metadata; union
            # quads from all overlapping annots with the new quads.
            anchor_annot, _ = overlapping[0]
            anchor_info = anchor_annot.info
            anchor_id = (
                anchor_info.get("name") or anchor_info.get("id")
                or f"existing-{page_index}"
            )
            anchor_text = anchor_info.get("content", "") or ""
            # Preserve the anchor's colour — a merge is an extension of
            # the existing highlight, not a repaint.
            colors = anchor_annot.colors or {}
            anchor_stroke = (
                colors.get("stroke") if isinstance(colors, dict) else None
            ) or _hex_to_rgb(color)

            merged_quads: List[Any] = []
            for _, quads in overlapping:
                merged_quads.extend(quads)
            merged_quads.extend(new_quads)

            # Delete all originals before re-adding. Iterate over a
            # copy so we don't mutate while iterating.
            for a, _ in overlapping:
                page.delete_annot(a)

            annot = page.add_highlight_annot(merged_quads)
            annot.set_colors(stroke=anchor_stroke)
            annot.set_opacity(_HIGHLIGHT_OPACITY)
            annot.set_name(anchor_id)
            info = annot.info
            info["title"] = "Distillate"
            info["content"] = anchor_text
            info["subject"] = _USER_SUBJECT
            annot.set_info(info)
            annot.update()
            doc.save(
                str(pdf_path),
                incremental=True,
                encryption=pymupdf.PDF_ENCRYPT_KEEP,
            )
            log.info(
                "Merged %d overlapping highlight(s) into %s on page %d "
                "(total %d rects)",
                len(overlapping), anchor_id, page_index, len(merged_quads),
            )
            return anchor_id

        # No overlap — write a fresh annotation.
        annot = page.add_highlight_annot(new_quads)
        annot.set_colors(stroke=_hex_to_rgb(color))
        annot.set_opacity(_HIGHLIGHT_OPACITY)

        # Generate a stable unique id and set it as the PDF /NM field via
        # set_name(). PyMuPDF's auto info["id"] is an internal counter that
        # resets across open/save cycles and can't be overridden via
        # set_info — set_name writes to /NM proper, which PDF.js exposes
        # as annotation.id on the client side.
        annot_id = f"distillate-{uuid.uuid4().hex[:12]}"
        annot.set_name(annot_id)

        info = annot.info
        info["title"] = "Distillate"
        info["content"] = text or ""
        info["subject"] = _USER_SUBJECT
        annot.set_info(info)
        annot.update()

        doc.save(
            str(pdf_path),
            incremental=True,
            encryption=pymupdf.PDF_ENCRYPT_KEEP,
        )
        log.debug("Added highlight %s to %s (page %d)", annot_id, pdf_path, page_index)
        return annot_id
    except (TypeError, ValueError) as exc:
        # Most commonly: a rect coord arrived as None/NaN and broke the
        # page_h - y subtraction. Route validation should catch this now,
        # but log the specific rect here so follow-up diagnosis is trivial.
        log.warning(
            "add_highlight: coordinate math failed for %s (rects=%r, page_index=%d): %s",
            pdf_path, rects, page_index, exc,
        )
        return None
    except Exception as exc:
        log.warning("add_highlight: unexpected failure on %s: %s",
                    pdf_path, exc, exc_info=True)
        return None
    finally:
        doc.close()


def delete_highlight(pdf_path: Path, annot_id: str) -> bool:
    """Delete a highlight by its ``/NM`` id. Incremental save."""
    try:
        import pymupdf
    except ImportError:
        return False

    if not annot_id:
        return False

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:
        log.warning("Could not open %s for highlight delete: %s", pdf_path, exc)
        return False

    try:
        removed = False
        for page in doc:
            # Materialize the iterator because we mutate during iteration.
            for annot in list(page.annots(types=(pymupdf.PDF_ANNOT_HIGHLIGHT,))):
                # Match against /NM (set_name) — see add_highlight.
                if annot.info.get("name") == annot_id:
                    page.delete_annot(annot)
                    removed = True
                    break
            if removed:
                break

        if removed:
            doc.save(
                str(pdf_path),
                incremental=True,
                encryption=pymupdf.PDF_ENCRYPT_KEEP,
            )
        return removed
    except Exception as exc:
        log.warning("Failed to delete highlight %s from %s: %s",
                    annot_id, pdf_path, exc)
        return False
    finally:
        doc.close()


def read_highlights(pdf_path: Path) -> List[Dict[str, Any]]:
    """Enumerate all user-created highlights in a PDF.

    Returns a list of dicts with ``{id, page_index, page_label, rects,
    text, color, created_at, subject}``. Rects are in PDF bottom-left
    coordinates (matching the Zotero schema). Pipeline-generated annots
    (``subject == "distillate"``) are excluded — callers that want those
    too should filter differently. User annots (``"distillate-user"``) are
    included along with any third-party highlights that lack a subject.
    """
    try:
        import pymupdf
    except ImportError:
        return []

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as exc:
        log.debug("Could not open %s for highlight read: %s", pdf_path, exc)
        return []

    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()  # (normalized_text, page_index)
    try:
        for page_index, page in enumerate(doc):
            page_h = page.rect.height
            for annot in page.annots(types=(pymupdf.PDF_ANNOT_HIGHLIGHT,)):
                info = annot.info
                subject = (info.get("subject") or "").strip()
                # Skip pipeline-generated annotations — the UI treats those
                # as part of the PDF, not user-editable.
                if subject == "distillate":
                    continue
                # Dedup: old PDFs may contain stacked duplicates from the
                # pre-idempotency bug. Return the first occurrence only.
                dedup_key = (_norm_text(info.get("content") or ""), page_index)
                if dedup_key[0] and dedup_key in seen:
                    continue
                seen.add(dedup_key)

                # Vertices is a flat list of 4 (x,y) tuples per quad in
                # PyMuPDF top-left space. Convert back to PDF bottom-left.
                verts = list(annot.vertices or [])
                rects: List[List[float]] = []
                for i in range(0, len(verts), 4):
                    quad = verts[i:i + 4]
                    if len(quad) < 4:
                        continue
                    xs = [p[0] for p in quad]
                    ys_tl = [p[1] for p in quad]
                    x0, x1 = min(xs), max(xs)
                    y0_tl, y1_tl = min(ys_tl), max(ys_tl)
                    # Flip back: y_bl = page_h - y_tl
                    y0_bl = page_h - y1_tl
                    y1_bl = page_h - y0_tl
                    rects.append([x0, y0_bl, x1, y1_bl])

                colors = annot.colors or {}
                stroke = colors.get("stroke") if isinstance(colors, dict) else None

                out.append({
                    # /NM field (set via set_name). Falls back to
                    # PyMuPDF's auto "id" for annots created outside
                    # Distillate (e.g. pre-existing Zotero highlights).
                    "id": info.get("name") or info.get("id", ""),
                    "page_index": page_index,
                    "page_label": str(page_index + 1),
                    "rects": rects,
                    "text": info.get("content", "") or "",
                    "color": _rgb_to_hex(stroke),
                    "created_at": info.get("creationDate", "") or "",
                    "subject": subject,
                })
    finally:
        doc.close()

    return out


def clear_user_highlights(pdf_path: Path) -> int:
    """Remove ALL user-created highlights from a PDF. Leaves pipeline-
    tagged annotations (``subject == 'distillate'``) and any non-highlight
    annotations intact. Returns the count removed.

    Useful for wiping the slate after accumulating extraneous test
    highlights.
    """
    try:
        import pymupdf
    except ImportError:
        return 0

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception:
        return 0

    removed = 0
    try:
        any_deleted = False
        for page in doc:
            for annot in list(page.annots(types=(pymupdf.PDF_ANNOT_HIGHLIGHT,))):
                subject = (annot.info.get("subject") or "")
                if subject == "distillate":
                    continue  # leave pipeline annots alone
                page.delete_annot(annot)
                removed += 1
                any_deleted = True
        if any_deleted:
            doc.save(
                str(pdf_path),
                incremental=True,
                encryption=pymupdf.PDF_ENCRYPT_KEEP,
            )
    finally:
        doc.close()

    if removed:
        log.info("Cleared %d user highlight(s) from %s", removed, pdf_path)
    return removed


def dedupe_pdf_highlights(pdf_path: Path) -> int:
    """Remove stacked duplicate user-highlights from a PDF.

    Finds user-tagged highlights that share (normalized text, page) and
    keeps only the first one on each page. Incremental save. Returns the
    number of duplicates removed. Pipeline-tagged annotations are left
    untouched.

    Useful for cleaning up PDFs affected by the pre-idempotency bug where
    repeated saves stacked annotations at 0.35 opacity, producing
    visually darker regions.
    """
    try:
        import pymupdf
    except ImportError:
        return 0

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception:
        return 0

    removed = 0
    try:
        any_deleted = False
        for page in doc:
            seen: set[str] = set()
            for annot in list(page.annots(types=(pymupdf.PDF_ANNOT_HIGHLIGHT,))):
                info = annot.info
                if (info.get("subject") or "") == "distillate":
                    continue
                norm = _norm_text(info.get("content", ""))
                if not norm:
                    continue
                if norm in seen:
                    page.delete_annot(annot)
                    removed += 1
                    any_deleted = True
                else:
                    seen.add(norm)
        if any_deleted:
            doc.save(
                str(pdf_path),
                incremental=True,
                encryption=pymupdf.PDF_ENCRYPT_KEEP,
            )
    finally:
        doc.close()

    if removed:
        log.info("Removed %d duplicate highlight(s) from %s", removed, pdf_path)
    return removed


def migrate_local_highlights(pdf_path: Path, local_highlights: list) -> int:
    """One-time migration for papers with pre-existing ``local_highlights``
    stored in SQLite state. Writes each into the PDF as a native annotation.
    Returns the number successfully migrated. Safe to call multiple times —
    but callers should clear the state list after a successful call.
    """
    if not local_highlights or not pdf_path.exists():
        return 0

    migrated = 0
    for h in local_highlights:
        text = h.get("text", "")
        page_index = h.get("page_index", 0)
        rects = h.get("rects", [])
        color = h.get("color", "#ffd400")
        annot_id = add_highlight(pdf_path, page_index, rects, text=text, color=color)
        if annot_id:
            migrated += 1
    return migrated
