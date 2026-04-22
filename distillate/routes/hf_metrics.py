"""Live GPU metrics for running HuggingFace Jobs.

A background daemon thread subscribes to HF's SSE metrics stream per job
(1 sample/sec). The latest sample is cached and served instantly from
GET /hf-jobs/latest-metrics so the status-bar strip can poll cheaply.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)

router = APIRouter()

# job_id → {"metrics": dict, "updated_at": float, "flavor": str}
_cache: dict[str, dict[str, Any]] = {}
# job_id → Thread (presence = watcher is running)
_watchers: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def _watch(job_id: str, flavor: str) -> None:
    """Daemon thread: stream HF metrics and keep cache fresh."""
    from distillate import auth as _auth, config as _cfg
    from huggingface_hub import HfApi

    token = _auth.hf_token_for("jobs")
    api = HfApi(token=token)
    ns = _cfg.HF_NAMESPACE or None

    while True:
        with _lock:
            if job_id not in _watchers:
                break  # caller called stop_watching
        try:
            for metric in api.fetch_job_metrics(job_id=job_id, namespace=ns):
                with _lock:
                    _cache[job_id] = {
                        "metrics": metric,
                        "updated_at": time.time(),
                        "flavor": flavor,
                    }
                    if job_id not in _watchers:
                        return
        except Exception as exc:
            log.debug("HF metrics watcher error for %s: %s", job_id, exc)
            time.sleep(5)


def start_watching(job_id: str, flavor: str = "") -> None:
    """Start a metrics watcher for job_id if not already running."""
    with _lock:
        if job_id in _watchers:
            return
        t = threading.Thread(target=_watch, args=(job_id, flavor), daemon=True, name=f"hf-metrics-{job_id[:8]}")
        _watchers[job_id] = t
    t.start()
    log.debug("Started HF metrics watcher for %s", job_id)


def stop_watching(job_id: str) -> None:
    with _lock:
        _watchers.pop(job_id, None)
        _cache.pop(job_id, None)


@router.get("/hf-jobs/latest-metrics")
async def latest_hf_metrics():
    """Return the most recent GPU metrics across all watched HF jobs.

    Also auto-starts watchers for any jobs currently marked 'running'
    in the experiment registries, so the frontend doesn't need to
    explicitly call start_watching.
    """
    _ensure_watchers_for_active_jobs()

    with _lock:
        entries = list(_cache.values())

    if not entries:
        return JSONResponse({"ok": True, "metrics": None})

    # Aggregate across all running jobs (sum GPU utilization, pick max mem %)
    total_gpu_util = 0.0
    total_gpu_mem_used = 0
    total_gpu_mem_total = 0
    total_cpu_pct = 0.0
    cpu_samples = 0
    gpu_count = 0
    flavor = ""
    stale = True

    now = time.time()
    for entry in entries:
        if now - entry.get("updated_at", 0) > 30:
            continue  # ignore stale entries (job likely done)
        stale = False
        if not flavor:
            flavor = entry.get("flavor", "")
        m = entry.get("metrics", {})
        # Handle both dict and dataclass/object metrics
        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        gpus = _get(m, "gpus") or {}
        if not isinstance(gpus, dict):
            try:
                gpus = dict(gpus)
            except Exception:
                gpus = {}
        for gpu in gpus.values():
            total_gpu_util += _get(gpu, "utilization", 0) or 0
            total_gpu_mem_used += _get(gpu, "memory_used_bytes", 0) or 0
            total_gpu_mem_total += _get(gpu, "memory_total_bytes", 0) or 0
            gpu_count += 1

        cpu = _get(m, "cpu_usage_pct", None)
        if cpu is not None:
            total_cpu_pct += float(cpu)
            cpu_samples += 1

    if stale or gpu_count == 0:
        return JSONResponse({"ok": True, "metrics": None})

    avg_util = total_gpu_util / gpu_count
    mem_pct = (total_gpu_mem_used / total_gpu_mem_total * 100) if total_gpu_mem_total else None
    mem_gb = total_gpu_mem_used / (1024 ** 3) if total_gpu_mem_used else None
    cpu_pct = (total_cpu_pct / cpu_samples) if cpu_samples else None

    return JSONResponse({
        "ok": True,
        "metrics": {
            "gpu_util_pct": round(avg_util, 1),
            "gpu_mem_pct": round(mem_pct, 1) if mem_pct is not None else None,
            "gpu_mem_gb": round(mem_gb, 1) if mem_gb is not None else None,
            "cpu_pct": round(cpu_pct, 1) if cpu_pct is not None else None,
            "gpu_count": gpu_count,
            "flavor": flavor,
        },
    })


def _ensure_watchers_for_active_jobs() -> None:
    """Scan experiment registries and start watchers for running jobs."""
    try:
        from distillate import config
        from distillate.routes._context import _context
        import json
        from pathlib import Path

        state = _context._state
        for exp in (state.experiments or {}).values():
            proj_path = Path(exp.get("path", ""))
            registry = proj_path / ".distillate" / "hf_jobs.json"
            if not registry.exists():
                continue
            try:
                # hf_jobs.json is {job_id: {flavor, submitted_at, ...}}
                data = json.loads(registry.read_text())
                if not isinstance(data, dict):
                    continue
                # Sort by submitted_at, check the 3 most recent
                entries = sorted(data.items(), key=lambda kv: kv[1].get("submitted_at", ""), reverse=True)
                for jid, info in entries[:3]:
                    if not jid:
                        continue
                    with _lock:
                        already = jid in _watchers
                    if not already:
                        _maybe_start_watcher(jid, info.get("flavor", ""))
            except Exception:
                pass
    except Exception as exc:
        log.debug("_ensure_watchers_for_active_jobs error: %s", exc)


def _maybe_start_watcher(job_id: str, flavor: str) -> None:
    """Start watcher only if the job is currently running."""
    try:
        from distillate import config
        from distillate.compute_hfjobs import HFJobsProvider
        provider = HFJobsProvider(namespace=config.HF_NAMESPACE)
        info = provider.get_job(job_id)
        if info and info.status in ("running", "starting"):
            start_watching(job_id, flavor)
        elif info and info.status in ("completed", "failed", "cancelled"):
            stop_watching(job_id)
    except Exception:
        pass
