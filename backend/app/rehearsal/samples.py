"""Sample sources for rehearsal phases. The envelope layout and the normal baseline come from the simulator; this module only decides,
for each phase, which sample function produces the fault.

Memory ramp: memory rises from about 80 % to about 97 % across the fault phase, so the first fault envelope is below the default
memory warning limit (85 %) and a later one crosses it. Every other metric stays at the simulator's normal baseline.
"""
from __future__ import annotations

import random

from simulator.scenarios import Sample, normal_baseline

RAMP_START = 80.0
RAMP_END = 97.0
RAMP_NOISE = 0.3


def memory_ramp(j: int, ticks: int, rng: random.Random) -> Sample:
    """Sample for fault tick `j` (0-based) of a fault phase with `ticks` ticks."""
    base = normal_baseline(j, ticks, rng)
    progress = (j + 1) / ticks
    memory = RAMP_START + (RAMP_END - RAMP_START) * progress + rng.uniform(-RAMP_NOISE, RAMP_NOISE)
    return Sample(base.cpu, memory, base.disk_root, base.disk_data, base.rx_rate, base.tx_rate)


def phase_sampler(source: str, j: int, ticks: int, planned: int, tick: int):
    """(scenario argument for build_envelope, t) for one envelope.

    normal         -> the simulator's baseline, indexed by the run tick
    cpu_saturation -> the simulator's saturated CPU sample (its index is past the ramp)
    memory_ramp    -> memory_ramp for fault tick j
    """
    if source == "normal":
        return "normal_baseline", tick
    if source == "cpu_saturation":
        return "cpu_saturation", planned - 1
    if source == "memory_ramp":
        return (lambda t, n, rng: memory_ramp(j, ticks, rng)), planned - 1
    raise ValueError(f"unknown phase source {source!r}")
