"""Agent runtime: session management, memory, execution, sub-agents.

This package provides the clean import path for Distillate's agent infrastructure.
Includes the Tier 2 sub-agent system (Librarian, Knowledge Agent, Research Agent).
"""

# --- Agent core infrastructure ---
from distillate.agent_core import (
    build_system_prompt,
    execute_tool,
    format_past_sessions,
    tool_label,
    truncate_result,
)

# --- Tier 2 sub-agent infrastructure ---
from distillate.agent_runtime.subagent import (
    SubAgent,
    SubAgentContext,
    SubAgentResult,
    get_subagent,
    list_subagents,
    register_subagent,
)
from distillate.agent_runtime.breadcrumbs import (
    emit as emit_breadcrumb,
    make_emitter,
    flush as flush_breadcrumbs,
)

# Auto-register built-in sub-agents on import
import distillate.agent_runtime.librarian   # noqa: F401
import distillate.agent_runtime.knowledge_agent   # noqa: F401
import distillate.agent_runtime.research_agent    # noqa: F401

# --- SDK client ---
from distillate.agent_sdk import NicolasClient

# --- Agent registry & Pi variants ---
from distillate.agents import (
    available_agents,
    build_agent_command,
    create_pi_variant,
    delete_pi_variant,
    detect_local_compute,
    get_agent,
    get_pi_env,
    get_pi_variants,
    get_protocol_file,
)

__all__ = [
    # agent_core
    "build_system_prompt",
    "execute_tool",
    "format_past_sessions",
    "tool_label",
    "truncate_result",
    # agent_sdk
    "NicolasClient",
    # agents
    "available_agents",
    "build_agent_command",
    "create_pi_variant",
    "delete_pi_variant",
    "detect_local_compute",
    "get_agent",
    "get_pi_env",
    "get_pi_variants",
    "get_protocol_file",
]
