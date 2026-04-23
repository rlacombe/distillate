"""Experiment launcher — scaffold, launch, monitor, and record experiments.

Manages the full experiment lifecycle: templates, scaffolding from templates,
tmux-based session management (local + SSH), and state tracking.
"""

import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from distillate.config import CONFIG_DIR

log = logging.getLogger(__name__)


def write_budget_json(
    project_path: Path,
    project: dict | None = None,
    *,
    session_started_at: str | None = None,
) -> Path:
    """Write .distillate/budget.json — the single source of truth for time budgets.

    Called at launch, continuation, and mid-session budget adjustment.
    Returns the path to the written file.

    Two budgets per run:
      - ``train_budget_seconds`` — visible cap for the training subprocess.
        ``distillate-run`` enforces this with SIGTERM+SIGKILL.
      - ``wrap_budget_seconds`` — grace window after training kill for the
        agent to call conclude_run, commit, and push. Floor 60s, scales as
        10% of train so longer runs get proportionally larger wrap windows.

    ``run_budget_seconds`` is kept equal to ``train_budget_seconds`` for
    back-compat with existing readers (post_bash hook, _compute_time_info).
    """
    project = project or {}
    train_budget = (project.get("duration_minutes") or 5) * 60
    wrap_budget = max(60, int(train_budget * 0.1))
    session_budget = project.get("session_budget_seconds")

    budget_dir = project_path / ".distillate"
    budget_dir.mkdir(exist_ok=True)
    budget_path = budget_dir / "budget.json"

    # Read existing file so we don't clobber keys written by other helpers
    # (e.g. the compute block written by write_compute_budget for HF Jobs).
    existing: dict = {}
    if budget_path.is_file():
        try:
            existing = json.loads(budget_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, json.JSONDecodeError):
            existing = {}

    existing.update({
        "run_budget_seconds": train_budget,
        "train_budget_seconds": train_budget,
        "wrap_budget_seconds": wrap_budget,
        "session_budget_seconds": session_budget,
        "session_started_at": session_started_at or datetime.now(timezone.utc).isoformat(),
    })

    budget_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return budget_path


def _ensure_path():
    """Augment PATH so tmux/claude are found from minimal-PATH environments (Electron)."""
    extra = ["/usr/local/bin", "/opt/homebrew/bin",
             str(Path.home() / ".local" / "bin")]
    path = os.environ.get("PATH", "")
    for p in extra:
        if p not in path:
            path = p + ":" + path
    os.environ["PATH"] = path


def ensure_tmux():
    """Check that tmux is installed; install via brew if missing (macOS)."""
    _ensure_path()
    if shutil.which("tmux"):
        return True
    system = platform.system()
    if system == "Darwin" and shutil.which("brew"):
        log.info("tmux not found, installing via Homebrew...")
        result = subprocess.run(
            ["brew", "install", "tmux"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            log.info("tmux installed successfully")
            return True
        log.error("Failed to install tmux: %s", result.stderr.strip())
    elif system == "Linux":
        for mgr, args in [("apt-get", ["-y"]), ("yum", ["-y"]), ("pacman", ["-S", "--noconfirm"])]:
            if shutil.which(mgr):
                log.info("tmux not found, installing via %s...", mgr)
                result = subprocess.run(
                    ["sudo", mgr, "install", *args, "tmux"],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    return True
                break
    raise RuntimeError(
        "tmux is required but not installed. "
        "Install it with: brew install tmux (macOS) or apt install tmux (Linux)"
    )


# ---------------------------------------------------------------------------
# Template management
# ---------------------------------------------------------------------------

_BUILTIN_TEMPLATES_DIR = Path(__file__).parent / "builtin_templates"


def _copy_tree_if_missing(src: Path, dst: Path) -> None:
    """Copy a directory tree into place only when the target is absent."""
    if dst.exists():
        return
    shutil.copytree(src, dst)


def ensure_builtin_templates() -> None:
    """Seed packaged templates into the user config directory on first access."""
    if not _BUILTIN_TEMPLATES_DIR.is_dir():
        return

    root = CONFIG_DIR / "templates"
    root.mkdir(parents=True, exist_ok=True)

    for child in sorted(_BUILTIN_TEMPLATES_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / "PROMPT.md").is_file():
            continue
        _copy_tree_if_missing(child, root / child.name)

def templates_dir() -> Path:
    """Return templates directory (~/.config/distillate/templates/)."""
    d = CONFIG_DIR / "templates"
    d.mkdir(parents=True, exist_ok=True)
    ensure_builtin_templates()
    return d


def list_templates() -> list[dict]:
    """List available templates.

    Returns [{"name": str, "path": Path, "has_data": bool, "prompt_lines": int}].
    """
    root = templates_dir()
    results = []
    if not root.is_dir():
        return results

    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        prompt = child / "PROMPT.md"
        prompt_lines = 0
        if prompt.exists():
            try:
                prompt_lines = len(prompt.read_text(encoding="utf-8").splitlines())
            except OSError:
                pass
        results.append({
            "name": child.name,
            "path": child,
            "has_data": (child / "data").is_dir(),
            "prompt_lines": prompt_lines,
        })
    return results


def import_template(source: Path, name: str | None = None) -> str:
    """Import an experiment directory as a reusable template.

    Copies PROMPT.md + data/ + *.py files. Returns template name.
    """
    source = source.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source}")

    template_name = name or source.name
    template_name = _slugify(template_name)
    dest = templates_dir() / template_name

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    # Copy PROMPT.md
    prompt = source / "PROMPT.md"
    if prompt.exists():
        shutil.copy2(prompt, dest / "PROMPT.md")

    # Copy data/ directory
    data_dir = source / "data"
    if data_dir.is_dir():
        shutil.copytree(data_dir, dest / "data")

    # Copy *.py files
    for py_file in source.glob("*.py"):
        shutil.copy2(py_file, dest / py_file.name)

    return template_name


def _create_demo_experiment_files(target: Path) -> None:
    """Generate files for the demo experiment: smallest transformer that adds 10-digit numbers.

    Inspired by work on arithmetic and grokking by Dimitrios Papailopoulos and others.
    """
    # PROMPT.md — experiment brief
    prompt_md = """# Addition Grokking

Train a small transformer to add two 10-digit numbers, inspired by research on
arithmetic learning and grokking (Papailopoulos et al., 2024).

## Goal

Build the minimal transformer that learns to reliably predict the sum of two 10-digit
integers. This task demonstrates how neural networks can develop structured
generalizations on seemingly simple tasks.

## Constraints

- Model should have < 1M parameters
- Training dataset: 50k random (a, b) pairs with their sum
- Test set: out-of-distribution pairs (different range)
- Track the exact moment of "grokking" (sudden generalization)

## Metrics

- `test_accuracy`: % of test examples predicted correctly (0-100)
- `param_count`: total model parameters (lower is better)
- `train_loss`: final training loss

## Deliverables

- Trained model checkpoint
- Metrics logged to metrics.json
- Brief analysis of learning dynamics
"""
    (target / "PROMPT.md").write_text(prompt_md, encoding="utf-8")

    # train.py — the training script
    train_py = '''"""Smallest transformer for learning integer addition.

Based on grokking research: when do neural networks develop robust generalizations
on arithmetic? This experiment trains a minimal transformer on the synthetic task
of adding two 10-digit numbers.

Inspired by Papailopoulos et al. (2024) and the grokking phenomenon.
"""

import json
import random
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset


class MiniTransformer(nn.Module):
    """Minimal transformer for arithmetic tasks."""

    def __init__(self, vocab_size=12, d_model=64, nhead=2, num_layers=2):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model, nhead, dim_feedforward=256, batch_first=True
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc_out = nn.Linear(d_model, vocab_size)

    def forward(self, x):
        x = self.embedding(x)
        x = self.encoder(x)
        x = self.fc_out(x[:, -1, :])
        return x


def generate_dataset(n_samples=50000, seed=42):
    """Generate random addition pairs."""
    random.seed(seed)
    np.random.seed(seed)

    # Training: range [10^9, 10^10)
    train_pairs = [
        (random.randint(int(1e9), int(1e10)-1), random.randint(int(1e9), int(1e10)-1))
        for _ in range(n_samples)
    ]

    # Test: out-of-distribution (smaller range)
    test_pairs = [
        (random.randint(1, int(1e8)), random.randint(1, int(1e8)))
        for _ in range(int(n_samples * 0.2))
    ]

    return train_pairs, test_pairs


def encode_number(n, vocab_size=10):
    """Encode a number as digit sequence."""
    digits = [int(d) for d in str(n).zfill(11)]
    return digits


def prepare_batch(pairs, vocab_size=10):
    """Prepare training batch: (a, b) -> sum."""
    inputs, outputs = [], []
    for a, b in pairs:
        a_digits = encode_number(a, vocab_size)
        b_digits = encode_number(b, vocab_size)
        sum_digits = encode_number(a + b, vocab_size)

        # Concatenate: [a_digits] + [b_digits] + [sum_digits[0]]
        seq = a_digits + b_digits
        target = sum_digits[0]  # Predict first digit of sum

        inputs.append(seq)
        outputs.append(target)

    return torch.tensor(inputs, dtype=torch.long), torch.tensor(outputs, dtype=torch.long)


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Training on {device}")

    # Dataset
    train_pairs, test_pairs = generate_dataset()
    X_train, Y_train = prepare_batch(train_pairs)
    X_test, Y_test = prepare_batch(test_pairs)

    train_ds = TensorDataset(X_train, Y_train)
    test_ds = TensorDataset(X_test, Y_test)

    train_loader = DataLoader(train_ds, batch_size=128, shuffle=True)
    test_loader = DataLoader(test_ds, batch_size=128)

    # Model
    model = MiniTransformer(vocab_size=12, d_model=64, nhead=2, num_layers=2)
    model = model.to(device)

    print(f"Model size: {sum(p.numel() for p in model.parameters())} parameters")

    # Train
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()

    best_acc = 0
    for epoch in range(50):
        model.train()
        total_loss = 0
        for X_batch, Y_batch in train_loader:
            X_batch, Y_batch = X_batch.to(device), Y_batch.to(device)
            logits = model(X_batch)
            loss = loss_fn(logits, Y_batch)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        # Evaluate
        model.eval()
        correct = 0
        with torch.no_grad():
            for X_batch, Y_batch in test_loader:
                X_batch, Y_batch = X_batch.to(device), Y_batch.to(device)
                logits = model(X_batch)
                pred = logits.argmax(dim=1)
                correct += (pred == Y_batch).sum().item()

        acc = 100 * correct / len(test_ds)

        if (epoch + 1) % 10 == 0 or acc > best_acc:
            print(f"Epoch {epoch+1:2d} | Loss: {total_loss/len(train_loader):.4f} | Test Acc: {acc:.1f}%")
            best_acc = max(best_acc, acc)

    # Save metrics
    metrics = {
        "test_accuracy": acc,
        "param_count": sum(p.numel() for p in model.parameters()),
        "train_loss": total_loss / len(train_loader),
    }

    Path("metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"\\nFinal metrics saved to metrics.json")
    print(f"Model: {metrics['param_count']:,} params, {metrics['test_accuracy']:.1f}% test accuracy")


if __name__ == "__main__":
    main()
'''
    (target / "train.py").write_text(train_py, encoding="utf-8")


def scaffold_experiment(
    template: str,
    target: Path,
    name: str | None = None,
    *,
    compute: str = "local",
    modal_gpu: str = "",
    modal_budget_usd: float = 0.0,
    gpu_type: str = "",
    budget_usd: float = 0.0,
) -> Path:
    """Create a new experiment directory from a template.

    - Copies template contents to target/ (or generates for special "demo" template)
    - git init
    - Creates .distillate/ with REPORTING.md
    - Installs Claude Code hooks (.claude/settings.json)
    - Creates .claude/settings.local.json with safe Bash permissions

    When ``compute == "modal"``, also writes the Modal budget block into
    ``.distillate/budget.json`` so the per-experiment watcher and the UI
    can read the $ cap. The agent's training script is responsible for
    actually *using* Modal; the scaffold only records the intent.

    Returns experiment path.
    """
    target = target.resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Target directory is not empty: {target}")

    target.mkdir(parents=True, exist_ok=True)

    # Handle special "demo" template by generating content inline
    if template == "demo":
        _create_demo_experiment_files(target)
    else:
        tmpl_dir = templates_dir() / template
        if not tmpl_dir.is_dir():
            raise FileNotFoundError(f"Template not found: {template}")

        # Copy template contents
        for item in tmpl_dir.iterdir():
            if item.name.startswith("."):
                continue
            dst = target / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)

    # git init (if not already a repo)
    if not (target / ".git").exists():
        subprocess.run(
            ["git", "init"],
            cwd=target,
            capture_output=True,
        )

    # Create .distillate/ with REPORTING.md
    distillate_dir = target / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    reporting_src = Path(__file__).parent / "autoresearch" / "REPORTING.md"
    if reporting_src.exists():
        shutil.copy2(reporting_src, distillate_dir / "REPORTING.md")

    # Install CLAUDE.md (consolidated protocol — auto-loaded by Claude Code)
    claude_md_src = Path(__file__).parent / "autoresearch" / "CLAUDE.md"
    if claude_md_src.exists():
        shutil.copy2(claude_md_src, target / "CLAUDE.md")

    # Install hooks via the shared function
    _install_hooks_into(target)

    # Install .mcp.json so the agent can call distillate tools (save_enrichment etc.)
    mcp_json = target / ".mcp.json"
    if not mcp_json.exists():
        mcp_config = {
            "mcpServers": {
                "distillate": {
                    "command": sys.executable,
                    "args": ["-m", "distillate.mcp_server"],
                },
            },
        }
        # Add HuggingFace MCP server when HF_TOKEN is available, OR when the
        # experiment is explicitly configured for HF Jobs compute.
        from distillate import auth as _auth, secrets as _secrets
        hf_token = _auth.hf_token_for("hub") or os.environ.get("HF_TOKEN", "").strip()
        if hf_token or compute == "hfjobs":
            mcp_config["mcpServers"]["huggingface"] = {
                "command": "npx",
                "args": ["-y", "@huggingface/mcp-server"],
                # Token will be in the env at runtime (injected by _spawn_local);
                # write a placeholder so the entry is present even if the token
                # isn't available at scaffold time.
                "env": {"HF_TOKEN": hf_token or "${HF_TOKEN}"},
            }
        mcp_json.write_text(
            json.dumps(mcp_config, indent=2) + "\n",
            encoding="utf-8",
        )

    # Create .claude/settings.local.json with safe Bash permissions
    claude_dir = target / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_local = claude_dir / "settings.local.json"
    if not settings_local.exists():
        local_config = {
            "permissions": {
                "allow": [
                    "Bash(python3:*)",
                    "Bash(git:*)",
                    "Bash(tail:*)",
                    "Bash(ls:*)",
                    "Bash(cat:*)",
                    "Bash(head:*)",
                    "Bash(wc:*)",
                    "Bash(mkdir:*)",
                    "Read",
                    "Write",
                    "Edit",
                    "Glob",
                    "Grep",
                    "WebFetch",
                    "WebSearch",
                    "mcp__distillate__start_run",
                    "mcp__distillate__conclude_run",
                    "mcp__distillate__save_enrichment",
                    "mcp__distillate__annotate_run",
                    # HuggingFace Jobs
                    "mcp__distillate__submit_hf_job",
                    "mcp__distillate__check_hf_job",
                    "mcp__distillate__cancel_hf_job",
                    "mcp__distillate__list_hf_jobs",
                ] + ([
                    # HuggingFace MCP server tools (when HF_TOKEN configured)
                    "mcp__huggingface__search_models",
                    "mcp__huggingface__search_datasets",
                    "mcp__huggingface__search_papers",
                    "mcp__huggingface__search_spaces",
                ] if os.environ.get("HF_TOKEN", "").strip() else []),
            },
        }
        settings_local.write_text(
            json.dumps(local_config, indent=2) + "\n",
            encoding="utf-8",
        )

    # Record compute intent into .distillate/budget.json (done last so any
    # earlier failure leaves no stale block behind).
    if compute == "modal":
        from distillate.budget import write_modal_config
        write_modal_config(
            cwd=target, gpu=modal_gpu, budget_usd=modal_budget_usd,
        )
    elif compute == "hfjobs":
        from distillate.budget import write_compute_budget
        write_compute_budget(
            cwd=target,
            provider="hfjobs",
            gpu_type=gpu_type or "a100-large",
            budget_usd=budget_usd or 25.0,
        )

    return target


def _install_hooks_into(project_path: Path, agent_type: str = "claude") -> None:
    """Install CLI hooks into a project (shared with main.py --install-hooks)."""
    hooks_src = Path(__file__).parent / "autoresearch" / "hooks.json"
    if not hooks_src.exists():
        return

    raw = hooks_src.read_text(encoding="utf-8")
    # Replace bare "python3" with the absolute path to the Python that has
    # distillate installed, so hooks work regardless of the experiment's PATH.
    python_bin = sys.executable
    raw = raw.replace("python3 -m distillate.", f"{python_bin} -m distillate.")
    hook_config = json.loads(raw)

    agent_dir = project_path / f".{agent_type}"
    agent_dir.mkdir(exist_ok=True)
    settings_file = agent_dir / "settings.json"

    existing: dict = {}
    if settings_file.exists():
        try:
            existing = json.loads(settings_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    # Merge hooks (don't overwrite existing hooks)
    existing_hooks = existing.setdefault("hooks", {})
    for event_type, hook_list in hook_config.get("hooks", {}).items():
        existing_entries = existing_hooks.setdefault(event_type, [])
        # Collect all commands already registered for dedup
        existing_commands = set()
        for entry in existing_entries:
            for h in entry.get("hooks", []):
                existing_commands.add(h.get("command", ""))
        for hook in hook_list:
            new_cmds = {h.get("command", "") for h in hook.get("hooks", [])}
            if not new_cmds & existing_commands:
                existing_entries.append(hook)

    settings_file.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _refresh_protocol_files(project_path: Path, agent_type: str = "claude") -> None:
    """Refresh protocol files and MCP config in the experiment project.

    Called on every session launch so running experiments always pick up
    protocol updates and have MCP tools available.  The *agent_type*
    determines which protocol file is copied (CLAUDE.md, PI.md, etc.).
    """
    from distillate.agents import get_agent

    autoresearch = Path(__file__).parent / "autoresearch"
    agent = get_agent(agent_type)
    context_file = agent.get("context_file", "CLAUDE.md")

    # Copy the agent-specific protocol file
    protocol_src = autoresearch / context_file
    if protocol_src.exists():
        # Claude uses CLAUDE.md; others use their own file name
        dest_name = context_file
        shutil.copy2(protocol_src, project_path / dest_name)
        # Also write as CLAUDE.md for backward compat (Claude Code reads it)
        if dest_name != "CLAUDE.md" and agent.get("mcp"):
            shutil.copy2(protocol_src, project_path / "CLAUDE.md")

    # Always copy CLAUDE.md as fallback if it exists and wasn't already copied
    claude_md_src = autoresearch / "CLAUDE.md"
    if context_file != "CLAUDE.md" and claude_md_src.exists():
        if not (project_path / "CLAUDE.md").exists():
            shutil.copy2(claude_md_src, project_path / "CLAUDE.md")

    distillate_dir = project_path / ".distillate"
    distillate_dir.mkdir(exist_ok=True)

    reporting_src = autoresearch / "REPORTING.md"
    if reporting_src.exists():
        shutil.copy2(reporting_src, distillate_dir / "REPORTING.md")

    # Ensure .mcp.json exists with current Python path (may change between
    # installs/upgrades — always overwrite to keep it current)
    mcp_json = project_path / ".mcp.json"
    mcp_config = {
        "mcpServers": {
            "distillate": {
                "command": sys.executable,
                "args": ["-m", "distillate.mcp_server"],
            },
        },
    }
    # Add HuggingFace MCP server when HF_TOKEN is configured
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    if hf_token:
        mcp_config["mcpServers"]["huggingface"] = {
            "command": "npx",
            "args": ["-y", "@huggingface/mcp-server"],
            "env": {"HF_TOKEN": hf_token},
        }
    mcp_json.write_text(
        json.dumps(mcp_config, indent=2) + "\n",
        encoding="utf-8",
    )

    # Ensure .claude/settings.local.json has MCP tool permissions
    # (older projects may be missing these)
    claude_dir = project_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_local = claude_dir / "settings.local.json"
    local_config = {
        "permissions": {
            "allow": [
                "Bash(python3:*)",
                "Bash(git:*)",
                "Bash(tail:*)",
                "Bash(ls:*)",
                "Bash(cat:*)",
                "Bash(head:*)",
                "Bash(wc:*)",
                "Bash(mkdir:*)",
                "Read",
                "Write",
                "Edit",
                "Glob",
                "Grep",
                "WebFetch",
                "WebSearch",
                "mcp__distillate__start_run",
                "mcp__distillate__conclude_run",
                "mcp__distillate__save_enrichment",
                "mcp__distillate__annotate_run",
                # HuggingFace Jobs (active when HF compute configured)
                "mcp__distillate__submit_hf_job",
                "mcp__distillate__check_hf_job",
                "mcp__distillate__cancel_hf_job",
                "mcp__distillate__list_hf_jobs",
            ] + ([
                # HuggingFace MCP server tools
                "mcp__huggingface__search_models",
                "mcp__huggingface__search_datasets",
                "mcp__huggingface__search_papers",
                "mcp__huggingface__search_spaces",
            ] if hf_token else []),
        },
    }
    settings_local.write_text(
        json.dumps(local_config, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Session management (tmux-based)
# ---------------------------------------------------------------------------


def _tmux_session_exists(session_name: str) -> bool:
    """Check if a tmux session with the given name is currently running."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")


def create_github_repo(project_path: Path, name: str, private: bool = False) -> dict:
    """Create a GitHub repo and push initial commit.

    Uses `gh` CLI (must be installed and authenticated).
    Returns {"ok": True, "url": "..."} or {"ok": False, "reason": "..."}.
    """
    _ensure_path()

    if not shutil.which("gh"):
        return {"ok": False, "reason": "gh CLI not installed. Install: brew install gh"}

    # Check gh auth
    auth_check = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True, text=True,
    )
    if auth_check.returncode != 0:
        return {"ok": False, "reason": "gh not authenticated. Run: gh auth login"}

    # Generate README.md if missing (public repos become Distillate ads)
    readme_path = project_path / "README.md"
    if not readme_path.exists():
        experiment_name = name.removeprefix("distillate-xp-") if name.startswith("distillate-xp-") else name
        readme_path.write_text(
            f"# {experiment_name}\n\n"
            "An autonomous ML experiment powered by "
            "[Distillate](https://github.com/rlacombe/distillate).\n\n"
            "## What is Distillate?\n\n"
            "Distillate is an open-source tool that helps scientists design, launch, "
            "and track autonomous ML experiments — with a paper library built in. "
            "Nicolas, the research alchemist, orchestrates Claude Code agents that "
            "iteratively improve your models.\n\n"
            "## Reproducing this experiment\n\n"
            "```bash\n"
            "# Install Distillate\n"
            "pip install distillate\n\n"
            "# Clone and run\n"
            f"git clone https://github.com/$(gh api user -q .login)/{name}.git\n"
            f"cd {name}\n"
            "distillate launch  # Resume the experiment\n"
            "```\n\n"
            "## Results\n\n"
            "See `.distillate/runs.jsonl` for the full experiment history.\n",
            encoding="utf-8",
        )

    # Initial commit if needed
    subprocess.run(["git", "add", "-A"], cwd=project_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial experiment setup"],
        cwd=project_path, capture_output=True,
    )

    # Create repo
    visibility = "--private" if private else "--public"
    result = subprocess.run(
        ["gh", "repo", "create", name, visibility, "--source", str(project_path), "--push"],
        cwd=project_path,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"ok": False, "reason": result.stderr.strip() or "Failed to create repo"}

    # Extract URL from output (first line only — gh also prints git push messages)
    url = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
    if not url:
        # Try to get it from git remote
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_path, capture_output=True, text=True,
        )
        url = remote.stdout.strip()

    return {"ok": True, "url": url}


def _session_name(project_name: str, session_num: int) -> str:
    """Generate a tmux session name: distillate-<slug>-<NNN>."""
    return f"distillate-{_slugify(project_name)}-{session_num:03d}"


def _next_session_id(project: dict) -> str:
    """Generate the next session ID for a project."""
    sessions = project.get("sessions", {})
    existing = [int(k.split("_")[-1]) for k in sessions if k.startswith("session_")]
    n = max(existing, default=0) + 1
    return f"session_{n:03d}"


def _generate_run_context(project_path: Path) -> Path | None:
    """Read runs.jsonl and generate .distillate/context.md with prior-run history.

    Returns the path to context.md if written, None otherwise.
    Caps at last 20 runs to keep context manageable.
    """
    runs_file = project_path / ".distillate" / "runs.jsonl"
    if not runs_file.exists():
        return None

    runs = []
    try:
        for line in runs_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return None

    if not runs:
        return None

    # Auto-close orphaned "running" entries — these are announcements
    # whose run never completed (agent crashed, restarted, or moved on).
    resolved_ids = {r["id"] for r in runs
                    if r.get("status") in ("best", "completed", "keep", "discard", "crash")
                    and "id" in r}
    orphans = [r for r in runs
               if r.get("status") == "running" and r.get("id") not in resolved_ids]
    if orphans:
        now = datetime.now(timezone.utc).isoformat()
        with open(runs_file, "a", encoding="utf-8") as f:
            for orph in orphans:
                # Use original timestamp so it doesn't inflate
                # experiment time calculations
                crash_entry = {
                    "$schema": "distillate/run/v1",
                    "id": orph.get("id", "unknown"),
                    "timestamp": orph.get("timestamp", now),
                    "status": "crash",
                    "description": orph.get("description", ""),
                    "reasoning": "Auto-closed: run was announced but never completed.",
                }
                f.write(json.dumps(crash_entry, ensure_ascii=False) + "\n")
                resolved_ids.add(orph.get("id", ""))
        # Re-read after cleanup
        runs = []
        for line in runs_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # Cap at last 20 runs
    recent = runs[-20:]

    lines = [
        "# Prior Run History",
        "",
        f"This experiment has **{len(runs)} prior run(s)**. "
        f"Below are the most recent {len(recent)}.",
        "",
        "**IMPORTANT:** Review this history before starting. Build on what worked, "
        "avoid repeating failed approaches. Reference specific run IDs when explaining "
        "your reasoning.",
        "",
    ]

    # Summarize each run
    best_metric_val = None
    best_metric_name = None
    best_run_id = None

    for run in recent:
        run_id = run.get("id", "?")
        status = run.get("status", "?")
        hypothesis = run.get("hypothesis", "")
        reasoning = run.get("reasoning", "")
        results = run.get("results", {})
        changes = run.get("changes", "")

        results_str = ", ".join(f"{k}={v}" for k, v in results.items()
                                if isinstance(v, (int, float)))

        lines.append(f"### {run_id} [{status}]")
        if hypothesis:
            lines.append(f"**Hypothesis:** {hypothesis}")
        if changes:
            lines.append(f"**Changes:** {changes}")
        if results_str:
            lines.append(f"**Results:** {results_str}")
        if reasoning:
            lines.append(f"**Reasoning:** {reasoning}")
        lines.append("")

        # Track best metric (from non-crash runs)
        if status != "crash":
            for k, v in results.items():
                if isinstance(v, (int, float)):
                    if best_metric_val is None or v > best_metric_val:
                        best_metric_val = v
                        best_metric_name = k
                        best_run_id = run_id

    # --- Key learnings from non-crash runs ---
    learnings: list[str] = []
    for run in recent:
        if run.get("status") in ("crash", "running"):
            continue
        reasoning = run.get("reasoning", "")
        if reasoning:
            learnings.append(reasoning)
        for lr in run.get("learnings", []):
            if isinstance(lr, str) and lr:
                learnings.append(lr)

    if learnings:
        lines.append("## Key Learnings So Far")
        lines.append("")
        # Deduplicate while preserving order, cap at 10
        seen_lr: set[str] = set()
        for lr in learnings:
            if lr not in seen_lr:
                seen_lr.add(lr)
                lines.append(f"- {lr}")
            if len(seen_lr) >= 10:
                break
        lines.append("")

    # --- Infer key metric and optimization direction ---
    key_metric, key_direction = _infer_key_metric(runs)
    if key_metric:
        # Find best value among non-crash runs
        best_val = None
        best_rid = None
        for run in recent:
            if run.get("status") in ("crash", "running"):
                continue
            val = run.get("results", {}).get(key_metric)
            if not isinstance(val, (int, float)):
                continue
            if best_val is None:
                best_val, best_rid = val, run.get("id", "?")
            elif key_direction == "lower" and val < best_val:
                best_val, best_rid = val, run.get("id", "?")
            elif key_direction == "higher" and val > best_val:
                best_val, best_rid = val, run.get("id", "?")

        direction_word = "minimize" if key_direction == "lower" else "maximize"
        best_str = ""
        if best_val is not None:
            best_str = f" Current best: **{key_metric}={best_val}** (from {best_rid})."
        lines.insert(7, f"**Key metric to {direction_word}:** `{key_metric}`.{best_str}")
        lines.insert(8, f"Your goal is to {direction_word} `{key_metric}` across runs. "
                        f"Report this metric for every run.")
        lines.insert(9, "")
    elif best_metric_val is not None:
        lines.insert(7, f"**Current best:** {best_metric_name}={best_metric_val} "
                        f"(from {best_run_id})")
        lines.insert(8, "")

    results_path = project_path / "RESULTS.md"
    if results_path.exists():
        lines.append("## RESULTS.md")
        lines.append("")
        lines.append("A RESULTS.md file exists in the repo root. "
                      "Update it after each run with your current findings summary.")
        lines.append("")

    context_path = project_path / ".distillate" / "context.md"
    context_path.parent.mkdir(exist_ok=True)
    context_path.write_text("\n".join(lines), encoding="utf-8")
    return context_path


def _infer_key_metric(runs: list[dict]) -> tuple[str, str]:
    """Infer the key metric to optimize from run history.

    Returns (metric_name, direction) where direction is "higher" or "lower".
    Returns ("", "") if no metric can be inferred.

    Uses ``classify_metric()`` from experiments.py for category-aware scoring:
    ratio > loss > generic, and prefers metrics present in most runs.
    """
    if not runs:
        return ("", "")

    from collections import Counter

    from distillate.experiments import classify_metric

    metric_counts: Counter = Counter()
    for run in runs:
        for k, v in run.get("results", {}).items():
            if isinstance(v, (int, float)):
                metric_counts[k] += 1
    if not metric_counts:
        return ("", "")

    total = len(runs)

    # Category-based relevance scores
    _CATEGORY_RELEVANCE = {
        "ratio": 50, "loss": 30, "count": 10,
        "time": 5, "cost": 5, "hyperparameter": 1, "generic": 15,
    }
    # Prefix boosts (test > val > train)
    _PREFIX_BOOST = {"test_": 40, "val_": 20, "train_": 5}

    def _score(name: str) -> float:
        coverage = metric_counts[name] / total
        cat = classify_metric(name)
        rel = _CATEGORY_RELEVANCE.get(cat, 15)
        lower_name = name.lower()
        for prefix, boost in _PREFIX_BOOST.items():
            if lower_name.startswith(prefix):
                rel += boost
                break
        return coverage * rel

    best = max(metric_counts.keys(), key=_score)
    cat = classify_metric(best)
    direction = "lower" if cat in ("loss", "count", "time", "cost") else "higher"
    return (best, direction)


def _resolve_linked_paper_lines(project: dict | None) -> list[str]:
    """Return bullet lines describing papers linked to the project's parent
    workspace. Returns an empty list if the project has no workspace, no
    papers are linked, or any lookup fails.
    """
    if not project:
        return []
    ws_id = project.get("workspace_id") or ""
    if not ws_id:
        return []
    try:
        from distillate.state import State
        st = State()
        ws = st.get_workspace(ws_id)
        if not ws:
            return []
        citekeys = ws.get("linked_papers") or []
        if not citekeys:
            return []
        out: list[str] = []
        for ck in citekeys[:12]:
            doc = st.find_by_citekey(ck)
            if not doc:
                out.append(f"- @{ck}")
                continue
            title = (doc.get("title") or "").strip()
            meta = doc.get("metadata") or {}
            author = (meta.get("author") or meta.get("creators") or "") if isinstance(meta, dict) else ""
            if isinstance(author, list) and author:
                author = author[0]
            year = meta.get("year") or meta.get("date") or "" if isinstance(meta, dict) else ""
            bits = [f"@{ck}"]
            if title:
                bits.append(f"— {title}")
            attr = ", ".join(s for s in (str(author), str(year)) if s)
            if attr:
                bits.append(f"({attr})")
            out.append("- " + " ".join(bits))
        return out
    except Exception:
        return []


def _build_launch_prompt(
    project: dict | None,
    project_path: Path,
    context_path: Path | None,
) -> str:
    """Build a rich launch prompt with project context."""
    lines = [
        "You are an autonomous experiment agent. Your job is to run experiments, "
        "improve metrics, and document everything.",
        "",
        "## Instructions",
        "",
        "1. Read CLAUDE.md — it contains the full experiment protocol",
        "2. Read PROMPT.md — it contains the experiment specification (what to build, dataset, constraints)",
        "3. Follow the protocol precisely: Plan → Train → Record → Commit, one run at a time",
        "",
        "## Key rules",
        "",
        "- You are fully autonomous. Do NOT pause to ask the human. Work indefinitely until stopped.",
        "- Use the MCP tools (start_run, conclude_run, save_enrichment) — do NOT write to runs.jsonl manually.",
        "- Every run MUST produce at least one numeric metric in results.",
        "- One configuration per run. No sweep scripts.",
        "- Commit after every run. conclude_run returns is_best — use: git add -A && git commit -m '[best] <change>: <metric>=<value>' (or without [best] prefix) && git push",
        "",
        "## Pre-registration contract",
        "",
        "Before ANY training script, call start_run() — it is required by the tool surface:",
        "  start_run(project, description, hypothesis, prediction, predicted_metric,",
        "            predicted_value: float, confidence: int 0–100, rationale)",
        "It returns run_id (required by conclude_run) and run_number (use in all prose).",
        "After training, call conclude_run(project, run_id, results, reasoning, outcome,",
        "                                  verdict, belief_update).",
        "conclude_run without a prior start_run fails. This order is enforced by the tool surface.",
    ]

    if project:
        runs = project.get("runs", {})
        key_metric = project.get("key_metric_name", "")
        duration = project.get("duration_minutes", 5)

        if key_metric and runs:
            # Find best value for the key metric
            from distillate.experiments import classify_metric
            cat = classify_metric(key_metric)
            lower_better = cat in ("loss", "count", "time", "cost")
            best_val = None
            for r in runs.values() if isinstance(runs, dict) else runs:
                v = r.get("results", {}).get(key_metric)
                if isinstance(v, (int, float)):
                    if best_val is None or (lower_better and v < best_val) or (not lower_better and v > best_val):
                        best_val = v

            lines.append("")
            lines.append("## Current state")
            lines.append("")
            lines.append(f"- {len(runs)} prior runs logged")
            if best_val is not None:
                direction = "lower is better" if lower_better else "higher is better"
                lines.append(f"- Key metric: {key_metric} = {best_val} ({direction})")
            lines.append(f"- Time budget per run: {duration} minutes")

    # Linked papers — fetch from the parent workspace (project). These are
    # seed references the experimentalist should treat as authoritative
    # context for the experiment's subject matter. Best-effort: if state or
    # the workspace can't be resolved, we just skip the section rather than
    # fail the launch.
    paper_lines = _resolve_linked_paper_lines(project)
    if paper_lines:
        lines.append("")
        lines.append("## Linked Papers")
        lines.append("")
        lines.append(
            "The parent project has the following papers linked — treat "
            "them as seed references for this experiment:"
        )
        lines.extend(paper_lines)

    # Prior run context — inlined so it is a prompt prefix, not a file the
    # agent must remember to read (Reflexion mechanism, not convention).
    if context_path and context_path.exists():
        try:
            context_text = context_path.read_text(encoding="utf-8").strip()
            if context_text:
                lines.append("")
                lines.append("## Prior Run Context")
                lines.append("")
                lines.append(context_text)
        except OSError:
            pass

    # Steering instructions — single-shot: inlined then deleted so the
    # next session starts clean.
    steering_path = project_path / ".distillate" / "steering.md"
    if steering_path.exists():
        try:
            steering_text = steering_path.read_text(encoding="utf-8").strip()
            if steering_text:
                lines.append("")
                lines.append("## Steering Instructions")
                lines.append("")
                lines.append(steering_text)
            steering_path.unlink()
        except OSError:
            pass

    lines.append("")
    lines.append("Begin your next experiment run. Start with Step 0: pre-register your approach via start_run().")

    return "\n".join(line for line in lines if line is not None)


def _build_claude_command(
    prompt_path: Path,
    *,
    model: str = "claude-sonnet-4-5-20250929",
    effort: str = "high",
    has_context: bool = False,
    prompt_override: str | None = None,
) -> str:
    """Build the claude CLI invocation string.

    Runs claude interactively (no -p) so the full TUI is visible in
    the tmux session / xterm.js. The prompt just tells claude to read
    PROMPT.md — all experiment logic lives in that file.

    If *prompt_override* is given, it replaces the default prompt text.
    """
    if prompt_override:
        prompt = prompt_override
    else:
        prompt = (
            "Read CLAUDE.md (the experiment protocol) and PROMPT.md (the experiment spec). "
            "Follow both precisely. You are fully autonomous — do NOT pause to ask the human. "
            "Work indefinitely until manually stopped."
        )
    parts = [
        "claude",
        "--permission-mode", "auto",
        shlex.quote(prompt),
    ]
    return " ".join(parts)


def create_sister_project(
    parent_path: Path,
    parent_project: dict,
    agent_type: str,
    state,
) -> dict:
    """Create a sister project: same experiment, different agent.

    Copies PROMPT.md and experiment config from the parent, creates a new
    project directory alongside it, registers in state with ``sister_of``
    pointing to the parent.

    Returns the new project dict from state.
    """
    from distillate.experiments import slugify

    parent_name = parent_project.get("name", parent_path.name)
    sister_name = f"{parent_name}-{agent_type}"
    sister_slug = slugify(sister_name)
    sister_path = parent_path.parent / sister_slug

    # Create directory and copy PROMPT.md
    sister_path.mkdir(parents=True, exist_ok=True)
    prompt_src = parent_path / "PROMPT.md"
    if prompt_src.exists():
        shutil.copy2(prompt_src, sister_path / "PROMPT.md")

    # Copy data files if they exist
    for fname in ("data", "dataset", "train_data", "test_data"):
        src = parent_path / fname
        if src.exists():
            if src.is_dir():
                shutil.copytree(src, sister_path / fname, dirs_exist_ok=True)
            else:
                shutil.copy2(src, sister_path / fname)

    # Git init
    subprocess.run(["git", "init"], capture_output=True, cwd=sister_path)

    # Create .distillate directory
    (sister_path / ".distillate").mkdir(exist_ok=True)

    # Register in state
    parent_id = parent_project.get("id", "")
    state.add_experiment(
        experiment_id=sister_slug,
        name=sister_name,
        path=str(sister_path),
        description=parent_project.get("description", ""),
        goals=parent_project.get("goals", []),
        agent_type=agent_type,
        session_budget_seconds=parent_project.get("session_budget_seconds"),
        sister_of=parent_id,
    )
    # Copy key metric config
    key_metric = parent_project.get("key_metric_name", "")
    if key_metric:
        state.update_experiment(sister_slug, key_metric_name=key_metric)
    duration = parent_project.get("duration_minutes")
    if duration:
        state.update_experiment(sister_slug, duration_minutes=duration)
    state.save()

    return state.get_experiment(sister_slug)


def launch_experiment(
    project_path: Path,
    *,
    host: str | None = None,
    model: str = "claude-sonnet-4-5-20250929",
    effort: str = "high",
    project: dict | None = None,
    prompt_override: str | None = None,
    max_turns: int = 100,
    agent_type: str | None = None,
) -> dict:
    """Launch an experiment agent session.

    1. Verify PROMPT.md exists
    2. Ensure hooks installed (always)
    3. Build agent command (Claude, Codex, Gemini, or Pi)
    4. Spawn tmux session (local or via SSH)
    5. Return session dict for state tracking

    If *prompt_override* is given, it replaces the default prompt
    (e.g. for backfill tasks).
    """
    project_path = project_path.resolve()
    ensure_tmux()

    # Check if a session is already running for this project
    if project:
        for sess in project.get("sessions", {}).values():
            if sess.get("status") == "running":
                tmux_name = sess.get("tmux_session", "")
                if tmux_name and _tmux_session_exists(tmux_name):
                    raise RuntimeError(
                        f"Session '{tmux_name}' is already running. Stop it first."
                    )

    # Belt-and-suspenders: also check for tmux sessions matching the project
    # name pattern, in case state got out of sync (e.g. crash, manual launch).
    proj_slug = (project.get("name", project_path.name) if project
                 else project_path.name).lower().replace(" ", "-")
    try:
        import subprocess
        ls = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        if ls.returncode == 0:
            for line in ls.stdout.strip().splitlines():
                if line.startswith(f"distillate-{proj_slug}-"):
                    raise RuntimeError(
                        f"Tmux session '{line}' is already running for this "
                        f"project. Stop it first, or use 'continue' to resume."
                    )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    prompt = project_path / "PROMPT.md"
    if not prompt.exists():
        raise FileNotFoundError(f"No PROMPT.md found in {project_path}")

    # Resolve agent type: explicit param > project field > default
    if agent_type is None:
        agent_type = (project.get("agent_type", "claude") if project else "claude")

    # Ensure hooks are always installed
    if agent_type in ("claude", "gemini"):
        _install_hooks_into(project_path, agent_type=agent_type)
        # Install HTTP status hooks pointing at the local Distillate server so
        # Stop/Notification/UserPromptSubmit events drive sidebar status dots
        # for this Experimentalist session. Only experiments get these — not
        # workspace coding sessions. No-op when the server port isn't set
        # (e.g. CLI or test contexts).
        try:
            from distillate.claude_hooks import get_server_port, write_hook_config
            _port = get_server_port()
            if _port:
                write_hook_config(project_path, _port, agent_type=agent_type)
        except Exception as _hook_err:
            log.warning(
                "Failed to write %s HTTP hook config for %s: %s",
                agent_type, project_path, _hook_err,
            )

    # Refresh protocol files for the correct agent type
    _refresh_protocol_files(project_path, agent_type=agent_type)

    # Generate run context from prior runs (if any)
    context_path = _generate_run_context(project_path)

    # Build a richer launch prompt if no override was given
    if not prompt_override:
        prompt_override = _build_launch_prompt(project, project_path, context_path)

    # Resume prior conversation if the last session ended cleanly.
    resume_id = ""
    last_id_path = project_path / ".distillate" / "last_session_id"
    if last_id_path.exists() and agent_type in ("claude", ""):
        try:
            resume_id = last_id_path.read_text(encoding="utf-8").strip()
            last_id_path.unlink()
        except OSError:
            resume_id = ""

    # Build command using the agent registry
    from distillate.agents import build_agent_command, get_pi_env
    cmd = build_agent_command(
        agent_type, prompt_override,
        model=model, effort=effort,
        resume_id=resume_id,
    )

    # Extra env vars for agent-specific backends (e.g. Pi + HF Inference)
    extra_env: dict[str, str] = {}
    if agent_type == "pi" or agent_type.startswith("pi-"):
        extra_env.update(get_pi_env(model))

    # Determine session name
    proj_name = project.get("name", project_path.name) if project else project_path.name
    session_id = _next_session_id(project) if project else "session_001"
    session_num = int(session_id.split("_")[-1])
    tmux_name = _session_name(proj_name, session_num)

    # Count current runs
    runs_at_start = len(project.get("runs", {})) if project else 0

    # Session output log file (stream-json piped via tee)
    log_dir = project_path / ".distillate"
    log_dir.mkdir(exist_ok=True)
    session_log = log_dir / f"{session_id}.jsonl"

    # Cloud GPU provisioning (if compute config is set)
    compute = (project.get("compute") if project else None) or {}
    cloud_provider = compute.get("provider", "")
    pod_info = None
    if cloud_provider in ("hfjobs", "huggingface"):
        # HF Jobs: agent runs locally, dispatches training as HF Jobs via MCP tools.
        # No pod provisioning needed — inject HF_TOKEN + compute markers.
        from distillate import auth as _auth, config as _config
        hf_token = _auth.hf_token_for("jobs") or os.environ.get("HF_TOKEN", "")
        if hf_token:
            extra_env["HF_TOKEN"] = hf_token
        if _config.HF_NAMESPACE:
            extra_env["HF_NAMESPACE"] = _config.HF_NAMESPACE
        # DISTILLATE_COMPUTE tells the agent unambiguously to use HF Jobs
        extra_env["DISTILLATE_COMPUTE"] = "hfjobs"
        gpu_flavor = compute.get("gpu_type", _config.HF_DEFAULT_GPU_FLAVOR)
        extra_env["DISTILLATE_GPU_FLAVOR"] = gpu_flavor
        budget_usd = float(compute.get("budget_usd", 25.0))
        log.info(
            "HF Jobs compute: gpu=%s budget=$%.2f path=%s",
            gpu_flavor, budget_usd, project_path,
        )
        # Write compute budget so MCP tools can enforce the $ cap
        from distillate.budget import write_compute_budget
        try:
            write_compute_budget(
                cwd=project_path,
                provider="hfjobs",
                gpu_type=compute.get("gpu_type", "a100-large"),
                budget_usd=budget_usd,
            )
        except Exception as e:
            log.warning("Failed to write HF Jobs budget config for %s: %s", project_path, e)
    elif cloud_provider and not host:
        from distillate.compute import get_provider
        provider = get_provider(cloud_provider)
        pod_info = provider.create_pod(
            gpu_type=compute.get("gpu_type", "RTX_4090"),
            gpu_count=compute.get("gpu_count", 1),
            name=f"distillate-{tmux_name}",
        )
        host = pod_info.host
        # Sync project to pod
        _sync_to_pod(project_path, pod_info)

    # Write budget.json — single source of truth for time budgets
    write_budget_json(project_path, project)
    run_budget = (project.get("duration_minutes") or 5) * 60 if project else 300
    session_budget = project.get("session_budget_seconds") if project else None

    # Spawn tmux session (interactive agent — no tee needed)
    if host:
        _spawn_ssh(tmux_name, host, str(project_path), cmd,
                   run_budget=run_budget, session_budget=session_budget)
    else:
        _spawn_local(tmux_name, project_path, cmd,
                     run_budget=run_budget, session_budget=session_budget,
                     extra_env=extra_env)

    now = datetime.now(timezone.utc).isoformat()
    session_data = {
        "session_id": session_id,
        "tmux_session": tmux_name,
        "started_at": now,
        "status": "running",
        "host": host,
        "model": model,
        "agent_type": agent_type,
        "runs_at_start": runs_at_start,
        "session_log": str(session_log),
    }
    if pod_info:
        session_data["pod_id"] = pod_info.id
        session_data["gpu_type"] = pod_info.gpu_type
        session_data["cost_per_hour"] = pod_info.cost_per_hour
    return session_data


def _sync_to_pod(project_path: Path, pod_info) -> None:
    """Sync local project to a cloud pod via rsync over SSH."""
    import subprocess as _sp

    dest = f"{pod_info.ssh_user}@{pod_info.host}:{project_path.name}/"
    ssh_opts = f"ssh -p {pod_info.ssh_port} -o StrictHostKeyChecking=no"

    # Check if there's a GitHub repo we can clone instead
    git_remote = _sp.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, cwd=project_path,
    )
    if git_remote.returncode == 0 and git_remote.stdout.strip():
        # Clone from GitHub (faster, avoids large file transfers)
        repo_url = git_remote.stdout.strip()
        _sp.run(
            ["ssh", "-p", str(pod_info.ssh_port),
             "-o", "StrictHostKeyChecking=no",
             f"{pod_info.ssh_user}@{pod_info.host}",
             f"git clone {repo_url} {project_path.name}"],
            capture_output=True, text=True, timeout=120,
        )
    else:
        # rsync local files
        _sp.run(
            ["rsync", "-avz", "--exclude", ".git",
             "-e", ssh_opts,
             str(project_path) + "/", dest],
            capture_output=True, text=True, timeout=300,
        )


def _spawn_local(session_name: str, work_dir: Path, command: str,
                  *, run_budget: int = 300,
                  session_budget: int | None = None,
                  extra_env: dict[str, str] | None = None) -> int:
    """Spawn a local tmux session. Returns tmux server PID."""
    # Prepend PATH setup into the command so claude/python3 are found
    # regardless of how tmux was started (Electron has minimal PATH,
    # and tmux server may have been started with a different environment).
    # Also unset CLAUDECODE so Claude Code doesn't refuse to start
    # (it blocks nested sessions, but tmux sessions are independent).
    # Unset ANTHROPIC_API_KEY so Claude Code uses SSO auth (Max/Pro
    # subscription) instead of billing against the raw API key.
    extra_paths = "/usr/local/bin:/opt/homebrew/bin:" + str(Path.home() / ".local" / "bin")
    # Source bash login profile for SSO auth, then run the command
    bash_profile = Path.home() / ".bash_profile"
    source_line = f"source {shlex.quote(str(bash_profile))} >/dev/null 2>&1; " if bash_profile.exists() else ""
    budget_exports = f"export DISTILLATE_RUN_BUDGET_SECONDS={run_budget}; "
    if session_budget is not None:
        budget_exports += f"export DISTILLATE_SESSION_BUDGET_SECONDS={session_budget}; "
    # Agent-specific env vars (e.g. HF Inference Providers for Pi)
    agent_exports = ""
    if extra_env:
        agent_exports = " ".join(f"export {k}={shlex.quote(v)};" for k, v in extra_env.items()) + " "
    full_command = f'{source_line}export PATH="{extra_paths}:$PATH"; export DISTILLATE_SESSION=1; {budget_exports}{agent_exports}unset CLAUDECODE; unset ANTHROPIC_API_KEY; {command}'

    print(f"[launch] tmux new-session -d -s {session_name} -c {work_dir}")
    print(f"[launch] command: {full_command}")

    # Set tmux options before creating the session to avoid green bar flash
    # -g sets global defaults that apply to new sessions
    subprocess.run(["tmux", "set-option", "-g", "status", "off"], capture_output=True)
    # escape-time 0 removes tmux's ~500 ms delay on ESC — set once on the
    # server so the desktop PTY attach doesn't need its own setup round-trip.
    subprocess.run(["tmux", "set-option", "-g", "escape-time", "0"], capture_output=True)
    # window-size latest: size the window to the most recently active client
    # instead of the smallest attached one. Prevents a stale 80-col client
    # (or the initial detached 80×24 default) from pinning the pane narrow
    # and clipping agent output on the real viewer.
    subprocess.run(["tmux", "set-option", "-g", "window-size", "latest"], capture_output=True)

    result = subprocess.run(
        [
            "tmux", "new-session", "-d",
            # -x/-y set the initial detached window size. Without this tmux
            # defaults to 80×24, the agent renders its first frames wrapped
            # at 80 cols, and those frames stay baked into scrollback even
            # after the desktop PTY attaches at a wider size.
            "-x", "220", "-y", "50",
            "-s", session_name,
            "-c", str(work_dir),
            full_command,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create tmux session '{session_name}': {result.stderr.strip()}"
        )

    # Configure tmux session for embedded use (xterm.js)
    subprocess.run(["tmux", "set", "-t", session_name, "status", "off"], capture_output=True)
    subprocess.run(["tmux", "set", "-t", session_name, "mouse", "on"], capture_output=True)
    # Disable tmux's outer alt-screen so xterm.js's native scrollback captures
    # session output. Claude Code and most agent CLIs render inline (Ink/React
    # for CLI) — with alt-screen off, their history flows into xterm's native
    # scrollback instead of being trapped in tmux's internal pane buffer.
    subprocess.run(["tmux", "set-window-option", "-t", session_name, "alternate-screen", "off"], capture_output=True)

    # Auto-confirm workspace trust dialog (Enter after brief delay)
    import time
    time.sleep(3)
    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, "Enter"],
        capture_output=True,
    )

    # Get tmux server PID
    pid_result = subprocess.run(
        ["tmux", "display-message", "-p", "#{pid}"],
        capture_output=True,
        text=True,
    )
    try:
        return int(pid_result.stdout.strip())
    except ValueError:
        return 0


def _spawn_ssh(
    session_name: str, host: str, remote_dir: str, command: str,
    *, run_budget: int = 300, session_budget: int | None = None,
) -> None:
    """Spawn a remote tmux session via SSH."""
    budget_exports = f"export DISTILLATE_RUN_BUDGET_SECONDS={run_budget} && "
    if session_budget is not None:
        budget_exports += f"export DISTILLATE_SESSION_BUDGET_SECONDS={session_budget} && "
    ssh_cmd = (
        f"cd {shlex.quote(remote_dir)} && export DISTILLATE_SESSION=1 && {budget_exports}"
        f"tmux new-session -d -s {shlex.quote(session_name)} {shlex.quote(command)} && "
        f"tmux set-window-option -t {shlex.quote(session_name)} alternate-screen off"
    )
    result = subprocess.run(
        ["ssh", host, ssh_cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create remote tmux session on {host}: {result.stderr.strip()}"
        )


def session_status(session_name: str, host: str | None = None) -> str:
    """Check if tmux session is alive. Returns 'running' | 'completed' | 'unknown'."""
    cmd = ["tmux", "has-session", "-t", session_name]
    if host:
        cmd = ["ssh", host, f"tmux has-session -t {shlex.quote(session_name)}"]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0:
        return "running"
    return "completed"


def get_detailed_agent_status(session_name: str, host: str | None = None) -> dict:
    """Check tmux for detailed agent status (working/idle/waiting)."""
    info = {"status": "unknown", "bell": False}
    if host:
        # Remote status check is more expensive, skip for now or implement minimally
        return info

    try:
        # Check bell flag and pane title
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", session_name, "#{pane_title}|#{pane_bell_flag}"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout:
            parts = result.stdout.rstrip("\n").split("|")
            title = parts[0]
            bell = parts[1] == "1" if len(parts) > 1 else False
            
            from distillate.experiment_tools.workspace_tools import _detect_spinner
            spinner = _detect_spinner(title)
            
            if bell:
                info["status"] = "waiting"
                info["bell"] = True
            elif spinner == "working":
                info["status"] = "working"
            elif spinner == "idle_or_waiting":
                info["status"] = "idle"
            else:
                info["status"] = "unknown"
    except Exception:
        pass
    return info


def capture_pane(session_name: str, lines: int = 200, *, escapes: bool = True) -> str:
    """Capture the last N lines of output from a tmux session pane.

    With *escapes=True* (default), includes ANSI escape sequences so the
    output can be replayed into an xterm.js instance with colors intact.
    """
    _ensure_path()
    cmd = ["tmux", "capture-pane", "-t", session_name, "-p", "-S", str(-lines)]
    if escapes:
        cmd.insert(3, "-e")  # include escape sequences
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout


def attach_session(session_name: str, host: str | None = None) -> None:
    """Open a new terminal window attached to the tmux session.

    Spawns a separate Terminal.app window (macOS) so it works from both
    CLI and the desktop app's "Attach" button.
    """
    system = platform.system()

    if host:
        attach_cmd = f"ssh -t {shlex.quote(host)} tmux attach -t {shlex.quote(session_name)}"
    else:
        attach_cmd = f"tmux attach -t {session_name}"

    if system == "Darwin":
        # macOS: use osascript to open a new Terminal.app window
        script = f'tell application "Terminal" to do script "{attach_cmd}"'
        subprocess.run(["osascript", "-e", script], capture_output=True)
    elif system == "Linux":
        # Linux: try common terminal emulators
        for term in ("x-terminal-emulator", "gnome-terminal", "xterm"):
            if shutil.which(term):
                if term == "gnome-terminal":
                    subprocess.Popen([term, "--", "bash", "-c", attach_cmd])
                else:
                    subprocess.Popen([term, "-e", attach_cmd])
                return
        raise RuntimeError("No terminal emulator found. Install x-terminal-emulator.")
    else:
        raise RuntimeError(f"Unsupported platform: {system}. Attach manually: {attach_cmd}")


def stop_session(session_name: str, host: str | None = None) -> bool:
    """Stop a tmux session: send C-c, wait briefly, then kill the session."""
    import time

    if host:
        subprocess.run(
            ["ssh", host, f"tmux send-keys -t {shlex.quote(session_name)} C-c ''"],
            capture_output=True,
        )
        time.sleep(2)
        subprocess.run(
            ["ssh", host, f"tmux kill-session -t {shlex.quote(session_name)}"],
            capture_output=True,
        )
        return True

    # Local: send C-c, wait, then kill the session
    subprocess.run(["tmux", "send-keys", "-t", session_name, "C-c", ""], capture_output=True)
    time.sleep(2)
    result = subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)
    return result.returncode == 0


def list_sessions() -> list[dict]:
    """List all distillate-* tmux sessions with status.

    Returns [{"name": str, "activity": str}].
    """
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name} #{session_activity}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    sessions = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(" ", 1)
        name = parts[0]
        if not name.startswith("distillate-"):
            continue
        activity = parts[1] if len(parts) > 1 else ""
        sessions.append({"name": name, "activity": activity})
    return sessions


# ---------------------------------------------------------------------------
# Refresh session statuses in state
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auto-continuation
# ---------------------------------------------------------------------------

def should_continue(project: dict) -> bool:
    """Check if project goals are unmet and another session should run.

    Compares the best result from kept runs against each goal's threshold.
    Returns True if any goal is not yet met.
    """
    goals = project.get("goals", [])
    if not goals:
        return False

    runs = project.get("runs", {})
    # Collect best metric values from non-crash runs
    best: dict[str, float] = {}
    for run in runs.values():
        if run.get("decision") == "crash" or run.get("status") == "failed":
            continue
        for k, v in run.get("results", {}).items():
            if not isinstance(v, (int, float)):
                continue
            if k not in best:
                best[k] = v
            else:
                best[k] = max(best[k], v)  # will compare per-goal direction below

    for goal in goals:
        metric = goal.get("metric", "")
        direction = goal.get("direction", "maximize")
        threshold = goal.get("threshold")
        if threshold is None or not metric:
            continue

        val = best.get(metric)
        if val is None:
            return True  # metric never measured → not met

        if direction == "maximize" and val < threshold:
            return True
        if direction == "minimize" and val > threshold:
            return True

    return False


def build_continuation_prompt(project: dict, original_prompt: str) -> str:
    """Append prior-run context to the original PROMPT.md for a continuation session."""
    runs = project.get("runs", {})
    if not runs:
        return original_prompt

    lines = [
        "",
        "## Context from Previous Sessions",
        "",
        f"This experiment has **{len(runs)} prior run(s)**.",
        "",
    ]

    # Summarize recent kept/discarded runs
    sorted_runs = sorted(
        runs.values(),
        key=lambda r: r.get("started_at", "") or r.get("timestamp", ""),
    )
    for run in sorted_runs[-10:]:
        run_id = run.get("id", "?")
        status = run.get("status", run.get("decision", "?"))
        hypothesis = run.get("hypothesis", "")
        results = run.get("results", {})
        reasoning = run.get("reasoning", "")

        results_str = ", ".join(
            f"{k}={v}" for k, v in results.items()
            if isinstance(v, (int, float))
        )

        lines.append(f"### {run_id} [{status}]")
        if hypothesis:
            lines.append(f"**Hypothesis:** {hypothesis}")
        if results_str:
            lines.append(f"**Results:** {results_str}")
        if reasoning:
            lines.append(f"**Reasoning:** {reasoning}")
        lines.append("")

    # Append goals reminder
    goals = project.get("goals", [])
    if goals:
        lines.append("### Goals Still to Meet")
        for g in goals:
            lines.append(
                f"- {g.get('metric', '?')}: {g.get('direction', '?')} "
                f"threshold {g.get('threshold', '?')}"
            )
        lines.append("")

    lines.append(
        "**Build on what worked, avoid repeating failed approaches. "
        "Reference prior run IDs in your reasoning.**"
    )

    return original_prompt + "\n".join(lines)


def launch_continuation(
    project_path: Path,
    project: dict,
    *,
    model: str = "claude-sonnet-4-5-20250929",
    max_turns: int = 100,
) -> dict:
    """Launch a continuation session with enriched context.

    Writes goal reminders into context.md (appended after the standard
    run history that ``_generate_run_context`` produces), then delegates
    to ``launch_experiment`` which already concatenates context.md with
    PROMPT.md.
    """
    project_path = project_path.resolve()

    # _generate_run_context is called inside launch_experiment, but we
    # want to append goal reminders.  Generate it first, then append.
    context_path = _generate_run_context(project_path)

    goals = project.get("goals", [])
    if goals and context_path:
        extra = ["\n## Goals Still to Meet\n"]
        for g in goals:
            extra.append(
                f"- {g.get('metric', '?')}: {g.get('direction', '?')} "
                f"threshold {g.get('threshold', '?')}"
            )
        extra.append("")
        extra.append(
            "**Focus on meeting these goals. Build on what worked, "
            "avoid repeating failed approaches.**"
        )
        with open(context_path, "a", encoding="utf-8") as f:
            f.write("\n".join(extra) + "\n")

    result = launch_experiment(
        project_path,
        model=model,
        max_turns=max_turns,
        project=project,
    )

    # Rewrite budget.json with the original session start time so the
    # session-level budget tracks cumulative time across restarts.
    sessions = project.get("sessions", {})
    earliest_start = None
    for sess in sessions.values():
        sa = sess.get("started_at", "")
        if sa and (earliest_start is None or sa < earliest_start):
            earliest_start = sa
    if earliest_start:
        write_budget_json(project_path, project, session_started_at=earliest_start)

    return result


def launch_sweep(
    project_path: Path,
    project: dict,
    configs: list[dict],
    *,
    model: str = "claude-sonnet-4-5-20250929",
    max_turns: int = 100,
) -> list[dict]:
    """Launch parallel ablation sessions, one per config variant.

    Each config dict is injected into the PROMPT.md as a "## Sweep
    Configuration" section.  Each session runs in its own tmux window.

    Returns a list of session dicts (same shape as ``launch_experiment``).
    """
    project_path = project_path.resolve()
    prompt_file = project_path / "PROMPT.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"No PROMPT.md found in {project_path}")

    original_prompt = prompt_file.read_text(encoding="utf-8")
    results = []

    for i, cfg in enumerate(configs):
        # Build variant PROMPT.md with config injected
        variant_lines = [
            original_prompt,
            "",
            "## Sweep Configuration",
            "",
            f"This is variant **{i + 1}** of a {len(configs)}-config sweep.",
            "Use exactly these hyperparameters:",
            "",
        ]
        for k, v in cfg.items():
            variant_lines.append(f"- **{k}**: `{v}`")
        variant_lines.append("")
        variant_lines.append(
            "Record results with a run ID prefixed with "
            f"`sweep_{i + 1:02d}_` in `.distillate/runs.jsonl`."
        )

        variant_prompt = "\n".join(variant_lines)

        # Write variant PROMPT.md to a temp location
        variant_path = project_path / ".distillate" / f"PROMPT_sweep_{i + 1:02d}.md"
        variant_path.parent.mkdir(exist_ok=True)
        variant_path.write_text(variant_prompt, encoding="utf-8")

        # Temporarily swap PROMPT.md for this launch
        backup = prompt_file.read_text(encoding="utf-8")
        try:
            prompt_file.write_text(variant_prompt, encoding="utf-8")
            session_data = launch_experiment(
                project_path,
                model=model,
                max_turns=max_turns,
                project=project,
            )
            results.append(session_data)
        finally:
            prompt_file.write_text(backup, encoding="utf-8")

    return results


def write_steering(project_path: Path, text: str) -> Path:
    """Write steering instructions to .distillate/steering.md.

    This is the durable-record primitive. Two readers consume it:

    - ``_build_launch_prompt()`` on session start — inlines the text into
      the launch prompt and unlinks the file (single-shot).
    - ``distillate.hooks.post_bash`` inside a running session — prints the
      text as a ``*** USER INSTRUCTION ***`` banner on the next tool use,
      then unlinks.

    Live sessions also receive the steering via
    :func:`inject_steering_to_tmux` (called from ``steer_experiment_tool``)
    for immediate delivery; the file is the fallback path.

    Returns the path written.
    """
    project_path = Path(project_path).resolve()
    steering_path = project_path / ".distillate" / "steering.md"
    steering_path.parent.mkdir(exist_ok=True)
    steering_path.write_text(
        f"# Steering Instructions\n\n{text}\n",
        encoding="utf-8",
    )
    return steering_path


def inject_into_tmux(
    tmux_name: str,
    text: str = "",
    *,
    keys: list[str] | None = None,
    host: str | None = None,
) -> dict:
    """Send literal text and/or named keys to a tmux pane.

    ``text`` is typed literally via ``send-keys -l`` so special characters
    are not interpreted.  ``keys`` are named keys sent without ``-l`` so
    tmux interprets names like ``Enter``, ``Escape``, ``Space``.

    When ``text`` is non-empty and ``keys`` is None, ``["Enter"]`` is used
    so the message is submitted automatically — same as the old
    ``inject_steering_to_tmux`` behaviour.  Pass ``keys=[]`` to type text
    without submitting.

    Works whether the agent is idle or mid-execution: if Claude Code is
    running a tool the keystrokes buffer in the PTY and are processed when
    the agent returns to its input prompt.

    Returns ``{"ok": bool, "session": str, "error": str | None}``.
    """
    import time

    if not tmux_name:
        return {"ok": False, "session": tmux_name, "error": "empty session name"}

    if text and keys is None:
        keys = ["Enter"]
    elif keys is None:
        keys = []

    try:
        if host:
            if text:
                subprocess.run(
                    ["ssh", host,
                     f"tmux send-keys -t {shlex.quote(tmux_name)} -l "
                     f"{shlex.quote(text)}"],
                    capture_output=True, timeout=5,
                )
                time.sleep(0.2)
            for key in keys:
                subprocess.run(
                    ["ssh", host,
                     f"tmux send-keys -t {shlex.quote(tmux_name)} "
                     f"{shlex.quote(key)}"],
                    capture_output=True, timeout=5,
                )
                time.sleep(0.1)
            return {"ok": True, "session": tmux_name, "error": None}

        if text:
            r = subprocess.run(
                ["tmux", "send-keys", "-t", tmux_name, "-l", text],
                capture_output=True, timeout=3,
            )
            if r.returncode != 0:
                err = f"send-keys -l rc={r.returncode}: {r.stderr.decode().strip()}"
                log.warning("inject_into_tmux text failed for %s: %s", tmux_name, err)
                return {"ok": False, "session": tmux_name, "error": err}
            time.sleep(0.2)

        for key in keys:
            r = subprocess.run(
                ["tmux", "send-keys", "-t", tmux_name, key],
                capture_output=True, timeout=3,
            )
            if r.returncode != 0:
                err = f"send-keys {key!r} rc={r.returncode}: {r.stderr.decode().strip()}"
                log.warning("inject_into_tmux key failed for %s: %s", tmux_name, err)
                return {"ok": False, "session": tmux_name, "error": err}
            time.sleep(0.1)

        return {"ok": True, "session": tmux_name, "error": None}

    except Exception as exc:
        log.exception("inject_into_tmux failed for %s", tmux_name)
        return {"ok": False, "session": tmux_name, "error": str(exc)}


def inject_steering_to_tmux(
    tmux_name: str, text: str, host: str | None = None
) -> bool:
    """Backward-compat wrapper around :func:`inject_into_tmux`."""
    return inject_into_tmux(tmux_name, text, host=host)["ok"]


def send_key_to_tmux(
    tmux_name: str, key: str, host: str | None = None
) -> bool:
    """Backward-compat wrapper around :func:`inject_into_tmux`."""
    return inject_into_tmux(tmux_name, keys=[key], host=host)["ok"]


def _rescan_after_session(experiment_id: str, state) -> dict | None:
    """Rescan a project after a session completes, adding new runs to state.

    Shared by ``run_campaign()`` (CLI foreground) and server SSE loop.
    Returns ``{"new_runs": int, "total_runs": int, "best_metric": dict|None}``
    or None on failure.
    """
    from distillate.experiments import scan_experiment
    from distillate.state import acquire_lock, release_lock

    proj = state.get_experiment(experiment_id)
    if not proj:
        return None

    proj_path = Path(proj.get("path", ""))
    if not proj_path.is_dir():
        return None

    result = scan_experiment(proj_path)
    if "error" in result:
        return None

    acquire_lock()
    try:
        state.reload()
        existing = state.get_experiment(experiment_id)
        if not existing:
            return None
        old_runs = existing.get("runs", {})
        old_count = len(old_runs)
        existing_names = {str(r.get("name", "")) for r in old_runs.values()}
        new_runs = 0
        for run_id, run_data in result.get("runs", {}).items():
            if run_data["name"] not in existing_names:
                state.add_run(experiment_id, run_id, run_data)
                new_runs += 1
        state.update_experiment(
            experiment_id,
            last_scanned_at=datetime.now(timezone.utc).isoformat(),
            last_commit_hash=result.get("head_hash", ""),
        )
        state.save()
    finally:
        release_lock()

    # Find best metric across all non-crash runs
    best_metric = None
    updated_proj = state.get_experiment(experiment_id)
    if updated_proj:
        for run in updated_proj.get("runs", {}).values():
            if run.get("decision") == "crash" or run.get("status") == "failed":
                continue
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    if best_metric is None or v > next(iter(best_metric.values())):
                        best_metric = {k: v}

    # Send experiment completion email (non-blocking)
    try:
        from distillate.cloud_email import send_experiment_event, _cloud_configured
        if _cloud_configured() and new_runs > 0:
            proj_data = state.get_experiment(experiment_id) or {}
            runs_all = proj_data.get("runs", {})
            kept = sum(1 for r in runs_all.values()
                       if (r.get("decision") or "") == "best")
            best_str = ""
            if best_metric:
                k, v = next(iter(best_metric.items()))
                best_str = f"{k}={v}"
            send_experiment_event(
                state,
                project_name=proj_data.get("name", experiment_id),
                runs=len(runs_all),
                kept=kept,
                best_metric=best_str,
                insight=proj_data.get("latest_learning", ""),
                github_url=proj_data.get("github_url", ""),
            )
    except Exception:
        log.debug("Cloud email send failed (non-critical)", exc_info=True)

    # Push updated state to cloud (non-blocking)
    try:
        from distillate.cloud_sync import cloud_sync_available, push_state
        if cloud_sync_available() and new_runs > 0:
            push_state(state)
    except Exception:
        log.debug("Cloud push after session failed (non-critical)", exc_info=True)

    return {
        "new_runs": new_runs,
        "total_runs": old_count + new_runs,
        "best_metric": best_metric,
    }


def run_campaign(
    experiment_id: str,
    state,
    *,
    max_sessions: int = 10,
    model: str = "claude-sonnet-4-5-20250929",
    max_turns: int = 100,
    poll_interval: int = 10,
    on_event: Optional[callable] = None,
    stop_flag: Optional["threading.Event"] = None,
) -> dict:
    """Synchronous campaign loop: launch → poll → rescan → check goals → repeat.

    Called by both the CLI (foreground) and server (via run_in_executor).
    Returns ``{"sessions_launched": N, "stop_reason": "goal_reached|budget_exhausted|user_stopped"}``.
    """
    import threading
    import time

    from distillate.state import acquire_lock, release_lock

    if stop_flag is None:
        stop_flag = threading.Event()

    sessions_launched = 0

    def _emit(event: dict):
        if on_event:
            on_event(event)
        # Also append to events.jsonl
        proj = state.get_experiment(experiment_id)
        if proj:
            proj_path = Path(proj.get("path", ""))
            events_file = proj_path / ".distillate" / "events.jsonl"
            events_file.parent.mkdir(exist_ok=True)
            try:
                with open(events_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
            except OSError:
                pass

    while not stop_flag.is_set():
        state.reload()
        proj = state.get_experiment(experiment_id)
        if not proj:
            break

        campaign = proj.get("campaign", {})
        if campaign.get("status") not in ("running", None):
            # Externally paused
            return {"sessions_launched": sessions_launched, "stop_reason": "user_stopped"}

        # Budget check
        total = campaign.get("sessions_launched", 0)
        if total >= max_sessions:
            _emit({
                "type": "campaign_completed",
                "ts": datetime.now(timezone.utc).isoformat(),
                "experiment_id": experiment_id,
                "sessions_launched": total,
                "stop_reason": "budget_exhausted",
            })
            return {"sessions_launched": sessions_launched, "stop_reason": "budget_exhausted"}

        # Goal check
        if not should_continue(proj):
            _emit({
                "type": "goal_reached",
                "ts": datetime.now(timezone.utc).isoformat(),
                "experiment_id": experiment_id,
                "sessions_launched": total,
            })
            return {"sessions_launched": sessions_launched, "stop_reason": "goal_reached"}

        # Launch session
        proj_path = Path(proj.get("path", ""))
        if not proj_path.is_dir():
            break

        try:
            session_data = launch_continuation(
                proj_path, proj, model=model, max_turns=max_turns,
            )
        except Exception:
            log.exception("Campaign launch failed for %s", experiment_id)
            time.sleep(30)
            continue

        sessions_launched += 1

        # Save session + update campaign counters
        acquire_lock()
        try:
            state.reload()
            state.add_session(experiment_id, session_data["session_id"], session_data)
            p = state.get_experiment(experiment_id)
            c = dict(p.get("campaign", {}))
            c["sessions_launched"] = c.get("sessions_launched", 0) + 1
            c["current_session_id"] = session_data["session_id"]
            state.update_experiment(experiment_id, campaign=c)
            state.save()
        finally:
            release_lock()

        _emit({
            "type": "campaign_run_started",
            "ts": datetime.now(timezone.utc).isoformat(),
            "experiment_id": experiment_id,
            "session_id": session_data["session_id"],
            "sessions_launched": c["sessions_launched"],
            "budget_remaining": max_sessions - c["sessions_launched"],
        })

        # Poll for session completion
        tmux_name = session_data.get("tmux_session", "")
        while not stop_flag.is_set():
            time.sleep(poll_interval)
            state.reload()
            p = state.get_experiment(experiment_id)
            c = p.get("campaign", {}) if p else {}
            if c.get("status") not in ("running", None):
                return {"sessions_launched": sessions_launched, "stop_reason": "user_stopped"}
            try:
                actual = session_status(tmux_name, None)
            except Exception:
                actual = "unknown"
            if actual != "running":
                # Rescan
                try:
                    _rescan_after_session(experiment_id, state)
                except Exception:
                    log.exception("Campaign rescan failed for %s", experiment_id)
                break

        # Small delay before next iteration
        time.sleep(5)

    # If we exited due to stop_flag
    if stop_flag.is_set():
        return {"sessions_launched": sessions_launched, "stop_reason": "user_stopped"}

    return {"sessions_launched": sessions_launched, "stop_reason": "unknown"}


def refresh_session_statuses(state) -> int:
    """Check all running sessions and update their status in state.

    Also discovers untracked tmux sessions matching the distillate-*
    naming pattern and registers them.

    Returns count of sessions that changed from running to completed.
    """
    changed = 0

    # Discover untracked tmux sessions
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}:#{session_created}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line or ":" not in line:
                    continue
                tmux_name, created = line.split(":", 1)
                if not tmux_name.startswith("distillate-"):
                    continue
                # Match to a project — require exact `distillate-<proj_id>-NNN`
                # so e.g. tinymatmul-t4 doesn't also register under tinymatmul.
                for proj_id, proj in state.experiments.items():
                    if re.match(rf"^distillate-{re.escape(proj_id)}-\d+$", tmux_name):
                        sessions = proj.get("sessions", {})
                        # Check if already tracked
                        already = any(
                            s.get("tmux_session") == tmux_name
                            for s in sessions.values()
                        )
                        if not already:
                            # Register the discovered session
                            sess_id = tmux_name
                            try:
                                started = datetime.fromtimestamp(
                                    int(created), tz=timezone.utc
                                ).isoformat()
                            except (ValueError, OSError):
                                started = datetime.now(timezone.utc).isoformat()
                            sessions[sess_id] = {
                                "tmux_session": tmux_name,
                                "status": "running",
                                "started_at": started,
                                "discovered": True,
                            }
                            state.update_experiment(proj_id, sessions=sessions)
                            changed += 1
                            log.info("Discovered untracked session: %s → %s",
                                     tmux_name, proj_id)
                        break
    except Exception:
        log.debug("tmux discovery failed (non-critical)", exc_info=True)

    # Check known sessions
    for proj_id, proj in state.experiments.items():
        sessions = proj.get("sessions", {})
        for sess_id, sess in sessions.items():
            if sess.get("status") != "running":
                continue
            tmux_name = sess.get("tmux_session", "")
            host = sess.get("host")
            
            # Detailed status check — preserve user-requested graceful stop
            detailed = get_detailed_agent_status(tmux_name, host)
            if sess.get("agent_status") != "stopping":
                sess["agent_status"] = detailed["status"]
            if detailed.get("bell"):
                sess["attention_needed"] = True
            else:
                sess.pop("attention_needed", None)

            # Graceful stop + idle → agent finished its run and is waiting
            # at the prompt. Kill the session so on_stop fires and the UI
            # exits the "Finishing run…" state. No C-c needed since it's idle.
            if sess.get("agent_status") == "stopping" and detailed.get("status") == "idle":
                try:
                    subprocess.run(
                        ["tmux", "kill-session", "-t", tmux_name],
                        capture_output=True, timeout=3,
                    )
                except Exception:
                    pass

            actual = session_status(tmux_name, host)
            if actual != "running":
                sess["status"] = "completed"
                sess["completed_at"] = datetime.now(timezone.utc).isoformat()
                changed += 1
    return changed
