"""Papers -- library listing, detail, promote/unpromote, metadata refresh, report."""

import asyncio
import json
import logging
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from distillate.routes import _context

log = logging.getLogger(__name__)

router = APIRouter()


def _pdf_cache_dir() -> Path:
    """Scratch directory for on-demand PDFs pulled from Zotero for the desktop
    reader. Separate from the Obsidian ``Saved/pdf/`` directory (which holds
    annotated PDFs produced by the reMarkable pipeline)."""
    from distillate.config import CONFIG_DIR
    d = CONFIG_DIR / "pdf_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Cache the auto-discovered Obsidian vault path for the process lifetime.
# Three states:
#   None      — not yet probed
#   False     — probed, nothing found
#   Path(...) — discovered vault root
_OBSIDIAN_AUTO_VAULT: "Path | bool | None" = None


def _discover_obsidian_vault() -> "Path | None":
    """Auto-discover an Obsidian vault when ``OBSIDIAN_VAULT_PATH`` isn't
    set. Probes common parent directories for a child that looks like an
    Obsidian vault *and* contains the configured papers folder.

    A directory qualifies as a vault when it contains both:
      - a ``.obsidian/`` subdirectory (Obsidian's config marker)
      - a ``{OBSIDIAN_PAPERS_FOLDER}/`` subdirectory (Distillate's papers)

    Returns the vault root Path, or None if no match is found. The result
    is cached in-process so repeated lookups are cheap.
    """
    global _OBSIDIAN_AUTO_VAULT
    if _OBSIDIAN_AUTO_VAULT is not None:
        return _OBSIDIAN_AUTO_VAULT if isinstance(_OBSIDIAN_AUTO_VAULT, Path) else None

    _OBSIDIAN_AUTO_VAULT = False  # sentinel: probed, nothing found

    from distillate import config as _config
    papers_folder = (_config.OBSIDIAN_PAPERS_FOLDER or "Distillate").strip()
    if not papers_folder:
        return None

    home = Path.home()
    # Common Obsidian vault parent directories. The first one that contains
    # a match wins.
    parents = [
        home / "Obsidian",
        home / "Documents" / "Obsidian",
        home / "iCloud Drive" / "Obsidian",
        home / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents",
        home / "Documents",
    ]

    for parent in parents:
        if not parent.is_dir():
            continue
        try:
            for child in parent.iterdir():
                if not child.is_dir():
                    continue
                if not (child / ".obsidian").is_dir():
                    continue
                if not (child / papers_folder).is_dir():
                    continue
                log.info("Auto-discovered Obsidian vault: %s", child)
                _OBSIDIAN_AUTO_VAULT = child
                # Push the discovered value into config so every other
                # helper that reads OBSIDIAN_VAULT_PATH (including
                # ``obsidian._papers_dir``) sees it immediately.
                _config.OBSIDIAN_VAULT_PATH = str(child)
                if not _config.OBSIDIAN_VAULT_NAME:
                    _config.OBSIDIAN_VAULT_NAME = child.name
                # Persist to .env so the next server start already has it —
                # saves the user from re-discovering after every relaunch.
                try:
                    _config.save_to_env("OBSIDIAN_VAULT_PATH", str(child))
                    log.info("Persisted OBSIDIAN_VAULT_PATH to .env")
                except Exception as exc:
                    log.debug("Failed to persist vault path: %s", exc)
                return child
        except OSError:
            continue

    return None


def _pdf_search_result(doc: dict) -> "dict":
    """Resolve a PDF for this document and return full diagnostic info.

    Returns a dict with:
        - ``path``: the resolved local Path if one was found, else None
        - ``candidates``: ordered list of exact paths we probed
        - ``vault_path``: the configured Obsidian vault path (or "")
        - ``papers_dir``: str path of ``obsidian._papers_dir()`` (or "")
        - ``stems``: the filename stems we searched for

    Resolution strategy:

    1. Probe the exact filenames the pipeline writes — ``citekey`` and
       ``_sanitize_note_name(title)`` — in ``Saved/pdf/``, ``Inbox/``, the
       papers folder itself, and our own ``pdf_cache`` scratch dir.
    2. As a fallback, recursively glob ``{papers_dir}/**/{stem}.pdf`` so
       we find files regardless of the exact subfolder layout (some
       users have PDFs directly under ``{vault}/Distillate/``, others
       have them under ``Saved/pdf/``, etc.).

    The recursive glob is scoped to the papers folder, not the whole vault,
    so it stays fast on large vaults.
    """
    meta = doc.get("metadata", {}) or {}
    citekey = (meta.get("citekey") or "").strip()
    title = (doc.get("title") or "").strip()
    att_key = (doc.get("zotero_attachment_key") or "").strip()

    try:
        from distillate import obsidian
    except Exception:
        obsidian = None

    # Build the list of filename stems to try (citekey preferred).
    stems: list[str] = []
    if citekey:
        stems.append(citekey)
    if title and obsidian is not None:
        try:
            sanitized = obsidian._sanitize_note_name(title)
            if sanitized and sanitized not in stems:
                stems.append(sanitized)
        except Exception:
            pass

    candidates: list[Path] = []
    papers_dir_path: "Path | None" = None
    vault_path = ""

    # Highest-priority probe: path stamped directly on the document record.
    local_pdf_path = (doc.get("local_pdf_path") or "").strip()
    if local_pdf_path:
        candidates.append(Path(local_pdf_path))

    if obsidian is not None:
        try:
            from distillate import config as _config
            vault_path = _config.OBSIDIAN_VAULT_PATH or ""
            # If config has no vault path, probe common Obsidian parents.
            # _discover_obsidian_vault() sets config.OBSIDIAN_VAULT_PATH
            # in-memory on success, so subsequent obsidian.* calls work.
            if not vault_path:
                discovered = _discover_obsidian_vault()
                if discovered:
                    vault_path = str(discovered)
        except Exception:
            vault_path = ""

        try:
            papers_dir_path = obsidian._papers_dir()
        except Exception:
            papers_dir_path = None

        # Exact-path probes in the known subdirs.
        dirs_to_probe: list["Path"] = []
        try:
            saved_pdf = obsidian._pdf_dir()
            if saved_pdf:
                dirs_to_probe.append(saved_pdf)
        except Exception:
            pass
        try:
            inbox = obsidian._inbox_dir()
            if inbox:
                dirs_to_probe.append(inbox)
        except Exception:
            pass
        # Also probe the papers folder itself — some layouts stash PDFs
        # directly under ``{vault}/Distillate/`` with no subfolder.
        if papers_dir_path:
            dirs_to_probe.append(papers_dir_path)

        for d in dirs_to_probe:
            for stem in stems:
                candidates.append(d / f"{stem}.pdf")

    # Scratch cache keyed by Zotero attachment id.
    if att_key:
        candidates.append(_pdf_cache_dir() / f"{att_key}.pdf")

    # Pass 1: any exact candidate that exists wins.
    resolved: "Path | None" = None
    for c in candidates:
        if c.exists():
            resolved = c
            break

    # Pass 2: recursive glob under papers_dir for each stem. This catches
    # vaults where the layout differs from our defaults (custom
    # PDF_SUBFOLDER, migrated files, manual moves).
    if resolved is None and papers_dir_path and papers_dir_path.exists():
        for stem in stems:
            try:
                for hit in papers_dir_path.rglob(f"{stem}.pdf"):
                    if hit not in candidates:
                        candidates.append(hit)
                    if resolved is None:
                        resolved = hit
                        break
                if resolved is not None:
                    break
            except OSError:
                continue

    return {
        "path": resolved,
        "candidates": candidates,
        "vault_path": vault_path,
        "papers_dir": str(papers_dir_path) if papers_dir_path else "",
        "stems": stems,
    }


def _resolve_local_pdf(doc: dict) -> "Path | None":
    """Return the first local PDF that matches this document, or None.
    Convenience wrapper around ``_pdf_search_result`` for callers that
    don't need the diagnostic fields."""
    return _pdf_search_result(doc)["path"]


@router.get("/papers")
async def list_papers(status: str = None):
    _context._cached_reload()
    _state = _context._state
    docs = _state.documents
    promoted_set = set(_state.promoted_papers)
    results = []
    for key, doc in docs.items():
        if status and doc.get("status") != status:
            continue
        meta = doc.get("metadata", {})
        idx = _state.index_of(key)
        summary_text = doc.get("summary", "") or ""
        results.append({
            "index": idx,
            "key": key,
            "title": doc.get("title", ""),
            "citekey": meta.get("citekey", ""),
            "status": doc.get("status", ""),
            "authors": doc.get("authors", [])[:3],
            "summary": summary_text[:200] + ("..." if len(summary_text) > 200 else ""),
            "engagement": doc.get("engagement", 0),
            "promoted": key in promoted_set,
            "promoted_at": doc.get("promoted_at", ""),
            "tags": meta.get("tags", [])[:5],
            "citation_count": meta.get("citation_count", 0),
            "publication_date": meta.get("publication_date", ""),
            "zotero_date_added": doc.get("zotero_date_added", "") or meta.get("zotero_date_added", ""),
            "uploaded_at": doc.get("uploaded_at", ""),
            "processed_at": doc.get("processed_at", ""),
            "page_count": meta.get("numPages") or meta.get("page_count", 0),
        })
    return JSONResponse({"ok": True, "papers": results, "total": len(results)})


@router.post("/papers/sync")
async def sync_papers():
    """Trigger the Zotero sync pipeline directly (no Nicolas required).

    Calls ``pipeline.run_sync()`` in a thread — credentials are already
    loaded in this process so no subprocess or PATH guessing is needed.
    """
    from distillate import config as _config, secrets as _sec

    # Re-hydrate config from secrets if credentials were lost across a restart.
    # secrets.set() silently catches DB write failures, so credentials may have
    # survived only in os.environ — ensure_loaded() won't re-run (guarded by
    # _loaded flag), so patch config directly if the values slipped through.
    if not _config.ZOTERO_USER_ID:
        _config.ZOTERO_USER_ID = _sec.get("ZOTERO_USER_ID")
    if not _config.ZOTERO_API_KEY:
        _config.ZOTERO_API_KEY = _sec.get("ZOTERO_API_KEY")

    if not (_config.ZOTERO_API_KEY and _config.ZOTERO_USER_ID):
        return JSONResponse(
            {"ok": False, "error": "Zotero credentials not configured — add them in Integrations"},
            status_code=400,
        )

    import io
    import sys as _sys
    loop = asyncio.get_event_loop()

    def _do_sync():
        from distillate.pipeline import run_sync
        buf = io.StringIO()
        old_stdout = _sys.stdout
        _sys.stdout = buf
        error: str | None = None
        try:
            run_sync()
        except Exception as exc:
            error = str(exc)
            log.exception("Sync error")
        finally:
            _sys.stdout = old_stdout
        output = buf.getvalue().strip()
        return {"success": error is None, "error": error, "output": output}

    result = await loop.run_in_executor(_context._executor, _do_sync)
    _context._state.reload()

    out = result.get("output", "")
    if "403 Forbidden" in out or "ZOTERO_API_KEY is not set" in out:
        result["ok"] = False
        if "403 Forbidden" in out:
            result["error"] = "Zotero API key rejected (403) — check your credentials in Integrations"
        else:
            result["error"] = "Zotero credentials not configured — add them in Integrations"
    elif "rate limit" in out.lower():
        result["ok"] = False
        result["error"] = "Zotero rate limit — wait a few minutes and try again"
    elif "Could not connect" in out:
        result["ok"] = False
        result["error"] = "Could not reach Zotero — check your internet connection"
    else:
        result["ok"] = result["success"]
    return JSONResponse(result)


@router.post("/papers/{paper_key}/promote")
async def promote_paper(paper_key: str):
    """Add a paper to the promoted list."""
    _state = _context._state
    _state.reload()
    doc = _state.documents.get(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
    promoted = _state.promoted_papers
    if paper_key not in promoted:
        promoted.append(paper_key)
        doc["promoted_at"] = datetime.now(timezone.utc).isoformat()
        _state.save()
    return JSONResponse({"ok": True, "promoted": True})


@router.post("/papers/{paper_key}/unpromote")
async def unpromote_paper(paper_key: str):
    """Remove a paper from the promoted list."""
    _state = _context._state
    _state.reload()
    doc = _state.documents.get(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
    promoted = _state.promoted_papers
    if paper_key in promoted:
        promoted.remove(paper_key)
        doc.pop("promoted_at", None)
        _state.save()
    return JSONResponse({"ok": True, "promoted": False})


@router.post("/papers/{paper_key}/refresh-metadata")
async def refresh_paper_metadata(paper_key: str):
    """Re-fetch metadata from Zotero + Semantic Scholar for a single paper."""
    _state = _context._state
    _state.reload()
    doc = _state.documents.get(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
    from distillate.tools import refresh_metadata
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        _context._executor, lambda: refresh_metadata(state=_state, identifier=paper_key)
    )
    return JSONResponse({"ok": True, "result": result})


@router.get("/papers/home")
async def papers_home():
    """Papers home page — recently read, queue snapshot, compact insights,
    and trending papers from HuggingFace.  Single endpoint so the desktop
    renderer can populate the center pane in one fetch."""
    _context._cached_reload()
    _state = _context._state
    docs = _state.documents
    promoted_set = set(_state.promoted_papers)
    now = datetime.now(timezone.utc)

    # ── Recently read (last 8 processed, newest first) ──────────────────
    processed = []
    for key, doc in docs.items():
        if doc.get("status") != "processed":
            continue
        meta = doc.get("metadata", {}) or {}
        processed.append({
            "key": key,
            "title": doc.get("title", ""),
            "authors": doc.get("authors", [])[:2],
            "engagement": doc.get("engagement", 0),
            "citation_count": meta.get("citation_count", 0),
            "publication_date": meta.get("publication_date", ""),
            "venue": meta.get("venue", ""),
            "arxiv_id": meta.get("arxiv_id", "") or doc.get("arxiv_id", ""),
            "processed_at": doc.get("processed_at", ""),
            "tags": (meta.get("tags") or [])[:3],
            "promoted": key in promoted_set,
        })
    processed.sort(key=lambda p: p["processed_at"], reverse=True)
    recently_read = processed[:8]

    # ── Queue snapshot (next-up unread, promoted first) ─────────────────
    unread = []
    for key, doc in docs.items():
        if doc.get("status") == "processed":
            continue
        meta = doc.get("metadata", {}) or {}
        unread.append({
            "key": key,
            "title": doc.get("title", ""),
            "authors": doc.get("authors", [])[:2],
            "citation_count": meta.get("citation_count", 0),
            "publication_date": meta.get("publication_date", ""),
            "venue": meta.get("venue", ""),
            "arxiv_id": meta.get("arxiv_id", "") or doc.get("arxiv_id", ""),
            "uploaded_at": doc.get("uploaded_at", ""),
            "tags": (meta.get("tags") or [])[:3],
            "promoted": key in promoted_set,
        })
    # Promoted first, then most recent upload first
    def _queue_sort_key(p):
        rank = 0 if p["promoted"] else 1
        ts = p.get("uploaded_at", "")
        try:
            t = datetime.fromisoformat(ts).timestamp() if ts else 0
        except (ValueError, TypeError):
            t = 0
        return (rank, -t)
    unread.sort(key=_queue_sort_key)
    queue = unread[:6]

    # ── Compact insights ────────────────────────────────────────────────
    total_read = len(processed)
    total_unread = sum(1 for d in docs.values() if d.get("status") != "processed")
    total_promoted = len(promoted_set)
    engagements = [p["engagement"] for p in processed if p["engagement"]]
    avg_engagement = round(sum(engagements) / len(engagements)) if engagements else 0

    # Reading velocity — papers per week, last 4 weeks
    week_counts: Counter = Counter()
    for p in processed:
        ts = p.get("processed_at", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            weeks_ago = (now - dt).days // 7
            if weeks_ago < 4:
                week_counts[weeks_ago] += 1
        except (ValueError, TypeError):
            pass
    velocity_4w = [week_counts.get(i, 0) for i in range(4)]  # [this week, last week, ...]

    # Top topics from the research profile (weighted by engagement + recency)
    try:
        from distillate.research_profile import get_or_build_profile, profile_topic_set
        profile = get_or_build_profile(_state)
        top_topics = [{"topic": t["name"], "count": t["count"]} for t in profile.get("topics", [])[:5]]
        library_topic_set = profile_topic_set(profile)
    except Exception:
        log.debug("Could not build research profile, falling back to inline", exc_info=True)
        topic_counter: Counter = Counter()
        for key, doc in docs.items():
            if doc.get("status") != "processed":
                continue
            tags = (doc.get("metadata", {}) or {}).get("tags") or []
            for tag in tags:
                topic_counter[tag] += 1
        top_topics = [{"topic": t, "count": c} for t, c in topic_counter.most_common(5)]
        library_topic_set = {tag.lower() for tag, count in topic_counter.items() if count >= 2}

    insights = {
        "total_read": total_read,
        "total_unread": total_unread,
        "total_promoted": total_promoted,
        "avg_engagement": avg_engagement,
        "velocity_4w": velocity_4w,
        "top_topics": top_topics,
    }

    # ── Why-is-this-next reason chips for the queue ─────────────────────
    # Rules in priority order: Promoted > New this week > Matches <topic>.
    top_topic_names = {t["topic"].lower() for t in top_topics}
    for item in queue:
        if item.get("promoted"):
            item["reason"] = "Promoted"
            continue
        ts = item.get("uploaded_at", "")
        is_new = False
        try:
            if ts:
                dt = datetime.fromisoformat(ts)
                if (now - dt).days < 7:
                    is_new = True
        except (ValueError, TypeError):
            pass
        if is_new:
            item["reason"] = "New this week"
            continue
        matched = next(
            (t for t in (item.get("tags") or []) if t and t.lower() in top_topic_names),
            None,
        )
        if matched:
            item["reason"] = f"Matches {matched}"

    # ── Trending papers (HuggingFace) ───────────────────────────────────
    # Run in executor to avoid blocking the event loop on the HTTP call.
    trending = []
    try:
        from distillate.huggingface import trending_papers
        loop = asyncio.get_event_loop()
        trending = await loop.run_in_executor(
            _context._executor, lambda: trending_papers(limit=8)
        )
    except Exception:
        log.debug("Failed to fetch trending papers for home", exc_info=True)

    # Mark papers already in library
    library_arxiv_ids = set()
    for doc in docs.values():
        aid = (doc.get("metadata", {}) or {}).get("arxiv_id", "")
        if aid:
            library_arxiv_ids.add(aid)
        aid2 = doc.get("arxiv_id", "")
        if aid2:
            library_arxiv_ids.add(aid2)
    for tp in trending:
        tp["in_library"] = tp.get("arxiv_id", "") in library_arxiv_ids

    # Generate relevance hints from ai_keywords overlap with profile topics
    for tp in trending:
        ai_kws = [kw.lower() for kw in tp.get("ai_keywords", [])]
        overlaps = [kw for kw in ai_kws if kw in library_topic_set]
        if overlaps:
            tp["relevance_hint"] = f"Related to your interest in {', '.join(overlaps[:3])}"

    return JSONResponse({
        "ok": True,
        "recently_read": recently_read,
        "queue": queue,
        "insights": insights,
        "trending": trending,
    })


@router.get("/papers/{paper_key}/radar")
async def paper_radar(paper_key: str):
    """Literature radar for a paper — related papers from the library and
    trending sources, with relevance annotations."""
    import re as _re
    from distillate.experiment_tools._helpers import EXPERIMENT_STOPWORDS

    _context._cached_reload()
    _state = _context._state
    doc = _state.get_document(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

    # ── Extract keywords from this paper ────────────────────────────
    meta = doc.get("metadata", {}) or {}
    paper_text = " ".join([
        doc.get("title", ""),
        " ".join(meta.get("tags") or []),
        doc.get("summary", "") or "",
        meta.get("abstract", "") or "",
    ]).lower()
    tokens = _re.findall(r"[a-z][a-z0-9_]{2,}", paper_text)
    keywords = list(dict.fromkeys(t for t in tokens if t not in EXPERIMENT_STOPWORDS))[:30]

    if not keywords:
        return JSONResponse({"ok": True, "library_matches": [], "trending_matches": [], "keywords": []})

    # ── Load research profile for enhanced annotations ──────────────
    profile_topics = set()
    try:
        from distillate.research_profile import get_or_build_profile, profile_topic_set
        profile = get_or_build_profile(_state)
        profile_topics = profile_topic_set(profile)
    except Exception:
        log.debug("Paper radar: could not load research profile", exc_info=True)

    # ── Library matches (processed papers, last 90 days) ────────────
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=90)).isoformat()
    library_matches = []

    for key, d in _state.documents.items():
        if key == paper_key:
            continue
        if d.get("status") != "processed":
            continue
        proc_at = d.get("processed_at", "")
        if proc_at and proc_at < cutoff:
            continue

        d_meta = d.get("metadata", {}) or {}
        d_text = " ".join([
            d.get("title", ""),
            " ".join(d_meta.get("tags") or []),
            d.get("summary", "") or "",
            d_meta.get("abstract", "") or "",
        ]).lower()

        matched = [kw for kw in keywords if kw in d_text]
        if len(matched) >= 2:
            kw_str = ", ".join(matched[:3])
            # Enhance with profile context
            d_tags = {t.lower() for t in d_meta.get("tags") or []}
            profile_overlap = d_tags & profile_topics
            if profile_overlap:
                relevance = f"Shares {kw_str} \u2014 aligns with your focus on {', '.join(list(profile_overlap)[:2])}"
            else:
                relevance = f"Shares {kw_str}"
            library_matches.append({
                "key": key,
                "title": d.get("title", ""),
                "authors": d.get("authors", [])[:2],
                "citation_count": d_meta.get("citation_count", 0),
                "matched_keywords": matched[:5],
                "match_count": len(matched),
                "relevance": relevance,
            })

    library_matches.sort(key=lambda m: m["match_count"], reverse=True)
    library_matches = library_matches[:5]

    # ── Trending matches ────────────────────────────────────────────
    trending_matches = []
    try:
        from distillate.huggingface import trending_papers
        loop = asyncio.get_event_loop()
        trending = await loop.run_in_executor(
            _context._executor, lambda: trending_papers(limit=8)
        )
        kw_set = set(keywords)
        for tp in trending:
            ai_kws = {kw.lower() for kw in tp.get("ai_keywords", [])}
            title_words = set(tp.get("title", "").lower().split())
            overlaps = list(kw_set & (ai_kws | title_words))
            if len(overlaps) >= 2:
                kw_str = ", ".join(overlaps[:3])
                profile_overlap = ai_kws & profile_topics
                if profile_overlap:
                    relevance = f"Discusses {kw_str} \u2014 aligns with your focus on {', '.join(list(profile_overlap)[:2])}"
                else:
                    relevance = f"Discusses {kw_str}"
                trending_matches.append({
                    "title": tp.get("title", ""),
                    "authors": tp.get("authors", [])[:2],
                    "upvotes": tp.get("upvotes", 0),
                    "github_stars": tp.get("github_stars"),
                    "hf_url": tp.get("hf_url", ""),
                    "pdf_url": tp.get("pdf_url", ""),
                    "matched_keywords": overlaps[:5],
                    "match_count": len(overlaps),
                    "relevance": relevance,
                })
        trending_matches.sort(key=lambda m: m["match_count"], reverse=True)
        trending_matches = trending_matches[:3]
    except Exception:
        log.debug("Paper radar: failed to fetch trending", exc_info=True)

    return JSONResponse({
        "ok": True,
        "library_matches": library_matches,
        "trending_matches": trending_matches,
        "keywords": keywords[:10],
    })


@router.get("/papers/{paper_key}")
async def paper_detail(paper_key: str):
    _state = _context._state
    _state.reload()
    doc = _state.get_document(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
    meta = doc.get("metadata", {})
    idx = _state.index_of(paper_key)

    # Read highlights from Obsidian note (post-processing) if available.
    highlights = ""
    try:
        from distillate.tools import _read_note_content
        note = _read_note_content(meta.get("citekey", ""), doc.get("title", ""))
        if note:
            start = note.find("## Highlights")
            if start >= 0:
                end = note.find("\n## ", start + 1)
                highlights = note[start:end] if end > 0 else note[start:]
    except Exception:
        pass

    # Include PDF-native highlights (from in-app reader + any third-party
    # annotations) as markdown bullets. These are the source of truth for
    # user-created highlights — they're written directly into the PDF via
    # PyMuPDF and don't depend on Zotero sync.
    search = _pdf_search_result(doc)
    local_path = search.get("path")
    if local_path:
        try:
            from distillate import highlight_io
            pdf_anns = highlight_io.read_highlights(local_path)
        except Exception:
            pdf_anns = []
    else:
        pdf_anns = []

    # Also include legacy local_highlights (pre-highlight_io era).
    local_hl = doc.get("local_highlights") or []
    all_reader_hl = pdf_anns + [
        {"text": h.get("text", ""), "page_label": h.get("page_label", ""),
         "page_index": h.get("page_index", 0)}
        for h in local_hl
    ]

    if all_reader_hl:
        existing_lines = {
            " ".join(line.split())
            for line in highlights.splitlines()
            if line.strip()
        }
        by_page: dict = {}
        seen_texts: set = set()
        for h in all_reader_hl:
            text = (h.get("text") or "").strip()
            if not text:
                continue
            # Normalize all whitespace (including newlines) to single spaces
            norm = " ".join(text.split())
            if norm in seen_texts:
                continue
            seen_texts.add(norm)
            if any(norm in line for line in existing_lines):
                continue
            page = h.get("page_label") or str(h.get("page_index", 0) + 1)
            by_page.setdefault(page, []).append(norm)

        if by_page:
            # Sort pages numerically if possible, else lexicographically
            def _page_sort_key(p):
                try:
                    return (0, int(p))
                except (ValueError, TypeError):
                    return (1, p)

            new_sections = []
            for page in sorted(by_page.keys(), key=_page_sort_key):
                bullets = "\n".join(f"- \"{t}\"" for t in by_page[page])
                new_sections.append(f"### Page {page}\n\n{bullets}")
            new_hl = "\n\n".join(new_sections)

            prefix = "## Highlights\n\n" if not highlights else ""
            highlights = (
                highlights.rstrip() + "\n\n" + new_hl
                if highlights else
                prefix + new_hl
            )

    # Resolve linked projects to names
    linked_projects = []
    for pid in doc.get("linked_projects", []):
        p = _state.find_experiment(pid)
        if p:
            linked_projects.append({
                "id": p.get("id", pid),
                "name": p.get("name", pid),
            })
        else:
            linked_projects.append({"id": pid, "name": pid})

    return JSONResponse({"ok": True, "paper": {
        "index": idx,
        "key": paper_key,
        "title": doc.get("title", ""),
        "citekey": meta.get("citekey", ""),
        "status": doc.get("status", ""),
        "authors": doc.get("authors", []),
        "summary": doc.get("summary", "") or "",
        "s2_tldr": meta.get("s2_tldr", ""),
        "engagement": doc.get("engagement", 0),
        "tags": meta.get("tags", []),
        "citation_count": meta.get("citation_count", 0),
        "publication_date": meta.get("publication_date", ""),
        "venue": meta.get("venue", ""),
        "doi": meta.get("doi", ""),
        "arxiv_id": meta.get("arxiv_id", ""),
        "url": meta.get("url", ""),
        "uploaded_at": doc.get("uploaded_at", ""),
        "processed_at": doc.get("processed_at", ""),
        "promoted_at": doc.get("promoted_at", ""),
        "highlights": highlights,
        "linked_projects": linked_projects,
        "last_read_page": doc.get("last_read_page") or 0,
        "last_read_at": doc.get("last_read_at") or "",
    }})


@router.get("/report")
async def report():
    """Reading insights dashboard data."""
    _state = _context._state
    _state.reload()
    processed = _state.documents_with_status("processed")

    if not processed:
        return JSONResponse({"ok": True, "empty": True})

    # Lifetime stats
    total_papers = len(processed)
    total_pages = sum(
        d.get("page_count", 0)
        or d.get("metadata", {}).get("numPages", 0)
        or 0
        for d in processed
    )
    total_words = sum(d.get("highlight_word_count", 0) for d in processed)
    engagements = [d.get("engagement", 0) for d in processed if d.get("engagement")]
    avg_engagement = round(sum(engagements) / len(engagements)) if engagements else 0

    # Reading velocity (last 8 weeks)
    velocity = []
    week_counts: Counter = Counter()
    now = datetime.now(timezone.utc)
    for doc in processed:
        ts = doc.get("processed_at", "")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            weeks_ago = (now - dt).days // 7
            if weeks_ago < 8:
                monday = dt - timedelta(days=dt.weekday())
                label = monday.strftime("%Y-%m-%d")
                week_counts[label] += 1
        except (ValueError, TypeError):
            pass
    for label in sorted(week_counts.keys()):
        velocity.append({"week": label, "count": week_counts[label]})

    # Top topics
    topic_counter: Counter = Counter()
    for doc in processed:
        tags = doc.get("metadata", {}).get("tags") or []
        for tag in tags:
            topic_counter[tag] += 1
    topics = [{"topic": t, "count": c} for t, c in topic_counter.most_common(8)]

    # Engagement distribution
    buckets = {"0-25%": 0, "25-50%": 0, "50-75%": 0, "75-100%": 0}
    for doc in processed:
        eng = doc.get("engagement", 0)
        if eng <= 25:
            buckets["0-25%"] += 1
        elif eng <= 50:
            buckets["25-50%"] += 1
        elif eng <= 75:
            buckets["50-75%"] += 1
        else:
            buckets["75-100%"] += 1
    engagement_dist = [{"range": k, "count": v} for k, v in buckets.items()]

    # Most-cited papers
    cited = sorted(
        [d for d in processed if d.get("metadata", {}).get("citation_count", 0) > 0],
        key=lambda d: d.get("metadata", {}).get("citation_count", 0),
        reverse=True,
    )
    cited_papers = []
    for doc in cited[:5]:
        key = doc.get("zotero_item_key", "")
        cited_papers.append({
            "title": doc.get("title", "")[:80],
            "citations": doc["metadata"]["citation_count"],
            "index": _state.index_of(key) if key else 0,
        })

    # Most-read authors
    author_counter: Counter = Counter()
    for doc in processed:
        for author in doc.get("authors", []):
            if author and author.lower() != "unknown":
                author_counter[author] += 1
    top_authors = [
        {"name": a, "count": c}
        for a, c in author_counter.most_common(5)
        if c >= 2
    ]

    return JSONResponse({
        "ok": True,
        "lifetime": {
            "papers": total_papers,
            "pages": total_pages,
            "words": total_words,
            "avg_engagement": avg_engagement,
        },
        "velocity": velocity,
        "topics": topics,
        "engagement": engagement_dist,
        "cited_papers": cited_papers,
        "top_authors": top_authors,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Desktop reader — PDF bytes, Zotero annotations, last-read position.
#
# These endpoints power the in-app reading experience. The reader is a
# parallel surface to reMarkable: Zotero stays the source of truth for
# highlights and read state.
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/papers/{paper_key}/pdf")
async def paper_pdf(paper_key: str):
    """Stream the PDF bytes for a paper to the desktop reader.

    Resolution order:
        1. Local cache (Obsidian annotated PDF or pdf_cache/{att_key}.pdf)
        2. Zotero cloud / WebDAV / direct URL via ``_fetch_pdf_bytes``
    When downloaded from Zotero, the bytes are cached under
    ``CONFIG_DIR/pdf_cache`` keyed by attachment key so subsequent opens are
    instant.
    """
    _state = _context._state
    _state.reload()
    doc = _state.get_document(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

    # 1. Local cache — exact-path probes + recursive fallback glob.
    search = _pdf_search_result(doc)
    local = search["path"]
    if local:
        log.info("PDF cache hit for %s: %s", paper_key, local)
        try:
            data = local.read_bytes()
        except OSError as exc:
            log.warning("Failed reading cached PDF %s: %s", local, exc)
            data = None
        if data:
            return Response(content=data, media_type="application/pdf")

    # Nothing local — log the miss path for diagnosis.
    log.info(
        "PDF cache miss for %s; tried %d candidate(s) (vault=%s, papers_dir=%s)",
        paper_key,
        len(search["candidates"]),
        search["vault_path"] or "<unset>",
        search["papers_dir"] or "<unset>",
    )
    for c in search["candidates"]:
        log.debug("  candidate: %s (exists=%s)", c, c.exists())
    searched = [str(c) for c in search["candidates"]]
    diag = {
        "vault_path": search["vault_path"],
        "papers_dir": search["papers_dir"],
        "stems": search["stems"],
    }

    # 2. Remote fetch via Zotero client (cloud → WebDAV → URL). This path
    # should rarely fire once Obsidian has the vault populated — the local
    # Saved/pdf and Inbox checks above are the primary hit path.
    from distillate.pipeline import _fetch_pdf_bytes
    att_key = (doc.get("zotero_attachment_key") or "").strip()
    item_key = (doc.get("zotero_item_key") or paper_key).strip()
    meta = doc.get("metadata", {}) or {}
    paper_url = meta.get("url") or ""
    title = doc.get("title", "")

    # Missing Zotero credentials make the remote fetch meaningless AND tend
    # to produce scary-looking API URLs in error messages. Short-circuit here
    # so the renderer gets a clean reason it can display.
    from distillate import config as _config
    if not (_config.ZOTERO_API_KEY and _config.ZOTERO_USER_ID):
        return JSONResponse(
            {
                "ok": False,
                "reason": "no_local_pdf_and_zotero_unconfigured",
                "searched": searched,
                "diag": diag,
            },
            status_code=404,
        )

    loop = asyncio.get_event_loop()
    try:
        pdf_bytes, new_att_key = await loop.run_in_executor(
            _context._executor,
            lambda: _fetch_pdf_bytes(
                att_key,
                item_key=item_key,
                paper_url=paper_url,
                title=title,
                check_fresh_attachment=True,
            ),
        )
    except Exception as exc:
        # Log the full error but never forward Zotero API URLs / raw
        # exception text to the client — callers only need a reason code.
        log.warning("PDF fetch failed for %s: %s", paper_key, exc)
        return JSONResponse(
            {"ok": False, "reason": "fetch_failed", "searched": searched, "diag": diag},
            status_code=502,
        )

    if not pdf_bytes:
        return JSONResponse(
            {"ok": False, "reason": "no_pdf_available", "searched": searched, "diag": diag},
            status_code=404,
        )

    # Cache for next open. Use whichever attachment key we ended up with.
    cache_key = new_att_key or att_key
    if cache_key:
        try:
            (_pdf_cache_dir() / f"{cache_key}.pdf").write_bytes(pdf_bytes)
        except OSError as exc:
            log.debug("Could not cache PDF for %s: %s", paper_key, exc)

    # If the attachment key changed (fresh attachment), persist it.
    if new_att_key and new_att_key != att_key:
        doc["zotero_attachment_key"] = new_att_key
        _state.save()

    return Response(content=pdf_bytes, media_type="application/pdf")


def _migrate_local_highlights_if_any(_state, doc, paper_key: str) -> int:
    """One-time migration: if this document has a legacy ``local_highlights``
    list from the old DOM-overlay implementation, write each entry into the
    PDF as a native ``/Highlight`` annotation, then clear the list. Also
    runs a one-shot cleanup pass to remove any stacked duplicates in the
    PDF (artifacts of the pre-idempotency bug)."""
    from distillate import highlight_io

    search = _pdf_search_result(doc)
    local_path = search.get("path")
    if not local_path:
        return 0

    # Clean up any stacked duplicates that may already be in the PDF.
    # Runs every time a paper is opened — no-op once clean.
    try:
        highlight_io.dedupe_pdf_highlights(local_path)
    except Exception as exc:
        log.debug("PDF dedup pass failed for %s: %s", paper_key, exc)

    local_hl = doc.get("local_highlights") or []
    if not local_hl:
        return 0
    migrated = highlight_io.migrate_local_highlights(local_path, local_hl)
    if migrated > 0:
        doc["local_highlights"] = []
        _state.save()
        log.info("Migrated %d legacy local_highlight(s) into %s", migrated, local_path)
    return migrated


@router.get("/papers/{paper_key}/annotations")
async def paper_annotations(paper_key: str):
    """Return all highlights for this paper — PDF-native + Zotero merged.

    Highlights come from three sources, in priority order:

    1. **PDF file** — native ``/Highlight`` annotations read via PyMuPDF.
       This is the source of truth for Distillate-created highlights and
       also captures highlights made in Preview / Acrobat / Zotero desktop.
    2. **Zotero cloud** — fetched when credentials are available, merged on
       top of the PDF set. Pipeline-tagged annotations are excluded.
    3. **Legacy local_highlights** — migrated into the PDF on first open,
       then cleared.

    Duplicates are suppressed by normalized ``(text, page_index)``.
    """
    _state = _context._state
    _state.reload()
    doc = _state.get_document(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

    # Migrate legacy local highlights if present (one-shot).
    _migrate_local_highlights_if_any(_state, doc, paper_key)

    annotations: list = []

    # ── 1. PDF-native highlights (the new source of truth) ──
    search = _pdf_search_result(doc)
    local_path = search.get("path")
    if local_path:
        from distillate import highlight_io
        loop = asyncio.get_event_loop()
        try:
            pdf_anns = await loop.run_in_executor(
                _context._executor,
                lambda: highlight_io.read_highlights(local_path),
            )
            annotations.extend(pdf_anns)
        except Exception as exc:
            log.debug("PDF annotation read failed for %s: %s", paper_key, exc)

    # ── 2. Zotero highlights (best-effort; merged on top) ──
    att_key = (doc.get("zotero_attachment_key") or "").strip()
    from distillate import config as _config
    if att_key and _config.ZOTERO_API_KEY and _config.ZOTERO_USER_ID:
        from distillate import zotero_client
        loop = asyncio.get_event_loop()
        try:
            zotero_anns = await loop.run_in_executor(
                _context._executor,
                lambda: zotero_client.get_raw_annotations(att_key),
            )
            def _norm(a):
                return (
                    " ".join((a.get("text") or "").split()),
                    a.get("page_index", 0),
                )
            existing = {_norm(a) for a in annotations}
            for a in zotero_anns:
                if _norm(a) not in existing:
                    annotations.append(a)
                    existing.add(_norm(a))
        except Exception as exc:
            log.debug("Zotero annotation fetch failed for %s: %s", paper_key, exc)

    return JSONResponse({"ok": True, "annotations": annotations})


@router.post("/papers/{paper_key}/annotations")
async def create_paper_annotation(paper_key: str, request: Request):
    """Save a highlight to the local PDF (primary) and Zotero (side-effect).

    **PDF-first**: the highlight is written as a native ``/Highlight``
    annotation into the local PDF file via PyMuPDF. PDF.js will render it
    via its built-in AnnotationLayer on next paper reload.

    **Zotero**: if creds + attachment key are available, the highlight is
    also pushed to Zotero as a best-effort side-effect.

    Body: ``{"highlight": {text, page_index, page_label, rects, color}}``
    or ``{"highlights": [...]}``.
    """
    _state = _context._state
    _state.reload()
    doc = _state.get_document(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "reason": "invalid_json"}, status_code=400)

    highlights = body.get("highlights")
    if highlights is None and body.get("highlight"):
        highlights = [body["highlight"]]
    if not isinstance(highlights, list) or not highlights:
        return JSONResponse(
            {"ok": False, "reason": "missing_highlights"}, status_code=400,
        )
    import math as _math
    for h in highlights:
        if not isinstance(h, dict) or not h.get("text") or not h.get("rects"):
            return JSONResponse(
                {"ok": False, "reason": "invalid_highlight"}, status_code=400,
            )
        # Each rect must be a 4-tuple of finite numbers. Without this
        # check, null/NaN values slip through (JSON.stringify(NaN) == null)
        # and cause a TypeError inside PyMuPDF which we then silently
        # swallow, producing a confusing "no ids" response.
        for rect in h["rects"]:
            if (
                not isinstance(rect, (list, tuple))
                or len(rect) != 4
                or not all(
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and _math.isfinite(v)
                    for v in rect
                )
            ):
                return JSONResponse(
                    {
                        "ok": False,
                        "reason": "invalid_rect_values",
                        "hint": "Rect coordinate is null, NaN, or not a number. "
                                "Usually means the PDF.js viewport was stale — "
                                "scroll a bit and re-select.",
                    },
                    status_code=400,
                )

    # ── 1. Write to the local PDF (primary store) ─────────────────────────
    search = _pdf_search_result(doc)
    local_path = search.get("path")
    if not local_path:
        return JSONResponse(
            {"ok": False, "reason": "no_local_pdf",
             "hint": "The PDF isn't cached locally — can't save a highlight."},
            status_code=400,
        )

    from distillate import highlight_io
    loop = asyncio.get_event_loop()

    def _write_all() -> tuple[list[str], list[dict]]:
        """Returns (created_ids, failures). Each failure carries the
        original highlight + a reason string so the client can surface
        exactly what went wrong for each rejected entry."""
        ids: list[str] = []
        failures: list[dict] = []
        for h in highlights:
            try:
                page_index = int(h.get("page_index", 0))
            except (TypeError, ValueError) as exc:
                failures.append({
                    "reason": f"bad page_index: {h.get('page_index')!r} ({exc})",
                    "text_preview": (h.get("text") or "")[:60],
                })
                continue
            annot_id = highlight_io.add_highlight(
                local_path,
                page_index=page_index,
                rects=h["rects"],
                text=h.get("text", ""),
                color=h.get("color", "#ffd400"),
            )
            if annot_id:
                ids.append(annot_id)
            else:
                # add_highlight already logged the specific reason; here
                # we just record a breadcrumb for the client.
                failures.append({
                    "reason": "add_highlight returned None "
                              f"(page={page_index}, rects_count={len(h['rects'])}, "
                              f"text_len={len(h.get('text') or '')})",
                    "text_preview": (h.get("text") or "")[:60],
                })
        return ids, failures

    try:
        created_ids, failures = await loop.run_in_executor(_context._executor, _write_all)
    except Exception as exc:
        log.warning("PDF highlight write failed for %s: %s", paper_key, exc, exc_info=True)
        return JSONResponse(
            {"ok": False, "reason": "pdf_write_failed",
             "hint": f"PyMuPDF raised: {exc}"},
            status_code=500,
        )
    if not created_ids:
        log.warning(
            "PDF highlight write returned no ids for %s (pdf=%s, failures=%r, highlights=%r)",
            paper_key, local_path, failures, highlights,
        )
        hint = (
            failures[0]["reason"] if failures
            else "add_highlight returned no ids"
        )
        return JSONResponse(
            {"ok": False, "reason": "pdf_write_failed",
             "hint": hint, "failures": failures},
            status_code=500,
        )

    # ── 2. Propagate to Zotero (best-effort) ─────────────────────────────
    # zotero_status reflects exactly what happened so the client can
    # render the right toast colour:
    #   "synced"          → green: highlight is in both PDF + Zotero
    #   "failed"          → yellow: PDF saved but Zotero rejected the write
    #   "not_configured"  → green/silent: user hasn't set up Zotero
    #   "not_attempted"   → green/silent: paper has no Zotero attachment
    from distillate import config as _config
    att_key = (doc.get("zotero_attachment_key") or "").strip()
    zotero_keys: list[str] = []
    zotero_status: str

    if not (_config.ZOTERO_API_KEY and _config.ZOTERO_USER_ID):
        zotero_status = "not_configured"
    elif not att_key:
        zotero_status = "not_attempted"
    else:
        from distillate import zotero_client

        async def _try_zotero(key):
            return await loop.run_in_executor(
                _context._executor,
                lambda: zotero_client.add_user_highlights(key, highlights),
            )

        try:
            zotero_keys = await _try_zotero(att_key)
        except Exception as exc:
            log.info("Zotero write failed (att=%s): %s", att_key, exc)
        if not zotero_keys:
            try:
                fresh = await loop.run_in_executor(
                    _context._executor,
                    lambda: zotero_client.get_pdf_attachment(paper_key),
                )
                if fresh and fresh["key"] != att_key:
                    doc["zotero_attachment_key"] = fresh["key"]
                    _state.save()
                    zotero_keys = await _try_zotero(fresh["key"])
            except Exception as exc2:
                log.info("Zotero retry failed for %s: %s", paper_key, exc2)
        zotero_status = "synced" if zotero_keys else "failed"

    return JSONResponse({
        "ok": True,
        "saved_to_pdf": True,
        "zotero_status": zotero_status,
        # Legacy fields for any clients still on the old contract.
        "synced_to_zotero": bool(zotero_keys),
        "annot_ids": created_ids,
        "zotero_keys": zotero_keys,
    })


@router.delete("/papers/{paper_key}/annotations")
async def delete_paper_annotation(paper_key: str, request: Request):
    """Delete a highlight from the PDF (primary) and Zotero (side-effect).

    Body accepts either:
      - ``{"id": "distillate-..."}`` — delete by PDF /NM id (preferred), or
      - ``{"text": "...", "page_index": N}`` — match Zotero-only highlights
        that don't yet have a PDF counterpart.
    """
    _state = _context._state
    _state.reload()
    doc = _state.get_document(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"ok": False, "reason": "invalid_json"}, status_code=400)

    annot_id = (body.get("id") or "").strip()
    target_text = " ".join((body.get("text") or "").split())
    target_page = body.get("page_index", 0)
    if not annot_id and not target_text:
        return JSONResponse(
            {"ok": False, "reason": "missing_target"}, status_code=400,
        )

    loop = asyncio.get_event_loop()

    # ── 1. Remove from the local PDF ──
    removed_pdf = False
    search = _pdf_search_result(doc)
    local_path = search.get("path")
    if local_path and annot_id:
        from distillate import highlight_io
        try:
            removed_pdf = await loop.run_in_executor(
                _context._executor,
                lambda: highlight_io.delete_highlight(local_path, annot_id),
            )
        except Exception as exc:
            log.debug("PDF highlight delete failed: %s", exc)

    # ── 2. Remove from Zotero (best-effort) ──
    removed_zotero = 0
    from distillate import config as _config
    att_key = (doc.get("zotero_attachment_key") or "").strip()
    if att_key and target_text and _config.ZOTERO_API_KEY and _config.ZOTERO_USER_ID:
        from distillate import zotero_client
        try:
            removed_zotero = await loop.run_in_executor(
                _context._executor,
                lambda: zotero_client.delete_user_highlight(
                    att_key, target_text, target_page,
                ),
            )
        except Exception as exc:
            log.debug("Zotero highlight delete failed: %s", exc)

    return JSONResponse({
        "ok": True,
        "removed_pdf": removed_pdf,
        "removed_zotero": removed_zotero,
    })


@router.post("/papers/{paper_key}/annotations/clear")
async def clear_paper_annotations(paper_key: str):
    """Remove all user-created highlights from the paper's local PDF.

    Pipeline-generated annotations (subject == "distillate") are preserved.
    Useful for wiping extraneous test highlights in one shot.
    """
    _state = _context._state
    _state.reload()
    doc = _state.get_document(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

    search = _pdf_search_result(doc)
    local_path = search.get("path")
    if not local_path:
        return JSONResponse(
            {"ok": False, "reason": "no_local_pdf"}, status_code=400,
        )

    from distillate import highlight_io
    loop = asyncio.get_event_loop()
    try:
        removed = await loop.run_in_executor(
            _context._executor,
            lambda: highlight_io.clear_user_highlights(local_path),
        )
    except Exception as exc:
        log.warning("Clear highlights failed for %s: %s", paper_key, exc)
        return JSONResponse(
            {"ok": False, "reason": "clear_failed", "detail": str(exc)},
            status_code=500,
        )

    return JSONResponse({"ok": True, "removed": removed})


@router.post("/papers/import")
async def import_paper_upload(file: UploadFile = File(...)):
    """Import a drag-dropped PDF into Zotero as a new paper.

    Creates a minimal parent item (title inferred from PDF metadata or
    filename), uploads the PDF via Zotero's imported_file flow, tags as
    inbox, and tracks it in state. Returns the new paper_key so the
    desktop client can select the paper immediately.
    """
    if not file.filename:
        return JSONResponse({"ok": False, "reason": "missing_filename"}, status_code=400)

    pdf_bytes = await file.read()
    if not pdf_bytes:
        return JSONResponse({"ok": False, "reason": "empty_file"}, status_code=400)
    if len(pdf_bytes) > 200 * 1024 * 1024:  # 200 MB guard
        return JSONResponse({"ok": False, "reason": "file_too_large"}, status_code=413)
    if not pdf_bytes.startswith(b"%PDF-"):
        return JSONResponse({"ok": False, "reason": "not_a_pdf"}, status_code=400)

    filename = Path(file.filename).name

    # Infer a display title from the PDF's metadata; fall back to filename
    # stem. Stem is a reasonable last-resort — the next sync/refresh cycle
    # will enrich via Semantic Scholar if the paper has a DOI/arxiv id.
    title = ""
    try:
        import pymupdf
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            meta = doc.metadata or {}
            title = (meta.get("title") or "").strip()
    except Exception:
        log.debug("PDF metadata read failed for %s", filename, exc_info=True)
    if not title:
        title = Path(filename).stem.replace("_", " ").strip()

    from distillate import config, zotero_client

    def _do_import():
        parent_key = zotero_client.create_paper(
            title=title,
            authors=[],
            item_type="preprint",
            tags=[config.ZOTERO_TAG_INBOX],
        )
        if not parent_key:
            return None, "parent_create_failed"

        att_key = zotero_client.upload_pdf_attachment(
            parent_key, filename, pdf_bytes,
        )
        if not att_key:
            # Leave the parent in Zotero so the user can re-attach manually —
            # Zotero's UI handles an empty parent cleanly.
            return None, "upload_failed"

        state = _context._state
        state.reload()
        state.add_document(
            zotero_item_key=parent_key,
            zotero_attachment_key=att_key,
            zotero_attachment_md5="",
            remarkable_doc_name=title,
            title=title,
            authors=[],
            status="tracked",
            metadata={"title": title, "citekey": ""},
        )
        state.save()

        # Save PDF to Obsidian inbox and refresh the vault index.
        try:
            from distillate import obsidian
            obsidian.save_inbox_pdf(title, pdf_bytes, citekey="")
            from distillate.vault_wiki import regenerate_index
            regenerate_index()
        except Exception:
            log.debug("Obsidian inbox save failed for '%s'", title, exc_info=True)

        return parent_key, None

    loop = asyncio.get_event_loop()
    try:
        parent_key, reason = await loop.run_in_executor(
            _context._executor, _do_import,
        )
    except Exception as exc:
        log.warning("Import failed for %s: %s", filename, exc)
        return JSONResponse(
            {"ok": False, "reason": "import_failed", "detail": str(exc)},
            status_code=500,
        )

    if not parent_key:
        return JSONResponse(
            {"ok": False, "reason": reason or "import_failed"},
            status_code=502,
        )

    return JSONResponse({
        "ok": True,
        "paper_key": parent_key,
        "title": title,
    })


@router.post("/papers/{paper_key}/mark-read")
async def mark_paper_read(paper_key: str):
    """Mark a paper as read from the desktop reader.

    Lightweight processing:
      - Computes engagement from existing Zotero highlights
      - Sets status to ``processed``
      - Adds the ``read`` tag to the Zotero item (if creds are configured)
      - Logs to the lab notebook

    Does NOT generate an AI summary or render an annotated PDF — those are
    pipeline concerns that run on the next full sync.
    """
    _state = _context._state
    _state.reload()
    doc = _state.get_document(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

    if doc.get("status") == "processed":
        return JSONResponse({"ok": True, "already": True})

    # Compute engagement from Zotero highlights (if available).
    att_key = (doc.get("zotero_attachment_key") or "").strip()
    engagement = 0
    highlight_count = 0
    if att_key:
        try:
            from distillate import zotero_client
            from distillate.pipeline import _compute_engagement
            loop = asyncio.get_event_loop()
            highlights = await loop.run_in_executor(
                _context._executor,
                lambda: zotero_client.get_highlight_annotations(att_key),
            )
            if highlights:
                meta = doc.get("metadata", {}) or {}
                page_count = meta.get("numPages") or meta.get("page_count", 0) or 0
                engagement = _compute_engagement(highlights, page_count)
                highlight_count = sum(len(v) for v in highlights.values())
        except Exception as exc:
            log.debug("Engagement computation failed for %s: %s", paper_key, exc)

    doc["engagement"] = engagement
    doc["highlight_word_count"] = highlight_count
    doc["highlight_count"] = highlight_count
    _state.mark_processed(paper_key)
    _state.save()

    from distillate import config as _config

    # Best-effort: tag the Zotero item as read.
    if _config.ZOTERO_API_KEY and _config.ZOTERO_USER_ID:
        try:
            from distillate import zotero_client
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                _context._executor,
                lambda: zotero_client.add_tag(paper_key, _config.ZOTERO_TAG_READ),
            )
        except Exception as exc:
            log.debug("Zotero tag write failed for %s: %s", paper_key, exc)

    # Obsidian: create a paper note (without AI summary — filled on next
    # sync), append to the reading log, and rebuild the vault index.
    try:
        from distillate import obsidian
        loop = asyncio.get_event_loop()
        meta = doc.get("metadata", {}) or {}
        citekey = meta.get("citekey", "")
        title = doc.get("title", "")

        def _obsidian_work():
            # Lightweight note — highlights but no AI summary yet.
            obsidian.create_paper_note(
                title=title,
                authors=doc.get("authors", []),
                date_added=doc.get("uploaded_at", ""),
                zotero_item_key=paper_key,
                highlights=highlights if highlight_count else None,
                doi=meta.get("doi", ""),
                abstract=meta.get("abstract", ""),
                url=meta.get("url", ""),
                publication_date=meta.get("publication_date", ""),
                journal=meta.get("journal", ""),
                topic_tags=meta.get("tags"),
                citation_count=meta.get("citation_count", 0),
                engagement=engagement,
                highlighted_pages=len(highlights) if highlights else 0,
                highlight_word_count=highlight_count,
                page_count=meta.get("page_count", 0),
                citekey=citekey,
            )
            obsidian.append_to_reading_log(
                title, "", citekey=citekey,
            )
            obsidian.delete_inbox_pdf(title, citekey=citekey)

            from distillate.vault_wiki import regenerate_index
            regenerate_index()

        await loop.run_in_executor(_context._executor, _obsidian_work)
    except Exception as exc:
        log.debug("Obsidian sync failed for %s: %s", paper_key, exc)

    return JSONResponse({
        "ok": True,
        "engagement": engagement,
        "highlight_count": highlight_count,
    })


@router.get("/papers/{paper_key}/read-position")
async def get_read_position(paper_key: str):
    """Return the persisted last-read page for this paper, if any."""
    _state = _context._state
    _state.reload()
    doc = _state.get_document(paper_key)
    if not doc:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
    return JSONResponse({
        "ok": True,
        "page": doc.get("last_read_page") or 1,
        "updated_at": doc.get("last_read_at") or "",
    })


@router.post("/papers/{paper_key}/read-position")
async def set_read_position(paper_key: str, request: Request):
    """Persist the current scroll position from the desktop reader.

    Body: ``{"page": <1-based page number>}``."""
    _state = _context._state
    _state.reload()
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    page = body.get("page", 1)
    if not _state.set_read_position(paper_key, page):
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)
    _state.save()
    return JSONResponse({"ok": True, "page": int(page)})
