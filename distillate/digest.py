"""Weekly email digest and daily paper suggestions."""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from distillate import config
from distillate import summarizer
from distillate.state import State

log = logging.getLogger(__name__)

_SIGNATURE = (
    '<p style="color:#999;font-size:13px;margin-top:24px;">--<br>'
    'Sent from <a href="https://distillate.dev" style="color:#999;">distillate</a>.</p>'
)


def _send_email(subject: str, html: str) -> dict | None:
    """Send an email via Resend. Returns result or None if unavailable."""
    try:
        import resend
    except ImportError:
        log.error(
            "Email requires the 'resend' package. "
            "Install it with: pip install distillate[email]"
        )
        return None
    if not config.RESEND_API_KEY:
        log.error("RESEND_API_KEY not set, cannot send email")
        return None
    if not config.DIGEST_TO:
        log.error("DIGEST_TO not set, cannot send email")
        return None
    resend.api_key = config.RESEND_API_KEY
    result = resend.Emails.send({
        "from": config.DIGEST_FROM,
        "to": [config.DIGEST_TO],
        "subject": subject,
        "html": html,
    })
    log.info("Sent email to %s: %s", config.DIGEST_TO, result)
    return result


def _sync_tags(state: State) -> None:
    """Refresh tags and URLs from Zotero for all tracked papers.

    Lightweight sync: only fetches items whose Zotero version changed,
    updates tags/URL/DOI in state metadata.
    """
    from distillate import zotero_client

    try:
        current_version = zotero_client.get_library_version()
        stored_version = state.zotero_library_version
        if current_version == stored_version:
            return

        changed_keys, _ = zotero_client.get_changed_item_keys(stored_version)
        tracked_changed = [k for k in changed_keys if state.has_document(k)]
        if not tracked_changed:
            state.zotero_library_version = current_version
            state.save()
            return

        items = zotero_client.get_items_by_keys(tracked_changed)
        items_by_key = {item["key"]: item for item in items}
        updated = 0

        for key in tracked_changed:
            item = items_by_key.get(key)
            if not item:
                continue
            doc = state.get_document(key)
            if not doc:
                continue

            new_meta = zotero_client.extract_metadata(item)
            old_meta = doc.get("metadata", {})

            # Preserve S2 enrichment
            for field in ("citation_count", "influential_citation_count",
                          "s2_url", "paper_type"):
                if field in old_meta:
                    new_meta[field] = old_meta[field]

            doc["metadata"] = new_meta
            doc["authors"] = new_meta.get("authors", doc["authors"])
            updated += 1

        state.zotero_library_version = current_version
        state.save()
        if updated:
            log.info("Synced metadata for %d paper(s) from Zotero", updated)
    except Exception:
        log.debug("Tag sync failed, continuing with cached data", exc_info=True)

# Pastel palette for tag pills (deterministic by tag name hash)
_PILL_COLORS = [
    "#e8f0fe",  # blue
    "#fce8e6",  # red
    "#e6f4ea",  # green
    "#fef7e0",  # yellow
    "#f3e8fd",  # purple
    "#e8f7f0",  # teal
    "#fde8ef",  # pink
    "#e8eaf6",  # indigo
]


def _tag_pills_html(tags: list) -> str:
    """Render topic tags as colored HTML pill badges."""
    if not tags:
        return ""
    pills = []
    for tag in tags:
        bg = _PILL_COLORS[hash(tag) % len(_PILL_COLORS)]
        # Use a table cell to bypass email client minimum font-size enforcement
        pills.append(
            f'<span style="display:inline-block;background:{bg};'
            f'color:#555;padding:0px 5px;border-radius:8px;'
            f'font-size:11px;line-height:14px;margin:1px 1px;'
            f'mso-line-height-rule:exactly;">{tag}</span>'
        )
    return " ".join(pills)


def _reading_stats_line(papers: list, label: str) -> str:
    """Render a single stats line like 'This week: read 3 papers · 65 pages · 3,830 words highlighted'."""
    count = len(papers)
    total_pages = sum(d.get("page_count", 0) for d in papers)
    words = sum(d.get("highlight_word_count", 0) for d in papers)

    parts = [f"{label}: read {count} paper{'s' if count != 1 else ''}"]
    if total_pages:
        parts.append(f"{total_pages:,} pages")
    if words:
        parts.append(f"{words:,} words highlighted")
    return " &middot; ".join(parts)


def _reading_stats_html(state: State) -> str:
    """Render reading stats footer with week and month lines."""
    now = datetime.now(timezone.utc)
    week_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
    month_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)

    week_papers = state.documents_processed_since(week_ago.isoformat())
    month_papers = state.documents_processed_since(month_ago.isoformat())

    week_line = _reading_stats_line(week_papers, "This week")
    month_line = _reading_stats_line(month_papers, "This month")

    return (
        f'<p style="color:#999;font-size:13px;margin:24px 0 0 0;">{week_line}</p>'
        f'<p style="color:#999;font-size:13px;margin:0;">{month_line}</p>'
    )


def _recent_topic_tags(state: State, limit: int = 5) -> list:
    """Return the most common topic tags from recent reads (last 30 days)."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = state.documents_processed_since(since)
    tag_counts: dict = {}
    for doc in recent:
        for tag in doc.get("metadata", {}).get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    # Sort by frequency, return top tags
    return [t for t, _ in sorted(tag_counts.items(), key=lambda x: -x[1])][:limit]


def _queue_health_html(state: State) -> str:
    """Render queue health snapshot for the suggest email."""
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()

    queue = state.documents_with_status("on_remarkable")
    total = len(queue)

    oldest_days = 0
    if queue:
        oldest_uploaded = min(d.get("uploaded_at", "") for d in queue)
        if oldest_uploaded:
            try:
                oldest_dt = datetime.fromisoformat(oldest_uploaded)
                oldest_days = (now - oldest_dt).days
            except (ValueError, TypeError):
                pass

    added_this_week = sum(
        1 for d in state.documents.values()
        if (d.get("uploaded_at") or "") >= week_ago
        and d.get("status") in ("on_remarkable", "processed")
    )
    processed_this_week = len(state.documents_processed_since(week_ago))

    awaiting = len(state.documents_with_status("awaiting_pdf"))
    awaiting_html = (
        f' &middot; {awaiting} missing PDF{"s" if awaiting != 1 else ""}'
        if awaiting else ""
    )

    return (
        f'<p style="color:#999;font-size:13px;margin:0;">'
        f'Queue: {total} papers waiting'
        f' &middot; oldest: {oldest_days} days'
        f' &middot; this week: +{added_this_week} added, '
        f'-{processed_this_week} read{awaiting_html}</p>'
    )


def _push_pending_to_gist(picks: list, suggestion_text: str) -> None:
    """Write pending.json to the state Gist so local --sync can promote."""
    gist_id = config.STATE_GIST_ID
    token = os.environ.get("GH_GIST_TOKEN", "")
    if not gist_id or not token:
        return

    pending = {
        "picks": picks,
        "suggestion_text": suggestion_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        resp = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"token {token}"},
            json={"files": {"pending.json": {"content": json.dumps(pending)}}},
            timeout=config.HTTP_TIMEOUT,
        )
        if resp.ok:
            log.info("Pushed pending picks to Gist")
        else:
            log.warning("Failed to push pending to Gist: %s", resp.status_code)
    except Exception:
        log.debug("Gist push failed", exc_info=True)


def fetch_pending_from_gist() -> dict | None:
    """Read pending.json from the state Gist. Returns dict or None."""
    gist_id = config.STATE_GIST_ID
    if not gist_id:
        return None
    try:
        resp = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            timeout=config.HTTP_TIMEOUT,
        )
        if not resp.ok:
            return None
        files = resp.json().get("files", {})
        content = files.get("pending.json", {}).get("content")
        if content:
            return json.loads(content)
    except Exception:
        log.debug("Failed to fetch pending from Gist", exc_info=True)
    return None


def send_weekly_digest(days: int = 7) -> None:
    """Compile and send a digest of papers processed in the last N days."""
    config.setup_logging()

    state = State()
    _sync_tags(state)

    now = datetime.now(timezone.utc)
    since = (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)).isoformat()
    papers = state.documents_processed_since(since)

    if not papers:
        log.info("No papers processed in the last %d days, skipping digest", days)
        return

    subject = _build_subject()
    body = _build_body(papers, state)
    _send_email(subject, body)


def _build_subject():
    return datetime.now().strftime("Reading digest \u2013 %b %-d, %Y")


def _paper_url(p):
    """Return a URL to the paper. Fallback chain: URL > DOI > S2 > Google Scholar."""
    from urllib.parse import quote_plus

    meta = p.get("metadata", {})
    url = meta.get("url", "")
    doi = meta.get("doi", "")
    s2_url = meta.get("s2_url", "")
    title = p.get("title", "")

    if url:
        return url
    if doi:
        return f"https://doi.org/{doi}"
    if s2_url:
        return s2_url
    if title:
        return f"https://scholar.google.com/scholar?q={quote_plus(title)}"
    return ""


def _paper_html(p):
    title = p.get("title", "Untitled")
    summary = p.get("summary", "")
    url = _paper_url(p)
    highlight_count = p.get("highlight_count", 0)
    engagement = p.get("engagement", 0)
    highlight_word_count = p.get("highlight_word_count", 0)
    processed_at = p.get("processed_at", "")

    # Title with Obsidian deep link
    from distillate import obsidian
    obsidian_uri = obsidian.get_obsidian_uri(title)
    if obsidian_uri:
        title_html = (
            f'<a href="{obsidian_uri}" style="color:#333;text-decoration:none;">'
            f'<strong>{title}</strong></a>'
        )
    else:
        title_html = f"<strong>{title}</strong>"

    # Date read (e.g. "Feb 10")
    date_html = ""
    if processed_at:
        try:
            dt = datetime.fromisoformat(processed_at)
            date_html = (
                f' <span style="color:#999;font-size:12px;">'
                f'{dt.strftime("%b %-d")}</span>'
            )
        except (ValueError, TypeError):
            pass

    citation_count = p.get("metadata", {}).get("citation_count", 0)

    # Stats badge: engagement + highlights + word count + citations
    stats_parts = []
    if engagement:
        stats_parts.append(f"{engagement}% engaged")
    if highlight_count:
        stats_parts.append(
            f'{highlight_count} highlight{"s" if highlight_count != 1 else ""}'
        )
    if highlight_word_count:
        stats_parts.append(f"{highlight_word_count} words")
    if citation_count:
        stats_parts.append(f"{citation_count:,} citations")
    stats_html = ""
    if stats_parts:
        stats_html = (
            f' <span style="color:#999;font-size:12px;">'
            f'({", ".join(stats_parts)})</span>'
        )

    summary_html = f" &mdash; {summary}" if summary else ""
    url_html = (
        f'<br><a href="{url}" style="color:#666;font-size:13px;">{url}</a>'
        if url else ""
    )

    return (
        f"<li style='margin-bottom: 14px;'>"
        f"{title_html}{date_html}{stats_html}{summary_html}{url_html}"
        f"</li>"
    )


def _build_body(papers, state: State):
    count = len(papers)
    lines = [
        "<html><body style='font-family: sans-serif; max-width: 600px; "
        "margin: 0 auto; padding: 20px; color: #333;'>",
        f"<p>Paper{'s' if count != 1 else ''} I read this week:</p>",
        "<ul style='padding-left: 20px;'>",
    ]

    for p in sorted(papers, key=lambda d: d.get("processed_at", ""), reverse=True):
        lines.append(_paper_html(p))

    lines.append("</ul>")
    lines.append(_reading_stats_html(state))
    lines.append(_queue_health_html(state))
    lines.append(_SIGNATURE)
    lines.append("</body></html>")
    return "\n".join(lines)


def send_suggestion() -> None:
    """Send a daily email suggesting 3 papers and store picks for promotion."""
    config.setup_logging()

    state = State()
    _sync_tags(state)

    # Gather unread papers (on_remarkable)
    unread = state.documents_with_status("on_remarkable")
    if not unread:
        log.info("No papers in reading queue, skipping suggestion")
        return

    # Enrich with metadata fields the suggestion engine needs
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

    # Gather recent reads for context (last 30 days)
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

    # Ask Claude
    result = summarizer.suggest_papers(unread_enriched, recent_enriched)
    if not result:
        log.warning("Could not generate suggestions")
        return

    # Store picks for auto-promote to execute during next sync
    title_to_key = {doc["title"].lower(): doc["zotero_item_key"] for doc in unread}
    pending = []
    for line in result.strip().split("\n"):
        clean = line.strip().replace("**", "")
        if not clean:
            continue
        clean_lower = clean.lower()
        suggestion_title = re.sub(r"^\d+\.\s*", "", clean_lower).rstrip(" —-").split(" — ")[0].strip()
        for title_lower, key in title_to_key.items():
            if (title_lower in clean_lower or suggestion_title in title_lower) and key not in pending:
                pending.append(key)
                break
    if pending:
        state.pending_promotions = pending
        state.save()
        log.info("Stored %d pending promotion(s)", len(pending))
        _push_pending_to_gist(pending, result)

    subject = datetime.now().strftime("What to read next \u2013 %b %-d, %Y")
    body = _build_suggestion_body(result, unread, state)

    _send_email(subject, body)


def send_themes_email(month: str, themes_text: str) -> None:
    """Send a monthly research themes email."""
    config.setup_logging()

    # Convert markdown paragraphs to HTML
    paragraphs = themes_text.strip().split("\n\n")
    body_html = "\n".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())

    html = (
        "<html><body style='font-family: sans-serif; max-width: 600px; "
        "margin: 0 auto; padding: 20px; color: #333;'>"
        f"<h1>Research Themes \u2014 {month}</h1>"
        f"{body_html}"
        f"{_SIGNATURE}"
        "</body></html>"
    )

    _send_email(f"Research themes \u2014 {month}", html)


def _build_suggestion_body(suggestion_text, unread, state: State):
    """Build HTML body from Claude's suggestion text."""
    # Build title -> doc lookup from full unread list
    url_lookup = {}
    tags_lookup = {}
    for doc in unread:
        url_lookup[doc["title"].lower()] = _paper_url(doc)
        tags_lookup[doc["title"].lower()] = doc.get("metadata", {}).get("tags", [])

    lines = [
        "<html><body style='font-family: sans-serif; max-width: 600px; "
        "margin: 0 auto; padding: 20px; color: #333;'>",
        "<p>Here are 3 papers to consider today:</p>",
        "<ul style='padding-left: 20px;'>",
    ]

    # Parse suggestion lines: "[number]. [title] — [reason]"
    # Claude may wrap in **bold** markdown or add preamble text
    for line in suggestion_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # Strip markdown bold markers
        clean = line.replace("**", "")

        # Extract queue number and rest
        m = re.match(r"(\d+)\.\s*(.*)", clean)
        if not m:
            continue
        queue_num = m.group(1)
        rest = m.group(2)

        # Match title to a known paper (bidirectional: handles journal suffixes)
        url = ""
        tags = []
        matched_title = ""
        rest_lower = rest.lower()
        suggestion_title = rest_lower.rstrip(" —-").split(" — ")[0].strip()
        for title_lower in tags_lookup:
            if title_lower in rest_lower or suggestion_title in title_lower:
                url = url_lookup.get(title_lower, "")
                tags = tags_lookup.get(title_lower, [])
                # Find the original-cased title in rest
                idx = rest_lower.find(title_lower)
                if idx >= 0:
                    matched_title = rest[idx:idx + len(title_lower)]
                else:
                    matched_title = rest[:len(suggestion_title)]
                break

        # Split into title and reason at " — " or " - "
        if matched_title:
            # Replace title with bold version
            title_end = rest.lower().index(matched_title.lower()) + len(matched_title)
            title_part = rest[:title_end]
            reason_part = rest[title_end:].lstrip(" —-").strip()
        elif " — " in rest:
            title_part, reason_part = rest.split(" — ", 1)
        elif " - " in rest:
            title_part, reason_part = rest.split(" - ", 1)
        else:
            title_part = rest
            reason_part = ""

        title_html = f"<strong>{title_part.strip()}</strong>"
        reason_html = f" &mdash; {reason_part}" if reason_part else ""
        pills_html = "" if tags else ""
        url_html = (
            f'<br><a href="{url}" style="color:#666;font-size:13px;">{url}</a>'
            if url else ""
        )

        lines.append(
            f"<li style='margin-bottom: 14px;'>"
            f'<span style="color:#999;">[{queue_num}]</span> '
            f"{title_html}{reason_html}{pills_html}{url_html}"
            f"</li>"
        )

    lines.append("</ul>")
    lines.append(_reading_stats_html(state))
    lines.append(_queue_health_html(state))
    lines.append(_SIGNATURE)
    lines.append("</body></html>")
    return "\n".join(lines)
