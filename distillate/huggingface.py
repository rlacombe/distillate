"""HuggingFace integration — papers, models, datasets, and Hub search.

Provides trending paper discovery, metadata enrichment, and Hub search
via the public HuggingFace API and huggingface_hub client.

Paper functions (trending, lookup) require no authentication.
Hub search functions (models, datasets, paper associations) use HF_TOKEN
when available for higher rate limits and private repo access.
"""

import logging
import os
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
            "ai_summary": data.get("ai_summary", ""),
        }
    except Exception:
        log.warning("Failed to look up paper %s on HuggingFace", arxiv_id, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Token validation & account info
# ---------------------------------------------------------------------------

def _get_hf_token() -> str:
    from distillate import auth as _auth
    return _auth.hf_token_for("hub")


def _auth_headers() -> Dict[str, str]:
    token = _get_hf_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def validate_token(token: str = "") -> Dict[str, Any]:
    """Validate an HF token and return account info.

    Returns a dict with:
        ok: True if token is valid
        username: HF username
        fullname: Display name
        email: Account email (if available)
        plan: Account plan (free, pro, enterprise)
        orgs: List of organizations
        can_pay: Whether the account has billing set up
        error: Error message (if ok is False)
    """
    token = token or _get_hf_token()
    if not token:
        return {"ok": False, "error": "No token provided"}

    try:
        resp = requests.get(
            f"{_BASE}/whoami-v2",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 401:
            return {"ok": False, "error": "Invalid token"}
        resp.raise_for_status()
        data = resp.json()

        # Extract account info
        plan = data.get("plan", {})
        plan_name = plan.get("name", "free") if isinstance(plan, dict) else str(plan)

        orgs = []
        for org in data.get("orgs", []):
            orgs.append({
                "name": org.get("name", ""),
                "plan": org.get("plan", {}).get("name", "free") if isinstance(org.get("plan"), dict) else "free",
            })

        return {
            "ok": True,
            "username": data.get("name", ""),
            "fullname": data.get("fullname", ""),
            "email": data.get("email", ""),
            "plan": plan_name,
            "orgs": orgs,
            "can_pay": data.get("canPay", False),
            "token_name": data.get("auth", {}).get("accessToken", {}).get("displayName", ""),
        }
    except requests.exceptions.HTTPError:
        return {"ok": False, "error": "Invalid token or API error"}
    except Exception as e:
        log.warning("HF token validation failed", exc_info=True)
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Hub search functions (use huggingface_hub when available, REST fallback)
# ---------------------------------------------------------------------------


def search_papers(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search HuggingFace papers by query (keyword or semantic)."""
    try:
        resp = requests.get(
            f"{_BASE}/papers/search",
            params={"query": query, "limit": limit},
            headers=_auth_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
        if isinstance(results, list):
            return [_parse_paper(entry) for entry in results[:limit]]
        return []
    except Exception:
        log.warning("HF paper search failed for query: %s", query, exc_info=True)
        return []


def search_models(
    query: str,
    task: str = "",
    library: str = "",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Search HuggingFace Hub for models.

    Args:
        query: Search query (model name, architecture, etc.)
        task: Filter by task (e.g. "text-classification", "image-classification")
        library: Filter by library (e.g. "transformers", "diffusers")
        limit: Max results to return
    """
    try:
        params: Dict[str, Any] = {
            "search": query,
            "limit": limit,
            "sort": "downloads",
            "direction": -1,
        }
        if task:
            params["pipeline_tag"] = task
        if library:
            params["library"] = library

        resp = requests.get(
            f"{_BASE}/models",
            params=params,
            headers=_auth_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        models = resp.json()
        return [
            {
                "model_id": m.get("modelId", m.get("id", "")),
                "author": m.get("author", ""),
                "downloads": m.get("downloads", 0),
                "likes": m.get("likes", 0),
                "task": m.get("pipeline_tag", ""),
                "library": m.get("library_name", ""),
                "tags": m.get("tags", [])[:5],
                "last_modified": m.get("lastModified", ""),
                "url": f"https://huggingface.co/{m.get('modelId', m.get('id', ''))}",
            }
            for m in (models if isinstance(models, list) else [])[:limit]
        ]
    except Exception:
        log.warning("HF model search failed for: %s", query, exc_info=True)
        return []


def search_datasets(
    query: str,
    task: str = "",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Search HuggingFace Hub for datasets.

    Args:
        query: Search query
        task: Filter by task tag
        limit: Max results
    """
    try:
        params: Dict[str, Any] = {
            "search": query,
            "limit": limit,
            "sort": "downloads",
            "direction": -1,
        }
        if task:
            params["task_categories"] = task

        resp = requests.get(
            f"{_BASE}/datasets",
            params=params,
            headers=_auth_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        datasets = resp.json()
        return [
            {
                "dataset_id": d.get("id", ""),
                "author": d.get("author", ""),
                "downloads": d.get("downloads", 0),
                "likes": d.get("likes", 0),
                "tags": d.get("tags", [])[:5],
                "description": d.get("description", "")[:200],
                "last_modified": d.get("lastModified", ""),
                "url": f"https://huggingface.co/datasets/{d.get('id', '')}",
            }
            for d in (datasets if isinstance(datasets, list) else [])[:limit]
        ]
    except Exception:
        log.warning("HF dataset search failed for: %s", query, exc_info=True)
        return []


def find_paper_models(arxiv_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Find models on HF Hub associated with a paper (by arXiv ID)."""
    if not arxiv_id:
        return []
    try:
        # Search models that mention this arXiv ID in their tags/description
        resp = requests.get(
            f"{_BASE}/models",
            params={"search": arxiv_id, "limit": limit, "sort": "downloads", "direction": -1},
            headers=_auth_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        models = resp.json()
        return [
            {
                "model_id": m.get("modelId", m.get("id", "")),
                "downloads": m.get("downloads", 0),
                "likes": m.get("likes", 0),
                "task": m.get("pipeline_tag", ""),
                "url": f"https://huggingface.co/{m.get('modelId', m.get('id', ''))}",
            }
            for m in (models if isinstance(models, list) else [])[:limit]
        ]
    except Exception:
        log.warning("Failed to find models for paper %s", arxiv_id, exc_info=True)
        return []


def find_paper_datasets(arxiv_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Find datasets on HF Hub associated with a paper (by arXiv ID)."""
    if not arxiv_id:
        return []
    try:
        resp = requests.get(
            f"{_BASE}/datasets",
            params={"search": arxiv_id, "limit": limit, "sort": "downloads", "direction": -1},
            headers=_auth_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        datasets = resp.json()
        return [
            {
                "dataset_id": d.get("id", ""),
                "downloads": d.get("downloads", 0),
                "url": f"https://huggingface.co/datasets/{d.get('id', '')}",
            }
            for d in (datasets if isinstance(datasets, list) else [])[:limit]
        ]
    except Exception:
        log.warning("Failed to find datasets for paper %s", arxiv_id, exc_info=True)
        return []


def find_paper_spaces(arxiv_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Find Spaces on HF Hub associated with a paper (demos, implementations)."""
    if not arxiv_id:
        return []
    try:
        resp = requests.get(
            f"{_BASE}/spaces",
            params={"search": arxiv_id, "limit": limit, "sort": "likes", "direction": -1},
            headers=_auth_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        spaces = resp.json()
        return [
            {
                "space_id": s.get("id", ""),
                "likes": s.get("likes", 0),
                "sdk": s.get("sdk", ""),
                "url": f"https://huggingface.co/spaces/{s.get('id', '')}",
            }
            for s in (spaces if isinstance(spaces, list) else [])[:limit]
        ]
    except Exception:
        log.warning("Failed to find spaces for paper %s", arxiv_id, exc_info=True)
        return []
