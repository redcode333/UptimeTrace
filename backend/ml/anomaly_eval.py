"""Anomaly-detection baselines on the raw-telemetry anomaly dataset (ml/anomaly_features.py). Train and validation only.

Detectors (fitted on benign TRAINING rows only):
  isolation_forest, ecod, copod (pyod for ECOD and COPOD)
Baselines:
  fixed_threshold_85  alarm when CPU or memory reaches the product's 85 % warning limit (no fitting)
  zscore_5min_mean    largest |z| of the 5-minute CPU and memory means against benign training statistics
Thresholds for the learned and z-score detectors: the 99th percentile of benign CALIBRATION scores, where the calibration rows are
benign training rows disjoint from the fit rows. No validation row is used to set any threshold.

Run from backend/:  python -m ml.anomaly_eval <anomaly_features_dir> <out_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score, precision_recall_fscore_support

SEED = 0
FIT_ROWS = 50_000
CAL_ROWS = 30_000
Q = 0.99
FIXED_LIMIT = 85.0
CHUNK = 40_000
ALLOWED = ("train", "val")
FEATURES_PREFIX = ("cpu_", "mem_", "disk_")


def load(root: Path, split: str) -> pd.DataFrame:
    assert split in ALLOWED, f"split {split!r} is not allowed"
    parts = sorted((root / f"split={split}").glob("run=*.parquet"))
    return pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith(FEATURES_PREFIX)]


def episodes(alarm: np.ndarray, run: np.ndarray) -> int:
    prev = np.r_[False, alarm[:-1]] & np.r_[False, run[1:] == run[:-1]]
    return int(np.sum(alarm & ~prev))


SILENCE_TAIL_S = 60  # an observation stream that ends more than this before impact_end is a partial stop


def silence_classes(dataset: Path, runs: list[str], faults: list[dict]) -> dict[str, dict]:
    """Classify each incident fault by the simulator's own observation timestamps inside its impact window.

    observable      observations continue to within SILENCE_TAIL_S of impact_end
    silent_partial  observations stop earlier; stop_offset_s = last observation - impact_start
    silent_at_onset no observation at all inside the impact window (telemetry stopped at onset)
    """
    out: dict[str, dict] = {}
    by_run: dict[str, list[dict]] = {}
    for f in faults:
        by_run.setdefault(f["run_id"], []).append(f)
    for run in runs:
        if run not in by_run:
            continue
        obs = pq.read_table(dataset / "observations" / f"run={run}.parquet", columns=["obs_ts"]).to_pandas()
        ts = (obs["obs_ts"] - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy(dtype=float)
        ts = np.sort(ts)
        for f in by_run[run]:
            lo, hi = _epoch(f["impact_start"]), _epoch(f["impact_end"])
            inside = ts[(ts >= lo) & (ts <= hi)]
            if inside.size == 0:
                cls, off = "silent_at_onset", None
            elif inside.max() < hi - SILENCE_TAIL_S:
                cls, off = "silent_partial", float(inside.max() - lo)
            else:
                cls, off = "observable", None
            out[f["fault_id"]] = {"class": cls, "stop_offset_s": off, "observations_in_window": int(inside.size)}
    return out


def evaluate(name: str, score: np.ndarray, tau: float, val: pd.DataFrame, host_days: float, classes: dict) -> dict:
    ok = val["scorable"].to_numpy() == 1
    y = val["label"].to_numpy()[ok]
    s = score[ok]
    alarm = np.zeros(len(val), dtype=bool)
    alarm[ok] = s >= tau
    pred = alarm[ok]
    p, r, f1, _ = precision_recall_fscore_support(y, pred, average="binary", zero_division=0)
    pr_auc = float(average_precision_score(y, s)) if y.any() else float("nan")
    run_codes = pd.factorize(val["run_id"])[0]
    benign_alarm = alarm & (val["label"].to_numpy() == 0)
    fp_episodes = episodes(benign_alarm, run_codes)
    fam_tot: dict[str, int] = {}
    fam_det: dict[str, int] = {}
    leads: list[float] = []
    for fid, grp in val[val["fault_id"] != ""].groupby("fault_id"):
        fam = grp["fault_family"].iloc[0]
        fam_tot[fam] = fam_tot.get(fam, 0) + 1
        hit = alarm[grp.index.to_numpy()]
        if hit.any():
            fam_det[fam] = fam_det.get(fam, 0) + 1
            first = grp["decision_epoch"].to_numpy()[hit].min()
            leads.append(float(first - grp["fault_start"].iloc[0]))
    n_f, n_d = sum(fam_tot.values()), sum(fam_det.values())
    # Silent faults (family silent_crash: the host stops reporting, by construction) are reported separately from
    # faults that leave telemetry in their window. "Observable" = at least one scorable row inside the impact window.
    cls_tot: dict[str, int] = {}
    cls_det: dict[str, int] = {}
    obs_tot = obs_det = silent_tot = silent_det = 0
    for fid, grp in val[val["fault_id"] != ""].groupby("fault_id"):
        is_silent = grp["fault_family"].iloc[0] == "silent_crash"
        observable = bool(grp["scorable"].to_numpy().any())
        detected = bool(alarm[grp.index.to_numpy()].any())
        if is_silent:
            silent_tot += 1
            silent_det += int(detected)
        if observable:
            obs_tot += 1
            obs_det += int(detected)
        c = classes.get(fid, {}).get("class", "unclassified")
        cls_tot[c] = cls_tot.get(c, 0) + 1
        cls_det[c] = cls_det.get(c, 0) + int(detected)
    return {
        "faults_all": n_f, "faults_all_detected": n_d, "fault_recall_all": round(n_d / n_f, 4) if n_f else None,
        "silent_faults": silent_tot, "silent_faults_detected": silent_det,
        "observable_faults": obs_tot, "observable_faults_detected": obs_det,
        "fault_recall_observable": round(obs_det / obs_tot, 4) if obs_tot else None,
        "by_telemetry_class": {k: {"faults": cls_tot[k], "detected": cls_det.get(k, 0), "recall": round(cls_det.get(k, 0) / cls_tot[k], 4)} for k in sorted(cls_tot)},
        "detector": name, "threshold": float(tau), "scored_rows": int(ok.sum()), "positive_rows": int(y.sum()),
        "pr_auc": pr_auc, "precision": float(p), "recall": float(r), "f1": float(f1),
        "tp": int(np.sum(pred & (y == 1))), "fp": int(np.sum(pred & (y == 0))), "fn": int(np.sum(~pred & (y == 1))),
        "benign_alarm_episodes": fp_episodes, "host_days": round(host_days, 2),
        "false_alarm_episodes_per_host_day": round(fp_episodes / host_days, 4),
        "faults": n_f, "faults_detected": n_d, "fault_recall": round(n_d / n_f, 4) if n_f else None,
        "median_lead_s": float(np.median(leads)) if leads else None,
        "per_family": {k: {"faults": fam_tot[k], "detected": fam_det.get(k, 0), "recall": round(fam_det.get(k, 0) / fam_tot[k], 4)}
                       for k in sorted(fam_tot)},
    }


def _epoch(s: str) -> float:
    return np.datetime64(s.rstrip("Z")).astype("datetime64[us]").astype(np.int64) / 1e6


def fault_meta(dataset: Path) -> dict[str, dict]:
    """Family and impact start (epoch s) for each fault with a recorded impact window, from the simulator's fault records."""
    meta = {}
    for line in open(dataset / "faults.jsonl", encoding="utf-8"):
        if line.strip():
            f = json.loads(line)
            if f.get("impact_start") and f.get("impact_end"):
                meta[f["fault_id"]] = {"family": f["family"], "impact_start_epoch": _epoch(f["impact_start"])}
    return meta


def main(root: Path, dataset: Path, out: Path) -> None:
    from pyod.models.copod import COPOD
    from pyod.models.ecod import ECOD

    out.mkdir(parents=True, exist_ok=True)
    tr = load(root, "train")
    va = load(root, "val")
    cols = feature_columns(tr)
    meta = fault_meta(dataset)
    val_runs = sorted(va["run_id"].unique())
    all_faults = [json.loads(l) for l in open(dataset / "faults.jsonl", encoding="utf-8") if l.strip()]
    classes = silence_classes(dataset, list(val_runs), [f for f in all_faults if f["run_id"] in set(val_runs) and f.get("impact_start") and f.get("impact_end")])
    va["fault_family"] = va["fault_id"].map(lambda f: meta.get(f, {}).get("family", ""))
    va["fault_start"] = va["fault_id"].map(lambda f: meta.get(f, {}).get("impact_start_epoch", np.nan))

    ben_tr = tr[(tr["label"] == 0) & (tr["scorable"] == 1)]
    rng = np.random.default_rng(SEED)
    fit_idx = rng.choice(ben_tr.index.to_numpy(), size=min(FIT_ROWS, len(ben_tr)), replace=False)
    rest = np.setdiff1d(ben_tr.index.to_numpy(), fit_idx)
    cal_idx = rng.choice(rest, size=min(CAL_ROWS, len(rest)), replace=False)
    fit = tr.loc[fit_idx, cols]
    med = fit.median()
    X = lambda df: df[cols].fillna(med).fillna(0.0).to_numpy(dtype=np.float64)  # noqa: E731

    Xfit = X(tr.loc[fit_idx])
    Xcal = X(tr.loc[cal_idx])
    Xva_all = X(va)
    ok_va = va["scorable"].to_numpy() == 1
    host_days = float((va.groupby("run_id")["decision_epoch"].agg(lambda s: (s.max() - s.min()) / 86400.0)).sum())

    detectors = {
        "isolation_forest": IsolationForest(n_estimators=200, max_samples=256, random_state=SEED, n_jobs=-1).fit(Xfit),
        "ecod": ECOD().fit(Xfit),
        "copod": COPOD().fit(Xfit),
    }

    def score(name: str, M: np.ndarray) -> np.ndarray:
        d = detectors[name]
        parts = []
        for i in range(0, len(M), CHUNK):
            c = M[i:i + CHUNK]
            parts.append(-d.score_samples(c) if name == "isolation_forest" else d.decision_function(c))
        return np.concatenate(parts)

    results = []
    for name in detectors:
        tau = float(np.quantile(score(name, Xcal), Q))
        s_va = np.full(len(va), -np.inf)
        s_va[ok_va] = score(name, Xva_all[ok_va])
        results.append(evaluate(name, s_va, tau, va, host_days, classes))

    # Fixed threshold: CPU or memory at the 85 % warning limit (uses the last values only).
    f_va = np.full(len(va), -np.inf)
    f_va[ok_va] = np.nanmax(np.column_stack([va["cpu_last"].to_numpy(), va["mem_last"].to_numpy()])[ok_va], axis=1)
    results.append(evaluate("fixed_threshold_85", f_va, FIXED_LIMIT, va, host_days, classes))

    # Z-score on 5-minute means against benign training statistics.
    mu_c, sd_c = ben_tr["cpu_mean300"].mean(), ben_tr["cpu_mean300"].std() + 1e-9
    mu_m, sd_m = ben_tr["mem_mean300"].mean(), ben_tr["mem_mean300"].std() + 1e-9
    z = lambda df: np.nanmax(np.column_stack([np.abs(df["cpu_mean300"] - mu_c) / sd_c, np.abs(df["mem_mean300"] - mu_m) / sd_m]), axis=1)  # noqa: E731
    tau_z = float(np.quantile(z(tr.loc[cal_idx]), Q))
    z_va = np.full(len(va), -np.inf)
    z_va[ok_va] = z(va[ok_va])
    results.append(evaluate("zscore_5min_mean", z_va, tau_z, va, host_days, classes))

    summary = {
        "task": "anomaly detection on raw-telemetry decision points (ground truth: fault impact windows)",
        "features_dir": str(root), "splits_read": list(ALLOWED), "test_splits_opened": False,
        "feature_columns": cols, "fit_rows": int(len(fit_idx)), "calibration_rows": int(len(cal_idx)),
        "train_rows": int(len(tr)), "train_positive_rows": int(tr["label"].sum()),
        "val_rows": int(len(va)), "val_scorable_rows": int(ok_va.sum()), "val_positive_rows": int(va["label"].sum()),
        "val_host_days": round(host_days, 2),
        "threshold_rule": f"{Q:.2f} quantile of benign calibration scores (learned and z-score); {FIXED_LIMIT} % fixed",
        "results": results,
    }
    (out / "anomaly_results.json").write_text(json.dumps(summary, indent=2, default=float), encoding="utf-8")
    for r in results:
        print({k: r[k] for k in ("detector", "pr_auc", "precision", "recall", "f1", "false_alarm_episodes_per_host_day", "fault_recall", "median_lead_s")}, flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
