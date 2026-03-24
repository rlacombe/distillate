---
name: steer
description: Steer a experimentalist agent — review progress and redirect a running experiment
---

# Steer

Review what a experimentalist agent is doing and redirect it. Like an alchemist adjusting the flame under the athanor.

## Arguments

The user provides an experiment name and optionally a steering direction.

## Steps

1. **Check status**: Call `mcp__distillate__manage_session` with action="status" to see if the experimentalist agent is running

2. **Review progress**: Call `mcp__distillate__get_project_details` to see recent runs, metrics, and trajectory

3. **Read the experimentalist agent's mind**: If session history exists (Glob `~/.claude/projects/*<name>*/*.jsonl`), read the most recent session to understand:
   - What the agent is currently trying
   - What it learned from recent runs
   - Whether it's stuck in a loop or making progress

4. **Gather intelligence** (if user hasn't specified direction):
   - Check if goals are being approached or stalled
   - Call `mcp__distillate__suggest_from_literature` for paper-inspired ideas
   - Look at dead ends from prior sessions to avoid repeating them

5. **Write steering instructions**: Call `mcp__distillate__steer_experiment` with specific, actionable guidance:
   - Reference what the agent has already tried (to avoid repetition)
   - Suggest concrete next steps with parameter values
   - Set priority: what to try first vs. fallback options

6. **Confirm**: Report the steering instructions and whether the experimentalist agent picked them up (the agent reads `.distillate/steering.md` via the post_bash hook)
