import { Link } from "react-router-dom";
import { HealthBadge, SourceBadge } from "../../components/ui/Badges";
import { reasonText } from "../../lib/commandCenter";
import {
  membershipOf, RULE_TEXT, serviceNodeId, STATE_ORDER, tallyNodes, type Topology, type TopoGroup, type TopoHost, type TopoNode, type TopoState,
} from "../../lib/topology";

interface Props {
  topology: Topology;
  selected: string | null;
  select: (nodeId: string) => void;
}

const KIND_LABEL: Record<TopoGroup["kind"], string> = { environment: "Environment", region: "Region", cluster: "Cluster", group: "Group" };

/** Relationship wording. Membership is what was saved in the inventory, never a verified dependency. */
export const MEMBERSHIP_NOTE = "Lines show the hosts you assigned to this service in Infrastructure. They are not verified dependencies, and nothing was discovered from telemetry.";

function AlertRow({ alert }: { alert: TopoHost["alerts"][number] }) {
  const sev = alert.severity;
  const value = alert.value !== null && alert.threshold !== null ? ` · ${alert.value} against a limit of ${alert.threshold}` : "";
  return (
    <li className="topo-alert-row">
      <span className={`topo-alert ${sev}`}><i aria-hidden="true">{sev === "critical" ? "■" : "▲"}</i> {sev}</span>{" "}
      {RULE_TEXT[alert.rule]}{alert.metric ? ` (${alert.metric})` : ""}{value}{" "}
      <Link to={`/alerts/${encodeURIComponent(alert.id)}`}>Open alert</Link>
    </li>
  );
}

function HostPanel({ topology, host }: { topology: Topology; host: TopoHost }) {
  return (
    <>
      <h2 className="topo-details-title">{host.label}</h2>
      {host.label !== host.hostId && <code className="small">{host.hostId}</code>}
      <p className="topo-details-row">
        <SourceBadge source={host.source} />{" "}
        {host.monitored ? <HealthBadge status={host.monitored.health.status} /> : <span className="dim">No monitoring data loaded</span>}
        {host.deactivated && <span className="dim small"> · deactivated</span>}
      </p>
      {host.monitored && (
        <p className="small">
          Reporting: {host.reportingStatus}. {reasonText(host.monitored)}
        </p>
      )}
      <p className="small"><b>Membership:</b> {membershipOf(topology, host)}</p>
      <div className="small">
        <b>Services:</b>{" "}
        {host.serviceNames.length === 0 ? <span className="dim">None assigned in the inventory</span> : host.serviceNames.join(", ")}
      </div>
      <div className="small topo-alerts-block">
        <b>Active alerts:</b>
        {host.alerts.length === 0 ? <span className="dim"> None</span> : <ul className="plain">{host.alerts.map((a) => <AlertRow key={a.id} alert={a} />)}</ul>}
      </div>
      <p><Link className="btn" to={`/hosts/${encodeURIComponent(host.hostId)}`}>Open host detail</Link></p>
    </>
  );
}

function ServicePanel({ topology, nodeId, select }: { topology: Topology; nodeId: string; select: (id: string) => void }) {
  const service = topology.services.find((s) => serviceNodeId(s.id) === nodeId);
  if (!service) return null;
  const tally = tallyNodes(service.hosts);
  return (
    <>
      <h2 className="topo-details-title">{service.name}</h2>
      <p className="small topo-note">{MEMBERSHIP_NOTE}</p>
      {service.description && <p className="dim small">{service.description}</p>}
      {service.hosts.length === 0 ? <p className="dim">No hosts assigned to this service yet.</p> : (
        <>
          <p className="small">{service.hosts.length} host{service.hosts.length === 1 ? "" : "s"}: {STATE_ORDER.filter((s) => tally.tally[s] > 0).map((s) => `${s === "unloaded" ? "no data" : s} ${tally.tally[s]}`).join(" · ")}</p>
          <ul className="plain">
            {service.hosts.map((h) => (
              <li key={h.id} className="topo-details-host">
                <button type="button" className="topo-link-btn" onClick={() => select(h.id)}>{h.label}</button>{" "}
                {h.monitored ? <HealthBadge status={h.monitored.health.status} /> : <span className="dim small">no data loaded</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function GroupPanel({ node }: { node: TopoGroup }) {
  const t = tallyNodes([node]);
  const childNames = node.children.map((c) => c.label);
  return (
    <>
      <h2 className="topo-details-title">{node.label}</h2>
      <p className="small dim">{KIND_LABEL[node.kind]}</p>
      <p className="small">{t.hosts} host{t.hosts === 1 ? "" : "s"} in this {node.kind === "group" ? "group" : node.kind}.</p>
      <ul className="plain topo-tally-list">
        {STATE_ORDER.filter((s) => t.tally[s] > 0).map((s: TopoState) => (
          <li key={s}>{s === "unloaded" ? "No data loaded" : s}: {t.tally[s]}</li>
        ))}
      </ul>
      <p className="small">Active alerts on these hosts: {t.alerts}</p>
      <p className="small topo-note">This group has no health status of its own. The counts show what its hosts report.</p>
      {childNames.length > 0 && <p className="small"><b>Contains:</b> {childNames.join(", ")}</p>}
    </>
  );
}

export function TopologyDetails({ topology, selected, select }: Props) {
  let body: React.ReactNode;
  if (!selected) {
    body = <p className="dim">Select a host, service, cluster or region to see what it contains and how it is reported.</p>;
  } else if (selected.startsWith("svc:")) {
    body = <ServicePanel topology={topology} nodeId={selected} select={select} />;
  } else {
    const node: TopoNode | undefined = topology.nodes.get(selected);
    if (!node) body = <p className="dim">That item is no longer in the inventory.</p>;
    else if (node.kind === "host") body = <HostPanel topology={topology} host={node} />;
    else body = <GroupPanel node={node} />;
  }
  return <aside className="topo-details" aria-label="Details">{body}</aside>;
}
