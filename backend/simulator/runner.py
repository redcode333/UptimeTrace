"""Sends scenario envelopes to a running API over real HTTP and tallies what actually came back."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable

import httpx

from simulator.scenarios import State, build_envelope

ENDPOINT = "/api/v1/telemetry"


@dataclass
class Summary:
    accepted: int = 0
    rejected: int = 0   # 4xx: the server understood and refused (a contract/simulator bug)
    failed: int = 0     # transport errors and 5xx: nothing was durably accepted
    by_status: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.accepted + self.rejected + self.failed

    def record(self, label: str) -> None:
        self.by_status[label] = self.by_status.get(label, 0) + 1


def run(
    *, url: str, scenario: str, count: int, interval: float, seed: int,
    agent_id: str, host_id: str, hostname: str, service: str | None,
    token: str | None = None, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Summary:
    rng = random.Random(seed)
    id_rng = random.Random(f"{seed}|{scenario}|{host_id}|{agent_id}|{now().isoformat()}")
    state = State(rx=rng.randint(10**9, 5 * 10**9), tx=rng.randint(10**9, 5 * 10**9))
    summary = Summary()
    own_client = client is None
    client = client or httpx.Client(base_url=url, timeout=10.0)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        for t in range(count):
            envelope = build_envelope(scenario, t, count, rng, state, id_rng, agent_id=agent_id, host_id=host_id,
                                      hostname=hostname, service=service, now=now(), interval=interval)
            try:
                response = client.post(ENDPOINT, json=envelope, headers=headers)
            except httpx.HTTPError as exc:
                summary.failed += 1
                summary.record(type(exc).__name__)
                summary.problems.append(f"#{t}: {type(exc).__name__}")
            else:
                label = str(response.status_code)
                summary.record(label)
                if response.status_code == 202:
                    summary.accepted += 1
                elif 400 <= response.status_code < 500:
                    summary.rejected += 1
                    summary.problems.append(f"#{t}: {label} {_error_text(response)}")
                else:
                    summary.failed += 1
                    summary.problems.append(f"#{t}: {label} {_error_text(response)}")
            if t < count - 1 and interval > 0:
                sleep(interval)
    finally:
        if own_client:
            client.close()
    return summary


def _error_text(response: httpx.Response) -> str:
    try:
        error = response.json()["error"]
        extra = "; ".join(f"{d['path']}: {d['message']}" for d in error.get("details", [])[:3])
        return f"{error['code']} {extra}".strip()
    except Exception:
        return "(non-contract response)"
