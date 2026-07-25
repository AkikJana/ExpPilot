import sys
import pytest

if __name__ == "__main__":
    args = [
        "tests/test_stats.py",
        "tests/test_recommender.py",
        "tests/test_api.py",
        "tests/test_lifecycle.py",
        "tests/test_decision.py",
        "-v",
    ]
    exit_code = pytest.main(args)
    sys.exit(exit_code)
