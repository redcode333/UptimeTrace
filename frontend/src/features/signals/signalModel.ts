import type { Host, LatestMetrics, MetricSample, Source } from "../../api/types";

/**
 * The Signal Graph is built only from what agents report. Each host is a lane; each component an agent reported (CPU, memory,
 * disk volume, network interface direction) is a block; each block has one thread that is drawn only after a sample arrives.
 * Nothing here infers a dependency between hosts, a service, or a trace: there is no such data in the product yet.
 */

export type ThreadStatus = "flowing" | "stale" | "silent" | "no-sample";

export interface SignalComponent {
  id: string;
  hostId: string;
  label: string;
  carries: string;
  /** The metric this component is read from, so an inspector can open its real history. */
  metric: string;
  /** 0-100 for utilisation components; null for counters, where a block height would be invented. */
  utilisation: number | null;
  status: ThreadStatus;
  observedAt: string | null;
  ageSeconds: number | null;
  ratePerSecond: number | null;
}

export interface SignalLane {
  hostId: string;
  hostname: string;
  source: Source;
  reportingStatus: Host["reporting_status"];
  /** False when the latest read for this host failed, so its components are unknown rather than absent. */
  latestAvailable: boolean;
  components: SignalComponent[];
}

export interface SignalModel {
  lanes: SignalLane[];
  /** Metric families actually observed in the latest reads, in display order. */
  observedFamilies: string[];
  counts: Record<Source, number>;
  totalComponents: number;
}

const SOURCES: Source[] = ["live", "synthetic", "replay"];

function componentFor(sample: MetricSample): { label: string; carries: string; metric: string; utilisation: boolean; key: string } | null {
  const mount = sample.labels?.mount;
  const iface = sample.labels?.interface;
  switch (sample.name) {
    case "system.cpu.utilization":
      return { label: "CPU", carries: "cpu utilisation", metric: sample.name, utilisation: true, key: "cpu" };
    case "system.memory.utilization":
      return { label: "Memory", carries: "memory utilisation", metric: sample.name, utilisation: true, key: "memory" };
    case "system.disk.utilization":
      if (!mount) return null;
      return { label: `Disk ${mount}`, carries: "disk utilisation", metric: sample.name, utilisation: true, key: `disk:${mount}` };
    case "system.network.rx_bytes":
      if (!iface) return null;
      return { label: `${iface} rx`, carries: "network received", metric: sample.name, utilisation: false, key: `net:${iface}:rx` };
    case "system.network.tx_bytes":
      if (!iface) return null;
      return { label: `${iface} tx`, carries: "network sent", metric: sample.name, utilisation: false, key: `net:${iface}:tx` };
    default:
      return null;
  }
}

const FAMILY_LABEL: Record<string, string> = {
  cpu: "CPU utilisation",
  memory: "Memory utilisation",
  disk: "Disk utilisation per volume",
  network: "Network rx / tx counters",
};

function familyOf(key: string): string {
  return key.split(":")[0] ?? key;
}

/** Thread status for one component. Order matters: a stale sample is stale whatever the host status says. */
export function threadStatus(
  sample: MetricSample | undefined,
  host: Pick<Host, "reporting_status">,
  nowMs: number,
  staleAfterSeconds: number,
): { status: ThreadStatus; ageSeconds: number | null } {
  if (!sample) return { status: "no-sample", ageSeconds: null };
  const ageSeconds = Math.max(0, Math.round((nowMs - Date.parse(sample.observed_at)) / 1000));
  if (sample.stale) return { status: "stale", ageSeconds };
  if (host.reporting_status === "silent") return { status: "silent", ageSeconds };
  if (host.reporting_status === "stale" || ageSeconds > staleAfterSeconds) return { status: "stale", ageSeconds };
  return { status: "flowing", ageSeconds };
}

export function buildSignalModel(
  hosts: Host[],
  latest: Record<string, LatestMetrics | null | undefined>,
  nowMs: number,
  staleAfterSeconds: number,
  facetCounts?: Partial<Record<Source, number>>,
): SignalModel {
  const families = new Set<string>();
  const lanes: SignalLane[] = hosts.map((host) => {
    const read = latest[host.host_id];
    const latestAvailable = read !== null && read !== undefined;
    const byKey = new Map<string, MetricSample>();
    for (const sample of read?.metrics ?? []) {
      const c = componentFor(sample);
      if (c && !byKey.has(c.key)) byKey.set(c.key, sample);
    }
    const components: SignalComponent[] = [];
    for (const [key, sample] of byKey) {
      const c = componentFor(sample)!;
      const { status, ageSeconds } = threadStatus(sample, host, nowMs, staleAfterSeconds);
      families.add(familyOf(key));
      components.push({
        id: `${host.host_id}:${key}`,
        hostId: host.host_id,
        label: c.label,
        carries: c.carries,
        metric: c.metric,
        utilisation: c.utilisation ? clampPercent(sample.value) : null,
        status,
        observedAt: sample.observed_at,
        ageSeconds,
        ratePerSecond: sample.rate_per_second ?? null,
      });
    }
    components.sort((a, b) => a.label.localeCompare(b.label));
    return {
      hostId: host.host_id,
      hostname: host.hostname,
      source: host.source,
      reportingStatus: host.reporting_status,
      latestAvailable,
      components,
    };
  });
  lanes.sort((a, b) => a.hostname.localeCompare(b.hostname));
  const counts = { live: 0, synthetic: 0, replay: 0 } as Record<Source, number>;
  for (const s of SOURCES) counts[s] = facetCounts?.[s] ?? hosts.filter((h) => h.source === s).length;
  return {
    lanes,
    observedFamilies: [...families].map((f) => FAMILY_LABEL[f] ?? f).sort(),
    counts,
    totalComponents: lanes.reduce((n, l) => n + l.components.length, 0),
  };
}

function clampPercent(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.min(100, Math.max(0, v));
}

/** Metric families the agent does not report at all (the agent contract: CPU, memory, disk usage and network counters only). */
export const NOT_COLLECTED = ["Disk I/O", "Process list", "Application logs"] as const;
