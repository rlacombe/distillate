---
name: literature-review
description: Mine paper library for experiment ideas and techniques
---

# Literature Review

Paper-driven experiment ideation — connect what you've read to what you should try.

## Steps

1. **Search**: Call `search_papers` with keywords relevant to the target experiment or research question
2. **Deep read**: For the top 3-5 papers, call `get_paper_details` to read highlights, notes, and key findings
3. **Synthesize themes**: Call `synthesize_across_papers` with the relevant paper indices to find common techniques, disagreements, and open questions
4. **Bridge to experiments**: Call `suggest_from_literature` for the target experiment to get concrete steering ideas grounded in the papers
5. **Extract baselines**: If papers report metrics, call `extract_baselines` to pull quantitative targets
6. **Propose experiments**: Synthesize 3 concrete experiment modifications based on the literature:
   - Each should reference the specific paper(s) that inspired it
   - Include expected impact and confidence level
   - Suggest specific hyperparameters or architectural changes
