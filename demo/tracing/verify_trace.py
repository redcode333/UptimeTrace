"""End-to-end check: drive both services, then confirm in Jaeger that one trace contains both services.

Run with the demo services and the Collector/Jaeger stack up. Prints a short report and exits non-zero on failure.
"""
from __future__ import annotations

import sys
import time

import httpx

FRONT = "http://127.0.0.1:8001"
JAEGER = "http://127.0.0.1:16686"


def wait_for_jaeger(timeout: float = 60) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(f"{JAEGER}/api/services", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit("Jaeger did not become ready")


def main() -> int:
    wait_for_jaeger()
    with httpx.Client(timeout=15) as c:
        for item in ("ok-1", "slow-1", "broken-1"):
            c.get(f"{FRONT}/checkout/{item}")
    deadline = time.time() + 30
    found = []
    while time.time() < deadline and not found:
        time.sleep(2)
        data = httpx.get(f"{JAEGER}/api/traces", params={"service": "frontend-api", "limit": 20}, timeout=10).json()["data"]
        for t in data:
            services = {p["serviceName"] for p in t["processes"].values()}
            if {"frontend-api", "inventory-api"} <= services:
                found.append(t)
    if not found:
        print("FAIL: no trace containing both frontend-api and inventory-api")
        return 1
    t = found[0]
    print(f"OK: {len(found)} distributed trace(s); example trace {t['traceID']} with {len(t['spans'])} spans")
    for s in t["spans"]:
        svc = t["processes"][s["processID"]]["serviceName"]
        print(f"  {svc:14} {s['operationName']:28} {s['duration'] / 1000:8.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
