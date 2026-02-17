# Distillate

*The essence of every paper you read.* &nbsp; [distillate.dev](https://distillate.dev)

Distill research papers from Zotero through reMarkable into structured notes.

## Why Distillate?

An open-source CLI with no cloud backend. Your notes, highlights, and PDFs are plain files on your machine — markdown you can read, move, or version-control however you like. Highlights flow back to Zotero as searchable annotations. AI summaries and email digests are optional; the core workflow needs only Zotero and reMarkable.

[![PyPI](https://img.shields.io/pypi/v/distillate)](https://pypi.org/project/distillate/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

```
$ distillate   # turn papers into notes!

save to Zotero ──> auto-syncs to reMarkable
                       │
        read & highlight on tablet
        just move to Read/ when done
                       │
                       V
        auto-saves notes + highlights
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
| Text recognition (on reMarkable) | Yes | Enable in Settings for highlight extraction |
| [Better BibTeX](https://retorque.re/zotero-better-bibtex/) | No | Citekey-based file naming for Obsidian Zotero Integration compatibility |
| [Obsidian](https://obsidian.md/) vault | No | Rich note integration (Dataview, Bases, reading stats, deep links) |
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

Create `~/.config/distillate/.env` (or copy [.env.example](https://github.com/rlacombe/distillate/blob/main/.env.example)):

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
3. Tags papers `inbox` in Zotero, enriches with Semantic Scholar data (citations, publication date, venue)
4. Checks reMarkable `Distillate/Read` for papers you've finished reading
5. Extracts highlighted text from the reMarkable document
6. Renders an annotated PDF with highlights overlaid on the original
7. Writes highlights back to Zotero as searchable annotations (visible in Zotero's PDF reader)
8. Creates a note with metadata, highlights, and AI summary (if configured)
9. Updates the Reading Log and tags the paper `read` in Zotero
10. Moves processed documents to `Distillate/Saved` on reMarkable

On first run, the script sets a watermark at your current Zotero library version. Only papers added *after* this point will be synced. To import existing papers, use `distillate --import`.

### Commands

```bash
distillate                          # Sync Zotero -> reMarkable -> notes (default)
distillate --import                 # Import existing papers from Zotero
distillate --status                 # Show queue health and reading stats
distillate --list                   # List all tracked papers
distillate --suggest                # Pick papers and promote to tablet home
distillate --digest                 # Show your reading digest
distillate --schedule               # Set up or manage automatic syncing
distillate --init                   # Run the setup wizard
```

<details>
<summary>Advanced commands</summary>

```bash
distillate --remove "Title"         # Remove a paper from tracking
distillate --reprocess "Title"      # Re-extract highlights and regenerate note
distillate --dry-run                # Preview sync without making changes
distillate --backfill-highlights     # Back-propagate highlights to Zotero (last 10)
distillate --refresh-metadata       # Re-fetch metadata from Zotero + Semantic Scholar
distillate --backfill-s2            # Refresh Semantic Scholar data for all papers
distillate --sync-state             # Push state.json to a GitHub Gist
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
| `CLAUDE_SMART_MODEL` | `claude-sonnet-4-5-20250929` | Model for summaries |
| `CLAUDE_FAST_MODEL` | `claude-haiku-4-5-20251001` | Model for suggestions and key learnings |
| `RESEND_API_KEY` | *(empty)* | Resend API key for email features |
| `DIGEST_TO` | *(empty)* | Email address for digests |
| `DIGEST_FROM` | `onboarding@resend.dev` | Sender email (Resend free tier includes 1 custom domain) |
| `SYNC_HIGHLIGHTS` | `true` | Write highlights back to Zotero as annotations |
| `KEEP_ZOTERO_PDF` | `true` | Keep PDF in Zotero after upload (`false` frees storage) |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for verbose console output |
| `STATE_GIST_ID` | *(empty)* | GitHub Gist ID for cross-machine state sync |

For GitHub Actions automation, engagement scores, reprocessing, custom AI models, and more — see the [Power users guide](https://distillate.dev/power-users.html).

## Works with your tools

Distillate is designed to complement your existing workflow:

- **[Better BibTeX](https://retorque.re/zotero-better-bibtex/)** — notes and PDFs are named by citekey (e.g. `einstein_relativity_1905.md`). If Better BibTeX isn't installed, citekeys are generated automatically.
- **[Obsidian Zotero Integration](https://github.com/mgmeyers/obsidian-zotero-desktop-connector)** — Distillate appends its sections to existing notes (between `<!-- distillate:start/end -->` markers) instead of overwriting them.
- **[PDF++](https://github.com/RyotaUshio/obsidian-pdf-plus)** — annotated PDFs are stored alongside notes in `Distillate/Saved/` with citekey filenames.
- **Zotero's built-in PDF reader** — highlights sync back as native Zotero annotations, visible on desktop and mobile.

## Troubleshooting

**`rmapi: command not found`**
Install rmapi ([macOS](https://github.com/ddvk/rmapi#macos): `brew install rmapi`). If using `--schedule`, launchd has a minimal PATH — use the full path to rmapi or add it to your shell profile.

**No highlights found**
Enable "Text recognition" in your reMarkable settings (Settings > General > Text recognition). Highlights made before enabling this won't have extractable text.

**Zotero API errors (403 / 400)**
Your API key needs read/write permissions. Generate a new key at [zotero.org/settings/keys](https://www.zotero.org/settings/keys) with "Allow library access" and "Allow write access" checked.

**Paper not uploading**
Zotero must have the actual PDF stored (not just a link). Check that the paper has an "Imported" attachment, not a "Linked" one. Web-only attachments can't be synced.

**Paper stuck in inbox**
On your reMarkable, move the document from `Distillate/Inbox` to `Distillate/Read`, then run `distillate` again. The next sync picks up papers from the Read folder.

## Your workflow

1. Save a paper to Zotero using the browser connector
2. Wait for Distillate to sync (or run it manually)
3. Read and highlight on your reMarkable
4. Move the document from `Distillate/Inbox` to `Distillate/Read`
5. The next sync picks it up and creates your note

## License

MIT
