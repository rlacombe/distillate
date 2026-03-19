---
name: forage
description: Forage for ingredients — discover trending papers and reading suggestions
---

# Forage

The apothecary ventures out to gather fresh ingredients. Find new papers based on research interests, trending topics, and experiment needs.

## Arguments

Optional: a topic or research question to focus the search.

## Steps

1. **Trending papers**: Call `mcp__distillate__get_trending_papers` to see what's hot on HuggingFace Daily Papers

2. **Personalized suggestions**: Call `mcp__distillate__suggest_next_reads` for recommendations based on reading history and interests

3. **Targeted search** (if topic provided): Use `WebSearch` to find recent arXiv papers on the topic, then `mcp__distillate__add_paper_to_zotero` for the best finds

4. **Experiment connections**: If experiments are active, call `mcp__distillate__list_projects` and check if any trending papers are relevant to ongoing work. Flag connections.

5. **Curate**: Present findings grouped by relevance:
   - Directly relevant to active experiments
   - Matches research interests
   - Generally interesting / trending

6. **Add**: For papers the user wants, call `mcp__distillate__add_paper_to_zotero` with the arXiv ID or URL
