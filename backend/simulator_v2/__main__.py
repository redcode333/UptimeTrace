"""python -m simulator_v2 generate --seed 0 [--workers N]  |  audit <dataset_dir>  |  dev-fixture --out DIR"""
from __future__ import annotations

import argparse
import os
import sys
import time


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="simulator_v2")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--seed", type=int, required=True)
    g.add_argument("--workers", type=int, default=None)
    g.add_argument("--out", default=None)
    g.add_argument("--overwrite", action="store_true", help="replace an existing dataset folder with the same id")
    a = sub.add_parser("audit")
    a.add_argument("dataset")
    a.add_argument("--fraction", type=float, default=0.05)
    a.add_argument("--seed", type=int, default=12345)
    d = sub.add_parser("dev-fixture")
    d.add_argument("--out", required=True)
    d.add_argument("--no-jitter", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "dev-fixture":
        os.environ["M31_DEV_RUN_DAYS"] = "3"          # must precede the first import of simulator_v2.constants
        from pathlib import Path
        from .dev_fixture import generate_dev_fixture
        for r in generate_dev_fixture(Path(args.out), jitter_on=not args.no_jitter):
            print("dev fixture:", r)
        return 0
    if args.cmd == "generate":
        from pathlib import Path
        from .generate import generate_dataset
        t = time.time()
        root = generate_dataset(args.seed, Path(args.out) if args.out else None, args.workers, overwrite=args.overwrite)
        print(f"generated {root} in {time.time() - t:.1f}s")
        return 0
    from .audit import run_audit
    return run_audit(args.dataset, args.fraction, args.seed)


if __name__ == "__main__":
    sys.exit(main())
