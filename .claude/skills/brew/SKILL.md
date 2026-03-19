---
name: brew
description: Brew — sync papers, process highlights, refresh the library
---

# Brew

The apothecary's daily work — process raw ingredients into refined materials. Sync papers from Zotero, extract highlights, refresh metadata, update the library.

## Steps

1. **Sync**: Call `mcp__distillate__run_sync` to pull papers from Zotero/reMarkable, extract highlights, generate notes
   - This is the core pipeline: Zotero → highlights → Obsidian notes → AI summaries

2. **Refresh stale metadata**: Call `mcp__distillate__get_reading_stats` to check library health, then `mcp__distillate__refresh_metadata` for any papers missing citations or venue data

3. **Check for awaiting papers**: Call `mcp__distillate__get_queue` to see if any papers are stuck in "awaiting_pdf" or need attention

4. **Promote standouts**: Review recently processed papers. If any have high engagement (>75%), offer to `mcp__distillate__promote_papers`

5. **Report**: Summarize what was brewed:
   - Papers synced and processed
   - Metadata refreshed
   - Papers promoted
   - Any issues encountered
