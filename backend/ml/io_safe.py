"""Crash-safe, verified Parquet writes for derived dataset files.

A derived file is never visible under its final name until it has been proven readable:

  1. write to a unique temporary file in the destination directory;
  2. flush and fsync it;
  3. reopen it and decode every column in full (a damaged block fails here, not later in a model);
  4. check the schema, the row count and that every value equals the table that was written;
  5. hash the verified bytes, then atomically rename the temporary file onto the final name;
  6. re-hash the final file and require the same digest.

A failed write removes its temporary file and raises; nothing partial is left under the final name. Unique temporary names make
parallel writers safe. The digest returned is the digest of the verified bytes, so manifests can be built without re-reading files.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


class VerificationError(RuntimeError):
    """A written file did not read back exactly as it was written."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _same_column(a, b) -> bool:
    """Equal values, where NaN equals NaN (features and labels use NaN for "not available"). Arrow's own equals treats NaN as
    unequal to itself, which would reject every faithful float column."""
    if a.type != b.type:
        return False
    if pa.types.is_floating(a.type):
        return np.array_equal(a.to_numpy(zero_copy_only=False), b.to_numpy(zero_copy_only=False), equal_nan=True)
    return a.equals(b)


def write_parquet_verified(table: pa.Table, final: Path, *, compression: str = "zstd", use_dictionary=True) -> str:
    """Write `table` to `final` atomically and return the SHA-256 of the verified file."""
    final = Path(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f".tmp-{uuid.uuid4().hex}-{final.name}")
    try:
        with open(tmp, "wb") as f:
            pq.write_table(table, f, compression=compression, use_dictionary=use_dictionary, write_statistics=False)
            f.flush()
            os.fsync(f.fileno())
        # ParquetFile reads the file itself. read_table would infer Hive partitions from the directory names (split=, horizon_s=)
        # and report columns the writer never wrote.
        back = pq.ParquetFile(tmp).read()                # decodes every column and page; damage raises here
        if back.num_rows != table.num_rows:
            raise VerificationError(f"{final.name}: wrote {table.num_rows} rows, reads back {back.num_rows}")
        if back.column_names != table.column_names:
            raise VerificationError(f"{final.name}: column names differ after read-back")
        bad = [c for c in table.column_names if not _same_column(back.column(c), table.column(c))]
        if bad:
            raise VerificationError(f"{final.name}: values differ after read-back in {bad[:5]}")
        digest = _sha256(tmp)
        os.replace(tmp, final)                          # atomic within one volume
        if _sha256(final) != digest:
            raise VerificationError(f"{final.name}: digest changed after rename")
        return digest
    finally:
        if tmp.exists():
            tmp.unlink()


def verify_parquet_file(path: Path, expected_rows: int | None = None, required_columns=()) -> dict:
    """Independent check used by validators: full read-back, row count and required columns. Returns digest and shape."""
    path = Path(path)
    t = pq.ParquetFile(path).read()
    if expected_rows is not None and t.num_rows != expected_rows:
        raise VerificationError(f"{path.name}: {t.num_rows} rows, expected {expected_rows}")
    missing = [c for c in required_columns if c not in t.column_names]
    if missing:
        raise VerificationError(f"{path.name}: missing columns {missing[:5]}")
    return {"rows": t.num_rows, "columns": t.num_columns, "sha256": _sha256(path)}
