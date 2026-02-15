# Changelog

## 0.1.2 — 2026-02-15

### Features

- **Cross-page highlight merging**: highlights that span page breaks are now joined into a single passage
- **Citation data surfacing**: Semantic Scholar citation counts shown in digest emails, suggestion prompts, and `--status` output
- **Richer `--status`**: config warnings, awaiting PDF titles, pending promotions
- **Smart `--init` re-run**: detects existing config and offers shortcut to optional features; shows existing values as defaults

### Reliability

- **Zotero API retry logic**: exponential backoff on 5xx, 429, and connection errors
- **Friendly error messages**: connection failures and auth errors show human-readable messages instead of stack traces
- **Config validation**: warnings for missing directories, malformed API keys
- **Duplicate detection**: skip papers already tracked by DOI or title
- **Item type filtering**: skip non-paper items (books, webpages, patents, etc.)

### Landing page

- Palatino serif typography, new tagline, FileHeart favicon
- Integration wordmarks with brand colors, Semantic Scholar card
- Open Graph and Twitter Card meta tags, colophon

## 0.1.1 — 2026-02-14

### Features

- **`--status` command**: show queue health, reading stats, and config summary
- **`--promote` cleanup**: demote old picks before promoting new ones
- **rmapi auth detection**: detect expired tokens and prompt re-registration
- **Suggest-then-promote flow**: GitHub Actions picks papers, local `--sync` promotes them on reMarkable
- **Engagement scores**: quantify reading engagement from highlight density, coverage, and volume
- **Email redesign**: lead with content, stats as footer, unified styling

## 0.1.0 — 2026-02-14

Initial public release.

### Features

- **Zotero to reMarkable sync**: automatically upload new papers from Zotero to reMarkable
- **Highlight extraction**: parse highlighted text from reMarkable `.rm` files via rmscene
- **Annotated PDFs**: overlay highlights on the original PDF using PyMuPDF text search
- **Markdown notes**: generate structured notes with metadata, highlights grouped by page, and optional AI summary
- **Reading log**: auto-updated log of all read papers, sorted by date
- **AI summaries**: one-liner, paragraph summary, and key learnings via Claude (optional, requires Anthropic API key)
- **Paper suggestions**: AI-powered daily reading suggestions from your queue
- **Email digest**: weekly reading digest and monthly research themes via Resend (optional)
- **Engagement scores**: quantify reading engagement from highlight density, coverage, and volume
- **Semantic Scholar enrichment**: citation counts and metadata via S2 API
- **Obsidian integration**: wiki-links, Dataview templates, reading stats
- **Plain folder mode**: alternative to Obsidian — just markdown notes and PDFs
- **Setup wizard**: interactive `distillate init` for first-time setup
- **Scheduling**: launchd (macOS) and cron (Linux) support for automatic syncing
