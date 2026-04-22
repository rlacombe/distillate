"""Sub-agent abstraction — Tier 2 in-process delegates.

A sub-agent is Nicolas's colleague: it gets its own context window, system
prompt, and tool subset. It processes work that would blow Nicolas's context
(e.g. scanning 50 highlights) and returns a synthesized result.

The protocol:
    1. Nicolas decides to delegate (triggered by user request or auto-routing).
    2. A SubAgent is instantiated with its system prompt + tool subset.
    3. invoke() runs a small agent loop in an isolated context.
    4. Breadcrumbs are emitted as it works ("⚗️ reading highlights...").
    5. A SubAgentResult is returned to Nicolas for formatting.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable

log = logging.getLogger(__name__)


@dataclass
class SubAgentContext:
    """Isolated context for a sub-agent invocation."""
    parent_session_id: str = ""
    experiment_id: Optional[str] = None
    user_intent: str = ""       # What Nicolas asked (the delegation prompt)
    max_tokens: int = 4096      # Response budget
    model: str = ""             # Override model (empty = use default)


@dataclass
class SubAgentResult:
    """The result of a sub-agent invocation."""
    summary: str = ""           # Synthesized text for Nicolas to read back
    raw_data: Optional[Any] = None
    citations: list[str] = field(default_factory=list)
    duration_ms: int = 0
    success: bool = True
    error: Optional[str] = None


@runtime_checkable
class SubAgent(Protocol):
    """Protocol for Tier 2 sub-agents."""

    name: str           # e.g. "librarian"
    label: str          # e.g. "Librarian"
    icon: str           # e.g. "📚"
    description: str    # For the Capabilities panel
    tools: list[str]    # MCP tool subset this sub-agent can call

    async def invoke(
        self,
        prompt: str,
        context: SubAgentContext,
        emit_breadcrumb: Callable[[str], None],
    ) -> SubAgentResult:
        """Run the sub-agent's work loop and return a result."""
        ...


# ---------------------------------------------------------------------------
# Registry of available sub-agents
# ---------------------------------------------------------------------------

_SUBAGENTS: dict[str, SubAgent] = {}


def register_subagent(agent: SubAgent) -> None:
    """Register a sub-agent in the global registry."""
    _SUBAGENTS[agent.name] = agent


def get_subagent(name: str) -> Optional[SubAgent]:
    """Look up a registered sub-agent by name."""
    return _SUBAGENTS.get(name)


def list_subagents() -> list[dict]:
    """Return all registered sub-agents as dicts (for the capabilities panel)."""
    return [
        {
            "name": sa.name,
            "label": sa.label,
            "icon": sa.icon,
            "description": sa.description,
            "tools": sa.tools,
            "status": "active",
        }
        for sa in _SUBAGENTS.values()
    ]
