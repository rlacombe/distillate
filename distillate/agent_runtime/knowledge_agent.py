"""Knowledge Agent — Tier 2 sub-agent for notebook + LLM-wiki coherence.

Placeholder for Phase 3.5. The Knowledge Agent keeps the Obsidian-compatible
knowledge base linted, cross-referenced, and indexed. Triggered by
hooks on file changes or on-demand from Nicolas.
"""

from distillate.agent_runtime.subagent import register_subagent, SubAgentContext, SubAgentResult
from typing import Callable


class KnowledgeAgent:
    name = "knowledge_agent"
    label = "Knowledge Agent"
    icon = "\uD83D\uDCDC"
    description = "Notebook & wiki upkeep: cross-references, indexing, linting"
    tools = [
        "read_lab_notebook",
        "append_lab_book",
        "notebook_digest",
        "get_workspace_notes",
        "save_workspace_notes",
    ]

    async def invoke(
        self,
        prompt: str,
        context: SubAgentContext,
        emit_breadcrumb: Callable[[str], None],
    ) -> SubAgentResult:
        emit_breadcrumb("Knowledge Agent is not yet implemented (Phase 3.5)")
        return SubAgentResult(
            summary="The Knowledge Agent sub-agent is coming in a future update.",
            success=False,
            error="not_implemented",
        )


_knowledge_agent = KnowledgeAgent()
register_subagent(_knowledge_agent)
