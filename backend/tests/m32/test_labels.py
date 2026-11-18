"""M3.2 focused tests: window boundaries, incident phases, merged incidents, gaps/buffering, horizons, split isolation, determinism."""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from ml import availability as av
from ml import build_labels as bl
from ml import labels as lb

S = lb.ONE_US


def arr(xs):
    """Seconds -> int64 microseconds (numpy's default int is 32-bit on Windows and would overflow)."""
    return np.array(xs, dtype=np.int64) * S
ML = Path(bl.__file__).resolve().parent
DATASETS = Path(__file__).resolve().parents[3] / "artifacts" / "datasets"


def inc(onset, recovery, cooldown_extra=900, mask_extra=0, group="PRECURSOR", iid="i1"):
    """Seconds -> IncidentView (microseconds). oracle_mask_end = max(cooldown_end, ...) is given via mask_extra beyond cooldown."""
    cd = recovery + cooldown_extra
    return lb.IncidentView(iid, onset * S, recovery * S, cd * S, (cd + mask_extra) * S, group)


# ------------------------------------------------------------------------------------------------ window boundaries
def test_prediction_window_is_open_at_t_and_closed_at_t_plus_h():
    H = 1800
    v = [inc(10_000, 10_300)]
    t = arr([10_000 - H - 1, 10_000 - H, 10_000 - H + 1, 9_999, 10_000])          # onset at t+H+1 / t+H / t+H-1 / t+1 / t
    n, anyp, nxt = lb.window_labels(t, v, H)
    assert list(n) == [0, 1, 1, 1, 0]                       # onset at exactly t+H is inside; onset at exactly t is not
    assert list(anyp) == [False, True, True, True, False]
    assert nxt[1] == H and nxt[3] == 1 and np.isnan(nxt[4])


def test_all_three_horizons_nest_and_share_exclusions():
    v = [inc(100_000, 100_400)]
    t = (100_000 - np.array([100, 899, 900, 901, 1799, 1800, 1801, 3599, 3600, 3601], dtype=np.int64)) * S
    got = {H: lb.window_labels(t, v, H)[0] > 0 for H in (900, 1800, 3600)}
    assert list(got[900]) == [1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    assert list(got[1800]) == [1, 1, 1, 1, 1, 1, 0, 0, 0, 0]
    assert list(got[3600]) == [1, 1, 1, 1, 1, 1, 1, 1, 1, 0]
    assert np.all(got[900] <= got[1800]) and np.all(got[1800] <= got[3600])      # larger horizon never loses a positive


# ------------------------------------------------------------------------------------------------ onset / recovery / cooldown / ramp
def test_active_cooldown_and_return_ramp_phases_have_right_open_boundaries():
    v = [inc(10_000, 10_600, mask_extra=300)]               # cooldown ends 11_500, return ramp until 11_800
    pts = [9_999, 10_000, 10_599, 10_600, 11_499, 11_500, 11_799, 11_800, 20_000]
    phase, owner = lb.active_phase(arr(pts), v)
    assert list(phase) == [0, lb.ACTIVE, lb.ACTIVE, lb.COOLDOWN, lb.COOLDOWN, lb.RETURN_RAMP, lb.RETURN_RAMP, 0, 0]
    assert list(owner >= 0) == [False, True, True, True, True, True, True, False, False]


def test_mask_without_return_ramp_ends_with_the_900s_cooldown():
    v = [inc(10_000, 10_600)]
    phase, _ = lb.active_phase(arr([11_499, 11_500]), v)
    assert list(phase) == [lb.COOLDOWN, 0]


def test_multiple_incidents_masks_union_counts_and_group_priority():
    v = [inc(10_000, 10_300, group="ABRUPT", iid="a"), inc(20_000, 20_300, group="PRECURSOR", iid="b"), inc(30_000, 30_300, group="SILENT", iid="c")]
    t = arr([9_000, 19_500, 29_500, 12_000, 25_000])
    n, anyp, _ = lb.window_labels(t, v, 1800)
    assert list(n) == [1, 1, 1, 0, 0]            # t=25_000: the next onset (30_000) is beyond t + 1800
    assert list(lb.label_class(n, anyp)) == ["unscorable", "positive", "unscorable", "negative", "negative"]
    t2 = arr([9_500])                                # window (9500, 11300] holds only the ABRUPT onset; 20000 is out
    assert lb.label_class(*lb.window_labels(t2, v, 1800)[:2])[0] == "unscorable"
    t3 = arr([19_000])                               # window holds the PRECURSOR onset only
    assert lb.label_class(*lb.window_labels(t3, v, 1800)[:2])[0] == "positive"
    ph, own = lb.active_phase(arr([10_100, 20_100, 30_100, 15_000]), v)
    assert list(own[:3]) == [0, 1, 2] and own[3] == -1
    # overlapping masks (never produced by the planner, but legal): the exclusion is the union
    ov = [inc(10_000, 10_100, mask_extra=5_000, iid="x"), inc(11_000, 11_100, iid="y")]
    ph2, own2 = lb.active_phase(arr([11_500, 14_000, 16_100]), ov)
    assert list(ph2 > 0) == [True, True, False]               # x's mask runs to 16_000 and covers y's onset: union
    assert list(own2[:2]) == [0, 0]                           # the incident with the latest mask end owns the exclusion


def test_a_merged_incident_has_exactly_one_onset_in_the_real_sidecar():
    ds = next(DATASETS.glob("uptimetrace-m31-seed0-*"), None)
    if ds is None:
        pytest.skip("seed-0 M3.1 artifacts not present")
    raw = [json.loads(l) for l in open(ds / "incidents.jsonl") if l.strip()]
    merged = next(r for r in raw if r["n_members"] > 1)
    views = lb.load_incident_views(ds, merged["run_id"])
    assert len([v for v in views if v.incident_id == merged["incident_id"]]) == 1
    assert len(views) == sum(1 for r in raw if r["run_id"] == merged["run_id"])
    v = next(x for x in views if x.incident_id == merged["incident_id"])
    assert v.onset == lb.to_us(merged["onset"]) and v.oracle_mask_end == lb.to_us(merged["oracle_mask_end"])
    t = np.array([v.onset - 1 * S, v.onset, merged["members"][1] and lb.to_us(merged["members"][1]["impact_start"])])
    n, _, _ = lb.window_labels(t, [v], 3600)
    assert list(n) == [1, 0, 0]                                # the relapse member does not create a second onset


# ------------------------------------------------------------------------------------------------ missing telemetry / gaps
def synth(obs_times, lag=1.0, drop=None, late=None):
    """Six identical series: observation k at obs_times[k], arriving after `lag` (late: dict obs_time -> arrival time)."""
    tau = np.array(sorted(obs_times), dtype=float)
    if drop is not None:
        tau = tau[~np.isin(tau, drop)]
    env = tau + lag
    if late:
        env = np.array([late.get(x, e) for x, e in zip(tau, env)])
    return {s: (tau, env) for s in av.SERIES}


def reasons(series, t):
    return av.decode(int(av.exclusion_masks(series, np.array([float(t)]))[0]))


def test_gap_example_from_the_spec_12_8_reproduces_exactly():
    obs = list(range(0, 7201, 60)) + list(range(7920, 12_001, 60))          # 11 ticks (7260..7860) missing
    ser = synth(obs)
    assert reasons(ser, 7205) == [] and reasons(ser, 7265) == [] and reasons(ser, 7325) == []     # staleness 5, 65, 125 <= 180
    r = reasons(ser, 7385)
    assert [x for x in r if x.startswith("freshness")] == [f"freshness:{s}" for s in av.SERIES]   # staleness 185 > 180: first exclusion
    r = reasons(ser, 7925)
    assert "coverage:cpu:300" in r and "coverage:cpu:900" in r and "coverage:cpu:3600" not in r
    assert "coverage:cpu:900" in reasons(ser, 8105) and "coverage:cpu:300" not in reasons(ser, 8105)
    assert "coverage:cpu:900" in reasons(ser, 8345)                                               # cov900 = 0.472 < 0.5
    assert reasons(ser, 8405) == []                                                               # first valid decision t0 + 485 s
    first_valid = next(t for t in range(7385, 9000, 60) if not reasons(ser, t + 0))
    assert first_valid == 8405


def test_warmup_needs_3600s_since_the_first_observation():
    ser = synth(list(range(0, 10_000, 60)))
    assert reasons(ser, 3599.0) == ["warmup"] and reasons(ser, 3600.0) == []


def test_isolated_omission_is_covered_but_a_long_series_gap_excludes():
    obs = list(range(0, 20_000, 60))
    one = synth(obs)
    one["cpu"] = (np.array([x for x in obs if x != 10_020], dtype=float) + 0.0, np.array([x for x in obs if x != 10_020], dtype=float) + 1.0)
    assert reasons(one, 10_085) == []                                                             # 120 s <= 3 c-hat: no effect
    big = [x for x in obs if not 10_000 <= x < 10_600]
    one["cpu"] = (np.array(big, dtype=float), np.array(big, dtype=float) + 1.0)
    r = reasons(one, 10_805)
    assert any(x.startswith("coverage:cpu") for x in r) and not any("mem" in x for x in r)       # only the failing series is named


def test_absent_series_is_excluded_by_name_at_every_decision():
    ser = synth(list(range(0, 10_000, 60)))
    ser["tx"] = (np.zeros(0), np.zeros(0))
    for t in (5000.0, 9000.0):
        r = reasons(ser, t)
        assert "absent:tx" in r and all("absent" not in x for x in r if "tx" not in x)


def test_buffered_observations_are_unavailable_until_the_replay_arrives():
    obs = list(range(0, 14_400, 60))
    a, b, replay = 8000, 9800, 9803.0
    late = {float(x): replay for x in obs if a <= x < b}
    ser = synth(obs, late=late)
    during = reasons(ser, 9000)                              # as-of view equals an agent_down of the same interval
    assert any(x.startswith("freshness:") for x in during)
    after = reasons(ser, 9810)                               # replay has arrived: the interval is backfilled
    assert after == []
    assert reasons(synth(obs, drop=[float(x) for x in obs if a <= x < b]), 9810) != []     # a true dropout is NOT recovered
    assert any(x.startswith("freshness:") for x in reasons(ser, 9000)), "earlier decisions are never revised"


# ------------------------------------------------------------------------------------------------ configuration (spec section 2)
@pytest.mark.parametrize("cadence,delta,offset,origin,ok", [
    (60, 60, 5, 0, True), (60, 60, 3.9, 0, False), (60, 60, 3.95, 0, True), (60, 60, 60, 0, False), (60, 90, 5, 0, False),
    (30, 60, 5, 0, True), (60, 60, 5, 120, False), (120, 60, 5, 0, False),
])
def test_decision_config_requirements(cadence, delta, offset, origin, ok):
    if ok:
        bl.check_decision_config(cadence, delta, offset, origin)
    else:
        with pytest.raises(bl.LabelConfigError):
            bl.check_decision_config(cadence, delta, offset, origin)


# ------------------------------------------------------------------------------------------------ independence
def test_ml_package_is_independent_of_the_api_the_simulator_and_llm_sdks():
    banned = {"app", "fastapi", "simulator_v2", "simulator", "anthropic", "openai", "langchain"}
    for f in ML.glob("*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else ([n.module] if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module else [])
            assert not ({m.split(".")[0] for m in mods} & banned), (f.name, mods)


def test_only_the_labels_module_reads_incidents_and_labels_never_read_values():
    for f in ML.glob("*.py"):
        if f.name not in ("labels.py", "build_labels.py"):
            assert "incidents.jsonl" not in f.read_text(encoding="utf-8"), f.name
    assert set(bl.OBS_COLUMNS) == {"metric", "labels", "obs_ts", "env_ts"}


# ------------------------------------------------------------------------------------------------ end to end on real M3.1 artifacts
RUNS = ("r0000", "r0095", "r0170")           # train, val, test_ood


@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    ds = next(DATASETS.glob("uptimetrace-m31-seed0-*"), None)
    if ds is None:
        pytest.skip("seed-0 M3.1 artifacts not present")
    src_files = [ds / n for n in ("manifest.json", "runs.jsonl", "incidents.jsonl", "faults.jsonl")] + \
                [ds / "observations" / f"run={r}.parquet" for r in RUNS]
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in src_files}
    a = bl.build_labels(ds, tmp_path_factory.mktemp("la"), workers=3, run_filter=lambda r: r["run_id"] in RUNS)
    b = bl.build_labels(ds, tmp_path_factory.mktemp("lb"), workers=1, run_filter=lambda r: r["run_id"] in RUNS)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in src_files}
    return {"ds": ds, "a": a, "b": b, "before": before, "after": after}


def test_labeling_is_deterministic_and_does_not_modify_the_m31_artifacts(e2e):
    ma = json.load(open(e2e["a"] / "labels_manifest.json"))["files_sha256"]
    mb = json.load(open(e2e["b"] / "labels_manifest.json"))["files_sha256"]
    assert ma == mb and len(ma) == 9                          # 3 runs x 3 horizons, identical bytes for 1 vs 3 workers
    assert e2e["before"] == e2e["after"]


def test_every_row_is_in_its_own_split_and_horizons_agree(e2e):
    runs = {json.loads(l)["run_id"]: json.loads(l) for l in open(e2e["ds"] / "runs.jsonl")}
    tbl = bl.load_labels(e2e["a"])
    for rid, split in zip(tbl.column("run_id").to_pylist()[::997], tbl.column("split").to_pylist()[::997]):
        assert runs[rid]["split"] == split
    assert {(r, s) for r, s in zip(tbl.column("run_id").to_pylist(), tbl.column("split").to_pylist())} == \
        {("r0000", "train"), ("r0095", "val"), ("r0170", "test_ood")}
    by_h = {}
    for H in (900, 1800, 3600):
        t = bl.load_labels(e2e["a"], splits=["val"], horizons=[H])
        by_h[H] = (bl.targets_numpy(t), t.column("exclusion").to_pylist())
    assert by_h[900][1] == by_h[1800][1] == by_h[3600][1]                     # exclusions do not depend on the horizon
    a, b, c = (by_h[H][0] for H in (900, 1800, 3600))
    assert np.all(a <= b) and np.all(b <= c) and (a == 1).sum() < (b == 1).sum() < (c == 1).sum()


def test_split_isolation_loader_never_opens_other_splits(e2e, tmp_path):
    poisoned = tmp_path / "poisoned"
    shutil.copytree(e2e["a"], poisoned)
    for sp in ("val", "test_ood"):
        for f in (poisoned / "labels" / f"split={sp}").rglob("*.parquet"):
            f.write_bytes(b"POISON: this split must not be read")
    train = bl.load_labels(poisoned, splits=["train"], horizons=[1800])
    clean = bl.load_labels(e2e["a"], splits=["train"], horizons=[1800])
    assert train.num_rows == clean.num_rows == 20160 and set(train.column("split").to_pylist()) == {"train"}
    with pytest.raises(Exception):
        bl.load_labels(poisoned, splits=["val"], horizons=[1800])


def test_labels_match_sidecar_ground_truth_on_a_real_run(e2e):
    ds = e2e["ds"]
    runs = {json.loads(l)["run_id"]: json.loads(l) for l in open(ds / "runs.jsonl")}
    incs = [json.loads(l) for l in open(ds / "incidents.jsonl") if json.loads(l)["run_id"] == "r0095"]
    start = lb.to_us(runs["r0095"]["start_ts"])
    t = bl.load_labels(e2e["a"], splits=["val"], horizons=[1800])
    ts = np.array(t.column("decision_ts").cast("int64").to_pylist())
    tgt = bl.targets_numpy(t)
    excl = np.array([e is not None for e in t.column("exclusion").to_pylist()])
    assert ts[0] - start == 5 * S and np.all(np.diff(ts) == 60 * S) and ts[-1] < lb.to_us(runs["r0095"]["end_ts"])
    for i in incs:                                            # independent re-derivation from the raw sidecar
        on, end = lb.to_us(i["onset"]), lb.to_us(i["oracle_mask_end"])
        inside = (ts >= on) & (ts < end)
        assert excl[inside].all() and (tgt[inside] == -1).all()
        win = (ts > on - 1800 * S - 1) & (ts <= on - 1) & ~excl      # t < onset <= t + H  <=>  onset - H <= t < onset
        win = (ts >= on - 1800 * S) & (ts < on) & ~excl
        assert (tgt[win] == 1).all()
    assert (tgt[~excl] >= 0).all() and (tgt[excl] == -1).all()
    positives = (tgt == 1)
    assert positives.sum() > 0 and not excl[positives].any()
