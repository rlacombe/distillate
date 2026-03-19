---
name: transmute
description: Transmute papers into experiments — connect literature to research directions
---

# Transmute

Turn base knowledge (papers) into gold (experiment ideas). Bridge the library to the lab.

## Arguments

The user provides a research question, experiment name, or topic area.

## Steps

1. **Search the library**: Call `mcp__distillate__search_papers` with relevant keywords
2. **Deep read**: For the top 3-5 papers, call `mcp__distillate__get_paper_details` to read highlights, notes, and key findings
3. **Cross-pollinate**: Call `mcp__distillate__synthesize_across_papers` to find common techniques, disagreements, and open questions
4. **Bridge to experiments**: If a target experiment exists, call `mcp__distillate__suggest_from_literature` for concrete steering ideas grounded in the papers
5. **Extract baselines**: Call `mcp__distillate__extract_baselines` to pull quantitative targets from the papers
6. **Propose transmutations**: Synthesize 3 concrete experiment modifications:
   - Each references the specific paper(s) that inspired it
   - Include expected impact and confidence level
   - Suggest specific hyperparameters or architectural changes
7. **Link**: Call `mcp__distillate__link_paper` to connect relevant papers to the experiment for future reference
