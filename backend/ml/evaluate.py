"""ML evaluation pipeline (M3.x). python -m ml.evaluate <dataset_dir> <features_dir> <labels_dir> [--horizon 1800]
        [--negative-stride 10] [--out DIR]

Fits the three baselines on TRAIN, chooses each threshold on VALIDATION, and writes evaluation.json + report.md with the
validation results. TEST splits are not read: test scoring requires a freeze record (frozen_run.json) in the output directory,
and this revision does not implement test scoring, so a freeze record is never created here.

Rules enforced before anything is fitted (ProtocolError, nothing written):
  * feature and label manifests come from the dataset named in the dataset manifest;
  * feature columns equal the registry (ml.features.NAMES) exactly, in order; labels carry the v2 columns;
  * the hashes of the train and validation files match the manifests (test files are not opened);
  * every run is in the split runs.jsonl assigns it to, no run is in two splits and no host is in two splits.

Targets and eligibility: positive = PRECURSOR onset in (t, t+H]; negative = no onset in (t, t+H]; unscorable (ABRUPT/SILENT
only) and masked/excluded decision points are not evaluated. ABRUPT and SILENT incidents are reported only as a separate
non-primary diagnostic. Preprocessing (median imputation, scaling) is fitted on train rows alone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import build_features as bf
from . import build_labels as bl
from . import features as ft
from . import labels as lb
from .predict import SCHEMA_FILE, RiskModel, load_model
from .train_baselines import EXCLUDED_FEATURES, RULE_FEATURES, choose_threshold

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts"
RESULT_SCHEMA = "uptimetrace.ml.evaluation/1.0"
MODEL_KINDS = ("rule", "logreg", "xgboost")
PROBABILISTIC = ("logreg", "xgboost")
MODEL_INPUTS = [n for n in ft.NAMES if n not in EXCLUDED_FEATURES]
ID_COLUMNS = ("run_id", "host_id", "decision_index", "decision_ts")
LABEL_COLUMNS = ("run_id", "decision_index", "decision_ts", "evaluable", "scored", "target", "label_class")
FREEZE_FILE = "frozen_run.json"
SEED = 7
STRIDE_OFFSET = 2                    # training negatives: decision_index % stride == 2 (same as train_baselines)
DEDUP_S = 900                        # alert suppression after an alert (spec 14.1)
DELTA_S = 60                         # decision cadence
ONE_S = 1_000_000                    # label/time units are microseconds
CALIBRATION_QUANTILES = (50, 80, 90, 95, 98, 99, 99.5, 99.9)
MIN_N_INCIDENTS = 30                 # below this an incident-level proportion is flagged as statistically unsupported


class ProtocolError(RuntimeError):
    """A rule of the evaluation protocol is violated. The pipeline stops before fitting or writing results."""


# ---------------------------------------------------------------------------------------------------- inputs and provenance
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def run_files(feature_dir: Path, split: str) -> list[Path]:
    return sorted((Path(feature_dir) / "features" / f"split={split}").glob("run=*.parquet"))


def _run_id(path: Path) -> str:
    return path.stem.split("=", 1)[1]


def verify_inputs(dataset_dir: Path, feature_dir: Path, labels_dir: Path, horizon_s: int, splits=("train", "val")) -> dict:
    """Verifies manifests, schemas, hashes and split isolation. Any mismatch raises ProtocolError; nothing is quarantined."""
    dataset_dir, feature_dir, labels_dir = Path(dataset_dir), Path(feature_dir), Path(labels_dir)
    man = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    fm = json.loads((feature_dir / "features_manifest.json").read_text(encoding="utf-8"))
    lm = json.loads((labels_dir / "labels_manifest.json").read_text(encoding="utf-8"))
    dsid = man["dataset_id"]
    if fm["source_dataset_id"] != dsid or lm["source_dataset_id"] != dsid:
        raise ProtocolError(f"feature/label manifests were not built from dataset {dsid}")
    if fm["feature_version"] != ft.VERSION:
        raise ProtocolError(f"features are version {fm['feature_version']}, the registry is {ft.VERSION}")
    if lm.get("labeler_version") != bl.LABELER_VERSION:
        raise ProtocolError(f"labels are version {lm.get('labeler_version')}, the labeler is {bl.LABELER_VERSION}")

    names = [f["name"] for f in fm["features"]]
    if names != ft.NAMES:
        raise ProtocolError(f"features manifest does not equal the registry: unexpected {sorted(set(names) - set(ft.NAMES))}, "
                            f"missing {sorted(set(ft.NAMES) - set(names))}, same order: {names == ft.NAMES}")

    for sp in splits:
        for f in run_files(feature_dir, sp):
            feats = [c for c in pq.read_schema(f).names if c not in ID_COLUMNS]
            if feats != ft.NAMES:
                raise ProtocolError(f"{f.name} ({sp}): feature columns differ from the registry: "
                                    f"missing {sorted(set(ft.NAMES) - set(feats))}, unexpected {sorted(set(feats) - set(ft.NAMES))}")
        for f in sorted((labels_dir / "labels" / f"split={sp}" / f"horizon_s={horizon_s}").glob("run=*.parquet")):
            cols = set(pq.read_schema(f).names)
            if not set(LABEL_COLUMNS) <= cols:
                raise ProtocolError(f"{f.name} ({sp}): label columns missing {sorted(set(LABEL_COLUMNS) - cols)}")

    checked = 0
    for rel, expected in fm["files_sha256"].items():
        sp = rel.split("split=", 1)[1].split("/", 1)[0]
        if sp in splits:
            got = sha256_file(feature_dir / rel)
            if got != expected:
                raise ProtocolError(f"hash mismatch for {rel}: manifest {expected[:12]}..., file {got[:12]}...")
            checked += 1
    for rel, expected in lm["files_sha256"].items():
        sp = rel.split("split=", 1)[1].split("/", 1)[0]
        if sp in splits and f"horizon_s={horizon_s}/" in rel:
            got = sha256_file(labels_dir / rel)
            if got != expected:
                raise ProtocolError(f"hash mismatch for {rel}: manifest {expected[:12]}..., file {got[:12]}...")
            checked += 1

    runs = {}
    with open(dataset_dir / "runs.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            runs[r["run_id"]] = (r["split"], r["host_id"])
    split_runs = {sp: sorted(_run_id(f) for f in run_files(feature_dir, sp)) for sp in splits}
    for sp, ids in split_runs.items():
        for rid in ids:
            if runs[rid][0] != sp:
                raise ProtocolError(f"run {rid} is stored under split {sp} but runs.jsonl assigns it to {runs[rid][0]}")
    host_splits: dict[str, set] = {}
    for sp, ids in split_runs.items():
        for rid in ids:
            host_splits.setdefault(runs[rid][1], set()).add(sp)
    shared = sorted(h for h, s in host_splits.items() if len(s) > 1)
    if shared:
        raise ProtocolError(f"hosts appear in more than one split: {shared[:5]}")

    return {"dataset_id": dsid, "dataset_manifest_sha256": sha256_file(dataset_dir / "manifest.json"),
            "features_manifest_sha256": sha256_file(feature_dir / "features_manifest.json"),
            "labels_manifest_sha256": sha256_file(labels_dir / "labels_manifest.json"),
            "feature_registry_version": ft.VERSION, "labeler_version": lm["labeler_version"],
            "registry_features": len(ft.NAMES), "manifest_features": len(names),
            "feature_names_sha256": _sha_json(ft.NAMES), "model_input_features": MODEL_INPUTS,
            "excluded_features": list(EXCLUDED_FEATURES), "label_horizon_s": horizon_s,
            "label_config": lm.get("config"), "file_hashes_checked": checked, "splits_verified": list(splits),
            "split_runs": split_runs, "host_count": {sp: len({runs[r][1] for r in ids}) for sp, ids in split_runs.items()}}


# ---------------------------------------------------------------------------------------------------- data
def train_select(stride: int):
    """Row selector for training: all positives, negatives thinned to every `stride`-th decision point. Unscorable rows
    (ABRUPT/SILENT-only windows) and masked or excluded rows are never selected."""
    def select(lc, di):
        keep = (lc == "positive") | (lc == "negative")
        if stride > 1:
            keep &= (lc == "positive") | (di % stride == STRIDE_OFFSET)
        return keep
    return select


def train_view(feature_dir: Path, labels_dir: Path, horizon_s: int, stride: int, runs=None):
    xy = bf.load_xy(feature_dir, labels_dir, ["train"], horizon_s, drop=(), select=train_select(stride), runs=runs)
    y = (xy["label_class"] == "positive").astype(np.int8)
    return xy, y


def score_split(feature_dir: Path, labels_dir: Path, horizon_s: int, split: str, models: dict[str, RiskModel]) -> dict:
    """Scores every scored row of a split, one run at a time (bounded memory). Only scores and ids are kept."""
    parts = []
    for f in run_files(feature_dir, split):
        xy = bf.load_xy(feature_dir, labels_dir, [split], horizon_s, drop=(), runs=[_run_id(f)])
        rec = {"run_id": xy["ids"]["run_id"], "decision_ts": xy["ids"]["decision_ts"], "label_class": xy["label_class"],
               "y": xy["y"], "n_in_window": xy["n_in_window"]}
        for kind, m in models.items():
            rec[kind] = m.predict_risk(xy["X"], feature_names=ft.NAMES)
        parts.append(rec)
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


# ---------------------------------------------------------------------------------------------------- models
def fit_models(X: np.ndarray, y: np.ndarray, neg_count: int, pos_count: int) -> dict:
    """Fits the two learned models on TRAIN rows. X holds MODEL_INPUTS columns only."""
    import xgboost as xgb
    lr = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                       LogisticRegression(C=0.1, class_weight="balanced", max_iter=2000, random_state=SEED))
    t0 = time.perf_counter()
    lr.fit(X, y)
    lr_s = time.perf_counter() - t0
    params = dict(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.5, min_child_weight=5,
                  reg_lambda=5.0, scale_pos_weight=neg_count / max(pos_count, 1), tree_method="hist", n_jobs=3,
                  random_state=SEED)
    xg = xgb.XGBClassifier(**params)
    t0 = time.perf_counter()
    xg.fit(X, y)
    xg_s = time.perf_counter() - t0
    return {"logreg": {"estimator": lr, "fit_s": round(lr_s, 3), "params": {"C": 0.1, "class_weight": "balanced",
                       "imputer": "median (train only)", "scaler": "standard (train only)", "random_state": SEED}},
            "xgboost": {"estimator": xg, "fit_s": round(xg_s, 3), "params": {k: v for k, v in params.items() if k != "n_jobs"}}}


def model_schema(kind: str, horizon_s: int, threshold: float, extra: dict | None = None) -> dict:
    return {"schema_version": "1.0", "model_kind": kind, "horizon_s": horizon_s, "feature_version": ft.VERSION,
            "feature_names": list(ft.NAMES), "model_input_features": MODEL_INPUTS, "excluded_features": list(EXCLUDED_FEATURES),
            "threshold": float(threshold), "threshold_chosen_on": "val", "preliminary": False, **(extra or {})}


def save_model(out: Path, kind: str, schema: dict, estimator) -> None:
    d = out / "models" / kind
    d.mkdir(parents=True, exist_ok=True)
    if kind == "logreg":
        import joblib
        joblib.dump(estimator, d / "model.joblib")
    elif kind == "xgboost":
        estimator.save_model(str(d / "model.json"))
    (d / SCHEMA_FILE).write_text(json.dumps(schema, indent=1, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------- metrics
def _r(x, n=4):
    return None if x is None else round(float(x), n)


def classification(y: np.ndarray, s: np.ndarray, thr: float) -> dict:
    """Row-level metrics. Undefined values are None, never 0."""
    pred = s >= thr
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    tn = int((~pred & (y == 0)).sum())
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec is not None and rec is not None and prec + rec > 0 else None
    pr_auc = float(average_precision_score(y, s)) if 0 < int(y.sum()) < len(y) else None
    return {"threshold": float(thr), "precision": _r(prec), "recall": _r(rec), "f1": _r(f1), "pr_auc": _r(pr_auc),
            "confusion": {"tn": tn, "fp": fp, "fn": fn, "tp": tp}, "positives": tp + fn, "negatives": fp + tn,
            "prevalence": _r(y.mean()) if len(y) else None}


def calibration(y: np.ndarray, s: np.ndarray, p_train: float, val_scores: np.ndarray) -> dict:
    """Brier score and skill against the constant train prevalence; reliability bins fixed from validation scores."""
    brier = float(np.mean((s - y) ** 2))
    brier_const = float(np.mean((p_train - y) ** 2))
    S = np.sort(val_scores)
    n = len(S)
    edges = np.unique([S[min(n - 1, math.ceil(q / 100.0 * n))] for q in CALIBRATION_QUANTILES])
    edges = edges[edges > S[0]]
    b = np.searchsorted(edges, s, side="right")
    bins, ece = [], 0.0
    for k in range(len(edges) + 1):
        m = b == k
        nk = int(m.sum())
        if nk:
            mp, ob = float(s[m].mean()), float(y[m].mean())
            ece += nk / len(s) * abs(mp - ob)
            bins.append({"bin": k, "n": nk, "mean_predicted": _r(mp), "observed_rate": _r(ob)})
        else:
            bins.append({"bin": k, "n": 0, "mean_predicted": None, "observed_rate": None})
    return {"brier": _r(brier, 6), "brier_constant_prevalence": _r(brier_const, 6),
            "brier_skill": _r(1 - brier / brier_const, 4) if brier_const > 0 else None,
            "calibration_in_the_large": _r(float(s.mean() - y.mean())), "expected_calibration_error": _r(ece),
            "bins": bins, "bin_boundaries_from": "validation scores"}


def alert_events(run_ids: np.ndarray, ts: np.ndarray, scores: np.ndarray, thr: float, label_class: np.ndarray) -> list[tuple]:
    """Deployed-style alerts over ALL evaluable decisions (unscorable included, protocol section 5): per run, a candidate >= thr
    alerts unless one fired in the last 900 s (spec 14.1). Returns (run_id, decision_ts_us, label_class of the decision)."""
    out = []
    for r in np.unique(run_ids):
        sel = np.nonzero(run_ids == r)[0]
        sel = sel[np.argsort(ts[sel], kind="stable")]
        last = None
        for k in sel[scores[sel] >= thr]:
            t = int(ts[k])
            if last is None or t - last >= DEDUP_S * ONE_S:
                out.append((str(r), t, str(label_class[k])))
                last = t
    return out


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


CREDIT_MIN_S = 120          # credit window of a PRECURSOR onset o is [o - H, o - 120 s] (protocol section 5)
OUTCOMES = ("hit", "duplicate", "late", "coincident_unpredictable", "false_alarm")


def classify_alerts(events: list[tuple], metas, horizon_s: int, split_runs: set) -> dict:
    """Protocol section 5 alert outcomes. Stage 1 (PRECURSOR matching): incidents in increasing onset each take the earliest
    unconsumed alert in their credit window. Every other alert is duplicate, late, coincident_unpredictable or false_alarm, in that
    order. Returns {"outcome": [per alert], "credited": {incident_id: (alert index, alert time us)}, "warned": set of incident ids}."""
    H, C = horizon_s * ONE_S, CREDIT_MIN_S * ONE_S
    pre: dict[str, list] = {}
    for m in metas:
        if m.run_id in split_runs and m.group == "PRECURSOR":
            pre.setdefault(m.run_id, []).append(m)
    for v in pre.values():
        v.sort(key=lambda m: m.onset)
    by_run: dict[str, list[int]] = {}
    for i, (r, _, _) in enumerate(events):
        by_run.setdefault(r, []).append(i)
    outcome = [None] * len(events)
    credited: dict[str, tuple] = {}
    for r, idxs in by_run.items():
        idxs.sort(key=lambda i: events[i][1])
        incs = pre.get(r, [])
        consumed = set()
        for m in incs:                                              # stage 1
            lo, hi = m.onset - H, m.onset - C
            for i in idxs:
                t = events[i][1]
                if i in consumed or t < lo:
                    continue
                if t > hi:
                    break
                consumed.add(i)
                outcome[i] = "hit"
                credited[m.incident_id] = (i, t)                  # (alert index, alert decision time in us)
                break
        for i in idxs:
            if i in consumed:
                continue
            _, t, lc = events[i]
            if any(m.onset - H <= t <= m.onset - C and m.incident_id in credited for m in incs):
                outcome[i] = "duplicate"
            elif any(t < m.onset < t + C for m in incs):
                outcome[i] = "late"
            elif lc == "positive":
                raise ProtocolError(f"alert at {r} {t} is labelled positive but matches no PRECURSOR window")
            elif lc == "unscorable":
                outcome[i] = "coincident_unpredictable"
            else:
                if any(t < m.onset <= t + H for m in incs):
                    raise ProtocolError(f"alert at {r} {t} would be a false alarm but a PRECURSOR onset follows")
                outcome[i] = "false_alarm"
    warned = set()                                                  # ABRUPT/SILENT diagnostic: any alert in [o-H, o-C]
    for m in metas:
        if m.run_id in split_runs and m.group != "PRECURSOR":
            for i in by_run.get(m.run_id, []):
                if m.onset - H <= events[i][1] <= m.onset - C:
                    warned.add(m.incident_id)
                    break
    return {"outcome": outcome, "credited": credited, "warned": warned}


def alert_summary(outcomes: list[str], host_days: float) -> dict:
    """Strict accounting (protocol sections 5 and 7.4): the budget false alarms include coincident_unpredictable alerts."""
    n = len(outcomes)
    c = {o: outcomes.count(o) for o in OUTCOMES}
    strict_fa = c["false_alarm"] + c["coincident_unpredictable"]
    hits = c["hit"]
    return {"alerts": n, "outcomes": c, "strict_false_alarms": strict_fa, "spec_false_alarms": c["false_alarm"],
            "precision_strict": _r(hits / n) if n else None,
            "precision_lenient": _r((hits + c["duplicate"] + c["late"]) / n) if n else None,
            "evaluable_host_days": round(host_days, 3),
            "strict_false_alarms_per_host_day": _r(strict_fa / host_days, 3) if host_days else None,
            "spec_false_alarms_per_host_day": _r(c["false_alarm"] / host_days, 3) if host_days else None,
            "alerts_per_host_day": _r(n / host_days, 3) if host_days else None}


def incident_block(metas, cls: dict, split_runs: set) -> dict:
    """Primary (PRECURSOR): detected iff stage-1 matched; lead = onset - credited alert time. ABRUPT and SILENT are never detected;
    their coincident warning is a labelled diagnostic only (protocol sections 6 and 7.9). The seeded chance baseline (7.9) is NOT
    implemented in this revision."""
    rows = []
    for m in metas:
        if m.run_id not in split_runs:
            continue
        if m.group == "PRECURSOR":
            hit = cls["credited"].get(m.incident_id)
            rows.append({"family": m.family, "group": m.group, "detected": hit is not None,
                         "lead_s": (m.onset - hit[1]) / ONE_S if hit else None, "warned": None})
        else:
            rows.append({"family": m.family, "group": m.group, "detected": False, "lead_s": None,
                         "warned": m.incident_id in cls["warned"]})

    def summarise(rs):
        n = len(rs)
        k = sum(r["detected"] for r in rs)
        leads = np.array([r["lead_s"] for r in rs if r["detected"]], dtype=float)
        lead = ({"median_s": round(float(np.median(leads)), 1), "p10_s": round(float(np.percentile(leads, 10)), 1),
                 "p90_s": round(float(np.percentile(leads, 90)), 1), "min_s": round(float(leads.min()), 1),
                 "max_s": round(float(leads.max()), 1)} if leads.size else None)
        out = {"incidents": n, "detected": k, "recall": _r(k / n) if n else None, "recall_wilson95": wilson(k, n),
               "lead_time": lead, "statistically_unsupported": bool(n < MIN_N_INCIDENTS)}
        if rs and rs[0]["warned"] is not None:
            w = sum(bool(r["warned"]) for r in rs)
            out["coincidentally_warned_diagnostic_not_success"] = {"count": w, "share": _r(w / n) if n else None}
        return out

    pre = [r for r in rows if r["group"] == "PRECURSOR"]
    fams = {f: summarise([r for r in pre if r["family"] == f]) for f in sorted({r["family"] for r in pre})}
    return {"primary_precursor": {**summarise(pre), "per_family": fams},
            "non_primary_diagnostic": {g: summarise([r for r in rows if r["group"] == g]) for g in ("ABRUPT", "SILENT")}}


# ---------------------------------------------------------------------------------------------------- orchestration
def test_status(out: Path) -> str:
    if (out / FREEZE_FILE).exists():
        return ("freeze record present, but test scoring is not implemented in this revision; no test metrics were produced")
    return ("not evaluated: no freeze record (frozen_run.json) exists. docs/ML_EVALUATION_PROTOCOL.md is marked NOT FROZEN, and no "
            "held-out test scoring has been authorised")


def run(dataset_dir, feature_dir, labels_dir, horizon_s: int = 1800, negative_stride: int = 10, out_dir=None) -> dict:
    t0 = time.perf_counter()
    dataset_dir, feature_dir, labels_dir = Path(dataset_dir), Path(feature_dir), Path(labels_dir)
    prov = verify_inputs(dataset_dir, feature_dir, labels_dir, horizon_s)
    train_runs = prov["split_runs"]["train"]
    out = Path(out_dir) if out_dir else ARTIFACTS / "evaluation" / prov["dataset_id"] / f"h{horizon_s}"
    out.mkdir(parents=True, exist_ok=True)

    tr, ytr = train_view(feature_dir, labels_dir, horizon_s, negative_stride, runs=train_runs)
    full_counts = tr["all_label_class_counts"]
    p_train = full_counts["positive"] / (full_counts["positive"] + full_counts["negative"])
    idx = [ft.NAMES.index(n) for n in MODEL_INPUTS]
    Xtr = tr["X"][:, idx]
    fitted = fit_models(Xtr, ytr, int((ytr == 0).sum()), int((ytr == 1).sum()))
    del Xtr

    # in-memory models for validation scoring; the same estimators are then saved and reloaded for parity checks
    ridx = [ft.NAMES.index(n) for n in RULE_FEATURES]
    mem = {"rule": RiskModel(model_schema("rule", horizon_s, 0.0, {"rule": {"utilisation_features": list(RULE_FEATURES),
           "score": "max(feature)/100, missing counts as 0"}}), None)}
    for kind in PROBABILISTIC:
        mem[kind] = RiskModel(model_schema(kind, horizon_s, 0.0), fitted[kind]["estimator"])
    va = score_split(feature_dir, labels_dir, horizon_s, "val", mem)
    el = va["label_class"] != "unscorable"

    thresholds = {}
    for kind in MODEL_KINDS:
        thr, _ = choose_threshold(va["y"][el], va[kind][el])        # threshold from VALIDATION only
        thresholds[kind] = thr
    for kind in MODEL_KINDS:
        if kind == "rule":
            extra = {"rule": {"utilisation_features": list(RULE_FEATURES), "score": "max(feature)/100, missing counts as 0"}}
        else:
            extra = {f"{kind}_params": fitted[kind]["params"]}
        save_model(out, kind, model_schema(kind, horizon_s, thresholds[kind], extra), fitted[kind]["estimator"] if kind in fitted else None)
    # save/load parity: the reloaded model must reproduce the in-memory validation scores exactly (first validation run)
    parity = {}
    first = run_files(feature_dir, "val")[0]
    xy0 = bf.load_xy(feature_dir, labels_dir, ["val"], horizon_s, drop=(), runs=[_run_id(first)])
    n0 = len(xy0["y"])
    for kind in MODEL_KINDS:
        reloaded = load_model(out / "models" / kind).predict_risk(xy0["X"], feature_names=ft.NAMES)
        parity[kind] = bool(np.array_equal(reloaded, va[kind][:n0]))
        if not parity[kind]:
            raise ProtocolError(f"{kind}: reloaded model does not reproduce the in-memory scores")
    del xy0

    metas = lb.load_incident_meta(dataset_dir)
    val_runs = set(prov["split_runs"]["val"])
    host_days = float(len(va["y"])) * DELTA_S / 86400.0          # exposure = all evaluable decisions, unscorable included
    models_out = {}
    for kind in MODEL_KINDS:
        thr = thresholds[kind]
        s_el, y_el = va[kind][el], va["y"][el]
        ev = alert_events(va["run_id"], va["decision_ts"], va[kind], thr, va["label_class"])
        cls = classify_alerts(ev, metas, horizon_s, val_runs)
        models_out[kind] = {
            "threshold": thr,
            "threshold_rule": "max F1 on validation (ties: higher threshold); validation metrics are therefore optimistic",
            "fit_seconds": fitted[kind]["fit_s"] if kind in fitted else 0.0,
            "params": fitted[kind]["params"] if kind in fitted else {"utilisation_features": list(RULE_FEATURES)},
            "validation": {
                "classification": classification(y_el, s_el, thr),
                "alerts": alert_summary(cls["outcome"], host_days),
                "calibration": (calibration(y_el, s_el, p_train, s_el) if kind in PROBABILISTIC else
                                {"status": "not a probability (rule score is a utilisation ratio); calibration not defined"}),
                "incidents": incident_block(metas, cls, val_runs),
            },
            "model_parity_reload": parity[kind],
        }
    ranked = [(models_out[k]["validation"]["classification"]["pr_auc"], k) for k in MODEL_KINDS]
    ranked = [(p, k) for p, k in ranked if p is not None]
    selected = max(ranked)[1] if ranked else None

    result = {
        "schema": RESULT_SCHEMA,
        "status": "DEVELOPMENT - validation results only. Test splits were not evaluated (no freeze record). Single master seed (0): "
                  "no generator-seed generalisation is claimed.",
        "test_status": {"test_iid": test_status(out), "test_ood": test_status(out)},
        "dataset_id": prov["dataset_id"], "horizon_s": horizon_s, "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": prov,
        "preprocessing": {"fitted_on": "train rows only", "imputation": "median (SimpleImputer, keep_empty_features)",
                          "scaling": "standard (logreg only)", "nan_handling_xgboost": "native"},
        "training": {"split": "train", "negative_stride": negative_stride, "stride_offset": STRIDE_OFFSET, "seed": SEED,
                     "rows_used": int(len(ytr)), "positives_used": int((ytr == 1).sum()), "negatives_used": int((ytr == 0).sum()),
                     "train_full_counts": full_counts, "train_prevalence_full": _r(p_train, 6), "runs": len(prov["split_runs"]["train"]),
                     "unscorable_excluded": full_counts["unscorable"]},
        "validation": {"runs": len(val_runs), "rows_scored": int(len(el)), "rows_evaluable": int(el.sum()),
                       "rows_unscorable_excluded": int((~el).sum()),
                       "host_days_evaluable": round(host_days, 3),
                       "incidents": {g: sum(1 for m in metas if m.run_id in val_runs and m.group == g) for g in ("PRECURSOR", "ABRUPT", "SILENT")}},
        "models": models_out,
        "selected_model": selected,
        "selection_rule": "highest validation PR-AUC over rule, logreg, xgboost; test-free",
        "limitations": [
            "Validation only: no held-out test metric is produced; the freeze protocol (docs/ML_EVALUATION_PROTOCOL.md section 11) is not run.",
            f"Features: {prov['manifest_features']} columns, equal to the registry (version {prov['feature_registry_version']}); "
            f"labels: labeler version {prov['labeler_version']}.",
            f"Training negatives are thinned to every {negative_stride}th decision point (memory); validation uses every evaluable row.",
            "Thresholds are chosen on the same validation split used to report them, so validation precision and recall are optimistic.",
            "One simulator master seed. Results do not show generalisation across generator seeds.",
            "Alerts are per-run deduplicated decision points, not the full match-1 event protocol.",
            "Incident counts in each split are small; proportions carry wide Wilson intervals, and families with n < 30 are flagged.",
            "No isotonic calibrator is fitted; calibration is reported on validation scores with validation-derived bins.",
            "No hyperparameter tuning: XGBoost uses fixed settings and logistic regression uses C = 0.1.",
        ],
        "runtime_seconds": round(time.perf_counter() - t0, 1),
    }
    (out / "evaluation.json").write_text(json.dumps(result, indent=1, sort_keys=True, default=_jsonable), encoding="utf-8")
    (out / "report.md").write_text(render_report(result), encoding="utf-8")
    result["output_dir"] = str(out)
    return result


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not serialisable: {type(o)}")


def render_report(r: dict) -> str:
    L = [f"# Outage-risk evaluation, horizon {r['horizon_s'] // 60} min (DEVELOPMENT, validation only)", "", f"**{r['status']}**", "",
         f"- Test splits: {r['test_status']['test_iid']}.", f"- Evaluated at {r['evaluated_at_utc']}; dataset {r['dataset_id']}.",
         f"- Training: {r['training']['runs']} runs, {r['training']['rows_used']} rows "
         f"({r['training']['positives_used']} positive, {r['training']['negatives_used']} negative after stride "
         f"{r['training']['negative_stride']}); train prevalence (full) {r['training']['train_prevalence_full']}.",
         f"- Validation: {r['validation']['runs']} runs, {r['validation']['rows_evaluable']} evaluable rows, "
         f"{r['validation']['rows_unscorable_excluded']} unscorable rows excluded; incidents {r['validation']['incidents']}.",
         f"- Selected model (highest validation PR-AUC): **{r['selected_model']}**.", "",
         "## Validation, row level and alerts", "",
         "| model | threshold | PR-AUC | precision | recall | F1 | TP | FP | FN | TN | alerts | false alarms / host-day | Brier skill | ECE |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, m in r["models"].items():
        c, a, cal = m["validation"]["classification"], m["validation"]["alerts"], m["validation"]["calibration"]
        cm = c["confusion"]
        L.append(f"| {k} | {m['threshold']:.4f} | {c['pr_auc']} | {c['precision']} | {c['recall']} | {c['f1']} | {cm['tp']} | {cm['fp']} | "
                 f"{cm['fn']} | {cm['tn']} | {a['alerts']} | {a['strict_false_alarms_per_host_day']} | "
                 f"{cal.get('brier_skill', 'n/a')} | {cal.get('expected_calibration_error', 'n/a')} |")
    L += ["", "## Validation, incident level (PRECURSOR; primary)", "",
          "| model | incidents | detected | recall (95% Wilson) | median lead (s) | p10 lead (s) | unsupported (n<30) |", "|---|---|---|---|---|---|---|"]
    for k, m in r["models"].items():
        p = m["validation"]["incidents"]["primary_precursor"]
        lt = p["lead_time"] or {}
        L.append(f"| {k} | {p['incidents']} | {p['detected']} | {p['recall']} {p['recall_wilson95'] or ''} | "
                 f"{lt.get('median_s', 'n/a')} | {lt.get('p10_s', 'n/a')} | {p['statistically_unsupported']} |")
    sel = r["models"].get(r["selected_model"]) if r["selected_model"] else None
    if sel:
        L += ["", f"## Per fault family, selected model ({r['selected_model']}), validation", "",
              "| family | incidents | detected | recall | unsupported |", "|---|---|---|---|---|"]
        for f, v in sel["validation"]["incidents"]["primary_precursor"]["per_family"].items():
            L.append(f"| {f} | {v['incidents']} | {v['detected']} | {v['recall']} | {v['statistically_unsupported']} |")
    L += ["", "## Non-primary diagnostic: ABRUPT and SILENT (not part of the primary score)", ""]
    for k, m in r["models"].items():
        d = m["validation"]["incidents"]["non_primary_diagnostic"]
        L.append(f"- {k}: ABRUPT {d['ABRUPT']['detected']}/{d['ABRUPT']['incidents']} detected, "
                 f"SILENT {d['SILENT']['detected']}/{d['SILENT']['incidents']} detected.")
    L += ["", "## Limitations", ""] + [f"- {x}" for x in r["limitations"]]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ml.evaluate")
    ap.add_argument("dataset")
    ap.add_argument("features")
    ap.add_argument("labels")
    ap.add_argument("--horizon", type=int, default=1800)
    ap.add_argument("--negative-stride", type=int, default=10)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = run(a.dataset, a.features, a.labels, a.horizon, a.negative_stride, a.out)
    print(Path(res["output_dir"], "report.md").read_text(encoding="utf-8"))
    print("written to", res["output_dir"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
