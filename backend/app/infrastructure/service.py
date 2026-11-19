"""Infrastructure inventory: environments, regions, clusters, services and their links to hosts.

Authorisation: every read and write is scoped to the workspace resolved on the SERVER (`default_workspace_id`). A request never
supplies a workspace id. Every referenced row is loaded with its workspace filter, so an id from another workspace behaves exactly
like an id that does not exist. The database enforces the same rule with composite foreign keys (migration 0008).

Deletion never removes hosts, telemetry, alerts or investigations: a grouping's references are detached (set to NULL), and only
service links are removed when a service is deleted.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import errors
from app.errors import ErrorDetail
from app.models import AuditEvent, Cluster, Environment, Host, Region, Service, ServiceHost

NAME_MAX = 64
DESCRIPTION_MAX = 500


def clean_name(raw: str, field: str = "name") -> str:
    name = raw.strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise errors.invalid_request([ErrorDetail(field, f"Between 1 and {NAME_MAX} characters.")])
    if any(ord(ch) < 32 for ch in name):
        raise errors.invalid_request([ErrorDetail(field, "Control characters are not allowed.")])
    return name


def clean_description(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if len(text) > DESCRIPTION_MAX:
        raise errors.invalid_request([ErrorDetail("description", f"At most {DESCRIPTION_MAX} characters.")])
    return text or None


def _audit(session: Session, wid: uuid.UUID, actor: str, action: str, target_type: str, target_id: str, detail: dict | None = None) -> None:
    session.add(AuditEvent(workspace_id=wid, actor=actor, action=action, target_type=target_type, target_id=target_id, detail=detail or {}))


def _own(session: Session, model, wid: uuid.UUID, row_id: uuid.UUID, code: str, what: str):
    """A row of this workspace, or 404. Another workspace's row is indistinguishable from a missing one."""
    row = session.execute(select(model).where(model.id == row_id, model.workspace_id == wid)).scalar_one_or_none()
    if row is None:
        raise errors.ApiError(404, code, f"No {what} with that id.")
    return row


def _host(session: Session, wid: uuid.UUID, host_id: str) -> Host:
    host = session.execute(select(Host).where(Host.host_id == host_id, Host.workspace_id == wid)).scalar_one_or_none()
    if host is None:
        raise errors.ApiError(404, "host_not_found", "No host with that id in this workspace.")
    return host


def _conflict_name(exc: IntegrityError, what: str) -> errors.ApiError:
    """A unique-name violation is a clear conflict. Any other integrity failure (for example a missing reference) is not a name clash."""
    if "unique" in str(exc.orig).lower() or "duplicate key" in str(exc.orig).lower():
        return errors.ApiError(409, "name_taken", f"A {what} with that name already exists here.")
    return errors.ApiError(409, "invalid_reference", f"The {what} refers to something that does not exist in this workspace.")


# ---------------------------------------------------------------------------------------------------------- inventory read
def inventory(session: Session, wid: uuid.UUID) -> dict:
    envs = list(session.execute(select(Environment).where(Environment.workspace_id == wid).order_by(Environment.name)).scalars())
    regions = list(session.execute(select(Region).where(Region.workspace_id == wid).order_by(Region.name)).scalars())
    clusters = list(session.execute(select(Cluster).where(Cluster.workspace_id == wid).order_by(Cluster.name)).scalars())
    services = list(session.execute(select(Service).where(Service.workspace_id == wid).order_by(Service.name)).scalars())
    hosts = list(session.execute(select(Host).where(Host.workspace_id == wid).order_by(Host.host_id)).scalars())
    links = list(session.execute(select(ServiceHost.service_id, ServiceHost.host_pk).where(ServiceHost.workspace_id == wid)).all())

    host_out = {h.id: {"host_id": h.host_id, "display_name": h.display_name, "kind": h.kind, "deactivated": h.deactivated_at is not None}
                for h in hosts}
    hosts_by_cluster: dict[uuid.UUID, list] = {}
    ungrouped: list = []
    for h in hosts:
        if h.cluster_id is None:
            ungrouped.append(host_out[h.id])
        else:
            hosts_by_cluster.setdefault(h.cluster_id, []).append(host_out[h.id])

    def cluster_node(c: Cluster) -> dict:
        return {"id": str(c.id), "name": c.name, "hosts": hosts_by_cluster.get(c.id, [])}

    clusters_by_region: dict[uuid.UUID, list] = {}
    unassigned_clusters: list = []
    for c in clusters:
        if c.region_id is None:
            unassigned_clusters.append(cluster_node(c))
        else:
            clusters_by_region.setdefault(c.region_id, []).append(cluster_node(c))

    def region_node(r: Region) -> dict:
        return {"id": str(r.id), "name": r.name, "clusters": clusters_by_region.get(r.id, [])}

    regions_by_env: dict[uuid.UUID, list] = {}
    unassigned_regions: list = []
    for r in regions:
        if r.environment_id is None:
            unassigned_regions.append(region_node(r))
        else:
            regions_by_env.setdefault(r.environment_id, []).append(region_node(r))

    hosts_by_service: dict[uuid.UUID, list] = {}
    for service_id, host_pk in links:
        hosts_by_service.setdefault(service_id, []).append(host_out[host_pk])

    return {
        "environments": [{"id": str(e.id), "name": e.name, "regions": regions_by_env.get(e.id, [])} for e in envs],
        "unassigned_regions": unassigned_regions,
        "unassigned_clusters": unassigned_clusters,
        "ungrouped_hosts": ungrouped,
        "services": [{"id": str(s.id), "name": s.name, "description": s.description, "hosts": hosts_by_service.get(s.id, [])} for s in services],
        "unassigned_services": [{"id": str(s.id), "name": s.name} for s in services if s.id not in hosts_by_service],
    }


# ------------------------------------------------------------------------------------------------------------ mutations
def create_environment(session: Session, wid: uuid.UUID, actor: str, name: str, now: datetime) -> Environment:
    row = Environment(workspace_id=wid, name=clean_name(name), created_at=now, created_by=actor)
    return _insert(session, wid, actor, row, "environment", "environment")


def update_environment(session: Session, wid: uuid.UUID, actor: str, env_id: uuid.UUID, name: str | None, now: datetime) -> Environment:
    row = _own(session, Environment, wid, env_id, "environment_not_found", "environment")
    if name is not None:
        row.name = clean_name(name)
    return _update(session, wid, actor, row, "environment", now)


def delete_environment(session: Session, wid: uuid.UUID, actor: str, env_id: uuid.UUID) -> None:
    row = _own(session, Environment, wid, env_id, "environment_not_found", "environment")
    _audit(session, wid, actor, "infrastructure.deleted", "environment", str(row.id), {"name": row.name})
    session.delete(row)  # regions in it are detached by the database (ON DELETE SET NULL), not deleted
    session.flush()


def create_region(session: Session, wid: uuid.UUID, actor: str, name: str, environment_id: uuid.UUID | None, now: datetime) -> Region:
    if environment_id is not None:
        _own(session, Environment, wid, environment_id, "environment_not_found", "environment")
    row = Region(workspace_id=wid, name=clean_name(name), environment_id=environment_id, created_at=now, created_by=actor)
    return _insert(session, wid, actor, row, "region", "region")


def update_region(session: Session, wid: uuid.UUID, actor: str, region_id: uuid.UUID, changes: dict, now: datetime) -> Region:
    row = _own(session, Region, wid, region_id, "region_not_found", "region")
    if "name" in changes and changes["name"] is not None:
        row.name = clean_name(changes["name"])
    if "environment_id" in changes:  # explicit null detaches the region from its environment
        env_id = changes["environment_id"]
        if env_id is not None:
            _own(session, Environment, wid, env_id, "environment_not_found", "environment")
        row.environment_id = env_id
    return _update(session, wid, actor, row, "region", now)


def delete_region(session: Session, wid: uuid.UUID, actor: str, region_id: uuid.UUID) -> None:
    row = _own(session, Region, wid, region_id, "region_not_found", "region")
    _audit(session, wid, actor, "infrastructure.deleted", "region", str(row.id), {"name": row.name})
    session.delete(row)
    session.flush()


def create_cluster(session: Session, wid: uuid.UUID, actor: str, name: str, region_id: uuid.UUID | None, now: datetime) -> Cluster:
    if region_id is not None:
        _own(session, Region, wid, region_id, "region_not_found", "region")
    row = Cluster(workspace_id=wid, name=clean_name(name), region_id=region_id, created_at=now, created_by=actor)
    return _insert(session, wid, actor, row, "cluster", "cluster")


def update_cluster(session: Session, wid: uuid.UUID, actor: str, cluster_id: uuid.UUID, changes: dict, now: datetime) -> Cluster:
    row = _own(session, Cluster, wid, cluster_id, "cluster_not_found", "cluster")
    if "name" in changes and changes["name"] is not None:
        row.name = clean_name(changes["name"])
    if "region_id" in changes:
        region_id = changes["region_id"]
        if region_id is not None:
            _own(session, Region, wid, region_id, "region_not_found", "region")
        row.region_id = region_id
    return _update(session, wid, actor, row, "cluster", now)


def delete_cluster(session: Session, wid: uuid.UUID, actor: str, cluster_id: uuid.UUID) -> None:
    row = _own(session, Cluster, wid, cluster_id, "cluster_not_found", "cluster")
    _audit(session, wid, actor, "infrastructure.deleted", "cluster", str(row.id), {"name": row.name})
    session.delete(row)  # its hosts are detached (hosts.cluster_id set NULL by the database); the hosts are not deleted
    session.flush()


def assign_host_to_cluster(session: Session, wid: uuid.UUID, actor: str, cluster_id: uuid.UUID, host_id: str) -> Host:
    cluster = _own(session, Cluster, wid, cluster_id, "cluster_not_found", "cluster")
    host = _host(session, wid, host_id)
    if host.deactivated_at is not None:
        raise errors.ApiError(409, "host_deactivated", "A deactivated host cannot be assigned to a cluster.")
    if host.cluster_id == cluster.id:
        raise errors.ApiError(409, "already_in_cluster", "This host is already in that cluster.")
    if host.cluster_id is not None:
        raise errors.ApiError(409, "host_in_other_cluster", "This host belongs to another cluster. Remove it there first.")
    host.cluster_id = cluster.id
    session.flush()
    _audit(session, wid, actor, "infrastructure.host_assigned", "cluster", str(cluster.id), {"host_id": host_id})
    return host


def remove_host_from_cluster(session: Session, wid: uuid.UUID, actor: str, cluster_id: uuid.UUID, host_id: str) -> None:
    cluster = _own(session, Cluster, wid, cluster_id, "cluster_not_found", "cluster")
    host = _host(session, wid, host_id)
    if host.cluster_id != cluster.id:
        raise errors.ApiError(404, "not_in_cluster", "This host is not in that cluster.")
    host.cluster_id = None
    session.flush()
    _audit(session, wid, actor, "infrastructure.host_removed", "cluster", str(cluster.id), {"host_id": host_id})


def create_service(session: Session, wid: uuid.UUID, actor: str, name: str, description: str | None, now: datetime) -> Service:
    row = Service(workspace_id=wid, name=clean_name(name), description=clean_description(description), created_at=now, created_by=actor)
    return _insert(session, wid, actor, row, "service", "service")


def update_service(session: Session, wid: uuid.UUID, actor: str, service_id: uuid.UUID, changes: dict, now: datetime) -> Service:
    row = _own(session, Service, wid, service_id, "service_not_found", "service")
    if "name" in changes and changes["name"] is not None:
        row.name = clean_name(changes["name"])
    if "description" in changes:
        row.description = clean_description(changes["description"])
    return _update(session, wid, actor, row, "service", now)


def delete_service(session: Session, wid: uuid.UUID, actor: str, service_id: uuid.UUID) -> None:
    row = _own(session, Service, wid, service_id, "service_not_found", "service")
    _audit(session, wid, actor, "infrastructure.deleted", "service", str(row.id), {"name": row.name})
    session.delete(row)  # only the service's host links are removed (ON DELETE CASCADE on service_hosts)
    session.flush()


def link_service_host(session: Session, wid: uuid.UUID, actor: str, service_id: uuid.UUID, host_id: str, now: datetime) -> None:
    service = _own(session, Service, wid, service_id, "service_not_found", "service")
    host = _host(session, wid, host_id)
    if host.deactivated_at is not None:
        raise errors.ApiError(409, "host_deactivated", "A deactivated host cannot be linked to a service.")
    existing = session.execute(select(ServiceHost).where(ServiceHost.service_id == service.id, ServiceHost.host_pk == host.id)).first()
    if existing is not None:
        raise errors.ApiError(409, "already_linked", "This service already runs on that host.")
    session.add(ServiceHost(workspace_id=wid, service_id=service.id, host_pk=host.id, created_at=now, created_by=actor))
    try:
        session.flush()
    except IntegrityError:  # a concurrent request linked it first
        raise errors.ApiError(409, "already_linked", "This service already runs on that host.") from None
    _audit(session, wid, actor, "infrastructure.service_linked", "service", str(service.id), {"host_id": host_id})


def unlink_service_host(session: Session, wid: uuid.UUID, actor: str, service_id: uuid.UUID, host_id: str) -> None:
    service = _own(session, Service, wid, service_id, "service_not_found", "service")
    host = _host(session, wid, host_id)
    link = session.execute(select(ServiceHost).where(ServiceHost.service_id == service.id, ServiceHost.host_pk == host.id)).scalar_one_or_none()
    if link is None:
        raise errors.ApiError(404, "not_linked", "This service does not run on that host.")
    session.delete(link)
    session.flush()
    _audit(session, wid, actor, "infrastructure.service_unlinked", "service", str(service.id), {"host_id": host_id})


# ------------------------------------------------------------------------------------------------------ shared helpers
def _insert(session: Session, wid: uuid.UUID, actor: str, row, action_target: str, what: str):
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        raise _conflict_name(exc, what) from None
    _audit(session, wid, actor, f"infrastructure.{action_target}_created", action_target, str(row.id), {"name": row.name})
    return row


def _update(session: Session, wid: uuid.UUID, actor: str, row, target: str, now: datetime):
    row.updated_at = now
    row.updated_by = actor
    try:
        session.flush()
    except IntegrityError as exc:
        raise _conflict_name(exc, target) from None
    _audit(session, wid, actor, f"infrastructure.{target}_updated", target, str(row.id), {"name": row.name})
    return row
