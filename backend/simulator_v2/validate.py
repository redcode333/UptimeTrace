"""Exhaustive contract validation (spec section 11) and the independent audit helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator

from . import constants as C

REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "contracts" / "telemetry" / "v1" / "envelope.schema.json"
_WALLCLOCK = datetime.fromisoformat(C.GENERATION_WALLCLOCK.replace("Z", "+00:00"))


def load_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)       # no format_checker: the schema is self-contained


def _semantic():
    from app.ingest.semantic import validate_semantics      # the API's own semantic checks
    return validate_semantics


class Validator:
    """Counts envelopes and observations checked and failures; keeps the first few failure messages."""

    def __init__(self):
        self.v = load_validator()
        self.sem = _semantic()
        self.envelopes_checked = 0
        self.observations_checked = 0
        self.schema_failures = 0
        self.semantic_failures = 0
        self.messages: list[str] = []

    def check(self, env: dict) -> bool:
        self.envelopes_checked += 1
        self.observations_checked += len(env["metrics"])
        ok = True
        if not self.v.is_valid(env):
            self.schema_failures += 1
            ok = False
            if len(self.messages) < 5:
                self.messages.append("schema: " + "; ".join(e.message[:120] for e in list(self.v.iter_errors(env))[:2]))
        else:
            errs = self.sem(env, _WALLCLOCK)
            if errs:
                self.semantic_failures += 1
                ok = False
                if len(self.messages) < 5:
                    self.messages.append("semantic: " + str(errs[0])[:160])
        return ok

    def counts(self) -> dict:
        return {"envelopes_checked": self.envelopes_checked, "observations_checked": self.observations_checked,
                "schema_failures": self.schema_failures, "semantic_failures": self.semantic_failures}


def envelopes_from_parquet(table, hostnames: dict, services: dict) -> list[dict]:
    """Rebuild contract envelopes from a run's Parquet table (independent audit path; no generator state used)."""
    import pyarrow as pa
    from .ticks import ts_str
    cols = {n: table.column(n).to_pylist() for n in ("envelope_id", "agent_id", "host_id", "metric", "kind", "unit", "labels", "value")}
    obs = table.column("obs_ts").cast(pa.int64()).to_pylist()
    env_ts = table.column("env_ts").cast(pa.int64()).to_pylist()
    env: dict[str, dict] = {}
    for i in range(table.num_rows):
        eid = cols["envelope_id"][i]
        if eid not in env:
            hid = cols["host_id"][i]
            e = {"schema_version": "1.0", "envelope_id": eid, "agent_id": cols["agent_id"][i], "host_id": hid,
                 "hostname": hostnames[hid], "timestamp": ts_str(env_ts[i]), "metrics": []}
            if services.get(hid):
                e["service"] = services[hid]
            env[eid] = e
        labels = json.loads(cols["labels"][i])
        v = cols["value"][i]
        m = {"name": cols["metric"][i], "kind": cols["kind"][i], "value": int(v) if cols["kind"][i] == "counter" else v,
             "unit": cols["unit"][i], "timestamp": ts_str(obs[i])}
        if labels:
            m["labels"] = labels
        env[eid]["metrics"].append(m)
    return list(env.values())
