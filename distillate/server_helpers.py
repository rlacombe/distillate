"""Pure helper functions extracted from server.py.

These are stateless utilities used by the WebSocket server but not
dependent on FastAPI or the app's closure state.
"""

import json
import logging

log = logging.getLogger(__name__)


def _summarize_tool_result(raw_result, is_err: bool) -> str:
    """Turn a tool result into a compact one-line summary."""
    prefix = "ERR" if is_err else "OK"
    if not raw_result:
        return ""

    # raw_result may be a dict (already parsed) or a string
    obj = raw_result
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except (json.JSONDecodeError, TypeError):
            pass

    if isinstance(obj, dict):
        # Glob results
        if "filenames" in obj:
            n = obj.get("numFiles", len(obj["filenames"]))
            return f"  [{prefix}] {n} files found"
        # File read
        if "filePath" in obj:
            fp = obj["filePath"].rsplit("/", 1)[-1]  # just filename
            lines = obj.get("numLines", "?")
            return f"  [{prefix}] {fp} ({lines} lines)"
        # Bash stdout
        if "stdout" in obj:
            stdout = obj["stdout"].strip()
            if len(stdout) > 150:
                stdout = stdout[:150] + "..."
            return f"  [{prefix}] {stdout}" if stdout else ""
        # File content (from Read)
        if "file" in obj and isinstance(obj["file"], dict):
            fp = obj["file"].get("filePath", "?").rsplit("/", 1)[-1]
            lines = obj["file"].get("numLines", "?")
            return f"  [{prefix}] {fp} ({lines} lines)"

    # Plain text fallback
    text = str(raw_result)
    if len(text) > 150:
        text = text[:150] + "..."
    return f"  [{prefix}] {text}"


def _parse_stream_json(raw: str) -> str:
    """Parse Claude Code stream-json output into human-readable text.

    The log file has one JSON object per line.  Falls back to a
    brace-depth scanner for tmux capture-pane where lines are wrapped.
    """
    # --- Extract JSON objects ---
    events: list[dict] = []

    # Try line-by-line (works for log files)
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except json.JSONDecodeError:
            pass

    # If line-by-line yielded few results, try brace-depth scan
    if len(events) < 3:
        flat = raw.replace("\n", "").replace("\r", "")
        decoder = json.JSONDecoder()
        i = 0
        while i < len(flat):
            pos = flat.find("{", i)
            if pos == -1:
                break
            try:
                obj, end = decoder.raw_decode(flat, pos)
                if isinstance(obj, dict) and "type" in obj:
                    events.append(obj)
                i = end
            except json.JSONDecodeError:
                i = pos + 1

    # --- Format events ---
    output: list[str] = []
    for evt in events:
        evt_type = evt.get("type", "")

        if evt_type == "thinking":
            thought = evt.get("thinking", "")
            if thought:
                first = thought.split("\n")[0][:120]
                output.append(f"[thinking] {first}")

        elif evt_type == "assistant":
            for block in evt.get("message", {}).get("content", []):
                btype = block.get("type", "")
                if btype == "text":
                    output.append(block.get("text", ""))
                elif btype == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    if name in ("Read", "Write", "Edit"):
                        detail = inp.get("file_path", "").rsplit("/", 1)[-1]
                    elif name == "Bash":
                        cmd = inp.get("command", "")
                        detail = cmd[:120] + ("..." if len(cmd) > 120 else "")
                    elif name in ("Grep", "Glob"):
                        detail = inp.get("pattern", "")
                    else:
                        detail = str(inp)[:80]
                    output.append(f">>> {name}: {detail}")

        elif evt_type == "user":
            for block in evt.get("message", {}).get("content", []):
                if block.get("type") == "tool_result":
                    raw_result = evt.get("tool_use_result", "")
                    if not raw_result:
                        raw_result = block.get("content", "")
                    is_err = block.get("is_error", False)
                    summary = _summarize_tool_result(raw_result, is_err)
                    if summary:
                        output.append(summary)

        elif evt_type == "text":
            f = evt.get("file", {})
            if isinstance(f, dict) and f.get("filePath"):
                name = f["filePath"].rsplit("/", 1)[-1]
                lines = f.get("numLines", "?")
                output.append(f"  [OK] {name} ({lines} lines)")

        elif evt_type == "result":
            result_text = evt.get("result", "")
            if result_text:
                output.append(f"\n--- Result ---\n{result_text}")

    return "\n".join(output) if output else "Session is running... waiting for output."


# ---------------------------------------------------------------------------
# Conversation persistence — reuses the CLI's conversations.json format
# ---------------------------------------------------------------------------

_MAX_SESSIONS = 50


def _conversations_path():
    """Return the path to the shared conversations log."""
    from distillate import config
    return config.CONFIG_DIR / "conversations.json"


def _load_sessions() -> list[dict]:
    """Load past sessions from the shared conversation log."""
    try:
        return json.loads(_conversations_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_session(all_sessions: list[dict], conversation: list[dict]) -> None:
    """Append a session to the conversation log."""
    from datetime import datetime, timezone

    # Build a summary from the first user message
    first_user = ""
    for msg in conversation:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                first_user = content[:120]
            break

    session = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": first_user,
        "turns": len(conversation),
    }
    all_sessions.append(session)

    # Trim and save
    trimmed = all_sessions[-_MAX_SESSIONS:]
    try:
        _conversations_path().write_text(
            json.dumps(trimmed, ensure_ascii=False, indent=None),
            encoding="utf-8",
        )
    except OSError:
        log.warning("Could not save conversation log")
