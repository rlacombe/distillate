# Distillate Architecture

Distillate is an IDE for autonomous AI research. Its architecture is designed around a "Scientific Core Loop" that enables agents to perform empirical experimentation at scale.

## The Scientific Core Loop

The heart of Distillate is the iterative cycle of research:
**Experiments $\rightarrow$ Results $\rightarrow$ Research Notebook**

1.  **Experiments (Orchestration):** The orchestrator spawns one or more "Spirits" (autonomous agent instances) to work on a specific research goal. These can be run in parallel to explore different hypotheses or hyperparameters.
2.  **Autonomous Execution:** Each Spirit runs inside a `tmux` session, using an interactive CLI agent (Claude Code or Gemini CLI) as its engine. The Spirit has access to the codebase, local compute, and Distillate's internal tools via the **Model Context Protocol (MCP)**.
3.  **Result Registration (The Empirical Guardrail):**
    *   **Pre-registration:** Before starting a run, the agent is mandated to log its hypothesis and intended changes to `.distillate/runs.jsonl`. This prevents post-hoc rationalization.
    *   **Post-registration:** After execution, the agent evaluates the run against the "Topline Metric" (e.g., accuracy, loss, latency). The final results, including logs and artifacts, are appended to the run record.
4.  **Topline Improvement:** The orchestrator tracks the "Best" run globally. When running parallel experiments, Spirits can be "steered" or "branched" from the current best-performing state, ensuring the collective intelligence of the lab converges on improving the topline metric.
5.  **Learning & Notebook:** Successful runs, key insights, and automated summaries are captured and mirrored into the **Research Notebook** (and optionally an Obsidian vault). This creates a permanent, human-readable record of the scientific progress.

## Multi-Agent Parallelism

Distillate can launch parallel auto-research experiments. The orchestrator:
- Manages multiple concurrent `tmux` sessions.
- Monitors each session's progress via a specialized status-detection heuristic (spinners, prompts, and terminal bells).
- Collects results from all parallel branches into a unified project state, allowing the user to compare different agent strategies or model performances.

## System Components (Add-ons)

While the Scientific Core Loop is the primary focus, Distillate includes several integrated modules that support the research process:

- **Nicolas (Research Assistant):** A persistent, SDK-based agent thread for higher-level analysis, literature review, and project management.
- **Paper Library & Zotero Sync:** Two-way synchronization with Zotero collections, allowing agents to read, summarize, and cite relevant literature.
- **Canvas Editor:** A visual space for drafting papers, designing experiments, and collaborating with agents on complex write-ups.
- **Compute Bridge:** Integration with cloud compute providers (e.g., Modal) to offload heavy training runs while keeping the control loop local.

## Technical Flow

1.  **Electron UI:** The user interface for monitoring experiments and interacting with Nicolas.
2.  **FastAPI Backend:** The central hub that manages state (`state.json` + SQLite), spawns tmux sessions, and provides endpoints for the UI.
3.  **Tmux Multiplexer:** Provides isolation and persistence for long-running agent sessions, allowing the UI to detach and re-attach without interrupting the research.
4.  **CLI Agent Bridge:** A "Mock SDK" wrapper (`gemini_sdk.py`) or direct SDK integration (`claude_agent_sdk`) that connects the agent engine to Distillate.
5.  **MCP Server:** Exposes Distillate's internal state and tools to the agents via a standardized interface.
