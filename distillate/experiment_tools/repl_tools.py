"""Lab REPL + thread-name tools — MCP schema and dispatch.

Exposes Nicolas's lab sandbox (lab_repl) and a small helper to name the
current chat thread (set_thread_name) so the conversations sidebar
shows what each thread is about.
"""

SCHEMAS = [
    {
        "name": "lab_repl",
        "description": (
            "Execute Python code in your persistent lab sandbox for "
            "multi-step reasoning. The sandbox has lab.papers, "
            "lab.experiments, lab.notebook, lab.experiments APIs, "
            "llm_query() for sub-LLM calls, delegate() for recursive "
            "reasoning, and FINAL(answer) to return results. "
            "Variables persist across calls."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute in the sandbox",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Brief human-readable description of what this "
                        "code does (shown to the user as a status indicator)"
                    ),
                },
            },
            "required": ["code", "description"],
        },
    },
    {
        "name": "set_thread_name",
        "description": (
            "Rename the current chat thread (the user's conversation with "
            "you) to a concise 3-5 word topic name. Call this once after "
            "your first substantive response in a fresh thread so the "
            "sidebar shows what the thread is about. Examples: 'DFM Glycan "
            "Generation', 'Reading Patterns Audit', 'Cmd+R Shortcut Fix'. "
            "Do not call again unless the topic clearly shifts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "3-5 word topic name. Title Case. No punctuation. "
                        "Avoid generic words like 'discussion' or 'help'."
                    ),
                },
            },
            "required": ["name"],
        },
    },
]


def lab_repl_tool(*, state, code: str, description: str = "") -> dict:
    """Execute code in the persistent lab sandbox."""
    from distillate.agent_runtime.lab_repl import execute
    return execute(code, state)


def set_thread_name_tool(*, state, name: str) -> dict:
    """Rename the active Nicolas thread.

    Looks up the active session_id from the registry (set by the most
    recent session_init / turn_end event) and updates that entry's
    ``name`` field. The MCP server runs in a separate process from the
    desktop server, but both write the same registry file via atomic
    replace, so updates are race-safe.
    """
    from distillate.agent_sdk import _load_registry, _save_registry

    cleaned = (name or "").strip()
    if not cleaned:
        return {"success": False, "error": "name_required"}
    cleaned = cleaned[:120]

    reg = _load_registry()
    sid = reg.get("active_session_id")
    if not sid:
        return {"success": False, "error": "no_active_thread"}

    for s in reg.get("sessions", []):
        if s.get("session_id") == sid:
            s["name"] = cleaned
            _save_registry(reg)
            return {"success": True, "session_id": sid, "name": cleaned}
    return {"success": False, "error": "session_not_found", "session_id": sid}
