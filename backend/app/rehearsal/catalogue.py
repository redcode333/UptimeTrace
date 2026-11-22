"""The fixed catalogue of rehearsal scenarios. Plans are server-side constants: an operator selects a scenario id and cannot change
its phases, duration, cadence or limits. Nothing here runs a command on any machine.

What a scenario does: it sends UptimeTrace telemetry envelopes for ONE registered synthetic host (kind `synthetic`, id `sim-…`).
The envelopes come from the simulator's sample functions (backend/simulator/scenarios.py) and app/rehearsal/samples.py. The machine
itself is not loaded. The rehearsal tests whether UptimeTrace's own ingest, alert and recovery path reacts to the reported fault.
"""
from __future__ import annotations

from dataclasses import dataclass

CPU_METRIC = "system.cpu.utilization"
MEMORY_METRIC = "system.memory.utilization"
METRIC_LABEL = {CPU_METRIC: "CPU", MEMORY_METRIC: "Memory"}
CONSECUTIVE_FAILURE_LIMIT = 3       # this many envelopes in a row not accepted => the injection is abandoned (FAILED)
DETECTION_BUDGET_SECONDS = 60.0     # an alert that opens later than this after confirmed fault onset is LATE_DETECTION
MAX_ENVELOPES = 20                  # hard ceiling on what one rehearsal may send
STALE_HEARTBEAT_SECONDS = 120       # a RUNNING rehearsal with no progress for this long was interrupted (server restart)


@dataclass(frozen=True)
class Phase:
    name: str        # baseline | fault | recovery
    ticks: int
    source: str      # normal | cpu_saturation | memory_ramp (see app/rehearsal/samples.py)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    summary: str
    metric: str
    affects: str
    interval_seconds: int
    phases: tuple[Phase, ...]
    # The fault is guaranteed to reach this value in its last fault envelope. The safety check refuses to run when the configured
    # warning limit is above it, because the fault could then never cross a threshold.
    reach_floor: float

    @property
    def planned_envelopes(self) -> int:
        return sum(p.ticks for p in self.phases)

    @property
    def expected_duration_seconds(self) -> int:
        return (self.planned_envelopes - 1) * self.interval_seconds


_PLAN = (Phase("baseline", 2, "normal"), Phase("fault", 6, "PLACEHOLDER"), Phase("recovery", 3, "normal"))


def _plan(source: str) -> tuple[Phase, ...]:
    return tuple(Phase(p.name, p.ticks, source if p.name == "fault" else p.source) for p in _PLAN)


SCENARIOS: dict[str, Scenario] = {
    "cpu_saturation": Scenario(
        id="cpu_saturation",
        title="CPU saturation on a synthetic host",
        summary="Reports normal CPU, then CPU pinned at 92–100 % for a fixed window, then normal CPU again.",
        metric=CPU_METRIC,
        affects=("Telemetry only. UptimeTrace receives CPU utilisation for this synthetic host at 92–100 % during the fault window. "
                 "No process, load or CPU is changed on any machine, and no other host or metric is touched."),
        interval_seconds=15,
        phases=_plan("cpu_saturation"),
        reach_floor=92.0,
    ),
    "memory_saturation": Scenario(
        id="memory_saturation",
        title="Memory saturation on a synthetic host",
        summary="Reports normal memory, then memory climbing from about 80 % to 97 % over the fault window, then normal memory again.",
        metric=MEMORY_METRIC,
        affects=("Telemetry only. UptimeTrace receives memory utilisation for this synthetic host rising from about 80 % to 97 % during "
                 "the fault window. No memory is allocated on any machine, and no other host or metric is touched."),
        interval_seconds=15,
        phases=_plan("memory_ramp"),
        reach_floor=95.0,
    ),
}


def get(scenario_id: str) -> Scenario | None:
    return SCENARIOS.get(scenario_id)


def describe(s: Scenario) -> dict:
    return {
        "id": s.id, "title": s.title, "summary": s.summary, "metric": s.metric, "metric_label": METRIC_LABEL[s.metric],
        "affects": s.affects, "interval_seconds": s.interval_seconds, "planned_envelopes": s.planned_envelopes,
        "expected_duration_seconds": s.expected_duration_seconds,
        "phases": [{"name": p.name, "ticks": p.ticks} for p in s.phases],
        "limits": {"max_envelopes": MAX_ENVELOPES, "consecutive_failure_limit": CONSECUTIVE_FAILURE_LIMIT,
                   "detection_budget_seconds": DETECTION_BUDGET_SECONDS},
    }
