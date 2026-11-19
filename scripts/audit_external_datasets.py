"""Read-only audit of external anomaly-detection datasets. Reads raw files, writes only a JSON report (never modifies raw data).

Run isolated, from outside the data directory, because the data is untrusted:
    python -I scripts/audit_external_datasets.py [--root <path>] --out docs/evidence/external_audit.json
--root defaults to the UPTIMETRACE_DATA_ROOT environment variable (e.g. D:/UptimeTrace-ML/external-data).
Facts reported come from the files themselves. Anything a file cannot tell us (sampling interval of SMD, units, meanings) is
reported as null and is NOT filled in from a paper; the decision document cites the paper separately.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import zipfile

import numpy as np
import pandas as pd


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def segments(label: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of 1 as (start, end_exclusive)."""
    x = np.asarray(label).astype(int)
    d = np.diff(np.concatenate([[0], x, [0]]))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def one_dir(root: str, pattern: str) -> str:
    hits = glob.glob(os.path.join(root, pattern))
    assert len(hits) == 1, (pattern, hits)
    return hits[0]


def audit_smd(root: str) -> dict:
    base = one_dir(root, "smd/raw/src/*/ServerMachineDataset")
    machines = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(base, "train", "*.txt")))
    rows, tot_train, tot_test, tot_anom, seg_counts, seg_lens, nan_cells = [], 0, 0, 0, [], [], 0
    const_cols, ranges = 0, []
    interp_ok = True
    for m in machines:
        tr = np.loadtxt(os.path.join(base, "train", m + ".txt"), delimiter=",")
        te = np.loadtxt(os.path.join(base, "test", m + ".txt"), delimiter=",")
        lb = np.loadtxt(os.path.join(base, "test_label", m + ".txt"), delimiter=",")
        with open(os.path.join(base, "interpretation_label", m + ".txt"), encoding="utf-8") as fh:
            interp = fh.read().splitlines()
        nan_cells += int(np.isnan(tr).sum() + np.isnan(te).sum())
        segs = segments(lb)
        seg_counts.append(len(segs))
        seg_lens += [e - s for s, e in segs]
        # interpretation lines look like "start-end:dim,dim,..."; they must cover the same segments as the point labels
        cover = sum(int(l.split(":")[0].split("-")[1]) - int(l.split(":")[0].split("-")[0]) + 1 for l in interp if ":" in l)
        interp_ok &= (len(interp) == len(segs))
        const_cols += int((np.nanstd(tr, axis=0) == 0).sum())
        ranges.append((float(np.nanmin(tr)), float(np.nanmax(tr)), float(np.nanmin(te)), float(np.nanmax(te))))
        rows.append({"machine": m, "train": len(tr), "test": len(te), "dims": tr.shape[1], "anomalies": int(lb.sum()), "segments": len(segs),
                     "interp_lines": len(interp), "interp_points": cover})
        tot_train, tot_test, tot_anom = tot_train + len(tr), tot_test + len(te), tot_anom + int(lb.sum())
    files = sorted(glob.glob(os.path.join(base, "*", "*.txt")))
    return {
        "dataset": "SMD", "machines": len(machines), "dims": sorted({r["dims"] for r in rows}), "train_points": tot_train,
        "test_points": tot_test, "anomaly_points_test": tot_anom, "anomaly_ratio_test": round(tot_anom / tot_test, 4),
        "nan_cells": nan_cells, "constant_columns_in_train_total": const_cols,
        "value_range_train_min_max_test_min_max_over_machines": [min(r[0] for r in ranges), max(r[1] for r in ranges), min(r[2] for r in ranges), max(r[3] for r in ranges)],
        "label_granularity": "point-level 0/1 per row; contiguous runs form anomaly SEGMENTS (interval-level) with a per-segment list of contributing dimensions (interpretation_label)",
        "segments_total": int(sum(seg_counts)), "segment_length_min_median_max": [int(min(seg_lens)), int(np.median(seg_lens)), int(max(seg_lens))],
        "interpretation_lines_equal_segment_count_every_machine": bool(interp_ok),
        "timestamps_in_files": False, "sampling_interval_in_files": None, "feature_names_in_files": False,
        "outage_or_impact_timestamps": False,
        "per_machine": rows,
        "n_files": len(files), "files_sha256_of_first_3": {os.path.relpath(f, base): sha(f) for f in files[:3]},
        "bytes": int(sum(os.path.getsize(f) for f in files)),
    }


def audit_psm(root: str) -> dict:
    d = one_dir(root, "psm/raw/src/*/data")
    tr = pd.read_csv(os.path.join(d, "train.csv"))
    te = pd.read_csv(os.path.join(d, "test.csv"))
    lb = pd.read_csv(os.path.join(d, "test_label.csv"))
    feats = [c for c in tr.columns if c != tr.columns[0]]
    ts = pd.to_numeric(tr[tr.columns[0]], errors="coerce")
    step = np.diff(ts.dropna().to_numpy()[:100000])
    labcol = next(c for c in lb.columns if c != lb.columns[0])
    y = lb[labcol].to_numpy()
    segs = segments(y)
    return {
        "dataset": "PSM", "columns_train": list(tr.columns[:3]) + ["..."], "n_features": len(feats),
        "train_rows": len(tr), "test_rows": len(te), "label_rows": len(lb),
        "label_alignment_ok": bool(len(lb) == len(te)),
        "first_column_name": tr.columns[0], "first_column_is_integer_index_or_time": bool(ts.notna().all()),
        "first_column_step_min_median_max": [float(step.min()), float(np.median(step)), float(step.max())] if len(step) else None,
        "nan_cells_train": int(tr[feats].isna().sum().sum()), "nan_cells_test": int(te[feats].isna().sum().sum()),
        "nan_rows_train": int(tr[feats].isna().any(axis=1).sum()), "nan_rows_test": int(te[feats].isna().any(axis=1).sum()),
        "anomaly_points_test": int(y.sum()), "anomaly_ratio_test": round(float(y.mean()), 4),
        "segments_total": len(segs), "segment_length_min_median_max": [int(min(e - s for s, e in segs)), int(np.median([e - s for s, e in segs])), int(max(e - s for s, e in segs))] if segs else None,
        "label_granularity": "point-level 0/1 per row (contiguous runs = interval segments); no per-dimension attribution",
        "feature_names_meaningful": bool(any(not c.startswith("feature_") for c in feats)), "sample_feature_names": feats[:4],
        "timestamps_real": False, "outage_or_impact_timestamps": False,
        "files_sha256": {f: sha(os.path.join(d, f)) for f in ("train.csv", "test.csv", "test_label.csv")},
        "bytes": int(sum(os.path.getsize(os.path.join(d, f)) for f in ("train.csv", "test.csv", "test_label.csv"))),
    }


def audit_kpi(root: str) -> dict:
    b = one_dir(root, "kpi/raw/src/*")
    out: dict = {"dataset": "AIOps KPI", "files": {}}
    for rel in ("Preliminary_dataset/train.csv", "Preliminary_dataset/test.csv"):
        p = os.path.join(b, rel)
        df = pd.read_csv(p)
        info = {"columns": list(df.columns), "rows": len(df), "kpi_ids": int(df["KPI ID"].nunique()) if "KPI ID" in df else None,
                "sha256": sha(p), "bytes": os.path.getsize(p), "nan_cells": int(df.isna().sum().sum())}
        if "label" in df:
            info["anomaly_ratio"] = round(float(df["label"].mean()), 4)
            segs_n, steps = 0, []
            for _, g in df.sort_values("timestamp").groupby("KPI ID"):
                segs_n += len(segments(g["label"].to_numpy()))
                steps.append(float(np.median(np.diff(g["timestamp"].to_numpy()))))
            info["segments_total"] = segs_n
            info["median_sampling_seconds_min_max_over_kpis"] = [min(steps), max(steps)]
        else:
            info["has_labels"] = False
        out["files"][rel] = info
    for rel in ("Finals_dataset/phase2_train.csv.zip", "Finals_dataset/phase2_ground_truth.hdf.zip"):
        p = os.path.join(b, rel)
        z = zipfile.ZipFile(p)
        out["files"][rel] = {"zip_members": [(i.filename, i.file_size) for i in z.infolist()], "sha256": sha(p), "bytes": os.path.getsize(p)}
    out["label_granularity"] = "point-level 0/1 per timestamp, per KPI series (interval segments by contiguity)"
    out["feature_semantics"] = "single anonymised KPI value per series (univariate); no resource/host semantics in files"
    out["outage_or_impact_timestamps"] = False
    return out


def audit_nab(root: str) -> dict:
    b = one_dir(root, "nab/raw/src/*")
    csvs = sorted(glob.glob(os.path.join(b, "data", "*", "*.csv")))
    with open(os.path.join(b, "labels", "combined_windows.json"), encoding="utf-8") as fh:
        labels = json.load(fh)
    rows, wins = 0, 0
    steps = {}
    for p in csvs:
        df = pd.read_csv(p)
        rows += len(df)
        t = pd.to_datetime(df["timestamp"])
        s = float(np.median(np.diff(t.astype("int64") // 10**9)))
        steps[s] = steps.get(s, 0) + 1
    wins = sum(len(v) for v in labels.values())
    real = [p for p in csvs if "/real" in p.replace("\\", "/")]
    return {
        "dataset": "NAB", "series_files": len(csvs), "real_world_series": len(real), "rows_total": rows, "label_windows_total": wins,
        "series_with_labels": sum(1 for v in labels.values() if v), "median_sampling_seconds_histogram": steps,
        "label_granularity": "anomaly WINDOWS (start,end timestamps) per series, not point labels",
        "feature_semantics": "single univariate value per series from mixed domains (AWS CloudWatch, ad exchange, traffic, tweets, artificial)",
        "outage_or_impact_timestamps": False,
        "bytes": int(sum(os.path.getsize(p) for p in csvs)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("UPTIMETRACE_DATA_ROOT"), help="defaults to $UPTIMETRACE_DATA_ROOT")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not a.root:
        ap.error("--root not given and UPTIMETRACE_DATA_ROOT is not set")
    report = {"smd": audit_smd(a.root), "psm": audit_psm(a.root), "kpi": audit_kpi(a.root), "nab": audit_nab(a.root)}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    for k, v in report.items():
        brief = {kk: vv for kk, vv in v.items() if kk not in ("per_machine", "files", "files_sha256", "files_sha256_of_first_3")}
        print(k.upper(), json.dumps(brief, default=str)[:1400])


if __name__ == "__main__":
    main()
