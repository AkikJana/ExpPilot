"""Engine configuration with environment overrides.

Preference model: the user picks a *provider preference* (``auto`` by default);
the engine resolves it against detected capabilities. Same for device and cuDF —
you can force cpu/gpu, or leave ``auto`` and let the engine choose the fastest path.
"""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

Provider = Literal["auto", "local", "api", "template"]
Device = Literal["auto", "cpu", "cuda"]
Toggle = Literal["auto", "on", "off"]


class SynthGenConfig(BaseModel):
    """How the synthetic-data engine should generate, simulate, and analyze."""

    # --- generation backend ---
    provider: Provider = "auto"
    local_model: str = "google/gemma-3n-E4B-it"
    api_model: str = "llama-3.3-70b-versatile"  # served via Groq by default

    # --- compute ---
    device: Device = "auto"
    use_cudf: Toggle = "auto"     # cuDF-accelerated pandas for dataframe ops
    use_torch: Toggle = "auto"    # torch autograd for differentiable stats

    # --- simulation size ---
    n_rows: int = Field(default=20_000, ge=1)
    seed: int = 2026

    @classmethod
    def from_env(cls) -> "SynthGenConfig":
        """Build config from SYNTHGEN_* environment variables (all optional)."""
        def env(name: str, default):
            return os.getenv(name, default)

        return cls(
            provider=env("SYNTHGEN_PROVIDER", "auto"),          # auto|local|api|template
            local_model=env("SYNTHGEN_LOCAL_MODEL", "google/gemma-3n-E4B-it"),
            api_model=env("SYNTHGEN_API_MODEL", "llama-3.3-70b-versatile"),
            device=env("SYNTHGEN_DEVICE", "auto"),              # auto|cpu|cuda
            use_cudf=env("SYNTHGEN_USE_CUDF", "auto"),          # auto|on|off
            use_torch=env("SYNTHGEN_USE_TORCH", "auto"),        # auto|on|off
            n_rows=int(env("SYNTHGEN_ROWS", 20_000)),
            seed=int(env("SYNTHGEN_SEED", 2026)),
        )

    def wants_cudf(self, gpu_dataframe_ready: bool) -> bool:
        if self.use_cudf == "off":
            return False
        if self.use_cudf == "on":
            return gpu_dataframe_ready  # cannot fabricate a GPU
        return gpu_dataframe_ready  # auto

    def wants_torch(self, torch_ready: bool) -> bool:
        if self.use_torch == "off":
            return False
        if self.use_torch == "on":
            return torch_ready
        return torch_ready  # auto
