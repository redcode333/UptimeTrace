# UptimeTrace: frontend architecture (baseline)

Status: **baseline for the frontend build, approved 2026-11-19 with the decisions in section 3.** Revision 3 (2026-11-19) records the warning-colour decision (D-13), the code-validated corrections in section 2, the Command Center implementation specification in sections 6.9 to 6.12, and the dependency sequence and milestone in section 10. This revision changed documentation only: no application code, route, API, schema, migration, data, design PDF or visual preview was changed. The provenance migration and the Command Center implementation have not started.
Authority: `docs/IMPLEMENTATION_STATUS.md` and the code. `docs/KNOWN_LIMITATIONS.md` and `docs/INVESTIGATION.md` agree with this document. Where the older product documents disagree, this document and the code win (section 2).
The design reference is `UptimeTrace Console Design.pdf` (`D:\Downloads`), used for visual direction only. Its sample data is not product data.

Status vocabulary:

| Status | Meaning |
|---|---|
| **Implemented** | Works in code and is covered by tests. Browser verification is stated separately. |
| **Frontend work** | Backend data exists; the UI is missing or incomplete. |
| **Backend work** | The product need is valid; the API or stored data does not exist. |
| **Deferred** | Needs a capability that does not exist, or an owner decision, before any work starts. |

---

## 1. The product as it exists today

### 1.1 Backend and data

| Area | Status | What is true (evidence) | Limits |
|---|---|---|---|
| Host registry and credentials | Implemented | `POST/GET /api/v1/registry/hosts`; credentials issued (shown once), listed, revoked; revocation takes effect on the next request (`test_registry`, E2E 2, 13) | Single workspace |
| Host kind at registration | Implemented | `hosts.kind` (`live`, `synthetic`, `replay`) is set by the operator and checked against host-id rules at registration (`registry/service.py`, `auth.check_scope`) | The read path does not use it (see 1.2) |
| Telemetry ingestion | Implemented | `POST /api/v1/telemetry`, bearer credential bound to agent and host; rows persisted as JSONB (`telemetry_envelopes`) | No provenance column on telemetry rows |
| Telemetry contract v1 | Implemented, frozen | Gauges CPU, memory, disk (`mount`); counters network rx/tx (`interface`). Five metrics only. The agent sends no OS, version, interval, disk I/O or process data | See section 9 (v2) |
| Host read API | Implemented | `GET /api/v1/hosts` (search, source, health, facets, paging up to 200, sort by last seen or hostname); `GET /api/v1/hosts/{id}`; `…/metrics/latest`; `…/metrics/series` (one metric per request; avg, min, max, last; counters returned as rates; gaps null; at most 1,500 points) | No sort by state; no offset beyond the page; no nearest-data time for empty ranges |
| Health states | Implemented | healthy, warning, critical, stale, offline, unknown; missing data is never healthy (`test_health_states`) | Thresholds per workspace, via API only |
| Reporting states | Implemented | reporting, stale, silent (offline), replay | — |
| Thresholds and freshness settings | Implemented via API | `GET/PUT /api/v1/settings/monitoring` with a version check; defaults 85/95 % CPU and memory, 90/95 % disk, stale 90 s, offline 300 s | **No console UI** |
| Threshold alerts and host-offline alerts | Implemented | Deduplicated by a database constraint; escalation; system recovery; acknowledge; manual resolve; lifecycle events (`test_alerts`; E2E 5–10) | No notifications; no routing; no grouping |
| Alert list and detail | Implemented | `GET /api/v1/alerts` (status, host, `limit` up to 200 with default 100, `total` exact, newest first by `opened_at`); `GET /api/v1/alerts/{id}` (with events); acknowledge and resolve | **No offset** on the list; no source field on alerts; no workspace-wide event feed |
| Alert trigger and recovery evidence | Implemented (migration 0007) | Trigger value, threshold, comparison, sample time written at open; recovery value and sample time written at system recovery; legacy rows show "unavailable" (`test_investigation`) | Sample times exist only from migration 0007 onward |
| Investigation view | Implemented | `GET /api/v1/alerts/{id}/investigation` (window 5–120 min, related alerts paged, bounded telemetry windows, lifecycle, host now) | See section 8 |
| Investigation record | Implemented | `GET`/`POST /api/v1/alerts/{id}/investigation/report`; one immutable record per alert; SHA-256 digest; re-derivation check on every read; the application role cannot update or delete records | No index endpoint |
| MCP | Implemented (opt-in) | Three read-only tools; token endpoints `GET/POST/DELETE /api/v1/registry/mcp-tokens` (corrected: the earlier path `/api/v1/mcp/tokens` was wrong) | Console page at `/settings/integrations` (see status doc); static bearer tokens; not for shared deployment (`KNOWN_LIMITATIONS`) |
| Current user | Implemented | `GET /api/v1/me` | — |
| Audit | Partly | `audit_events` written for registry changes | **No read endpoint** |

### 1.2 Source provenance today

Source (LIVE, SYNTHETIC, REPLAY) is **derived at read time from the host-id prefix** (`monitoring/sources.py`, `auth.py`: `sim<digits>-` is synthetic, `replay-` is replay, anything else is live). The registry stores a `kind` that is checked against the same prefix rule at registration, but the read API ignores the stored kind. Telemetry rows carry no provenance. Hosts that report through the legacy `credentials.json` path have no registry row, so they have no stored kind at all. The approved rule in section 3 (D-1) replaces this derivation.

### 1.3 Frontend today

| Page | Route | Status | What it renders |
|---|---|---|---|
| Command Center | `/` (nav label "Overview") | Implemented, unit-tested, not browser-verified | Health count strip; data-sources panel; health distribution bar; CPU, memory and disk utilisation histograms; hosts-needing-attention table (10 rows, link to Hosts); "Recent infrastructure activity" shown as not available |
| Hosts | `/hosts` | Implemented | Search, health chips, source scope, facets, table of 25 per page, pagination |
| Host detail | `/hosts/:hostId` | Implemented | Identity, health, current observations, history charts. **No alerts section. No tabs.** |
| Metrics Explorer | `/metrics` | Implemented for one host and one metric | Host select, metric select, aggregation, range, chart |
| Alerts | `/alerts` | Implemented | Status filter (active, resolved, all); table with acknowledge, resolve, timeline, Investigate |
| Investigation | `/alerts/:alertId` | Implemented; **browser-unverified** | Summary, trigger, recovery, telemetry windows, lifecycle, related alerts, host now, record |
| Agents | `/settings/agents` | Implemented | Register host; hosts table; issue credential (token shown once); install snippet; revoke |
| Not available | Dependencies, Anomalies, Incidents, Events, Risk Outlook, Investigations index, Workspace & members | Placeholder pages with a stated reason | Nothing is drawn |

Shell: sidebar (216 px), top bar with breadcrumb, a **disabled** search control ("not available yet"), the data-source scope toggle (URL-backed), time range (URL-backed), a **disabled** Notifications control, and the user menu. **No active-alert count in the top bar.**

Auth: Clerk sign-in and sign-up pages exist and Clerk verification code exists on the backend. Both were exercised only with local test keys. **No real Clerk tenant has been used.** Access is a single-workspace allowlist, not membership. A 403 shows generic "No access" error copy; there is no dedicated page with sign-out.

Tests: frontend 88 passing across 13 files (`npx vitest run`), type check clean (`npx tsc --noEmit -p .`).

### 1.4 Verified end-to-end workflows

| Workflow | Result |
|---|---|
| 34-check agent-to-database-to-alerts workflow (`scripts/e2e/run_workflow.py`) | PASS, 34/34 (real Go agent, real API, disposable PostgreSQL) |
| Alert → investigation workflow (`scripts/e2e/investigation_workflow.py`) | PASS, 30/30 (signed ingestion, real clock; no Go agent in this script) |
| Backend affected set | 156 passed |
| Go agent | 6/6 packages ok |

**Browser verification of any console page is outstanding.**

---

## 2. Corrections to earlier drafts

Each item was checked against the code.

| # | Earlier claim | Correct statement |
|---|---|---|
| C1 | The Command Center has no health distribution bar. | It is drawn today (`OverviewPage.tsx`, "Host health distribution"). |
| C2 | The Command Center's activity panel needed a new backend endpoint before it could exist. | A v1 version is derived from alert list fields (section 6.2, CC-6), limited to alert transitions. |
| C3 | Command Center panels included a fleet CPU time series, a host map, top consumers and an insight bulletin. | The host map and top consumers are removed by decision. The CPU time series is not in the approved hierarchy. |
| C4 | The install command used `-token-file`. | The Agents page shows `UPTIMETRACE_URL=… UPTIMETRACE_TOKEN=<token> agent -host-id … -agent-id …`. |
| C5 | Hosts had an "Add host" button. | It does not. Registration is on the Agents page. |
| C6 | Investigation access returned a 403 page with sign-out. | 403 shows generic "No access" copy through the shared error component. |
| C7 | The top bar showed search and an alert count. | Search and Notifications are disabled controls. There is no alert count. |
| C8 | Provenance was described as a proposal, with no mention of the existing registry `kind`. | The registry stores `kind` and validates it at registration; the read path ignores it (section 1.2). |
| C9 | Settings items were "Alert policy & thresholds", "Workspace & members" and "Integrations". | Approved names: **Alert Policies**, **Integrations**, **Workspace**. Members are deferred. |
| C10 | Investigation was described as needing a future evidence store and a language model, with the nav entry "not built". | Investigation detail is implemented with no language model. Only the index page is missing. |
| C11 | A roadmap of phases began with a read API and frontend scaffolding. | Obsolete. Replaced by the next steps in section 10. |
| C12 | The critical badge uses a solid square. | The code draws critical as a diamond (`◆`, `components/ui/Badges.tsx`, `HEALTH_SHAPE`). The approved glyph is a solid square (D-3). Change required at implementation. |
| C13 | The warning colour could be chosen without checking other uses of violet. | The SYNTHETIC source badge is drawn in violet (`.src-synthetic` uses `--s4`, `#a78bfa`), and the chart's third series uses the same violet. The warning violet must be separated from both (section 11, Q10). |
| C14 | The utilisation histogram colours follow the health thresholds. | They do not. Bars are coloured by fixed cut-offs (lower bound 80 gives warning, nothing gives critical), so the 80–100 band is always warning-coloured. Must follow the configured per-metric thresholds or stay neutral (section 6.10, CC-7). |
| C15 | The "LIVE only" line can always show LIVE hosts. | The design rule is that scope filters every panel. CC-2 respects scope and shows "LIVE hidden by scope" when LIVE is not selected (section 6.10, CC-2). |
| C16 | Alerts can be filtered from another page with a URL parameter. | The alerts page keeps its status filter in component state, not the URL. "All active alerts" links to `/alerts`, which defaults to active and works. Other filtered links need a frontend change first. |
| C17 | The alert list can return up to 200 rows. | The default limit is 100. The frontend `api.alerts` call has no `limit` argument (`api/client.ts`), so it always requests 100. The Command Center needs a client change. |
| C18 | The host facets are exact. | Confirmed: the health facet respects the source scope; the source facet respects the health filter; both are computed over the whole fleet before paging (`monitoring/router.py`). Partial coverage applies only to item lists, not facets. |

### 2.1 Stale statements in other documents (approved for correction, not yet made)

These documents are outside this architecture file. The owner approved correcting them in a separate documentation-only change (section 11, Q7). Scope and historical decisions are not to be rewritten.

| Document | Stale statements to correct |
|---|---|
| `PRODUCT.md` | "No frontend code exists yet" and "No frontend." The "Real today" list omits the host registry, alerts, alert trigger and recovery evidence, investigation view and record, and the console. Its "Undecided" list still names the chart library and the alert-evaluation location; a chart component (`frontend/src/components/ui/TimeSeriesChart.tsx`) is in use, and alert evaluation runs in the API process (`evaluate_after_ingest` and the periodic sweep in `app/main.py`). |
| `docs/design/PRODUCT_DEFINITION.md` | Section 1 inventory ("Frontend: nothing"; "API: no read endpoint"; "Identity: nothing"). Section 6 palette, which differs from the implemented tokens (`frontend/src/styles/tokens.css`). The roadmap in section 11 (phases starting with the read API and scaffolding). |

---

## 3. Approved decisions

| # | Decision (approved 2026-11-19) | Consequence for the build |
|---|---|---|
| D-1 | **Provenance.** A registry host whose `kind` was explicitly set by an operator is trusted, and its stored kind is used. Legacy telemetry without a trustworthy registry record is **UNKNOWN**. Provenance is never inferred from host IDs or names. | The read path stops deriving source from the prefix. UNKNOWN is a fourth source value. The provenance migration is designed next and **not started**. |
| D-2 | **Amber is reserved** for predictions, forecasts and experimental states. Amber is not used for confirmed warning health. | Warning gets its own hue (section 5.2, decision pending). |
| D-3 | **Warning and critical shapes.** Warning uses a triangle cue. Critical uses a solid square cue. Textual labels are always kept. | Applies everywhere a severity appears (section 5.2). |
| D-4 | **Interim alert-to-host join.** Approved, with partial coverage exposed whenever either input is capped. Capped results are never presented as complete. | Rules in section 6.4. |
| D-5 | **Activity** derived from alert timestamps for v1, limited to alert transitions. | Section 6.2, CC-6, with the semantics in 6.5. |
| D-6 | **Telemetry v2** stays narrow: agent version and configured collection interval metadata. Observed reporting cadence is derived server-side from telemetry timestamps. The contract is specified before implementation. | Section 9 is the draft contract, for confirmation. |
| D-7 | **Documentation corrections** to `PRODUCT.md` and `docs/design/PRODUCT_DEFINITION.md` may be made as a separate documentation-only change. Factual claims are updated; product scope is unchanged; historical decisions are not rewritten silently. | Section 2.1. Not done in this revision. |
| D-8 | **Host detail** is one page with anchored sections for v1. Not tabs. | Section 7.2. |
| D-9 | **Workspace page is deferred** until its read endpoint exists. No nonfunctional page is built. | Section 7.7. |
| D-10 | **Command Center first.** Designed in detail (section 6). | Implementation waits for the warning hue decision (section 11, Q2). |
| D-11 | **Functional navigation only** (section 4). | — |
| D-12 | **Amber, warning, critical** (D-2 and D-3 together). Warning and critical have distinct semantics beyond colour. | Section 5.2. |
| D-13 | **Warning colour (decided 2026-11-19): Candidate 2, pale violet `#e9b8ff`.** Amber stays reserved for forecast, prediction and experimental states. Warning is identified by its triangle glyph and its WARNING label. Critical is identified by its solid square glyph and CRITICAL label. Status is never conveyed by colour alone. | The token change and its consumers are an approved implementation step (section 10.1). Two preconditions apply first: Q10 (SYNTHETIC badge colour) and the residual-risk note in section 11.1. |

---

## 4. Navigation

Approved baseline:

```
COMMAND CENTER                       /                     (landing)
INFRASTRUCTURE
  Hosts                              /hosts
  Metrics Explorer                   /metrics
DETECT & RESPOND
  Alerts                             /alerts               (Investigate per alert)
  Investigations                     /investigations       (appears only when the index page exists)
SETTINGS
  Agents                             /settings/agents
  Alert Policies                     /settings/alerts      (thresholds and freshness; needs UI)
  Integrations                       /settings/integrations (MCP tokens; needs UI)
  Workspace                          /settings/workspace   (read-only; appears in the sidebar only once its endpoint exists, D-9)

Contextual (not in the sidebar):
  Host detail                        /hosts/:id            (from Hosts, Command Center, Alerts)
  Investigation                      /alerts/:id           (from Alerts; implemented)

Hidden until their workflow exists:
  Incidents, Anomalies, Risk Outlook, Failure Readiness, Model Performance, Dependencies, Events
```

Rules that follow:

* **Only working destinations appear in the sidebar.** The "not available" pages are removed from the sidebar. Their routes may keep the honest placeholder for deep links.
* **Investigations** appears in the sidebar only when an index page lists records and alerts awaiting one. Until then, investigations are reached from Alerts.
* **Events** is not a destination. Alert lifecycle appears in Alerts and in the investigation; host-level activity appears on the host page.
* **Integrations** holds MCP tokens until another integration exists.
* **Host detail** is reached from rows and links. It is not a sidebar item.

---

## 5. Source provenance, states and colour

### 5.1 Provenance vocabulary

| Value | Meaning | Shown as | Counted as connected infrastructure |
|---|---|---|---|
| LIVE | Stored provenance says the host is a real, connected host | LIVE badge (text + solid border) | Yes |
| SYNTHETIC | Stored provenance says the data is generated | SYNTHETIC badge (text + hatched border) | No |
| REPLAY | Stored provenance says the data is historical replay | REPLAY badge (text + dotted border) | No |
| UNKNOWN | No trustworthy stored provenance | UNKNOWN badge (text + dashed border, neutral colour) | No, never counted as LIVE |

Rules:
* **Approved (Q1):** a registry host whose `kind` was explicitly set by an operator is trusted, and its stored kind is used. Telemetry from legacy credentials with no trustworthy registry record is UNKNOWN.
* A value is never guessed from a host ID or name, and existing hosts without trustworthy provenance are UNKNOWN.
* Every host row, tooltip and legend carries the source as text. The shape cue keeps it readable in greyscale.
* The scope toggle gains UNKNOWN. UNKNOWN hosts are visible under the scope toggle and are never counted as LIVE.

### 5.2 Colour, shape and semantics

| Meaning | Treatment | Rule |
|---|---|---|
| Critical (confirmed) | Red `--critical` `#e5483a`; text `#ff6a55`; **solid square glyph** (D-3; the code currently draws a diamond, see C12) and solid border | Keep red. Text label CRITICAL always. |
| Warning (confirmed) | **Pale violet `#e9b8ff`** (D-13). Today's `#e0951f` is too close to the forecast amber (ΔE 14 in normal vision) and is retired. | Glyph **triangle**, text label WARNING always. Badges are drawn with a solid border and text, never as a chart series swatch. Violet is also a series hue; see Q10 and section 11.1. |
| Normal / healthy | Green-teal `#1f8f73`, text `#35d0b5` | Keep. Text label always. |
| Offline / silent | Grey-blue `--offline` `#8aa3b8`; glyph ○ today (`HEALTH_SHAPE` in `Badges.tsx`) | Keep the glyph and the text. |
| Stale | Current stale badge | Keep. The age is stated. |
| Unknown health | Neutral, dashed border | Never green. |
| Forecast, prediction, experimental | Amber `#f2b64a`, dashed | **Reserved** (D-2). Also the single primary action. |
| Series | Blue, teal, red-coral, violet | Never amber. |

Confirmed and predicted are never the same colour, shape or label. Every severity has a text label, a glyph and a colour, so the state survives greyscale and colour-vision deficiency.

### 5.3 Empty, loading, error, stale, partial and disconnected states

| State | Trigger | Shown | Copy and behaviour |
|---|---|---|---|
| Loading | First load | Skeleton that keeps final widths | Placeholder rows; no zeros |
| No hosts yet | Registry and telemetry empty for the workspace | Empty state with an action | "No hosts have reported yet." Action: register a host on Agents. Hosts appear after their first telemetry. |
| No hosts in scope | Scope excludes every source that has hosts | Empty state | "Widen the data-source scope in the top bar." |
| No active alerts | Alert query succeeded and its `total` is zero | Positive empty state | "No active alerts in scope." |
| Alerts unavailable | Alert query failed | Inline error in the alert panels only | "Could not load alerts." with retry. **Never shown as "no active alerts".** |
| Partial coverage | A capped input (section 6.4) | Inline notice on each affected panel | "Partial: N of M …" naming the cap. **Counts that depend on the capped input are not shown as exact.** |
| No recent activity | Query succeeded, nothing in window | Empty line | "No alert transitions in the last 24 hours." |
| Activity unavailable | Query failed | Inline error | As above |
| Stale data | Background refresh failed; previous data kept | Banner | "STALE DATA. Refresh failed; showing the last successful response from …" with retry |
| API unreachable | Network error on first load | Error state | "Could not reach the API" with retry |
| Silent host | Reporting status silent | Row state | Age shown; the line is not extended |
| Source hidden by scope | Host's source excluded | Host page state | "This host's source is hidden." with a control to show it |
| Not collected | Metric not in contract v1 | Text, no empty widget | "Not collected" with the reason |

---

## 6. Command Center

### 6.1 Question and hierarchy

The Command Center answers: **what is happening across my infrastructure right now, and what needs me?** It is a triage surface. Every item links to the place that explains it. Nothing is ranked by invented scores.

Order on the page, top to bottom:

| Level | Element | Purpose |
|---|---|---|
| 1 | **Overall state strip** (CC-1, CC-2) | The whole in-scope picture in one line of counts, plus the connected-infrastructure line |
| 2 | **Attention and alerts** side by side (CC-4, CC-5) | What needs me, and the active alerts with entry points |
| 3 | **Recent activity** (CC-6) and **utilisation distribution** (CC-7) | What changed; how loaded the fleet is |
| 4 | **Data sources and provenance** (CC-3) | How the picture is composed; always visible, never hidden |

### 6.2 Panel inventory

| ID | Panel | Question | Shows | Visual | Interactions | Data | Status |
|---|---|---|---|---|---|---|---|
| CC-1 | **Overall state** | How is the in-scope fleet doing? | Total, healthy, warning, critical, offline, stale, unknown (in scope); the health distribution bar, merged here | KPI tiles and one stacked bar; solid fill only for critical; warning uses its border and triangle glyph | Each tile opens Hosts filtered to that state | `GET /api/v1/hosts` facets (scope applied). Facets are computed over the whole fleet, so they are exact at any size. | Implemented (the bar merges into the strip) |
| CC-2 | **Connected infrastructure** | What is actually real and reporting? | "LIVE: x of y reporting, z silent"; names of silent LIVE hosts | One outlined line beneath the strip | Opens Hosts with the LIVE source | `GET /api/v1/hosts?source=live` facets and rows | Frontend work (a silent-count hint exists today; the line does not) |
| CC-3 | **Data sources** | What is the picture made of? | Count per source (LIVE, SYNTHETIC, REPLAY; UNKNOWN once D-1 ships); "in scope" or "excluded" | Source badges with counts | Toggles the scope | `GET /api/v1/hosts` facets, source (whole fleet) | Implemented (UNKNOWN pending D-1) |
| CC-4 | **Hosts needing attention** | Which hosts need me, and why? | Host name, source badge, health badge, primary reason, CPU, memory, disk (latest), last seen | Table; ten rows; "Top 10 of N not healthy →" to Hosts | Opens Host detail | `GET /api/v1/hosts?health=…` (scope applied, page size 100) | Implemented; partial-coverage rule in 6.4 |
| CC-5 | **Active alerts** | Which conditions are open, and where do I act? | Total active (exact); counts by severity (critical, warning) and by rule (threshold, host offline), each exact or marked partial (6.4); the five most severe open alerts with condition, host, opened, acknowledged-by | Two counts and a short table | Acknowledge in place; **Investigate** per row; "All active alerts →" to Alerts | `GET /api/v1/alerts?status=active&limit=200`, joined to in-scope hosts client-side (6.4) | **Frontend work** |
| CC-6 | **Recent alert transitions** | What alert transitions happened recently? | Last 20 transitions in scope, newest first, each with host, severity and time: OPENED (`opened_at`); ACKNOWLEDGED by X (`acknowledged_at`, `acknowledged_by`); RECOVERED (`resolved_at` where `resolution` = `recovered`); RESOLVED MANUALLY by X (`resolved_at` where `resolution` = `manual`, `resolved_by` = X). Captioned: "Alert transitions only." | Time-ordered list | Opens the alert | `GET /api/v1/alerts?status=all&limit=200` (fields in 6.5) | **Frontend work (derived)**; partial when the alert list is capped (6.4) |
| CC-7 | **Utilisation distribution** | How loaded is the fleet? | Histograms for CPU, memory and worst disk mount: host counts per band; "N with no data" (never counted as zero); the highest host, as a single link | Three histograms | Opens a host | `GET /api/v1/hosts` (`latest`), first 200 hosts by name | Implemented; caption states "first 200 hosts by name" when capped |

Explicitly not on the Command Center:
* No host map, hexagonal or geographic. The real fleet is small, and a map would show a shape with no meaning.
* No "top consumers" ranking. The distribution and the attention table show the same hosts.
* No risk score, forecast, predicted count or "outlook" card. None exists (section 7).
* No incident statistics or incident cards. Incidents do not exist.
* No fleet CPU time series in the first build. It is not in the approved hierarchy.
* No "Recent infrastructure activity" placeholder once CC-6 ships.

### 6.3 What is available now versus what is missing

| Need | Available | Missing |
|---|---|---|
| Overall counts in scope | Yes, exact at any fleet size | — |
| LIVE-only counts | Yes (query with `source=live`) | — |
| Attention table | Yes, partial above 100 non-healthy hosts (6.4) | Ordering by state is client-side over the loaded rows |
| Active alerts and counts | Yes, exact total; scope-filtered counts exact only when complete (6.4) | Source on each alert; server-side scope filter |
| Alert transitions | Yes, alert transitions only, partial when capped | Agent or host state transitions are not recorded; no workspace-wide event feed |
| Utilisation distribution | Yes, first 200 hosts by name | — |
| Forecast, risk, incidents | No | Deferred |

### 6.4 Partial coverage: detection, display and the interim join

The interim design joins alerts to hosts on the client, because the alert payload has no source. It is approved as interim. The durable fix is server-side (section 6.6).

**How partial coverage is detected.** Every list response carries an exact total for the rows it matches. A capped input is one where the returned rows are fewer than that total.

| Input | Request | Exact total | Capped when |
|---|---|---|---|
| Host list (state and source for the join; utilisation) | `GET /api/v1/hosts`, page 1, page size 200 | `total` | `total > items.length` |
| Attention hosts | `GET /api/v1/hosts?health=…`, page size 100 | `total` | `total > items.length` |
| Active alerts | `GET /api/v1/alerts?status=active&limit=200` | `total` | `total > alerts.length` |
| Alert transitions | `GET /api/v1/alerts?status=all&limit=200` | `total` | `total > alerts.length` |
| Host facets (state and source counts) | Computed over the whole fleet before paging | exact | never capped |

**Display rules.**
1. **Exact, always:** the facet counts (CC-1, CC-3) and the unfiltered alert total. They are labelled exact.
2. **Scope-filtered alert counts** (CC-5, by severity or rule) are exact only when the alert list is complete **and** the host list is complete **and** every alert's host resolves to a loaded host. When every source is selected, no join is needed: those counts are exact only when the alert list is complete. When any of these fails, the count is replaced by "Not exact for this scope: N of M alerts loaded". The loaded count is labelled as a subset. It is never shown under a "total", "all" or "exact" label.
3. **Unresolved alerts.** An alert whose host is not in the loaded host list is counted in an explicit "source not loaded" bucket, shown only when non-zero. It is never dropped and never assigned a source.
4. **Attention (CC-4).** The heading reads "Top 10 of N not healthy", with N exact. When the attention query is capped, a notice reads: "Ranked among the 100 most recent non-healthy hosts. Hosts beyond those are not shown." Severity ordering applies only to the loaded rows, so a critical host outside the loaded set can be missing. The notice must say so.
5. **Utilisation (CC-7).** The caption reads "First 200 hosts by name" when the host list is capped.
6. **Transitions (CC-6).** When the alert list is capped, a notice reads "Covers the 200 newest alerts. Transitions of older alerts are not shown." The alert list is ordered by `opened_at`, so a recovery of an older alert can be missing even inside the time window.
7. Every notice names the cap and the total, for example "Partial: 200 of 340 active alerts loaded".

**Test expectations for the build.** Fixtures with 199, 200 and 201 hosts, and with 199, 200 and 201 active alerts, must each show the correct exact or partial state. Counts must never change between "exact" and "partial" without the notice changing.

### 6.5 Activity semantics (CC-6)

Verified against `AlertOut` in `backend/app/alerts/router.py`. The list response carries: `opened_at`; `acknowledged_at` and `acknowledged_by`; `resolved_at`, `resolved_by` and `resolution` (`recovered` or `manual`). It does **not** carry `severity_changed` events, the sample time of any breach, or the full per-alert event history.

| Transition | Derived from | Label | Reliable? |
|---|---|---|---|
| Alert opened | `opened_at` | OPENED | Yes |
| Alert acknowledged | `acknowledged_at`, `acknowledged_by` | ACKNOWLEDGED by X | Yes. Acknowledgement happens once; repeats are no-ops, so the first acknowledgement is the one stored. |
| Recovered by telemetry | `resolved_at` where `resolution = recovered`; `resolved_by` is `system` | RECOVERED | Yes. The list distinguishes system recovery from manual resolution, so the label RESOLVED is not needed. |
| Resolved by an operator | `resolved_at` where `resolution = manual`; `resolved_by` is the operator | RESOLVED MANUALLY by X | Yes |
| Severity changed | not in the list | — | Not shown. The panel does not claim it. |
| Agent or host state change | not stored | — | Not shown. |

Caption on the panel, verbatim: "Alert transitions only. Severity changes, agent starts and stops, and host state changes are not recorded here."

The list is capped (6.4), so transitions of older alerts can be missing. The panel must show the partial notice when capped.

### 6.6 Durable fix (backend, after D-1)

* Add `source` to every alert payload, taken from the stored host provenance at the time of the alert, and add a `source` filter to `GET /api/v1/alerts`.
* Add a workspace-level alert summary endpoint returning exact counts by severity, rule, state and source, so that scope counts never depend on the client join.
* Add an `offset` to the alert list and a workspace event endpoint, which together make CC-6 complete.
* Add a server-side sort by health state on the host list, so the attention set is ordered before paging.

None of these is started. Each needs its own design and tests.

### 6.7 Honest states for the Command Center

Section 5.3 applies in full. Command-Center-specific rules:
* An alerts failure never reads as "no active alerts". CC-5 and CC-6 each have their own error state, and the rest of the page still renders.
* Counts from the facets are exact. Counts that depend on a capped input follow section 6.4.
* Timestamps are relative with the exact time on hover; the mono face is used for every number.

### 6.8 Visual direction

Keep the approved dark observability console: 13 px body, 12 px tables, 11 px mono axes and tags, 1 px seams, no shadows, 4 px radius on controls and 0 on widgets. The grid is the existing 12-column widget grid. Widgets in a row share height. Phones: two KPI tiles per row, tables scroll inside their widget, acknowledgement works on a phone.

---

### 6.9 Verified data contract for the Command Center

Read from the code (`backend/app/monitoring/router.py`, `schemas.py`, `backend/app/alerts/router.py`, `frontend/src/api/*`). Sections 6.10 to 6.12 depend on these facts; a change to any of them requires updating this list.

**Host list: `GET /api/v1/hosts`**
* Query: `source` (comma list of `live`, `synthetic`, `replay`; absent means all), `health` (comma list), `search`, `sort` (`last_seen` or `hostname`), `order`, `page` (default 1), `page_size` (1 to 200, default 50).
* Response `HostListOut`: `items` (HostOut[]), `page`, `page_size`, `total` (count after both filters, exact), `facets`, `thresholds`.
* `facets.source`: counts per source; ignores the source filter; respects the health filter; computed over the whole fleet.
* `facets.health`: counts per health state; ignores the health filter; respects the source filter; computed over the whole fleet. **Exact at any fleet size.**
* `HostOut` fields used: `host_id`, `hostname`, `source`, `connected_infrastructure`, `reporting_status` (`reporting`, `stale`, `silent`, `replay`), `last_seen`, `last_seen_age_seconds`, `health` (`status`, `category`, `reasons[]` with `metric`, `labels`, `value`, `threshold`, `level`, `key`), `latest` (`cpu_percent`, `memory_percent`, `disk_percent_max`, `disk_mount_max`; null when not available, never zero).
* Health `offline` is the same condition as reporting status `silent` (age beyond the offline window). Health `stale` is reporting status `stale`.
* `ReasonOut` carries no observation time. The alert trigger time is on the alert, not on the host list.
* Ordering: server default is last seen, descending. There is no sort by state. Item lists are capped at 200 (the page-size maximum). There is no offset beyond `page`.

**Alert list: `GET /api/v1/alerts`**
* Query: `status` (`active` default, `resolved`, `all`), `host_id`, `limit` (1 to 200, default **100**). There is **no offset** and no source filter.
* Response `AlertList`: `alerts` (AlertOut[]) and `total`, the exact count of rows matching the status and host filters (before the limit).
* Ordering: `opened_at` descending, then id. A capped list therefore holds the 200 newest alerts.
* `AlertOut` fields: `id`, `host_id`, `condition_key`, `rule` (`threshold` or `host_offline`), `metric`, `labels`, `severity` (`warning` or `critical`), `state` (`open`, `acknowledged`, `resolved`), `value` (value at last evaluation, or the recovery value once resolved), `threshold`, `opened_at`, `last_evaluated_at`, `acknowledged_at`, `acknowledged_by`, `resolved_at`, `resolved_by`, `resolution` (`recovered` or `manual`, present whenever the alert is resolved).
* Constraint enforced in the database: a resolved alert always has `resolution` and `resolved_at`. A non-resolved alert has no `resolution`.
* Actions: `POST /api/v1/alerts/{id}/acknowledge` (first acknowledgement is the one stored), `POST /api/v1/alerts/{id}/resolve` (manual).

**Frontend facts that affect the build**
* `api.alerts(status)` in `frontend/src/api/client.ts` does not send `limit`, so it always requests 100. Adding a `limit` argument is a client change (stage 1).
* `useAlerts(status)` in `frontend/src/api/hooks.ts` uses query key `["alerts", status]`. Acknowledge and resolve invalidate every `["alerts"]` key, so any Command Center alert query sharing the prefix refreshes after an action.
* `useHostList(params)` uses query key `["hosts", params]` and refetches every 15 s. Two queries with identical parameters share one cache entry.
* `useHostList` maps `source` to the array of selected sources. When every source is selected the scope helper sends no `source` filter (`scopeFilter` in `lib/scope.ts`).
* The alerts page keeps its status filter in component state, not in the URL (`AlertsPage.tsx`). Only the status-free link to `/alerts` is reliable.
* `OverviewPage.tsx` already requests the whole fleet with `page_size` 200 and `sort: hostname` for the utilisation panels, and a second query with `health` filters and `page_size` 100 for attention.

### 6.10 Panel specifications

Each panel lists the question, fields, endpoint and parameters, states, interactions and routes, existing-API status, dependencies and acceptance criteria. "Scope" means the in-scope source selection from the URL (`source`). "Time range" means the URL time-range parameter, carried on every link.

#### CC-1 Overall state

| Item | Specification |
|---|---|
| Question | How is the in-scope fleet doing right now? |
| Fields | Total in scope; counts for healthy, warning, critical, offline, stale, unknown; the stacked distribution bar drawn from the same counts. |
| Endpoint | `GET /api/v1/hosts?source=<scope>&page_size=1` (only `facets` and `total` are used). Use a separate, small query so the strip does not wait for the 200-row fleet query. When every source is selected, omit `source`. |
| Source of each count | `total` for in-scope total. `facets.health` for the state counts. Facets are exact at any fleet size. |
| Loading | Tiles shown as skeletons at their final widths. No zeros while loading. |
| Empty | If `facets.source` sums to zero across all sources: "No hosts have reported yet." with an action to register a host on Agents. If hosts exist but the scope excludes all of them: "No hosts in the selected sources. Widen the data-source scope in the top bar." |
| Error | First load fails: "Could not reach the API" with retry, and the strip is not drawn. A later refetch failure keeps the last counts, dimmed, with the stale banner (section 5.3). |
| Stale | Background refetch failed: stale banner names the age of the last successful response. |
| Partial | Never. The counts are exact. |
| Interactions | Each tile links to `/hosts?health=<state>` with the scope and time range carried. The bar segments link the same way. |
| Existing API | Yes. |
| Dependencies | None for the counts. Warning and critical tiles take the new colours only after D-13 is implemented. |
| Acceptance | (1) Healthy + warning + critical + offline + stale + unknown equals `total`. (2) With a subset selected, the counts equal the facets returned for that subset. (3) Each tile's link contains `health` and the current `source`. (4) A failed refetch keeps the previous counts and shows the stale banner. (5) With zero hosts and all sources selected, the empty-state copy is shown and no zero tiles are drawn. |

#### CC-2 Connected infrastructure (LIVE only)

| Item | Specification |
|---|---|
| Question | Which hosts are real and reporting now? |
| Fields | "LIVE: x of y reporting · z silent · w stale". Names of silent LIVE hosts, up to five, each linked; "and N more" when there are more. |
| Derivation | y = `facets.source.live` (exact, whole fleet). z = `facets.health.offline` from a `source=live` query (exact; equals silent LIVE hosts). w = `facets.health.stale` from the same query (exact). x = y − z − w − (LIVE hosts with health unknown, if any; shown as "x unknown" only when non-zero). Names come from the items of `source=live`. |
| Endpoint | `GET /api/v1/hosts?source=live&page_size=200&sort=hostname` (facets for counts, items for names). |
| Scope rule | This panel respects the scope (C15). When LIVE is not selected, show "LIVE hidden by scope" with a control that adds LIVE to the scope. No LIVE counts are shown in that state. |
| Loading | Outlined line as a skeleton. |
| Empty | No LIVE hosts in the workspace: "No connected hosts have reported. Connected infrastructure appears here after an agent sends its first telemetry." Links to Agents. |
| Error | Inline: "Could not load connected hosts." with retry. Other panels are unaffected. |
| Stale | Inline stale marker with age. |
| Partial | Names only: when LIVE hosts exceed 200, the names list reads "Names shown for the first 200 live hosts." Counts remain exact. |
| Interactions | The line links to `/hosts?source=live` (carry time range). Each name links to `/hosts/<id>`. |
| Existing API | Yes. |
| Dependencies | Provenance (D-1): the LIVE count is only as trustworthy as the source label. Until D-1 ships, the label is prefix-derived; the panel's caption must not claim provenance is stored. |
| Acceptance | (1) y equals the LIVE facet. (2) z equals the number of names shown when fewer than 200 LIVE hosts exist. (3) With LIVE excluded by scope, no LIVE count is rendered. (4) A LIVE host with health offline appears by name; a SYNTHETIC host never appears. (5) The line never reports "all connected" while any LIVE host is offline or stale. |

#### CC-3 Data-source provenance

| Item | Specification |
|---|---|
| Question | What is the picture built from, and what is in scope? |
| Fields | One row per source: badge (text and border shape, section 5.1), count, and "in scope" or "excluded by scope". UNKNOWN is a fourth row, shown only when its count is non-zero (after D-1). |
| Endpoint | The same small query as CC-1 (`facets.source` is whole-fleet and ignores the source filter). |
| Loading | Rows as skeletons. |
| Empty | Covered by CC-1's empty state; the panel is not drawn when the fleet is empty. |
| Error | Inline, with retry. |
| Stale | Inline stale marker. |
| Partial | Never. |
| Interactions | Each row toggles that source in the scope (the same action as the top-bar toggle, refusing to deselect the last source). |
| Existing API | Yes. |
| Dependencies | D-1 for UNKNOWN and for the stored-provenance claim. Until D-1, the panel must not say that source is stored. |
| Acceptance | (1) Counts sum to the whole-fleet total. (2) Toggling a row changes the URL `source` parameter and refetches CC-1 and CC-4. (3) The last selected source cannot be deselected. (4) After D-1: UNKNOWN is never added to LIVE. |

#### CC-4 Hosts needing attention

| Item | Specification |
|---|---|
| Question | Which hosts need me, and why? |
| Fields per row | Host name (link), source badge, health badge (glyph and label), reason text, CPU, memory, disk (`latest`, en dash when null), last seen (relative; exact on hover). |
| Reason text | Stale: "Telemetry N old". Offline: "No telemetry for N". Unknown: "No fresh CPU, memory or disk measurement". Otherwise the first reason: metric short name, mount if any, value, and "≥ threshold"; "+ N more" when several. |
| Endpoint | `GET /api/v1/hosts?source=<scope>&health=critical,warning,stale,offline,unknown&page_size=100`. |
| Total | "Top 10 of N not healthy": N is `total` (exact). |
| Ordering | Client-side over the loaded rows only: health severity (critical, warning, stale, offline, unknown), then hostname. |
| Loading | Table skeleton with final column widths. |
| Empty | When `total` is zero and some hosts are in scope: "Every host in scope is healthy." When no hosts are in scope, CC-1 already explains it; this panel is not drawn. |
| Error | Inline, with retry. |
| Stale | Inline stale marker. |
| Partial | When `total` exceeds the rows returned (over 100 non-healthy hosts): "Ranked among the 100 most recent non-healthy hosts. Hosts beyond those are not shown." Severity ordering applies only to the loaded rows. |
| Interactions | Row name links to `/hosts/<id>` with scope and time range carried. "View all" links to `/hosts?health=<same list>` with scope carried. |
| Existing API | Yes. Current code requests `page_size` 100 and sorts client-side. |
| Dependencies | None for v1. The durable fix is a server-side sort by health state (backend, section 6.6). |
| Acceptance | (1) At most 10 rows, all non-healthy. (2) The critical row precedes the warning row whenever both are loaded. (3) With 101 non-healthy hosts, the partial notice is shown and the heading still shows N = 101. (4) Each row's reason matches its first `health.reasons` entry. (5) "View all" lists the same health set. |

#### CC-5 Active alerts

| Item | Specification |
|---|---|
| Question | Which conditions are open, and where do I act? |
| Fields | Total active across all sources (exact). Counts by severity (critical, warning) and by rule (threshold, host offline), each exact or marked partial. The five most severe open alerts: severity badge, condition text, host (link), opened (relative), acknowledged-by when set. |
| Endpoints | `GET /api/v1/alerts?status=active&limit=200` (new `limit` argument, stage 1). Host join uses the CC-2/CC-4 host list only when a subset of sources is selected. |
| Count rules | (a) All sources selected: no join. Severity and rule counts are exact only if the alert list is complete (`total` equals returned rows). (b) Subset selected: each alert's host must resolve in the loaded host list (fleet query, 200 rows). Counts are exact only when both the alert list and the host list are complete and every host resolves. (c) An alert whose host is not loaded is counted in "source not loaded", shown only when non-zero. |
| Severity ordering of the five | Critical before warning, then `opened_at` newest first, among the loaded alerts. |
| Loading | Counts as skeleton tiles; table skeleton. |
| Empty | Only when the alert query succeeded, its `total` is zero, and the scope is exact: "No active alerts in scope." |
| Error | Inline: "Could not load alerts." with retry. Never shown as "no active alerts". The rest of the page renders. |
| Stale | Inline stale marker. |
| Partial | Alert list capped (`total` above rows returned): "Partial: 200 of N active alerts loaded." Scope-filtered counts show "Not exact for this scope: n of N loaded" and the loaded number is labelled as a subset. Unfiltered `total` stays exact. |
| Interactions | Acknowledge in place (the existing mutation; refreshes all alert queries). Investigate links to `/alerts/<id>`. "All active alerts" links to `/alerts`, which defaults to active. |
| Existing API | Yes, with the client `limit` change. No backend change is required for the interim. |
| Dependencies | Stage 1 client change. Durable fixes (source on alerts, a summary endpoint, offset) are backend work, not prerequisites (section 10.1). |
| Acceptance | (1) With 250 active alerts, `total` shows 250 and the partial notice shows 200 loaded. (2) With all sources selected and 250 alerts, severity counts are marked partial. (3) With a subset selected and a host missing from the loaded list, the affected alert is counted under "source not loaded" and no exact scope count is shown. (4) Acknowledging an alert updates the count and the row within one refetch. (5) A failed alert request shows the inline error and leaves the other panels rendered. |

#### CC-6 Recent alert transitions

| Item | Specification |
|---|---|
| Question | What alert transitions happened recently? |
| Derivation | From each alert in `GET /api/v1/alerts?status=all&limit=200`, emit up to four transitions. **OPENED** at `opened_at`. **ACKNOWLEDGED by X** at `acknowledged_at` when set, actor `acknowledged_by`. **RECOVERED** at `resolved_at` when `resolution` is `recovered`, actor `system`. **RESOLVED MANUALLY by X** at `resolved_at` when `resolution` is `manual`, actor `resolved_by`. No other transitions are emitted. |
| Window | Transitions within the 24 hours before the page's current time. |
| Fields per row | Time (relative; exact on hover), label, severity badge, host (link), actor. |
| Count | The 20 newest transitions in scope. |
| Scope | Same rules as CC-5 (no join when all sources are selected). |
| Caption | "Alert transitions only. Severity changes, agent starts and stops, and host state changes are not recorded here." |
| Loading | Row skeletons. |
| Empty | When the alert list is complete, or it is capped but its oldest loaded alert is within the window: "No alert transitions in the last 24 hours." When it is capped and the oldest loaded alert is inside the window, the empty state is not claimed; show the partial notice instead. |
| Error | Inline, with retry. Independent of CC-5. |
| Stale | Inline stale marker. |
| Partial | Alert list capped: "Covers the 200 newest alerts. Transitions of older alerts are not shown." |
| Interactions | Row links to `/alerts/<id>`. |
| Existing API | Yes, with the client `limit` change. |
| Dependencies | Stage 1 client change. A full history needs an alert-event endpoint and an offset (backend, not a prerequisite). |
| Acceptance | (1) An alert opened, acknowledged and recovered yields three rows in time order, with the recovery labelled RECOVERED and actor `system`. (2) An alert closed by an operator yields RESOLVED MANUALLY with the operator's name, never RECOVERED. (3) No row is labelled SEVERITY CHANGED, AGENT, or HOST STATE. (4) A resolved alert with no `resolution` is omitted and never labelled. (5) With a capped list whose oldest loaded alert is inside the window, the empty state is not shown. |

#### CC-7 Utilisation distribution

| Item | Specification |
|---|---|
| Question | How loaded is the fleet? |
| Fields | Three histograms: CPU, memory, worst disk mount (`disk_percent_max`). Bands 0–20, 20–40, 40–60, 60–80, 80–100 percent, with host counts. A "N with no data" line. The highest host as one link with its value. |
| Endpoint | The fleet query `GET /api/v1/hosts?source=<scope>&page_size=200&sort=hostname`. Shared with CC-5's join, so it is fetched once. |
| Caption | "First 200 hosts by name" when `total` exceeds the rows returned. |
| Colour | Bars must not use the fixed cut-offs of the current code (C14). Bars are neutral by default. Where a band is shown as a threshold state, it uses the configured per-metric thresholds (85/95 CPU and memory, 90/95 disk) and the same warning and critical tokens as the health badges. |
| Loading | Three histogram skeletons. |
| Empty | "No fresh measurements from any host in scope." (the existing copy). |
| Error | Inline, with retry. |
| Stale | Inline stale marker. |
| Partial | Caption above, as stated. |
| Interactions | The highest host links to `/hosts/<id>` with scope carried. Bands are not clickable in v1. |
| Existing API | Yes. |
| Dependencies | None for the data. The colour rule depends on D-13 being implemented. |
| Acceptance | (1) Band counts sum to the hosts with a value. (2) With 201 hosts, the caption reads "First 200 hosts by name". (3) A host at 96 % CPU is counted in the 80–100 band and is coloured critical, not warning, when the critical threshold is applied. (4) No band is coloured by a fixed cut-off that differs from the configured thresholds. (5) Hosts with no value are counted separately and never as zero. |

### 6.11 Page-level rules

* Query sharing: CC-1 and CC-3 share the small query. CC-4 and CC-5's host join share the attention query and the fleet query. CC-5 and CC-6 share the alert query prefix so one acknowledge refreshes both.
* Every query carries the scope, so every panel follows the same scope (design rule).
* Refresh: 15 seconds for all Command Center queries, as today.
* Time range: carried on every link; the page does not change it.
* No fake content: no host map, no top-consumer list, no risk value, no forecast, no incident figure, no activity beyond the four transitions, no placeholder widget for data that does not exist.
* A failure in one query never blanks the page. Each panel owns its error state.
* The page header must not claim "live" or "current" for data older than its last successful response. The stale banner is the only way a stale response is labelled.

### 6.12 Implementation acceptance for the page

The first release of the Command Center is accepted when, in a browser session against a real authenticated console:
1. Every panel in 6.10 renders its specified states for: no hosts, hosts in one source only, a mixed fleet, a fleet above 200 hosts (fixture), an alert list above 200 (fixture), a failed alert request, and a failed host request.
2. Every count matches its stated source and exactness rule, checked against the API response.
3. Every link lands on the page it names, with scope and time range preserved.
4. Acknowledge works from CC-5 and the list updates.
5. No application code outside the Command Center files changes behaviour, and the existing frontend tests still pass.
6. Browser verification is recorded only after it is actually performed (blocked, Q11, until a sign-in path exists).

## 7. Other pages

### 7.1 Hosts (`/hosts`) — implemented
* Facets narrow within scope and never widen it.
* Table: host, host id, source, agent status, last seen, CPU, memory, disk, state, service. Default order is last seen (server), not state. Page size 25.
* **Gaps:** no OS column (no data); no "awaiting first report" rows (registered hosts that have not reported are on Agents, not here).
* Frontend work: awaiting-first-report rows merged from the registry list; state-first sort, client-side for loaded rows and marked partial when capped.

### 7.2 Host detail (`/hosts/:id`) — implemented, contextual, one page (D-8)
Current sections: identity, health, current observations, history. Recommended additions, in order:
1. **Alerts for this host:** active and recent alerts, with acknowledge, resolve and Investigate. Data exists (`GET /api/v1/alerts?host_id=`). This is the largest gap.
2. **Not collected:** one line for disk I/O and process data (not in contract v1). No empty widgets.
3. **Metadata:** host, host id, source, agent id, service, first seen, last seen, credential count.
4. **Agent version and configured interval:** when telemetry v2 ships (section 9). Until then, "not reported".

Tabs are not used for v1 (D-8). Sections are anchored and reachable from a short in-page index.

### 7.3 Metrics Explorer (`/metrics`) — implemented for one host
A single-metric comparison tool over stored series. It is not a query builder: the backend answers one metric per request, with one aggregation and one step. Multi-host comparison is client-side fan-out (frontend work). Formulas, multiple queries, group-by tags and forecasts are not built and are not planned. Network values are labelled as derived rates. Replay hosts are hidden with a stated reason.

### 7.4 Alerts (`/alerts`) — implemented
Filters (status, host); table with condition in words, last value (not trigger value), state, opened, last evaluated, acknowledged-by, and actions; inline lifecycle. Gaps: paging beyond 200 (backend); counts strip (frontend, from the bounded list, with the partial rule in 6.4). Alert Policies (threshold editing) is a settings page, not part of this list.

### 7.5 Investigation (`/alerts/:id`) — implemented, browser-unverified
Section 8.

### 7.6 Agents (`/settings/agents`) — implemented
Register host; registered hosts with telemetry state (awaiting first telemetry, reporting, silent, replay), last envelope, active credentials; issue credential (token shown once, with a copy action and a "stored it" dismissal); install snippet using the environment-variable token with `-host-id` and `-agent-id`; revoke with confirmation. Gaps: agent version and configured interval (telemetry v2); rejection history (backend work).

### 7.7 Settings
* **Alert Policies** (`/settings/alerts`): thresholds and freshness. Frontend work on the existing settings API. Shows defaults beside values; version conflict message.
* **Integrations** (`/settings/integrations`): MCP tokens. Frontend work on the existing API. Shows scopes (read only), expiry and revoke; creation shows the token once.
* **Workspace** (`/settings/workspace`): **deferred until its read endpoint exists (D-9).** It is not built as a placeholder and does not appear in the sidebar until then.
* Members, roles and notifications: deferred.

### 7.8 Onboarding (flow, not a sidebar item)
Sign in (Clerk, built, not verified against a tenant) → workspace (the single default; no creation) → register host (Agents) → issue credential and copy the install snippet → waiting for first telemetry (registry state, polling) → verify the first reported values → thresholds (optional) → Command Center.

Failure states to design: credential not saved (revoke and issue again); agent cannot reach the API (the console cannot see it; show a checklist: URL reachable, token set, host id and agent id match the credential); revoked credential (401 on the next request, stated plainly); host registered but silent after reporting (silent state, with age).

Honest limit: the console cannot distinguish "agent never reached the API" from "agent is not running". The waiting state must say so.

---

## 8. Investigation workflow (implemented)

An engineer opens an alert from Alerts and sees:

| Section | Content | Source of truth |
|---|---|---|
| Summary | Severity, state, host, the trigger in words, first fired, evidence time, window | Investigation view |
| Trigger evidence | Rule, metric and labels, observed value, comparison and limit, sample time, first fired, last evaluated. Anything not recorded is labelled "not recorded". | `trigger`, persisted at open |
| Recovery evidence | Recovered by telemetry (value, sample time, closed time), or closed manually by a named operator with the statement that telemetry does not show the condition cleared, or not recovered | `recovery`, persisted at system recovery |
| Telemetry windows | Around the trigger and around the recovery sample; window choice 15–120 minutes; gaps are breaks | Bounded series |
| Lifecycle | Opened, acknowledged, recovered or resolved, actor, time | Alert events |
| Related alerts | Same host, same window, labelled as temporal correlation only; paged | Alerts table |
| Host now | Current health and reporting state | Host read API |
| Record | Facts (each with a source), conclusions (each citing the facts it rests on), not-established items, digest, verification | `investigation_reports` |

Rules the page must keep:
* Trigger evidence is written once and never changes. Recovery is written only by the system.
* A manual close is never presented as telemetry recovery.
* Facts, conclusions and not-established items are three visually separate lists.
* The record says "Root cause is not established." No conclusion claims a cause.
* Nothing is generated by a language model. No narrative is shown (decision D-3 in section 3 governs any future explanation).

Future AI explanation, if approved later (decision 3): optional, grounded in the stored evidence and its fact ids, and unable to set operational state, risk or any evidence.

Browser verification is outstanding. The page is unit-tested (11 tests); the API and database behaviour is verified by the 30/30 E2E run and 12 backend tests.

---

## 9. Telemetry v2 (narrow): draft contract for confirmation

Scope (D-6): **agent version and configured collection interval metadata only.** The observed reporting cadence is **derived server-side** from telemetry timestamps. The agent does not report it. No process, OS, disk I/O or application telemetry is added.

This is a draft for confirmation. Implementation waits for confirmation.

| Item | Proposal |
|---|---|
| Carrier | An optional `agent` object in the envelope, present only when `schema_version` is `"2.0"`. Version 1.0 envelopes remain valid and unchanged. |
| `agent.version` | String: the agent build version (the agent's version constant is `0.1.0`). |
| `agent.configured_interval_seconds` | Integer, 1 to 3600: the configured collection interval (`UPTIMETRACE_INTERVAL` or `-interval`; default 60 s). |
| Not carried | Hostname changes, OS, paths, tokens, process lists, disk I/O |
| Observed cadence (server-side) | Derived from the spacing of recent envelope timestamps for the host (a bounded window; median gap). Reported as `observed_cadence_seconds`, computed by the read path, never sent by the agent. |
| Display | Configured and observed shown on separate labelled lines. Never merged. A large difference is stated, not hidden. |
| Absent metadata | v1 agents show "not reported". Never a default value. |
| Storage | Decided at implementation: per envelope (in the JSONB payload), or on the registry host row. Recommendation: the registry row, because the value describes the agent configuration rather than one envelope. |
| Validation | The ingestion validator accepts `agent` only under `schema_version` `2.0`; the value ranges above are enforced there. |
| Compatibility | Agents built before v2 keep sending 1.0. The console shows "not reported" for them. |

Still to specify before implementation: the exact validator changes, and the window and minimum sample count for the observed-cadence median.

Not in v2: OS, disk I/O, process data, application telemetry.

---

## 10. Next steps (after approval)

### 10.1 Dependency sequence

| Item | Type | Blocks | Needed for Command Center v1? |
|---|---|---|---|
| Client `limit` argument for alerts (`api/client.ts`, `api/hooks.ts`) | Frontend | CC-5, CC-6 | **Yes**, stage 1 |
| Critical glyph from diamond to solid square (`components/ui/Badges.tsx`, `HEALTH_SHAPE`) | Frontend | D-3 compliance everywhere a critical badge appears | Yes, stage 2. Affects Hosts and Host detail too, so their tests need the same change. |
| SYNTHETIC badge colour decision (Q10) | Decision | The warning colour change (D-13) | **Yes**, before the colour change ships |
| Warning colour change (D-13): `--warning` in `styles/tokens.css` and its consumers (section 10.2, stage 6) | Frontend, styling | Final warning visuals | Yes for the final visuals. The panels are functionally complete without it. |
| Provenance design and migration (D-1) | Backend and frontend | Stored-provenance claims; UNKNOWN; CC-2 and CC-3 as complete | **Yes** for CC-2 and CC-3. Other panels can ship before it, with source labels that do not claim to be stored. |
| Alert source field and `source` filter; alert summary endpoint; alert `offset`; host state sort | Backend | Exact scope counts without a client join; complete activity; severity-correct attention ordering | **No.** Approved interim behaviour covers v1 (section 6.4). These are durable improvements. |
| Browser verification of the existing Overview | Verification | A visual and behavioural baseline before the rewrite | Recommended, but **blocked** by Q11. |
| Correction of `PRODUCT.md` and `PRODUCT_DEFINITION.md` (D-7) | Documentation | Nothing | No. Separate change. |

Ordering constraints:
* Stage 1 (client `limit`) comes first. Nothing else in the alert panels can be tested against real limits without it.
* Q10 is answered before the warning colour change.
* CC-2 and CC-3 are held until D-1 is implemented. They may be built behind a stated "provenance not yet stored" caption only if the owner accepts that, which is not the current decision.
* No backend change is a prerequisite for the first release.

### 10.2 Command Center first milestone (stages)

File names below were checked in the repository unless marked **new (proposed)**. Each stage lists its verification. No stage starts until the previous stage's tests pass.

**Stage 1: data integration and source plumbing (no visible change)**
* `frontend/src/api/client.ts`: add an optional `limit` argument to `alerts(status, signal, limit)`. Default stays 100 so existing callers are unchanged.
* `frontend/src/api/hooks.ts`: `useAlerts(status, limit?)` with key `["alerts", status, limit]`, so prefix invalidation still works.
* `frontend/src/lib/scope.ts`: source list stays `live`, `synthetic`, `replay` until D-1. Read the source through one helper so the UNKNOWN change is a single edit later.
* Verification: `frontend/src/api/client.test.ts` (exists) asserts the query string with and without `limit`. Existing tests pass unchanged.

**Stage 2: overall state and source panels (CC-1, CC-3; CC-2 held)**
* `frontend/src/features/overview/OverviewPage.tsx`: CC-1 reads counts from a `page_size=1` facet query; keep the strip, the data-sources widget (CC-3) and the health bar as one unit.
* `frontend/src/components/ui/Badges.tsx`: critical glyph to a solid square; keep the text label.
* `frontend/src/features/overview/OverviewPage.test.tsx` (exists): tests for CC-1 states and tile links; `Badges.tsx` glyph assertion.
* Verification: acceptance criteria CC-1 (1) to (5) and CC-3 (1) to (3). Hosts and Host detail tests still pass after the glyph change.

**Stage 3: attention and active alerts (CC-4, CC-5)**
* `frontend/src/features/overview/OverviewPage.tsx`: CC-4 reason text per 6.10; partial notice; "Top 10 of N" heading.
* `frontend/src/features/overview/ActiveAlertsPanel.tsx`: **new (proposed)**, the CC-5 panel. Uses `useAlertMutations` from `api/hooks.ts` (exists).
* `frontend/src/lib/commandCenter.ts`: **new (proposed)**, pure functions for the join, the exact-or-partial decision and the "source not loaded" bucket. Kept pure so the rules are tested without rendering.
* `frontend/src/lib/commandCenter.test.ts`: **new (proposed)**, covers 199, 200 and 201 hosts and alerts, subset and full scope, and missing hosts.
* Verification: 6.10 CC-4 (1) to (5); CC-5 (1) to (5).

**Stage 4: recent transitions and utilisation (CC-6, CC-7)**
* `frontend/src/lib/alertTransitions.ts`: **new (proposed)**, pure derivation of the four transitions and the 24-hour window.
* `frontend/src/lib/alertTransitions.test.ts`: **new (proposed)**, covers a recovered alert, a manually resolved alert, an acknowledged alert and a missing `resolution`.
* `frontend/src/features/overview/RecentTransitionsPanel.tsx`: **new (proposed)**, CC-6 panel.
* `frontend/src/features/overview/OverviewPage.tsx`: remove the `NotAvailable` activity block; fix the histogram colour rule (C14) in `UtilPanel`; capped caption.
* Verification: 6.10 CC-6 (1) to (5); CC-7 (1) to (5).

**Stage 5: shared states and partial notices**
* `frontend/src/components/ui/Banners.tsx` (exists, holds `StaleBanner`): add a **new (proposed)** `PartialBanner` export with the wording in section 5.3.
* `frontend/src/components/states/States.tsx` (exists): confirm `ErrorState`, `EmptyState`, `Loading`, `NotAvailable` cover the 5.3 states; add per-panel variants only where the copy differs.
* Verification: one test per row of the section 5.3 table that applies to the Command Center.

**Stage 6: warning colour (D-13) and regression**
* `frontend/src/styles/tokens.css`: `--warning` from `#e0951f` to `#e9b8ff`, after Q10.
* Consumers to review, all in `frontend/src/styles/features.css` (checked):
  * `.kpi-warn .kpi-value`
  * `.hb-warning`
  * `.util-bar i.warn`, `.hist-bar i.warn`
  * `.seg-warning`
  * `.banner.warn`, which is used for **informational** notices such as the truncation banner. Those should move to a neutral border; a notice is not a health state.
  * `.lvl-warning`
  * `.tokenbox`, the credential token box, which uses the warning border for a non-status element. Change to a neutral border.
* `.src-synthetic` uses `--s4`. Change per Q10 (recommended: neutral ink with its dashed border).
* Verification: full frontend suite (`npx vitest run`) and type check (`npx tsc --noEmit -p .`). Visual check in a browser is **blocked** by Q11.

**Browser verification and regression (after stage 6)**
* Automated: the full frontend suite, the type check, and the backend affected set (no backend change in this milestone, so the 156-test result stands unless backend files change).
* Manual browser walkthrough of the Command Center, Hosts, Host detail, Alerts and Investigation: **not performed**. Blocked by Q11. Results are recorded only when run.

### 10.3 Remaining items

Only these, in order. None has started.

1. **Provenance design and migration (D-1).** Write the migration design: stored source on hosts and on telemetry or the credential; backfill rule (registry rows with an operator-set kind keep their kind; everything else UNKNOWN); the read-path change that stops prefix derivation. Then implement, with tests and a real-stack check.
2. **Warning colour (Q2, D-13).** Decided. The token and its consumers change in an approved implementation step, after Q10 is answered (section 10.1).
3. **Command Center (D-10).** Implement CC-1 to CC-7 as in section 6, with the states in section 5.3 and the partial-coverage rules in 6.4. Browser-verify before calling it done.
4. **Documentation corrections (D-7).** Correct the stale statements in section 2.1, as a separate documentation-only change.
5. **Browser verification of the existing pages.** Command Center (as it stands), Hosts, Host detail, Alerts, Investigation, Agents. Use a real Clerk tenant when available.
6. **Host detail alerts section** (section 7.2, item 1). Data exists.
7. **Alert Policies and Integrations UI** on existing APIs.
8. **Telemetry v2:** confirm section 9, specify the validator changes, then implement.
9. **Investigations index** (backend list endpoint, then the nav entry).

Deferred until their prerequisite exists: incidents, anomalies, risk outlook, failure readiness, model performance, dependencies, events from logs, notifications, members and roles, the workspace page (D-9), the public website, Docker Compose deployment, OpenTelemetry.

---

## 11. Decision record and open items

| ID | Question | Status | Decision or recommendation |
|---|---|---|---|
| Q1 | Which existing hosts have trustworthy provenance? | **Approved** | Registry hosts with an operator-set `kind` are trusted. Legacy telemetry without a trustworthy registry record is UNKNOWN. |
| Q2 | Warning hue | **Decided 2026-11-19: Candidate 2, pale violet `#e9b8ff`** (D-13) | Preconditions: Q10 and section 11.1. |
| Q10 | SYNTHETIC badge colour and the violet collision | **Open. Approval needed before the warning colour is implemented.** | The SYNTHETIC badge is violet (`--s4`), the same family as the approved warning. Recommended: draw SYNTHETIC with neutral ink (`#a3b8c8`) and its existing dashed border, so the source is carried by text and shape, not hue. The chart's violet series keeps its colour, since series never carry status. |
| Q11 | Browser verification of any authenticated page | **Blocked** | No real Clerk tenant is available, and no documented browser-test sign-in path exists. Options: a Clerk development instance with a test user, or a clearly labelled local-only test session. Decision needed. |
| Q3 | Shape cues | **Approved** | Warning: triangle. Critical: solid square. Textual labels always kept. |
| Q4 | Alert-to-host join | **Approved as interim** | Partial coverage exposed on capped inputs (6.4). Durable server-side fix in 6.6. |
| Q5 | Activity from alert timestamps | **Approved for v1** | Alert transitions only, with the semantics in 6.5. |
| Q6 | Telemetry v2 | **Approved in principle; contract drafted** | Section 9. Confirm the draft before implementation. |
| Q7 | Correct `PRODUCT.md` and `PRODUCT_DEFINITION.md` | **Approved; not done in this revision** | Separate documentation-only change (section 2.1). |
| Q8 | Host detail layout | **Approved** | One page with anchored sections (D-8). |
| Q9 | Workspace page | **Approved: deferred** | Until its read endpoint exists (D-9). |
| Q10 | SYNTHETIC badge colour | **Open** (see above) | Required before the warning colour is implemented. |
| Q11 | Browser verification | **Blocked** (see above) | Required before any page is called browser-verified. |

### 11.1 Warning hue candidates (for your choice)

Measured against the widget background `--widget` `#0f1d2c` (WCAG 2.x contrast). Colour distances are CIELAB ΔE76. Colour-vision simulation uses Machado et al. 2009, severity 1.0, for protanopia and deuteranopia. The reference colours are the implemented tokens: forecast amber `#f2b64a`, critical text `#ff6a55`, healthy `#35d0b5`, offline `#8aa3b8`.

| Candidate | Hex | Contrast on widget | ΔE to forecast (normal / protan / deutan) | ΔE to critical | ΔE to healthy | ΔE to offline |
|---|---|---|---|---|---|---|
| **1. Pale yellow** | `#fff27a` | **14.8 : 1** | 28 / 18 / 16 | 75 / 49 / 29 | 69 / 56 / 62 | 78 / 79 / 77 |
| **2. Pale violet** | `#e9b8ff` | **10.4 : 1** | 92 / 93 / 88 | 75 / 65 / 74 | 82 / 39 / 27 | 40 / 22 / 21 |
| Current warning (rejected) | `#e0951f` | 6.9 : 1 | 14 / 12 / 11 | 45 / 38 / 21 | 92 / 61 / 68 | 84 / 80 / 81 |

How each candidate stays distinguishable:

* **Candidate 1, pale yellow.** It is a clearly different hue family from the amber forecast, but the nearest in hue. The forecast is a saturated orange-amber; this is a pale, luminous yellow. Its closest pair is the forecast (ΔE 16 to 18 under colour-vision deficiency). Separation there depends on lightness and saturation, the triangle glyph, and the forecast's dashed styling and EXP tag. It is clearly separate from healthy, critical and offline in all three views.
* **Candidate 2, pale violet.** It is far from the forecast (ΔE 88 and above in every view) and from critical. Its weakest pair is offline under deuteranopia (ΔE 21), and healthy under deuteranopia (ΔE 27). Offline has its own text label and grey-blue colour, but the two should not be relied on by colour alone. Violet is also one of the series colours; warning is always shown with its label and triangle, so a series line is never mistaken for a status, but the palette overlap should be accepted deliberately.

Both candidates have contrast above 10:1 on the widget. **Decision (2026-11-19): Candidate 2, pale violet `#e9b8ff`, is chosen.** Under the approved rule the warning colour must not be amber, and this one is not.

Residual risks recorded with the decision (measured, not assumed):
* Against the series violet `#a78bfa` (third chart series, `--s4`): ΔE 29 in normal vision, 28 under protanopia, 31 under deuteranopia. Distinguishable, but in the same hue family.
* Against the SYNTHETIC source badge, which is currently the same violet family: this is the collision that must be removed before the change ships (Q10).
* Against the offline grey-blue: ΔE 21 under deuteranopia (unchanged from the comparison above).
* Mitigations, required by the approved rule: status is always text plus glyph; warning badges use a solid border and never appear as a legend swatch; series lines carry no glyph or status label.

### 11.2 Facts that are still unknown (not assumed)

* Whether any console page has been used in a browser with a real Clerk session. It has not.
* Whether the Linux agent collects correctly on a real host. It has not been run.
* How the console behaves above 200 hosts in a browser. It has not been tested at that size.
* Whether any real host has been labelled SYNTHETIC or REPLAY by the prefix rule. Not checked.
* The exact values of the observed-cadence window and minimum sample count (section 9).

---

## 12. Files

Revision 3 edited `docs/FRONTEND_ARCHITECTURE.md` only. No application code, styling, route, API, schema, migration, dataset, database content, design PDF or visual preview was modified. The warning colour was decided in the document and not implemented. The provenance migration and the Command Center were not started. No browser verification was performed.

Current glyphs in the code, for reference (`frontend/src/components/ui/Badges.tsx`): healthy ●, warning ▲, critical ◆ (to become ■, C12), stale ◐, offline ○, unknown ?.
