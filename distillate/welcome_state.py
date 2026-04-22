"""Welcome screen state synthesizer — rule-based fallback chain.

Walks a 7-state priority chain and returns a structured JSON blob that
the desktop renderer uses to populate the B+ welcome screen.

Phase 1: rule-based templates. Phase 3+ will upgrade to agent-generated
narration without changing the return schema.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from distillate.state import State

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Greeting helper
# ---------------------------------------------------------------------------

def _time_greeting(name: str = "") -> str:
    hour = datetime.now().hour
    if hour < 12:
        salut = "Good morning"
    elif hour < 17:
        salut = "Good afternoon"
    else:
        salut = "Good evening"
    return f"{salut}, {name}" if name else salut


# ---------------------------------------------------------------------------
# Individual state detectors
# ---------------------------------------------------------------------------

def _active_experiments(state: State) -> List[Dict[str, Any]]:
    """Return experiments with a running session right now."""
    active = []
    for proj in state.experiments.values():
        sessions = proj.get("sessions") or {}
        for sess in sessions.values():
            if sess.get("status") == "running":
                active.append(proj)
                break
    return active


def _recent_wins(state: State, days: int = 7) -> List[Dict[str, Any]]:
    """Experiments that completed in the last N days with a positive result."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    wins = []
    for proj in state.experiments.values():
        runs = proj.get("runs") or {}
        for run in runs.values():
            decision = run.get("decision", "")
            if decision in ("best", "completed"):
                started = run.get("started_at", "")
                if started >= cutoff:
                    wins.append(proj)
                    break
    return wins


def _stuck_experiments(state: State, flat_threshold: int = 3) -> List[Dict[str, Any]]:
    """Experiments where the last N runs show no improvement."""
    stuck = []
    for proj in state.experiments.values():
        runs_dict = proj.get("runs") or {}
        runs = sorted(runs_dict.values(), key=lambda r: r.get("started_at", ""))
        if len(runs) < flat_threshold:
            continue
        # Check if the last `flat_threshold` runs have no "best" decision
        tail = runs[-flat_threshold:]
        if all(r.get("decision") != "best" for r in tail):
            stuck.append(proj)
    return stuck


def _reading_queue(state: State, min_unread: int = 3) -> Dict[str, Any]:
    """Check for unread highlights / papers."""
    queue_status = "tracked" if _is_zotero_reader() else "on_remarkable"
    queue = state.documents_with_status(queue_status)
    if len(queue) >= min_unread:
        # Group by source
        sources: Dict[str, int] = {}
        for doc in queue:
            title = doc.get("title", "Unknown")
            sources[title] = sources.get(title, 0) + 1
        return {"count": len(queue), "sources": sources, "papers": queue[:5]}
    return {}


def _stale_projects(state: State, days: int = 7) -> List[Dict[str, Any]]:
    """Projects quiet for N+ days but with activity history."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    stale = []
    for ws in state.workspaces.values():
        if ws.get("default"):
            continue  # Skip Workbench
        updated = ws.get("updated_at", "")
        created = ws.get("created_at", "")
        # Must have been active at some point (not brand new and stale)
        if created and updated and updated < cutoff and created < cutoff:
            stale.append(ws)
    return stale


def _is_zotero_reader() -> bool:
    try:
        from distillate.config import is_zotero_reader
        return is_zotero_reader()
    except Exception:
        return True


# ---------------------------------------------------------------------------
# State builders — each returns a partial welcome dict
# ---------------------------------------------------------------------------

def _build_active(experiments: List[Dict], state: State, greeting: str) -> Dict[str, Any]:
    top = experiments[0]
    name = top.get("name", top.get("id", "experiment"))
    runs = top.get("runs") or {}
    run_count = len(runs)

    # Calculate improvement
    annotation = f"live \u00b7 run {run_count}"
    narration = [
        f"{'An experiment is' if len(experiments) == 1 else f'{len(experiments)} experiments are'} running. "
        f"**{name}** is on run {run_count}.",
    ]
    suggestions = [
        {"label": f"Check on {name}", "prompt": f"What's the status of {name}? Show me the latest runs.", "specialist": None},
        {"label": "Compare active experiments", "prompt": "Compare all my running experiments side by side.", "specialist": None},
    ]

    return {
        "state_id": "active",
        "greeting": greeting,
        "strip": {
            "type": "frontier_chart",
            "experiment_id": top.get("id", ""),
            "label": name,
            "annotation": annotation,
            "secondary_link": f"see all ({len(experiments)}) \u2192" if len(experiments) > 1 else "",
            "secondary_target": "/experiments",
        },
        "narration_paragraphs": narration,
        "suggestions": suggestions,
        "input_placeholder": "What shall we transmute today?",
    }


def _build_recent_win(wins: List[Dict], greeting: str) -> Dict[str, Any]:
    top = wins[0]
    name = top.get("name", top.get("id", "experiment"))
    runs = top.get("runs") or {}
    run_count = len(runs)

    return {
        "state_id": "recent_win",
        "greeting": greeting,
        "strip": {
            "type": "frontier_chart",
            "experiment_id": top.get("id", ""),
            "label": name,
            "annotation": f"completed \u00b7 {run_count} runs",
            "secondary_link": "view results \u2192",
            "secondary_target": f"/experiments/{top.get('id', '')}",
        },
        "narration_paragraphs": [
            f"**{name}** wrapped recently. Worth a quick look at the results before you start the next thing.",
        ],
        "suggestions": [
            {"label": f"Review {name} results", "prompt": f"Show me the final results of {name}.", "specialist": None},
            {"label": "Propose a follow-up", "prompt": f"Based on the results of {name}, what should I try next?", "specialist": None},
            {"label": "Start something new", "prompt": "Let's start a new experiment.", "specialist": None},
        ],
        "input_placeholder": "What's next?",
    }


def _build_stuck(experiments: List[Dict], greeting: str) -> Dict[str, Any]:
    top = experiments[0]
    name = top.get("name", top.get("id", "experiment"))

    return {
        "state_id": "stuck",
        "greeting": greeting,
        "strip": {
            "type": "frontier_chart",
            "experiment_id": top.get("id", ""),
            "label": name,
            "annotation": "flat \u2014 no improvement in recent runs",
            "secondary_link": "investigate \u2192",
            "secondary_target": f"/experiments/{top.get('id', '')}",
        },
        "narration_paragraphs": [
            f"**{name}** looks stuck. The last few runs haven't improved. "
            f"I can read the recent run logs and tell you what changed, or propose a different approach.",
        ],
        "suggestions": [
            {"label": "Read the run logs", "prompt": f"Read the recent run logs of {name} and tell me what changed.", "specialist": None},
            {"label": "Propose a parameter sweep", "prompt": f"Propose a parameter sweep for {name} to break through the plateau.", "specialist": None},
            {"label": "Open the experiment", "prompt": f"Show me {name} in detail.", "specialist": None},
        ],
        "input_placeholder": "What's wrong with it?",
    }


def _build_reading_queue(queue_info: Dict, greeting: str) -> Dict[str, Any]:
    count = queue_info["count"]
    papers = queue_info.get("papers", [])

    strip_items = []
    for p in papers[:3]:
        title = p.get("title", "Untitled")
        strip_items.append({"title": title, "unread": 1})

    return {
        "state_id": "reading_queue",
        "greeting": greeting,
        "strip": {
            "type": "paper_queue",
            "label": "Reading queue",
            "annotation": f"{count} unread",
            "items": strip_items,
            "secondary_link": "view library \u2192",
            "secondary_target": "/papers",
        },
        "narration_paragraphs": [
            f"Quiet moment. No experiments running, but you've got **{count} papers** waiting in the queue.",
            "Want me to summarize them, or are you in the mood to start something new?",
        ],
        "suggestions": [
            {"label": "Summarize my reading queue", "prompt": "Summarize the papers in my reading queue.", "specialist": "librarian"},
            {"label": "Suggest experiments from readings", "prompt": "Based on my recent reading, suggest experiments I could try.", "specialist": None},
            {"label": "Launch a new experiment", "prompt": "Let's start a new experiment from scratch.", "specialist": None},
        ],
        "input_placeholder": "Type to Nicolas\u2026",
    }


def _build_stale_project(projects: List[Dict], greeting: str) -> Dict[str, Any]:
    top = projects[0]
    name = top.get("name", top.get("id", "project"))
    updated = top.get("updated_at", "")

    days_stale = 0
    if updated:
        try:
            dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            days_stale = (datetime.now(timezone.utc) - dt).days
        except Exception:
            pass

    return {
        "state_id": "stale_project",
        "greeting": greeting,
        "strip": {
            "type": "project_activity",
            "label": name,
            "annotation": f"quiet for {days_stale} days" if days_stale else "quiet",
            "secondary_link": "open project \u2192",
            "secondary_target": f"/projects/{top.get('id', '')}",
        },
        "narration_paragraphs": [
            f"The lab is quiet, but **{name}** has been waiting"
            + (f" for {days_stale} days." if days_stale else "."),
            "I can pull it up and remind you where you left off.",
        ],
        "suggestions": [
            {"label": f"Open {name}", "prompt": f"Show me the {name} project and remind me what I was working on.", "specialist": None},
            {"label": "Start something new", "prompt": "Let's start a new experiment.", "specialist": None},
            {"label": "Just chat", "prompt": "I want to think out loud about what to work on next.", "specialist": None},
        ],
        "input_placeholder": "Type to Nicolas\u2026",
    }


def _build_reflective(state: State, greeting: str) -> Dict[str, Any]:
    # Summarize recent activity
    projects = list(state.experiments.values())
    total_runs = sum(len(p.get("runs") or {}) for p in projects)

    return {
        "state_id": "reflective",
        "greeting": greeting,
        "strip": {
            "type": "week_in_review",
            "label": "Last week",
            "annotation": f"{len(projects)} experiments \u00b7 {total_runs} runs",
            "secondary_link": "view all results \u2192",
            "secondary_target": "/experiments",
        },
        "narration_paragraphs": [
            "The lab is quiet \u2014 no experiments running, no urgent reading. "
            "This is a good moment to step back: what would you like to try next?",
        ],
        "suggestions": [
            {"label": "Suggest follow-ups", "prompt": "Based on my recent experiments, suggest what I should try next.", "specialist": None},
            {"label": "Survey the library", "prompt": "What's interesting in my paper library that I haven't explored yet?", "specialist": "research_agent"},
            {"label": "Review my projects", "prompt": "Review all my projects and tell me where each stands.", "specialist": None},
            {"label": "Just chat", "prompt": "I want to think out loud about my research direction.", "specialist": None},
        ],
        "input_placeholder": "Type to Nicolas\u2026",
    }


def _build_onboarding(greeting: str) -> Dict[str, Any]:
    return {
        "state_id": "onboarding",
        "greeting": greeting,
        "strip": {
            "type": "onboarding",
            "label": "Welcome to your lab",
            "annotation": "",
            "steps": [
                "Launch a demo experiment \u2014 watch an AI agent improve a model in real time",
                "Import your paper library from Zotero or arXiv",
                "Tell me about your research \u2014 I\u2019ll suggest experiments to run",
            ],
        },
        "narration_paragraphs": [
            "Distillate is an integrated research environment where AI agents run your "
            "experiments \u2014 autonomously coding, training, and reporting results while you "
            "steer from the chat.",
            "The fastest way to see it: launch the TinyMatMul demo below. A transformer "
            "will train itself while you watch the chart update in real time. Or tell me "
            "what you\u2019re working on and I\u2019ll suggest what to run first.",
        ],
        "suggestions": [
            {"label": "I\u2019m researching\u2026", "prompt": "I'm researching ", "specialist": None},
            {"label": "Launch demo experiment", "prompt": "Launch the TinyMatMul demo experiment.", "specialist": None},
            {"label": "Import a project", "prompt": "I have an existing project directory I'd like to import.", "specialist": None},
            {"label": "What can you do?", "prompt": "What can you do? Give me a concrete overview.", "specialist": None},
        ],
        "input_placeholder": "What are you working on?",
    }


# ---------------------------------------------------------------------------
# Main synthesizer
# ---------------------------------------------------------------------------

def synthesize_welcome_state(state: State, user_name: str = "") -> Dict[str, Any]:
    """Walk the fallback chain and return the welcome screen state.

    Returns a dict matching the schema in primitives-v2-welcome-screen.md §5.
    """
    greeting = _time_greeting(user_name)

    # Priority 1: active experiments
    active = _active_experiments(state)
    if active:
        return _build_active(active, state, greeting)

    # Priority 2: recent wins
    wins = _recent_wins(state)
    if wins:
        return _build_recent_win(wins, greeting)

    # Priority 3: stuck experiments
    stuck = _stuck_experiments(state)
    if stuck:
        return _build_stuck(stuck, greeting)

    # Priority 4: reading queue
    queue = _reading_queue(state)
    if queue:
        return _build_reading_queue(queue, greeting)

    # Priority 5: stale projects
    stale = _stale_projects(state)
    if stale:
        return _build_stale_project(stale, greeting)

    # Priority 6: reflective (projects exist, nothing urgent)
    has_projects = bool(state.experiments) or bool(state.workspaces)
    if has_projects:
        return _build_reflective(state, greeting)

    # Priority 7: onboarding (first run, no data)
    return _build_onboarding(greeting)
