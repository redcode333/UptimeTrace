"""AC-17 (cadence fixture, section 18 checks 4/5/7), AC-03/AC-30 loader gate, AC-18 reporting, sidecar separation.

All of this runs on the real 6-run x 3-day development fixture built by the CLI (views 30/60/120 s).
"""
from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from simulator_v2 import audit
from simulator_v2.loader import DatasetRejected, verify_dataset

TRUTH = ["runs.jsonl", "faults.jsonl", "incidents.jsonl", "benign_episodes.jsonl", "gaps.jsonl"]
PKG = Path(__file__).resolve().parents[2] / "simulator_v2"
ONE = 10 ** 6


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def us(ts):
    return audit.us(ts)


def series_arrays(root, run_id):
    t = pq.read_table(root / "observations" / f"run={run_id}.parquet")
    metric, labels = np.array(t.column("metric").to_pylist()), np.array(t.column("labels").to_pylist())
    key = np.char.add(metric, labels)
    return {k: (np.array(t.column("obs_ts").cast(pa.int64()).to_pylist(), dtype=np.int64)[key == k],
                np.array(t.column("env_ts").cast(pa.int64()).to_pylist(), dtype=np.int64)[key == k],
                np.array(t.column("value").to_pylist(), dtype=np.float64)[key == k])
            for k in np.unique(key)}


# ------------------------------------------------------------------------------------------------ AC-17
def test_truth_files_are_byte_identical_across_cadence_views(dev_roots):
    for f in TRUTH:
        blobs = {c: (r / f).read_bytes() for c, r in dev_roots.items()}
        assert blobs[30] == blobs[60] == blobs[120], f
    assert len({(r / "protocol.json").read_bytes() for r in dev_roots.values()}) == 1
    views = {c: json.load(open(r / "view.json")) for c, r in dev_roots.items()}
    assert {c: v["cadence_s"] for c, v in views.items()} == {30: 30, 60: 60, 120: 120}
    assert all(v["origin_offset_s"] == 0 and v["dataset_kind"] == "dev" and v["jitter_on"] for v in views.values())
    # replay envelope ids are view-specific
    ids = {c: {i for g in jl(r / "view_gaps.jsonl") for i in g["replay_envelope_ids"]} for c, r in dev_roots.items()}
    assert ids[30].isdisjoint(ids[60]) and ids[60].isdisjoint(ids[120])


def test_dev_fixture_has_every_required_feature(dev_roots):
    root = dev_roots[60]
    faults, incs, ben, gaps = jl(root / "faults.jsonl"), jl(root / "incidents.jsonl"), jl(root / "benign_episodes.jsonl"), jl(root / "gaps.jsonl")
    assert {f["family"] for f in faults if f["outcome"] == "incident"} == {"mem_leak", "disk_fill", "cpu_runaway", "net_flood", "pool_exhaustion", "silent_crash"}
    assert {f["family"] for f in faults if f["member_relation"] is None} >= {"mem_leak", "disk_fill", "cpu_runaway", "net_flood", "pool_exhaustion"}
    assert any(i["n_members"] == 2 for i in incs)                                  # a merged pair (relapse)
    assert {"transient_blip", "planned_reboot"} <= {b["type"] for b in ben}
    assert any(g["type"] == "agent_down" and us(g["end"]) - us(g["start"]) >= 600 * ONE for g in gaps)
    assert any(g["type"] == "net_partition_buffered" for g in gaps)
    assert all(us(f["start"]) - us(jl(root / "runs.jsonl")[int(f["run_id"][1:])]["start_ts"]) >= 7200 * ONE for f in faults)


def test_check5_observation_timestamps_strictly_increase_per_series_in_every_view(dev_roots):
    for c, root in dev_roots.items():
        for r in jl(root / "runs.jsonl"):
            for k, (obs, env, val) in series_arrays(root, r["run_id"]).items():
                assert np.all(np.diff(np.sort(obs)) > 0), (c, r["run_id"], k)


def test_check7_resets_are_identical_across_views_and_show_at_the_first_tick_at_or_after_the_reset(dev_roots):
    expected = {}
    for c, root in dev_roots.items():
        runs = {r["run_id"]: r for r in jl(root / "runs.jsonl")}
        resets = {}
        for f in jl(root / "faults.jsonl") + jl(root / "benign_episodes.jsonl"):
            if f["counter_reset_at"]:
                resets.setdefault(f["run_id"], []).append(us(f["counter_reset_at"]))
        expected[c] = resets
        assert any(resets.values())
        for rid, rs in resets.items():
            start = us(runs[rid]["start_ts"]); skew = runs[rid]["profile"]["skew_s"]
            for s in ("system.network.rx_bytes", "system.network.tx_bytes"):
                key = s + '{"interface":"eth0"}'
                obs, env, val = series_arrays(root, rid)[key]
                order = np.argsort(obs); obs, val = obs[order], val[order]
                u = (obs - start) / 1e6 - skew                             # true sample time of each emitted observation
                dec = np.nonzero(np.diff(val) < 0)[0]
                assert len(dec) == len(rs), (c, rid, s)
                for r_us in sorted(rs):
                    R = (r_us - start) / 1e6
                    assert any(u[d] < R <= u[d + 1] + 1e-6 for d in dec), (c, rid, s, R)       # right-continuous: first tick with u >= R
    assert expected[30] == expected[60] == expected[120]


def coverage(taus, t, w, gmax):
    """Spec 12.4 on available observation times `taus` (sorted)."""
    taus = [x for x in taus if x <= t]
    cov = 0.0
    lo = t - w
    for a, b in zip(taus, taus[1:]):
        if b - a <= gmax:
            cov += max(0.0, min(b, t) - max(a, lo))
    if taus and t - taus[-1] <= gmax:
        cov += max(0.0, t - max(taus[-1], lo))
    return cov / w


def test_check4_gap_free_windows_hold_floor_or_ceil_of_w_over_c_observations(dev_roots):
    for c, root in dev_roots.items():
        run = jl(root / "runs.jsonl")[0]
        start = us(run["start_ts"]); skew = run["profile"]["skew_s"]
        gaps = [(us(g["start"]) - start, us(g["end"]) - start) for g in jl(root / "gaps.jsonl") if g["run_id"] == run["run_id"]]
        obs, env, val = series_arrays(root, run["run_id"])["system.cpu.utilization{}"]
        u = np.sort((obs - start) / 1e6 - skew)
        tested = 0
        for w in (300, 900, 3600):
            for t0 in range(8000, 200000, 7919):
                off = next(o for o in range(10, c - 9, 5) if 5 <= (o - w) % c <= c - 5)       # both window edges >= 5 s from any tick
                t = (t0 // c) * c + off                                # so +-2 s jitter cannot move a tick across an edge
                if any(a <= t + 1 and b >= t - w - 1 for a, b in [(x / 1e6, y / 1e6) for x, y in gaps]):
                    continue
                slots = [k * c for k in range(int((t - w) // c) - 1, int(t // c) + 2) if t - w < k * c <= t]
                if not (np.floor(w / c) <= len(slots) <= np.ceil(w / c)):
                    pytest.fail(f"slot count {len(slots)} outside floor/ceil of {w}/{c}")
                present = int(((u > t - w) & (u <= t)).sum())
                missing = len(slots) - present                         # slots lost to metric_omitted (p = 0.002)
                assert 0 <= missing <= 2 and present <= len(slots)
                tested += 1
        assert tested > 30


def test_check4_window_inside_a_long_dropout_has_zero_coverage_in_every_view(dev_roots):
    checked = 0
    for c, root in dev_roots.items():
        runs = {r["run_id"]: r for r in jl(root / "runs.jsonl")}
        for g in jl(root / "gaps.jsonl"):
            a, b = us(g["start"]) , us(g["end"])
            if g["type"] != "agent_down" or b - a <= 360 * ONE:
                continue
            r = runs[g["run_id"]]; start = us(r["start_ts"]); skew = r["profile"]["skew_s"]
            obs, env, val = series_arrays(dev_roots[c], g["run_id"])["system.cpu.utilization{}"]
            taus = sorted(((obs - start) / 1e6 - skew).tolist())
            t = (a - start) / 1e6 + 362.0                              # window (t-300, t] lies inside the dropout and t - last_obs > 360 s = max g_max
            assert t < (b - start) / 1e6
            assert coverage(taus, t, 300.0, 3 * c) == 0.0
            checked += 1
    assert checked >= 3


def test_check4_before_the_replay_arrives_a_buffered_partition_looks_like_an_agent_down(dev_roots):
    checked = 0
    for c, root in dev_roots.items():
        runs = {r["run_id"]: r for r in jl(root / "runs.jsonl")}
        vg = {g["gap_id"]: g for g in jl(root / "view_gaps.jsonl")}
        for g in jl(root / "gaps.jsonl"):
            if g["type"] != "net_partition_buffered":
                continue
            r = runs[g["run_id"]]; start = us(r["start_ts"]); skew = r["profile"]["skew_s"]
            a, b = us(g["start"]), us(g["end"])
            obs, env, val = series_arrays(root, g["run_id"])["system.cpu.utilization{}"]
            inside = (obs - skew * ONE >= a) & (obs - skew * ONE < b)
            if inside.any():
                assert env[inside].min() >= b + (skew + 0.5) * ONE - 2      # nothing from the partition is available before the replay
                checked += 1
            else:
                assert vg[g["gap_id"]]["chunks"] == 0
    assert checked >= 1


# ------------------------------------------------------------------------------------------------ audit + loader (AC-03, AC-30, AC-18)
@pytest.mark.parametrize("cadence", [30, 60, 120])
def test_independent_audit_passes_on_every_dev_view(dev_roots, cadence):
    rep = audit.audit_dataset(dev_roots[cadence], fraction=0.34, seed=7)
    failed = [(n, d) for n, ok, d in rep.rows if not ok]
    assert not failed, failed
    assert len(rep.rows) >= 30


def test_loader_accepts_the_fixture_and_refuses_tampered_copies(dev_roots, tmp_path):
    man = verify_dataset(dev_roots[60])
    assert man["validation"]["envelopes_checked"] == man["validation"]["envelopes_total"] > 0

    def tampered(name, mutate):
        dst = tmp_path / name
        shutil.copytree(dev_roots[60], dst)
        mutate(dst)
        with pytest.raises(DatasetRejected) as e:
            verify_dataset(dst)
        return str(e.value)

    def edit_manifest(fn):
        def go(d):
            m = json.load(open(d / "manifest.json")); fn(m); json.dump(m, open(d / "manifest.json", "w"))
        return go
    assert "envelopes_checked" in tampered("a", edit_manifest(lambda m: m["validation"].__setitem__("envelopes_checked", m["validation"]["envelopes_total"] - 1)))
    assert "observations_checked" in tampered("b", edit_manifest(lambda m: m["validation"].__setitem__("observations_checked", m["validation"]["observations_total"] + 1)))
    assert "failures" in tampered("c", edit_manifest(lambda m: m["validation"].__setitem__("schema_failures", 1)))
    assert "failures" in tampered("d", edit_manifest(lambda m: m["validation"].__setitem__("semantic_failures", 2)))

    def poke_protocol(d):
        p = json.load(open(d / "protocol.json")); p["oracle_mask"]["end"] = "cooldown_end"; json.dump(p, open(d / "protocol.json", "w"))
    assert "protocol_hash" in tampered("e", poke_protocol)
    assert "Parquet rows" in tampered("f", lambda d: next((d / "observations").glob("*.parquet")).unlink())

    def bad_sidecar(d):
        lines = (d / "faults.jsonl").read_text().splitlines(); r = json.loads(lines[0]); r["leaked_truth"] = 1
        lines[0] = json.dumps(r); (d / "faults.jsonl").write_text("\n".join(lines) + "\n")
    assert "gt-1.0" in tampered("g", bad_sidecar)


def test_manifest_reporting_blocks_ac18(dev_roots):
    m = json.load(open(dev_roots[60] / "manifest.json"))
    assert m["protocol_version"] == "4.5" and m["simulator_version"] == "2.0.0" and m["spec_version"] == "4.5.1"
    assert set(m["accounting"]) == {"total", "train", "val", "test_iid", "test_ood"}
    assert set(m["accounting"]["total"]) == {"ticks", "partitions", "envelopes", "observations"}
    assert m["availability"]["normal"]["lag_vs_nominal_bound_s"] == 3.9 and m["availability"]["buffered"]["lag_vs_nominal_bound_s"] == 1808.5
    assert m["availability"]["buffered"]["observed_max_lag_vs_nominal_s"] <= 1806.5      # observed maxima are kept separate from bounds
    assert m["validation"]["footprint_violations"] == 0 and m["code_hash"] and len(m["code_hash"]) == 64
    assert m["counts"]["incidents"] and all(k in m["counts"]["incidents"][0] for k in ("split", "family", "group_or_relation", "silent_reason_or_outcome", "n"))
    assert m["low_n"] and all(r["flag"] == "LOW-N" for r in m["low_n"])                 # tiny dev cells are all below 30
    res = m["resources"]
    for k in ("generation_wall_clock_s", "per_run_mean_s", "per_run_p95_s", "validation_envelopes_per_second_per_worker",
              "parquet_bytes_total", "peak_memory_per_worker_mb", "cpu_model", "cpu_count", "python", "numpy", "workers"):
        assert k in res
    assert m["rejection_statistics"]["max_attempts_any_fault"] <= 1000


# ------------------------------------------------------------------------------------------------ ground truth / telemetry separation
def test_parquet_has_exactly_the_observation_columns_and_no_truth(dev_roots):
    cols = ["host_id", "metric", "labels", "kind", "unit", "value", "obs_ts", "env_ts", "agent_id", "envelope_id"]
    forbidden = ("fault", "incident", "precursor", "group", "family", "footprint", "outcome", "label", "silent", "mask")
    for p in (dev_roots[60] / "observations").glob("*.parquet"):
        t = pq.read_table(p)
        assert t.column_names == cols
        assert not [c for c in t.column_names if c not in ("labels",) and any(f in c for f in forbidden)]
        assert set(t.column("labels").to_pylist()) <= {"{}", '{"mount":"/"}', '{"mount":"/data"}', '{"interface":"eth0"}'}


def test_telemetry_rendering_does_not_read_truth_records(truth_cache):
    from simulator_v2.run import render_view
    t = truth_cache(95)
    a = render_view(t, 60, True)
    saved, t.records = t.records, {"runs": {}, "faults": [], "incidents": [], "benign": [], "gaps": []}
    try:
        b = render_view(t, 60, True)
    finally:
        t.records = saved
    assert np.array_equal(a.columns["value"], b.columns["value"]) and a.columns["envelope_id"] == b.columns["envelope_id"]


def test_observation_modules_do_not_import_truth_modules():
    truth = {"precursor", "merge", "planner", "faults", "alloc", "run", "episodes"}
    for mod in ("ticks", "signals", "writer", "bounds", "validate"):
        tree = ast.parse((PKG / f"{mod}.py").read_text(encoding="utf-8"))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.level == 1:
                imported |= {a.name for a in n.names} | ({n.module} if n.module else set())
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[-1] for a in n.names}
        leaked = {i.split(".")[0] for i in imported} & truth
        # signals.py takes the plan as an argument (duck typed); it must not import the planner / precursor / merge code
        assert not leaked, (mod, leaked)
