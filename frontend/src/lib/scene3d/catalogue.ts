/**
 * The component catalogue: every model type the 3D scene knows how to load, place and connect.
 *
 * This is a list of available models, NOT a list of what any customer runs. Nothing here is shown as a customer resource
 * unless an inventory entity is bound to it (see ENTITY_BINDING and sceneModel.ts).
 *
 * Convention: a model's origin sits at the centre of its base, +Y is up and the front faces +Z. One metre is one unit.
 */
import type { Category, ComponentDefinition, ConnectionPort, MetadataField, RelationType, SceneState, Vec3 } from "./types";
import { SCENE_STATES } from "./types";

export const ASSET_ROOT = "/assets/infrastructure";

/** Model file path for a component: one file per type, shared by every instance. */
export const assetPathFor = (category: Category, componentType: string) => `${ASSET_ROOT}/${category}/${componentType}.glb`;

/** Six named ports on a box-shaped model whose origin is the centre of its base. */
export function boxPorts([w, h, d]: Vec3): ConnectionPort[] {
  return [
    { name: "top", offset: [0, h, 0], normal: [0, 1, 0] },
    { name: "bottom", offset: [0, 0, 0], normal: [0, -1, 0] },
    { name: "front", offset: [0, h / 2, d / 2], normal: [0, 0, 1] },
    { name: "back", offset: [0, h / 2, -d / 2], normal: [0, 0, -1] },
    { name: "left", offset: [-w / 2, h / 2, 0], normal: [-1, 0, 0] },
    { name: "right", offset: [w / 2, h / 2, 0], normal: [1, 0, 0] },
  ];
}

/** Ports on a flat scope plate (environment, region, cluster): its surface centre and its four edges. */
const PLATE: readonly ConnectionPort[] = [
  { name: "surface", offset: [0, 0, 0], normal: [0, 1, 0] },
  { name: "edge_front", offset: [0, 0, 0.5], normal: [0, 0, 1] },
  { name: "edge_back", offset: [0, 0, -0.5], normal: [0, 0, -1] },
  { name: "edge_left", offset: [-0.5, 0, 0], normal: [-1, 0, 0] },
  { name: "edge_right", offset: [0.5, 0, 0], normal: [1, 0, 0] },
];
/** Scopes use plate ports and are drawn by the layout as plates sized to their contents, not from a model file. */
export const platePorts = (): readonly ConnectionPort[] => PLATE;

type Compat = ComponentDefinition["compatible_connection_types"];
const R = (...r: RelationType[]) => r;

/** Relationship roles by family. A pair is valid only if the source allows the type as_source and the target as_target. */
export const COMPAT = {
  scope: { as_source: R("CONTAINS"), as_target: R("CONTAINS") },
  computeHost: {
    as_source: R("CONNECTS_TO", "SENDS_TELEMETRY_TO", "DEPENDS_ON"),
    as_target: R("CONTAINS", "RUNS_ON", "CONNECTS_TO", "ROUTES_TO"),
  },
  workload: {
    as_source: R("RUNS_ON", "CONNECTS_TO", "DEPENDS_ON", "SENDS_TELEMETRY_TO"),
    as_target: R("CONTAINS", "RUNS_ON", "CONNECTS_TO", "ROUTES_TO", "DEPENDS_ON"),
  },
  network: {
    as_source: R("ROUTES_TO", "CONNECTS_TO", "SENDS_TELEMETRY_TO", "RUNS_ON"),
    as_target: R("CONTAINS", "CONNECTS_TO", "ROUTES_TO", "DEPENDS_ON"),
  },
  networkScope: { as_source: R("CONTAINS"), as_target: R("CONTAINS") },
  data: {
    as_source: R("RUNS_ON", "CONNECTS_TO", "SENDS_TELEMETRY_TO", "DEPENDS_ON"),
    as_target: R("CONTAINS", "READS_FROM", "WRITES_TO", "CONNECTS_TO", "DEPENDS_ON"),
  },
  application: {
    as_source: R("RUNS_ON", "CONNECTS_TO", "DEPENDS_ON", "READS_FROM", "WRITES_TO", "PUBLISHES_TO", "CONSUMES_FROM", "SENDS_TELEMETRY_TO"),
    as_target: R("CONTAINS", "CONNECTS_TO", "ROUTES_TO", "DEPENDS_ON"),
  },
  broker: {
    as_source: R("RUNS_ON", "CONNECTS_TO", "SENDS_TELEMETRY_TO"),
    as_target: R("CONTAINS", "PUBLISHES_TO", "CONSUMES_FROM", "CONNECTS_TO", "DEPENDS_ON"),
  },
  producer: { as_source: R("PUBLISHES_TO", "RUNS_ON", "SENDS_TELEMETRY_TO", "DEPENDS_ON"), as_target: R("CONTAINS", "DEPENDS_ON") },
  consumer: { as_source: R("CONSUMES_FROM", "RUNS_ON", "SENDS_TELEMETRY_TO", "DEPENDS_ON"), as_target: R("CONTAINS", "DEPENDS_ON") },
  processor: {
    as_source: R("CONSUMES_FROM", "PUBLISHES_TO", "RUNS_ON", "SENDS_TELEMETRY_TO", "DEPENDS_ON"),
    as_target: R("CONTAINS", "DEPENDS_ON"),
  },
  collector: {
    as_source: R("RUNS_ON", "SENDS_TELEMETRY_TO", "READS_FROM", "CONNECTS_TO"),
    as_target: R("CONTAINS", "SENDS_TELEMETRY_TO", "CONNECTS_TO", "DEPENDS_ON"),
  },
  agent: { as_source: R("SENDS_TELEMETRY_TO", "RUNS_ON"), as_target: R("CONTAINS") },
  probe: { as_source: R("CONNECTS_TO", "SENDS_TELEMETRY_TO", "RUNS_ON"), as_target: R("CONTAINS") },
  external: { as_source: R("SENDS_TELEMETRY_TO"), as_target: R("CONNECTS_TO", "ROUTES_TO", "DEPENDS_ON", "READS_FROM", "WRITES_TO") },
} satisfies Record<string, Compat>;

const STR = (label: string): MetadataField => ({ type: "string", label });
const NUM = (label: string): MetadataField => ({ type: "number", label });

interface DefInput {
  type: string;
  name: string;
  compat: Compat;
  footprint?: Vec3;
  ports?: readonly ConnectionPort[];
  schema?: Record<string, MetadataField>;
  states?: readonly SceneState[];
  availability?: ComponentDefinition["availability"];
  scale?: Vec3;
  rotation?: Vec3;
  position?: Vec3;
}

function family(category: Category, defaults: { footprint: Vec3 }, items: DefInput[]): ComponentDefinition[] {
  return items.map((i) => {
    const footprint = i.footprint ?? defaults.footprint;
    return {
      component_type: i.type,
      component_id: `${category}.${i.type}`,
      display_name: i.name,
      category,
      asset_path: assetPathFor(category, i.type),
      default_scale: i.scale ?? [1, 1, 1],
      default_rotation: i.rotation ?? [0, 0, 0],
      default_position: i.position ?? [0, 0, 0],
      supported_states: i.states ?? SCENE_STATES,
      connection_ports: i.ports ?? boxPorts(footprint),
      compatible_connection_types: i.compat,
      metadata_schema: i.schema ?? {},
      availability: i.availability ?? "catalogue",
      rendering: i.ports === PLATE ? "layout-plate" : "model",
      footprint,
    };
  });
}

const HOST_SCHEMA = { host_id: STR("Host ID"), hostname: STR("Hostname"), agent_id: STR("Agent ID"), source: STR("Data source") };
const SCOPE_STATES: readonly SceneState[] = []; // Scopes have no health of their own; group figures are aggregates.

export const CATALOGUE: readonly ComponentDefinition[] = [
  ...family("scope", { footprint: [1, 0.12, 1] }, [
    { type: "environment", name: "Environment", compat: COMPAT.scope, ports: platePorts(), states: SCOPE_STATES, availability: "active", schema: { name: STR("Name") } },
    { type: "cluster", name: "Cluster", compat: COMPAT.scope, ports: platePorts(), states: SCOPE_STATES, availability: "active", schema: { name: STR("Name") } },
    { type: "unassigned-group", name: "Unassigned group", compat: COMPAT.scope, ports: platePorts(), states: SCOPE_STATES, availability: "active", schema: { name: STR("Name") } },
  ]),

  ...family("compute", { footprint: [1.4, 0.7, 1.0] }, [
    { type: "physical-server", name: "Physical server", compat: COMPAT.computeHost, availability: "active", schema: HOST_SCHEMA },
    { type: "server-rack", name: "Server rack", compat: COMPAT.computeHost, footprint: [0.8, 2.0, 1.0], schema: { rack_id: STR("Rack ID"), units: NUM("Rack units") } },
    { type: "virtual-machine", name: "Virtual machine", compat: COMPAT.computeHost, footprint: [1.0, 0.8, 1.0], schema: { ...HOST_SCHEMA, hypervisor: STR("Hypervisor") } },
    { type: "bare-metal-server", name: "Bare-metal server", compat: COMPAT.computeHost, schema: HOST_SCHEMA },
    { type: "gpu-server", name: "GPU server", compat: COMPAT.computeHost, footprint: [1.4, 0.9, 1.1], schema: { ...HOST_SCHEMA, gpu_count: NUM("GPUs") } },
    { type: "compute-node", name: "Compute node", compat: COMPAT.computeHost, schema: HOST_SCHEMA },
    { type: "worker-node", name: "Worker node", compat: COMPAT.computeHost, schema: HOST_SCHEMA },
    { type: "k8s-control-plane-node", name: "Kubernetes control-plane node", compat: COMPAT.computeHost, schema: { ...HOST_SCHEMA, k8s_version: STR("Kubernetes version") } },
    { type: "k8s-worker-node", name: "Kubernetes worker node", compat: COMPAT.computeHost, schema: { ...HOST_SCHEMA, node_pool: STR("Node pool") } },
    { type: "container", name: "Container", compat: COMPAT.workload, footprint: [0.6, 0.6, 0.6], schema: { image: STR("Image") } },
    { type: "pod", name: "Pod", compat: COMPAT.workload, footprint: [0.9, 0.9, 0.9], schema: { namespace: STR("Namespace") } },
    { type: "docker-host", name: "Docker host", compat: COMPAT.computeHost, schema: HOST_SCHEMA },
    { type: "edge-node", name: "Edge node", compat: COMPAT.computeHost, footprint: [0.8, 0.5, 0.8], schema: { ...HOST_SCHEMA, location: STR("Location") } },
  ]),

  ...family("networking", { footprint: [1.4, 0.35, 0.9] }, [
    { type: "router", name: "Router", compat: COMPAT.network },
    { type: "network-switch", name: "Network switch", compat: COMPAT.network, footprint: [1.6, 0.25, 0.8], schema: { ports: NUM("Ports") } },
    { type: "firewall", name: "Firewall", compat: COMPAT.network, footprint: [1.2, 0.9, 0.3] },
    { type: "load-balancer", name: "Load balancer", compat: COMPAT.network, schema: { algorithm: STR("Algorithm") } },
    { type: "api-gateway", name: "API gateway", compat: COMPAT.network },
    { type: "internet-gateway", name: "Internet gateway", compat: COMPAT.network },
    { type: "nat-gateway", name: "NAT gateway", compat: COMPAT.network },
    { type: "vpn-gateway", name: "VPN gateway", compat: COMPAT.network },
    { type: "dns-server", name: "DNS server", compat: COMPAT.network },
    { type: "cdn", name: "CDN", compat: COMPAT.network },
    { type: "reverse-proxy", name: "Reverse proxy", compat: COMPAT.network },
    { type: "waf", name: "WAF", compat: COMPAT.network },
    { type: "vpc", name: "VPC", compat: COMPAT.networkScope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES, schema: { cidr: STR("CIDR") } },
    { type: "subnet", name: "Subnet", compat: COMPAT.networkScope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES, schema: { cidr: STR("CIDR") } },
    { type: "availability-zone", name: "Availability zone", compat: COMPAT.networkScope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES },
    { type: "region", name: "Region", compat: COMPAT.networkScope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES, availability: "active", schema: { name: STR("Name") } },
  ]),

  ...family("data", { footprint: [1.0, 1.1, 1.0] }, [
    { type: "postgresql", name: "PostgreSQL", compat: COMPAT.data, schema: { version: STR("Version") } },
    { type: "mysql", name: "MySQL", compat: COMPAT.data, schema: { version: STR("Version") } },
    { type: "mongodb", name: "MongoDB", compat: COMPAT.data, schema: { version: STR("Version") } },
    { type: "redis", name: "Redis", compat: COMPAT.data, schema: { version: STR("Version") } },
    { type: "cassandra", name: "Cassandra", compat: COMPAT.data },
    { type: "elasticsearch", name: "Elasticsearch", compat: COMPAT.data },
    { type: "clickhouse", name: "ClickHouse", compat: COMPAT.data },
    { type: "database-cluster", name: "Database cluster", compat: { ...COMPAT.data, as_source: R(...COMPAT.data.as_source, "CONTAINS") }, footprint: [2.0, 1.2, 1.4] },
    { type: "database-replica", name: "Database replica", compat: COMPAT.data, schema: { replica_of: STR("Replica of") } },
    { type: "object-storage", name: "Object storage", compat: COMPAT.data, footprint: [1.2, 0.9, 1.2] },
    { type: "block-storage", name: "Block storage", compat: COMPAT.data, footprint: [1.0, 0.5, 1.0] },
    { type: "file-storage", name: "File storage", compat: COMPAT.data },
    { type: "backup-storage", name: "Backup storage", compat: COMPAT.data },
  ]),

  ...family("application", { footprint: [1.0, 1.0, 1.0] }, [
    { type: "frontend-application", name: "Frontend application", compat: COMPAT.application },
    { type: "backend-application", name: "Backend application", compat: COMPAT.application },
    { type: "rest-api", name: "REST API", compat: COMPAT.application },
    { type: "graphql-api", name: "GraphQL API", compat: COMPAT.application },
    { type: "authentication-service", name: "Authentication service", compat: COMPAT.application },
    { type: "payment-service", name: "Payment service", compat: COMPAT.application },
    { type: "notification-service", name: "Notification service", compat: COMPAT.application },
    { type: "search-service", name: "Search service", compat: COMPAT.application },
    { type: "microservice", name: "Microservice", compat: COMPAT.application, availability: "active", schema: { name: STR("Name"), description: STR("Description") } },
    { type: "monolith", name: "Monolith", compat: COMPAT.application, footprint: [1.6, 1.4, 1.6] },
    { type: "scheduler", name: "Scheduler", compat: COMPAT.application },
    { type: "background-worker", name: "Background worker", compat: COMPAT.application },
    { type: "websocket-service", name: "WebSocket service", compat: COMPAT.application },
    { type: "grpc-service", name: "gRPC service", compat: COMPAT.application },
  ]),

  ...family("messaging", { footprint: [1.4, 0.8, 0.8] }, [
    { type: "kafka-broker", name: "Kafka broker", compat: COMPAT.broker },
    { type: "kafka-cluster", name: "Kafka cluster", compat: { ...COMPAT.broker, as_source: R(...COMPAT.broker.as_source, "CONTAINS") }, footprint: [2.2, 0.9, 1.2] },
    { type: "rabbitmq", name: "RabbitMQ", compat: COMPAT.broker },
    { type: "message-queue", name: "Message queue", compat: COMPAT.broker },
    { type: "event-bus", name: "Event bus", compat: COMPAT.broker, footprint: [2.4, 0.4, 0.6] },
    { type: "event-producer", name: "Event producer", compat: COMPAT.producer, footprint: [0.8, 0.8, 0.8] },
    { type: "event-consumer", name: "Event consumer", compat: COMPAT.consumer, footprint: [0.8, 0.8, 0.8] },
    { type: "stream-processor", name: "Stream processor", compat: COMPAT.processor },
    { type: "dead-letter-queue", name: "Dead-letter queue", compat: COMPAT.broker },
    { type: "pubsub", name: "Pub/Sub", compat: COMPAT.broker },
  ]),

  ...family("observability", { footprint: [1.0, 0.9, 1.0] }, [
    { type: "uptimetrace-agent", name: "UptimeTrace Agent", compat: COMPAT.agent, footprint: [0.5, 0.5, 0.5], schema: { agent_id: STR("Agent ID") } },
    { type: "metrics-collector", name: "Metrics collector", compat: COMPAT.collector },
    { type: "otel-collector", name: "OpenTelemetry Collector", compat: COMPAT.collector },
    { type: "prometheus", name: "Prometheus", compat: COMPAT.collector },
    { type: "grafana", name: "Grafana", compat: COMPAT.collector },
    { type: "jaeger", name: "Jaeger", compat: COMPAT.collector },
    { type: "tempo", name: "Tempo", compat: COMPAT.collector },
    { type: "loki", name: "Loki", compat: COMPAT.collector },
    { type: "log-aggregator", name: "Log aggregator", compat: COMPAT.collector },
    { type: "alert-manager", name: "Alert manager", compat: COMPAT.collector },
    { type: "trace-collector", name: "Trace collector", compat: COMPAT.collector },
    { type: "synthetic-monitor", name: "Synthetic monitor", compat: COMPAT.probe },
    { type: "failure-experiment-runner", name: "Failure experiment runner", compat: COMPAT.probe },
  ]),

  ...family("cloud", { footprint: [1.2, 0.8, 1.0] }, [
    { type: "aws-region", name: "AWS region", compat: COMPAT.scope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES },
    { type: "aws-availability-zone", name: "AWS availability zone", compat: COMPAT.scope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES },
    { type: "ec2-instance", name: "EC2 instance", compat: COMPAT.computeHost, schema: { instance_id: STR("Instance ID") } },
    { type: "ecs-cluster", name: "ECS cluster", compat: COMPAT.scope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES },
    { type: "eks-cluster", name: "EKS cluster", compat: COMPAT.scope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES },
    { type: "rds-database", name: "RDS database", compat: COMPAT.data, footprint: [1.0, 1.1, 1.0] },
    { type: "s3-storage", name: "S3 storage", compat: COMPAT.data, footprint: [1.2, 0.9, 1.2] },
    { type: "lambda-function", name: "Lambda function", compat: COMPAT.application, footprint: [0.8, 0.8, 0.8] },
    { type: "azure-vm", name: "Azure VM", compat: COMPAT.computeHost },
    { type: "azure-kubernetes-service", name: "Azure Kubernetes Service", compat: COMPAT.scope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES },
    { type: "google-compute-engine", name: "Google Compute Engine", compat: COMPAT.computeHost },
    { type: "google-kubernetes-engine", name: "Google Kubernetes Engine", compat: COMPAT.scope, footprint: [1, 0.12, 1], ports: platePorts(), states: SCOPE_STATES },
    { type: "cloudflare", name: "Cloudflare", compat: COMPAT.external },
    { type: "external-saas", name: "External SaaS", compat: COMPAT.external },
    { type: "third-party-api", name: "Third-party API", compat: COMPAT.external },
  ]),
];

/**
 * Which model represents each kind of real inventory entity. Changing a binding changes the model, never the data:
 * every instance keeps its own entity id, health and alerts. A host bound to "physical-server" is drawn with that model
 * because no host kind is recorded; it is labelled "Host", not "Physical server".
 */
export const ENTITY_BINDING = {
  environment: "environment",
  region: "region",
  cluster: "cluster",
  host: "physical-server",
  service: "microservice",
  group: "unassigned-group",
} as const satisfies Record<import("./types").EntityKind, string>;
