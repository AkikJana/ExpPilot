"""Runner script to execute evals suite and test suite in-process and dump report to file."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ["CURSOR_AGENT_BIN"] = "cursor-agent-not-installed"

from evals.run_evals import run_evaluation_suite, print_summary

OUTPUT_FILE = ROOT_DIR / ".agents" / "challenger_m6_2_gen2" / "test_and_eval_results.json"


def main():
    results = {}
    print("--- RUNNING EVALS SUITE ---")
    try:
        eval_report = run_evaluation_suite()
        results["evals_report"] = eval_report
        print_summary(eval_report)
    except Exception as e:
        print(f"Evals suite failed with exception: {e}")
        results["evals_error"] = str(e)

    print("\n--- RUNNING UNIT & ADVERSARIAL TESTS ---")
    suite = unittest.defaultTestLoader.discover(str(ROOT_DIR / "tests"))
    runner = unittest.TextTestRunner(verbosity=2)
    test_result = runner.run(suite)

    results["test_summary"] = {
        "testsRun": test_result.testsRun,
        "wasSuccessful": test_result.wasSuccessful(),
        "errors": [str(e) for e in test_result.errors],
        "failures": [str(f) for f in test_result.failures],
        "skipped": len(test_result.skipped),
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nWrote full results to {OUTPUT_FILE}")
    if not test_result.wasSuccessful():
        sys.exit(1)


if __name__ == "__main__":
    main()
