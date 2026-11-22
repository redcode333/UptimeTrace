"""M3.2 labeling pipeline:  python -m ml.build_labels <dataset_dir> [--out DIR] [--workers N]

Reads the (read-only) M3.1 artifacts, writes artifacts/labels/<dataset_id>/ :
  labels/split=<split>/horizon_s=<H>/run=<run_id>.parquet   one file per run, hive-partitioned so a reader that asks for
                                                            split=train never opens a val / test file
  summary.json, labels_manifest.json

Row = one decision point (host, t). `split` and `horizon_s` come from the path (load them with `load_labels`).
Imports nothing from app / fastapi / simulator code: ML code stays independent of the API and of the generator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import availability as av
from . import labels as lb
from .io_safe import write_parquet_verified

LABELER_VERSION = "2.0"
HORIZONS = (900, 1800, 3600)            # 30 min is primary; 15 and 60 min are sensitivity horizons
OBS_COLUMNS = ["metric", "labels", "obs_ts", "env_ts"]          # NO `value`: labels never see telemetry values
SERIES_KEY = {"cpu": ("system.cpu.utilization", "{}"), "mem": ("system.memory.utilization", "{}"),
              "disk_root": ("system.disk.utilization", '{"mount":"/"}'), "disk_data": ("system.disk.utilization", '{"mount":"/data"}'),
              "rx": ("system.network.rx_bytes", '{"interface":"eth0"}'), "tx": ("system.network.tx_bytes", '{"interface":"eth0"}')}
ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts"


class LabelConfigError(ValueError):
    pass


def check_decision_config(cadence_s: int, delta: int, offset: float, origin_offset_s: int) -> None:
    """Spec section 2: feature-building configurations need 3.9 < offset < delta, delta a multiple of cadence, origin = run_start."""
    if not (0 <= offset < delta):
        raise LabelConfigError(f"decision_offset {offset} must satisfy 0 <= offset < delta {delta}")
    if not offset > 3.9:
        raise LabelConfigError(f"decision_offset {offset} must exceed 3.9 s (normal-tick availability guarantee)")
    if delta % cadence_s:
        raise LabelConfigError(f"delta {delta} must be a multiple of cadence_s {cadence_s}")
    if origin_offset_s != 0:
        raise LabelConfigError("sampling origin must be run_start (origin_offset_s == 0)")


def canonical_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def verify_source(root: Path) -> dict:
    """The labeler only reads a dataset that passed exhaustive validation and whose protocol hash matches."""
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    proto = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
    v = man["validation"]
    if canonical_hash(proto) != man["protocol_hash"]:
        raise LabelConfigError("protocol_hash mismatch")
    if v["envelopes_checked"] < v["envelopes_total"] or v["observations_checked"] != v["observations_total"] \
            or v["schema_failures"] or v["semantic_failures"]:
        raise LabelConfigError("source dataset was not exhaustively validated")
    return man


def _us(ts: str) -> int:
    return lb.to_us(ts)


def label_views(evaluable: np.ndarray, n_in: np.ndarray, any_p: np.ndarray):
    """The three label views for one horizon, from the evaluable mask and the window facts.

    Returns (class, scored, target, operational):
      class        excluded | positive | negative | unscorable
      scored       True only for positive and negative: the PRIMARY view. Unscorable decisions are never scored.
      target       primary label: 1 for positive, 0 for negative (meaningful only where scored)
      operational  SECONDARY view: 1 when any incident onset lies in the window (positive and unscorable); 0 where excluded
    """
    cls = np.where(evaluable, lb.label_class(n_in, any_p), "excluded").astype(object)
    scored = (cls == "positive") | (cls == "negative")
    target = (cls == "positive").astype(np.int8)
    operational = np.where(evaluable, (n_in > 0).astype(np.int8), 0).astype(np.int8)
    return cls, scored, target, operational


def label_one_run(args) -> dict:
    root, out, run, cadence, delta, offset, horizons = args
    root, out = Path(root), Path(out)
    start_us, end_us = _us(run["start_ts"]), _us(run["end_ts"])
    n_dec = -(-(end_us - start_us) // (delta * lb.ONE_US))                 # intervals; a decision needs t_j < run_end
    j = np.arange(n_dec, dtype=np.int64)
    t_us = start_us + int(round(offset * lb.ONE_US)) + j * delta * lb.ONE_US
    keep = t_us < end_us
    j, t_us = j[keep], t_us[keep]
    # --- availability (timestamps only)
    tbl = pq.read_table(root / "observations" / f"run={run['run_id']}.parquet", columns=OBS_COLUMNS)
    key = np.char.add(np.array(tbl.column("metric").to_pylist()), np.array(tbl.column("labels").to_pylist()))
    obs = (np.array(tbl.column("obs_ts").cast(pa.int64()).to_pylist(), dtype=np.int64) - start_us) / 1e6
    env = (np.array(tbl.column("env_ts").cast(pa.int64()).to_pylist(), dtype=np.int64) - start_us) / 1e6
    series_obs = {}
    for s, (m, l) in SERIES_KEY.items():
        sel = key == (m + l)
        o, e = obs[sel], env[sel]
        order = np.argsort(o, kind="stable")
        series_obs[s] = (o[order], e[order])
    t_s = (t_us - start_us) / 1e6
    mask = av.exclusion_masks(series_obs, t_s)
    insufficient = mask != 0
    # --- incidents (the only place truth is read)
    incs = lb.load_incident_views(root, run["run_id"])
    phase, owner = lb.active_phase(t_us, incs)
    active = phase != 0
    excluded_active = active & ~insufficient                                # precedence: insufficient data first
    host_id = run["host_id"]
    detail = np.full(t_us.size, "", dtype=object)
    for k in np.nonzero(insufficient)[0]:
        detail[k] = ";".join(av.decode(int(mask[k])))
    for k in np.nonzero(excluded_active)[0]:
        detail[k] = f"{lb.PHASE[int(phase[k])]}:{incs[int(owner[k])].incident_id}"
    exclusion = np.where(insufficient, "insufficient_data", np.where(active, "active_incident", None)).astype(object)
    # evaluable = not excluded. Excluded decisions (insufficient data, or inside an incident's oracle mask) have no label and no features.
    # Evaluable decisions are positive, negative or unscorable (the protocol's "evaluable_classes").
    evaluable = ~(insufficient | active)
    counts = {"decisions": int(t_us.size), "insufficient_data": int(insufficient.sum()), "active_incident": int(excluded_active.sum()),
              "active_phases": dict(Counter(lb.PHASE[int(p)] for p in phase[excluded_active])),
              "reasons": dict(Counter(code for k in np.nonzero(insufficient)[0] for code in av.decode(int(mask[k])))),
              "by_horizon": {}}
    split = run["split"]
    for H in horizons:
        n_in, any_p, nxt = lb.window_labels(t_us, incs, H)
        cls, scored, target, operational = label_views(evaluable, n_in, any_p)
        table = pa.table({
            "host_id": pa.array([host_id] * t_us.size, type=pa.string()).dictionary_encode(),
            "run_id": pa.array([run["run_id"]] * t_us.size, type=pa.string()).dictionary_encode(),
            "decision_index": pa.array(j.astype(np.int32)),
            "decision_ts": pa.array(t_us, type=pa.int64()).cast(pa.timestamp("us", tz="UTC")),
            "evaluable": pa.array(evaluable),                  # not excluded: these decisions have features and can alert
            "scored": pa.array(scored),                        # primary view only: positive or negative
            "target": pa.array(np.where(scored, target, 0).astype(np.int8), mask=~scored),
            "operational_target": pa.array(np.where(evaluable, operational, 0).astype(np.int8), mask=~evaluable),
            "label_class": pa.array(cls.astype(object), type=pa.string()).dictionary_encode(),
            "exclusion": pa.array(exclusion, type=pa.string()).dictionary_encode(),
            "exclusion_detail": pa.array(np.where(detail == "", None, detail), type=pa.string()),
            "n_incidents_in_window": pa.array(n_in),
            "next_onset_s": pa.array(nxt.astype(np.float32), mask=np.isnan(nxt)),
        })
        d = out / "labels" / f"split={split}" / f"horizon_s={H}"
        digest = write_parquet_verified(table, d / f"run={run['run_id']}.parquet")
        c = Counter(cls.tolist())
        counts["by_horizon"][H] = {"evaluable": int(evaluable.sum()), "scored": int(scored.sum()), "positive": c["positive"],
                                   "negative": c["negative"], "unscorable": c["unscorable"], "target_1": int((target == 1).sum()),
                                   "operational_1": int((operational == 1)[evaluable].sum()), "sha256": digest}
    return {"run_id": run["run_id"], "split": split, "counts": counts}


def build_labels(dataset_dir: Path, out_root: Path | None = None, workers: int = 3, horizons=HORIZONS, delta: int | None = None,
                 offset: float = 5.0, run_filter=None) -> Path:
    root = Path(dataset_dir)
    man = verify_source(root)
    view = json.loads((root / "view.json").read_text(encoding="utf-8"))
    cadence = view["cadence_s"]
    delta = delta or cadence
    check_decision_config(cadence, delta, offset, view.get("origin_offset_s", 0))
    runs = [json.loads(l) for l in open(root / "runs.jsonl", encoding="utf-8") if l.strip()]
    if run_filter:
        runs = [r for r in runs if run_filter(r)]
    out = (out_root or ARTIFACTS / "labels") / man["dataset_id"]
    t0 = time.time()
    jobs = [(str(root), str(out), r, cadence, delta, offset, tuple(horizons)) for r in runs]
    if workers <= 1:
        results = [label_one_run(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(label_one_run, jobs, chunksize=1))
    results.sort(key=lambda r: r["run_id"])
    summary = summarize(results, horizons)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    files = sorted((out / "labels").rglob("*.parquet"))
    manifest = {
        "labeler_version": LABELER_VERSION, "source_dataset_id": man["dataset_id"], "source_protocol_hash": man["protocol_hash"],
        "source_code_hash": man["code_hash"], "spec_version": man.get("spec_version"),
        "config": {"delta_s": delta, "decision_offset_s": offset, "cadence_s": cadence, "horizons_s": list(horizons),
                   "primary_horizon_s": 1800, "origin": "run_start"},
        "definition": "Excluded (no label, no features): t in [onset, oracle_mask_end) of any incident, or failing the data-sufficiency rule. "
                      "Evaluable decisions are positive (a PRECURSOR onset in (t, t+H]), negative (no onset in (t, t+H]) or unscorable "
                      "(onsets in (t, t+H] but none PRECURSOR). PRIMARY view: target = 1 for positive, 0 for negative, scored only for those; "
                      "unscorable is never scored. SECONDARY operational view: operational_target = 1 for positive and unscorable.",
        "n_runs": len(results), "n_files": len(files), "seconds": round(time.time() - t0, 1),
        "files_sha256": {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    }
    (out / "labels_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return out


def summarize(results: list[dict], horizons) -> dict:
    out = {}
    splits = sorted({r["split"] for r in results})
    for sp in splits + ["total"]:
        rs = [r for r in results if sp == "total" or r["split"] == sp]
        entry = {"runs": len(rs), "decisions": sum(r["counts"]["decisions"] for r in rs),
                 "excluded_insufficient_data": sum(r["counts"]["insufficient_data"] for r in rs),
                 "excluded_active_incident": sum(r["counts"]["active_incident"] for r in rs),
                 "active_incident_phases": dict(sum((Counter(r["counts"]["active_phases"]) for r in rs), Counter())),
                 "insufficient_data_reasons": dict(sum((Counter(r["counts"]["reasons"]) for r in rs), Counter())), "horizons": {}}
        for H in horizons:
            h = {k: sum(r["counts"]["by_horizon"][H][k] for r in rs) for k in ("scored", "positive", "negative", "unscorable", "target_1")}
            h["prevalence_target"] = round(h["target_1"] / h["scored"], 5) if h["scored"] else None
            entry["horizons"][str(H)] = h
        out[sp] = entry
    return out


def load_labels(out_dir: Path, splits=None, horizons=None, columns=None, runs=None) -> pa.Table:
    """Load labels for the requested splits / horizons only. Files of other splits are never opened (not even for schema
    discovery); `split` and `horizon_s` columns are added from the directory names. Row order: split, horizon, run, time."""
    base = Path(out_dir) / "labels"
    split_dirs = sorted(d for d in base.glob("split=*") if splits is None or d.name.split("=", 1)[1] in set(splits))
    if splits is not None and len(split_dirs) != len(set(splits)):
        raise FileNotFoundError(f"requested splits {sorted(set(splits))} not all present under {base}")
    tables = []
    for sd in split_dirs:
        split = sd.name.split("=", 1)[1]
        for hd in sorted(sd.glob("horizon_s=*")):
            h = int(hd.name.split("=", 1)[1])
            if horizons is not None and h not in {int(x) for x in horizons}:
                continue
            for f in sorted(hd.glob("run=*.parquet")):
                if runs is not None and f.stem.split("=", 1)[1] not in set(runs):
                    continue
                t = pq.ParquetFile(f).read(columns=columns)      # ParquetFile: no automatic hive partition columns
                t = t.append_column("split", pa.array([split] * t.num_rows, type=pa.string()).dictionary_encode())
                tables.append(t.append_column("horizon_s", pa.array([h] * t.num_rows, type=pa.int32())))
    if not tables:
        raise FileNotFoundError("no label files match the request")
    return pa.concat_tables(tables)


def targets_numpy(table: pa.Table) -> np.ndarray:
    """int8 targets: 1 / 0, and -1 where the decision point is excluded (target is null).

    Do NOT use Array.fill_null on these columns: pyarrow 21.0.0 returns wrong values for arrays of >= ~1000 elements that
    contain null runs. Filter on the non-null `scored` column, or use this helper."""
    v = table.column("target").combine_chunks().to_numpy(zero_copy_only=False)
    return np.where(np.isnan(v), -1, v).astype(np.int8)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ml.build_labels")
    ap.add_argument("dataset")
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    out = build_labels(Path(a.dataset), Path(a.out) if a.out else None, a.workers)
    s = json.loads((out / "summary.json").read_text())
    print(f"labels written to {out}")
    for sp, e in s.items():
        print(f"{sp:9s} runs {e['runs']:3d} decisions {e['decisions']:>8d} insufficient {e['excluded_insufficient_data']:>6d} "
              f"active {e['excluded_active_incident']:>7d}")
        for H, h in e["horizons"].items():
            print(f"   H={int(H)//60:>2d}min scored {h['scored']:>8d} positive {h['positive']:>6d} negative {h['negative']:>8d} "
                  f"unscorable {h['unscorable']:>5d} target=1 {h['target_1']:>6d} prevalence {h['prevalence_target']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
