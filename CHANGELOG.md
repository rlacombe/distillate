# Changelog

## 0.7.0 — 2026-03-09

Auto-research control plane: live experiment capture, desktop lab dashboard, and Claude Code hooks.

### New Features

- **Live experiment capture**: three data sources feed into a unified experiment timeline — structured reports (`.distillate/runs.jsonl`), Claude Code hooks (passive capture), and artifact scanning (existing)
- **Structured reporting standard**: agents append one JSON line per iteration with hypothesis, results, decision (keep/discard/crash), and reasoning
- **Claude Code hooks**: `post_bash.py` captures training runs from Bash stdout, `on_stop.py` logs session boundaries — zero-effort passive tracking
- **`--install-hooks <path>`**: one-command setup copies hook config into `.claude/settings.json` and creates `.distillate/` directory
- **`--watch <path>`**: watches an experiment repo, regenerates notebooks on changes, opens in browser
- **Decision-aware notebooks**: keep/discard/crash column in timeline, agent reasoning blocks, SVG metric progression chart
- **Desktop lab tab**: live experiment timeline via SSE, sparkline chart, decision badges, banner stats
- **Desktop notifications**: new baseline alerts, stuck agent detection (5+ consecutive discards), crash alerts
- **SSE endpoint**: `GET /experiments/stream` for real-time experiment event streaming
- **Expanded `/status`**: experiment stats (total runs, kept, discarded, active sessions)
- **Autoresearch kit**: `REPORTING.md` prompt addendum, `hooks.json` config template, `/experiment` skill
- **Desktop app**: Electron shell for Nicolas — native macOS app with syntax highlighting and tool indicators
- **BYOK mode**: bring your own Anthropic API key via Settings (Cmd+,)

### CLI & Internals

- **`--report`**: reading insights dashboard — lifetime stats, weekly velocity, topic breakdown, engagement distribution, most-cited papers, top authors
- **`--export-state` / `--import-state`**: backup and restore tracked papers and reading history
- **`--verbose` / `-v`**: show INFO-level logs on the console without full DEBUG output
- **State schema versioning**: migration framework for future state.json changes (currently v1)
- **S2 TLDR fallback**: papers without AI summaries now fall back to Semantic Scholar's one-sentence TLDR before using the abstract
- **Progress bar for `--refresh-metadata`**: shows `[i/N]` progress and a summary of changes
- **Init safety**: `--init` warns when existing tracked papers are found, offers backup before re-setup
- Internal: extracted `_parse_page_ids()` (renderer, 4 call sites) and `_fetch_pdf_bytes()` (pipeline, 5 call sites) to reduce code duplication

### Improvements

- Dual-source ingestion with fingerprint-based correlation and deduplication
- Desktop tab bar (Lab / Notebook / Chat) — Lab tab auto-activates when experiments are running
- Tool indicator subtitles in the desktop UI
- `[desktop]` optional dependency group in pyproject.toml

### Migration from 0.6.x

- **No breaking changes** — all new features are additive
- **Desktop app is optional** — the CLI works exactly the same as before
- **Hooks are opt-in** — run `distillate --install-hooks <path>` to enable passive experiment capture in any repo

## 0.6.0 — 2026-02-25

### New Features

- **Read on any device**: no longer requires a reMarkable tablet — read and highlight in the Zotero app on iPad, desktop, Android, or any device. Pick your reading surface during `--init` setup. reMarkable remains fully supported.

### Improvements

- Agent: dim magenta for verbose tool output, response truncation fix
- Email digest: trending section with top 3 papers, mobile-friendly layout
- Init wizard: WebDAV configuration step, reading surface choice
- Landing page: reMarkable now optional, "any device" messaging
- Windows: `--schedule` shows Task Scheduler instructions instead of crashing
- Lazy rmscene imports: Zotero-only users don't need rmscene/rmapi installed
- WebDAV fallback: catches all HTTP errors, visible retry output, manual upload detection

### Migration from 0.5.x

- **Newsletter signup** — the init wizard (`--init`) now offers an optional email signup at the end

## 0.5.2 — 2026-02-25

### Improvements

- **HuggingFace summary fallback**: papers get a real one-liner even without a Claude API key, using HF's AI-generated summaries
- **Email trending**: default limit tightened to 3 papers

## 0.5.1 — 2026-02-23

### Bug Fix

- **WebDAV PDF downloads broken since 0.4.4**: `get_pdf_attachment()` only matched `imported_file` and `imported_url` link modes, missing WebDAV's `linked_url` attachments. Papers got stuck as "Awaiting PDF" instead of downloading.

## 0.5.0 — 2026-02-24

Interactive agent mode — distillate becomes a research assistant.

### New Features

- **Nicolas — AI research assistant**: `distillate` now launches an interactive REPL ("Nicolas") powered by Claude. Search papers, compare findings, get reading suggestions — all in natural language.
- **Add papers from the REPL**: give an arXiv ID and it's added to Zotero, enriched with metadata, and synced on next run.
- **HuggingFace Daily Papers**: trending research with GitHub repo links, AI summaries, and community votes.
- **Cross-paper synthesis**: ask Nicolas to compare or synthesize across multiple papers in your library.
- **Refresh metadata**: agent can fix metadata gaps on existing papers via Semantic Scholar.
- **Conversation memory**: sessions persist locally for cross-session context.

### Migration from 0.4.x

- **`distillate` now opens the agent REPL** — use `distillate --sync` for the previous sync-only behavior
- **Optional extras removed** — `[ai]`, `[email]`, and `[all]` install extras are gone. `pip install distillate` (or `uv tool install distillate`) now includes everything

## 0.4.4 — 2026-02-21

Paper index numbers, terminal colors, and reliability fixes.

### Features

- **Paper index numbers**: every paper gets a stable `[index]` shown in `--status`, `--digest`, `--suggest`, `--list`, and emails — use it to target papers in commands
- **Paper lookup by index, citekey, or title**: `--reprocess 3`, `--remove kindel`, `--refresh-metadata "DynaSpec"` all work
- **Single-paper refresh**: `--refresh-metadata` now accepts an optional query to refresh just one paper
- **PDF subfolder**: annotated PDFs now saved to `Saved/pdf/` (configurable via `PDF_SUBFOLDER`), keeping notes and PDFs separate — auto-migrates existing files
- **S2 author backfill**: papers with unknown authors are enriched from Semantic Scholar, with automatic citekey regeneration
- **Terminal colors**: bold bright-white titles on dark terminals, dim gray metadata lines — TTY-aware with dark/light background detection

### Bug Fixes

- **Awaiting PDF retry**: re-checks Zotero children when the stored attachment returns 404 — fixes papers where the user adds a PDF after initial import
- **Missing years in citekeys**: extracts year from DOI patterns (e.g. `chemrxiv-2026-xxx`) when Zotero has no date; uses S2 `year` field as fallback when `publicationDate` is empty
- **Title cleaning**: strips `: JournalName` suffixes from Zotero web clipper titles (e.g. "Title: Neuron" → "Title")
- **Citekey for S2 authors**: handles "First Last" name format (not just "Last, First") when generating citekeys from Semantic Scholar data
- **Author preservation**: `--refresh-metadata` no longer overwrites S2-filled authors with empty Zotero creators
- **Refresh reporting**: citekey changes for non-processed papers (queue, awaiting) are now reported instead of showing "up to date"

## 0.4.3 — 2026-02-19

### Bug Fixes

- **Email stats missing pages/words**: the main sync loop now auto-pushes state to the Gist after processing papers, so GH Actions emails have fresh reading stats (page counts, highlight word counts)

## 0.4.2 — 2026-02-19

### Improvements

- **Suggestion compute-once**: `--suggest-email` now calls Claude at most once per day — subsequent runs reuse cached suggestions (from local state or Gist) and just re-send the email. Saves API cost on retries and manual re-runs

## 0.4.1 — 2026-02-19

### Bug Fixes

- **Traceback logging on crash**: unhandled exceptions now log the full traceback to the log file / CI output, instead of only showing the one-line message
- **CI uv cache warning**: disabled uv cache in the email workflow to silence spurious "no lockfile" warnings

## 0.4.0 — 2026-02-18

Handwriting OCR, personalized summaries, and Zotero collection filtering.

### Features

- **Handwriting OCR via Claude Vision**: handwritten margin notes are transcribed using Claude Haiku — renders ink onto the PDF page for context, then sends to the Vision API for accurate OCR
- **Personalized AI summaries**: reader's handwritten margin notes are fed to the summarizer, so AI-generated insights prioritize what *you* found interesting (not just what the paper says)
- **Zotero collection filtering**: scope Distillate to a specific Zotero collection (e.g. "To Read") via `ZOTERO_COLLECTION_KEY` — only papers in that collection get picked up
- **Collection picker in init wizard**: `--init` now lists your Zotero collections and lets you pick one to scope to

### Improvements

- **Landing page restructure**: separated core features ("Built in") from optional extensions ("Plug in what you need")

### Removals

- **Apple Vision / Pillow dependencies**: handwriting OCR now uses Claude Vision instead — removed `pyobjc-framework-Vision` and `Pillow` optional dependency group

## 0.3.3 — 2026-02-18

Handwritten notes on PDFs, Windows compatibility, and Zotero WebDAV support.

### Features

- **Ink layer on PDFs**: handwritten strokes from reMarkable are now rendered onto the annotated PDF, preserving your margin notes and annotations alongside highlights
- **reMarkable Paper Pro support**: auto-detects Paper Pro's 227 DPI coordinate system and maps ink/highlights accurately (classic RM1/RM2 also supported)
- **Typed notes from reMarkable**: keyboard-typed text on the reMarkable is extracted and included in Obsidian notes under "Notes from reMarkable"
- **Zotero WebDAV fallback**: users storing attachments via WebDAV can now download PDFs automatically — configure `ZOTERO_WEBDAV_URL`, `ZOTERO_WEBDAV_USERNAME`, `ZOTERO_WEBDAV_PASSWORD`
- **Auto-promote from suggestions**: papers suggested via `--suggest` that are later added to Zotero are auto-promoted out of the suggestion pool

### Bug Fixes

- **PDF highlights invisible in Obsidian**: fixed SYNC_HIGHLIGHTS overwriting annotated PDFs with originals, making highlights disappear
- **Windows rmapi path leak**: `rmapi put` on Windows could use the full temp path as the document name — now detected and renamed automatically
- **Windows UTF-8 encoding**: all file I/O now specifies `encoding="utf-8"` and `newline="\n"` to prevent corruption on Windows (thanks @davidlukacik!)
- **Tag pills in emails**: abbreviated long category names (e.g. "Computer Science" → "CS"), smaller pill size
- **Email footer overflow**: condensed stats labels for mobile readability
- **Suggestion emails include already-read papers**: `_sync_tags()` now marks papers as processed when Zotero tag is `read`
- **Year frontmatter garbled**: dates like "8 September 2024" were parsed as "8 Se" — now extracts 4-digit year correctly from any format

### Removals

- **`--dry-run`**: removed vestigial flag that added maintenance burden without clear user value

## 0.3.1 — 2026-02-17

UX fixes, CI hardening, and documentation improvements.

### Bug Fixes

- **Unknown CLI flags detected**: `distillate --foo` now prints an error instead of silently triggering a full sync
- **TTY output for `--reprocess`, `--dry-run`, `--backfill-s2`**: these commands now print to the terminal instead of silently logging to file
- **Tag pills in suggestion emails**: topic tag pills are now actually rendered (were silently skipped)
- **Queue health on empty queue**: no longer shows "oldest: 0 days" when no papers are waiting
- **`--send-digest` feedback**: prints "digest not sent" when no papers processed recently instead of returning silently
- **Digest email mobile links**: paper titles link to web URLs (works on any device) with Obsidian as a secondary link

### Improvements

- **CI matrix**: added Python 3.11 and 3.13 (matching pyproject.toml classifiers)
- **Publish safety gate**: PyPI publish workflow now runs tests before building
- **`.env.example`**: added `SYNC_HIGHLIGHTS` and uncommented `OBSIDIAN_VAULT_NAME`
- **README config table**: added `OBSIDIAN_VAULT_NAME` setting
- **`pyproject.toml`**: added Changelog URL to project URLs

## 0.3.0 — 2026-02-17

Smart metadata enrichment from Semantic Scholar.

### Features

- **Smart metadata from Semantic Scholar**: auto-completes missing publication dates, venues, and citation counts from Semantic Scholar when Zotero data is incomplete
- **Citekey regeneration**: when Semantic Scholar fills a missing date, the citekey is regenerated (e.g. `liu_embeddings` → `liu_embeddings_2024`) and all files are renamed automatically
- **`--refresh-metadata` with S2 enrichment**: re-queries Semantic Scholar for papers missing dates or S2 data, in addition to re-fetching from Zotero

### Improvements

- **Landing page copy refresh**: new ASCII flow ending, section subtitles ("Save. Read. Highlight. Distill."), redistributed copy

## 0.2.3 — 2026-02-17

Citekey rename, metadata refresh, and naming consistency fixes.

### Features

- **Citekey rename on metadata sync**: when a paper's citekey changes in Zotero (e.g. after adding a publication date), Distillate renames note + PDF files, updates reading log wikilinks, and PATCHes Zotero linked attachments — no orphaned files or broken links
- **`--refresh-metadata`**: new command to re-extract metadata from Zotero for all tracked papers, with verbose progress output — useful for one-time migrations and fixing stale citekeys
- **Title sync**: title changes in Zotero propagate to note frontmatter and reading log entries
- **Zotero PATCH helpers**: `update_obsidian_link()` and `update_linked_attachment_path()` update existing Zotero attachments in place (safer than delete + recreate)

### Bug Fixes

- **Citekey date parsing**: `_generate_citekey()` now extracts 4-digit years from any date format (`10/2024`, `12 February 2026`, `2024-10-15`) — previously only worked with `YYYY-*` format
- **Inbox PDFs use citekey**: `save_inbox_pdf()` and `delete_inbox_pdf()` now use citekey-based filenames, consistent with Saved folder naming
- **Frontmatter title updates**: `update_note_frontmatter()` now syncs the `title`, `citekey`, and `pdf` fields
- **Obsidian Bases template v4**: fixed sort syntax and added `file.ext == "md"` filter to exclude PDFs from Base view

## 0.2.2 — 2026-02-17

Bug fixes, safety improvements, and dead code cleanup.

### Bug Fixes

- **`distillate --version`**: was stuck at "0.1.7" — now reads version from package metadata
- **`date_read` frontmatter**: papers processed in the main sync pipeline now use the actual processing date instead of defaulting to today
- **`delete_attachment("")` crash**: papers fetched from arXiv (no Zotero PDF) no longer trigger an invalid API call when `KEEP_ZOTERO_PDF=false`
- **Highlight OCR crash**: `_recover_pdf_text` no longer crashes when a highlight normalizes to an empty string (e.g. pure hyphens)
- **Double-sleep on Zotero 429**: when Zotero sends a `Retry-After` header, we no longer also apply the exponential backoff delay
- **Citekey change detection**: Better BibTeX citekey changes are now picked up by metadata sync

### Safety Improvements

- **Annotation create-before-delete**: Zotero highlight annotations are now created before deleting old ones, preventing data loss if the POST fails mid-batch
- **Missing end marker protection**: if `<!-- distillate:end -->` is accidentally deleted from a note, Distillate warns and appends instead of silently deleting everything after the start marker

### Cleanup

- **Removed dead `_themes()` code**: removed `_themes()`, `generate_monthly_themes()`, `create_themes_note()`, and `send_themes_email()` — unreachable since v0.1.7
- **Public API functions**: `zotero_client.build_note_html()` and `remarkable_client.sanitize_filename()` are now public (were private but called cross-module)
- **Removed duplicate migration calls**: `ensure_dataview_note()`, `ensure_stats_note()`, `ensure_bases_note()` no longer run inside the per-paper loop (already run at sync start)
- **Module-level marker constants**: `MARKER_START` and `MARKER_END` promoted to module-level constants in `obsidian.py`

## 0.2.1 — 2026-02-17

### Bug Fixes

- **Awaiting PDF retry**: papers stuck in `awaiting_pdf` status are now correctly retried when a PDF attachment appears in Zotero

## 0.2.0 — 2026-02-16

Zotero Round-Trip: highlights flow back from reMarkable to Zotero, citekey-based naming, and Obsidian plugin compatibility.

### Features

- **Zotero highlight back-sync**: highlights made on reMarkable are written back to Zotero as searchable annotations — visible in Zotero's built-in PDF reader and compatible with the Zotero iOS/Android apps
- **Citekey-based file naming**: notes and annotated PDFs use Better BibTeX citekeys (e.g. `einstein_relativity_1905.md`) for compatibility with the Obsidian Zotero Integration plugin ecosystem
- **Note merge for plugin coexistence**: when a note already exists (e.g. from the Zotero Integration plugin), Distillate appends its sections between `<!-- distillate:start/end -->` markers instead of overwriting
- **Obsidian Bases support**: generates a `Papers.base` file for native table views in Obsidian 1.9+ (alongside existing Dataview template)
- **`--backfill-highlights [N]`**: back-propagate highlights to Zotero for already-processed papers (processes last N, default: all)
- **`--list` command**: list all tracked papers grouped by status

### Improvements

- **Tag sanitization**: Zotero tags like `Computer Science - Artificial Intelligence` become nested Obsidian tags (`computer-science/artificial-intelligence`) instead of appearing crossed out
- **Citekey fallback**: when Better BibTeX isn't installed, generates citekeys automatically from `surname_word_year`
- **Frontmatter additions**: notes now include `citekey`, `year`, and `aliases` fields for richer Obsidian integration
- **Saved/ folder rename**: processed papers output folder changed from `Read/` to `Saved/` for clarity
- **`SYNC_HIGHLIGHTS` config toggle**: control highlight back-propagation (default: on)
- **Duplicate prevention**: Zotero annotations tagged `distillate` are cleaned up before re-sync to prevent duplicates
- **Reading log citekey links**: reading log uses `[[citekey|title]]` wikilinks for stable references

## 0.1.7 — 2026-02-16

Friction reduction, power-user documentation, and tech debt cleanup.

### Features

- **`--status` shows Read/ folder**: papers waiting in Distillate/Read/ on your reMarkable are now listed, so you can confirm a paper is ready for processing
- **Power users guide**: new standalone page at distillate.dev/power-users.html documenting GitHub Actions automation, engagement scores, reprocessing, custom AI models, storage management, debug mode, and state sync

### Improvements

- **Better no-highlights guidance**: when no highlights are found, shows a numbered checklist (text recognition, highlighter tool, `--reprocess`) instead of a one-line warning
- **Awaiting PDF explanation**: `--status` and `--list` now explain why papers are stuck and what to do about it
- **Note overwrite on re-sync**: `create_paper_note` now overwrites existing notes instead of silently skipping — no more stale notes after re-sync
- **First-run `--status` onboarding**: shows "No papers tracked yet. Run `distillate --init` to get started." when state and config are empty
- **Unified suggestion title-matching**: extracted shared `match_suggestion_to_title()` helper, replacing three duplicate implementations across `digest.py` and `main.py`
- **`extract_insights` uses Haiku**: key learnings extraction now uses the fast model (Haiku) instead of Sonnet — equivalent quality at lower cost
- **GH Actions workflow fixes**: added `DISTILLATE_CONFIG_DIR` and `OBSIDIAN_VAULT_NAME` to workflow environment

### Removed

- **`--themes` entry point disabled**: monthly research themes synthesis is removed from `--help`, CLI routing, and GH Actions workflow. The underlying code is preserved for future use when users have enough papers to make it useful.

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
