"""Usage (from backend/):
    python -m simulator --scenario cpu_saturation --count 30 --interval 0.5 --seed 7
"""
from __future__ import annotations

import argparse
import os
import sys

from simulator.runner import run
from simulator.scenarios import HOST_ID_PATTERN, SCENARIOS


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="simulator", description="UptimeTrace telemetry simulator (real HTTP).")
    p.add_argument("--url", default="http://127.0.0.1:8000", help="API base URL (default %(default)s)")
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal_baseline")
    p.add_argument("--count", type=int, default=20, help="number of envelopes to send")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between requests")
    p.add_argument("--seed", type=int, default=1, help="RNG seed (metric values are reproducible; envelope ids are unique per run)")
    p.add_argument("--agent-id", default="sim-agent-01")
    p.add_argument("--host-id", default="sim-host-01", help="must start with 'sim-' so simulator data stays identifiable")
    p.add_argument("--hostname", default="sim-host-01.local")
    p.add_argument("--service", default="sim-service", help="optional service; pass '' to omit")
    p.add_argument("--token", default=os.environ.get("UPTIMETRACE_TOKEN"),
                   help="agent bearer token (default: $UPTIMETRACE_TOKEN); issue one with python -m app.auth issue --kind synthetic")
    a = p.parse_args(argv)
    if not HOST_ID_PATTERN.match(a.host_id):
        p.error("--host-id must start with 'sim-' (or legacy 'sim<digits>-') so simulator rows stay distinguishable")
    if a.count < 1 or a.interval < 0:
        p.error("--count must be >= 1 and --interval >= 0")

    summary = run(url=a.url, scenario=a.scenario, count=a.count, interval=a.interval, seed=a.seed,
                  agent_id=a.agent_id, host_id=a.host_id, hostname=a.hostname, service=a.service or None, token=a.token)

    print(f"scenario={a.scenario} seed={a.seed} target={a.url}")
    print(f"sent={summary.total} accepted={summary.accepted} rejected={summary.rejected} failed={summary.failed}")
    print("status codes: " + (", ".join(f"{k}x{v}" for k, v in sorted(summary.by_status.items())) or "none"))
    for line in summary.problems[:10]:
        print("  " + line)
    return 0 if summary.accepted == summary.total else 1


if __name__ == "__main__":
    sys.exit(main())
