"""Fault planner (spec section 5): sequential groups, relapses, twins, footprint compatibility, concurrency."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import constants as C
from .faults.families import REDRAW_START, SIM, Ctx, GenerationAborted
from .footprints import compatible

N = C.N_STEPS
STEPS_900 = C.COOLDOWN // C.LATENT_DT          # 30
T_MIN = C.PLANNER_WARMUP // C.LATENT_DT        # 240
GUARD = C.PLANNER_GUARD // C.LATENT_DT         # 1
T_MAX = N - (C.COOLDOWN + C.PLANNER_GUARD) // C.LATENT_DT


class PlanInfeasible(RuntimeError):
    pass


R_START_DOMAIN = "start_domain_infeasible"      # every attempt failed a start-dependent precondition / horizon check
R_ATTEMPTS = "start_attempts_exhausted"         # attempts got past the preconditions but none produced an accepted placement
R_NO_ROOM = "no_room_after_t_free"
R_MASK = "mask_end_exceeds_run_end"
R_RELAPSE = "relapse_unplaceable"
R_TWIN = "twin_unplaceable"


class GroupRejected(Exception):
    """The run cannot fit all its primaries (spec section 5): the whole run plan is redrawn (<= 100 times)."""

    def __init__(self, reason: str, detail: dict | None = None):
        self.reason, self.detail = reason, dict(detail or {})
        super().__init__(reason if not detail else f"{reason} {self.detail}")


class PlacementInfeasible(GroupRejected):
    """A primary has no feasible start after T_free within the 1000-attempt limit."""


@dataclass
class PlannedFault:
    sim: object
    role: str                    # primary | relapse | twin
    planned_outcome: str
    attempts: int
    group: int | None
    fault_id: str = ""


@dataclass
class RunPlan:
    faults: list = field(default_factory=list)
    replans: int = 0
    attempts_total: int = 0
    rejections: list = field(default_factory=list)      # one structured record per rejected plan


def overlaps(a0, a1, b0, b1) -> bool:
    """Half-open [a0, a1) and [b0, b1) overlap iff a0 < b1 and b0 < a1 (touching does not overlap)."""
    return a0 < b1 and b0 < a1


def placement_ok(sim, placed: list[PlannedFault]) -> bool:
    """Footprint compatibility with every overlapping fault, and at most CONCURRENCY_LIMIT overlapping at any instant."""
    ov = [pf for pf in placed if overlaps(sim.start_i, sim.conflict_end_i, pf.sim.start_i, pf.sim.conflict_end_i)]
    for pf in ov:
        if not compatible(sim.footprint_class, pf.sim.footprint_class):
            return False
    if len(ov) + 1 > C.CONCURRENCY_LIMIT:
        # more than 2 candidates overlap the new interval: a triple overlap exists iff two of them overlap each other
        if len(ov) >= C.CONCURRENCY_LIMIT:
            for i in range(len(ov)):
                for j in range(i + 1, len(ov)):
                    a, b = ov[i].sim, ov[j].sim
                    if overlaps(a.start_i, a.conflict_end_i, b.start_i, b.conflict_end_i):
                        return False
    return True


def _rng_for(streams, replan, slot, sub=0):
    return lambda attempt: streams.fresh("faults", 1, replan, slot, sub, attempt)


def _place_incident(family, ctx, start, rng_of, placed, tret_max, force_step=False, accept_extra=None):
    start0 = start
    kinds: dict[str, int] = {"precondition_redraw_start": 0, "precondition_other": 0, "outcome_mismatch": 0,
                             "beyond_run_end": 0, "placement_conflict": 0, "rejected_by_caller": 0}
    for attempt in range(1, C.MAX_ATTEMPTS + 1):
        f = SIM[family](ctx, start, "incident", rng_of(attempt), tret_max, force_step)
        if f is REDRAW_START:
            kinds["precondition_redraw_start"] += 1
            # theta - M0 < 5: redraw the start uniformly in [start0, t_max] (never earlier than the planner's T_free);
            # the redraw consumes an attempt, so the 1000-attempt cap is unchanged
            start = int(rng_of(attempt + 1_000_000).integers(start0, T_MAX + 1))
            continue
        if f is None:
            kinds["precondition_other"] += 1
            continue
        if f.outcome != "incident":
            kinds["outcome_mismatch"] += 1
            continue
        if f.conflict_end_i > N:
            kinds["beyond_run_end"] += 1
            continue
        if not placement_ok(f, placed):
            kinds["placement_conflict"] += 1
            continue
        if accept_extra is not None and not accept_extra(f):
            kinds["rejected_by_caller"] += 1
            continue
        return f, attempt
    # Attempts exhausted. If not a single attempt got past the start-dependent preconditions / horizon checks, the start
    # domain [start0, t_max] is (operationally) infeasible; otherwise the attempts were merely exhausted.
    past = kinds["outcome_mismatch"] + kinds["placement_conflict"] + kinds["rejected_by_caller"]
    reason = R_START_DOMAIN if past == 0 else R_ATTEMPTS
    raise PlacementInfeasible(reason, {"family": family, "start0": start0, "attempts": C.MAX_ATTEMPTS, **kinds})


def _plan_once(spec, ctx, streams, replan: int) -> RunPlan:
    rng = streams.fresh("planner", replan)
    plan = RunPlan(replans=replan)
    primaries = list(spec.primaries)
    order = rng.permutation(len(primaries))
    primaries = [primaries[int(i)] for i in order]
    targets = np.sort(rng.integers(T_MIN, T_MAX + 1, size=len(primaries)))
    t_free = T_MIN
    slot = 0
    for gi, ((family, gets_relapse), target) in enumerate(zip(primaries, targets)):
        start = max(int(target), t_free)
        slot += 1
        tret_max = C.RELAPSE_TRET_MAX_MIN if gets_relapse else C.TRET_MIN[1]
        if start > N - 3 * STEPS_900:
            raise GroupRejected(R_NO_ROOM, {"family": family, "start": start})
        if gets_relapse:
            group = None
            for attempt in range(1, C.MAX_ATTEMPTS + 1):
                prim, a1 = _place_incident(family, ctx, start, _rng_for(streams, replan, slot, attempt), plan.faults, tret_max)
                rel = _try_relapse(family, ctx, prim, plan.faults + [PlannedFault(prim, "primary", "incident", a1, gi)],
                                   streams, replan, slot, attempt)
                if rel is not None:
                    group = (prim, a1, rel[0], rel[1])
                    break
            if group is None:
                raise GroupRejected(R_RELAPSE, {"family": family, "attempts": C.MAX_ATTEMPTS})
            prim, a1, rel, a2 = group
            members = [PlannedFault(prim, "primary", "incident", a1, gi), PlannedFault(rel, "relapse", "incident", a2, gi)]
        else:
            prim, a1 = _place_incident(family, ctx, start, _rng_for(streams, replan, slot), plan.faults, tret_max)
            members = [PlannedFault(prim, "primary", "incident", a1, gi)]
        R_k = max(m.sim.impact_end_i for m in members)
        mask_end = max(R_k + STEPS_900, max(m.sim.conflict_end_i for m in members))
        if mask_end > N:                                   # group rejected (spec step 3)
            raise GroupRejected(R_MASK, {"family": family, "group": gi, "mask_end": mask_end, "run_end": N})
        plan.faults.extend(members)
        t_free = max(R_k + STEPS_900 + GUARD, max(m.sim.conflict_end_i for m in members))
    # twins: one per precursor-family primary (>= 1:1 per (split, family))
    tslot = 0
    for family in C.PRECURSOR_FAMILIES:
        n_tw = sum(1 for fam, _ in spec.primaries if fam == family)
        for _ in range(n_tw):
            tslot += 1
            trng = streams.fresh("planner", 2, replan, tslot)
            placed_tw = None
            for attempt in range(1, C.MAX_ATTEMPTS + 1):
                start = int(trng.integers(T_MIN, N - 100))
                f = SIM[family](ctx, start, "twin", streams.fresh("faults", 2, replan, tslot, attempt))
                if f is None or f.outcome != "twin" or f.conflict_end_i > N:
                    continue
                if not placement_ok(f, plan.faults):
                    continue
                placed_tw = (f, attempt)
                break
            if placed_tw is None:
                raise GroupRejected(R_TWIN, {"family": family, "attempts": C.MAX_ATTEMPTS})
            plan.faults.append(PlannedFault(placed_tw[0], "twin", "twin", placed_tw[1], None))
    return plan


def _try_relapse(family, ctx, prim, placed, streams, replan, slot, outer):
    lo = max(prim.conflict_end_i, prim.impact_end_i + C.RELAPSE_JOIN_MIN // C.LATENT_DT)
    hi = prim.impact_end_i + C.RELAPSE_JOIN_MAX // C.LATENT_DT
    if lo > hi:
        return None
    rng = streams.fresh("planner", 3, replan, slot, outer)
    force_step = family in ("cpu_runaway", "net_flood")

    def accept(f):
        return (f.start_i >= prim.conflict_end_i and
                prim.impact_end_i + C.RELAPSE_JOIN_MIN // C.LATENT_DT <= f.impact_start_i <= hi)

    for attempt in range(1, 201):
        start = int(rng.integers(lo, hi + 1))
        f = SIM[family](ctx, start, "incident", streams.fresh("faults", 3, replan, slot, outer, attempt),
                        C.TRET_MIN[1], force_step)
        if f is None or f.outcome != "incident" or f.conflict_end_i > N or not accept(f):
            continue
        if not placement_ok(f, placed):
            continue
        return f, attempt
    return None


def plan_run(spec, profile, base, streams) -> RunPlan:
    ctx = Ctx(profile, base, C.OOD_SLOWDOWN if spec.ood_slow else 1.0)
    rejections: list[dict] = []
    for replan in range(C.MAX_REPLANS):
        try:
            plan = _plan_once(spec, ctx, streams, replan)
            plan.replans = replan
            plan.rejections = rejections
            # chronological ids
            plan.faults.sort(key=lambda pf: (pf.sim.start_i, pf.role))
            for n, pf in enumerate(plan.faults, 1):
                pf.fault_id = f"{spec.run_id}-f{n:03d}"
            plan.attempts_total = sum(pf.attempts for pf in plan.faults)
            return plan
        except GroupRejected as e:
            rejections.append({"replan": replan, "reason": e.reason, **{k: v for k, v in e.detail.items() if k in ("family", "group")},
                               "detail": e.detail})
            continue
    from collections import Counter
    counts = dict(Counter(r["reason"] for r in rejections))
    err = PlanInfeasible(f"{spec.run_id}: {C.MAX_REPLANS} re-plans exhausted; reasons {counts}; last {rejections[-1]['detail']}")
    err.rejections = rejections
    raise err
