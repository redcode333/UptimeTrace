"""M3.4 baselines:  python -m ml.train_baselines <features_dir> <labels_dir> [--horizon 1800] [--negative-stride 5] [--out DIR]

Trains a utilisation rule, Logistic Regression and XGBoost on the TRAIN split, picks each model's threshold on the VAL split, and
writes models + schema + a preliminary report. The test splits are never loaded (the only splits ever requested are train and val).

Training eligibility follows the spec's default view (section 13): positive = a PRECURSOR incident begins in (t, t+H];
negative = no incident begins in (t, t+H]; windows whose only onsets are ABRUPT or SILENT ("unscorable") are dropped, and so are
excluded decision points. Nothing is trained on a target the protocol masks.

--negative-stride k keeps every k-th decision point (decision_index % k == 2) among TRAINING negatives and all positives. It exists
only because the full train split (~1.75M rows x 195 features) does not fit this machine's memory with scikit-learn's float64
conversion; adjacent 60 s decision points are near-duplicates. Validation always uses every row. Default 1 = no thinning.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import build_features as bf
from . import features as ft
from . import labels as lb
from .predict import SCHEMA_FILE, load_model

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts"
SPLITS_USED = ("train", "val")                    # test_iid / test_ood are never loaded here
EXCLUDED_FEATURES = ("elapsed_s",)                # absolute position in the run would let a model memorise where its few incidents sit
RULE_FEATURES = ("cpu_mean_300", "mem_last", "disk_root_last", "disk_data_last")
MODEL_VERSION = "1.0"
SEED = 7
STRIDE_OFFSET = 2
DEDUP_ROWS_S = 900                                # alert_dedup (spec section 2)


def eligible_view(label_class) -> tuple[np.ndarray, np.ndarray]:
    """(keep mask, y) for the default training view: positive -> 1, negative -> 0, unscorable dropped."""
    lc = np.asarray(label_class)
    keep = (lc == "positive") | (lc == "negative")
    return keep, (lc[keep] == "positive").astype(np.int8)


def load_view(feature_dir: Path, labels_dir: Path, split: str, horizon_s: int, negative_stride: int = 1, all_scored: bool = False) -> dict:
    """Rows of one split. Training view (default): eligible rows only, negatives optionally thinned. all_scored=True loads every
    scored row (incl. unscorable) so alerts can be evaluated like a deployed system; `eligible` marks the training-view rows."""
    assert split in SPLITS_USED, f"{split} must not be used for training or selection"

    def select(lclass, di):
        if all_scored:
            return np.ones(len(lclass), dtype=bool)
        keep = (lclass == "positive") | (lclass == "negative")
        if negative_stride > 1:
            keep &= (lclass == "positive") | (di % negative_stride == STRIDE_OFFSET)
        return keep

    xy = bf.load_xy(feature_dir, labels_dir, [split], horizon_s, drop=EXCLUDED_FEATURES, select=select)
    lc = np.asarray(xy["label_class"])
    eligible = (lc == "positive") | (lc == "negative")
    y = np.where(eligible, (lc == "positive").astype(np.int8), -1).astype(np.int8)
    runs = np.asarray(xy["ids"]["run_id"])
    idx = xy["ids"]["decision_index"]
    windows = 0                                     # contiguous positive windows ~ PRECURSOR incidents represented
    for r in np.unique(runs):
        pos = np.sort(idx[(runs == r) & (y == 1)])
        windows += int(1 + (np.diff(pos) > 1).sum()) if pos.size else 0
    allc = xy["all_label_class_counts"]
    return {"X": xy["X"], "feature_names": xy["feature_names"], "y": y, "eligible": eligible, "label_class": lc,
            "n_in_window": xy["n_in_window"], "ids": xy["ids"],
            "counts": {"rows_scored": sum(allc.values()), "positive": allc["positive"], "negative": allc["negative"],
                       "unscorable_dropped": allc["unscorable"], "positive_windows": windows, "runs": int(len(np.unique(runs))),
                       "rows_used": int(eligible.sum()), "positive_used": int((y == 1).sum()), "negative_used": int((y == 0).sum())}}


def choose_threshold(y: np.ndarray, score: np.ndarray) -> tuple[float, float]:
    """Threshold that maximises F1 on the given (validation) data; ties go to the higher threshold."""
    prec, rec, thr = precision_recall_curve(y, score)
    f1 = 2 * prec[:-1] * rec[:-1] / np.maximum(prec[:-1] + rec[:-1], 1e-12)
    best = np.nonzero(f1 == f1.max())[0][-1]
    return float(thr[best]), float(f1[best])


def metrics(y: np.ndarray, score: np.ndarray, thr: float) -> dict:
    pred = score >= thr
    tp, fp = int((pred & (y == 1)).sum()), int((pred & (y == 0)).sum())
    fn, tn = int((~pred & (y == 1)).sum()), int((~pred & (y == 0)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"threshold": thr, "precision": round(p, 4), "recall": round(r, 4), "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
            "pr_auc": round(float(average_precision_score(y, score)), 4), "prevalence": round(float(y.mean()), 5),
            "confusion": {"tn": tn, "fp": fp, "fn": fn, "tp": tp}, "false_positives": fp, "false_negatives": fn,
            "positives": int((y == 1).sum()), "negatives": int((y == 0).sum())}


def alert_stats(run_ids, decision_index, score, thr, n_in_window, delta_s: int = 60) -> dict:
    """Deployed-style alerting on ALL scored decision points: the first score >= thr alerts and suppresses candidates for 900 s
    (spec 14.1). An alert is a false alarm iff no merged incident begins in (t, t+H] (spec 14.3). Evaluable exposure = scored
    decision points x delta (spec 14.4). This is not the full match-1 event evaluation (no incident-level recall)."""
    run_ids = np.asarray(run_ids)
    gap = DEDUP_ROWS_S // delta_s
    alerts = false_alarms = 0
    for r in np.unique(run_ids):
        sel = np.nonzero(run_ids == r)[0]
        cand = sel[score[sel] >= thr]
        last = -10 ** 9
        for k in cand:
            d = int(decision_index[k])
            if d - last >= gap:                       # t >= t0 + 900 s fires
                alerts += 1
                false_alarms += int(n_in_window[k] == 0)
                last = d
    host_days = len(run_ids) * delta_s / 86400.0
    return {"alerts": alerts, "false_alarms": false_alarms, "evaluable_host_days": round(host_days, 3),
            "alerts_per_evaluable_host_day": round(alerts / host_days, 2) if host_days else None,
            "false_alarms_per_evaluable_host_day": round(false_alarms / host_days, 2) if host_days else None}


def train_all(feature_dir: Path, labels_dir: Path, out_dir: Path | None = None, horizon_s: int = 1800, negative_stride: int = 1,
              prelim_report: Path | None = None) -> Path:
    import joblib
    import xgboost as xgb
    feature_dir, labels_dir = Path(feature_dir), Path(labels_dir)
    fm = json.loads((feature_dir / "features_manifest.json").read_text(encoding="utf-8"))
    out = (out_dir or ARTIFACTS / "models") / fm["source_dataset_id"] / f"h{horizon_s}"
    tr = load_view(feature_dir, labels_dir, "train", horizon_s, negative_stride)
    va = load_view(feature_dir, labels_dir, "val", horizon_s, 1, all_scored=True)
    names = list(ft.NAMES)
    use = tr["feature_names"]
    assert use == va["feature_names"] == [n for n in names if n not in EXCLUDED_FEATURES]
    Xtr, ytr = tr["X"], tr["y"]
    Xva, yva, el = va["X"], va["y"], va["eligible"]
    pos, neg = tr["counts"]["positive_used"], tr["counts"]["negative_used"]
    ridx = [use.index(n) for n in RULE_FEATURES]
    results = {}

    def finish(kind, fit_s, predict_fn, save_fn, extra=None):
        t0 = time.perf_counter()
        s_va = predict_fn(Xva)
        inf_s = time.perf_counter() - t0
        s_tr = predict_fn(Xtr)
        thr, _ = choose_threshold(yva[el], s_va[el])             # threshold from VALIDATION data only
        mdir = out / kind
        mdir.mkdir(parents=True, exist_ok=True)
        save_fn(mdir)
        schema = {"schema_version": "1.0", "model_version": MODEL_VERSION, "model_kind": kind, "horizon_s": horizon_s,
                  "feature_version": ft.VERSION, "feature_names": names, "model_input_features": use,
                  "excluded_features": list(EXCLUDED_FEATURES), "threshold": thr, "threshold_chosen_on": "val",
                  "training": {"split": "train", "eligibility": "positive=PRECURSOR onset in (t,t+H]; negative=no onset; "
                               "unscorable (ABRUPT/SILENT-only) windows and excluded decision points dropped",
                               "rows": int(len(ytr)), "positives": pos, "negatives": neg, "negative_stride": negative_stride,
                               "positive_windows": tr["counts"]["positive_windows"], "runs": tr["counts"]["runs"],
                               "source_dataset_id": fm["source_dataset_id"]},
                  "preliminary": True, **(extra or {})}
        (mdir / SCHEMA_FILE).write_text(json.dumps(schema, indent=1), encoding="utf-8")
        results[kind] = {"train_seconds": round(fit_s, 3), "inference_seconds_val": round(inf_s, 4),
                         "inference_rows_val": int(len(yva)), "inference_ms_per_1000_rows": round(1e6 * inf_s / len(yva), 3),
                         "val": metrics(yva[el], s_va[el], thr),
                         "val_alerts": alert_stats(va["ids"]["run_id"], va["ids"]["decision_index"], s_va, thr, va["n_in_window"]),
                         "train_at_val_threshold": metrics(ytr, s_tr, thr)}

    # 1. deterministic rule: highest utilisation among the configured signals (no fitting)
    finish("rule", 0.0, lambda X: np.nan_to_num(X[:, ridx], nan=0.0).max(axis=1) / 100.0, lambda d: None,
           {"rule": {"utilisation_features": list(RULE_FEATURES), "score": "max(feature)/100, missing counts as 0"}})

    # 2. logistic regression (train-fit median imputation + scaling; class-balanced)
    lr = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                       LogisticRegression(C=0.1, class_weight="balanced", max_iter=2000, random_state=SEED))
    t0 = time.perf_counter(); lr.fit(Xtr, ytr); fit_s = time.perf_counter() - t0
    finish("logreg", fit_s, lambda X: lr.predict_proba(X)[:, 1], lambda d: joblib.dump(lr, d / "model.joblib"))
    del lr

    # 3. XGBoost (fixed, modest settings; NaN handled natively; no hyper-parameter search)
    params = dict(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.5, min_child_weight=5,
                  reg_lambda=5.0, scale_pos_weight=neg / max(pos, 1), tree_method="hist", n_jobs=3, random_state=SEED,
                  eval_metric="aucpr")
    xg = xgb.XGBClassifier(**params)
    t0 = time.perf_counter(); xg.fit(Xtr, ytr); fit_s = time.perf_counter() - t0
    finish("xgboost", fit_s, lambda X: xg.predict_proba(X)[:, 1], lambda d: xg.save_model(str(d / "model.json")),
           {"xgboost_params": {k: v for k, v in params.items() if k != "n_jobs"}})

    ds = ARTIFACTS / "datasets" / fm["source_dataset_id"]
    inc = lb.incident_counts(ds) if ds.exists() else None
    report = {"status": "PRELIMINARY - synthetic data, single simulator seed; not evidence of generalisation or production readiness",
              "horizon_s": horizon_s, "feature_version": ft.VERSION, "n_features_used": len(use),
              "excluded_features": list(EXCLUDED_FEATURES), "train": tr["counts"], "val": va["counts"],
              "incidents_per_split": {k: inc[k] for k in ("train", "val") if inc and k in inc} if inc else None,
              "negative_stride": negative_stride, "test_splits_used": False,
              "threshold_rule": "max F1 on val (val metrics are therefore optimistic)", "models": results}
    if prelim_report and Path(prelim_report).exists():
        prev = json.loads(Path(prelim_report).read_text(encoding="utf-8"))
        report["preliminary_7run"] = {"train": prev["train"], "val": prev["val"],
                                      "models": {k: {"val": v["val"], "train_seconds": v["train_seconds"]} for k, v in prev["models"].items()}}
    (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out / "report.md").write_text(render_report(report), encoding="utf-8")
    probe = Xva[:50]
    for kind in results:                                         # prove each saved model loads on its own
        full = np.insert(probe, names.index("elapsed_s"), np.nan, axis=1)
        assert np.isfinite(load_model(out / kind).predict_risk(full)).all()
    return out


def render_report(r: dict) -> str:
    tr, va = r["train"], r["val"]
    inc = r.get("incidents_per_split") or {}
    L = [f"# Outage-risk baselines, horizon {r['horizon_s'] // 60} min (PRELIMINARY, synthetic)", "", f"**{r['status']}.**", ""]
    for name, c in (("Train", tr), ("Validation", va)):
        i = inc.get(name.lower()[:5] if name == "Train" else "val")
        L.append(f"- {name}: {c['runs']} runs" + (f", {i['incidents']} merged incidents ({i['PRECURSOR']} PRECURSOR, {i['ABRUPT']} ABRUPT, "
                 f"{i['SILENT']} SILENT)" if i else "") + f"; {c['positive']} positive and {c['negative']} negative rows "
                 f"(~{c['positive_windows']} PRECURSOR windows); {c['unscorable_dropped']} unscorable rows dropped"
                 + (f"; used for fitting: {c['positive_used']} positive / {c['negative_used']} negative" if name == "Train" else "") + ".")
    if r["negative_stride"] > 1:
        L.append(f"- Training negatives thinned to every {r['negative_stride']}th decision point (memory); validation uses every row.")
    L += ["- Threshold per model = max F1 on validation, so validation precision/recall are optimistic. Test splits were not used.",
          f"- Features: {r['n_features_used']} (excluded: {', '.join(r['excluded_features'])}).", "",
          "| model | PR-AUC | precision | recall | F1 | TP | FP | FN | TN | train s | infer ms/1000 rows |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, m in r["models"].items():
        v = m["val"]; c = v["confusion"]
        L.append(f"| {k} | {v['pr_auc']} | {v['precision']} | {v['recall']} | {v['f1']} | {c['tp']} | {c['fp']} | {c['fn']} | {c['tn']} | "
                 f"{m['train_seconds']} | {m['inference_ms_per_1000_rows']} |")
    L += ["", f"Validation prevalence (chance PR-AUC): {r['models']['rule']['val']['prevalence']}.", "",
          "Deployed-style alerts on validation (900 s dedup; false alarm = no incident begins in the next horizon):", "",
          "| model | alerts | false alarms | evaluable host-days | false alarms / evaluable host-day |", "|---|---|---|---|---|"]
    for k, m in r["models"].items():
        a = m["val_alerts"]
        L.append(f"| {k} | {a['alerts']} | {a['false_alarms']} | {a['evaluable_host_days']} | {a['false_alarms_per_evaluable_host_day']} |")
    L += ["", "| model | train PR-AUC at val threshold | gap to val PR-AUC |", "|---|---|---|"]
    for k, m in r["models"].items():
        L.append(f"| {k} | {m['train_at_val_threshold']['pr_auc']} | {round(m['train_at_val_threshold']['pr_auc'] - m['val']['pr_auc'], 4)} |")
    if r.get("preliminary_7run"):
        p = r["preliminary_7run"]
        L += ["", f"## Versus the seven-run preliminary ({p['train']['runs']} train runs / {p['val']['runs']} val run)", "",
              "| model | PR-AUC 7-run | PR-AUC full | precision 7-run | precision full | recall 7-run | recall full |", "|---|---|---|---|---|---|---|"]
        for k, m in r["models"].items():
            a, b = p["models"][k]["val"], m["val"]
            L.append(f"| {k} | {a['pr_auc']} | {b['pr_auc']} | {a['precision']} | {b['precision']} | {a['recall']} | {b['recall']} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ml.train_baselines")
    ap.add_argument("features")
    ap.add_argument("labels")
    ap.add_argument("--horizon", type=int, default=1800)
    ap.add_argument("--negative-stride", type=int, default=1)
    ap.add_argument("--out", default=None)
    ap.add_argument("--prelim-report", default=None)
    a = ap.parse_args(argv)
    out = train_all(Path(a.features), Path(a.labels), Path(a.out) if a.out else None, a.horizon, a.negative_stride, a.prelim_report)
    print((out / "report.md").read_text(encoding="utf-8"))
    print("models written to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
