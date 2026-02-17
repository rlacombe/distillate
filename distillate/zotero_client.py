"""Zotero Web API v3 client.

Handles polling for new items, downloading/uploading PDFs, and managing tags.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

from distillate import config

log = logging.getLogger(__name__)

_BASE = "https://api.zotero.org"

_MAX_RETRIES = 3
_RETRY_DELAY_BASE = 2  # seconds; exponential: 2, 4, 8
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _headers() -> dict:
    return {
        "Zotero-API-Version": "3",
        "Zotero-API-Key": config.ZOTERO_API_KEY,
    }


def _url(path: str) -> str:
    return f"{_BASE}/users/{config.ZOTERO_USER_ID}{path}"


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """HTTP request with retry on transient failures.

    Retries on ConnectionError, Timeout, and 5xx/429 with exponential backoff.
    4xx client errors (except 429) propagate immediately.
    """
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=config.HTTP_TIMEOUT, **kwargs)
            _handle_backoff(resp)

            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY_BASE * (2 ** attempt)
                log.warning(
                    "Zotero returned %d, retrying in %ds (%d/%d)",
                    resp.status_code, delay, attempt + 1, _MAX_RETRIES,
                )
                time.sleep(delay)
                continue

            resp.raise_for_status()
            return resp

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY_BASE * (2 ** attempt)
                log.warning(
                    "Zotero request failed (%s), retrying in %ds (%d/%d)",
                    type(exc).__name__, delay, attempt + 1, _MAX_RETRIES,
                )
                time.sleep(delay)
            else:
                raise

    raise last_exc  # type: ignore[misc]


def _get(path: str, params: Optional[Dict] = None, **kwargs) -> requests.Response:
    return _request_with_retry(
        "GET", _url(path), headers=_headers(), params=params, **kwargs,
    )


def _post(path: str, **kwargs) -> requests.Response:
    return _request_with_retry(
        "POST", _url(path), headers=_headers(), **kwargs,
    )


def _patch(path: str, **kwargs) -> requests.Response:
    headers = {**_headers(), **kwargs.pop("headers", {})}
    return _request_with_retry(
        "PATCH", _url(path), headers=headers, **kwargs,
    )


def _delete(path: str, **kwargs) -> requests.Response:
    headers = {**_headers(), **kwargs.pop("headers", {})}
    return _request_with_retry(
        "DELETE", _url(path), headers=headers, **kwargs,
    )


def _handle_backoff(resp: requests.Response) -> None:
    backoff = resp.headers.get("Backoff") or resp.headers.get("Retry-After")
    if backoff:
        wait = int(backoff)
        log.warning("Zotero asked to back off for %d seconds", wait)
        time.sleep(wait)


# -- Polling --


def get_library_version() -> int:
    """Get the current library version (cheap check)."""
    resp = _get("/items", params={"limit": "0"})
    return int(resp.headers["Last-Modified-Version"])


def get_changed_item_keys(since_version: int) -> Tuple[Dict[str, int], int]:
    """Get item keys changed since a given library version.

    Returns (dict of {item_key: version}, new_library_version).
    """
    resp = _get("/items/top", params={
        "format": "versions",
        "since": str(since_version),
    })
    new_version = int(resp.headers["Last-Modified-Version"])
    return resp.json(), new_version


def get_recent_papers(limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch recent top-level items sorted by dateAdded (newest first).

    Returns items that pass filter_new_papers() — i.e. valid paper types
    without workflow tags already applied.
    """
    resp = _get("/items/top", params={
        "sort": "dateAdded",
        "direction": "desc",
        "limit": str(limit),
        "format": "json",
    })
    return filter_new_papers(resp.json())


def get_items_by_keys(keys: List[str]) -> List[Dict[str, Any]]:
    """Fetch full item data for a list of item keys (max 50 per call)."""
    items = []
    for i in range(0, len(keys), 50):
        batch = keys[i : i + 50]
        resp = _get("/items", params={"itemKey": ",".join(batch)})
        items.extend(resp.json())
    return items


# -- Filtering --


def filter_new_papers(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter items to only new papers (no workflow tags, skips non-paper types).

    Keeps academic paper types (journalArticle, conferencePaper, preprint, etc.)
    and skips items that won't have a useful PDF for reMarkable.
    """
    skip_types = {
        "attachment", "note",
        "book", "bookSection",
        "webpage", "blogPost", "forumPost",
        "presentation", "document",
        "letter", "email", "map",
        "artwork", "film", "tvBroadcast", "radioBroadcast",
        "podcast", "audioRecording", "videoRecording",
        "encyclopediaArticle", "dictionaryEntry",
        "case", "statute", "bill", "hearing",
        "patent", "computerProgram",
        "interview", "instantMessage",
    }
    workflow_tags = {config.ZOTERO_TAG_INBOX, config.ZOTERO_TAG_READ}

    result = []
    for item in items:
        data = item.get("data", {})
        item_type = data.get("itemType", "")
        if item_type in skip_types:
            log.debug("Skipping %s: %s", item_type, data.get("title", ""))
            continue
        item_tags = {t["tag"] for t in data.get("tags", [])}
        if item_tags & workflow_tags:
            continue
        result.append(item)
    return result


# -- Children & PDF --


def get_pdf_attachment(item_key: str) -> Optional[Dict[str, Any]]:
    """Find the first PDF attachment child of an item."""
    resp = _get(f"/items/{item_key}/children")
    for child in resp.json():
        data = child.get("data", {})
        if (
            data.get("itemType") == "attachment"
            and data.get("contentType") == "application/pdf"
            and data.get("linkMode") in ("imported_file", "imported_url")
        ):
            return child
    return None


def download_pdf(attachment_key: str) -> bytes:
    """Download the PDF file for an attachment item."""
    resp = _get(f"/items/{attachment_key}/file")
    return resp.content


def download_pdf_from_url(url: str) -> Optional[bytes]:
    """Try to download a PDF directly from a paper URL (arxiv, biorxiv, etc.).

    Converts abstract page URLs to direct PDF links where possible.
    Returns PDF bytes or None if download fails.
    """
    import re as _re

    pdf_url = None

    # arxiv: http://arxiv.org/abs/XXXX -> https://arxiv.org/pdf/XXXX.pdf
    m = _re.search(r"arxiv\.org/abs/([\d.]+)", url)
    if m:
        pdf_url = f"https://arxiv.org/pdf/{m.group(1)}.pdf"

    # biorxiv/medrxiv: .../content/ID -> .../content/ID.full.pdf
    if not pdf_url:
        m = _re.search(r"(bio|med)rxiv\.org/content/([\d./v]+)", url)
        if m:
            base = url.rstrip("/")
            if not base.endswith(".pdf"):
                pdf_url = f"{base}.full.pdf"

    if not pdf_url:
        return None

    try:
        resp = requests.get(pdf_url, timeout=60, allow_redirects=True)
        resp.raise_for_status()
        if resp.headers.get("content-type", "").startswith("application/pdf") or len(resp.content) > 10000:
            log.info("Downloaded PDF from %s (%d bytes)", pdf_url, len(resp.content))
            return resp.content
    except requests.exceptions.Timeout:
        log.debug("Timed out downloading PDF from %s", pdf_url)
    except Exception:
        log.warning("Could not download PDF from %s", pdf_url, exc_info=True)

    return None


# -- Tagging --


def add_tag(item_key: str, tag: str) -> None:
    """Add a tag to an item, preserving existing tags."""
    resp = _get(f"/items/{item_key}")
    item = resp.json()
    version = item["version"]
    existing_tags = [t["tag"] for t in item["data"].get("tags", [])]

    if tag in existing_tags:
        return

    existing_tags.append(tag)
    _patch(
        f"/items/{item_key}",
        json={"tags": [{"tag": t} for t in existing_tags]},
        headers={"If-Unmodified-Since-Version": str(version)},
    )
    log.info("Added tag '%s' to %s", tag, item_key)


def replace_tag(item_key: str, old_tag: str, new_tag: str) -> None:
    """Replace one tag with another on an item."""
    resp = _get(f"/items/{item_key}")
    item = resp.json()
    version = item["version"]
    tags = [t["tag"] for t in item["data"].get("tags", [])]

    new_tags = [new_tag if t == old_tag else t for t in tags]
    if new_tag not in new_tags:
        new_tags.append(new_tag)

    _patch(
        f"/items/{item_key}",
        json={"tags": [{"tag": t} for t in new_tags]},
        headers={"If-Unmodified-Since-Version": str(version)},
    )
    log.info("Replaced tag '%s' → '%s' on %s", old_tag, new_tag, item_key)



def delete_attachment(attachment_key: str) -> None:
    """Delete a PDF attachment item from Zotero (file + metadata entry).

    The parent item (paper metadata, tags, etc.) is preserved.
    """
    resp = _get(f"/items/{attachment_key}")
    version = resp.json()["version"]
    _delete(
        f"/items/{attachment_key}",
        headers={"If-Unmodified-Since-Version": str(version)},
    )
    log.info("Deleted attachment %s from Zotero", attachment_key)


def get_linked_attachment(item_key: str) -> Optional[Dict[str, Any]]:
    """Find the first linked file attachment child of an item."""
    resp = _get(f"/items/{item_key}/children")
    for child in resp.json():
        data = child.get("data", {})
        if (
            data.get("itemType") == "attachment"
            and data.get("linkMode") == "linked_file"
        ):
            return child
    return None


def create_linked_attachment(
    parent_key: str, filename: str, local_path: str,
) -> Optional[str]:
    """Create a linked file attachment pointing to a local PDF.

    Returns the new attachment's item key, or None on failure.
    """
    resp = _post(
        "/items",
        json=[{
            "itemType": "attachment",
            "parentItem": parent_key,
            "linkMode": "linked_file",
            "title": filename,
            "contentType": "application/pdf",
            "path": local_path,
            "tags": [],
            "relations": {},
        }],
    )
    result = resp.json()
    successful = result.get("successful", {})
    if "0" in successful:
        key = successful["0"]["key"]
        log.info("Created linked attachment %s → %s", key, local_path)
        return key
    log.warning("Failed to create linked attachment: %s", result.get("failed"))
    return None


def create_obsidian_link(parent_key: str, obsidian_uri: str) -> Optional[str]:
    """Create a linked_url attachment with an obsidian:// URI.

    Checks for an existing "Open in Obsidian" attachment first to avoid
    duplicates. Returns the attachment key on success, None on failure.
    """
    # Check for existing Obsidian link
    resp = _get(f"/items/{parent_key}/children")
    for child in resp.json():
        data = child.get("data", {})
        if (
            data.get("itemType") == "attachment"
            and data.get("linkMode") == "linked_url"
            and data.get("title") == "Open in Obsidian"
        ):
            log.info("Obsidian link already exists for %s, skipping", parent_key)
            return child["key"]

    resp = _post(
        "/items",
        json=[{
            "itemType": "attachment",
            "parentItem": parent_key,
            "linkMode": "linked_url",
            "title": "Open in Obsidian",
            "url": obsidian_uri,
            "tags": [],
            "relations": {},
        }],
    )
    result = resp.json()
    successful = result.get("successful", {})
    if "0" in successful:
        key = successful["0"]["key"]
        log.info("Created Obsidian link %s for %s", key, parent_key)
        return key
    log.warning("Failed to create Obsidian link: %s", result.get("failed"))
    return None


# -- Notes --


def set_note(
    parent_key: str,
    html_content: str,
    note_key: str = "",
) -> Optional[str]:
    """Create or update a child note on a Zotero item.

    If note_key is provided, updates that note directly (avoids searching
    children, which can miss notes due to Zotero sync lag). Otherwise
    searches children for an existing note to update.
    Returns the note's item key on success, None on failure.
    """
    # Fast path: update existing note by key
    if note_key:
        try:
            resp = _get(f"/items/{note_key}")
            if resp.status_code == 200:
                version = resp.json()["version"]
                _patch(
                    f"/items/{note_key}",
                    json={"note": html_content},
                    headers={"If-Unmodified-Since-Version": str(version)},
                )
                log.info("Updated note %s on %s", note_key, parent_key)
                return note_key
        except Exception:
            log.debug("Could not update note %s, will search/create", note_key)

    # Search children for an existing note
    resp = _get(f"/items/{parent_key}/children")
    for child in resp.json():
        data = child.get("data", {})
        if data.get("itemType") == "note":
            version = child["version"]
            _patch(
                f"/items/{child['key']}",
                json={"note": html_content},
                headers={"If-Unmodified-Since-Version": str(version)},
            )
            log.info("Updated note on %s", parent_key)
            return child["key"]

    # Create new note
    resp = _post(
        "/items",
        json=[{
            "itemType": "note",
            "parentItem": parent_key,
            "note": html_content,
            "tags": [],
            "relations": {},
        }],
    )
    result = resp.json()
    successful = result.get("successful", {})
    if "0" in successful:
        key = successful["0"]["key"]
        log.info("Created note %s on %s", key, parent_key)
        return key
    log.warning("Failed to create note: %s", result.get("failed"))
    return None


def _build_note_html(
    summary: str = "",
    highlights: Optional[Union[List[str], Dict[int, List[str]]]] = None,
) -> str:
    """Build HTML content for a Zotero note from summary and highlights."""
    parts = []
    if summary:
        parts.append(f"<p>{summary}</p>")
    if highlights:
        if isinstance(highlights, list):
            parts.append("<h2>Highlights</h2>")
            for h in highlights:
                parts.append(f"<p>&ldquo;{h}&rdquo;</p>")
        elif isinstance(highlights, dict):
            if len(highlights) == 1:
                parts.append("<h2>Highlights</h2>")
                for h in next(iter(highlights.values())):
                    parts.append(f"<p>&ldquo;{h}&rdquo;</p>")
            else:
                for page_num in sorted(highlights.keys()):
                    parts.append(f"<h2>Page {page_num}</h2>")
                    for h in highlights[page_num]:
                        parts.append(f"<p>&ldquo;{h}&rdquo;</p>")
    return "\n".join(parts)


def create_highlight_annotations(
    attachment_key: str,
    highlights: List[Dict[str, Any]],
) -> List[str]:
    """Create Zotero highlight annotations on a PDF attachment.

    highlights: list of dicts from renderer.extract_zotero_highlights().
    Returns list of created annotation item keys.
    Batches in groups of 50 (Zotero API limit).
    """
    import json as _json

    if not highlights:
        return []

    # Check for existing Distillate annotations and remove them
    resp = _get(f"/items/{attachment_key}/children", params={"itemType": "annotation"})
    if resp.status_code == 200:
        existing = resp.json()
        distillate_anns = [
            a for a in existing
            if any(t.get("tag") == "distillate" for t in a.get("data", {}).get("tags", []))
        ]
        for a in distillate_anns:
            _delete(
                f"/items/{a['key']}",
                headers={"If-Unmodified-Since-Version": str(a["version"])},
            )
            log.debug("Deleted existing Distillate annotation %s", a["key"])

    # Build annotation items
    items = []
    for h in highlights:
        items.append({
            "itemType": "annotation",
            "parentItem": attachment_key,
            "annotationType": "highlight",
            "annotationText": h["text"],
            "annotationComment": "",
            "annotationColor": h.get("color", "#ffd400"),
            "annotationPageLabel": h["page_label"],
            "annotationSortIndex": h["sort_index"],
            "annotationPosition": _json.dumps({
                "pageIndex": h["page_index"],
                "rects": h["rects"],
            }),
            "tags": [{"tag": "distillate"}],
        })

    # Batch in groups of 50
    created_keys: List[str] = []
    for i in range(0, len(items), 50):
        batch = items[i:i + 50]
        resp = _post("/items", json=batch)
        if resp.status_code in (200, 201):
            result = resp.json()
            successful = result.get("successful", {})
            created_keys.extend(v["key"] for v in successful.values())
        else:
            log.warning(
                "Failed to create annotations (batch %d): %s",
                i // 50, resp.status_code,
            )

    log.info(
        "Created %d Zotero highlight annotation(s) on %s",
        len(created_keys), attachment_key,
    )
    return created_keys


# -- Convenience: extract metadata --

_STOP_WORDS = {"a", "an", "the", "of", "in", "on", "for", "and", "to", "with", "from"}


def _generate_citekey(authors: list, title: str, date: str) -> str:
    """Generate a citekey from author, title, and date.

    Format: surname_word_year (e.g. vaswani_attention_2017).
    """
    import re

    surname = "unknown"
    if authors:
        # Extract surname: take part before comma (e.g. "Doe, J." → "Doe")
        raw = authors[0].split(",")[0].strip()
        surname = re.sub(r"[^a-z]", "", raw.lower()) or "unknown"

    word = "untitled"
    for w in title.split():
        cleaned = re.sub(r"[^a-z]", "", w.lower())
        if cleaned and cleaned not in _STOP_WORDS:
            word = cleaned
            break

    year = date[:4] if date and len(date) >= 4 else ""

    parts = [p for p in [surname, word, year] if p]
    return "_".join(parts)


def extract_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    """Extract metadata from a Zotero item."""
    data = item.get("data", {})
    title = data.get("title", "Untitled")
    # Strip journal suffix added by some Zotero translators (e.g. "Title | Science")
    if " | " in title:
        title = title.rsplit(" | ", 1)[0].strip()
    # Strip author prefix (e.g. "Dario Amodei — Title" → "Title")
    if " — " in title:
        prefix, rest = title.split(" — ", 1)
        creator_names = {
            c.get("lastName", "").lower()
            for c in data.get("creators", [])
        } | {
            c.get("name", "").lower()
            for c in data.get("creators", [])
        }
        creator_names.discard("")
        prefix_lower = prefix.lower()
        if prefix_lower in creator_names or prefix_lower.split()[-1] in creator_names:
            title = rest.strip()
    creators = data.get("creators", [])
    authors = [
        c.get("lastName") or c.get("name", "Unknown")
        for c in creators
        if c.get("creatorType") == "author"
    ]
    if not authors:
        authors = [
            c.get("lastName") or c.get("name", "Unknown")
            for c in creators
        ]
    # Extract Zotero tags, excluding workflow tags
    workflow_tags = {config.ZOTERO_TAG_INBOX, config.ZOTERO_TAG_READ}
    tags = [
        t["tag"] for t in data.get("tags", [])
        if t["tag"] not in workflow_tags
    ]
    # Extract citekey from Better BibTeX's "Citation Key:" in extra field
    extra = data.get("extra", "")
    citekey = ""
    for line in extra.splitlines():
        if line.startswith("Citation Key:"):
            citekey = line.split(":", 1)[1].strip()
            break

    # Fallback: generate citekey from first author + first title word + year
    if not citekey:
        citekey = _generate_citekey(authors, title, data.get("date", ""))

    publication_date = data.get("date", "")
    return {
        "title": title,
        "authors": authors,
        "citekey": citekey,
        "doi": data.get("DOI", ""),
        "abstract": data.get("abstractNote", ""),
        "url": data.get("url", ""),
        "publication_date": publication_date,
        "journal": (
            data.get("publicationTitle")
            or data.get("proceedingsTitle")
            or data.get("bookTitle")
            or ""
        ),
        "tags": tags,
    }
