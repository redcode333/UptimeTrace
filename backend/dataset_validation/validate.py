"""Runs every check against one dataset and returns the report. Read-only: nothing under the dataset folders is written."""
from __future__ import annotations

import json
from pathlib import Path

from . import policy as P
from .checks_coverage import check_coverage, check_split_definition
from .checks_features import check_features, check_leakage
from .checks_labels import check_labels, check_primary_horizon
from .checks_raw import check_faults, check_gaps, check_incidents, check_observations, check_runs, load_raw
from .report import Report


def dataset_paths(artifacts: Path, dataset_id: str) -> dict[str, Path]:
    return {
        "raw": artifacts / "datasets" / dataset_id,
        "labels": artifacts / "labels_full" / dataset_id / "labels",
        "features": artifacts / "features_full" / dataset_id / "features",
        "partial_features_definitions": artifacts / "features" / dataset_id / "features_manifest.json",
        "partial_labels": artifacts / "labels" / dataset_id,
        "partial_features": artifacts / "features" / dataset_id / "features",
    }


def validate_dataset(artifacts: Path, dataset_id: str, deep: bool = True, derived_root: Path | None = None) -> Report:
    paths = dataset_paths(artifacts, dataset_id)
    if derived_root is not None:
        # A versioned replacement: raw telemetry stays in the original artifacts; labels and features come from the version directory.
        paths["labels"] = Path(derived_root) / "labels_full" / dataset_id / "labels"
        paths["features"] = Path(derived_root) / "features_full" / dataset_id / "features"
    raw = paths["raw"]
    r = Report(dataset_id=dataset_id, validator_version=P.VERSION)
    r.check("raw.load")
    data = load_raw(raw)
    r.stats["config"] = {"n_runs": data["manifest"]["config"]["n_runs"], "cadence_s": data["manifest"]["config"]["cadence_s"],
                         "run_len_s": P.RUN_LEN_S, "primary_horizon_s": P.PRIMARY_HORIZON_S, "horizons_s": list(P.HORIZONS),
                         "protocol_version": data["protocol"].get("protocol_version"), "protocol_hash": data["manifest"].get("protocol_hash"),
                         "simulator_version": data["manifest"].get("simulator_version"), "spec_version": data["manifest"].get("spec_version"),
                         "master_seed": data["manifest"].get("master_seed"), "code_hash_declared": data["manifest"].get("code_hash")}

    runs = check_runs(raw, data, r)
    check_faults(data, runs, r)
    check_incidents(data, runs, r)
    check_gaps(data, runs, r)
    check_observations(raw, runs, data["gaps"], r, deep=deep)
    check_coverage(data["incidents"], runs, r)
    check_split_definition(runs, data["protocol"], data["manifest"]["master_seed"], r)
    lab = check_labels(paths["labels"], runs, data["incidents"], r)
    check_primary_horizon(lab, r)

    definitions = None
    definitions_path = (Path(derived_root) / "features_full" / dataset_id / "features_manifest.json") if derived_root is not None         else paths["partial_features_definitions"]
    if definitions_path.exists():
        definitions = json.loads(definitions_path.read_text(encoding="utf-8"))
    else:
        r.add("warn", "features", "features.definitions_missing", "no feature manifest found; feature names were not checked against it")
    check_features(paths["features"], paths["labels"], runs, definitions, r)
    check_leakage(raw, paths["features"], runs, r, all_runs=deep)

    partial = [p for p in (paths["partial_labels"], paths["partial_features"]) if p.exists()]
    if partial:
        r.add("warn", "coverage", "derived.partial_folders",
              "partial label and feature folders from earlier development runs are present; this validator reads only the full folders")
    r.stats["paths"] = {k: str(v) for k, v in paths.items()}
    return r


def write_report(report: Report, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
