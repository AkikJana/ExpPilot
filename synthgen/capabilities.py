"""Runtime capability detection: GPU, cuDF, PyTorch, Transformers, local Gemma.

Pure detection — never imports the heavy libraries at module load, only checks
whether they *can* be imported. This lets the engine pick the fastest available
path (GPU + cuDF + torch) or fall back to numpy + pandas, with zero hard deps.
"""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass, asdict


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _cuda_via_torch() -> bool:
    if not _installed("torch"):
        return False
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _nvidia_present() -> bool:
    """True if an NVIDIA GPU looks present, even before torch is installed."""
    if shutil.which("nvidia-smi"):
        return True
    # /proc/driver/nvidia exists when the kernel module is loaded.
    import os

    return os.path.exists("/proc/driver/nvidia")


@dataclass(frozen=True)
class Capabilities:
    """Snapshot of what acceleration/generation options are available."""

    nvidia_gpu: bool
    cuda_torch: bool
    torch: bool
    cudf: bool
    transformers: bool
    accelerate: bool
    litellm: bool
    huggingface_hub: bool

    @property
    def gpu_dataframe_ready(self) -> bool:
        """cuDF can only accelerate pandas when both cuDF and an NVIDIA GPU exist."""
        return self.cudf and self.nvidia_gpu

    @property
    def differentiable_ready(self) -> bool:
        """True autograd requires torch (GPU optional)."""
        return self.torch

    @property
    def local_llm_ready(self) -> bool:
        """Local Gemma via HF needs transformers + torch."""
        return self.transformers and self.torch

    def summary(self) -> dict:
        d = asdict(self)
        d.update(
            gpu_dataframe_ready=self.gpu_dataframe_ready,
            differentiable_ready=self.differentiable_ready,
            local_llm_ready=self.local_llm_ready,
        )
        return d


def detect() -> Capabilities:
    """Detect the current machine's acceleration and generation capabilities."""
    return Capabilities(
        nvidia_gpu=_nvidia_present(),
        cuda_torch=_cuda_via_torch(),
        torch=_installed("torch"),
        cudf=_installed("cudf"),
        transformers=_installed("transformers"),
        accelerate=_installed("accelerate"),
        litellm=_installed("litellm"),
        huggingface_hub=_installed("huggingface_hub"),
    )
