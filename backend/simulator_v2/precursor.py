"""Precursor metadata and groups (spec section 7). Sidecar-only evaluation metadata."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import constants as C

ORDER = ("mem", "disk", "cpu", "rx", "tx")          # fixed tie order
SIG_ORDER = {"mem": "mem", "disk_root": "disk", "disk_data": "disk", "cpu": "cpu", "rx": "rx", "tx": "tx"}
DT = C.LATENT_DT


@dataclass
class PrecursorInfo:
    precursor_start_i: int | None
    lead_s: int | None
    signal: str | None
    by_signal: dict
    group: str | None            # incidents only
    silent_reason: str | None


def contiguous_run(exceed: np.ndarray, t_ref_k: int) -> tuple[int, int] | None:
    """Maximal run of True containing index t_ref_k, as (first, last) or None."""
    if t_ref_k >= exceed.size or not exceed[t_ref_k]:
        return None
    a = t_ref_k
    while a > 0 and exceed[a - 1]:
        a -= 1
    e = t_ref_k
    while e + 1 < exceed.size and exceed[e + 1]:
        e += 1
    return a, e


def signal_run_start(delta: np.ndarray, sigma: float, t_ref_k: int) -> int | None:
    """Window index of the first point of a qualifying run (elapsed >= 60 s), else None."""
    run = contiguous_run(np.abs(delta) >= C.PRECURSOR_SIGMA * sigma, t_ref_k)
    if run is None:
        return None
    a, e = run
    return a if (e - a) * DT >= 60 else None


def derive_precursor(fault, profile, incident: bool) -> PrecursorInfo:
    """fault: FaultSim; profile: HostProfile (noise floors)."""
    if fault.family == "silent_crash":
        return PrecursorInfo(None, None, None, {}, "SILENT" if incident else None, "by_construction")
    t_ref = fault.impact_start_i if incident else fault.peak_i
    k_ref = t_ref - fault.start_i
    best = {}
    for sig, arr in fault.delta.items():
        a = signal_run_start(arr, profile.sigma_marg[sig], k_ref)
        if a is not None:
            best[sig] = fault.start_i + a
    if not best:
        return PrecursorInfo(None, None, None, {}, "SILENT" if incident else None, "sub_noise")
    first_i = min(best.values())
    pick = None
    for name in ORDER:
        for sig, i in best.items():
            if SIG_ORDER[sig] == name and i == first_i:
                pick = sig
                break
        if pick:
            break
    lead = (t_ref - first_i) * DT
    group = None
    if incident:
        group = "PRECURSOR" if lead >= C.PRECURSOR_GROUP_LEAD else "ABRUPT"
    return PrecursorInfo(first_i, lead, pick, {k: v for k, v in best.items()}, group, None)
