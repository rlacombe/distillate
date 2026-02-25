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
            backed_off = _handle_backoff(resp)

            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                if not backed_off:
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


def _handle_backoff(resp: requests.Response) -> bool:
    """Sleep if Zotero asks for backoff. Returns True if it slept."""
    backoff = resp.headers.get("Backoff") or resp.headers.get("Retry-After")
    if backoff:
        wait = int(backoff)
        log.warning("Zotero asked to back off for %d seconds", wait)
        time.sleep(wait)
        return True
    return False


# -- Polling --


def get_library_version() -> int:
    """Get the current library version (cheap check)."""
    resp = _get("/items", params={"limit": "0"})
    return int(resp.headers["Last-Modified-Version"])


def get_changed_item_keys(
    since_version: int, collection_key: str = "",
) -> Tuple[Dict[str, int], int]:
    """Get item keys changed since a given library version.

    If ``collection_key`` is set, only items in that collection are returned.
    Returns (dict of {item_key: version}, new_library_version).
    """
    path = (
        f"/collections/{collection_key}/items/top"
        if collection_key
        else "/items/top"
    )
    resp = _get(path, params={
        "format": "versions",
        "since": str(since_version),
    })
    new_version = int(resp.headers["Last-Modified-Version"])
    return resp.json(), new_version


def get_recent_papers(
    limit: int = 100, collection_key: str = "",
) -> List[Dict[str, Any]]:
    """Fetch recent top-level items sorted by dateAdded (newest first).

    If ``collection_key`` is set, only items in that collection are returned.
    Returns items that pass filter_new_papers() — i.e. valid paper types
    without workflow tags already applied.
    """
    path = (
        f"/collections/{collection_key}/items/top"
        if collection_key
        else "/items/top"
    )
    resp = _get(path, params={
        "sort": "dateAdded",
        "direction": "desc",
        "limit": str(limit),
        "format": "json",
    })
    return filter_new_papers(resp.json())


def list_collections() -> List[Dict[str, Any]]:
    """List all collections in the library."""
    resp = _get("/collections")
    return resp.json()


def get_collection_name(collection_key: str) -> str:
    """Get the name of a collection by key."""
    resp = _get(f"/collections/{collection_key}")
    return resp.json()["data"]["name"]


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
            and data.get("linkMode") in (
                "imported_file", "imported_url", "linked_url",
            )
        ):
            return child
    return None


def download_pdf(attachment_key: str) -> bytes:
    """Download the PDF file for an attachment item."""
    resp = _get(f"/items/{attachment_key}/file")
    return resp.content


def download_pdf_from_webdav(attachment_key: str) -> Optional[bytes]:
    """Download a PDF from the user's WebDAV server.

    Zotero WebDAV storage stores attachments as <KEY>.zip containing a
    single PDF. Returns PDF bytes or None if WebDAV is not configured or
    the download fails.
    """
    import io
    import zipfile

    if not config.ZOTERO_WEBDAV_URL:
        return None

    url = f"{config.ZOTERO_WEBDAV_URL}/zotero/{attachment_key}.zip"
    log.debug("Trying WebDAV: %s", url)

    try:
        auth = None
        if config.ZOTERO_WEBDAV_USERNAME:
            auth = (config.ZOTERO_WEBDAV_USERNAME, config.ZOTERO_WEBDAV_PASSWORD)
        resp = requests.get(url, auth=auth, timeout=config.HTTP_TIMEOUT)
        if resp.status_code == 404:
            log.debug("WebDAV 404 for %s", attachment_key)
            return None
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        log.warning("WebDAV download failed for '%s': %s", attachment_key, exc)
        return None

    log.debug(
        "WebDAV response: %d, %d bytes, type=%s",
        resp.status_code, len(resp.content),
        resp.headers.get("Content-Type", "unknown"),
    )

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            pdf_names = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
            if not pdf_names:
                log.warning(
                    "WebDAV zip for '%s' contains no PDF (files: %s)",
                    attachment_key, zf.namelist(),
                )
                return None
            return zf.read(pdf_names[0])
    except zipfile.BadZipFile:
        log.warning(
            "WebDAV response for '%s' is not a valid zip (%d bytes, starts: %r)",
            attachment_key, len(resp.content), resp.content[:100],
        )
        return None


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

    # arxiv direct PDF link: https://arxiv.org/pdf/XXXX or .pdf
    if not pdf_url:
        m = _re.search(r"arxiv\.org/pdf/([\d.]+)", url)
        if m:
            arxiv_id = m.group(1)
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    # biorxiv/medrxiv: .../content/ID -> .../content/ID.full.pdf
    if not pdf_url:
        m = _re.search(r"(bio|med)rxiv\.org/content/([\d./v]+)", url)
        if m:
            base = url.rstrip("/")
            if not base.endswith(".pdf"):
                pdf_url = f"{base}.full.pdf"

    # Direct PDF link (any URL ending in .pdf)
    if not pdf_url and url.rstrip("/").lower().endswith(".pdf"):
        pdf_url = url

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


def create_paper(
    title: str,
    authors: List[str],
    item_type: str = "preprint",
    doi: str = "",
    url: str = "",
    abstract: str = "",
    publication_date: str = "",
    tags: List[str] | None = None,
) -> Optional[str]:
    """Create a new paper item in the user's Zotero library.

    Returns the new item's key, or None on failure.
    """
    creators = []
    for name in authors:
        name = name.strip()
        if not name:
            continue
        parts = name.rsplit(" ", 1)
        if len(parts) == 2:
            creators.append({
                "firstName": parts[0], "lastName": parts[1],
                "creatorType": "author",
            })
        else:
            creators.append({
                "firstName": "", "lastName": name,
                "creatorType": "author",
            })

    item: Dict[str, Any] = {
        "itemType": item_type,
        "title": title,
        "creators": creators,
        "tags": [{"tag": t} for t in (tags or [])],
        "relations": {},
    }
    if doi:
        item["DOI"] = doi
    if url:
        item["url"] = url
    if abstract:
        item["abstractNote"] = abstract
    if publication_date:
        item["date"] = publication_date

    # Do NOT add the inbox tag here — filter_new_papers() skips items
    # that already have workflow tags.  The sync loop adds the inbox tag
    # in _upload_paper() after processing.

    # Place in the user's collection so scoped syncs pick it up
    if config.ZOTERO_COLLECTION_KEY:
        item["collections"] = [config.ZOTERO_COLLECTION_KEY]

    resp = _post("/items", json=[item])
    result = resp.json()
    successful = result.get("successful", {})
    if "0" in successful:
        key = successful["0"]["key"]
        log.info("Created paper '%s' -> %s", title[:60], key)
        return key
    log.warning("Failed to create paper: %s", result.get("failed"))
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


def build_note_html(
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


def get_highlight_annotations(attachment_key: str) -> Dict[int, List[str]]:
    """Read user's highlight annotations from a Zotero PDF attachment.

    Returns Dict mapping page numbers (1-based) to lists of highlighted text,
    matching the format of renderer.extract_highlights() for drop-in
    compatibility. Excludes annotations tagged 'distillate' (back-propagated).
    """
    import json as _json

    resp = _get(
        f"/items/{attachment_key}/children",
        params={"itemType": "annotation"},
    )
    if resp.status_code != 200:
        return {}

    # Collect highlights with position for sorting
    entries: List[tuple] = []  # (page_num, y_pos, text)
    for ann in resp.json():
        data = ann.get("data", {})
        # Skip our own back-propagated annotations
        if any(t.get("tag") == "distillate" for t in data.get("tags", [])):
            continue
        if data.get("annotationType") != "highlight":
            continue
        text = data.get("annotationText", "").strip()
        if not text:
            continue
        try:
            page_num = int(data.get("annotationPageLabel", "1"))
        except (ValueError, TypeError):
            page_num = 1
        # Sort by y-position within page (top of first rect)
        y_pos = 0.0
        try:
            pos = _json.loads(data.get("annotationPosition", "{}"))
            rects = pos.get("rects", [])
            if rects:
                y_pos = rects[0][1]  # y0 of first rect
        except (ValueError, TypeError, IndexError):
            pass
        entries.append((page_num, y_pos, text))

    # Group by page, sorted by position
    entries.sort(key=lambda e: (e[0], e[1]))
    by_page: Dict[int, List[str]] = {}
    for page_num, _, text in entries:
        by_page.setdefault(page_num, []).append(text)
    return by_page


def get_raw_annotations(attachment_key: str) -> List[Dict[str, Any]]:
    """Read all highlight annotations with position data for PDF rendering.

    Returns raw Zotero annotation dicts (excluding distillate-tagged ones).
    """
    import json as _json

    resp = _get(
        f"/items/{attachment_key}/children",
        params={"itemType": "annotation"},
    )
    if resp.status_code != 200:
        return []

    annotations = []
    for ann in resp.json():
        data = ann.get("data", {})
        if any(t.get("tag") == "distillate" for t in data.get("tags", [])):
            continue
        if data.get("annotationType") != "highlight":
            continue
        if not data.get("annotationText", "").strip():
            continue
        # Parse position JSON
        try:
            pos = _json.loads(data.get("annotationPosition", "{}"))
        except (ValueError, TypeError):
            pos = {}
        annotations.append({
            "text": data["annotationText"].strip(),
            "page_index": pos.get("pageIndex", 0),
            "page_label": data.get("annotationPageLabel", "1"),
            "rects": pos.get("rects", []),
            "color": data.get("annotationColor", "#ffd400"),
        })
    return annotations


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

    # Collect existing Distillate annotations (delete after successful create)
    old_anns = []
    resp = _get(f"/items/{attachment_key}/children", params={"itemType": "annotation"})
    if resp.status_code == 200:
        existing = resp.json()
        old_anns = [
            a for a in existing
            if any(t.get("tag") == "distillate" for t in a.get("data", {}).get("tags", []))
        ]

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

    # Create new annotations first (safe: tagged "distillate" so no duplicates)
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

    # Delete old annotations only after successful creation
    if created_keys and old_anns:
        for a in old_anns:
            _delete(
                f"/items/{a['key']}",
                headers={"If-Unmodified-Since-Version": str(a["version"])},
            )
            log.debug("Deleted old Distillate annotation %s", a["key"])

    log.info(
        "Created %d Zotero highlight annotation(s) on %s",
        len(created_keys), attachment_key,
    )
    return created_keys


def update_obsidian_link(parent_key: str, new_url: str) -> bool:
    """PATCH the existing 'Open in Obsidian' linked_url attachment with a new URL.

    Returns True if the update succeeded.
    """
    resp = _get(f"/items/{parent_key}/children")
    if resp.status_code != 200:
        return False

    for child in resp.json():
        data = child.get("data", {})
        if data.get("title") == "Open in Obsidian" and data.get("linkMode") == "linked_url":
            patch_resp = _patch(
                f"/items/{child['key']}",
                json={"url": new_url},
                headers={"If-Unmodified-Since-Version": str(child["version"])},
            )
            if patch_resp.status_code in (200, 204):
                log.info("Updated Obsidian link for %s", parent_key)
                return True
            log.warning("Failed to update Obsidian link: %s", patch_resp.status_code)
            return False

    log.debug("No 'Open in Obsidian' attachment found for %s", parent_key)
    return False


def update_linked_attachment_path(parent_key: str, new_title: str, new_path: str) -> bool:
    """PATCH the existing linked_file attachment with a new title and path.

    Returns True if the update succeeded.
    """
    resp = _get(f"/items/{parent_key}/children")
    if resp.status_code != 200:
        return False

    for child in resp.json():
        data = child.get("data", {})
        if data.get("linkMode") == "linked_file":
            patch_resp = _patch(
                f"/items/{child['key']}",
                json={"title": new_title, "path": new_path},
                headers={"If-Unmodified-Since-Version": str(child["version"])},
            )
            if patch_resp.status_code in (200, 204):
                log.info("Updated linked attachment path for %s", parent_key)
                return True
            log.warning("Failed to update linked attachment: %s", patch_resp.status_code)
            return False

    log.debug("No linked_file attachment found for %s", parent_key)
    return False


# -- Convenience: extract metadata --

_STOP_WORDS = {"a", "an", "the", "of", "in", "on", "for", "and", "to", "with", "from"}


def _normalize_ascii(text: str) -> str:
    """Normalize accented characters to ASCII (e.g. Lála → Lala, Müller → Muller)."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _generate_citekey(authors: list, title: str, date: str) -> str:
    """Generate a citekey from author, title, and date.

    Format: surname_word_year (e.g. vaswani_attention_2017).
    """
    import re

    surname = "unknown"
    if authors:
        first = authors[0].strip()
        if "," in first:
            # Zotero format: "Doe, J." → surname is before comma
            raw = first.split(",")[0].strip()
        else:
            # S2/natural format: "Morgan Kindel" → surname is last word
            raw = first.rsplit(None, 1)[-1] if " " in first else first
        raw = _normalize_ascii(raw)
        surname = re.sub(r"[^a-z]", "", raw.lower()) or "unknown"

    word = "untitled"
    for w in title.split():
        cleaned = re.sub(r"[^a-z]", "", _normalize_ascii(w).lower())
        if cleaned and cleaned not in _STOP_WORDS:
            word = cleaned
            break

    # Extract 4-digit year from various formats (2024-10, 10/2024, 12 February 2026)
    year_match = re.search(r"\b(\d{4})\b", date) if date else None
    year = year_match.group(1) if year_match else ""

    parts = [p for p in [surname, word, year] if p]
    return "_".join(parts)


def extract_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    """Extract metadata from a Zotero item."""
    data = item.get("data", {})
    title = data.get("title", "Untitled")
    # Strip journal suffix added by some Zotero translators (e.g. "Title | Science")
    if " | " in title:
        title = title.rsplit(" | ", 1)[0].strip()
    # Strip ": JournalName" suffix (e.g. "Title: Neuron" from Cell/Elsevier web clipper)
    journal_name = (
        data.get("publicationTitle")
        or data.get("proceedingsTitle")
        or data.get("bookTitle")
        or ""
    )
    if journal_name and title.endswith(": " + journal_name):
        title = title[: -(len(journal_name) + 2)].strip()
    elif ": " in title:
        # Fallback: strip trailing ": Word" when Word matches the URL's domain
        # (handles broken web clipper saves like "Title: Neuron" from cell.com/neuron/)
        suffix = title.rsplit(": ", 1)[1]
        url = data.get("url", "")
        if suffix and "/" + suffix.lower() + "/" in url.lower():
            title = title.rsplit(": ", 1)[0].strip()
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

    publication_date = data.get("date", "")
    # Fallback: extract year from DOI when Zotero date is empty
    # Matches patterns like chemrxiv-2026-xxx or preprint DOIs with embedded year
    if not publication_date:
        doi = data.get("DOI", "")
        if doi:
            import re as _re
            m = _re.search(r"[/-](20[12]\d)-", doi)
            if m:
                publication_date = m.group(1)

    # Fallback: generate citekey from first author + first title word + year
    if not citekey:
        citekey = _generate_citekey(authors, title, publication_date)

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
