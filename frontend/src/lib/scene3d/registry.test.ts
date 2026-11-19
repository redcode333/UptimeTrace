import { describe, expect, it } from "vitest";
import { CATALOGUE, ENTITY_BINDING } from "./catalogue";
import { createRegistry, joinBase, registry, validateDefinitions } from "./registry";
import type { ComponentDefinition } from "./types";

/** Every type named in the product brief, by display name. */
const BRIEF: Record<string, string[]> = {
  compute: ["Physical server", "Server rack", "Virtual machine", "Bare-metal server", "GPU server", "Compute node", "Worker node", "Kubernetes control-plane node", "Kubernetes worker node", "Container", "Pod", "Docker host", "Edge node"],
  networking: ["Router", "Network switch", "Firewall", "Load balancer", "API gateway", "Internet gateway", "NAT gateway", "VPN gateway", "DNS server", "CDN", "Reverse proxy", "WAF", "VPC", "Subnet", "Availability zone", "Region"],
  data: ["PostgreSQL", "MySQL", "MongoDB", "Redis", "Cassandra", "Elasticsearch", "ClickHouse", "Database cluster", "Database replica", "Object storage", "Block storage", "File storage", "Backup storage"],
  application: ["Frontend application", "Backend application", "REST API", "GraphQL API", "Authentication service", "Payment service", "Notification service", "Search service", "Microservice", "Monolith", "Scheduler", "Background worker", "WebSocket service", "gRPC service"],
  messaging: ["Kafka broker", "Kafka cluster", "RabbitMQ", "Message queue", "Event bus", "Event producer", "Event consumer", "Stream processor", "Dead-letter queue", "Pub/Sub"],
  observability: ["UptimeTrace Agent", "Metrics collector", "OpenTelemetry Collector", "Prometheus", "Grafana", "Jaeger", "Tempo", "Loki", "Log aggregator", "Alert manager", "Trace collector", "Synthetic monitor", "Failure experiment runner"],
  cloud: ["AWS region", "AWS availability zone", "EC2 instance", "ECS cluster", "EKS cluster", "RDS database", "S3 storage", "Lambda function", "Azure VM", "Azure Kubernetes Service", "Google Compute Engine", "Google Kubernetes Engine", "Cloudflare", "External SaaS", "Third-party API"],
};

describe("component registry", () => {
  it("is valid as shipped", () => {
    expect(validateDefinitions(CATALOGUE)).toEqual([]);
  });

  it.each(Object.entries(BRIEF))("registers every %s type from the brief under that category", (category, names) => {
    const got = registry.byCategory(category as ComponentDefinition["category"]).map((d) => d.display_name);
    for (const n of names) expect(got).toContain(n);
  });

  it("looks components up by type and by id, and reports unknown types", () => {
    const d = registry.get("postgresql")!;
    expect(d.display_name).toBe("PostgreSQL");
    expect(d.component_id).toBe("data.postgresql");
    expect(registry.getById("data.postgresql")).toBe(d);
    expect(registry.get("mainframe")).toBeUndefined();
    expect(() => registry.require("mainframe")).toThrow("Unknown component type: mainframe");
  });

  it("carries the full asset contract on every definition", () => {
    for (const d of registry.all()) {
      expect(d).toEqual(expect.objectContaining({
        component_type: expect.any(String), component_id: expect.any(String), display_name: expect.any(String), category: expect.any(String),
        asset_path: expect.any(String), default_scale: expect.any(Array), default_rotation: expect.any(Array), default_position: expect.any(Array),
        supported_states: expect.any(Array), connection_ports: expect.any(Array), compatible_connection_types: expect.any(Object), metadata_schema: expect.any(Object),
      }));
    }
  });

  it("binds only the five inventory entity kinds (plus the unassigned group) to active models; everything else is catalogue-only", () => {
    const active = registry.all().filter((d) => d.availability === "active").map((d) => d.component_type).sort();
    expect(active).toEqual(Object.values(ENTITY_BINDING).sort());
    expect(registry.forEntity("host").component_type).toBe("physical-server");
    expect(registry.forEntity("service").component_type).toBe("microservice");
    expect(registry.get("kafka-broker")!.availability).toBe("catalogue");
  });

  it("marks scopes as layout-drawn plates and everything else as model-rendered", () => {
    for (const t of ["environment", "region", "cluster", "unassigned-group", "vpc", "subnet", "eks-cluster"]) expect(registry.require(t).rendering).toBe("layout-plate");
    for (const t of ["physical-server", "microservice", "postgresql", "kafka-broker", "ec2-instance"]) expect(registry.require(t).rendering).toBe("model");
  });

  it("gives scope models no health states of their own", () => {
    for (const t of ["environment", "region", "cluster", "unassigned-group"]) expect(registry.require(t).supported_states).toEqual([]);
    expect(registry.require("physical-server").supported_states).toContain("critical");
  });
});

describe("asset path resolution", () => {
  it("follows /assets/infrastructure/<category>/<component_type>.glb", () => {
    expect(registry.require("physical-server").asset_path).toBe("/assets/infrastructure/compute/physical-server.glb");
    expect(registry.require("kafka-broker").asset_path).toBe("/assets/infrastructure/messaging/kafka-broker.glb");
    for (const d of registry.all()) expect(d.asset_path).toBe(`/assets/infrastructure/${d.category}/${d.component_type}.glb`);
  });

  it("resolves under the app base path", () => {
    expect(registry.assetUrl("redis")).toBe("/assets/infrastructure/data/redis.glb");
    expect(registry.assetUrl("redis", "/console/")).toBe("/console/assets/infrastructure/data/redis.glb");
    expect(joinBase("https://cdn.example.com/app", "/assets/x.glb")).toBe("https://cdn.example.com/app/assets/x.glb");
  });

  it("uses one model file per type, so instances share it", () => {
    const paths = registry.all().map((d) => d.asset_path);
    expect(new Set(paths).size).toBe(paths.length);
  });
});

describe("registry validation", () => {
  const base = registry.require("redis");

  it("rejects duplicates, bad slugs, bad paths, unknown relations and duplicate ports", () => {
    const bad: ComponentDefinition[] = [
      base,
      { ...base },
      { ...base, component_type: "Bad Type", component_id: "data.Bad Type" },
      { ...base, component_type: "x-path", component_id: "data.x-path", asset_path: "/models/x.fbx" },
      { ...base, component_type: "x-rel", component_id: "data.x-rel", compatible_connection_types: { as_source: ["TELEPORTS_TO" as never], as_target: [] } },
      { ...base, component_type: "x-port", component_id: "data.x-port", connection_ports: [base.connection_ports[0]!, base.connection_ports[0]!] },
    ];
    const errors = validateDefinitions(bad, {});
    expect(errors).toEqual(expect.arrayContaining([
      "redis: duplicate component_type",
      "Bad Type: component_type must be a lowercase slug",
      "x-path: asset_path must be /assets/infrastructure/…/*.glb",
      "x-rel: unknown relation TELEPORTS_TO",
      "x-port: duplicate connection port name",
    ]));
  });

  it("refuses to build a registry whose bindings point at a missing model", () => {
    expect(() => createRegistry(CATALOGUE.filter((d) => d.component_type !== "physical-server"))).toThrow("binding host: no component physical-server");
  });
});
