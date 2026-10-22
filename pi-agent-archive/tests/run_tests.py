#!/usr/bin/env python3
"""Test runner for the PI-quantum tracker.

Usage:
  cd /tmp/pi-tracker  (or wherever tests/ lives) then:
    /root/pi-agent/.venv/bin/python tests/run_tests.py [--fast]

  --fast (default): unit tests against temp dirs + stub embedder (no network,
                    no HF download, no model load).
  With RUN_INTEGRATION=1: also runs live integration checks
                    (real embed against live store, exit-0 segfault gate).

Teardown uses os._exit() to skip flaky LanceDB/pyarrow native finalizers that
segfault (exit 139) at interpreter shutdown even after all tests pass.
"""
import argparse
import gc
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", default=True,
                    help="run only fast unit tests (default)")
    ap.add_argument("--integ", action="store_true",
                    help="also run live integration tests (slow; real embed)")
    args = ap.parse_args()
    if args.integ:
        os.environ["RUN_INTEGRATION"] = "1"

    import unittest
    loader = unittest.TestLoader()
    suite = loader.discover(HERE, pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except BaseException:
        import traceback
        traceback.print_exc()
    # Skip flaky native teardown (LanceDB/pyarrow/torch) that can segfault at
    # interpreter shutdown — same guard as embed_index.py.
    gc.collect()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(rc)