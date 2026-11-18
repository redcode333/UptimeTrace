"""Development-only cadence fixtures (spec section 18): 6 runs x 3 days with explicit faults, views at 30/60/120 s.

Must run with M31_DEV_RUN_DAYS=3 set before the package is imported (the CLI does this). Never registered as a
dataset id, never loaded by training or reporting code.
"""
from __future__ import annotations

from pathlib import Path

from . import constants as C
from .alloc import RunSpec

DEV_SEED = 987_654_321      # distinct from the primary master seeds 0..4


def dev_runs() -> list[RunSpec]:
    assert C.RUN_DAYS == 3, "dev fixtures need M31_DEV_RUN_DAYS=3"
    plan = [
        ("web_api", [("mem_leak", False), ("silent_crash", True)]),
        ("database", [("disk_fill", False), ("cpu_runaway", False)]),
        ("worker_batch", [("net_flood", False), ("pool_exhaustion", False)]),
        ("queue_consumer", [("silent_crash", False), ("mem_leak", False)]),
        ("web_api", [("cpu_runaway", True), ("disk_fill", False)]),
        ("database", [("net_flood", True), ("pool_exhaustion", False), ("silent_crash", False)]),
    ]
    return [RunSpec(i, f"r{i:04d}", "train", arch, False, prim) for i, (arch, prim) in enumerate(plan)]


def generate_dev_fixture(out_root: Path, cadences=C.CADENCES, workers: int = 6, jitter_on: bool = True) -> list[Path]:
    from .generate import generate_dataset
    roots = []
    for c in cadences:
        roots.append(generate_dataset(DEV_SEED, out_root, workers=workers, cadence=c, jitter_on=jitter_on, kind="dev",
                                      label=f"dev-c{c}", runs_override=dev_runs(), force_one_each=True))
    return roots
