# UptimeTrace: product definition, dashboard architecture and design direction

Status: proposal for review. Nothing here is built. Visual previews: https://claude.ai/artifact/DVpxnmu5wV8xwJkXPgJf9y (private canvas, 8 boards, all marked PREVIEW DATA). Product truth: `PRODUCT.md`. Direction contract: `.impeccable/surfaces/design-previews.md`.

## 0. The questions, answered

- **What are we building?** A monitoring console that also estimates near-term failure risk, where every number says whether it is LIVE, SYNTHETIC or REPLAY and every prediction is labelled as a prediction.
- **Who uses it?** A small-team infrastructure or on-call engineer, on desktop, daily and during incidents.
- **What after login?** Overview (Command Center): a bulletin of what is confirmed now, what the model expects next (experimental), and what is silent.
- **What is distinctive?** The NOW line: measured history on the left, dashed amber model outlook on the right. Predicted and confirmed never share a number, a flag or a colour.
- **Implementable today?** Only against new read APIs (section 10). The backend has ingestion and nothing to read from.

## 1. Current inventory (verified in the repository)

| Area | Exists | Does not exist |
|---|---|---|
| Frontend | Nothing. Node 24.11.1 and npm 11.6.2 installed. | Any `package.json`, `.tsx`, build config |
| API | `GET /health`, `POST /api/v1/telemetry` (scoped bearer credentials, 401/403/schema errors) | Any read or query endpoint, host registry, auth for users, alert/incident/risk endpoints |
| Data | `telemetry_envelopes` (raw JSONB, idempotent insert); Alembic 0001/0002 | Normalised metric tables, hosts table, rules, incidents |
| Collector | Go agent (Windows verified; Linux parsers implemented, untested live) | Python collector, installers |
| ML | Baseline rule/logreg/xgboost on synthetic data (experimental), simulator and datasets in `artifacts/` | Real-data validation, serving, feature pipeline in backend |
| Identity | Nothing (Clerk planned) | Clerk integration, membership, roles |
| Design | `PRODUCT.md`, direction contract, 8-board design canvas | `DESIGN.md` (written at build finish, not now) |

## 2. Recommended product architecture

- **SPA**: Vite + React + TypeScript, talking only to FastAPI. Clerk React SDK for sessions; the session token goes to FastAPI as a bearer; FastAPI verifies and authorises. No database or service-role key ever reaches the browser.
- **Read path**: new FastAPI query routes over a normalised metrics table fed from `telemetry_envelopes`. Rates derived from counters server-side (reset-safe).
- **Provenance** is a column on every metric row (from the credential kind), so LIVE / SYNTHETIC / REPLAY is a data property, not a UI guess.
- **State vs prediction** are separate resources: `host state` (rules, silence) and `risk score` (model). The UI never derives one from the other.
- **Libraries** (decided at build start, not now): one chart library supporting zoom and brush, TanStack Query for fetching, React Router, Clerk. No component framework unless needed.

## 3. Navigation (changes explained)

| Your proposal | Proposed | Why |
|---|---|---|
| A Overview / Command Center | **Overview** (page title "Command Center") | Short label, same page |
| B Infrastructure: Hosts, Metrics Explorer, Service Dependency Map | **Infrastructure**: Hosts, Metrics Explorer, Dependencies (visible, gated "needs discovery") | The map appears only when discovery data exists; until then it is an honest not-available state, not a fake graph |
| C Observability: Anomalies, Alerts & Rules, Incidents, Logs & Events | **Detect & respond**: Anomalies, Alerts & Rules, Incidents, Events | "Observability" collides with Infrastructure's metrics; these four form a pipeline (signal, rule, incident). "Logs & Events" becomes **Events** because logs are not ingested |
| D Intelligence: Predictive Risk Explorer, AI Investigation Workspace | **Intelligence**: Risk Outlook (EXP tag), Investigations | Shorter, and the EXP tag is always visible |
| E Settings (5 items) | **Settings**: Agents, Workspace & members (integrations and notification prefs as tabs inside, added when real) | Avoids empty pages; agent onboarding also reachable from Hosts "Add host" |

Anomalies, Alerts & Rules, Incidents, Events, Dependencies, Investigations and Settings pages are not drawn in full; the canvas shows their honest placeholder states. Host detail is a page reached from Hosts, not a nav item.

## 4. Page-by-page layouts (see canvas boards)

1. **Command Center** (Main): page title, one-line count strip (hosts, critical, warning, outlook, offline, open incidents), observed-plus-outlook chart over a "hosts needing attention" table, right column Bulletin (warnings in force, outlook, no report), recent events, experimental notice. No hero-number cards.
2. **Hosts**: filter row (find, state, source, OS, agent), inventory table (hostname, host ID, source, OS, agent status, last seen, CPU/mem/disk, state with optional OUTLOOK tag), pagination, Add host.
3. **Host detail**: breadcrumb, state + source in the title, tabs (Overview, Metrics, Events, Alerts, Incidents, History), four charts with rule lines, events strip, metadata, risk outlook, open incidents.
4. **Metrics Explorer**: query bar (metric, hosts, aggregation, step, range incl. custom, compare), large chart with zoom brush and tooltip listing every series with source, series legend with line styles, derived-rate note.
5. **Risk Outlook**: experimental banner, hosts by estimated risk (meter with threshold tick, contributing signals, confirmed state alongside), risk over time with NOW split, model card (trained on synthetic data only, no real-data check), "how to read this".
6. **Investigations**: facet panel of evidence types, query chips, evidence-over-time histogram, evidence table with stable IDs, and an AI panel whose steps each cite evidence IDs and whose conclusion lists what is not determined. It restates stored evidence only; it never sets scores or state.
7. **States**: empty, loading, error, disconnected agent, no data in range, forbidden, not available yet (Dependencies, Events), replay ended.
7. **Sign in / sign up / reset**: Clerk-hosted fields themed to match, error state, flow strip.
8. **Design system**: tokens, type, badges, flags, controls, chart and table rules, shell.

Not designed in full (deliberately): Anomalies, Alerts & Rules, Incidents, Investigations, Settings. They follow the table + detail pattern once their backends exist.

## 5. Shared component inventory

App shell (sidebar, collapsible; top bar; breadcrumbs; page header with actions); command search; global time-range and source filters (URL-backed); notifications menu; user menu; Panel; Count strip; Data table (sortable, filter chips, pagination, sticky header, row link); State flag; Source badge; EXP tag; Bulletin item; Chart frame (axes, NOW marker, outlook shading, thresholds, tooltip, legend, brush); Series legend; Metadata list; Tabs; Empty / Loading / Error / Disconnected / Not-available state blocks; Banner (experimental, stale-data); Form field set; Button, input, select.

## 6. Design tokens

- **Colour**: ground `#0b1722`, panel `#102231`, raised `#15293b`, seam `#26435c`, overlay seam `#3a5f7e`, ink `#e6eef4`, secondary `#a3b8c8` (7.9:1 on panel), labels `#8fa8bb` (6.6:1), outlook / experimental / primary action `#f2b64a`, confirmed warning and critical `#ff7a45`, normal `#4fd1a5`, focus `#62a8ff`. Amber means "prediction or experiment" except on the single primary button.
- **Type**: Public Sans (UI) and Spline Sans Mono (figures, IDs, timestamps). Scale 24/600, 20/600, 14/400, 13/400, 12/600 tracked caps, mono 13/500. Density follows Datadog and Dynatrace: 13px base, 12px tables, 11px chart axes, legends and source tags; nothing smaller. Widgets are title-barred panels in a 12-column grid; KPI tiles carry a filled area sparkline and take a solid state colour only when critical or warning; a hex host map shows fleet state; forecast is a shaded band right of NOW.
- **Spacing** 4, 8, 12, 16, 24, 32. **Radii** 4 controls, 0 panels. **Borders** 1px seams only. **Shadows** none; no gradients or blur.

## 7. Chart and table conventions

Charts: measured solid 2px; outlook dashed amber right of a shaded NOW line; comparison dotted at 70%; replay fine dash; percent axes fixed 0 to 100; gaps break the line; thresholds dashed orange with label; tooltip lists every series with source and exact value; at most 5 colour-coded series. Tables: text left, numbers right in mono; 40px rows; missing value is an en dash, never 0; relative age with exact time on hover; default sort by state then name; 25 per page; wide tables scroll inside their panel.

## 8. Auth and onboarding journeys

Sign in (Google or email) to Clerk session to redirect to the requested page or Overview to API calls with the session token to workspace-membership check (403 state if not a member). Sign up, forgot password and sign out are Clerk flows themed to match; UptimeTrace stores no passwords. Onboarding with zero hosts: Hosts shows the empty state with "Create agent credential" and the agent command; the host appears after its first report. Not built: Clerk integration, membership model, token-verification route.

## 9. Empty, loading, error and disconnected states

Drawn on the States board. Rules: skeleton keeps final column widths and gives way to the error after 10 s; error keeps the last data dimmed with its age; offline after 3 missed intervals with the line stopped, not interpolated; REPLAY ended is not offline; unavailable features state what unlocks them.

## 10. Real now vs future

Classes: 1 available now, 2 buildable with existing backend, 3 needs new backend/API, 4 needs future ML or telemetry.

| Capability | Class | Note |
|---|---|---|
| Ingest telemetry, scoped credentials, Go agent on Windows | 1 | Done |
| App shell, sign-in screens, design system, states | 2 | Pure frontend; Clerk keys needed for real sign-in |
| Hosts list, host detail, latest values, time series, Metrics Explorer | 3 | Needs read API and a normalised metrics store; derives rates from counters |
| Command Center health counts, attention table, recent events | 3 | Needs host registry, state rules, and an events table |
| Alerts and rules, Incidents | 3 | Rules engine, incident lifecycle, notifications |
| Anomalies | 3 then 4 | Statistical detectors (PyOD, z-score) over stored metrics |
| Agent management, workspace and members | 3 | Credential admin API, Clerk membership |
| Risk Outlook | 4 | Needs feature pipeline in the backend, serving, and real-data validation before any claim |
| Investigations | 4 | Needs evidence store and LLM over structured evidence |
| Dependencies | 4 | Needs a discovery source |
| Events from logs | 4 | Needs a log ingestion design; system-generated events are class 3 |

## 11. Phased roadmap (each phase ends with a review)

1. **Read API v0** (class 3): host registry, latest values, time series with rate derivation, health state by rule; then run the throwaway Postgres to prove ingestion end to end (pending your approval).
2. **Frontend v0**: Vite scaffold, shell, Clerk sign-in, Hosts, Host detail, Metrics Explorer on real data, live from the Windows agent.
3. **Three live agents + one synthetic + one replay** instance, Command Center on real counts.
4. **Rules and alerts**, events, Incidents.
5. **Anomalies**, then **Risk Outlook** after the feature pipeline and a real-data check.
6. **Investigations** and **Dependencies** when evidence and discovery data exist.

## 12. Open decisions for you

Chart library; light theme (not designed); alert evaluation in-process vs a worker; whether Hosts shows OUTLOOK by default; sidebar labels (Detect & respond vs Observability).
