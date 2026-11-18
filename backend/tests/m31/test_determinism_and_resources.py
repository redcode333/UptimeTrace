"""AC-02 determinism (same config twice, 1 vs 2 workers, identical SHA-256 for every file) and the section 19 memory check."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from simulator_v2.generate import generate_dataset

BACKEND = Path(__file__).resolve().parents[2]
PICK = lambda r: r.run_index in (95, 170)          # a val run and an OOD run (slow ramps)


def digests(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            if p.name == "manifest.json":              # wall-clock timings legitimately differ; compare the rest
                m = json.loads(p.read_text())
                m.pop("resources"); m["validation"].pop("footprint_violations", None)
                out[p.name] = hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
            else:
                out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_same_config_twice_and_different_worker_counts_give_identical_files(tmp_path):
    a = generate_dataset(0, tmp_path / "a", workers=1, run_filter=PICK, label="d", validate=False)
    b = generate_dataset(0, tmp_path / "b", workers=2, run_filter=PICK, label="d", validate=False)
    c = generate_dataset(0, tmp_path / "c", workers=2, run_filter=PICK, label="d", validate=False)
    da, db, dc = digests(a), digests(b), digests(c)
    assert da == db == dc
    assert any(k.endswith(".parquet") for k in da) and "faults.jsonl" in da and "protocol.json" in da
    d = generate_dataset(1, tmp_path / "d", workers=1, run_filter=PICK, label="d", validate=False)
    assert digests(d)["observations/run=r0095.parquet"] != da["observations/run=r0095.parquet"]       # the seed matters


MEM_SCRIPT = r"""
import sys, json
sys.path.insert(0, sys.argv[1])
from simulator_v2.alloc import allocate
from simulator_v2.run import prepare_run, render_view
import psutil, pathlib, tempfile
from simulator_v2.writer import write_run_parquet
runs, _ = allocate(0)
n = int(sys.argv[2])
tmp = pathlib.Path(tempfile.mkdtemp())
for i in range(n):
    t = prepare_run(0, runs[60 + i])          # one run at a time: nothing is kept between runs
    out = render_view(t, 60, True)
    write_run_parquet(tmp / f"r{i}.parquet", out.columns)
    del t, out
print(json.dumps({"peak_mb": psutil.Process().memory_info().peak_wset / 2**20}))
"""


def test_peak_memory_for_8_runs_is_at_most_1_5x_the_peak_for_2_runs():
    pytest.importorskip("psutil")
    peaks = {}
    for n in (2, 8):
        r = subprocess.run([sys.executable, "-c", MEM_SCRIPT, str(BACKEND), str(n)], capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stderr[-1500:]
        peaks[n] = json.loads(r.stdout.strip().splitlines()[-1])["peak_mb"]
    print("peak MB", peaks)
    assert peaks[8] <= 1.5 * peaks[2], peaks
