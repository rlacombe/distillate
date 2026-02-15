"""Semantic Scholar API integration.

Looks up papers by DOI or arXiv ID (preferred), falls back to title search.
Fetches citation counts. Free API, no key needed.
"""

import logging
import re
import time
from typing import Any, Dict, Optional

import requests

from distillate import config

log = logging.getLogger(__name__)

_BASE = "https://api.semanticscholar.org"
_PAPER_FIELDS = "citationCount,influentialCitationCount,url"

# Delay between API calls to avoid rate limits (free tier: ~1 req/sec)
_REQUEST_DELAY = 1.5

# Match arXiv IDs in DOIs or URLs
_ARXIV_DOI_RE = re.compile(r"10\.48550/arXiv\.(\d+\.\d+)")
_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d+\.\d+)")


def lookup_paper(
    doi: str = "", title: str = "", url: str = "",
) -> Optional[Dict[str, Any]]:
    """Look up a paper on Semantic Scholar.

    Tries arXiv ID first (extracted from DOI or URL), then publisher DOI,
    then title search. Returns None if the paper can't be found.
    """
    paper = None

    # Try arXiv ID first (extracted from DOI or URL)
    arxiv_id = _extract_arxiv_id(doi, url)
    if arxiv_id:
        paper = _fetch_by_id(f"ARXIV:{arxiv_id}")

    # Try publisher DOI (skip arXiv meta-DOIs)
    if paper is None and doi and not doi.startswith("10.48550/"):
        paper = _fetch_by_id(f"DOI:{doi}")

    # Fall back to title search
    if paper is None and title:
        paper = _fetch_by_title(title)

    if paper is None:
        return None

    citation_count = paper.get("citationCount") or 0
    influential = paper.get("influentialCitationCount") or 0
    s2_url = paper.get("url") or ""

    return {
        "citation_count": citation_count,
        "influential_citation_count": influential,
        "s2_url": s2_url,
    }


def _extract_arxiv_id(doi: str, url: str) -> str:
    """Extract arXiv ID from a DOI or URL, or return empty string."""
    if doi:
        m = _ARXIV_DOI_RE.search(doi)
        if m:
            return m.group(1)
    if url:
        m = _ARXIV_URL_RE.search(url)
        if m:
            return m.group(1)
    return ""


def _fetch_by_id(paper_id: str) -> Optional[Dict[str, Any]]:
    """Fetch paper metadata by S2 paper identifier (DOI:xxx or ARXIV:xxx)."""
    try:
        time.sleep(_REQUEST_DELAY)
        resp = requests.get(
            f"{_BASE}/graph/v1/paper/{paper_id}",
            params={"fields": _PAPER_FIELDS},
            timeout=config.HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            return _retry_on_429(
                f"{_BASE}/graph/v1/paper/{paper_id}",
                {"fields": _PAPER_FIELDS},
            )
        log.debug("S2 lookup returned %d for %s", resp.status_code, paper_id)
    except Exception:
        log.debug("S2 lookup failed for %s", paper_id, exc_info=True)
    return None


def _fetch_by_title(title: str) -> Optional[Dict[str, Any]]:
    """Fetch paper metadata by title search (first result)."""
    try:
        time.sleep(_REQUEST_DELAY)
        resp = requests.get(
            f"{_BASE}/graph/v1/paper/search",
            params={"query": title, "limit": 1, "fields": _PAPER_FIELDS},
            timeout=config.HTTP_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            papers = data.get("data", [])
            if papers:
                return papers[0]
        if resp.status_code == 429:
            result = _retry_on_429(
                f"{_BASE}/graph/v1/paper/search",
                {"query": title, "limit": 1, "fields": _PAPER_FIELDS},
            )
            if result:
                papers = result.get("data", [])
                if papers:
                    return papers[0]
            return None
        log.debug("S2 title search returned %d for '%s'", resp.status_code, title)
    except Exception:
        log.debug("S2 title search failed for '%s'", title, exc_info=True)
    return None


def _retry_on_429(url: str, params: dict, retries: int = 3) -> Optional[Any]:
    """Retry a request with exponential backoff on 429."""
    for attempt in range(retries):
        delay = 5 * (2 ** attempt)  # 5s, 10s, 20s
        log.debug("S2 rate limited, retrying in %ds (attempt %d/%d)", delay, attempt + 1, retries)
        time.sleep(delay)
        try:
            resp = requests.get(url, params=params, timeout=config.HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code != 429:
                return None
        except Exception:
            return None
    log.debug("S2 rate limit retries exhausted")
    return None
