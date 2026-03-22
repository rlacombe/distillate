---
name: conjure
description: Conjure a homunculus — initialize and launch a new autonomous experiment
---

# Conjure a Homunculus

Summon a new experiment agent into being. From an empty flask (directory) to a living, running homunculus.

## Arguments

The user provides a research goal, and optionally a directory path, constraints, or a template name.

## Steps

1. **Gather requirements**: Ask the user for:
   - Research goal / hypothesis
   - Experiment directory path (or create a new one under EXPERIMENTS_ROOT)
   - Time budget per iteration (default: 5 minutes) — controls the MAX_SECONDS guard
   - Constraints (max sessions, hardware, model preference)
   - Whether to base on a template (`mcp__distillate__list_templates`) or start fresh

2. **Initialize**: Call `mcp__distillate__init_experiment` with the path and goal
   - This scans the directory, drafts PROMPT.md with Claude, sets up hooks and tracking
   - Review the generated protocol with the user

3. **Set goals**: Call `mcp__distillate__update_goals` to set measurable success criteria
   - Pull baselines from literature if relevant (`mcp__distillate__extract_baselines`)

4. **Launch**: On user approval, call `mcp__distillate__manage_session` with action="start"
   - The homunculus comes alive in a tmux session

5. **Verify**: Call `mcp__distillate__manage_session` with action="status" to confirm it's running

6. **Create GitHub repo**: Offer to call `mcp__distillate__create_github_repo` for public tracking
