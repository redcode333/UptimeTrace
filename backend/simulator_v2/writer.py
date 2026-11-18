"""Parquet and JSONL writers."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA = pa.schema([
    ("host_id", pa.dictionary(pa.int32(), pa.string())),
    ("metric", pa.dictionary(pa.int32(), pa.string())),
    ("labels", pa.dictionary(pa.int32(), pa.string())),
    ("kind", pa.dictionary(pa.int32(), pa.string())),
    ("unit", pa.dictionary(pa.int32(), pa.string())),
    ("value", pa.float64()),
    ("obs_ts", pa.timestamp("us", tz="UTC")),
    ("env_ts", pa.timestamp("us", tz="UTC")),
    ("agent_id", pa.dictionary(pa.int32(), pa.string())),
    ("envelope_id", pa.string()),
])


def _dict(values, n=None):
    if isinstance(values, str):
        return pa.DictionaryArray.from_arrays(pa.array(np.zeros(n, dtype=np.int32)), pa.array([values]))
    return pa.array(values, type=pa.string()).dictionary_encode()


def write_run_parquet(path: Path, cols: dict) -> int:
    n = len(cols["value"])
    table = pa.table({
        "host_id": _dict(cols["host_id"], n), "metric": _dict(cols["metric"]), "labels": _dict(cols["labels"]),
        "kind": _dict(cols["kind"]), "unit": _dict(cols["unit"]), "value": pa.array(cols["value"], type=pa.float64()),
        "obs_ts": pa.array(cols["obs_ts"], type=pa.int64()).cast(pa.timestamp("us", tz="UTC")),
        "env_ts": pa.array(cols["env_ts"], type=pa.int64()).cast(pa.timestamp("us", tz="UTC")),
        "agent_id": _dict(cols["agent_id"], n), "envelope_id": pa.array(cols["envelope_id"], type=pa.string()),
    }, schema=SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd", use_dictionary=True, write_statistics=False, store_schema=False)
    return n


def write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")


def write_json(path: Path, obj: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, sort_keys=True, indent=2, ensure_ascii=False)
        f.write("\n")
