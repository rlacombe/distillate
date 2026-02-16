# Changelog

## 0.1.6 — 2026-02-16

First-impression hardening: make the first 5 minutes bulletproof.

### Security

- **`.env` file permissions**: config directory created with 0700, `.env` file set to 0600 after every write — API keys no longer world-readable
- **PDF delete guard**: Zotero PDF is no longer deleted when local save fails — prevents data loss when `KEEP_ZOTERO_PDF=false`

### Features

- **`--list` command**: list all tracked papers grouped by status (on_remarkable, processing, awaiting_pdf, processed)
- **`--remove "Title"` command**: remove a paper from tracking with substring match and confirmation prompt
- **`--status` queue contents**: shows individual paper titles with age in days (up to 10)

### Improvements

- **Clean terminal output**: TTY-aware logging — sync shows progress milestones (`Checking Zotero...`, `Uploading: "Title"`, `Extracting highlights... 14 found`, `Done: 2 sent, 1 synced`) instead of raw log lines; full logs go to `~/.config/distillate/distillate.log`
- **Claude data disclosure**: init wizard Step 5 now mentions that highlights and abstracts are sent to the Claude API
- **Text recognition prerequisite**: init wizard Step 2 and README mention enabling text recognition on reMarkable
- **Intermediate state save**: Step 2 saves `processing` status after Zotero tag change, resumes on restart — prevents papers stuck in limbo after crashes
- **"My Notes" section**: Obsidian/markdown notes now include a `## My Notes` section at the end
- **DOI link in notes**: papers with a DOI get an "Open paper" link at the top of the note
- **`_sync_state` timeout**: Gist sync now times out after 30 seconds instead of hanging indefinitely
- **PDF download logging**: failed arXiv/biorxiv downloads now log a warning instead of silently failing
- **Expanded `--help`**: commands grouped by category (core, management, advanced) with descriptions
- **Local-first positioning**: landing page and README now emphasize that notes stay on your machine
- **Troubleshooting guide**: README section covering common issues (rmapi not found, empty highlights, API errors)
- **Resend custom domain**: init wizard mentions free tier includes 1 custom domain
- **Config table**: README now documents `DIGEST_FROM`, `KEEP_ZOTERO_PDF`, `LOG_LEVEL`, `STATE_GIST_ID`

## 0.1.5 — 2026-02-16

### Features

- **`--suggest` polish**: structured terminal output matching `--digest` style — per-paper blocks with title, reason, days in queue, and citation count
- **First-run guidance**: helpful message on first sync explaining watermark and pointing to `--import`
- **Missing API key UX**: `--suggest` without Anthropic key now shows a clear message instead of silently failing

### Bug fixes

- **Suggest failure no longer demotes**: Claude API errors no longer remove previously promoted papers from your reMarkable
- **`--schedule` works for pip installs**: plist generation is now inline Python instead of shelling out to a bundled script
- **OG URL**: fixed `og:url` meta tag to point to distillate.dev instead of GitHub Pages

### Improvements

- **Empty highlights warning**: prints "Is text recognition enabled on your reMarkable?" when no highlights are found
- **Import progress**: shows per-paper progress (`[3/47] Uploading: Paper Title...`) and separates papers awaiting PDF in final count
- **`--status` config clarity**: missing optional features labeled as "Optional" instead of appearing as issues
- **`--status` empty queue hint**: suggests `--import` when queue is empty
- **Init Step 5 skip hint**: makes it clear optional features can be skipped and configured later via `--init`
- **Init Step 3 fix**: removed stale wiki-links claim, fixed Obsidian vault path navigation instructions
- **Init Step 5 DIGEST_FROM**: mentions custom sender domain option when Resend is configured
- **Register output formatting**: consistent indentation with the rest of the wizard
- **Top-level error handler**: unhandled exceptions show a clean message with a link to report issues
- **README model IDs**: synced config table with actual default model identifiers

## 0.1.4 — 2026-02-15

### Features

- **`--import` command**: import existing papers from your Zotero library (interactive selection or `--import all`)
- **`--schedule` command**: set up, check, or remove automatic syncing (launchd on macOS, cron instructions on Linux)
- **Init seed**: setup wizard now offers to import existing papers at the end

### Improvements

- **`_upload_paper()` helper**: extracted reusable per-paper upload logic from sync loop
- **Command order**: commands now follow workflow lifecycle across `--help`, landing page, and README
- **ASCII flow**: concrete outputs ("Notes + highlights + annotated PDF") instead of vague ending

## 0.1.3 — 2026-02-15

### Bug fixes

- **Title propagation**: changing a paper title in Zotero now updates `--status` and promoted papers list
- **`stat_document()` false negatives**: papers stuck in promoted list because empty rmapi stat output was treated as failure
- **Corrupt state recovery**: corrupted `state.json` is backed up and reset instead of crashing
- **rmapi timeout handling**: network timeouts now show a clean error instead of an unhandled exception

### Improvements

- **`--status` promoted list**: show last 3 promoted papers, one per line (was all on one unreadable line)
- **ASCII flow refresh**: `$ distillate   # turn papers into notes!` shell-comment style

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
