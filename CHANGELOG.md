# Changelog

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
