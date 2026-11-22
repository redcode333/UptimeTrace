"""Features: alignment with the labels, completeness, and an as-of leakage test that recomputes sampled features from raw telemetry
using only observations stamped at or before the decision time. A stored feature that does not match its as-of recomputation was
influenced by information from after the decision, which is a hard failure."""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pandas as pd

from . import policy as P
from .reading import try_read
from .timeutil import epoch_seconds
from .report import Report

# Features recomputed for the leakage test: gauge last-value, age and window statistics (windows use the (t-W, t] convention)
LEAK_FEATURES = {
    "cpu": ("system.cpu.utilization", "{}"),
    "mem": ("system.memory.utilization", "{}"),
    "disk_root": ("system.disk.utilization", '{"mount":"/"}'),
}


def _feature_files(features_dir: Path) -> list[Path]:
    return sorted(features_dir.rglob("*.parquet"))


def check_features(features_dir: Path, labels_dir: Path, runs: dict, feature_manifest: dict | None, r: Report) -> dict:
    r.check("features.alignment_and_completeness")
    files = _feature_files(features_dir)
    if len(files) != len(runs):
        r.add("hard", "features", "features.file_count", f"expected {len(runs)} feature files (one per run), found {len(files)}")
    schemas = set()
    id_cols = {"run_id", "host_id", "decision_index", "decision_ts", "split"}
    per_run_rows = {}
    nan_counts: dict[str, int] = {}
    total = 0
    inf_cols: set[str] = set()
    seen_runs = set()
    for path in files:
        run_id = path.stem.split("=", 1)[1]
        seen_runs.add(run_id)
        t = try_read(path, None, r, "features", run_id)
        if t is None:
            continue
        schemas.add(tuple(t.columns))
        feat_cols = [c for c in t.columns if c not in id_cols]
        total += len(t)
        per_run_rows[run_id] = len(t)
        if run_id in runs and t["split"].iloc[0] != runs[run_id]["split"]:
            r.add("hard", "features", "features.split", "feature split disagrees with the run's split", 1, [run_id])
        if t["decision_index"].duplicated().any():
            r.add("hard", "features", "features.duplicate_decision", "duplicate decision index", 1, [run_id])
        vals = t[feat_cols].to_numpy(dtype="float64")
        if np.isinf(vals).any():
            inf_cols.update(c for c, bad in zip(feat_cols, np.isinf(vals).any(axis=0)) if bad)
        for c, n in zip(feat_cols, np.isnan(vals).sum(axis=0)):
            nan_counts[c] = nan_counts.get(c, 0) + int(n)
    missing_runs = sorted(set(runs) - seen_runs)
    if missing_runs:
        r.add("hard", "features", "features.missing_run", "run has no feature file", len(missing_runs), missing_runs)
    if len(schemas) > 1:
        r.add("hard", "features", "features.schema", "feature files do not share one column schema", len(schemas))
    if inf_cols:
        r.add("hard", "features", "features.infinite", "features contain infinite values", len(inf_cols), sorted(inf_cols))
    feature_names = sorted(next(iter(schemas)) if schemas else [])
    feature_names = [c for c in feature_names if c not in id_cols]
    if feature_manifest is not None:
        declared = [f["name"] for f in feature_manifest["features"]]
        if sorted(declared) != sorted(feature_names):
            only_file = sorted(set(feature_names) - set(declared))
            only_manifest = sorted(set(declared) - set(feature_names))
            r.add("hard", "features", "features.manifest_mismatch",
                  f"features differ from the feature manifest (file only: {only_file[:5]}, manifest only: {only_manifest[:5]})")
        else:
            r.check("features.manifest_names")
    all_nan = [c for c in feature_names if nan_counts.get(c, 0) == total and total > 0]
    if all_nan:
        r.add("hard", "features", "features.all_nan", "feature is never defined: it is missing in every row, so it carries no information", len(all_nan), all_nan)
    stats = {"files": len(files), "rows": total, "n_features": len(feature_names),
             "nan_share_by_feature_max": round(max((nan_counts.get(c, 0) / total for c in feature_names), default=0.0), 4) if total else None,
             "features_with_nan_share_over_50pct": sum(1 for c in feature_names if total and nan_counts.get(c, 0) / total > 0.5)}
    r.stats["features"] = stats
    check_alignment(features_dir, labels_dir, runs, r)
    return stats


def check_alignment(features_dir: Path, labels_dir: Path, runs: dict, r: Report) -> None:
    """Feature rows must be exactly the decisions the labels keep: every excluded decision (active incident or insufficient data) has no
    feature row, and every kept decision does."""
    r.check("features.labels_alignment")
    mismatched = []
    for path in _feature_files(features_dir):
        run_id = path.stem.split("=", 1)[1]
        if run_id not in runs:
            continue
        lab_path = next(labels_dir.rglob(f"horizon_s={P.PRIMARY_HORIZON_S}/run={run_id}.parquet"), None)
        if lab_path is None:
            r.add("hard", "features", "features.no_label_file", "features exist for a run with no primary-horizon labels", 1, [run_id])
            continue
        lab = try_read(lab_path, ["decision_index", "exclusion", "decision_ts"], r, "labels", run_id)
        feat = try_read(path, ["decision_index", "decision_ts"], r, "features", run_id)
        if lab is None or feat is None:
            continue
        kept = set(lab.loc[lab["exclusion"].isna(), "decision_index"])
        have = set(feat["decision_index"])
        if kept != have:
            mismatched.append(run_id)
        merged = feat.merge(lab, on="decision_index", suffixes=("_f", "_l"))
        if (merged["decision_ts_f"] != merged["decision_ts_l"]).any():
            mismatched.append(run_id)
    if mismatched:
        r.add("hard", "features", "features.labels_alignment", "feature rows are not exactly the decisions the labels keep", len(set(mismatched)), mismatched)


def _as_of(env_t: np.ndarray, series_v: np.ndarray, obs_t: np.ndarray, t: float, window: float | None, closed_left: bool):
    """The as-of view at decision time t, per the feature manifest's rule.

    Inclusion: an observation is available iff its ENVELOPE time is <= t (leakage-relevant: a sample whose envelope arrives after t
    cannot have been used). Ordering and windows: by OBSERVATION time, because buffered envelopes carry samples taken long before they
    were sent. cpu_age_s is measured from the latest included sample's observation time. Window statistics use (t-W, t] by observation
    time (or [t-W, t] when closed_left).
    """
    inc = env_t <= t
    if not inc.any():
        return None
    o = obs_t[inc]
    v = series_v[inc]
    order = np.argsort(o, kind="stable")
    o, v = o[order], v[order]
    last = len(o) - 1
    out = {"last": v[last], "age": t - o[last]}
    if window is not None:
        lo = t - window
        sel = (o >= lo) if closed_left else (o > lo)
        sel &= o <= t
        vals = v[sel]
        n = len(vals)
        out["n"] = n
        out["mean"] = float(vals.mean()) if n >= 3 else np.nan
        out["max"] = float(vals.max()) if n >= 3 else np.nan
    return out


def check_leakage(raw: Path, features_dir: Path, runs: dict, r: Report, seed: int = 42, all_runs: bool = False) -> dict:
    """Recompute sampled features from raw telemetry (envelope time <= decision time only) and compare with the stored features."""
    r.check("leakage.as_of_recomputation")
    rng = random.Random(seed)
    checked_features: set[str] = set()
    sampled_runs = []
    mismatches: list[str] = []
    future_leaks: list[str] = []
    boundary_checked = 0
    compared = 0
    convention_counts = {"open_left": 0, "closed_left": 0}
    targets = sorted(runs) if all_runs else [
        (sorted(rid for rid, run in runs.items() if run["split"] == split) or [None])[0 if split == "train" else -1]
        for split in P.LEAKAGE_RUNS]
    for run_id in targets:
        if run_id is None:
            continue
        sampled_runs.append(run_id)
        feat_path = next(features_dir.rglob(f"run={run_id}.parquet"))
        feat = try_read(feat_path, None, r, "features", run_id)
        obs = try_read(raw / "observations" / f"run={run_id}.parquet", ["metric", "labels", "value", "env_ts", "obs_ts"], r, "observations", run_id)
        if feat is None or obs is None:
            continue
        feat = feat.set_index("decision_index")
        start = pd.Timestamp(runs[run_id]["start_ts"]).timestamp()
        candidates = [i for i in feat.index if i >= 60]
        pick = sorted(rng.sample(candidates, min(P.LEAKAGE_SAMPLE_PER_RUN, len(candidates))))
        pick = sorted(set(pick) | {candidates[0], candidates[-1]})
        for prefix, (metric, labels) in LEAK_FEATURES.items():
            sel = obs[(obs["metric"] == metric) & (obs["labels"] == labels)].sort_values("env_ts")
            ts_ = epoch_seconds(sel["env_ts"])
            vs = sel["value"].to_numpy(dtype="float64")
            obs_s = epoch_seconds(sel["obs_ts"])
            env_s = ts_
            for i in pick:
                t = start + P.DECISION_OFFSET_S + i * P.DECISION_DELTA_S
                row = feat.loc[i]
                # Boundary test: the first envelope AFTER t must not be the value the stored feature reports, unless that same value
                # was already available at t. Any such match means the feature used information from after its decision time.
                later = np.searchsorted(env_s, t, side="right")
                if 1 <= later < len(vs) and vs[later] != vs[later - 1] and not np.isnan(row[f"{prefix}_last"]):
                    if abs(row[f"{prefix}_last"] - vs[later]) < P.FEATURE_TOLERANCE and abs(row[f"{prefix}_last"] - vs[later - 1]) > P.FEATURE_TOLERANCE:
                        future_leaks.append(f"{run_id}@{i}:{prefix}")
                boundary_checked += 1
                if np.searchsorted(env_s, t, side="right") == 0:
                    continue                      # no telemetry yet at this decision: nothing to compare
                for conv in (False, True):
                    rec = _as_of(env_s, vs, obs_s, t, 300.0, conv)
                    if rec is None:
                        continue
                    checks = {f"{prefix}_last": rec["last"], f"{prefix}_age_s": rec["age"],
                              f"{prefix}_n_300": rec.get("n"), f"{prefix}_mean_300": rec.get("mean"), f"{prefix}_max_300": rec.get("max")}
                    ok = True
                    for name, expected in checks.items():
                        if name not in feat.columns or expected is None:
                            continue
                        stored = row[name]
                        # NaN is the documented "not available" value (too few observations, or the latest sample is stale), so a
                        # stored NaN is not compared. A stored finite value must be reproduced from as-of telemetry.
                        if np.isnan(stored) or np.isnan(expected):
                            continue
                        if abs(stored - expected) > P.FEATURE_TOLERANCE * max(1.0, abs(expected)):
                            ok = False
                            break
                    if ok:
                        convention_counts["closed_left" if conv else "open_left"] += 1
                        checked_features.update(k for k in checks if k in feat.columns)
                        compared += 1
                        break
                else:
                    mismatches.append(f"{run_id}@{i}:{prefix}")
    if future_leaks:
        r.add("hard", "leakage", "leakage.future_sample",
              "a stored feature equals a sample that arrived after its decision time", len(future_leaks), sorted({m.split("@")[0] for m in future_leaks}))
    if mismatches:
        r.add("hard", "leakage", "leakage.as_of_mismatch",
              "a sampled stored feature cannot be reproduced from telemetry stamped at or before its decision time", len(mismatches),
              sorted({m.split("@")[0] for m in mismatches}))
    result = {"sampled_runs": sampled_runs, "decisions_compared": compared, "mismatches": len(mismatches),
              "future_sample_leaks": len(future_leaks), "boundary_checks": boundary_checked,
              "window_convention_matches": convention_counts, "features_checked": sorted(checked_features),
              "not_checked": "rate features (rx/tx), slope/std/delta and free-form windows are not recomputed by this test"}
    r.stats["leakage"] = result
    return result
