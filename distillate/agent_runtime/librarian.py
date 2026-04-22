"""Librarian — Tier 2 sub-agent for paper workflows.

The Librarian handles paper-heavy operations that would blow Nicolas's
context window: highlight extraction, multi-paper summarization, library
search, reading queue management.

It processes data in its own context and returns a synthesized summary.
"""

import logging
import time
from typing import Callable

from distillate.agent_runtime.subagent import (
    SubAgentContext,
    SubAgentResult,
    register_subagent,
)
from distillate.agent_runtime.breadcrumbs import make_emitter

log = logging.getLogger(__name__)

# Tool subset the Librarian is allowed to call
LIBRARIAN_TOOLS = [
    "search_papers",
    "get_paper_details",
    "get_reading_stats",
    "get_queue",
    "get_recent_reads",
    "suggest_next_reads",
    "synthesize_across_papers",
    "run_sync",
    "refresh_metadata",
    "reprocess_paper",
    "promote_papers",
    "get_trending_papers",
    "find_paper_associations",
    "add_paper_to_zotero",
    "reading_report",
    "discover_relevant_papers",
]

SYSTEM_PROMPT = (
    "You are the Librarian, a specialist in paper workflows within the "
    "Distillate lab. You process highlights, summarize papers, manage the "
    "reading queue, and find trending research. You work in an isolated "
    "context — focus on the specific task Nicolas delegated to you, process "
    "the data efficiently, and return a concise synthesis. Do not engage in "
    "conversation — just do the work and report back."
)


class Librarian:
    """Paper workflow sub-agent."""

    name = "librarian"
    label = "Librarian"
    icon = "\U0001F4DA"  # 📚
    description = "Paper workflows: highlights, summaries, reading queue, trending search"
    tools = LIBRARIAN_TOOLS

    async def invoke(
        self,
        prompt: str,
        context: SubAgentContext,
        emit_breadcrumb: Callable[[str], None],
    ) -> SubAgentResult:
        """Execute a paper workflow task."""
        start = time.monotonic()
        emit_breadcrumb(f"Processing: {prompt[:80]}...")

        try:
            result = await self._execute(prompt, context, emit_breadcrumb)
            duration_ms = int((time.monotonic() - start) * 1000)
            emit_breadcrumb(f"Done \u2014 completed in {duration_ms / 1000:.1f}s")
            return SubAgentResult(
                summary=result.get("summary", ""),
                raw_data=result.get("data"),
                citations=result.get("citations", []),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.exception("Librarian error")
            return SubAgentResult(
                summary=f"Error: {e}",
                duration_ms=duration_ms,
                success=False,
                error=str(e),
            )

    async def _execute(
        self,
        prompt: str,
        context: SubAgentContext,
        emit_breadcrumb: Callable[[str], None],
    ) -> dict:
        """Route the prompt to the appropriate paper workflow."""
        from distillate.state import State
        from distillate.agent_core import execute_tool

        state = State()
        prompt_lower = prompt.lower()

        if "queue" in prompt_lower or "reading" in prompt_lower:
            emit_breadcrumb("Scanning reading queue...")
            result = execute_tool("get_queue", {}, state=state)
            queue = result.get("queue", [])
            if not queue:
                return {"summary": "Your reading queue is empty."}
            lines = []
            for i, doc in enumerate(queue[:10], 1):
                title = doc.get("title", "Untitled")
                lines.append(f"{i}. **{title}**")
            summary = f"You have {len(queue)} papers in your queue.\n\n" + "\n".join(lines)
            return {"summary": summary, "data": queue}

        if "trending" in prompt_lower or "discover" in prompt_lower:
            emit_breadcrumb("Searching for trending papers...")
            topic = prompt.replace("trending", "").replace("discover", "").strip()
            result = execute_tool("get_trending_papers", {"topic": topic or "machine learning"}, state=state)
            papers = result.get("papers", [])
            if not papers:
                return {"summary": "No trending papers found for that topic."}
            lines = [f"- **{p.get('title', '?')}** ({p.get('source', '?')})" for p in papers[:5]]
            return {"summary": f"Top {len(lines)} trending papers:\n\n" + "\n".join(lines), "data": papers}

        if "summarize" in prompt_lower or "highlights" in prompt_lower:
            emit_breadcrumb("Gathering highlights...")
            result = execute_tool("get_recent_reads", {"limit": 5}, state=state)
            papers = result.get("papers", [])
            if not papers:
                return {"summary": "No recent reads with highlights to summarize."}
            emit_breadcrumb(f"Processing {len(papers)} papers...")
            lines = []
            for doc in papers:
                title = doc.get("title", "Untitled")
                hl_count = doc.get("highlight_count", 0)
                lines.append(f"- **{title}**: {hl_count} highlights")
            return {
                "summary": f"Recent reads ({len(papers)} papers):\n\n" + "\n".join(lines),
                "data": papers,
            }

        if "stats" in prompt_lower or "report" in prompt_lower:
            emit_breadcrumb("Generating reading report...")
            result = execute_tool("reading_report", {}, state=state)
            return {"summary": result.get("report", "No report data available."), "data": result}

        # Fallback: general paper search
        emit_breadcrumb("Searching library...")
        result = execute_tool("search_papers", {"query": prompt}, state=state)
        papers = result.get("papers", [])
        if not papers:
            return {"summary": f"No papers found matching: {prompt}"}
        lines = [f"- **{p.get('title', '?')}**" for p in papers[:5]]
        return {"summary": f"Found {len(papers)} papers:\n\n" + "\n".join(lines), "data": papers}


# Auto-register on import
_librarian = Librarian()
register_subagent(_librarian)
