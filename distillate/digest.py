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

def match_suggestion_to_title(line: str, known_titles: list[str]) -> str | None:
    """Match a suggestion line from Claude to a known paper title.

    Given a line like '1. Some Title — reason', checks bidirectionally
    against each known title (case-insensitive). Returns the matched
    title (original case) or None.
    """
    clean = line.strip().replace("**", "")
    if not clean:
        return None
    clean_lower = clean.lower()
    suggestion_title = re.sub(r"^\d+\.\s*", "", clean_lower).rstrip(" —-").split(" — ")[0].strip()
    for title in known_titles:
        title_lower = title.lower()
        if title_lower in clean_lower or suggestion_title in title_lower:
            return title
    return None


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

            # Check raw Zotero tags for "read" before extract_metadata strips them
            raw_tags = {t["tag"] for t in item.get("data", {}).get("tags", [])}
            if (config.ZOTERO_TAG_READ in raw_tags
                    and doc.get("status") == "on_remarkable"):
                doc["status"] = "processed"
                if not doc.get("processed_at"):
                    doc["processed_at"] = datetime.now(timezone.utc).isoformat()
                log.info("Marked '%s' as processed (read tag in Zotero)",
                         doc.get("title", key))

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


# ---------------------------------------------------------------------------
# Tag abbreviation — human-readable short words, no dotted codes
# ---------------------------------------------------------------------------

# Unified lookup: arXiv categories, S2 fields, and common research tags → short labels
_TAG_ABBREV: dict[str, str] = {
    # -- arXiv: Computer Science --
    "Computer Science - Artificial Intelligence": "AI",
    "Computer Science - Hardware Architecture": "Hardware",
    "Computer Science - Computational Complexity": "Complexity",
    "Computer Science - Computational Engineering, Finance, and Science": "CompEng",
    "Computer Science - Computational Geometry": "CompGeom",
    "Computer Science - Computation and Language": "NLP",
    "Computer Science - Cryptography and Security": "Security",
    "Computer Science - Computer Vision and Pattern Recognition": "Vision",
    "Computer Science - Computers and Society": "CS+Society",
    "Computer Science - Databases": "Databases",
    "Computer Science - Distributed, Parallel, and Cluster Computing": "Distributed",
    "Computer Science - Digital Libraries": "DigLib",
    "Computer Science - Discrete Mathematics": "DiscMath",
    "Computer Science - Data Structures and Algorithms": "Algorithms",
    "Computer Science - Emerging Technologies": "EmergTech",
    "Computer Science - Formal Languages and Automata Theory": "Automata",
    "Computer Science - General Literature": "CS General",
    "Computer Science - Graphics": "Graphics",
    "Computer Science - Computer Science and Game Theory": "GameTheory",
    "Computer Science - Human-Computer Interaction": "HCI",
    "Computer Science - Information Retrieval": "InfoRetrieval",
    "Computer Science - Information Theory": "InfoTheory",
    "Computer Science - Machine Learning": "ML",
    "Computer Science - Logic in Computer Science": "Logic",
    "Computer Science - Multiagent Systems": "Multiagent",
    "Computer Science - Multimedia": "Multimedia",
    "Computer Science - Mathematical Software": "MathSW",
    "Computer Science - Numerical Analysis": "NumAnalysis",
    "Computer Science - Neural and Evolutionary Computing": "NeuroEvo",
    "Computer Science - Networking and Internet Architecture": "Networking",
    "Computer Science - Other Computer Science": "CS Other",
    "Computer Science - Operating Systems": "OS",
    "Computer Science - Performance": "Perf",
    "Computer Science - Programming Languages": "ProgLang",
    "Computer Science - Robotics": "Robotics",
    "Computer Science - Symbolic Computation": "Symbolic",
    "Computer Science - Sound": "Audio",
    "Computer Science - Software Engineering": "SWE",
    "Computer Science - Social and Information Networks": "SocNetworks",
    "Computer Science - Systems and Control": "Controls",
    # -- arXiv: Quantitative Biology --
    "Quantitative Biology - Biomolecules": "Biomolecules",
    "Quantitative Biology - Cell Behavior": "CellBio",
    "Quantitative Biology - Genomics": "Genomics",
    "Quantitative Biology - Molecular Networks": "MolNetworks",
    "Quantitative Biology - Neurons and Cognition": "Neuroscience",
    "Quantitative Biology - Other Quantitative Biology": "QBio",
    "Quantitative Biology - Populations and Evolution": "PopEvo",
    "Quantitative Biology - Quantitative Methods": "QBioMethods",
    "Quantitative Biology - Subcellular Processes": "Subcellular",
    "Quantitative Biology - Tissues and Organs": "Tissues",
    # -- arXiv: Quantitative Finance --
    "Quantitative Finance - Computational Finance": "CompFinance",
    "Quantitative Finance - General Finance": "Finance",
    "Quantitative Finance - Portfolio Management": "Portfolio",
    "Quantitative Finance - Risk Management": "RiskMgmt",
    "Quantitative Finance - Statistical Finance": "StatFinance",
    "Quantitative Finance - Trading and Market Microstructure": "Trading",
    # -- arXiv: EESS --
    "Electrical Engineering and Systems Science - Audio and Speech Processing": "Speech",
    "Electrical Engineering and Systems Science - Image and Video Processing": "ImageProc",
    "Electrical Engineering and Systems Science - Signal Processing": "Signals",
    "Electrical Engineering and Systems Science - Systems and Control": "Controls",
    # -- arXiv: Statistics --
    "Statistics - Applications": "StatApps",
    "Statistics - Computation": "StatComp",
    "Statistics - Machine Learning": "StatML",
    "Statistics - Methodology": "StatMethods",
    "Statistics - Other Statistics": "Stats",
    "Statistics - Statistics Theory": "StatTheory",
    # -- arXiv: Physics --
    "Physics - Biological Physics": "BioPhysics",
    "Physics - Chemical Physics": "ChemPhysics",
    "Physics - Classical Physics": "ClassPhysics",
    "Physics - Optics": "Optics",
    "Physics - Plasma Physics": "Plasma",
    # -- S2 broad fields --
    "Computer Science": "CS",
    "Environmental Science": "EnvSci",
    "Materials Science": "MatSci",
    "Political Science": "PoliSci",
    "Agricultural and Food Sciences": "AgriFood",
    # -- Common research tags (Zotero / manual) --
    "reinforcement learning": "RL",
    "Machine learning": "ML",
    "machine learning": "ML",
    "Gene regulatory networks": "GRN",
    "Gene expression": "GeneExpr",
    "Protein structure": "ProtStruct",
    "Protein structure prediction": "ProtPredict",
    "Protein folding": "ProtFolding",
    "Protein design": "ProtDesign",
    "Macromolecular design": "MolDesign",
    "Computational models": "CompModels",
    "Computational biology and bioinformatics": "CompBio",
    "Computational chemistry": "CompChem",
    "Computational science": "CompSci",
    "Genome informatics": "GenomeInfo",
    "Molecular biophysics": "MolBioPhys",
    "Molecular dynamics": "MolDyn",
    "Molecular modelling": "MolModel",
    "Scanning probe microscopy": "SPM",
    "Cryoelectron microscopy": "CryoEM",
    "Statistical physics": "StatPhysics",
    "Predictive medicine": "PredMed",
    "Synthetic biology": "SynBio",
    "Quantum metrology": "QuantMetro",
    "organic chemistry": "OrgChem",
    "Image processing": "ImageProc",
    "Spatial filtering": "SpatFilter",
    "Fourier transforms": "Fourier",
    "Ultraviolet lasers": "UV Lasers",
    "Cylindrical lenses": "CylLenses",
    "Latent profile analysis": "LPA",
    "Recreational runners": "Runners",
    "Sports injuries": "SportsInj",
    "large-language-models": "LLM",
    "World Models": "WorldModels",
}

# Tags ≤ this length pass through unchanged (covers "Biology", "Medicine", etc.)
_SHORT_TAG_LEN = 14


def _abbreviate_tag(tag: str) -> str:
    """Abbreviate a tag to a short human-readable label.

    Lookup order: static map → passthrough if short → smart truncation.
    """
    short = _TAG_ABBREV.get(tag)
    if short:
        return short
    if len(tag) <= _SHORT_TAG_LEN:
        return tag
    # Smart truncation for unknown long tags: keep significant words
    words = [w for w in tag.split() if len(w) > 3 or w[0].isupper()]
    if words:
        return "".join(w[:4].title() for w in words[:3])
    return tag[:_SHORT_TAG_LEN]


def _tag_pills_html(tags: list, max_tags: int = 3) -> str:
    """Render topic tags as colored HTML pill badges (max_tags shown)."""
    if not tags:
        return ""
    pills = []
    for tag in tags[:max_tags]:
        label = _abbreviate_tag(tag)
        bg = _PILL_COLORS[hash(tag) % len(_PILL_COLORS)]
        pills.append(
            f'<span style="display:inline-block;background:{bg};'
            f'color:#555;padding:0 4px;border-radius:8px;'
            f'font-size:10px;line-height:13px;margin:1px 1px;'
            f'mso-line-height-rule:exactly;">{label}</span>'
        )
    return " ".join(pills)


def _reading_stats_line(papers: list, label: str) -> str:
    """Render a compact stats line like 'Week: 3 papers · 65 pages · 3,830 h/l words'."""
    count = len(papers)
    total_pages = sum(d.get("page_count", 0) for d in papers)
    words = sum(d.get("highlight_word_count", 0) for d in papers)

    parts = [f"{label}: {count} paper{'s' if count != 1 else ''}"]
    if total_pages:
        parts.append(f"{total_pages:,} pages")
    if words:
        parts.append(f"{words:,} h/l words")
    return " \u00b7 ".join(parts)


def _reading_stats_html(state: State) -> str:
    """Render reading stats footer with week and month lines."""
    now = datetime.now(timezone.utc)
    week_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
    month_ago = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)

    week_papers = state.documents_processed_since(week_ago.isoformat())
    month_papers = state.documents_processed_since(month_ago.isoformat())

    week_line = _reading_stats_line(week_papers, "Week")
    month_line = _reading_stats_line(month_papers, "Month")

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

    oldest_html = f" &middot; oldest {oldest_days}d" if total else ""

    return (
        f'<p style="color:#999;font-size:13px;margin:0;">'
        f'Queue: {total} waiting'
        f'{oldest_html}'
        f' &middot; +{added_this_week}/-{processed_this_week} this week'
        f'{awaiting_html}</p>'
    )


def _trending_html(papers: list) -> str:
    """Build HTML for a 'Trending on HuggingFace' section."""
    if not papers:
        return ""
    lines = [
        '<p style="margin-top:20px;"><strong>Trending on HuggingFace</strong></p>',
        "<ul style='padding-left: 20px;'>",
    ]
    for p in papers:
        title = p.get("title", "?")
        hf_url = p.get("hf_url", "")
        ai_summary = p.get("ai_summary", "")
        upvotes = p.get("upvotes", 0)

        title_html = (
            f'<a href="{hf_url}" style="color:#333;">{title}</a>'
            if hf_url else title
        )
        summary_html = f" &mdash; {ai_summary}" if ai_summary else ""
        upvote_badge = (
            f' <span style="color:#999;font-size:12px;">'
            f"\u25b2{upvotes}</span>"
        )
        lines.append(
            f"<li style='margin-bottom:10px;'>"
            f"{title_html}{summary_html}{upvote_badge}</li>"
        )
    lines.append("</ul>")
    return "\n".join(lines)


def _fetch_trending_for_email(state: State, limit: int = 5) -> list:
    """Fetch trending papers, optionally filtered by user's topics."""
    try:
        from distillate import huggingface
        papers = huggingface.trending_papers(limit=limit)
        return papers
    except Exception:
        log.warning("Could not fetch HF trending for email", exc_info=True)
        return []


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
        print(f"No papers processed in the last {days} days — digest not sent.")
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


def _paper_html(p, index: int = 0):
    title = p.get("title", "Untitled")
    summary = p.get("summary", "")
    url = _paper_url(p)
    highlight_count = p.get("highlight_count", 0)
    engagement = p.get("engagement", 0)
    highlight_word_count = p.get("highlight_word_count", 0)
    processed_at = p.get("processed_at", "")

    # Title links to web URL (works on mobile), Obsidian link as secondary
    from distillate import obsidian
    citekey = p.get("metadata", {}).get("citekey", "")
    if url:
        title_html = (
            f'<a href="{url}" style="color:#333;text-decoration:none;">'
            f'<strong>{title}</strong></a>'
        )
    else:
        title_html = f"<strong>{title}</strong>"
    obsidian_uri = obsidian.get_obsidian_uri(title, citekey=citekey)
    obsidian_html = (
        f' <a href="{obsidian_uri}" style="color:#999;font-size:11px;">'
        f'Open in Obsidian</a>'
        if obsidian_uri else ""
    )

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

    index_html = (
        f'<span style="color:#999;">[{index}]</span> '
        if index else ""
    )

    return (
        f"<li style='margin-bottom: 14px;'>"
        f"{index_html}{title_html}{date_html}{obsidian_html}{stats_html}{summary_html}"
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
        idx = state.index_of(p.get("zotero_item_key", ""))
        lines.append(_paper_html(p, index=idx))

    lines.append("</ul>")
    lines.append(_reading_stats_html(state))
    lines.append(_queue_health_html(state))
    trending = _fetch_trending_for_email(state, limit=5)
    if trending:
        lines.append(_trending_html(trending))
    lines.append(_SIGNATURE)
    lines.append("</body></html>")
    return "\n".join(lines)


def _compute_suggestions(state: State) -> str | None:
    """Call Claude to pick 3 papers. Returns suggestion text or None."""
    unread = state.documents_with_status("on_remarkable")
    if not unread:
        log.info("No papers in reading queue, skipping suggestion")
        return None

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
        return None

    # Store picks for auto-promote
    title_to_key = {doc["title"].lower(): doc["zotero_item_key"] for doc in unread}
    known_titles = [doc["title"] for doc in unread]
    pending = []
    for line in result.strip().split("\n"):
        matched = match_suggestion_to_title(line, known_titles)
        if matched:
            key = title_to_key.get(matched.lower())
            if key and key not in pending:
                pending.append(key)
    if pending:
        state.pending_promotions = pending

    # Persist suggestion text + timestamp locally and to Gist
    today = datetime.now(timezone.utc).isoformat()
    state._data["last_suggestion"] = {"text": result, "timestamp": today}
    state.save()
    log.info("Stored %d pending promotion(s)", len(pending))
    _push_pending_to_gist(pending, result)

    return result


def _get_todays_suggestions(state: State) -> str | None:
    """Return cached suggestion text if already computed today, else None."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check local state first
    local = state._data.get("last_suggestion", {})
    if local.get("timestamp", "")[:10] == today:
        log.info("Reusing today's suggestions (cached locally)")
        return local["text"]

    # Check Gist (for GH Actions runs that wrote to Gist but not local state)
    gist = fetch_pending_from_gist()
    if gist and gist.get("timestamp", "")[:10] == today:
        text = gist.get("suggestion_text", "")
        if text:
            log.info("Reusing today's suggestions (cached in Gist)")
            return text

    return None


def send_suggestion() -> None:
    """Send a daily suggestion email. Computes suggestions at most once per day."""
    config.setup_logging()

    state = State()
    _sync_tags(state)

    unread = state.documents_with_status("on_remarkable")
    if not unread:
        log.info("No papers in reading queue, skipping suggestion")
        return

    # Reuse today's suggestions if already computed, otherwise call Claude
    result = _get_todays_suggestions(state)
    if result:
        log.info("Suggestions already computed today, re-sending email")
    else:
        result = _compute_suggestions(state)
        if not result:
            return

    subject = datetime.now().strftime("What to read next \u2013 %b %-d, %Y")
    body = _build_suggestion_body(result, unread, state)

    _send_email(subject, body)


def _rank_tags(tags: list, user_top_tags: list) -> list:
    """Sort tags by user reading frequency, putting most-read topics first."""
    if not tags or not user_top_tags:
        return tags
    tag_rank = {t: i for i, t in enumerate(user_top_tags)}
    return sorted(tags, key=lambda t: tag_rank.get(t, len(user_top_tags)))


def _build_suggestion_body(suggestion_text, unread, state: State):
    """Build HTML body from Claude's suggestion text."""
    # Build title -> doc lookup from full unread list
    known_titles = [doc["title"] for doc in unread]
    url_lookup = {}
    tags_lookup = {}
    index_lookup = {}
    user_top_tags = _recent_topic_tags(state, limit=20)
    for doc in unread:
        t_lower = doc["title"].lower()
        url_lookup[t_lower] = _paper_url(doc)
        raw_tags = doc.get("metadata", {}).get("tags", [])
        tags_lookup[t_lower] = _rank_tags(raw_tags, user_top_tags)
        index_lookup[t_lower] = state.index_of(doc.get("zotero_item_key", ""))

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
        paper_idx = 0
        matched_title = ""
        matched = match_suggestion_to_title(line, known_titles)
        if matched:
            matched_lower = matched.lower()
            url = url_lookup.get(matched_lower, "")
            tags = tags_lookup.get(matched_lower, [])
            paper_idx = index_lookup.get(matched_lower, 0)
            rest_lower = rest.lower()
            idx = rest_lower.find(matched_lower)
            if idx >= 0:
                matched_title = rest[idx:idx + len(matched_lower)]

        # Split into title and reason at " — " or " - "
        if matched_title:
            # Known title found literally in rest
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
        pills_html = f"<br>{_tag_pills_html(tags)}" if tags else ""
        url_html = (
            f'<br><a href="{url}" style="color:#666;font-size:13px;">{url}</a>'
            if url else ""
        )

        idx_label = paper_idx if paper_idx else queue_num
        lines.append(
            f"<li style='margin-bottom: 14px;'>"
            f'<span style="color:#999;">[{idx_label}]</span> '
            f"{title_html}{reason_html}{pills_html}{url_html}"
            f"</li>"
        )

    lines.append("</ul>")
    lines.append(_reading_stats_html(state))
    lines.append(_queue_health_html(state))
    trending = _fetch_trending_for_email(state, limit=5)
    if trending:
        lines.append(_trending_html(trending))
    lines.append(_SIGNATURE)
    lines.append("</body></html>")
    return "\n".join(lines)
