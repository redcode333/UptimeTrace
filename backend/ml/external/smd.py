"""SMD (Server Machine Dataset; NetManAIOps/OmniAnomaly, KDD 2019). Dataset-specific feature and label mappings live here only.

Raw layout:  ServerMachineDataset/{train,test,test_label,interpretation_label}/machine-G-N.txt   (28 machines, 38 dims, no header).
  * train / test: comma-separated rows. Not wall-clock timestamped; the files carry no sampling interval and no feature names.
  * test_label: one 0/1 per test row. Contiguous runs are anomaly SEGMENTS (interval-level events).
  * interpretation_label: per-segment lists of contributing dimensions; kept out of the normalised output (not a detection label).
Feature mapping: f00..f37 = raw columns 1..38 in file order. Their physical meaning is NOT published, so they must not be mapped
onto UptimeTrace's cpu/memory/disk/network series. Label mapping: label=1 means "expert-labelled anomalous point"; this is NOT an
outage and carries no impact timestamp.
"""
from __future__ import annotations

from pathlib import Path

from ml.external.common import (SourceInfo, assert_outside, read_labels, read_matrix, sha256_file, write_entity, write_manifest)

DIMS = 38
EXPECTED_MACHINES = 28


def normalise(raw_root: Path, out_root: Path, source: SourceInfo, *, expected_machines: int | None = EXPECTED_MACHINES, dims: int | None = DIMS) -> dict:
    """`raw_root` is the ServerMachineDataset directory. Reads only; writes to `out_root` (must be outside `raw_root`)."""
    raw_root, out_root = Path(raw_root), Path(out_root)
    assert_outside(raw_root, out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    machines = sorted(p.stem for p in (raw_root / "train").glob("machine-*.txt"))
    if expected_machines is not None and len(machines) != expected_machines:
        raise ValueError(f"expected {expected_machines} machines, found {len(machines)}")
    entities, raw_hashes = [], {}
    for m in machines:
        train, _ = read_matrix(raw_root / "train" / f"{m}.txt", header=False)
        test, _ = read_matrix(raw_root / "test" / f"{m}.txt", header=False)
        label = read_labels(raw_root / "test_label" / f"{m}.txt", header=False, column=0)
        if dims is not None and not (train.shape[1] == test.shape[1] == dims):
            raise ValueError(f"{m}: expected {dims} dimensions, got {train.shape[1]} / {test.shape[1]}")
        for sub in ("train", "test", "test_label", "interpretation_label"):
            raw_hashes[f"{sub}/{m}.txt"] = sha256_file(raw_root / sub / f"{m}.txt")
        for split, vals, lab in (("train", train, None), ("test", test, label)):
            info = write_entity(out_root, m, split, vals, lab)
            entities.append({"entity": m, "split": split, **info})
    return write_manifest(
        out_root, source, raw_hashes, entities,
        preprocessing="none: values copied verbatim as float32 (the publisher already scaled them to [0,1]); no imputation; NaN preserved",
        notes=["Train split is unlabelled and assumed normal by the benchmark; it may contain anomalies.",
               "No validation split is published: derive one from the TAIL of train (temporal), never from test.",
               "No wall-clock timestamps and no sampling interval in the files; t_index is row order only.",
               "No outage/impact timestamps: unsuitable for outage-risk prediction."])
