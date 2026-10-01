#!/usr/bin/env python3
"""
run_all.py - ONE command: self-test the harness, then run every validator over cases.json.

  python run_all.py --base-url https://YOUR-BACKEND.onrender.com
  python run_all.py --base-url ... --cases my_cases.json --split test

Stops before touching the API if the self-test fails. Extra flags are passed through to run_validation.py.
"""
import subprocess, sys
from pathlib import Path

HERE = Path(__file__).parent


def main():
    args = sys.argv[1:]
    if "--base-url" not in args:
        sys.exit("usage: python run_all.py --base-url https://YOUR-BACKEND.onrender.com [--cases cases.json] [other run_validation flags]")
    if "--cases" not in args:
        default = HERE / "cases.json"
        if not default.exists():
            print("== cases.json missing: building ground truth (district crop statistics + boundaries) ==")
            if subprocess.call([sys.executable, str(HERE / "build_ground_truth.py")], cwd=HERE):
                sys.exit("Ground-truth build failed (needs internet access to raw.githubusercontent.com).")
        args += ["--cases", str(default)]
    if "--timeout" not in args: args += ["--timeout", "300"]      # district-sized AOIs can be slow in GEE
    print("== 1/2 self-test ==")
    if subprocess.call([sys.executable, str(HERE / "selftest.py")], cwd=HERE):
        sys.exit("Self-test failed - not calling the API.")
    print("\n== 2/2 validation run ==")
    sys.exit(subprocess.call([sys.executable, str(HERE / "run_validation.py")] + args, cwd=HERE))


if __name__ == "__main__":
    main()
