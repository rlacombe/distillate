"""Agents — long-lived agent CRUD and session control."""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from distillate.routes import _context

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/agents/templates")
async def list_agent_templates():
    """List available agent templates for the creation UI."""
    from distillate.agent_templates import list_all_templates
    return JSONResponse({"templates": list_all_templates()})


@router.get("/agents/live")
async def list_live_agents():
    """List all long-lived agents with session status."""
    _state = _context._state
    from distillate.experiment_tools import list_agents_tool
    return JSONResponse(list_agents_tool(state=_state))


@router.post("/agents/live")
async def create_live_agent(request: Request):
    """Create a long-lived agent. Body: {"name": "...", "personality": "...", "model": "..."}"""
    _state = _context._state
    from distillate.experiment_tools import create_agent_tool
    body = await request.json()
    return JSONResponse(create_agent_tool(state=_state, **body))


@router.get("/agents/live/{agent_id}")
async def get_live_agent(agent_id: str):
    """Get agent details."""
    _state = _context._state
    from distillate.experiment_tools import get_agent_details_tool
    return JSONResponse(get_agent_details_tool(state=_state, agent=agent_id))


@router.patch("/agents/live/{agent_id}")
async def update_live_agent(agent_id: str, request: Request):
    """Update agent fields. Body: any of {name, personality, model}"""
    _state = _context._state
    from distillate.experiment_tools import update_agent_tool
    body = await request.json()
    return JSONResponse(update_agent_tool(state=_state, agent=agent_id, **body))


@router.delete("/agents/live/{agent_id}")
async def delete_live_agent(agent_id: str):
    """Delete a long-lived agent."""
    _state = _context._state
    from distillate.experiment_tools import delete_agent_tool
    return JSONResponse(delete_agent_tool(state=_state, agent=agent_id))


@router.post("/agents/live/{agent_id}/start")
async def start_live_agent(agent_id: str, request: Request):
    """Start an agent's terminal session. Optional body: {"initial_task": "..."}"""
    _state = _context._state
    from distillate.experiment_tools import start_agent_session_tool
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass  # No body is fine — starts without a task
    return JSONResponse(start_agent_session_tool(state=_state, agent=agent_id, **body))


@router.post("/agents/live/{agent_id}/stop")
async def stop_live_agent(agent_id: str):
    """Stop an agent's terminal session."""
    _state = _context._state
    from distillate.experiment_tools import stop_agent_session_tool
    return JSONResponse(stop_agent_session_tool(state=_state, agent=agent_id))


@router.get("/agents")
async def list_experiment_agents():
    """List experiment-runner agents (harnesses) with availability status."""
    from distillate.agents import available_agents
    _state = _context._state
    return JSONResponse({"agents": available_agents(state=_state)})


@router.get("/agents/roster")
async def list_agent_roster():
    """List all agents by tier: shell (Nicolas), agents (Tier 3b), experimentalists."""
    _state = _context._state
    agents = _state.agents
    shell = [a for a in agents.values() if a.get("tier") == "shell"]
    tier3b = [a for a in agents.values() if a.get("tier") != "shell"]
    return JSONResponse({
        "shell": shell,
        "agents": tier3b,
    })


@router.get("/agents/capabilities")
async def list_capabilities():
    """List Nicolas's capabilities: skills + sub-agents + backend info."""
    from distillate.agent_runtime import list_subagents
    subagents = list_subagents()
    return JSONResponse({
        "subagents": subagents,
        "backend": {
            "model": "Claude Opus 4.6",
            "harness": "Distillate Agent SDK",
        },
    })


@router.get("/harnesses")
async def list_harnesses():
    """List all registered harnesses with availability status."""
    import shutil
    _state = _context._state
    harnesses = []
    for h_id, h in _state.harnesses.items():
        harnesses.append({
            **h,
            "available": bool(shutil.which(h.get("binary", ""))) if h.get("binary") else h.get("available", False),
        })
    return JSONResponse({"harnesses": harnesses})
