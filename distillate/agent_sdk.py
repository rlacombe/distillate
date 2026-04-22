"""Nicolas agent powered by Claude Agent SDK.

Replaces agent_core.py's custom Claude API loop with the Agent SDK,
giving Nicolas all of Claude Code's capabilities plus Distillate tools
via MCP server.

Architecture:
    Desktop WebSocket ─► NicolasClient (wraps ClaudeSDKClient)
                              │
                         Claude Code process (persistent)
                              │
                    ┌─────────┴──────────┐
                    │                    │
              MCP: distillate      Built-in tools
              (46 tools)           (Read, Edit, Bash, …)
"""

import json
import logging
import os
import re
import sys
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

# Session cost guardrails. Soft warn once per session at the lower threshold;
# hard warn BLOCKS further turns above the upper threshold until the user
# either confirms (unblock_budget) or starts a new conversation. Day-level
# soft/hard thresholds mirror the same two-tier pattern for daily spend.
# All four are env-tunable so power users can lift ceilings without a rebuild.
_SESSION_SOFT_WARN_USD: float = float(
    os.environ.get("DISTILLATE_SESSION_SOFT_WARN_USD", "1.00")
)
_SESSION_HARD_WARN_USD: float = float(
    os.environ.get("DISTILLATE_SESSION_HARD_WARN_USD", "5.00")
)
_DAY_SOFT_WARN_USD: float = float(
    os.environ.get("DISTILLATE_DAY_SOFT_WARN_USD", "5.00")
)
_DAY_HARD_WARN_USD: float = float(
    os.environ.get("DISTILLATE_DAY_HARD_WARN_USD", "20.00")
)
# Auto-compact suggestion — fires once per session when session cost
# crosses this threshold, prompting the user to start a fresh conversation.
_COMPACT_SUGGEST_USD: float = float(
    os.environ.get("DISTILLATE_COMPACT_SUGGEST_USD", "1.00")
)

from distillate import config, preferences, pricing
from distillate.agent_core import TOOL_LABELS, VERBOSE_TOOLS
from distillate.nicolas_state import set_nicolas_state
# usage_tracker is imported lazily at its single use site to break a
# circular import: distillate.agent_runtime/__init__.py re-exports
# NicolasClient, so importing usage_tracker here at module load time
# re-enters agent_sdk before NicolasClient is defined.
from distillate.state import State

log = logging.getLogger(__name__)


# Sessions registry: persists all Nicolas conversations with metadata so
# the desktop can render a picker. Format:
#   {
#     "version": 1,
#     "active_session_id": "abc-123" | null,
#     "sessions": [
#       {session_id, name, created_at, last_activity, preview}, ...
#     ]
#   }
_SESSIONS_FILE = config.NICOLAS_SESSIONS_FILE
_LEGACY_SESSION_FILE = config.CONFIG_DIR / "nicolas_session.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _default_registry() -> dict:
    return {"version": 1, "active_session_id": None, "sessions": []}


def _migrate_legacy_session() -> dict | None:
    """One-shot migration from the old single-session file to the registry.

    Runs if ``nicolas_session.json`` exists and ``nicolas_sessions.json``
    does not. The legacy file is removed after successful migration.
    """
    if _SESSIONS_FILE.exists() or not _LEGACY_SESSION_FILE.exists():
        return None
    try:
        data = json.loads(_LEGACY_SESSION_FILE.read_text())
        sid = data.get("session_id")
        if not (isinstance(sid, str) and sid):
            return None
        reg = _default_registry()
        now = _now_iso()
        reg["sessions"].append({
            "session_id": sid,
            "name": "Conversation",
            "created_at": now,
            "last_activity": now,
            "preview": "",
            "status": "idle",
        })
        reg["active_session_id"] = sid
        _save_registry(reg)
        _LEGACY_SESSION_FILE.unlink(missing_ok=True)
        log.info("Migrated legacy nicolas_session.json -> sessions registry")
        return reg
    except (OSError, json.JSONDecodeError):
        log.debug("Legacy session migration failed", exc_info=True)
        return None


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _is_valid_session_id(sid: object) -> bool:
    return isinstance(sid, str) and bool(_UUID_RE.match(sid))


def _load_registry() -> dict:
    migrated = _migrate_legacy_session()
    if migrated is not None:
        return migrated
    try:
        data = json.loads(_SESSIONS_FILE.read_text())
        if isinstance(data, dict) and isinstance(data.get("sessions"), list):
            # Strip any non-UUID session IDs that leaked in (e.g. from tests).
            # A bogus active_session_id causes the claude CLI to exit 1 on startup.
            before = len(data["sessions"])
            data["sessions"] = [
                s for s in data["sessions"]
                if _is_valid_session_id(s.get("session_id"))
            ]
            if not _is_valid_session_id(data.get("active_session_id")):
                data["active_session_id"] = None
            if len(data["sessions"]) < before:
                log.warning(
                    "Stripped %d non-UUID session(s) from registry",
                    before - len(data["sessions"]),
                )
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return _default_registry()


def _save_registry(reg: dict) -> None:
    """Atomically persist the sessions registry.

    Writes to a sibling temp file then renames over the live target. On
    POSIX, ``os.replace`` is atomic, so a crash mid-write can never leave
    the live registry truncated — the previous good copy stays put. This
    is the root-cause guard for the 'previous conversation disappeared'
    bug: a partial write_text() would empty the file, _load_registry
    would then return the default (empty) registry, and the next save
    would persist that loss.
    """
    import os
    target = _SESSIONS_FILE
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(reg, indent=2))
        os.replace(tmp, target)
    except OSError:
        log.debug("Failed to persist sessions registry", exc_info=True)
        # Best-effort cleanup of the temp file.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _needs_auto_name(session_entry: dict) -> bool:
    """Return True if this thread still needs an auto-generated name.

    A session needs naming when it has not yet been auto-named AND its
    current name is either the default ("New conversation", "Thread",
    empty) or was derived from the first-message preview (and thus
    isn't a crisp 3-5 word topic). We detect preview-derived names by
    comparing name to a truncated preview — anything longer than ~40
    chars, containing sentence-like punctuation, or matching the
    preview prefix is treated as not-yet-named.
    """
    if session_entry.get("auto_named"):
        return False
    name = (session_entry.get("name") or "").strip()
    if not name or name in ("New conversation", "Thread", "Conversation"):
        return True
    preview = (session_entry.get("preview") or "").strip()
    # If the name shares a long prefix with the preview, it's the
    # default first-50-chars derivation — replace it.
    if preview:
        min_len = min(len(name), len(preview))
        if min_len >= 10 and name[:min_len] == preview[:min_len]:
            return True
    if name.endswith("\u2026") or name.endswith("..."):
        return True
    if len(name) > 40 or "." in name or "?" in name or "!" in name:
        return True
    return False


def _generate_thread_name(user_message: str, assistant_response: str) -> str | None:
    """Call a fast model to summarize the first exchange in 3-5 Title
    Case words. Returns None if the call fails or produces garbage.
    Synchronous — call inside asyncio.to_thread() from async code.
    """
    try:
        from distillate.agent_runtime.lab_repl import _llm_query
    except Exception:
        return None

    prompt = (
        "Summarize the topic of this conversation in 3-5 Title Case "
        "words. Respond with ONLY the name — no quotes, no punctuation, "
        "no trailing period, no explanation. Examples of good names: "
        "\"DFM Glycan Generation\", \"Reading Patterns Audit\", "
        "\"Cmd+R Shortcut Fix\".\n\n"
        f"User: {(user_message or '')[:600]}\n\n"
        f"Assistant: {(assistant_response or '')[:600]}\n\n"
        "Name:"
    )
    raw = _llm_query(prompt, max_tokens=24)
    if not raw or raw.startswith("ERROR:"):
        return None
    # Reduce to first line first — the LLM occasionally adds explanation.
    name = raw.strip().split("\n", 1)[0].strip()
    # LLMs wrap in quotes/asterisks/backticks and append periods in
    # various orderings (`"Name".`, `"Name."`, `Name.`). Iterate until
    # the string stabilizes so every combination converges to the core.
    for _ in range(5):
        before = name
        for ch in ('"', "'", "`", "*"):
            name = name.strip(ch)
        name = name.strip().rstrip(".").strip()
        if name == before:
            break
    if not (2 <= len(name) <= 60):
        return None
    return name


def _apply_auto_name(session_id: str, name: str) -> bool:
    """Write the auto-generated name into the registry and set the
    ``auto_named`` flag so we don't re-name on later turns. Returns
    True if the session entry was found and updated.
    """
    if not session_id or not name:
        return False
    reg = _load_registry()
    for s in reg.get("sessions", []):
        if s.get("session_id") == session_id:
            s["name"] = name[:120]
            s["auto_named"] = True
            _save_registry(reg)
            return True
    return False


def _touch_session(
    reg: dict,
    session_id: str,
    *,
    preview: str | None = None,
    name: str | None = None,
    increment_turn: bool = False,
) -> None:
    """Upsert a session entry. Sets last_activity to now. Preview is only
    written if the session has no preview yet (first-user-message rule).
    """
    now = _now_iso()
    for s in reg["sessions"]:
        if s["session_id"] == session_id:
            s["last_activity"] = now
            if preview and not s.get("preview"):
                s["preview"] = preview[:120]
            if name is not None:
                s["name"] = name
            if increment_turn:
                s["turn_count"] = s.get("turn_count", 0) + 1
            return
    reg["sessions"].append({
        "session_id": session_id,
        "name": name or (preview[:50] if preview else "New conversation"),
        "created_at": now,
        "last_activity": now,
        "preview": (preview or "")[:120],
        "turn_count": 1 if increment_turn else 0,
        "status": "idle",
    })


# ---------------------------------------------------------------------------
# NicolasClient — persistent wrapper around ClaudeSDKClient
# ---------------------------------------------------------------------------

class NicolasClient:
    """Persistent Claude Code connection for one WebSocket session.

    Keeps the Claude Code subprocess and MCP server connections alive
    between messages.  Supports model switching, new conversations,
    and streaming responses as desktop-protocol events.
    """

    def __init__(self, state: State, model: str = ""):
        self._state = state
        # If no explicit model, honor the user's saved preference (RLM uses
        # the API directly — the choice is real and must persist).
        self._model = model or preferences.get("nicolas_model", "") or ""
        self._client = None  # lazily created on first query
        self._last_user_input: str = ""  # for first-message preview capture
        # Optional name stashed by new_conversation(pending_name=...) so
        # the next minted session gets a meaningful title (e.g. the
        # experiment name after a launch_experiment-triggered branch).
        self._pending_thread_name: str = ""
        # Resume the active session from the registry. If the saved id no
        # longer exists on the Claude Code side, the SDK simply starts a
        # fresh one and the init message gives us a new id.
        reg = _load_registry()
        self._session_id: str | None = reg.get("active_session_id")
        # Budget guardrail: flip to True after the first soft-warn event so
        # we don't yell on every subsequent turn. Reset on new_conversation
        # / switch_session so each session gets its own warning lifecycle.
        self._budget_soft_warned: bool = False
        self._day_soft_warned: bool = False
        self._compact_suggested: bool = False
        # Hard-cap override: user confirmed "continue anyway" via the UI.
        # Resets on new_conversation / switch_session.
        self._budget_override: bool = False
        # Runtime-overridable thresholds — preferences take precedence over
        # env-var defaults so the user's saved settings survive restarts.
        self._compact_suggest_usd: float = float(
            preferences.get("budget_compact_suggest_usd", _COMPACT_SUGGEST_USD)
        )
        self._session_hard_warn_usd: float = float(
            preferences.get("budget_session_hard_usd", _SESSION_HARD_WARN_USD)
        )
        self._day_hard_warn_usd: float = float(
            preferences.get("budget_day_hard_usd", _DAY_HARD_WARN_USD)
        )
        # Set by the stderr callback when Claude Code reports "Session ID X is
        # already in use". Consumed by send() to trigger an evict-then-resume
        # retry on that specific session.
        self._stale_session_id: str | None = None

    async def _ensure_connected(self) -> None:
        """Create and connect the ClaudeSDKClient or GeminiSDKClient."""
        if self._client is not None:
            return

        # Find the MCP server python — same venv as the server
        python_path = sys.executable

        # Is this a Gemini model?
        is_gemini = self._model and self._model.startswith("gemini")

        if is_gemini:
            from distillate.gemini_sdk import GeminiAgentOptions, GeminiSDKClient
            options = GeminiAgentOptions(
                model=self._model or None,
                resume=self._session_id,
                mcp_servers={
                    "distillate": {
                        "command": python_path,
                        "args": ["-m", "distillate.mcp_server"],
                    },
                },
                allowed_tools=[
                    "mcp__distillate__*",
                    "Read", "Edit", "Write", "Glob", "Grep",
                    "WebSearch", "WebFetch",
                ],
            )
            self._client = GeminiSDKClient(options)
            await self._client.connect()
            log.info("NicolasClient connected via Gemini (model=%s, resume=%s)",
                     self._model, self._session_id)
            return

        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        # Reset stale-session capture before each new connection attempt.
        self._stale_session_id = None

        # Capture Claude Code's stderr: log it (so ProcessError is diagnosable
        # instead of opaque) and parse the "already in use" session ID so
        # send() can retry with --continue --resume <stale-id>.
        def _claude_stderr(line: str) -> None:
            log.warning("[claude-code stderr] %s", line.rstrip())
            m = re.search(
                r"Session ID ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) is already in use",
                line, _re.IGNORECASE,
            )
            if m:
                self._stale_session_id = m.group(1)

        # Subscription-auth opt-in: when set, strip ANTHROPIC_API_KEY from
        # the subprocess env so Claude Code falls back to its OAuth login
        # (billed against the user's Pro/Max subscription, not the API
        # console). The parent process keeps the key for lab_repl sub-calls.
        subprocess_env: dict[str, str] = dict(os.environ)
        if config.NICOLAS_USE_SUBSCRIPTION:
            subprocess_env.pop("ANTHROPIC_API_KEY", None)
            subprocess_env.pop("ANTHROPIC_AUTH_TOKEN", None)
            log.info("Nicolas: using Claude Code subscription (API key stripped from subprocess env)")

        options = ClaudeAgentOptions(
            system_prompt={
                "type": "custom",
                "custom": "You are Nicolas, the Alchemist of the Distillate lab. You are a senior research engineer helping a user run experiments, read papers, and manage their research library. Your tone is professional, encouraging, and focused on empirical progress. " + _build_dynamic_context(self._state),
            },
            mcp_servers={
                "distillate": {
                    "command": python_path,
                    "args": ["-m", "distillate.mcp_server"],
                },
            },
            allowed_tools=[
                "mcp__distillate__*",
                # Bash intentionally omitted — forces Nicolas to use lab_repl
                # for data exploration instead of `python3 << 'EOF'` bypasses.
                "Read", "Edit", "Write", "Glob", "Grep",
                "WebSearch", "WebFetch",
            ],
            permission_mode="bypassPermissions",
            model=self._model or None,
            # Only pass resume if the ID is a valid UUID — bogus IDs (e.g. from
            # tests writing to the real config dir) cause the CLI to exit 1.
            resume=self._session_id if _is_valid_session_id(self._session_id) else None,
            stderr=_claude_stderr,
            env=subprocess_env,
        )
        if not _is_valid_session_id(self._session_id):
            if self._session_id is not None:
                log.warning("Discarding invalid session_id %r — starting fresh", self._session_id)
            self._session_id = None

        self._client = ClaudeSDKClient(options)
        await self._client.connect()
        log.info("NicolasClient connected (model=%s, resume=%s)",
                 self._model or "default", self._session_id)

    async def _receive_events(self) -> AsyncGenerator[dict, None]:
        """Yield desktop-protocol events from the active client's response stream."""
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            SystemMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )
        async for message in self._client.receive_response():
            # --- AssistantMessage: text + tool calls ---
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield {"type": "text_delta", "text": block.text}
                    elif isinstance(block, ToolUseBlock):
                        display = block.name
                        if display.startswith("mcp__distillate__"):
                            display = display[len("mcp__distillate__"):]
                        yield {
                            "type": "tool_start",
                            "name": display,
                            "input": block.input,
                            "tool_use_id": block.id,
                            "verbose": display in VERBOSE_TOOLS,
                            "label": TOOL_LABELS.get(display, ""),
                        }

            # --- UserMessage: tool results ---
            elif isinstance(message, UserMessage):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        result = _parse_tool_result(block.content)
                        yield {
                            "type": "tool_done",
                            "tool_use_id": block.tool_use_id,
                            "result": result,
                            "is_error": block.is_error or False,
                        }

            # --- SystemMessage: init, task events ---
            elif isinstance(message, SystemMessage):
                if message.subtype == "init":
                    sid = message.data.get("session_id", "")
                    if sid:
                        self._session_id = sid
                        reg = _load_registry()
                        # If a pending thread name was stashed by an
                        # earlier branch (launch_experiment), use it
                        # as the session's name AND mark auto_named
                        # so the Haiku auto-namer skips this session.
                        pending = self._pending_thread_name
                        self._pending_thread_name = ""
                        _touch_session(
                            reg, sid,
                            preview=self._last_user_input,
                            name=pending or None,
                        )
                        if pending:
                            for s in reg.get("sessions", []):
                                if s.get("session_id") == sid:
                                    s["auto_named"] = True
                                    break
                        reg["active_session_id"] = sid
                        _save_registry(reg)
                        yield {"type": "session_init", "session_id": sid}

            # --- ResultMessage: turn complete ---
            elif isinstance(message, ResultMessage):
                set_nicolas_state("idle")
                self._session_id = message.session_id
                reg = _load_registry()
                _touch_session(reg, message.session_id, preview=self._last_user_input, increment_turn=True)
                reg["active_session_id"] = message.session_id
                _save_registry(reg)

                # Extract per-turn usage from the SDK's ResultMessage so we
                # can compute cost via our own pricing table (consistent
                # with sub-LLM calls from lab_repl) and show a token
                # breakdown in the billing UI.
                #
                # IMPORTANT: claude-agent-sdk gives us usage as a DICT, not
                # an object, so we must use ``.get()`` not ``getattr()`` —
                # attribute access on a dict silently returns the default
                # and we'd never see cost. Tests may still pass an object
                # (MagicMock / fake), so handle both shapes.
                turn_model = getattr(message, "model", None) or self._model or pricing.DEFAULT_MODEL
                usage_obj = getattr(message, "usage", None) or {}
                if isinstance(usage_obj, dict):
                    _u = usage_obj.get
                    raw_usage = {
                        "input_tokens": _u("input_tokens", 0) or 0,
                        "output_tokens": _u("output_tokens", 0) or 0,
                        "cache_read_input_tokens": _u("cache_read_input_tokens", 0) or 0,
                        "cache_creation_input_tokens": _u("cache_creation_input_tokens", 0) or 0,
                    }
                else:
                    raw_usage = {
                        "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
                        "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
                        "cache_read_input_tokens": getattr(usage_obj, "cache_read_input_tokens", 0) or 0,
                        "cache_creation_input_tokens": getattr(usage_obj, "cache_creation_input_tokens", 0) or 0,
                    }
                computed_cost = pricing.cost_for_usage(turn_model, raw_usage)
                # Fallback chain for robust cost visibility:
                #   1. Our pricing table × token breakdown (best — real
                #      per-model cost including cache).
                #   2. SDK's total_cost_usd (works even when the SDK omits
                #      the usage dict — older Claude Code versions).
                #   3. Sum of per-model costs from the SDK's modelUsage
                #      dict, if present.
                if computed_cost == 0 and message.total_cost_usd:
                    computed_cost = float(message.total_cost_usd)
                if computed_cost == 0:
                    model_usage = getattr(message, "model_usage", None) or {}
                    if isinstance(model_usage, dict):
                        for entry in model_usage.values():
                            if isinstance(entry, dict):
                                computed_cost += float(entry.get("costUSD") or entry.get("cost_usd") or 0)

                # Diagnostic log so the user can correlate the billing pill
                # against the real SDK traffic if something looks off.
                log.info(
                    "Nicolas turn_end: model=%s tokens=%s sdk_cost=%s → recorded=$%.6f",
                    turn_model, raw_usage, message.total_cost_usd, computed_cost,
                )

                # Tag the row with the billing source the turn actually
                # rode. When NICOLAS_USE_SUBSCRIPTION is on we strip the
                # API key from the subprocess env so Claude Code falls
                # back to OAuth — the cost figure is then a *shadow* of
                # what the turn would have cost on the Anthropic API.
                billing_source = (
                    "subscription" if config.NICOLAS_USE_SUBSCRIPTION else "api"
                )
                try:
                    from distillate.agent_runtime import usage_tracker
                    usage_tracker.get_tracker().record(
                        model=turn_model,
                        role="nicolas_turn",
                        session_id=message.session_id,
                        tokens=raw_usage,
                        cost_usd=computed_cost,
                        billing_source=billing_source,
                    )
                except Exception:
                    log.debug("Usage tracker write failed (non-critical)", exc_info=True)

                yield {
                    "type": "turn_end",
                    "session_id": message.session_id,
                    "num_turns": message.num_turns,
                    "model": turn_model,
                    "tokens": {
                        "input": raw_usage["input_tokens"],
                        "output": raw_usage["output_tokens"],
                        "cache_read": raw_usage["cache_read_input_tokens"],
                        "cache_creation": raw_usage["cache_creation_input_tokens"],
                    },
                    "cost_usd": computed_cost,
                    "sdk_reported_cost_usd": message.total_cost_usd,
                }

    def _cost_snapshot(self) -> tuple[float, float]:
        """Return (session_cost_usd, today_cost_usd) — total including shadow.

        Used for the "conversation is getting heavy" compact suggestion,
        where context weight matters even when the user isn't paying
        out-of-pocket on subscription.
        """
        try:
            from distillate.agent_runtime import usage_tracker
            snap = usage_tracker.get_tracker().snapshot(self._session_id)
            session_cost = float(snap.get("session", {}).get("cost_usd", 0.0) or 0.0)
            today_cost = float(snap.get("today", {}).get("cost_usd", 0.0) or 0.0)
            return session_cost, today_cost
        except Exception:
            log.debug("cost snapshot lookup failed", exc_info=True)
            return 0.0, 0.0

    def _api_cost_snapshot(self) -> tuple[float, float]:
        """Return (session_api_cost_usd, today_api_cost_usd) — real dollars only.

        Used by the hard-cap budget guards. Subscription-backed usage
        doesn't count against the user's out-of-pocket spend and must
        not trip blocking warnings. Falls back to total cost for older
        snapshots that predate the billing_source split.
        """
        try:
            from distillate.agent_runtime import usage_tracker
            snap = usage_tracker.get_tracker().snapshot(self._session_id)
            def _pull(bucket_name: str) -> float:
                b = snap.get(bucket_name) or {}
                v = b.get("api_cost_usd")
                if v is None:
                    v = b.get("cost_usd", 0.0)
                return float(v or 0.0)
            return _pull("session"), _pull("today")
        except Exception:
            log.debug("api cost snapshot lookup failed", exc_info=True)
            return 0.0, 0.0

    def _session_cost_usd(self) -> float:
        """Current accumulated cost for this session (in USD)."""
        return self._cost_snapshot()[0]

    def unblock_budget(self) -> None:
        """User confirmed 'continue anyway' — lifts the hard-cap block for
        the remainder of this session. Reset by new_conversation() /
        switch_session()."""
        self._budget_override = True
        log.info("Budget override enabled for session %s", self._session_id)

    def set_budget_thresholds(
        self,
        compact_suggest: float | None = None,
        session_hard: float | None = None,
        day_hard: float | None = None,
    ) -> None:
        """Update runtime budget thresholds and persist to preferences."""
        if compact_suggest is not None:
            self._compact_suggest_usd = float(compact_suggest)
            preferences.set("budget_compact_suggest_usd", self._compact_suggest_usd)
        if session_hard is not None:
            self._session_hard_warn_usd = float(session_hard)
            preferences.set("budget_session_hard_usd", self._session_hard_warn_usd)
        if day_hard is not None:
            self._day_hard_warn_usd = float(day_hard)
            preferences.set("budget_day_hard_usd", self._day_hard_warn_usd)

    def _budget_guard_event(self) -> dict | None:
        """Surface a budget-related event before the next turn.

        Priority order (most severe first):
          1. Session hard cap — BLOCKS the turn unless ``_budget_override``
             is set. Emits ``budget_blocked`` with ``blocking: true``.
          2. Daily hard cap — BLOCKS the turn unless ``_budget_override``
             is set. Same semantics.
          3. Compact suggestion — fires once per session at
             ``DISTILLATE_COMPACT_SUGGEST_USD`` (default $0.50) so the
             user can start a fresh conversation before the prefix
             gets expensive.
          4. Daily soft warn — fires once per day-warn cycle at
             ``DISTILLATE_DAY_SOFT_WARN_USD`` (default $5).
          5. Session soft warn — fires once per session at
             ``DISTILLATE_SESSION_SOFT_WARN_USD`` (default $1).

        All thresholds are env-tunable. Blocking events carry
        ``blocking: true`` so the renderer can present the user with a
        modal before they proceed; non-blocking events are informational.

        Subscription users: hard caps check real API spend only
        (``api_cost_usd``), so OAuth-backed turns can't trip a block.
        The compact suggestion keeps firing on total/shadow cost — it's
        a context-weight signal, not a spend signal, and still useful
        even when the dollars are free.
        """
        session_cost, day_cost = self._cost_snapshot()          # total incl. shadow
        api_session, api_day = self._api_cost_snapshot()        # real dollars only

        # 1–2. Hard caps — real dollars only. Subscription usage won't trip
        # these since api_cost_usd stays at 0 for OAuth-backed turns.
        if not self._budget_override:
            if api_session >= self._session_hard_warn_usd:
                return {
                    "type": "budget_blocked",
                    "reason": "session",
                    "blocking": True,
                    "session_cost_usd": round(api_session, 2),
                    "today_cost_usd": round(api_day, 2),
                    "threshold_usd": self._session_hard_warn_usd,
                }
            if api_day >= self._day_hard_warn_usd:
                return {
                    "type": "budget_blocked",
                    "reason": "day",
                    "blocking": True,
                    "session_cost_usd": round(api_session, 2),
                    "today_cost_usd": round(api_day, 2),
                    "threshold_usd": self._day_hard_warn_usd,
                }

        # 3. Compact suggestion — "this conversation is getting heavy."
        # Keyed on total cost (shadow included) because the goal is to
        # compact the thread before the context prefix gets unwieldy —
        # same win on API or subscription.
        if (
            session_cost >= self._compact_suggest_usd
            and not self._compact_suggested
        ):
            self._compact_suggested = True
            on_subscription = config.NICOLAS_USE_SUBSCRIPTION and api_session < 1e-6
            return {
                "type": "context_warning",
                "session_cost_usd": round(session_cost, 2),
                "api_cost_usd": round(api_session, 2),
                "billing_source": "subscription" if on_subscription else "api",
                "threshold_usd": self._compact_suggest_usd,
            }

        # 4. Daily soft warn — real dollars.
        if api_day >= _DAY_SOFT_WARN_USD and not self._day_soft_warned:
            self._day_soft_warned = True
            return {
                "type": "day_budget_warning",
                "today_cost_usd": round(api_day, 2),
                "threshold_usd": _DAY_SOFT_WARN_USD,
            }

        # 5. Session soft warn — real dollars.
        if (
            api_session >= _SESSION_SOFT_WARN_USD
            and not self._budget_soft_warned
        ):
            self._budget_soft_warned = True
            return {
                "type": "budget_warning",
                "session_cost_usd": round(api_session, 2),
                "threshold_usd": _SESSION_SOFT_WARN_USD,
            }
        return None

    async def send(self, user_input: str) -> AsyncGenerator[dict, None]:
        """Send a message and yield desktop-protocol events.

        On first failure with "Session ID X is already in use", automatically
        reconnects via ``--continue --resume X`` (≈ ``claude -c X``) and
        replays the message once.
        """
        self._last_user_input = user_input
        set_nicolas_state("working")
        # Pre-turn budget check. Non-blocking events (warnings, compact
        # suggestions) just surface info. Blocking events stop the turn
        # until the user calls unblock_budget() or starts a new
        # conversation — this is the real cost-control guardrail.
        budget_event = self._budget_guard_event()
        if budget_event is not None:
            yield budget_event
            if budget_event.get("blocking"):
                set_nicolas_state("idle")
                return
        try:
            await self._ensure_connected()
            await self._client.query(user_input)
            async for event in self._receive_events():
                yield event
        except Exception as exc:
            stale = self._stale_session_id
            if stale and not isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                self._stale_session_id = None
                _evict_stale_session(stale)
                log.warning("Session %s evicted — reconnecting with resume", stale)
                await self.disconnect()
                self._session_id = stale
                await self._ensure_connected()
                await self._client.query(user_input)
                async for event in self._receive_events():
                    yield event
            else:
                raise
        finally:
            # Guarantee idle on any exit path — success, exception, or
            # generator close. Without this, an SDK error mid-turn would
            # leave the bell stuck in "working" forever (the renderer only
            # raises the notification on a transition to "idle").
            set_nicolas_state("idle")

    async def interrupt(self) -> None:
        """Interrupt a running query (sends SIGINT to Claude Code)."""
        # Reset state first — the SDK emits no ResultMessage on interrupt,
        # so without this the tray/bell would stay armed in "working"
        # until the next prompt flipped it back.
        set_nicolas_state("idle")
        if self._client:
            await self._client.interrupt()

    async def set_model(self, model: str) -> None:
        """Change model for subsequent queries and persist the preference."""
        self._model = model
        preferences.set("nicolas_model", model)
        if self._client:
            await self._client.set_model(model)

    async def new_conversation(self, pending_name: str = "") -> None:
        """Start a fresh conversation (disconnect + clear active session).

        The new session_id is assigned on the next send() via session_init.
        If ``pending_name`` is supplied (e.g. experiment name from
        launch_experiment's thread-branch hint), it's stashed on the
        instance so session_init can apply it to the newly minted
        session entry before auto-naming kicks in.
        """
        await self.disconnect()
        from distillate.agent_runtime.lab_repl import reset_sandbox
        reset_sandbox()
        self._session_id = None
        self._pending_thread_name = pending_name or ""
        self._budget_soft_warned = False
        self._compact_suggested = False
        self._budget_override = False
        reg = _load_registry()
        reg["active_session_id"] = None
        _save_registry(reg)

    def list_sessions(self) -> list[dict]:
        """Return sessions sorted by last_activity (most recent first).

        Ensures all sessions have a status field for backwards compatibility.
        """
        reg = _load_registry()
        sessions = reg.get("sessions", [])
        # Ensure all sessions have a status field
        for s in sessions:
            if "status" not in s:
                s["status"] = "idle"
        return sorted(
            sessions,
            key=lambda s: s.get("last_activity", ""),
            reverse=True,
        )

    async def switch_session(self, session_id: str) -> None:
        """Resume a past session by id. Disconnect + reconnect with resume."""
        await self.disconnect()
        from distillate.agent_runtime.lab_repl import reset_sandbox
        reset_sandbox()
        self._session_id = session_id
        self._budget_soft_warned = False
        self._compact_suggested = False
        self._budget_override = False
        reg = _load_registry()
        reg["active_session_id"] = session_id
        _save_registry(reg)

    def rename_session(self, session_id: str, name: str) -> bool:
        """Rename a session in the registry. Returns True if the id exists."""
        reg = _load_registry()
        for s in reg.get("sessions", []):
            if s["session_id"] == session_id:
                s["name"] = name[:120]
                _save_registry(reg)
                return True
        return False

    def set_session_status(self, session_id: str, status: str) -> bool:
        """Update a session's status (e.g., 'waiting', 'idle'). Returns True if found."""
        reg = _load_registry()
        for s in reg.get("sessions", []):
            if s["session_id"] == session_id:
                s["status"] = status
                _save_registry(reg)
                return True
        return False

    async def delete_session(self, session_id: str) -> bool:
        """Drop a session from the registry. Returns True if removed.

        If the session is currently active, disconnects first and clears
        the active pointer so the next turn starts a fresh conversation.
        Claude Code's on-disk .jsonl store is left alone — this only
        removes the registry entry so it disappears from the sidebar.
        """
        reg = _load_registry()
        sessions = reg.get("sessions", [])
        new_sessions = [s for s in sessions if s.get("session_id") != session_id]
        if len(new_sessions) == len(sessions):
            return False
        if self._session_id == session_id:
            await self.disconnect()
            self._session_id = None
            reg["active_session_id"] = None
        reg["sessions"] = new_sessions
        _save_registry(reg)
        return True

    async def disconnect(self) -> None:
        """Clean up the Claude Code subprocess."""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                log.debug("Disconnect error (non-critical)", exc_info=True)
            self._client = None

    @property
    def session_id(self) -> str | None:
        return self._session_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_tool_result(content) -> dict:
    """Extract a JSON-serializable dict from a ToolResultBlock's content."""
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {"output": content[:500] if content else ""}
    if isinstance(content, list):
        texts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        if texts:
            try:
                return json.loads(texts[0])
            except (json.JSONDecodeError, TypeError):
                return {"output": "\n".join(texts)[:500]}
    return {}


def _evict_stale_session(session_id: str) -> None:
    """Kill the zombie Claude Code process that holds session_id and remove its
    session file so the next --resume can acquire the session cleanly.

    Claude Code records active sessions in ~/.claude/sessions/<pid>.json.
    When a process crashes without cleanup, the file stays behind; any
    subsequent --resume <id> hits "Session ID X is already in use" because the
    file exists and the PID might still be running (or a new process re-used it).
    """
    import glob as _glob
    import os as _os
    import signal as _signal

    sessions_dir = _os.path.expanduser("~/.claude/sessions")
    try:
        pattern = _os.path.join(sessions_dir, "*.json")
        for path in _glob.glob(pattern):
            try:
                import json as _json
                data = _json.loads(open(path, "rb").read())
                if data.get("sessionId") == session_id:
                    pid = data.get("pid")
                    if pid:
                        try:
                            _os.kill(pid, _signal.SIGTERM)
                            log.info("Sent SIGTERM to stale Claude process PID %s", pid)
                        except ProcessLookupError:
                            pass  # already dead
                        except PermissionError:
                            log.warning("Cannot kill PID %s (permission denied)", pid)
                    try:
                        _os.unlink(path)
                        log.info("Removed stale session file %s", path)
                    except OSError:
                        pass
                    return
            except (OSError, ValueError, KeyError):
                continue
    except OSError:
        pass
    log.warning("Could not find session file for stale session %s", session_id)


def _classify_error(msg: str) -> str:
    """Map error message to a UI-friendly category."""
    if "credit balance is too low" in msg:
        return "credits_depleted"
    if "authentication_error" in msg or "invalid x-api-key" in msg.lower():
        return "invalid_key"
    if "overloaded" in msg:
        return "overloaded"
    if "rate_limit" in msg:
        return "rate_limited"
    return "unknown"


def _build_dynamic_context(state: State) -> str:
    """Build Nicolas's identity + current library/lab state.

    Appended to Claude Code's default system prompt.  Nicolas gains all
    of Claude Code's capabilities (file editing, bash, web search,
    subagents) while keeping his alchemist identity and Distillate
    domain knowledge.
    """
    # --- Identity override ---
    parts = [
        "# IDENTITY OVERRIDE\n"
        "You are **Nicolas**, a research alchemist — named after Nicolas Flamel. "
        "Do NOT identify as Claude Code or Claude. Your name is Nicolas. "
        "You are the command and control center for a researcher's experimental work.",
    ]

    if config.EXPERIMENTS_ENABLED:
        parts.append(
            " Your primary job is helping them design, launch, monitor, and "
            "analyze autonomous research experiments. You can scaffold new "
            "experiments from templates, launch Claude Code sessions in tmux, "
            "track runs, compare results, and generate lab notebooks."
        )

    parts.append(
        " You also manage their paper library"
        + (
            " — they read and highlight papers in the Zotero app "
            "(on any device), and Distillate extracts highlights and "
            "generates notes."
            if config.is_zotero_reader() else
            " via a Zotero → reMarkable → Obsidian workflow."
        )
        + " You have tools to search their library, read their "
        "highlights and notes, analyze reading patterns, and synthesize "
        "insights across papers.\n\n"
    )

    # --- Lab REPL (placed early so the model attends to it) ---
    parts.append(
        "## Lab REPL — Your Primary Analysis Tool\n"
        "You have a `mcp__distillate__lab_repl` tool: a persistent Python "
        "sandbox for multi-step reasoning. **USE IT** whenever a question:\n"
        "- Spans multiple papers AND experiments (cross-entity)\n"
        "- Requires filtering, ranking, or comparison\n"
        "- Needs multi-step analysis or synthesis\n"
        "- Would otherwise require 3+ sequential tool calls\n\n"
        "The sandbox persists variables across calls. Available:\n"
        "```\n"
        "lab.papers.search(q), .get(key), .recent(), .queue(), .stats(), .highlights(key)\n"
        "lab.experiments.list(), .get(id), .runs(id), .run_details(p,r), .active()\n"
        "lab.notebook.recent(n), .digest(days)\n"
        "lab.experiments.list(), .get(id), .papers(id)\n"
        "llm_query(prompt)           — fast sub-LLM call (Haiku)\n"
        "delegate(prompt, context)   — recursive sub-agent with data\n"
        "delegate_batch(tasks)       — parallel sub-agents\n"
        "FINAL(answer)               — return your final answer\n"
        "```\n"
        "Always provide a human-readable `description` parameter.\n"
        "For quick single lookups, use the specific MCP tools directly.\n\n"

        "### Reflex: search before admitting ignorance\n"
        "Before saying \"I don't know\" or asking the user to supply facts "
        "about any **named model, paper, dataset, benchmark, or method** "
        "(e.g. \"GlycoBART\", \"ESM-2\", \"HelixFold\"), you MUST first call:\n"
        "```\n"
        "lab.papers.search(\"<name>\")      # library hit?\n"
        "lab.papers.recent(20)              # was it read recently?\n"
        "lab.experiments.active()           # is it the active experiment's baseline?\n"
        "```\n"
        "If any of those return a hit, read the paper/experiment details "
        "(`lab.papers.get(...)`, `lab.experiments.get(...)`) and answer "
        "from what you find. Only ask the user to fill in specs after the "
        "library search comes up empty — and say so explicitly "
        "(\"I searched your library for X and didn't find it; can you share "
        "the citation?\").\n\n"
    )

    # --- Local time (date + timezone only — minute precision would bust
    # the prompt cache on every turn, forcing expensive cache recreation.
    # If Nicolas needs the exact wall-clock time for a specific task, it
    # can evaluate datetime.now() inside the lab_repl sandbox.)
    _local_now = datetime.now().astimezone()
    _tz_name = _local_now.tzname() or ""
    _utc_offset = _local_now.strftime("%z")  # e.g. "-0700"
    _utc_offset_fmt = (
        f"{_utc_offset[:3]}:{_utc_offset[3:]}" if len(_utc_offset) == 5 else _utc_offset
    )
    parts.append(
        "## Local Time\n"
        f"Today is {_local_now.strftime('%Y-%m-%d')} in "
        f"{_tz_name} (UTC{_utc_offset_fmt}).\n"
        "Tool results, lab notebook entries, and experiment logs often "
        "contain UTC timestamps (ISO-8601 ending in `Z` or `+00:00`). "
        "**Always convert these to the user's local time** before "
        "presenting them — never show raw UTC. Subtract/add the UTC "
        f"offset ({_utc_offset_fmt}) to get local hours.\n\n"
    )

    # --- Context block: injected by the renderer when user has a selection ---
    parts.append(
        "## Context Block — CRITICAL\n"
        "When the user's message starts with `[Context: ...]`, that block "
        "is injected by the UI and tells you exactly what the user is "
        "currently looking at. **Treat it as a direct answer to any "
        "\"which experiment / project / paper?\" question.** You MUST:\n"
        "- NEVER ask \"which experiment?\" / \"which project?\" / \"which run?\" "
        "if a `[Context: ...]` block is present — the answer is already there.\n"
        "- NEVER ask the user to clarify a reference that the context block resolves.\n"
        "- Use the project name / id from the block DIRECTLY in the first tool call "
        "— no look-up round-trips needed.\n"
        "Example: `[Context: user is currently viewing project \"Glyco DFM V1\" "
        "(id=abc-123)]` + user says \"check in on the experiment\" → "
        "call `experiment_status(project=\"Glyco DFM V1\")` immediately. "
        "Do not ask \"which experiment?\". Do not ask where it's running. "
        "Look it up.\n\n"
    )

    # --- Workflows: common user intents → tool sequences ---
    parts.append(
        "## Workflows — Common User Intents\n"
        "When the user asks to do X, prefer the matching tool sequence:\n"
        "- \"Start a coding session in [project]\" → "
        "If the user's message includes a `[Context: ...]` block with "
        "an active project ID, call `launch_coding_session` directly "
        "with `workspace=ID` — skip `list_workspaces`. Otherwise: "
        "`list_workspaces` to find the workspace ID, then "
        "`launch_coding_session(workspace_id)`.\n"
        "- \"Start a new experiment about X\" → "
        "`init_experiment` to scaffold (writes PROMPT.md), show the "
        "PROMPT.md to the user for review **wrapped in a "
        "`> [!experiment]` callout block** (the renderer frames it as "
        "an experiment scaffold preview), then `launch_experiment`\n"
        "- \"Continue experiment X\" → "
        "`manage_session(action=\"continue\", project=\"X\")`\n"
        "- \"Show what's running\" → `lab_repl` with "
        "`lab.experiments.active()` (returns experiments with active sessions, "
        "including tmux_session names and total_runs count)\n"
        "- \"Is run N still training? / What's the status of run N?\" → "
        "`lab_repl`: call `lab.experiments.status()` to refresh live session "
        "state, then find the experiment whose `total_runs >= N` and check "
        "whether it has an active session. For exact run details: "
        "`lab.experiments.get('<name>')` then inspect `runs`. NEVER ask the user.\n"
        "- \"Which experiment is run N in?\" → `lab_repl`: "
        "`status = lab.experiments.status(); "
        "[e for e in status['experiments'] if e['total_runs'] >= N and e['active_sessions'] > 0]` "
        "to find active ones, or `lab.experiments.get('<name>')` for full run list.\n"
        "- \"Compare runs A and B\" → `compare_runs(project, run_a, run_b)`\n"
        "- \"Steer experiment X to do Y\" → "
        "`steer_experiment(project=\"X\", text=Y)` — it resolves the running "
        "tmux session internally and injects the text directly into the agent's "
        "Claude Code TUI (typed + submitted). No tmux session name needed.\n"
        "- \"Inject a message into the running agent\" / \"tell the agent X\" → "
        "same as above: `steer_experiment(project=X, text=Y)`. This is the ONLY "
        "correct way to redirect a running Experimentalist. NEVER ask the user "
        "for the tmux session name; NEVER use raw `tmux send-keys`.\n"
        "- \"/btw\", \"ask the agent\", \"check in with the experiment\", "
        "\"what is the agent thinking\", \"what's in its context\", "
        "\"ask it X without stopping it\", \"ping the experiment\" → "
        "`ask_experimentalist(project=X, question=Y)`. "
        "This is the FAST PATH for querying a running Experimentalist without "
        "redirecting it. Under the hood it injects Claude Code's built-in "
        "`/btw` slash command, which spawns a read-only ephemeral sub-agent "
        "(reuses prompt cache, ~2-5s, not saved to history) and dismisses "
        "cleanly with Escape so the research loop is never disrupted.\n"
        "  WHEN TO USE — reach for ask_experimentalist whenever you want to "
        "know something the agent knows from its own context window:\n"
        "    · 'What run are you on?' · 'What's your current best metric?'\n"
        "    · 'What's your plan for the next run?' · 'Why did that run crash?'\n"
        "    · 'What does your PROMPT.md say?' · 'What hyperparams are you using?'\n"
        "    · 'Are you stuck / what are you doing right now?'\n"
        "    · 'What's in your context that I can't see from the logs?'\n"
        "  READING THE RESULT:\n"
        "    · answer: the sub-agent's response — present this to the user directly.\n"
        "    · current_pane: live terminal snapshot — often answers passively "
        "(e.g. current loss, bash output) without needing to read answer.\n"
        "    · timed_out=true: agent is mid-bash (long training run). The /btw "
        "is buffered and will fire between turns — tell the user and offer to "
        "call again in a moment, or read current_pane for partial info.\n"
        "  steer_experiment = directive (change course, authoritative); "
        "ask_experimentalist = query (answer and keep going, ephemeral).\n"
        "- \"Find papers about X\" → `lab_repl` with "
        "`lab.papers.search(\"X\")`\n"
        "- \"What's on the reading queue?\" → `lab_repl` with "
        "`lab.papers.queue()`\n"
        "- \"Recap the lab notebook\" → `lab_repl` with "
        "`lab.notebook.read()` or `lab.notebook.digest()`\n"
        "- \"What patterns / trends across X\" → `lab_repl` (multi-step)\n"
        "- \"Diagnose this failing experiment\" → `lab_repl` with "
        "`delegate()` to analyze logs, then cross-reference papers\n"
        "Always confirm destructive actions (delete, stop running session) "
        "with the user before calling the tool.\n\n"
    )

    # --- Delegation + tool-use guardrails (tight — detail lives in schemas) ---
    parts.append(
        "## Tool-Use Guardrails\n"
        "- Do NOT call `set_thread_name` — thread naming is automatic.\n"
        "- Do NOT use the `Agent`/`Task` tool for routine delegation. "
        "It spawns a full Claude Code sub-process (expensive, can hang). "
        "Use `lab_repl` with `delegate()` for analysis, or "
        "`start_agent_session` for specialist workflows.\n"
        "- Do NOT improvise with Bash/`python3`/`tmux`/`claude` when an "
        "MCP tool exists for the job — the MCP server has 70+ tools for "
        "launching, monitoring, and steering experiments. If an MCP tool "
        "fails, tell the user to restart the app; don't work around it.\n"
        "- **NEVER use raw `tmux send-keys` or any tmux command to inject "
        "text into a running Experimentalist agent.** Use "
        "`steer_experiment(project=X, text=Y)` for directives or "
        "`ask_experimentalist(project=X, question=Y)` for queries — both "
        "resolve the session and send Enter automatically. If you bypass "
        "these tools and use raw `tmux send-keys` from lab_repl or Bash, "
        "you MUST immediately follow with a second "
        "`tmux send-keys -t <name> Enter` call — omitting Enter leaves the "
        "message unsubmitted. Best practice: never bypass these tools.\n"
        "- **NEVER run experiment code directly via Bash, Python scripts, "
        "or shell commands.** Always use `init_experiment` → user reviews "
        "PROMPT.md → `launch_experiment`. Running scripts directly bypasses "
        "experiment tracking, run logging, and the lab notebook — the user "
        "will not see the experiment in the Experiments view. If you are "
        "ever tempted to run `python train.py`, `bash run.sh`, or spawn a "
        "background task for research work, stop and use `launch_experiment` "
        "instead. There are NO exceptions to this rule.\n"
        "- **NEVER ask the user for information you can look up via `lab_repl` "
        "or other tools.** If you want to know whether a run is still training, "
        "what the latest metrics are, whether a session has finished, the state "
        "of an experiment, or any other observable lab state — call `lab_repl` "
        "first. Asking the user \"is run 38 still training?\" when you have "
        "direct tool access is a workflow violation. Look it up, then respond.\n\n"
    )

    # Experiment state and recent notebook entries used to be injected
    # here, but both churn on every turn (run counts, commit deltas,
    # notebook growth) and busted the prompt cache — multiplying cost
    # by ~5x in long conversations. Nicolas pulls them on demand via
    # lab.experiments.list() / lab.notebook.recent() instead.

    # --- Active agents ---
    agent_lines = []
    for _aid, agent in state.agents.items():
        if agent.get("agent_type") == "nicolas":
            continue  # skip self
        status = agent.get("session_status", "stopped")
        line = f"- {agent.get('name', _aid)}: {status}"
        pid = agent.get("workspace_id")
        if pid:
            ws = state.get_workspace(pid)
            if ws:
                line += f" (linked to {ws.get('name', pid)})"
        agent_lines.append(line)
    if agent_lines:
        parts.append("## Agents\n" + "\n".join(agent_lines) + "\n\n")

    # --- Personality ---
    parts.append(
        "## Personality\n"
        "You're warm, witty, and genuinely curious about the user's research. "
        "Think of yourself as a fellow scholar who happens to live in an "
        "alchemist's workshop — you might say a paper's findings are "
        "\"pure gold\" or that you'll \"distill the key insights.\" Keep the "
        "alchemy flavor light and natural, not forced. Show enthusiasm when "
        "a paper is interesting. Be opinionated — if a result is "
        "surprising or a method is clever, say so.\n\n"
    )

    # --- Guidelines (tight — tool-level detail already lives in the schemas) ---
    parts.append(
        "## Guidelines\n"
        "- Distillate tools are prefixed `mcp__distillate__`. Built-ins "
        "available: Read, Edit, Write, Glob, Grep, WebSearch, WebFetch.\n"
        "- Look up papers/experiments with tools before answering — don't "
        "guess from memory. Show paper [index] refs and **bold titles**.\n"
        "- Specialists (`start_agent_session`): paper-reader, "
        "write-up-drafter, result-checker, find-papers, experiment-monitor. "
        "Always pass a concrete `initial_task`. Use them for sustained "
        "work; use your own tools for quick lookups.\n"
        "- Sprinkle one or two alchemy emojis (⚗️ 🧪 🔬 ✨ 📜) inline, "
        "never at the start of a message.\n"
        "- Confirm destructive writes. Keep responses concise. End with a "
        "statement, not a question.\n"
    )
    return "".join(parts)
