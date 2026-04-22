"""Experiments -- create, launch, scan, campaign, stream, compare."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from distillate.routes import _context
from distillate.state import acquire_lock, release_lock

log = logging.getLogger(__name__)

router = APIRouter()

# Campaign task tracking (moved from closure scope)
_campaign_tasks: dict[str, asyncio.Task] = {}


@router.get("/health/harness")
async def harness_health():
    """Return whether the default Claude Code harness binary is available."""
    import shutil
    cli = shutil.which("claude")
    return JSONResponse({
        "ok": cli is not None,
        "harness": "claude-code",
        "path": cli,
        "install_hint": "npm install -g @anthropic-ai/claude-code" if cli is None else None,
    })


# -------------------------------------------------------------------
# Helper functions (were closure functions in _create_app)
# -------------------------------------------------------------------

def _rescan_project(proj_id: str, proj: dict) -> dict | None:
    """Rescan a project, update state, return summary or None."""
    _state = _context._state

    from distillate.experiments import (
        backfill_runs_from_events,
        scan_experiment,
    )

    proj_path = Path(proj.get("path", ""))
    if not proj_path.is_dir():
        return None

    # Backfill runs.jsonl from events.jsonl before scanning
    backfilled = backfill_runs_from_events(proj_path)
    if backfilled:
        log.info("Backfilled %d run(s) for %s", backfilled, proj_id)

    result = scan_experiment(proj_path)
    if "error" in result:
        return None

    acquire_lock()
    try:
        _state.reload()
        existing = _state.get_experiment(proj_id)
        if not existing:
            return None
        old_runs = existing.get("runs", {})
        old_count = len(old_runs)
        scan_names = {r["name"] for r in result.get("runs", {}).values()}
        existing_names = {r["name"] for r in old_runs.values()}

        # Remove stale runs no longer in scan results (e.g. artifact-
        # scanned duplicates superseded by structured runs)
        stale_keys = [
            eid for eid, erun in old_runs.items()
            if erun["name"] not in scan_names
        ]
        for k in stale_keys:
            del old_runs[k]

        new_runs = 0
        for run_id, run_data in result.get("runs", {}).items():
            if run_data["name"] not in existing_names:
                _state.add_run(proj_id, run_id, run_data)
                new_runs += 1
            else:
                # Merge scan data into existing run (preserves state,
                # picks up new fields like backfilled descriptions)
                for eid, erun in old_runs.items():
                    if erun["name"] == run_data["name"]:
                        for k, v in run_data.items():
                            if k == "id":
                                continue
                            if k == "decision":
                                # Don't overwrite "best" with "completed"
                                # from backward-compat parsing of old
                                # runs.jsonl keep/discard entries
                                if v and erun.get(k) != "best":
                                    erun[k] = v
                            elif k in ("status", "results",
                                     "agent_reasoning", "description",
                                     "hypothesis", "reasoning"):
                                # Always take latest value for mutable fields
                                if v:
                                    erun[k] = v
                            elif v and not erun.get(k):
                                erun[k] = v
                        break
        _state.update_experiment(
            proj_id,
            last_scanned_at=datetime.now(timezone.utc).isoformat(),
            last_commit_hash=result.get("head_hash", ""),
        )
        _state.save()
    finally:
        release_lock()

    # Find best metric across all non-crash runs
    best_metric = None
    updated_proj = _state.get_experiment(proj_id)
    if updated_proj:
        for run in updated_proj.get("runs", {}).values():
            if run.get("decision") == "crash" or run.get("status") == "failed":
                continue
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    if best_metric is None or v > next(iter(best_metric.values())):
                        best_metric = {k: v}

    return {
        "new_runs": new_runs,
        "total_runs": old_count + new_runs,
        "best_metric": best_metric,
        "backfilled": backfilled,
    }


async def _maybe_auto_continue(
    proj_id: str, proj: dict, loop,
) -> dict | None:
    """If goals unmet (or queue remains), launch a continuation session.

    Returns a ``session_continued`` SSE event dict, or None.
    """
    _state = _context._state

    from distillate.launcher import launch_continuation, should_continue

    # Check queue first (decrement count if present)
    queue = proj.get("continuation_queue", {})
    queue_remaining = queue.get("count", 0)
    if queue_remaining <= 0 and not should_continue(proj):
        return None

    proj_path = Path(proj.get("path", ""))
    if not proj_path.is_dir():
        return None

    model = queue.get("model") or "claude-sonnet-4-5-20250929"
    max_turns = queue.get("max_turns", 100)

    try:
        session_data = await loop.run_in_executor(
            _context._executor,
            lambda: launch_continuation(
                proj_path, proj, model=model, max_turns=max_turns,
            ),
        )
    except Exception:
        log.exception("Auto-continue failed for %s", proj_id)
        return None

    # Save session + decrement queue
    acquire_lock()
    try:
        _state.reload()
        _state.add_session(proj_id, session_data["session_id"], session_data)
        if queue_remaining > 0:
            _state.update_experiment(
                proj_id,
                continuation_queue={
                    **queue,
                    "count": queue_remaining - 1,
                },
            )
        _state.save()
    finally:
        release_lock()

    return {
        "type": "session_continued",
        "experiment_id": proj_id,
        "tmux_session": session_data["tmux_session"],
        "model": model,
        "queue_remaining": max(0, queue_remaining - 1),
    }


async def _campaign_loop(experiment_id: str):
    """Background campaign loop -- delegates to shared run_campaign()."""
    import threading

    from distillate.launcher import run_campaign

    _state = _context._state

    _state.reload()
    proj = _state.get_experiment(experiment_id)
    if not proj:
        _campaign_tasks.pop(experiment_id, None)
        return

    campaign = proj.get("campaign", {})
    budget = campaign.get("budget", {})
    max_sessions = budget.get("max_sessions", 10)
    model = campaign.get("model", "claude-sonnet-4-5-20250929")
    max_turns = campaign.get("max_turns", 100)

    # The stop flag is checked by run_campaign; we set it on cancel
    stop_flag = threading.Event()

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            _context._executor,
            lambda: run_campaign(
                experiment_id,
                _state,
                max_sessions=max_sessions,
                model=model,
                max_turns=max_turns,
                stop_flag=stop_flag,
            ),
        )
    except Exception:
        log.exception("Campaign loop failed for %s", experiment_id)
    finally:
        _campaign_tasks.pop(experiment_id, None)


def _extract_run_number(name: str) -> tuple:
    """Extract sortable (number, suffix) from run name like 'run_122' or 'run_004a'.

    Returns (number, suffix) for run_NNN patterns, or (0, "") for others
    so non-numeric IDs sort first by timestamp fallback.
    """
    m = re.match(r"(?:run_?)(\d+)([a-z]?)", name)
    if m:
        return (int(m.group(1)), m.group(2))
    return (0, "")


def _sort_runs_chronologically(proj: dict, runs: dict) -> list:
    """Sort runs by runs.jsonl file order (true chronological).

    The append order in runs.jsonl is the ground truth for when runs
    happened. Timestamps can be wrong (midnight sessions, backfills).
    Runs only in state.json (no runs.jsonl) are appended at the end
    sorted by timestamp as fallback.
    """
    # Build file-order index from runs.jsonl
    file_order: dict[str, int] = {}  # run_name -> position
    proj_path = proj.get("path", "")
    if proj_path:
        runs_file = Path(proj_path) / ".distillate" / "runs.jsonl"
        if runs_file.exists():
            try:
                pos = 0
                for line in runs_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    rid = entry.get("id", "")
                    st = entry.get("status", "")
                    # Use first terminal entry position for each run
                    if rid and rid not in file_order and st in (
                        "best", "completed", "keep", "discard", "crash",
                    ):
                        file_order[rid] = pos
                        pos += 1
            except OSError:
                pass

    # Build name->file_position lookup
    name_to_pos: dict[str, int] = {}
    for sid, r in runs.items():
        name = r.get("name", sid)
        if name in file_order:
            name_to_pos[sid] = file_order[name]

    fallback_start = len(file_order)

    def _sort_key(sid_run):
        sid, r = sid_run
        if sid in name_to_pos:
            return (0, name_to_pos[sid], "")
        # Fallback: timestamp sort for state-only runs
        ts = r.get("started_at", "") or r.get("completed_at", "")
        return (1, fallback_start, ts)

    return [r for _, r in sorted(runs.items(), key=_sort_key)]


# -------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------

@router.post("/experiments/create")
async def create_experiment(body: dict):
    """Create a new experiment: scan directory, draft PROMPT.md with Claude,
    install hooks, register, and launch a Claude Code session.

    Uses the CLI's init_experiment_tool for steps 1-4, then launches.

    Body: {"name": "experiment-name", "goal": "what to optimize",
           "target": "/path" (optional), "constraints": "..." (optional),
           "launch": true (optional)}

    Returns progress via streaming NDJSON so the desktop can update a
    flowchart in real time.
    """
    _state = _context._state

    from starlette.responses import StreamingResponse

    from distillate import config
    from distillate.experiments import slugify

    name = body.get("name", "").strip()
    goal = body.get("goal", "").strip()
    if not name:
        return JSONResponse(
            {"ok": False, "reason": "name is required"}, status_code=400,
        )

    # Default workspace_id to Workbench if not provided
    workspace_id = body.get("workspace_id")
    if not workspace_id:
        default_ws = _state.get_default_workspace()
        if not default_ws:
            _state.ensure_workbench()
            default_ws = _state.get_default_workspace()
        workspace_id = default_ws["id"] if default_ws else None

    target = body.get("target", "")
    if not target:
        experiment_id = slugify(name)
        root = config.EXPERIMENTS_ROOT or str(Path.home() / "experiments")
        target = str(Path(root) / experiment_id)
    target_path = Path(target).expanduser().resolve()

    async def generate():
        from distillate.experiment_tools import init_experiment_tool
        from distillate.launcher import launch_experiment

        # Step 1: Create project directory
        yield json.dumps({"step": 1, "label": "Create project directory", "status": "active"}) + "\n"
        try:
            target_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            yield json.dumps({"step": 1, "status": "error", "detail": str(e)}) + "\n"
            return
        yield json.dumps({"step": 1, "status": "done", "detail": str(target_path)}) + "\n"

        # Steps 2-4: init_experiment_tool handles scanning, PROMPT.md
        # generation (via Claude), hooks, reporting, and registration
        yield json.dumps({"step": 2, "label": "Draft PROMPT.md with Claude", "status": "active"}) + "\n"

        _state.reload()
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                _context._executor,
                lambda: init_experiment_tool(
                    state=_state,
                    path=str(target_path),
                    goal=goal,
                    name=name,
                    constraints=body.get("constraints", ""),
                    duration_minutes=body.get("duration_minutes", 5),
                    primary_metric=body.get("primary_metric", ""),
                    metric_direction=body.get("metric_direction", ""),
                    metric_constraint=body.get("metric_constraint", ""),
                    workspace_id=workspace_id or "",
                ),
            )
        except Exception as e:
            yield json.dumps({"step": 2, "status": "error", "detail": str(e)}) + "\n"
            return

        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            # If PROMPT.md already exists, that's okay -- use the existing one
            if "already exists" in error_msg:
                yield json.dumps({"step": 2, "status": "done", "detail": "Using existing PROMPT.md"}) + "\n"
                # Still need to ensure hooks and registration
                from distillate.launcher import _install_hooks_into
                yield json.dumps({"step": 3, "label": "Install hooks & reporting", "status": "active"}) + "\n"
                _install_hooks_into(target_path)
                yield json.dumps({"step": 3, "status": "done"}) + "\n"

                yield json.dumps({"step": 4, "label": "Register experiment", "status": "active"}) + "\n"
                from distillate.experiments import slugify
                from distillate.experiment_tools import _parse_goals_from_text
                experiment_id = slugify(name)
                if not _state.has_experiment(experiment_id):
                    display_name = name.replace("-", " ").title() if name == experiment_id else name
                    _state.add_experiment(
                        experiment_id=experiment_id,
                        name=display_name,
                        path=str(target_path),
                        description=goal,
                        agent_type=body.get("agent_type", "claude"),
                        session_budget_seconds=body.get("session_budget_seconds"),
                        workspace_id=workspace_id,
                    )
                    _state.save()
                # Auto-parse goals from free-form goal text
                parsed_goals = _parse_goals_from_text(goal)
                if parsed_goals:
                    _state.update_experiment(experiment_id, goals=parsed_goals)
                primary_metric = body.get("primary_metric", "")
                if primary_metric:
                    _state.update_experiment(experiment_id, key_metric_name=primary_metric)
                if body.get("agent_type"):
                    _state.update_experiment(experiment_id, agent_type=body["agent_type"])
                if body.get("session_budget_seconds"):
                    _state.update_experiment(experiment_id, session_budget_seconds=body["session_budget_seconds"])
                _state.save()
                yield json.dumps({"step": 4, "status": "done", "experiment_id": experiment_id}) + "\n"
            else:
                yield json.dumps({"step": 2, "status": "error", "detail": error_msg}) + "\n"
                return
        elif result.get("needs_prompt_generation"):
            # No Anthropic API key — init_experiment_tool returned early without
            # installing hooks or registering. Complete setup here; the agent
            # will draft PROMPT.md on its first run using the context.
            from distillate.experiments import slugify
            from distillate.experiment_tools import _parse_goals_from_text
            from distillate.launcher import _install_hooks_into
            import shutil as _shutil

            experiment_id = slugify(name)

            # Write a minimal PROMPT.md so the directory isn't empty
            prompt_file = target_path / "PROMPT.md"
            if not prompt_file.exists():
                ctx = result.get("context", "")
                prompt_file.write_text(
                    f"# {name}\n\n{ctx}\n\n"
                    "<!-- PROMPT.md will be expanded by the agent on first run -->\n",
                    encoding="utf-8",
                )

            # Copy CLAUDE.md and install hooks
            claude_md_src = Path(__file__).parent.parent / "autoresearch" / "CLAUDE.md"
            if claude_md_src.exists():
                _shutil.copy2(claude_md_src, target_path / "CLAUDE.md")
            yield json.dumps({"step": 2, "status": "done", "detail": "PROMPT.md scaffolded"}) + "\n"

            yield json.dumps({"step": 3, "label": "Install hooks & reporting", "status": "active"}) + "\n"
            _install_hooks_into(target_path)
            yield json.dumps({"step": 3, "status": "done"}) + "\n"

            yield json.dumps({"step": 4, "label": "Register experiment", "status": "active"}) + "\n"
            if not _state.has_experiment(experiment_id):
                display_name = name.replace("-", " ").title() if name == experiment_id else name
                _state.add_experiment(
                    experiment_id=experiment_id,
                    name=display_name,
                    path=str(target_path),
                    description=goal,
                    agent_type=body.get("agent_type", "claude"),
                    session_budget_seconds=body.get("session_budget_seconds"),
                    workspace_id=workspace_id,
                )
            parsed_goals = _parse_goals_from_text(goal)
            if parsed_goals:
                _state.update_experiment(experiment_id, goals=parsed_goals)
            if body.get("agent_type"):
                _state.update_experiment(experiment_id, agent_type=body["agent_type"])
            if body.get("session_budget_seconds"):
                _state.update_experiment(experiment_id, session_budget_seconds=body["session_budget_seconds"])
            _state.save()
            yield json.dumps({"step": 4, "status": "done", "experiment_id": experiment_id}) + "\n"

        else:
            experiment_id = result["experiment_id"]
            yield json.dumps({"step": 2, "status": "done"}) + "\n"

            # Step 3: Hooks & reporting (already done by init_experiment_tool)
            yield json.dumps({"step": 3, "label": "Install hooks & reporting", "status": "done"}) + "\n"

            # Step 4: Register (already done by init_experiment_tool)
            yield json.dumps({"step": 4, "label": "Register experiment", "status": "done", "experiment_id": experiment_id}) + "\n"

        # Persist model and effort selection so the detail page and re-launch use them.
        _model = body.get("model", "") or "claude-sonnet-4-6"
        _effort = body.get("effort", "") or "high"
        _state.update_experiment(experiment_id, model=_model, effort=_effort)
        _state.save()

        # Record compute intent (if the user picked a cloud provider in the wizard).
        # Placed after registration so a registration failure leaves no stale
        # config behind; placed before launch so the agent's first run
        # sees the config in .distillate/budget.json.
        if body.get("compute") == "modal":
            try:
                from distillate.budget import write_modal_config
                write_modal_config(
                    cwd=target_path,
                    gpu=body.get("modal_gpu", "A100-80GB"),
                    budget_usd=float(body.get("modal_budget_usd", 25.0)),
                )
            except Exception as e:
                log.warning("Failed to write Modal config: %s", e)
        elif body.get("compute") == "hfjobs":
            try:
                from distillate.budget import write_compute_budget
                write_compute_budget(
                    cwd=target_path,
                    provider="hfjobs",
                    gpu_type=body.get("gpu_type", "a100-large"),
                    budget_usd=float(body.get("compute_budget_usd", 25.0)),
                )
                # Also persist compute config on the project in state
                _state.update_experiment(experiment_id, compute={
                    "provider": "hfjobs",
                    "gpu_type": body.get("gpu_type", "a100-large"),
                    "budget_usd": float(body.get("compute_budget_usd", 25.0)),
                })
                _state.save()
            except Exception as e:
                log.warning("Failed to write HF Jobs compute config: %s", e)

        # Step 5: Launch agent session
        if body.get("launch", True):
            _agent_label = body.get("agent_type", "claude").title()
            yield json.dumps({"step": 5, "label": f"Launch {_agent_label} session", "status": "active"}) + "\n"
            try:
                _state.reload()
                proj = _state.find_experiment(experiment_id)
                launch_result = await loop.run_in_executor(
                    _context._executor,
                    lambda: launch_experiment(
                        target_path, model=_model,
                        effort=_effort, max_turns=100, project=proj,
                    ),
                )
                acquire_lock()
                try:
                    sessions = proj.setdefault("sessions", {})
                    sessions[launch_result["session_id"]] = launch_result
                    _state.save()
                finally:
                    release_lock()
                yield json.dumps({
                    "step": 5, "status": "done",
                    "tmux_session": launch_result.get("tmux_session", ""),
                }) + "\n"
            except Exception as e:
                yield json.dumps({"step": 5, "status": "error", "detail": str(e)}) + "\n"
                return

        yield json.dumps({"done": True, "experiment_id": experiment_id, "path": str(target_path)}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/experiments/scaffold")
async def scaffold_from_template(body: dict):
    """Scaffold an experiment from a built-in template (no Claude API call)."""
    _state = _context._state

    from distillate import config
    from distillate.experiments import slugify
    from distillate.launcher import scaffold_experiment

    template = body.get("template", "").strip()
    name = body.get("name", "").strip() or template.replace("-", " ").title()
    if not template:
        return JSONResponse({"ok": False, "reason": "template required"}, status_code=400)

    experiment_id = slugify(name)
    root = config.EXPERIMENTS_ROOT or str(Path.home() / "experiments")
    target = Path(root) / experiment_id

    # If already scaffolded and registered, just return the existing project
    existing = _state.experiments.get(experiment_id)
    if existing:
        return JSONResponse({"ok": True, "experiment_id": experiment_id, "path": existing.get("path", str(target)), "already_exists": True})

    try:
        loop = asyncio.get_event_loop()
        result_path = await loop.run_in_executor(
            _context._executor, lambda: scaffold_experiment(template, target, name=name)
        )
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=404)
    except FileExistsError as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=409)
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

    # Register in state with appropriate metadata
    default_ws = _state.get_default_workspace()
    if not default_ws:
        _state.ensure_workbench()
        default_ws = _state.get_default_workspace()
    workspace_id = default_ws["id"] if default_ws else None

    _state.add_experiment(experiment_id, name, str(result_path), workspace_id=workspace_id)
    _state.update_experiment(experiment_id,
        key_metric_name="param_count",
        duration_minutes=5,
        goals=[
            {"metric": "test_accuracy", "threshold": 0.99, "direction": "maximize"},
            {"metric": "param_count", "threshold": 100000, "direction": "minimize"},
        ],
    )
    _state.save()

    return JSONResponse({"ok": True, "experiment_id": experiment_id, "path": str(result_path)})


@router.post("/experiments/{experiment_id}/github")
async def create_github_repo_endpoint(experiment_id: str, body: dict = None):
    """Create a GitHub repo for the experiment and push initial commit."""
    _state = _context._state

    from distillate.launcher import create_github_repo

    proj = _context._get_project_or_404(experiment_id)

    proj_path = Path(proj.get("path", ""))
    body = body or {}
    repo_name = body.get("name", f"distillate-xp-{experiment_id}")
    private = body.get("private", False)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        _context._executor,
        lambda: create_github_repo(proj_path, repo_name, private=private),
    )

    if result.get("ok"):
        _state.update_experiment(experiment_id, github_url=result.get("url", ""))
        _state.save()

    return JSONResponse(result)


@router.get("/experiments/{experiment_id}/prompt")
async def get_experiment_prompt(experiment_id: str):
    """Get the PROMPT.md content for a project."""
    proj = _context._get_project_or_404(experiment_id)

    prompt_path = Path(proj.get("path", "")) / "PROMPT.md"
    if not prompt_path.exists():
        return JSONResponse({"ok": False, "reason": "no_prompt"})

    content = prompt_path.read_text(encoding="utf-8")
    return JSONResponse({"ok": True, "content": content, "path": str(prompt_path)})


@router.put("/experiments/{experiment_id}/prompt")
async def update_experiment_prompt(experiment_id: str, body: dict):
    """Update the PROMPT.md content for a project."""
    _state = _context._state

    from distillate.experiments import detect_primary_metric as _detect_primary_metric

    proj = _context._get_project_or_404(experiment_id)

    content = body.get("content", "")
    project_path = Path(proj.get("path", ""))
    prompt_path = project_path / "PROMPT.md"
    prompt_path.write_text(content, encoding="utf-8")

    # Signal running agent that PROMPT.md changed
    distillate_dir = project_path / ".distillate"
    distillate_dir.mkdir(exist_ok=True)
    flag = distillate_dir / "prompt_updated"
    flag.write_text(
        datetime.now(timezone.utc).isoformat() + "\n",
        encoding="utf-8",
    )

    # Auto-detect primary metric from PROMPT.md content
    detected_metric = _detect_primary_metric(content)
    if detected_metric:
        _state.update_experiment(experiment_id, key_metric_name=detected_metric)
        _state.save()

    return JSONResponse({"ok": True, "detected_metric": detected_metric})


@router.get("/experiments/{experiment_id}/results")
async def get_experiment_results(experiment_id: str):
    """Get the RESULTS.md content for a project."""
    proj = _context._get_project_or_404(experiment_id)

    results_path = Path(proj.get("path", "")) / "RESULTS.md"
    if not results_path.exists():
        return JSONResponse({"ok": False, "reason": "no_results"})

    content = results_path.read_text(encoding="utf-8")
    return JSONResponse({"ok": True, "content": content, "path": str(results_path)})


@router.get("/experiments/{experiment_id}/radar")
async def experiment_radar(experiment_id: str):
    """Literature radar — papers from the library and trending sources that
    are relevant to this experiment's goals and techniques."""
    from distillate.experiment_tools._helpers import extract_experiment_keywords

    proj = _context._get_project_or_404(experiment_id)
    keywords = extract_experiment_keywords(proj)
    if not keywords:
        return JSONResponse({"ok": True, "library_matches": [], "trending_matches": [], "keywords": []})

    _state = _context._state
    goal_metric = next((g["metric"] for g in proj.get("goals", []) if g.get("metric")), None)
    already_linked = set(p.lower() for p in proj.get("linked_papers", []))

    # ── Library scan ────────────────────────────────────────────────────
    library_matches = []
    for key, doc in _state.documents.items():
        if doc.get("status") != "processed":
            continue
        meta = doc.get("metadata", {}) or {}
        citekey = (meta.get("citekey") or "").strip()
        title = doc.get("title", "")
        if citekey.lower() in already_linked or title.lower() in already_linked:
            continue

        paper_text = " ".join([
            title,
            " ".join(meta.get("tags") or []),
            doc.get("summary", "") or "",
            meta.get("abstract", "") or "",
        ]).lower()

        matched = [kw for kw in keywords if kw in paper_text]
        if len(matched) >= 2:
            kw_str = ", ".join(matched[:3])
            relevance = f"Discusses {kw_str} — relevant to your {goal_metric} goal" if goal_metric else f"Discusses {kw_str}"
            library_matches.append({
                "key": key,
                "title": title,
                "authors": doc.get("authors", [])[:2],
                "citation_count": meta.get("citation_count", 0),
                "matched_keywords": matched[:5],
                "match_count": len(matched),
                "relevance": relevance,
            })

    library_matches.sort(key=lambda m: m["match_count"], reverse=True)
    library_matches = library_matches[:5]

    # ── Trending scan ───────────────────────────────────────────────────
    trending_matches = []
    try:
        from distillate.huggingface import trending_papers
        loop = asyncio.get_event_loop()
        trending = await loop.run_in_executor(
            _context._executor, lambda: trending_papers(limit=8)
        )
        kw_set = set(keywords)
        for tp in trending:
            ai_kws = {kw.lower() for kw in tp.get("ai_keywords", [])}
            # Also match against title words
            title_words = set(tp.get("title", "").lower().split())
            overlaps = list(kw_set & (ai_kws | title_words))
            if len(overlaps) >= 2:
                kw_str = ", ".join(overlaps[:3])
                relevance = f"Discusses {kw_str} — relevant to your {goal_metric} goal" if goal_metric else f"Discusses {kw_str}"
                trending_matches.append({
                    "title": tp.get("title", ""),
                    "authors": tp.get("authors", [])[:2],
                    "upvotes": tp.get("upvotes", 0),
                    "github_stars": tp.get("github_stars"),
                    "hf_url": tp.get("hf_url", ""),
                    "pdf_url": tp.get("pdf_url", ""),
                    "matched_keywords": overlaps[:5],
                    "match_count": len(overlaps),
                    "relevance": relevance,
                })
        trending_matches.sort(key=lambda m: m["match_count"], reverse=True)
        trending_matches = trending_matches[:3]
    except Exception:
        log.debug("Radar: failed to fetch trending papers", exc_info=True)

    return JSONResponse({
        "ok": True,
        "library_matches": library_matches,
        "trending_matches": trending_matches,
        "keywords": keywords[:10],
    })


@router.get("/experiments/{experiment_id}/claude-md")
async def get_experiment_claude_md(experiment_id: str):
    """Get the CLAUDE.md (agent protocol) for a project."""
    proj = _context._get_project_or_404(experiment_id)

    claude_path = Path(proj.get("path", "")) / "CLAUDE.md"
    if not claude_path.exists():
        return JSONResponse({"ok": False, "reason": "no_claude_md"})

    content = claude_path.read_text(encoding="utf-8")
    return JSONResponse({"ok": True, "content": content, "path": str(claude_path)})


@router.put("/experiments/{experiment_id}/claude-md")
async def update_experiment_claude_md(experiment_id: str, body: dict):
    """Update the CLAUDE.md content for a project."""
    proj = _context._get_project_or_404(experiment_id)

    content = body.get("content", "")
    claude_path = Path(proj.get("path", "")) / "CLAUDE.md"
    claude_path.write_text(content, encoding="utf-8")
    return JSONResponse({"ok": True})


@router.get("/experiments/{experiment_id}/session")
async def get_session_output(experiment_id: str):
    """Get parsed session output for the project.

    Reads the session log file (.distillate/<session_id>.jsonl) which
    contains stream-json output piped via tee.  Falls back to
    capture_pane for sessions started before the log-file change.
    """
    from distillate.launcher import _ensure_path, capture_pane
    from distillate.server_helpers import _parse_stream_json

    _ensure_path()
    proj = _context._get_project_or_404(experiment_id)

    sessions = proj.get("sessions", {})
    # Find the most recent running session
    running = [s for s in sessions.values() if s.get("status") == "running"]
    if not running:
        return JSONResponse({"ok": False, "reason": "no_running_session", "output": ""}, status_code=404)

    sess = running[-1]
    tmux_name = sess.get("tmux_session", "")
    session_log = sess.get("session_log", "")

    try:
        raw = ""
        # Prefer log file (reliable, survives scrollback limits)
        if session_log:
            log_path = Path(session_log)
            if log_path.exists():
                # Read last ~50KB of the log file
                size = log_path.stat().st_size
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    if size > 50_000:
                        f.seek(size - 50_000)
                        f.readline()  # skip partial line
                    raw = f.read()

        # Fallback to capture_pane
        if not raw.strip() and tmux_name:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                _context._executor,
                lambda: capture_pane(tmux_name, lines=500),
            )

        output = _parse_stream_json(raw)
        return JSONResponse({
            "ok": True,
            "session": tmux_name,
            "output": output,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e), "output": ""}, status_code=500)


@router.post("/experiments/attach")
async def attach_experiment(body: dict):
    """Open a new Terminal window attached to the experiment's tmux session.

    Called by desktop app's 'Attach' button in Lab tab.
    Body: {"project": "tiny-gene-code"}
    """
    from distillate.launcher import attach_session

    project_query = body.get("project", "")
    if not project_query:
        return JSONResponse({"ok": False, "reason": "missing_project"}, status_code=400)

    proj = _context._get_project_or_404(project_query)

    sessions = proj.get("sessions", {})
    running = [s for s in sessions.values() if s.get("status") == "running"]
    if not running:
        return JSONResponse({"ok": False, "reason": "no_running_session"}, status_code=404)

    sess = running[-1]
    tmux_name = sess.get("tmux_session", "")
    host = sess.get("host")

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_context._executor, attach_session, tmux_name, host)
        return JSONResponse({"ok": True, "session": tmux_name})
    except RuntimeError as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@router.post("/experiments/{experiment_id}/launch")
async def launch_experiment_endpoint(experiment_id: str, body: dict = None):
    """Launch a new experiment session for the project."""
    _state = _context._state

    from distillate.launcher import launch_experiment

    import shutil

    body = body or {}
    proj = _context._get_project_or_404(experiment_id)

    proj_path = Path(proj.get("path", ""))
    if not proj_path.exists():
        return JSONResponse({"ok": False, "reason": "path_not_found"}, status_code=404)

    model = body.get("model", proj.get("model", "claude-sonnet-4-6"))
    prompt_override = body.get("prompt_override")
    agent_type = body.get("agent_type") or proj.get("agent_type", "claude")
    effort = body.get("effort", proj.get("effort", "high"))

    # Persist the effective model/agent/effort so the detail page chip
    # reflects what actually runs, even for experiments created before model
    # was stored (proj.model was empty).
    persist_updates = {}
    if proj.get("model") != model:
        persist_updates["model"] = model
    if proj.get("agent_type") != agent_type:
        persist_updates["agent_type"] = agent_type
    if proj.get("effort") != effort:
        persist_updates["effort"] = effort
    if persist_updates:
        _state.update_experiment(experiment_id, **persist_updates)
        _state.save()
        proj.update(persist_updates)
    if body.get("duration_minutes"):
        duration_minutes = int(body["duration_minutes"])
        _state.update_experiment(experiment_id, duration_minutes=duration_minutes)
        _state.save()
        proj["duration_minutes"] = duration_minutes

    if agent_type in ("claude", "claude-code") and shutil.which("claude") is None:
        return JSONResponse({
            "ok": False,
            "reason": "claude_not_found",
            "install_hint": "npm install -g @anthropic-ai/claude-code",
        }, status_code=422)

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _context._executor,
            lambda: launch_experiment(
                proj_path, model=model, project=proj,
                prompt_override=prompt_override,
                agent_type=agent_type,
                effort=effort,
            ),
        )
        # Persist session to state
        acquire_lock()
        try:
            sessions = proj.setdefault("sessions", {})
            sessions[result["session_id"]] = result
            _state.save()
        finally:
            release_lock()

        # Ensure a GitHub repo exists — create one silently if missing.
        if not proj.get("github_url"):
            async def _ensure_github():
                try:
                    from distillate.launcher import create_github_repo
                    repo_name = f"distillate-xp-{experiment_id}"
                    gh_result = await loop.run_in_executor(
                        _context._executor,
                        lambda: create_github_repo(proj_path, repo_name, private=False),
                    )
                    if gh_result.get("url"):
                        _state.update_experiment(experiment_id, github_url=gh_result["url"])
                        _state.save()
                except Exception as e:
                    log.debug("Auto GitHub repo creation failed for %s: %s", experiment_id, e)
            asyncio.create_task(_ensure_github())

        return JSONResponse({
            "ok": True,
            "session_id": result.get("session_id", ""),
            "tmux_session": result.get("tmux_session", ""),
        })
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@router.get("/experiments/{experiment_id}/jobs")
async def experiment_jobs(experiment_id: str):
    """Return GPU jobs for this experiment (from compute_spend.json + live status)."""
    proj = _context._get_project_or_404(experiment_id)
    proj_path = Path(proj.get("path", ""))
    spend_path = proj_path / ".distillate" / "compute_spend.json"

    if not spend_path.is_file():
        return JSONResponse({"ok": True, "jobs": []})

    try:
        spend_data = json.loads(spend_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return JSONResponse({"ok": True, "jobs": []})

    jobs = spend_data.get("jobs", [])
    result_jobs = []
    for j in jobs:
        entry = {
            "job_id": j.get("job_id", ""),
            "flavor": j.get("flavor", ""),
            "duration_seconds": j.get("duration_seconds", 0),
            "cost_usd": j.get("cost_usd", 0),
            "status": j.get("status", "completed"),
        }
        result_jobs.append(entry)

    # Try to get live status for jobs that may still be running
    compute = proj.get("compute", {}) or {}
    if compute.get("provider") in ("hfjobs", "huggingface"):
        try:
            from distillate import config
            from distillate.compute_hfjobs import HFJobsProvider
            provider = HFJobsProvider(namespace=config.HF_NAMESPACE)
            for entry in result_jobs:
                if entry["status"] in ("running", "pending"):
                    try:
                        info = provider.get_job(entry["job_id"])
                        if info:
                            entry["status"] = info.status
                            if hasattr(info, "duration_seconds") and info.duration_seconds:
                                entry["duration_seconds"] = info.duration_seconds
                    except Exception:
                        pass
        except Exception:
            pass

    return JSONResponse({
        "ok": True,
        "jobs": result_jobs,
        "total_usd": spend_data.get("total_usd", 0),
    })


@router.post("/experiments/{experiment_id}/jobs/{job_id}/cancel")
async def cancel_experiment_job(experiment_id: str, job_id: str):
    """Cancel a running HF Job for this experiment."""
    proj = _context._get_project_or_404(experiment_id)
    compute = proj.get("compute", {}) or {}
    if compute.get("provider") not in ("hfjobs", "huggingface"):
        return JSONResponse({"ok": False, "reason": "Not an HF Jobs experiment"}, status_code=400)

    try:
        from distillate import config
        from distillate.compute_hfjobs import HFJobsProvider
        provider = HFJobsProvider(namespace=config.HF_NAMESPACE)
        success = provider.cancel_job(job_id)
        return JSONResponse({"ok": success, "job_id": job_id})
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)


@router.post("/experiments/{experiment_id}/stop")
async def stop_experiment_endpoint(experiment_id: str):
    """Stop all running sessions for the project."""
    _state = _context._state

    from distillate.launcher import _ensure_path, refresh_session_statuses, stop_session

    _ensure_path()

    # Refresh tmux statuses first -- a session may have died but state
    # still says "running".  This prevents false "no_running_session".
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_context._executor, refresh_session_statuses, _state)

    proj = _context._get_project_or_404(experiment_id)

    sessions = proj.get("sessions", {})
    running = [s for s in sessions.values() if s.get("status") == "running"]
    if not running:
        _state.save()
        return JSONResponse({"ok": False, "reason": "no_running_session"}, status_code=404)

    stopped = []
    for sess in running:
        tmux_name = sess.get("tmux_session", "")
        host = sess.get("host")
        if tmux_name:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    _context._executor, stop_session, tmux_name, host,
                )
                sess["status"] = "completed"
                stopped.append(tmux_name)
            except Exception:
                log.debug("Failed to stop session %s", tmux_name, exc_info=True)
    if stopped:
        _state.save()
    return JSONResponse({"ok": True, "stopped": stopped})


@router.post("/experiments/{experiment_id}/stop-after-run")
async def stop_after_run_endpoint(experiment_id: str):
    """Request graceful stop: agent finishes its current run, then exits."""
    _state = _context._state

    from distillate.launcher import _ensure_path, refresh_session_statuses
    _ensure_path()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_context._executor, refresh_session_statuses, _state)

    proj = _context._get_project_or_404(experiment_id)

    sessions = proj.get("sessions", {})
    running = [s for s in sessions.values() if s.get("status") == "running"]
    if not running:
        _state.save()
        return JSONResponse({"ok": False, "reason": "no_running_session"}, status_code=404)

    proj_path = Path(proj.get("path", ""))
    if not proj_path.is_dir():
        return JSONResponse({"ok": False, "reason": "project_path_missing"}, status_code=500)

    stop_flag = proj_path / ".distillate" / "stop_requested"
    stop_flag.parent.mkdir(exist_ok=True)
    stop_flag.write_text("stop_requested\n", encoding="utf-8")

    for sess in running:
        sess["agent_status"] = "stopping"

    _state.save()

    # Inject a live message into the agent's context window so it knows
    # immediately, without waiting for the next start_run() call.
    from distillate.launcher import inject_into_tmux
    injection_results = []
    for sess in running:
        tmux_name = sess.get("tmux_session", "")
        host = sess.get("host")
        if not tmux_name:
            continue
        result = await loop.run_in_executor(
            _context._executor,
            lambda tn=tmux_name, h=host: inject_into_tmux(
                tn,
                "Stop requested: please finish your current run "
                "(call conclude_run), then exit.",
                host=h,
            ),
        )
        if not result["ok"]:
            log.warning(
                "stop-after-run injection failed for %s: %s",
                tmux_name, result.get("error"),
            )
        injection_results.append(result)

    injected = any(r["ok"] for r in injection_results)
    return JSONResponse({"ok": True, "injected": injected, "sessions": len(running)})


@router.post("/experiments/{experiment_id}/scan")
async def scan_experiment(experiment_id: str, full: str = ""):
    """Manually trigger a rescan for a project."""
    proj = _context._get_project_or_404(experiment_id)

    # Full rescan: clear watch state + scan state to force re-read
    if full:
        proj_path = Path(proj.get("path", ""))
        for state_file in ("watch_state.json", "scan_state.json"):
            sf = proj_path / ".distillate" / state_file
            if sf.exists():
                sf.unlink()

    loop = asyncio.get_event_loop()
    summary = await loop.run_in_executor(
        _context._executor, _rescan_project, experiment_id, proj,
    )
    if summary is None:
        return JSONResponse({"ok": False, "reason": "scan_failed"}, status_code=500)
    return JSONResponse({"ok": True, **summary})


@router.post("/experiments/{experiment_id}/queue")
async def queue_continuation(experiment_id: str, request: Request):
    """Queue N continuation sessions for a project.

    Body: ``{"count": int, "model": str (optional), "max_turns": int (optional)}``
    """
    _state = _context._state

    body = await request.json()
    count = body.get("count", 1)
    model = body.get("model", "claude-sonnet-4-5-20250929")
    max_turns = body.get("max_turns", 100)

    _context._get_project_or_404(experiment_id)

    _state.update_experiment(experiment_id, continuation_queue={
        "count": count,
        "model": model,
        "max_turns": max_turns,
    }, auto_continue=True)
    _state.save()

    return JSONResponse({
        "ok": True,
        "queued": count,
        "model": model,
    })


@router.post("/experiments/{experiment_id}/sweep")
async def sweep_experiment(experiment_id: str, request: Request):
    """Launch a parallel hyperparameter sweep.

    Body: ``{"configs": [{"lr": 0.001}, ...], "model": str, "max_turns": int}``
    """
    _state = _context._state

    from distillate.launcher import launch_sweep

    body = await request.json()
    configs = body.get("configs", [])
    model = body.get("model", "claude-sonnet-4-5-20250929")
    max_turns = body.get("max_turns", 100)

    if not configs or len(configs) < 2:
        return JSONResponse(
            {"ok": False, "reason": "Provide at least 2 config variants."},
            status_code=400,
        )

    proj = _context._get_project_or_404(experiment_id)

    proj_path = Path(proj.get("path", ""))
    if not proj_path.is_dir():
        return JSONResponse(
            {"ok": False, "reason": "project_path_missing"},
            status_code=400,
        )

    loop = asyncio.get_event_loop()
    try:
        sessions = await loop.run_in_executor(
            _context._executor,
            lambda: launch_sweep(
                proj_path, proj, configs,
                model=model, max_turns=max_turns,
            ),
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "reason": str(e)}, status_code=500,
        )

    acquire_lock()
    try:
        _state.reload()
        for sd in sessions:
            _state.add_session(experiment_id, sd["session_id"], sd)
        _state.save()
    finally:
        release_lock()

    return JSONResponse({
        "ok": True,
        "variants": len(sessions),
        "sessions": [s["tmux_session"] for s in sessions],
    })


@router.post("/experiments/{experiment_id}/campaign/start")
async def start_campaign(experiment_id: str, request: Request):
    """Start an autonomous campaign loop for a project."""
    _state = _context._state

    body = await request.json()

    proj = _context._get_project_or_404(experiment_id)

    # Validate goals exist
    if not proj.get("goals"):
        return JSONResponse(
            {"ok": False, "reason": "Set goals first with update_goals."},
            status_code=400,
        )

    # Don't start if already running
    existing = proj.get("campaign", {})
    if existing.get("status") == "running":
        return JSONResponse(
            {"ok": False, "reason": "Campaign already running."},
            status_code=409,
        )

    objective = body.get("objective", "")
    max_sessions = body.get("max_sessions", 10)
    max_hours = body.get("max_hours", 8)
    model = body.get("model", "claude-sonnet-4-5-20250929")
    max_turns = body.get("max_turns", 100)

    campaign = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "objective": objective,
        "budget": {"max_sessions": max_sessions, "max_hours": max_hours},
        "model": model,
        "max_turns": max_turns,
        "sessions_launched": 0,
        "current_session_id": None,
        "completed_at": None,
        "stop_reason": None,
    }

    _state.update_experiment(
        experiment_id, campaign=campaign, auto_continue=True,
    )
    _state.save()

    # Start background campaign loop
    task = asyncio.create_task(_campaign_loop(experiment_id))
    _campaign_tasks[experiment_id] = task

    return JSONResponse({"ok": True, "campaign": campaign})


@router.post("/experiments/{experiment_id}/campaign/pause")
async def pause_campaign(experiment_id: str):
    """Pause a running campaign (finishes current session, stops launching)."""
    _state = _context._state

    proj = _context._get_project_or_404(experiment_id)
    campaign = proj.get("campaign", {})
    if campaign.get("status") != "running":
        return JSONResponse(
            {"ok": False, "reason": "Campaign not running."},
            status_code=400,
        )
    campaign["status"] = "paused"
    campaign["stop_reason"] = "user_paused"
    _state.update_experiment(experiment_id, campaign=campaign)
    _state.save()
    return JSONResponse({"ok": True})


@router.post("/experiments/{experiment_id}/campaign/resume")
async def resume_campaign(experiment_id: str):
    """Resume a paused campaign."""
    _state = _context._state

    proj = _context._get_project_or_404(experiment_id)
    campaign = proj.get("campaign", {})
    if campaign.get("status") != "paused":
        return JSONResponse(
            {"ok": False, "reason": "Campaign not paused."},
            status_code=400,
        )
    campaign["status"] = "running"
    campaign["stop_reason"] = None
    _state.update_experiment(experiment_id, campaign=campaign)
    _state.save()
    # Restart the background loop
    task = asyncio.create_task(_campaign_loop(experiment_id))
    _campaign_tasks[experiment_id] = task
    return JSONResponse({"ok": True})


@router.post("/experiments/{experiment_id}/campaign/stop")
async def stop_campaign(experiment_id: str):
    """Stop a campaign permanently."""
    _state = _context._state

    proj = _context._get_project_or_404(experiment_id)
    campaign = proj.get("campaign", {})
    campaign["status"] = "paused"
    campaign["stop_reason"] = "user_stopped"
    campaign["completed_at"] = datetime.now(timezone.utc).isoformat()
    _state.update_experiment(experiment_id, campaign=campaign)
    _state.save()
    # Cancel the background task
    task = _campaign_tasks.pop(experiment_id, None)
    if task:
        task.cancel()
    return JSONResponse({"ok": True})


@router.patch("/experiments/{experiment_id:path}")
async def patch_experiment(experiment_id: str, request: Request):
    """Update experiment fields (key_metric_name, description, etc.)."""
    _state = _context._state

    proj = _context._get_project_or_404(experiment_id)
    actual_id = proj.get("id", experiment_id)
    body = await request.json()
    updates = {}
    if "name" in body:
        updates["name"] = body["name"]
    if "key_metric_name" in body:
        updates["key_metric_name"] = body["key_metric_name"]
    if "description" in body:
        updates["description"] = body["description"]
    if "goals" in body:
        updates["goals"] = body["goals"]
    if updates:
        _state.update_experiment(actual_id, **updates)
        _state.save()
    return JSONResponse({"ok": True, "updated": list(updates.keys())})


@router.delete("/experiments/{experiment_id:path}")
async def delete_experiment(experiment_id: str):
    """Delete experiment from tracking. Does NOT delete files or remote repo."""
    _state = _context._state

    from distillate.launcher import _tmux_session_exists

    proj = _context._get_project_or_404(experiment_id)

    # Use the actual state key, not the URL param
    actual_id = proj.get("id", experiment_id)

    # Refuse if sessions are running
    for sess in proj.get("sessions", {}).values():
        if sess.get("status") == "running":
            tmux_name = sess.get("tmux_session", "")
            if tmux_name and _tmux_session_exists(tmux_name):
                return JSONResponse(
                    {"ok": False, "reason": f"Session '{tmux_name}' is still running. Stop it first."},
                    status_code=409,
                )

    name = proj.get("name", actual_id)
    run_count = len(proj.get("runs", {}))
    _state.remove_experiment(actual_id)
    _state.save()
    return JSONResponse({"ok": True, "message": f"Deleted '{name}' ({run_count} runs). Files and remote repo untouched."})


@router.post("/experiments/{experiment_id}/compare-agents")
async def compare_agents(experiment_id: str, request: Request):
    """Launch the same experiment with multiple agents in parallel.

    Body: {"agents": ["gemini", "codex"], "stagger_minutes": 0, "compute": {}}

    Creates sister projects for each agent, copies PROMPT.md,
    and launches sessions. The parent project is also (re)launched
    if it has no active session.
    """
    _state = _context._state

    from distillate.launcher import (
        create_sister_project,
        launch_experiment,
    )

    body = await request.json()
    agents = body.get("agents", [])
    stagger = body.get("stagger_minutes", 0)

    if not agents:
        return JSONResponse(
            {"ok": False, "reason": "No agents specified"}, status_code=400,
        )

    proj = _context._get_project_or_404(experiment_id)
    proj_path = Path(proj.get("path", ""))
    if not proj_path.exists():
        return JSONResponse(
            {"ok": False, "reason": "Project path not found"}, status_code=404,
        )

    loop = asyncio.get_event_loop()
    launched = []

    # Launch parent if not already running
    parent_active = any(
        s.get("status") == "running"
        for s in proj.get("sessions", {}).values()
    )
    if not parent_active:
        try:
            result = await loop.run_in_executor(
                _context._executor,
                lambda: launch_experiment(proj_path, project=proj),
            )
            _state.add_session(experiment_id, result["session_id"], result)
            _state.save()
            launched.append({
                "experiment_id": experiment_id,
                "agent_type": proj.get("agent_type", "claude"),
                "tmux_session": result["tmux_session"],
            })
        except Exception as e:
            log.warning("Failed to launch parent: %s", e)

    # Create and launch sister projects
    for agent_type in agents:
        try:
            _state.reload()
            sister_proj = await loop.run_in_executor(
                _context._executor,
                lambda at=agent_type: create_sister_project(
                    proj_path, proj, at, _state,
                ),
            )
            if not sister_proj:
                continue

            sister_path = Path(sister_proj.get("path", ""))
            sister_id = sister_proj.get("id", "")

            # Stagger: wait before launching next agent
            if stagger and launched:
                await asyncio.sleep(stagger * 60)

            result = await loop.run_in_executor(
                _context._executor,
                lambda sp=sister_path, sproj=sister_proj, at=agent_type: launch_experiment(
                    sp, project=sproj, agent_type=at,
                ),
            )
            _state.reload()
            _state.add_session(sister_id, result["session_id"], result)
            _state.save()
            launched.append({
                "experiment_id": sister_id,
                "agent_type": agent_type,
                "tmux_session": result["tmux_session"],
            })
        except Exception as e:
            log.warning("Failed to launch sister %s: %s", agent_type, e)

    return JSONResponse({
        "ok": True,
        "launched": launched,
        "count": len(launched),
    })


@router.get("/experiments/{experiment_id}/sisters")
async def get_sisters(experiment_id: str):
    """Return the parent + all sister projects with their runs.

    Used by the desktop to render a combined frontier chart.
    """
    _state = _context._state

    from distillate.experiment_tools import _run_summary_full

    _context._cached_reload()
    proj = _context._get_project_or_404(experiment_id)

    # Find the family: this project + all projects sharing the same parent
    proj_actual_id = proj.get("id", experiment_id)
    parent_id = proj.get("sister_of") or proj_actual_id

    family = []
    for pid, p in _state.experiments.items():
        is_parent = (pid == parent_id)
        is_sister = (p.get("sister_of") == parent_id)
        is_self = (pid == proj_actual_id)
        if is_parent or is_sister or is_self:
            runs = p.get("runs", {})
            sorted_runs = _sort_runs_chronologically(p, runs)
            family.append({
                "id": pid,
                "name": p.get("name", ""),
                "agent_type": p.get("agent_type", "claude"),
                "is_parent": is_parent,
                "active_sessions": sum(
                    1 for s in p.get("sessions", {}).values()
                    if s.get("status") == "running"
                ),
                "run_count": len(runs),
                "runs": [
                    _run_summary_full(r, i + 1)
                    for i, r in enumerate(sorted_runs)
                ],
            })

    return JSONResponse({"ok": True, "parent_id": parent_id, "family": family})


@router.post("/experiments/{experiment_id}/steer")
async def steer_experiment(experiment_id: str, request: Request):
    """Write steering instructions for the next session."""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse(
            {"ok": False, "reason": "No steering text provided."},
            status_code=400,
        )

    proj = _context._get_project_or_404(experiment_id)

    from distillate.launcher import write_steering

    proj_path = Path(proj.get("path", ""))
    write_steering(proj_path, text)

    return JSONResponse({"ok": True})


# ------------------------------------------------------------------
# M4 stubs: Multi-Agent Research Lab
# ------------------------------------------------------------------

@router.get("/experiments/compare")
async def compare_experiments(ids: str = ""):
    """Compare metrics across multiple experiments.

    Query param: ?ids=proj1,proj2,proj3
    Returns a grid of metrics for side-by-side comparison.
    """
    _state = _context._state

    if not ids:
        return JSONResponse(
            {"ok": False, "reason": "No project IDs provided."},
            status_code=400,
        )

    project_ids = [i.strip() for i in ids.split(",") if i.strip()]
    _state.reload()

    comparison = []
    all_metrics: set[str] = set()

    for pid in project_ids:
        proj = _state.find_experiment(pid)
        if not proj:
            continue

        # Find best results across non-crash runs
        best: dict[str, float] = {}
        for run in proj.get("runs", {}).values():
            if run.get("decision") == "crash" or run.get("status") == "failed":
                continue
            for k, v in run.get("results", {}).items():
                if isinstance(v, (int, float)):
                    if k not in best or v > best[k]:
                        best[k] = v
                    all_metrics.add(k)

        comparison.append({
            "id": proj.get("id", pid),
            "name": proj.get("name", pid),
            "run_count": len(proj.get("runs", {})),
            "best_metrics": best,
            "goals": proj.get("goals", []),
            "campaign": proj.get("campaign"),
        })

    return JSONResponse({
        "ok": True,
        "experiments": comparison,
        "metrics": sorted(all_metrics),
    })


@router.post("/experiments/{experiment_id}/save-template")
async def save_template(experiment_id: str, request: Request):
    """Save a successful experiment as a reusable template."""
    from distillate.launcher import import_template

    proj = _context._get_project_or_404(experiment_id)

    proj_path = Path(proj.get("path", ""))
    if not proj_path.is_dir():
        return JSONResponse(
            {"ok": False, "reason": "path_not_found"}, status_code=404,
        )

    body = await request.json()
    template_name = body.get("name", proj.get("name", experiment_id))

    loop = asyncio.get_event_loop()
    try:
        result_name = await loop.run_in_executor(
            _context._executor,
            lambda: import_template(proj_path, template_name),
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "reason": str(e)}, status_code=500,
        )

    return JSONResponse({
        "ok": True,
        "template_name": result_name,
        "message": f"Saved as template '{result_name}'.",
    })


@router.post("/experiments/campaign/parallel")
async def start_parallel_campaigns(request: Request):
    """Launch campaigns across multiple projects in parallel.

    Body: {"project_ids": ["proj1", "proj2"], "max_sessions": 5, "model": "..."}
    """
    _state = _context._state

    body = await request.json()
    project_ids = body.get("project_ids", [])
    max_sessions = body.get("max_sessions", 5)
    model = body.get("model", "claude-sonnet-4-5-20250929")
    max_turns = body.get("max_turns", 100)

    if len(project_ids) < 2:
        return JSONResponse(
            {"ok": False, "reason": "Provide at least 2 project IDs."},
            status_code=400,
        )

    _state.reload()
    launched = []
    errors = []

    for pid in project_ids:
        proj = _state.find_experiment(pid)
        if not proj:
            errors.append({"id": pid, "reason": "not_found"})
            continue
        if not proj.get("goals"):
            errors.append({"id": pid, "reason": "no_goals"})
            continue
        if proj.get("campaign", {}).get("status") == "running":
            errors.append({"id": pid, "reason": "already_running"})
            continue

        campaign = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "objective": "",
            "budget": {"max_sessions": max_sessions},
            "model": model,
            "max_turns": max_turns,
            "sessions_launched": 0,
            "current_session_id": None,
            "completed_at": None,
            "stop_reason": None,
        }
        _state.update_experiment(pid, campaign=campaign, auto_continue=True)
        _state.save()

        task = asyncio.create_task(_campaign_loop(pid))
        _campaign_tasks[pid] = task
        launched.append(pid)

    return JSONResponse({
        "ok": True,
        "launched": launched,
        "errors": errors,
    })


@router.get("/experiments/stream")
async def experiments_stream():
    """SSE endpoint that tails experiment events and runs.jsonl."""
    _state = _context._state

    from starlette.responses import StreamingResponse

    async def _event_generator():
        """Yield SSE events from .distillate/events.jsonl, runs.jsonl, and live_metrics.jsonl."""
        # Track file offsets: events + runs + live metrics per project
        event_offsets: dict[str, int] = {}
        run_offsets: dict[str, int] = {}
        metric_offsets: dict[str, int] = {}
        # Track alert keys already broadcast this stream (project_id:kind:ts)
        _seen_alert_keys: set[str] = set()

        _state.reload()
        projects = _state.experiments

        while True:
            for proj_id, proj in projects.items():
                proj_path = proj.get("path", "")
                if not proj_path:
                    continue
                base = Path(proj_path) / ".distillate"

                # --- Tail events.jsonl ---
                events_file = base / "events.jsonl"
                if events_file.exists():
                    ekey = str(events_file)
                    last_offset = event_offsets.get(ekey, 0)
                    try:
                        file_size = events_file.stat().st_size
                    except OSError:
                        file_size = 0

                    if file_size > last_offset:
                        try:
                            with open(events_file, encoding="utf-8") as f:
                                f.seek(last_offset)
                                for line in f:
                                    line = line.strip()
                                    if not line:
                                        continue
                                    # Check for lifecycle events -> trigger rescan
                                    try:
                                        evt = json.loads(line)
                                    except json.JSONDecodeError:
                                        yield f"data: {line}\n\n"
                                        continue
                                    yield f"data: {line}\n\n"
                                    if evt.get("type") == "run_completed":
                                        # Background rescan keeps state.db in sync after each run.
                                        # Fire-and-forget: don't block the SSE stream.
                                        loop = asyncio.get_event_loop()
                                        loop.run_in_executor(
                                            _context._executor,
                                            _rescan_project, proj_id, proj,
                                        )
                                    elif evt.get("type") == "session_end":
                                        # Auto-rescan in background
                                        loop = asyncio.get_event_loop()
                                        summary = await loop.run_in_executor(
                                            _context._executor,
                                            _rescan_project, proj_id, proj,
                                        )
                                        if summary:
                                            completed_evt = {
                                                "type": "session_completed",
                                                "experiment_id": proj_id,
                                                "new_runs": summary["new_runs"],
                                                "total_runs": summary["total_runs"],
                                                "best_metric": summary["best_metric"],
                                            }
                                            yield f"data: {json.dumps(completed_evt)}\n\n"

                                            # Push updated state to cloud
                                            from distillate.cloud_sync import cloud_sync_available, push_state
                                            if cloud_sync_available():
                                                loop.run_in_executor(
                                                    _context._executor, push_state, _state,
                                                )

                                        # Auto-continue if goals unmet
                                        _state.reload()
                                        fresh_proj = _state.get_experiment(proj_id)
                                        if fresh_proj and fresh_proj.get("auto_continue"):
                                            cont_evt = await _maybe_auto_continue(
                                                proj_id, fresh_proj, loop,
                                            )
                                            if cont_evt:
                                                yield f"data: {json.dumps(cont_evt)}\n\n"
                            event_offsets[ekey] = events_file.stat().st_size
                        except OSError:
                            pass

                # --- Tail runs.jsonl ---
                runs_file = base / "runs.jsonl"
                if runs_file.exists():
                    rkey = str(runs_file)
                    last_offset = run_offsets.get(rkey, 0)
                    try:
                        file_size = runs_file.stat().st_size
                    except OSError:
                        file_size = 0

                    if file_size > last_offset:
                        try:
                            with open(runs_file, encoding="utf-8") as f:
                                f.seek(last_offset)
                                for line in f:
                                    line = line.strip()
                                    if not line:
                                        continue
                                    try:
                                        run_data = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    run_evt = {
                                        "type": "run_update",
                                        "experiment_id": proj_id,
                                        "run": run_data,
                                    }
                                    yield f"data: {json.dumps(run_evt)}\n\n"

                                    # --- Goal checker ---
                                    if run_data.get("status") in ("best", "completed", "keep") or run_data.get("decision") in ("best", "completed"):
                                        _state.reload()
                                        fresh_proj = _state.get_experiment(proj_id)
                                        if fresh_proj and fresh_proj.get("goals"):
                                            from distillate.launcher import should_continue, stop_session
                                            if not should_continue(fresh_proj):
                                                # Find which goal was met
                                                run_results = run_data.get("results", {})
                                                for g in fresh_proj["goals"]:
                                                    gmetric = g.get("metric", "")
                                                    gthresh = g.get("threshold")
                                                    if gmetric and gthresh is not None and gmetric in run_results:
                                                        val = run_results[gmetric]
                                                        if isinstance(val, (int, float)):
                                                            met = False
                                                            if g.get("direction") == "maximize" and val >= gthresh:
                                                                met = True
                                                            elif g.get("direction") == "minimize" and val <= gthresh:
                                                                met = True
                                                            if met:
                                                                goal_evt = {
                                                                    "type": "goal_reached",
                                                                    "experiment_id": proj_id,
                                                                    "metric": gmetric,
                                                                    "value": val,
                                                                    "target": gthresh,
                                                                }
                                                                yield f"data: {json.dumps(goal_evt)}\n\n"
                                                                break

                                                # Auto-stop running sessions
                                                loop = asyncio.get_event_loop()
                                                sessions = fresh_proj.get("sessions", {})
                                                stopped_any = False
                                                for sess in sessions.values():
                                                    if sess.get("status") == "running":
                                                        tmux = sess.get("tmux_session", "")
                                                        if tmux:
                                                            await loop.run_in_executor(
                                                                _context._executor, stop_session, tmux, None,
                                                            )
                                                            sess["status"] = "completed"
                                                            stopped_any = True
                                                if stopped_any:
                                                    _state.save()

                            run_offsets[rkey] = runs_file.stat().st_size
                        except OSError:
                            pass

                # --- Tail live_metrics.jsonl ---
                metrics_file = base / "live_metrics.jsonl"
                if metrics_file.exists():
                    mkey = str(metrics_file)
                    last_offset = metric_offsets.get(mkey, 0)
                    try:
                        file_size = metrics_file.stat().st_size
                    except OSError:
                        file_size = 0

                    if file_size > last_offset:
                        try:
                            with open(metrics_file, encoding="utf-8") as f:
                                f.seek(last_offset)
                                for line in f:
                                    line = line.strip()
                                    if not line:
                                        continue
                                    try:
                                        metric_data = json.loads(line)
                                    except json.JSONDecodeError:
                                        continue
                                    metric_evt = {
                                        "type": "metric_update",
                                        "experiment_id": proj_id,
                                        **metric_data,
                                    }
                                    yield f"data: {json.dumps(metric_evt)}\n\n"
                            metric_offsets[mkey] = metrics_file.stat().st_size
                        except OSError:
                            pass

            # --- Session watchdog: restart died sessions, enforce budget ---
            _state.reload()
            for proj_id_w, proj_w in _state.experiments.items():
                sessions_w = proj_w.get("sessions", {})
                budget = proj_w.get("session_budget_seconds")
                for sess_id_w, sess_w in list(sessions_w.items()):
                    if sess_w.get("status") != "running":
                        continue
                    tmux_w = sess_w.get("tmux_session", "")
                    if not tmux_w:
                        continue

                    from distillate.launcher import session_status as _sess_status
                    actual = _sess_status(tmux_w, sess_w.get("host"))

                    sess_started = sess_w.get("started_at", "")
                    elapsed_w = 0.0
                    if sess_started:
                        try:
                            from datetime import datetime as _dt, timezone as _tz
                            st = _dt.fromisoformat(sess_started.replace("Z", "+00:00"))
                            elapsed_w = (_dt.now(_tz.utc) - st).total_seconds()
                        except (ValueError, TypeError):
                            pass

                    if actual == "completed":
                        restarts = sess_w.get("restarts", 0)
                        max_restarts = 5
                        budget_remaining = (budget - elapsed_w) if budget else None

                        if budget is not None and budget_remaining is not None and budget_remaining <= 0:
                            # Budget expired -- mark completed, rescan
                            sess_w["status"] = "completed"
                            _state.save()
                            summary = _rescan_project(proj_id_w, proj_w)
                            if summary:
                                yield f"data: {json.dumps({'type': 'session_budget_expired', 'experiment_id': proj_id_w, **summary})}\n\n"
                                from distillate.cloud_sync import cloud_sync_available, push_state
                                if cloud_sync_available():
                                    loop = asyncio.get_event_loop()
                                    loop.run_in_executor(_context._executor, push_state, _state)
                        elif restarts < max_restarts:
                            # Always restart -- agent keeps working until budget expires
                            from distillate.launcher import launch_continuation
                            proj_path_w = Path(proj_w.get("path", ""))
                            if proj_path_w.is_dir():
                                try:
                                    loop = asyncio.get_event_loop()
                                    # Rescan first to capture any final runs
                                    await loop.run_in_executor(
                                        _context._executor, _rescan_project, proj_id_w, proj_w,
                                    )
                                    _state.reload()
                                    fresh_w = _state.get_experiment(proj_id_w) or proj_w
                                    new_sess = await loop.run_in_executor(
                                        _context._executor,
                                        lambda: launch_continuation(
                                            proj_path_w, fresh_w,
                                        ),
                                    )
                                    # Update old session, add new one
                                    sess_w["status"] = "completed"
                                    new_sess["restarts"] = restarts + 1
                                    new_sess["budget_seconds"] = budget
                                    restart_hist = sess_w.get("restart_history", [])
                                    restart_hist.append(sess_w.get("started_at", ""))
                                    new_sess["restart_history"] = restart_hist
                                    _state.add_session(proj_id_w, new_sess["session_id"], new_sess)
                                    _state.save()
                                    yield f"data: {json.dumps({'type': 'session_restarted', 'experiment_id': proj_id_w, 'tmux_session': new_sess['tmux_session'], 'restart_count': restarts + 1})}\n\n"
                                except Exception:
                                    log.exception("Watchdog restart failed for %s", proj_id_w)
                                    sess_w["status"] = "completed"
                                    _state.save()
                        else:
                            # Max restarts reached
                            sess_w["status"] = "completed"
                            _state.save()
                            yield f"data: {json.dumps({'type': 'session_max_restarts', 'experiment_id': proj_id_w, 'restarts': restarts})}\n\n"

                    elif budget is not None and elapsed_w >= budget:
                        # Budget expired while session still running -- stop it
                        from distillate.launcher import stop_session as _stop_sess
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            _context._executor, _stop_sess, tmux_w, sess_w.get("host"),
                        )
                        sess_w["status"] = "completed"
                        _state.save()
                        summary = _rescan_project(proj_id_w, proj_w)
                        yield f"data: {json.dumps({'type': 'session_budget_expired', 'experiment_id': proj_id_w})}\n\n"

            # --- HF Jobs budget watcher ---
            # Runs every SSE tick (~2s). Cheap: only reads a small JSON file.
            for proj_id_hf, proj_hf in list(_state.experiments.items()):
                compute_hf = (proj_hf.get("compute") or {})
                if compute_hf.get("provider") != "hfjobs":
                    continue
                proj_path_hf = Path(proj_hf.get("path", ""))
                if not proj_path_hf.is_dir():
                    continue
                try:
                    from distillate.budget import read_compute_budget, read_compute_spend
                    budget_cfg = read_compute_budget(cwd=proj_path_hf)
                    if not budget_cfg:
                        continue
                    budget_usd = budget_cfg.get("budget_usd", 0)
                    if not budget_usd:
                        continue
                    spend_data = read_compute_spend(cwd=proj_path_hf)
                    spent = spend_data.get("total_usd", 0.0)
                    if spent >= budget_usd:
                        # Cancel all running HF Jobs for this experiment
                        registry_path = proj_path_hf / ".distillate" / "hf_jobs.json"
                        if registry_path.exists():
                            import json as _json_hf
                            from distillate import config as _cfg_hf
                            from distillate.compute_hfjobs import HFJobsProvider
                            try:
                                jobs_data = _json_hf.loads(registry_path.read_text())
                                provider_hf = HFJobsProvider(namespace=_cfg_hf.HF_NAMESPACE)
                                for jid, jinfo in jobs_data.items():
                                    try:
                                        jstatus = provider_hf.get_job(jid)
                                        if jstatus and jstatus.status in ("pending", "starting", "running"):
                                            provider_hf.cancel_job(jid)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        # Write persistent alert so it survives reconnects
                        try:
                            from distillate.budget import write_experiment_alert
                            write_experiment_alert(
                                cwd=proj_path_hf,
                                kind="compute_budget_exceeded",
                                message=(
                                    f"HF Jobs $ budget exhausted "
                                    f"(${round(spent, 2):.2f} of ${budget_usd:.2f} used). "
                                    "All running jobs have been cancelled."
                                ),
                            )
                        except Exception:
                            pass
                        yield f"data: {json.dumps({'type': 'hfjobs_budget_exceeded', 'experiment_id': proj_id_hf, 'spent_usd': round(spent, 2), 'budget_usd': budget_usd})}\n\n"
                    elif spent >= budget_usd * 0.90:
                        yield f"data: {json.dumps({'type': 'hfjobs_budget_warning', 'experiment_id': proj_id_hf, 'spent_usd': round(spent, 2), 'budget_usd': budget_usd})}\n\n"
                except Exception:
                    pass

            # --- HF Jobs GPU standby timeout watcher ---
            # If a job has been in pending/starting for >10 minutes, fire an alert.
            _GPU_STANDBY_LIMIT_S = 600
            for proj_id_sb, proj_sb in list(_state.experiments.items()):
                compute_sb = (proj_sb.get("compute") or {})
                if compute_sb.get("provider") != "hfjobs":
                    continue
                proj_path_sb = Path(proj_sb.get("path", ""))
                if not proj_path_sb.is_dir():
                    continue
                registry_sb = proj_path_sb / ".distillate" / "hf_jobs.json"
                if not registry_sb.is_file():
                    continue
                try:
                    import json as _json_sb
                    from datetime import datetime as _dt_sb, timezone as _tz_sb
                    from distillate.budget import write_experiment_alert as _wea_sb
                    jobs_sb = _json_sb.loads(registry_sb.read_text(encoding="utf-8"))
                    now_sb = _dt_sb.now(_tz_sb.utc)
                    for jid_sb, jinfo_sb in jobs_sb.items():
                        submitted_raw = jinfo_sb.get("submitted_at", "")
                        if not submitted_raw:
                            continue
                        try:
                            submitted_dt = _dt_sb.fromisoformat(submitted_raw)
                        except ValueError:
                            continue
                        elapsed_sb = (now_sb - submitted_dt).total_seconds()
                        if elapsed_sb < _GPU_STANDBY_LIMIT_S:
                            continue
                        # Only alert on jobs still in a waiting state
                        try:
                            from distillate import config as _cfg_sb
                            from distillate.compute_hfjobs import HFJobsProvider as _HFP_sb
                            _prov_sb = _HFP_sb(namespace=_cfg_sb.HF_NAMESPACE)
                            _jstat_sb = _prov_sb.get_job(jid_sb)
                            if not _jstat_sb or _jstat_sb.status not in ("pending", "starting"):
                                continue
                        except Exception:
                            continue
                        elapsed_min = int(elapsed_sb // 60)
                        _wea_sb(
                            cwd=proj_path_sb,
                            kind="gpu_standby_timeout",
                            message=(
                                f"Job {jid_sb[:8]}… has been waiting for a GPU "
                                f"for {elapsed_min} min. "
                                "HF Jobs may be overloaded — consider cancelling and retrying."
                            ),
                        )
                except Exception:
                    pass

            # --- Alert scanner: broadcast new entries from alerts.json ---
            # Track seen alert keys (project_id:kind:ts) to avoid re-sending
            # the same alert on every SSE tick.  The set is local to this
            # generator so it resets on SSE reconnect — acceptable because the
            # frontend deduplicates by (kind, ts) before showing OS notifications.
            for proj_id_al, proj_al in list(_state.experiments.items()):
                proj_path_al = proj_al.get("path", "")
                if not proj_path_al:
                    continue
                alerts_file = Path(proj_path_al) / ".distillate" / "alerts.json"
                if not alerts_file.is_file():
                    continue
                try:
                    from distillate.budget import read_experiment_alerts
                    active_alerts = read_experiment_alerts(cwd=Path(proj_path_al))
                    for alert in active_alerts:
                        akey = f"{proj_id_al}:{alert.get('kind', '')}:{alert.get('ts', '')}"
                        if akey not in _seen_alert_keys:
                            _seen_alert_keys.add(akey)
                            yield f"data: {json.dumps({'type': 'experiment_alert', 'experiment_id': proj_id_al, 'kind': alert.get('kind', ''), 'message': alert.get('message', ''), 'ts': alert.get('ts', '')})}\n\n"
                except Exception:
                    pass

            await asyncio.sleep(2)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/experiments/list")
async def list_experiments():
    _state = _context._state

    from distillate.experiment_tools import _run_summary_full
    from distillate.experiments import infer_key_metric_name as _infer_key_metric_name
    from distillate.launcher import refresh_session_statuses

    _context._cached_reload()

    # Refresh tmux session statuses so the UI doesn't show stale "running"
    loop = asyncio.get_event_loop()
    changed = await loop.run_in_executor(
        _context._executor, refresh_session_statuses, _state,
    )
    if changed:
        _state.save()

    _context._cached_reload()
    projects = _state.experiments
    result = []
    for proj_id, proj in projects.items():
        runs = proj.get("runs", {})
        # Fix 2: prune state.runs of phantoms not in runs.jsonl. This is
        # cheap (one small file read, ID set comparison) and stops stale
        # cache from inflating displayRuns.length in the UI or masking
        # real run state after a crash+restart.
        proj_path_str = proj.get("path", "")
        if proj_path_str and runs:
            try:
                from distillate.experiments import prune_orphan_state_runs
                runs_jsonl = Path(proj_path_str) / ".distillate" / "runs.jsonl"
                if runs_jsonl.is_file():
                    jsonl_ids: set[str] = set()
                    for line in runs_jsonl.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        rid = rec.get("id")
                        if rid:
                            jsonl_ids.add(rid)
                    pruned = prune_orphan_state_runs(runs, jsonl_ids)
                    if len(pruned) != len(runs):
                        runs = pruned
            except OSError:
                pass  # non-fatal; serve the cached data unchanged
        sessions = proj.get("sessions", {})
        active_sessions = {
            sid: s for sid, s in sessions.items()
            if s.get("status") == "running"
        }
        active = len(active_sessions)
        # Session timing: find earliest active session start
        session_started_at = ""
        for s in active_sessions.values():
            sat = s.get("started_at", "")
            if sat and (not session_started_at or sat < session_started_at):
                session_started_at = sat

        entry = {
            "id": proj_id,
            "name": proj.get("name", ""),
            "path": proj.get("path", ""),
            "status": proj.get("status", ""),
            "description": proj.get("description", ""),
            "tags": proj.get("tags", []),
            "goals": proj.get("goals", []),
            "run_count": len(runs),
            "active_sessions": active,
            "sessions": {
                sid: {
                    "tmux_session": s.get("tmux_session", ""),
                    "started_at": s.get("started_at", ""),
                    "budget_seconds": s.get("budget_seconds"),
                    "restarts": s.get("restarts", 0),
                    "agent_status": s.get("agent_status", "unknown"),
                    "attention_needed": s.get("attention_needed", False),
                }
                for sid, s in active_sessions.items()
                if s.get("tmux_session")
            },
            "session_started_at": session_started_at,
            "session_budget_seconds": proj.get("session_budget_seconds"),
            "agent_type": proj.get("agent_type", "claude"),
            "agent_id": proj.get("agent_id", "claude-code"),
            "harness_id": proj.get("harness_id", proj.get("agent_type", "claude-code")),
            "workspace_id": proj.get("workspace_id", ""),
            "workspace_name": (
                _state.workspaces.get(proj.get("workspace_id", ""), {}).get("name", "")
                if proj.get("workspace_id") else ""
            ),
            "sister_of": proj.get("sister_of"),
            "key_metric_name": _infer_key_metric_name(proj),
            "duration_minutes": proj.get("duration_minutes", 5),
            "added_at": proj.get("added_at", ""),
            "last_scanned_at": proj.get("last_scanned_at", ""),
            "runs": [_run_summary_full(r, i + 1) for i, r in enumerate(
                _sort_runs_chronologically(proj, runs),
            )],
        }
        linked_papers = proj.get("linked_papers", [])
        if linked_papers:
            entry["linked_papers"] = linked_papers
        github_url = proj.get("github_url", "")
        if github_url:
            entry["github_url"] = github_url
        campaign = proj.get("campaign")
        if campaign:
            entry["campaign"] = campaign
        # Include research insights from LLM enrichment or RESULTS.md fallback
        proj_path_str = proj.get("path", "")
        if proj_path_str:
            from distillate.experiments import load_enrichment_cache
            proj_p = Path(proj_path_str)
            cache = load_enrichment_cache(proj_p)
            enr = cache.get("enrichment", cache)
            project_insights = enr.get("project", {})
            if project_insights:
                entry["insights"] = project_insights
            elif (proj_p / "RESULTS.md").exists():
                # Fallback: use RESULTS.md as key_breakthrough
                try:
                    results_text = (proj_p / "RESULTS.md").read_text(
                        encoding="utf-8"
                    ).strip()
                    if results_text:
                        entry["insights"] = {
                            "key_breakthrough": results_text[:2000],
                        }
                except Exception:
                    pass

            # Latest learning + current run from runs.jsonl
            runs_jsonl = proj_p / ".distillate" / "runs.jsonl"
            if runs_jsonl.exists():
                try:
                    all_lines = runs_jsonl.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    found_learning = False
                    found_current = False
                    resolved_ids: set[str] = set()
                    for line in reversed(all_lines):
                        if found_learning and found_current:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rr = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        rid = rr.get("id", "")
                        status = rr.get("status", "")
                        # Track completed runs so we skip stale
                        # "running" announcements
                        if status in ("best", "completed", "keep", "discard", "crash"):
                            resolved_ids.add(rid)
                        # Surface what the agent is currently attempting
                        if (not found_current
                                and status == "running"
                                and rid not in resolved_ids
                                and rr.get("description")):
                            entry["current_run"] = rr["description"]
                            entry["current_run_started"] = rr.get(
                                "timestamp", "")
                            # L3.5: propagate deadlines so renderer shows
                            # a correct "wrapping up" / "over budget" state
                            # instead of recomputing from duration_minutes.
                            if rr.get("train_deadline_at"):
                                entry["current_run_train_deadline"] = rr["train_deadline_at"]
                            if rr.get("wrap_deadline_at"):
                                entry["current_run_wrap_deadline"] = rr["wrap_deadline_at"]
                            # Fix 1: propagate the canonical run_number so
                            # the renderer stops recomputing from
                            # displayRuns.length (which drifts when
                            # state.runs gets stale).
                            if rr.get("run_number") is not None:
                                try:
                                    entry["current_run_number"] = int(rr["run_number"])
                                except (TypeError, ValueError):
                                    pass
                            found_current = True
                        if (not found_learning
                                and status in ("best", "completed", "keep")
                                and rr.get("reasoning")):
                            entry["latest_learning"] = rr["reasoning"]
                            found_learning = True

                    # Total experiment time: pair-matching for
                    # running->completed, gap-based for remainder
                    MAX_GAP = 1800  # 30 min = session break
                    run_starts: dict[str, datetime] = {}
                    pair_secs = 0.0
                    unpaired_dts: list[datetime] = []
                    active_run_start = ""

                    for fwd_line in all_lines:
                        fwd_line = fwd_line.strip()
                        if not fwd_line:
                            continue
                        try:
                            rr = json.loads(fwd_line)
                        except json.JSONDecodeError:
                            continue
                        ts = rr.get("timestamp", "")
                        st = rr.get("status", "")
                        rid = rr.get("id", "")
                        if not ts:
                            continue
                        try:
                            dt = datetime.fromisoformat(
                                ts.replace("Z", "+00:00"))
                            if dt.tzinfo is not None:
                                dt = dt.replace(tzinfo=None)
                        except (ValueError, TypeError):
                            continue

                        if st == "running":
                            active_run_start = ts
                            run_starts[rid] = dt
                        elif st in ("best", "completed", "keep", "discard", "crash"):
                            active_run_start = ""
                            if rid in run_starts:
                                pair_secs += (
                                    dt - run_starts[rid]
                                ).total_seconds()
                                del run_starts[rid]
                            else:
                                unpaired_dts.append(dt)
                        else:
                            unpaired_dts.append(dt)

                    # Gap-based for unpaired entries
                    gap_secs = 0.0
                    prev_dt = None
                    for udt in sorted(unpaired_dts):
                        if prev_dt is not None:
                            gap = (udt - prev_dt).total_seconds()
                            if 0 < gap <= MAX_GAP:
                                gap_secs += gap
                        prev_dt = udt

                    entry["experiment_total_secs"] = (
                        pair_secs + gap_secs
                    )
                    if active_run_start:
                        # Only send if the announcement is recent
                        # (stale = agent logged "running" but never
                        # completed -- don't show a ticking timer)
                        try:
                            ar_dt = datetime.fromisoformat(
                                active_run_start.replace("Z", "+00:00"))
                            if ar_dt.tzinfo is not None:
                                ar_dt = ar_dt.replace(tzinfo=None)
                            budget = (proj.get("duration_minutes") or 5) * 60
                            age = (datetime.utcnow() - ar_dt).total_seconds()
                            if age < budget * 3:
                                entry["active_run_start"] = active_run_start
                        except (ValueError, TypeError):
                            pass
                except OSError:
                    pass

            # Only report current_run/active_run_start for active sessions
            if active == 0:
                entry.pop("current_run", None)
                entry.pop("current_run_started", None)
                entry.pop("current_run_train_deadline", None)
                entry.pop("current_run_wrap_deadline", None)
                entry.pop("current_run_number", None)
                entry["active_run_start"] = ""
            elif active > 0 and not entry.get("current_run"):
                # Active session but no unresolved running entry
                sess = next(iter(active_sessions.values()), {})
                entry["current_run"] = "Session active"
                entry["current_run_started"] = sess.get("started_at", "")

            # Experiment summary from PROMPT.md first meaningful line
            prompt_md = proj_p / "PROMPT.md"
            if prompt_md.exists():
                try:
                    for pline in prompt_md.read_text(
                        encoding="utf-8"
                    ).splitlines():
                        pline = pline.strip()
                        if (pline and not pline.startswith("#")
                                and not pline.startswith("```")):
                            entry["experiment_summary"] = pline[:500]
                            break
                except OSError:
                    pass

        # Include compute config and spend for GPU experiments
        compute_cfg = proj.get("compute")
        if compute_cfg:
            entry["compute"] = compute_cfg
        if proj_path_str:
            spend_path = Path(proj_path_str) / ".distillate" / "compute_spend.json"
            if spend_path.is_file():
                try:
                    spend_data = json.loads(spend_path.read_text(encoding="utf-8"))
                    entry["compute_spend_usd"] = spend_data.get("total_usd", 0)
                    entry["compute_jobs_count"] = len(spend_data.get("jobs", []))
                except (OSError, json.JSONDecodeError):
                    pass

        # Include active (non-dismissed) alerts
        if proj_path_str:
            try:
                from distillate.budget import read_experiment_alerts
                active_alerts = read_experiment_alerts(cwd=Path(proj_path_str))
                if active_alerts:
                    entry["alerts"] = active_alerts
            except Exception:
                pass

        result.append(entry)
    return JSONResponse({"ok": True, "experiments": result})


@router.post("/experiments/{experiment_id}/dismiss-alert")
async def dismiss_experiment_alert(experiment_id: str, body: dict = None):
    """Mark one or all alerts dismissed for an experiment.

    Body: ``{"kind": "wrong_platform"}`` to dismiss a specific kind,
    or ``{}`` to dismiss all active alerts.
    """
    proj = _context._get_project_or_404(experiment_id)
    proj_path = Path(proj.get("path", ""))
    kind = (body or {}).get("kind")
    try:
        from distillate.budget import dismiss_experiment_alerts
        dismiss_experiment_alerts(cwd=proj_path, kind=kind or None)
    except Exception as e:
        log.warning("Failed to dismiss alert for %s: %s", experiment_id, e)
    return JSONResponse({"ok": True})


@router.get("/experiments/{experiment_id}/notebook")
async def experiment_notebook(experiment_id: str):
    from starlette.responses import HTMLResponse

    from distillate.experiments import (
        generate_html_notebook,
        load_enrichment_cache,
    )

    proj = _context._get_project_or_404(experiment_id)

    proj_path = Path(proj.get("path", ""))
    enrichment = load_enrichment_cache(proj_path) if proj_path.exists() else {}

    html = generate_html_notebook(proj, enrichment=enrichment)
    return HTMLResponse(html)


@router.get("/experiments/{experiment_id}/chart/export")
async def export_chart(experiment_id: str, metric: str = "", format: str = "png",
                       log_scale: str = ""):
    """Generate a Karpathy-style clean chart PNG for sharing."""
    from distillate.experiments import (
        generate_export_chart,
        infer_key_metric_name as _infer_key_metric_name,
    )

    proj = _context._get_project_or_404(experiment_id)

    runs = list(proj.get("runs", {}).values())
    if not metric:
        metric = _infer_key_metric_name(proj)
    if not metric:
        return JSONResponse({"ok": False, "reason": "no_metric"}, status_code=400)

    try:
        use_log = log_scale in ("1", "true", "yes")
        # Get experiment summary for chart subtitle
        subtitle = ""
        proj_path_str = proj.get("path", "")
        if proj_path_str:
            prompt_md = Path(proj_path_str) / "PROMPT.md"
            if prompt_md.exists():
                try:
                    for line in prompt_md.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and not line.startswith("```"):
                            # Strip markdown bold
                            subtitle = re.sub(r'\*\*([^*]+)\*\*', r'\1', line)
                            if len(subtitle) > 80:
                                subtitle = subtitle[:78] + "\u2026"
                            break
                except OSError:
                    pass
        png_bytes = generate_export_chart(runs, metric, proj.get("name", experiment_id),
                                          log_scale=use_log, subtitle=subtitle)
        from starlette.responses import Response
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)
