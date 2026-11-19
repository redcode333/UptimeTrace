import { useState } from "react";
import { Link } from "react-router-dom";
import type { ApiClient } from "../../api/client";
import { useInfrastructure, useInfrastructureMutation } from "../../api/hooks";
import type { Inventory, InventoryCluster, InventoryHost, InventoryRegion } from "../../api/types";
import { PageHeader } from "../../components/PageHeader";
import { EmptyState, ErrorState, Loading } from "../../components/states/States";
import { SourceBadge } from "../../components/ui/Badges";
import {
  clusterAssignableHosts, clusterChoices, DESCRIPTION_MAX, failureText, hostLabel, NAME_MAX, regionChoices, serviceAssignableHosts, summarize,
  validateDescription, validateName,
} from "../../lib/infrastructure";

/** One change. Each maps to exactly one server call; the server validates it again. */
type Op =
  | { kind: "createEnvironment"; name: string }
  | { kind: "renameEnvironment"; id: string; name: string }
  | { kind: "deleteEnvironment"; id: string }
  | { kind: "createRegion"; name: string; environmentId: string | null }
  | { kind: "moveRegion"; id: string; environmentId: string | null }
  | { kind: "renameRegion"; id: string; name: string }
  | { kind: "deleteRegion"; id: string }
  | { kind: "createCluster"; name: string; regionId: string | null }
  | { kind: "moveCluster"; id: string; regionId: string | null }
  | { kind: "renameCluster"; id: string; name: string }
  | { kind: "deleteCluster"; id: string }
  | { kind: "assignHost"; clusterId: string; hostId: string }
  | { kind: "removeHost"; clusterId: string; hostId: string }
  | { kind: "createService"; name: string; description: string | null }
  | { kind: "updateService"; id: string; name: string; description: string | null }
  | { kind: "deleteService"; id: string }
  | { kind: "linkHost"; serviceId: string; hostId: string }
  | { kind: "unlinkHost"; serviceId: string; hostId: string };

function run(api: ApiClient, op: Op): Promise<unknown> {
  switch (op.kind) {
    case "createEnvironment": return api.createEnvironment(op.name);
    case "renameEnvironment": return api.updateEnvironment(op.id, op.name);
    case "deleteEnvironment": return api.deleteEnvironment(op.id);
    case "createRegion": return api.createRegion({ name: op.name, environment_id: op.environmentId });
    case "moveRegion": return api.updateRegion(op.id, { environment_id: op.environmentId });
    case "renameRegion": return api.updateRegion(op.id, { name: op.name });
    case "deleteRegion": return api.deleteRegion(op.id);
    case "createCluster": return api.createCluster({ name: op.name, region_id: op.regionId });
    case "moveCluster": return api.updateCluster(op.id, { region_id: op.regionId });
    case "renameCluster": return api.updateCluster(op.id, { name: op.name });
    case "deleteCluster": return api.deleteCluster(op.id);
    case "assignHost": return api.assignClusterHost(op.clusterId, op.hostId);
    case "removeHost": return api.removeClusterHost(op.clusterId, op.hostId);
    case "createService": return api.createService({ name: op.name, description: op.description });
    case "updateService": return api.updateService(op.id, { name: op.name, description: op.description });
    case "deleteService": return api.deleteService(op.id);
    case "linkHost": return api.linkServiceHost(op.serviceId, op.hostId);
    case "unlinkHost": return api.unlinkServiceHost(op.serviceId, op.hostId);
  }
}

export function InfrastructurePage() {
  const inv = useInfrastructure();
  const change = useInfrastructureMutation<Op, unknown>((api, op) => run(api, op));
  const [notice, setNotice] = useState<string | null>(null);
  const apply = (op: Op, done?: string) => {
    setNotice(null);
    change.mutate(op, { onSuccess: () => setNotice(done ?? null) });
  };

  return (
    <>
      <PageHeader
        title="Infrastructure"
        subtitle="Organise hosts into environments, regions, clusters and services. Every level is optional, and the hosts themselves are never changed here."
        actions={<Link className="btn" to="/hosts">Back to hosts</Link>}
      />
      {inv.isPending && <Loading label="Loading the inventory" />}
      {inv.isError && !inv.data && <ErrorState error={inv.error} onRetry={() => inv.refetch()} />}
      {inv.data && (
        <>
          {change.isError && <p className="banner warn" role="alert">{failureText(change.error)}</p>}
          {notice && <p className="banner notice" role="status">{notice}</p>}
          <Summary inv={inv.data} />
          {inv.data.ungrouped_hosts.length === 0 && inv.data.environments.length === 0 && inv.data.services.length === 0 &&
            inv.data.unassigned_regions.length === 0 && inv.data.unassigned_clusters.length === 0 ? (
            <EmptyState title="Nothing to organise yet" body="Hosts appear here once they are registered. Grouping them is optional.">
              <Link to="/onboarding">Register a host</Link>
            </EmptyState>
          ) : null}
          <CreateForms inv={inv.data} apply={apply} busy={change.isPending} />
          <Environments inv={inv.data} apply={apply} busy={change.isPending} />
          <Section title="Regions without an environment" hint="Optional. A region can stand on its own.">
            {inv.data.unassigned_regions.map((r) => (
              <RegionCard key={r.id} region={r} inv={inv.data!} apply={apply} busy={change.isPending} />
            ))}
            {inv.data.unassigned_regions.length === 0 && <p className="dim small">None.</p>}
          </Section>
          <Section title="Clusters without a region" hint="Optional. A cluster can stand on its own.">
            {inv.data.unassigned_clusters.map((c) => (
              <ClusterCard key={c.id} cluster={c} inv={inv.data!} apply={apply} busy={change.isPending} />
            ))}
            {inv.data.unassigned_clusters.length === 0 && <p className="dim small">None.</p>}
          </Section>
          <UngroupedHosts inv={inv.data} apply={apply} busy={change.isPending} />
          <Services inv={inv.data} apply={apply} busy={change.isPending} />
        </>
      )}
    </>
  );
}

type Apply = (op: Op, done?: string) => void;

function Summary({ inv }: { inv: Inventory }) {
  const s = summarize(inv);
  return (
    <p className="small dim" aria-label="Inventory summary">
      {s.environments} environment{s.environments === 1 ? "" : "s"} · {s.regions} region{s.regions === 1 ? "" : "s"} · {s.clusters} cluster{s.clusters === 1 ? "" : "s"} · {s.services} service{s.services === 1 ? "" : "s"} · {s.groupedHosts} grouped host{s.groupedHosts === 1 ? "" : "s"} · {s.ungroupedHosts} not in a cluster
    </p>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="widget" aria-label={title}>
      <div className="widget-head"><h2>{title}</h2>{hint && <span className="dim small">{hint}</span>}</div>
      <div className="widget-body">{children}</div>
    </section>
  );
}

/** Name plus a single-field editor. The editor is validated before anything is sent. */
function EditableName({ name, label, onSave, busy }: { name: string; label: string; onSave: (n: string) => void; busy: boolean }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);
  const error = validateName(value);
  if (!editing) {
    return (
      <span>
        <b>{name}</b>{" "}
        <button type="button" className="btn small-btn" aria-label={`Rename ${label}`} onClick={() => { setValue(name); setEditing(true); }}>Rename</button>
      </span>
    );
  }
  return (
    <span className="row-actions">
      <input aria-label={`New name for ${label}`} value={value} maxLength={NAME_MAX} onChange={(e) => setValue(e.target.value)} />
      <button type="button" className="btn" disabled={!!error || busy || value.trim() === name} onClick={() => { onSave(value.trim()); setEditing(false); }}>Save</button>
      <button type="button" className="btn" onClick={() => setEditing(false)}>Cancel</button>
      {error && <span className="field-error small" role="alert">{error}</span>}
    </span>
  );
}

/** Delete with confirmation in place. The text states what is detached and what is kept. */
function DeleteButton({ label, consequence, onConfirm, busy }: { label: string; consequence: string; onConfirm: () => void; busy: boolean }) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming) return <button type="button" className="btn danger" aria-label={`Delete ${label}`} onClick={() => setConfirming(true)}>Delete</button>;
  return (
    <span className="confirm" role="group" aria-label={`Confirm deleting ${label}`}>
      <span className="small">{consequence}</span>{" "}
      <button type="button" className="btn danger" disabled={busy} onClick={() => { onConfirm(); setConfirming(false); }}>Confirm delete</button>{" "}
      <button type="button" className="btn" onClick={() => setConfirming(false)}>Cancel</button>
    </span>
  );
}

function HostRow({ h, action }: { h: InventoryHost; action?: React.ReactNode }) {
  return (
    <li className="host-row">
      <Link className="hostlink" to={`/hosts/${encodeURIComponent(h.host_id)}`}>{hostLabel(h)}</Link>{" "}
      <SourceBadge source={h.kind === "live" ? "live" : h.kind} />
      {h.deactivated && <span className="dim small"> · deactivated</span>}
      {action}
    </li>
  );
}

function CreateForms({ inv, apply, busy }: { inv: Inventory; apply: Apply; busy: boolean }) {
  return (
    <div className="grid2 create-forms">
      <CreateName label="environment" placeholder="e.g. production" onCreate={(name) => apply({ kind: "createEnvironment", name }, "Environment created.")} busy={busy} />
      <CreateRegion inv={inv} apply={apply} busy={busy} />
      <CreateCluster inv={inv} apply={apply} busy={busy} />
      <CreateService apply={apply} busy={busy} />
    </div>
  );
}

function CreateName({ label, placeholder, onCreate, busy }: { label: string; placeholder: string; onCreate: (name: string) => void; busy: boolean }) {
  const [name, setName] = useState("");
  const error = name.trim() === "" ? null : validateName(name);
  return (
    <section className="widget" aria-label={`Create an ${label}`}>
      <div className="widget-head"><h2>Add {label === "environment" ? "an environment" : `a ${label}`}</h2></div>
      <form className="widget-body" onSubmit={(e) => { e.preventDefault(); if (!validateName(name)) { onCreate(name.trim()); setName(""); } }}>
        <div className="field">
          <input aria-label={`${label} name`} placeholder={placeholder} value={name} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)} />
          <button type="submit" className="btn" disabled={busy || !!validateName(name)}>Add</button>
        </div>
        {error && <div className="field-error small" role="alert">{error}</div>}
      </form>
    </section>
  );
}

function CreateRegion({ inv, apply, busy }: { inv: Inventory; apply: Apply; busy: boolean }) {
  const [name, setName] = useState("");
  const [env, setEnv] = useState("");
  const error = name.trim() === "" ? null : validateName(name);
  return (
    <section className="widget" aria-label="Create a region">
      <div className="widget-head"><h2>Add a region</h2></div>
      <form className="widget-body" onSubmit={(e) => { e.preventDefault(); if (!validateName(name)) { apply({ kind: "createRegion", name: name.trim(), environmentId: env || null }, "Region created."); setName(""); } }}>
        <div className="field">
          <input aria-label="region name" placeholder="e.g. eu-west-1" value={name} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)} />
          <select aria-label="region environment" value={env} onChange={(e) => setEnv(e.target.value)}>
            <option value="">No environment</option>
            {inv.environments.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
          <button type="submit" className="btn" disabled={busy || !!validateName(name)}>Add</button>
        </div>
        {error && <div className="field-error small" role="alert">{error}</div>}
      </form>
    </section>
  );
}

function CreateCluster({ inv, apply, busy }: { inv: Inventory; apply: Apply; busy: boolean }) {
  const [name, setName] = useState("");
  const [region, setRegion] = useState("");
  const error = name.trim() === "" ? null : validateName(name);
  return (
    <section className="widget" aria-label="Create a cluster">
      <div className="widget-head"><h2>Add a cluster</h2></div>
      <form className="widget-body" onSubmit={(e) => { e.preventDefault(); if (!validateName(name)) { apply({ kind: "createCluster", name: name.trim(), regionId: region || null }, "Cluster created."); setName(""); } }}>
        <div className="field">
          <input aria-label="cluster name" placeholder="e.g. api" value={name} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)} />
          <select aria-label="cluster region" value={region} onChange={(e) => setRegion(e.target.value)}>
            <option value="">No region</option>
            {regionChoices(inv).map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
          <button type="submit" className="btn" disabled={busy || !!validateName(name)}>Add</button>
        </div>
        {error && <div className="field-error small" role="alert">{error}</div>}
      </form>
    </section>
  );
}

function CreateService({ apply, busy }: { apply: Apply; busy: boolean }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const nameError = name.trim() === "" ? null : validateName(name);
  const descError = validateDescription(description);
  return (
    <section className="widget" aria-label="Create a service">
      <div className="widget-head"><h2>Add a service</h2></div>
      <form className="widget-body" onSubmit={(e) => {
        e.preventDefault();
        if (!validateName(name) && !descError) {
          apply({ kind: "createService", name: name.trim(), description: description.trim() || null }, "Service created.");
          setName(""); setDescription("");
        }
      }}>
        <div className="field">
          <input aria-label="service name" placeholder="e.g. checkout-api" value={name} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)} />
          <input aria-label="service description" placeholder={`Optional, up to ${DESCRIPTION_MAX} characters`} value={description} onChange={(e) => setDescription(e.target.value)} />
          <button type="submit" className="btn" disabled={busy || !!validateName(name) || !!descError}>Add</button>
        </div>
        {nameError && <div className="field-error small" role="alert">{nameError}</div>}
        {descError && <div className="field-error small" role="alert">{descError}</div>}
      </form>
    </section>
  );
}

function Environments({ inv, apply, busy }: { inv: Inventory; apply: Apply; busy: boolean }) {
  if (inv.environments.length === 0) return null;
  return (
    <Section title="Environments" hint="Production, staging and so on. Each environment holds regions.">
      {inv.environments.map((e) => (
        <div key={e.id} className="env-block" aria-label={`Environment ${e.name}`}>
          <h3 className="small">
            <EditableName name={e.name} label={`environment ${e.name}`} busy={busy} onSave={(n) => apply({ kind: "renameEnvironment", id: e.id, name: n }, "Environment renamed.")} />{" "}
            <DeleteButton label={`environment ${e.name}`} busy={busy}
              consequence="Its regions are kept and become regions without an environment. Hosts and telemetry are not affected."
              onConfirm={() => apply({ kind: "deleteEnvironment", id: e.id }, "Environment deleted. Its regions are kept.")} />
          </h3>
          {e.regions.length === 0 && <p className="dim small">No regions in this environment yet.</p>}
          {e.regions.map((r) => <RegionCard key={r.id} region={r} inv={inv} apply={apply} busy={busy} />)}
        </div>
      ))}
    </Section>
  );
}

function RegionCard({ region, inv, apply, busy }: { region: InventoryRegion; inv: Inventory; apply: Apply; busy: boolean }) {
  const envChoices = inv.environments;
  return (
    <div className="region-block" aria-label={`Region ${region.name}`}>
      <div className="row-actions">
        <span className="small">Region</span>
        <EditableName name={region.name} label={`region ${region.name}`} busy={busy} onSave={(n) => apply({ kind: "renameRegion", id: region.id, name: n }, "Region renamed.")} />
        <select aria-label={`Environment of ${region.name}`} disabled={busy} defaultValue=""
          onChange={(e) => { if (e.target.value) apply({ kind: "moveRegion", id: region.id, environmentId: e.target.value === "none" ? null : e.target.value }, "Region moved."); e.target.value = ""; }}>
          <option value="">Move to…</option>
          <option value="none">No environment</option>
          {envChoices.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
        <DeleteButton label={`region ${region.name}`} busy={busy}
          consequence="Its clusters are kept and become clusters without a region. Hosts and telemetry are not affected."
          onConfirm={() => apply({ kind: "deleteRegion", id: region.id }, "Region deleted. Its clusters are kept.")} />
      </div>
      {region.clusters.length === 0 && <p className="dim small">No clusters in this region.</p>}
      {region.clusters.map((c) => <ClusterCard key={c.id} cluster={c} inv={inv} apply={apply} busy={busy} />)}
    </div>
  );
}

function ClusterCard({ cluster, inv, apply, busy }: { cluster: InventoryCluster; inv: Inventory; apply: Apply; busy: boolean }) {
  const [host, setHost] = useState("");
  const assignable = clusterAssignableHosts(inv);
  return (
    <div className="cluster-block" aria-label={`Cluster ${cluster.name}`}>
      <div className="row-actions">
        <span className="small">Cluster</span>
        <EditableName name={cluster.name} label={`cluster ${cluster.name}`} busy={busy} onSave={(n) => apply({ kind: "renameCluster", id: cluster.id, name: n }, "Cluster renamed.")} />
        <select aria-label={`Region of ${cluster.name}`} disabled={busy} defaultValue=""
          onChange={(e) => { if (e.target.value) apply({ kind: "moveCluster", id: cluster.id, regionId: e.target.value === "none" ? null : e.target.value }, "Cluster moved."); e.target.value = ""; }}>
          <option value="">Move to…</option>
          <option value="none">No region</option>
          {regionChoices(inv).map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
        </select>
        <DeleteButton label={`cluster ${cluster.name}`} busy={busy}
          consequence="Its hosts are kept and become hosts not in a cluster. Hosts, telemetry and alerts are not deleted."
          onConfirm={() => apply({ kind: "deleteCluster", id: cluster.id }, "Cluster deleted. Its hosts are kept.")} />
      </div>
      {cluster.hosts.length === 0 ? <p className="dim small">No hosts in this cluster.</p> : (
        <ul className="plain">
          {cluster.hosts.map((h) => (
            <HostRow key={h.host_id} h={h} action={
              <button type="button" className="btn" disabled={busy} aria-label={`Remove ${h.host_id} from ${cluster.name}`}
                onClick={() => apply({ kind: "removeHost", clusterId: cluster.id, hostId: h.host_id }, "Host removed from the cluster.")}>Remove</button>
            } />
          ))}
        </ul>
      )}
      {assignable.length > 0 && (
        <div className="row-actions">
          <select aria-label={`Host to add to ${cluster.name}`} value={host} onChange={(e) => setHost(e.target.value)}>
            <option value="">Choose a host…</option>
            {assignable.map((h) => <option key={h.host_id} value={h.host_id}>{hostLabel(h)}</option>)}
          </select>
          <button type="button" className="btn" disabled={busy || !host}
            onClick={() => { apply({ kind: "assignHost", clusterId: cluster.id, hostId: host }, "Host added to the cluster."); setHost(""); }}>Add host</button>
        </div>
      )}
    </div>
  );
}

function UngroupedHosts({ inv, apply, busy }: { inv: Inventory; apply: Apply; busy: boolean }) {
  const [choice, setChoice] = useState<Record<string, string>>({});
  const clusters = clusterChoices(inv);
  return (
    <Section title="Hosts not in a cluster" hint="These hosts work as they did before. Grouping them is optional.">
      {inv.ungrouped_hosts.length === 0 && <p className="dim small">Every host is in a cluster.</p>}
      <ul className="plain">
        {inv.ungrouped_hosts.map((h) => (
          <HostRow key={h.host_id} h={h} action={
            h.deactivated ? null : (
              <span className="row-actions">
                <select aria-label={`Cluster for ${h.host_id}`} value={choice[h.host_id] ?? ""} disabled={busy || clusters.length === 0}
                  onChange={(e) => setChoice({ ...choice, [h.host_id]: e.target.value })}>
                  <option value="">{clusters.length === 0 ? "Create a cluster first" : "Choose a cluster…"}</option>
                  {clusters.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
                <button type="button" className="btn" disabled={busy || !choice[h.host_id]}
                  onClick={() => apply({ kind: "assignHost", clusterId: choice[h.host_id]!, hostId: h.host_id }, "Host added to the cluster.")}>Add to cluster</button>
              </span>
            )
          } />
        ))}
      </ul>
    </Section>
  );
}

function Services({ inv, apply, busy }: { inv: Inventory; apply: Apply; busy: boolean }) {
  return (
    <Section title="Services" hint="A service can run on several hosts, and a host can run several services. Hosts do not need a cluster.">
      {inv.services.length === 0 && <p className="dim small">No services yet. Add one above, then choose the hosts it runs on.</p>}
      {inv.services.map((s) => <ServiceCard key={s.id} service={s} inv={inv} apply={apply} busy={busy} />)}
    </Section>
  );
}

function ServiceCard({ service, inv, apply, busy }: { service: { id: string; name: string; description: string | null; hosts: InventoryHost[] }; inv: Inventory; apply: Apply; busy: boolean }) {
  const [host, setHost] = useState("");
  const choices = serviceAssignableHosts(inv, service.hosts.map((h) => h.host_id));
  return (
    <div className="service-block" aria-label={`Service ${service.name}`}>
      <div className="row-actions">
        <EditableName name={service.name} label={`service ${service.name}`} busy={busy} onSave={(n) => apply({ kind: "updateService", id: service.id, name: n, description: service.description }, "Service renamed.")} />
        {service.hosts.length === 0 && <span className="dim small">No hosts assigned yet</span>}
        <DeleteButton label={`service ${service.name}`} busy={busy}
          consequence="The service is removed and its host links with it. The hosts and their telemetry are not affected."
          onConfirm={() => apply({ kind: "deleteService", id: service.id }, "Service deleted.")} />
      </div>
      {service.description && <p className="dim small">{service.description}</p>}
      {service.hosts.length > 0 && (
        <ul className="plain">
          {service.hosts.map((h) => (
            <HostRow key={h.host_id} h={h} action={
              <button type="button" className="btn" disabled={busy} aria-label={`Stop ${service.name} on ${h.host_id}`}
                onClick={() => apply({ kind: "unlinkHost", serviceId: service.id, hostId: h.host_id }, "Host unlinked from the service.")}>Remove</button>
            } />
          ))}
        </ul>
      )}
      {choices.length > 0 && (
        <div className="row-actions">
          <select aria-label={`Host for ${service.name}`} value={host} onChange={(e) => setHost(e.target.value)}>
            <option value="">Choose a host…</option>
            {choices.map((h) => <option key={h.host_id} value={h.host_id}>{hostLabel(h)}</option>)}
          </select>
          <button type="button" className="btn" disabled={busy || !host}
            onClick={() => { apply({ kind: "linkHost", serviceId: service.id, hostId: host }, "Host linked to the service."); setHost(""); }}>Add host</button>
        </div>
      )}
    </div>
  );
}
