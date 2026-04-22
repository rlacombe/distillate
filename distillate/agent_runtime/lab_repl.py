"""Lab REPL sandbox for Nicolas's recursive reasoning.

Provides a persistent Python sandbox where Nicolas can write code to
query the lab, spawn sub-LLM calls, and build up multi-step analysis.
Adapted from the Recursive Language Models paradigm (Zhang, Kraska,
Khattab — arXiv 2512.24601).

Security model: AST pre-scan + restricted builtins allowlist.
The threat model is accidental damage from LLM-generated code, not
adversarial exploitation.
"""

import ast
import io
import json
import logging
import math
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from distillate import config

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

# Pricing lives in distillate.pricing (single source of truth).
# ``_DEFAULT_PRICE`` below is the (input, output) pair used by the legacy
# budget-guard path, which only tracks a running input/output count.
from distillate import pricing

_DEFAULT_PRICE = (pricing.DEFAULT_PRICE[0], pricing.DEFAULT_PRICE[1])


@dataclass
class CostTracker:
    """Track token usage and estimated cost for sub-LLM calls.

    Has two responsibilities:
      1. In-tracker counters + budget guards (legacy behavior).
      2. Per-record append to the global :mod:`usage_tracker` so the billing
         UI sees every sub-LLM call alongside Nicolas's root turns. The
         per-record path uses :func:`pricing.cost_for_usage` for accurate
         per-model cost (including cache tokens), whereas the legacy
         aggregate cost below still uses ``_DEFAULT_PRICE`` because it's
         only consulted for rough budget ceilings.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    api_calls: int = 0
    session_budget_usd: float = 2.00
    call_budget_usd: float = 0.50
    _call_input: int = field(default=0, repr=False)
    _call_output: int = field(default=0, repr=False)
    _session_id: str = field(default="", repr=False)

    @property
    def estimated_cost_usd(self) -> float:
        inp, out = _DEFAULT_PRICE
        return (self.input_tokens * inp + self.output_tokens * out) / 1_000_000

    @property
    def call_cost_usd(self) -> float:
        inp, out = _DEFAULT_PRICE
        return (self._call_input * inp + self._call_output * out) / 1_000_000

    def begin_call(self) -> None:
        self._call_input = 0
        self._call_output = 0

    def set_session(self, session_id: str) -> None:
        """Associate sub-LLM records with Nicolas's current session_id so
        the billing UI can attribute them correctly.
        """
        self._session_id = session_id or ""

    def _resolve_session_id(self) -> str:
        """Return the Nicolas session to attribute sub-LLM costs to.

        Checks in order: explicit ``set_session`` value, then the active
        session recorded in ``nicolas_sessions.json`` (which Nicolas
        updates on every ``session_init``). The registry lookup lets the
        MCP subprocess attribute sub-LLM costs without the parent
        process having to push session updates over the MCP transport.
        """
        if self._session_id:
            return self._session_id
        try:
            from distillate import config as _config
            import json as _json
            path = _config.NICOLAS_SESSIONS_FILE
            if path.exists():
                reg = _json.loads(path.read_text())
                sid = reg.get("active_session_id") or ""
                if isinstance(sid, str) and sid:
                    return sid
        except Exception:
            log.debug("Nicolas session lookup failed", exc_info=True)
        return ""

    def record(self, response: Any, model: str = "") -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        i = getattr(usage, "input_tokens", 0) or 0
        o = getattr(usage, "output_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        cc = getattr(usage, "cache_creation_input_tokens", 0) or 0
        self._record_tokens(model, i, o, cr, cc, cost_usd=None, billing_source="api")

    def record_cli(self, data: dict, model: str = "") -> None:
        """Record usage from a ``claude -p --output-format json`` response.

        The CLI reports a shadow ``total_cost_usd`` (what the call would
        cost via API). We keep it as-is for budget guards and cost
        modelling, but tag the row ``billing_source="subscription"`` so
        the UI can split real API spend from subscription-backed usage.
        """
        usage = (data or {}).get("usage") or {}
        i = int(usage.get("input_tokens") or 0)
        o = int(usage.get("output_tokens") or 0)
        cr = int(usage.get("cache_read_input_tokens") or 0)
        cc = int(usage.get("cache_creation_input_tokens") or 0)
        cost = data.get("total_cost_usd") if data else None
        try:
            cost_val = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost_val = None
        # Fall back to the first model key in `modelUsage` if the caller
        # didn't pin one — the CLI's own accounting is authoritative.
        if not model:
            mu = (data or {}).get("modelUsage") or {}
            if mu:
                model = next(iter(mu.keys()), "")
        self._record_tokens(
            model, i, o, cr, cc,
            cost_usd=cost_val,
            billing_source="subscription",
        )

    def _record_tokens(
        self,
        model: str,
        i: int,
        o: int,
        cr: int,
        cc: int,
        *,
        cost_usd: float | None,
        billing_source: str,
    ) -> None:
        self.input_tokens += i
        self.output_tokens += o
        self._call_input += i
        self._call_output += o
        self.api_calls += 1

        # Bridge to the global usage tracker so the billing UI reflects
        # sub-LLM cost in real time. Resolves the active Nicolas session
        # from the registry if no explicit session was set, so MCP-spawned
        # sub-calls also land in usage.jsonl.
        session_id = self._resolve_session_id()
        if session_id and model:
            try:
                from distillate.agent_runtime import usage_tracker
                usage_tracker.get_tracker().record(
                    model=model,
                    role="lab_repl_subcall",
                    session_id=session_id,
                    tokens={
                        "input_tokens": i,
                        "output_tokens": o,
                        "cache_read_input_tokens": cr,
                        "cache_creation_input_tokens": cc,
                    },
                    cost_usd=cost_usd,
                    billing_source=billing_source,
                )
            except Exception:
                log.debug("Sub-LLM usage tracker write failed (non-critical)", exc_info=True)

    def check_session_budget(self) -> None:
        if self.estimated_cost_usd > self.session_budget_usd:
            raise BudgetExhaustedError(
                f"Session cost ${self.estimated_cost_usd:.3f} "
                f"exceeds budget ${self.session_budget_usd:.2f}"
            )

    def check_call_budget(self) -> None:
        if self.call_cost_usd > self.call_budget_usd:
            raise BudgetExhaustedError(
                f"Call cost ${self.call_cost_usd:.3f} "
                f"exceeds per-call budget ${self.call_budget_usd:.2f}"
            )

    def summary(self) -> dict:
        return {
            "api_calls": self.api_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "est_usd": round(self.estimated_cost_usd, 4),
        }


class BudgetExhaustedError(Exception):
    pass


# ---------------------------------------------------------------------------
# AST security scanner
# ---------------------------------------------------------------------------

_BLOCKED_DUNDERS = frozenset({
    "__class__", "__globals__", "__subclasses__", "__code__",
    "__builtins__", "__import__", "__bases__", "__mro__",
    "__init_subclass__", "__set_name__",
})

_BLOCKED_CALLS = frozenset({
    "eval", "exec", "compile", "open", "breakpoint", "exit", "quit",
    "__import__", "input", "globals", "locals",
})


class _SecurityVisitor(ast.NodeVisitor):
    """Reject unsafe AST constructs."""

    def __init__(self):
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        names = ", ".join(a.name for a in node.names)
        self.errors.append(f"import not allowed: {names}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.errors.append(f"import not allowed: from {node.module}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _BLOCKED_DUNDERS:
            self.errors.append(f"access to '{node.attr}' not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_CALLS:
            self.errors.append(f"call to '{node.func.id}' not allowed")
        self.generic_visit(node)


def _validate_ast(code: str) -> list[str]:
    """Parse and validate code. Returns list of error messages (empty = OK)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]
    visitor = _SecurityVisitor()
    visitor.visit(tree)
    return visitor.errors


# ---------------------------------------------------------------------------
# Safe builtins
# ---------------------------------------------------------------------------

_SAFE_BUILTIN_NAMES = {
    "abs", "all", "any", "bool", "bytearray", "bytes", "callable", "chr",
    "complex", "dict", "divmod", "enumerate", "filter", "float",
    "format", "frozenset", "hasattr", "hash", "hex", "id", "int",
    "isinstance", "issubclass", "iter", "len", "list", "map", "max",
    "min", "next", "oct", "ord", "pow", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple", "type",
    "zip",
}

import builtins as _builtins_mod


def _make_safe_builtins() -> dict:
    safe = {}
    for name in _SAFE_BUILTIN_NAMES:
        obj = getattr(_builtins_mod, name, None)
        if obj is not None:
            safe[name] = obj
    safe["True"] = True
    safe["False"] = False
    safe["None"] = None
    return safe


# ---------------------------------------------------------------------------
# FINAL mechanism
# ---------------------------------------------------------------------------

class FinalResult(Exception):
    """Raised by FINAL() to halt execution and return a result."""

    def __init__(self, value: str):
        self.value = str(value)
        super().__init__(self.value)


def _final(value: Any) -> None:
    """Return a final answer to the user. Halts execution."""
    raise FinalResult(str(value))


def _final_var(var_name: str, namespace: dict) -> None:
    """Return the contents of a variable as the final answer."""
    if var_name not in namespace:
        available = [k for k in namespace if not k.startswith("_")]
        raise NameError(
            f"Variable '{var_name}' not found. "
            f"Available: {', '.join(sorted(available)[:20])}"
        )
    raise FinalResult(str(namespace[var_name]))


# ---------------------------------------------------------------------------
# Sub-LLM calls
# ---------------------------------------------------------------------------

# Module-level executor for concurrent sub-calls
_executor = ThreadPoolExecutor(max_workers=6)
_cost_tracker = CostTracker()
_lock = threading.Lock()


def _get_anthropic_client():
    """Lazy-init sync Anthropic client."""
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Subscription transport — `claude -p --output-format json`
# ---------------------------------------------------------------------------

_CLAUDE_CLI_TIMEOUT = 120  # seconds — generous for Haiku cold starts


def _claude_cli_path() -> str | None:
    """Resolve the Claude Code CLI binary (or None if unavailable)."""
    return shutil.which("claude")


def _llm_query_subscription(
    prompt: str,
    model: str,
    system: str,
) -> tuple[str, dict | None]:
    """Route a one-shot LLM call through ``claude -p`` (OAuth subscription).

    Returns ``(text, json_payload)``. ``json_payload`` is ``None`` on
    unrecoverable errors so the caller can decide whether to fall back.
    """
    cli = _claude_cli_path()
    if cli is None:
        return "ERROR: claude CLI not found on PATH", None

    # Strip API keys so the CLI falls back to its OAuth subscription.
    # Parent env otherwise wins because CLI precedence is API key first.
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    args = [
        cli, "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--no-session-persistence",
    ]
    if system:
        args.extend(["--system-prompt", system])

    try:
        proc = subprocess.run(
            args,
            env=env,
            capture_output=True,
            text=True,
            timeout=_CLAUDE_CLI_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: claude -p timed out after {_CLAUDE_CLI_TIMEOUT}s", None
    except FileNotFoundError:
        return "ERROR: claude CLI disappeared from PATH", None

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if not stdout:
        msg = stderr[:200] if stderr else f"exit {proc.returncode}"
        return f"ERROR: claude -p returned no output ({msg})", None

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return f"ERROR: could not parse claude -p output: {stdout[:200]}", None

    if data.get("is_error") or proc.returncode != 0:
        reason = data.get("result") or stderr or f"exit {proc.returncode}"
        return f"ERROR: {reason}", data

    text = str(data.get("result") or "").strip()
    return text, data


def _llm_query(
    prompt: str,
    model: str | None = None,
    max_tokens: int = 2048,
    system: str = "",
) -> str:
    """Single synchronous sub-LLM call.

    Routes through ``claude -p`` when :data:`config.NICOLAS_USE_SUBSCRIPTION`
    is set (default), falling back to the Anthropic SDK otherwise. The
    subscription path records usage into ``usage.jsonl`` tagged
    ``billing_source="subscription"`` so the billing UI can split real
    API spend from subscription-backed usage.
    """
    use_model = model or config.CLAUDE_FAST_MODEL

    if config.NICOLAS_USE_SUBSCRIPTION:
        text, data = _llm_query_subscription(prompt, use_model, system)
        if data is not None:
            with _lock:
                _cost_tracker.record_cli(data, use_model)
                try:
                    _cost_tracker.check_session_budget()
                except BudgetExhaustedError:
                    raise
        return text

    if not config.ANTHROPIC_API_KEY:
        return "ERROR: No Anthropic API key configured"

    try:
        client = _get_anthropic_client()
        kwargs: dict[str, Any] = {
            "model": use_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)

        with _lock:
            _cost_tracker.record(response, use_model)
            _cost_tracker.check_session_budget()

        return response.content[0].text.strip()
    except BudgetExhaustedError:
        raise
    except Exception as e:
        log.exception("llm_query failed")
        return f"ERROR: {e}"


def _llm_query_batch(
    prompts: list[str],
    model: str | None = None,
    max_tokens: int = 2048,
) -> list[str]:
    """Parallel sub-LLM calls via ThreadPoolExecutor."""
    futures = [
        _executor.submit(_llm_query, p, model, max_tokens)
        for p in prompts
    ]
    return [f.result(timeout=120) for f in futures]


# ---------------------------------------------------------------------------
# Delegate — recursive sub-agent loop
# ---------------------------------------------------------------------------

_DELEGATE_SYSTEM = (
    "You are a research analysis sub-agent in the Distillate lab. "
    "You have a Python sandbox with pre-loaded variables.\n\n"
    "Write Python code in ```python fenced blocks to analyze the data. "
    "Call FINAL(answer) with your answer as a string when done.\n\n"
    "Available in your namespace:\n"
    "- Any variables described in the task (e.g. 'context')\n"
    "- lab.papers.*, lab.experiments.*, lab.notebook.*, lab.experiments.*\n"
    "- llm_query(prompt) — call an LLM for classification/extraction\n"
    "- print() — for intermediate output\n"
    "- FINAL(answer) — return your final answer (must be a string)\n\n"
    "Rules:\n"
    "1. Do not import anything. All tools are pre-loaded.\n"
    "2. Be concise — you have a limited turn budget.\n"
    "3. Call FINAL(answer) as soon as you have the answer.\n"
)

_RESERVED_TOOL_NAMES = frozenset({
    "FINAL", "__builtins__", "_llm_query", "_delegate", "_delegate_batch",
    "llm_query_batch", "context", "lab", "print", "exec", "eval",
})


def _validate_tool_name(name: str) -> str | None:
    """Check if a tool name is valid for the sandbox.

    Returns None if valid, otherwise an error string.
    """
    if not name.isidentifier():
        return f"Tool name '{name}' is not a valid Python identifier"
    if name in _RESERVED_TOOL_NAMES:
        return f"Tool name '{name}' is reserved"
    return None


def _extract_code_block(text: str) -> str | None:
    """Extract the first fenced Python code block from LLM output."""
    pattern = r"```(?:python)?\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else None


@dataclass
class _DelegateStep:
    """Outcome of one delegate turn after code extraction + exec.

    ``final`` short-circuits the loop. ``next_user_msg`` is the user
    message to feed into the next LLM turn (both transports share the
    same feedback protocol).
    """
    final: str | None = None
    next_user_msg: str | None = None


def _build_delegate_sandbox(context: Any, state: Any) -> tuple[dict[str, Any], str]:
    """Build the delegate's sub-sandbox + initial user message.

    Shared between the API and subscription delegate paths so both
    expose the exact same namespace and context protocol.
    """
    from distillate.agent_runtime.lab_api import LabAPI
    import sys

    sub_ns: dict[str, Any] = {}
    sub_ns["__builtins__"] = _make_safe_builtins()
    sub_ns["FINAL"] = _final
    sub_ns["print"] = print  # will be captured per turn
    sub_ns["llm_query"] = _llm_query
    sub_ns["llm_query_batch"] = _llm_query_batch
    sub_ns["math"] = math
    sub_ns["json"] = json
    sub_ns["re"] = re
    sub_ns["statistics"] = statistics
    sub_ns["datetime"] = datetime
    sub_ns["timedelta"] = timedelta
    sub_ns["deepcopy"] = deepcopy
    sub_ns["OrderedDict"] = OrderedDict

    if state is not None:
        sub_ns["lab"] = LabAPI(state)

    if context is not None:
        # Safeguard: warn if context is suspiciously large (> 100MB),
        # as this can cause memory spikes when deepcopied in parallel.
        context_size = sys.getsizeof(context)
        if context_size > 100_000_000:
            log.warning(
                f"Large context in delegate sandbox: {context_size / 1e6:.1f}MB. "
                "This will be deepcopied; consider filtering context."
            )
        sub_ns["context"] = deepcopy(context)

    user_msg = ""  # prompt gets prepended by caller
    if context is not None:
        user_msg = (
            "\n\nA variable named 'context' has been pre-loaded with the data "
            "described above. Explore it programmatically."
        )
    return sub_ns, user_msg


def _extract_fallback_result(sub_ns: dict[str, Any], last_output: str) -> str | None:
    """Try to extract a meaningful result from the delegate's final state.

    Called when max_iterations is reached without FINAL(). Tries:
      1. Obvious variable names: answer, result, output, findings, response
      2. Last code output (if any)
      3. None (fallback failed, caller will return an error)
    """
    for var_name in ("answer", "result", "output", "findings", "response"):
        if var_name in sub_ns:
            try:
                return str(sub_ns[var_name])
            except Exception:
                pass
    if last_output:
        return last_output
    return None


def _run_delegate_turn(text: str, sub_ns: dict[str, Any]) -> _DelegateStep:
    """Process one LLM response: extract code, validate, exec, build next input.

    Returns a :class:`_DelegateStep` — either the final answer (when no
    code block is present or FINAL was raised) or the next user message
    to feed back into the LLM.
    """
    code = _extract_code_block(text)
    if code is None:
        # No code block — treat the text as the final answer.
        return _DelegateStep(final=text)

    errors = _validate_ast(code)
    if errors:
        return _DelegateStep(
            next_user_msg=f"Security error: {'; '.join(errors)}\nFix your code.",
        )

    stdout_capture = io.StringIO()
    sub_ns["print"] = lambda *a, **kw: print(*a, file=stdout_capture, **kw)

    try:
        exec(code, sub_ns)  # noqa: S102
        output = stdout_capture.getvalue()
        return _DelegateStep(
            next_user_msg=f"Code output:\n{output}" if output else "(no output)",
        )
    except FinalResult as f:
        output = stdout_capture.getvalue()
        result = f.value
        if output:
            result = f"{output}\n---\n{result}"
        return _DelegateStep(final=result)
    except Exception as e:
        return _DelegateStep(
            next_user_msg=f"Error: {type(e).__name__}: {e}\nFix your code.",
        )


def _delegate_subscription(
    prompt: str,
    context: Any,
    use_model: str,
    max_turns: int,
    state: Any,
) -> str:
    """Multi-turn delegate via ``claude -p`` with session resume.

    First call pins a random session UUID via ``--session-id``; later
    calls resume it with ``--resume``. Each turn parses the JSON stdout
    blob so the per-turn usage lands in usage.jsonl tagged as
    subscription spend, same as :func:`_llm_query_subscription`.
    Built-in Claude Code tools are disabled (``--tools ""``) — all code
    execution happens in our sandbox, never in the sub-agent's host.
    """
    import uuid as _uuid

    cli = _claude_cli_path()
    if cli is None:
        return "ERROR: claude CLI not found on PATH"

    sub_ns, context_suffix = _build_delegate_sandbox(context, state)
    session_uuid = str(_uuid.uuid4())

    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    current_input = prompt + context_suffix
    last_input = current_input

    for turn in range(max_turns):
        try:
            with _lock:
                _cost_tracker.check_session_budget()
        except BudgetExhaustedError:
            return "BUDGET_EXCEEDED: session cost limit reached"

        if turn == 0:
            args = [
                cli, "-p", current_input,
                "--output-format", "json",
                "--model", use_model,
                "--session-id", session_uuid,
                "--system-prompt", _DELEGATE_SYSTEM,
                "--tools", "",
            ]
        else:
            args = [
                cli, "-p", current_input,
                "--output-format", "json",
                "--resume", session_uuid,
                "--tools", "",
            ]

        try:
            proc = subprocess.run(
                args, env=env,
                capture_output=True, text=True,
                timeout=_CLAUDE_CLI_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: delegate turn {turn} timed out after {_CLAUDE_CLI_TIMEOUT}s"

        stdout = (proc.stdout or "").strip()
        if not stdout:
            stderr = (proc.stderr or "").strip()[:200]
            return f"ERROR: delegate turn {turn} returned no output ({stderr or 'exit '+str(proc.returncode)})"

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return f"ERROR: could not parse delegate turn {turn} output"

        if data.get("is_error") or proc.returncode != 0:
            reason = data.get("result") or (proc.stderr or "").strip()[:200] or f"exit {proc.returncode}"
            return f"ERROR: delegate turn {turn}: {reason}"

        with _lock:
            _cost_tracker.record_cli(data, use_model)

        text = str(data.get("result") or "").strip()
        step = _run_delegate_turn(text, sub_ns)
        if step.final is not None:
            return step.final
        last_input = step.next_user_msg or ""
        current_input = last_input

    # Max iterations reached. Try fallback extraction before giving up.
    fallback = _extract_fallback_result(sub_ns, last_input)
    if fallback:
        return f"(Reached iteration limit. Extracted from state:)\n{fallback}"
    return (
        f"ERROR: completed {max_turns} turns without reaching FINAL(). "
        f"Last output: {last_input[:500]}"
    )


def _delegate(
    prompt: str,
    context: Any = None,
    model: str | None = None,
    max_turns: int = 5,
    state: Any = None,
) -> str:
    """Recursive sub-agent loop: LLM → code → exec → repeat.

    Routes through ``claude -p`` subscription when
    :data:`config.NICOLAS_USE_SUBSCRIPTION` is set (default), falling
    back to the Anthropic SDK when the user is on explicit API billing.
    """
    use_model = model or config.CLAUDE_FAST_MODEL

    if config.NICOLAS_USE_SUBSCRIPTION:
        return _delegate_subscription(prompt, context, use_model, max_turns, state)

    if not config.ANTHROPIC_API_KEY:
        return "ERROR: No Anthropic API key configured"

    sub_ns, context_suffix = _build_delegate_sandbox(context, state)
    user_msg = prompt + context_suffix
    messages: list[dict[str, str]] = [
        {"role": "user", "content": user_msg},
    ]

    client = _get_anthropic_client()

    for turn in range(max_turns):
        try:
            with _lock:
                _cost_tracker.check_session_budget()

            response = client.messages.create(
                model=use_model,
                max_tokens=4096,
                system=_DELEGATE_SYSTEM,
                messages=messages,
            )

            with _lock:
                _cost_tracker.record(response, use_model)

            text = response.content[0].text.strip()
        except BudgetExhaustedError:
            return "BUDGET_EXCEEDED: session cost limit reached"
        except Exception as e:
            log.exception("delegate LLM call failed at turn %d", turn)
            return f"ERROR: LLM call failed — {e}"

        step = _run_delegate_turn(text, sub_ns)
        if step.final is not None:
            return step.final
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": step.next_user_msg or ""})

    return (
        f"BUDGET_EXCEEDED: completed {max_turns} turns without reaching FINAL(). "
        f"Last output: {messages[-1].get('content', '')[:500]}"
    )


def _delegate_batch(
    tasks: list[dict],
    model: str | None = None,
    state: Any = None,
) -> list[str]:
    """Parallel delegates via ThreadPoolExecutor."""
    futures = [
        _executor.submit(
            _delegate,
            task.get("prompt", ""),
            task.get("context"),
            model,
            task.get("max_turns", 5),
            state,
        )
        for task in tasks
    ]
    return [f.result(timeout=300) for f in futures]


# ---------------------------------------------------------------------------
# Sandbox — persistent namespace + exec wrapper
# ---------------------------------------------------------------------------

# Module-level sandbox (scoped by MCP server process lifecycle)
_sandbox_ns: dict[str, Any] | None = None
_sandbox_state: Any = None

_RESERVED_NAMES = frozenset({
    "lab", "llm_query", "llm_query_batch", "delegate", "delegate_batch",
    "FINAL", "FINAL_VAR", "SHOW_VARS",
    "math", "json", "re", "statistics", "datetime", "timedelta",
    "deepcopy", "OrderedDict", "collections",
    "__builtins__",
})

_EXEC_TIMEOUT = 60  # seconds


class _TimeoutError(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _TimeoutError("Execution timed out (60s limit)")


def _init_sandbox(state: Any) -> dict[str, Any]:
    """Initialize the sandbox namespace with lab API + helpers."""
    global _sandbox_ns, _sandbox_state
    from distillate.agent_runtime.lab_api import LabAPI
    import collections

    ns: dict[str, Any] = {}
    ns["__builtins__"] = _make_safe_builtins()

    # Lab API
    ns["lab"] = LabAPI(state)

    # Sub-LLM functions
    ns["llm_query"] = _llm_query
    ns["llm_query_batch"] = _llm_query_batch
    ns["delegate"] = lambda prompt, context=None, model=None, max_turns=5: (
        _delegate(prompt, context, model, max_turns, state)
    )
    ns["delegate_batch"] = lambda tasks, model=None: (
        _delegate_batch(tasks, model, state)
    )

    # FINAL
    ns["FINAL"] = _final
    ns["FINAL_VAR"] = lambda var_name: _final_var(var_name, ns)
    ns["SHOW_VARS"] = lambda: "\n".join(
        f"  {k}: {type(v).__name__}"
        for k, v in sorted(ns.items())
        if not k.startswith("_") and k not in _RESERVED_NAMES
    )

    # Standard library
    ns["math"] = math
    ns["json"] = json
    ns["re"] = re
    ns["statistics"] = statistics
    ns["collections"] = collections
    ns["datetime"] = datetime
    ns["timedelta"] = timedelta
    ns["deepcopy"] = deepcopy
    ns["OrderedDict"] = OrderedDict

    _sandbox_ns = ns
    _sandbox_state = state
    return ns


def _restore_reserved(ns: dict[str, Any], state: Any) -> None:
    """Restore reserved names after exec (prevents user overwrites)."""
    from distillate.agent_runtime.lab_api import LabAPI
    import collections

    ns["__builtins__"] = _make_safe_builtins()
    ns["lab"] = LabAPI(state)
    ns["llm_query"] = _llm_query
    ns["llm_query_batch"] = _llm_query_batch
    ns["delegate"] = lambda prompt, context=None, model=None, max_turns=5: (
        _delegate(prompt, context, model, max_turns, state)
    )
    ns["delegate_batch"] = lambda tasks, model=None: (
        _delegate_batch(tasks, model, state)
    )
    ns["FINAL"] = _final
    ns["FINAL_VAR"] = lambda var_name: _final_var(var_name, ns)
    ns["math"] = math
    ns["json"] = json
    ns["re"] = re
    ns["statistics"] = statistics
    ns["collections"] = collections
    ns["datetime"] = datetime
    ns["timedelta"] = timedelta
    ns["deepcopy"] = deepcopy
    ns["OrderedDict"] = OrderedDict


# ---------------------------------------------------------------------------
# Public API — called by the MCP tool
# ---------------------------------------------------------------------------

def execute(code: str, state: Any) -> dict:
    """Execute Python code in the persistent lab sandbox.

    Returns a dict with:
      - success: bool
      - output: str (FINAL value, stdout, or error message)
      - stdout: str (captured print output)
      - cost: dict (token/cost summary)
      - variables: list[str] (user-defined variable names)
    """
    global _sandbox_ns, _sandbox_state

    # Initialize or reinitialize sandbox if needed
    if _sandbox_ns is None or _sandbox_state is not state:
        _init_sandbox(state)

    ns = _sandbox_ns
    assert ns is not None

    # Reset per-call cost tracking
    _cost_tracker.begin_call()

    # AST validation
    errors = _validate_ast(code)
    if errors:
        return {
            "success": False,
            "output": f"Security error: {'; '.join(errors)}",
            "stdout": "",
            "cost": _cost_tracker.summary(),
            "variables": [],
        }

    # Capture stdout
    stdout_capture = io.StringIO()
    ns["print"] = lambda *args, **kwargs: print(*args, file=stdout_capture, **kwargs)

    # Execute with timeout
    old_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(_EXEC_TIMEOUT)

        exec(code, ns)  # noqa: S102

        signal.alarm(0)  # cancel alarm

        stdout = stdout_capture.getvalue()
        user_vars = [
            k for k in ns
            if not k.startswith("_") and k not in _RESERVED_NAMES
        ]

        return {
            "success": True,
            "output": stdout if stdout else "(code executed, no output — use FINAL(answer) to return a result or print() for output)",
            "stdout": stdout,
            "cost": _cost_tracker.summary(),
            "variables": sorted(user_vars),
        }

    except FinalResult as f:
        signal.alarm(0)
        stdout = stdout_capture.getvalue()
        return {
            "success": True,
            "output": f.value,
            "stdout": stdout,
            "cost": _cost_tracker.summary(),
            "variables": [],
        }

    except _TimeoutError:
        return {
            "success": False,
            "output": "Execution timed out (60s limit). Simplify your code or break it into smaller steps.",
            "stdout": stdout_capture.getvalue(),
            "cost": _cost_tracker.summary(),
            "variables": [],
        }

    except BudgetExhaustedError as e:
        signal.alarm(0)
        return {
            "success": False,
            "output": str(e),
            "stdout": stdout_capture.getvalue(),
            "cost": _cost_tracker.summary(),
            "variables": [],
        }

    except Exception as e:
        signal.alarm(0)
        return {
            "success": False,
            "output": f"{type(e).__name__}: {e}",
            "stdout": stdout_capture.getvalue(),
            "cost": _cost_tracker.summary(),
            "variables": [],
        }

    finally:
        signal.signal(signal.SIGALRM, old_handler)
        # Restore reserved names (prevent user overwrites from persisting)
        _restore_reserved(ns, state)


def reset_sandbox() -> None:
    """Clear the sandbox namespace (used on new conversation).

    Also shuts down the executor to prevent thread/memory accumulation
    across sessions (see: massive memory leak from unreleased threads).
    """
    global _sandbox_ns, _sandbox_state, _cost_tracker, _executor
    _sandbox_ns = None
    _sandbox_state = None
    _cost_tracker = CostTracker()

    # Shut down the old executor and create a fresh one to prevent
    # threads and queued tasks from accumulating across session resets.
    # This is critical for long-running processes (MCP server) where
    # multiple sessions may be created/destroyed without process restart.
    _executor.shutdown(wait=False)
    _executor = ThreadPoolExecutor(max_workers=6)
