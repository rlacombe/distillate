---
name: scan-all
description: Scan all tracked experiments, discover new runs, generate insights
---

# Scan All Experiments

Perform a comprehensive scan of all tracked experiments to discover new runs and generate fresh insights.

## Steps

1. Call `list_projects` to get all tracked experiments
2. For each project with a valid path, call `scan_project` to discover new runs
3. For projects with 3+ runs, check if insights are up to date
4. Compare results across projects — identify which experiments are improving
5. Summarize findings:
   - New runs discovered (per project)
   - Best metrics achieved
   - Breakthrough insights (significant improvements)
   - Recommended next steps (continue, steer, or start new direction)
6. If any experiment has active sessions, report their status via `manage_session` with action="status"
