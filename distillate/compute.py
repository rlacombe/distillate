"""Abstract compute provider for cloud GPU provisioning.

Defines the interface for provisioning, monitoring, and tearing down
remote compute resources. Implementations live in compute_*.py modules.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PodInfo:
    """Metadata for a provisioned compute pod."""
    id: str
    provider: str           # "hfjobs", etc.
    host: str               # SSH hostname or IP
    ssh_user: str = "root"
    ssh_port: int = 22
    gpu_type: str = ""
    gpu_count: int = 1
    cost_per_hour: float = 0.0
    status: str = "pending"  # pending, running, stopping, terminated
    image: str = ""
    extra: dict = field(default_factory=dict)


class ComputeProvider(ABC):
    """Interface for cloud compute providers."""

    @abstractmethod
    def create_pod(
        self,
        gpu_type: str = "RTX_4090",
        gpu_count: int = 1,
        image: str = "pytorch/pytorch:2.1.0-cuda11.8-cudnn8-devel",
        disk_size_gb: int = 50,
        name: str = "distillate",
    ) -> PodInfo:
        """Provision a new pod and return its info.

        Blocks until the pod has an SSH endpoint ready.
        """
        ...

    @abstractmethod
    def terminate_pod(self, pod_id: str) -> bool:
        """Terminate a running pod. Returns True on success."""
        ...

    @abstractmethod
    def list_pods(self) -> list[PodInfo]:
        """List all active pods for this account."""
        ...

    @abstractmethod
    def get_pod(self, pod_id: str) -> Optional[PodInfo]:
        """Get current status of a specific pod."""
        ...


def get_provider(name: str) -> ComputeProvider:
    """Factory: return a compute provider by name.

    Raises ValueError for unknown providers.
    """
    raise ValueError(f"Unknown compute provider: {name}")


def get_job_provider(name: str = "hfjobs"):
    """Factory: return a job-based compute provider by name.

    Job providers use a submit-and-poll model (unlike SSH-pod providers).
    Currently only HuggingFace Jobs is supported.
    """
    if name in ("hfjobs", "huggingface"):
        from distillate.compute_hfjobs import HFJobsProvider
        return HFJobsProvider()
    raise ValueError(f"Unknown job provider: {name}")
