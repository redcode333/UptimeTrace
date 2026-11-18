"""AC-15 / spec section 17 distinctness checks across the master-seed datasets. Reads artifacts only.

The spec says "parameter coefficients of variation exceed fixed minimums" without giving the minimums; this module fixes
CV_MIN = 0.15 for the listed profile parameters (every profile parameter drawn from a U/LogU range has CV >= 0.24).
"""
from __future__ import annotations

import hashlib
import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import audit
from . import constants as C

CV_MIN = 0.15
CV_PARAMS = ("cpu_base", "mem_base", "disk_root_base", "disk_data_base", "s_cpu", "s_mem", "rx_base", "heap_drift",
             "g_gc", "root_growth", "data_growth", "sigma_ou")
CORR_MAX, CORR_SHARE = 0.9, 0.99
JACCARD_MAX = 0.05
CHI2_ALPHA = 0.01


def chi2_uniform(counts) -> tuple[float, float]:
    """Pearson chi-squared goodness of fit against the uniform distribution; returns (statistic, p-value)."""
    counts = np.asarray(counts, dtype=float)
    exp = counts.sum() / counts.size
    stat = float(((counts - exp) ** 2 / exp).sum())
    df = counts.size - 1
    try:
        from scipy.stats import chi2
        return stat, float(chi2.sf(stat, df))
    except Exception:                       # Wilson-Hilferty approximation if scipy is unavailable
        from math import erfc, sqrt
        z = ((stat / df) ** (1 / 3) - (1 - 2 / (9 * df))) / sqrt(2 / (9 * df))
        return stat, 0.5 * erfc(z / sqrt(2))


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def coeff_var(x) -> float:
    x = np.asarray(x, dtype=float)
    return float(x.std() / abs(x.mean()))


def check_distinctness(dirs: list[Path]) -> list[tuple[str, bool, str]]:
    rows = []
    seeds, runs_all, fault_sched, onsets = [], [], {}, {}
    for d in dirs:
        man = json.load(open(d / "manifest.json", encoding="utf-8"))
        seeds.append(man["master_seed"])
        runs = audit.jl(d / "runs.jsonl")
        runs_all.append((man["master_seed"], d, runs))
        by_run = {}
        for f in audit.jl(d / "faults.jsonl"):
            by_run.setdefault(f["run_id"], []).append(f)
        sched = {}
        for r in runs:
            st = audit.us(r["start_ts"])
            sched[r["run_id"]] = sorted((f["family"], f["variant"], f["outcome"], (audit.us(f["start"]) - st) // 10 ** 6)
                                        for f in by_run.get(r["run_id"], []))
        fault_sched[man["master_seed"]] = sched
        runs_by_id = {r["run_id"]: r for r in runs}
        onsets[man["master_seed"]] = {(i["run_id"], (audit.us(i["onset"]) - audit.us(runs_by_id[i["run_id"]]["start_ts"])) // (30 * 10 ** 6))
                                      for i in audit.jl(d / "incidents.jsonl")}
    # 1 no shared run seeds across master seeds
    keys = [(s, tuple(r["seed_spawn_key"])) for s, _, runs in runs_all for r in runs]
    rows.append(("no shared (master seed, run) streams; master seeds distinct", len(set(keys)) == len(keys) and len(set(seeds)) == len(seeds),
                 f"{len(keys)} runs, seeds {sorted(seeds)}"))
    # 2 no duplicate (profile, fault-schedule) hash
    hashes = []
    for s, d, runs in runs_all:
        for r in runs:
            payload = json.dumps([{k: v for k, v in r["profile"].items() if k not in ("run_id", "host_id", "agent_id", "hostname")},
                                  fault_sched[s][r["run_id"]]], sort_keys=True)
            hashes.append(hashlib.sha256(payload.encode()).hexdigest())
    rows.append(("no duplicate (profile, fault-schedule) hash", len(set(hashes)) == len(hashes), f"{len(set(hashes))} unique of {len(hashes)}"))
    # 3 raw CPU series correlate below 0.9 for >= 99% of run pairs
    K = C.RUN_LEN // 60
    mats = []
    for s, d, runs in runs_all:
        for r in runs:
            t = pq.read_table(d / "observations" / f"run={r['run_id']}.parquet", columns=["metric", "obs_ts", "value"])
            m = np.array(t.column("metric").to_pylist()) == "system.cpu.utilization"
            obs = np.array(t.column("obs_ts").cast(pa.int64()).to_pylist(), dtype=np.int64)[m]
            val = np.array(t.column("value").to_pylist(), dtype=float)[m]
            k = np.clip(np.rint((obs - audit.us(r["start_ts"])) / 60e6).astype(int), 0, K - 1)
            row = np.full(K, val.mean())
            row[k] = val
            mats.append(row)
    X = np.array(mats)
    cm = np.corrcoef(X)
    iu = np.triu_indices(len(X), 1)
    share = float((cm[iu] < CORR_MAX).mean())
    rows.append(("raw CPU series: share of run pairs with correlation < 0.9 is >= 99%", share >= CORR_SHARE,
                 f"{share:.5f} of {iu[0].size} pairs; max corr {cm[iu].max():.3f}"))
    # 4 fault times of day vs uniform (host-local hour)
    hours = []
    for s, d, runs in runs_all:
        tz = {r["run_id"]: r["profile"]["tz_offset_h"] for r in runs}
        for i in audit.jl(d / "incidents.jsonl"):
            hours.append(((audit.us(i["onset"]) / 1e6 + tz[i["run_id"]] * 3600) % 86400) // 3600)
    counts = np.bincount(np.array(hours, dtype=int), minlength=24)
    stat, p = chi2_uniform(counts)
    rows.append((f"incident onset local hour is uniform (chi-squared, alpha {CHI2_ALPHA})", p > CHI2_ALPHA,
                 f"chi2 {stat:.1f}, p {p:.3f}, n {len(hours)}"))
    # 5 parameter coefficients of variation
    allp = [r["profile"] for _, _, runs in runs_all for r in runs]
    cvs = {k: coeff_var([p[k] for p in allp]) for k in CV_PARAMS}
    rows.append((f"profile parameter CV >= {CV_MIN} (minimum fixed here; the spec does not give one)", min(cvs.values()) >= CV_MIN,
                 "min " + f"{min(cvs, key=cvs.get)}={min(cvs.values()):.3f}"))
    # 6 Jaccard overlap of onset sets across seeds
    jac = {(a, b): jaccard(onsets[a], onsets[b]) for a, b in combinations(sorted(onsets), 2)}
    rows.append((f"Jaccard overlap of onset sets across seeds < {JACCARD_MAX}", max(jac.values()) < JACCARD_MAX,
                 f"max {max(jac.values()):.5f} over {len(jac)} seed pairs"))
    return rows


def main(argv=None) -> int:
    dirs = [Path(a) for a in (argv or sys.argv[1:])]
    rows = check_distinctness(dirs)
    w = max(len(n) for n, _, _ in rows)
    for n, ok, d in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(w)}  {d}")
    ok = all(r[1] for r in rows)
    print("DISTINCTNESS", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
