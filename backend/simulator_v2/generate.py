"""Dataset generation: worker pool, per-run validation, sidecars, manifest, resource report (spec 9-11, 19)."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import statistics
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from . import constants as C
from .alloc import allocate
from .bounds import buffered_bounds, c_max, normal_bounds
from .protocol import build_protocol, protocol_hash
from .run import footprint_violations, prepare_run, render_view
from .validate import Validator
from .writer import write_json, write_jsonl, write_run_parquet

REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO / "artifacts" / "datasets"
PKG = Path(__file__).resolve().parent


class DatasetExists(FileExistsError):
    """The dataset folder already holds files. Generation refuses to write into it unless told to replace a dataset."""


def _refuse_or_clear(root: Path, overwrite: bool) -> None:
    """Protect earlier runs. An empty or missing folder is fine. A folder holding a previous dataset (it has a manifest) is
    replaced only when `overwrite` is set. Any other non-empty folder is never touched."""
    if not root.exists() or not any(root.iterdir()):
        return
    if not (root / "manifest.json").exists():
        raise DatasetExists(f"{root} is not empty and holds no dataset manifest; choose another location")
    if not overwrite:
        raise DatasetExists(f"{root} already holds a generated dataset; pass overwrite=True (--overwrite) to replace it")
    shutil.rmtree(root)


def code_hash() -> str:
    """SHA-256 of the v2 source tree (sorted relative paths + file bytes)."""
    h = hashlib.sha256()
    for f in sorted(PKG.rglob("*.py")):
        h.update(str(f.relative_to(PKG)).replace("\\", "/").encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def _peak_mb() -> float | None:
    try:
        import psutil
        mi = psutil.Process().memory_info()
        return getattr(mi, "peak_wset", mi.rss) / 2 ** 20
    except Exception:      # psutil is an optional dependency
        return None


def run_worker(args) -> dict:
    master_seed, spec, out_dir, cadence, jitter_on, kind, do_validate, force_one_each = args
    t0 = time.perf_counter()
    truth = prepare_run(master_seed, spec, force_one_each)
    t1 = time.perf_counter()
    out = render_view(truth, cadence, jitter_on)
    t2 = time.perf_counter()
    val = Validator()
    if do_validate:
        for env in out.envelopes:
            val.check(env)
    t3 = time.perf_counter()
    pq_path = Path(out_dir) / "observations" / f"run={spec.run_id}.parquet"
    n_rows = write_run_parquet(pq_path, out.columns)
    t4 = time.perf_counter()
    assert n_rows == out.ledger.observations_normal + out.ledger.observations_buffered
    return {
        "run_index": spec.run_index, "run_id": spec.run_id, "split": spec.split,
        "records": truth.records, "view_gaps": out.view_gaps, "ledger": out.ledger.as_dict(),
        "validation": val.counts(), "validation_messages": val.messages,
        "footprint_violations": footprint_violations(truth), "n_envelopes": out.n_envelopes,
        "rows": n_rows, "parquet_bytes": pq_path.stat().st_size, "stats": truth.stats,
        "t_prepare": t1 - t0, "t_render": t2 - t1, "t_validate": t3 - t2, "t_write": t4 - t3, "peak_mb": _peak_mb(),
    }


def _strip(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def generate_dataset(master_seed: int, out_root: Path | None = None, workers: int | None = None, cadence: int = 60,
                     jitter_on: bool = True, kind: str = "primary", run_filter=None, label: str | None = None,
                     runs_override=None, validate: bool = True, force_one_each: bool = False,
                     overwrite: bool = False) -> Path:
    t_start = time.perf_counter()
    runs, alloc_doc = allocate(master_seed) if runs_override is None else (runs_override, {"rule": "explicit dev runs"})
    if run_filter is not None:
        runs = [r for r in runs if run_filter(r)]
    protocol = build_protocol()
    phash = protocol_hash(protocol)
    ds_id = label or f"uptimetrace-m31-seed{master_seed}-{phash[:12]}"
    root = (out_root or ARTIFACTS) / ds_id
    _refuse_or_clear(root, overwrite)
    root.mkdir(parents=True, exist_ok=True)
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    jobs = [(master_seed, r, str(root), cadence, jitter_on, kind, validate, force_one_each) for r in runs]
    results: list[dict] = []
    if workers == 1:
        results = [run_worker(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(run_worker, jobs, chunksize=1))
    results.sort(key=lambda r: r["run_index"])
    gen_wall = time.perf_counter() - t_start

    write_json(root / "protocol.json", protocol)
    write_json(root / "view.json", {"cadence_s": cadence, "latent_dt_s": C.LATENT_DT, "jitter_on": jitter_on,
                                    "dataset_kind": kind, "origin_offset_s": 0})
    write_jsonl(root / "runs.jsonl", [r["records"]["runs"] for r in results])
    write_jsonl(root / "faults.jsonl", [f for r in results for f in r["records"]["faults"]])
    write_jsonl(root / "incidents.jsonl", [_strip(i) for r in results for i in r["records"]["incidents"]])
    write_jsonl(root / "benign_episodes.jsonl", [b for r in results for b in r["records"]["benign"]])
    write_jsonl(root / "gaps.jsonl", [g for r in results for g in r["records"]["gaps"]])
    write_jsonl(root / "view_gaps.jsonl", [g for r in results for g in r["view_gaps"]])

    manifest = build_manifest(master_seed, ds_id, protocol, phash, results, alloc_doc, cadence, jitter_on, kind,
                              workers, gen_wall)
    write_json(root / "manifest.json", manifest)
    write_file_index(root)
    return root


def write_file_index(root: Path) -> dict:
    """Index of the dataset folder: path, size and SHA-256 of every data file, so any copy can be checked against it.

    manifest.json is not hashed byte for byte, because its `resources` block holds wall-clock timings that differ between
    identical runs. It is recorded by the SHA-256 of its content with that block removed."""
    skip = {"file_index.json", "manifest.json"}
    entries = []
    for f in sorted(p for p in root.rglob("*") if p.is_file() and p.name not in skip):
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        entries.append({"path": f.relative_to(root).as_posix(), "bytes": f.stat().st_size, "sha256": h.hexdigest()})
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("resources", None)
    manifest_content = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
    index = {"dataset_id": root.name, "files": entries,
             "manifest": {"path": "manifest.json", "content_sha256_without_resources": manifest_content}}
    write_json(root / "file_index.json", index)
    return index


LEDGER_SUM = ["ticks_nominal", "lost_agent_down", "lost_planned_reboot_gap", "lost_panic_gap", "normal_emitting",
              "normal_all_omitted", "buffered_emitting", "buffered_all_omitted", "partitions_total",
              "partitions_with_replay", "envelopes_normal", "envelopes_replay", "observations_candidate",
              "observations_omitted_normal", "observations_omitted_buffered", "observations_normal",
              "observations_buffered"]


def accounting_block(ledgers: list[dict]) -> dict:
    s = {k: sum(l[k] for l in ledgers) for k in LEDGER_SUM}
    ticks_normal = s["normal_emitting"] + s["normal_all_omitted"]
    ticks_buffered = s["buffered_emitting"] + s["buffered_all_omitted"]
    return {
        "ticks": {"ticks_nominal": s["ticks_nominal"],
                  "ticks_lost": {"agent_down": s["lost_agent_down"], "planned_reboot_gap": s["lost_planned_reboot_gap"],
                                 "panic_gap": s["lost_panic_gap"]},
                  "ticks_produced": ticks_normal + ticks_buffered, "ticks_normal": ticks_normal,
                  "ticks_buffered": ticks_buffered, "ticks_normal_emitting": s["normal_emitting"],
                  "ticks_normal_all_omitted": s["normal_all_omitted"], "ticks_buffered_emitting": s["buffered_emitting"],
                  "ticks_buffered_all_omitted": s["buffered_all_omitted"]},
        "partitions": {"partitions_total": s["partitions_total"], "partitions_with_replay": s["partitions_with_replay"],
                       "partitions_without_replay": s["partitions_total"] - s["partitions_with_replay"]},
        "envelopes": {"envelopes_normal": s["envelopes_normal"], "envelopes_replay": s["envelopes_replay"],
                      "envelopes_total": s["envelopes_normal"] + s["envelopes_replay"]},
        "observations": {"observations_candidate": s["observations_candidate"],
                         "observations_omitted_normal": s["observations_omitted_normal"],
                         "observations_omitted_buffered": s["observations_omitted_buffered"],
                         "observations_omitted": s["observations_omitted_normal"] + s["observations_omitted_buffered"],
                         "observations_normal": s["observations_normal"], "observations_buffered": s["observations_buffered"],
                         "observations_total": s["observations_normal"] + s["observations_buffered"]},
    }


def build_manifest(master_seed, ds_id, protocol, phash, results, alloc_doc, cadence, jitter_on, kind, workers, gen_wall):
    by_split = {s: [r for r in results if r["split"] == s] for s in C.SPLITS}
    acct = {"total": accounting_block([r["ledger"] for r in results])}
    for s in C.SPLITS:
        acct[s] = accounting_block([r["ledger"] for r in by_split[s]])
    vsum = {k: sum(r["validation"][k] for r in results) for k in
            ("envelopes_checked", "observations_checked", "schema_failures", "semantic_failures")}
    validation = {"envelopes_total": acct["total"]["envelopes"]["envelopes_total"],
                  "observations_total": acct["total"]["observations"]["observations_total"], **vsum,
                  "footprint_violations": sum(len(r["footprint_violations"]) for r in results)}
    led = [r["ledger"] for r in results]
    norm_lag_obs = max(l["max_lag_obs_normal"] for l in led)
    availability = {
        "latency_model": "zero",
        "normal": {"lag_vs_obs_ts_bound_s": normal_bounds()[0], "lag_vs_nominal_bound_s": normal_bounds()[1],
                   "rule": "env_ts <= t", "observed_max_lag_vs_obs_ts_s": norm_lag_obs,
                   "observed_max_lag_vs_nominal_s": max(l["max_lag_nominal_normal"] for l in led)},
        "buffered": {"membership": "true_sample_time", "c_max": c_max(cadence),
                     "lag_vs_obs_ts_bound_s": buffered_bounds(c_max(cadence))[0],
                     "lag_vs_nominal_bound_s": buffered_bounds(c_max(cadence))[1], "rule": "replay send time",
                     "observed_max_lag_vs_obs_ts_s": max(l["max_lag_obs_buffered"] for l in led),
                     "observed_max_lag_vs_nominal_s": max(l["max_lag_nominal_buffered"] for l in led),
                     "observed_max_chunks_per_partition": max(l["max_chunks"] for l in led)},
        "note": "bounds are derived from the section 2 constants; observed_max_* are observed, not guarantees",
    }
    counts = {"incidents": Counter(), "faults": Counter()}
    for r in results:
        for inc in r["records"]["incidents"]:
            counts["incidents"][(r["split"], inc["family"], inc["group"], inc["silent_reason"] or "-")] += 1
        for f in r["records"]["faults"]:
            counts["faults"][(r["split"], f["family"], f["member_relation"] or "twin", f["outcome"])] += 1
    cell = lambda c: [{"split": k[0], "family": k[1], "group_or_relation": k[2], "silent_reason_or_outcome": k[3],
                       "n": v} for k, v in sorted(c.items(), key=lambda kv: tuple(map(str, kv[0])))]
    inc_by_sf = Counter((r["split"], i["family"]) for r in results for i in r["records"]["incidents"])
    low_n = [{"split": k[0], "family": k[1], "n": v, "flag": "LOW-N" if v < 30 else None,
              "suppress_estimate": v < 10} for k, v in sorted(inc_by_sf.items())]
    attempts = Counter()
    mx = 0
    fam_att = {}
    for r in results:
        for fam, role, a in r["stats"]["attempts"]:
            fam_att.setdefault((fam, role), []).append(a)
            mx = max(mx, a)
    reasons = Counter()
    by_family = Counter()
    for r in results:
        for rej in r["stats"]["rejections"]:
            reasons[rej["reason"]] += 1
            by_family[f"{rej['reason']}/{rej.get('family', '-')}"] += 1
    rejection = {"max_attempts_any_fault": mx, "replan_reasons": dict(reasons), "replan_reasons_by_family": dict(by_family),
                 "runs_with_replans": sum(1 for r in results if r["stats"]["replans"]),
                 "replan_limit": C.MAX_REPLANS, "attempt_limit": C.MAX_ATTEMPTS,
                 "replans_total": sum(r["stats"]["replans"] for r in results),
                 "replans_max_run": max(r["stats"]["replans"] for r in results),
                 "mean_attempts": {f"{k[0]}/{k[1]}": round(float(np.mean(v)), 3) for k, v in sorted(fam_att.items())}}
    times = [r["t_prepare"] + r["t_render"] + r["t_validate"] + r["t_write"] for r in results]
    val_t = sum(r["t_validate"] for r in results)
    peaks = [r["peak_mb"] for r in results if r["peak_mb"] is not None]
    resources = {
        "workers": workers, "generation_wall_clock_s": round(gen_wall, 2),
        "per_run_mean_s": round(statistics.mean(times), 3),
        "per_run_p95_s": round(float(np.percentile(times, 95)), 3),
        "validation_envelopes_per_second_per_worker": round(validation["envelopes_total"] / max(val_t, 1e-9), 1),
        "parquet_bytes_total": sum(r["parquet_bytes"] for r in results),
        "parquet_bytes_per_run_mean": int(statistics.mean(r["parquet_bytes"] for r in results)),
        "peak_memory_per_worker_mb": round(max(peaks), 1) if peaks else None,
        "cpu_model": platform.processor(), "cpu_count": os.cpu_count(), "python": platform.python_version(),
        "numpy": np.__version__,
    }
    import pyarrow
    resources["pyarrow"] = pyarrow.__version__
    return {
        "dataset_id": ds_id, "simulator_version": C.SIMULATOR_VERSION, "spec_version": C.SPEC_VERSION,
        "code_hash": code_hash(), "master_seed": master_seed,
        "config": {"cadence_s": cadence, "jitter_on": jitter_on, "dataset_kind": kind,
                   "generation_wallclock": C.GENERATION_WALLCLOCK, "n_runs": len(results)},
        "protocol_hash": phash, "protocol_version": protocol["protocol_version"], "numpy_version": np.__version__,
        "accounting": acct, "availability": availability, "validation": validation,
        "exposure": {"partition": "tick-anchored, decision-quantised", "oracle_mask": True,
                     "oracle_mask_end_rule": "max(cooldown_end, max_member_conflict_end)",
                     "filled_at_evaluation_time": True},
        "allocation": alloc_doc,
        "counts_per_split": {s: len(by_split[s]) for s in C.SPLITS},
        "counts": {"incidents": cell(counts["incidents"]), "faults": cell(counts["faults"])},
        "low_n": low_n, "rejection_statistics": rejection, "resources": resources,
    }
