"""Independent audit: reads ONLY the written artifacts (no generator state) and re-checks the M3.1 invariants.

Run in a separate process:  python -m simulator_v2 audit <dataset_dir>
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import constants as C
from .footprints import CLASS_SIGNALS, MATRIX
from .protocol import protocol_hash
from .validate import Validator, envelopes_from_parquet

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
ONE_US = timedelta(microseconds=1)


def us(ts: str) -> int:
    return (datetime.fromisoformat(ts.replace("Z", "+00:00")) - _EPOCH) // ONE_US


def jl(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


class Report:
    def __init__(self):
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str = ""):
        self.rows.append((name, bool(ok), detail))

    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.rows)


def overlap(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def audit_dataset(root: Path, fraction: float = 0.05, seed: int = 12345, full_parquet: bool = True) -> Report:
    R = Report()
    man = json.load(open(root / "manifest.json", encoding="utf-8"))
    proto = json.load(open(root / "protocol.json", encoding="utf-8"))
    runs = jl(root / "runs.jsonl")
    faults = jl(root / "faults.jsonl")
    incidents = jl(root / "incidents.jsonl")
    gaps = jl(root / "gaps.jsonl")
    vgaps = jl(root / "view_gaps.jsonl")
    view = json.load(open(root / "view.json", encoding="utf-8"))
    cadence = view["cadence_s"]
    run_by_id = {r["run_id"]: r for r in runs}
    full = man["config"]["dataset_kind"] == "primary" and len(runs) == sum(C.SPLIT_RUNS.values())

    # AC-30 protocol hash / version / required fields
    R.add("AC-30 protocol_hash matches recomputation", protocol_hash(proto) == man["protocol_hash"])
    R.add("AC-30 protocol_version == 4.5", proto["protocol_version"] == "4.5" == man["protocol_version"])
    R.add("AC-30 frozen fields present",
          proto["sampling_origin"] == "run_start" and proto["buffered_membership"] == "true_sample_time"
          and proto["oracle_mask"]["end"] == "max(cooldown_end, max_member_conflict_end)"
          and proto["availability_bounds_s"]["buffered"] == {"lag_vs_obs_ts": 1805.0, "lag_vs_nominal": 1808.5})

    # AC-03 / validation block + Parquet recounts (all runs)
    v = man["validation"]
    R.add("AC-03 envelopes_checked == envelopes_total", v["envelopes_checked"] == v["envelopes_total"])
    R.add("AC-03 observations_checked == observations_total", v["observations_checked"] == v["observations_total"])
    R.add("AC-03 zero schema/semantic failures", v["schema_failures"] == 0 and v["semantic_failures"] == 0)
    R.add("AC-27 generator-state footprint checks (FP3/FP4/FP11 observed signals) found no violations",
          v.get("footprint_violations") == 0, str(v.get("footprint_violations")))
    pq_files = sorted((root / "observations").glob("run=*.parquet"))
    R.add("one Parquet file per run", len(pq_files) == len(runs), f"{len(pq_files)} files / {len(runs)} runs")
    total_rows, env_ids_all = 0, set()
    replay_ids = {i for g in vgaps for i in g["replay_envelope_ids"]}
    mono_bad = avail_bad = decreases_bad = columns_bad = 0
    max_norm_obs = max_norm_nom = max_buf_obs = max_buf_nom = 0.0
    obs_cols = ["host_id", "metric", "labels", "kind", "unit", "value", "obs_ts", "env_ts", "agent_id", "envelope_id"]
    reset_times: dict[str, list[int]] = defaultdict(list)
    for f in faults:
        if f["counter_reset_at"]:
            reset_times[f["run_id"]].append(us(f["counter_reset_at"]))
    for b in jl(root / "benign_episodes.jsonl"):
        if b["counter_reset_at"]:
            reset_times[b["run_id"]].append(us(b["counter_reset_at"]))
    ids_dup = 0
    n_resets_seen = n_resets_expected = 0
    for pf in pq_files:
        run_id = pf.stem.split("=")[1]
        r = run_by_id[run_id]
        t = pq.read_table(pf)
        if t.column_names != obs_cols:
            columns_bad += 1
        total_rows += t.num_rows
        eids = t.column("envelope_id").to_pylist()
        s = set(eids)
        ids_dup += len(s & env_ids_all)
        env_ids_all |= s
        start_us = us(r["start_ts"])
        skew_us = int(round(r["profile"]["skew_s"] * 1e6))
        obs = np.array(t.column("obs_ts").cast(pa.int64()).to_numpy(zero_copy_only=False), dtype=np.int64)
        env = np.array(t.column("env_ts").cast(pa.int64()).to_numpy(zero_copy_only=False), dtype=np.int64)
        metric = np.array(t.column("metric").to_pylist()); labels = np.array(t.column("labels").to_pylist())
        val = np.asarray(t.column("value").to_numpy(zero_copy_only=False), dtype=np.float64)
        is_replay = np.array([e in replay_ids for e in eids])
        series_key = np.char.add(metric, labels)
        for key in np.unique(series_key):
            m = series_key == key
            o = obs[m]
            order = np.argsort(o, kind="stable")
            if np.any(np.diff(o[order]) <= 0):
                mono_bad += 1
            if key.startswith("system.network"):
                vv = val[m][order]
                dec = int(np.sum(np.diff(vv) < 0))
                n_resets_seen += dec
        # nominal ticks recovered from observation timestamps: n_k = round((obs - skew - start) / cadence) * cadence
        n_k = np.rint(((obs - skew_us - start_us) / 1e6) / cadence) * cadence
        lag_obs = (env - obs) / 1e6
        lag_nom = (env - start_us - n_k * 1e6) / 1e6
        if np.any(env < obs - 5_000_000):
            avail_bad += 1
        nm = ~is_replay
        if nm.any():
            max_norm_obs = max(max_norm_obs, float(lag_obs[nm].max())); max_norm_nom = max(max_norm_nom, float(lag_nom[nm].max()))
        if is_replay.any():
            max_buf_obs = max(max_buf_obs, float(lag_obs[is_replay].max())); max_buf_nom = max(max_buf_nom, float(lag_nom[is_replay].max()))
        # jitter/skew within bounds: |obs - skew - start - n_k| <= 2 s
        dev = np.abs((obs - skew_us - start_us) / 1e6 - n_k)
        if dev.max() > C.J_MAX + 1e-6:
            avail_bad += 1
    R.add("AC-03 Parquet rows == observations_total", total_rows == v["observations_total"], f"{total_rows} vs {v['observations_total']}")
    R.add("AC-03 distinct envelope ids == envelopes_total and none shared across runs",
          len(env_ids_all) == v["envelopes_total"] and ids_dup == 0, f"{len(env_ids_all)} vs {v['envelopes_total']}")
    R.add("Parquet column set has no truth columns", columns_bad == 0)
    R.add("AC-14 observation timestamps strictly increasing per series", mono_bad == 0, f"{mono_bad} bad series")
    R.add("AC-14 jitter within +-2 s and obs <= env + 5 s", avail_bad == 0, f"{avail_bad} runs")
    R.add("AC-14 normal lag_vs_obs_ts <= 0.4 s", max_norm_obs <= 0.4 + 1e-6, f"max {max_norm_obs:.6f}")
    R.add("AC-14 normal lag_vs_nominal <= 3.9 s", max_norm_nom <= 3.9 + 1e-6, f"max {max_norm_nom:.6f}")
    R.add("AC-14 buffered lag_vs_obs_ts <= 1805 s", max_buf_obs <= 1805 + 1e-6, f"max {max_buf_obs:.3f}")
    R.add("AC-14 buffered lag_vs_nominal <= 1808.5 s (generated data <= 1806.5 s)", max_buf_nom <= 1806.5 + 1e-6 if cadence >= 30 else True,
          f"max {max_buf_nom:.3f}")
    R.add("AC-14 manifest bounds are the derived values",
          man["availability"]["normal"]["lag_vs_nominal_bound_s"] == 3.9
          and man["availability"]["buffered"]["lag_vs_obs_ts_bound_s"] == 1805
          and man["availability"]["buffered"]["lag_vs_nominal_bound_s"] == 1808.5
          and man["availability"]["buffered"]["c_max"] == 1)

    # AC-16 accounting identities I1-I11 per split and total (from the manifest), plus independent recounts
    ok_id, detail = True, []
    for scope, a in man["accounting"].items():
        t_, p_, e_, o_ = a["ticks"], a["partitions"], a["envelopes"], a["observations"]
        lost = t_["ticks_lost"]
        checks = {
            "I1": t_["ticks_nominal"] == t_["ticks_produced"] + lost["agent_down"] + lost["planned_reboot_gap"] + lost["panic_gap"],
            "I2": t_["ticks_produced"] == t_["ticks_normal"] + t_["ticks_buffered"],
            "I3": t_["ticks_normal"] == t_["ticks_normal_emitting"] + t_["ticks_normal_all_omitted"],
            "I4": t_["ticks_buffered"] == t_["ticks_buffered_emitting"] + t_["ticks_buffered_all_omitted"],
            "I5": e_["envelopes_normal"] == t_["ticks_normal_emitting"] == t_["ticks_normal"] - t_["ticks_normal_all_omitted"],
            "I6": p_["partitions_total"] == p_["partitions_with_replay"] + p_["partitions_without_replay"]
                  and p_["partitions_with_replay"] <= e_["envelopes_replay"],
            "I7": e_["envelopes_total"] == e_["envelopes_normal"] + e_["envelopes_replay"],
            "I8": o_["observations_normal"] == 6 * t_["ticks_normal"] - o_["observations_omitted_normal"]
                  and o_["observations_buffered"] == 6 * t_["ticks_buffered"] - o_["observations_omitted_buffered"],
            "I9": o_["observations_total"] == o_["observations_normal"] + o_["observations_buffered"]
                  == 6 * t_["ticks_produced"] - o_["observations_omitted"],
        }
        for k, ok in checks.items():
            if not ok:
                ok_id = False; detail.append(f"{scope}:{k}")
    R.add("AC-16 identities I1-I9 (manifest, every split + total)", ok_id, ",".join(detail))
    tot = man["accounting"]["total"]
    R.add("AC-16 I10/I11 recounts from artifacts",
          total_rows == tot["observations"]["observations_total"] and len(env_ids_all) == tot["envelopes"]["envelopes_total"]
          and len(replay_ids) == tot["envelopes"]["envelopes_replay"]
          and tot["envelopes"]["envelopes_normal"] == tot["envelopes"]["envelopes_total"] - len(replay_ids)
          and sum(1 for g in gaps if g["type"] == "net_partition_buffered") == tot["partitions"]["partitions_total"])
    R.add("AC-16 per-split values sum to totals",
          all(sum(man["accounting"][s]["envelopes"]["envelopes_total"] for s in C.SPLITS) == tot["envelopes"]["envelopes_total"]
              for _ in [0]) and sum(man["accounting"][s]["observations"]["observations_total"] for s in C.SPLITS) == tot["observations"]["observations_total"])
    nominal_ticks = tot["ticks"]["ticks_nominal"]
    ratio_env = tot["envelopes"]["envelopes_total"] / nominal_ticks
    ratio_obs = tot["observations"]["observations_total"] / (6 * nominal_ticks)
    if full:         # the band is a design sanity band for the primary dataset (dev fixtures add forced extra dropouts)
        R.add("AC-16 sanity band [0.990, 0.998]", 0.990 <= ratio_env <= 0.998 and 0.990 <= ratio_obs <= 0.998,
              f"env {ratio_env:.4f} obs {ratio_obs:.4f}")
    else:
        R.add("AC-16 ratios reported (sanity band applies to the full primary dataset only)", True,
              f"env {ratio_env:.4f} obs {ratio_obs:.4f}")

    # AC-05 quotas, relapses
    prim = Counter((run_by_id[i["run_id"]]["split"], i["family"]) for i in incidents)
    q_ok = all(prim[(s, f)] == C.QUOTAS[f][s] for f in C.FAMILIES for s in C.SPLITS)
    if full:
        R.add("AC-05 merged-incident counts per (split, family) == frozen quotas", q_ok)
    rel = Counter(run_by_id[f["run_id"]]["split"] for f in faults if f["member_relation"] == "relapse")
    if full:
        R.add("AC-05 relapses 65/19/29/16", dict(rel) == C.SECONDARIES, str(dict(rel)))
        R.add("run counts per split", Counter(r["split"] for r in runs) == Counter(C.SPLIT_RUNS))

    # AC-06/07/09/10/27/28/32 from sidecars
    by_run_f: dict[str, list] = defaultdict(list)
    for f in faults:
        by_run_f[f["run_id"]].append(f)
    by_run_i: dict[str, list] = defaultdict(list)
    for i in incidents:
        by_run_i[i["run_id"]].append(i)
    spacing_bad = warm_bad = maskend_bad = conc_bad = comp_bad = outcome_bad = att_bad = twin_bad = merge_bad = mask_def_bad = 0
    members_bad = 0
    for rid, fs in by_run_f.items():
        r = run_by_id[rid]
        st, en = us(r["start_ts"]), us(r["end_ts"])
        inc_f = [f for f in fs if f["member_relation"]]
        for f in fs:
            if us(f["start"]) < st + 7200 * 10 ** 6:
                warm_bad += 1
            if f["outcome"] != f["planned_outcome"]:
                outcome_bad += 1
            if f["rejection_attempts"] > C.MAX_ATTEMPTS:
                att_bad += 1
        iv = [(us(f["conflict_start"]), us(f["conflict_end"]), f) for f in fs]
        for a in range(len(iv)):
            for b in range(a + 1, len(iv)):
                if overlap(iv[a][0], iv[a][1], iv[b][0], iv[b][1]):
                    ca, cb = iv[a][2]["footprint_class"], iv[b][2]["footprint_class"]
                    if not MATRIX[ca][cb]:
                        comp_bad += 1
                    if (iv[a][2]["member_relation"] and iv[b][2]["member_relation"]):
                        comp_bad += 1                       # no two incident faults overlap (FP9)
                    sa, sb = CLASS_SIGNALS[ca], CLASS_SIGNALS[cb]
                    if sa & sb:
                        comp_bad += 1                       # FP4 on declared footprints
        pts = sorted({x for a, b, _ in iv for x in (a, b)})
        for x in pts:
            if sum(1 for a, b, _ in iv if a <= x < b) > C.CONCURRENCY_LIMIT:
                conc_bad += 1
        for f in fs:
            if set(f["conflict_footprint"]) != set(CLASS_SIGNALS[f["footprint_class"]]):
                comp_bad += 1
        # twins >= incidents per (family) among the 5 precursor families
        prim_fam = Counter(i["family"] for i in by_run_i.get(rid, []))
        tw_fam = Counter(f["family"] for f in fs if f["member_relation"] is None)
        for fam in C.PRECURSOR_FAMILIES:
            if tw_fam[fam] < prim_fam[fam]:
                twin_bad += 1
        incs = sorted(by_run_i.get(rid, []), key=lambda i: i["onset"])
        for a, b in zip(incs, incs[1:]):
            if us(b["onset"]) - us(a["recovery"]) < 930 * 10 ** 6:
                spacing_bad += 1
            if us(b["onset"]) < us(a["oracle_mask_end"]):
                maskend_bad += 1
        for i in incs:
            conf = max(us(m["conflict_end"]) for m in i["members"])
            expect = max(us(i["cooldown_end"]), conf)
            if us(i["oracle_mask_end"]) != expect:
                mask_def_bad += 1
            if us(i["oracle_mask_end"]) > en:
                maskend_bad += 1
            if us(i["cooldown_end"]) != us(i["recovery"]) + 900 * 10 ** 6:
                mask_def_bad += 1
            if i["oracle_mask_end_source"] != ("cooldown" if us(i["cooldown_end"]) >= conf else "return_ramp"):
                mask_def_bad += 1
            ms = i["members"]
            if min(us(m["impact_start"]) for m in ms) != us(i["onset"]) or ms[0]["member_relation"] != "primary" \
                    or any(m["member_relation"] not in ("primary", "relapse") for m in ms):
                members_bad += 1
    R.add("AC-07 onset_{k+1} - recovery_k >= 930 s", spacing_bad == 0, str(spacing_bad))
    R.add("AC-07 masks of consecutive incidents disjoint; AC-09 oracle_mask_end <= run_end", maskend_bad == 0, str(maskend_bad))
    R.add("AC-07 primary is earliest member; relations in {primary, relapse}", members_bad == 0, str(members_bad))
    R.add("AC-06 incident record: cooldown = recovery + 900 s; oracle_mask_end = max(cooldown_end, member conflict_end); source",
          mask_def_bad == 0, str(mask_def_bad))
    R.add("AC-09 starts >= run_start + 7200 s", warm_bad == 0, str(warm_bad))
    R.add("AC-09 concurrency <= 2", conc_bad == 0, str(conc_bad))
    R.add("AC-09 twins >= incidents per (run, precursor family)", twin_bad == 0, str(twin_bad))
    R.add("AC-10 simulated outcome == planned outcome; attempts <= 1000", outcome_bad == 0 and att_bad == 0,
          f"{outcome_bad}/{att_bad}")
    R.add("AC-27/28 footprints == table, compatible overlaps only, no incident overlaps (FP1/FP4/FP9)", comp_bad == 0, str(comp_bad))
    # gaps disjointness (AC-09) and AC-32 reset agreement
    by_run_g: dict[str, list] = defaultdict(list)
    for g in gaps:
        by_run_g[g["run_id"]].append(g)
    gap_bad = 0
    for rid, gs in by_run_g.items():
        iv = sorted((us(g["start"]), us(g["end"])) for g in gs if g["type"] != "x")
        for (a0, a1), (b0, b1) in zip(iv, iv[1:]):
            if a1 > b0:
                gap_bad += 1
    R.add("AC-09 gap / dropout intervals mutually disjoint", gap_bad == 0, str(gap_bad))
    reset_src_bad = 0
    for f in faults:
        if f["counter_reset_at"] is not None:
            if f["footprint_class"] != "F10" or f["counter_reset_series"] != ["rx", "tx"]:
                reset_src_bad += 1
    for f in faults:
        is_panic = f["variant"] in ("oom_panic", "kernel_panic")
        if is_panic != (f["counter_reset_at"] is not None):
            reset_src_bad += 1
    for b in jl(root / "benign_episodes.jsonl"):
        if b["counter_reset_at"] is not None and (b["type"] != "planned_reboot" or b["counter_reset_series"] != ["rx", "tx"]):
            reset_src_bad += 1
    R.add("AC-32 resets only for oom-panic / kernel-panic / planned_reboot, rx+tx only, F10 sources", reset_src_bad == 0, str(reset_src_bad))
    n_resets_expected = sum(len(v) for v in reset_times.values())
    R.add("AC-32 emitted counter decreases == 2 x logged resets (rx and tx, nowhere else)", n_resets_seen == 2 * n_resets_expected,
          f"decreases {n_resets_seen}, logged resets {n_resets_expected}")

    # AC-04 independent audit: 5% seeded sample re-validated with a freshly loaded validator from Parquet only
    rnd = random.Random(seed)
    sample = rnd.sample(pq_files, max(1, int(round(len(pq_files) * fraction))))
    hostnames = {r["host_id"]: r["hostname"] for r in runs}
    services = {r["host_id"]: r["service"] for r in runs}
    val = Validator()
    for pf in sample:
        for env in envelopes_from_parquet(pq.read_table(pf), hostnames, services):
            val.check(env)
    R.add("AC-04 independent revalidation of seeded sample", val.schema_failures == 0 and val.semantic_failures == 0
          and val.envelopes_checked > 0, f"{len(sample)} runs, {val.envelopes_checked} envelopes, {val.observations_checked} observations")
    return R


def run_audit(dataset: str, fraction: float, seed: int) -> int:
    rep = audit_dataset(Path(dataset), fraction, seed)
    w = max(len(n) for n, _, _ in rep.rows)
    for n, ok, d in rep.rows:
        print(f"{'PASS' if ok else 'FAIL'}  {n.ljust(w)}  {d}")
    print("AUDIT", "PASSED" if rep.passed() else "FAILED")
    return 0 if rep.passed() else 1


if __name__ == "__main__":
    sys.exit(run_audit(sys.argv[1], 0.05, 12345))
