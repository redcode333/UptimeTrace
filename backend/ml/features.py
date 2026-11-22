"""As-of feature computation (M3.0 spec 12.2-12.5). Inputs are observation timestamps and VALUES only.

For decision time t an observation is usable iff its envelope timestamp <= t (so buffered observations count only after their
replay arrived). Lookback windows are (t - w, t] for w in 300 / 900 / 3600 s. Missing values are NaN, never zero; counts and
coverage are provided so a model can tell low usage from missing telemetry. No incident, fault or label information is used.
"""
from __future__ import annotations

import numpy as np

VERSION = "1.1"
WINDOWS = (300, 900, 3600)
PCT = ("cpu", "mem", "disk_root", "disk_data")           # percent gauges
NET = ("rx", "tx")                                       # byte counters -> rates
ALL_SERIES = PCT + NET
HISTORY = 6 * 3600.0                                     # c-hat window (spec 12.3)
MIN_INTERVALS = 20
C_BOUNDS = (1.0, 3600.0)
MIN_BASIC, MIN_STD_SLOPE, MIN_SPAN = 3, 5, 0.5           # observations for mean/min/max/delta; for std and slope; slope span share
DISK = ("disk_root", "disk_data")

PCT_WIN = ("n", "cov", "missing", "mean", "max", "min", "std", "slope", "delta")
NET_WIN = ("pairs", "cov", "missing", "rate_mean", "rate_max", "rate_min", "rate_std", "rate_slope", "rate_delta", "resets")


def _registry():
    names, meta = [], {}

    def add(name, series, window, desc, unit):
        if not _supported(name):
            return
        names.append(name)
        meta[name] = {"source": series, "window_s": window, "description": desc, "unit": unit}
    for s in PCT:
        add(f"{s}_last", s, None, "latest available value; NaN if staler than 3 x cadence", "percent")
        add(f"{s}_age_s", s, None, "seconds since the latest available observation", "s")
        add(f"{s}_cadence_s", s, None, "median observation interval over the trailing 6 h (c-hat); NaN if < 20 intervals", "s")
        for w in WINDOWS:
            d = {"n": "observations in window", "cov": "coverage fraction (spec 12.4)", "missing": "1 if fewer than 3 observations",
                 "mean": "mean", "max": "maximum", "min": "minimum", "std": "sample standard deviation (>= 5 obs)",
                 "slope": "least-squares slope per minute (>= 5 obs spanning >= 50% of the window)", "delta": "last minus first observation (>= 3 obs)"}
            for k in PCT_WIN:
                add(f"{s}_{k}_{w}", s, w, d[k], "count" if k == "n" else ("fraction" if k in ("cov", "missing") else ("percent per minute" if k == "slope" else "percent")))
    for s in DISK:
        add(f"{s}_free_last", s, None, "available capacity = 100 - latest utilisation", "percent")
        for w in WINDOWS:
            add(f"{s}_free_min_{w}", s, w, "lowest available capacity in the window = 100 - window max", "percent")
    for s in NET:
        add(f"{s}_rate_last", s, None, "latest valid per-interval rate; NaN if staler than 3 x cadence", "bytes/s")
        add(f"{s}_age_s", s, None, "seconds since the latest available counter observation", "s")
        add(f"{s}_cadence_s", s, None, "median observation interval over the trailing 6 h (c-hat); NaN if < 20 intervals", "s")
        for w in WINDOWS:
            d = {"pairs": "valid consecutive-observation pairs (no reset, interval <= 3 x cadence)", "cov": "coverage fraction (spec 12.4)",
                 "missing": "1 if no valid pair", "rate_mean": "bytes / seconds over valid pairs", "rate_max": "maximum pair rate",
                 "rate_min": "minimum pair rate", "rate_std": "sample std of pair rates (>= 5 pairs)",
                 "rate_slope": "slope of pair rates per minute (>= 5 pairs spanning >= 50% of the window)",
                 "rate_delta": "last minus first pair rate (>= 2 pairs)", "resets": "counter decreases (resets) seen in the window"}
            for k in NET_WIN:
                add(f"{s}_{k}_{w}", s, w, d[k], "count" if k in ("pairs", "resets") else ("fraction" if k in ("cov", "missing") else
                    ("bytes/s per minute" if k == "rate_slope" else "bytes/s")))
    add("elapsed_s", "all", None, "seconds since the first observation of the run", "s")
    add("n_series_stale", "all", None, "number of the six series whose latest observation is older than 3 x cadence (or undefined)", "count")
    return names, meta


# Features that cannot be defined under the protocol's own requirements, so they are not produced.
# A rate standard deviation or slope needs at least 5 valid pairs (MIN_STD_SLOPE), i.e. at least 6 observations inside a 300-second
# window. At 60-second sampling the window holds 5 observations, so this is attainable only through sampling jitter. Measured on the
# dataset: NaN in every one of 355,387 sampled rows for each of these four features. They carry no information and are not produced;
# the 900 and 3600-second rate spreads (which have room for 5 pairs) are unchanged.
UNSUPPORTED = frozenset(f"{s}_{k}_300" for s in NET for k in ("rate_std", "rate_slope"))


def _supported(name: str) -> bool:
    return name not in UNSUPPORTED


NAMES, META = _registry()
INDEX = {n: i for i, n in enumerate(NAMES)}
F = len(NAMES)


def coverage(ta: np.ndarray, tk: float, w: float, g: float) -> float:
    """Spec 12.4: pairs with interval <= g are covered over their span; the stretch after the last observation counts iff
    staleness <= g; the pair straddling the window start is included."""
    lo = tk - w
    j0 = max(0, int(np.searchsorted(ta, lo, side="left")) - 1)
    a, b = ta[j0:-1], ta[j0 + 1:]
    cov = float(np.clip(np.minimum(b, tk) - np.maximum(a, lo), 0, None)[(b - a) <= g].sum()) if a.size else 0.0
    if tk - ta[-1] <= g:
        cov += max(0.0, tk - max(ta[-1], lo))
    return cov / w


def _slope_per_min(x: np.ndarray, y: np.ndarray) -> float:
    xc = (x - x.mean()) / 60.0
    den = float((xc * xc).sum())
    return float((xc * (y - y.mean())).sum() / den) if den > 0 else np.nan


def _available(tau, env, val, tk):
    hi = np.searchsorted(tau, tk, side="right")
    lo = np.searchsorted(tau, tk - HISTORY, side="right")
    se = env[lo:hi]
    m = se <= tk
    if m.all():
        return tau[lo:hi], val[lo:hi]
    return tau[lo:hi][m], val[lo:hi][m]


def compute_features(series: dict, t: np.ndarray) -> np.ndarray:
    """series: {name: (tau_sorted_seconds, env_seconds, values)}; t: decision times (same time axis). Returns float32 (len(t), F)."""
    out = np.full((t.size, F), np.nan, dtype=np.float64)
    first_obs = min((float(v[0][0]) for v in series.values() if v[0].size), default=np.inf)
    for r in range(t.size):
        tk = float(t[r])
        stale = 0
        row = out[r]
        for s in ALL_SERIES:
            tau, env, val = series[s]
            ta, va = _available(tau, env, val, tk)
            cad = np.nan
            if ta.size - 1 >= MIN_INTERVALS:
                cad = min(max(float(np.median(np.diff(ta))), C_BOUNDS[0]), C_BOUNDS[1])
            g = 3.0 * cad
            age = tk - float(ta[-1]) if ta.size else np.nan
            fresh = (not np.isnan(g)) and (not np.isnan(age)) and age <= g
            if not fresh:
                stale += 1
            row[INDEX[f"{s}_age_s"]] = age
            row[INDEX[f"{s}_cadence_s"]] = cad
            if s in PCT:
                if fresh:
                    row[INDEX[f"{s}_last"]] = va[-1]
                    if s in DISK:
                        row[INDEX[f"{s}_free_last"]] = 100.0 - va[-1]
                for w in WINDOWS:
                    j0 = int(np.searchsorted(ta, tk - w, side="right"))
                    wt, wv = ta[j0:], va[j0:]
                    n = wt.size
                    row[INDEX[f"{s}_n_{w}"]] = n
                    row[INDEX[f"{s}_cov_{w}"]] = coverage(ta, tk, w, g) if (ta.size and not np.isnan(g)) else 0.0
                    row[INDEX[f"{s}_missing_{w}"]] = 1.0 if n < MIN_BASIC else 0.0
                    if n >= MIN_BASIC:
                        row[INDEX[f"{s}_mean_{w}"]] = wv.mean()
                        row[INDEX[f"{s}_max_{w}"]] = wv.max()
                        row[INDEX[f"{s}_min_{w}"]] = wv.min()
                        row[INDEX[f"{s}_delta_{w}"]] = wv[-1] - wv[0]
                        if s in DISK:
                            row[INDEX[f"{s}_free_min_{w}"]] = 100.0 - wv.max()
                    if n >= MIN_STD_SLOPE:
                        row[INDEX[f"{s}_std_{w}"]] = wv.std(ddof=1)
                        if wt[-1] - wt[0] >= MIN_SPAN * w:
                            row[INDEX[f"{s}_slope_{w}"]] = _slope_per_min(wt, wv)
            else:
                last_rate = np.nan
                for w in WINDOWS:
                    j0 = int(np.searchsorted(ta, tk - w, side="right"))
                    wt, wv = ta[j0:], va[j0:]
                    row[INDEX[f"{s}_cov_{w}"]] = coverage(ta, tk, w, g) if (ta.size and not np.isnan(g)) else 0.0
                    pairs, resets = 0, 0
                    if wt.size >= 2:
                        dt, dc = np.diff(wt), np.diff(wv)
                        resets = int((dc < 0).sum())                       # a decrease is a reset: that interval has no rate
                        ok = (dt > 0) & (dt <= g) & (dc >= 0) if not np.isnan(g) else np.zeros(dt.size, dtype=bool)
                        pairs = int(ok.sum())
                    row[INDEX[f"{s}_pairs_{w}"]] = pairs
                    row[INDEX[f"{s}_resets_{w}"]] = resets
                    row[INDEX[f"{s}_missing_{w}"]] = 1.0 if pairs < 1 else 0.0
                    if pairs >= 1:
                        r_ = dc[ok] / dt[ok]
                        row[INDEX[f"{s}_rate_mean_{w}"]] = dc[ok].sum() / dt[ok].sum()
                        row[INDEX[f"{s}_rate_max_{w}"]] = r_.max()
                        row[INDEX[f"{s}_rate_min_{w}"]] = r_.min()
                        if pairs >= 2:
                            row[INDEX[f"{s}_rate_delta_{w}"]] = r_[-1] - r_[0]
                        if pairs >= MIN_STD_SLOPE:
                            if f"{s}_rate_std_{w}" in INDEX:
                                row[INDEX[f"{s}_rate_std_{w}"]] = r_.std(ddof=1)
                            tm = ((wt[:-1] + wt[1:]) / 2.0)[ok]
                            if tm[-1] - tm[0] >= MIN_SPAN * w and f"{s}_rate_slope_{w}" in INDEX:
                                row[INDEX[f"{s}_rate_slope_{w}"]] = _slope_per_min(tm, r_)
                        if w == WINDOWS[-1]:
                            te = float(wt[1:][ok][-1])
                            if tk - te <= g:
                                last_rate = float(r_[-1])
                row[INDEX[f"{s}_rate_last"]] = last_rate
        row[INDEX["elapsed_s"]] = tk - first_obs
        row[INDEX["n_series_stale"]] = stale
    return out.astype(np.float32)


def manifest_entries() -> list[dict]:
    return [{"name": n, "dtype": "float32", **META[n]} for n in NAMES]
