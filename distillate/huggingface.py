"""HuggingFace Daily Papers integration.

Provides trending paper discovery and metadata enrichment (GitHub repo/stars)
via the public HuggingFace API. No authentication required.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

_BASE = "https://huggingface.co/api"
_TIMEOUT = 15


def _parse_paper(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a daily_papers API entry into a flat dict."""
    paper = entry.get("paper", entry)
    return {
        "arxiv_id": paper.get("id", ""),
        "title": paper.get("title", ""),
        "abstract": paper.get("summary", ""),
        "authors": [a["name"] for a in paper.get("authors", []) if not a.get("hidden")],
        "upvotes": paper.get("upvotes", 0),
        "ai_summary": paper.get("ai_summary", ""),
        "ai_keywords": paper.get("ai_keywords", []),
        "github_repo": paper.get("githubRepo"),
        "github_stars": paper.get("githubStars"),
        "pdf_url": f"https://arxiv.org/pdf/{paper['id']}" if paper.get("id") else None,
        "hf_url": f"https://huggingface.co/papers/{paper['id']}" if paper.get("id") else None,
    }


def trending_papers(limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch today's trending papers from HuggingFace Daily Papers."""
    try:
        resp = requests.get(
            f"{_BASE}/daily_papers",
            params={"sort": "trending", "limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return [_parse_paper(entry) for entry in resp.json()]
    except Exception:
        log.warning("Failed to fetch HuggingFace trending papers", exc_info=True)
        return []


def trending_papers_for_week(week: str = "", limit: int = 5) -> List[Dict[str, Any]]:
    """Fetch trending papers for a given ISO week (e.g. '2026-W08').

    If week is empty, uses the current week.
    """
    if not week:
        now = datetime.now(timezone.utc)
        week = now.strftime("%G-W%V")
    try:
        resp = requests.get(
            f"{_BASE}/daily_papers",
            params={"week": week, "sort": "trending", "limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return [_parse_paper(entry) for entry in resp.json()]
    except Exception:
        log.warning("Failed to fetch HuggingFace weekly trending", exc_info=True)
        return []


def lookup_paper(arxiv_id: str) -> Optional[Dict[str, Any]]:
    """Look up a paper by arXiv ID for metadata enrichment.

    Returns title, authors, abstract, github_repo, github_stars,
    upvotes, ai_keywords — or None if not found.
    """
    if not arxiv_id:
        return None
    try:
        resp = requests.get(f"{_BASE}/papers/{arxiv_id}", timeout=_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return {
            "title": data.get("title", ""),
            "authors": [
                a["name"]
                for a in data.get("authors", [])
                if not a.get("hidden")
            ],
            "abstract": data.get("summary", ""),
            "github_repo": data.get("githubRepo"),
            "github_stars": data.get("githubStars"),
            "upvotes": data.get("upvotes", 0),
            "ai_keywords": data.get("ai_keywords", []),
        }
    except Exception:
        log.warning("Failed to look up paper %s on HuggingFace", arxiv_id, exc_info=True)
        return None
