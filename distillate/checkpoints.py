"""Model checkpoint storage — upload best-run weights to persistent storage.

Supports GitHub Releases (default, no extra auth), HuggingFace Hub,
and generic S3/R2 storage. Storage connector is configured per-project.
"""

import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Default checkpoint directory inside experiment repos
CHECKPOINT_DIR = ".distillate/checkpoints"


class CheckpointStorage(ABC):
    """Interface for checkpoint upload/download."""

    @abstractmethod
    def upload(self, project_path: Path, run_id: str, checkpoint_path: Path) -> str:
        """Upload a checkpoint file/directory. Returns a URL or identifier."""
        ...

    @abstractmethod
    def download(self, url: str, dest: Path) -> Path:
        """Download a checkpoint to dest. Returns the local path."""
        ...


class GitHubReleasesStorage(CheckpointStorage):
    """Upload checkpoints as GitHub Release assets.

    Uses `gh` CLI (must be installed and authenticated).
    Supports files up to 2GB. No extra auth needed — repo already exists.
    """

    def upload(self, project_path: Path, run_id: str, checkpoint_path: Path) -> str:
        """Upload checkpoint as a GitHub Release asset.

        Creates a release tagged `checkpoint-{run_id}` and attaches the file.
        """
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")

        # Get repo name from git remote
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=project_path,
        )
        if remote.returncode != 0:
            raise RuntimeError("No git remote configured — can't upload to GitHub Releases")

        tag = f"checkpoint-{run_id}"

        # Create release
        result = subprocess.run(
            ["gh", "release", "create", tag,
             "--title", f"Checkpoint: {run_id}",
             "--notes", f"Best model checkpoint from run {run_id}",
             "--repo", remote.stdout.strip()],
            capture_output=True, text=True, cwd=project_path,
        )
        if result.returncode != 0 and "already exists" not in result.stderr:
            raise RuntimeError(f"Failed to create release: {result.stderr.strip()}")

        # Upload asset(s)
        if checkpoint_path.is_dir():
            # Tar the directory first
            import tarfile
            tar_path = checkpoint_path.parent / f"{checkpoint_path.name}.tar.gz"
            with tarfile.open(tar_path, "w:gz") as tar:
                tar.add(checkpoint_path, arcname=checkpoint_path.name)
            upload_path = tar_path
        else:
            upload_path = checkpoint_path

        result = subprocess.run(
            ["gh", "release", "upload", tag, str(upload_path),
             "--clobber",
             "--repo", remote.stdout.strip()],
            capture_output=True, text=True, cwd=project_path,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to upload asset: {result.stderr.strip()}")

        # Return the release URL
        repo_url = remote.stdout.strip().rstrip(".git")
        return f"{repo_url}/releases/tag/{tag}"

    def download(self, url: str, dest: Path) -> Path:
        """Download a checkpoint from a GitHub Release."""
        dest.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["gh", "release", "download",
             "--dir", str(dest),
             "--pattern", "*",
             url.split("/releases/tag/")[-1]],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to download: {result.stderr.strip()}")
        return dest


class HuggingFaceStorage(CheckpointStorage):
    """Upload checkpoints to HuggingFace Hub.

    Requires: pip install huggingface_hub
    Auth: HF_TOKEN environment variable or `huggingface-cli login`.
    """

    def upload(self, project_path: Path, run_id: str, checkpoint_path: Path) -> str:
        """Upload checkpoint to HuggingFace Hub."""
        from huggingface_hub import HfApi

        api = HfApi()

        # Derive repo name from project directory
        repo_name = project_path.name
        try:
            user = api.whoami()["name"]
        except Exception:
            raise RuntimeError("HF_TOKEN not set. Run: huggingface-cli login")

        repo_id = f"{user}/{repo_name}"

        # Create repo if it doesn't exist
        api.create_repo(repo_id, exist_ok=True, repo_type="model")

        # Upload
        if checkpoint_path.is_dir():
            api.upload_folder(
                folder_path=str(checkpoint_path),
                repo_id=repo_id,
                path_in_repo=f"checkpoints/{run_id}",
                commit_message=f"Checkpoint from run {run_id}",
            )
        else:
            api.upload_file(
                path_or_fileobj=str(checkpoint_path),
                path_in_repo=f"checkpoints/{run_id}/{checkpoint_path.name}",
                repo_id=repo_id,
                commit_message=f"Checkpoint from run {run_id}",
            )

        return f"https://huggingface.co/{repo_id}/tree/main/checkpoints/{run_id}"

    def download(self, url: str, dest: Path) -> Path:
        """Download a checkpoint from HuggingFace Hub."""
        from huggingface_hub import snapshot_download

        # Parse URL to get repo_id and path
        parts = url.replace("https://huggingface.co/", "").split("/tree/main/")
        repo_id = parts[0]
        subfolder = parts[1] if len(parts) > 1 else ""

        local = snapshot_download(
            repo_id,
            local_dir=str(dest),
            allow_patterns=f"{subfolder}/**" if subfolder else None,
        )
        return Path(local)


def get_storage(storage_type: str = "github") -> CheckpointStorage:
    """Factory: return a checkpoint storage backend by type."""
    if storage_type == "github":
        return GitHubReleasesStorage()
    elif storage_type == "huggingface":
        return HuggingFaceStorage()
    raise ValueError(f"Unknown checkpoint storage: {storage_type}")


def upload_checkpoint_if_exists(
    project_path: Path,
    run_id: str,
    storage_type: str = "github",
) -> Optional[str]:
    """Check for checkpoint files and upload if found.

    Called after a [best] run concludes. Looks for files in
    .distillate/checkpoints/ directory.

    Returns the checkpoint URL, or None if no checkpoint found.
    """
    checkpoint_dir = project_path / CHECKPOINT_DIR
    if not checkpoint_dir.exists() or not any(checkpoint_dir.iterdir()):
        return None

    try:
        storage = get_storage(storage_type)
        url = storage.upload(project_path, run_id, checkpoint_dir)
        log.info("Checkpoint uploaded: %s", url)
        return url
    except Exception:
        log.exception("Failed to upload checkpoint for run %s", run_id)
        return None
