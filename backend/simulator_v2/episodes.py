"""Signal-modifying benign episodes and observation-masking dropouts (spec section 3.5), placed after faults."""
from __future__ import annotations

import numpy as np

from . import constants as C
from .baseline import grid_ceil, grid_round
from .planner import overlaps

N = C.N_STEPS


class EpisodePlacementError(RuntimeError):
    pass


def _free(s, e, blockers):
    return all(not overlaps(s, e, a, b) for a, b in blockers)


def _poisson_place(rng, rate_per_day, dur_fn, blockers, label):
    out = []
    for _ in range(int(rng.poisson(rate_per_day * C.RUN_DAYS))):
        dur = dur_fn(rng)
        for _attempt in range(C.MAX_ATTEMPTS):
            s = int(rng.integers(0, N - dur))
            if _free(s, s + dur, blockers):
                out.append((s, s + dur))
                blockers.append((s, s + dur))
                break
        else:
            raise EpisodePlacementError(f"cannot place {label}")
    return out


def _place_one(rng, dur, blockers, label):
    for _ in range(C.MAX_ATTEMPTS):
        s = int(rng.integers(0, N - dur))
        if _free(s, s + dur, blockers):
            blockers.append((s, s + dur))
            return [(s, s + dur)]
    raise EpisodePlacementError(f"cannot place forced {label}")


def place_episodes(profile, plan, streams, force_one_each: bool = False) -> dict:
    """Returns dict with lists of (start_i, end_i[, params]) for reboots, blips, agent_down, partitions."""
    rng = streams.get("gaps")
    fault_iv = [(pf.sim.start_i, pf.sim.conflict_end_i) for pf in plan.faults]
    panic_iv = [pf.sim.panic_gap for pf in plan.faults if pf.sim.panic_gap]
    # signal-modifying episodes: never inside any fault's conflict interval, disjoint from each other
    sig_block = list(fault_iv)
    reboots = _poisson_place(rng, 0.05, lambda r: max(1, grid_round(float(r.uniform(2, 8)) * 60)), sig_block, "planned_reboot")
    blips = _poisson_place(rng, 0.3, lambda r: grid_ceil(float(r.uniform(20, 110))), sig_block, "transient_blip")
    if force_one_each and not reboots:
        reboots += _place_one(rng, max(1, grid_round(float(rng.uniform(2, 8)) * 60)), sig_block, "planned_reboot")
    if force_one_each and not blips:
        blips += _place_one(rng, grid_ceil(float(rng.uniform(20, 110))), sig_block, "transient_blip")
    blip_params = [{"mu": {s: float(rng.uniform(0.1, 0.4)) for s in ("cpu", "mem", "net")}} for _ in blips]
    # dropouts: disjoint from every gap interval (reboot gaps, panic gaps) and from each other
    drop_block = list(reboots) + list(panic_iv)
    agent_down = _poisson_place(rng, 0.3, lambda r: max(1, grid_round(float(r.uniform(2, 30)) * 60)), drop_block, "agent_down")
    parts = _poisson_place(rng, 0.2, lambda r: max(1, grid_round(float(r.uniform(2, 30)) * 60)), drop_block, "net_partition_buffered")
    if force_one_each:
        if not any((e - s) * C.LATENT_DT >= 600 for s, e in agent_down):
            agent_down += _place_one(rng, max(20, grid_round(float(rng.uniform(10, 30)) * 60)), drop_block, "agent_down>=10min")
        if not parts:
            parts += _place_one(rng, max(1, grid_round(float(rng.uniform(2, 30)) * 60)), drop_block, "net_partition_buffered")
    return {"reboots": reboots, "blips": blips, "blip_params": blip_params, "agent_down": agent_down,
            "partitions": parts, "panic": list(panic_iv)}
