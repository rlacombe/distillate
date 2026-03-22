---
name: tincture
description: Tincture — deep extraction from a single paper (highlights, connections, experiment ideas)
---

# Tincture

A concentrated extract from a single paper. Read it deeply, connect it to the library, and distill actionable insights.

## Arguments

The user provides a paper identifier (index number, title, arXiv ID, or citekey).

## Steps

1. **Full details**: Call `mcp__distillate__get_paper_details` to get the complete paper record — summary, highlights, notes, metadata

2. **Context in the library**: Call `mcp__distillate__search_papers` with the paper's key topics to find related papers already in the library

3. **Cross-reference**: If related papers exist, call `mcp__distillate__synthesize_across_papers` to understand how this paper relates to what's already been read — does it confirm, contradict, or extend prior work?

4. **Experiment connections**: If experiments are active:
   - Check if the paper's techniques could improve any running experiment
   - Call `mcp__distillate__suggest_from_literature` for the most relevant experiment
   - If a strong connection exists, call `mcp__distillate__link_paper` to associate them

5. **Extract the tincture**: Synthesize a concentrated summary:
   - **Core contribution**: one sentence on what's new
   - **Key technique**: the method, described concretely enough to implement
   - **Reported results**: metrics and baselines from the paper
   - **Connections**: how it relates to other papers and active experiments
   - **Actionable ideas**: 1-3 specific things to try based on this paper

6. **Offer next steps**: promote the paper, steer an experiment with its insights, or add related papers to the library
