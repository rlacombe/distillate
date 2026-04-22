"""Research profile — auto-built model of a user's research interests.

Aggregates topics, authors, venues, and experiment keywords from the
user's paper library and experiments.  Stored as a JSON file in the
config directory and used to power relevance ranking across all
discovery surfaces (Papers Home, Paper Radar, Field Pulse).

The profile is rebuilt lazily: ``get_or_build_profile`` returns a cached
version if it's less than 1 hour old, otherwise rebuilds from scratch.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from distillate.config import CONFIG_DIR

log = logging.getLogger(__name__)

_PROFILE_PATH = CONFIG_DIR / "research_profile.json"
_PROFILE_VERSION = 1
_STALE_SECONDS = 3600  # 1 hour


# ── Stopwords (shared with experiment keyword extraction) ───────────
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "for", "of", "with", "by", "from", "is", "it", "as", "be", "was",
    "are", "were", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "shall", "should", "may", "might", "can",
    "could", "not", "no", "so", "if", "then", "than", "that", "this",
    "these", "those", "which", "what", "who", "how", "when", "where",
    "why", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "only", "same", "very", "just", "also",
    "now", "new", "use", "using", "used", "run", "try", "tried",
    "paper", "model", "method", "results", "show", "based", "proposed",
    "approach", "work", "data", "learning", "training", "performance",
})


def build_research_profile(state) -> dict:
    """Build a research profile from the user's library and experiments.

    Returns a dict ready to be serialized to JSON.
    """
    now = datetime.now(timezone.utc)
    processed = state.documents_with_status("processed")
    promoted_set = set(state.promoted_papers)

    # ── Topics (from paper tags, weighted by engagement + recency) ───
    topic_weights: Counter = Counter()
    topic_counts: Counter = Counter()

    # ── Authors ──────────────────────────────────────────────────────
    author_counts: Counter = Counter()

    # ── Venues ───────────────────────────────────────────────────────
    venue_counts: Counter = Counter()

    # ── Methods (extracted from summaries via keyword matching) ──────
    method_tokens: Counter = Counter()

    for doc in processed:
        meta = doc.get("metadata", {}) or {}
        engagement = doc.get("engagement", 0) or 0
        engagement_mult = 1.0 + (engagement / 100.0)

        # Recency decay
        processed_at = doc.get("processed_at", "")
        recency = 0.4  # default for old papers
        if processed_at:
            try:
                dt = datetime.fromisoformat(processed_at)
                days_ago = (now - dt).days
                if days_ago <= 30:
                    recency = 1.0
                elif days_ago <= 90:
                    recency = 0.7
            except (ValueError, TypeError):
                pass

        # Promoted boost
        key = doc.get("zotero_item_key", "")
        promoted_mult = 1.5 if key in promoted_set else 1.0

        weight = engagement_mult * recency * promoted_mult

        # Tags → topics
        tags = meta.get("tags") or []
        for tag in tags:
            tag_lower = tag.lower().strip()
            if tag_lower and len(tag_lower) > 1:
                topic_weights[tag_lower] += weight
                topic_counts[tag_lower] += 1

        # Authors
        for author in doc.get("authors", []):
            if author and author.lower() != "unknown":
                author_counts[author] += 1

        # Venues
        venue = meta.get("venue") or meta.get("journal") or ""
        if venue and len(venue) > 2:
            venue_counts[venue] += 1

        # Methods — extract from summary + abstract
        text = " ".join([
            doc.get("summary", "") or "",
            meta.get("abstract", "") or "",
        ]).lower()
        tokens = re.findall(r"[a-z][a-z0-9_]{2,}", text)
        for tok in tokens:
            if tok not in _STOPWORDS:
                method_tokens[tok] += weight

    # ── Experiment keywords ──────────────────────────────────────────
    experiment_kws: Counter = Counter()
    try:
        from distillate.experiment_tools._helpers import extract_experiment_keywords
        projects = state.experiments if hasattr(state, "experiments") else {}
        for proj in projects.values():
            kws = extract_experiment_keywords(proj)
            for kw in kws:
                experiment_kws[kw] += 1
    except Exception:
        log.debug("Could not extract experiment keywords", exc_info=True)

    # ── Assemble profile ─────────────────────────────────────────────
    # Normalize topic weights to 0–1 range
    max_weight = max(topic_weights.values()) if topic_weights else 1.0
    topics = [
        {"name": name, "count": topic_counts[name],
         "weight": round(topic_weights[name] / max_weight, 2)}
        for name, _ in topic_weights.most_common(30)
        if topic_counts[name] >= 2  # at least 2 papers
    ]

    authors = [
        {"name": name, "count": count}
        for name, count in author_counts.most_common(15)
        if count >= 2
    ]

    venues = [
        {"name": name, "count": count}
        for name, count in venue_counts.most_common(10)
        if count >= 2
    ]

    # Top method-like keywords (filter topics already captured)
    topic_names = {t["name"] for t in topics}
    methods = [
        tok for tok, _ in method_tokens.most_common(50)
        if tok not in topic_names and len(tok) > 3
    ][:20]

    exp_keywords = [kw for kw, _ in experiment_kws.most_common(20)]

    return {
        "generated_at": now.isoformat(),
        "version": _PROFILE_VERSION,
        "topics": topics,
        "methods": methods,
        "authors": authors,
        "venues": venues,
        "experiment_keywords": exp_keywords,
        "paper_count": len(processed),
        "promoted_count": len(promoted_set),
    }


def save_profile(profile: dict) -> None:
    """Write the profile to disk."""
    try:
        _PROFILE_PATH.write_text(
            json.dumps(profile, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        log.warning("Failed to save research profile", exc_info=True)


def load_profile() -> dict | None:
    """Load the profile from disk.  Returns None if missing or stale."""
    if not _PROFILE_PATH.exists():
        return None
    try:
        profile = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        # Check staleness
        generated = profile.get("generated_at", "")
        if generated:
            dt = datetime.fromisoformat(generated)
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            if age < _STALE_SECONDS:
                return profile
        return None  # stale
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def get_or_build_profile(state) -> dict:
    """Return a fresh profile — cached from disk if recent, else rebuilt."""
    profile = load_profile()
    if profile is not None:
        return profile
    profile = build_research_profile(state)
    save_profile(profile)
    return profile


def profile_topic_set(profile: dict) -> set[str]:
    """Return the set of topic names from a profile (lowercased)."""
    return {t["name"].lower() for t in profile.get("topics", [])}
