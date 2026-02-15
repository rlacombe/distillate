# Backlog

## Done

- ~~Smart storage~~ — PDFs deleted from Zotero after upload. Originals in Obsidian `Inbox/`, annotated in `Read/`. Zotero free tier is sustainable.
- ~~Re-process command~~ — `--reprocess "Paper Title"` re-runs highlights + PDF rendering + AI summaries.
- ~~Richer Obsidian notes~~ — DOI, abstract, journal, publication date, URL in YAML frontmatter.
- ~~Claude summarization~~ — Impact-focused one-liner + paragraph summary + key learnings with "so what". Sonnet for quality.
- ~~Weekly email digest~~ — `--digest` via Resend with read papers, summaries, URLs.
- ~~Zotero notes sync~~ — Summary + highlights pushed to Zotero child note.
- ~~Dry run mode~~ — `--dry-run` previews without changes.
- ~~Obsidian deep links~~ — "Open in Obsidian" attachment in Zotero.
- ~~Safety improvements~~ — Stale lock, create-then-delete, try-except, per-paper saves.
- ~~Two-column highlights~~ — y-sorted merging with boundary deduplication.
- ~~AI reading log~~ — `Reading Log.md` with dates and one-sentence summaries, sorted newest-first.
- ~~Paper suggestions~~ — `--suggest` daily email with picks, auto-promoted to RM root during sync.
- ~~GitHub Actions~~ — Scheduled `--suggest`, `--digest`, `--sync-state`.
- ~~Semantic Scholar enrichment~~ — Citation counts at ingestion. `--backfill-s2` for existing papers.
- ~~Reading analytics dashboard~~ — `Reading Stats.md` Dataview note: monthly breakdown, topics, recent completions.
- ~~Monthly research themes~~ — `--themes` synthesizes a month's reading into a research narrative.
- ~~Leafed removal~~ — Never used in practice. Unified into single Read path.
- ~~Auto-promote~~ — auto-promote runs during `--sync`. Smart demotion skips papers user started reading. Suggestions use Sonnet.
- ~~Metadata sync~~ — Auto-detects Zotero metadata changes (tags, authors, DOI, etc.) on each run. Updates state.json and Obsidian note frontmatter.
- ~~Richer emails~~ — Digest: topic tag pills, highlight count, reading velocity, Obsidian deep links. Suggest: tag pills, velocity, queue health snapshot.

### Email copy refresh
Improve email templates: better copy for queue health, reading velocity, suggestion framing. Make the tone more engaging and the stats more actionable. Low priority — current emails work, just not polished.

### Cross-paper wiki-links
At note creation time, find 2-3 existing papers with overlapping topic tags and add a `## Related Reading` section with `[[wiki-links]]`. No AI needed, just tag matching against state.json.

**Why**: Builds an organic knowledge graph as you read more papers. Most useful after 10+ read papers.

---

## Dropped

- ~~Log rotation + better notifications~~ — Low impact. Current notifications work fine, logs don't grow fast enough to matter.
- ~~Read vs Leafed triage~~ — Zero papers ever used the Leafed path.
- ~~AI-generated topic tags~~ — Too noisy. Zotero's own arxiv/biorxiv categories are better.
- ~~Structured highlight categories~~ — AI classification into categories was too noisy; page-based grouping works better.
- ~~Open questions extraction~~ — Tried and dropped. Key learnings with "so what" bullet are more useful.
- ~~Collection filtering~~ — Not needed. Only use Zotero for papers going to reMarkable.
- ~~Paper comparison tables~~ — Ambitious but premature. Need more papers per topic first.
- ~~Obsidian Canvas maps~~ — Cool but low utility vs. effort.
- ~~Handwritten margin notes~~ — rmscene pen stroke extraction too complex for the payoff.
- ~~Literature review generator~~ — Dream feature, deferred indefinitely. Need 20+ papers per topic.
