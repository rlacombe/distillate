"""Lab Notebook — append-only daily research journal.

Chronological record across all primitives (experiments, papers, agents,
coding sessions, user notes).  Each day gets a markdown file at
``KNOWLEDGE_DIR/notebook/YYYY/MM/YYYY-MM-DD.md``.

Distinct from ``notebook.py`` which generates per-experiment analytical
reports with metrics and diffs.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import os

from distillate import config

log = logging.getLogger(__name__)

# Knowledge base root — defaults to CONFIG_DIR/knowledge, overridable
_KB_DIR = Path(
    os.environ.get("DISTILLATE_KNOWLEDGE_DIR", "")
    or config.CONFIG_DIR / "knowledge"
)

NOTEBOOK_ROOT = _KB_DIR / "notebook"
PINS_PATH = NOTEBOOK_ROOT / "pins.json"


def _day_path(dt: datetime) -> Path:
    """Return the daily notebook file path for *dt*."""
    return NOTEBOOK_ROOT / str(dt.year) / f"{dt.month:02d}" / f"{dt.strftime('%Y-%m-%d')}.md"


def _slug_tag(value: str) -> str:
    """Make a string safe for use as a #hashtag — single token, no whitespace.

    Strips emoji and punctuation, lowercases, and joins words with hyphens
    so that ``"Distillate ⚗️"`` becomes ``"distillate"``.
    """
    import re
    cleaned = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    parts = cleaned.split()
    return "-".join(p.lower() for p in parts if p)


def _ensure_header(path: Path, dt: datetime) -> None:
    """Create *path* with a human-readable header if it doesn't exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"# Lab Notebook — {dt.strftime('%A, %B %-d, %Y')}\n\n"
    path.write_text(header, encoding="utf-8")


# ------------------------------------------------------------------
# Shared signal filter — used by both the in-app feed and the Obsidian
# vault regenerator. Keeps research activity; drops bookkeeping events.
# ------------------------------------------------------------------

_NOISE_PATTERNS: list[re.Pattern[str]] = [
    # Agent session lifecycle — start/stop pairs are noise; the per-session
    # run summaries ("Session wrapped: N runs …") carry the signal.
    re.compile(r"^Agent '.+' session (started|stopped)\b"),
    # Experiment launch/stop/end bookkeeping (handles both single and double
    # quotes around the experiment name, and session-end events).
    re.compile(r"^Experiment (launched|stopped) on ['\"]"),
    re.compile(r"^Experiment session ended\b"),
    # Placeholder / test paper entries: literal "Title", "T1", "T2",
    # "Tracked Paper" etc.
    re.compile(r'^Paper processed: "(Title|[Tt]\d+|Tracked Paper)"'),
    # Paper entries with no metadata at all (just `Paper processed: "X"`).
    # Real paper entries have an engagement % and word count after the title.
    re.compile(r'^Paper processed: "[^"]*"\s*$'),
]

# Entry types that never reach the vault, regardless of body content.
# run_completed is tracked in events.jsonl but not mirrored to Obsidian;
# conclude_run writes these after every run and they're too granular for the vault digest.
_VAULT_DROP_TYPES: set[str] = {"run_completed"}


def is_noise_text(text: str) -> bool:
    """Return True if *text* matches a low-signal bookkeeping pattern."""
    for pat in _NOISE_PATTERNS:
        if pat.search(text):
            return True
    return False


def is_noise_entry(entry: dict) -> bool:
    """Return True if a parsed entry dict should be hidden from feeds."""
    return is_noise_text(entry.get("text", ""))


def _is_vault_signal(entry: dict) -> bool:
    """Return True if *entry* should appear in the Obsidian vault digest."""
    if entry.get("type", "") in _VAULT_DROP_TYPES:
        return False
    text = entry.get("text", "")
    if not text.strip():
        return False
    return not is_noise_text(text)


# ------------------------------------------------------------------
# Obsidian vault — curated daily digest
# ------------------------------------------------------------------

_MANAGED_BEGIN = "<!-- distillate:managed -->"
_MANAGED_END = "<!-- /distillate:managed -->"

# Section order for the vault digest. Entries with a type not listed
# here are rendered into a trailing "Other" section.
_VAULT_SECTIONS: list[tuple[str, str]] = [
    ("note", "Notes"),
    ("paper", "Papers"),
    ("experiment", "Experiments"),
    ("session", "Sessions"),
]
_KNOWN_SECTION_TYPES = {t for t, _ in _VAULT_SECTIONS}


def _vault_day_path(dt: datetime) -> Path | None:
    """Return the vault file path for *dt*, or None if no vault configured."""
    if not config.OBSIDIAN_VAULT_PATH:
        return None
    return (
        Path(config.OBSIDIAN_VAULT_PATH)
        / config.OBSIDIAN_PAPERS_FOLDER
        / "Lab Notebook"
        / f"{dt.strftime('%Y-%m-%d')}.md"
    )


def _strip_trailing_tags(text: str) -> str:
    """Remove trailing ``#hashtags`` from an entry body for vault rendering."""
    return re.sub(r"(?:\s+#\S+)+\s*$", "", text).strip()


# Phase 2: Obsidian wikilinks -------------------------------------------------

_PAPER_TITLE_RE = re.compile(r'^(Paper processed: )"([^"]+)"')


def _wikify_entry(text: str, entry_type: str, tags: list[str]) -> str:
    """Add Obsidian wikilinks to an entry body for vault rendering.

    - Paper entries: wikify the title (``"Title"`` → ``"[[Title]]"``).
      Obsidian resolves via the ``aliases`` frontmatter on the paper note.
    - Entries with a project tag (first tag): append a ``[[Projects/…]]``
      wikilink so the vault shows a clickable cross-reference.
    """
    body = _strip_trailing_tags(text)

    if entry_type == "paper":
        body = _PAPER_TITLE_RE.sub(r'\1"[[\2]]"', body)

    if tags:
        body += f" → [[Projects/{tags[0]}]]"

    return body


def _render_vault_day(dt: datetime, entries: list[dict]) -> str:
    """Render a curated list of entries as sectioned markdown.

    Returns the *inner* managed content (no header, no markers). Empty
    string if there are no entries to show.
    """
    if not entries:
        return ""

    by_type: dict[str, list[dict]] = {}
    for e in entries:
        by_type.setdefault(e.get("type", "other"), []).append(e)

    lines: list[str] = []
    for etype, heading in _VAULT_SECTIONS:
        bucket = by_type.get(etype, [])
        if not bucket:
            continue
        lines.append(f"## {heading}")
        for e in bucket:
            body = _wikify_entry(
                e.get("text", ""), etype, e.get("tags", []),
            )
            lines.append(f"- **{e.get('time', '')}** — {body}")
        lines.append("")

    other_types = [t for t in by_type if t not in _KNOWN_SECTION_TYPES]
    if other_types:
        lines.append("## Other")
        for etype in other_types:
            for e in by_type[etype]:
                body = _wikify_entry(
                    e.get("text", ""), etype, e.get("tags", []),
                )
                lines.append(f"- **{e.get('time', '')}** — [{etype}] {body}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _merge_managed_block(
    out_path: Path, dt: datetime, managed: str
) -> str:
    """Build final file content, preserving user content outside markers.

    If *managed* is empty, collapses to an empty managed block so the
    file still contains the distillate markers and header.
    """
    header = f"# Lab Notebook — {dt.strftime('%A, %B %-d, %Y')}\n\n"
    block = f"{_MANAGED_BEGIN}\n{managed}{_MANAGED_END}\n"

    if not out_path.exists():
        return header + block

    existing = out_path.read_text(encoding="utf-8")

    if _MANAGED_BEGIN in existing and _MANAGED_END in existing:
        start = existing.index(_MANAGED_BEGIN)
        end = existing.index(_MANAGED_END) + len(_MANAGED_END)
        before = existing[:start].rstrip()
        after = existing[end:].lstrip("\n")
        prefix = before + "\n\n" if before else header
        suffix = "\n" + after if after else ""
        return prefix + block + suffix

    # Legacy file with no managed markers — the old live-mirror left
    # a flat bullet stream here. Replace with a clean managed block.
    return header + block


def regenerate_obsidian_day(date_str: str) -> Path | None:
    """Rewrite the Obsidian vault's daily lab notebook file.

    Reads the local KB source file for *date_str*, filters out low-signal
    entries, groups the survivors into sections, and writes the result
    to the vault. Preserves any user content outside the managed
    ``<!-- distillate:managed -->`` markers. No-op if no vault is
    configured or the KB has no file for that date.

    Returns the written vault path, or None.
    """
    if not config.OBSIDIAN_VAULT_PATH:
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        log.debug("regenerate_obsidian_day: bad date %r", date_str)
        return None

    kb_path = _day_path(dt)
    if not kb_path.exists():
        return None

    try:
        entries: list[dict] = []
        for line in kb_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("- **"):
                parsed = _parse_entry(line)
                if _is_vault_signal(parsed):
                    entries.append(parsed)

        managed = _render_vault_day(dt, entries)

        out_path = _vault_day_path(dt)
        if out_path is None:
            return None
        out_path.parent.mkdir(parents=True, exist_ok=True)
        final = _merge_managed_block(out_path, dt, managed)
        out_path.write_text(final, encoding="utf-8", newline="\n")
        return out_path
    except Exception:
        log.debug("Obsidian vault regeneration failed", exc_info=True)
        return None


def cleanup_legacy_notebook(*, delete_orphans: bool = True) -> dict:
    """Migrate all legacy lab notebook files in the Obsidian vault.

    Scans ``{vault}/Distillate/Lab Notebook/`` for files without
    managed markers. For each:

    - If a corresponding KB source file exists for the date, regenerate
      the vault file (filtered + sectioned).
    - If no KB source exists and *delete_orphans* is True, remove the
      vault file (it was pure live-mirror noise with no backing data).

    Returns ``{"cleaned": N, "deleted": N, "skipped": N}``.
    """
    if not config.OBSIDIAN_VAULT_PATH:
        return {"cleaned": 0, "deleted": 0, "skipped": 0}

    nb_dir = (
        Path(config.OBSIDIAN_VAULT_PATH)
        / config.OBSIDIAN_PAPERS_FOLDER
        / "Lab Notebook"
    )
    if not nb_dir.is_dir():
        return {"cleaned": 0, "deleted": 0, "skipped": 0}

    cleaned = deleted = skipped = 0
    for path in sorted(nb_dir.glob("*.md")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.md$", path.name)
        if not m:
            continue
        date_str = m.group(1)

        content = path.read_text(encoding="utf-8", errors="replace")
        if _MANAGED_BEGIN in content:
            skipped += 1
            continue  # already managed

        # Try to regenerate from KB
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            skipped += 1
            continue

        kb_path = _day_path(dt)
        if kb_path.exists():
            regenerate_obsidian_day(date_str)
            cleaned += 1
        elif delete_orphans:
            path.unlink()
            deleted += 1
            log.info("Deleted orphan vault notebook: %s", path.name)
        else:
            skipped += 1

    log.info("Vault notebook cleanup: cleaned=%d deleted=%d skipped=%d",
             cleaned, deleted, skipped)
    return {"cleaned": cleaned, "deleted": deleted, "skipped": skipped}


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def append_entry(
    entry: str,
    entry_type: str = "note",
    project: str = "",
    tags: list[str] | None = None,
    when: datetime | None = None,
) -> dict:
    """Append a timestamped entry to a daily notebook file.

    By default the entry is stamped with the current local time. Pass ``when``
    to back-date the entry — used when a coding session is wrapped up long
    after Claude's last activity, so the notebook reflects when the work
    actually happened.

    Returns ``{"success": True, "path": ..., "date": ...}``.
    """
    ts = when if when is not None else datetime.now()
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        ts = ts.astimezone().replace(tzinfo=None)
    path = _day_path(ts)
    _ensure_header(path, ts)

    line = _format_entry_line(ts.strftime("%H:%M"), entry, entry_type, project, tags)

    with open(path, "a", encoding="utf-8") as f:
        f.write(line)

    # Regenerate the Obsidian vault digest for this day if the entry
    # is vault-worthy (i.e., passes the shared signal filter). Keeps
    # the firehose local and produces one vault diff per meaningful
    # event. No-op if no vault is configured.
    if _is_vault_signal({"type": entry_type, "text": " ".join(entry.split())}):
        regenerate_obsidian_day(ts.strftime("%Y-%m-%d"))

    return {"success": True, "path": str(path), "date": ts.strftime("%Y-%m-%d")}


def _format_entry_line(
    timestamp: str,
    entry: str,
    entry_type: str = "note",
    project: str = "",
    tags: list[str] | None = None,
) -> str:
    """Format an entry as a markdown list item line."""
    entry = " ".join(entry.split())
    proj_tag = _slug_tag(project) if project else ""
    suffix = f" #{proj_tag}" if proj_tag else ""
    if tags:
        suffix += " " + " ".join(f"#{_slug_tag(t)}" for t in tags)
    return f"- **{timestamp}** — [{entry_type}] {entry}{suffix}\n"


def update_entry(
    date: str,
    time: str,
    new_text: str,
    entry_type: str = "note",
    project: str = "",
    tags: list[str] | None = None,
) -> dict:
    """Replace the entry at (date, time) in the lab notebook daily file.

    Identifies the line by exact ``- **HH:MM** —`` prefix match. If multiple
    entries share a timestamp, only the first is updated. Returns ``{"success":
    True, "path": ...}`` or an error dict.
    """
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"success": False, "error": f"Bad date: {date}"}

    path = _day_path(dt)
    if not path.exists():
        return {"success": False, "error": f"No notebook for {date}"}

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_line = _format_entry_line(time, new_text, entry_type, project, tags)

    prefix = f"- **{time}** —"
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = new_line
            path.write_text("".join(lines), encoding="utf-8")
            regenerate_obsidian_day(date)
            return {"success": True, "path": str(path), "date": date}
    return {"success": False, "error": f"Entry at {time} not found"}


def delete_entry(date: str, time: str) -> dict:
    """Remove the entry at (date, time) from the lab notebook daily file.

    Identifies by ``- **HH:MM** —`` prefix. Returns ``{"success": True}`` or
    an error dict.
    """
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"success": False, "error": f"Bad date: {date}"}

    path = _day_path(dt)
    if not path.exists():
        return {"success": False, "error": f"No notebook for {date}"}

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    prefix = f"- **{time}** —"
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            del lines[i]
            path.write_text("".join(lines), encoding="utf-8")
            regenerate_obsidian_day(date)
            return {"success": True, "path": str(path), "date": date}
    return {"success": False, "error": f"Entry at {time} not found"}


def read_recent(
    n: int = 20,
    date: str = "",
    project: str = "",
) -> dict:
    """Read recent lab notebook entries.

    If *date* is given (``YYYY-MM-DD``), read that day's file.
    Otherwise walk backwards from today collecting up to *n* entries.
    If *project* is set, keep only entries containing ``#project``.
    """
    entries: list[str] = []
    dates_covered: list[str] = []

    if date:
        # Read a specific day
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return {"entries": [], "dates_covered": [], "error": f"Bad date: {date}"}
        path = _day_path(dt)
        if path.exists():
            dates_covered.append(date)
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("- **"):
                    entries.append(line)
    else:
        # Walk backwards from today
        dt = datetime.now()
        for _ in range(30):  # look back at most 30 days
            path = _day_path(dt)
            if path.exists():
                dates_covered.append(dt.strftime("%Y-%m-%d"))
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("- **"):
                        entries.append(line)
                if len(entries) >= n:
                    break
            dt -= timedelta(days=1)

    # Filter by project if requested
    if project:
        tag = f"#{project}"
        entries = [e for e in entries if tag in e]

    # Trim to n
    entries = entries[-n:]

    return {
        "entries": entries,
        "dates_covered": dates_covered,
        "total": len(entries),
    }


def read_recent_parsed(
    n: int = 50,
    date: str = "",
    project: str = "",
) -> dict:
    """Like :func:`read_recent` but returns structured entry dicts.

    Each entry dict has keys: ``date``, ``time``, ``type``, ``text``, ``tags``.
    Entries are returned in reverse-chronological order (newest first).
    """
    entries: list[dict] = []
    dates_covered: list[str] = []

    if date:
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return {"entries": [], "dates_covered": [], "total": 0, "error": f"Bad date: {date}"}
        path = _day_path(dt)
        if path.exists():
            dates_covered.append(date)
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("- **"):
                    entry = _parse_entry(line)
                    entry["date"] = date
                    entries.append(entry)
    else:
        dt = datetime.now()
        for _ in range(30):
            path = _day_path(dt)
            if path.exists():
                date_str = dt.strftime("%Y-%m-%d")
                dates_covered.append(date_str)
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("- **"):
                        entry = _parse_entry(line)
                        entry["date"] = date_str
                        entries.append(entry)
                if len(entries) >= n:
                    break
            dt -= timedelta(days=1)

    if project:
        entries = [e for e in entries if project in e.get("tags", [])]

    # Reverse so newest entries come first
    entries = list(reversed(entries[-n:]))

    return {
        "entries": entries,
        "dates_covered": dates_covered,
        "total": len(entries),
    }


# ------------------------------------------------------------------
# Digest generation
# ------------------------------------------------------------------

def _parse_entry(line: str) -> dict:
    """Parse a notebook entry line into structured parts."""
    import re
    m = re.match(r"- \*\*(\d{2}:\d{2})\*\* — \[(\w+)\] (.+)", line)
    if not m:
        return {"time": "", "type": "unknown", "text": line, "tags": []}
    text = m.group(3)
    tags = re.findall(r"#(\S+)", text)
    return {"time": m.group(1), "type": m.group(2), "text": text, "tags": tags}


def generate_digest(days: int = 7) -> dict:
    """Generate a weekly digest from recent notebook entries.

    Groups entries by type and project, counts activity, and produces
    a structured markdown summary. Writes to ``NOTEBOOK_ROOT/digests/``.
    """
    from collections import Counter

    now = datetime.now()
    all_entries: list[dict] = []
    dates_with_entries: list[str] = []

    for i in range(days):
        dt = now - timedelta(days=i)
        path = _day_path(dt)
        if path.exists():
            date_str = dt.strftime("%Y-%m-%d")
            dates_with_entries.append(date_str)
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("- **"):
                    entry = _parse_entry(line)
                    entry["date"] = date_str
                    all_entries.append(entry)

    if not all_entries:
        return {"success": True, "digest": f"No notebook entries in the last {days} days.", "path": ""}

    type_counts = Counter(e["type"] for e in all_entries)
    project_counts: Counter = Counter()
    for e in all_entries:
        for tag in e["tags"]:
            project_counts[tag] += 1

    week_num = now.isocalendar()[1]
    start_date = (now - timedelta(days=days - 1)).strftime("%B %-d")
    end_date = now.strftime("%B %-d, %Y")
    lines = [f"# Research Digest — Week {week_num}\n"]
    lines.append(f"*{start_date} to {end_date}*\n")

    lines.append("\n## Activity")
    for entry_type, count in type_counts.most_common():
        lines.append(f"- **{count}** {entry_type} entries")
    lines.append(f"- **{len(dates_with_entries)}** active days out of {days}")

    if project_counts:
        lines.append("\n## Projects")
        for proj, count in project_counts.most_common(10):
            lines.append(f"- **{proj}**: {count} entries")

    lines.append("\n## Timeline")
    current_date = ""
    for entry in reversed(all_entries):
        if entry["date"] != current_date:
            current_date = entry["date"]
            lines.append(f"\n### {current_date}")
        lines.append(f"- **{entry['time']}** [{entry['type']}] {entry['text']}")

    digest = "\n".join(lines) + "\n"

    digest_dir = NOTEBOOK_ROOT / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_path = digest_dir / f"{now.strftime('%Y')}-W{week_num:02d}.md"
    digest_path.write_text(digest, encoding="utf-8")

    return {"success": True, "digest": digest, "path": str(digest_path)}


# ------------------------------------------------------------------
# Pins — mark any entry as a milestone/highlight
# ------------------------------------------------------------------

def pin_key(source: str, date: str, time: str, session_name: str = "") -> str:
    """Build the canonical key used to identify a pinned entry.

    Workspace session entries include the session name to disambiguate
    multiple sessions that complete in the same minute. All other sources
    use ``source:dateTtime`` which is unique in practice at minute grain.
    """
    if source == "workspace" and session_name:
        return f"workspace:{date}T{time}:{session_name}"
    return f"{source or 'notebook'}:{date}T{time}"


def load_pins() -> set[str]:
    """Load the set of pinned entry keys from disk. Empty set if no file."""
    if not PINS_PATH.exists():
        return set()
    try:
        data = json.loads(PINS_PATH.read_text(encoding="utf-8"))
        return set(data.get("pinned", []))
    except Exception:
        log.warning("Failed to load pins.json", exc_info=True)
        return set()


def save_pins(pins: set[str]) -> None:
    """Persist the pin set to disk as a sorted JSON list."""
    PINS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pinned": sorted(pins),
        "updated_at": datetime.now().isoformat(),
    }
    PINS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def toggle_pin(source: str, date: str, time: str, session_name: str = "") -> bool:
    """Flip the pin state of an entry. Returns the new pin state."""
    key = pin_key(source, date, time, session_name)
    pins = load_pins()
    if key in pins:
        pins.remove(key)
        state = False
    else:
        pins.add(key)
        state = True
    save_pins(pins)
    return state


def is_pinned(source: str, date: str, time: str, session_name: str = "") -> bool:
    """Check whether an entry is currently pinned."""
    return pin_key(source, date, time, session_name) in load_pins()
