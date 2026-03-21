# Distillate

*Your research alchemist. Conjure experiments, distill insights, transmute papers into gold.* &nbsp; [distillate.dev](https://distillate.dev)

[![PyPI](https://img.shields.io/pypi/v/distillate)](https://pypi.org/project/distillate/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## What is Distillate?

Distillate is an alchemy-themed autonomous research platform powered by [Claude Code](https://claude.ai/claude-code). It combines paper reading, experiment running, and insight extraction into a single workflow — no API key needed, just a Claude Code subscription (Max or Pro).

At the center is **Nicolas**, your research alchemist (named after Nicolas Flamel). He spawns **experimentalist agents** that live in tmux sessions, running ML experiments, testing hypotheses, and reporting results. He also tends the **apothecary** — your paper library, flowing from Zotero through optional reading surfaces (reMarkable, iPad, desktop) into structured notes with highlights and AI summaries.

The core loop is simple: read papers, run experiments, see them improve on the chart, distill what you learned, and let those insights inform the next experiment. Nicolas remembers everything and connects the dots between your reading and your research. ⚗️

```
$ distillate

  ─── ⚗️  Nicolas ──────────────────────────────
  12 experiments · 42 papers read · 7 in queue
  Your research alchemist. Type /help or /quit.

> /conjure tiny-matmul --duration 30m
  🧪 Spawning experimentalist...
  Created distillate-xp-tiny-matmul
  Experimentalist spawned — 30 min budget, will report when done.

> /distill tiny-matmul
  🔬 Distilling 8 runs...
  Best: run-7 (loss 0.0023, -42% from baseline)
  Key insight: block size 64 with gradient accumulation
  outperforms larger batches on this scale.
```

## Skills

Nicolas responds to 9 skills organized across three roles:

### The Laboratory 🧪 — experiments

| Skill | Description |
|-------|-------------|
| `/survey` | Scan all experiments for new runs and breakthroughs |
| `/conjure` | Spawn an experimentalist — launch an experiment from a research question |
| `/steer` | Review and redirect a running experiment mid-session |
| `/assay` | Deep analysis of experiment results with comparisons |
| `/distill` | Extract insights from an experiment's session histories (zero API calls) |

### The Apothecary 📜 — papers

| Skill | Description |
|-------|-------------|
| `/brew` | Sync papers, process highlights, refresh the library |
| `/forage` | Discover trending papers and reading suggestions |
| `/tincture` | Deep extraction from a single paper's highlights and notes |

### The Bridge 🔬 — papers to experiments

| Skill | Description |
|-------|-------------|
| `/transmute` | Turn paper insights into experiment ideas and replications |

## Quick Start

### Install

```bash
pip install distillate
# or
uv pip install distillate
```

### Requirements

- **Claude Code** (`claude` CLI) — Distillate runs through your Claude Code subscription. No separate API key needed.
- **Zotero** — for paper management (optional if you only run experiments)

### Launch

```bash
distillate          # Start the Nicolas REPL
distillate --init   # Run the setup wizard (first time)
distillate --sync   # Classic sync-only workflow
```

Or use the [desktop app](#desktop-app) for a full IDE experience.

## Desktop App

The Distillate desktop app provides an IDE-style layout with four tabs:

- **Control Panel** — metric chart, session timer, goal tracking, experiment overview
- **Session** — live terminal attached to the running Claude Code agent
- **Results** — runs grid with research insights (key breakthrough, lessons learned, dead ends)
- **Prompt** — view and edit PROMPT.md with markdown rendering

The desktop app connects to the same backend as the CLI — everything stays in sync. [Download for macOS](https://github.com/rlacombe/distillate/releases/latest).

## How It Works

The core research loop:

1. **📜 Add papers** — Save papers to Zotero, read and highlight on any device. Nicolas extracts highlights, generates summaries, and builds your knowledge base.

2. **⚗️ Conjure experiments** — Describe a research question or point at a paper. Nicolas drafts the prompt, sets up a git repo, and spawns an autonomous experimentalist agent to run it.

3. **🔬 Distill insights** — As experiments run, Nicolas tracks every iteration with metrics, diffs, and decisions. Distill the results to see what worked, what didn't, and why.

4. **✨ Transmute findings** — Connect paper insights to experiment results. What you read informs what you try next. The cycle continues.

Every experiment lives in a git repo. Every paper lives in your Zotero library. Notes are plain markdown. There's no lock-in — Distillate enhances your existing tools.

## Configuration

All settings live in `~/.config/distillate/.env`. See [.env.example](.env.example) for the full list.

The setup wizard (`distillate --init`) walks you through connecting Zotero, choosing a reading surface, and configuring optional features.

For advanced configuration, engagement scores, scheduling, and GitHub Actions automation — see the [Power users guide](https://distillate.dev/power-users.html).

## Development

```bash
git clone https://github.com/rlacombe/distillate.git
cd distillate
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e .
pytest tests/
```

## License

MIT
