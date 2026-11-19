"""External-dataset adapters on tiny fixtures that mimic the REAL raw layouts (no network, no large files, no database)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from ml.external import psm, smd
from ml.external.common import SourceInfo, assert_outside, segments

SRC = SourceInfo("X", "https://example.invalid/repo", "0" * 40, "a" * 64, "2026-11-18T00:00Z", "MIT", "b" * 64, "attribution text", "paper")


def tree_hash(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def make_smd(root: Path, machines=("machine-1-1", "machine-1-2"), dims=4, n=30) -> Path:
    base = root / "ServerMachineDataset"
    rng = np.random.default_rng(0)
    for sub in ("train", "test", "test_label", "interpretation_label"):
        (base / sub).mkdir(parents=True)
    for m in machines:
        np.savetxt(base / "train" / f"{m}.txt", rng.random((n, dims)), delimiter=",", fmt="%.6f")
        np.savetxt(base / "test" / f"{m}.txt", rng.random((n, dims)), delimiter=",", fmt="%.6f")
        lab = np.zeros(n, dtype=int)
        lab[5:9] = 1
        lab[20:21] = 1
        np.savetxt(base / "test_label" / f"{m}.txt", lab, fmt="%d")
        (base / "interpretation_label" / f"{m}.txt").write_text("5-8:1,2\n20-20:3\n", encoding="utf-8")
    return base


def make_psm(root: Path, feats=3, n_train=12, n_test=10, nan_at=(2, 1)) -> Path:
    d = root / "data"
    d.mkdir(parents=True)
    cols = "timestamp_(min)," + ",".join(f"feature_{i}" for i in range(feats))
    rng = np.random.default_rng(1)
    tr = rng.random((n_train, feats)).round(5).astype(str).tolist()
    tr[nan_at[0]][nan_at[1]] = ""                                    # a genuinely empty cell, as in the real train.csv
    (d / "train.csv").write_text(cols + "\n" + "\n".join(f"{i}.0," + ",".join(r) for i, r in enumerate(tr)) + "\n", encoding="utf-8")
    te = rng.random((n_test, feats)).round(5)
    (d / "test.csv").write_text(cols + "\n" + "\n".join(f"{i}.0," + ",".join(map(str, r)) for i, r in enumerate(te)) + "\n", encoding="utf-8")
    lab = [0, 0, 1, 1, 1, 0, 0, 1, 0, 0][:n_test]
    (d / "test_label.csv").write_text("timestamp_(min),label\n" + "\n".join(f"{i}.0,{y}" for i, y in enumerate(lab)) + "\n", encoding="utf-8")
    return d


# ------------------------------------------------------------------------------------------------ common
def test_segments_are_maximal_runs_with_exclusive_end():
    assert segments(np.array([0, 1, 1, 0, 1, 0, 0, 1, 1])) == [(1, 3), (4, 5), (7, 9)]
    assert segments(np.zeros(5)) == [] and segments(np.ones(3)) == [(0, 3)]


def test_output_inside_the_raw_tree_is_refused(tmp_path):
    raw = make_smd(tmp_path / "raw")
    for bad in (raw, raw / "out", raw / "train" / "x"):
        with pytest.raises(ValueError, match="immutable"):
            assert_outside(raw, bad)
        with pytest.raises(ValueError, match="immutable"):
            smd.normalise(raw, bad, SRC, expected_machines=2, dims=4)
    assert_outside(raw, tmp_path / "elsewhere")          # a sibling directory is fine


# ------------------------------------------------------------------------------------------------ SMD
def test_smd_roundtrip_values_labels_and_provenance(tmp_path):
    raw = make_smd(tmp_path / "raw")
    before = tree_hash(raw)
    out = tmp_path / "norm"
    man = smd.normalise(raw, out, SRC, expected_machines=2, dims=4)
    assert tree_hash(raw) == before                                            # raw data untouched
    assert man["totals"]["files"] == 4 and man["source"]["commit"] == "0" * 40 and man["source"]["licence_name"] == "MIT"
    t = pq.read_table(out / "machine-1-1.test.parquet")
    assert t.column_names == ["t_index", "f00", "f01", "f02", "f03", "label"]
    orig = np.loadtxt(raw / "test" / "machine-1-1.txt", delimiter=",")
    assert np.allclose(np.column_stack([t[f"f{j:02d}"].to_numpy() for j in range(4)]), orig, atol=1e-6)
    assert t["label"].to_numpy().sum() == 5 and str(t.schema.field("label").type) == "int8"
    ent = [e for e in man["entities"] if e["entity"] == "machine-1-1" and e["split"] == "test"][0]
    assert ent["anomaly_points"] == 5 and ent["segments"] == 2
    tr = pq.read_table(out / "machine-1-1.train.parquet")
    assert "label" not in tr.column_names                                      # train is unlabelled
    assert json.loads((out / "manifest.json").read_text())["raw_file_sha256"]["test/machine-1-1.txt"] == before["test/machine-1-1.txt"]


def test_smd_is_deterministic(tmp_path):
    raw = make_smd(tmp_path / "raw")
    a, b = tmp_path / "a", tmp_path / "b"
    ma, mb = smd.normalise(raw, a, SRC, expected_machines=2, dims=4), smd.normalise(raw, b, SRC, expected_machines=2, dims=4)
    assert ma == mb and tree_hash(a) == tree_hash(b)


def test_smd_rejects_wrong_shape_and_bad_labels(tmp_path):
    raw = make_smd(tmp_path / "raw")
    with pytest.raises(ValueError, match="expected 28 machines"):
        smd.normalise(raw, tmp_path / "o1", SRC)                               # real-dataset defaults on a 2-machine fixture
    with pytest.raises(ValueError, match="dimensions"):
        smd.normalise(raw, tmp_path / "o2", SRC, expected_machines=2, dims=5)
    (raw / "test_label" / "machine-1-1.txt").write_text("\n".join(["0"] * 29 + ["2"]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="0/1"):
        smd.normalise(raw, tmp_path / "o3", SRC, expected_machines=2, dims=4)


# ------------------------------------------------------------------------------------------------ PSM
def test_psm_keeps_missing_cells_as_nan_never_zero(tmp_path):
    raw = make_psm(tmp_path / "raw")
    out = tmp_path / "norm"
    man = psm.normalise(raw, out, SRC, features=3)
    tr = pq.read_table(out / "psm.train.parquet")
    f1 = tr["f01"].to_numpy()
    assert np.isnan(f1[2]) and not np.isnan(np.delete(f1, 2)).any()
    assert man["totals"]["nan_cells"] == 1
    te = pq.read_table(out / "psm.test.parquet")
    assert te["label"].to_numpy().tolist() == [0, 0, 1, 1, 1, 0, 0, 1, 0, 0]
    assert [e["segments"] for e in man["entities"] if e["split"] == "test"] == [2]
    assert "timestamp_(min)" not in tr.column_names and tr["t_index"].to_numpy()[:3].tolist() == [0, 1, 2]


def test_psm_refuses_misaligned_or_malformed_inputs(tmp_path):
    raw = make_psm(tmp_path / "raw")
    (raw / "test_label.csv").write_text("timestamp_(min),label\n0.0,0\n1.0,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not aligned"):
        psm.normalise(raw, tmp_path / "o1", SRC, features=3)
    raw2 = make_psm(tmp_path / "raw2")
    (raw2 / "train.csv").write_text("idx,feature_0,feature_1,feature_2\n0,1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected first column"):
        psm.normalise(raw2, tmp_path / "o2", SRC, features=3)
    raw3 = make_psm(tmp_path / "raw3")
    with pytest.raises(ValueError, match="expected 25 features"):
        psm.normalise(raw3, tmp_path / "o3", SRC, features=25)
    raw4 = make_psm(tmp_path / "raw4")
    (raw4 / "train.csv").write_text("timestamp_(min),feature_0,feature_1,feature_2\n0.0,1,abc,3\n", encoding="utf-8")
    with pytest.raises(Exception):                                             # non-numeric content must fail loudly, not be coerced
        psm.normalise(raw4, tmp_path / "o4", SRC, features=3)
