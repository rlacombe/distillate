"""Note output integration (Obsidian vault or plain folder).

Creates per-paper markdown notes with YAML frontmatter and highlights,
and maintains a simple reading log.
"""

import logging
from datetime import date
from pathlib import Path
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote

from distillate import config

log = logging.getLogger(__name__)

MARKER_START = "<!-- distillate:start -->"
MARKER_END = "<!-- distillate:end -->"

_TEMPLATE_VERSION = "4"  # bump when Stats or Bases templates change

_STATS_TEMPLATE = """\
<!-- distillate:template:{version} -->
# Distillate Stats

## Monthly Breakdown

```dataview
TABLE length(rows) as "Papers", sum(map(rows, (r) => default(r.page_count, 0))) as "Pages Read", round(sum(map(rows, (r) => default(r.engagement, 0))) / length(rows)) as "Avg Eng%", sum(map(rows, (r) => default(r.highlight_word_count, 0))) as "Words Highlighted"
FROM "{folder}/Saved"
WHERE date_read
GROUP BY dateformat(date_read, "yyyy-MM") as "Month"
SORT rows[0].date_read DESC
```

## Top Topics

```dataview
TABLE length(rows) as "Papers", round(sum(map(rows, (r) => default(r.engagement, 0))) / length(rows)) as "Avg Eng%"
FROM "{folder}/Saved"
WHERE date_read
FLATTEN tags as tag
GROUP BY tag as "Topic"
SORT round(sum(map(rows, (r) => default(r.engagement, 0))) / length(rows)) DESC
LIMIT 15
```

## Most Engaged

```dataview
TABLE date_read as "Read", engagement as "Eng%", highlighted_pages as "Pages", highlight_word_count as "Words"
FROM "{folder}/Saved"
WHERE date_read AND engagement > 0
SORT engagement DESC
LIMIT 10
```

## Recent Completions

```dataview
TABLE date_read as "Read", default(engagement, "-") as "Eng%"
FROM "{folder}/Saved"
WHERE date_read
SORT date_read DESC
LIMIT 10
```
"""


def _papers_dir() -> Optional[Path]:
    """Return the papers directory, or None if unconfigured.

    Checks OBSIDIAN_VAULT_PATH first (full Obsidian integration), then
    OUTPUT_PATH (plain folder mode — notes + PDFs without Obsidian features).
    """
    if config.OBSIDIAN_VAULT_PATH:
        d = Path(config.OBSIDIAN_VAULT_PATH) / config.OBSIDIAN_PAPERS_FOLDER
    elif config.OUTPUT_PATH:
        d = Path(config.OUTPUT_PATH)
    else:
        return None
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_obsidian() -> bool:
    """Return True if we're using an Obsidian vault (vs plain folder)."""
    return bool(config.OBSIDIAN_VAULT_PATH)


def _inbox_dir() -> Optional[Path]:
    """Return the Inbox subdirectory in the papers folder, or None if unconfigured."""
    d = _papers_dir()
    if d is None:
        return None
    inbox = d / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def _read_dir() -> Optional[Path]:
    """Return the Saved subdirectory in the papers folder, or None if unconfigured."""
    d = _papers_dir()
    if d is None:
        return None
    rd = d / "Saved"
    rd.mkdir(parents=True, exist_ok=True)
    return rd


def save_inbox_pdf(title: str, pdf_bytes: bytes, citekey: str = "") -> Optional[Path]:
    """Save an original PDF to the Inbox folder.

    Returns the path to the saved file, or None if output is unconfigured.
    """
    inbox = _inbox_dir()
    if inbox is None:
        return None

    filename = citekey if citekey else _sanitize_note_name(title)
    pdf_path = inbox / f"{filename}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    log.info("Saved PDF to Inbox: %s", pdf_path)
    return pdf_path


def delete_inbox_pdf(title: str, citekey: str = "") -> None:
    """Delete a PDF from the Inbox folder after processing."""
    inbox = _inbox_dir()
    if inbox is None:
        return

    # Try citekey-based name first, then title-based
    for name in ([citekey] if citekey else []) + [_sanitize_note_name(title)]:
        pdf_path = inbox / f"{name}.pdf"
        if pdf_path.exists():
            pdf_path.unlink()
            log.info("Removed from Inbox: %s", pdf_path)
            return


def delete_paper_note(title: str, citekey: str = "") -> None:
    """Delete an existing paper note if it exists (checks Read/ subfolder)."""
    rd = _read_dir()
    if rd is None:
        return

    filename = citekey if citekey else _sanitize_note_name(title)
    sanitized = _sanitize_note_name(title)
    note_path = rd / f"{filename}.md"
    if note_path.exists():
        note_path.unlink()
        log.info("Deleted existing note: %s", note_path)

    # Also check papers root for notes created before subfolder migration
    d = _papers_dir()
    if d is None:
        return
    legacy_path = d / f"{sanitized}.md"
    if legacy_path.exists():
        legacy_path.unlink()
        log.info("Deleted legacy note: %s", legacy_path)


def save_annotated_pdf(title: str, pdf_bytes: bytes, citekey: str = "") -> Optional[Path]:
    """Save an annotated PDF to the Read folder.

    Returns the path to the saved file, or None if output is unconfigured.
    """
    rd = _read_dir()
    if rd is None:
        return None

    filename = citekey if citekey else _sanitize_note_name(title)
    pdf_path = rd / f"{filename}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    log.info("Saved annotated PDF: %s", pdf_path)
    return pdf_path


def ensure_dataview_note() -> None:
    """Remove the legacy Papers List note (replaced by Bases)."""
    if not _is_obsidian():
        return
    d = _papers_dir()
    if d is None:
        return

    old_path = d / "Papers List.md"
    if old_path.exists():
        old_path.unlink()
        log.info("Removed legacy Papers List: %s", old_path)


def _needs_template_update(path: Path) -> bool:
    """Return True if the file is missing or has an outdated template version."""
    if not path.exists():
        return True
    return f"distillate:template:{_TEMPLATE_VERSION}" not in path.read_text()


def ensure_stats_note() -> None:
    """Create or update the Distillate Stats dashboard note."""
    if not _is_obsidian():
        return
    d = _papers_dir()
    if d is None:
        return

    # Remove legacy name
    old_path = d / "Reading Stats.md"
    if old_path.exists():
        old_path.rename(d / "Distillate Stats.md")
        log.info("Renamed Reading Stats -> Distillate Stats")

    stats_path = d / "Distillate Stats.md"
    if _needs_template_update(stats_path):
        stats_path.write_text(
            _STATS_TEMPLATE.format(
                folder=config.OBSIDIAN_PAPERS_FOLDER,
                version=_TEMPLATE_VERSION,
            )
        )
        log.info("Created Distillate Stats: %s", stats_path)


_BASES_TEMPLATE = """\
# distillate:template:{version}
filters:
  and:
    - file.inFolder("{folder}/Saved")
    - 'file.ext == "md"'
views:
  - type: table
    name: All Papers
    order:
      - file.name
      - property.date_added
      - property.date_read
      - property.engagement
      - property.highlighted_pages
      - property.highlight_word_count
      - property.page_count
    sort:
      - column: property.date_read
        direction: DESC
"""


def ensure_bases_note() -> None:
    """Create or update the Obsidian Bases .base file.

    Bases (Obsidian 1.9+) is the native replacement for Dataview.
    """
    if not _is_obsidian():
        return
    d = _papers_dir()
    if d is None:
        return

    # Remove legacy name
    old_path = d / "Papers.base"
    if old_path.exists():
        old_path.unlink()
        log.info("Removed legacy Papers.base")

    bases_path = d / "Distillate Papers.base"
    if _needs_template_update(bases_path):
        bases_path.write_text(
            _BASES_TEMPLATE.format(
                folder=config.OBSIDIAN_PAPERS_FOLDER,
                version=_TEMPLATE_VERSION,
            )
        )
        log.info("Created Bases file: %s", bases_path)


def _render_highlights_md(
    highlights: Optional[Union[List[str], Dict[int, List[str]]]],
) -> str:
    """Render highlights as markdown.

    Accepts either a flat list (legacy) or a page-based dict
    mapping page numbers to highlight lists.
    """
    if not highlights:
        return "*No highlights extracted.*"

    # Flat list — single section
    if isinstance(highlights, list):
        return "\n".join(f"- \"{h}\"" for h in highlights)

    # Page-based dict
    if len(highlights) == 1:
        # Single page — no headers needed
        items = next(iter(highlights.values()))
        return "\n".join(f"- \"{h}\"" for h in items)

    sections = []
    for page_num in sorted(highlights.keys()):
        items = highlights[page_num]
        bullet_list = "\n".join(f"- \"{h}\"" for h in items)
        sections.append(f"### Page {page_num}\n\n{bullet_list}")
    return "\n\n".join(sections)


def create_paper_note(
    title: str,
    authors: List[str],
    date_added: str,
    zotero_item_key: str,
    highlights: Optional[Union[List[str], Dict[int, List[str]]]] = None,
    pdf_filename: Optional[str] = None,
    doi: str = "",
    abstract: str = "",
    url: str = "",
    publication_date: str = "",
    journal: str = "",
    summary: str = "",
    one_liner: str = "",
    topic_tags: Optional[List[str]] = None,
    citation_count: int = 0,
    key_learnings: Optional[List[str]] = None,
    date_read: str = "",
    engagement: int = 0,
    highlighted_pages: int = 0,
    highlight_word_count: int = 0,
    page_count: int = 0,
    citekey: str = "",
) -> Optional[Path]:
    """Create a markdown note for a read paper in the Read subfolder.

    Supports three scenarios:
    1. No existing note → create full note with Distillate markers.
    2. Existing note with ``<!-- distillate:start -->`` marker (re-sync)
       → replace content between markers, preserve everything else.
    3. Existing note without marker (e.g. from Zotero Integration plugin)
       → merge Distillate frontmatter fields into existing frontmatter,
         append Distillate sections between markers.

    Always preserves the user's ``## My Notes`` section.
    Returns the path to the created note, or None if output is unconfigured.
    """
    rd = _read_dir()
    if rd is None:
        return None

    filename = citekey if citekey else _sanitize_note_name(title)
    note_path = rd / f"{filename}.md"

    today = date_read[:10] if date_read else date.today().isoformat()

    # Build highlights section
    highlights_md = _render_highlights_md(highlights)

    # One-liner blockquote at top
    oneliner_md = f"> {one_liner}\n\n" if one_liner else ""

    # Summary paragraph
    summary_md = f"{summary}\n\n" if summary else ""

    # Key ideas as bare bullet list (no header)
    if key_learnings:
        learnings_md = "\n".join(f"- {item}" for item in key_learnings) + "\n\n"
    else:
        learnings_md = ""

    # Optional abstract section
    if abstract:
        abstract_md = f"## Abstract\n\n> {abstract}\n\n"
    else:
        abstract_md = ""

    # DOI link in note body
    doi_link_md = f"[Open paper](https://doi.org/{doi})\n\n" if doi else ""

    # PDF embed
    if pdf_filename and _is_obsidian():
        pdf_embed = f"![[{pdf_filename}]]\n\n"
    else:
        pdf_embed = ""

    # Build the Distillate-specific content block (between markers)
    distillate_block = (
        f"{doi_link_md}{oneliner_md}{summary_md}{learnings_md}"
        f"{pdf_embed}{abstract_md}"
        f"## Highlights\n\n{highlights_md}\n"
    )

    _MARKER_START = MARKER_START
    _MARKER_END = MARKER_END

    # -- Scenario 2 or 3: existing note --
    if note_path.exists():
        existing = note_path.read_text()

        # Preserve user's "My Notes" section
        preserved_notes = ""
        my_notes_marker = "\n## My Notes\n"
        idx = existing.find(my_notes_marker)
        if idx >= 0:
            preserved_notes = existing[idx + len(my_notes_marker):]

        if _MARKER_START in existing:
            # Scenario 2: re-sync — replace between markers
            log.info("Re-syncing Distillate sections in: %s", note_path)
            start = existing.index(_MARKER_START)
            if _MARKER_END not in existing:
                log.warning("Missing end marker in %s — appending instead of replacing", note_path)
                end = start + len(_MARKER_START)
            else:
                end = existing.index(_MARKER_END) + len(_MARKER_END)

            new_block = (
                f"{_MARKER_START}\n\n"
                f"{distillate_block}\n"
                f"## My Notes\n\n"
                f"{_MARKER_END}"
            )
            content = existing[:start] + new_block + existing[end:]

            # Re-insert preserved notes
            if preserved_notes:
                my_notes_idx = content.find("\n## My Notes\n")
                if my_notes_idx >= 0:
                    insert_at = my_notes_idx + len("\n## My Notes\n")
                    # Find the marker end after My Notes
                    marker_end_idx = content.find(f"\n{_MARKER_END}", insert_at)
                    if marker_end_idx >= 0:
                        content = (
                            content[:insert_at]
                            + preserved_notes.rstrip("\n") + "\n\n"
                            + content[marker_end_idx:]
                        )

            # Merge Distillate frontmatter into existing
            if content.startswith("---\n"):
                try:
                    fm_end = content.index("\n---\n", 4)
                    fm_text = content[4:fm_end]
                    body = content[fm_end + 5:]
                    blocks = _parse_frontmatter_blocks(fm_text)
                    _merge_distillate_frontmatter(
                        blocks, title=title, authors=authors, date_added=date_added,
                        today=today, publication_date=publication_date, doi=doi,
                        journal=journal, url=url, citation_count=citation_count,
                        engagement=engagement, highlighted_pages=highlighted_pages,
                        highlight_word_count=highlight_word_count, page_count=page_count,
                        pdf_filename=pdf_filename, citekey=citekey,
                        zotero_item_key=zotero_item_key, topic_tags=topic_tags,
                    )
                    content = f"---\n{_rebuild_frontmatter(blocks)}\n---\n{body}"
                except ValueError:
                    pass

            note_path.write_text(content)
            log.info("Updated note: %s", note_path)
            return note_path

        else:
            # Scenario 3: external note (e.g. Zotero Integration plugin)
            log.info("Merging Distillate sections into existing note: %s", note_path)

            # Build the marker-wrapped block to append
            marker_block = (
                f"\n{_MARKER_START}\n\n"
                f"{distillate_block}\n"
                f"## My Notes\n\n"
                f"{_MARKER_END}\n"
            )
            if preserved_notes:
                marker_block = (
                    f"\n{_MARKER_START}\n\n"
                    f"{distillate_block}\n"
                    f"## My Notes\n\n"
                    f"{preserved_notes.rstrip(chr(10))}\n\n"
                    f"{_MARKER_END}\n"
                )

            # Merge frontmatter
            if existing.startswith("---\n"):
                try:
                    fm_end = existing.index("\n---\n", 4)
                    fm_text = existing[4:fm_end]
                    body = existing[fm_end + 5:]
                    blocks = _parse_frontmatter_blocks(fm_text)
                    _merge_distillate_frontmatter(
                        blocks, title=title, authors=authors, date_added=date_added,
                        today=today, publication_date=publication_date, doi=doi,
                        journal=journal, url=url, citation_count=citation_count,
                        engagement=engagement, highlighted_pages=highlighted_pages,
                        highlight_word_count=highlight_word_count, page_count=page_count,
                        pdf_filename=pdf_filename, citekey=citekey,
                        zotero_item_key=zotero_item_key, topic_tags=topic_tags,
                    )
                    content = f"---\n{_rebuild_frontmatter(blocks)}\n---\n{body}"
                except ValueError:
                    content = existing
            else:
                content = existing

            # Remove old My Notes section from body (it's now inside the marker block)
            my_notes_idx = content.find("\n## My Notes\n")
            if my_notes_idx >= 0:
                content = content[:my_notes_idx]

            content = content.rstrip("\n") + marker_block
            note_path.write_text(content)
            log.info("Merged note: %s", note_path)
            return note_path

    # -- Scenario 1: fresh note --
    # Build YAML frontmatter
    authors_yaml = "\n".join(f"  - {a}" for a in authors) if authors else "  - Unknown"
    all_tags = [_sanitize_tag(t) for t in (topic_tags or [])]
    tags_yaml = "\n".join(f"  - {t}" for t in all_tags) if all_tags else ""

    # Optional frontmatter lines
    optional = ""
    if doi:
        optional += f'\ndoi: "{_escape_yaml(doi)}"'
    if journal:
        optional += f'\njournal: "{_escape_yaml(journal)}"'
    if publication_date:
        optional += f'\npublication_date: "{publication_date}"'
    if url:
        optional += f'\nurl: "{_escape_yaml(url)}"'
    if citation_count:
        optional += f"\ncitation_count: {citation_count}"
    if engagement:
        optional += f"\nengagement: {engagement}"
    if highlighted_pages:
        optional += f"\nhighlighted_pages: {highlighted_pages}"
    if highlight_word_count:
        optional += f"\nhighlight_word_count: {highlight_word_count}"
    if page_count:
        optional += f"\npage_count: {page_count}"
    if pdf_filename and _is_obsidian():
        pdf_yaml = f'\npdf: "[[{pdf_filename}]]"'
    elif pdf_filename:
        pdf_yaml = f'\npdf: "{pdf_filename}"'
    else:
        pdf_yaml = ""

    # Citekey and year frontmatter
    citekey_yaml = f'\ncitekey: "{citekey}"' if citekey else ""
    year = publication_date[:4] if publication_date and len(publication_date) >= 4 else ""
    year_yaml = f"\nyear: {year}" if year else ""
    aliases_yaml = f'\naliases:\n  - "{_escape_yaml(title)}"' if citekey else ""

    content = f"""\
---
title: "{_escape_yaml(title)}"{citekey_yaml}
authors:
{authors_yaml}
date_added: {date_added[:10]}
date_read: {today}{year_yaml}
zotero: "zotero://select/library/items/{zotero_item_key}"{optional}{pdf_yaml}{aliases_yaml}
tags:
{tags_yaml}
---

# {title}

{_MARKER_START}

{distillate_block}
## My Notes

{_MARKER_END}
"""
    note_path.write_text(content)
    log.info("Created note: %s", note_path)
    return note_path


def _parse_frontmatter_blocks(fm_text: str) -> OrderedDict:
    """Parse frontmatter text into ordered blocks keyed by field name.

    Each value is the full text of that block (key line + any continuation lines
    like list items). Preserves original formatting for untouched fields.
    """
    blocks: OrderedDict[str, str] = OrderedDict()
    current_key: Optional[str] = None
    current_lines: list = []

    for line in fm_text.split("\n"):
        # Top-level key: starts with a non-whitespace char and contains ":"
        if line and not line[0].isspace() and ":" in line:
            if current_key is not None:
                blocks[current_key] = "\n".join(current_lines)
            current_key = line.split(":", 1)[0]
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_key is not None:
        blocks[current_key] = "\n".join(current_lines)

    return blocks


def _rebuild_frontmatter(blocks: OrderedDict) -> str:
    """Reassemble frontmatter from ordered blocks."""
    return "\n".join(blocks.values())


def _merge_distillate_frontmatter(
    blocks: OrderedDict,
    *,
    title: str,
    authors: List[str],
    date_added: str,
    today: str,
    publication_date: str,
    doi: str,
    journal: str,
    url: str,
    citation_count: int,
    engagement: int,
    highlighted_pages: int,
    highlight_word_count: int,
    page_count: int,
    pdf_filename: Optional[str],
    citekey: str,
    zotero_item_key: str,
    topic_tags: Optional[List[str]],
) -> None:
    """Merge Distillate-specific fields into existing frontmatter blocks.

    Only adds/updates fields; never removes existing fields that Distillate
    doesn't manage.
    """
    # Add Distillate fields (don't overwrite existing title/authors from plugin)
    if "title" not in blocks:
        blocks["title"] = f'title: "{_escape_yaml(title)}"'
    if citekey and "citekey" not in blocks:
        blocks["citekey"] = f'citekey: "{citekey}"'
    if "date_added" not in blocks:
        blocks["date_added"] = f"date_added: {date_added[:10]}"
    blocks["date_read"] = f"date_read: {today}"

    year = publication_date[:4] if publication_date and len(publication_date) >= 4 else ""
    if year and "year" not in blocks:
        blocks["year"] = f"year: {year}"
    blocks["zotero"] = f'zotero: "zotero://select/library/items/{zotero_item_key}"'

    if doi:
        blocks["doi"] = f'doi: "{_escape_yaml(doi)}"'
    if journal:
        blocks["journal"] = f'journal: "{_escape_yaml(journal)}"'
    if publication_date:
        blocks["publication_date"] = f'publication_date: "{publication_date}"'
    if url:
        blocks["url"] = f'url: "{_escape_yaml(url)}"'
    if citation_count:
        blocks["citation_count"] = f"citation_count: {citation_count}"
    if engagement:
        blocks["engagement"] = f"engagement: {engagement}"
    if highlighted_pages:
        blocks["highlighted_pages"] = f"highlighted_pages: {highlighted_pages}"
    if highlight_word_count:
        blocks["highlight_word_count"] = f"highlight_word_count: {highlight_word_count}"
    if page_count:
        blocks["page_count"] = f"page_count: {page_count}"
    if pdf_filename:
        if _is_obsidian():
            blocks["pdf"] = f'pdf: "[[{pdf_filename}]]"'
        else:
            blocks["pdf"] = f'pdf: "{pdf_filename}"'
    if citekey and "aliases" not in blocks:
        blocks["aliases"] = f'aliases:\n  - "{_escape_yaml(title)}"'

    # Merge tags: keep existing tags, add topic_tags (skip paper/read workflow tags)
    existing_tags: List[str] = []
    if "tags" in blocks:
        for line in blocks["tags"].split("\n")[1:]:
            tag = line.strip().lstrip("- ").strip()
            if tag and tag not in ("paper", "read"):
                existing_tags.append(tag)

    new_tags = [_sanitize_tag(t) for t in (topic_tags or [])]
    merged = list(dict.fromkeys(existing_tags + new_tags))  # dedup, preserve order
    tags_yaml = "\n".join(f"  - {t}" for t in merged)
    blocks["tags"] = f"tags:\n{tags_yaml}"


def update_note_frontmatter(title: str, metadata: Dict[str, Any], citekey: str = "") -> bool:
    """Update YAML frontmatter on an existing paper note.

    Replaces authors, tags, doi, journal, publication_date, url, and
    citation_count. Preserves all other fields and the entire note body.
    Returns True if updated, False if note not found.
    """
    rd = _read_dir()
    if rd is None:
        return False

    filename = citekey if citekey else _sanitize_note_name(title)
    note_path = rd / f"{filename}.md"
    if not note_path.exists():
        # Fall back to title-based lookup for pre-citekey notes
        if citekey:
            sanitized = _sanitize_note_name(title)
            note_path = rd / f"{sanitized}.md"
            if not note_path.exists():
                return False
        else:
            return False

    content = note_path.read_text()
    if not content.startswith("---\n"):
        return False

    try:
        end_idx = content.index("\n---\n", 4)
    except ValueError:
        return False

    fm_text = content[4:end_idx]
    body = content[end_idx + 5:]  # after "\n---\n"

    blocks = _parse_frontmatter_blocks(fm_text)

    # Update title
    new_title = metadata.get("title", "")
    if new_title:
        blocks["title"] = f'title: "{_escape_yaml(new_title)}"'

    # Update authors
    authors = metadata.get("authors", [])
    if authors:
        authors_yaml = "\n".join(f"  - {a}" for a in authors)
        blocks["authors"] = f"authors:\n{authors_yaml}"

    # Update tags (topic tags only, skip paper/read workflow tags)
    all_tags = [_sanitize_tag(t) for t in (metadata.get("tags") or [])]
    tags_yaml = "\n".join(f"  - {t}" for t in all_tags) if all_tags else ""
    blocks["tags"] = f"tags:\n{tags_yaml}"

    # Simple key-value fields — update if new value is non-empty
    for key, meta_key in [
        ("doi", "doi"),
        ("journal", "journal"),
        ("publication_date", "publication_date"),
        ("url", "url"),
    ]:
        val = metadata.get(meta_key, "")
        if val:
            blocks[key] = f'{key}: "{_escape_yaml(val)}"'

    citation_count = metadata.get("citation_count", 0)
    if citation_count:
        blocks["citation_count"] = f"citation_count: {citation_count}"

    new_ck = metadata.get("citekey", "")
    if new_ck:
        blocks["citekey"] = f'citekey: "{_escape_yaml(new_ck)}"'
        if _is_obsidian():
            blocks["pdf"] = f'pdf: "[[{_escape_yaml(new_ck)}.pdf]]"'
        else:
            blocks["pdf"] = f'pdf: "{_escape_yaml(new_ck)}.pdf"'

    new_fm = _rebuild_frontmatter(blocks)
    new_content = f"---\n{new_fm}\n---\n{body}"
    note_path.write_text(new_content)
    log.info("Updated frontmatter: %s", note_path.name)
    return True


def rename_paper(title: str, old_citekey: str, new_citekey: str) -> bool:
    """Rename note + PDF files when a paper's citekey changes.

    Also updates wikilinks in the reading log. Returns True if any file
    was renamed.
    """
    rd = _read_dir()
    if rd is None:
        return False

    # Candidates for the old filename: explicit old_citekey, then title-based
    sanitized = _sanitize_note_name(title)
    candidates = []
    if old_citekey:
        candidates.append(old_citekey)
        # Also try without the year suffix (e.g. old state had malformed key
        # but file was created with the base citekey before date was added)
        base = old_citekey.rsplit("_", 1)[0] if "_" in old_citekey else old_citekey
        if base != old_citekey:
            candidates.append(base)
    candidates.append(sanitized)

    renamed = False
    actual_old_name = None

    for ext in (".md", ".pdf"):
        dst = rd / f"{new_citekey}{ext}"
        if dst.exists():
            log.warning("Rename skip (target exists): %s", dst.name)
            continue
        # Try each candidate until we find a source file
        for candidate in candidates:
            src = rd / f"{candidate}{ext}"
            if src.exists():
                src.rename(dst)
                log.info("Renamed %s -> %s", src.name, dst.name)
                renamed = True
                if actual_old_name is None:
                    actual_old_name = candidate
                break
        else:
            log.debug("Rename skip (no source found): *%s", ext)

    # Update wikilinks in reading log
    d = _papers_dir()
    if d is not None:
        log_path = d / "Distillate Log.md"
        if log_path.exists():
            content = log_path.read_text()
            # Try all candidates for old wikilinks
            for candidate in ([actual_old_name] if actual_old_name else candidates):
                old_link = f"[[{candidate}|"
                new_link = f"[[{new_citekey}|"
                if old_link in content:
                    content = content.replace(old_link, new_link)
                    log_path.write_text(content)
                    log.info("Updated reading log links: %s -> %s", candidate, new_citekey)
                    break

    return renamed


def update_reading_log_title(old_title: str, new_title: str, citekey: str = "") -> bool:
    """Update a paper's display title in the reading log.

    Returns True if the log was modified.
    """
    d = _papers_dir()
    if d is None:
        return False

    log_path = d / "Distillate Log.md"
    if not log_path.exists():
        return False

    content = log_path.read_text()

    if _is_obsidian() and citekey:
        old_link = f"[[{citekey}|{old_title}]]"
        new_link = f"[[{citekey}|{new_title}]]"
    elif _is_obsidian():
        old_san = _sanitize_note_name(old_title)
        new_san = _sanitize_note_name(new_title)
        old_link = f"[[{old_san}|{old_title}]]"
        new_link = f"[[{new_san}|{new_title}]]"
    else:
        old_link = old_title
        new_link = new_title

    if old_link not in content:
        return False

    content = content.replace(old_link, new_link)
    log_path.write_text(content)
    log.info("Updated reading log title: %s -> %s", old_title[:40], new_title[:40])
    return True


def append_to_reading_log(
    title: str,
    summary: str,
    date_read: str = "",
    citekey: str = "",
) -> None:
    """Append a paper entry to the Reading Log note.

    Flat bullet list, newest first. Creates the note if needed.
    Removes ALL existing entries for the same paper to prevent duplicates.
    On reprocess, preserves the original entry date.
    """
    d = _papers_dir()
    if d is None:
        return

    # Rename legacy file
    old_log = d / "Reading Log.md"
    if old_log.exists():
        old_log.rename(d / "Distillate Log.md")
        log.info("Renamed Reading Log -> Distillate Log")

    log_path = d / "Distillate Log.md"

    if not log_path.exists():
        log_path.write_text("# Distillate Log\n\n")
        log.info("Created Distillate Log: %s", log_path)

    existing = log_path.read_text()
    sanitized = _sanitize_note_name(title)
    note_name = citekey if citekey else sanitized

    # Build link marker and entry format based on mode
    if _is_obsidian():
        link_marker = f"[[{note_name}|"
        link_text = f"[[{note_name}|{title}]]"
    else:
        link_marker = note_name
        link_text = title

    # Also check for old title-based entries when using citekey
    old_marker = f"[[{sanitized}|" if (citekey and _is_obsidian()) else None

    # Find existing entries and preserve the oldest date
    lines = existing.split("\n")
    existing_date = ""
    for line in lines:
        if link_marker in line and line.startswith("- "):
            existing_date = line[2:12]  # extract YYYY-MM-DD after "- "
            break
        if old_marker and old_marker in line and line.startswith("- "):
            existing_date = line[2:12]
            break

    entry_date = existing_date or (date_read[:10] if date_read else date.today().isoformat())
    bullet = f"- {entry_date} — {link_text} — {summary}"

    # Remove ALL existing entries for this paper (both old and new format)
    def _is_entry_for_paper(line: str) -> bool:
        if link_marker in line:
            return True
        if old_marker and old_marker in line:
            return True
        return False

    cleaned = [line for line in lines if not _is_entry_for_paper(line)]

    # Separate header from bullet entries
    header_lines = []
    entry_lines = []
    for line in cleaned:
        if entry_lines or (line.startswith("- ") and len(line) > 12):
            entry_lines.append(line)
        else:
            header_lines.append(line)

    entry_lines.append(bullet)

    # Sort entries by date, newest first
    entry_lines = [line for line in entry_lines if line.strip()]
    entry_lines.sort(key=lambda line: line[2:12] if line.startswith("- ") else "", reverse=True)

    header = "\n".join(header_lines).rstrip("\n") + "\n\n"
    updated = header + "\n".join(entry_lines) + "\n"
    log_path.write_text(updated)
    log.info("Updated Distillate Log: %s", title)


def get_obsidian_uri(title: str, subfolder: str = "Saved", citekey: str = "") -> Optional[str]:
    """Return an obsidian:// URI that opens the paper note in the vault.

    Returns None if vault name is not configured.
    """
    if not config.OBSIDIAN_VAULT_NAME:
        return None

    note_name = citekey if citekey else _sanitize_note_name(title)
    file_path = f"{config.OBSIDIAN_PAPERS_FOLDER}/{subfolder}/{note_name}"
    return f"obsidian://open?vault={quote(config.OBSIDIAN_VAULT_NAME)}&file={quote(file_path)}"


def _sanitize_tag(tag: str) -> str:
    """Sanitize a tag string for Obsidian compatibility.

    Obsidian tags can't contain spaces. Convert " - " separators (common
    in Zotero arXiv tags like "Computer Science - AI") to nested tag
    format, replace remaining spaces with hyphens, and lowercase.
    """
    import re
    # Replace " - " separators (with spaces on both sides) with /
    tag = re.sub(r'\s+-\s+', '/', tag)
    # Replace remaining spaces with hyphens
    tag = tag.replace(' ', '-')
    # Remove characters invalid in Obsidian tags
    tag = re.sub(r'[^\w/\-]', '', tag)
    return tag.lower()


def _sanitize_note_name(name: str) -> str:
    """Sanitize a string for use as a note filename."""
    bad_chars = '<>:"/\\|?*#^[]'
    result = name
    for c in bad_chars:
        result = result.replace(c, "")
    result = " ".join(result.split())
    return result[:200].strip()


def _escape_yaml(s: str) -> str:
    """Escape a string for use in YAML double-quoted context."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
