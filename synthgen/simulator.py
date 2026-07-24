"""Vectorized, GPU-aware row-level experiment simulator.

Generates thousands to millions of per-user observation rows with no Python
row loops. Random generation is vectorized on numpy (or cupy on GPU); the
resulting frame is materialized through pandas or cuDF-accelerated pandas.
Fully deterministic given a seed.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from synthgen.accel import get_array_module, get_pandas
from synthgen.spec import ScenarioSpec

_NOVELTY_BUMP = 0.6      # early-period relative inflation of the treatment effect
_NOVELTY_TAU = 3.0       # decay constant (days)


@dataclass
class SimResult:
    """Row-level frame plus the compute backends actually used."""

    frame: object            # pandas / cudf DataFrame
    n_rows: int
    array_backend: str       # 'numpy' | 'cupy'
    frame_backend: str       # 'pandas' | 'cudf.pandas'


def _to_numpy(arr, xp_name: str):
    if xp_name == "cupy":
        return arr.get()  # cupy -> numpy for the pandas constructor when needed
    return arr


def simulate(
    spec: ScenarioSpec,
    seed: int = 2026,
    use_cudf: bool = False,
    prefer_gpu: bool = False,
) -> SimResult:
    """Simulate the full experiment as a tidy per-observation frame."""
    xp_name, xp = get_array_module(prefer_gpu)
    pd = get_pandas(use_cudf)

    n = spec.n_rows
    # Seeded RNG (numpy always; cupy has its own Generator with the same API surface).
    if xp_name == "cupy":
        rng = xp.random.default_rng(seed)
    else:
        rng = np.random.default_rng(seed)

    # --- day index: daily_traffic rows per day ---
    day = xp.repeat(xp.arange(1, spec.n_days + 1), spec.daily_traffic)[:n]

    # --- variant assignment (respect allocation, or SRM if injected) ---
    ctrl_share = spec.srm_ratio if spec.srm else spec.allocation.get("control", 0.5)
    u_variant = rng.random(n)
    is_treatment = u_variant >= ctrl_share  # True -> treatment

    # --- segment assignment via normalized weights ---
    weights = np.array([s.weight for s in spec.segments], dtype=float)
    weights = weights / weights.sum()
    seg_idx = rng.choice(len(spec.segments), size=n, p=weights) if xp_name != "cupy" \
        else xp.asarray(np.random.default_rng(seed + 1).choice(len(spec.segments), size=n, p=weights))
    eff_mult = xp.asarray(np.array([s.effect_multiplier for s in spec.segments]))[seg_idx]
    base_shift = xp.asarray(np.array([s.base_shift for s in spec.segments]))[seg_idx]

    # --- pre-experiment covariate (for CUPED-style variance reduction) ---
    covariate = rng.standard_normal(n)

    # --- novelty decay multiplier on the treatment effect ---
    if spec.novelty:
        novelty_mult = 1.0 + _NOVELTY_BUMP * xp.exp(-day / _NOVELTY_TAU)
    else:
        novelty_mult = xp.ones(n)

    treat = is_treatment.astype(float)
    effect_term = treat * spec.true_effect * eff_mult * novelty_mult

    if spec.metric_type == "binary":
        p = spec.base_rate + base_shift + effect_term
        # weak covariate coupling so pre-metric predicts outcome (CUPED)
        p = p + spec.covariate_correlation * 0.02 * covariate
        p = xp.clip(p, 1e-4, 1 - 1e-4)
        metric = (rng.random(n) < p).astype(float)
    else:
        mean = spec.base_mean + base_shift + effect_term + spec.covariate_correlation * covariate
        metric = mean + rng.standard_normal(n) * spec.base_sd

    # --- guardrail event ---
    g_rate = spec.guardrail.base_rate + treat * spec.guardrail.treatment_delta
    g_rate = xp.clip(g_rate, 1e-4, 1 - 1e-4)
    guardrail = (rng.random(n) < g_rate).astype(float)

    variant_arr = np.where(_to_numpy(is_treatment, xp_name), "treatment", "control")
    seg_names = np.array([s.name for s in spec.segments])
    segment_arr = seg_names[_to_numpy(seg_idx, xp_name)]

    frame = pd.DataFrame(
        {
            "user_id": np.arange(n),
            "day": _to_numpy(day, xp_name),
            "variant": variant_arr,
            "segment": segment_arr,
            "covariate": _to_numpy(covariate, xp_name),
            "metric": _to_numpy(metric, xp_name),
            "guardrail": _to_numpy(guardrail, xp_name),
        }
    )

    frame_backend = "cudf.pandas" if type(frame).__module__.startswith("cudf") else "pandas"
    return SimResult(frame=frame, n_rows=n, array_backend=xp_name, frame_backend=frame_backend)
