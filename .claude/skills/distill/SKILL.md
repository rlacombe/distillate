---
name: distill
description: Distill insights from a homunculus's session histories — zero API calls
---

# Distill

Extract the essence from a homunculus's work. Read its Claude Code session histories, cross-reference with runs.jsonl, and produce structured research insights.

## Arguments

The user provides an experiment name or project ID (e.g. "tiny-matmul").

## Steps

1. **Resolve the project** — call `mcp__distillate__get_project_details` to get the project path and run list.

2. **Find session histories** — Claude Code sessions are at `~/.claude/projects/` with a path-based key. Use Glob to find `~/.claude/projects/*<project-name>*/*.jsonl`. Each .jsonl file is one homunculus session.

3. **Read sessions** — for each session file (newest first, up to 10), read it and extract:
   - **Agent reasoning** — `assistant` messages with `type: "text"` blocks
   - **Thinking blocks** — `type: "thinking"` in assistant content
   - **Tool calls** — what the agent read, edited, ran
   - **Run announcements** — writes to `runs.jsonl` (status: "running", "keep", "discard")

4. **Cross-reference with runs** — match sessions to runs by timestamp overlap. For each run:
   - What hypothesis the agent was testing
   - What changes it made (from Edit/Write tool calls)
   - Why it kept or discarded the run
   - Key metrics achieved

5. **Synthesize** — across all sessions, identify:
   - **Key breakthrough**: the single most impactful discovery
   - **Lessons learned**: 3-5 actionable insights
   - **Dead ends**: approaches tried and abandoned
   - **Trajectory**: how the agent's strategy evolved

6. **Save enrichment** — call `mcp__distillate__save_enrichment` with the project name and structured insights:
   - `key_breakthrough`: the single most impactful discovery
   - `lessons_learned`: list of 3-5 actionable insights
   - `dead_ends`: list of approaches tried and abandoned
   - `trajectory`: narrative of how the strategy evolved
   - `run_insights`: dict of per-run insights (keyed by run ID)

   This writes to `.distillate/llm_enrichment.json` and the insights immediately appear in the desktop Control Panel.

7. **Report** — summarize: sessions analyzed, runs enriched, key breakthrough.

## Important

- Session files are JSONL — one JSON object per line. Use the Read tool.
- Focus on `assistant` messages — skip `user` messages (just tool results).
- Session dir path: `/Users/foo/experiments/tiny-matmul` → `~/.claude/projects/-Users-foo-experiments-tiny-matmul/`
- Don't invent insights — only report what the agent wrote. Quote its words.
- This skill makes ZERO API calls. All data comes from local session files.
