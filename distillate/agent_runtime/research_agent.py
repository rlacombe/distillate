"""Research Agent — Tier 2 sub-agent for paper discovery.

Placeholder for Phase 3.5. The Research Agent handles trending search, ArXiv crawls,
Semantic Scholar lookups, and Connected Papers traversals. Returns large
filtered lists that Nicolas can present concisely.
"""

from distillate.agent_runtime.subagent import register_subagent, SubAgentContext, SubAgentResult
from typing import Callable


class ResearchAgent:
    name = "research_agent"
    label = "Research Agent"
    icon = "\uD83D\uDD0D"
    description = "Paper discovery: trending search, ArXiv, Semantic Scholar"
    tools = [
        "get_trending_papers",
        "search_hf_models",
        "search_hf_datasets",
        "discover_relevant_papers",
    ]

    async def invoke(
        self,
        prompt: str,
        context: SubAgentContext,
        emit_breadcrumb: Callable[[str], None],
    ) -> SubAgentResult:
        emit_breadcrumb("Research Agent is not yet implemented (Phase 3.5)")
        return SubAgentResult(
            summary="The Research Agent sub-agent is coming in a future update.",
            success=False,
            error="not_implemented",
        )


_research_agent = ResearchAgent()
register_subagent(_research_agent)
