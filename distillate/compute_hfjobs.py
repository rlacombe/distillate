"""HuggingFace Jobs compute provider — submit and manage GPU jobs via huggingface_hub.

Unlike SSH-pod providers (RunPod), HF Jobs uses a submit-and-poll model:
the agent runs locally, dispatches training scripts as HF Jobs, and reads
results from storage buckets. This is a fundamentally different paradigm
from ComputeProvider (which provisions persistent SSH-accessible pods).

Requires: pip install huggingface_hub>=1.8.0
Auth: HF_TOKEN environment variable (or `hf auth login`).

Docs: https://huggingface.co/docs/hub/en/jobs
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GPU flavor mapping: friendly names → HF Jobs flavor strings
# ---------------------------------------------------------------------------

GPU_FLAVORS = {
    # CPUs
    "cpu-basic": "cpu-basic",
    "cpu-upgrade": "cpu-upgrade",
    # GPUs — friendly name → flavor string
    "T4": "t4-small",
    "T4-small": "t4-small",
    "T4-medium": "t4-medium",
    "L4": "l4x1",
    "L4x4": "l4x4",
    "L40S": "l40sx1",
    "L40Sx4": "l40sx4",
    "L40Sx8": "l40sx8",
    "A10G": "a10g-small",
    "A10G-small": "a10g-small",
    "A10G-large": "a10g-large",
    "A10Gx2": "a10g-largex2",
    "A10Gx4": "a10g-largex4",
    "A100": "a100-large",
    "A100x4": "a100x4",
    "A100x8": "a100x8",
    "H200": "h200",
    "H200x2": "h200x2",
    "H200x4": "h200x4",
    "H200x8": "h200x8",
}

# Approximate hourly cost for display/budgeting
GPU_COST_PER_HOUR = {
    "cpu-basic": 0.01,
    "cpu-upgrade": 0.03,
    "t4-small": 0.40,
    "t4-medium": 0.60,
    "l4x1": 0.80,
    "l4x4": 3.80,
    "l40sx1": 1.80,
    "l40sx4": 8.30,
    "l40sx8": 23.50,
    "a10g-small": 1.00,
    "a10g-large": 1.50,
    "a10g-largex2": 3.00,
    "a10g-largex4": 5.00,
    "a100-large": 2.50,
    "a100x4": 10.00,
    "a100x8": 20.00,
    "h200": 5.00,
    "h200x2": 10.00,
    "h200x4": 20.00,
    "h200x8": 40.00,
}


@dataclass
class JobInfo:
    """Metadata for a submitted HF Job."""
    id: str
    status: str = "pending"  # pending, starting, running, completed, failed, cancelled
    flavor: str = ""
    cost_per_hour: float = 0.0
    duration_seconds: float = 0.0
    logs_url: str = ""
    created_at: str = ""
    extra: dict = field(default_factory=dict)


class HFJobsProvider:
    """HuggingFace Jobs provider — submit training scripts as cloud GPU jobs.

    This does NOT implement ComputeProvider (which assumes SSH pods).
    Instead it provides a job-submission interface suited for the
    "agent dispatches training runs" pattern.
    """

    def __init__(self, namespace: str = ""):
        try:
            from huggingface_hub import HfApi
        except ImportError:
            raise ImportError(
                "huggingface_hub >= 1.8.0 required. Install with: "
                "pip install 'huggingface_hub>=1.8.0'"
            )
        from distillate import auth as _auth
        token = _auth.hf_token_for("jobs")
        if not token:
            raise RuntimeError(
                "No HF token found. Sign in with Hugging Face or set HF_TOKEN. "
                "Get your token at https://huggingface.co/settings/tokens"
            )
        self._api = HfApi(token=token)
        self._token = token
        ns = namespace or os.environ.get("HF_NAMESPACE", "")
        if not ns:
            try:
                ns = self._api.whoami()["name"]
            except Exception:
                pass
        self._namespace = ns

    def resolve_flavor(self, gpu_type: str) -> str:
        """Map a friendly GPU name to an HF Jobs flavor string."""
        return GPU_FLAVORS.get(gpu_type, gpu_type)

    def upload_script_for_job(
        self,
        script_local_path: str,
        proj_slug: str,
        extra_files: list[str] | None = None,
    ) -> tuple[str, str]:
        """Upload a training script to a private HF Hub dataset repo.

        The job mounts this repo at /workspace so the script is accessible
        to the container. Returns (repo_id, script_filename).

        extra_files: additional local files (utils.py, requirements.txt, …)
        to upload alongside the main script.
        """
        from pathlib import Path as _Path

        ns = self._namespace
        slug = proj_slug.lower().replace(" ", "-").replace("_", "-")
        repo_name = f"distillate-xp-{slug}"
        repo_id = f"{ns}/{repo_name}" if ns else repo_name

        self._api.create_repo(
            repo_id=repo_id,
            repo_type="dataset",
            private=True,
            exist_ok=True,
        )

        script_path = _Path(script_local_path)
        self._api.upload_file(
            path_or_fileobj=script_path.read_bytes(),
            path_in_repo=script_path.name,
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=f"distillate: upload {script_path.name}",
        )

        for extra in (extra_files or []):
            ep = _Path(extra)
            if ep.exists():
                try:
                    self._api.upload_file(
                        path_or_fileobj=ep.read_bytes(),
                        path_in_repo=ep.name,
                        repo_id=repo_id,
                        repo_type="dataset",
                        commit_message=f"distillate: upload {ep.name}",
                    )
                except Exception as e:
                    log.warning("Failed to upload %s: %s", extra, e)

        return repo_id, script_path.name

    def submit_job(
        self,
        script: str,
        *,
        gpu_flavor: str = "a100-large",
        timeout_minutes: int = 30,
        volumes: list[str] | None = None,
        env: dict[str, str] | None = None,
        secrets: dict[str, str] | None = None,
        dependencies: list[str] | None = None,
        labels: dict[str, str] | None = None,
        image: str = "",
        github_url: str = "",
        code_repo_id: str = "",
    ) -> JobInfo:
        """Submit a training script as an HF Job.

        Args:
            script: Script filename (e.g. "train.py"). If code_repo_id is
                given, the script is fetched from /workspace/<script> in the
                mounted code repo; otherwise the script name is used as-is
                which will fail unless the image already contains it.
            gpu_flavor: HF Jobs flavor (e.g. "a100-large", "h200").
            timeout_minutes: Max runtime in minutes (default 30).
            volumes: Extra HF volume mount strings ("hf://datasets/org/data:/data").
            env: Environment variables to pass to the job.
            secrets: Secret environment variables (encrypted server-side).
            dependencies: Python packages — installed via `uv run --with`.
            image: Custom Docker image.
            code_repo_id: HF Hub dataset repo ID containing the script.
                When provided, this repo is mounted at /workspace and the
                command runs /workspace/<script>. Call upload_script_for_job()
                to populate it before submitting.
        """
        from pathlib import Path as _Path

        from huggingface_hub import Volume, run_job

        flavor = self.resolve_flavor(gpu_flavor)
        cost = GPU_COST_PER_HOUR.get(flavor, 0.0)

        log.info(
            "Submitting HF Job: script=%s, flavor=%s ($%.2f/hr), timeout=%dm, code_repo=%s",
            script, flavor, cost, timeout_minutes, code_repo_id or "(none)",
        )

        # Build volume list from caller-supplied mounts
        hf_volumes = []
        for vol_str in (volumes or []):
            parts = vol_str.split(":")
            if len(parts) >= 2 and parts[0].startswith("hf://"):
                source = parts[0].replace("hf://", "")
                mount_path = parts[-1]
                vol_type = "dataset" if "datasets/" in source else "bucket"
                source_name = source.split("/", 1)[-1] if "/" in source else source
                hf_volumes.append(Volume(
                    type=vol_type,
                    source=source_name,
                    mount_path=mount_path,
                ))

        # Build command based on script source
        if github_url:
            # Clone from public GitHub repo — no auth needed in the container
            run_part = (
                "uv run " + " ".join(f"--with={d}" for d in dependencies) + f" {script}"
                if dependencies else f"python3 {script}"
            )
            command: list[str] = [
                "bash", "-c",
                f"git clone --depth=1 {github_url} /workspace && cd /workspace && {run_part}",
            ]
        else:
            # Mount the code repo (HF Hub dataset) at /workspace
            if code_repo_id:
                hf_volumes.insert(0, Volume(
                    type="dataset",
                    source=code_repo_id,
                    mount_path="/workspace",
                ))
                script_in_container = f"/workspace/{_Path(script).name}"
            else:
                script_in_container = script

            if dependencies:
                command = (
                    ["uv", "run"]
                    + [f"--with={dep}" for dep in dependencies]
                    + [script_in_container]
                )
            else:
                command = ["python3", script_in_container]

        all_secrets = dict(secrets or {})

        kwargs: dict = {
            "command": command,
            "flavor": flavor,
            "timeout": int(timeout_minutes * 60),
        }
        if image:
            kwargs["image"] = image
        if hf_volumes:
            kwargs["volumes"] = hf_volumes
        if env:
            kwargs["env"] = env
        if all_secrets:
            kwargs["secrets"] = all_secrets
        if self._namespace:
            kwargs["namespace"] = self._namespace

        job = run_job(**kwargs)

        job_id = job.id if hasattr(job, "id") else str(job)
        return JobInfo(
            id=job_id,
            status="starting",
            flavor=flavor,
            cost_per_hour=cost,
            extra={"timeout_minutes": timeout_minutes},
        )

    def get_job(self, job_id: str) -> Optional[JobInfo]:
        """Get current status of a job."""
        try:
            job = self._api.get_job(job_id)
            if not job:
                return None
            status = getattr(job, "status", "unknown")
            flavor = getattr(job, "flavor", "")

            # Extract duration: prefer explicit field, fall back to timestamp delta
            duration_seconds = 0.0
            raw_duration = getattr(job, "duration", None) or getattr(job, "duration_seconds", None)
            if raw_duration:
                try:
                    duration_seconds = float(raw_duration)
                except (TypeError, ValueError):
                    pass
            if not duration_seconds:
                started = getattr(job, "started_at", None) or getattr(job, "created_at", None)
                ended = getattr(job, "completed_at", None) or getattr(job, "finished_at", None)
                if started and ended and status in ("completed", "failed", "cancelled"):
                    try:
                        from datetime import datetime as _dt
                        fmt = "%Y-%m-%dT%H:%M:%S"
                        s = _dt.fromisoformat(str(started).replace("Z", "+00:00"))
                        e = _dt.fromisoformat(str(ended).replace("Z", "+00:00"))
                        duration_seconds = max(0.0, (e - s).total_seconds())
                    except Exception:
                        pass

            return JobInfo(
                id=job_id,
                status=status,
                flavor=flavor,
                cost_per_hour=GPU_COST_PER_HOUR.get(flavor, 0.0),
                duration_seconds=duration_seconds,
                created_at=str(getattr(job, "created_at", "")),
                extra={"raw": job.__dict__ if hasattr(job, "__dict__") else {}},
            )
        except Exception:
            log.exception("Failed to get job %s", job_id)
            return None

    def get_logs(self, job_id: str) -> str:
        """Get logs from a job."""
        try:
            logs = self._api.get_job_logs(job_id)
            return logs if isinstance(logs, str) else str(logs)
        except Exception:
            log.exception("Failed to get logs for job %s", job_id)
            return ""

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job."""
        try:
            self._api.cancel_job(job_id)
            log.info("Cancelled job: %s", job_id)
            return True
        except Exception:
            log.exception("Failed to cancel job %s", job_id)
            return False

    def list_jobs(self, limit: int = 20) -> list[JobInfo]:
        """List recent jobs."""
        try:
            jobs = self._api.list_jobs()
            result = []
            for job in (jobs or [])[:limit]:
                result.append(JobInfo(
                    id=getattr(job, "id", ""),
                    status=getattr(job, "status", "unknown"),
                    flavor=getattr(job, "flavor", ""),
                    created_at=getattr(job, "created_at", ""),
                ))
            return result
        except Exception:
            log.exception("Failed to list jobs")
            return []

    def wait_for_completion(
        self, job_id: str, *, timeout: int = 3600, poll_interval: int = 10,
    ) -> JobInfo:
        """Poll until a job completes or times out."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self.get_job(job_id)
            if info and info.status in ("completed", "failed", "cancelled"):
                return info
            time.sleep(poll_interval)
        raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")


# ---------------------------------------------------------------------------
# Storage bucket helpers
# ---------------------------------------------------------------------------

def ensure_bucket(bucket_name: str, token: str = "") -> str:
    """Create a storage bucket if it doesn't exist. Returns the bucket name."""
    from huggingface_hub import HfApi
    api = HfApi(token=token or os.environ.get("HF_TOKEN", ""))
    try:
        api.create_repo(bucket_name, repo_type="bucket", exist_ok=True)
        log.info("Storage bucket ready: %s", bucket_name)
    except Exception:
        log.warning("Could not create bucket %s — may already exist", bucket_name)
    return bucket_name


def bucket_volume(bucket_name: str, mount_path: str = "/output") -> str:
    """Return an HF volume mount string for a storage bucket."""
    return f"hf://buckets/{bucket_name}:{mount_path}"
