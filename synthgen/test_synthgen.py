"""Tests for the synthetic-data engine (CPU / numpy / pandas / template fallback path)."""
from __future__ import annotations

import math

from synthgen import SyntheticDataEngine, SynthGenConfig, detect
from synthgen.simulator import simulate
from synthgen.spec import ScenarioRequest, ScenarioSpec
from synthgen.stats_diff import (
    optimize_min_detectable_effect,
    power_with_gradients,
    two_proportion_ztest,
)


def _cfg(**kw) -> SynthGenConfig:
    base = dict(provider="template", use_cudf="off", use_torch="auto", seed=7, n_rows=8000)
    base.update(kw)
    return SynthGenConfig(**base)


def test_detect_returns_flags():
    caps = detect()
    assert isinstance(caps.summary(), dict)
    assert "gpu_dataframe_ready" in caps.summary()


def test_template_spec_is_valid():
    eng = SyntheticDataEngine(_cfg())
    spec = eng.generate_spec("Increase checkout conversion")
    assert isinstance(spec, ScenarioSpec)
    assert 0 < spec.base_rate < 1
    assert abs(sum(spec.allocation.values()) - 1.0) < 1e-6


def test_simulation_shape_and_determinism():
    spec = ScenarioSpec(name="t", hypothesis="h", base_rate=0.1, true_effect=0.03,
                        n_days=10, daily_traffic=1000)
    a = simulate(spec, seed=42, use_cudf=False)
    b = simulate(spec, seed=42, use_cudf=False)
    assert a.n_rows == 10_000
    assert a.array_backend == "numpy"
    assert a.frame_backend == "pandas"
    # deterministic given seed
    assert int(a.frame["metric"].sum()) == int(b.frame["metric"].sum())


def test_treatment_lifts_metric():
    spec = ScenarioSpec(name="t", hypothesis="h", base_rate=0.1, true_effect=0.05,
                        segments=[], n_days=10, daily_traffic=3000)
    # default segment injected? ensure at least one
    spec.segments = spec.segments or []
    if not spec.segments:
        from synthgen.spec import Segment
        spec.segments = [Segment(name="all", weight=1.0)]
    sim = simulate(spec, seed=1, use_cudf=False)
    df = sim.frame
    ctrl = df[df["variant"] == "control"]["metric"].mean()
    trt = df[df["variant"] == "treatment"]["metric"].mean()
    assert trt > ctrl  # positive true effect should show up


def test_power_and_gradients_finite():
    res = power_with_gradients(n_per_arm=5000, base_rate=0.1, effect=0.02, use_torch=True)
    assert 0.0 <= res.power <= 1.0
    assert math.isfinite(res.d_power_d_effect)
    assert math.isfinite(res.d_power_d_n)
    assert res.d_power_d_effect > 0  # bigger effect -> more power
    assert res.d_power_d_n > 0       # bigger n -> more power


def test_optimize_mde_hits_target():
    n = 6000
    mde = optimize_min_detectable_effect(n_per_arm=n, base_rate=0.1, target_power=0.8, use_torch=True)
    p = power_with_gradients(n_per_arm=n, base_rate=0.1, effect=mde).power
    assert 0.7 <= p <= 0.9


def test_ztest_detects_significant():
    r = two_proportion_ztest(5000, 500, 5000, 650)
    assert r["significant"] is True
    assert r["abs_effect"] > 0


def test_to_daystats_bridge():
    eng = SyntheticDataEngine(_cfg())
    spec = eng.generate_spec("Increase checkout conversion")
    spec.n_days, spec.daily_traffic = 5, 1000
    sim = eng.simulate(spec)
    days = eng.to_daystats(spec, sim, "exp_synth")
    assert len(days) == 5
    total = sum(d.control_n + d.treatment_n for d in days)
    assert total == sim.n_rows


def test_full_run_smoke():
    eng = SyntheticDataEngine(_cfg(n_rows=6000))
    result = eng.run(ScenarioRequest(goal="Reduce churn for prepaid users"))
    assert result.analysis["design_power"] >= 0.0
    assert result.analysis["power_backend"] in ("torch-autograd", "numpy-finite-diff")
