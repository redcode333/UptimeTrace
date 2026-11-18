/** The metrics defined by telemetry contract v1. Nothing else is collected, so nothing else is offered. */
export interface MetricDef {
  name: string;
  label: string;
  /** What the series endpoint returns: gauges stay percent, counters come back as per-second rates. */
  valueType: "gauge" | "counter_rate";
  unit: "percent" | "bytes_per_second";
  note?: string;
}

export const METRICS: readonly MetricDef[] = [
  { name: "system.cpu.utilization", label: "CPU utilization", valueType: "gauge", unit: "percent" },
  { name: "system.memory.utilization", label: "Memory utilization", valueType: "gauge", unit: "percent" },
  { name: "system.disk.utilization", label: "Disk utilization (per mount)", valueType: "gauge", unit: "percent" },
  { name: "system.network.rx_bytes", label: "Network receive rate", valueType: "counter_rate", unit: "bytes_per_second", note: "Derived by the backend from the cumulative byte counter." },
  { name: "system.network.tx_bytes", label: "Network transmit rate", valueType: "counter_rate", unit: "bytes_per_second", note: "Derived by the backend from the cumulative byte counter." },
] as const;

export const metricDef = (name: string): MetricDef | undefined => METRICS.find((m) => m.name === name);

export const AGGS = ["avg", "min", "max", "last"] as const;
export type Agg = (typeof AGGS)[number];
