# 3D infrastructure view

Status: foundation implemented. `/infrastructure?view=3d` (the **3D view** tab, between Topology and Manage) draws the existing inventory, host health and active alerts as a 3D scene. It reads the same data as the Topology tab and adds no API, schema or backend change.

## Three separate things

| Layer | Where | What it is |
|---|---|---|
| Component registry | `frontend/src/lib/scene3d/catalogue.ts`, `registry.ts` | Which models exist and how to place and connect them. Not customer data. |
| Infrastructure inventory | `GET /api/v1/infrastructure`, hosts API, alerts API | What the customer configured. The only source of scene entities. |
| Scene | `frontend/src/lib/scene3d/sceneModel.ts`, `layout.ts` | The two combined: every entity is a real inventory entity drawn with a registry model. |

A model is never shown because it exists in the registry. An empty inventory gives an empty scene. The scene is a pure function of the inventory, monitoring records, active alerts and the collapse state, so it can always be reproduced from them.

Rendering lives in `frontend/src/features/infrastructure/scene3d/` (React Three Fiber and three.js, loaded on demand so they are not in the main bundle). It receives a finished `Scene` and makes no data decisions.

## Component registry

Each `ComponentDefinition` (`lib/scene3d/types.ts`) carries:

| Field | Meaning |
|---|---|
| `component_type` | Lowercase slug, unique. Also the model's file name. |
| `component_id` | `<category>.<component_type>` |
| `display_name`, `category` | Category is one of `scope`, `compute`, `networking`, `data`, `application`, `messaging`, `observability`, `cloud`. |
| `asset_path` | `/assets/infrastructure/<category>/<component_type>.glb` |
| `default_scale`, `default_rotation`, `default_position` | Applied to every instance. Rotation is XYZ Euler in radians. |
| `supported_states` | Health states the model can show. Scopes have none: a group has no health of its own. |
| `connection_ports` | Named anchors: `name`, `offset` (model space), `normal` (direction lines leave in). |
| `compatible_connection_types` | `as_source` and `as_target` relation lists. A connection is valid only if both ends allow it. |
| `metadata_schema` | Field names and types the model can display. |
| `availability` | `active`: bound to an entity kind UptimeTrace has data for. `catalogue`: loadable, never shown until real data exists. |
| `rendering` | `model`: drawn from the GLB. `layout-plate`: a scope drawn as a plate sized to its contents; no file is loaded. |
| `footprint` | Approximate size after default scale; used for layout, the hit area and placeholder sizing. |

The registry validates itself when the app loads (unique ids, path convention, known relations, unique port names, bindings). An invalid registry throws.

**Entity bindings** (`ENTITY_BINDING` in `catalogue.ts`) choose the model for each real entity kind:

| Entity | Model | Rendering |
|---|---|---|
| Environment | `environment` | layout plate |
| Region | `region` | layout plate |
| Cluster | `cluster` | layout plate |
| Unassigned group | `unassigned-group` | layout plate, dashed outline |
| Host | `physical-server` | model |
| Service | `microservice` | model |

Hosts use the physical-server model because no host kind is recorded. They are labelled "Host", never "Physical server". The catalogue has 97 types: the 94 from the product brief plus the environment, cluster and unassigned-group scopes. The 84 model-rendered types all load and render, and the 13 scope types are drawn as plates. They become customer resources only when an integration supplies real entities and an entity binding is added.

## Supplying models

**Format:** glTF binary (`.glb`), one file per component type, shared by every instance.

**Location:** `frontend/public/assets/infrastructure/<category>/<component_type>.glb`. The exact path for each type is its `asset_path`, and the 3D view lists every missing file it needs.

**Conventions:**

* Units are metres. Origin at the centre of the base, +Y up, front facing +Z.
* Keep the model close to the registry `footprint` (physical server: 1.4 × 0.7 × 1.0). Change `footprint` if the model is a different size.
* Use one or a few materials. Do not bake health colours, labels or text into the model: health, alerts and data source are overlays.
* Anchors are empty nodes (Blender: Empty) named `anchor_<port>`, for example `anchor_top`, `anchor_bottom`, `anchor_front`. They are hidden at render time.
* Draco or meshopt compression is not configured. Ship uncompressed GLB, or add the decoder to `lib/scene3d/modelAssets.ts` first.

Ports the current relationships use:

| Model | Port | Used by |
|---|---|---|
| `physical-server` | `top` | Target of RUNS_ON (a service line ends here) |
| `microservice` | `bottom` | Source of RUNS_ON (a service line starts here) |

Other declared ports (`front`, `back`, `left`, `right`) exist for future relationship types.

## Container names

A cluster, region, environment or unassigned group shows its name flat on its top surface, in the band its back edge reserves above its children (`LABEL_TOP`, `LABEL_HEIGHT` and `LABEL_PAD` in `lib/scene3d/layout.ts`). The name is cut short with an ellipsis when it would run past the plate, and the full name is in the tooltip and details panel. A collapsed container shows its host count on a stack placed at its front edge, so the count never covers the name. Names are drawn under health and alert overlays. `layout.test.ts` checks that each name stays on its own plate and that nested names never share a band.

## Connection anchors

`resolveAnchor` (`lib/scene3d/connections.ts`) resolves a port name in this order:

1. `anchor_<name>` in the supplied model (origin `model`);
2. the registry port offset (origin `registry`);
3. a safe fallback: the top centre of the footprint (origin `fallback`).

A connection therefore always has an end point, and a supplied model can move it without code changes. Anchor offsets go through the instance's scale, rotation and position (`applyTransform`).

## One model, many instances

`modelCache` (`lib/scene3d/modelAssets.ts`) loads each URL once per session. Concurrent requests share one promise. A missing file is remembered, so 500 hosts make one failed request, not 500. Each instance is a clone (`SkeletonUtils.clone`) that shares the template's geometry and materials. Only the transform and `userData.entityId` differ. Verified in a browser: 14 hosts and 4 services requested `physical-server.glb` and `microservice.glb` exactly once each.

## Relationships

Ten typed relationships are defined: CONTAINS, RUNS_ON, CONNECTS_TO, ROUTES_TO, READS_FROM, WRITES_TO, PUBLISHES_TO, CONSUMES_FROM, DEPENDS_ON, SENDS_TELEMETRY_TO. Each connection has a source and target entity id, a type, source and target anchors, an optional direction and metadata, and its evidence.

The scene creates only two kinds today, both from the inventory:

* **CONTAINS**: saved containment (environment > region > cluster > host). Drawn as nested plates, not lines. Virtual "unassigned" groups contain nothing in the inventory, so they record no containment.
* **RUNS_ON**: a service-to-host assignment saved in Infrastructure. Its evidence states that it is not a verified or discovered dependency.

Nothing is inferred from position, shared clusters or network reachability. Hosts are never linked to each other. Each type has a configurable style (`RELATION_STYLES`) with a dash pattern as its non-colour cue. Amber is not used because it is reserved for forecasts.

When a container is collapsed, lines to its hidden hosts end at the topmost collapsed container and are merged into one line. The underlying connections are unchanged.

## Health, alerts and provenance

All are overlays (`lib/scene3d/overlays.ts`). The model itself is never recoloured, except the status LED on development placeholders.

| Overlay | Shows |
|---|---|
| Ring colour and label | The host's backend `health.status`, with the same glyphs as the badges: ● healthy, ▲ warning, ■ critical, ◐ stale, ○ offline, ? unknown. A host with no loaded monitoring record is "– NO DATA LOADED", never healthy. Critical pulses fast, warning slowly. |
| Ring pattern and tag | Data source: solid LIVE, dashed SYNTHETIC, dotted REPLAY. SYNTHETIC and REPLAY hosts always show their tag. |
| Beam and badge | Only on a host with an active alert: severity glyph and count. The details panel lists each alert's rule, metric, value and limit, with "Open alert" to the existing investigation page (`/alerts/:id`). |
| Group figures | The hover tooltip and details panel give "Aggregate of N hosts: critical 1 · healthy 3" for a cluster, region or environment; the plate itself shows only its name. A cluster, region, environment or service is never given a health state, and a host alert never marks its group as failed. |

The partial-coverage banners are the ones the Topology tab uses (`CoverageBanners.tsx`).

## Interaction and performance

Orbit, zoom and pan; hover tooltips; click to select, which moves the camera to the item and uses the existing Topology details panel plus a "Scene object" panel; double-click a group to collapse or expand it; "Select in scene" for keyboard users; Expand all, Collapse all and Reset view. Reduced-motion preferences turn off the entrance and pulse animations.

Large hierarchies (over 60 hosts) start collapsed, as on the Topology tab. Materials and geometries are shared, and the pulse animations update shared materials once per frame instead of once per object. The 2,000-host and 200-alert caps of the Topology tab apply.

Without WebGL the tab says so and links to the Topology tab, which shows the same data as an accessible list.

## What works without supplied models

Everything except the final artwork. Host and service models render as **development placeholders**: procedural shapes with a cyan outline. The view lists each missing file with the number of objects using it. When a file is added, it replaces the placeholder on every instance after a reload.

## Not in scope

No discovery, cloud or Kubernetes integration, dependency inference, group health derivation or ML. No change to monitoring rules, alert evaluation, investigation logic, the infrastructure schema or the API. Catalogue types other than the six bindings are not shown as customer resources.
