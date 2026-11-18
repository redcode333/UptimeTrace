"""Time helpers: grid index / seconds -> UTC microsecond timestamp strings."""
from __future__ import annotations

from .ticks import ts_str
from . import constants as C


def ts_of(run_start_epoch: int, grid_index) -> str:
    """Grid index (30 s steps, may be float for second-valued inputs) -> contract timestamp string."""
    return ts_str(run_start_epoch * 1_000_000 + int(round(float(grid_index) * C.LATENT_DT * 1_000_000)))
