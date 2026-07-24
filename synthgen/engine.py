"""SyntheticDataEngine: orchestrates spec -> simulate -> analyze.

Public entry point. Resolves the configured provider/device preferences against
detected capabilities, generates a scenario spec via the LLM pipeline, simulates
row-level telemetry (GPU/cuDF when available), computes differentiable statistics,
and can emit ExpPilot ``DayStats`` so generated experiments feed the copilot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from synthgen.capabilities import Capabilities, detect
from synthgen.config import SynthGenConfig
from synthgen.pipeline import ScenarioPipeline
from synthgen.simulator import SimResult, simulate
from synthgen.spec import ScenarioRequest, ScenarioSpec
from synthgen.stats_diff import (
    optimize_min_detectable_effect,
    power_with_gradients,
    two_proportion_ztest,
)


@dataclass
class RunResult:
    spec: ScenarioSpec
    sim: SimResult
    analysis: dict[str, Any]


class SyntheticDataEngine:
    def __init__(self, config: SynthGenConfig | None = None):
        self.config = config or SynthGenConfig.from_env()
        self.caps: Capabilities = detect()
        self.pipeline = ScenarioPipeline(self.config, self.caps)
        # resolve compute preferences once
        self._use_cudf = self.config.wants_cudf(self.caps.gpu_dataframe_ready)
        self._use_torch = self.config.wants_torch(self.caps.differentiable_ready)
        self._prefer_gpu = self.caps.nvidia_gpu and self.config.device != "cpu"

    # ---- introspection ----
    def describe(self) -> dict[str, Any]:
        """Resolved backends + raw capabilities (great for a UI/console banner)."""
        return {
            "provider": self.pipeline.backend_name,
            "device": "cuda" if self._prefer_gpu else "cpu",
            "differentiable_stats": "torch-autograd" if self._use_torch else "numpy-finite-diff",
            "dataframe": "cudf.pandas" if self._use_cudf else "pandas",
            "capabilities": self.caps.summary(),
        }

    # ---- stages ----
    def generate_spec(self, request: ScenarioRequest | str) -> ScenarioSpec:
        return self.pipeline(request)

    def simulate(self, spec: ScenarioSpec) -> SimResult:
        return simulate(
            spec,
            seed=self.config.seed,
            use_cudf=self._use_cudf,
            prefer_gpu=self._prefer_gpu,
        )

    def analyze(self, spec: ScenarioSpec, sim: SimResult) -> dict[str, Any]:
        df = sim.frame
        ctrl = df[df["variant"] == "control"]
        trt = df[df["variant"] == "treatment"]
        c_n, t_n = int(len(ctrl)), int(len(trt))
        if spec.metric_type == "binary":
            c_conv = int(ctrl["metric"].sum())
            t_conv = int(trt["metric"].sum())
            observed = two_proportion_ztest(c_n, c_conv, t_n, t_conv)
        else:
            observed = {
                "control_mean": float(ctrl["metric"].mean()),
                "treatment_mean": float(trt["metric"].mean()),
                "abs_effect": float(trt["metric"].mean() - ctrl["metric"].mean()),
            }
        n_per_arm = min(c_n, t_n)
        power = power_with_gradients(
            n_per_arm=n_per_arm,
            base_rate=spec.base_rate,
            effect=spec.true_effect,
            use_torch=self._use_torch,
        )
        mde = optimize_min_detectable_effect(
            n_per_arm=n_per_arm,
            base_rate=spec.base_rate,
            target_power=0.8,
            use_torch=self._use_torch,
        )
        return {
            "observed": observed,
            "design_power": power.power,
            "d_power_d_effect": power.d_power_d_effect,
            "d_power_d_n": power.d_power_d_n,
            "power_backend": power.backend,
            "min_detectable_effect_at_0.8": mde,
            "n_per_arm": n_per_arm,
        }

    def to_daystats(self, spec: ScenarioSpec, sim: SimResult, experiment_id: str) -> list:
        """Aggregate row-level frame into per-day ExpPilot DayStats records."""
        from shared.models import DayStats

        df = sim.frame
        out: list[DayStats] = []
        for d in range(1, spec.n_days + 1):
            day_df = df[df["day"] == d]
            c = day_df[day_df["variant"] == "control"]
            t = day_df[day_df["variant"] == "treatment"]
            out.append(
                DayStats(
                    experiment_id=experiment_id,
                    day=d,
                    control_n=int(len(c)),
                    control_conversions=int(c["metric"].sum()),
                    treatment_n=int(len(t)),
                    treatment_conversions=int(t["metric"].sum()),
                    guardrail_control_rate=float(c["guardrail"].mean()) if len(c) else 0.0,
                    guardrail_treatment_rate=float(t["guardrail"].mean()) if len(t) else 0.0,
                )
            )
        return out

    # ---- one-shot ----
    def run(self, request: ScenarioRequest | str) -> RunResult:
        spec = self.generate_spec(request)
        sim = self.simulate(spec)
        analysis = self.analyze(spec, sim)
        return RunResult(spec=spec, sim=sim, analysis=analysis)
