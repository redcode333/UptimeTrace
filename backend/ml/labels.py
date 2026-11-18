"""Incident labels (M3.0 spec section 13). The ONLY module that reads incidents.jsonl.

y_H(host, t) = 1 iff a merged incident begins in (t, t + H]. Decision points inside an incident, its 900 s cooldown or its
return ramp (t in [onset, oracle_mask_end)) are excluded. Everything here works on integer microseconds.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
ONE_US = 1_000_000
ACTIVE, COOLDOWN, RETURN_RAMP = 1, 2, 3
PHASE = {0: None, ACTIVE: "active", COOLDOWN: "cooldown", RETURN_RAMP: "return_ramp"}


def to_us(ts: str) -> int:
    return (datetime.fromisoformat(ts.replace("Z", "+00:00")) - _EPOCH) // timedelta(microseconds=1)


@dataclass(frozen=True)
class IncidentView:
    """The only truth the labeler sees (spec 7.4): no precursor fields, no fault parameters."""
    incident_id: str
    onset: int                # microseconds since epoch
    recovery: int
    cooldown_end: int
    oracle_mask_end: int
    group: str                # PRECURSOR | ABRUPT | SILENT


def load_incident_views(dataset_root: Path, run_id: str) -> list[IncidentView]:
    views = []
    with open(Path(dataset_root) / "incidents.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["run_id"] == run_id:
                views.append(IncidentView(r["incident_id"], to_us(r["onset"]), to_us(r["recovery"]), to_us(r["cooldown_end"]),
                                          to_us(r["oracle_mask_end"]), r["group"]))
    return sorted(views, key=lambda v: v.onset)


def load_all_incident_views(dataset_root: Path) -> dict[str, list[IncidentView]]:
    out: dict[str, list[IncidentView]] = {}
    with open(Path(dataset_root) / "incidents.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            out.setdefault(r["run_id"], []).append(IncidentView(
                r["incident_id"], to_us(r["onset"]), to_us(r["recovery"]), to_us(r["cooldown_end"]),
                to_us(r["oracle_mask_end"]), r["group"]))
    return {k: sorted(v, key=lambda x: x.onset) for k, v in out.items()}


def incident_counts(dataset_root: Path) -> dict:
    """Merged incidents per split and group (reporting only)."""
    root = Path(dataset_root)
    split_of = {}
    with open(root / "runs.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            split_of[r["run_id"]] = r["split"]
    out: dict = {}
    for sp in set(split_of.values()):
        out[sp] = {"runs": sum(1 for s in split_of.values() if s == sp), "incidents": 0, "PRECURSOR": 0, "ABRUPT": 0, "SILENT": 0}
    with open(root / "incidents.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            e = out[split_of[r["run_id"]]]
            e["incidents"] += 1
            e[r["group"]] += 1
    return out


def active_phase(t_us: np.ndarray, incidents: list[IncidentView]):
    """Oracle active-incident mask: onset <= t < oracle_mask_end for any incident (union, left-closed right-open).

    Returns (phase code per t: 0 none | 1 active | 2 cooldown | 3 return_ramp, owning incident index or -1)."""
    t_us = np.asarray(t_us, dtype=np.int64)          # never trust the caller's dtype (numpy's default int is 32-bit on Windows)
    n = t_us.size
    if not incidents:
        return np.zeros(n, dtype=np.int8), np.full(n, -1, dtype=np.int64)
    onsets = np.array([v.onset for v in incidents])
    ends = np.array([v.oracle_mask_end for v in incidents])
    owner_pref = np.zeros(len(incidents), dtype=np.int64)
    best = 0
    for j in range(len(incidents)):
        if ends[j] > ends[best]:
            best = j
        owner_pref[j] = best
    idx = np.searchsorted(onsets, t_us, side="right") - 1              # latest incident with onset <= t
    has = idx >= 0
    own = np.where(has, owner_pref[np.maximum(idx, 0)], -1)
    live = has & (ends[np.maximum(own, 0)] > t_us)
    phase = np.zeros(n, dtype=np.int8)
    rec = np.array([v.recovery for v in incidents]); cool = np.array([v.cooldown_end for v in incidents])
    o = np.maximum(own, 0)
    phase[live & (t_us < rec[o])] = ACTIVE
    phase[live & (t_us >= rec[o]) & (t_us < cool[o])] = COOLDOWN
    phase[live & (t_us >= cool[o])] = RETURN_RAMP
    return phase, np.where(live, own, -1)


def window_labels(t_us: np.ndarray, incidents: list[IncidentView], horizon_s: int):
    """Per decision time: (n incidents with onset in (t, t+H], any PRECURSOR among them, seconds to the next onset or NaN)."""
    t_us = np.asarray(t_us, dtype=np.int64)
    if not incidents:
        z = np.zeros(t_us.size, dtype=np.int16)
        return z, z.astype(bool), np.full(t_us.size, np.nan)
    onsets = np.array([v.onset for v in incidents])
    is_p = np.array([v.group == "PRECURSOR" for v in incidents], dtype=np.int64)
    cum = np.concatenate([[0], np.cumsum(is_p)])
    lo = np.searchsorted(onsets, t_us, side="right")                   # onset > t
    hi = np.searchsorted(onsets, t_us + horizon_s * ONE_US, side="right")    # onset <= t + H
    nxt = np.where(lo < len(onsets), (onsets[np.minimum(lo, len(onsets) - 1)] - t_us) / ONE_US, np.nan)
    return (hi - lo).astype(np.int16), (cum[hi] - cum[lo]) > 0, nxt


def label_class(n_in_window: np.ndarray, any_precursor: np.ndarray) -> np.ndarray:
    """Spec 13: positive (a PRECURSOR onset in the window) | unscorable (only ABRUPT/SILENT) | negative (none)."""
    return np.where(n_in_window == 0, "negative", np.where(any_precursor, "positive", "unscorable"))
