"""Anomaly-detection dataset, built from raw telemetry observations (separate from the outage-prediction feature table).

Decision points: one per minute, at second :05 (the simulator's decision grid), across each run's whole span, including fault
impact windows. The outage feature table omits decisions inside faults by design, so it cannot evaluate detection; this
builder does not inherit that exclusion.

Features at a decision point d, from observations with obs_ts in (d - 300 s, d]:
  per metric (cpu, mem, disk_max): last value, age of last value (s), mean, std, min, max and count over 300 s.
Scorable: at least one of cpu/mem has an observation no older than 180 s. Otherwise the point is unscorable (no telemetry).

Label (ground truth from the simulator): 1 if d lies inside any fault impact window of the run, else 0.
Only the requested splits are read; the test splits are never opened.

Run from backend/:  python -m ml.anomaly_features <dataset_dir> <out_dir> <split> [<split> ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ALLOWED_SPLITS = ("train", "val")
DEFAULT_WINDOW_S = 300  # primary window; 60 and 600 are used only for the sensitivity comparison
MAX_AGE_S = 180
METRICS = {"cpu": "system.cpu.utilization", "mem": "system.memory.utilization", "disk": "system.disk.utilization"}


def _epoch(s: str) -> float:
    return np.datetime64(s.rstrip("Z")).astype("datetime64[us]").astype(np.int64) / 1e6


def decision_grid(start_ts: str, end_ts: str) -> np.ndarray:
    """Epoch seconds of every minute at :05 inside [start, end)."""
    s, e = _epoch(start_ts), _epoch(end_ts)
    first = np.ceil((s - 5) / 60) * 60 + 5  # the first :05 second at or after the run start
    return np.arange(first, e, 60.0)


def to_epoch_seconds(ts: pd.Series) -> np.ndarray:
    return (pd.to_datetime(ts, utc=True) - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy(dtype=float)


def series_features(obs_ts: np.ndarray, values: np.ndarray, grid: np.ndarray, prefix: str, window_s: int) -> dict[str, np.ndarray]:
    """Rolling 300 s window features sampled at each decision point, using only observations at or before it."""
    out = {f"{prefix}_{k}": np.full(len(grid), np.nan) for k in ("last", "age_s", "mean300", "std300", "min300", "max300", "count300")}
    if len(obs_ts) == 0:
        return out
    order = np.argsort(obs_ts)
    s = pd.Series(values[order], index=pd.to_datetime(obs_ts[order], unit="s", utc=True)).sort_index()
    roll = s.rolling(f"{window_s}s")
    stats = pd.DataFrame({"mean300": roll.mean(), "std300": roll.std(), "min300": roll.min(), "max300": roll.max(), "count300": roll.count()})
    stats["last"] = s
    idx = pd.to_datetime(grid, unit="s", utc=True)
    # The last observation at or before each decision point (asof), and that observation's own window statistics.
    at = stats.reindex(stats.index.union(idx)).ffill().reindex(idx)
    last_obs_time = pd.Series(s.index, index=s.index).reindex(s.index.union(idx)).ffill().reindex(idx)
    age = (idx - pd.DatetimeIndex(last_obs_time.to_numpy())).total_seconds().to_numpy()
    out[f"{prefix}_last"] = at["last"].to_numpy(dtype=float)
    out[f"{prefix}_age_s"] = age.astype(float)
    for k in ("mean300", "std300", "min300", "max300", "count300"):
        out[f"{prefix}_{k}"] = at[k].to_numpy(dtype=float)
    return out


def build_run(obs: pd.DataFrame, run: dict, faults: list[dict], window_s: int) -> pd.DataFrame:
    grid = decision_grid(run["start_ts"], run["end_ts"])
    cols: dict[str, np.ndarray] = {"run_id": np.array([run["run_id"]] * len(grid)), "host_id": np.array([run["host_id"]] * len(grid)),
                                   "decision_epoch": grid}
    for short, metric in METRICS.items():
        sub = obs[obs["metric"] == metric]
        if short == "disk":
            sub = sub.groupby("obs_ts", as_index=False)["value"].max()  # worst mount at each observation time
        ts = to_epoch_seconds(sub["obs_ts"])
        vals = sub["value"].to_numpy(dtype=float)
        cols.update(series_features(ts, vals, grid, short, window_s))
    y = np.zeros(len(grid), dtype=np.int8)
    for f in faults:
        if not f.get("impact_start") or not f.get("impact_end"):
            continue
        lo, hi = _epoch(f["impact_start"]), _epoch(f["impact_end"])
        y[(grid >= lo) & (grid <= hi)] = 1
    cols["label"] = y
    cols["fault_id"] = np.array([""] * len(grid), dtype=object)
    for f in faults:
        if not f.get("impact_start") or not f.get("impact_end"):
            continue
        lo, hi = _epoch(f["impact_start"]), _epoch(f["impact_end"])
        m = (grid >= lo) & (grid <= hi)
        cols["fault_id"][m] = f["fault_id"]
    fresh = (np.nan_to_num(cols["cpu_age_s"], nan=1e9) <= MAX_AGE_S) | (np.nan_to_num(cols["mem_age_s"], nan=1e9) <= MAX_AGE_S)
    cols["scorable"] = fresh.astype(np.int8)
    return pd.DataFrame(cols)


def main(dataset: Path, out_dir: Path, window_s: int, splits: list[str]) -> None:
    for s in splits:
        assert s in ALLOWED_SPLITS, f"split {s!r} is not allowed"
    faults: dict[str, list[dict]] = {}
    for line in open(dataset / "faults.jsonl", encoding="utf-8"):
        if line.strip():
            f = json.loads(line)
            faults.setdefault(f["run_id"], []).append(f)
    runs = [json.loads(l) for l in open(dataset / "runs.jsonl", encoding="utf-8") if l.strip()]
    for split in splits:
        part = out_dir / f"split={split}"
        part.mkdir(parents=True, exist_ok=True)
        for run in sorted((r for r in runs if r["split"] == split), key=lambda r: r["run_id"]):
            obs = pq.read_table(dataset / "observations" / f"run={run['run_id']}.parquet",
                                columns=["metric", "obs_ts", "value"]).to_pandas()
            df = build_run(obs, run, faults.get(run["run_id"], []), window_s)
            pq.write_table(pa.Table.from_pandas(df, preserve_index=False), part / f"run={run['run_id']}.parquet")
            print(split, run["run_id"], "rows", len(df), "positives", int(df["label"].sum()), "scorable", int(df["scorable"].sum()), flush=True)


if __name__ == "__main__":
    ds, od, w = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    main(ds, od, w, sys.argv[4:])
