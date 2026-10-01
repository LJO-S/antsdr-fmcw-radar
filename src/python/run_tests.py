"""
Run every board-free Python test suite and report one line each.

    cd src/python && python run_tests.py [-v] [name ...]

Finds */test_*.py (common, offline, online) and runs each as `python -m`, the way
they are written to run, in its own process: the suites seed global RNGs and
mutate configs, so they must not share one. A suite fails on a non-zero exit;
its full output is printed then (always with -v). Names filter by substring,
e.g. `python run_tests.py recorder`.

Not covered: the HDL testbenches (VUnit, firmware/.../projects/e200/test/run.py)
and anything that needs the board.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent  # the import root


def find_suites() -> list[str]:
    return sorted(
        ".".join(p.relative_to(ROOT).with_suffix("").parts)
        for p in ROOT.glob("*/test_*.py")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the board-free test suites")
    ap.add_argument("-v", "--verbose", action="store_true", help="print all output")
    ap.add_argument("names", nargs="*", help="only suites whose name contains one")
    args = ap.parse_args()

    suites = [
        s for s in find_suites() if not args.names or any(n in s for n in args.names)
    ]
    if not suites:
        print("No test suite matches")
        return 1

    failed = []
    for suite in suites:
        started = time.monotonic()
        r = subprocess.run(
            [sys.executable, "-m", suite], cwd=ROOT, capture_output=True, text=True
        )
        seconds = time.monotonic() - started
        ok = r.returncode == 0
        last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        print(f"{'PASS' if ok else 'FAIL'}  {suite:28s} {seconds:5.1f} s  {last}")
        if not ok or args.verbose:
            print(r.stdout + r.stderr)
        if not ok:
            failed.append(suite)

    print(f"\n{len(suites) - len(failed)}/{len(suites)} suites passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
