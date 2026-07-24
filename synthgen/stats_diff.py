"""Differentiable statistics for experiment design.

Statistical power for a two-proportion z-test is expressed as a smooth function of
the design parameters (effect size, per-arm sample size). With PyTorch installed we
compute exact gradients via autograd (GPU-ready); otherwise we fall back to numpy
with central-difference gradients. Gradients let us *optimize* designs (e.g. find
the smallest effect detectable at a target power) rather than only evaluate them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def _torch_available(use_torch: bool) -> bool:
    if not use_torch:
        return False
    try:
        import torch  # noqa: F401

        return True
    except Exception:
        return False


@dataclass
class PowerResult:
    power: float
    d_power_d_effect: float
    d_power_d_n: float
    backend: str  # 'torch-autograd' | 'numpy-finite-diff'


# ---------------------------------------------------------------------------
# numpy path
# ---------------------------------------------------------------------------
def _np_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _np_norm_ppf(p: float) -> float:
    # Acklam-style rational approximation; ample accuracy for design math.
    a = [-39.6968302866538, 220.946098424521, -275.928510446969,
         138.357751867269, -30.6647980661472, 2.50662827745924]
    b = [-54.4760987982241, 161.585836858041, -155.698979859887,
         66.8013118877197, -13.2806815528857]
    c = [-0.00778489400243029, -0.322396458041136, -2.40075827716184,
         -2.54973253934373, 4.37466414146497, 2.93816398269878]
    d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _np_power(n: float, base_rate: float, effect: float, alpha: float) -> float:
    p1 = base_rate
    p2 = min(max(base_rate + effect, 1e-6), 1 - 1e-6)
    pbar = 0.5 * (p1 + p2)
    sd0 = math.sqrt(max(2 * pbar * (1 - pbar), 1e-12))
    sd1 = math.sqrt(max(p1 * (1 - p1) + p2 * (1 - p2), 1e-12))
    z_alpha = _np_norm_ppf(1 - alpha / 2)
    arg = (math.sqrt(max(n, 1e-9)) * abs(effect) - z_alpha * sd0) / sd1
    return _np_normal_cdf(arg)


def _np_power_result(n: float, base_rate: float, effect: float, alpha: float) -> PowerResult:
    h_e = max(1e-5, abs(effect) * 1e-3)
    h_n = max(1.0, n * 1e-3)
    de = (_np_power(n, base_rate, effect + h_e, alpha) -
          _np_power(n, base_rate, effect - h_e, alpha)) / (2 * h_e)
    dn = (_np_power(n + h_n, base_rate, effect, alpha) -
          _np_power(n - h_n, base_rate, effect, alpha)) / (2 * h_n)
    return PowerResult(
        power=_np_power(n, base_rate, effect, alpha),
        d_power_d_effect=de,
        d_power_d_n=dn,
        backend="numpy-finite-diff",
    )


# ---------------------------------------------------------------------------
# torch path
# ---------------------------------------------------------------------------
def _torch_power_result(n: float, base_rate: float, effect: float, alpha: float) -> PowerResult:
    import torch

    eff = torch.tensor(float(effect), dtype=torch.float64, requires_grad=True)
    nn = torch.tensor(float(n), dtype=torch.float64, requires_grad=True)
    p1 = torch.tensor(float(base_rate), dtype=torch.float64)
    p2 = torch.clamp(p1 + eff, 1e-6, 1 - 1e-6)
    pbar = 0.5 * (p1 + p2)
    sd0 = torch.sqrt(torch.clamp(2 * pbar * (1 - pbar), min=1e-12))
    sd1 = torch.sqrt(torch.clamp(p1 * (1 - p1) + p2 * (1 - p2), min=1e-12))
    z_alpha = torch.special.ndtri(torch.tensor(1 - alpha / 2, dtype=torch.float64))
    arg = (torch.sqrt(torch.clamp(nn, min=1e-9)) * torch.abs(eff) - z_alpha * sd0) / sd1
    power = torch.special.ndtr(arg)
    power.backward()
    return PowerResult(
        power=float(power.detach()),
        d_power_d_effect=float(eff.grad),
        d_power_d_n=float(nn.grad),
        backend="torch-autograd",
    )


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def power_with_gradients(
    n_per_arm: float,
    base_rate: float,
    effect: float,
    alpha: float = 0.05,
    use_torch: bool = True,
) -> PowerResult:
    """Statistical power and its gradients w.r.t. effect size and sample size."""
    if _torch_available(use_torch):
        try:
            return _torch_power_result(n_per_arm, base_rate, effect, alpha)
        except Exception:
            pass
    return _np_power_result(n_per_arm, base_rate, effect, alpha)


def optimize_min_detectable_effect(
    n_per_arm: float,
    base_rate: float,
    target_power: float = 0.8,
    alpha: float = 0.05,
    use_torch: bool = True,
    steps: int = 600,
    lr: float = 5e-3,
    max_step: float = 5e-4,
) -> float:
    """Gradient-descend the effect size to just hit target power at fixed n.

    Demonstrates differentiable-programming design optimization: we minimize
    ``(power(effect) - target)^2`` w.r.t. effect using the analytic/autograd
    gradient of power. The per-iteration step is capped so we never leap into the
    saturated region (power->1) where the gradient vanishes and descent stalls.
    """
    effect = max(1e-3, base_rate * 0.05)
    upper = 1 - base_rate - 1e-4
    for _ in range(steps):
        res = power_with_gradients(n_per_arm, base_rate, effect, alpha, use_torch)
        grad_loss = 2 * (res.power - target_power) * res.d_power_d_effect
        step = lr * grad_loss
        step = max(-max_step, min(max_step, step))  # trust-region clamp
        effect -= step
        effect = min(max(effect, 1e-4), upper)
        if abs(res.power - target_power) < 1e-3:
            break
    return effect


def two_proportion_ztest(
    control_n: int, control_conv: int, treatment_n: int, treatment_conv: int
) -> dict:
    """Non-differentiable observed-result test (for reporting alongside design math)."""
    p1 = control_conv / max(control_n, 1)
    p2 = treatment_conv / max(treatment_n, 1)
    pooled = (control_conv + treatment_conv) / max(control_n + treatment_n, 1)
    se = math.sqrt(max(pooled * (1 - pooled) * (1 / max(control_n, 1) + 1 / max(treatment_n, 1)), 1e-12))
    z = (p2 - p1) / se if se > 0 else 0.0
    p_value = 2 * (1 - _np_normal_cdf(abs(z)))
    return {
        "control_rate": p1,
        "treatment_rate": p2,
        "abs_effect": p2 - p1,
        "rel_effect": (p2 - p1) / p1 if p1 > 0 else 0.0,
        "z": z,
        "p_value": p_value,
        "significant": p_value < 0.05,
    }
