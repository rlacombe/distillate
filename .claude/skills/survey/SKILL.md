---
name: survey
description: Survey the laboratory — scan all experiments for new runs and breakthroughs
---

# Survey the Laboratory

Walk through every experiment in the lab, discover new runs, and report what's changed.

## Steps

1. Call `mcp__distillate__list_projects` to get all tracked experiments
2. For each project with a valid path, call `mcp__distillate__scan_project` to discover new runs
3. For projects with active sessions, check status via `mcp__distillate__manage_session` with action="status"
4. Compare results across projects — identify which experiments are improving fastest
5. Report findings:
   - New runs discovered (per project)
   - Best metrics achieved and whether goals are met
   - Active sessions and their progress
   - Which experiments are stalled vs. making progress
6. Recommend next actions: continue promising experiments, steer stalled ones, or start fresh directions
