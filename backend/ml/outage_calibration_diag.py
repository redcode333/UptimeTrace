"""Calibration of outage-prediction probabilities (horizon 1800 s). Fitting uses TRAINING runs only; validation is evaluated once.

Mathematics of the negative thinning (verified against the recorded manifest):
  Training kept every positive decision and every STRIDE-th negative decision. If the full training set had P positives and N
  negatives, the fitted odds are P / (N / STRIDE) = STRIDE * (P / N). Training also weighted positives by scale_pos_weight = w =
  (thinned negatives) / P, which multiplies the odds by w again. The total inflation is w * STRIDE, so the analytic correction is
  logit(p_adj) = logit(p) - ln(w * STRIDE). w is recomputed from the half-A training data. Check: P = 13270 kept positives and N = 173693 * 10 = 1736930 full
  negatives give prevalence 13270 / (13270 + 1736930) = 0.007583, the recorded train_prevalence_full.

Leakage-safe isotonic calibration (training data only):
  1. Split the TRAINING runs into halves A and B by a fixed seeded permutation of run ids.
  2. Train an XGBoost model on half A with the saved hyper-parameters and the same negative thinning as production.
  3. Fit an isotonic calibrator on half B: raw model scores on ALL of B's scored decisions (unthinned, deployment-like) against labels.
  4. Score validation once. The validation labels are used only for reporting, never for any fit or threshold.
Compared on validation: raw (half-A model), analytic prior correction, isotonic (half-A model + calibrator from B), and the
saved production model as reported in evaluation.json. Discrimination (PR-AUC) is reported for every variant: monotone
transforms cannot change it, so calibration alone is not a claim of better discrimination.

Run from backend/:  python -m ml.outage_calibration_diag <artifacts_dir> <out_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score

HORIZON = 1800
STRIDE = 10
BINS = 10
SEED = 0


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def ece(p: np.ndarray, y: np.ndarray, bins: int = BINS) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-z))


def load_split(artifacts: Path, split: str) -> pd.DataFrame:
    fdir = next(artifacts.glob(f"features_v11_trainval/**/features/split={split}"))
    ldir = next(artifacts.glob(f"labels_v2_trainval/**/labels/split={split}/horizon_s={HORIZON}"))
    parts = []
    for lf in sorted(ldir.glob("run=*.parquet")):
        run = lf.stem.split("=")[1]
        lab = pq.read_table(lf, columns=["run_id", "decision_index", "decision_ts", "scored", "target"]).to_pandas()
        lab["run_id"] = lab["run_id"].astype(str)
        lab = lab[lab["scored"]]
        feat = pq.read_table(fdir / f"run={run}.parquet").to_pandas()
        feat["run_id"] = feat["run_id"].astype(str)
        parts.append(lab.merge(feat, on=["run_id", "decision_index"], how="inner", suffixes=("", "_f")))
    return pd.concat(parts, ignore_index=True)


def main(artifacts: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ev = artifacts / "evaluation" / "uptimetrace-m31-seed0-2723fc6789d8" / f"h{HORIZON}"
    schema = json.loads((ev / "models" / "xgboost" / "schema.json").read_text(encoding="utf-8"))
    names = schema["model_input_features"]
    params = dict(schema["xgboost_params"])
    prod = xgb.Booster()
    prod.load_model(str(ev / "models" / "xgboost" / "model.json"))

    tr = load_split(artifacts, "train")
    va = load_split(artifacts, "val")

    runs = sorted(tr["run_id"].unique())
    rng = np.random.default_rng(SEED)
    rng.shuffle(runs)
    half_a_runs = set(runs[: len(runs) // 2])
    is_a = tr["run_id"].isin(half_a_runs).to_numpy()

    # Production-like training: all positives, every STRIDE-th negative (decision_index % STRIDE == 0).
    keep_train_a = is_a & ((tr["target"].to_numpy() == 1) | (tr["decision_index"].to_numpy() % STRIDE == 0))
    A = tr[keep_train_a]
    # scale_pos_weight in the production model = negatives / positives of the thinned training set; recomputed for half A.
    pos_a, neg_a = int((A["target"] == 1).sum()), int((A["target"] == 0).sum())
    params["scale_pos_weight"] = neg_a / pos_a
    correction = float(np.log(params["scale_pos_weight"] * STRIDE))
    model_a = xgb.train({**params, "seed": SEED}, xgb.DMatrix(A[names].to_numpy(np.float32), label=A["target"].to_numpy(), feature_names=names),
                        num_boost_round=int(schema.get("num_boost_round", params.get("num_boost_round", 300))))
    # Isotonic calibrator on half B: all scored decisions, unthinned (deployment-like distribution).
    B = tr[~is_a]
    raw_b = model_a.predict(xgb.DMatrix(B[names].to_numpy(np.float32), feature_names=names))
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(raw_b, B["target"].to_numpy())

    yv = va["target"].to_numpy(dtype=float)
    dv = xgb.DMatrix(va[names].to_numpy(np.float32), feature_names=names)
    p_a = model_a.predict(dv)
    p_prod = prod.predict(dv)
    p_prior = sigmoid(logit(p_a) - correction)
    p_iso = iso.predict(p_a)

    def block(p: np.ndarray) -> dict:
        return {"brier": brier(p, yv), "ece": ece(p, yv), "mean_prediction": float(p.mean()),
                "pr_auc": float(average_precision_score(yv, p)), "skill_vs_climatology": 1 - brier(p, yv) / brier(np.full_like(yv, yv.mean()), yv)}

    res = {
        "horizon_s": HORIZON,         "negative_stride": STRIDE, "scale_pos_weight_half_a": params["scale_pos_weight"], "analytic_correction_ln_odds": correction, "half_a_positives": pos_a, "half_a_negatives_thinned": neg_a, "test_splits_opened": False,
        "train_runs": len(runs), "half_a_runs": len(half_a_runs), "half_b_runs": len(runs) - len(half_a_runs),
        "train_rows_half_a_fit": int(len(A)), "train_rows_half_b_calibration": int(len(B)),
        "train_prevalence_half_b": float(B["target"].mean()),
        "validation_rows": int(len(va)), "validation_positives": int(yv.sum()), "validation_prevalence": float(yv.mean()),
        "production_model_as_reported": block(p_prod),
        "half_a_model_raw": block(p_a),
        "half_a_model_prior_correction": block(p_prior),
        "half_a_model_isotonic_from_half_b": block(p_iso),
        "climatology_brier": brier(np.full_like(yv, yv.mean()), yv),
    }
    (out / "outage_calibration_v2.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
