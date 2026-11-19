"""Shared, dataset-agnostic helpers: hashing, safe writing, the normalised parquet layout and segment extraction."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from ml.external import ADAPTER_VERSION


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_outside(raw_root: Path, out_root: Path) -> None:
    """Raw data is immutable: refuse any output location inside (or equal to) the raw tree."""
    raw, out = raw_root.resolve(), out_root.resolve()
    if out == raw or raw in out.parents:
        raise ValueError(f"output directory {out} is inside the raw data tree {raw}; raw data must stay immutable")


def read_matrix(path: Path, *, header: bool) -> tuple[np.ndarray, list[str]]:
    """CSV -> float32 matrix. Empty cells become NaN (never 0). Returns (values, column names)."""
    ro = pacsv.ReadOptions(autogenerate_column_names=not header)
    table = pacsv.read_csv(path, read_options=ro, convert_options=pacsv.ConvertOptions(strings_can_be_null=True))
    cols = []
    for name in table.column_names:
        col = table[name].cast(pa.float64())            # fails loudly on non-numeric content instead of coercing it
        cols.append(col.to_numpy(zero_copy_only=False))  # nulls -> NaN
    return np.column_stack(cols).astype(np.float32), list(table.column_names)


def read_labels(path: Path, *, header: bool, column: int = -1) -> np.ndarray:
    values, _ = read_matrix(path, header=header)
    y = values[:, column]
    if np.isnan(y).any() or not np.isin(y, (0.0, 1.0)).all():
        raise ValueError(f"{path.name}: labels must be 0/1 with no missing values")
    return y.astype(np.int8)


def segments(label: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of 1 as (start, end_exclusive) row indices."""
    d = np.diff(np.concatenate([[0], np.asarray(label).astype(np.int8), [0]]))
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist()))


def write_entity(out_dir: Path, entity: str, split: str, values: np.ndarray, label: np.ndarray | None) -> dict:
    """One parquet file per (entity, split): f00.. float32 columns, `t_index` (row order; NOT wall-clock), optional int8 `label`."""
    n, d = values.shape
    arrays = {"t_index": pa.array(np.arange(n, dtype=np.int64))}
    for j in range(d):
        arrays[f"f{j:02d}"] = pa.array(values[:, j], type=pa.float32(), from_pandas=False)
    if label is not None:
        if len(label) != n:
            raise ValueError(f"{entity}.{split}: {len(label)} labels for {n} rows")
        arrays["label"] = pa.array(label.astype(np.int8), type=pa.int8())
    path = out_dir / f"{entity}.{split}.parquet"
    pq.write_table(pa.table(arrays), path, compression="zstd")
    return {"file": path.name, "rows": int(n), "features": int(d), "nan_cells": int(np.isnan(values).sum()),
            "anomaly_points": int(label.sum()) if label is not None else None,
            "segments": len(segments(label)) if label is not None else None, "sha256": sha256_file(path)}


@dataclass(frozen=True)
class SourceInfo:
    dataset: str
    repository: str
    commit: str
    archive_sha256: str
    retrieved_utc: str
    licence_name: str
    licence_file_sha256: str
    attribution: str
    paper: str


def write_manifest(out_dir: Path, source: SourceInfo, raw_files: dict[str, str], entities: list[dict], preprocessing: str, notes: list[str]) -> dict:
    manifest = {
        "adapter_version": ADAPTER_VERSION, "source": source.__dict__, "raw_file_sha256": raw_files, "preprocessing": preprocessing,
        "entities": entities, "totals": {
            "files": len(entities), "rows": int(sum(e["rows"] for e in entities)), "nan_cells": int(sum(e["nan_cells"] for e in entities))},
        "notes": notes,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest
