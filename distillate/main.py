"""Distillate entry point.

One-shot script: polls Zotero and reMarkable, processes papers, then exits.
Designed to be run on a schedule via cron or launchd.
"""

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

import requests

log = logging.getLogger("distillate")


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


def _reprocess(args: list[str]) -> None:
    """Re-run highlight extraction + PDF rendering on processed papers."""
    from distillate import config
    from distillate import remarkable_client
    from distillate import obsidian
    from distillate import renderer
    from distillate import summarizer
    from distillate import zotero_client
    from distillate.state import State

    config.setup_logging()

    state = State()
    processed = state.documents_with_status("processed")

    if not processed:
        log.info("No processed papers to reprocess")
        return

    # Filter to specific title if provided
    if args:
        query = " ".join(args).lower()
        matches = [d for d in processed if query in d["title"].lower()]
        if not matches:
            log.error("No processed paper matching '%s'", " ".join(args))
            log.info("Processed papers: %s", ", ".join(d["title"] for d in processed))
            return
        processed = matches

    log.info("Reprocessing %d paper(s)...", len(processed))

    for doc in processed:
        title = doc["title"]
        rm_name = doc["remarkable_doc_name"]
        item_key = doc["zotero_item_key"]
        log.info("Reprocessing: %s", title)

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / f"{rm_name}.zip"
            pdf_path = Path(tmpdir) / f"{rm_name}.pdf"

            bundle_ok = remarkable_client.download_document_bundle_to(
                config.RM_FOLDER_SAVED, rm_name, zip_path,
            )

            if not bundle_ok or not zip_path.exists():
                log.warning("Could not download bundle for '%s', skipping", title)
                continue

            highlights = renderer.extract_highlights(zip_path)
            page_count = renderer.get_page_count(zip_path)
            render_ok = renderer.render_annotated_pdf(zip_path, pdf_path)

            if render_ok and pdf_path.exists():
                annotated_bytes = pdf_path.read_bytes()
                saved = obsidian.save_annotated_pdf(title, annotated_bytes)
                pdf_filename = saved.name if saved else None
                log.info("Saved annotated PDF to Obsidian vault")
            else:
                log.warning("Could not render annotated PDF for '%s'", title)
                saved = None
                pdf_filename = None

            # Compute engagement score
            engagement = _compute_engagement(highlights, page_count)
            doc["engagement"] = engagement

            # Update linked attachment to point to annotated PDF
            linked = zotero_client.get_linked_attachment(item_key)
            if saved:
                new_att = zotero_client.create_linked_attachment(
                    item_key, saved.name, str(saved),
                )
                if new_att and linked:
                    zotero_client.delete_attachment(linked["key"])
            elif linked:
                zotero_client.delete_attachment(linked["key"])

            # Ensure read tag is set in Zotero
            zotero_client.add_tag(item_key, config.ZOTERO_TAG_READ)

            # Fetch fresh metadata from Zotero (includes tags)
            items = zotero_client.get_items_by_keys([item_key])
            if items:
                meta = zotero_client.extract_metadata(items[0])
                doc["metadata"] = meta
            else:
                meta = doc.get("metadata", {})

            # Flatten highlights for summarizer (needs raw text, not pages)
            flat_highlights = [
                h for page_hl in (highlights or {}).values() for h in page_hl
            ] or None

            # Extract key learnings first (summary uses them)
            learnings = summarizer.extract_insights(
                title,
                highlights=flat_highlights,
                abstract=meta.get("abstract", ""),
            )

            # Always regenerate summary on reprocess
            summary, one_liner = summarizer.summarize_read_paper(
                title, abstract=meta.get("abstract", ""),
                key_learnings=learnings,
            )

            # Use original processing date, not today
            read_date = doc.get("processed_at", "")

            # Recreate Obsidian note (delete existing first)
            obsidian.ensure_dataview_note()
            obsidian.ensure_stats_note()
            obsidian.delete_paper_note(title)
            # Compute highlight stats for note and state
            flat_hl = [h for hl in (highlights or {}).values() for h in hl]
            hl_pages = len(highlights) if highlights else 0
            hl_words = sum(len(h.split()) for h in flat_hl)

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
                date_read=read_date,
                engagement=engagement,
                highlighted_pages=hl_pages,
                highlight_word_count=hl_words,
                page_count=page_count,
            )

            # Add Obsidian deep link in Zotero
            obsidian_uri = obsidian.get_obsidian_uri(title)
            if obsidian_uri:
                zotero_client.create_obsidian_link(item_key, obsidian_uri)

            # Sync note to Zotero
            zotero_note_html = zotero_client._build_note_html(
                summary=summary, highlights=highlights or None,
            )
            zotero_client.set_note(item_key, zotero_note_html)

            # Update reading log
            obsidian.append_to_reading_log(title, one_liner, date_read=read_date)

            # Save summary and highlight count to state
            doc["highlight_count"] = len(flat_hl)
            doc["highlighted_pages"] = len(highlights) if highlights else 0
            doc["highlight_word_count"] = sum(
                len(h.split()) for h in flat_hl
            )
            doc["page_count"] = page_count
            state.mark_processed(item_key, summary=one_liner)
            state.save()

            log.info("Reprocessed: %s", title)


def _dry_run() -> None:
    """Preview what the workflow would do without making any changes."""
    from distillate import config
    from distillate import zotero_client
    from distillate import remarkable_client
    from distillate.state import State

    config.setup_logging()

    log.info("=== DRY RUN — no changes will be made ===")
    state = State()

    # Retry queue
    awaiting = state.documents_with_status("awaiting_pdf")
    if awaiting:
        log.info("[dry-run] %d paper(s) awaiting PDF sync:", len(awaiting))
        for doc in awaiting:
            log.info("  - %s", doc["title"])

    # Step 1: Check Zotero for new papers
    current_version = zotero_client.get_library_version()
    stored_version = state.zotero_library_version

    if stored_version == 0:
        log.info("[dry-run] First run — would set version watermark to %d", current_version)
    elif current_version == stored_version:
        log.info("[dry-run] Zotero library unchanged (version %d)", current_version)
    else:
        log.info("[dry-run] Zotero library changed: %d → %d", stored_version, current_version)
        changed_keys, _ = zotero_client.get_changed_item_keys(stored_version)
        new_keys = [k for k in changed_keys if not state.has_document(k)]
        if new_keys:
            items = zotero_client.get_items_by_keys(new_keys)
            new_papers = zotero_client.filter_new_papers(items)
            if new_papers:
                log.info("[dry-run] Would send %d paper(s) to reMarkable:", len(new_papers))
                for p in new_papers:
                    meta = zotero_client.extract_metadata(p)
                    log.info("  - %s (%s)", meta["title"], ", ".join(meta["authors"][:2]))
            else:
                log.info("[dry-run] No new papers to send")
        else:
            log.info("[dry-run] All changed items already tracked")

        # Check for metadata changes on tracked papers
        existing_changed = [k for k in changed_keys if state.has_document(k)]
        if existing_changed:
            log.info("[dry-run] %d tracked paper(s) have Zotero changes (would check metadata)", len(existing_changed))

    # Step 2: Check reMarkable for read papers
    on_remarkable = state.documents_with_status("on_remarkable")
    read_docs = remarkable_client.list_folder(config.RM_FOLDER_READ)

    read_matches = [d for d in on_remarkable if d["remarkable_doc_name"] in read_docs]
    if read_matches:
        log.info("[dry-run] Would process %d read paper(s):", len(read_matches))
        for doc in read_matches:
            log.info("  - %s", doc["title"])
    else:
        log.info("[dry-run] No read papers to process")

    # Summary
    total = len(read_matches)
    if awaiting:
        total += len(awaiting)
    if total:
        log.info("[dry-run] Total actions: %d paper(s) would be processed", total)
    else:
        log.info("[dry-run] Nothing to do")

    log.info("=== DRY RUN complete ===")



def _backfill_s2() -> None:
    """Backfill Semantic Scholar data for papers that don't have it yet."""
    from distillate import config
    from distillate import semantic_scholar
    from distillate import zotero_client
    from distillate.state import State

    config.setup_logging()

    state = State()
    count = 0

    for key, doc in state.documents.items():
        meta = doc.get("metadata", {})
        # Skip papers already enriched (have s2_url = actually found on S2)
        if meta.get("s2_url"):
            continue

        # Fetch metadata from Zotero if missing DOI
        if not meta.get("doi"):
            items = zotero_client.get_items_by_keys([key])
            if items:
                meta = zotero_client.extract_metadata(items[0])
                doc["metadata"] = meta

        s2_data = semantic_scholar.lookup_paper(
            doi=meta.get("doi", ""), title=doc["title"],
            url=meta.get("url", ""),
        )
        if s2_data:
            meta["citation_count"] = s2_data["citation_count"]
            meta["influential_citation_count"] = s2_data["influential_citation_count"]
            meta["s2_url"] = s2_data["s2_url"]
            log.info(
                "S2 enriched '%s': %d citations",
                doc["title"], s2_data["citation_count"],
            )
        else:
            log.info("S2: no data found for '%s'", doc["title"])

        doc["metadata"] = meta
        state.save()
        count += 1

    log.info("Backfilled S2 data for %d paper(s)", count)


def _themes(args: list[str]) -> None:
    """Generate a monthly research themes synthesis."""
    from datetime import datetime, timedelta, timezone

    from distillate import config
    from distillate import digest
    from distillate import obsidian
    from distillate import summarizer
    from distillate.state import State

    config.setup_logging()

    # Determine target month
    if args:
        month = args[0]  # e.g. "2026-02"
    else:
        # Default to previous month
        last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)
        month = last_month.strftime("%Y-%m")

    log.info("Generating themes for %s...", month)

    # Gather papers processed in the target month
    state = State()
    # Use first and last day of month as range
    month_start = f"{month}-01T00:00:00"
    # Get next month for the upper bound
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 12:
        next_month = f"{year + 1}-01-01T00:00:00"
    else:
        next_month = f"{year}-{mon + 1:02d}-01T00:00:00"

    all_processed = state.documents_processed_since(month_start)
    papers = [
        p for p in all_processed
        if (p.get("processed_at") or "") < next_month
    ]

    if not papers:
        log.info("No papers processed in %s", month)
        return

    log.info("Found %d papers for %s", len(papers), month)

    # Build enriched list for synthesis
    enriched = []
    for doc in papers:
        meta = doc.get("metadata", {})
        enriched.append({
            "title": doc["title"],
            "tags": meta.get("tags", []),
            "summary": doc.get("summary", ""),
            "paper_type": meta.get("paper_type", ""),
        })

    # Generate themes
    themes_text = summarizer.generate_monthly_themes(month, enriched)
    if not themes_text:
        log.warning("Could not generate themes for %s", month)
        return

    # Write Obsidian note
    note_path = obsidian.create_themes_note(month, themes_text)
    if note_path:
        log.info("Themes note: %s", note_path)
    else:
        # Print to stdout if Obsidian unconfigured
        print(f"\n# Research Themes — {month}\n\n{themes_text}")

    # Send email
    digest.send_themes_email(month, themes_text)

    log.info("Done generating themes for %s", month)


def _sync_state() -> None:
    """Upload state.json to a private GitHub Gist for GitHub Actions."""
    import subprocess

    from distillate import config

    config.setup_logging()

    gist_id = config.STATE_GIST_ID
    if not gist_id:
        log.error("STATE_GIST_ID not set — run: gh gist create state.json")
        return

    from distillate.state import STATE_PATH
    if not STATE_PATH.exists():
        log.info("No state.json to sync")
        return

    subprocess.run(
        ["gh", "gist", "edit", gist_id, "-f", "state.json", str(STATE_PATH)],
        check=True,
    )
    log.info("Synced state.json to gist %s", gist_id)


def _status() -> None:
    """Print a quick status overview to the terminal."""
    from datetime import datetime, timedelta, timezone

    from distillate import config
    from distillate.state import State

    config.setup_logging()
    state = State()
    now = datetime.now(timezone.utc)

    print()
    print("  Distillate")
    print("  " + "\u2500" * 40)

    # Queue
    queue = state.documents_with_status("on_remarkable")
    oldest_days = 0
    if queue:
        oldest_uploaded = min(d.get("uploaded_at", "") for d in queue)
        if oldest_uploaded:
            try:
                oldest_days = (now - datetime.fromisoformat(oldest_uploaded)).days
            except (ValueError, TypeError):
                pass
    queue_str = f"{len(queue)} paper{'s' if len(queue) != 1 else ''} waiting"
    if oldest_days:
        queue_str += f" (oldest: {oldest_days} days)"
    print(f"  Queue:     {queue_str}")

    # Promoted
    promoted = state.promoted_papers
    if promoted:
        titles = []
        for key in promoted:
            doc = state.get_document(key)
            if doc:
                titles.append(doc["title"])
        if titles:
            print(f"  Promoted:  {', '.join(titles)}")

    # Last sync
    last_poll = state.last_poll_timestamp
    if last_poll:
        try:
            poll_dt = datetime.fromisoformat(last_poll)
            delta = now - poll_dt
            if delta.total_seconds() < 60:
                ago = "just now"
            elif delta.total_seconds() < 3600:
                mins = int(delta.total_seconds() / 60)
                ago = f"{mins} min{'s' if mins != 1 else ''} ago"
            elif delta.total_seconds() < 86400:
                hours = int(delta.total_seconds() / 3600)
                ago = f"{hours} hour{'s' if hours != 1 else ''} ago"
            else:
                days = delta.days
                ago = f"{days} day{'s' if days != 1 else ''} ago"
            print(f"  Last sync: {ago}")
        except (ValueError, TypeError):
            pass
    else:
        print("  Last sync: never")

    # Reading stats
    week_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
    month_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    week_papers = state.documents_processed_since(week_ago.isoformat())
    month_papers = state.documents_processed_since(month_ago.isoformat())

    def _stats_line(papers, label):
        count = len(papers)
        pages = sum(d.get("page_count", 0) for d in papers)
        words = sum(d.get("highlight_word_count", 0) for d in papers)
        parts = [f"read {count} paper{'s' if count != 1 else ''}"]
        if pages:
            parts.append(f"{pages:,} pages")
        if words:
            parts.append(f"{words:,} words highlighted")
        sep = " \u00b7 "
        return f"{label}: {sep.join(parts)}"

    print()
    print(f"  {_stats_line(week_papers, 'This week')}")
    print(f"  {_stats_line(month_papers, 'This month')}")

    # Awaiting PDF (show titles)
    awaiting = state.documents_with_status("awaiting_pdf")
    if awaiting:
        print()
        print(f"  Awaiting PDF: {len(awaiting)} paper{'s' if len(awaiting) != 1 else ''}")
        for doc in awaiting:
            print(f"    - {doc['title']}")

    # Pending promotions
    pending_promo = state.pending_promotions
    if pending_promo:
        titles = [state.get_document(k)["title"] for k in pending_promo if state.get_document(k)]
        if titles:
            print()
            print(f"  Pending promotions: {len(titles)}")
            for t in titles:
                print(f"    - {t}")

    # Total processed
    processed = state.documents_with_status("processed")
    print()
    print(f"  Total: {len(processed)} papers read, {len(queue)} in queue")

    # Config health
    import shutil
    issues = []
    if not config.OBSIDIAN_VAULT_PATH and not config.OUTPUT_PATH:
        issues.append("No output configured (set OBSIDIAN_VAULT_PATH or OUTPUT_PATH)")
    elif config.OBSIDIAN_VAULT_PATH and not Path(config.OBSIDIAN_VAULT_PATH).is_dir():
        issues.append(f"Vault path missing: {config.OBSIDIAN_VAULT_PATH}")
    if not config.ANTHROPIC_API_KEY:
        issues.append("No ANTHROPIC_API_KEY (AI summaries disabled)")
    if not config.RESEND_API_KEY:
        issues.append("No RESEND_API_KEY (email digest disabled)")
    if not shutil.which("rmapi"):
        issues.append("rmapi not found (reMarkable sync will fail)")

    if issues:
        print()
        print("  Config:")
        for issue in issues:
            print(f"    - {issue}")
    print()


def _print_digest() -> None:
    """Print a reading digest to the terminal."""
    from datetime import datetime, timedelta, timezone

    from distillate import config
    from distillate.state import State

    config.setup_logging()
    state = State()

    now = datetime.now(timezone.utc)
    since = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)).isoformat()
    papers = state.documents_processed_since(since)

    if not papers:
        print("  No papers read in the last 7 days.")
        return

    papers = sorted(papers, key=lambda d: d.get("processed_at", ""), reverse=True)

    print()
    print(f"  Reading digest — last 7 days ({len(papers)} paper{'s' if len(papers) != 1 else ''})")
    print("  " + "-" * 48)

    for p in papers:
        title = p.get("title", "Untitled")
        summary = p.get("summary", "")
        engagement = p.get("engagement", 0)
        highlight_count = p.get("highlight_count", 0)
        processed_at = p.get("processed_at", "")

        date_str = ""
        if processed_at:
            try:
                dt = datetime.fromisoformat(processed_at)
                date_str = dt.strftime("%b %-d")
            except (ValueError, TypeError):
                pass

        citation_count = p.get("metadata", {}).get("citation_count", 0)
        stats = []
        if engagement:
            stats.append(f"{engagement}% engaged")
        if highlight_count:
            stats.append(f"{highlight_count} highlights")
        if citation_count:
            stats.append(f"{citation_count:,} citations")
        stats_str = f" ({', '.join(stats)})" if stats else ""

        print()
        print(f"  {title}")
        if date_str or stats_str:
            print(f"    {date_str}{stats_str}")
        if summary:
            print(f"    {summary}")

    # Reading stats footer (matches email format)
    month_since = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)).isoformat()
    month_papers = state.documents_processed_since(month_since)
    unread = state.documents_with_status("on_remarkable")

    def _stats_line(docs, label):
        count = len(docs)
        pages = sum(d.get("page_count", 0) for d in docs)
        words = sum(d.get("highlight_word_count", 0) for d in docs)
        parts = [f"read {count} paper{'s' if count != 1 else ''}"]
        if pages:
            parts.append(f"{pages:,} pages")
        if words:
            parts.append(f"{words:,} words highlighted")
        sep = " \u00b7 "
        return f"{label}: {sep.join(parts)}"

    print()
    print(f"  {_stats_line(papers, 'This week')}")
    print(f"  {_stats_line(month_papers, 'This month')}")
    print(f"  Queue: {len(unread)} paper{'s' if len(unread) != 1 else ''} waiting")
    print()


def _demote_and_promote(state, pick_keys: list, verbose: bool = False) -> None:
    """Demote old promoted papers, promote new picks on reMarkable.

    Shared logic used by both _suggest() (manual) and _auto_promote() (sync).
    Caller must hold the lock and pass a loaded State.
    """
    from datetime import datetime, timezone

    from distillate import config
    from distillate import remarkable_client

    # Demote old promoted papers back to Inbox (skip if user started reading)
    old_promoted = state.promoted_papers
    remaining_promoted = []
    if old_promoted:
        papers_root_docs = remarkable_client.list_folder(config.RM_FOLDER_PAPERS)
        for key in old_promoted:
            doc = state.get_document(key)
            if not doc or doc["status"] != "on_remarkable":
                continue
            rm_name = doc["remarkable_doc_name"]
            if rm_name not in papers_root_docs:
                log.info("Skipping demotion (not at Papers root): %s", doc["title"])
                continue

            stat = remarkable_client.stat_document(config.RM_FOLDER_PAPERS, rm_name)
            if stat and stat.get("current_page", 0) > 0:
                log.info("User started reading, not demoting: %s", doc["title"])
                remaining_promoted.append(key)
                continue

            if stat is None:
                log.info("Could not stat document, skipping demotion: %s", doc["title"])
                remaining_promoted.append(key)
                continue

            remarkable_client.move_document(
                rm_name, config.RM_FOLDER_PAPERS, config.RM_FOLDER_INBOX,
            )
            log.info("Demoted: %s", doc["title"])
        state.promoted_papers = remaining_promoted
        state.save()

    # Move picked papers from Inbox to Papers root
    inbox_docs = remarkable_client.list_folder(config.RM_FOLDER_INBOX)
    promoted_keys = list(remaining_promoted)

    for key in pick_keys:
        if key in promoted_keys:
            continue
        doc = state.get_document(key)
        if not doc or doc["status"] != "on_remarkable":
            continue
        rm_name = doc["remarkable_doc_name"]
        if rm_name in inbox_docs:
            remarkable_client.move_document(
                rm_name, config.RM_FOLDER_INBOX, config.RM_FOLDER_PAPERS,
            )
            doc["promoted_at"] = datetime.now(timezone.utc).isoformat()
            promoted_keys.append(key)
            if verbose:
                print(f"  Promoted: {doc['title']}")
            log.info("Promoted: %s", doc["title"])

    state.promoted_papers = promoted_keys
    state.pending_promotions = []
    state.save()


def _auto_promote(state) -> None:
    """Check Gist for pending picks from GH Actions and promote them.

    Called during --sync. If GH Actions ran --suggest-email, the picks
    are stored in pending.json on the Gist. This function reads them
    and promotes the papers on reMarkable.
    """
    from distillate import config
    from distillate.digest import fetch_pending_from_gist

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


def _suggest() -> None:
    """Suggest papers to read next, promote them on reMarkable.

    Checks Gist for pending picks from GH Actions first. If none,
    calls Claude directly. For users without GH Actions, this is
    the primary way to get suggestions.
    """
    from datetime import datetime, timedelta, timezone

    from distillate import config
    from distillate import remarkable_client
    from distillate import summarizer
    from distillate.digest import fetch_pending_from_gist
    from distillate.state import State, acquire_lock, release_lock

    config.setup_logging()

    if not acquire_lock():
        log.warning("Another instance is running (lock held), exiting")
        return

    try:
        state = State()

        # Check Gist for pending picks from GH Actions
        pick_keys = None
        if config.STATE_GIST_ID:
            pending = fetch_pending_from_gist()
            if pending:
                timestamp = pending.get("timestamp", "")
                last_processed = state._data.get("last_pending_timestamp", "")
                if timestamp and timestamp > last_processed:
                    pick_keys = pending.get("picks", [])
                    suggestion_text = pending.get("suggestion_text", "")
                    if pick_keys and suggestion_text:
                        print()
                        print("  Suggested papers to read next:")
                        print()
                        for line in suggestion_text.strip().split("\n"):
                            if line.strip():
                                print(f"  {line.strip()}")
                        print()
                        state._data["last_pending_timestamp"] = timestamp

        # Fall back to Claude if no pending picks
        if not pick_keys:
            unread = state.documents_with_status("on_remarkable")
            if not unread:
                print("  No papers in your reading queue.")
                return

            unread_enriched = []
            for doc in unread:
                meta = doc.get("metadata", {})
                unread_enriched.append({
                    "title": doc["title"],
                    "tags": meta.get("tags", []),
                    "paper_type": meta.get("paper_type", ""),
                    "uploaded_at": doc.get("uploaded_at", ""),
                    "citation_count": meta.get("citation_count", 0),
                })

            since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            recent = state.documents_processed_since(since)
            recent_enriched = []
            for doc in recent:
                meta = doc.get("metadata", {})
                recent_enriched.append({
                    "title": doc["title"],
                    "tags": meta.get("tags", []),
                    "summary": doc.get("summary", ""),
                    "engagement": doc.get("engagement", 0),
                    "citation_count": meta.get("citation_count", 0),
                })

            result = summarizer.suggest_papers(unread_enriched, recent_enriched)
            if not result:
                log.warning("Could not generate suggestions")
                pick_keys = []
            else:
                # Print suggestions to terminal
                print()
                print("  Suggested papers to read next:")
                print()
                for line in result.strip().split("\n"):
                    if line.strip():
                        print(f"  {line.strip()}")
                print()

                # Parse picks from Claude's response
                title_to_key = {doc["title"].lower(): doc["zotero_item_key"] for doc in unread}
                pick_keys = []
                for line in result.strip().split("\n"):
                    clean = line.strip().replace("**", "")
                    if not clean:
                        continue
                    clean_lower = clean.lower()
                    suggestion_title = re.sub(r"^\d+\.\s*", "", clean_lower).rstrip(" —-").split(" — ")[0].strip()
                    for title_lower, key in title_to_key.items():
                        if (title_lower in clean_lower or suggestion_title in title_lower) and key not in pick_keys:
                            pick_keys.append(key)
                            break

        _demote_and_promote(state, pick_keys, verbose=True)

    except remarkable_client.RmapiAuthError as e:
        print(f"\n  {e}\n")
        return
    except requests.exceptions.ConnectionError:
        print(
            "\n  Could not connect to the internet."
            "\n  Check your network connection and try again.\n"
        )
        return
    except Exception:
        log.exception("Unexpected error in suggest")
        raise
    finally:
        release_lock()


def _init_step5(save_to_env) -> None:
    """Step 5: Optional features (AI summaries, email digest)."""
    print("  " + "-" * 48)
    print("  Optional Features")
    print("  " + "-" * 48)
    print()

    # AI Summaries
    print("  AI Summaries")
    print()
    print("  With an Anthropic API key, each paper you read gets:")
    print("    - A one-liner summary (shown in your Reading Log)")
    print("    - A paragraph overview of methods and findings")
    print("    - 4-6 key learnings distilled from your highlights")
    print()
    print("  Without a key, papers use their abstract as fallback.")
    print()
    anthropic_key = _prompt_with_default(
        "  Anthropic API key (Enter to skip)", "ANTHROPIC_API_KEY", sensitive=True,
    )
    if anthropic_key:
        save_to_env("ANTHROPIC_API_KEY", anthropic_key)
        print("  AI summaries enabled.")
    else:
        print("  Skipped.")
    print()

    # Email Digest
    print("  Email Digest")
    print()
    print("  Get a weekly email summarizing what you've read, plus")
    print("  daily suggestions for what to read next from your queue.")
    print()
    print("  Requires a free Resend account: https://resend.com")
    print()
    resend_key = _prompt_with_default(
        "  Resend API key (Enter to skip)", "RESEND_API_KEY", sensitive=True,
    )
    if resend_key:
        save_to_env("RESEND_API_KEY", resend_key)
        email_to = _prompt_with_default("  Your email address", "DIGEST_TO")
        if email_to:
            save_to_env("DIGEST_TO", email_to)
        print("  Email digest enabled.")
    else:
        print("  Skipped.")


def _init_done(env_path) -> None:
    """Print post-setup instructions and offer automatic syncing."""
    print()
    print("  " + "=" * 48)
    print("  Setup complete!")
    print("  " + "=" * 48)
    print()
    print(f"  Config saved to: {env_path}")
    print()
    print("  " + "-" * 48)
    print("  How it works")
    print("  " + "-" * 48)
    print()
    print("  There are just four commands:")
    print()
    print("    distillate --sync")
    print("      Syncs everything in both directions:")
    print("      Zotero -> reMarkable (new papers)")
    print("      reMarkable -> notes (papers you finished reading)")
    print()
    print("    distillate --status")
    print("      Shows queue health and reading stats at a glance.")
    print()
    print("    distillate --suggest")
    print("      Picks 3 papers from your queue and moves them")
    print("      to the front of your Distillate folder. Unread")
    print("      suggestions are moved back to Inbox automatically.")
    print()
    print("    distillate --digest")
    print("      Shows a summary of what you read this week.")
    print()
    print("  Your workflow:")
    print("    1. Save a paper to Zotero (browser connector)")
    print("    2. distillate --sync (PDF lands on your reMarkable)")
    print("    3. Read and highlight on your reMarkable")
    print("    4. Move the document to Distillate/Read")
    print("    5. distillate --sync (annotated PDF + notes are ready)")
    print()

    # Offer automated sync
    print("  " + "-" * 48)
    print("  Automatic syncing")
    print("  " + "-" * 48)
    print()
    print("  Distillate can run automatically every 15 minutes")
    print("  so your papers stay in sync without running it manually.")
    print()

    import platform
    if platform.system() == "Darwin":
        setup_auto = input("  Set up automatic syncing? [Y/n] ").strip().lower()
        if setup_auto != "n":
            import subprocess
            scripts_dir = Path(__file__).parent.parent / "scripts"
            launchd_script = scripts_dir / "install-launchd.sh"
            if launchd_script.exists():
                print()
                result = subprocess.run(
                    ["bash", str(launchd_script)],
                    capture_output=False,
                )
                if result.returncode == 0:
                    print()
                    print("  Automatic syncing enabled (every 15 minutes).")
                else:
                    print()
                    print("  Could not set up automatic syncing.")
                    print(f"  You can try manually: bash {launchd_script}")
            else:
                print("  Launch script not found. You can set it up manually:")
                print("    crontab -e")
                print("    */15 * * * * distillate")
        else:
            print("  Skipped. You can set it up later:")
            print("    bash ./scripts/install-launchd.sh")
    else:
        print("  Add this to your crontab (crontab -e):")
        print("    */15 * * * * distillate")

    print()
    print("  " + "=" * 48)
    print("  Run 'distillate' now to sync your first papers!")
    print("  " + "=" * 48)
    print()


def _mask_value(value: str) -> str:
    """Mask a config value for display, showing first/last 4 chars."""
    if len(value) > 12:
        return value[:4] + "..." + value[-4:]
    return value


def _prompt_with_default(prompt: str, env_key: str, sensitive: bool = False) -> str | None:
    """Prompt user, showing existing value as default. Returns None if skipped."""
    current = os.environ.get(env_key, "")
    if current:
        display = _mask_value(current) if sensitive else current
        user_input = input(f"{prompt} [{display}]: ").strip()
    else:
        user_input = input(f"{prompt}: ").strip()

    if not user_input and current:
        return current
    return user_input or None


def _init_wizard() -> None:
    """Interactive setup wizard for first-time users."""
    from distillate.config import save_to_env, ENV_PATH

    # Detect existing config for re-run shortcut
    has_existing = ENV_PATH.exists() and os.environ.get("ZOTERO_API_KEY", "")

    print()
    if has_existing:
        print("  Distillate Setup")
        print("  " + "=" * 48)
        print()
        print(f"  Existing config found at: {ENV_PATH}")
        print()
        print("    1. Re-run full setup")
        print("    2. Configure optional features (AI, email)")
        print()
        choice = input("  Your choice [2]: ").strip()
        if choice != "1":
            print()
            _init_step5(save_to_env)
            _init_done(ENV_PATH)
            return
        print()
    else:
        print("  Welcome to Distillate")
        print("  " + "=" * 48)
        print()
        print("  Distillate automates your research paper workflow:")
        print()
        print("    1. You save a paper to Zotero (browser connector)")
        print("    2. Distillate uploads the PDF to your reMarkable")
        print("    3. You read and highlight on the reMarkable")
        print("    4. When done, move the document to the Read folder")
        print("    5. Distillate extracts your highlights, creates an")
        print("       annotated PDF, writes a note, and archives it")
        print()
        print("  Power-user features (optional):")
        print("    - AI summaries & key learnings (with Anthropic API)")
        print("    - Daily reading suggestions & weekly digest emails")
        print("      (with a free Resend account)")
        print()
        print("  Let's get you set up. This takes about 2 minutes.")
        print()
        print(f"  Config will be saved to: {ENV_PATH}")
        print()

    # -- Step 1: Zotero --

    print("  " + "-" * 48)
    print("  Step 1 of 5: Zotero")
    print("  " + "-" * 48)
    print()
    print("  Distillate watches your Zotero library for new papers.")
    print("  When you save a paper using the browser connector,")
    print("  Distillate picks it up and sends the PDF to your")
    print("  reMarkable.")
    print()
    print("  You need a Zotero API key with read/write library access.")
    print("  Create one here: https://www.zotero.org/settings/keys/new")
    print()
    api_key = _prompt_with_default("  API key", "ZOTERO_API_KEY", sensitive=True)
    if not api_key:
        print("\n  Error: A Zotero API key is required to continue.")
        return

    print()
    print("  Your user ID is the number shown on the same page.")
    print()
    user_id = _prompt_with_default("  User ID", "ZOTERO_USER_ID")
    if not user_id:
        print("\n  Error: A Zotero user ID is required to continue.")
        return

    print()
    print("  Verifying...")
    save_to_env("ZOTERO_API_KEY", api_key)
    save_to_env("ZOTERO_USER_ID", user_id)
    try:
        import requests
        resp = requests.get(
            f"https://api.zotero.org/users/{user_id}/items?limit=1",
            headers={"Zotero-API-Version": "3", "Zotero-API-Key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        print("  Connected! Found your Zotero library.")
    except Exception as e:
        print(f"  Warning: could not verify credentials ({e})")
        print("  Saved anyway — you can fix them later in .env")
    print()

    # -- Step 2: reMarkable --

    print("  " + "-" * 48)
    print("  Step 2 of 5: reMarkable")
    print("  " + "-" * 48)
    print()
    print("  Distillate uses rmapi to sync PDFs with your reMarkable")
    print("  via the reMarkable Cloud.")
    print()

    import shutil
    already_registered = bool(os.environ.get("REMARKABLE_DEVICE_TOKEN", ""))

    if already_registered:
        print("  reMarkable already registered.")
        print()
        register = input("  Re-register? [y/N] ").strip().lower()
        if register == "y":
            from distillate.remarkable_auth import register_interactive
            register_interactive()
        else:
            print("  Keeping existing registration.")
    elif shutil.which("rmapi"):
        print("  rmapi found.")
        print()
        print("  You need to authorize this device once.")
        print()
        register = input("  Register your reMarkable now? [Y/n] ").strip().lower()
        if register != "n":
            from distillate.remarkable_auth import register_interactive
            register_interactive()
        else:
            print("  Skipped. Run 'distillate --register' later.")
    else:
        print("  Distillate requires rmapi to sync files with your")
        print("  reMarkable via the cloud.")
        print()
        import platform
        if platform.system() == "Darwin":
            print("  Install it with Homebrew:")
            print("    brew install rmapi")
        else:
            print("  Download the latest binary from:")
            print("    https://github.com/ddvk/rmapi/releases")
        print()
        install_now = input("  Install rmapi now? [Y/n] ").strip().lower()
        if install_now != "n":
            if platform.system() == "Darwin":
                print()
                print("  Running: brew install rmapi")
                print()
                import subprocess
                result = subprocess.run(
                    ["brew", "install", "rmapi"],
                    capture_output=False,
                )
                print()
                if result.returncode == 0 and shutil.which("rmapi"):
                    print("  rmapi installed successfully!")
                    print()
                    register = input("  Register your reMarkable now? [Y/n] ").strip().lower()
                    if register != "n":
                        from distillate.remarkable_auth import register_interactive
                        register_interactive()
                    else:
                        print("  Skipped. Run 'distillate --register' later.")
                else:
                    print("  Installation failed. You can install manually later.")
                    print("  Run 'distillate --register' when ready.")
            else:
                print()
                print("  Please install rmapi manually from the link above,")
                print("  then run 'distillate --register' to connect.")
        else:
            print("  Skipped. Install rmapi and run 'distillate --register'")
            print("  when you're ready.")
    print()

    # -- Step 3: Notes & PDFs --

    print("  " + "-" * 48)
    print("  Step 3 of 5: Notes & PDFs")
    print("  " + "-" * 48)
    print()
    print("  When you finish reading, Distillate creates two files")
    print("  for each paper:")
    print()
    print("    - An annotated PDF with your highlights overlaid")
    print("      on the original document")
    print("    - A markdown note with paper metadata, your")
    print("      highlights grouped by page, and (optionally)")
    print("      AI-generated summaries")
    print()
    print("  These files need a home on your computer. The best")
    print("  option is an Obsidian vault — a free, local-first")
    print("  markdown knowledge base (https://obsidian.md).")
    print()
    print("  With Obsidian, Distillate also creates:")
    print("    - Wiki-links between your paper notes")
    print("    - A searchable paper database (via Dataview)")
    print("    - A reading statistics dashboard")
    print("    - 'Open in Obsidian' deep links from Zotero")
    print()

    # Default to Obsidian if vault path already set
    existing_vault = os.environ.get("OBSIDIAN_VAULT_PATH", "")
    existing_output = os.environ.get("OUTPUT_PATH", "")
    if existing_vault:
        obsidian_default = "Y"
    elif existing_output:
        obsidian_default = "n"
    else:
        obsidian_default = "Y"

    use_obsidian = input(f"  Use an Obsidian vault? [{obsidian_default}/{'n' if obsidian_default == 'Y' else 'Y'}] ").strip().lower()
    if not use_obsidian:
        use_obsidian = obsidian_default.lower()

    if use_obsidian != "n":
        print()
        print("  To find your vault path in Obsidian:")
        print("    Open Obsidian > Settings > Files and Links")
        print("    The path is shown under 'Vault path'")
        print()
        vault_path = _prompt_with_default("  Vault path", "OBSIDIAN_VAULT_PATH")
        if vault_path:
            vault_path = str(Path(vault_path).expanduser().resolve())
            save_to_env("OBSIDIAN_VAULT_PATH", vault_path)
            print()
            print("  Obsidian mode enabled! Distillate will create a")
            print("  Distillate/ folder inside your vault at:")
            print(f"    {vault_path}/Distillate/")
        else:
            print("  No path provided — skipping.")
    else:
        print()
        print("  You can use any local folder instead. You'll get")
        print("  the annotated PDFs and markdown notes, but not")
        print("  the Obsidian-specific features listed above.")
        print()
        folder = _prompt_with_default("  Output folder path (Enter to skip)", "OUTPUT_PATH")
        if folder:
            folder = str(Path(folder).expanduser().resolve())
            save_to_env("OUTPUT_PATH", folder)
            Path(folder).mkdir(parents=True, exist_ok=True)
            print(f"  Notes and PDFs will go to: {folder}")
        else:
            print("  Skipped. Notes will only be stored in Zotero.")
    print()

    # -- Step 4: PDF storage --

    print("  " + "-" * 48)
    print("  Step 4 of 5: PDF Storage")
    print("  " + "-" * 48)
    print()
    print("  After syncing a paper to your reMarkable, where should")
    print("  the PDF be kept?")
    print()
    print("  Zotero gives you 300 MB of free cloud storage for PDFs.")
    print("  If you're on the free plan, that fills up fast.")
    print()
    print("  Either way, the PDF is always on your reMarkable and")
    print("  saved locally with your notes after you read it.")
    print()
    print("    1. Keep in Zotero (uses Zotero storage)")
    print("    2. Remove from Zotero after sync (saves space)")
    print()
    existing_keep = os.environ.get("KEEP_ZOTERO_PDF", "true")
    default_storage = "2" if existing_keep.lower() == "false" else "1"
    storage = input(f"  Your choice [{default_storage}]: ").strip()
    if not storage:
        storage = default_storage
    if storage == "2":
        save_to_env("KEEP_ZOTERO_PDF", "false")
        print("  PDFs will be removed from Zotero after upload.")
    else:
        save_to_env("KEEP_ZOTERO_PDF", "true")
        print("  PDFs will stay in Zotero.")
    print()

    # -- Step 5: Optional features --

    _init_step5(save_to_env)

    # -- Done --

    _init_done(ENV_PATH)


_VERSION = "0.1.2"

_HELP = """\
Usage: distillate <command>

  distillate --sync     Sync Zotero -> reMarkable -> notes
  distillate --init     First-time setup wizard
  distillate --status   Show queue health and reading stats
  distillate --suggest  Suggest papers to read next from your queue
  distillate --digest   Show your reading digest

Options:
  -h, --help            Show this help
  -V, --version         Show version

Advanced:
  --reprocess "Title"   Re-extract highlights for a paper
"""


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(_HELP)
        return

    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"distillate {_VERSION}")
        return

    if "--init" in sys.argv:
        _init_wizard()
        return

    if "--register" in sys.argv:
        from distillate.remarkable_auth import register_interactive
        register_interactive()
        return

    from distillate import config
    config.ensure_loaded()

    if "--status" in sys.argv:
        _status()
        return

    if "--reprocess" in sys.argv:
        idx = sys.argv.index("--reprocess")
        _reprocess(sys.argv[idx + 1:])
        return

    if "--digest" in sys.argv:
        _print_digest()
        return

    if "--send-digest" in sys.argv:
        from distillate import digest
        digest.send_weekly_digest()
        return

    if "--dry-run" in sys.argv:
        _dry_run()
        return

    if "--backfill-s2" in sys.argv:
        _backfill_s2()
        return

    if "--suggest" in sys.argv:
        _suggest()
        return

    if "--suggest-email" in sys.argv:
        from distillate import digest
        digest.send_suggestion()
        return

    if "--themes" in sys.argv:
        idx = sys.argv.index("--themes")
        _themes(sys.argv[idx + 1:])
        return

    if "--sync-state" in sys.argv:
        _sync_state()
        return

    from distillate import zotero_client
    from distillate import remarkable_client
    from distillate import obsidian
    from distillate import notify
    from distillate import renderer
    from distillate import semantic_scholar
    from distillate import summarizer
    from distillate.state import State, acquire_lock, release_lock

    config.setup_logging()

    # Prevent overlapping runs
    if not acquire_lock():
        log.warning("Another instance is running (lock held), exiting")
        return

    try:
        state = State()
        sent_count = 0
        synced_count = 0

        # -- Retry papers awaiting PDF sync --
        awaiting = state.documents_with_status("awaiting_pdf")
        if awaiting:
            log.info("Retrying %d papers awaiting PDF sync...", len(awaiting))
            remarkable_client.ensure_folders()
            for doc in awaiting:
                title = doc["title"]
                att_key = doc["zotero_attachment_key"]
                item_key = doc["zotero_item_key"]
                meta = doc.get("metadata", {})
                try:
                    pdf_bytes = None

                    # Try Zotero cloud first (if we have an attachment key)
                    if att_key:
                        try:
                            pdf_bytes = zotero_client.download_pdf(att_key)
                            log.info("PDF now available for '%s' (%d bytes)", title, len(pdf_bytes))
                        except requests.exceptions.HTTPError as e:
                            if e.response is not None and e.response.status_code == 404:
                                log.info("PDF still not synced in Zotero for '%s'", title)
                            else:
                                raise

                    # Fall back to direct URL download (arxiv, biorxiv, etc.)
                    if pdf_bytes is None:
                        paper_url = meta.get("url", "")
                        if paper_url:
                            pdf_bytes = zotero_client.download_pdf_from_url(paper_url)
                            if pdf_bytes:
                                log.info("Downloaded PDF from URL for '%s'", title)

                    if pdf_bytes is None:
                        log.info("No PDF available yet for '%s', will retry", title)
                        continue

                    remarkable_client.upload_pdf_bytes(
                        pdf_bytes, config.RM_FOLDER_INBOX, title
                    )
                    saved = obsidian.save_inbox_pdf(title, pdf_bytes)
                    if saved:
                        new_att = zotero_client.create_linked_attachment(
                            item_key, saved.name, str(saved),
                        )
                        if new_att and att_key and not config.KEEP_ZOTERO_PDF:
                            zotero_client.delete_attachment(att_key)
                    elif att_key and not config.KEEP_ZOTERO_PDF:
                        zotero_client.delete_attachment(att_key)
                    zotero_client.add_tag(item_key, config.ZOTERO_TAG_INBOX)
                    state.set_status(item_key, "on_remarkable")
                    state.save()
                    sent_count += 1
                    log.info("Sent to reMarkable: %s", title)
                except Exception:
                    log.exception("Failed to retry '%s'", title)
            state.save()

        # -- Step 1: Poll Zotero for new papers --
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
        elif current_version == stored_version:
            log.info("Zotero library unchanged (version %d)", current_version)
        else:
            log.info(
                "Zotero library changed: %d → %d",
                stored_version, current_version,
            )
            changed_keys, new_version = zotero_client.get_changed_item_keys(
                stored_version
            )

            if changed_keys:
                # Filter out items we already track
                new_keys = [
                    k for k in changed_keys if not state.has_document(k)
                ]

                if new_keys:
                    items = zotero_client.get_items_by_keys(new_keys)
                    new_papers = zotero_client.filter_new_papers(items)
                    log.info("Found %d new papers", len(new_papers))

                    # Ensure reMarkable folders exist and get existing docs
                    if new_papers:
                        remarkable_client.ensure_folders()
                        existing_on_rm = set(
                            remarkable_client.list_folder(config.RM_FOLDER_INBOX)
                        )
                    else:
                        existing_on_rm = set()

                    for paper in new_papers:
                        try:
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
                                continue

                            # Find PDF attachment
                            attachment = zotero_client.get_pdf_attachment(item_key)
                            att_key = attachment["key"] if attachment else ""
                            att_md5 = attachment["data"].get("md5", "") if attachment else ""

                            # Upload to reMarkable (skip if already there)
                            if title in existing_on_rm:
                                log.info("Already on reMarkable, skipping upload: %s", title)
                            else:
                                pdf_bytes = None

                                # Try Zotero cloud download
                                if att_key:
                                    try:
                                        pdf_bytes = zotero_client.download_pdf(att_key)
                                    except requests.exceptions.HTTPError as e:
                                        if e.response is not None and e.response.status_code == 404:
                                            log.info("PDF not synced to Zotero cloud for '%s'", title)
                                        else:
                                            raise

                                # Fall back to direct URL download
                                if pdf_bytes is None:
                                    paper_url = meta.get("url", "")
                                    if paper_url:
                                        pdf_bytes = zotero_client.download_pdf_from_url(paper_url)
                                        if pdf_bytes:
                                            log.info("Downloaded PDF from URL for '%s'", title)

                                if pdf_bytes is None:
                                    log.warning(
                                        "No PDF available for '%s', will retry next run", title,
                                    )
                                    state.add_document(
                                        zotero_item_key=item_key,
                                        zotero_attachment_key=att_key,
                                        zotero_attachment_md5=att_md5,
                                        remarkable_doc_name=remarkable_client._sanitize_filename(title),
                                        title=title,
                                        authors=authors,
                                        status="awaiting_pdf",
                                        metadata=meta,
                                    )
                                    continue
                                log.info("Downloaded PDF (%d bytes)", len(pdf_bytes))
                                remarkable_client.upload_pdf_bytes(
                                    pdf_bytes, config.RM_FOLDER_INBOX, title
                                )
                                # Save original to Obsidian Inbox folder
                                saved = obsidian.save_inbox_pdf(title, pdf_bytes)
                                # Create linked attachment, optionally delete imported
                                if saved:
                                    new_att = zotero_client.create_linked_attachment(
                                        item_key, saved.name, str(saved),
                                    )
                                    if new_att and not config.KEEP_ZOTERO_PDF:
                                        zotero_client.delete_attachment(att_key)
                                    elif not new_att:
                                        log.warning("Could not create linked attachment for '%s', keeping imported PDF", title)
                                elif not config.KEEP_ZOTERO_PDF:
                                    zotero_client.delete_attachment(att_key)

                            # Semantic Scholar enrichment
                            try:
                                s2_data = semantic_scholar.lookup_paper(
                                    doi=meta.get("doi", ""), title=title,
                                    url=meta.get("url", ""),
                                )
                                if s2_data:
                                    meta["citation_count"] = s2_data["citation_count"]
                                    meta["influential_citation_count"] = s2_data["influential_citation_count"]
                                    meta["s2_url"] = s2_data["s2_url"]
                                    log.info(
                                        "S2: %d citations",
                                        s2_data["citation_count"],
                                    )
                            except Exception:
                                log.debug("S2 lookup failed for '%s'", title, exc_info=True)

                            # Tag in Zotero
                            zotero_client.add_tag(item_key, config.ZOTERO_TAG_INBOX)

                            # Track in state
                            state.add_document(
                                zotero_item_key=item_key,
                                zotero_attachment_key=att_key,
                                zotero_attachment_md5=att_md5,
                                remarkable_doc_name=remarkable_client._sanitize_filename(title),
                                title=title,
                                authors=authors,
                                metadata=meta,
                            )
                            state.save()
                            sent_count += 1
                            log.info("Sent to reMarkable: %s", title)

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
                                  "publication_date", "url", "title"):
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

                    doc["metadata"] = new_meta
                    doc["authors"] = new_meta.get("authors", doc["authors"])

                    # Update Obsidian note frontmatter for processed papers
                    if doc.get("status") == "processed":
                        obsidian.update_note_frontmatter(doc["title"], new_meta)

                    state.save()

            state.zotero_library_version = current_version
            state.save()

        # -- Step 2: Poll reMarkable for read papers --
        log.info("Step 2: Checking reMarkable for read papers...")

        read_docs = remarkable_client.list_folder(config.RM_FOLDER_READ)
        on_remarkable = state.documents_with_status("on_remarkable")

        for doc in on_remarkable:
            rm_name = doc["remarkable_doc_name"]

            if rm_name not in read_docs:
                continue

            log.info("Found read paper: %s", rm_name)
            item_key = doc["zotero_item_key"]

            try:
                highlights = None

                with tempfile.TemporaryDirectory() as tmpdir:
                    zip_path = Path(tmpdir) / f"{rm_name}.zip"
                    pdf_path = Path(tmpdir) / f"{rm_name}.pdf"

                    # Download raw document bundle
                    bundle_ok = remarkable_client.download_document_bundle_to(
                        config.RM_FOLDER_READ, rm_name, zip_path,
                    )

                    if bundle_ok and zip_path.exists():
                        # Extract highlighted text
                        highlights = renderer.extract_highlights(zip_path)
                        if not highlights:
                            log.info("No text highlights found for '%s'", rm_name)

                        # Get page count for engagement score
                        stat = remarkable_client.stat_document(
                            config.RM_FOLDER_READ, rm_name,
                        )
                        page_count = (stat or {}).get("page_count", 0)
                        if not page_count:
                            page_count = renderer.get_page_count(zip_path)

                        # Render annotated PDF
                        render_ok = renderer.render_annotated_pdf(zip_path, pdf_path)
                    else:
                        render_ok = False
                        page_count = 0

                    # Fall back to geta if render failed
                    if not render_ok:
                        log.info("Falling back to rmapi geta for '%s'", rm_name)
                        render_ok = remarkable_client.download_annotated_pdf_to(
                            config.RM_FOLDER_READ, rm_name, pdf_path,
                        )

                    pdf_filename = None
                    if render_ok and pdf_path.exists():
                        annotated_bytes = pdf_path.read_bytes()
                        saved = obsidian.save_annotated_pdf(doc["title"], annotated_bytes)
                        if saved:
                            pdf_filename = saved.name
                            log.info("Saved annotated PDF to Obsidian vault")
                    else:
                        log.warning(
                            "Could not get annotated PDF for '%s'", rm_name,
                        )

                    # Clean up original from Inbox folder
                    obsidian.delete_inbox_pdf(doc["title"])

                    # Update linked attachment to point to annotated PDF
                    linked = zotero_client.get_linked_attachment(item_key)
                    if saved:
                        new_att = zotero_client.create_linked_attachment(
                            item_key, saved.name, str(saved),
                        )
                        if new_att and linked:
                            zotero_client.delete_attachment(linked["key"])
                    elif linked:
                        zotero_client.delete_attachment(linked["key"])

                # Update Zotero tag
                zotero_client.replace_tag(
                    item_key, config.ZOTERO_TAG_INBOX, config.ZOTERO_TAG_READ,
                )

                # Flatten highlights for summarizer (needs raw text, not pages)
                meta = doc.get("metadata", {})
                flat_highlights = [
                    h for page_hl in (highlights or {}).values() for h in page_hl
                ] or None

                # Extract key learnings first (summary uses them)
                learnings = summarizer.extract_insights(
                    doc["title"],
                    highlights=flat_highlights,
                    abstract=meta.get("abstract", ""),
                )

                # Generate AI summaries
                summary, one_liner = summarizer.summarize_read_paper(
                    doc["title"],
                    abstract=meta.get("abstract", ""),
                    key_learnings=learnings,
                )

                # Compute engagement score and highlight stats
                engagement = _compute_engagement(highlights, page_count)
                doc["engagement"] = engagement
                hl_pages = len(highlights) if highlights else 0
                hl_words = sum(
                    len(h.split())
                    for hl in (highlights or {}).values() for h in hl
                )

                # Create Obsidian note with page-grouped highlights
                obsidian.ensure_dataview_note()
                obsidian.ensure_stats_note()
                obsidian.create_paper_note(
                    title=doc["title"],
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
                    engagement=engagement,
                    highlighted_pages=hl_pages,
                    highlight_word_count=hl_words,
                    page_count=page_count,
                )

                # Add Obsidian deep link in Zotero
                obsidian_uri = obsidian.get_obsidian_uri(doc["title"])
                if obsidian_uri:
                    zotero_client.create_obsidian_link(item_key, obsidian_uri)

                # Sync note to Zotero
                zotero_note_html = zotero_client._build_note_html(
                    summary=summary, highlights=highlights or None,
                )
                zotero_client.set_note(item_key, zotero_note_html)

                # Append to reading log
                obsidian.append_to_reading_log(doc["title"], one_liner)

                # Move to Saved on reMarkable
                remarkable_client.move_document(
                    rm_name, config.RM_FOLDER_READ, config.RM_FOLDER_SAVED,
                )

                # Update state
                flat_hl = [h for hl in (highlights or {}).values() for h in hl]
                doc["highlight_count"] = len(flat_hl)
                doc["highlighted_pages"] = len(highlights) if highlights else 0
                doc["highlight_word_count"] = sum(
                    len(h.split()) for h in flat_hl
                )
                doc["page_count"] = page_count
                state.mark_processed(item_key, summary=one_liner)
                state.save()
                synced_count += 1
                log.info("Processed: %s", rm_name)

            except Exception:
                log.exception("Failed to process read paper '%s', skipping", rm_name)
                continue

        # -- Auto-promote pending picks from GH Actions --
        try:
            _auto_promote(state)
        except Exception:
            log.debug("Auto-promote check failed, continuing", exc_info=True)

        state.touch_poll_timestamp()
        state.save()

        # -- Step 3: Notify --
        if sent_count or synced_count:
            log.info("Done: %d sent, %d synced", sent_count, synced_count)
            notify.notify_summary(sent_count, synced_count)
        else:
            log.info("Nothing to do.")

    except remarkable_client.RmapiAuthError as e:
        print(f"\n  {e}\n")
        return
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
    except Exception:
        log.exception("Unexpected error")
        raise
    finally:
        release_lock()


if __name__ == "__main__":
    main()
