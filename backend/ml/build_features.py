"""M3.3 feature pipeline:  python -m ml.build_features <dataset_dir> <labels_dir> [--workers N]

Rows = the SCORED decision points of the M3.2 labels (labels are the authority for which (run, decision_index, decision_ts)
exist). Features are horizon independent, so one file per run:
  artifacts/features/<dataset_id>/features/split=<split>/run=<run_id>.parquet
  features_manifest.json
Features use observation timestamps/values only: no incident, fault or target data is read here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import build_labels as bl
from . import features as ft
from .io_safe import write_parquet_verified
from . import labels as lb

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts"
ID_COLUMNS = ["run_id", "host_id", "decision_index", "decision_ts"]
MISSING_BEHAVIOUR = ("Missing values are NaN (float32 NaN, not Arrow null), never zero or imputed. A window statistic is NaN when the "
                     "window has too few observations (mean/min/max/delta < 3, std/slope < 5, slope also needs a span >= 50% of the "
                     "window; rate statistics need valid pairs). `*_n_*`/`*_pairs_*`, `*_cov_*` and `*_missing_*` say how much "
                     "telemetry backed each window; `*_last` is NaN if the latest observation is staler than 3 x the cadence estimate.")


def _read_run_labels(labels_dir: Path, split: str, run_id: str):
    f = labels_dir / "labels" / f"split={split}" / "horizon_s=1800" / f"run={run_id}.parquet"
    t = pq.ParquetFile(f).read(columns=["run_id", "host_id", "decision_index", "decision_ts", "evaluable"])   # no target columns: features only need the evaluable mask
    return t


def features_for_run(args) -> dict:
    ds, labels_dir, out, run = args
    ds, labels_dir, out = Path(ds), Path(labels_dir), Path(out)
    lab = _read_run_labels(labels_dir, run["split"], run["run_id"])
    # Features exist for every EVALUABLE decision (positive, negative and unscorable), and for no excluded decision.
    evaluable = np.array(lab.column("evaluable").to_pylist(), dtype=bool)
    lab = lab.filter(pa.array(evaluable))
    start_us = lb.to_us(run["start_ts"])
    ts_us = np.array(lab.column("decision_ts").cast(pa.int64()).to_pylist(), dtype=np.int64)
    t = (ts_us - start_us) / 1e6
    tbl = pq.read_table(ds / "observations" / f"run={run['run_id']}.parquet", columns=["metric", "labels", "obs_ts", "env_ts", "value"])
    key = np.char.add(np.array(tbl.column("metric").to_pylist()), np.array(tbl.column("labels").to_pylist()))
    obs = (np.array(tbl.column("obs_ts").cast(pa.int64()).to_pylist(), dtype=np.int64) - start_us) / 1e6
    env = (np.array(tbl.column("env_ts").cast(pa.int64()).to_pylist(), dtype=np.int64) - start_us) / 1e6
    val = np.array(tbl.column("value").to_pylist(), dtype=np.float64)
    series = {}
    for s, (m, l) in bl.SERIES_KEY.items():
        sel = key == (m + l)
        o = np.argsort(obs[sel], kind="stable")
        series[s] = (obs[sel][o], env[sel][o], val[sel][o])
    X = ft.compute_features(series, t)
    cols = {"run_id": lab.column("run_id"), "host_id": lab.column("host_id"), "decision_index": lab.column("decision_index"),
            "decision_ts": lab.column("decision_ts")}
    for i, name in enumerate(ft.NAMES):
        cols[name] = pa.array(X[:, i])
    d = out / "features" / f"split={run['split']}"
    digest = write_parquet_verified(pa.table(cols), d / f"run={run['run_id']}.parquet")
    return {"run_id": run["run_id"], "split": run["split"], "rows": int(lab.num_rows), "sha256": digest}


def build_features(dataset_dir: Path, labels_dir: Path, out_root: Path | None = None, workers: int = 3) -> Path:
    ds, labels_dir = Path(dataset_dir), Path(labels_dir)
    lm = json.loads((labels_dir / "labels_manifest.json").read_text(encoding="utf-8"))
    man = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    if lm["source_dataset_id"] != man["dataset_id"]:
        raise ValueError("labels were not built from this dataset")
    runs_all = {json.loads(l)["run_id"]: json.loads(l) for l in open(ds / "runs.jsonl", encoding="utf-8") if l.strip()}
    have = sorted(p.stem.split("=", 1)[1] for p in (labels_dir / "labels").glob("split=*/horizon_s=1800/run=*.parquet"))
    runs = [runs_all[r] for r in have]                       # exactly the runs that were labeled
    out = (out_root or ARTIFACTS / "features") / man["dataset_id"]
    t0 = time.time()
    jobs = [(str(ds), str(labels_dir), str(out), r) for r in runs]
    if workers <= 1:
        results = [features_for_run(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(features_for_run, jobs, chunksize=1))
    files = sorted((out / "features").rglob("*.parquet"))
    manifest = {
        "feature_version": ft.VERSION, "source_dataset_id": man["dataset_id"], "source_labels": str(labels_dir.name),
        "labels_manifest_version": lm["labeler_version"], "lookback_windows_s": list(ft.WINDOWS),
        "source_metrics": {s: bl.SERIES_KEY[s][0] + bl.SERIES_KEY[s][1] for s in ft.ALL_SERIES},
        "as_of_rule": "an observation is used at decision time t iff its envelope timestamp <= t (spec 12.2)",
        "missing_value_behaviour": MISSING_BEHAVIOUR,
        "n_features": ft.F, "n_runs": len(results), "n_rows": sum(r["rows"] for r in results),
        "rows_per_split": {sp: sum(r["rows"] for r in results if r["split"] == sp) for sp in sorted({r["split"] for r in results})},
        "id_columns": ID_COLUMNS, "features": ft.manifest_entries(), "seconds": round(time.time() - t0, 1),
        "size_bytes": sum(p.stat().st_size for p in files),
        "files_sha256": {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    }
    (out / "features_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return out


def load_features(feature_dir: Path, splits=None, columns=None) -> pa.Table:
    """Features for the requested splits only (other splits' files are never opened). Adds a `split` column."""
    base = Path(feature_dir) / "features"
    dirs = sorted(d for d in base.glob("split=*") if splits is None or d.name.split("=", 1)[1] in set(splits))
    if splits is not None and len(dirs) != len(set(splits)):
        raise FileNotFoundError(f"requested splits {sorted(set(splits))} not all present under {base}")
    tables = []
    for d in dirs:
        sp = d.name.split("=", 1)[1]
        for f in sorted(d.glob("run=*.parquet")):
            t = pq.ParquetFile(f).read(columns=columns)
            tables.append(t.append_column("split", pa.array([sp] * t.num_rows, type=pa.string()).dictionary_encode()))
    if not tables:
        raise FileNotFoundError("no feature files match the request")
    return pa.concat_tables(tables)


def _str_np(col) -> np.ndarray:
    """String column -> numpy str array without building a Python list of all rows (dictionary columns use their codes)."""
    a = col.combine_chunks() if hasattr(col, "combine_chunks") else col
    if pa.types.is_dictionary(a.type):
        return np.asarray(a.dictionary.to_pylist())[a.indices.to_numpy(zero_copy_only=False)]
    return np.asarray(a.to_pylist())


def load_xy(feature_dir: Path, labels_dir: Path, splits, horizon_s: int = 1800, drop=(), select=None, runs=None) -> dict:
    """Feature matrix and labels together, row-aligned on (run_id, decision_index).

    Returns {"X": float32 [n, F], "feature_names": [...], "y": int8 [n] (0/1; excluded rows are not present),
             "label_class": [...], "n_in_window": [...], "ids": {run_id, host_id, split, decision_index, decision_ts (int64 us)}}.
    X holds only the manifest's feature columns; ids and targets never enter X.
    drop:   feature names to leave out of X.
    select: optional callable(label_class ndarray, decision_index ndarray) -> bool mask choosing which scored rows to load. The
            mask is applied BEFORE the features are read, so X is filled once, in place, with no second copy (memory).
    runs:   optional run ids to load (streaming evaluation, one run at a time)."""
    base = Path(feature_dir) / "features"
    dirs = sorted(d for d in base.glob("split=*") if splits is None or d.name.split("=", 1)[1] in set(splits))
    if splits is not None and len(dirs) != len(set(splits)):
        raise FileNotFoundError(f"requested splits {sorted(set(splits))} not all present under {base}")
    rs = None if runs is None else set(runs)
    files = [(d.name.split("=", 1)[1], f) for d in dirs for f in sorted(d.glob("run=*.parquet"))
             if rs is None or f.stem.split("=", 1)[1] in rs]
    if not files:
        raise FileNotFoundError("no feature files match the request")
    id_cols = ["run_id", "host_id", "decision_index", "decision_ts"]
    idt = []
    for sp, f in files:
        t = pq.ParquetFile(f).read(columns=id_cols)
        idt.append(t.append_column("split", pa.array([sp] * t.num_rows, type=pa.string())))
    ids = pa.concat_tables(idt)
    labs = bl.load_labels(labels_dir, splits=splits, horizons=[horizon_s], runs=rs)
    # rows = EVALUABLE decisions (positive, negative and unscorable), the same set the feature files were built on.
    # `scored` is the narrower primary view (positive or negative only) and must not define the feature rows.
    sc = labs.column("evaluable").combine_chunks().to_numpy(zero_copy_only=False).astype(bool)
    lab = labs.filter(pa.array(sc))
    fr, lr_ = _str_np(ids.column("run_id")), _str_np(lab.column("run_id"))
    fi = ids.column("decision_index").to_numpy().astype(np.int64)
    li = lab.column("decision_index").to_numpy().astype(np.int64)
    if len(fr) != len(lr_) or not (np.array_equal(fr, lr_) and np.array_equal(fi, li)):
        raise ValueError("feature rows and scored label rows differ")
    if not np.array_equal(ids.column("decision_ts").cast(pa.int64()).to_numpy(), lab.column("decision_ts").cast(pa.int64()).to_numpy()):
        raise ValueError("decision timestamps differ between features and labels")
    lclass = _str_np(lab.column("label_class"))
    keep = np.ones(len(fr), dtype=bool) if select is None else np.asarray(select(lclass, fi), dtype=bool)
    names = [n for n in ft.NAMES if n not in set(drop)]
    X = np.empty((int(keep.sum()), len(names)), dtype=np.float32)
    src = dst = 0
    for sp, f in files:
        t = pq.ParquetFile(f).read(columns=names)
        n = t.num_rows
        m = keep[src:src + n]
        if m.any():
            blk = np.column_stack([t.column(c).to_numpy() for c in names])
            X[dst:dst + int(m.sum())] = blk[m]
            dst += int(m.sum())
        src += n
        del t
    y_all = bl.targets_numpy(lab)
    scored_rows = np.isin(lclass, ["positive", "negative"])
    assert ((y_all >= 0) == scored_rows).all(), "target must be defined exactly on the scored (positive/negative) rows"
    kidx = np.nonzero(keep)[0]
    ids_sel = ids.take(pa.array(kidx))
    return {"X": X, "feature_names": names, "y": y_all[keep], "label_class": lclass[keep],
            "n_in_window": lab.column("n_incidents_in_window").to_numpy()[keep],
            "all_label_class_counts": {k: int((lclass == k).sum()) for k in ("positive", "negative", "unscorable")},
            "ids": {"run_id": fr[keep], "host_id": _str_np(ids_sel.column("host_id")), "split": _str_np(ids_sel.column("split")),
                    "decision_index": fi[keep], "decision_ts": ids_sel.column("decision_ts").cast(pa.int64()).to_numpy()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ml.build_features")
    ap.add_argument("dataset")
    ap.add_argument("labels")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    out = build_features(Path(a.dataset), Path(a.labels), None, a.workers)
    m = json.loads((out / "features_manifest.json").read_text())
    print(f"features written to {out}: {m['n_rows']} rows x {m['n_features']} features, {m['size_bytes']/1e6:.1f} MB, {m['seconds']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
