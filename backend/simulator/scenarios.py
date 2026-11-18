"""Six telemetry scenarios. Each produces contract-valid envelopes from a seeded RNG.

A scenario is a pure function of (tick index, total ticks) plus the seeded RNG, so the metric
*values* of a run are reproducible from --seed. Envelope ids come from a separate RNG that the
runner seeds per run (seed + scenario + host + agent + run start time): re-running the same seed
must produce NEW envelopes, not retries. Timestamps come from the clock the runner supplies.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

import re

SCHEMA_VERSION = "1.0"
# Simulator rows are identifiable by host_id: `sim-…`, or the legacy `sim2-…` used by the first runs.
HOST_ID_PATTERN = re.compile(r"^sim[0-9]*-")
MOUNTS = ("/", "/data")


def fmt_ts(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def clamp_pct(value: float) -> float:
    return round(min(100.0, max(0.0, value)), 2)


@dataclass
class State:
    """Counter state carried across ticks (counters are cumulative and never decrease)."""
    rx: int
    tx: int
    last: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Sample:
    cpu: float
    memory: float
    disk_root: float
    disk_data: float
    rx_rate: float  # bytes / second
    tx_rate: float


def _noise(rng: random.Random, spread: float) -> float:
    return rng.uniform(-spread, spread)


def normal_baseline(t: int, n: int, rng: random.Random) -> Sample:
    return Sample(
        cpu=25 + _noise(rng, 8), memory=48 + _noise(rng, 2), disk_root=58 + t * 0.01 + _noise(rng, 0.2),
        disk_data=34 + _noise(rng, 0.2), rx_rate=120_000 + _noise(rng, 30_000), tx_rate=60_000 + _noise(rng, 15_000))


def cpu_saturation(t: int, n: int, rng: random.Random) -> Sample:
    """Normal for the first third, then CPU pinned at 92-100% while memory/network barely move."""
    base = normal_baseline(t, n, rng)
    saturated = t >= n // 3
    cpu = rng.uniform(92, 100) if saturated else 28 + _noise(rng, 8)
    return Sample(cpu, base.memory + (4 if saturated else 0), base.disk_root, base.disk_data, base.rx_rate, base.tx_rate)


def memory_pressure(t: int, n: int, rng: random.Random) -> Sample:
    """Memory climbs 45% -> ~97% with small reclaim dips; CPU rises a little (swapping/GC)."""
    progress = t / max(n - 1, 1)
    memory = 45 + 52 * progress + _noise(rng, 1.2) - (3 if t % 9 == 8 else 0)
    base = normal_baseline(t, n, rng)
    return Sample(base.cpu + 20 * progress, memory, base.disk_root, base.disk_data, base.rx_rate, base.tx_rate)


def disk_pressure(t: int, n: int, rng: random.Random) -> Sample:
    """The root volume fills linearly 60% -> 97%; /data stays flat. Nothing else is abnormal."""
    progress = t / max(n - 1, 1)
    base = normal_baseline(t, n, rng)
    return Sample(base.cpu, base.memory, 60 + 37 * progress + _noise(rng, 0.15), base.disk_data,
                  base.rx_rate, base.tx_rate)


def network_spike(t: int, n: int, rng: random.Random) -> Sample:
    """A burst across the middle fifth of the run: receive rate x40, transmit x15, CPU up modestly."""
    base = normal_baseline(t, n, rng)
    burst = 2 * n // 5 <= t < 3 * n // 5
    return Sample(base.cpu + (18 if burst else 0), base.memory, base.disk_root, base.disk_data,
                  base.rx_rate * (40 if burst else 1), base.tx_rate * (15 if burst else 1))


def gradual_degradation(t: int, n: int, rng: random.Random) -> Sample:
    """Everything drifts the wrong way together and noise grows: the pattern a predictor should catch
    before any single metric crosses an obvious threshold."""
    progress = t / max(n - 1, 1)
    jitter = 1 + 3 * progress
    return Sample(
        cpu=25 + 55 * progress + _noise(rng, 2 * jitter),
        memory=48 + 38 * progress + _noise(rng, 0.8 * jitter),
        disk_root=58 + 14 * progress + _noise(rng, 0.2),
        disk_data=34 + 6 * progress + _noise(rng, 0.2),
        rx_rate=120_000 * (1 - 0.5 * progress) + _noise(rng, 20_000 * jitter),
        tx_rate=60_000 * (1 + 1.5 * progress) + _noise(rng, 10_000 * jitter))


SCENARIOS: dict[str, Callable[[int, int, random.Random], Sample]] = {
    "normal_baseline": normal_baseline,
    "cpu_saturation": cpu_saturation,
    "memory_pressure": memory_pressure,
    "disk_pressure": disk_pressure,
    "network_spike": network_spike,
    "gradual_degradation": gradual_degradation,
}


def build_envelope(
    scenario: str, t: int, n: int, rng: random.Random, state: State, id_rng: random.Random, *,
    agent_id: str, host_id: str, hostname: str, service: str | None, now: datetime, interval: float,
) -> dict:
    sample = SCENARIOS[scenario](t, n, rng)
    state.rx += max(0, int(sample.rx_rate * max(interval, 1.0)))
    state.tx += max(0, int(sample.tx_rate * max(interval, 1.0)))
    observed = fmt_ts(now - timedelta(milliseconds=rng.randint(50, 400)))  # sampled just before sending

    def gauge(name, value, labels=None):
        m = {"name": name, "kind": "gauge", "value": clamp_pct(value), "unit": "percent", "timestamp": observed}
        if labels:
            m["labels"] = labels
        return m

    def counter(name, value):
        return {"name": name, "kind": "counter", "value": value, "unit": "bytes",
                "timestamp": observed, "labels": {"interface": "eth0"}}

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "envelope_id": str(uuid.UUID(int=id_rng.getrandbits(128), version=4)),
        "agent_id": agent_id,
        "host_id": host_id,
        "hostname": hostname,
        "timestamp": fmt_ts(now),
        "metrics": [
            gauge("system.cpu.utilization", sample.cpu),
            gauge("system.memory.utilization", sample.memory),
            gauge("system.disk.utilization", sample.disk_root, {"mount": MOUNTS[0]}),
            gauge("system.disk.utilization", sample.disk_data, {"mount": MOUNTS[1]}),
            counter("system.network.rx_bytes", state.rx),
            counter("system.network.tx_bytes", state.tx),
        ],
    }
    if service:
        envelope["service"] = service
    return envelope
