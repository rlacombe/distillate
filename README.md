# Distillate

*The essence of every paper you read.* &nbsp; [distillate.dev](https://distillate.dev)

Distill research papers from Zotero through reMarkable into structured notes.

[![PyPI](https://img.shields.io/pypi/v/distillate)](https://pypi.org/project/distillate/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

```
$ distillate   # turn papers into notes!

Save to Zotero  ──>  syncs to reMarkable
                                │
                   Read & highlight on tablet
                   Just move to Read when done
                                │
                                V
               Notes + highlights + annotated PDF
```

## Quick Start

```bash
pip install distillate
distillate --init
```

The setup wizard walks you through connecting Zotero, reMarkable, and choosing where your notes go.

## What You Need

| Component | Required? | What it does |
|-----------|-----------|-------------|
| [Zotero](https://www.zotero.org/) | Yes | Paper library + browser connector for saving papers |
| [reMarkable](https://remarkable.com/) tablet | Yes | Read & highlight papers with the built-in highlighter |
| [rmapi](https://github.com/ddvk/rmapi) | Yes | CLI bridge to reMarkable Cloud |
| [Obsidian](https://obsidian.md/) vault | No | Rich note integration (wiki-links, Dataview, stats) |
| Plain folder | No | Alternative to Obsidian — just markdown notes + PDFs |
| [Anthropic API key](https://console.anthropic.com/) | No | AI-generated summaries and key learnings |
| [Resend API key](https://resend.com) | No | Email digests and paper suggestions |

## Install

### 1. Install rmapi

Distillate uses [rmapi](https://github.com/ddvk/rmapi) to talk to the reMarkable Cloud.

**macOS:**
```bash
brew install rmapi
```

**Linux:**
```bash
curl -L -o /usr/local/bin/rmapi \
  https://github.com/ddvk/rmapi/releases/latest/download/rmapi-linuxx86-64
chmod +x /usr/local/bin/rmapi
```

### 2. Install Distillate

**Basic** (notes + highlights only):
```bash
pip install distillate
```

**With AI summaries:**
```bash
pip install "distillate[ai]"
```

**With email digest:**
```bash
pip install "distillate[email]"
```

**Everything:**
```bash
pip install "distillate[all]"
```

### 3. Run the setup wizard

```bash
distillate --init
```

This walks you through:
1. Connecting your Zotero account
2. Registering your reMarkable device
3. Choosing where notes go (Obsidian vault or plain folder)
4. Optionally configuring AI summaries and email digests

<details>
<summary>Manual setup (without the wizard)</summary>

Create `~/.config/distillate/.env` (or copy [.env.example](.env.example)):

```
ZOTERO_API_KEY=your_key
ZOTERO_USER_ID=your_id
OBSIDIAN_VAULT_PATH=/path/to/vault   # or OUTPUT_PATH=/path/to/folder
ANTHROPIC_API_KEY=your_key            # optional
```

Register your reMarkable:
```bash
distillate --register
```

</details>

<details>
<summary>Development install</summary>

```bash
git clone https://github.com/rlacombe/distillate.git
cd distillate
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[all]"
pytest tests/
```

</details>

## Usage

```bash
distillate
```

### What happens each run

1. Polls Zotero for new papers added since last run
2. Downloads PDFs and uploads to reMarkable `Distillate/Inbox`
3. Tags papers `inbox` in Zotero, enriches with Semantic Scholar citation data
4. Checks reMarkable `Distillate/Read` for papers you've finished reading
5. Extracts highlighted text from the reMarkable document
6. Renders an annotated PDF with highlights overlaid on the original
7. Creates a note with metadata, highlights, and AI summary (if configured)
8. Updates the Reading Log and tags the paper `read` in Zotero
9. Moves processed documents to `Distillate/Saved` on reMarkable

On first run, the script sets a watermark at your current Zotero library version. Only papers added *after* this point will be synced. To import existing papers, use `distillate --import`.

### Commands

```bash
distillate                          # Sync Zotero -> reMarkable -> notes (default)
distillate --import                 # Import existing papers from Zotero
distillate --status                 # Show queue health and reading stats
distillate --suggest                # Get paper suggestions for your queue
distillate --digest                 # Show your reading digest
distillate --schedule               # Set up or manage automatic syncing
distillate --init                   # Run the setup wizard
```

<details>
<summary>Advanced commands</summary>

```bash
distillate --reprocess "Title"      # Re-run highlights + summary for a paper
distillate --dry-run                # Preview what would happen (no changes)
distillate --themes 2026-02         # Generate monthly research themes synthesis
distillate --backfill-s2            # Backfill Semantic Scholar data
distillate --sync-state             # Push state to a GitHub Gist
distillate --register               # Register a reMarkable device
```

</details>

### How highlights work

When you highlight text on the reMarkable using the built-in highlighter (with text recognition enabled), the text is embedded in the document's `.rm` files.

Distillate:
1. Downloads the raw document bundle via rmapi
2. Parses `.rm` files using [rmscene](https://github.com/ricklupton/rmscene) to extract highlighted text
3. Searches for that text in the original PDF using [PyMuPDF](https://pymupdf.readthedocs.io/) and adds highlight annotations
4. Saves the annotated PDF and writes highlights to the note

### AI summaries

With an Anthropic API key, each processed paper gets:

- A **one-liner** explaining why the paper matters (shown in the Reading Log)
- A **paragraph summary** of methods and findings
- **Key learnings** — 4-6 bullet points distilling the most important insights

Without an API key, papers use their abstract as a fallback.

## Scheduling

```bash
distillate --schedule
```

Sets up automatic syncing every 15 minutes. On macOS, creates a launchd agent. On Linux, shows crontab instructions.

### Manual setup

<details>
<summary>macOS (launchd)</summary>

```bash
./scripts/install-launchd.sh

# Useful commands
launchctl start com.distillate.sync        # Run sync now
tail -f ~/Library/Logs/distillate.log      # Watch logs
./scripts/uninstall-launchd.sh             # Remove schedule
```

</details>

<details>
<summary>Linux (cron)</summary>

```
*/15 * * * * /path/to/.venv/bin/distillate >> /var/log/distillate.log 2>&1
```

</details>

## Configuration

All settings live in `.env` (either `~/.config/distillate/.env` or your working directory). See [.env.example](.env.example) for the full list.

| Setting | Default | Description |
|---------|---------|-------------|
| `ZOTERO_API_KEY` | *(required)* | Zotero API key |
| `ZOTERO_USER_ID` | *(required)* | Zotero numeric user ID |
| `RM_FOLDER_PAPERS` | `Distillate` | Parent folder on reMarkable |
| `RM_FOLDER_INBOX` | `Distillate/Inbox` | Folder for unread papers |
| `RM_FOLDER_READ` | `Distillate/Read` | Move papers here when done reading |
| `RM_FOLDER_SAVED` | `Distillate/Saved` | Archive folder for processed papers |
| `OBSIDIAN_VAULT_PATH` | *(empty)* | Path to Obsidian vault |
| `OBSIDIAN_PAPERS_FOLDER` | `Distillate` | Subfolder within the vault |
| `OUTPUT_PATH` | *(empty)* | Plain folder for notes (alternative to Obsidian) |
| `ANTHROPIC_API_KEY` | *(empty)* | Anthropic API key for AI summaries |
| `CLAUDE_SMART_MODEL` | `claude-sonnet-4-5` | Model for summaries |
| `CLAUDE_FAST_MODEL` | `claude-haiku-4-5` | Model for suggestions and themes |
| `RESEND_API_KEY` | *(empty)* | Resend API key for email features |
| `DIGEST_TO` | *(empty)* | Email address for digests |

## Your workflow

1. Save a paper to Zotero using the browser connector
2. Wait for Distillate to sync (or run it manually)
3. Read and highlight on your reMarkable
4. Move the document from `Distillate/Inbox` to `Distillate/Read`
5. The next sync picks it up and creates your note

## License

MIT
