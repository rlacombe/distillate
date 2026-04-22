"""LLM-backed narrative enrichment for experiment runs.

Split out of experiments.py to keep the Claude API integration in one
place. Public names are re-exported from distillate.experiments for
backwards compatibility.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from distillate.experiments import _run_sort_key

log = logging.getLogger(__name__)


_FINGERPRINT_HEX_LENGTH = 16    # hex chars for enrichment cache fingerprint
_COMMAND_DISPLAY_CHARS = 200     # truncation limit for commands in LLM prompt


def _runs_fingerprint(runs: dict) -> str:
    """Hash of sorted run hyperparams+metrics for cache invalidation."""
    items = []
    for rid in sorted(runs.keys()):
        r = runs[rid]
        hp = json.dumps(r.get("hyperparameters", {}), sort_keys=True)
        mt = json.dumps(r.get("results", {}), sort_keys=True)
        items.append(f"{rid}:{hp}:{mt}")
    return hashlib.sha256("|".join(items).encode()).hexdigest()[:_FINGERPRINT_HEX_LENGTH]


def load_enrichment_cache(project_path: Path) -> dict:
    """Load LLM enrichment cache from .distillate/llm_enrichment.json."""
    cache_file = project_path / ".distillate" / "llm_enrichment.json"
    if not cache_file.exists():
        return {}
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_enrichment_cache(project_path: Path, data: dict) -> None:
    """Save LLM enrichment cache to .distillate/llm_enrichment.json."""
    distillate_dir = project_path / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    (distillate_dir / "llm_enrichment.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def _build_enrichment_prompt(runs: dict, project_name: str, results_md: str = "") -> str:
    """Build the Sonnet prompt for enriching experiment runs."""
    # Sort runs by version number, then chronologically
    sorted_runs = sorted(runs.items(), key=lambda kv: _run_sort_key(kv[1]))

    run_descriptions = []
    prev_hp: dict = {}
    for i, (rid, run) in enumerate(sorted_runs, 1):
        hp = run.get("hyperparameters", {})
        metrics = run.get("results", {})
        command = run.get("command", "")

        # Build diff from previous
        diff_parts = []
        if prev_hp:
            for k in sorted(set(hp.keys()) | set(prev_hp.keys())):
                old = prev_hp.get(k)
                new = hp.get(k)
                if old != new:
                    if old is not None and new is not None:
                        diff_parts.append(f"{k}: {old} -> {new}")
                    elif new is not None:
                        diff_parts.append(f"{k}: (new) {new}")

        hp_str = ", ".join(f"{k}={v}" for k, v in sorted(hp.items()))
        metric_str = ", ".join(
            f"{k}={v}" for k, v in sorted(metrics.items())
            if isinstance(v, (int, float))
        )
        diff_str = "; ".join(diff_parts) if diff_parts else "(first experiment)"

        # Use run number from ID if available, otherwise positional
        m = re.match(r"(?:run_?)(\d+)", rid)
        num_label = f"#{m.group(1)}" if m else f"#{i}"
        desc = f"Experiment {num_label} [{rid}]: {run.get('name', '?')}\n"
        desc += f"  Hyperparameters: {hp_str}\n"
        desc += f"  Metrics: {metric_str}\n"
        desc += f"  Changes from previous: {diff_str}\n"
        if command:
            desc += f"  Command: {command[:_COMMAND_DISPLAY_CHARS]}\n"

        run_descriptions.append(desc)
        prev_hp = hp

    run_ids_json = json.dumps([rid for rid, _ in sorted_runs])

    results_section = ""
    if results_md:
        truncated = results_md[:10000]
        if len(results_md) > 10000:
            truncated += "\n\n[... truncated ...]"
        results_section = f"""
The agent also wrote this research summary (RESULTS.md):

{truncated}

Use the agent's own observations to inform your analysis. Prefer the agent's phrasing when accurate.
"""

    return f"""You are a research scientist writing a lab notebook for an ML experiment series.

Project: {project_name}

Experiment timeline (chronological order):

{chr(10).join(run_descriptions)}
{results_section}
For each experiment, generate:
1. name: A descriptive human-readable name (e.g. "Baseline Character-Level Transformer", "Scaled-Up Model", "Triplet Tokenization Breakthrough"). Keep it short (3-6 words).
2. hypothesis: Why this experiment was tried (1-2 sentences). For the first experiment, describe the baseline rationale.
3. approach: What was done differently from the previous experiment (1-2 sentences).
4. analysis: What the results mean — interpret the metrics by name, note failure modes, explain surprising outcomes (2-3 sentences). Be specific: say "loss dropped from 13.0 to 0.0" not just "improved". If no metrics were reported, say so explicitly and speculate why (e.g. incomplete run, setup step, utility script).
5. next_steps: What to try next based on these results (1 sentence).
6. params: Estimated total trainable parameter count as a SHORT string (e.g. "~98K", "~1.5K", "~350"). ALWAYS estimate this from the architecture hyperparameters — for a transformer, consider embeddings, attention projections, feedforward layers, and layer norms. If a run's hyperparameters are incomplete, infer the architecture from adjacent experiments in the series (e.g. the first run likely used the same architecture as the next run that has full hyperparameters). NEVER use "N/A" — every training run had a model, estimate its size.
7. validation: The key quality metric result as a SHORT string. Use the most important evaluation metric (e.g. "100%", "99.8%", "converged", "loss=0.0"). If the experiment has no reported results but the architecture was later re-run successfully with the same or similar hyperparameters, infer the likely outcome. If the experiment genuinely failed or was too small to learn, say "failed" or "did not converge". NEVER say "no metrics" — always make a judgment call based on context.

Also generate project-level insights:
8. key_breakthrough: Which experiment was the biggest improvement and why (1-2 sentences). Reference specific metric values.
9. lessons_learned: 3-5 bullets of deeper insights connecting the experiments. Reference concrete numbers, not vague claims.

Output ONLY valid JSON in this exact format (no markdown, no code blocks):
{{"project": {{"key_breakthrough": "...", "lessons_learned": ["..."]}}, "runs": {{{run_ids_json[1:-1].replace('"', '')}: see below}}}}

The "runs" object must have keys matching these exact run IDs: {run_ids_json}
Each run value: {{"name": "...", "hypothesis": "...", "approach": "...", "analysis": "...", "next_steps": "...", "params": "...", "validation": "..."}}"""


def enrich_runs_with_llm(runs: dict, project_name: str,
                          project_path: Path) -> Optional[dict]:
    """Enrich experiment runs with LLM-generated narrative sections.

    Uses Claude Sonnet to add hypothesis, approach, analysis, next_steps,
    and a descriptive name to each run.  Also adds project-level insights.

    Returns enrichment dict or None if unavailable (no API key, error).
    Results are cached in .distillate/llm_enrichment.json.
    """
    from distillate import config

    if not config.ANTHROPIC_API_KEY:
        log.debug("No ANTHROPIC_API_KEY — skipping LLM enrichment")
        return None

    if not runs:
        return None

    # Check cache
    fingerprint = _runs_fingerprint(runs)
    cache = load_enrichment_cache(project_path)
    if cache.get("fingerprint") == fingerprint and cache.get("enrichment"):
        log.info("Using cached LLM enrichment for %s", project_name)
        return cache["enrichment"]

    # Read RESULTS.md if available
    results_md = ""
    results_path = project_path / "RESULTS.md"
    if results_path.exists():
        try:
            results_md = results_path.read_text(encoding="utf-8")
        except OSError:
            pass

    # Build prompt and call Sonnet
    prompt = _build_enrichment_prompt(runs, project_name, results_md=results_md)

    # Guard against overly large prompts (rough estimate: ~4 chars per token)
    _MAX_ENRICHMENT_CHARS = 400_000  # ~100K tokens, well under 200K limit
    if len(prompt) > _MAX_ENRICHMENT_CHARS:
        log.warning(
            "Enrichment prompt too large (%d chars) for %s — skipping",
            len(prompt), project_name,
        )
        return None

    try:
        import anthropic
    except ImportError:
        log.error("LLM enrichment requires the 'anthropic' package")
        return None

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=16384,
            messages=[{"role": "user", "content": prompt}],
        )
        if not response.content or not hasattr(response.content[0], "text"):
            log.error("Unexpected API response: no content blocks")
            return None
        text = response.content[0].text.strip()
        stop_reason = response.stop_reason
        log.info("LLM enrichment response: %d chars, stop=%s", len(text), stop_reason)
    except (anthropic.APIError, anthropic.APIConnectionError) as e:
        log.error("Claude API error during LLM enrichment: %s", e)
        return None

    # Parse JSON response (handle markdown code blocks)
    if text.startswith("```") and "\n" in text:
        text = text.split("\n", 1)[1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]

    # If response was truncated (end_turn not reached), try to close JSON
    if stop_reason == "max_tokens":
        log.warning("LLM enrichment truncated — attempting to repair JSON")
        # Count unclosed braces and brackets
        open_braces = text.count("{") - text.count("}")
        open_brackets = text.count("[") - text.count("]")
        # Strip trailing incomplete string/value
        text = text.rstrip()
        if text and text[-1] not in "{}[],":
            # Likely mid-value — find last complete entry
            for trim_char in [",", "}", "]"]:
                last_pos = text.rfind(trim_char)
                if last_pos > 0:
                    text = text[:last_pos + 1]
                    break
        # Close brackets/braces
        text += "]" * max(0, open_brackets) + "}" * max(0, open_braces)

    enrichment = None
    try:
        enrichment = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON object from surrounding text
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                enrichment = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                log.error(
                    "Failed to parse LLM enrichment JSON "
                    "(first 200 chars: %s)", text[:200],
                )
                return None
        else:
            log.error("No JSON found in LLM enrichment response")
            return None

    if not isinstance(enrichment, dict) or "runs" not in enrichment:
        log.error("LLM enrichment missing 'runs' key")
        return None

    if "project" not in enrichment:
        log.warning("LLM enrichment missing 'project' key — not caching (will retry)")
        return enrichment  # Return partial result but don't cache

    # Cache the result
    _save_enrichment_cache(project_path, {
        "fingerprint": fingerprint,
        "enrichment": enrichment,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })

    return enrichment
