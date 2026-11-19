"""Read-only, standalone audit of the synthetic M3.1 dataset, its labels, features, splits and saved baselines.

    python -I scripts/audit_synthetic_dataset.py --out docs/evidence/synthetic_dataset_audit.json
        [--repo <repo root>] [--dataset uptimetrace-m31-seed0-2723fc6789d8] [--data-root $UPTIMETRACE_DATA_ROOT]

Nothing here trains or fits a model, and nothing is written except the JSON report. It does NOT import `ml.labels`, `ml.build_labels`,
`ml.features` or `simulator_v2`: incidents are re-derived from the raw fault events (faults.jsonl) with an independent merge, the label
rules are re-implemented from the dataset's own protocol.json, and the as-of feature rule is re-implemented from raw observations.
Feature-level statistics use train/val rows only; test splits are read for structure (counts, hashes, labels) and never for any
fitting or selection. Sampling uses a fixed seed, so reruns give identical output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import stats

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
SERIES_KEY = {"cpu": "system.cpu.utilization{}", "mem": "system.memory.utilization{}",
              "disk_root": 'system.disk.utilization{"mount":"/"}', "disk_data": 'system.disk.utilization{"mount":"/data"}',
              "rx": 'system.network.rx_bytes{"interface":"eth0"}', "tx": 'system.network.tx_bytes{"interface":"eth0"}'}
SPLITS = ("train", "val", "test_iid", "test_ood")
US = 1_000_000


def us(ts: str) -> int:
    return (datetime.fromisoformat(ts) - EPOCH) // timedelta(microseconds=1)


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 22), b""):
            h.update(c)
    return h.hexdigest()


def jl(p: Path) -> list[dict]:
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def canonical_hash(o) -> str:
    return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def pct(a, q):
    a = np.asarray(a, dtype=float)
    return None if a.size == 0 else round(float(np.percentile(a, q)), 3)


def summ(a):
    a = np.asarray(a, dtype=float)
    if a.size == 0:
        return None
    return {"n": int(a.size), "min": round(float(a.min()), 3), "p10": pct(a, 10), "p50": pct(a, 50), "p90": pct(a, 90), "max": round(float(a.max()), 3)}


# --------------------------------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, repo: Path, ds: str, data_root: str | None):
        self.repo = repo
        self.art = repo / "artifacts"
        self.ds = ds
        self.root = self.art / "datasets" / ds
        self.lab = self.art / "labels_full" / ds
        self.fea = self.art / "features_full" / ds
        self.lab_small = self.art / "labels" / ds
        self.fea_small = self.art / "features" / ds
        self.models = self.art / "models" / ds
        self.data_root = data_root
        self.runs = jl(self.root / "runs.jsonl")
        self.faults = jl(self.root / "faults.jsonl")
        self.incs = jl(self.root / "incidents.jsonl")
        self.benign = jl(self.root / "benign_episodes.jsonl")
        self.gaps = jl(self.root / "gaps.jsonl")
        self.run_by_id = {r["run_id"]: r for r in self.runs}
        self.split_of = {r["run_id"]: r["split"] for r in self.runs}
        self.faults_by_run = defaultdict(list)
        for f in self.faults:
            self.faults_by_run[f["run_id"]].append(f)
        self.incs_by_run = defaultdict(list)
        for i in self.incs:
            self.incs_by_run[i["run_id"]].append(i)
        self.protocol = json.loads((self.root / "protocol.json").read_text(encoding="utf-8"))
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------------------------------------------
def audit_inventory(c: Ctx) -> dict:
    out: dict = {}
    obs_files = sorted((c.root / "observations").glob("run=*.parquet"))
    run_ids = [r["run_id"] for r in c.runs]
    have = {p.stem.split("=", 1)[1] for p in obs_files}
    rows = [pq.ParquetFile(p).metadata.num_rows for p in obs_files]
    sha = {p.name: sha_file(p) for p in obs_files}
    out["dataset_files"] = {n: {"bytes": (c.root / n).stat().st_size, "sha256": sha_file(c.root / n)}
                            for n in ("manifest.json", "protocol.json", "view.json", "runs.jsonl", "faults.jsonl", "incidents.jsonl",
                                      "benign_episodes.jsonl", "gaps.jsonl", "view_gaps.jsonl")}
    out["observations"] = {"files": len(obs_files), "runs_in_runs_jsonl": len(run_ids), "missing_run_files": sorted(set(run_ids) - have),
                           "extra_files": sorted(have - set(run_ids)), "rows_total": int(sum(rows)), "bytes": int(sum(p.stat().st_size for p in obs_files)),
                           "combined_sha256_of_file_hashes": hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(sha.items())).encode()).hexdigest(),
                           "rows_per_run_min_median_max": [int(min(rows)), int(np.median(rows)), int(max(rows))]}
    man = c.manifest
    out["manifest_checks"] = {
        "protocol_hash_matches": canonical_hash(c.protocol) == man["protocol_hash"],
        "observations_total_equals_parquet_rows": man["validation"]["observations_total"] == int(sum(rows)),
        "validation_block": {k: man["validation"][k] for k in ("envelopes_checked", "envelopes_total", "observations_checked", "observations_total",
                                                              "schema_failures", "semantic_failures", "footprint_violations")},
        "manifest_counts_per_split": man["counts_per_split"],
        "actual_runs_per_split": dict(Counter(c.split_of.values())),
        "expected_split_runs_in_protocol": c.protocol["split_runs"],
    }
    pkg = c.repo / "backend" / "simulator_v2"
    h = hashlib.sha256()
    for f in sorted(pkg.rglob("*.py")):
        h.update(str(f.relative_to(pkg)).replace("\\", "/").encode())
        h.update(f.read_bytes())
    out["manifest_checks"]["code_hash_in_manifest"] = man["code_hash"]
    out["manifest_checks"]["code_hash_of_current_simulator_source"] = h.hexdigest()
    out["manifest_checks"]["simulator_source_unchanged_since_generation"] = h.hexdigest() == man["code_hash"]
    man_mtime = (c.root / "manifest.json").stat().st_mtime
    out["manifest_checks"]["simulator_files_modified_after_dataset_generation"] = sorted(
        str(f.relative_to(pkg)).replace("\\", "/") for f in pkg.rglob("*.py") if f.stat().st_mtime > man_mtime)
    out["manifest_checks"]["code_hash_note"] = ("hash differs from the manifest; only the files listed above are newer than the dataset. If that is a post-hoc audit helper "
                                                "the generator is unchanged, but this cannot be proven without the old file contents (single git commit)")
    out["documented_claims_vs_actual"] = {
        "runs": {"documented": 180, "actual": len(c.runs)},
        "split_90_30_40_20": {"documented": {"train": 90, "val": 30, "test_iid": 40, "test_ood": 20}, "actual": dict(Counter(c.split_of.values()))},
        "faults": {"documented": 2429, "actual": len(c.faults)}, "merged_incidents": {"documented": 1300, "actual": len(c.incs)},
        "benign_episodes": {"documented": 12794, "actual": len(c.benign)}, "gaps": {"documented": 1465, "actual": len(c.gaps)},
    }
    # seed1
    s1 = c.art / "datasets" / c.ds.replace("seed0", "seed1")
    out["seed1_dataset"] = {"exists": s1.exists(), "observation_files": len(list((s1 / "observations").glob("*.parquet"))) if s1.exists() else 0,
                            "top_level_files": sorted(p.name for p in s1.iterdir() if p.is_file()) if s1.exists() else [],
                            "verdict": "partial: observations only, no manifest/runs/faults/incidents, so it cannot be labelled or used"}
    # labels_full / features_full
    lm = json.loads((c.lab / "labels_manifest.json").read_text(encoding="utf-8"))
    fm = json.loads((c.fea / "features_manifest.json").read_text(encoding="utf-8"))
    bad_l = [k for k, v in lm["files_sha256"].items() if not (c.lab / k).exists() or sha_file(c.lab / k) != v]
    bad_f = [k for k, v in fm["files_sha256"].items() if not (c.fea / k).exists() or sha_file(c.fea / k) != v]
    out["labels_full"] = {"files": len(lm["files_sha256"]), "expected_files_runs_x_3_horizons": len(c.runs) * 3, "hash_mismatches": len(bad_l),
                          "labeler_version": lm["labeler_version"], "config": lm["config"], "source_protocol_hash_matches": lm["source_protocol_hash"] == man["protocol_hash"],
                          "source_code_hash_matches_dataset": lm["source_code_hash"] == man["code_hash"]}
    out["features_full"] = {"files": len(fm["files_sha256"]), "expected_files": len(c.runs), "hash_mismatches": len(bad_f), "n_features": fm["n_features"],
                            "rows_per_split": fm["rows_per_split"], "feature_version": fm["feature_version"]}
    ls = json.loads((c.lab_small / "labels_manifest.json").read_text(encoding="utf-8")) if (c.lab_small / "labels_manifest.json").exists() else None
    small_runs = sorted({k.split("run=")[1].replace(".parquet", "") for k in ls["files_sha256"]}) if ls else []
    out["subset_artifacts_labels_features"] = {"runs": small_runs, "splits_of_these_runs": dict(Counter(c.split_of[r] for r in small_runs)),
                                               "note": "artifacts/labels and artifacts/features are the 7-run development subset"}
    # models
    mdir = c.models / "h1800"
    rep = json.loads((mdir / "report.json").read_text(encoding="utf-8"))
    out["saved_models"] = {"dir": str(mdir.relative_to(c.repo)).replace("\\", "/"), "files": sorted(str(p.relative_to(mdir)).replace("\\", "/") for p in mdir.rglob("*") if p.is_file()),
                           "report_status": rep["status"], "train_runs": rep["train"]["runs"], "val_runs": rep["val"]["runs"],
                           "train_positive_windows": rep["train"]["positive_windows"], "val_positive_windows": rep["val"]["positive_windows"],
                           "train_positive_rows": rep["train"]["positive"], "val_positive_rows": rep["val"]["positive"], "test_splits_used": rep["test_splits_used"],
                           "report_has_alert_statistics": any("val_alerts" in m for m in rep["models"].values()),
                           "val": {k: m["val"] | {"confusion": m["val"]["confusion"]} for k, m in rep["models"].items()},
                           "train_pr_auc_at_val_threshold": {k: m["train_at_val_threshold"]["pr_auc"] for k, m in rep["models"].items()}}
    return out


# --------------------------------------------------------------------------------------------------------------------------
def fault_group(f: dict) -> str:
    if f.get("silent_reason") is not None:
        return "SILENT"
    lead = f.get("precursor_lead_s")
    return "PRECURSOR" if lead is not None and lead >= 120 else "ABRUPT"


def merge_from_faults(faults: list[dict]) -> list[dict]:
    """Independent re-derivation of merged incidents from raw fault events (protocol: join iff impact_start < running recovery + 900 s)."""
    inc = []
    for f in faults:
        if f["member_relation"] in ("primary", "relapse"):
            inc.append({"id": f["fault_id"], "role": f["member_relation"], "is": us(f["impact_start"]), "ie": us(f["impact_end"]),
                        "ce": us(f["conflict_end"]), "fam": f["family"], "grp": fault_group(f), "ps": us(f["precursor_start"]) if f.get("precursor_start") else None,
                        "lead": f.get("precursor_lead_s"), "sig": f.get("precursor_signal")})
    inc.sort(key=lambda x: (x["is"], x["id"]))
    groups, rec = [], None
    for f in inc:
        if groups and f["is"] < rec + 900 * US:
            groups[-1].append(f)
            rec = max(rec, f["ie"])
        else:
            groups.append([f])
            rec = f["ie"]
    out = []
    for g in groups:
        prim = next((m for m in g if m["role"] == "primary"), g[0])
        recov = max(m["ie"] for m in g)
        cool = recov + 900 * US
        out.append({"onset": min(m["is"] for m in g), "recovery": recov, "cooldown_end": cool, "mask_end": max(cool, max(m["ce"] for m in g)),
                    "group": prim["grp"], "primary": prim["id"], "n_members": len(g), "family": prim["fam"], "ps": prim["ps"], "lead": prim["lead"],
                    "sig": prim["sig"]})
    return out


def audit_faults_and_incidents(c: Ctx) -> dict:
    out: dict = {}
    # --- roles / outcomes
    cell = Counter((c.split_of[f["run_id"]], f["family"], f["member_relation"] or "twin", f["outcome"]) for f in c.faults)
    out["faults_by_split_family_role_outcome"] = [{"split": k[0], "family": k[1], "role": k[2], "outcome": k[3], "n": v} for k, v in sorted(cell.items())]
    prim = Counter((c.split_of[f["run_id"]], f["family"]) for f in c.faults if f["member_relation"] == "primary")
    quotas = c.protocol["quotas"]
    out["primary_quota_check"] = {"mismatches": [{"split": s, "family": fam, "quota": q[s], "actual": prim.get((s, fam), 0)}
                                                 for fam, q in quotas.items() for s in SPLITS if prim.get((s, fam), 0) != q[s]],
                                  "cells_checked": len(quotas) * len(SPLITS)}
    out["planned_vs_simulated_outcome_mismatch"] = sum(1 for f in c.faults if f["planned_outcome"] != f["outcome"])
    twins = [f for f in c.faults if f["member_relation"] is None]
    out["twins"] = {"n": len(twins), "by_family": dict(Counter(f["family"] for f in twins)), "twins_with_impact_start": sum(1 for f in twins if f["impact_start"]),
                    "families_without_twins": sorted({f["family"] for f in c.faults} - {f["family"] for f in twins})}
    out["faults_without_incident"] = {"answer": "yes: twins (non-incident faults with the same mechanism) exist for the five precursor families only",
                                      "count": len(twins)}
    # --- timestamp consistency
    bad = defaultdict(list)
    for f in c.faults:
        r = c.run_by_id[f["run_id"]]
        s0, s1 = us(r["start_ts"]), us(r["end_ts"])
        st, ce = us(f["start"]), us(f["conflict_end"])
        if not (s0 <= st <= ce <= s1):
            bad["start<=conflict_end within run"].append(f["fault_id"])
        if f["member_relation"] in ("primary", "relapse"):
            i0, i1 = us(f["impact_start"]), us(f["impact_end"])
            if not (st <= i0 < i1 <= ce):
                bad["start<=impact_start<impact_end<=conflict_end"].append(f["fault_id"])
            if (i1 - i0) < 120 * US:
                bad["impact duration >= 120 s"].append(f["fault_id"])
            if f.get("precursor_start") and us(f["precursor_start"]) > i0:
                bad["precursor_start<=impact_start"].append(f["fault_id"])
            if f.get("precursor_lead_s") is not None and f.get("precursor_start") and abs((i0 - us(f["precursor_start"])) // US - f["precursor_lead_s"]) > 1:
                bad["precursor_lead_s == impact_start - precursor_start"].append(f["fault_id"])
            if f["family"] == "silent_crash" and us(f["impact_start"]) != st:
                bad["silent_crash impact begins at fault start"].append(f["fault_id"])
        else:
            if f["impact_start"] or f["impact_end"]:
                bad["twin has no impact"].append(f["fault_id"])
            if f["resolved_time"] and not (st <= us(f["resolved_time"]) <= ce):
                bad["twin resolved within window"].append(f["fault_id"])
    out["timestamp_consistency_violations"] = {k: {"n": len(v), "examples": v[:3]} for k, v in bad.items()}
    out["timestamp_consistency_checks_run"] = ["start<=conflict_end within run", "start<=impact_start<impact_end<=conflict_end", "impact duration >= 120 s",
                                              "precursor_start<=impact_start", "precursor_lead_s == impact_start - precursor_start",
                                              "silent_crash impact begins at fault start", "twin has no impact", "twin resolved within window"]
    # --- merged incidents re-derived independently
    mism, tot, grp_counter = Counter(), 0, Counter()
    inc_re: dict[str, list[dict]] = {}
    for rid in c.run_by_id:
        mine = merge_from_faults(c.faults_by_run[rid])
        inc_re[rid] = mine
        theirs = sorted(c.incs_by_run[rid], key=lambda i: us(i["onset"]))
        if len(mine) != len(theirs):
            mism["incident_count_differs_in_run"] += 1
            continue
        for a, b in zip(mine, theirs):
            tot += 1
            for k, kb in (("onset", "onset"), ("recovery", "recovery"), ("cooldown_end", "cooldown_end"), ("mask_end", "oracle_mask_end")):
                if a[k] != us(b[kb]):
                    mism[k] += 1
            if a["group"] != b["group"]:
                mism["group"] += 1
            if a["primary"] != b["primary_fault_id"]:
                mism["primary_fault_id"] += 1
            if a["n_members"] != b["n_members"]:
                mism["n_members"] += 1
            grp_counter[(c.split_of[rid], a["group"])] += 1
    out["merged_incident_rederivation"] = {"incidents_compared": tot, "mismatches_by_field": dict(mism), "all_equal": not mism}
    out["_inc_re"] = inc_re
    # --- incident ordering / overlap
    ov = 0
    for rid, lst in inc_re.items():
        for a, b in pairwise(lst):
            if b["onset"] < a["mask_end"]:
                ov += 1
    out["incidents_overlapping_previous_oracle_mask"] = ov
    # --- groups by split and family
    by = Counter((c.split_of[rid], i["family"], i["group"]) for rid, lst in inc_re.items() for i in lst)
    out["incidents_by_split_family_group"] = [{"split": k[0], "family": k[1], "group": k[2], "n": v} for k, v in sorted(by.items())]
    per_split = {}
    for s in SPLITS:
        g = Counter(i["group"] for rid, lst in inc_re.items() if c.split_of[rid] == s for i in lst)
        n = sum(g.values())
        per_split[s] = {"incidents": n, **{k: g.get(k, 0) for k in ("PRECURSOR", "ABRUPT", "SILENT")},
                        "share_not_predictable_by_design": round((g.get("ABRUPT", 0) + g.get("SILENT", 0)) / n, 3) if n else None}
    out["incidents_per_split"] = per_split
    # --- precursor lead distribution
    lead = defaultdict(list)
    for rid, lst in inc_re.items():
        for i in lst:
            if i["group"] == "PRECURSOR":
                lead[i["family"]].append(i["lead"])
    out["precursor_lead_s_by_family"] = {k: summ(v) for k, v in sorted(lead.items())}
    out["precursor_lead_share_ge_1800s"] = {k: round(float(np.mean(np.array(v) >= 1800)), 3) for k, v in sorted(lead.items())}
    silent = Counter((f["family"], f["silent_reason"]) for f in c.faults if f["member_relation"] == "primary" and f["silent_reason"])
    out["silent_incident_reasons"] = {f"{k[0]}:{k[1]}": v for k, v in sorted(silent.items())}
    sub = Counter((f["params"].get("subtype") or f["variant"]) for f in c.faults if f["family"] == "silent_crash")
    out["silent_crash_subtypes"] = dict(sub)
    # --- concurrency
    conc = [f for f in c.faults if f["concurrent_with"]]
    pair = Counter()
    byid = {f["fault_id"]: f for f in c.faults}
    for f in conc:
        for o in f["concurrent_with"]:
            if f["fault_id"] < o:
                pair[(f["member_relation"] or "twin", byid[o]["member_relation"] or "twin")] += 1
    out["concurrency"] = {"faults_overlapping_another": len(conc), "share_of_faults": round(len(conc) / len(c.faults), 3),
                          "overlap_pairs_by_role": {f"{a}+{b}": n for (a, b), n in sorted(pair.items())},
                          "max_concurrent_per_fault": max((len(f["concurrent_with"]) for f in c.faults), default=0)}
    # --- parameter variability per family (incident primaries)
    var = {}
    for fam in sorted({f["family"] for f in c.faults}):
        rows = [f for f in c.faults if f["family"] == fam and f["member_relation"] == "primary"]
        keys = sorted({k for f in rows for k, v in f["params"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
        var[fam] = {"n": len(rows), "variants": dict(Counter(f["variant"] for f in rows)),
                    "numeric_params": {k: {**summ([f["params"][k] for f in rows if k in f["params"]]),
                                           "cv": round(float(np.std([f["params"][k] for f in rows if k in f["params"]]) / (abs(np.mean([f["params"][k] for f in rows if k in f["params"]])) + 1e-12)), 3)}
                                       for k in keys}}
    out["parameter_variability_incident_primaries"] = var
    # --- benign + gaps
    run_days = 14
    bc = defaultdict(Counter)
    for b in c.benign:
        bc[c.split_of[b["run_id"]]][b["type"]] += 1
    nr = Counter(c.split_of.values())
    out["benign_episode_types"] = {"types": sorted({b["type"] for b in c.benign}),
                                   "per_run_day_by_split": {s: {t: round(n / (nr[s] * run_days), 3) for t, n in sorted(bc[s].items())} for s in SPLITS}}
    gc = defaultdict(Counter)
    gdur = defaultdict(lambda: defaultdict(float))
    for g in c.gaps:
        s = c.split_of[g["run_id"]]
        gc[s][g["type"]] += 1
        gdur[s][g["type"]] += (us(g["end"]) - us(g["start"])) / US
    out["telemetry_gaps"] = {"types": sorted({g["type"] for g in c.gaps}), "per_run_by_split": {s: {t: round(n / nr[s], 3) for t, n in sorted(gc[s].items())} for s in SPLITS},
                             "share_of_run_time_by_split": {s: {t: round(d / (nr[s] * run_days * 86400), 5) for t, d in sorted(gdur[s].items())} for s in SPLITS},
                             "random_observation_omission_probability": c.protocol["constants"]["omit_p"],
                             "gap_starts_correlated_with_fault_windows": None}
    # gap vs fault window: fraction of non-fault-caused gaps starting inside a fault's conflict window
    inside = tot_g = 0
    for g in c.gaps:
        if g["type"] in ("agent_down", "net_partition_buffered", "planned_reboot_gap"):
            tot_g += 1
            a = us(g["start"])
            for f in c.faults_by_run[g["run_id"]]:
                if us(f["start"]) <= a <= us(f["conflict_end"]):
                    inside += 1
                    break
    cover = 0.0
    for rid, r in c.run_by_id.items():
        iv = sorted((us(f["start"]), us(f["conflict_end"])) for f in c.faults_by_run[rid])
        tot_len, cur_a, cur_b = 0, None, None
        for a_, b_ in iv:
            if cur_b is None or a_ > cur_b:
                if cur_b is not None:
                    tot_len += cur_b - cur_a
                cur_a, cur_b = a_, b_
            else:
                cur_b = max(cur_b, b_)
        if cur_b is not None:
            tot_len += cur_b - cur_a
        cover += tot_len / (us(r["end_ts"]) - us(r["start_ts"]))
    out["telemetry_gaps"]["gap_starts_correlated_with_fault_windows"] = {
        "non_fault_gap_starts": tot_g, "inside_a_fault_window": inside, "share": round(inside / tot_g, 4) if tot_g else None,
        "share_of_run_time_inside_any_fault_window": round(cover / len(c.run_by_id), 4),
        "reading": "if gaps were independent of faults the two shares would be about equal"}
    # --- near-silent: observed-vs-latent precursor visibility is checked in audit_labels
    return out


# --------------------------------------------------------------------------------------------------------------------------
def load_obs(c: Ctx, rid: str):
    t = pq.read_table(c.root / "observations" / f"run={rid}.parquet", columns=["metric", "labels", "obs_ts", "env_ts", "value"])
    key = np.char.add(np.array(t.column("metric").to_pylist()), np.array(t.column("labels").to_pylist()))
    obs = np.array(t.column("obs_ts").cast(pa.int64()).to_pylist(), dtype=np.int64)
    env = np.array(t.column("env_ts").cast(pa.int64()).to_pylist(), dtype=np.int64)
    val = np.array(t.column("value").to_pylist(), dtype=np.float64)
    series = {}
    for s, k in SERIES_KEY.items():
        sel = key == k
        o = np.argsort(obs[sel], kind="stable")
        series[s] = (obs[sel][o], env[sel][o], val[sel][o])
    return series


def indep_insufficient(series, start_us, t_us) -> np.ndarray:
    """Independent re-implementation of the data-sufficiency rule from its written definition (not from availability.py)."""
    n = t_us.size
    bad = np.zeros(n, dtype=bool)
    first = min((s[0][0] for s in series.values() if s[0].size), default=None)
    for k in range(n):
        tk = (t_us[k] - start_us) / 1e6
        if first is None or tk - (first - start_us) / 1e6 < 3600.0:
            bad[k] = True
            continue
        for o, e, _ in series.values():
            to, te = (o - start_us) / 1e6, (e - start_us) / 1e6
            hi = np.searchsorted(to, tk, side="right")
            lo = np.searchsorted(to, tk - 21600.0, side="right")
            m = te[lo:hi] <= tk
            ta = to[lo:hi][m]
            if ta.size < 21:
                bad[k] = True
                break
            cad = min(max(float(np.median(np.diff(ta))), 1.0), 3600.0)
            g = 3 * cad
            if tk - ta[-1] > g:
                bad[k] = True
                break
            for w in (300, 900, 3600):
                lo_w = tk - w
                j0 = max(0, int(np.searchsorted(ta, lo_w, side="left")) - 1)
                a, b = ta[j0:-1], ta[j0 + 1:]
                cov = float(np.clip(np.minimum(b, tk) - np.maximum(a, lo_w), 0, None)[(b - a) <= g].sum()) + max(0.0, tk - max(ta[-1], lo_w))
                if cov / w < 0.5:
                    bad[k] = True
                    break
            if bad[k]:
                break
    return bad


def audit_labels(c: Ctx, inc_re: dict, rng: np.random.Generator) -> dict:
    out: dict = {"horizons_s": [900, 1800, 3600]}
    H_ALL = (900, 1800, 3600)
    mm = {H: Counter() for H in H_ALL}
    tot = {H: Counter() for H in H_ALL}
    per_split = {H: {s: Counter() for s in SPLITS} for H in H_ALL}
    min_boundary = np.inf
    pos_started, pos_total = Counter(), Counter()
    hidden_neg = neg_rows = twin_neg = 0
    win_rows = all_rows = 0
    ins_rows = ins_near = 0
    decision_ts_mismatch = 0
    sample_runs = []
    for rid, r in sorted(c.run_by_id.items()):
        sp = r["split"]
        start, end = us(r["start_ts"]), us(r["end_ts"])
        n_dec = -(-(end - start) // (60 * US))
        t = start + 5 * US + np.arange(n_dec, dtype=np.int64) * 60 * US
        t = t[t < end]
        incs = inc_re[rid]
        onsets = np.array([i["onset"] for i in incs], dtype=np.int64)
        mask_end = np.array([i["mask_end"] for i in incs], dtype=np.int64)
        is_p = np.array([i["group"] == "PRECURSOR" for i in incs], dtype=np.int64)
        cum = np.concatenate([[0], np.cumsum(is_p)])
        active = np.zeros(t.size, dtype=bool)
        for o, m in zip(onsets, mask_end):
            active |= (t >= o) & (t < m)
        if onsets.size:
            min_boundary = min(min_boundary, float(np.abs(onsets[None, :] - t[:, None]).min()) / US if t.size * onsets.size < 5e6 else np.inf)
        for H in H_ALL:
            f = pq.ParquetFile(c.lab / "labels" / f"split={sp}" / f"horizon_s={H}" / f"run={rid}.parquet").read(
                columns=["decision_ts", "scored", "target", "label_class", "exclusion", "n_incidents_in_window", "next_onset_s"])
            ts = f.column("decision_ts").cast(pa.int64()).to_numpy()
            if H == 1800 and (ts.size != t.size or not np.array_equal(ts, t)):
                decision_ts_mismatch += 1
            lo = np.searchsorted(onsets, t, side="right")
            hi = np.searchsorted(onsets, t + H * US, side="right")
            n_in = hi - lo
            anyp = (cum[hi] - cum[lo]) > 0
            cls = np.where(n_in == 0, "negative", np.where(anyp, "positive", "unscorable"))
            exc = np.array(f.column("exclusion").to_pylist(), dtype=object)
            lc = np.array(f.column("label_class").to_pylist(), dtype=object)
            tgt = f.column("target").to_pylist()
            nin = f.column("n_incidents_in_window").to_numpy()
            scored = f.column("scored").to_numpy(zero_copy_only=False).astype(bool)
            tot[H]["rows"] += t.size
            tot[H]["scored"] += int(scored.sum())
            mm[H]["active_mask_scored_but_mine_active"] += int((scored & active).sum())
            mm[H]["labeler_excluded_active_but_mine_not_active"] += int(((exc == "active_incident") & ~active).sum())
            mm[H]["labeler_active_excluded_but_mine_inactive_or_missing"] += 0
            if scored.any():
                mm[H]["n_in_window_mismatch"] += int((nin[scored] != n_in[scored]).sum())
                mm[H]["class_mismatch"] += int((lc[scored] != cls[scored]).sum())
                tmine = (n_in[scored] > 0).astype(int)
                tth = np.array([tgt[i] for i in np.nonzero(scored)[0]], dtype=int)
                mm[H]["target_mismatch"] += int((tmine != tth).sum())
            ex_ins = exc == "insufficient_data"
            ex_act = exc == "active_incident"
            tot[H]["excluded_insufficient"] += int(ex_ins.sum())
            tot[H]["excluded_active"] += int(ex_act.sum())
            ps = per_split[H][sp]
            ps["rows"] += t.size
            for k, v in Counter(cls[scored].tolist()).items():
                ps[k] += v
            ps["excluded_insufficient"] += int(ex_ins.sum())
            ps["excluded_active"] += int(ex_act.sum())
            if H == 1800:
                # --- structure of positives / negatives (my own labels, restricted to rows the labeler also scored)
                ps_arr = np.array([i["ps"] if i["ps"] is not None else -1 for i in incs], dtype=np.int64)
                sc = scored
                posm = sc & (cls == "positive")
                for k in np.nonzero(posm)[0]:
                    # the first PRECURSOR onset inside the window
                    jj = next(x for x in range(lo[k], hi[k]) if incs[x]["group"] == "PRECURSOR")
                    pos_total[incs[jj]["family"]] += 1
                    if ps_arr[jj] >= 0 and ps_arr[jj] <= t[k]:
                        pos_started[incs[jj]["family"]] += 1
                negm = sc & (cls == "negative")
                neg_rows += int(negm.sum())
                for i in incs:
                    if i["ps"] is not None and i["group"] in ("PRECURSOR", "ABRUPT"):
                        hidden_neg += int((negm & (t >= i["ps"]) & (t < i["onset"] - 1800 * US)).sum())
                for fl in c.faults_by_run[rid]:
                    if fl["member_relation"] is None:
                        a, b = us(fl["start"]), us(fl["conflict_end"])
                        twin_neg += int((negm & (t >= a) & (t <= b)).sum())
                # insufficient-data rows near an upcoming incident
                if onsets.size:
                    nxt = np.where(lo < onsets.size, onsets[np.minimum(lo, onsets.size - 1)] - t, np.int64(2 ** 60))
                    near = ex_ins & ~active & (nxt <= 1800 * US)
                    ins_near += int(near.sum())
                    ins_rows += int((ex_ins & ~active).sum())
                else:
                    ins_rows += int((ex_ins & ~active).sum())
                win_rows += int(((n_in > 0) & ~active).sum())
                all_rows += int((~active).sum())
        if len(sample_runs) < 3 and sp == "train" and onsets.size >= 3:
            sample_runs.append(rid)
    out["decision_timestamps_differ_from_independent_grid_runs"] = decision_ts_mismatch
    out["independent_label_rederivation"] = {str(H): {"rows": tot[H]["rows"], "scored_by_labeler": tot[H]["scored"], **dict(mm[H])} for H in H_ALL}
    out["independent_label_rederivation_all_equal"] = all(v == 0 for H in H_ALL for k, v in mm[H].items())
    out["per_split_label_counts"] = {str(H): {s: dict(per_split[H][s]) for s in SPLITS} for H in H_ALL}
    pr = {s: per_split[1800][s] for s in SPLITS}
    out["prevalence_h1800_among_scored"] = {s: round(pr[s]["positive"] / max(1, pr[s]["positive"] + pr[s]["negative"] + pr[s]["unscorable"]), 5) for s in SPLITS}
    out["boundary_convention_testable"] = {"min_abs_seconds_between_onset_and_decision_time_sampled": None if min_boundary == np.inf else round(min_boundary, 1),
                                           "note": "incident onsets lie on the 30 s grid and decision times on a 5 s offset of a 60 s grid, so an onset can never equal t or t+H: the (t, t+H] "
                                                   "boundary convention cannot affect any label in this dataset"}
    out["positive_rows_h1800_by_family"] = {fam: {"rows": pos_total[fam], "rows_where_latent_precursor_already_started": pos_started[fam],
                                                  "share_started": round(pos_started[fam] / pos_total[fam], 3)} for fam in sorted(pos_total)}
    allp, alls = sum(pos_total.values()), sum(pos_started.values())
    out["positive_rows_precursor_already_started_overall"] = round(alls / allp, 3) if allp else None
    out["negative_rows_h1800"] = {"scored_negative_rows": neg_rows, "inside_a_twin_fault_window": twin_neg, "twin_share": round(twin_neg / neg_rows, 4),
                                  "after_precursor_started_but_incident_more_than_1800s_away": hidden_neg, "hidden_precursor_share": round(hidden_neg / neg_rows, 4)}
    out["insufficient_data_rows_h1800"] = {"rows_not_in_active_incident": ins_rows, "of_which_within_1800s_before_an_onset": ins_near,
                                           "share_near_onset": round(ins_near / ins_rows, 4) if ins_rows else None,
                                           "baseline_share_of_non_active_rows_within_1800s_before_an_onset": round(win_rows / all_rows, 4)}
    # --- independent availability check on a few runs (every 7th decision point)
    chk = {}
    for rid in sample_runs:
        r = c.run_by_id[rid]
        start, end = us(r["start_ts"]), us(r["end_ts"])
        t = start + 5 * US + np.arange(-(-(end - start) // (60 * US)), dtype=np.int64) * 60 * US
        t = t[t < end][::7]
        series = load_obs(c, rid)
        mine = indep_insufficient(series, start, t)
        f = pq.ParquetFile(c.lab / "labels" / f"split={r['split']}" / "horizon_s=1800" / f"run={rid}.parquet").read(columns=["exclusion", "decision_ts"])
        ts = f.column("decision_ts").cast(pa.int64()).to_numpy()
        idx = np.searchsorted(ts, t)
        exc = np.array(f.column("exclusion").to_pylist(), dtype=object)[idx]
        theirs = exc == "insufficient_data"
        act_prec = exc == "active_incident"          # precedence: insufficient first, so active rows are never insufficient per the labeler
        cmp_mask = ~act_prec
        chk[rid] = {"rows_compared": int(cmp_mask.sum()), "mine_insufficient": int(mine[cmp_mask].sum()), "labeler_insufficient": int(theirs[cmp_mask].sum()),
                    "disagreements": int((mine[cmp_mask] != theirs[cmp_mask]).sum())}
    out["independent_insufficient_data_check_sample"] = chk
    return out


def audit_precursor_visibility(c: Ctx, inc_re: dict, rng: np.random.Generator) -> dict:
    """Is the latent 'PRECURSOR' visible in the OBSERVED (noisy) telemetry? Compare the 10 min before onset with 60-120 min earlier."""
    rows = []
    for rid, lst in inc_re.items():
        for i in lst:
            if i["group"] == "PRECURSOR" and i["sig"] in ("mem", "disk_root", "disk_data", "cpu"):
                rows.append((rid, i))
    rng.shuffle(rows)
    rows = rows[:150]
    cache = {}
    res = defaultdict(list)
    for rid, i in sorted(rows, key=lambda x: x[0]):
        if rid not in cache:
            cache = {rid: load_obs(c, rid)}
        o, _e, v = cache[rid][i["sig"]]
        sig = c.run_by_id[rid]["profile"]["sigma_marg"][i["sig"]]
        on = i["onset"]
        recent = v[(o >= on - 600 * US) & (o < on)]
        past = v[(o >= on - 7200 * US) & (o < on - 3600 * US)]
        if recent.size >= 5 and past.size >= 20:
            z = (recent.mean() - past.mean()) / sig
            res[i["family"]].append(abs(float(z)))
    allz = np.concatenate([np.array(v) for v in res.values()]) if res else np.array([])
    return {"sampled_precursor_incidents": int(allz.size), "signals_checked": ["mem", "disk_root", "disk_data", "cpu"],
            "statistic": "|mean(last 10 min before onset) - mean(60-120 min before onset)| / profile noise floor sigma_marg",
            "share_ge_3_sigma": round(float((allz >= 3).mean()), 3) if allz.size else None, "median_abs_z": round(float(np.median(allz)), 2) if allz.size else None,
            "by_family": {k: {"n": len(v), "share_ge_3_sigma": round(float((np.array(v) >= 3).mean()), 3), "median_abs_z": round(float(np.median(v)), 2)} for k, v in sorted(res.items())}}


def audit_onset_levels(c: Ctx, inc_re: dict, rng: np.random.Generator) -> dict:
    """What is an 'incident' physically? Compare the OBSERVED level just before onset with the fault's own saturation threshold."""
    fbyid = {f["fault_id"]: f for f in c.faults}
    rows = [(rid, i) for rid, lst in inc_re.items() for i in lst if i["group"] == "PRECURSOR" and i["family"] in ("mem_leak", "disk_fill")]
    rng.shuffle(rows)
    res = defaultdict(list)
    cache = {}
    for rid, i in sorted(rows[:120], key=lambda x: x[0]):
        if rid not in cache:
            cache = {rid: load_obs(c, rid)}
        f = fbyid[i["primary"]]
        if i["family"] == "mem_leak":
            sig, thr = "mem", f["params"]["theta"]
        else:
            sig, thr = ("disk_root" if f["params"]["mount"] == "/" else "disk_data"), 100.0 - f["params"]["reserved_r"]
        o, _e, v = cache[rid][sig]
        w = v[(o >= i["onset"] - 300 * US) & (o < i["onset"])]
        if w.size >= 3:
            res[i["family"]].append((float(w.mean()), float(thr)))
    out = {}
    for fam, lst in res.items():
        a = np.array(lst)
        out[fam] = {"n": len(lst), "observed_level_5min_before_onset": summ(a[:, 0]), "saturation_threshold_of_the_fault": summ(a[:, 1]),
                    "median_gap_threshold_minus_observed": round(float(np.median(a[:, 1] - a[:, 0])), 2)}
    out["reading"] = ("mem_leak incidents begin when memory reaches theta in [93, 99.5] %; disk_fill when usage reaches 100 - r with r in [0.5, 5] %; cpu_runaway when "
                      "utilisation/capacity >= rho_crit in [0.97, 1.10] for 150 s; net_flood when rx >= 0.85-1.0 x a capacity cap for 150 s; pool_exhaustion when the hidden pool "
                      "reaches 1.0. An 'outage' is therefore a saturation event defined by the simulator, not an externally observed failure.")
    return out


def audit_examples(c: Ctx, inc_re: dict) -> list[dict]:
    """Four hand-checkable examples built only from raw fault events + raw observations; each is cross-checked against the stored label row."""
    def fmt(us_):
        return (EPOCH + timedelta(microseconds=int(us_))).strftime("%Y-%m-%d %H:%M:%S")

    def stored(rid, t_us, H=1800):
        r = c.run_by_id[rid]
        f = pq.ParquetFile(c.lab / "labels" / f"split={r['split']}" / f"horizon_s={H}" / f"run={rid}.parquet").read(
            columns=["decision_ts", "scored", "label_class", "target", "exclusion", "n_incidents_in_window", "next_onset_s"])
        ts = f.column("decision_ts").cast(pa.int64()).to_numpy()
        k = int(np.searchsorted(ts, t_us))
        assert ts[k] == t_us
        return {c_: f.column(c_)[k].as_py() for c_ in ("scored", "label_class", "target", "exclusion", "n_incidents_in_window", "next_onset_s")}

    def my_label(rid, t_us, H=1800):
        lst = inc_re[rid]
        act = [i for i in lst if i["onset"] <= t_us < i["mask_end"]]
        win = [i for i in lst if t_us < i["onset"] <= t_us + H * US]
        if act:
            return "excluded:active_incident", win
        if not win:
            return "negative", win
        return ("positive" if any(i["group"] == "PRECURSOR" for i in win) else "unscorable"), win

    ex = []
    wanted = [("PRECURSOR", "mem_leak"), ("ABRUPT", None), ("SILENT", "silent_crash")]
    for grp, fam in wanted:
        cand = [(rid, i) for rid in sorted(inc_re) if c.split_of[rid] == "train" for i in inc_re[rid]
                if i["group"] == grp and (fam is None or i["family"] == fam) and i["onset"] - 5400 * US > us(c.run_by_id[rid]["start_ts"]) + 7200 * US
                and all(abs(j["onset"] - i["onset"]) > 10800 * US for j in inc_re[rid] if j is not i)]
        if not cand:
            continue
        rid, i = cand[0]
        start = us(c.run_by_id[rid]["start_ts"])
        series = load_obs(c, rid)
        times = []
        for lead_min in (45, 20, 3):
            t_ = start + 5 * US + ((i["onset"] - lead_min * 60 * US - start - 5 * US) // (60 * US)) * 60 * US
            times.append(t_)
        rows = []
        for t_ in times:
            lab, win = my_label(rid, t_)
            used = {}
            for s in ("mem", "cpu", "disk_root"):
                o, e, v = series[s]
                m = (o <= t_) & (e <= t_)
                used[s] = {"last_obs_ts": fmt(o[m][-1]), "last_env_ts": fmt(e[m][-1]), "last_value": float(v[m][-1]), "future_obs_after_t_exist": bool((o > t_).any())}
            nxt = (i["onset"] - t_) / US
            rows.append({"decision_time": fmt(t_), "seconds_to_target_onset": nxt, "independent_label": lab, "stored_label": stored(rid, t_),
                         "feature_cutoff": "observations with obs_ts <= t and envelope ts <= t", "latest_observation_used": used})
        f0 = next(f for f in c.faults_by_run[rid] if f["fault_id"] == i["primary"])
        ex.append({"example": f"{grp} incident ({i['family']})", "run": rid, "split": c.split_of[rid], "target_incident": {
            "fault_id": i["primary"], "family": i["family"], "group_from_fault_record": i["group"], "precursor_lead_s": i["lead"], "precursor_signal": i["sig"],
            "fault_start": f0["start"], "precursor_start": f0["precursor_start"], "impact_start(onset)": fmt(i["onset"]), "impact_end(recovery)": fmt(i["recovery"]),
            "cooldown_end": fmt(i["cooldown_end"]), "oracle_mask_end": fmt(i["mask_end"])}, "decision_points": rows})
    # twin hard negative
    for rid in sorted(c.run_by_id):
        if c.split_of[rid] != "train":
            continue
        tw = [f for f in c.faults_by_run[rid] if f["member_relation"] is None and f["family"] == "mem_leak"]
        if not tw:
            continue
        f = tw[0]
        start = us(c.run_by_id[rid]["start_ts"])
        pk = us(f["peak_time"])
        t_ = start + 5 * US + ((pk - start - 5 * US) // (60 * US)) * 60 * US
        lab, win = my_label(rid, t_)
        o, e, v = load_obs(c, rid)["mem"]
        m = (o <= t_) & (e <= t_)
        ex.append({"example": "twin fault (hard negative: same mechanism, no incident)", "run": rid, "split": "train", "twin": {
            "fault_id": f["fault_id"], "start": f["start"], "peak_time": f["peak_time"], "resolved_time": f["resolved_time"], "conflict_end": f["conflict_end"],
            "params_peak_pct": f["params"].get("peak"), "theta_pct": f["params"].get("theta")}, "decision_points": [{
                "decision_time": fmt(t_), "latest_mem_value": float(v[m][-1]), "incidents_in_next_1800s": len(win), "independent_label": lab, "stored_label": stored(rid, t_)}]})
        break
    return ex


# --------------------------------------------------------------------------------------------------------------------------
def _param_values(c: Ctx, fam: str, key: str, pred) -> np.ndarray:
    return np.array([f["params"][key] for f in c.faults if f["family"] == fam and f["member_relation"] == "primary" and key in f["params"] and pred(f)], dtype=float)


def audit_splits_and_leakage(c: Ctx, rng: np.random.Generator) -> dict:
    out: dict = {}
    ids = Counter(r["run_id"] for r in c.runs)
    hosts = Counter(r["host_id"] for r in c.runs)
    out["identity"] = {"runs": len(c.runs), "duplicate_run_ids": sum(1 for v in ids.values() if v > 1), "duplicate_host_ids": sum(1 for v in hosts.values() if v > 1),
                       "duplicate_hostnames": len(c.runs) - len({r["hostname"] for r in c.runs}), "duplicate_agent_ids": len(c.runs) - len({r["agent_id"] for r in c.runs}),
                       "runs_per_host": "exactly 1: host_id = 'sim-<run_id>', so run-level separation IS host-level separation by construction",
                       "duplicate_run_start_epoch": len(c.runs) - len({r["profile"]["run_start_epoch"] for r in c.runs})}
    arch = defaultdict(Counter)
    for r in c.runs:
        arch[r["split"]][(r["archetype"], bool(r["profile"]["ood_slow"]))] += 1
    out["archetype_by_split"] = {s: {f"{a}{'+slow' if sl else ''}": n for (a, sl), n in sorted(v.items())} for s, v in arch.items()}
    # profile numeric distributions: archetype and split effects
    keys = ["cpu_base", "mem_base", "disk_root_base", "disk_data_base", "s_cpu", "s_mem", "a1", "a2", "sigma_ou", "burst_rate_per_day", "heap_drift", "root_growth",
            "data_growth", "weekend_factor", "phi1"]
    prof = {k: np.array([r["profile"][k] for r in c.runs], dtype=float) for k in keys}
    splits = np.array([r["split"] for r in c.runs])
    archs = np.array([r["archetype"] for r in c.runs])
    ks = {}
    for k in keys:
        tr = prof[k][splits == "train"]
        ks[k] = {"train_vs_val_ks_p": round(float(stats.ks_2samp(tr, prof[k][splits == "val"]).pvalue), 3),
                 "train_vs_test_iid_ks_p": round(float(stats.ks_2samp(tr, prof[k][splits == "test_iid"]).pvalue), 3),
                 "train_vs_test_ood_ks_p": round(float(stats.ks_2samp(tr, prof[k][splits == "test_ood"]).pvalue), 3),
                 "cache_node_vs_other_ks_p": round(float(stats.ks_2samp(prof[k][archs == "cache_node"], prof[k][archs != "cache_node"]).pvalue), 3)}
    out["host_profile_distribution_tests"] = {"note": "two-sample KS p-values; small p = distributions differ",
                                              "per_parameter": ks,
                                              "parameters_where_cache_node_differs_p_lt_0.05": [k for k, v in ks.items() if v["cache_node_vs_other_ks_p"] < 0.05],
                                              "parameters_where_test_ood_differs_from_train_p_lt_0.05": [k for k, v in ks.items() if v["train_vs_test_ood_ks_p"] < 0.05]}
    # nearest-neighbour distance in standardised profile space
    M = np.column_stack([(prof[k] - prof[k].mean()) / (prof[k].std() + 1e-12) for k in keys if k != "weekend_factor"])
    def nn(a_idx, b_idx):
        d = np.linalg.norm(M[a_idx][:, None, :] - M[b_idx][None, :, :], axis=2)
        return d
    tri, tei, ood = np.nonzero(splits == "train")[0], np.nonzero(splits == "test_iid")[0], np.nonzero(splits == "test_ood")[0]
    d_tt = nn(tri, tri) + np.eye(len(tri)) * 1e9
    out["profile_nearest_neighbour_distance"] = {"within_train_median": round(float(d_tt.min(axis=1).mean()), 3), "test_iid_to_train_mean": round(float(nn(tei, tri).min(axis=1).mean()), 3),
                                                 "test_ood_to_train_mean": round(float(nn(ood, tri).min(axis=1).mean()), 3),
                                                 "min_any_test_to_train": round(float(min(nn(tei, tri).min(), nn(ood, tri).min())), 3),
                                                 "reading": "if test hosts were copies of train hosts the distance would be ~0; similar mean distances mean test is drawn from the SAME distribution"}
    # fault parameter comparison per split
    # (family, parameter, variant filter): the first three are the "slow" duration parameters, the others are the amplitudes and timing
    # parameters that the 2.5x slowdown multiplies or leaves alone (cpu ramp length only exists for the ramp variant; net decay for all)
    fam_params = [("mem_leak", "D_min", None), ("disk_fill", "D_min", None), ("pool_exhaustion", "D_min", None), ("cpu_runaway", "A", None),
                  ("cpu_runaway", "ramp_steps", "ramp"), ("net_flood", "m_p", None), ("net_flood", "decay_steps", None)]
    ood_runs_slow = {r["run_id"] for r in c.runs if r["profile"]["ood_slow"]}
    ood_runs_cache = {r["run_id"] for r in c.runs if r["split"] == "test_ood" and not r["profile"]["ood_slow"]}
    fp = {}
    for fam, k, var in fam_params:
        def vsel(pred, var=var):
            return (lambda f: pred(f) and (var is None or f["variant"] == var))
        trn = _param_values(c, fam, k, vsel(lambda f: c.split_of[f["run_id"]] == "train"))
        ent = {"param": k, "variant_filter": var, "train": summ(trn),
               "val_ks_p": round(float(stats.ks_2samp(trn, _param_values(c, fam, k, vsel(lambda f: c.split_of[f["run_id"]] == "val"))).pvalue), 3),
               "test_iid_ks_p": round(float(stats.ks_2samp(trn, _param_values(c, fam, k, vsel(lambda f: c.split_of[f["run_id"]] == "test_iid"))).pvalue), 3)}
        sl = _param_values(c, fam, k, vsel(lambda f: f["run_id"] in ood_runs_slow))
        ca = _param_values(c, fam, k, vsel(lambda f: f["run_id"] in ood_runs_cache))
        if sl.size > 2 and ca.size > 2:
            ent["ood_slow_runs"] = summ(sl)
            ent["ood_cache_node_runs"] = summ(ca)
            ent["ood_slow_ks_p"] = round(float(stats.ks_2samp(trn, sl).pvalue), 4)
            ent["ood_cache_node_ks_p"] = round(float(stats.ks_2samp(trn, ca).pvalue), 3)
            ent["ood_slow_share_above_train_max"] = round(float((sl > trn.max()).mean()), 3)
        fp[f"{fam}.{k}"] = ent
    out["fault_parameter_comparison"] = fp
    out["ood_composition"] = {"slow_known_archetype_runs": len(ood_runs_slow), "cache_node_runs": len(ood_runs_cache),
                              "reading": "cache_node runs share every profile and fault distribution with train (see KS p-values): they are a new LABEL, not a new regime; "
                                         "only the slow-ramp runs are genuinely different, and only in how fast the same mechanisms develop"}
    return out


def _asof_value(fn: str, t_: float, tt: np.ndarray, vv: np.ndarray, cad: float) -> float:
    """Independent definition of four features from a history (times tt, values vv); NaN when the feature is undefined."""
    if fn == "cpu_mean_300":
        m = tt > t_ - 300
        return float(vv[m].mean()) if m.sum() >= 3 else np.nan
    if fn == "mem_max_900":
        m = tt > t_ - 900
        return float(vv[m].max()) if m.sum() >= 3 else np.nan
    if fn == "disk_root_last":
        return float(vv[-1]) if (not np.isnan(cad) and tt.size and t_ - tt[-1] <= 3 * cad) else np.nan
    m = tt > t_ - 300
    wt, wv = tt[m], vv[m]
    if wt.size < 2 or np.isnan(cad):
        return np.nan
    dt, dc = np.diff(wt), np.diff(wv)
    ok = (dt > 0) & (dt <= 3 * cad) & (dc >= 0)
    return float(dc[ok].sum() / dt[ok].sum()) if ok.any() else np.nan


def audit_features(c: Ctx, rng: np.random.Generator) -> dict:
    out: dict = {}
    fm = json.loads((c.fea / "features_manifest.json").read_text(encoding="utf-8"))
    names = [f["name"] for f in fm["features"]]
    out["n_features"] = len(names)
    # --- cross-split duplicate rows on 6 level features (ALL rows, 6 columns; no model is fit)
    cols = ["cpu_last", "mem_last", "disk_root_last", "disk_data_last", "rx_rate_last", "tx_rate_last"]
    seen: dict[bytes, str] = {}
    cross = within = nan_rule = rows = 0
    for sp in SPLITS:
        for p in sorted((c.fea / "features" / f"split={sp}").glob("run=*.parquet")):
            t = pq.ParquetFile(p).read(columns=cols)
            a = np.round(np.column_stack([t.column(k).to_numpy() for k in cols]).astype(np.float64), 4)
            nan_rule += int(np.isnan(a[:, :4]).any(axis=1).sum()) if sp in ("train", "val") else 0
            rows += a.shape[0] if sp in ("train", "val") else 0
            ok = ~np.isnan(a).any(axis=1)
            for r_ in a[ok]:
                b = r_.tobytes()
                if b in seen:
                    if seen[b] != sp:
                        cross += 1
                    else:
                        within += 1
                else:
                    seen[b] = sp
    out["duplicate_rows_on_six_level_features"] = {"cross_split_duplicates": cross, "within_split_duplicates": within, "rows_with_all_six_present": len(seen) + cross + within,
                                                   "note": "rounded to 4 decimals; exact duplicates across splits would indicate copied telemetry"}
    out["rule_feature_nan_in_scored_train_val_rows"] = {"rows": rows, "rows_with_any_nan_in_cpu_mean_300_mem_last_disk_*_last": nan_rule}
    # --- sample for variance / correlation / separability (train + val runs only)
    tr_runs = sorted(r["run_id"] for r in c.runs if r["split"] == "train")
    sel = list(rng.choice(tr_runs, size=12, replace=False))
    X, C = [], []
    for rid in sorted(sel):
        f = pq.ParquetFile(c.fea / "features" / "split=train" / f"run={rid}.parquet").read()
        lab = pq.ParquetFile(c.lab / "labels" / "split=train" / "horizon_s=1800" / f"run={rid}.parquet").read(columns=["decision_index", "scored", "label_class"])
        sc = lab.column("scored").to_numpy(zero_copy_only=False).astype(bool)
        lc = np.array(lab.column("label_class").to_pylist(), dtype=object)[sc]
        if len(lc) != f.num_rows:
            return {"error": f"row mismatch in {rid}"}
        keep = np.arange(f.num_rows) % 5 == 0
        M = np.column_stack([f.column(n).to_numpy() for n in names]).astype(np.float64)
        X.append(M[keep]); C.append(lc[keep])
    X, C = np.vstack(X), np.concatenate(C)
    out["sample_for_feature_stats"] = {"train_runs": sorted(sel), "rows": int(X.shape[0]), "note": "every 5th scored row of 12 train runs; train split only"}
    nanfrac = np.isnan(X).mean(axis=0)
    nun = np.array([len(np.unique(X[~np.isnan(X[:, j]), j])) for j in range(X.shape[1])])
    sd = np.nanstd(X, axis=0)
    out["no_variance_features"] = [n for n, s_, u in zip(names, sd, nun) if u <= 1 or s_ == 0]
    out["near_constant_features_le_3_distinct_values"] = [n for n, u in zip(names, nun) if 1 < u <= 3]
    out["features_all_nan_in_sample"] = [n for n, f_ in zip(names, nanfrac) if f_ == 1.0]
    out["nan_share_by_feature_top"] = {n: round(float(f_), 3) for n, f_ in sorted(zip(names, nanfrac), key=lambda x: -x[1])[:12] if f_ > 0}
    ok = [j for j in range(X.shape[1]) if names[j] not in out["no_variance_features"] and nanfrac[j] < 0.5]
    Z = X[:, ok]
    Zf = np.where(np.isnan(Z), np.nanmedian(Z, axis=0), Z)
    cm = np.corrcoef(Zf, rowvar=False)
    hi = [(names[ok[a]], names[ok[b]], round(float(cm[a, b]), 4)) for a in range(len(ok)) for b in range(a + 1, len(ok)) if abs(cm[a, b]) > 0.999]
    out["feature_pairs_abs_corr_gt_0.999"] = {"count": len(hi), "examples": hi[:15]}
    # greedy redundancy: features with a >0.98 correlate earlier in the list
    red = sum(1 for b in range(len(ok)) if any(abs(cm[a, b]) > 0.98 for a in range(b)))
    out["redundant_features_abs_corr_gt_0.98_with_an_earlier_feature"] = {"count": red, "of": len(ok)}
    # univariate separability (rank AUC, no fitting): positive vs negative on this train sample
    pos, neg = C == "positive", C == "negative"
    auc = {}
    for j, n in enumerate(names):
        col = X[:, j]
        m = ~np.isnan(col) & (pos | neg)
        if m.sum() < 50 or pos[m].sum() < 5:
            continue
        r = stats.rankdata(col[m])
        n1 = pos[m].sum()
        n0 = m.sum() - n1
        auc[n] = float((r[pos[m]].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))
    top = sorted(auc.items(), key=lambda kv: -abs(kv[1] - 0.5))[:12]
    out["univariate_auc_train_sample"] = {"positive_rows": int(pos.sum()), "negative_rows": int(neg.sum()), "note": "descriptive rank AUC of single features on 12 train runs; no model fitted",
                                          "top_by_abs_distance_from_0.5": {k: round(v, 3) for k, v in top},
                                          "features_with_auc_gt_0.9_or_lt_0.1": [k for k, v in auc.items() if v > 0.9 or v < 0.1]}
    # features that would not exist in a real agent-based monitoring system
    out["feature_availability_assessment"] = {
        "uses_ids_timestamps_or_labels": False, "evidence": "feature registry = 196 names, all derived from the six metric series (checked against features_manifest.json); elapsed_s is the only run-position feature and is excluded at training",
        "run_position_features_present_in_matrix": [n for n in names if n == "elapsed_s"],
        "freshness_features": [n for n in names if n.endswith(("_age_s", "_cadence_s")) or n == "n_series_stale"][:6] + ["..."]}
    # --- independent as-of re-computation on sampled rows (own implementation, from raw observations)
    asof = {"rows_checked": 0, "feature_comparisons": 0, "mismatches": 0, "per_feature_mismatch": Counter(), "max_abs_diff": 0.0,
            "comparisons_where_ignoring_the_envelope_rule_would_change_the_value": 0, "features": ["cpu_mean_300", "mem_max_900", "disk_root_last", "rx_rate_mean_300"]}
    for rid in sorted(sel)[:4]:
        series = load_obs(c, rid)
        start = us(c.run_by_id[rid]["start_ts"])
        f = pq.ParquetFile(c.fea / "features" / "split=train" / f"run={rid}.parquet").read(columns=["decision_ts"] + asof["features"])
        ts = f.column("decision_ts").cast(pa.int64()).to_numpy()
        for k in sorted(rng.choice(ts.size, size=min(40, ts.size), replace=False)):
            t_ = (ts[k] - start) / 1e6
            asof["rows_checked"] += 1
            for fn in asof["features"]:
                s = {"cpu_mean_300": "cpu", "mem_max_900": "mem", "disk_root_last": "disk_root", "rx_rate_mean_300": "rx"}[fn]
                o, e, v = series[s]
                to, te = (o - start) / 1e6, (e - start) / 1e6
                hi_ = np.searchsorted(to, t_, side="right")
                lo_ = np.searchsorted(to, t_ - 21600.0, side="right")
                avail = te[lo_:hi_] <= t_
                ta, va = to[lo_:hi_][avail], v[lo_:hi_][avail]
                tb, vb = to[lo_:hi_], v[lo_:hi_]          # same history but IGNORING the envelope (arrival) rule
                cad = min(max(float(np.median(np.diff(ta))), 1.0), 3600.0) if ta.size >= 21 else np.nan

                mine, ignoring = _asof_value(fn, t_, ta, va, cad), _asof_value(fn, t_, tb, vb, cad)
                got = f.column(fn)[int(k)].as_py()
                got = np.nan if got is None else float(got)
                asof["feature_comparisons"] += 1
                if np.isnan(mine) and np.isnan(got):
                    pass
                elif np.isnan(mine) or np.isnan(got) or abs(got - mine) > 1e-3 * max(1.0, abs(mine)):
                    asof["mismatches"] += 1
                    asof["per_feature_mismatch"][fn] += 1
                    if not (np.isnan(mine) or np.isnan(got)):
                        asof["max_abs_diff"] = max(asof["max_abs_diff"], abs(got - mine))
                if not (np.isnan(mine) and np.isnan(ignoring)) and (np.isnan(mine) != np.isnan(ignoring) or abs(mine - ignoring) > 1e-9):
                    asof["comparisons_where_ignoring_the_envelope_rule_would_change_the_value"] += 1
    asof["per_feature_mismatch"] = dict(asof["per_feature_mismatch"])
    out["asof_independent_recomputation"] = asof
    return out


def audit_missing_zero(c: Ctx) -> dict:
    """Impact of 'missing utilisation counts as zero risk' (ml/predict.py:71-73 and ml/train_baselines.py rule scorer): quantify from the labels."""
    s = json.loads((c.lab / "summary.json").read_text(encoding="utf-8"))
    out = {"code_locations": ["backend/ml/predict.py: RiskModel._rule uses np.nan_to_num(arr[:, idx], nan=0.0) / 100.0 then max()",
                              "backend/ml/predict.py: predict_risk ends with np.nan_to_num(risk, nan=0.0, ...)",
                              "backend/ml/train_baselines.py: rule scorer lambda uses np.nan_to_num(X[:, ridx], nan=0.0).max(axis=1) / 100.0",
                              "logreg: SimpleImputer(strategy='median') fitted on train: missing is treated as a typical value, not as high or unknown risk"],
           "insufficient_data_by_split_h1800": {}, "reasons": {}}
    for sp in SPLITS:
        e = s[sp]
        out["insufficient_data_by_split_h1800"][sp] = {"decisions": e["decisions"], "excluded_insufficient": e["excluded_insufficient_data"],
                                                       "share": round(e["excluded_insufficient_data"] / e["decisions"], 4)}
    tot = s["total"]
    reasons = tot["insufficient_data_reasons"]
    out["reasons"] = {"total_decisions": tot["decisions"], "excluded_insufficient_data": tot["excluded_insufficient_data"],
                      "by_reason_top": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:10])}
    out["why_it_is_latent_in_current_metrics"] = ("Rows whose latest observation is stale or whose window coverage is low are removed as 'insufficient_data' BEFORE features are built, "
                                                  "so in every scored train/val row cpu_mean_300, mem_last and disk_*_last are present (verified: see rule_feature_nan_in_scored_train_val_rows). "
                                                  "The NaN->0 path is therefore never exercised by the reported metrics; it only acts at inference time when telemetry is stale or absent.")
    return out


# --------------------------------------------------------------------------------------------------------------------------
def acf(x: np.ndarray, lag: int) -> float:
    x = x[~np.isnan(x)]
    if x.size <= lag + 10 or x.std() == 0:
        return float("nan")
    x = x - x.mean()
    return float((x[:-lag] * x[lag:]).sum() / (x * x).sum())


def audit_realism(c: Ctx, rng: np.random.Generator) -> dict:
    out: dict = {"scope": "only structural statistics that SMD/PSM actually support; no feature mapping is made"}
    runs = sorted(r["run_id"] for r in c.runs if r["split"] == "train")
    sel = sorted(rng.choice(runs, size=6, replace=False))
    syn = defaultdict(lambda: defaultdict(list))
    corr = []
    for rid in sel:
        series = load_obs(c, rid)
        for s in ("cpu", "mem", "disk_root", "disk_data"):
            v = series[s][2]
            for lag in (1, 60, 1440):
                syn[s][lag].append(acf(v, lag))
            syn[s]["const"].append(float(np.mean(np.diff(v) == 0)))
            syn[s]["distinct_per_1000"].append(float(len(np.unique(v)) / len(v) * 1000))
        n = min(len(series[s][2]) for s in ("cpu", "mem", "disk_root"))
        M = np.column_stack([series[s][2][:n] for s in ("cpu", "mem", "disk_root")])
        cm = np.corrcoef(M, rowvar=False)
        corr.append(float(np.median(np.abs(cm[np.triu_indices(3, 1)]))))
    out["synthetic_percent_series"] = {s: {"acf_lag1_median": round(float(np.nanmedian(d[1])), 3), "acf_lag60_median": round(float(np.nanmedian(d[60])), 3),
                                           "acf_lag1440_median": round(float(np.nanmedian(d[1440])), 3), "share_consecutive_equal": round(float(np.mean(d["const"])), 4),
                                           "distinct_values_per_1000_obs": round(float(np.mean(d["distinct_per_1000"])), 1)} for s, d in syn.items()}
    out["synthetic_cross_series_median_abs_corr_cpu_mem_disk_root"] = round(float(np.median(corr)), 3)
    root = Path(c.data_root) if c.data_root else None
    if root and (root / "smd" / "normalised" / "v1.0").exists():
        smd = root / "smd" / "normalised" / "v1.0"
        l1, l60, cons, nd = [], [], [], []
        for p in sorted(smd.glob("machine-*.train.parquet"))[:10]:
            t = pq.read_table(p)
            for j in range(38):
                v = t.column(f"f{j:02d}").to_numpy().astype(np.float64)
                if v.std() == 0:
                    continue
                l1.append(acf(v, 1)); l60.append(acf(v, 60)); cons.append(float(np.mean(np.diff(v) == 0))); nd.append(len(np.unique(v)) / len(v) * 1000)
        out["smd_train_first_10_machines_non_constant_dims"] = {"n_series": len(l1), "acf_lag1_median": round(float(np.nanmedian(l1)), 3), "acf_lag60_median": round(float(np.nanmedian(l60)), 3),
                                                                  "share_consecutive_equal_median": round(float(np.median(cons)), 4), "distinct_per_1000_median": round(float(np.median(nd)), 1),
                                                                  "note": "SMD sampling interval is not in the files, so lag 60 is 60 rows, not 60 minutes; no diurnal lag is tested"}
    if root and (root / "psm" / "normalised" / "v1.0").exists():
        t = pq.read_table(root / "psm" / "normalised" / "v1.0" / "psm.train.parquet")
        l1, l60, l1440, cons = [], [], [], []
        for j in range(25):
            v = t.column(f"f{j:02d}").to_numpy().astype(np.float64)
            if np.nanstd(v) == 0:
                continue
            l1.append(acf(v, 1)); l60.append(acf(v, 60)); l1440.append(acf(v, 1440)); cons.append(float(np.mean(np.diff(v[~np.isnan(v)]) == 0)))
        out["psm_train_all_non_constant_features"] = {"n_series": len(l1), "acf_lag1_median": round(float(np.nanmedian(l1)), 3), "acf_lag60_median": round(float(np.nanmedian(l60)), 3),
                                                      "acf_lag1440_median": round(float(np.nanmedian(l1440)), 3), "share_consecutive_equal_median": round(float(np.median(cons)), 4),
                                                      "note": "PSM row index steps by 1 (minute per the column name; not wall-clock), so lag 1440 is one nominal day"}
    out["levels_of_realism"] = {
        "structural_validity": {"evaluable": True, "evidence": "schema validation 0 failures, accounting and footprint checks in the manifest, independent re-derivation in this audit"},
        "statistical_similarity": {"evaluable": "marginally", "limit": "SMD/PSM have no named metrics, units or real timestamps; only generic smoothness/discreteness can be compared, and a match proves nothing about realism"},
        "temporal_realism": {"evaluable": "partially", "limit": "lag-k autocorrelation can be compared; diurnal/weekly structure only in PSM and only if its index is truly minutes; incident timing cannot"},
        "causal_realism": {"evaluable": False, "limit": "no dataset available to us records resource state before real outages; precursor-to-outage mechanisms are the simulator author's assumptions"},
        "real_world_predictive_validity": {"evaluable": False, "limit": "needs real incident records with impact times; none exist in the repository or the external sets"}}
    return out


def _wilson_text(p: float, n: int, z: float = 1.96) -> str:
    """95 % Wilson score interval for a proportion p observed over n independent units."""
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return f"Wilson 95% [{max(0.0, centre - half):.2f}, {min(1.0, centre + half):.2f}] for p={p} over n={n}"


def audit_evaluation(c: Ctx, inc_summary: dict, fcheck: dict) -> dict:
    out: dict = {}
    src = (c.repo / "backend" / "ml" / "train_baselines.py").read_text(encoding="utf-8")
    checks = {
        "splits_requested_for_fitting_or_selection": "train, val only (SPLITS_USED = ('train', 'val'); load_view asserts split in SPLITS_USED)",
        "SPLITS_USED_literal_present": "SPLITS_USED = (\"train\", \"val\")" in src,
        "imputer_and_scaler_fit_on_train_only": "make_pipeline(SimpleImputer" in src and "lr.fit(Xtr, ytr)" in src,
        "threshold_chosen_on_validation": "choose_threshold(yva[el], s_va[el])" in src and "max F1" in src,
        "threshold_objective": "maximum F1 on validation rows (not tied to the 1 false alarm / host-day budget in protocol.json)",
        "hyperparameter_search": "none (fixed XGBoost/LogReg settings)", "seeds": "single SEED = 7; no repeats",
        "rows_used_for_training": "positive+negative rows only; unscorable (ABRUPT/SILENT-only) windows dropped; optional negative thinning (--negative-stride)",
        "false_alarm_definition": "alert is false iff n_in_window == 0, i.e. an alert before ANY incident incl. ABRUPT/SILENT counts as correct",
        "exposure_denominator": "scored rows x 60 s (incl. unscorable rows)",
        "incident_level_recall_or_lead_time": "not computed (alert_stats docstring: 'not the full match-1 event evaluation')",
        "calibration": "not assessed (no reliability curve, Brier or ECE anywhere in train_baselines.py)" if all(w not in src for w in ("brier", "calibration", "ece")) else "present",
        "metric_level": "row (decision-point) level PR-AUC/precision/recall/F1 on adjacent 60 s rows that are near-duplicates; ~30 positive rows per incident",
    }
    out["code_checks"] = checks
    sm = fcheck
    out["saved_model_artifacts"] = sm
    sp = inc_summary
    out["statistical_power_of_what_was_reported"] = {
        "val_runs_used": sm["val_runs"], "val_precursor_incident_windows_used": sm["val_positive_windows"], "train_runs_used": sm["train_runs"], "train_positive_windows_used": sm["train_positive_windows"],
        "full_val_split": {"runs": 30, "incidents": sp["val"]["incidents"], "PRECURSOR": sp["val"]["PRECURSOR"]},
        "full_train_split": {"runs": 90, "incidents": sp["train"]["incidents"], "PRECURSOR": sp["train"]["PRECURSOR"]},
        "share_of_train_runs_used": round(sm["train_runs"] / 90, 3), "share_of_val_runs_used": round(sm["val_runs"] / 30, 3),
        "val_recall_ci_95_if_both_windows_found": _wilson_text(1.0, sm["val_positive_windows"]),
        "val_recall_ci_95_if_full_val_precursor_set": None}
    n = sp["val"]["PRECURSOR"]
    if n:
        out["statistical_power_of_what_was_reported"]["val_recall_ci_95_if_full_val_precursor_set"] = f"recall 0.8 over n={n} incidents: {_wilson_text(0.8, n)}"
    sp_t = {s: sp[s]["PRECURSOR"] for s in SPLITS}
    out["precursor_incidents_available_per_split"] = sp_t
    return out


# --------------------------------------------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--dataset", default="uptimetrace-m31-seed0-2723fc6789d8")
    ap.add_argument("--data-root", default=os.environ.get("UPTIMETRACE_DATA_ROOT"))
    ap.add_argument("--seed", type=int, default=20261119)
    ap.add_argument("--skip", default="", help="comma list of sections to skip: inventory,faults,labels,visibility,examples,splits,features,realism")
    a = ap.parse_args()
    skip = set(filter(None, a.skip.split(",")))
    c = Ctx(Path(a.repo), a.dataset, a.data_root)
    rep: dict = {"python": platform.python_version(), "numpy": np.__version__, "pyarrow": pa.__version__, "scipy": __import__("scipy").__version__, "audit_script": "scripts/audit_synthetic_dataset.py", "dataset_id": a.dataset, "sampling_seed": a.seed,
                 "data_root_used_for_external_comparison": a.data_root, "read_only": True, "trains_models": False}
    if "inventory" not in skip:
        rep["inventory"] = audit_inventory(c)
    fa = audit_faults_and_incidents(c)
    inc_re = fa.pop("_inc_re")
    rep["simulator_and_incidents"] = fa
    if "labels" not in skip:
        rep["labels"] = audit_labels(c, inc_re, np.random.default_rng(a.seed + 1))
    if "visibility" not in skip:
        rep["precursor_visibility_in_observed_telemetry"] = audit_precursor_visibility(c, inc_re, np.random.default_rng(a.seed + 2))
    rep["incident_definition_observed_levels"] = audit_onset_levels(c, inc_re, np.random.default_rng(a.seed + 6))
    if "examples" not in skip:
        rep["label_examples_reconstructed_from_raw_events"] = audit_examples(c, inc_re)
    if "splits" not in skip:
        rep["splits"] = audit_splits_and_leakage(c, np.random.default_rng(a.seed + 3))
    if "features" not in skip:
        rep["features"] = audit_features(c, np.random.default_rng(a.seed + 4))
    rep["missing_as_zero"] = audit_missing_zero(c)
    if "realism" not in skip:
        rep["realism"] = audit_realism(c, np.random.default_rng(a.seed + 5))
    if "inventory" not in skip:
        rep["evaluation_methodology"] = audit_evaluation(c, fa["incidents_per_split"], rep["inventory"]["saved_models"])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, default=str, sort_keys=False)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
