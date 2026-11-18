"""M3.1 tests are self-contained: no database, no Supabase, no app settings."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def runs0():
    from simulator_v2.alloc import allocate
    runs, doc = allocate(0)
    return runs


@pytest.fixture(scope="session")
def truth_cache(runs0):
    """Prepared runs (truth + latent) cached across tests; preparation is ~0.3 s per run."""
    from simulator_v2.run import prepare_run
    cache = {}

    def get(idx: int, force_one_each: bool = False):
        key = (idx, force_one_each)
        if key not in cache:
            cache[key] = prepare_run(0, runs0[idx], force_one_each)
        return cache[key]
    return get


@pytest.fixture(scope="session")
def dev_roots(tmp_path_factory):
    """The section 18 development fixture (6 runs x 3 days, views 30/60/120 s), built by the CLI in a subprocess
    because the 3-day run length is fixed at import time."""
    import os
    reuse = os.environ.get("M31_REUSE_DEV_FIXTURE")           # local iteration aid only; the final run always rebuilds
    if reuse:
        return {c: Path(reuse) / f"dev-c{c}" for c in (30, 60, 120)}
    out = tmp_path_factory.mktemp("dev_fixture")
    r = subprocess.run([sys.executable, "-m", "simulator_v2", "dev-fixture", "--out", str(out)], cwd=BACKEND,
                       capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stderr[-2000:]
    return {c: out / f"dev-c{c}" for c in (30, 60, 120)}
