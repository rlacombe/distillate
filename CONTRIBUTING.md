# Contributing to Distillate

## Development Setup

```bash
git clone https://github.com/rlacombe/distillate.git
cd distillate
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[all]"
```

Copy `.env.example` to `.env` and fill in your credentials.

## Running Tests

```bash
pytest tests/ -v
```

## Project Structure

```
distillate/
  main.py              # Entry point, CLI flags, core workflow (Steps 1-2)
  config.py            # Environment-based configuration, lazy loading
  state.py             # JSON state persistence, file locking
  zotero_client.py     # Zotero API (items, tags, attachments, notes)
  remarkable_client.py # rmapi CLI wrapper (upload, download, move)
  remarkable_auth.py   # One-time reMarkable device registration
  renderer.py          # Highlight extraction (rmscene) + PDF annotation (PyMuPDF)
  obsidian.py          # Markdown notes, reading log, Dataview templates
  summarizer.py        # AI summaries via Claude (Sonnet for quality, Haiku for bulk)
  digest.py            # Email digest + suggestions via Resend
  semantic_scholar.py  # Citation data enrichment
  notify.py            # macOS notifications
```

## How It Works

**Step 1** (Zotero -> reMarkable): Poll Zotero for new papers, download PDFs, upload to reMarkable Inbox, tag as `inbox`.

**Step 2** (reMarkable -> Notes): Check reMarkable Read folder for finished papers, extract highlights from `.rm` files, render annotated PDF, generate AI summary, create note, update reading log, move to Saved.

**Config loading**: Required vars (`ZOTERO_API_KEY`, `ZOTERO_USER_ID`) are validated lazily via `config.ensure_loaded()`, called at the start of `main()`. This allows `--init` and `--register` to run without credentials.

**Optional features**: AI summaries (`anthropic`) and email (`resend`) are imported lazily with clear error messages if the packages aren't installed.

## Code Style

- Lint with `ruff check .` before submitting
- Follow existing patterns in the codebase
- Keep functions focused and files under 600 lines
- Guard optional features with `if not config.X: return None`

## Pull Requests

- One logical change per PR
- Include tests for new behavior
- Run `pytest tests/` before submitting
- Keep commit messages concise and descriptive
