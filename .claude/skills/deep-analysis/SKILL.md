---
name: deep-analysis
description: Deep dive into experiment results with cross-run comparison and literature context
---

# Deep Analysis

Comprehensive analysis of an experiment's results with literature context.

## Steps

1. **Get latest data**: Call `scan_project` to ensure we have the most recent runs
2. **Full history**: Call `get_project_details` for complete run history with metrics
3. **Identify best run**: Find the run with the best primary metric value
4. **Cross-run comparison**: Use `compare_runs` to understand what changed between the best run and recent runs
5. **Literature context**: Call `suggest_from_literature` to find related techniques from the paper library
6. **Synthesize**: Generate a comprehensive analysis including:
   - Trajectory: how the metric has evolved across runs
   - What worked: techniques/configs that improved results
   - What didn't: approaches that regressed
   - Literature connections: how results compare to published work
   - Concrete next steps with specific parameter suggestions
7. **Lab notebook**: If the user wants a persistent record, call `get_experiment_notebook`
