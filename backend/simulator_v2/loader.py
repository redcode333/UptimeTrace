"""Dataset loader gate (spec 11, AC-03, AC-30): refuses datasets that are not exhaustively validated or whose hash differs."""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq

from .protocol import protocol_hash

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = Path(__file__).resolve().parents[1] / "ml" / "schema"
SIDECARS = {"runs.jsonl": "runs", "faults.jsonl": "faults", "incidents.jsonl": "incidents",
            "benign_episodes.jsonl": "benign_episodes", "gaps.jsonl": "gaps", "view_gaps.jsonl": "view_gaps"}


class DatasetRejected(RuntimeError):
    pass


def verify_dataset(root: Path, check_sidecar_schemas: bool = True) -> dict:
    """Returns the manifest, or raises DatasetRejected naming the first violated rule."""
    root = Path(root)
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    proto = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
    v = man["validation"]
    if protocol_hash(proto) != man["protocol_hash"]:
        raise DatasetRejected("protocol_hash mismatch: recomputed hash differs from the manifest")
    if v["envelopes_checked"] < v["envelopes_total"]:
        raise DatasetRejected(f"envelopes_checked {v['envelopes_checked']} < envelopes_total {v['envelopes_total']}")
    if v["observations_checked"] != v["observations_total"]:
        raise DatasetRejected(f"observations_checked {v['observations_checked']} != observations_total {v['observations_total']}")
    if v["schema_failures"] or v["semantic_failures"]:
        raise DatasetRejected(f"validation failures: schema {v['schema_failures']}, semantic {v['semantic_failures']}")
    rows = sum(pq.ParquetFile(f).metadata.num_rows for f in (root / "observations").glob("run=*.parquet"))
    if rows != v["observations_total"]:
        raise DatasetRejected(f"Parquet rows {rows} != observations_total {v['observations_total']}")
    if check_sidecar_schemas:
        from jsonschema import Draft202012Validator
        for fname, name in SIDECARS.items():
            val = Draft202012Validator(json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8")))
            for ln, line in enumerate((root / fname).read_text(encoding="utf-8").splitlines(), 1):
                errs = list(val.iter_errors(json.loads(line)))
                if errs:
                    raise DatasetRejected(f"{fname}:{ln} violates gt-1.0 schema: {errs[0].message[:150]}")
        vv = Draft202012Validator(json.loads((SCHEMA_DIR / "view.schema.json").read_text(encoding="utf-8")))
        errs = list(vv.iter_errors(json.loads((root / "view.json").read_text(encoding="utf-8"))))
        if errs:
            raise DatasetRejected(f"view.json violates gt-1.0 schema: {errs[0].message[:150]}")
    return man
