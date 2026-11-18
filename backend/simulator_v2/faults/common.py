"""Shared fault structures: impact ramp weights, recovery draws, simulated-fault container (spec 4.3-4.5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .. import constants as C

DT = C.LATENT_DT
SPM = 60 // DT        # grid steps per minute (2)


def steps_ceil(minutes: float) -> int:
    return max(1, int(np.ceil(minutes * SPM - 1e-9)))


def draw_tret_steps(rng, tret_max_min: float = C.TRET_MIN[1]) -> int:
    return steps_ceil(float(rng.uniform(C.TRET_MIN[0], tret_max_min)))


def draw_recovery_steps(rng, median_min: float, lo: float, hi: float) -> int:
    """R ~ LogN(median, 0.6) clipped to [lo, hi] minutes, snapped up to the grid."""
    r = median_min * np.exp(0.6 * rng.standard_normal())
    return steps_ceil(float(np.clip(r, lo, hi)))


def ramp_weights(n_win: int, k_start: int, k_end: int, tret: int) -> np.ndarray:
    """w = 0 before k_start, 1 on [k_start, k_end], linear 1 -> 0 over `tret` steps after k_end (exactly 0 at k_end + tret)."""
    w = np.zeros(n_win)
    w[k_start:k_end + 1] = 1.0
    k = np.arange(k_end + 1, k_end + tret + 1)
    w[k] = 1.0 - (k - k_end) / tret
    return w


def first_run(mask: np.ndarray, min_points: int) -> int | None:
    """Index of the first position starting `min_points` consecutive True values (None if absent)."""
    if mask.size < min_points:
        return None
    m = mask.astype(np.int8)
    cs = np.concatenate([[0], np.cumsum(m)])
    full = np.nonzero(cs[min_points:] - cs[:-min_points] == min_points)[0]
    return int(full[0]) if full.size else None


def first_at_least(arr: np.ndarray, level: float) -> int | None:
    idx = np.nonzero(arr >= level)[0]
    return int(idx[0]) if idx.size else None


def peak_index(stress: np.ndarray) -> int:
    """First grid index at which the stress variable is >= 0.99 of its maximum."""
    mx = float(np.max(stress))
    return int(np.nonzero(stress >= 0.99 * mx)[0][0])


@dataclass
class FaultSim:
    family: str
    variant: str
    outcome: str                    # 'incident' | 'twin' (as simulated)
    start_i: int
    impact_start_i: int | None
    impact_end_i: int | None
    resolved_i: int | None
    conflict_end_i: int
    peak_i: int | None
    footprint_class: str
    mechanism_resource: str
    eff: dict                       # signal -> (mul, add) arrays over [start_i, conflict_end_i]; level*mul + add
    delta: dict                     # designated precursor contributions, arrays over the same window
    params: dict = field(default_factory=dict)
    severity: float = 0.0
    reset_i: int | None = None
    panic_gap: tuple | None = None  # (a_i, b_i) no ticks in [a, b)
    tret_steps: int = 0
    subtype: str | None = None
    mount: str | None = None

    @property
    def window_len(self) -> int:
        return self.conflict_end_i - self.start_i + 1

    def apply_to(self, levels: dict) -> None:
        """levels: signal -> array (modified in place). Percent signals and rx/tx rates."""
        a, b = self.start_i, self.conflict_end_i + 1
        for sig, (mul, add) in self.eff.items():
            levels[sig][a:b] = levels[sig][a:b] * mul + add
