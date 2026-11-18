"""One run end to end: profile -> baseline -> plan -> episodes -> latent (truth) and per-view rendering."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import constants as C
from .baseline import build_baseline, build_workload, draw_profile
from .episodes import place_episodes
from .footprints import footprint_signals
from .latent_util import ts_of
from .merge import merge_incidents
from .planner import overlaps, plan_run
from .precursor import derive_precursor
from .rng import RunStreams
from .signals import build_latent
from .ticks import build_run_output, omission_mask

DT = C.LATENT_DT
N = C.N_STEPS


@dataclass
class RunTruth:
    spec: object
    profile: object
    base: object
    plan: object
    episodes: dict
    latent: object
    omit: object
    streams: object
    records: dict = field(default_factory=dict)
    partition_gap_ids: list = field(default_factory=list)
    gaps_grid: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)


def prepare_run(master_seed: int, spec, force_one_each: bool = False) -> RunTruth:
    streams = RunStreams(master_seed, spec.run_index)
    profile = draw_profile(streams, spec.run_index, spec.split, spec.archetype, spec.ood_slow)
    lam, smooth = build_workload(profile, streams)
    base = build_baseline(profile, lam, smooth, streams)
    plan = plan_run(spec, profile, base, streams)
    episodes = place_episodes(profile, plan, streams, force_one_each)
    latent = build_latent(profile, base, plan, episodes, streams)
    omit = omission_mask(profile, streams)
    t = RunTruth(spec, profile, base, plan, episodes, latent, omit, streams)
    t.gaps_grid = {
        "agent_down": sorted(episodes["agent_down"]),
        "net_partition_buffered": sorted(episodes["partitions"]),
        "planned_reboot_gap": sorted(episodes["reboots"]),
        "panic_gap": sorted(episodes["panic"]),
    }
    _build_records(t)
    return t


def _build_records(t: RunTruth) -> None:
    p, spec, plan = t.profile, t.spec, t.plan
    T = lambda i: ts_of(p.run_start_epoch, i)
    start_ts = ts_of(p.run_start_epoch, 0)
    end_ts = ts_of(p.run_start_epoch, N)
    runs = {"run_id": p.run_id, "host_id": p.host_id, "agent_id": p.agent_id, "hostname": p.hostname,
            "service": p.service, "archetype": p.archetype, "split": p.split,
            "regime": "ood" if p.split == "test_ood" else "iid",
            "seed_spawn_key": [spec.run_index], "start_ts": start_ts, "end_ts": end_ts,
            "expected_series": list(C.SERIES), "profile": p.to_dict()}
    faults, inc_input = [], []
    ids = {pf.fault_id: pf for pf in plan.faults}
    group_ids = {}
    for pf in plan.faults:
        if pf.group is not None and pf.group not in group_ids:
            group_ids[pf.group] = f"{p.run_id}-g{len(group_ids) + 1:02d}"
    infos = {}
    for pf in plan.faults:
        s = pf.sim
        incident = pf.role != "twin"
        info = derive_precursor(s, p, incident)
        infos[pf.fault_id] = info
        conc = [o.fault_id for o in plan.faults if o is not pf and
                overlaps(s.start_i, s.conflict_end_i, o.sim.start_i, o.sim.conflict_end_i)]
        rec = {
            "fault_id": pf.fault_id, "run_id": p.run_id, "host_id": p.host_id, "family": s.family, "variant": s.variant,
            "planned_outcome": pf.planned_outcome, "outcome": s.outcome,
            "plan_group_id": group_ids.get(pf.group, f"{p.run_id}-tw-{pf.fault_id[-3:]}"),
            "params": s.params, "severity": s.severity, "mechanism_resource": s.mechanism_resource,
            "footprint_class": s.footprint_class, "conflict_footprint": sorted(footprint_signals(s.footprint_class)),
            "conflict_start": T(s.start_i), "conflict_end": T(s.conflict_end_i),
            "counter_reset_at": T(s.reset_i) if s.reset_i is not None else None,
            "counter_reset_series": ["rx", "tx"] if s.reset_i is not None else [],
            "start": T(s.start_i),
            "precursor_start": T(info.precursor_start_i) if info.precursor_start_i is not None else None,
            "precursor_lead_s": info.lead_s, "precursor_signal": info.signal,
            "precursor_start_by_signal": {k: T(v) for k, v in info.by_signal.items()},
            "silent_reason": info.silent_reason, "peak_time": T(s.peak_i) if s.peak_i is not None else None,
            "impact_start": T(s.impact_start_i) if s.impact_start_i is not None else None,
            "impact_end": T(s.impact_end_i) if s.impact_end_i is not None else None,
            "resolved_time": T(s.resolved_i) if s.resolved_i is not None else None,
            "member_relation": pf.role if incident else None, "concurrent_with": conc,
            "rejection_attempts": pf.attempts,
        }
        faults.append(rec)
        if incident:
            inc_input.append({"fault_id": pf.fault_id, "family": s.family, "member_relation": pf.role,
                              "impact_start": s.impact_start_i * DT, "impact_end": s.impact_end_i * DT,
                              "conflict_end": s.conflict_end_i * DT, "severity": s.severity,
                              "group": info.group if pf.role == "primary" else None,
                              "silent_reason": info.silent_reason, "precursor_lead_s": info.lead_s,
                              "_gi": pf.group})
    # merged incidents (integer seconds from run_start) -> timestamps
    incidents = []
    by_group = {}
    for f in inc_input:
        by_group.setdefault(f["_gi"], []).append(f)
    merged = merge_incidents([{k: v for k, v in f.items() if k != "_gi"} for f in inc_input])
    for n, m in enumerate(merged, 1):
        base_s = p.run_start_epoch * 1_000_000
        conv = lambda sec: ts_of(p.run_start_epoch, sec / DT)
        incidents.append({
            "incident_id": f"{p.run_id}-i{n:03d}", "run_id": p.run_id, "host_id": p.host_id,
            "onset": conv(m["onset"]), "recovery": conv(m["recovery"]), "cooldown_end": conv(m["cooldown_end"]),
            "oracle_mask_end": conv(m["oracle_mask_end"]), "oracle_mask_end_source": m["oracle_mask_end_source"],
            "primary_fault_id": m["primary_fault_id"], "family": m["family"], "families": m["families"],
            "members": [{"fault_id": x["fault_id"], "family": x["family"], "member_relation": x["member_relation"],
                         "impact_start": conv(x["impact_start"]), "impact_end": conv(x["impact_end"]),
                         "conflict_end": conv(x["conflict_end"])} for x in m["members"]],
            "severity": m["severity"], "group": m["group"], "silent_reason": m["silent_reason"],
            "precursor_lead_s": m["precursor_lead_s"], "n_members": m["n_members"],
            "_onset_s": m["onset"], "_mask_end_s": m["oracle_mask_end"], "_recovery_s": m["recovery"],
        })
    benign = []
    for e in t.base.benign:
        benign.append({"episode_id": e["episode_id"], "run_id": p.run_id, "type": e["type"], "start": T(e["start_i"]),
                       "end": T(e["end_i"]), "params": e["params"], "footprint": None,
                       "counter_reset_at": None, "counter_reset_series": []})
    k = len(benign)
    for (s, e) in t.episodes["reboots"]:
        k += 1
        benign.append({"episode_id": f"{p.run_id}-b{k:03d}", "run_id": p.run_id, "type": "planned_reboot",
                       "start": T(s), "end": T(e), "params": {"tick_gap_min": (e - s) * DT / 60.0},
                       "footprint": sorted(footprint_signals("F10")), "counter_reset_at": T(e),
                       "counter_reset_series": ["rx", "tx"]})
    for (s, e), prm in zip(t.episodes["blips"], t.episodes["blip_params"]):
        k += 1
        benign.append({"episode_id": f"{p.run_id}-b{k:03d}", "run_id": p.run_id, "type": "transient_blip",
                       "start": T(s), "end": T(e), "params": prm, "footprint": ["cpu", "mem", "rx", "tx"],
                       "counter_reset_at": None, "counter_reset_series": []})
    gaps = []
    gi = 0
    t.partition_gap_ids = []
    for typ in ("agent_down", "net_partition_buffered", "planned_reboot_gap", "panic_gap"):
        for (a, b) in t.gaps_grid[typ]:
            gi += 1
            gid = f"{p.run_id}-gap{gi:03d}"
            if typ == "net_partition_buffered":
                t.partition_gap_ids.append(gid)
            gaps.append({"gap_id": gid, "run_id": p.run_id, "type": typ, "start": T(a), "end": T(b)})
    gaps.sort(key=lambda g: (g["start"], g["gap_id"]))
    t.records = {"runs": runs, "faults": sorted(faults, key=lambda f: f["fault_id"]), "incidents": incidents,
                 "benign": benign, "gaps": gaps}
    t.stats = {"replans": plan.replans, "rejections": plan.rejections, "attempts": [(pf.sim.family, pf.role, pf.attempts) for pf in plan.faults],
               "groups": [(i["group"], i["silent_reason"], i["family"]) for i in incidents]}


def footprint_violations(t: RunTruth) -> list[str]:
    """FP3/FP4/FP11 on generator state (test mode): observed modified signals vs declared footprint."""
    import numpy as np
    from .footprints import CLASS_SIGNALS, footprint_class
    out, active = [], {s: np.zeros(N + 1, dtype=np.int16) for s in C.SERIES}
    for pf in t.plan.faults:
        f = pf.sim
        expect = footprint_class(f.family, f.outcome, mount=f.mount, panic=f.panic_gap is not None, subtype=f.subtype)
        if f.footprint_class != expect:
            out.append(f"{pf.fault_id}: class {f.footprint_class} != table {expect}")
        observed = set()
        for sig, (mul, add) in f.eff.items():
            nz = (mul != 1.0) | (add != 0.0)
            if nz.any():
                observed.add(sig)
                active[sig][f.start_i:f.conflict_end_i + 1] += nz.astype(np.int16)
        if not observed <= CLASS_SIGNALS[f.footprint_class]:
            out.append(f"{pf.fault_id}: observed {sorted(observed)} outside footprint {f.footprint_class}")
        if (f.reset_i is not None or f.panic_gap is not None) and f.footprint_class != "F10":
            out.append(f"{pf.fault_id}: reset/gap source not F10")
    for sig, a in active.items():
        if a.max() > 1:
            out.append(f"signal {sig}: two faults non-zero at once")
    return out


def render_view(t: RunTruth, cadence: int, jitter_on: bool = True, overrides: dict | None = None):
    return build_run_output(t.profile, t.latent, t.gaps_grid, cadence, jitter_on, t.streams, t.omit, overrides=overrides,
                            partition_ids=t.partition_gap_ids)
