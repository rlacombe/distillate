"""Helpers for reading ``.distillate/budget.json``.

This file is the shared state between two independent budget systems
that both live in ``.distillate/budget.json``:

* ``train_budget_seconds`` -- wall-clock per-run training budget,
  enforced by ``distillate-run`` at the kernel level (SIGTERM -> SIGKILL)
  and mirrored by an in-script guard written by the agent. Use
  :func:`read_train_budget` to read it.

* ``modal`` -- remote-GPU config (Modal.com) with a $-denominated cap,
  polled by the per-experiment budget watcher and consulted by the
  Integrations UI. Use :func:`read_modal_config` to read it and
  :func:`write_modal_config` to upsert without clobbering the train
  budget.

The two systems are deliberately orthogonal (wall-clock vs. dollars)
but share the same file so experiment templates can ship a single
config artefact rather than scattering JSON across the tree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# Fallback when no budget.json is reachable. Deliberately 3300 (= 3600 -
# the default 300s reserve) so a freshly-cloned repo without a budget
# file still runs a sensible ~55-minute training loop.
_FALLBACK_BUDGET_SECONDS = 3300

# Absolute floor: even if reserve >= budget, return something non-zero so
# the agent's guard has a tiny bit of headroom rather than looping forever
# or dividing by zero. 60s is short enough to surface the config error
# fast but long enough for training loops to reach an epoch boundary.
_MIN_TRAIN_SECONDS = 60


def read_train_budget(
    reserve_seconds: int = 300,
    *,
    cwd: Optional[Path] = None,
) -> int:
    """Return the training budget for this experiment, in seconds.

    Walks up from ``cwd`` (default: ``Path.cwd()``) looking for a
    ``.distillate/budget.json``. Reads ``train_budget_seconds`` (falling
    back to the legacy ``run_budget_seconds``), subtracts
    ``reserve_seconds`` to leave headroom for eval/wrap-up, and floors
    the result at 60s.

    Fallback returns ``_FALLBACK_BUDGET_SECONDS`` (3300s) when:

    * no ``.distillate/budget.json`` on the walk-up path
    * the file can't be parsed
    * neither budget key is present (or both are zero/negative)

    :param reserve_seconds: seconds to reserve for post-training work
        (eval, checkpoint save, ``conclude_run``). Default 300s.
    :param cwd: optional working directory to start the walk-up from.
        Default is ``Path.cwd()`` (the script's current dir).
    """
    start = Path(cwd) if cwd is not None else Path.cwd()
    budget_path = _find_budget_json(start)

    if budget_path is None:
        return _FALLBACK_BUDGET_SECONDS

    try:
        data = json.loads(budget_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _FALLBACK_BUDGET_SECONDS

    raw = data.get("train_budget_seconds") or data.get("run_budget_seconds")
    try:
        budget = int(raw) if raw else 0
    except (TypeError, ValueError):
        budget = 0

    if budget <= 0:
        return _FALLBACK_BUDGET_SECONDS

    return max(_MIN_TRAIN_SECONDS, budget - int(reserve_seconds))


def _find_budget_json(start: Path) -> Optional[Path]:
    """Walk up from *start* looking for ``.distillate/budget.json``."""
    for parent in [start, *start.parents]:
        candidate = parent / ".distillate" / "budget.json"
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Modal config -- remote-GPU spend cap stored alongside the train budget
# ---------------------------------------------------------------------------


def read_modal_config(*, cwd: Optional[Path] = None) -> Optional[dict]:
    """Return the Modal config for this experiment, or None.

    Walks up from *cwd* (default: ``Path.cwd()``) looking for
    ``.distillate/budget.json``. If the file has a ``modal`` dict with
    ``enabled`` truthy, returns that dict. Every other case (no file,
    no ``modal`` key, ``enabled: false``, malformed JSON, wrong type)
    returns None so callers can gate with ``if cfg is None: return``.
    """
    start = Path(cwd) if cwd is not None else Path.cwd()
    budget_path = _find_budget_json(start)
    if budget_path is None:
        return None
    try:
        data = json.loads(budget_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    modal = data.get("modal") if isinstance(data, dict) else None
    if not isinstance(modal, dict):
        return None
    if not modal.get("enabled"):
        return None
    return modal


def write_modal_config(
    *,
    cwd: Path,
    gpu: str,
    budget_usd: float,
    enabled: bool = True,
) -> None:
    """Upsert the Modal block into ``{cwd}/.distillate/budget.json``.

    Reads the existing file (if any), merges in the Modal config, and
    writes it back. Creates ``.distillate/`` if missing. Preserves
    unrelated keys like ``train_budget_seconds`` -- we share the file
    with the wall-clock budget system, so overwriting it blindly would
    nuke the training guard.
    """
    distillate_dir = cwd / ".distillate"
    distillate_dir.mkdir(parents=True, exist_ok=True)
    budget_path = distillate_dir / "budget.json"

    data: dict = {}
    if budget_path.is_file():
        try:
            existing = json.loads(budget_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if isinstance(existing, dict):
            data = existing

    data["modal"] = {
        "enabled": bool(enabled),
        "gpu": gpu,
        "budget_usd": float(budget_usd),
    }
    budget_path.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Generic compute budget — HF Jobs (and future providers)
# ---------------------------------------------------------------------------


def write_compute_budget(
    *,
    cwd: Path,
    provider: str,
    gpu_type: str,
    budget_usd: float,
    cost_per_hour: float = 0.0,
) -> None:
    """Upsert the compute budget block into ``{cwd}/.distillate/budget.json``.

    Preserves existing keys (train_budget_seconds, modal, etc.).
    If cost_per_hour is 0, it will be looked up from the pricing table.
    """
    distillate_dir = cwd / ".distillate"
    distillate_dir.mkdir(parents=True, exist_ok=True)
    budget_path = distillate_dir / "budget.json"

    data: dict = {}
    if budget_path.is_file():
        try:
            existing = json.loads(budget_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if isinstance(existing, dict):
            data = existing

    if not cost_per_hour and provider == "hfjobs":
        from distillate.compute_hfjobs import GPU_COST_PER_HOUR, GPU_FLAVORS
        flavor = GPU_FLAVORS.get(gpu_type, gpu_type)
        cost_per_hour = GPU_COST_PER_HOUR.get(flavor, 0.0)

    data["compute"] = {
        "provider": provider,
        "gpu_type": gpu_type,
        "budget_usd": float(budget_usd),
        "cost_per_hour": cost_per_hour,
    }
    budget_path.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8",
    )


def read_compute_budget(*, cwd: Optional[Path] = None) -> Optional[dict]:
    """Return the compute budget config, or None if not set."""
    start = Path(cwd) if cwd is not None else Path.cwd()
    budget_path = _find_budget_json(start)
    if budget_path is None:
        return None
    try:
        data = json.loads(budget_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    compute = data.get("compute") if isinstance(data, dict) else None
    if not isinstance(compute, dict):
        return None
    return compute


def write_experiment_alert(*, cwd: Path, kind: str, message: str) -> bool:
    """Append an alert to .distillate/alerts.json.

    Deduplicates by kind — if an undismissed alert of the same kind already
    exists, this is a no-op and returns False.  Returns True when written.
    """
    from datetime import datetime, timezone as _tz

    distillate_dir = Path(cwd) / ".distillate"
    distillate_dir.mkdir(parents=True, exist_ok=True)
    alerts_path = distillate_dir / "alerts.json"

    alerts: list = []
    if alerts_path.is_file():
        try:
            alerts = json.loads(alerts_path.read_text(encoding="utf-8"))
            if not isinstance(alerts, list):
                alerts = []
        except (OSError, json.JSONDecodeError):
            alerts = []

    if any(a.get("kind") == kind and not a.get("dismissed") for a in alerts):
        return False

    alerts.append({
        "kind": kind,
        "message": message,
        "ts": datetime.now(_tz.utc).isoformat(),
        "dismissed": False,
    })
    alerts_path.write_text(json.dumps(alerts, indent=2) + "\n", encoding="utf-8")
    return True


def read_experiment_alerts(*, cwd: Path) -> list:
    """Return active (non-dismissed) alerts from .distillate/alerts.json."""
    alerts_path = Path(cwd) / ".distillate" / "alerts.json"
    if not alerts_path.is_file():
        return []
    try:
        alerts = json.loads(alerts_path.read_text(encoding="utf-8"))
        if not isinstance(alerts, list):
            return []
        return [a for a in alerts if isinstance(a, dict) and not a.get("dismissed")]
    except (OSError, json.JSONDecodeError):
        return []


def dismiss_experiment_alerts(*, cwd: Path, kind: str | None = None) -> None:
    """Mark alerts dismissed in .distillate/alerts.json.

    If *kind* is given, only that kind is dismissed.  Otherwise all are.
    """
    alerts_path = Path(cwd) / ".distillate" / "alerts.json"
    if not alerts_path.is_file():
        return
    try:
        alerts = json.loads(alerts_path.read_text(encoding="utf-8"))
        if not isinstance(alerts, list):
            return
        for a in alerts:
            if not kind or a.get("kind") == kind:
                a["dismissed"] = True
        alerts_path.write_text(json.dumps(alerts, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass


def read_compute_spend(*, cwd: Optional[Path] = None) -> dict:
    """Read cumulative compute spend from .distillate/compute_spend.json."""
    start = Path(cwd) if cwd is not None else Path.cwd()
    for parent in [start, *start.parents]:
        candidate = parent / ".distillate" / "compute_spend.json"
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError):
                pass
            break
    return {"total_usd": 0.0, "jobs": []}


def record_job_spend(
    *,
    cwd: Path,
    job_id: str,
    flavor: str,
    duration_seconds: float,
    cost_per_hour: float,
) -> dict:
    """Record spend for a completed/checked job. Returns updated spend data."""
    distillate_dir = cwd / ".distillate"
    distillate_dir.mkdir(parents=True, exist_ok=True)
    spend_path = distillate_dir / "compute_spend.json"

    data: dict = {"total_usd": 0.0, "jobs": []}
    if spend_path.is_file():
        try:
            existing = json.loads(spend_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
        except (OSError, json.JSONDecodeError):
            pass

    # Update existing job entry or add new one
    jobs = data.get("jobs", [])
    cost_usd = (duration_seconds / 3600.0) * cost_per_hour
    existing_job = next((j for j in jobs if j.get("job_id") == job_id), None)
    if existing_job:
        existing_job["duration_seconds"] = duration_seconds
        existing_job["cost_usd"] = cost_usd
    else:
        jobs.append({
            "job_id": job_id,
            "flavor": flavor,
            "duration_seconds": duration_seconds,
            "cost_usd": cost_usd,
        })

    data["jobs"] = jobs
    data["total_usd"] = sum(j.get("cost_usd", 0.0) for j in jobs)

    spend_path.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8",
    )
    return data
