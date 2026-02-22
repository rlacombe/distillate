"""Agent tool definitions for Distillate.

Each tool is a pure function that takes `state` as a keyword argument
(injected by the dispatcher, invisible to Claude) and returns a
JSON-serializable dict.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_papers_from_state(query: str, state) -> List[tuple]:
    """Find matching (item_key, doc) pairs from state.

    Reuses main._find_papers logic: index number, exact citekey,
    citekey substring, title substring.
    """
    from distillate.main import _find_papers
    return _find_papers(query, state)


def _read_note_content(citekey: str, title: str) -> Optional[str]:
    """Read the Obsidian markdown note for a paper, if it exists."""
    from distillate.obsidian import _read_dir, _sanitize_note_name
    rd = _read_dir()
    if rd is None:
        return None
    filename = citekey if citekey else _sanitize_note_name(title)
    note_path = rd / f"{filename}.md"
    if note_path.exists():
        return note_path.read_text(encoding="utf-8")
    return None


def _extract_highlights_from_note(note_text: str) -> str:
    """Extract the Highlights section from an Obsidian note."""
    start = note_text.find("## Highlights")
    if start < 0:
        return ""
    end = note_text.find("\n## ", start + 1)
    section = note_text[start:end] if end > 0 else note_text[start:]
    # Truncate to keep tool results compact
    if len(section) > 3000:
        section = section[:3000] + "\n... (truncated)"
    return section


def _paper_summary(key: str, doc: dict, state) -> dict:
    """Build a concise paper summary dict for tool results."""
    meta = doc.get("metadata", {})
    idx = state.index_of(key)
    return {
        "index": idx,
        "key": key,
        "title": doc.get("title", ""),
        "citekey": meta.get("citekey", ""),
        "status": doc.get("status", ""),
        "authors": doc.get("authors", []),
        "summary": doc.get("summary", ""),
        "engagement": doc.get("engagement", 0),
        "tags": meta.get("tags", []),
        "citation_count": meta.get("citation_count", 0),
        "publication_date": meta.get("publication_date", ""),
    }


# ---------------------------------------------------------------------------
# Tool schemas (sent to Claude)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "search_papers",
        "description": (
            "Search the paper library by title, citekey, index number, "
            "or topic tag. Returns matching papers with basic metadata."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Title substring, citekey, index number, or topic tag",
                },
                "status": {
                    "type": "string",
                    "enum": ["on_remarkable", "processed", "awaiting_pdf"],
                    "description": "Optional status filter",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_paper_details",
        "description": (
            "Get full details for a single paper including metadata, "
            "highlights, summary, and engagement score. Use this when "
            "the user asks about a specific paper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Paper index number, citekey, or title substring",
                },
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "get_reading_stats",
        "description": (
            "Get reading statistics: papers read, pages, highlights, "
            "engagement averages, queue size, and top research topics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period_days": {
                    "type": "integer",
                    "description": "Number of days to look back (default 30)",
                },
            },
        },
    },
    {
        "name": "get_queue",
        "description": (
            "Get the current reading queue — papers on the reMarkable tablet "
            "waiting to be read. Shows days in queue for each paper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_recent_reads",
        "description": (
            "Get recently read papers with their summaries, engagement "
            "scores, and highlight counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of recent papers to return (default 10)",
                },
            },
        },
    },
    {
        "name": "suggest_next_reads",
        "description": (
            "Analyze the reading queue and suggest which papers to read next, "
            "based on recent interests, diversity, and queue age."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "synthesize_across_papers",
        "description": (
            "Synthesize insights across multiple papers to answer a research "
            "question. Gathers highlights, summaries, and abstracts from the "
            "specified papers and produces a synthesis that cites each paper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_identifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of paper identifiers (index, citekey, or title)",
                },
                "question": {
                    "type": "string",
                    "description": "The research question or topic to synthesize around",
                },
            },
            "required": ["paper_identifiers", "question"],
        },
    },
    {
        "name": "run_sync",
        "description": (
            "Trigger the full Zotero → reMarkable → notes sync pipeline. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "reprocess_paper",
        "description": (
            "Re-extract highlights and regenerate the note for a paper. "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Paper to reprocess (index, citekey, or title)",
                },
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "promote_papers",
        "description": (
            "Promote papers to the reMarkable tablet home screen so they're "
            "easy to find and read next. Demotes previously promoted papers "
            "back to Inbox (unless the user started reading them). "
            "This is a write operation — ask the user to confirm first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of paper identifiers (index numbers, citekeys, "
                        "or titles) to promote"
                    ),
                },
            },
            "required": ["identifiers"],
        },
    },
    {
        "name": "get_trending_papers",
        "description": (
            "Fetch today's trending AI/ML papers from HuggingFace Daily Papers. "
            "Returns titles, authors, upvotes, AI-generated summaries, and keywords. "
            "Use when the user asks about trending research or wants paper recommendations "
            "beyond their own library."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max papers to return (default 10)",
                },
            },
        },
    },
    {
        "name": "add_paper_to_zotero",
        "description": (
            "Add a new paper to the user's Zotero library so it gets synced to "
            "reMarkable on the next distillate run. Provide an arXiv ID or URL "
            "and the PDF will be downloaded automatically during sync. "
            "arXiv URLs are auto-converted to PDF downloads. "
            "If an arXiv ID is provided or detected from the URL, metadata "
            "(title, authors, abstract) is auto-enriched from HuggingFace."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Paper title (optional if arxiv_id or arXiv URL provided)",
                },
                "authors": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Author names (e.g. ['Alice Smith', 'Bob Jones'])",
                },
                "arxiv_id": {
                    "type": "string",
                    "description": "arXiv ID (e.g. '2401.12345'). PDF auto-downloaded during sync.",
                },
                "doi": {
                    "type": "string",
                    "description": "DOI (e.g. '10.1234/example')",
                },
                "url": {
                    "type": "string",
                    "description": "Paper URL (arXiv, biorxiv, or direct PDF link). arXiv/biorxiv URLs auto-resolve to PDF.",
                },
                "abstract": {
                    "type": "string",
                    "description": "Paper abstract",
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def search_papers(*, state, query: str, status: str = None) -> dict:
    """Search papers by title, citekey, index, or tag."""
    matches = _find_papers_from_state(query, state)

    # Also try tag search if no matches from title/citekey
    if not matches:
        query_lower = query.lower()
        for key, doc in state.documents.items():
            tags = doc.get("metadata", {}).get("tags", [])
            if any(query_lower in t.lower() for t in tags):
                matches.append((key, doc))

    # Apply status filter
    if status:
        matches = [(k, d) for k, d in matches if d.get("status") == status]

    results = [_paper_summary(k, d, state) for k, d in matches[:20]]
    return {"results": results, "total": len(matches)}


def get_paper_details(*, state, identifier: str) -> dict:
    """Get full details for a single paper."""
    matches = _find_papers_from_state(identifier, state)
    if not matches:
        return {"found": False, "error": f"No paper found matching '{identifier}'"}

    key, doc = matches[0]
    meta = doc.get("metadata", {})
    idx = state.index_of(key)

    paper = {
        "index": idx,
        "key": key,
        "title": doc.get("title", ""),
        "citekey": meta.get("citekey", ""),
        "status": doc.get("status", ""),
        "authors": doc.get("authors", []),
        "doi": meta.get("doi", ""),
        "url": meta.get("url", ""),
        "journal": meta.get("journal", ""),
        "publication_date": meta.get("publication_date", ""),
        "paper_type": meta.get("paper_type", ""),
        "tags": meta.get("tags", []),
        "abstract": meta.get("abstract", ""),
        "summary": doc.get("summary", ""),
        "engagement": doc.get("engagement", 0),
        "highlight_count": doc.get("highlight_count", 0),
        "highlight_word_count": doc.get("highlight_word_count", 0),
        "page_count": doc.get("page_count", 0),
        "citation_count": meta.get("citation_count", 0),
        "influential_citation_count": meta.get("influential_citation_count", 0),
        "s2_url": meta.get("s2_url", ""),
        "github_repo": meta.get("github_repo", ""),
        "github_stars": meta.get("github_stars"),
        "uploaded_at": doc.get("uploaded_at", ""),
        "processed_at": doc.get("processed_at", ""),
    }

    # Read highlights from Obsidian note
    highlights = ""
    notes_text = ""
    citekey = meta.get("citekey", "")
    note_content = _read_note_content(citekey, doc.get("title", ""))
    if note_content:
        highlights = _extract_highlights_from_note(note_content)
        # Extract handwritten notes section too
        hw_start = note_content.find("## Handwritten Notes")
        if hw_start >= 0:
            hw_end = note_content.find("\n## ", hw_start + 1)
            notes_text = note_content[hw_start:hw_end] if hw_end > 0 else note_content[hw_start:]
            if len(notes_text) > 1500:
                notes_text = notes_text[:1500] + "\n... (truncated)"

    return {
        "found": True,
        "paper": paper,
        "highlights": highlights,
        "handwritten_notes": notes_text,
    }


def get_reading_stats(*, state, period_days: int = 30) -> dict:
    """Get reading statistics for a time period."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=period_days)).isoformat()
    recent = state.documents_processed_since(since)

    queue = state.documents_with_status("on_remarkable")
    awaiting = state.documents_with_status("awaiting_pdf")
    all_processed = state.documents_with_status("processed")

    total_pages = sum(d.get("page_count", 0) for d in recent)
    total_hl_words = sum(d.get("highlight_word_count", 0) for d in recent)
    engagements = [d.get("engagement", 0) for d in recent if d.get("engagement")]
    avg_engagement = round(sum(engagements) / len(engagements)) if engagements else 0

    # Top tags from recent reads
    tag_counts: Dict[str, int] = {}
    for doc in recent:
        for tag in doc.get("metadata", {}).get("tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = sorted(tag_counts, key=tag_counts.get, reverse=True)[:10]

    # Queue age
    queue_oldest_days = 0
    if queue:
        oldest = min(
            (d.get("uploaded_at", "") for d in queue),
            default="",
        )
        if oldest:
            try:
                dt = datetime.fromisoformat(oldest)
                queue_oldest_days = (now - dt).days
            except (ValueError, TypeError):
                pass

    return {
        "period_days": period_days,
        "papers_read": len(recent),
        "total_pages": total_pages,
        "total_highlight_words": total_hl_words,
        "avg_engagement": avg_engagement,
        "queue_size": len(queue),
        "queue_oldest_days": queue_oldest_days,
        "awaiting_pdf": len(awaiting),
        "total_processed": len(all_processed),
        "top_tags": top_tags,
    }


def get_queue(*, state) -> dict:
    """Get the current reading queue."""
    now = datetime.now(timezone.utc)
    queue = state.documents_with_status("on_remarkable")
    promoted = set(state.promoted_papers)

    papers = []
    for doc in queue:
        key = doc.get("zotero_item_key", "")
        meta = doc.get("metadata", {})
        uploaded = doc.get("uploaded_at", "")
        days = 0
        if uploaded:
            try:
                days = (now - datetime.fromisoformat(uploaded)).days
            except (ValueError, TypeError):
                pass
        papers.append({
            "index": state.index_of(key),
            "title": doc.get("title", ""),
            "citekey": meta.get("citekey", ""),
            "days_in_queue": days,
            "tags": meta.get("tags", []),
            "citation_count": meta.get("citation_count", 0),
            "paper_type": meta.get("paper_type", ""),
            "promoted": key in promoted,
        })

    papers.sort(key=lambda p: p["days_in_queue"], reverse=True)
    return {"queue": papers, "total": len(papers)}


def get_recent_reads(*, state, count: int = 10) -> dict:
    """Get recently read papers with summaries."""
    # Look back far enough to get at least `count` papers
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=365)).isoformat()
    all_recent = state.documents_processed_since(since)

    # Most recent first
    all_recent.reverse()
    papers = []
    for doc in all_recent[:count]:
        key = doc.get("zotero_item_key", "")
        meta = doc.get("metadata", {})
        papers.append({
            "index": state.index_of(key),
            "title": doc.get("title", ""),
            "citekey": meta.get("citekey", ""),
            "summary": doc.get("summary", ""),
            "engagement": doc.get("engagement", 0),
            "date_read": doc.get("processed_at", ""),
            "tags": meta.get("tags", []),
            "highlight_count": doc.get("highlight_count", 0),
            "citation_count": meta.get("citation_count", 0),
        })

    return {"papers": papers, "total": len(all_recent)}


def suggest_next_reads(*, state) -> dict:
    """AI-ranked suggestions from the reading queue."""
    from distillate import summarizer

    queue = state.documents_with_status("on_remarkable")
    if not queue:
        return {"suggestions": "Your reading queue is empty.", "queue_size": 0}

    # Build inputs for suggest_papers
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).isoformat()
    recent = state.documents_processed_since(since)

    unread = []
    for doc in queue:
        meta = doc.get("metadata", {})
        unread.append({
            "title": doc.get("title", ""),
            "tags": meta.get("tags", []),
            "paper_type": meta.get("paper_type", ""),
            "uploaded_at": doc.get("uploaded_at", ""),
            "citation_count": meta.get("citation_count", 0),
        })

    recent_reads = []
    for doc in recent:
        meta = doc.get("metadata", {})
        recent_reads.append({
            "title": doc.get("title", ""),
            "tags": meta.get("tags", []),
            "summary": doc.get("summary", ""),
            "engagement": doc.get("engagement", 0),
            "citation_count": meta.get("citation_count", 0),
        })

    result = summarizer.suggest_papers(unread, recent_reads)
    return {
        "suggestions": result or "Could not generate suggestions.",
        "queue_size": len(queue),
    }


def synthesize_across_papers(
    *, state, paper_identifiers: List[str], question: str,
) -> dict:
    """Cross-paper synthesis using Claude."""
    from distillate import config
    from distillate.summarizer import _call_claude

    papers_context = []
    titles_used = []

    for ident in paper_identifiers:
        matches = _find_papers_from_state(ident, state)
        if not matches:
            continue
        key, doc = matches[0]
        title = doc.get("title", "")
        titles_used.append(title)
        meta = doc.get("metadata", {})

        parts = [f"Title: {title}"]
        if doc.get("summary"):
            parts.append(f"Summary: {doc['summary']}")
        abstract = meta.get("abstract", "")
        if abstract:
            parts.append(f"Abstract: {abstract}")

        # Read highlights from note
        note = _read_note_content(meta.get("citekey", ""), title)
        if note:
            hl = _extract_highlights_from_note(note)
            if hl:
                # Cap per-paper highlight size
                parts.append(hl[:2000])

        papers_context.append("\n".join(parts))

    if not papers_context:
        return {"error": "No matching papers found.", "papers_used": []}

    prompt = (
        f"Synthesize insights across {len(papers_context)} research papers.\n\n"
        + "\n\n---\n\n".join(papers_context)
        + f"\n\nQuestion: {question}\n\n"
        "Provide a concise synthesis that draws connections between these papers. "
        "Cite specific papers by title when making claims."
    )

    result = _call_claude(prompt, max_tokens=800, model=config.CLAUDE_SMART_MODEL)
    return {
        "synthesis": result or "Could not generate synthesis.",
        "papers_used": titles_used,
    }


def run_sync(*, state) -> dict:
    """Trigger the full sync pipeline via subprocess."""
    import subprocess
    try:
        result = subprocess.run(
            ["distillate", "--sync"],
            capture_output=True, text=True, timeout=300,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            err = result.stderr.strip() or output or "Sync failed"
            return {"success": False, "error": err}
        return {"success": True, "output": output}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Sync timed out after 5 minutes."}
    except FileNotFoundError:
        return {"success": False, "error": "distillate command not found."}


def reprocess_paper(*, state, identifier: str) -> dict:
    """Re-extract highlights and regenerate note for a paper."""
    matches = _find_papers_from_state(identifier, state)
    if not matches:
        return {"success": False, "error": f"No paper found matching '{identifier}'"}

    if len(matches) > 1:
        titles = [d.get("title", "") for _, d in matches[:5]]
        return {
            "success": False,
            "error": f"Multiple papers match '{identifier}'. Be more specific.",
            "matches": titles,
        }

    key, doc = matches[0]
    title = doc.get("title", "")

    try:
        from distillate.main import _reprocess
        _reprocess([identifier])
        return {"success": True, "title": title}
    except Exception as e:
        log.exception("Reprocess failed for '%s'", title)
        return {"success": False, "error": str(e), "title": title}


def promote_papers(*, state, identifiers: List[str]) -> dict:
    """Promote papers to the reMarkable tablet home screen."""
    from distillate.state import acquire_lock, release_lock

    # Resolve identifiers to item keys
    pick_keys = []
    resolved_titles = []
    errors = []

    for ident in identifiers:
        matches = _find_papers_from_state(ident, state)
        if not matches:
            errors.append(f"No paper found matching '{ident}'")
            continue
        key, doc = matches[0]
        if doc.get("status") != "on_remarkable":
            errors.append(f"'{doc.get('title', ident)}' is not on reMarkable (status: {doc.get('status')})")
            continue
        pick_keys.append(key)
        resolved_titles.append(doc.get("title", ""))

    if not pick_keys:
        return {"success": False, "promoted": [], "errors": errors}

    if not acquire_lock():
        return {"success": False, "error": "Another distillate instance is running."}

    try:
        from distillate.main import _demote_and_promote
        _demote_and_promote(state, pick_keys, verbose=False)
        state.reload()
        return {
            "success": True,
            "promoted": resolved_titles,
            "errors": errors if errors else [],
        }
    except Exception as e:
        log.exception("Promote failed")
        return {"success": False, "error": str(e)}
    finally:
        release_lock()


def get_trending_papers(*, state, limit: int = 10) -> dict:
    """Fetch today's trending AI/ML papers from HuggingFace."""
    from distillate import huggingface

    papers = huggingface.trending_papers(limit=limit)
    results = []
    for p in papers:
        entry = {
            "title": p["title"],
            "authors": p["authors"][:3],
            "upvotes": p["upvotes"],
            "ai_summary": p["ai_summary"],
            "ai_keywords": p["ai_keywords"],
            "hf_url": p["hf_url"],
        }
        if p.get("github_repo"):
            entry["github_repo"] = p["github_repo"]
            entry["github_stars"] = p.get("github_stars")
        results.append(entry)
    return {"papers": results, "total": len(results)}


def add_paper_to_zotero(
    *, state,
    title: str = "",
    authors: List[str] | None = None,
    arxiv_id: str = "",
    doi: str = "",
    url: str = "",
    abstract: str = "",
) -> dict:
    """Add a new paper to the user's Zotero library."""
    from distillate import zotero_client

    # Extract arXiv ID from URL if not provided explicitly
    if not arxiv_id and url:
        from distillate.semantic_scholar import extract_arxiv_id
        arxiv_id = extract_arxiv_id("", url)

    # Enrich from HuggingFace if we have an arXiv ID
    hf_data = None
    if arxiv_id:
        try:
            from distillate import huggingface
            hf_data = huggingface.lookup_paper(arxiv_id)
        except Exception:
            log.debug("HF lookup failed for %s", arxiv_id, exc_info=True)
        if not url:
            url = f"https://arxiv.org/abs/{arxiv_id}"

    # Use HF data to fill gaps
    if hf_data:
        if not title or title == arxiv_id:
            title = hf_data.get("title", title)
        if not authors:
            authors = hf_data.get("authors", [])
        if not abstract:
            abstract = hf_data.get("abstract", "")

    # Build DOI-based URL fallback
    if doi and not url:
        url = f"https://doi.org/{doi}"

    # Must have at least a title
    if not title:
        return {
            "success": False,
            "error": "Could not determine paper title. Provide a title or a valid arXiv ID.",
        }

    # Duplicate check
    existing = state.find_by_title(title)
    if existing:
        return {
            "success": False,
            "error": f"Paper '{title}' is already in your library.",
        }
    if doi:
        existing = state.find_by_doi(doi)
        if existing:
            return {
                "success": False,
                "error": f"A paper with DOI {doi} is already in your library.",
            }

    # Create in Zotero
    tags = hf_data.get("ai_keywords", []) if hf_data else []
    item_key = zotero_client.create_paper(
        title=title,
        authors=authors or [],
        doi=doi,
        url=url,
        abstract=abstract,
        tags=tags,
    )
    if not item_key:
        return {"success": False, "error": "Failed to create paper in Zotero."}

    return {
        "success": True,
        "item_key": item_key,
        "title": title,
        "message": (
            f"Added '{title}' to Zotero. "
            "It will sync to reMarkable on the next distillate run."
        ),
    }
