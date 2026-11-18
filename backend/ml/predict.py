"""Outage-risk prediction interface. Loads a saved baseline and scores a feature matrix. Imports nothing from the training code.

    model = load_model("artifacts/models/<dataset>/h1800/xgboost")
    risk = model.predict_risk(X, feature_names)      # float64 in [0, 1], one score per row
    flags = model.alert(X, feature_names)            # risk >= the threshold chosen on the validation split

X is a (n, 196) feature matrix in the order of `ml.features.NAMES` (the manifest order), or any column order if `feature_names`
(or a DataFrame) is given. Missing values are NaN. Every model handles NaN itself; the output is always finite.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

SCHEMA_FILE = "schema.json"


class SchemaError(ValueError):
    pass


class RiskModel:
    def __init__(self, schema: dict, estimator=None):
        self.schema = schema
        self.kind = schema["model_kind"]
        self.feature_names: list[str] = list(schema["feature_names"])
        self.input_features: list[str] = list(schema["model_input_features"])
        self.threshold: float = float(schema["threshold"])
        self.horizon_s: int = int(schema["horizon_s"])
        self._est = estimator
        pos = {n: i for i, n in enumerate(self.feature_names)}
        self._cols = np.array([pos[n] for n in self.input_features], dtype=int)

    # ------------------------------------------------------------------ input handling
    def _matrix(self, X, feature_names=None) -> np.ndarray:
        if hasattr(X, "columns") and feature_names is None:            # pandas DataFrame
            feature_names = list(X.columns)
        arr = np.asarray(X, dtype=np.float64)
        if arr.ndim != 2:
            raise SchemaError(f"X must be 2-D, got shape {arr.shape}")
        if arr.shape[1] != len(self.feature_names):
            raise SchemaError(f"X has {arr.shape[1]} columns, the model schema has {len(self.feature_names)} features")
        if feature_names is not None:
            names = list(feature_names)
            if len(names) != arr.shape[1] or set(names) != set(self.feature_names):
                missing = sorted(set(self.feature_names) - set(names))[:5]
                extra = sorted(set(names) - set(self.feature_names))[:5]
                raise SchemaError(f"feature names do not match the model schema (missing {missing}, unexpected {extra})")
            order = [names.index(n) for n in self.feature_names]
            arr = arr[:, order]
        arr = arr.copy()
        arr[~np.isfinite(arr)] = np.nan
        return arr

    # ------------------------------------------------------------------ scoring
    def predict_risk(self, X, feature_names=None) -> np.ndarray:
        arr = self._matrix(X, feature_names)
        sub = arr[:, self._cols]
        if self.kind == "rule":
            risk = self._rule(arr)
        elif self.kind in ("logreg", "xgboost"):
            risk = self._est.predict_proba(sub)[:, 1]
        else:
            raise SchemaError(f"unknown model kind {self.kind}")
        risk = np.nan_to_num(np.asarray(risk, dtype=np.float64), nan=0.0, posinf=1.0, neginf=0.0)
        return np.clip(risk, 0.0, 1.0)

    def _rule(self, arr: np.ndarray) -> np.ndarray:
        """Deterministic rule: risk = highest utilisation (0..1) among the configured features. Missing counts as 0."""
        idx = [self.feature_names.index(n) for n in self.schema["rule"]["utilisation_features"]]
        vals = np.nan_to_num(arr[:, idx], nan=0.0) / 100.0
        return vals.max(axis=1)

    def alert(self, X, feature_names=None) -> np.ndarray:
        return self.predict_risk(X, feature_names) >= self.threshold


def load_model(model_dir) -> RiskModel:
    d = Path(model_dir)
    schema = json.loads((d / SCHEMA_FILE).read_text(encoding="utf-8"))
    kind = schema["model_kind"]
    est = None
    if kind == "logreg":
        import joblib
        est = joblib.load(d / "model.joblib")
    elif kind == "xgboost":
        import xgboost as xgb
        est = xgb.XGBClassifier()
        est.load_model(str(d / "model.json"))
    elif kind != "rule":
        raise SchemaError(f"unknown model kind {kind}")
    return RiskModel(schema, est)
