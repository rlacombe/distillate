"""Sync pipeline and paper processing.

Contains the main sync loop (run_sync), paper upload/processing,
engagement scoring, and promotion logic.
"""

import logging
import tempfile
from pathlib import Path

import requests

log = logging.getLogger("distillate")


def _fetch_pdf_bytes(
    att_key: str,
    item_key: str = "",
    paper_url: str = "",
    title: str = "",
    check_fresh_attachment: bool = False,
) -> tuple[bytes | None, str]:
    """Download PDF with fallback: Zotero cloud -> WebDAV -> URL.

    When *check_fresh_attachment* is True and the initial download fails,
    re-queries Zotero children for a newer PDF attachment before trying
    WebDAV and URL fallbacks.

    Returns (pdf_bytes, possibly_updated_att_key).
    """
    from distillate import zotero_client

    pdf_bytes = None

    # 1. Try Zotero cloud download
    if att_key:
        try:
            pdf_bytes = zotero_client.download_pdf(att_key)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                log.info("PDF not synced to Zotero cloud for '%s'", title)
            else:
                raise

    # 2. Optionally re-check for a newer PDF attachment
    if pdf_bytes is None and check_fresh_attachment and item_key:
        fresh_att = zotero_client.get_pdf_attachment(item_key)
        if fresh_att and fresh_att["key"] != att_key:
            att_key = fresh_att["key"]
            log.info("Found new PDF attachment for '%s'", title)
            try:
                pdf_bytes = zotero_client.download_pdf(att_key)
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    log.info("New attachment also has no file for '%s'", title)
                else:
                    raise

    # 3. Fall back to WebDAV
    if pdf_bytes is None and att_key:
        pdf_bytes = zotero_client.download_pdf_from_webdav(att_key)
        if pdf_bytes:
            log.info("Downloaded PDF from WebDAV for '%s'", title)

    # 4. Fall back to direct URL download
    if pdf_bytes is None and paper_url:
        pdf_bytes = zotero_client.download_pdf_from_url(paper_url)
        if pdf_bytes:
            log.info("Downloaded PDF from URL for '%s'", title)

    return pdf_bytes, att_key


def _find_papers(query: str, state) -> list[tuple[str, dict]]:
    """Resolve a query to a list of (item_key, doc) matches.

    Tries (in order): index number, exact citekey, citekey substring,
    then title substring.
    """
    query = query.strip().strip('"').strip("'")

    # Try index number
    if query.isdigit():
        idx = int(query)
        key = state.key_for_index(idx)
        if key:
            doc = state.get_document(key)
            if doc:
                return [(key, doc)]

    query_lower = query.lower()
    matches = []
    for key, doc in state.documents.items():
        ck = doc.get("metadata", {}).get("citekey", "")
        if ck and ck.lower() == query_lower:
            return [(key, doc)]  # exact citekey match
        if (query_lower in ck.lower()
                or query_lower in doc.get("title", "").lower()):
            matches.append((key, doc))
    return matches


def _compute_engagement(
    highlights: dict | None, page_count: int,
) -> int:
    """Compute an engagement score (0–100) from highlights and page count.

    Components (weighted):
      - Highlight density (30%): highlights per page, saturates at 1 per page
      - Page coverage (40%): fraction of pages with at least one highlight
      - Highlight volume (30%): absolute count, saturates at 20
    """
    if not highlights:
        return 0
    highlight_count = sum(len(v) for v in highlights.values())
    highlighted_pages = len(highlights)
    pages = max(page_count, 1)

    density = min(highlight_count / pages, 1.0)
    coverage = min(highlighted_pages / pages, 1.0)
    volume = min(highlight_count / 20, 1.0)

    return round((density * 0.3 + coverage * 0.4 + volume * 0.3) * 100)


def _process_paper_bundle(
    doc: dict,
    state,
    *,
    rm_folder: str = "",
    refresh_metadata: bool = False,
    delete_inbox_pdf: bool = False,
    move_on_rm: bool = False,
    ensure_read_tag: bool = False,
    recreate_note: bool = False,
    use_rm_stat: bool = False,
    use_rm_geta_fallback: bool = False,
    date_read_override: str | None = None,
) -> bool:
    """Process a single paper: extract highlights, render PDF, create note.

    Shared logic for both _reprocess() and run_sync() Step 2.
    Returns True on success, False on skip/failure.
    """
    from distillate import config
    from distillate import obsidian
    from distillate import renderer
    from distillate import summarizer
    from distillate import zotero_client

    zotero_mode = config.is_zotero_reader()
    if not zotero_mode:
        from distillate.integrations.remarkable import client as remarkable_client
        from distillate.integrations.remarkable import renderer as rm_renderer
    else:
        rm_renderer = None  # unused in Zotero path

    title = doc["title"]
    rm_name = doc["remarkable_doc_name"]
    item_key = doc["zotero_item_key"]
    att_key = doc.get("zotero_attachment_key", "")

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "paper.pdf"
        zip_path = Path(tmpdir) / f"{rm_name}.zip"

        bundle_ok = False
        if zotero_mode:
            highlights = zotero_client.get_highlight_annotations(att_key) if att_key else {}
            typed_notes = {}
            handwritten_notes = {}

            pdf_bytes, att_key = _fetch_pdf_bytes(att_key, title=title)
            render_ok = False
            page_count = 0
            if pdf_bytes:
                raw_anns = zotero_client.get_raw_annotations(att_key)
                render_ok = renderer.render_annotated_pdf_from_annotations(
                    pdf_bytes, raw_anns, pdf_path,
                )
                try:
                    import pymupdf
                    doc_pdf = pymupdf.open(stream=pdf_bytes, filetype="pdf")
                    page_count = len(doc_pdf)
                    doc_pdf.close()
                except Exception:
                    log.debug("Failed to get page count from PDF for '%s'", title, exc_info=True)
            else:
                log.warning("Could not download PDF for '%s', skipping", title)
                return False
        else:
            bundle_ok = remarkable_client.download_document_bundle_to(
                rm_folder, rm_name, zip_path,
            )
            if bundle_ok and zip_path.exists():
                highlights = rm_renderer.extract_highlights(zip_path)
                typed_notes = rm_renderer.extract_typed_notes(zip_path)
                try:
                    handwritten_notes = rm_renderer.ocr_handwritten_notes(zip_path)
                except Exception:
                    handwritten_notes = {}
                    log.debug("Handwritten OCR skipped", exc_info=True)

                page_count = 0
                if use_rm_stat:
                    stat = remarkable_client.stat_document(rm_folder, rm_name)
                    page_count = (stat or {}).get("page_count", 0)
                if not page_count:
                    page_count = rm_renderer.get_page_count(zip_path)

                render_ok = rm_renderer.render_annotated_pdf(zip_path, pdf_path)
            else:
                log.warning("Could not download bundle for '%s', skipping", title)
                highlights = None
                typed_notes = None
                handwritten_notes = None
                render_ok = False
                page_count = 0

            if not render_ok and use_rm_geta_fallback:
                log.info("Falling back to rmapi geta for '%s'", rm_name)
                render_ok = remarkable_client.download_annotated_pdf_to(
                    rm_folder, rm_name, pdf_path,
                )

        # Save annotated PDF
        citekey = doc.get("metadata", {}).get("citekey", "")
        pdf_filename = None
        saved = None
        if render_ok and pdf_path.exists():
            annotated_bytes = pdf_path.read_bytes()
            saved = obsidian.save_annotated_pdf(title, annotated_bytes, citekey=citekey)
            if saved:
                pdf_filename = saved.name
            log.info("Saved annotated PDF to Obsidian vault")
        else:
            log.warning("Could not render annotated PDF for '%s'", title)

        # Engagement score
        engagement = _compute_engagement(highlights, page_count)
        doc["engagement"] = engagement

        # Update linked attachment in Zotero
        linked = zotero_client.get_linked_attachment(item_key)
        if saved:
            new_att = zotero_client.create_linked_attachment(
                item_key, saved.name, str(saved),
            )
            if new_att and linked:
                zotero_client.delete_attachment(linked["key"])
        elif linked:
            zotero_client.delete_attachment(linked["key"])

        if ensure_read_tag:
            zotero_client.add_tag(item_key, config.ZOTERO_TAG_READ)

        # Metadata
        if refresh_metadata:
            items = zotero_client.get_items_by_keys([item_key])
            if items:
                meta = zotero_client.extract_metadata(items[0])
                doc["metadata"] = meta
            else:
                meta = doc.get("metadata", {})
        else:
            meta = doc.get("metadata", {})

        # Flatten highlights and notes for summarizer
        flat_highlights = [
            h for page_hl in (highlights or {}).values() for h in page_hl
        ] or None
        flat_notes = [
            text for _, text in sorted(handwritten_notes.items())
        ] if handwritten_notes else None

        # Extract key learnings and generate summary
        learnings = summarizer.extract_insights(
            title,
            highlights=flat_highlights,
            abstract=meta.get("abstract", ""),
            reader_notes=flat_notes,
        )
        summary, one_liner = summarizer.summarize_read_paper(
            title, abstract=meta.get("abstract", ""),
            key_learnings=learnings,
            reader_notes=flat_notes,
            hf_summary=meta.get("hf_summary", ""),
            s2_tldr=meta.get("s2_tldr", ""),
        )

        # Compute highlight stats
        flat_hl = [h for hl in (highlights or {}).values() for h in hl]
        hl_pages = len(highlights) if highlights else 0
        hl_words = sum(len(h.split()) for h in flat_hl)

        # Determine date_read
        if date_read_override is not None:
            date_read = date_read_override
        else:
            date_read = doc.get("processed_at", "")

        # Recreate Obsidian note infrastructure if needed
        if recreate_note:
            obsidian.ensure_dataview_note()
            obsidian.ensure_stats_note()
            obsidian.ensure_bases_note()
            obsidian.delete_paper_note(title, citekey=citekey)

        citekey = meta.get("citekey", citekey)
        obsidian.create_paper_note(
            title=title,
            authors=doc["authors"],
            date_added=doc["uploaded_at"],
            zotero_item_key=item_key,
            highlights=highlights or None,
            pdf_filename=pdf_filename,
            doi=meta.get("doi", ""),
            abstract=meta.get("abstract", ""),
            url=meta.get("url", ""),
            publication_date=meta.get("publication_date", ""),
            journal=meta.get("journal", ""),
            summary=summary,
            one_liner=one_liner,
            topic_tags=meta.get("tags"),
            citation_count=meta.get("citation_count", 0),
            key_learnings=learnings,
            date_read=date_read,
            engagement=engagement,
            highlighted_pages=hl_pages,
            highlight_word_count=hl_words,
            page_count=page_count,
            citekey=citekey,
            typed_notes=typed_notes or None,
            handwritten_notes=handwritten_notes or None,
        )

        # Obsidian deep link
        obsidian_uri = obsidian.get_obsidian_uri(title, citekey=citekey)
        if obsidian_uri:
            zotero_client.create_obsidian_link(item_key, obsidian_uri)

        # Refresh vault wiki index after paper ingest
        try:
            from distillate.vault_wiki import regenerate_index
            regenerate_index()
        except Exception:
            pass

        # Extract Zotero positions while zip is available
        zotero_positions = []
        if not zotero_mode and config.SYNC_HIGHLIGHTS and highlights and bundle_ok:
            zotero_positions = rm_renderer.extract_zotero_highlights(zip_path)

    # Back-propagate highlights to Zotero as PDF annotations
    highlights_synced = False
    if config.SYNC_HIGHLIGHTS and highlights and zotero_positions:
        att = zotero_client.get_pdf_attachment(item_key)
        if not att:
            att = zotero_client.get_linked_attachment(item_key)
        if att:
            from datetime import datetime, timezone
            ann_keys = zotero_client.create_highlight_annotations(
                att["key"], zotero_positions,
            )
            doc["highlights_synced_at"] = datetime.now(timezone.utc).isoformat()
            doc["zotero_annotation_count"] = len(ann_keys)
            highlights_synced = True

    # Sync note to Zotero (only when highlights are NOT on the PDF as annotations)
    if not highlights_synced:
        zotero_note_html = zotero_client.build_note_html(
            summary=summary, highlights=highlights or None,
        )
        note_key = zotero_client.set_note(
            item_key, zotero_note_html,
            note_key=doc.get("zotero_note_key", ""),
        )
        if note_key:
            doc["zotero_note_key"] = note_key

    if delete_inbox_pdf:
        obsidian.delete_inbox_pdf(title, citekey=citekey)

    # Append to reading log
    obsidian.append_to_reading_log(title, one_liner, date_read=date_read, citekey=citekey)

    # Move to Saved on reMarkable
    if move_on_rm and not zotero_mode:
        remarkable_client.move_document(
            rm_name, rm_folder, config.RM_FOLDER_SAVED,
        )

    # Update state
    doc["highlight_count"] = len(flat_hl)
    doc["highlighted_pages"] = hl_pages
    doc["highlight_word_count"] = hl_words
    doc["page_count"] = page_count
    state.mark_processed(item_key, summary=one_liner)
    state.save()

    return True


def _reprocess(args: list[str]) -> None:
    """Re-run highlight extraction + PDF rendering on processed papers."""
    from distillate import config
    from distillate.state import State

    config.setup_logging()

    state = State()
    processed = state.documents_with_status("processed")

    if not processed:
        print("No processed papers to reprocess.")
        return

    # Filter by index, citekey, or title substring
    if args:
        query = " ".join(args)
        matches = _find_papers(query, state)
        if not matches:
            print(f"No paper matching '{query}'")
            return
        # Only keep processed papers from matches
        match_keys = {k for k, _ in matches}
        processed = [d for d in processed if d["zotero_item_key"] in match_keys]
        if not processed:
            print(f"No processed paper matching '{query}'")
            return

    print(f"Reprocessing {len(processed)} paper(s)...")

    for doc in processed:
        title = doc["title"]
        print(f"  Reprocessing: {title}")
        ok = _process_paper_bundle(
            doc, state,
            rm_folder=config.RM_FOLDER_SAVED,
            refresh_metadata=True,
            ensure_read_tag=True,
            recreate_note=True,
            date_read_override=doc.get("processed_at", ""),
        )
        if ok:
            print(f"  Done: {title}")
        else:
            print(f"  Skipped: {title}")


def _upload_paper(paper, state, existing_on_rm, skip_remarkable=False) -> bool:
    """Process a single paper: download PDF, upload to RM, tag, track in state.

    Returns True if the paper was processed (uploaded or marked awaiting_pdf),
    False if skipped (duplicate, error).
    skip_remarkable=True skips the RM upload (papers get uploaded on first sync).
    """
    from distillate import config
    from distillate import obsidian
    from distillate import semantic_scholar
    from distillate import zotero_client

    zotero_mode = config.is_zotero_reader()
    if not zotero_mode:
        from distillate.integrations.remarkable import client as remarkable_client

    item_key = paper["key"]
    meta = zotero_client.extract_metadata(paper)
    title = meta["title"]
    authors = meta["authors"]

    log.info("Processing: %s", title)

    # Duplicate check by DOI then title
    doi = meta.get("doi", "")
    existing = state.find_by_doi(doi) if doi else None
    if existing is None:
        existing = state.find_by_title(title)
    if existing is not None:
        log.info(
            "Skipping duplicate: '%s' (already tracked as %s)",
            title, existing["zotero_item_key"],
        )
        zotero_client.add_tag(item_key, config.ZOTERO_TAG_INBOX)
        return False

    # Find PDF attachment
    attachment = zotero_client.get_pdf_attachment(item_key)
    att_key = attachment["key"] if attachment else ""
    att_md5 = attachment["data"].get("md5", "") if attachment else ""

    # Upload to reMarkable (skip in Zotero mode, or if already there)
    if zotero_mode:
        # Zotero reader mode: download PDF for local save but no RM upload
        pdf_bytes, att_key = _fetch_pdf_bytes(
            att_key, paper_url=meta.get("url", ""), title=title,
        )

        if pdf_bytes is not None:
            citekey = meta.get("citekey", "")
            saved = obsidian.save_inbox_pdf(title, pdf_bytes, citekey=citekey)
            if saved:
                new_att = zotero_client.create_linked_attachment(
                    item_key, saved.name, str(saved),
                )
                if new_att and att_key and not config.KEEP_ZOTERO_PDF:
                    zotero_client.delete_attachment(att_key)
    elif skip_remarkable:
        log.info("Skipping RM upload (will upload on first sync): %s", title)
    elif title in existing_on_rm:
        log.info("Already on reMarkable, skipping upload: %s", title)
    else:
        pdf_bytes, att_key = _fetch_pdf_bytes(
            att_key, paper_url=meta.get("url", ""), title=title,
        )

        if pdf_bytes is None:
            log.warning(
                "No PDF available for '%s', will retry next run", title,
            )
            state.add_document(
                zotero_item_key=item_key,
                zotero_attachment_key=att_key,
                zotero_attachment_md5=att_md5,
                remarkable_doc_name=remarkable_client.sanitize_filename(title),
                title=title,
                authors=authors,
                status="awaiting_pdf",
                metadata=meta,
            )
            return True
        log.info("Downloaded PDF (%d bytes)", len(pdf_bytes))
        remarkable_client.upload_pdf_bytes(
            pdf_bytes, config.RM_FOLDER_INBOX, title
        )
        # Save original to Obsidian Inbox folder
        citekey = meta.get("citekey", "")
        saved = obsidian.save_inbox_pdf(title, pdf_bytes, citekey=citekey)
        # Create linked attachment, optionally delete imported
        if saved:
            new_att = zotero_client.create_linked_attachment(
                item_key, saved.name, str(saved),
            )
            if new_att and att_key and not config.KEEP_ZOTERO_PDF:
                zotero_client.delete_attachment(att_key)
            elif not new_att:
                log.warning("Could not create linked attachment for '%s', keeping imported PDF", title)
        else:
            log.warning("Could not save local PDF for '%s', keeping Zotero copy", title)

    # Semantic Scholar enrichment
    try:
        had_date = bool(meta.get("publication_date"))
        had_unknown_author = "unknown" in meta.get("citekey", "")
        s2_data = semantic_scholar.lookup_paper(
            doi=meta.get("doi", ""), title=title,
            url=meta.get("url", ""),
        )
        if s2_data:
            semantic_scholar.enrich_metadata(meta, s2_data)
            log.info(
                "S2: %d citations",
                s2_data["citation_count"],
            )
            # Regenerate citekey if S2 filled a missing date or unknown author
            needs_regen = (not had_date and meta.get("publication_date"))
            if had_unknown_author and s2_data.get("authors"):
                needs_regen = True
                # Also update top-level authors list
                authors = meta["authors"]
            if needs_regen:
                meta["citekey"] = zotero_client._generate_citekey(
                    meta["authors"], meta["title"], meta["publication_date"],
                )
                log.info("Regenerated citekey after S2 enrichment: %s", meta["citekey"])
    except Exception:
        log.debug("S2 lookup failed for '%s'", title, exc_info=True)

    # HuggingFace enrichment (GitHub repo, stars)
    try:
        from distillate import huggingface
        arxiv_id = semantic_scholar.extract_arxiv_id(
            meta.get("doi", ""), meta.get("url", ""),
        )
        if arxiv_id:
            hf_data = huggingface.lookup_paper(arxiv_id)
            if hf_data:
                meta.setdefault("github_repo", hf_data.get("github_repo"))
                meta.setdefault("github_stars", hf_data.get("github_stars"))
                if hf_data.get("ai_summary"):
                    meta.setdefault("hf_summary", hf_data["ai_summary"])
                if hf_data.get("github_repo"):
                    log.info("HF: GitHub %s (%s stars)",
                             hf_data["github_repo"],
                             hf_data.get("github_stars", "?"))
    except Exception:
        log.debug("HF lookup failed for '%s'", title, exc_info=True)

    # Tag in Zotero
    zotero_client.add_tag(item_key, config.ZOTERO_TAG_INBOX)

    # Track in state
    if zotero_mode:
        status = "tracked"
        rm_doc_name = title  # no RM name needed
    elif skip_remarkable:
        status = "awaiting_pdf"
        rm_doc_name = remarkable_client.sanitize_filename(title)
    else:
        status = "on_remarkable"
        rm_doc_name = remarkable_client.sanitize_filename(title)
    state.add_document(
        zotero_item_key=item_key,
        zotero_attachment_key=att_key,
        zotero_attachment_md5=att_md5,
        remarkable_doc_name=rm_doc_name,
        title=title,
        authors=authors,
        status=status,
        metadata=meta,
    )
    state.save()
    if zotero_mode:
        log.info("Tracking paper: %s", title)
    else:
        log.info("Sent to reMarkable: %s", title)
    return True


def _demote_and_promote(state, pick_keys: list, verbose: bool = False, demote: bool = True) -> dict:
    """Demote old promoted papers, promote new picks on reMarkable.

    Shared logic used by both _suggest() (manual) and _auto_promote() (sync).
    Caller must hold the lock and pass a loaded State.
    In Zotero reader mode, only updates state (no RM folder moves).
    If demote=False, skip demotion and just add to existing promoted list.

    Returns a result dict with counts of what actually happened.
    """
    from datetime import datetime, timezone

    from distillate import config

    result = {
        "promoted": [],       # titles actually promoted
        "demoted": [],        # titles actually demoted
        "kept": [],           # titles kept (user started reading)
        "skipped": [],        # titles skipped (not found on device)
        "already_promoted": [],  # titles already in promoted list
        "total_promoted": 0,  # total papers in promoted list after operation
    }

    if config.is_zotero_reader():
        # In Zotero mode, just track promoted keys in state (no RM moves)
        promoted_keys = list(state.promoted_papers)
        for key in pick_keys:
            if key not in promoted_keys:
                doc = state.get_document(key)
                if doc and doc["status"] == "tracked":
                    doc["promoted_at"] = datetime.now(timezone.utc).isoformat()
                    promoted_keys.append(key)
                    result["promoted"].append(doc["title"])
                    if verbose:
                        print(f"  Promoted: {doc['title']}")
            else:
                doc = state.get_document(key)
                result["already_promoted"].append(doc["title"] if doc else key)
        state.promoted_papers = promoted_keys
        state.pending_promotions = []
        state.save()
        result["total_promoted"] = len(promoted_keys)
        return result

    from distillate.integrations.remarkable import client as remarkable_client

    # Demote old promoted papers back to Inbox (skip if user started reading)
    old_promoted = state.promoted_papers
    remaining_promoted = list(old_promoted)
    if demote and old_promoted:
        remaining_promoted = []
        papers_root_docs = remarkable_client.list_folder(config.RM_FOLDER_PAPERS)
        for key in old_promoted:
            doc = state.get_document(key)
            if not doc or doc["status"] != "on_remarkable":
                continue
            rm_name = doc["remarkable_doc_name"]
            if rm_name not in papers_root_docs:
                log.info("Skipping demotion (not at Papers root): %s", doc["title"])
                result["skipped"].append(doc["title"])
                continue

            stat = remarkable_client.stat_document(config.RM_FOLDER_PAPERS, rm_name)
            if stat and stat.get("current_page", 0) > 0:
                log.info("User started reading, not demoting: %s", doc["title"])
                remaining_promoted.append(key)
                result["kept"].append(doc["title"])
                continue

            if stat is None:
                log.info("Could not stat document, skipping demotion: %s", doc["title"])
                remaining_promoted.append(key)
                result["kept"].append(doc["title"])
                continue

            remarkable_client.move_document(
                rm_name, config.RM_FOLDER_PAPERS, config.RM_FOLDER_INBOX,
            )
            result["demoted"].append(doc["title"])
            log.info("Demoted: %s", doc["title"])
        state.promoted_papers = remaining_promoted
        state.save()

    # Move picked papers from Inbox to Papers root
    inbox_docs = remarkable_client.list_folder(config.RM_FOLDER_INBOX)
    promoted_keys = list(remaining_promoted)

    for key in pick_keys:
        if key in promoted_keys:
            doc = state.get_document(key)
            result["already_promoted"].append(doc["title"] if doc else key)
            continue
        doc = state.get_document(key)
        if not doc or doc["status"] != "on_remarkable":
            result["skipped"].append(doc["title"] if doc else key)
            continue
        rm_name = doc["remarkable_doc_name"]
        if rm_name in inbox_docs:
            remarkable_client.move_document(
                rm_name, config.RM_FOLDER_INBOX, config.RM_FOLDER_PAPERS,
            )
            doc["promoted_at"] = datetime.now(timezone.utc).isoformat()
            promoted_keys.append(key)
            result["promoted"].append(doc["title"])
            if verbose:
                print(f"  Promoted: {doc['title']}")
            log.info("Promoted: %s", doc["title"])
        else:
            result["skipped"].append(doc["title"])
            log.warning("Paper not found in Inbox on reMarkable: %s", doc["title"])

    state.promoted_papers = promoted_keys
    state.pending_promotions = []
    state.save()
    result["total_promoted"] = len(promoted_keys)
    return result


def _auto_promote(state) -> None:
    """Promote pending picks on reMarkable.

    Called during sync. Checks two sources:
    1. Local state.pending_promotions (from local --suggest-email)
    2. Gist pending.json (from GH Actions --suggest-email)
    """
    from distillate import config
    from distillate.digest import fetch_pending_from_gist

    # Source 1: local pending promotions
    local_picks = state.pending_promotions
    if local_picks:
        log.info("Found %d local pending pick(s), promoting...", len(local_picks))
        _demote_and_promote(state, local_picks)
        state.save()
        return  # Don't also process Gist picks in the same run

    # Source 2: Gist (GH Actions)
    if not config.STATE_GIST_ID:
        return

    pending = fetch_pending_from_gist()
    if not pending:
        return

    timestamp = pending.get("timestamp", "")
    last_processed = state._data.get("last_pending_timestamp", "")
    if timestamp and timestamp <= last_processed:
        return  # Already processed this batch

    picks = pending.get("picks", [])
    if not picks:
        return

    log.info("Found %d pending pick(s) from GH Actions, promoting...", len(picks))
    _demote_and_promote(state, picks)
    state._data["last_pending_timestamp"] = timestamp
    state.save()


def run_sync() -> None:
    """Main sync loop: poll Zotero, process read papers, update state."""
    from distillate import config
    from distillate import notify
    from distillate import obsidian
    from distillate import zotero_client
    from distillate.state import State, acquire_lock, release_lock

    zotero_mode = config.is_zotero_reader()
    if not zotero_mode:
        from distillate.integrations.remarkable import client as remarkable_client

    config.setup_logging()

    # Prevent overlapping runs
    if not acquire_lock():
        log.warning("Another instance is running (lock held), exiting")
        return

    try:
        state = State()
        sent_count = 0
        synced_count = 0

        # Pull cloud state before starting (picks up papers from other devices)
        from distillate.cloud_sync import cloud_sync_available, push_state
        if cloud_sync_available():
            from distillate.cloud_sync import pull_state
            pull_state(state)

        # Backfill zotero_date_added for existing papers that predate the field
        _missing_date = [
            (k, doc) for k, doc in state.documents.items()
            if not doc.get("zotero_date_added")
        ]
        if _missing_date:
            log.info("Backfilling zotero_date_added for %d papers...", len(_missing_date))
            try:
                _batch_items = zotero_client.get_items_by_keys(
                    [k for k, _ in _missing_date]
                )
                _by_key = {item["key"]: item for item in _batch_items}
                _updated = 0
                for k, doc in _missing_date:
                    item = _by_key.get(k)
                    if item:
                        date_added = item.get("data", {}).get("dateAdded", "")
                        if date_added:
                            doc["zotero_date_added"] = date_added
                            _updated += 1
                if _updated:
                    state.save()
                    log.info("Backfilled zotero_date_added for %d papers", _updated)
            except Exception:
                log.warning("Could not backfill zotero_date_added", exc_info=True)

        # Migrate legacy Obsidian files on every run
        obsidian.ensure_dataview_note()   # removes Papers List.md
        obsidian.ensure_stats_note()      # renames to Distillate Stats
        obsidian.ensure_bases_note()      # replaces Papers.base → Distillate Papers.base

        # Vault wiki structural files (schema + index)
        try:
            from distillate.vault_wiki import generate_schema, regenerate_index
            generate_schema()
            regenerate_index()
        except Exception:
            pass

        # Move PDFs from Saved/ into Saved/<subfolder>/ (one-time migration)
        moved_pdfs = obsidian.migrate_pdfs_to_subdir()
        if moved_pdfs:
            log.info("Migrated %d PDFs to %s/", len(moved_pdfs), config.PDF_SUBFOLDER)
            # Update Zotero linked attachments to point to new paths
            for new_path in moved_pdfs:
                citekey = new_path.stem
                doc = state.find_by_citekey(citekey)
                if doc:
                    item_key = doc.get("zotero_item_key", "")
                    if item_key:
                        zotero_client.update_linked_attachment_path(
                            item_key, new_path.name, str(new_path),
                        )

        # -- Retry papers awaiting PDF sync --
        awaiting = state.documents_with_status("awaiting_pdf")
        if awaiting:
            print(f"\n  Retrying {len(awaiting)} paper{'s' if len(awaiting) != 1 else ''} awaiting PDF...")
            log.info("Retrying %d papers awaiting PDF sync...", len(awaiting))
            if not zotero_mode:
                remarkable_client.ensure_folders()
                on_rm = set(remarkable_client.list_folder(config.RM_FOLDER_INBOX))
            else:
                on_rm = set()
            for doc in awaiting:
                title = doc["title"]
                att_key = doc["zotero_attachment_key"]
                item_key = doc["zotero_item_key"]
                meta = doc.get("metadata", {})
                rm_name = doc.get("remarkable_doc_name", "")
                try:
                    # Check if paper is already on reMarkable (manually uploaded)
                    if not zotero_mode and (rm_name and rm_name in on_rm or title in on_rm):
                        log.info("'%s' already on reMarkable, updating status", title)
                        zotero_client.add_tag(item_key, config.ZOTERO_TAG_INBOX)
                        state.set_status(item_key, "on_remarkable")
                        state.save()
                        sent_count += 1
                        print(f"  Found \"{title}\" on reMarkable.")
                        continue

                    pdf_bytes, att_key = _fetch_pdf_bytes(
                        att_key, item_key=item_key,
                        paper_url=meta.get("url", ""),
                        title=title,
                        check_fresh_attachment=True,
                    )
                    if att_key != doc["zotero_attachment_key"]:
                        doc["zotero_attachment_key"] = att_key

                    if pdf_bytes is None:
                        log.info("No PDF available yet for '%s', will retry", title)
                        print(f"  Still awaiting PDF: \"{title}\"")
                        continue

                    if zotero_mode:
                        # Zotero mode: save locally and transition to tracked
                        citekey = meta.get("citekey", "")
                        saved = obsidian.save_inbox_pdf(title, pdf_bytes, citekey=citekey)
                        if saved:
                            new_att = zotero_client.create_linked_attachment(
                                item_key, saved.name, str(saved),
                            )
                            if new_att and att_key and not config.KEEP_ZOTERO_PDF:
                                zotero_client.delete_attachment(att_key)
                        zotero_client.add_tag(item_key, config.ZOTERO_TAG_INBOX)
                        state.set_status(item_key, "tracked")
                        state.save()
                        sent_count += 1
                        print(f"  Tracking: \"{title}\"")
                    else:
                        remarkable_client.upload_pdf_bytes(
                            pdf_bytes, config.RM_FOLDER_INBOX, title
                        )
                        citekey = meta.get("citekey", "")
                        saved = obsidian.save_inbox_pdf(title, pdf_bytes, citekey=citekey)
                        if saved:
                            new_att = zotero_client.create_linked_attachment(
                                item_key, saved.name, str(saved),
                            )
                            if new_att and att_key and not config.KEEP_ZOTERO_PDF:
                                zotero_client.delete_attachment(att_key)
                        else:
                            log.warning("Could not save local PDF for '%s', keeping Zotero copy", title)
                        zotero_client.add_tag(item_key, config.ZOTERO_TAG_INBOX)
                        state.set_status(item_key, "on_remarkable")
                        state.save()
                        sent_count += 1
                        print(f"  Sent to reMarkable: \"{title}\"")
                        log.info("Sent to reMarkable: %s", title)
                except Exception:
                    log.exception("Failed to retry '%s'", title)
                    print(f"  Failed to sync \"{title}\" — check log for details.")
            state.save()

        # -- Step 1: Poll Zotero for new papers --
        _coll_key = config.ZOTERO_COLLECTION_KEY
        if _coll_key:
            try:
                _coll_name = zotero_client.get_collection_name(_coll_key)
            except Exception:
                _coll_name = _coll_key
            print(f"  Checking Zotero (collection: {_coll_name})...")
            log.info("Step 1: Checking Zotero collection '%s' for new papers...", _coll_name)
        else:
            print("  Checking Zotero...")
            log.info("Step 1: Checking Zotero for new papers...")

        current_version = zotero_client.get_library_version()
        stored_version = state.zotero_library_version

        if stored_version == 0:
            # First run: just record the current version, don't process
            # existing items. Only papers added after this point will be synced.
            log.info(
                "First run: setting Zotero version watermark to %d "
                "(existing papers will not be processed)",
                current_version,
            )
            state.zotero_library_version = current_version
            state.save()
            print()
            print("  First run! Watermark set at Zotero version %d." % current_version)
            print("  Save a paper to Zotero and run again, or use")
            print("  'distillate --import' to add existing papers.")
            print()
        elif current_version == stored_version:
            log.info("Zotero library unchanged (version %d)", current_version)
        else:
            log.info(
                "Zotero library changed: %d → %d",
                stored_version, current_version,
            )
            changed_keys, new_version = zotero_client.get_changed_item_keys(
                stored_version, collection_key=_coll_key,
            )

            # Check for items deleted from Zotero
            try:
                deleted_keys = zotero_client.get_deleted_item_keys(stored_version)
                for dk in deleted_keys:
                    if state.has_document(dk):
                        title = state.get_document(dk).get("title", dk)
                        log.info("Zotero item deleted: '%s'", title)
                        print(f"  Removed (deleted from Zotero): \"{title}\"")
                        state.remove_document(dk)
            except Exception:
                log.warning("Could not check Zotero deletions", exc_info=True)

            if changed_keys:
                # Filter out items we already track
                new_keys = [
                    k for k in changed_keys if not state.has_document(k)
                ]

                if new_keys:
                    items = zotero_client.get_items_by_keys(new_keys)
                    new_papers = zotero_client.filter_new_papers(items)
                    log.info("Found %d new papers", len(new_papers))
                    if new_papers:
                        print(f"  Found {len(new_papers)} new paper{'s' if len(new_papers) != 1 else ''}")

                    # Ensure reMarkable folders exist and get existing docs
                    if new_papers and not zotero_mode:
                        remarkable_client.ensure_folders()
                        existing_on_rm = set(
                            remarkable_client.list_folder(config.RM_FOLDER_INBOX)
                        )
                    else:
                        existing_on_rm = set()

                    for paper in new_papers:
                        try:
                            paper_title = paper.get("data", {}).get("title", "")
                            if paper_title:
                                print(f"  Uploading: \"{paper_title}\"")
                            if _upload_paper(paper, state, existing_on_rm):
                                sent_count += 1
                        except Exception:
                            log.exception("Failed to process paper '%s', skipping",
                                          paper.get("data", {}).get("title", paper.get("key")))
                            continue

                # -- Metadata sync for existing tracked papers --
                existing_changed = [
                    k for k in changed_keys if state.has_document(k)
                ]
                if existing_changed:
                    log.info(
                        "Checking %d tracked paper(s) for metadata changes...",
                        len(existing_changed),
                    )
                    items = zotero_client.get_items_by_keys(existing_changed)
                    items_by_key = {item["key"]: item for item in items}

                    for key in existing_changed:
                        item = items_by_key.get(key)
                        if not item:
                            continue
                        doc = state.get_document(key)
                        if not doc:
                            continue

                        new_meta = zotero_client.extract_metadata(item)
                        old_meta = doc.get("metadata", {})

                        # Compare fields that come from Zotero
                        changed = False
                        for field in ("authors", "tags", "doi", "journal",
                                      "publication_date", "url", "title",
                                      "citekey"):
                            if new_meta.get(field) != old_meta.get(field):
                                changed = True
                                break

                        if not changed:
                            continue

                        log.info("Metadata changed for '%s'", doc["title"])

                        # Preserve S2 enrichment fields
                        for field in ("citation_count", "influential_citation_count",
                                      "s2_url", "paper_type"):
                            if field in old_meta:
                                new_meta[field] = old_meta[field]

                        # Rename files if citekey changed
                        old_ck = old_meta.get("citekey", "")
                        new_ck = new_meta.get("citekey", "")
                        if old_ck != new_ck and new_ck:
                            status = doc.get("status", "")

                            # Rename Saved note + PDF for processed papers
                            if status == "processed":
                                if obsidian.rename_paper(doc["title"], old_ck, new_ck):
                                    log.info("Renamed paper files: %s -> %s", old_ck or "(title)", new_ck)

                                new_uri = obsidian.get_obsidian_uri(doc["title"], citekey=new_ck)
                                if new_uri:
                                    zotero_client.update_obsidian_link(key, new_uri)

                                pd = obsidian._pdf_dir()
                                if pd:
                                    new_pdf = pd / f"{new_ck}.pdf"
                                    if new_pdf.exists():
                                        zotero_client.update_linked_attachment_path(
                                            key, new_pdf.name, str(new_pdf),
                                        )

                            # Rename Inbox PDF for queued / awaiting papers
                            if status in ("on_remarkable", "awaiting_pdf", "tracked"):
                                inbox = obsidian._inbox_dir()
                                if inbox:
                                    old_name = old_ck if old_ck else obsidian._sanitize_note_name(doc["title"])
                                    old_inbox = inbox / f"{old_name}.pdf"
                                    new_inbox = inbox / f"{new_ck}.pdf"
                                    if old_inbox.exists() and not new_inbox.exists():
                                        old_inbox.rename(new_inbox)
                                        log.info("Renamed inbox PDF: %s -> %s", old_inbox.name, new_inbox.name)
                                        zotero_client.update_linked_attachment_path(
                                            key, new_inbox.name, str(new_inbox),
                                        )

                        # Update title in reading log if it changed
                        old_title = doc["title"]
                        new_title = new_meta.get("title", old_title)
                        if old_title != new_title and doc.get("status") == "processed":
                            ck = new_meta.get("citekey", "")
                            obsidian.update_reading_log_title(old_title, new_title, citekey=ck)

                        doc["metadata"] = new_meta
                        doc["title"] = new_title
                        doc["authors"] = new_meta.get("authors", doc["authors"])

                        # Update Obsidian note frontmatter for processed papers
                        if doc.get("status") == "processed":
                            ck = new_meta.get("citekey", "")
                            obsidian.update_note_frontmatter(doc["title"], new_meta, citekey=ck)

                        state.save()

            state.zotero_library_version = current_version
            state.save()

        # -- Step 2: Poll for read papers --
        if zotero_mode:
            print("  Checking Zotero for read papers...")
            log.info("Step 2: Checking Zotero for read papers...")
        else:
            print("  Checking reMarkable...")
            log.info("Step 2: Checking reMarkable for read papers...")

        if zotero_mode:
            # Zotero mode: check tracked papers for "read" tag
            tracked = state.documents_with_status("tracked")
            processing = state.documents_with_status("processing")
            docs_to_process = list(processing)  # resume interrupted

            if tracked:
                # Batch-fetch Zotero items to check tags
                tracked_keys = [d["zotero_item_key"] for d in tracked]
                items = zotero_client.get_items_by_keys(tracked_keys)
                items_by_key = {item["key"]: item for item in items}

                for doc in tracked:
                    item = items_by_key.get(doc["zotero_item_key"])
                    if not item:
                        continue
                    tags = [t["tag"] for t in item.get("data", {}).get("tags", [])]
                    if config.ZOTERO_TAG_READ in tags:
                        docs_to_process.append(doc)
        else:
            read_docs = remarkable_client.list_folder(config.RM_FOLDER_READ)

            # Resume any papers left in "processing" state from a previous crash
            processing = state.documents_with_status("processing")
            on_remarkable = state.documents_with_status("on_remarkable")

            # Combine: processing docs first (resume), then newly found read docs
            docs_to_process = []
            for doc in processing:
                rm_name = doc["remarkable_doc_name"]
                if rm_name in read_docs:
                    log.info("Resuming processing for '%s'", doc["title"])
                    docs_to_process.append(doc)
                else:
                    log.info(
                        "Skipping '%s' (processing state but not in Read/)",
                        doc["title"],
                    )
                    state.mark_processed(doc["zotero_item_key"])
                    state.save()

            for doc in on_remarkable:
                rm_name = doc["remarkable_doc_name"]
                if rm_name in read_docs:
                    docs_to_process.append(doc)

        for doc in docs_to_process:
            item_key = doc["zotero_item_key"]

            print(f"  Processing: \"{doc['title']}\"")
            log.info("Found read paper: %s", doc["title"])

            try:
                # Update Zotero tag and save intermediate state BEFORE
                # expensive work so we can resume if interrupted
                if doc["status"] != "processing":
                    zotero_client.replace_tag(
                        item_key, config.ZOTERO_TAG_INBOX, config.ZOTERO_TAG_READ,
                    )
                    state.set_status(item_key, "processing")
                    state.save()

                ok = _process_paper_bundle(
                    doc, state,
                    rm_folder=config.RM_FOLDER_READ,
                    delete_inbox_pdf=True,
                    move_on_rm=True,
                    use_rm_stat=True,
                    use_rm_geta_fallback=True,
                )
                if ok:
                    synced_count += 1
                    log.info("Processed: %s", doc["title"])

            except Exception:
                log.exception("Failed to process read paper '%s', skipping", doc["title"])
                continue

        # -- Auto-promote pending picks from GH Actions --
        if not zotero_mode:
            try:
                _auto_promote(state)
            except Exception:
                log.debug("Auto-promote check failed, continuing", exc_info=True)

        state.touch_poll_timestamp()
        state.save()

        # -- Step 3: Notify --
        if sent_count or synced_count:
            parts = []
            if sent_count:
                parts.append(f"{sent_count} sent")
            if synced_count:
                parts.append(f"{synced_count} synced")
            print(f"  Done: {', '.join(parts)}")
            log.info("Done: %d sent, %d synced", sent_count, synced_count)
            notify.notify_summary(sent_count, synced_count)
            # Push state to cloud or Gist
            from distillate.commands import _sync_state
            if cloud_sync_available():
                push_state(state)
            elif config.STATE_GIST_ID:
                _sync_state()
        else:
            print("  Nothing to do.")
            log.info("Nothing to do.")

        # Scan tracked experiment projects for new commits
        if config.EXPERIMENTS_ENABLED and state.experiments:
            from distillate.experiments import (
                generate_notebook,
                update_experiment,
            )
            from distillate.obsidian import write_experiment_notebook

            exp_updated = 0
            for proj in state.experiments.values():
                if update_experiment(proj, state):
                    notebook_md = generate_notebook(proj)
                    write_experiment_notebook(proj, notebook_md)
                    exp_updated += 1
            if exp_updated:
                state.save()
                print(f"  Updated {exp_updated} experiment project(s).")

    except requests.exceptions.ConnectionError:
        print(
            "\n  Could not connect to the internet."
            "\n  Check your network connection and try again.\n"
        )
        return
    except requests.exceptions.HTTPError as e:
        resp = e.response
        if resp is not None and resp.status_code == 403:
            print(
                "\n  Zotero returned 403 Forbidden."
                "\n  Your API key may be invalid or expired."
                "\n  Check ZOTERO_API_KEY in your config.\n"
            )
            return
        if resp is not None and resp.status_code == 429:
            print(
                "\n  Zotero rate limit reached."
                "\n  Wait a few minutes and try again.\n"
            )
            return
        log.exception("HTTP error")
        raise
    except Exception as e:
        # Check for RmapiAuthError without requiring the import
        if type(e).__name__ == "RmapiAuthError":
            print(f"\n  {e}\n")
            return
        log.exception("Unexpected error")
        raise
    finally:
        release_lock()
