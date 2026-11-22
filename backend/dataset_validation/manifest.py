"""Freeze and verify a dataset manifest. A manifest is written only when validation passes, lives outside the dataset folders, and
records the SHA-256 and size of every file so that any later change to the frozen dataset is detected by `verify`."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from . import policy as P
from .report import Report

FROZEN_TREES = ("datasets", "labels_full", "features_full")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _inventory(artifacts: Path, dataset_id: str) -> dict[str, dict]:
    files: dict[str, dict] = {}
    for tree in FROZEN_TREES:
        root = artifacts / tree / dataset_id
        if not root.exists():
            raise FileNotFoundError(f"{root} does not exist")
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = p.relative_to(artifacts).as_posix()
                files[rel] = {"bytes": p.stat().st_size, "sha256": _sha256(p)}
    return files


def freeze(artifacts: Path, dataset_id: str, report: Report, out: Path) -> dict:
    if report.verdict != "PASS":
        raise RuntimeError(f"refusing to freeze: validation has {len(report.hard)} hard failure(s)")
    files = _inventory(artifacts, dataset_id)
    manifest = {
        "dataset_id": dataset_id,
        "frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "validator_version": P.VERSION,
        "validation": {"verdict": report.verdict, "hard_failures": 0, "warnings": len(report.warnings), "checks_run": report.checked},
        "config": report.stats.get("config", {}),
        "counts": {"runs": report.stats.get("runs", {}).get("n"), "observation_rows": report.stats.get("observations", {}).get("rows"),
                   "envelopes": report.stats.get("observations", {}).get("envelopes"), "faults": report.stats.get("faults", {}).get("n"),
                   "incidents": report.stats.get("incidents", {}).get("n"), "label_rows": report.stats.get("labels", {}).get("rows"),
                   "feature_rows": report.stats.get("features", {}).get("rows")},
        "distributions": {"incident_by_group": report.stats.get("incidents", {}).get("by_group"),
                          "incident_by_split_group": report.stats.get("incidents", {}).get("by_split_group"),
                          "splits": report.stats.get("splits")},
        "files": files,
        "total_bytes": sum(v["bytes"] for v in files.values()),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def verify(artifacts: Path, dataset_id: str, manifest_path: Path) -> dict:
    """Compare the dataset on disk with the frozen manifest. Any change, addition or removal is reported by path."""
    frozen = json.loads(manifest_path.read_text(encoding="utf-8"))
    if frozen["dataset_id"] != dataset_id:
        raise ValueError("manifest belongs to a different dataset")
    current = _inventory(artifacts, dataset_id)
    expected = frozen["files"]
    changed = sorted(k for k in expected if k in current and current[k] != expected[k])
    missing = sorted(set(expected) - set(current))
    added = sorted(set(current) - set(expected))
    return {"ok": not (changed or missing or added), "files_checked": len(expected), "changed": changed, "missing": missing, "added": added}
