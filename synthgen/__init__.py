"""ExpPilot synthetic-data generation engine.

An LLM-driven, GPU-aware, differentiable synthetic experiment generator:

- Scenario specs are drafted by a switchable LLM backend (local Gemma 3n E4B via
  Hugging Face / LiteRT, or an API model), always validated into a typed spec.
- Row-level telemetry is simulated with a vectorized engine that transparently
  uses cuDF (``cudf.pandas``) when an NVIDIA GPU is present, and pandas otherwise.
- Statistics are computed with differentiable programming (PyTorch autograd when
  available; a numpy analytic-gradient fallback otherwise), so experiment designs
  can be optimized by gradient descent.

Everything degrades gracefully: with no GPU and no ML libraries installed, the
engine still runs end to end on numpy + pandas + a deterministic template backend.
"""
from synthgen.config import SynthGenConfig
from synthgen.capabilities import Capabilities, detect
from synthgen.engine import SyntheticDataEngine
from synthgen.spec import ScenarioRequest, ScenarioSpec, Segment

__all__ = [
    "SynthGenConfig",
    "Capabilities",
    "detect",
    "SyntheticDataEngine",
    "ScenarioRequest",
    "ScenarioSpec",
    "Segment",
]
