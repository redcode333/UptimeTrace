# UptimeTrace Telemetry Contract

Contract between the Go agent (producer) and the FastAPI backend (consumer).

**Single source of truth:** [`v1/envelope.schema.json`](v1/envelope.schema.json) (JSON Schema 2020-12).
It is self-contained: every rule is enforced by plain schema keywords, with no reliance on `format` assertion
or custom code. Implementations in Go and Python are *consumers* of this file and must pass the shared fixtures
in `v1/examples/` (see [Verifying](#verifying)).

## Envelope

One envelope = one batch of observations from one host. JSON, UTF-8.

| Field | Req | Type | Rules |
|---|---|---|---|
| `schema_version` | yes | string | Exactly `"1.0"` for this schema |
| `envelope_id` | yes | string | lowercase hyphenated UUID (versions 1–8, RFC 4122 variant; v4 or v7 recommended; nil UUID rejected). See Envelope identity |
| `agent_id` | yes | string | 1–128 chars, `[A-Za-z0-9][A-Za-z0-9._:-]*`; stable per agent install |
| `host_id` | yes | string | same rules; stable per host, survives hostname change; part of the series key |
| `hostname` | yes | string | 1–253 chars; display only |
| `timestamp` | yes | string | **send time** (see Timestamps) |
| `service` | no | string | `^[a-z][a-z0-9_.-]{0,62}$` |
| `metrics` | yes | array | 1–500 observations. Unknown fields anywhere are rejected |

### Envelope identity and idempotency

`envelope_id` is the identity of one envelope. `host_id` + `timestamp` is **not** an identity: a host can legitimately send several envelopes with the same send time (clock resolution, restarts, clock steps), and two different envelopes may collide on it.

Agent rules:
- Every newly created envelope gets a **new** `envelope_id` (a fresh UUID).
- A retry of an envelope resends the **same content with the same `envelope_id`**: the agent persists the envelope as built (id included) in its buffer and never rebuilds it on retry.
- Data that is re-collected, re-batched, or split into new envelopes is a *new* envelope with a new id, even if it contains the same observations.

Intended server behaviour (specified, not implemented; storage details are not decided here):
- The ingestion service keeps `envelope_id` in durable storage together with a fingerprint of the envelope content, written atomically with the durable acceptance described under `202 Accepted`.
- First time an id is seen → validate, durably accept, answer `202`.
- Id already accepted with equivalent content (parsed-JSON equality, not byte equality) → do not store again; answer `202` with the same body. This is what makes agent retries after a lost response safe.
- Id already accepted with **different** content → `409 envelope_id_conflict`; this is an agent bug and is not retried.
- Ids need to be remembered at least as long as an agent can still retry an envelope (its buffer retention). The retention period is an open decision.
- `envelope_id` deduplicates *envelopes*. It says nothing about observations repeated inside different envelopes; see Observation identity.

### Metric observation

| Field | Req | Type | Rules |
|---|---|---|---|
| `name` | yes | string | `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, ≤128 chars |
| `kind` | yes | `gauge` \| `counter` | counter = cumulative total; may reset to a lower value (reboot/restart), consumers treat a decrease as a reset |
| `value` | yes | number | see Values |
| `unit` | yes | `percent` \| `bytes` \| `seconds` \| `count` | |
| `timestamp` | yes | string | **observation time** (see Timestamps) |
| `labels` | no | object | ≤8 entries; keys `^[a-z][a-z0-9_]{0,31}$`; values strings ≤128 chars |

### Observation identity

An observation is identified by:

```
host_id  +  name  +  labels  +  observation timestamp
```

- `host_id` and the observation fields come from the envelope and the observation respectively. `labels` compare as a set of key/value pairs (key order is irrelevant; absent `labels` equals `{}`).
- `service` and `agent_id` are **not** part of the identity: they describe who reported it, not what was measured. A host is expected to report under one service at a time; if it is reassigned, history keeps its identity but carries whichever service was reported.
- `kind` and `unit` are properties of a metric name, not of identity.

How legitimate repeats are distinguished: the same metric from different devices (`mount=/` vs `mount=/data`, `interface=eth0` vs `eth1`) differs by **labels**; successive samples of one series differ by **timestamp**; the same name on different hosts differs by **host_id**.

An **accidental duplicate** is two observations *within one envelope* with the same name, same labels and same observation timestamp. This is rejected even when the values are equal, since it indicates an agent bug (e.g. a collector run twice). It is a semantic check (`422 validation_error`, `path` of the second occurrence): it is deliberately not expressed in the JSON Schema, because uniqueness over a computed key is not expressible without brittle constructs.

Out of scope for validation: the same identity arriving in *different* envelopes (e.g. overlapping replay after an agent restart). That is not an error; how the storage layer reconciles it (ignore, or last write wins) is an open decision. Identical values make this harmless.

### Values

- `value` is any **finite JSON number**, integer or floating point (`42`, `42.0`, `-0.0042`, `1.5e3` are all fine and
  are the same kind of thing). The schema bounds it to the IEEE-754 double range, which rejects overflow literals like `1e999`.
- **Default: no sign or range constraint.** Constraints are attached only where they follow from the meaning:
  - `unit: percent` → `0 ≤ value ≤ 100` (utilisation is a percentage, not a 0–1 ratio).
  - `kind: counter` → `value ≥ 0` (a cumulative total cannot be negative).
  - well-known metric names (below) → fixed `kind`, `unit` and required labels.
- Negative gauges are valid: e.g. `system.clock.offset` = `-0.0042` seconds (see `examples/valid/extended-values.json`).
- JSON has no NaN/Infinity. Parsers must treat the literals `NaN`, `Infinity`, `-Infinity` as malformed JSON
  (Python's `json.loads` accepts them by default; the server must pass `parse_constant` that raises). The agent omits an observation it cannot compute rather than sending a placeholder.
- Precision: consumers read numbers as IEEE doubles; integers are exact up to 2^53−1 (≈9 PB for byte counters). Beyond that, counters lose integer precision. Accepted limitation for v1.

### Timestamps

Both `timestamp` fields use one format, defined precisely by the pattern in `$defs/timestamp`:

```
YYYY-MM-DDTHH:MM:SS[.f]Z      f = 1–6 fractional digits
```

- Uppercase `T` and `Z`. **`Z` is the only accepted zone designator**: no numeric offsets, not even `+00:00` or `-00:00`, and no missing zone. That makes every accepted value an unambiguous UTC instant; the agent converts local time to UTC before sending. The Go agent can use `t.UTC().Format("2006-01-02T15:04:05.000000Z")` (or a trimmed fraction).
- Year `1000`–`9999`; month `01–12`; day valid for that month **including leap years** (`2024-02-29` yes, `2026-02-29` and `2100-02-29` no); hour `00–23`; minute/second `00–59`. Leap second `:60` is not accepted (agents must clamp). ASCII digits only.
- Calendar validity is enforced **by the schema pattern itself**, so any compliant validator rejects month 13 or Feb 30 without `format` support. Tests prove the pattern agrees with the real calendar (see Verifying).

The two timestamps have different jobs:

| Field | Meaning | Used for |
|---|---|---|
| envelope `timestamp` | when the agent created/sent the envelope | clock-skew diagnostics, ordering of envelopes, "last seen" of an agent. Not a key |
| observation `timestamp` | when that value was sampled | the stored time-series point; all queries and prediction use this |

Observation timestamps may be older than the envelope timestamp, by any amount (the agent may batch, or replay buffered data after an outage), and differ between observations. They should not be *later* than it.

Semantic checks the server applies on top of the schema (each failure → `422 validation_error` with the offending `path`; **specified, not yet implemented**). They are independent of one another:

1. **Observation vs envelope:** `observation.timestamp ≤ envelope.timestamp + 5 s`. The 5-second tolerance absorbs clock steps or NTP adjustments on the agent between sampling and building the envelope; it is a constant of this contract version, not a per-deployment setting. Both values are agent-clock readings, so server time plays no part.
2. **Envelope vs server:** `envelope.timestamp ≤ server_now + 5 min`. This guards against a badly wrong agent clock. It applies to the envelope timestamp only; since check 1 bounds every observation by it, no observation can be more than 5 min 5 s ahead of the server either. There is no lower bound (old data is legitimate).
3. **Duplicate observations** within one envelope (see Observation identity).

No maximum age is enforced in v1 (buffered replay after an outage must work); a retention/staleness limit is a later server-side policy.

### Naming conventions

- Metric names: lowercase, dot-separated `<domain>.<subject>.<quantity>`, underscores inside segments, no unit suffix (`unit` carries it).
- Label keys: lowercase snake_case. Labels distinguish series of one metric; they are not free-form metadata.
- Series identity = `host_id` + `name` + `labels`; adding the observation `timestamp` gives observation identity (see Observation identity).

### v1 well-known metrics

The schema enforces `kind`, `unit` and required labels for these. Any other name that satisfies the general rules is accepted unchanged.

| Name | kind | unit | Required labels | Notes |
|---|---|---|---|---|
| `system.cpu.utilization` | gauge | percent | – | whole-host CPU busy % |
| `system.memory.utilization` | gauge | percent | – | used / total, excluding reclaimable cache where the OS reports it |
| `system.disk.utilization` | gauge | percent | `mount` | optional `device` |
| `system.network.rx_bytes` | counter | bytes | `interface` | cumulative received |
| `system.network.tx_bytes` | counter | bytes | `interface` | cumulative transmitted |

Examples: [`full-linux-host.json`](v1/examples/valid/full-linux-host.json),
[`minimal-no-service.json`](v1/examples/valid/minimal-no-service.json),
[`extended-values.json`](v1/examples/valid/extended-values.json) (negative gauge, integer vs float, unlisted metric names, leap day).

## HTTP interface

### `POST /api/v1/telemetry`

- `Content-Type: application/json` (UTF-8). Max body 1 MiB. Body: one envelope.
- `Authorization: Bearer <agent token>` is **required**. A missing or invalid token gets `401 unauthorized` (checked before the body is read). The token is bound to one `agent_id` and an explicit list of `host_id`s and is never part of the body; an envelope whose `agent_id` / `host_id` is outside the token's scope gets `403 forbidden` (checked after schema validation).

#### `202 Accepted`

```json
{ "status": "accepted", "envelope_id": "0190f3a2-6b1e-7c4d-9a3f-5d2e8b7a1c04", "accepted_metrics": 6 }
```

**`202` means the server has durably accepted responsibility for the envelope.** The future implementation may return `202` only after **both**:
1. the envelope passed all validation (JSON Schema and the semantic checks), **and**
2. it was durably accepted: written to persistent storage or to a durable queue, so that it survives a server restart or crash, together with its `envelope_id` (see Envelope identity).

Validation alone is not acceptance. If durable acceptance fails or cannot be confirmed (storage down, queue full, timeout), the server **must not** return `202`; it answers `503` (or `500`) and the agent retries the same envelope with the same `envelope_id`. After `202` the agent may delete the envelope from its buffer.

A retry of an already-accepted envelope (same `envelope_id`, equivalent content) also gets `202` with the same body, without storing it twice.

What `202` still does **not** mean: that the data has been processed or is already queryable (processing after durable acceptance is asynchronous, and failures there are handled by the server, not reported to the agent), or any ordering across envelopes.

`202` rather than `200` is used so clients do not read it as "stored and queryable".

#### Errors

One shape for all errors:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Telemetry envelope failed validation.",
    "details": [
      { "path": "metrics[0].value", "message": "100.5 is greater than the maximum of 100" }
    ]
  }
}
```

`path` points into the request body; `details` is present only for `validation_error`.

| Status | `code` | When | Agent action |
|---|---|---|---|
| 400 | `malformed_json` | not parseable JSON (incl. `NaN`/`Infinity` literals), or top level is not an object | drop, log |
| 400 | `unsupported_schema_version` | `schema_version` is a string not in the server's supported list; `error.supported_versions` lists accepted ones | drop, log; see below |
| 401 | `unauthorized` | missing, malformed or unknown bearer token | do not retry blindly; fix credentials |
| 403 | `forbidden` | valid token, but `agent_id` / `host_id` is outside the credential's scope | drop, log (misconfiguration; never retried) |
| 409 | `envelope_id_conflict` | `envelope_id` was already accepted with different content | drop, log (agent bug; never retried) |
| 413 | `payload_too_large` | body > 1 MiB | drop, log (reduce batch size) |
| 415 | `unsupported_media_type` | Content-Type is not `application/json` | drop, log (bug) |
| 422 | `validation_error` | valid JSON, supported version, but fails schema or semantic checks (including `schema_version` missing or not a string) | drop, log |
| 429 | `rate_limited` | reserved (future) | retry with backoff, honour `Retry-After` |
| 500/503 | `internal_error` / `unavailable` | server fault, or validated but **not durably accepted** | retry with backoff |

Rule of thumb for agents: retry 429, 5xx and network errors; do not retry any other 4xx, because resending the same bytes cannot succeed.

Order of checks: authentication → size → content type → JSON parse → `schema_version` → schema → host identity (scope) → semantic checks. The version is read *before* full validation so a payload of a different version yields one clear `unsupported_schema_version` instead of many field errors.

```json
{ "error": { "code": "unsupported_schema_version", "message": "Schema version '2.0' is not supported.", "supported_versions": ["1.0"] } }
```

## Versioning

**`schema_version` is a string `"MAJOR.MINOR"` naming the exact contract the payload claims to follow.** The server keeps an explicit list of versions it accepts (initially `["1.0"]`) and matches strings exactly — no ranges, no parsing of "newer-than". The URL carries only the major (`/api/v1/…`).

**Breaking change (→ new MAJOR, new path `/api/v2/…`, new directory `v2/`):** removing or renaming a field; making an optional field required; changing the type, unit, or meaning of a field or of an existing metric name; tightening a constraint on an existing field or metric (e.g. forbidding a previously valid value); changing timestamp format or error codes. The old major keeps being served for a deprecation period.

**Non-breaking (→ new MINOR):**
- *Adding an optional field* to the envelope or an observation. Because objects are closed (unknown fields rejected), an old server would reject it; hence a minor bump, new schema file, and the rollout rule below.
- *Adding a unit* (extending the `unit` enum). Same procedure.
- *Loosening* a constraint (e.g. admitting a wider range for a unit).
- *Adding per-name rules for a metric name that no agent has been sending with different semantics* (e.g. promoting a name to well-known).

**New metric names need no version change**: any name matching the naming rules is accepted with the default value rules. To introduce a metric with a different range, give it a distinct name or unit; if you need a range check, add a per-name rule in a minor version. Never repurpose an existing name.

**Agent/server version mismatch** (no negotiation exists in v1; deliberately kept simple):

| Situation | Behaviour |
|---|---|
| agent version ∈ server's supported list | processed normally; server may be newer than the agent |
| agent newer than server (e.g. agent `1.1`, server `["1.0"]`) | `400 unsupported_schema_version` with `supported_versions`; agent drops and logs, does not retry. **Prevented by rollout order: upgrade the server first, agents afterwards.** |
| agent speaks a retired major | same response; operator must upgrade the agent |

Dropping on a version mismatch loses data during a mis-ordered rollout; agent-side downgrade-and-resend using `supported_versions` is possible later but out of scope.

`envelope_id` is a new required field but was added to `1.0` itself: nothing has been released or implemented against `1.0` yet, so no producer or consumer is broken. After the first implementation ships, a change like this would be a new MAJOR.

File naming when 1.1 first appears (e.g. `v1/envelope-1.1.schema.json` next to the existing file) is intentionally left undecided until then.

## Python validation approach (future backend, not implemented)

The JSON Schema stays the only place where contract rules live. Planned backend design:

1. The ingestion endpoint reads the raw body (not a Pydantic parameter, so FastAPI's built-in 422 format never leaks), parses it with a strict `parse_constant`, and checks `schema_version`.
2. It validates the parsed document with `jsonschema.Draft202012Validator` loaded from `contracts/telemetry/v1/envelope.schema.json`, and maps errors to the error shape above.
3. Semantic checks (the three listed under Timestamps) are small hand-written functions.
   Idempotency and durable acceptance (Envelope identity, `202`) live in the ingestion service, not in validation.
4. Pydantic models, if used, describe the *already-validated* data for internal code and the response bodies. They carry **no constraint of their own** (no ranges, patterns or validators duplicating the schema). Generating models from the schema is not viable because generators lose the `if/then` per-name rules.

**Approved decision:** the JSON Schema is the canonical, language-neutral contract; Python validates the raw parsed JSON against it; Pydantic may provide typed application objects after schema validation, and must not independently re-implement the schema's conditional rules (per-name `kind`/`unit`/label rules, percent range, counter sign). When the schema and a model disagree, the schema wins and the model is wrong. A repo test should run the shared fixtures through the real endpoint and compare with `expected-failures.json`.

Caveat for any Python regex handling: Python's `$` also matches before a trailing newline and `\d` matches non-ASCII digits. The schema avoids both (explicit `[0-9]`, plus a `not: {pattern: "\n"}` guard next to every anchored pattern), so do not "simplify" those.

## Verifying

```
pip install jsonschema
python -m unittest discover -s contracts/telemetry/tests -v
```

The suite checks: the schema is valid; every `valid/` example passes; every `invalid/` fixture fails with **exactly** the (JSON pointer, keyword) pairs declared in [`v1/examples/expected-failures.json`](v1/examples/expected-failures.json) (and no fixture lacks an entry); the timestamp pattern agrees with the real calendar for sampled years × months 0–13 × days 0–32 and enforces UTC-only/ASCII-only forms. Other-language implementations should reuse the fixtures and map their error output to the same expectations.

## Known limitations

- Semantic checks and everything under HTTP are specified only; nothing implements them yet.
- Open decisions: how long `envelope_id`s are remembered; how storage reconciles the same observation identity arriving in different envelopes.
- Semantic-check fixtures do not exist yet because the schema cannot express them; they belong with the backend implementation.
- Timestamp fractions are limited to microseconds; years before 1000 are rejected.
- Python `jsonschema` is the only validator exercised so far; the Go side (regex dialect RE2, error mapping) is untested.
- Counters above 2^53 lose integer precision.
