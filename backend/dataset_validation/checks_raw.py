"""Checks on the raw simulator output: run identity, fault and incident ground truth, and every observation file. Read-only."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import policy as P
from .reading import try_read
from .timeutil import epoch_seconds
from .report import Report


def _jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _ts(s: str | None) -> datetime | None:
    return None if s is None else datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_raw(raw: Path) -> dict:
    return {
        "runs": _jsonl(raw / "runs.jsonl"),
        "faults": _jsonl(raw / "faults.jsonl"),
        "incidents": _jsonl(raw / "incidents.jsonl"),
        "gaps": _jsonl(raw / "gaps.jsonl"),
        "protocol": json.loads((raw / "protocol.json").read_text(encoding="utf-8")),
        "manifest": json.loads((raw / "manifest.json").read_text(encoding="utf-8")),
    }


def check_runs(raw: Path, data: dict, r: Report) -> dict:
    runs = data["runs"]
    r.check("runs.count_and_identity")
    ids = [x["run_id"] for x in runs]
    if len(runs) != data["manifest"]["config"]["n_runs"]:
        r.add("hard", "raw", "runs.count", f"runs.jsonl has {len(runs)} records; the manifest declares {data['manifest']['config']['n_runs']}")
    dup = [k for k, v in Counter(ids).items() if v > 1]
    if dup:
        r.add("hard", "raw", "runs.duplicate_run_id", "duplicate run ids", len(dup), dup)
    dup_hosts = [k for k, v in Counter(x["host_id"] for x in runs).items() if v > 1]
    if dup_hosts:
        r.add("hard", "raw", "runs.duplicate_host_id", "two runs share a host id", len(dup_hosts), dup_hosts)
    mismatched = [x["run_id"] for x in runs if x["host_id"] != "sim-" + x["run_id"]]
    if mismatched:
        r.add("hard", "raw", "runs.host_identity", "host id is not the run's own simulator host", len(mismatched), mismatched)
    bad_dur = []
    for x in runs:
        dur = (_ts(x["end_ts"]) - _ts(x["start_ts"])).total_seconds()
        if abs(dur - P.RUN_LEN_S) > 1e-6:
            bad_dur.append(x["run_id"])
    if bad_dur:
        r.add("hard", "raw", "runs.duration", f"run length is not {P.RUN_LEN_S} s", len(bad_dur), bad_dur)
    bad_split = [x["run_id"] for x in runs if x["split"] not in P.SPLITS]
    if bad_split:
        r.add("hard", "raw", "runs.split_value", "unknown split value", len(bad_split), bad_split)
    counts = Counter(x["split"] for x in runs)
    expected = data["protocol"]["split_runs"]
    for s in P.SPLITS:
        if counts.get(s, 0) != expected[s]:
            r.add("hard", "raw", "runs.split_count", f"split {s} has {counts.get(s, 0)} runs; the protocol requires {expected[s]}")
    r.stats["runs"] = {"n": len(runs), "unique_run_ids": len(set(ids)), "unique_host_ids": len({x['host_id'] for x in runs}),
                       "split_counts": dict(counts), "archetypes": dict(Counter(x["archetype"] for x in runs))}
    return {x["run_id"]: x for x in runs}


def check_faults(data: dict, runs: dict, r: Report) -> None:
    r.check("faults.ground_truth")
    faults = data["faults"]
    fid = [f["fault_id"] for f in faults]
    dup = [k for k, v in Counter(fid).items() if v > 1]
    if dup:
        r.add("hard", "raw", "faults.duplicate_id", "duplicate fault ids", len(dup), dup)
    unknown = sorted({f["run_id"] for f in faults} - set(runs))
    if unknown:
        r.add("hard", "raw", "faults.unknown_run", "fault references a run that does not exist", len(unknown), unknown)
    bad_family = [f["fault_id"] for f in faults if f["family"] not in P.FAMILIES]
    if bad_family:
        r.add("hard", "raw", "faults.family", "unknown fault family", len(bad_family), bad_family)
    mismatch = [f["fault_id"] for f in faults if f["outcome"] != f["planned_outcome"]]
    if mismatch:
        r.add("hard", "raw", "faults.outcome_not_planned", "an injected fault did not produce its planned outcome", len(mismatch), mismatch)
    bad_outcome = [f["fault_id"] for f in faults if f["outcome"] not in ("incident", "twin")]
    if bad_outcome:
        r.add("hard", "raw", "faults.outcome_value", "outcome is neither incident nor twin", len(bad_outcome), bad_outcome)
    outside = []
    timing = []
    for f in faults:
        if f["impact_start"] is None:
            continue
        run = runs.get(f["run_id"])
        if run is None:
            continue
        s, e = _ts(f["impact_start"]), _ts(f["impact_end"])
        if not (_ts(run["start_ts"]) <= s and e <= _ts(run["end_ts"])):
            outside.append(f["fault_id"])
        if e < s:
            timing.append(f["fault_id"])
        if f["precursor_start"] is not None:
            lead = (s - _ts(f["precursor_start"])).total_seconds()
            if f["precursor_lead_s"] is None or abs(lead - f["precursor_lead_s"]) > 1 or lead < 0:
                timing.append(f["fault_id"])
    if outside:
        r.add("hard", "raw", "faults.outside_run", "fault impact falls outside its run", len(outside), outside)
    if timing:
        r.add("hard", "raw", "faults.timing", "impact end before start, or precursor lead inconsistent", len(timing), timing)
    r.stats["faults"] = {"n": len(faults), "by_outcome": dict(Counter(f["outcome"] for f in faults)),
                         "by_family": dict(Counter(f["family"] for f in faults))}


def check_incidents(data: dict, runs: dict, r: Report) -> dict:
    r.check("incidents.ground_truth")
    incidents = data["incidents"]
    faults = {f["fault_id"]: f for f in data["faults"]}
    iid = [i["incident_id"] for i in incidents]
    dup = [k for k, v in Counter(iid).items() if v > 1]
    if dup:
        r.add("hard", "raw", "incidents.duplicate_id", "duplicate incident ids", len(dup), dup)
    bad_group = [i["incident_id"] for i in incidents if i["group"] not in P.GROUPS]
    if bad_group:
        r.add("hard", "raw", "incidents.group", "unknown outcome group", len(bad_group), bad_group)
    silent_mismatch = [i["incident_id"] for i in incidents
                       if (i["group"] == "SILENT") != (i["family"] in P.SILENT_FAMILIES) or (i["group"] == "SILENT") != (i["silent_reason"] is not None)]
    if silent_mismatch:
        r.add("hard", "raw", "incidents.silent_rule", "SILENT must be exactly the silent_crash family with a silent reason", len(silent_mismatch), silent_mismatch)
    short = [i["incident_id"] for i in incidents if i["group"] == "PRECURSOR" and (i["precursor_lead_s"] or 0) < P.PRECURSOR_MIN_LEAD_S]
    if short:
        r.add("hard", "raw", "incidents.precursor_lead", "PRECURSOR lead is below the protocol minimum", len(short), short)
    abrupt_with_precursor = [i["incident_id"] for i in incidents if i["group"] == "ABRUPT" and i["precursor_lead_s"] is not None]
    if abrupt_with_precursor:
        r.add("warn", "raw", "incidents.abrupt_has_precursor_lead", "ABRUPT incidents carry a precursor lead value", len(abrupt_with_precursor), abrupt_with_precursor)
    link = []
    family = []
    onset = []
    for i in incidents:
        f = faults.get(i["primary_fault_id"])
        if f is None or f["member_relation"] != "primary" or f["run_id"] != i["run_id"]:
            link.append(i["incident_id"])
            continue
        if f["family"] != i["family"]:
            family.append(i["incident_id"])
        if f["impact_start"] is None or _ts(f["impact_start"]) != _ts(i["onset"]):
            onset.append(i["incident_id"])
    if link:
        r.add("hard", "raw", "incidents.primary_link", "incident's primary fault is missing, not primary, or in another run", len(link), link)
    if family:
        r.add("hard", "raw", "incidents.family_link", "incident family differs from its primary fault", len(family), family)
    if onset:
        r.add("hard", "raw", "incidents.onset_link", "incident onset is not its primary fault's impact start", len(onset), onset)
    bad_oracle = [i["incident_id"] for i in incidents if _ts(i["oracle_mask_end"]) < _ts(i["onset"])]
    if bad_oracle:
        r.add("hard", "raw", "incidents.oracle_mask", "oracle mask ends before the onset", len(bad_oracle), bad_oracle)
    per = Counter((runs[i["run_id"]]["split"], i["group"]) for i in incidents)
    stats = {"n": len(incidents), "by_group": dict(Counter(i["group"] for i in incidents)),
             "by_split_group": {f"{s}/{g}": per[(s, g)] for s in P.SPLITS for g in P.GROUPS},
             "by_split_family_group": {}}
    for i in incidents:
        k = f"{runs[i['run_id']]['split']}/{i['family']}/{i['group']}"
        stats["by_split_family_group"][k] = stats["by_split_family_group"].get(k, 0) + 1
    r.stats["incidents"] = stats
    return stats


def check_gaps(data: dict, runs: dict, r: Report) -> None:
    gaps = data["gaps"]
    unknown = sorted({g["run_id"] for g in gaps} - set(runs))
    if unknown:
        r.add("hard", "raw", "gaps.unknown_run", "gap references an unknown run", len(unknown), unknown)
    bad = [g["gap_id"] for g in gaps if _ts(g["end"]) <= _ts(g["start"])]
    if bad:
        r.add("hard", "raw", "gaps.window", "gap ends before it starts", len(bad), bad)
    r.stats["gaps"] = {"n": len(gaps), "by_type": dict(Counter(g["type"] for g in gaps))}


def check_observations(raw: Path, runs: dict, gaps: list, r: Report, deep: bool) -> None:
    """One observation file per run, identity and timing checks on every row, and cadence and gap explanation per run."""
    r.check("observations.files_and_identity")
    obs_dir = raw / "observations"
    files = {p.name for p in obs_dir.glob("run=*.parquet")}
    expected = {f"run={rid}.parquet" for rid in runs}
    missing = sorted(expected - files)
    extra = sorted(files - expected)
    if missing:
        r.add("hard", "raw", "observations.missing_file", "run has no observation file", len(missing), [m[4:-8] for m in missing])
    if extra:
        r.add("hard", "raw", "observations.extra_file", "observation file for an unknown run", len(extra), [e[4:-8] for e in extra])
    if not deep:
        return
    gaps_by_run: dict[str, list] = {}
    for g in gaps:
        gaps_by_run.setdefault(g["run_id"], []).append(g)
    per_run = {}
    cadence_bad, identity_bad, dup_bad, nulls_bad, future_bad, env_multi_bad, unknown_metric_bad, outside_bad = ([] for _ in range(8))
    unexplained_long, early_env = [], []
    total_rows = 0
    total_env = 0
    for rid in sorted(runs):
        run = runs[rid]
        path = obs_dir / f"run={rid}.parquet"
        if not path.exists():
            continue
        t = try_read(path, ["host_id", "metric", "labels", "value", "obs_ts", "env_ts", "envelope_id"], r, "observations", rid)
        if t is None:
            continue
        total_rows += len(t)
        if t.isna().any().any():
            nulls_bad.append(rid)
        if (t["host_id"] != run["host_id"]).any():
            identity_bad.append(rid)
        known = set(P.METRIC_NAMES)
        if (~t["metric"].isin(known)).any():
            unknown_metric_bad.append(rid)
        if t.duplicated(["metric", "labels", "obs_ts"]).any():
            dup_bad.append(rid)
        if (t["obs_ts"] > t["env_ts"]).any():
            future_bad.append(rid)
        n_env_ts = t.groupby("envelope_id")["env_ts"].nunique()
        if (n_env_ts > 1).any():
            env_multi_bad.append(rid)
        start, end = _ts(run["start_ts"]), _ts(run["end_ts"])
        if ((t["env_ts"] > end)).any():
            outside_bad.append(rid)
        n_early = int((t["env_ts"].drop_duplicates() < start).sum())
        if n_early:
            early_env.append((rid, n_early))
        env = t.loc[t["metric"] == "system.cpu.utilization", "env_ts"].drop_duplicates().sort_values()
        total_env += len(env)
        env_s = epoch_seconds(env)
        gaps = np.diff(env_s) if len(env_s) > 1 else np.array([])
        med = float(np.median(gaps)) if len(gaps) else float("nan")
        if not (abs(med - P.CADENCE_S) <= P.CADENCE_TOLERANCE_S):
            cadence_bad.append(rid)
        long_idx = np.where(gaps >= P.LONG_GAP_S)[0]
        windows = [(_ts(g["start"]), _ts(g["end"])) for g in gaps_by_run.get(rid, [])]
        for k in long_idx:
            a = env.iloc[k].to_pydatetime()
            b = env.iloc[k + 1].to_pydatetime()
            tol = P.GAP_EXPLAIN_TOLERANCE_S
            explained = any(ws - timedelta(seconds=tol) <= a and b <= we + timedelta(seconds=tol) for ws, we in windows)
            if not explained:
                unexplained_long.append(rid)
                break
        per_run[rid] = {"rows": len(t), "envelopes": int(len(env)), "median_interval_s": round(med, 3),
                        "max_interval_s": round(float(gaps.max()), 1) if len(gaps) else None, "long_gaps": int(len(long_idx))}
    for code, bad, msg in [
        ("observations.cadence", cadence_bad, f"median envelope interval is not {P.CADENCE_S} s ± {P.CADENCE_TOLERANCE_S} s"),
        ("observations.host_identity", identity_bad, "observation host_id differs from the run's host"),
        ("observations.unknown_metric", unknown_metric_bad, "observation uses a metric outside the expected series"),
        ("observations.nulls", nulls_bad, "observation file contains nulls"),
        ("observations.duplicate", dup_bad, "duplicate (metric, labels, observation time) rows"),
        ("observations.future_timestamp", future_bad, "an observation time is after its envelope time (future information)"),
        ("observations.envelope_timestamp", env_multi_bad, "one envelope id carries more than one envelope time"),
        ("observations.after_run_end", outside_bad, "an envelope is stamped after the run ended"),
    ]:
        if bad:
            r.add("hard", "raw", code, msg, len(bad), bad)
    if unexplained_long:
        r.add("warn", "raw", "observations.unexplained_gap", f"a telemetry gap of at least {P.LONG_GAP_S} s is not inside a recorded gap window",
              len(set(unexplained_long)), unexplained_long)
    if early_env:
        r.add("warn", "raw", "observations.before_run_start", "envelopes stamped just before the run start (first sample of the grid)",
              sum(n for _, n in early_env), [rid for rid, _ in early_env])
    r.stats["observations"] = {"files": len(files), "rows": total_rows, "envelopes": total_env,
                               "per_run_median_interval_s": {k: v["median_interval_s"] for k, v in per_run.items()},
                               "per_run": per_run}
