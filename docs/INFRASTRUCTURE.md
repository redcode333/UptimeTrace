# Infrastructure inventory

Status: foundation implemented (grouping only). This document describes what exists, how it is authorised, and what it deliberately does not do.

## Model

| Entity | Parent | Notes |
|---|---|---|
| Environment | none | Workspace-unique name. |
| Region | Environment (optional) | Unique name within its environment (unassigned regions are unique among themselves). |
| Cluster | Region (optional) | Unique name within its region. |
| Host | Cluster (optional) | Single `hosts.cluster_id`. Existing hosts have none. |
| Service | none | Workspace-unique name; optional description. |
| Service–Host link | Service and Host | Many to many; one row per pair. |

Environment → Region → Cluster → Host is a containment hierarchy. Services are a separate many-to-many relationship with hosts.

## Authorisation and safety

- Every endpoint sits behind the browser session gate (`authorize`). The workspace is resolved on the server; no request accepts a workspace id.
- Every referenced row is loaded with its workspace filter, so a foreign id is answered as "not found".
- Migration 0008 uses composite foreign keys `(workspace_id, id)` so the database rejects a cross-workspace reference even if the application attempted one. This is tested by writing the reference directly through the restricted application role.
- The application role receives only the grants it needs. It cannot change host identity fields or delete hosts (tested).
- Deleting a grouping sets references to NULL or removes only link rows. It never deletes hosts, telemetry, alerts or investigation records (tested).
- Every change writes an audit event.

## API

`GET /api/v1/infrastructure` returns the hierarchy, `ungrouped_hosts`, `unassigned_regions`, `unassigned_clusters` and `unassigned_services`.

Writes: `POST|PATCH|DELETE` on `/environments`, `/regions`, `/clusters`, `/services`; `POST|DELETE /clusters/{id}/hosts[/{host_id}]`; `POST|DELETE /services/{id}/hosts[/{host_id}]`.

Updates distinguish an omitted field (unchanged) from an explicit `null` (detach).

Conflict codes: `name_taken`, `already_in_cluster`, `host_in_other_cluster`, `already_linked`, `host_deactivated`, `not_in_cluster`, `not_linked`, `invalid_reference`. Missing or foreign ids return 404 with a specific `*_not_found` code.

## Frontend

`/infrastructure`, linked from the Hosts page header. Not in the sidebar. Grouped hosts show under their hierarchy; hosts not in a cluster, unassigned regions and clusters, and services are listed in their own sections. Each change is confirmed by the server before the page refreshes. Deletes state what is detached and what is kept.

## Topology view (read-only)

`/infrastructure` opens on a **Topology** tab; **Manage** holds the editor. The topology reads the inventory, the paged host list (health, reporting, source) and the active alert list. It derives nothing else.

- Containment: Environment > Region > Cluster > Host, with Hosts not in a cluster, unassigned regions and unassigned clusters shown as groups.
- Services sit in their own column. Relationship lines join a service to each host it was assigned to in the inventory. Two hosts in the same cluster are not linked, and no dependency is inferred.
- Host health is the backend's `health.status`. A host with no loaded monitoring record shows "no data loaded", never a health state.
- Alert indicators appear only on hosts with an active alert.
- Groups show counts by actual host state and their active alert count. They have no health status of their own.
- Selecting an item shows its details; each host links to its existing Host Detail page.
- Large hierarchies (over 60 hosts) start with clusters and host groups collapsed. Lines to a collapsed container are drawn to that container.
- Relationship lines are drawn at widths of 720px and above; narrower screens list the hosts on each service card instead.

Limits: at most 2,000 hosts (10 pages of 200) are loaded for the topology, and the alert list is capped at 200. Banners state when either is incomplete.

## Not in scope

No service discovery, dependency inference, cloud or Kubernetes integrations, OpenTelemetry, alert propagation through groups, or changes to the telemetry contract or alert evaluation.

## 3D view

`/infrastructure?view=3d` draws the same inventory, health and alerts as a 3D scene, with a reusable component registry for models. See `docs/3D_INFRASTRUCTURE.md`.

## Future Infrastructure Map readiness

The data model already exposes the containment hierarchy and the service–host relationships a map would need. A map would still need layout, a visual component and decisions about how group-level state is derived.
