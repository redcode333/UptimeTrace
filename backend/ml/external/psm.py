"""PSM (Pooled Server Metrics; eBay/RANSynCoders, KDD 2021). Dataset-specific mappings live here only.

Raw layout: data/{train.csv, test.csv, test_label.csv}. Header present. Column 0 is `timestamp_(min)`: a RELATIVE minute index
starting at 0 (not wall-clock). Columns feature_0..feature_24 are anonymised. test_label.csv has the same index plus a 0/1 label.
Feature mapping: f00..f24 = feature_0..feature_24. Label mapping: label=1 is "labelled anomalous point", not an outage.
Missing values: train.csv contains empty cells; they stay NaN (never 0).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ml.external.common import SourceInfo, assert_outside, read_matrix, sha256_file, write_entity, write_manifest

FEATURES = 25


def normalise(raw_root: Path, out_root: Path, source: SourceInfo, *, features: int | None = FEATURES) -> dict:
    """`raw_root` is the `data` directory. Reads only; writes to `out_root` (must be outside `raw_root`)."""
    raw_root, out_root = Path(raw_root), Path(out_root)
    assert_outside(raw_root, out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    tr, tr_cols = read_matrix(raw_root / "train.csv", header=True)
    te, te_cols = read_matrix(raw_root / "test.csv", header=True)
    lb, lb_cols = read_matrix(raw_root / "test_label.csv", header=True)
    for name, cols in (("train.csv", tr_cols), ("test.csv", te_cols)):
        if cols[0] != "timestamp_(min)":
            raise ValueError(f"{name}: unexpected first column {cols[0]!r}")
    if tr_cols[1:] != te_cols[1:]:
        raise ValueError("train/test feature columns differ")
    if features is not None and tr.shape[1] - 1 != features:
        raise ValueError(f"expected {features} features, got {tr.shape[1] - 1}")
    if len(lb) != len(te) or not np.array_equal(lb[:, 0], te[:, 0]):
        raise ValueError("test_label.csv is not aligned with test.csv (row count or index differs)")
    y = lb[:, 1]
    if np.isnan(y).any() or not np.isin(y, (0.0, 1.0)).all():
        raise ValueError("test_label.csv: labels must be 0/1 with no missing values")
    entities = []
    for split, vals, lab in (("train", tr[:, 1:], None), ("test", te[:, 1:], y.astype(np.int8))):
        entities.append({"entity": "psm", "split": split, **write_entity(out_root, "psm", split, vals, lab)})
    raw_hashes = {n: sha256_file(raw_root / n) for n in ("train.csv", "test.csv", "test_label.csv")}
    return write_manifest(
        out_root, source, raw_hashes, entities,
        preprocessing="none: values copied verbatim as float32; the relative minute index is dropped (t_index = row order); NaN preserved",
        notes=["Single pooled entity; train contains missing cells (kept as NaN).",
               "Test anomaly prevalence is high and dominated by one very long segment; point-adjusted metrics would be misleading.",
               "No validation split is published: derive one from the TAIL of train (temporal), never from test.",
               "Anonymised features; no outage/impact timestamps: unsuitable for outage-risk prediction."])
