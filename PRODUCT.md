# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Vite + React + TypeScript single-page app (confirmed by the owner). It talks only to the FastAPI backend through its API; Clerk's React SDK provides sessions. Undecided and to be chosen as the first build step: chart library, component primitives, styling approach. No frontend code exists yet (verified: no package.json, no `.tsx` anywhere in the repository).

## Users

Primary: a small-team infrastructure engineer or on-call responder who opens the console daily and during incidents. They scan fast and want the answer to "what needs me now?" before anything else. Desktop is the design target; tablet and phone must remain usable for glancing and acknowledging, not for deep analysis. Workspaces and multiple members are planned but not first-release scope: the first release is a single workspace, with the shell designed so multi-workspace can be added without rework.

## Product Purpose

UptimeTrace monitors infrastructure, collects telemetry from agents, detects anomalies, estimates the risk of future failures, relates incidents to dependencies, and supports investigations whose conclusions are tied to evidence the backend actually returned. Success for the first release is a working, honest proof of concept: 4 to 5 monitored instances (three live hosts, one historical-replay host, one controlled synthetic host) shown accurately, with every number's provenance visible.

## Positioning

Predictive infrastructure intelligence rather than another metrics dashboard. Two claims a neighbouring product could not copy without doing the same work: (1) predicted risk is always a separate, labelled kind of information from confirmed incidents and carries its own maturity status; (2) every measurement states whether it is LIVE, SYNTHETIC or REPLAY, so a demonstration can never silently mix real and simulated data. The LLM never sets a risk score and never invents evidence.

## Operating Context

- Agents send authenticated telemetry to FastAPI (`POST /api/v1/telemetry`, bearer credentials scoped to an agent and host list, kind `live` / `synthetic` / `replay`). A minimal Go agent exists and reports from Windows; Linux collection is implemented but untested on a real host.
- Telemetry contract v1 is fixed: gauges `system.cpu.utilization`, `system.memory.utilization`, `system.disk.utilization{mount}` (percent) and cumulative counters `system.network.rx_bytes` / `tx_bytes{interface}` (bytes). Rates are derived by the backend later, never sent.
- Data lives in Supabase PostgreSQL; the browser never receives database or service-role credentials. Identity comes from Clerk (Google OAuth and email); FastAPI verifies sessions and enforces authorization.
- Intelligence components planned: XGBoost risk model, statistical / PyOD anomaly detection, a rules engine, NetworkX dependency graph, LLM investigations over structured evidence.

## Capabilities and Constraints

Real today (verified in the repository): authenticated envelope ingestion with scope validation; the Go agent; stored raw envelopes (JSONB). Not existing yet: any read/query API, hosts registry, alert rules, incidents, anomaly detection service, dependency discovery, log ingestion, model serving, Clerk integration, any frontend.

The risk model is experimental and was validated only on synthetic simulator data (validation PR-AUC about 0.02 against a 0.003 chance level on a seven-run subset); it must never be presented as accurate or production-ready. Dependency relationships may only be shown if discovery data exists; none does. SMD and NAB datasets are deferred until their files are accessible and metric mappings verified; no SMD anomaly label may be presented as an outage.

Undecided: charting library; whether alert evaluation runs in the API process or a worker; log and event ingestion design; dependency-discovery source.

## Brand Commitments

Name: UptimeTrace. Tagline: "Trace the signals. Stay ahead of downtime." The owner wants a distinctive identity of its own, inspired in depth and professionalism (not appearance) by Datadog and Dynatrace. Dark-first is a stated direction to explore, not a fixed commitment. No logo or brand assets exist yet.

## Evidence on Hand

Synthetic simulator datasets (180 simulated hosts, seed 0) and trained baseline models under `artifacts/` (synthetic only). Real telemetry from one Windows development laptop via the Go agent (collected in tests). No customer data, no real incidents, no testimonials, no benchmarks, no pricing: future work must not fabricate any of these. Any demonstration data in design previews must be labelled as preview data.

## Product Principles

1. Provenance before polish: every displayed measurement shows whether it is LIVE, SYNTHETIC or REPLAY, and nothing mixes them silently.
2. Prediction is not confirmation: predicted risk is visually and structurally distinct from detected incidents and carries its maturity status; the interface never implies accuracy that has not been demonstrated.
3. Evidence over assertion: investigations show the backend evidence they rest on; the AI explains, it does not decide or invent.
4. Show only what exists: no fake charts, no invented dependencies, no pages that exist only to fill the sidebar; absent capabilities appear as honest empty states with the next step.
5. Triage first: the landing view answers "what needs me now?" in seconds, and every deeper view is reachable from the thing that raised the question.

## Accessibility & Inclusion

Not specified by the owner. Working assumption, unconfirmed: WCAG 2.2 AA for contrast and keyboard operation, with status never conveyed by colour alone (health, severity and data source each need a text or shape cue).
