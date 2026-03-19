---
name: launch-experiment
description: Initialize and launch a new autonomous ML experiment
---

# Launch Experiment

Full experiment setup wizard — from directory selection to running agent.

## Steps

1. **Gather requirements**: Ask the user for:
   - Experiment directory path (or create a new one)
   - Research goal / hypothesis
   - Constraints (max sessions, budget, model preference)
   - Whether to base on a template (`list_templates`) or start fresh

2. **Initialize**: Call `init_experiment` with the path and goal
   - This scans the directory, drafts PROMPT.md with Claude, sets up hooks and tracking
   - Review the generated protocol with the user

3. **Set goals**: Call `update_goals` to set measurable success criteria
   - Pull baselines from literature if relevant (`extract_baselines`)

4. **Launch**: On user approval, call `manage_session` with action="start" to begin
   - Or use `launch_experiment` for direct launch

5. **Verify**: Call `manage_session` with action="status" to confirm the session is running

6. **Monitor**: Inform the user they can check progress anytime or use `steer_experiment` to guide the agent
