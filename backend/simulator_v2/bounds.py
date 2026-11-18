"""Availability bounds derived from the timing constants (spec 8.4, 8.5). Used for protocol.json and manifest."""
from __future__ import annotations

import math

from . import constants as C


def c_max(cadence_s: float, d_max=C.D_MAX, j_max=C.J_MAX, chunk=C.REPLAY_CHUNK_MAX) -> int:
    """Largest number of chunks a partition can need: N_max = ceil((D_max + 2 j_max) / cadence), 6 obs per tick."""
    n_max = math.ceil((d_max + 2 * j_max) / cadence_s)
    return math.ceil(6 * n_max / chunk)


def buffered_bounds(cmax: int = 1, d_max=C.D_MAX, j_max=C.J_MAX, s_max=C.S_MAX, dr_max=C.REPLAY_DELAY[1],
                    chunk_delay_max=C.CHUNK_DELAY[1]) -> tuple[float, float]:
    """(lag_vs_obs_ts, lag_vs_nominal) upper bounds for buffered replay."""
    extra = chunk_delay_max * (cmax - 1)
    return d_max + dr_max + extra, d_max + j_max + s_max + dr_max + extra


def normal_bounds(j_max=C.J_MAX, s_max=C.S_MAX, d_max=C.D_NORMAL[1]) -> tuple[float, float]:
    return d_max, s_max + j_max + d_max
