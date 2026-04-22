"""Agent registry — command builders and availability checks for experiment agents.

Each agent backend has a command builder, a context file (protocol),
and MCP support metadata. Agent type is per-project (default: "claude").

Built-in agent: Claude Code.
Pi is a variant-based agent — users create named variants with specific
LLM backends (e.g. "Pi · Opus 4.6", "Pi · Haiku 4.5").
"""

import os
import shlex
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Built-in agent definitions
# ---------------------------------------------------------------------------

_PI_HF_INFERENCE = {
    "base_url": "https://router.huggingface.co/v1",
    "auth_env": "HF_TOKEN",
    "routing_tags": [":fastest", ":cheapest", ":preferred"],
}

AGENTS = {
    "claude": {
        "id": "claude",
        "label": "Claude Code",
        "binary": "claude",
        "context_file": "CLAUDE.md",
        "mcp": True,
        "install": "npm install -g @anthropic-ai/claude-code",
        "auth": "Anthropic Max/Pro subscription",
        "url": "https://docs.anthropic.com/en/docs/claude-code",
        "description": "Anthropic's Claude Code CLI — full MCP support, interactive TUI",
    },
    "gemini": {
        "id": "gemini",
        "label": "Gemini CLI",
        "binary": "gemini",
        "context_file": "GEMINI.md",
        "mcp": True,
        "install": "npm install -g @google/gemini-cli",
        "auth": "Google Cloud / Gemini API key",
        "url": "https://github.com/google/gemini-cli",
        "description": "Google's Gemini CLI agent — full MCP support, interactive TUI",
    },
}

# Known models for Pi variant creation
PI_MODELS = [
    {"id": "claude-opus-4-6", "label": "Claude Opus 4.6"},
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
    {"id": "google/gemma-4-31B-it", "label": "Gemma 4 31B"},
    {"id": "google/gemma-4-26B-A4B-it", "label": "Gemma 4 26B MoE"},
]


def available_agents(state=None) -> list[dict]:
    """Return all agents with availability status (checks PATH).

    Includes built-in agents plus user-defined Pi variants from state.
    """
    pi_available = shutil.which("pi") is not None

    result = []
    for agent_id, agent in AGENTS.items():
        result.append({
            **agent,
            "available": shutil.which(agent["binary"]) is not None,
        })

    # Append Pi variants from state
    for variant in get_pi_variants(state):
        result.append({
            **variant,
            "available": pi_available,
        })

    return result


def get_agent(agent_type: str, state=None) -> dict:
    """Get agent definition by type. Checks Pi variants, falls back to claude."""
    if agent_type in AGENTS:
        return AGENTS[agent_type]
    for v in get_pi_variants(state):
        if v["id"] == agent_type:
            return v
    return AGENTS["claude"]


# ---------------------------------------------------------------------------
# Pi variant management
# ---------------------------------------------------------------------------

def get_pi_variants(state=None) -> list[dict]:
    """Read Pi variants from state. Returns agent-shaped dicts."""
    if state is None:
        return []
    raw = getattr(state, "data", state) if not isinstance(state, dict) else state
    if hasattr(raw, "get"):
        variants = raw.get("pi_agents", [])
    else:
        variants = []
    result = []
    for v in variants:
        result.append({
            "id": v["id"],
            "label": v.get("label", f"Pi · {v.get('model', '?')}"),
            "binary": "pi",
            "context_file": "PI.md",
            "mcp": True,
            "model": v.get("model", ""),
            "variant": True,
            "install": "npm install -g @anthropic-ai/claude-code @mariozechner/pi-coding-agent",
            "auth": "LLM API key (configurable provider) or HF_TOKEN",
            "url": "https://github.com/badlogic/pi-mono",
            "description": f"Pi agent with {v.get('model', 'custom')} backend",
            "hf_inference": _PI_HF_INFERENCE,
        })
    return result


def create_pi_variant(state, label: str, model: str) -> dict:
    """Create a new Pi variant and persist it to state."""
    import hashlib

    now = datetime.now(timezone.utc).isoformat()
    slug = hashlib.sha256(f"{label}-{now}".encode()).hexdigest()[:6]
    variant_id = f"pi-{slug}"

    variant = {
        "id": variant_id,
        "label": label,
        "model": model,
        "added_at": now,
    }

    if not hasattr(state, "data"):
        return variant

    if "pi_agents" not in state.data:
        state.data["pi_agents"] = []
    state.data["pi_agents"].append(variant)
    state.save()

    return variant


def delete_pi_variant(state, variant_id: str) -> bool:
    """Delete a Pi variant by ID. Returns True if found and deleted."""
    if not hasattr(state, "data"):
        return False

    agents = state.data.get("pi_agents", [])
    before = len(agents)
    state.data["pi_agents"] = [a for a in agents if a["id"] != variant_id]
    if len(state.data["pi_agents"]) < before:
        state.save()
        return True
    return False


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------

def build_agent_command(
    agent_type: str,
    prompt: str,
    *,
    model: str = "",
    effort: str = "high",
    resume_id: str = "",
    state=None,
) -> str:
    """Build the shell command to launch an experiment agent."""
    agent = get_agent(agent_type, state)
    if agent.get("variant"):
        return _build_pi_command(prompt, model=agent.get("model", model), effort=effort)

    builder = _COMMAND_BUILDERS.get(agent_type, _build_claude_command)
    if agent_type in ("claude", "") and resume_id:
        return builder(prompt, model=model, effort=effort, resume_id=resume_id)
    return builder(prompt, model=model, effort=effort)


def _build_claude_command(
    prompt: str,
    *,
    model: str = "",
    effort: str = "high",
    resume_id: str = "",
) -> str:
    """Build claude CLI invocation (interactive TUI, no -p)."""
    parts = ["claude"]
    if resume_id:
        parts.extend(["--resume", shlex.quote(resume_id)])
    parts.extend(["--permission-mode", "auto"])
    if model:
        parts.extend(["--model", model])
    if effort and effort != "high":
        parts.extend(["--effort", effort])
    parts.append(shlex.quote(prompt))
    return " ".join(parts)


def _build_pi_command(
    prompt: str,
    *,
    model: str = "",
    effort: str = "high",
) -> str:
    """Build pi CLI invocation with optional model override."""
    parts = ["pi"]
    if model:
        parts.extend(["--model", shlex.quote(model)])
    parts.append(shlex.quote(prompt))
    return " ".join(parts)


def _build_gemini_command(
    prompt: str,
    *,
    model: str = "",
    effort: str = "high",
) -> str:
    """Build gemini CLI invocation (interactive TUI, no -p)."""
    parts = [
        "gemini",
        "--approval-mode", "default",
    ]
    if model:
        parts.extend(["--model", shlex.quote(model)])
    parts.append(shlex.quote(prompt))
    return " ".join(parts)


def get_pi_env(model: str = "", state=None) -> dict[str, str]:
    """Return extra environment variables for a Pi agent session.

    When the model uses an HF routing tag (e.g. ``meta-llama/...:fastest``)
    or ``HF_TOKEN`` is set, this configures Pi to use HuggingFace Inference
    Providers as its OpenAI-compatible backend.
    """
    env: dict[str, str] = {}
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    hf_routing_tags = _PI_HF_INFERENCE["routing_tags"]
    uses_hf = any(model.endswith(tag) for tag in hf_routing_tags) if model else False

    if hf_token and (uses_hf or not model):
        env["OPENAI_API_KEY"] = hf_token
        env["OPENAI_BASE_URL"] = _PI_HF_INFERENCE["base_url"]
    return env


_COMMAND_BUILDERS = {
    "claude": _build_claude_command,
    "gemini": _build_gemini_command,
    "pi": _build_pi_command,
}


# ---------------------------------------------------------------------------
# Long-lived agent config directories
# ---------------------------------------------------------------------------

def get_agent_config_dir(agent_id: str) -> Path:
    """Return the config directory for a long-lived agent."""
    from distillate.config import CONFIG_DIR
    return CONFIG_DIR / "agents" / agent_id


def ensure_agent_dir(agent_id: str, name: str, personality: str = "") -> Path:
    """Create the config dir + default CLAUDE.md for a long-lived agent."""
    d = get_agent_config_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    claude_md = d / "CLAUDE.md"
    if not claude_md.exists():
        content = f"# {name}\n\n{personality}\n" if personality else f"# {name}\n\nYou are {name}.\n"
        claude_md.write_text(content, encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# Compute detection
# ---------------------------------------------------------------------------

def detect_local_compute() -> dict:
    """Detect local hardware capabilities for experiment compute."""
    import platform

    machine = platform.machine().lower()
    detail = "CPU"

    if machine in ("arm64", "aarch64") and platform.system() == "Darwin":
        try:
            import subprocess
            chip = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                text=True, timeout=5,
            ).strip()
            detail = f"{chip} · MPS"
        except Exception:
            detail = "Apple Silicon · MPS"
    elif shutil.which("nvidia-smi"):
        try:
            import subprocess
            gpu = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"],
                text=True, timeout=5,
            ).strip().split("\n")[0]
            detail = f"{gpu} · CUDA"
        except Exception:
            detail = "NVIDIA · CUDA"

    return {
        "id": "local",
        "label": "Local",
        "detail": detail,
        "connected": True,
        "provider": "local",
    }


# ---------------------------------------------------------------------------
# Protocol file management
# ---------------------------------------------------------------------------

def get_protocol_file(agent_type: str, state=None) -> Optional[Path]:
    """Return the path to the protocol file for the given agent type."""
    agent = get_agent(agent_type, state)
    context_file = agent.get("context_file", "CLAUDE.md")
    autoresearch = Path(__file__).parent / "autoresearch"
    path = autoresearch / context_file
    return path if path.exists() else None


# ---------------------------------------------------------------------------
# HarnessAdapter — v2 Phase 4: multi-backend experiment harnesses
# ---------------------------------------------------------------------------

class HarnessAdapter:
    """Base class for experiment CLI runtime adapters.

    Each adapter knows how to build the shell command, set environment
    variables, and locate the context/protocol file for its harness.
    """

    id: str = ""
    label: str = ""
    binary: str = ""
    context_file: str = ""
    mcp_support: bool = False
    description: str = ""
    install_hint: str = ""

    @property
    def available(self) -> bool:
        return bool(self.binary and shutil.which(self.binary))

    def build_command(self, prompt: str, *, model: str = "", effort: str = "high") -> str:
        raise NotImplementedError

    def get_env(self, *, model: str = "", state=None) -> dict[str, str]:
        return {}

    def get_protocol_template(self) -> Optional[Path]:
        if not self.context_file:
            return None
        p = Path(__file__).parent / "autoresearch" / self.context_file
        return p if p.exists() else None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "binary": self.binary,
            "context_file": self.context_file,
            "mcp_support": self.mcp_support,
            "available": self.available,
            "description": self.description,
            "install_hint": self.install_hint,
        }


class ClaudeCodeAdapter(HarnessAdapter):
    id = "claude-code"
    label = "Claude Code"
    binary = "claude"
    context_file = "CLAUDE.md"
    mcp_support = True
    description = "Anthropic's Claude Code CLI — full MCP support, interactive TUI"
    install_hint = "npm install -g @anthropic-ai/claude-code"

    def build_command(self, prompt: str, *, model: str = "", effort: str = "high") -> str:
        return _build_claude_command(prompt, model=model, effort=effort)


class CodexAdapter(HarnessAdapter):
    id = "codex"
    label = "Codex CLI"
    binary = "codex"
    context_file = "AGENTS.md"
    mcp_support = False
    description = "OpenAI's Codex CLI agent"
    install_hint = "npm install -g @openai/codex"

    def build_command(self, prompt: str, *, model: str = "", effort: str = "high") -> str:
        parts = ["codex"]
        if model:
            parts.extend(["--model", shlex.quote(model)])
        parts.append(shlex.quote(prompt))
        return " ".join(parts)


class GeminiCLIAdapter(HarnessAdapter):
    id = "gemini-cli"
    label = "Gemini CLI"
    binary = "gemini"
    context_file = "GEMINI.md"
    mcp_support = True
    description = "Google's Gemini CLI agent"
    install_hint = "npm install -g @google/gemini-cli"

    def build_command(self, prompt: str, *, model: str = "", effort: str = "high") -> str:
        return _build_gemini_command(prompt, model=model, effort=effort)


class OpenHandsAdapter(HarnessAdapter):
    id = "openhands"
    label = "OpenHands"
    binary = "openhands"
    context_file = "OPENHANDS.md"
    mcp_support = False
    description = "All-Hands AI's OpenHands agent platform"
    install_hint = "pip install openhands-ai"

    def build_command(self, prompt: str, *, model: str = "", effort: str = "high") -> str:
        parts = ["openhands"]
        if model:
            parts.extend(["--model", shlex.quote(model)])
        parts.append(shlex.quote(prompt))
        return " ".join(parts)


# Adapter registry
HARNESS_ADAPTERS: dict[str, HarnessAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex": CodexAdapter(),
    "gemini-cli": GeminiCLIAdapter(),
    "openhands": OpenHandsAdapter(),
}


def get_harness_adapter(harness_id: str) -> HarnessAdapter:
    """Get a harness adapter by ID. Falls back to Claude Code."""
    return HARNESS_ADAPTERS.get(harness_id, HARNESS_ADAPTERS["claude-code"])


def list_harness_adapters() -> list[dict]:
    """Return all harness adapters as dicts with availability status."""
    return [a.to_dict() for a in HARNESS_ADAPTERS.values()]
