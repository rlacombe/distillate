---
name: assay
description: Assay an experiment — deep analysis of results with cross-run comparison
---

# Assay

Test the purity of what the experimentalist agent produced. Deep analysis of an experiment's results.

## Arguments

The user provides an experiment name or project ID.

## Steps

1. **Get latest data**: Call `mcp__distillate__scan_project` to ensure we have the most recent runs
2. **Full history**: Call `mcp__distillate__get_project_details` for complete run history with metrics
3. **Identify the frontier**: Find the best run for each metric, trace how the frontier evolved
4. **Cross-run comparison**: Use `mcp__distillate__compare_runs` to understand what changed between breakthrough runs
5. **Session archaeology**: If session histories exist, read them (Glob `~/.claude/projects/*<name>*/*.jsonl`) to understand *why* the agent made its choices
6. **Literature context**: Call `mcp__distillate__suggest_from_literature` to compare with published results
7. **Synthesize**:
   - Trajectory: how the metric evolved across runs (phases, plateaus, breakthroughs)
   - What worked: techniques/configs that improved results
   - What didn't: approaches that regressed or were abandoned
   - Concrete next steps with specific parameter suggestions
8. **Lab notebook**: Offer to call `mcp__distillate__get_experiment_notebook` for the full record
