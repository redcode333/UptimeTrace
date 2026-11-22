# OpenTelemetry architecture audit and integration decision

Status: **investigation and recommendation. Nothing is implemented by this document.** Every finding cites the file and
function it was read from. Claims that could not be confirmed in code are marked as such.

## 1. Current telemetry pipeline

```
Go agent (agent/)            HTTPS POST /api/v1/telemetry           PostgreSQL                      consumers
collector -> processor  ->   Bearer token, JSON envelope v1.0  ->   telemetry_envelopes (JSONB)  ->   alerts, monitoring,
buffer -> exporter                                                   hosts, registry tables           investigations, ML
```

### 1.1 Agent

| Step | File and function | What it does |
|---|---|---|
| Collection | `agent/internal/collector/collector.go` (`Collector.Collect`, `gauge`, `counter`) | Gathers five well-known metrics: `system.cpu.utilization`, `system.memory.utilization`, `system.disk.utilization` (label `mount`), `system.network.rx_bytes` and `tx_bytes` (counters, label `interface`). Units and names are fixed by the contract. |
| Linux sources | `collector_linux.go` | Reads `/proc/stat`, `/proc/meminfo`, `/proc/mounts` plus `statfs`, and `/proc/net/dev`. Loopback is skipped. Windows and other systems have their own files. |
| Envelope | `agent/internal/processor/processor.go` (`Builder.Build`, `toMetric`, `Percent`) | Cuts observations into envelopes of at most 500 metrics. Each envelope gets a UUID once, so retries resend identical bytes. Percentages are clamped to 0–100 and rounded to two decimals. Counters are exact integers. |
| Buffering | `agent/internal/buffer/buffer.go` (`New`, `Push`, `Head`, `Remove`) | A bounded in-memory queue that drops the oldest entries when full and counts the drops. Nothing is persisted to disk, so a restart loses unsent envelopes. |
| Transmission | `agent/internal/exporter/exporter.go` (`Send`, `Run`, `Drain`, `Backoff`) | POST with `Authorization: Bearer <token>`. 202 or 200 is delivered. 429, 5xx and 408 retry with backoff and Retry-After. Other statuses drop. |
| Configuration | `agent/internal/config/config.go` (`Load`) | Flags with environment fallbacks: `UPTIMETRACE_TOKEN`, `UPTIMETRACE_INTERVAL`, `UPTIMETRACE_BATCH_MAX`, `UPTIMETRACE_BUFFER_MAX`. The agent ID, host ID, hostname and `--service` are supplied by configuration. |
| Dependencies | `agent/go.mod` | Go 1.22, **no third-party modules**. |

### 1.2 Envelope schema

Source of truth: `contracts/telemetry/v1/envelope.schema.json` (JSON Schema 2020-12, `schema_version` const `"1.0"`), with
fixtures in `contracts/telemetry/v1/examples/` and a Python contract test in `contracts/telemetry/tests/test_contract.py`.

```
{ schema_version "1.0", envelope_id (lowercase UUID), agent_id, host_id, hostname, timestamp (RFC 3339 UTC, Z),
  service (optional), metrics: [ { name, kind (gauge|counter), value, unit, timestamp, labels? } ] }
```

Unknown top-level fields are rejected (`unknown-top-level-field.json`). The contract is consumed by the Go agent and the
backend, and by the simulator's validator (`backend/simulator_v2/validate.py`).

### 1.3 Backend ingestion

| Step | File and function | Behaviour |
|---|---|---|
| Route | `backend/app/main.py`, `POST /api/v1/telemetry` (`ingest`) | Authenticates first, then reads the body with a size limit, checks content type, parses JSON, checks `schema_version`, validates the schema, checks **identity**, validates semantics, then stores. |
| Authentication | `backend/app/auth.py` (`CredentialStore`, `hash_token`, `check_scope`, `permits`) | A credential is a random bearer token stored as a SHA-256 hash, bound to **one `agent_id`** and an explicit list of `host_id`s, with a kind (live, synthetic or replay). |
| Identity check | `auth.py` `permits` (line ~155) | `envelope.agent_id == credential.agent_id` **and** `envelope.host_id in credential.host_ids`. A mismatch returns 403. |
| Semantics | `backend/app/ingest/semantic.py` (`validate_semantics`), `ingest/storage_limits.py` | Timestamp plausibility and NUL-character rejection. |
| Idempotency | `backend/app/ingest/service.py` (`store_envelope`) | `INSERT … ON CONFLICT (envelope_id) DO NOTHING`. A duplicate with the same content hash is accepted; a different hash is a conflict. |
| Alerts after commit | `ingest` → `app.alerts.service.evaluate_after_ingest` | Runs after the commit for the envelope's host. A periodic sweep (`sweep_all`) catches silent hosts. |

### 1.4 Storage

`backend/app/models.py`:

* `telemetry_envelopes` (`TelemetryEnvelope`): one row per envelope. Columns include `envelope_id` (unique), `agent_id`, `host_id`, `hostname`, `schema_version`, `envelope_timestamp`, `service`, `metric_count`, `content_hash`, `payload` (JSONB, the whole envelope), `received_at`. Index `ix_telemetry_envelopes_host_ts` on `(host_id, envelope_timestamp)`.
* **Metrics are not normalised.** They stay inside `payload->'metrics'` and are unwound at read time. `backend/app/monitoring/queries.py` uses `jsonb_array_elements` and a `ROW_NUMBER()` window over `(host, name, labels)`.
* `hosts`, `agent_credentials`, `services`, `service_hosts`, `alerts`, `alert_events`, `investigation_reports`, `audit_events`, `monitoring_settings`, `mcp_tokens`, and the grouping tables (`environments`, `regions`, `clusters`).

### 1.5 Consumers

| Consumer | File and function | Telemetry it uses |
|---|---|---|
| Monitoring API | `backend/app/monitoring/router.py` (`/api/v1/hosts`, `/hosts/{id}/metrics/latest`, `/hosts/{id}/metrics/series`), `monitoring/service.py` (`load_series`, `build_host`), `monitoring/health.py` | Latest and time-series of all five metrics, derived rates for counters. |
| Alerts | `backend/app/alerts/rules.py`, `alerts/service.py` | Threshold reasons from `monitoring/health.py` on the same metrics; `host_offline` from `last_seen`. |
| Investigations | `backend/app/investigation/service.py` (`build_view`, `_series_window`) | Reads the alert's own metric series around the trigger and recovery through `monitoring.service.load_series`. Evidence is the stored envelopes; there is no separate evidence store. |
| ML | `backend/ml/features.py` | Uses `cpu`, `mem`, `disk_root`, `disk_data` (percent gauges) and `rx`, `tx` (counters, turned into rates). An observation is usable at decision time `t` only if its envelope timestamp is `<= t`. |
| MCP | `backend/app/mcp/service.py`, `mcp/server.py` | Read-only host, metric and alert tools. |

## 2. Existing OpenTelemetry and observability audit

Classification: **Implemented** (works end to end), **Partial** (some code, not end to end), **Dependency only** (installed or
declared, not used), **Not implemented**.

| Finding | Evidence | Class |
|---|---|---|
| OpenTelemetry SDK in the Go agent | `agent/go.mod` has no requirements | Not implemented |
| OpenTelemetry SDK or API imported in backend code | No `opentelemetry` import under `backend/` | Not implemented |
| `opentelemetry-api` 1.45.2 pinned | `backend/requirements.lock` line ~1088, "via fastapi" | Dependency only |
| `opentelemetry-api` in the frontend | `frontend/package-lock.json` (transitive) | Dependency only |
| OTLP exporters or receivers | None in code or config | Not implemented |
| OTLP HTTP or gRPC endpoints | None; the only ingest route is `POST /api/v1/telemetry` (JSON) | Not implemented |
| OpenTelemetry Collector configuration | No collector config or compose file in the repository | Not implemented |
| Jaeger deployment or dependency | None; only docs and diagram icons (`frontend/src/lib/scene3d/catalogue.ts`) | Not implemented |
| Prometheus exporter or dependency | None; catalogue icon only | Not implemented |
| Trace IDs, span IDs, `traceparent` | None in the agent, the ingest path or the storage schema | Not implemented |
| Resource attributes and service identity | `service` is one optional string per envelope, set by the agent's `--service` flag. Mapped to UptimeTrace services through `service_hosts`. | Partial (no OTel resource model) |
| Docker Compose or deployment for observability | None in the repository | Not implemented |
| `get_service_traces` MCP tool | Named in `docs/MCP_INTEGRATION.md` ("M4 OpenTelemetry"); the name appears nowhere in `backend/`, `agent/` or `frontend/src` | Not implemented (documentation claims a tool that does not exist) |
| Application-level metrics, logs or distributed traces | No application instrumentation exists; the agent logs to stderr with `slog` (`agent/cmd/agent/main.go`), which is local output only | Not implemented |
| Status documents | `docs/IMPLEMENTATION_STATUS.md` line 25 lists "OpenTelemetry/Jaeger: Absent"; `docs/KNOWN_LIMITATIONS.md` agrees | Consistent with the code |

**Conclusion:** the repository has no working OpenTelemetry pipeline. The only current telemetry is the custom JSON envelope
for host metrics. The plan in `docs/BUILD_PLAN.md` (M4: demo FastAPI service, private OTLP Collector, Jaeger, linked spans)
is a plan, not existing code.

## 3. Architecture options

### Option A: keep the custom pipeline

**Fit with what exists:** complete. Contract-tested, idempotent, authenticated, with a frozen ML dependency on the same
fields.

**Limitations:**

* No standard for application telemetry: traces, service-to-service calls and application logs have no path into UptimeTrace.
* Every new signal needs a new contract version and custom code in the agent, the backend and the simulator.
* Interoperability: other OTel-instrumented systems cannot send to UptimeTrace without a translator.
* Storage: all metrics are JSONB inside one row per envelope, queried with `jsonb_array_elements`. This scales poorly as volume grows, whatever telemetry standard is chosen.

### Option B: replace the custom pipeline with OpenTelemetry

Agent uses the OTel Go SDK and OTLP; the Collector becomes the ingress; FastAPI and PostgreSQL would consume OTLP.

**Costs and risks:**

* Migration: the agent's collection and buffering would move to OTel metric instruments. The `system.*` names, units and label sets would need an exact mapping, and the contract's counter and percent rules would need to hold on the OTel side.
* Loss of guarantees: the current envelope identity check (token bound to `agent_id` and `host_id`), the content-hash idempotency and the 0–100 clamps are custom. They would have to be rebuilt as Collector processors or FastAPI middleware. Collector authentication is not enabled by default.
* History: the ~180 synthetic runs and any real stored envelopes are in the custom format. A replacement needs a parallel read path or a backfill.
* ML: the ML features assume the envelope timestamp and the observation sampling (60 s, as-of rule). OTel aggregation and export intervals change the sampling characteristics and would require a new feature version and a new, unfrozen evaluation.
* Benefit: standard tooling for application telemetry. That benefit does not depend on replacing host metrics.

**Verdict:** rejected. The cost falls on the working, tested host pipeline and the frozen ML dependency, and the benefit (application
telemetry) does not require it.

### Option C: hybrid (recommended)

Keep the Go agent and its envelope for host metrics. Add OpenTelemetry only where there is a product requirement: application
traces (and later application metrics and logs) from instrumented services.

## 4. The three signals

### 4.1 Metrics

* **Host metrics stay on the envelope.** The five metrics, their units and labels are already the contract; the ML features and alerts depend on them.
* **OTel metric instruments** are not needed for host metrics now. If they are added later for application metrics, use the OTel semantic-convention names and units (for example `system.cpu.utilization` is a ratio in the OTel convention, a percent in UptimeTrace's contract, so a mapping and unit conversion would be required). Do not mix the two naming schemes in one table without a mapping.
* **Duplicate collection:** if OTel host-metric receivers (for example the Collector's `hostmetrics` receiver) were ever enabled beside the Go agent, the same host would be measured twice. Don't enable both for the same host.
* **Cardinality:** labels are `mount` and `interface` today. Application metrics with request-level labels (user IDs, URLs) must be kept out of the metric label set.
* **Aggregation and retention:** the current 60 s samples are stored in full in JSONB. Retention is not defined in the repository for telemetry rows; this is an open decision.

### 4.2 Traces

* **Needed for:** application-level context for an alert (which service call slowed down), and for the MCP tool that the docs already promise.
* **Ingestion:** OTLP to the Collector. The Collector exports traces to Jaeger. UptimeTrace does not need to store spans itself for the first milestone; Jaeger's query API can serve the trace view.
* **Storage:** Jaeger's own backend (memory or Badger for development; Elasticsearch, Cassandra or a Jaeger-supported database for production). Do not store spans in the PostgreSQL telemetry tables.
* **Propagation:** W3C `traceparent` on HTTP calls between instrumented services. The demo service (M4) is the first place to test it.
* **Correlation:** a trace carries `service.name` and optional `host.name`/`host.id` resource attributes. The alert or investigation links to a trace only through those attributes, and only after the identity mapping in section 5 validates them.

### 4.3 Logs

* Not needed for the first milestone. No application log pipeline exists, the agent's `slog` output is local, and no requirement names logs.
* When needed: OTel log records with `trace_id` and `span_id` for correlation; storage in a log backend (for example Loki), not PostgreSQL, unless volume is small.
* **ML scope:** logs are excluded from the initial ML scope (no feature definition, no labels, no dataset).

The three signals do not need to share one storage backend. Host metrics: PostgreSQL (existing). Traces: Jaeger's backend. Logs (later): a log store.

## 5. Identity and correlation

### 5.1 What exists

* Agent identity: a token bound to `agent_id` and a list of `host_id`s. The envelope must match both (`auth.py` `permits`).
* Host registry: `hosts.workspace_id` is set when a host is registered. Telemetry rows have **no** workspace column.
* Reads: monitoring queries use the default workspace from the registry (`monitoring/router.py`, `default_workspace_id`).
* Services: `service` is the agent's configured string. Service entities are linked to hosts with `service_hosts`, assigned in Infrastructure, not inferred from telemetry.

**Gap:** telemetry is not bound to a workspace at ingestion. The authorization that protects a host's data is the agent token, and the read path assumes one default workspace. This is acceptable for the current single-workspace proof of concept, but it blocks multi-workspace operation regardless of OTel.

### 5.2 Mapping OTel resource attributes to UptimeTrace entities

| UptimeTrace entity | Source of truth | OTel attribute | Rule |
|---|---|---|---|
| Workspace | the authenticated credential's host registration (`hosts.workspace_id`) | none; **never** from telemetry | Derived server-side from the token. A `workspace.id` attribute in the payload is ignored or rejected. |
| Host | the credential's `host_ids` (checked by `permits`) | `host.id` (standard) with `host.name` | The attribute must be in the credential's host list. |
| Agent | the credential's `agent_id` | `uptimetrace.agent.id` (UptimeTrace-specific) | Must equal the credential's `agent_id`. |
| Service | `services` + `service_hosts` (assigned by a user) | `service.name` (standard) | Mapped only through the existing assignment; an unassigned `service.name` is kept as a raw label, not an entity. |
| Environment, region, cluster | `environments`, `regions`, `clusters` and the host's `cluster_id` | none in OTel | Never inferred from telemetry. Set only in Infrastructure. |
| Deployment identity | `service.version`, `deployment.environment` (standard) | standard attributes | Recorded as labels; not used for authorization. |

Standard OTel attributes: `service.name`, `service.version`, `service.instance.id`, `host.id`, `host.name`, `deployment.environment`.
UptimeTrace-specific attributes: `uptimetrace.workspace` (not trusted; see above), `uptimetrace.agent.id`, `uptimetrace.cluster`. Prefix every custom attribute with `uptimetrace.` to avoid collisions.

### 5.3 Multi-workspace rules

1. The workspace of a telemetry record comes from the authenticated credential, never from the payload.
2. A telemetry record whose host is not registered to the credential's workspace is rejected, not attributed.
3. Trace queries are filtered by the caller's workspace, and the filter is applied by the server, not by the trace backend's query alone.
4. Before multi-workspace operation, add `workspace_id` to `telemetry_envelopes` and to the trace index, and enforce it on every read.

## 6. Authentication and security

* **Agent to backend:** unchanged bearer tokens. Tokens are stored as SHA-256 hashes and compared with `hmac.compare_digest`. Keep this for host metrics.
* **Application to Collector (OTLP):** the Collector must not accept unauthenticated traffic from anywhere but the private network. Use OTLP over TLS with a per-service token (Collector `bearertokenauth` extension) or mTLS. Bind receivers to a private interface.
* **Collector to Jaeger:** private network only. Jaeger's UI and query API need authentication when exposed; the UptimeTrace frontend should query through the backend, not expose Jaeger directly.
* **No secrets in spans:** span attributes and events must not contain tokens, connection strings or request bodies. The Collector can drop attributes with a processor (`attributes` or `redaction`) as a second line of defence.
* **Trust boundary:** every attribute from an OTLP sender is untrusted input. Identity attributes are validated against the sender's credential, as in section 5.
* **Sizes and rates:** the Collector's `memory_limiter` and batch limits protect the backend; the existing 1 MiB body limit on `/api/v1/telemetry` (`MAX_BODY_BYTES` in `backend/app/config.py`) stays.

## 7. Storage and retention

| Data | Store | Retention |
|---|---|---|
| Host envelopes (metrics) | PostgreSQL `telemetry_envelopes` (existing) | Not defined in the repository. Decision needed: the row count grows with agents × 60 s; a partition or rollup policy is required before scale. |
| Traces | Jaeger backend (memory for development; a persistent backend in production) | Set in Jaeger's configuration (days, not kept in PostgreSQL). |
| Logs (later) | A log store | Decide when logs are in scope. |
| Synthetic datasets | `artifacts/` (existing, frozen) | Unchanged. |

Do not put spans into PostgreSQL. Do not move host metrics to a time-series database without a reason that the current JSONB
storage cannot meet; that is a separate decision with its own migration.

## 8. Data flow (recommended)

```
Host:       Go agent (stdlib, envelope v1.0) ──POST /api/v1/telemetry (bearer)──► FastAPI ─► PostgreSQL telemetry_envelopes
                                                                                    │          ├─► alerts / monitoring / investigations / ML (unchanged)
Application: instrumented service (OTel SDK) ──OTLP (token, private)──► OTel Collector ─► Jaeger (traces)
             (M4 demo first)                                              │
                                                                          └─(later, optional) logs → log store
Trace view:  frontend ─► FastAPI ─► Jaeger query API (workspace-filtered on the server)
```

The Collector does not replace FastAPI or PostgreSQL. In the recommended design it only receives OTLP and exports traces.

## 9. Metrics, traces and logs routing

| Signal | Source | Route | Store | Consumers |
|---|---|---|---|---|
| Host metrics | Go agent | envelope → FastAPI | PostgreSQL | alerts, monitoring, investigations, ML (unchanged) |
| Application traces | OTel SDK in a service | OTLP → Collector → Jaeger | Jaeger backend | trace view, MCP trace tool (to be built) |
| Application metrics (later) | OTel SDK | OTLP → Collector → FastAPI adapter (new) | PostgreSQL (new table, with mapping) | alerts only after a mapping and a unit decision |
| Logs (later) | OTel SDK or agent | OTLP → Collector → log store | log store | investigation evidence (later) |

Do not send host metrics through the Collector into the same store as the envelope path. That would create two records of the same host.

## 10. Migration from the current pipeline

Nothing is migrated in the recommended option. The envelope path stays. OpenTelemetry is added beside it.

If Option B is ever chosen later, the migration would need: a parallel run (agent emits both, backend compares), a metric-by-metric
mapping with unit tests, a stored-data read path for old envelopes, and a new feature version for the ML pipeline with its own
protocol. That is a separate decision.

## 11. Local development and production

| Environment | Components |
|---|---|
| Local development | FastAPI, PostgreSQL, Go agent (unchanged). For traces only: OTel Collector and Jaeger in a local compose file, with the demo service. |
| Production | FastAPI and PostgreSQL as now; agents as now; Collector and Jaeger on a private network with TLS and tokens; Jaeger storage backend chosen with its own retention. |

No compose file or deployment definition exists in the repository today. Adding one is part of milestone M4 below, not this decision.

## 12. Dependencies: what is actually needed

| Component | Needed now? | Reason |
|---|---|---|
| OpenTelemetry SDK (Go) for the agent | **No** | Host metrics stay on the envelope. Revisit only for Option B. |
| OpenTelemetry SDK (Python) for FastAPI | **Only in M4** | Needed for the demo service's traces and for the backend's own spans later. |
| OTLP | **Only in M4** | The transport between instrumented services and the Collector. |
| OpenTelemetry Collector | **Only in M4** | Needed to route OTLP to Jaeger and to enforce authentication at the boundary. A direct SDK-to-Jaeger export would skip that control. |
| Jaeger | **Only in M4** | Trace storage and the trace UI. |
| Prometheus | **No** | No requirement names it; host metrics are served from PostgreSQL. Revisit only if application metrics need a pull-based scrape store. |
| Additional storage (time-series DB, log store) | **No** | Not justified by any requirement yet. |

Avoid adding `opentelemetry-api` to the backend's direct dependencies until M4 needs it. It is already in the lock file through FastAPI.

## 13. Implementation sequence

Each milestone is small and leaves the envelope path unchanged.

**M-A: documentation and contract decisions (no code).** Objective: approve the mapping in section 5 and the workspace rule. Affected: this document, `docs/MCP_INTEGRATION.md` (remove or mark the `get_service_traces` claim as planned), `docs/IMPLEMENTATION_STATUS.md`. New components: none. Exit: decisions recorded.

**M-B: workspace binding for telemetry (prerequisite for M6 workspaces; the BUILD_PLAN says every new table carries `workspace_id`, which `telemetry_envelopes` does not).** Objective: store `workspace_id` on each envelope row, derived from the credential, and filter reads by it. Affected: `backend/app/ingest/service.py`, `backend/app/models.py` (migration), `backend/app/monitoring/queries.py`, `backend/app/auth.py`. New components: one migration. Exit: tests showing a token for workspace A cannot read workspace B's hosts.

**M4-1: local trace path (BUILD_PLAN M4).** Objective: a demo FastAPI service with the OTel Python SDK exporting OTLP to a local Collector and to Jaeger, with no secrets in spans. Affected: none in production code; a new `demo/` folder. New components: OTel SDK (Python), Collector config, a local compose file, Jaeger. Exit: a trace visible in Jaeger with a `traceparent` propagated across two services.

**M4-2: Collector authentication and redaction.** Objective: token authentication on the OTLP receiver and an attribute redaction processor, tested with a rejected unauthenticated request and a redacted secret. Affected: Collector config only. Exit: tests and a documented rejection.

**M4-3: trace query through the backend.** Objective: a backend endpoint that queries Jaeger, filtered by workspace and by the service mapping of section 5, and the MCP `get_service_traces` tool implemented against it. Affected: a new `backend/app/traces/` package, `backend/app/mcp/server.py`. New components: a Jaeger query client. Exit: workspace-isolation tests.

**M4-4 (optional, only if required): application metrics.** Objective: OTel metric instruments mapped to a separate table with explicit units and cardinality limits. Affected: new table and adapter. Decision required first (section 4.1).

**Later, not scheduled: logs, and any change to host metrics.** Logs need their own requirement. Host metrics stay on the envelope unless Option B is approved in a separate decision.

## 14. ML and synthetic data

* **Current features and their dependency:** `cpu`, `mem`, `disk_root`, `disk_data`, `rx`, `tx` (from `backend/ml/features.py`). They depend on the envelope fields, the 60 s grid and the as-of rule. Nothing in the recommended option changes them.
* **OTel metrics** would map to these features only through an explicit unit and aggregation mapping, with a new feature version. Aggregation by the Collector would change the sampling characteristics, so OTel host metrics are not a drop-in replacement.
* **Traces** could add application-level context to a model later (for example, a downstream dependency's latency). That is a new feature family, with its own as-of rule and its own labels. Not in the initial ML scope.
* **Logs** are excluded from the initial ML scope.
* **Simulator:** `backend/simulator_v2` produces parquet observations that follow the envelope contract. It does not emit OTLP. If traces are ever used by a model, the simulator would need to generate application spans; that is a new generator, not a change to the existing one.
* **Frozen protocol and datasets:** unchanged. Nothing in this decision changes a dataset, label, split, feature definition or evaluation protocol. The protocol document itself is "not frozen" per its own status line, which is a separate issue for the ML owners.

## 15. Findings to resolve

1. `docs/MCP_INTEGRATION.md` lists `get_service_traces` as an M4 OpenTelemetry tool. It does not exist in code. Mark it planned.
2. Telemetry has no workspace binding, although `docs/BUILD_PLAN.md` says new tables carry `workspace_id` from M1. Multi-workspace operation depends on M-B.
3. Host telemetry retention is undefined in the repository.
4. `opentelemetry-api` appears in the backend lock file via FastAPI. It is harmless but should not be mistaken for an integration.
5. The agent has no offline persistence: a restart loses buffered envelopes. This matters more than OTel to data completeness.

## 16. Recommendation

Adopt Option C (hybrid). Keep the Go agent and its envelope for host metrics: they are working, contract-tested, authenticated and
depended on by the ML pipeline. Add OpenTelemetry for application traces through a private Collector and Jaeger, starting with the
M4 demo milestone. Do not add OTel metrics or logs yet. Do not add Prometheus or a new storage system without a product requirement.
Bind telemetry to workspaces (M-B) before any multi-workspace claim; the BUILD_PLAN already requires this before M6.

This needs approval before implementation: the mapping in section 5, the workspace rule in section 5.3, and the M4 scope.
