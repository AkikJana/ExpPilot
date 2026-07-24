"""CLI: python -m synthgen "<goal>" [--rows N] [--provider auto|local|api|template] ...

Prints the resolved backends, the generated scenario spec, and the analysis
(observed effect + differentiable design power with gradients).
"""
from __future__ import annotations

import argparse
import json
import time

from synthgen.config import SynthGenConfig
from synthgen.engine import SyntheticDataEngine
from synthgen.spec import ScenarioRequest


def main() -> None:
    ap = argparse.ArgumentParser(prog="synthgen", description="ExpPilot synthetic data engine")
    ap.add_argument("goal", nargs="?", default="Increase checkout conversion on the mobile app",
                    help="business goal to design an experiment for")
    ap.add_argument("--rows", type=int, default=None, help="approx total rows to simulate")
    ap.add_argument("--provider", default=None, choices=["auto", "local", "api", "template"])
    ap.add_argument("--device", default=None, choices=["auto", "cpu", "cuda"])
    ap.add_argument("--metric", default="binary", choices=["binary", "continuous"])
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--capabilities", action="store_true", help="print capability report and exit")
    args = ap.parse_args()

    cfg = SynthGenConfig.from_env()
    if args.provider:
        cfg.provider = args.provider
    if args.device:
        cfg.device = args.device
    if args.seed is not None:
        cfg.seed = args.seed
    if args.rows:
        cfg.n_rows = args.rows

    engine = SyntheticDataEngine(cfg)

    if args.capabilities:
        print(json.dumps(engine.describe(), indent=2))
        return

    print("=== resolved backends ===")
    print(json.dumps(engine.describe(), indent=2))

    req = ScenarioRequest(goal=args.goal, hint_metric=args.metric, n_rows=args.rows, seed=args.seed)
    t0 = time.perf_counter()
    result = engine.run(req)
    dt = time.perf_counter() - t0

    print("\n=== scenario spec ===")
    print(json.dumps(result.spec.model_dump(), indent=2, default=str))
    print(f"\n=== simulation ({result.sim.n_rows:,} rows in {dt:.3f}s"
          f" via {result.sim.array_backend}/{result.sim.frame_backend}) ===")
    print("\n=== analysis ===")
    print(json.dumps(result.analysis, indent=2, default=str))


if __name__ == "__main__":
    main()
