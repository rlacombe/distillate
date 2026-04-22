"""HuggingFace compute job tools."""

import logging

log = logging.getLogger(__name__)

SCHEMAS = [
    {
        "name": "submit_hf_job",
        "description": (
            "Submit a training script as a HuggingFace Job on cloud GPUs. "
            "Use this instead of running 'python3 train.py' locally when "
            "the experiment is configured for HF compute (DISTILLATE_COMPUTE=hfjobs). "
            "The script is automatically uploaded to HF Hub and mounted in the container. "
            "Declare Python dependencies in a comment at the top of the script: "
            "# requirements: torch transformers datasets. "
            "Returns a job_id to track with check_hf_job."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project id, name substring, or index number"},
                "script": {"type": "string", "description": "Path to the Python training script (relative to project dir)"},
                "gpu_flavor": {"type": "string", "description": "GPU flavor: A100, H200, L40S, T4, L4, etc."},
                "timeout_minutes": {"type": "integer", "description": "Max runtime in minutes (default: matches run budget)"},
                "volumes": {"type": "array", "items": {"type": "string"}, "description": "Extra HF volume mounts, e.g. ['hf://datasets/org/data:/data']"},
                "env": {"type": "object", "description": "Extra environment variables for the job"},
                "extra_files": {"type": "array", "items": {"type": "string"}, "description": "Additional local files to upload alongside the script (e.g. utils.py, requirements.txt)"},
            },
            "required": ["project", "script"],
        },
    },
    {
        "name": "check_hf_job",
        "description": (
            "Check the status of a HuggingFace Job. Returns status, logs, and cost info. "
            "Poll every 60 seconds until status is 'completed' or 'failed'. "
            "When completed, parse metrics from logs (lines matching 'METRIC key=value')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID returned by submit_hf_job"},
                "project": {"type": "string", "description": "Project id (used to record spend against the experiment budget)"},
                "include_logs": {"type": "boolean", "description": "Include job logs in the response", "default": True},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "tail_hf_job_logs",
        "description": (
            "Stream live logs from a running HuggingFace Job directly to the terminal. "
            "Polls every few seconds and prints new lines as they arrive — call this right "
            "after submit_hf_job to watch training loss and metrics in real time. "
            "Stops automatically when the job completes, fails, or the duration expires."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID returned by submit_hf_job"},
                "duration_seconds": {"type": "integer", "description": "How long to tail (default: 300s). The tool returns early if the job finishes.", "default": 300},
                "poll_interval": {"type": "integer", "description": "Seconds between log polls (default: 5)", "default": 5},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "cancel_hf_job",
        "description": "Cancel a running HuggingFace Job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The job ID to cancel"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "list_hf_jobs",
        "description": "List recent HuggingFace Jobs for this account.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max number of jobs to return (default: 10)", "default": 10},
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Implementation functions
# ---------------------------------------------------------------------------

def _parse_requirements_comment(script_path: str) -> list[str]:
    """Extract packages from a '# requirements: torch transformers' header line."""
    from pathlib import Path as _Path
    try:
        for line in _Path(script_path).read_text(encoding="utf-8").splitlines()[:20]:
            line = line.strip()
            if line.startswith("# requirements:"):
                pkgs = line[len("# requirements:"):].strip()
                return [p.strip() for p in pkgs.split() if p.strip()]
    except OSError:
        pass
    return []


def _push_script_to_github(proj_path, script_local, extras: list[str]) -> None:
    """Stage, commit, and push the training script (+ extras) to the experiment's GitHub repo."""
    import subprocess as _sp
    from pathlib import Path as _Path

    files = [str(script_local)] + extras
    _sp.run(["git", "add", "--"] + files, cwd=str(proj_path), check=True, capture_output=True)

    # Only commit if there is something staged
    diff = _sp.run(
        ["git", "diff", "--cached", "--quiet"], cwd=str(proj_path), capture_output=True
    )
    if diff.returncode != 0:
        _sp.run(
            ["git", "commit", "-m", f"distillate: update {_Path(str(script_local)).name} for HF job"],
            cwd=str(proj_path), check=True, capture_output=True,
        )

    _sp.run(["git", "push"], cwd=str(proj_path), check=True, capture_output=True)


def submit_hf_job_tool(
    *, state, project: str, script: str,
    gpu_flavor: str = "", timeout_minutes: int = 0,
    volumes: list[str] | None = None,
    env: dict[str, str] | None = None,
    extra_files: list[str] | None = None,
) -> dict:
    """Submit a training script as a HuggingFace Job.

    Uploads the script (and any extra_files) to a private HF Hub dataset
    repo so it's accessible inside the job container, then submits the job
    with the script mounted at /workspace/<script>.
    """
    from pathlib import Path as _Path

    from distillate import config
    from distillate.compute_hfjobs import HFJobsProvider, bucket_volume, ensure_bucket

    from ._helpers import _resolve_project

    proj, error = _resolve_project(state, project)
    if error:
        return error

    proj_path = _Path(proj.get("path", ""))
    script_local = proj_path / script
    if not script_local.exists():
        return {
            "success": False,
            "error": (
                f"Script not found: {script_local}. "
                "Write the training script first, then call submit_hf_job."
            ),
        }

    # Resolve GPU flavor from project config or default
    compute = proj.get("compute", {}) or {}
    flavor = gpu_flavor or compute.get("gpu_type", "") or config.HF_DEFAULT_GPU_FLAVOR

    # Resolve timeout from run budget (train_budget + 2 min wrap)
    if not timeout_minutes:
        timeout_minutes = (proj.get("duration_minutes") or 5) + 2

    # Budget gate — refuse if cumulative spend already at or over cap
    from distillate.budget import read_compute_budget, read_compute_spend
    budget_cfg = read_compute_budget(cwd=proj_path)
    if budget_cfg:
        budget_usd = budget_cfg.get("budget_usd", 0)
        if budget_usd > 0:
            spend = read_compute_spend(cwd=proj_path)
            if spend.get("total_usd", 0) >= budget_usd:
                return {
                    "success": False,
                    "error": (
                        f"GPU budget exhausted: ${spend['total_usd']:.2f} of "
                        f"${budget_usd:.2f} spent. Increase the budget in the "
                        "experiment wizard or switch to local compute."
                    ),
                }

    provider = HFJobsProvider(namespace=config.HF_NAMESPACE)

    extras = [str(proj_path / f) for f in (extra_files or []) if (proj_path / f).exists()]

    # Also include requirements.txt if present alongside the script
    req_txt = script_local.parent / "requirements.txt"
    if req_txt.exists() and str(req_txt) not in extras:
        extras.append(str(req_txt))

    # Parse dependencies from script header comment or requirements.txt
    dependencies = _parse_requirements_comment(str(script_local))
    if not dependencies and req_txt.exists():
        try:
            lines = req_txt.read_text(encoding="utf-8").splitlines()
            dependencies = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
        except OSError:
            pass

    # Prefer GitHub (public, no auth needed in the container) over HF Hub upload.
    github_url = proj.get("github_url", "")
    code_repo_id = ""
    script_name = _Path(script).name

    if github_url:
        try:
            _push_script_to_github(proj_path, script_local, extras)
        except Exception as e:
            return {
                "success": False,
                "error": (
                    f"Failed to push script to GitHub ({github_url}): {e}. "
                    "Ensure the experiment directory is a git repo with a remote."
                ),
            }
    else:
        # Fall back to HF Hub upload (private dataset repo)
        proj_slug = proj.get("id", proj.get("name", "experiment")).lower().replace(" ", "-")
        try:
            code_repo_id, script_name = provider.upload_script_for_job(
                str(script_local), proj_slug, extra_files=extras,
            )
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to upload script to HF Hub: {e}",
            }

    # Auto-create output bucket for checkpoints/artifacts
    proj_name = proj.get("name", "experiment").lower().replace(" ", "-")
    bucket_name = config.HF_STORAGE_BUCKET or f"distillate-{proj_name}"
    try:
        ensure_bucket(bucket_name)
    except Exception:
        pass

    all_volumes = list(volumes or [])
    all_volumes.append(bucket_volume(bucket_name, "/output"))

    # Submit the job
    job_info = provider.submit_job(
        script_name,
        gpu_flavor=flavor,
        timeout_minutes=timeout_minutes,
        volumes=all_volumes,
        env=env or {},
        dependencies=dependencies or None,
        github_url=github_url,
        code_repo_id=code_repo_id,
    )

    # Record job in per-experiment registry for spend tracking
    _register_job(proj_path, job_info.id, script=script, flavor=job_info.flavor)

    return {
        "success": True,
        "job_id": job_info.id,
        "status": job_info.status,
        "gpu_flavor": job_info.flavor,
        "cost_per_hour": job_info.cost_per_hour,
        "timeout_minutes": timeout_minutes,
        "bucket": bucket_name,
        "script_source": github_url or code_repo_id,
        "dependencies": dependencies,
        "message": (
            f"Job {job_info.id} submitted on {job_info.flavor} "
            f"(${job_info.cost_per_hour:.2f}/hr, {timeout_minutes}min timeout). "
            f"Script source: {github_url or code_repo_id}. "
            f"Poll with: check_hf_job(job_id='{job_info.id}', project='{project}')"
        ),
    }


def _register_job(proj_path, job_id: str, *, script: str, flavor: str) -> None:
    """Record job_id → project_path mapping in .distillate/hf_jobs.json."""
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timezone as _tz

    registry_path = _Path(proj_path) / ".distillate" / "hf_jobs.json"
    data: dict = {}
    if registry_path.exists():
        try:
            data = _json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError):
            pass
    data[job_id] = {
        "project_path": str(proj_path),
        "script": script,
        "flavor": flavor,
        "submitted_at": _dt.now(_tz.utc).isoformat(),
    }
    try:
        registry_path.write_text(_json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("Could not write hf_jobs.json: %s", e)


def _lookup_job_project(job_id: str) -> str | None:
    """Find project_path for a job_id by scanning active experiment dirs."""
    import json as _json
    from pathlib import Path as _Path
    from distillate import config as _cfg

    experiments_root = _cfg.EXPERIMENTS_ROOT or str(_Path.home() / "experiments")
    root = _Path(experiments_root)
    if not root.is_dir():
        return None

    for exp_dir in root.iterdir():
        registry = exp_dir / ".distillate" / "hf_jobs.json"
        if not registry.exists():
            continue
        try:
            data = _json.loads(registry.read_text(encoding="utf-8"))
            if job_id in data:
                return data[job_id].get("project_path")
        except (OSError, _json.JSONDecodeError):
            pass
    return None


def check_hf_job_tool(
    *, state, job_id: str, project: str = "", include_logs: bool = True,
) -> dict:
    """Check the status of a HuggingFace Job."""
    from pathlib import Path as _Path

    from distillate import config
    from distillate.compute_hfjobs import GPU_COST_PER_HOUR, HFJobsProvider

    from ._helpers import _resolve_project

    provider = HFJobsProvider(namespace=config.HF_NAMESPACE)
    info = provider.get_job(job_id)
    if not info:
        return {"error": f"Job {job_id} not found"}

    result: dict = {
        "job_id": job_id,
        "status": info.status,
        "flavor": info.flavor,
    }

    # Start live metrics watcher when job is running
    if info.status in ("running", "starting"):
        try:
            from distillate.routes.hf_metrics import start_watching
            start_watching(job_id, info.flavor)
        except Exception:
            pass

    # Record spend when we have duration data
    if info.duration_seconds:
        cost_per_hour = GPU_COST_PER_HOUR.get(info.flavor, info.cost_per_hour)
        cost_usd = (info.duration_seconds / 3600.0) * cost_per_hour
        result["duration_seconds"] = info.duration_seconds
        result["cost_usd"] = round(cost_usd, 4)

        # Resolve project path for spend tracking — prefer explicit param,
        # then fall back to the hf_jobs.json registry.
        proj_path: _Path | None = None
        if project:
            proj, err = _resolve_project(state, project)
            if not err and proj.get("path"):
                proj_path = _Path(proj["path"])
        if proj_path is None:
            found = _lookup_job_project(job_id)
            if found:
                proj_path = _Path(found)

        if proj_path:
            try:
                from distillate.budget import record_job_spend
                record_job_spend(
                    cwd=proj_path,
                    job_id=job_id,
                    flavor=info.flavor,
                    duration_seconds=info.duration_seconds,
                    cost_per_hour=cost_per_hour,
                )
            except Exception:
                pass

    if include_logs:
        logs = provider.get_logs(job_id)
        log_lines = logs.strip().split("\n") if logs else []
        if len(log_lines) > 200:
            result["logs"] = "\n".join(["...(truncated)..."] + log_lines[-200:])
        else:
            result["logs"] = logs or ""

        # Extract METRIC lines for easy parsing by the agent
        if logs:
            metrics: dict[str, float] = {}
            for line in log_lines:
                # Match: METRIC key=value or METRIC key: value
                import re as _re
                for m in _re.finditer(r"(\w+)[=:]\s*([\d.]+)", line):
                    if line.strip().upper().startswith("METRIC"):
                        key, val = m.group(1), m.group(2)
                        try:
                            metrics[key] = float(val)
                        except ValueError:
                            pass
            if metrics:
                result["metrics_from_logs"] = metrics

    return result


def tail_hf_job_logs_tool(
    *, state, job_id: str, duration_seconds: int = 300, poll_interval: int = 5,
) -> dict:
    """Stream job logs live to the terminal by polling and printing new lines."""
    import time as _time

    from distillate import config
    from distillate.compute_hfjobs import HFJobsProvider

    provider = HFJobsProvider(namespace=config.HF_NAMESPACE)

    seen = 0
    deadline = _time.time() + duration_seconds
    final_status = "unknown"

    print(f"\n── HF Job {job_id} · live log tail ──────────────────────────", flush=True)

    while _time.time() < deadline:
        info = provider.get_job(job_id)
        if info:
            final_status = info.status

        logs = provider.get_logs(job_id)
        if logs:
            lines = logs.split("\n")
            new_lines = lines[seen:]
            for line in new_lines:
                print(line, flush=True)
            seen = len(lines)

        if final_status in ("completed", "failed", "cancelled"):
            break

        _time.sleep(poll_interval)

    print(f"── job {final_status} · {seen} lines shown ─────────────────────────\n", flush=True)
    return {"job_id": job_id, "status": final_status, "lines_shown": seen}


def cancel_hf_job_tool(*, state, job_id: str) -> dict:
    """Cancel a running HuggingFace Job."""
    from distillate import config
    from distillate.compute_hfjobs import HFJobsProvider

    provider = HFJobsProvider(namespace=config.HF_NAMESPACE)
    success = provider.cancel_job(job_id)
    return {
        "success": success,
        "job_id": job_id,
        "message": f"Job {job_id} {'cancelled' if success else 'could not be cancelled'}.",
    }


def list_hf_jobs_tool(*, state, limit: int = 10) -> dict:
    """List recent HuggingFace Jobs."""
    from distillate import config
    from distillate.compute_hfjobs import HFJobsProvider

    provider = HFJobsProvider(namespace=config.HF_NAMESPACE)
    jobs = provider.list_jobs(limit=limit)
    return {
        "jobs": [
            {
                "job_id": j.id,
                "status": j.status,
                "flavor": j.flavor,
                "created_at": j.created_at,
            }
            for j in jobs
        ],
        "total": len(jobs),
    }
