"""Data-sufficiency exclusion (M3.0 spec 12.2-12.6). Uses observation and envelope TIMESTAMPS only, never values.

An observation is available at decision time t iff its envelope timestamp <= t. Reason codes are packed into one int64 bitmask
per decision point:  bit 0 warmup | bit 1+i absent:<series i> | bit 7+i freshness:<series i> | bit 13+3i+j coverage:<series i>:<window j>.
"""
from __future__ import annotations

import numpy as np

SERIES = ("cpu", "mem", "disk_root", "disk_data", "rx", "tx")
WINDOWS = (300, 900, 3600)
MIN_COVERAGE = 0.5
MIN_ELAPSED = 3600.0
HISTORY = 6 * 3600.0
MIN_INTERVALS = 20
C_BOUNDS = (1.0, 3600.0)

BIT_WARMUP = 0


def bit_absent(i): return 1 + i
def bit_fresh(i): return 7 + i
def bit_cov(i, j): return 13 + 3 * i + j


def decode(mask: int) -> list[str]:
    out = []
    if mask >> BIT_WARMUP & 1:
        out.append("warmup")
    for i, s in enumerate(SERIES):
        if mask >> bit_absent(i) & 1:
            out.append(f"absent:{s}")
    for i, s in enumerate(SERIES):
        if mask >> bit_fresh(i) & 1:
            out.append(f"freshness:{s}")
    for i, s in enumerate(SERIES):
        for j, w in enumerate(WINDOWS):
            if mask >> bit_cov(i, j) & 1:
                out.append(f"coverage:{s}:{w}")
    return out


def series_flags(tau: np.ndarray, env: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Reason bits (without the series offset) for one series: returns (len(t), 5) bool = absent, fresh, cov300, cov900, cov3600.

    tau: observation times (s), sorted ascending; env: matching envelope (availability) times; t: decision times (s)."""
    out = np.zeros((t.size, 2 + len(WINDOWS)), dtype=bool)
    hi_all = np.searchsorted(tau, t, side="right")
    lo_all = np.searchsorted(tau, t - HISTORY, side="right")
    for k in range(t.size):
        tk = t[k]
        sub, se = tau[lo_all[k]:hi_all[k]], env[lo_all[k]:hi_all[k]]
        avail = se <= tk
        ta = sub if avail.all() else sub[avail]
        if ta.size == 0:
            out[k, 0] = True                          # absent: no observation in the preceding 6 h -> c-hat undefined
            out[k, 1:] = True
            continue
        d = np.diff(ta)
        if d.size < MIN_INTERVALS:                    # c-hat undefined: freshness and coverage fail
            out[k, 1:] = True
            continue
        c = min(max(float(np.median(d)), C_BOUNDS[0]), C_BOUNDS[1])
        g = 3.0 * c
        stale = tk - ta[-1]
        out[k, 1] = stale > g
        tail_ok = stale <= g
        for j, w in enumerate(WINDOWS):
            lo = tk - w
            j0 = max(0, int(np.searchsorted(ta, lo, side="left")) - 1)    # include the pair straddling the window start
            a, b = ta[j0:-1], ta[j0 + 1:]
            ok = (b - a) <= g
            cov = float(np.clip(np.minimum(b, tk) - np.maximum(a, lo), 0, None)[ok].sum()) if a.size else 0.0
            if tail_ok:
                cov += max(0.0, tk - max(ta[-1], lo))
            out[k, 2 + j] = (cov / w) < MIN_COVERAGE
    return out


def exclusion_masks(series_obs: dict, t: np.ndarray) -> np.ndarray:
    """series_obs: {series: (tau_sorted, env_aligned)}; t: decision times (s since run start). Returns int64 reason bitmask per t."""
    mask = np.zeros(t.size, dtype=np.int64)
    first = np.inf
    for i, s in enumerate(SERIES):
        tau, env = series_obs[s]
        if tau.size:
            first = min(first, float(tau[0]))
        f = series_flags(tau, env, t)
        mask |= f[:, 0].astype(np.int64) << bit_absent(i)
        mask |= f[:, 1].astype(np.int64) << bit_fresh(i)
        for j in range(len(WINDOWS)):
            mask |= f[:, 2 + j].astype(np.int64) << bit_cov(i, j)
    # warm-up: elapsed time since the first available observation must be >= 3600 s (>= passes)
    mask |= ((t - first) < MIN_ELAPSED).astype(np.int64) << BIT_WARMUP
    return mask
