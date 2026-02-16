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

_DATAVIEW_TEMPLATE = """\
# Papers List

```dataview
TABLE date_added as "Added", date_read as "Read", default(engagement, "-") as "Eng%", default(highlighted_pages, "-") as "Pages", default(highlight_word_count, "-") as "Words"
FROM "{folder}"
WHERE tags AND contains(tags, "read")
SORT date_read DESC
```
"""


_STATS_TEMPLATE = """\
# Reading Stats

## Monthly Breakdown

```dataview
TABLE length(rows) as "Papers", sum(map(rows, (r) => default(r.page_count, 0))) as "Pages Read", round(sum(map(rows, (r) => default(r.engagement, 0))) / length(rows)) as "Avg Eng%", sum(map(rows, (r) => default(r.highlight_word_count, 0))) as "Words Highlighted"
FROM "{folder}"
WHERE tags AND contains(tags, "read")
GROUP BY dateformat(date_read, "yyyy-MM") as "Month"
SORT rows[0].date_read DESC
```

## Top Topics

```dataview
TABLE length(rows) as "Papers", round(sum(map(rows, (r) => default(r.engagement, 0))) / length(rows)) as "Avg Eng%"
FROM "{folder}"
WHERE tags AND contains(tags, "read")
FLATTEN tags as tag
WHERE tag != "paper" AND tag != "read"
GROUP BY tag as "Topic"
SORT round(sum(map(rows, (r) => default(r.engagement, 0))) / length(rows)) DESC
LIMIT 15
```

## Most Engaged

```dataview
TABLE date_read as "Read", engagement as "Eng%", highlighted_pages as "Pages", highlight_word_count as "Words"
FROM "{folder}"
WHERE tags AND contains(tags, "read") AND engagement > 0
SORT engagement DESC
LIMIT 10
```

## Recent Completions

```dataview
TABLE date_read as "Read", default(engagement, "-") as "Eng%"
FROM "{folder}"
WHERE tags AND contains(tags, "read")
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
    """Return the Read subdirectory in the papers folder, or None if unconfigured."""
    d = _papers_dir()
    if d is None:
        return None
    rd = d / "Read"
    rd.mkdir(parents=True, exist_ok=True)
    return rd


def save_inbox_pdf(title: str, pdf_bytes: bytes) -> Optional[Path]:
    """Save an original PDF to the Inbox folder.

    Returns the path to the saved file, or None if output is unconfigured.
    """
    inbox = _inbox_dir()
    if inbox is None:
        return None

    sanitized = _sanitize_note_name(title)
    pdf_path = inbox / f"{sanitized}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    log.info("Saved PDF to Inbox: %s", pdf_path)
    return pdf_path


def delete_inbox_pdf(title: str) -> None:
    """Delete a PDF from the Inbox folder after processing."""
    inbox = _inbox_dir()
    if inbox is None:
        return

    sanitized = _sanitize_note_name(title)
    pdf_path = inbox / f"{sanitized}.pdf"
    if pdf_path.exists():
        pdf_path.unlink()
        log.info("Removed from Inbox: %s", pdf_path)


def delete_paper_note(title: str) -> None:
    """Delete an existing paper note if it exists (checks Read/ subfolder)."""
    rd = _read_dir()
    if rd is None:
        return

    sanitized = _sanitize_note_name(title)
    note_path = rd / f"{sanitized}.md"
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


def save_annotated_pdf(title: str, pdf_bytes: bytes) -> Optional[Path]:
    """Save an annotated PDF to the Read folder.

    Returns the path to the saved file, or None if output is unconfigured.
    """
    rd = _read_dir()
    if rd is None:
        return None

    sanitized = _sanitize_note_name(title)
    pdf_path = rd / f"{sanitized}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    log.info("Saved annotated PDF: %s", pdf_path)
    return pdf_path


def ensure_dataview_note() -> None:
    """Create the Dataview reading log note if it doesn't exist."""
    if not _is_obsidian():
        return
    d = _papers_dir()
    if d is None:
        return

    dataview_path = d / "Papers List.md"
    if not dataview_path.exists():
        dataview_path.write_text(
            _DATAVIEW_TEMPLATE.format(folder=config.OBSIDIAN_PAPERS_FOLDER)
        )
        log.info("Created Dataview note: %s", dataview_path)


def ensure_stats_note() -> None:
    """Create the Reading Stats dashboard note if it doesn't exist."""
    if not _is_obsidian():
        return
    d = _papers_dir()
    if d is None:
        return

    stats_path = d / "Reading Stats.md"
    if not stats_path.exists():
        stats_path.write_text(
            _STATS_TEMPLATE.format(folder=config.OBSIDIAN_PAPERS_FOLDER)
        )
        log.info("Created Reading Stats note: %s", stats_path)


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
) -> Optional[Path]:
    """Create a markdown note for a read paper in the Read subfolder.

    Returns the path to the created note, or None if output is unconfigured
    or the note already exists.
    """
    rd = _read_dir()
    if rd is None:
        return None

    sanitized = _sanitize_note_name(title)
    note_path = rd / f"{sanitized}.md"

    if note_path.exists():
        log.warning("Note already exists, skipping: %s", note_path)
        return None

    today = date_read[:10] if date_read else date.today().isoformat()

    # Build YAML frontmatter
    authors_yaml = "\n".join(f"  - {a}" for a in authors) if authors else "  - Unknown"
    all_tags = ["paper", "read"] + (topic_tags or [])
    tags_yaml = "\n".join(f"  - {t}" for t in all_tags)

    # Build highlights section
    highlights_md = _render_highlights_md(highlights)

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
        pdf_embed = f"![[{pdf_filename}]]\n\n"
    elif pdf_filename:
        pdf_yaml = f'\npdf: "{pdf_filename}"'
        pdf_embed = ""
    else:
        pdf_yaml = ""
        pdf_embed = ""

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

    content = f"""\
---
title: "{_escape_yaml(title)}"
authors:
{authors_yaml}
date_added: {date_added[:10]}
date_read: {today}
zotero: "zotero://select/library/items/{zotero_item_key}"{optional}{pdf_yaml}
tags:
{tags_yaml}
---

# {title}

{doi_link_md}{oneliner_md}{summary_md}{learnings_md}{pdf_embed}{abstract_md}## Highlights

{highlights_md}

## My Notes

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


def update_note_frontmatter(title: str, metadata: Dict[str, Any]) -> bool:
    """Update YAML frontmatter on an existing paper note.

    Replaces authors, tags, doi, journal, publication_date, url, and
    citation_count. Preserves all other fields and the entire note body.
    Returns True if updated, False if note not found.
    """
    rd = _read_dir()
    if rd is None:
        return False

    sanitized = _sanitize_note_name(title)
    note_path = rd / f"{sanitized}.md"
    if not note_path.exists():
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

    # Update authors
    authors = metadata.get("authors", [])
    if authors:
        authors_yaml = "\n".join(f"  - {a}" for a in authors)
        blocks["authors"] = f"authors:\n{authors_yaml}"

    # Update tags (preserve paper + read prefix)
    all_tags = ["paper", "read"] + (metadata.get("tags") or [])
    tags_yaml = "\n".join(f"  - {t}" for t in all_tags)
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

    new_fm = _rebuild_frontmatter(blocks)
    new_content = f"---\n{new_fm}\n---\n{body}"
    note_path.write_text(new_content)
    log.info("Updated frontmatter: %s", note_path.name)
    return True


def append_to_reading_log(
    title: str,
    summary: str,
    date_read: str = "",
) -> None:
    """Append a paper entry to the Reading Log note.

    Flat bullet list, newest first. Creates the note if needed.
    Removes ALL existing entries for the same paper to prevent duplicates.
    On reprocess, preserves the original entry date.
    """
    d = _papers_dir()
    if d is None:
        return

    log_path = d / "Reading Log.md"

    if not log_path.exists():
        log_path.write_text("# Reading Log\n\n")
        log.info("Created Reading Log: %s", log_path)

    existing = log_path.read_text()
    sanitized = _sanitize_note_name(title)

    # Build link marker and entry format based on mode
    if _is_obsidian():
        link_marker = f"[[{sanitized}|"
        link_text = f"[[{sanitized}|{title}]]"
    else:
        link_marker = sanitized
        link_text = title

    # Find existing entries and preserve the oldest date
    lines = existing.split("\n")
    existing_date = ""
    for line in lines:
        if link_marker in line and line.startswith("- "):
            existing_date = line[2:12]  # extract YYYY-MM-DD after "- "
            break

    entry_date = existing_date or (date_read[:10] if date_read else date.today().isoformat())
    bullet = f"- {entry_date} — {link_text} — {summary}"

    # Remove ALL existing entries for this paper, then add new one
    cleaned = [line for line in lines if link_marker not in line]

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
    log.info("Updated Reading Log: %s", title)


def get_obsidian_uri(title: str, subfolder: str = "Read") -> Optional[str]:
    """Return an obsidian:// URI that opens the paper note in the vault.

    Returns None if vault name is not configured.
    """
    if not config.OBSIDIAN_VAULT_NAME:
        return None

    sanitized = _sanitize_note_name(title)
    file_path = f"{config.OBSIDIAN_PAPERS_FOLDER}/{subfolder}/{sanitized}"
    return f"obsidian://open?vault={quote(config.OBSIDIAN_VAULT_NAME)}&file={quote(file_path)}"


def _themes_dir() -> Optional[Path]:
    """Return the Themes subdirectory in the papers folder, or None if unconfigured."""
    d = _papers_dir()
    if d is None:
        return None
    td = d / "Themes"
    td.mkdir(parents=True, exist_ok=True)
    return td


def create_themes_note(month: str, content: str) -> Optional[Path]:
    """Create a monthly themes note in the Themes subfolder.

    month should be like '2026-02'. Returns the path, or None if unconfigured.
    """
    td = _themes_dir()
    if td is None:
        return None

    note_path = td / f"{month}.md"

    themes_content = f"""\
---
tags:
  - themes
  - monthly-review
month: {month}
---

# Research Themes — {month}

{content}
"""
    note_path.write_text(themes_content)
    log.info("Created themes note: %s", note_path)
    return note_path


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
