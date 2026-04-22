import asyncio
import json
import logging
import os
import shlex
import signal
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Union

log = logging.getLogger(__name__)

try:
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
except ImportError:
    @dataclass
    class TextBlock:
        text: str
        type: str = "text"

    @dataclass
    class ToolUseBlock:
        id: str
        name: str
        input: dict
        type: str = "tool_use"

    @dataclass
    class ToolResultBlock:
        tool_use_id: str
        content: Any
        is_error: bool = False
        type: str = "tool_result"

    @dataclass
    class AssistantMessage:
        content: list[Union[TextBlock, ToolUseBlock]]

    @dataclass
    class UserMessage:
        content: list[ToolResultBlock]

    @dataclass
    class SystemMessage:
        subtype: str
        data: dict

    @dataclass
    class ResultMessage:
        session_id: str
        num_turns: int
        model: str
        usage: dict
        total_cost_usd: float = 0.0

class GeminiSDKClient:
    """Mock-SDK for Gemini CLI that mimics ClaudeSDKClient interface."""

    def __init__(self, options: Any):
        self.options = options
        self._process: Optional[asyncio.subprocess.Process] = None
        self._session_id: Optional[str] = None
        self._model: Optional[str] = getattr(options, "model", None)
        self._num_turns = 0
        self._response_queue: asyncio.Queue = asyncio.Queue()
        self._read_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        """Start the gemini subprocess in stream-json mode."""
        cmd = ["gemini", "--output-format", "stream-json", "--approval-mode", "yolo"]
        
        # Add model if specified
        if self._model:
            cmd.extend(["--model", self._model])
            
        # Add resume if specified
        resume = getattr(self.options, "resume", None)
        if resume:
            cmd.extend(["--resume", resume])

        # Add allowed tools if specified
        allowed_tools = getattr(self.options, "allowed_tools", None)
        if allowed_tools:
            for tool in allowed_tools:
                cmd.extend(["--allowed-tools", tool])

        log.info("Starting Gemini CLI: %s", " ".join(shlex.quote(c) for c in cmd))
        
        # MCP support: write temporary .gemini/settings.json in current directory
        # if mcp_servers are provided.
        mcp_servers = getattr(self.options, "mcp_servers", None)
        if mcp_servers:
            cfg_dir = Path(".gemini")
            cfg_dir.mkdir(exist_ok=True)
            cfg_file = cfg_dir / "settings.json"
            
            existing = {}
            if cfg_file.exists():
                try:
                    existing = json.loads(cfg_file.read_text())
                except Exception:
                    pass
            
            # Merge MCP servers
            existing_mcp = existing.setdefault("mcpServers", {})
            for name, cfg in mcp_servers.items():
                existing_mcp[name] = cfg
            
            cfg_file.write_text(json.dumps(existing, indent=2))
            log.info("Wrote MCP config to %s", cfg_file)

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        
        self._read_task = asyncio.create_task(self._read_stdout())
        # We don't wait for 'init' here, it will come through receive_response

    async def _read_stdout(self) -> None:
        """Background task to read and parse JSON lines from gemini."""
        if not self._process or not self._process.stdout:
            return

        try:
            async for line in self._process.stdout:
                line_str = line.decode().strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    await self._response_queue.put(data)
                except json.JSONDecodeError:
                    log.debug("Failed to parse gemini output line: %s", line_str)
        except Exception as e:
            log.error("Error reading gemini stdout: %s", e)

    async def query(self, user_input: str) -> None:
        """Send a prompt to the gemini process."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Gemini process not connected")
        
        # Gemini expects the prompt on stdin for interactive mode if we didn't use -p
        self._process.stdin.write(f"{user_input}\n".encode())
        await self._process.stdin.drain()
        self._num_turns += 1

    async def receive_response(self) -> AsyncGenerator[Union[AssistantMessage, UserMessage, SystemMessage, ResultMessage], None]:
        """Yield mapped messages from the gemini process."""
        while True:
            data = await self._response_queue.get()
            msg_type = data.get("type")
            
            if msg_type == "init":
                self._session_id = data.get("session_id")
                yield SystemMessage(subtype="init", data=data)
            
            elif msg_type == "message":
                role = data.get("role")
                content = data.get("content", "")
                if role == "assistant":
                    yield AssistantMessage(content=[TextBlock(text=content)])
            
            elif msg_type == "tool_use":
                yield AssistantMessage(content=[ToolUseBlock(
                    id=data.get("tool_id"),
                    name=data.get("tool_name"),
                    input=data.get("parameters", {})
                )])
            
            elif msg_type == "tool_result":
                yield UserMessage(content=[ToolResultBlock(
                    tool_use_id=data.get("tool_id"),
                    content=data.get("output", ""),
                    is_error=data.get("status") == "error"
                )])
            
            elif msg_type == "result":
                stats = data.get("stats", {})
                yield ResultMessage(
                    session_id=self._session_id or "",
                    num_turns=self._num_turns,
                    model=data.get("model") or self._model or "gemini",
                    usage={
                        "input_tokens": stats.get("input_tokens", 0),
                        "output_tokens": stats.get("output_tokens", 0),
                        "cache_read_input_tokens": stats.get("cached", 0),
                        "cache_creation_input_tokens": 0, # Gemini CLI doesn't split these yet
                    },
                    total_cost_usd=0.0 # We compute this in NicolasClient
                )
                break # End of turn
            
            self._response_queue.task_done()

    async def interrupt(self) -> None:
        """Send SIGINT to the gemini process."""
        if self._process:
            self._process.send_signal(signal.SIGINT)

    async def set_model(self, model: str) -> None:
        """Gemini CLI doesn't support model switching mid-session easily, 
        so we just update the internal state for next restart."""
        self._model = model

    async def disconnect(self) -> None:
        """Terminate the gemini process."""
        if self._read_task:
            self._read_task.cancel()
        if self._process:
            try:
                self._process.terminate()
                await self._process.wait()
            except Exception:
                pass
            self._process = None

class GeminiAgentOptions:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
