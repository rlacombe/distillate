# Distillate Roadmap

A research alchemist living in your terminal. Distillate is a command and control center for autonomous research agents — from discovering, reading, and extracting knowledge from cutting-edge research papers, to orchestrating swarms of code-gen agents running autonomous ML experiments, locally or in the cloud.

Shared across CLI, desktop, and cloud.

---

## P0 — Now

Core experiment lifecycle and UX fundamentals.

- **Git commit enforcement**: Agent auto-commits code changes + results after every run, pushes to remote
- **Experiment naming**: Sequential `#001`, `#002` numbering, truncated display names
- **North star metric**: Hero display of best metric value in experiment header
- **Chart styling**: Gray dots for discards (Karpathy convention), green for keeps, orange for crashes
- **Chat resize fix**: Bottom panel resizes correctly in both directions
- **Notebook overhaul**: Structured sections (goal, insights, metrics summary), collapsible experiment cards
- **Prompt editor**: View/edit PROMPT.md from the app while experiments run
- **Notifications**: OS notifications for crashes (always), goal reached, session end, agent stuck
- **Delete experiment**: Safe deletion with double confirmation, refuses if sessions running
- **Session lifecycle**: Auto-rescan + notification when experiment session ends

## P1 — Next

Research workflow improvements.

- **Prompt templates library**: Save and reuse experiment configurations across projects
- **Live cost tracking**: Show $$ spent per session and cumulative per experiment
- **Comparison view**: Side-by-side metrics across experiments with highlighted bests
- **Effort level selection**: Let users pick Claude effort level (low/medium/high) per experiment launch

## P2 — Soon

Deeper analysis and history.

- **Auto-generated figures**: Publication-ready plots and tables for papers
- **Multi-experiment dashboard**: Overview across all active experiments
- **Branching strategy**: Git branch per experiment for clean isolation
- **Session replay**: Scroll through past sessions, see what the agent did step by step
- **Enhanced notebook**: Richer HTML notebook with interactive charts and filtering

## P3 — Later

Scale and collaboration.

- **Cloud compute dispatch**: Run experiments on GPU instances (Lambda, RunPod, etc.)
- **Collaborative experiments**: Multiple researchers on the same experiment
- **Slack/Discord integration**: Notifications and commands from chat
- **nicolas-cloud**: Hosted auth + AI proxy (Cloudflare Workers)
- **Auth flow**: Magic link email, deep link, keychain storage
- **Windows support**: Full testing and polish for Windows builds
