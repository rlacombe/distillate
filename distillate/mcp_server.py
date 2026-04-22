"""MCP server exposing Distillate paper library + experiment tools.

Usage:
    python -m distillate.mcp_server

Configure in Claude Code:
    .mcp.json: {"mcpServers": {"distillate": {"command": "python3", "args": ["-m", "distillate.mcp_server"]}}}
"""

import asyncio
import json
import logging

from mcp.server import Server
from mcp.types import TextContent, Tool

from distillate.agent_core import NICOLAS_TOOL_SCHEMAS, execute_tool
from distillate.state import State

log = logging.getLogger(__name__)

server = Server("distillate")
_state = State()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Convert Anthropic tool schemas to MCP Tool format."""
    return [
        Tool(
            name=schema["name"],
            description=schema.get("description", ""),
            inputSchema=schema.get("input_schema", {}),
        )
        for schema in NICOLAS_TOOL_SCHEMAS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a Distillate tool and return JSON result.

    The lab_repl tool may run long (sub-LLM calls, recursive delegation),
    so it runs in a thread to avoid blocking the MCP event loop.
    """
    _state.reload()
    # Run blocking tools in a thread to avoid stalling the MCP event loop.
    _THREADED_TOOLS = {"lab_repl", "distillate_repl", "distillate_search"}
    if name in _THREADED_TOOLS:
        result = await asyncio.to_thread(execute_tool, name, arguments, _state)
    else:
        result = execute_tool(name, arguments, _state)
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def main():
    from mcp.server.stdio import stdio_server

    async def _run():
        async with stdio_server() as (read, write):
            await server.run(
                read, write, server.create_initialization_options()
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
