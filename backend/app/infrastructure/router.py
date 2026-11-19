"""Browser API for the infrastructure inventory. Same session gate as the rest of the browser API. The workspace is resolved on
the server and never accepted from the request. Updates distinguish an omitted field (unchanged) from an explicit null (detach)."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.clerk_auth import Principal
from app.infrastructure import service as S
from app.monitoring.router import HOST_ID
from app.registry.service import default_workspace_id


class NameIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=S.NAME_MAX)


class EnvironmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=S.NAME_MAX)


class RegionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=S.NAME_MAX)
    environment_id: uuid.UUID | None = None


class RegionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=S.NAME_MAX)
    environment_id: uuid.UUID | None = None


class ClusterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=S.NAME_MAX)
    region_id: uuid.UUID | None = None


class ClusterPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=S.NAME_MAX)
    region_id: uuid.UUID | None = None


class ServiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=S.NAME_MAX)
    description: str | None = Field(default=None, max_length=S.DESCRIPTION_MAX)


class ServicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=S.NAME_MAX)
    description: str | None = Field(default=None, max_length=S.DESCRIPTION_MAX)


class HostRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host_id: str = Field(pattern=HOST_ID)


def _changes(body: BaseModel) -> dict[str, Any]:
    """Only the fields the caller actually sent. An explicit null is kept (it means detach)."""
    return body.model_dump(exclude_unset=True)


def build_infrastructure_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], authorize) -> APIRouter:
    router = APIRouter(prefix="/api/v1/infrastructure", dependencies=[Depends(authorize)])

    @router.get("")
    def get_inventory(_: Principal = Depends(authorize)):
        with session_factory() as session:
            return S.inventory(session, default_workspace_id(session))

    # ---- environments
    @router.post("/environments", status_code=201)
    def create_environment(body: NameIn, who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            row = S.create_environment(session, default_workspace_id(session), who.user_id, body.name, clock())
            return {"id": str(row.id), "name": row.name}

    @router.patch("/environments/{env_id}")
    def update_environment(body: EnvironmentPatch, env_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            row = S.update_environment(session, default_workspace_id(session), who.user_id, env_id, body.name, clock())
            return {"id": str(row.id), "name": row.name}

    @router.delete("/environments/{env_id}")
    def delete_environment(env_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            S.delete_environment(session, default_workspace_id(session), who.user_id, env_id)
            return {"deleted": str(env_id)}

    # ---- regions
    @router.post("/regions", status_code=201)
    def create_region(body: RegionIn, who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            row = S.create_region(session, default_workspace_id(session), who.user_id, body.name, body.environment_id, clock())
            return {"id": str(row.id), "name": row.name}

    @router.patch("/regions/{region_id}")
    def update_region(body: RegionPatch, region_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            row = S.update_region(session, default_workspace_id(session), who.user_id, region_id, _changes(body), clock())
            return {"id": str(row.id), "name": row.name}

    @router.delete("/regions/{region_id}")
    def delete_region(region_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            S.delete_region(session, default_workspace_id(session), who.user_id, region_id)
            return {"deleted": str(region_id)}

    # ---- clusters
    @router.post("/clusters", status_code=201)
    def create_cluster(body: ClusterIn, who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            row = S.create_cluster(session, default_workspace_id(session), who.user_id, body.name, body.region_id, clock())
            return {"id": str(row.id), "name": row.name}

    @router.patch("/clusters/{cluster_id}")
    def update_cluster(body: ClusterPatch, cluster_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            row = S.update_cluster(session, default_workspace_id(session), who.user_id, cluster_id, _changes(body), clock())
            return {"id": str(row.id), "name": row.name}

    @router.delete("/clusters/{cluster_id}")
    def delete_cluster(cluster_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            S.delete_cluster(session, default_workspace_id(session), who.user_id, cluster_id)
            return {"deleted": str(cluster_id)}

    @router.post("/clusters/{cluster_id}/hosts", status_code=201)
    def assign_host(body: HostRef, cluster_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            host = S.assign_host_to_cluster(session, default_workspace_id(session), who.user_id, cluster_id, body.host_id)
            return {"cluster_id": str(cluster_id), "host_id": host.host_id}

    @router.delete("/clusters/{cluster_id}/hosts/{host_id}")
    def remove_host(cluster_id: uuid.UUID = Path(), host_id: str = Path(pattern=HOST_ID), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            S.remove_host_from_cluster(session, default_workspace_id(session), who.user_id, cluster_id, host_id)
            return {"cluster_id": str(cluster_id), "host_id": host_id, "removed": True}

    # ---- services
    @router.post("/services", status_code=201)
    def create_service(body: ServiceIn, who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            row = S.create_service(session, default_workspace_id(session), who.user_id, body.name, body.description, clock())
            return {"id": str(row.id), "name": row.name}

    @router.patch("/services/{service_id}")
    def update_service(body: ServicePatch, service_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            row = S.update_service(session, default_workspace_id(session), who.user_id, service_id, _changes(body), clock())
            return {"id": str(row.id), "name": row.name}

    @router.delete("/services/{service_id}")
    def delete_service(service_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            S.delete_service(session, default_workspace_id(session), who.user_id, service_id)
            return {"deleted": str(service_id)}

    @router.post("/services/{service_id}/hosts", status_code=201)
    def link_host(body: HostRef, service_id: uuid.UUID = Path(), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            S.link_service_host(session, default_workspace_id(session), who.user_id, service_id, body.host_id, clock())
            return {"service_id": str(service_id), "host_id": body.host_id}

    @router.delete("/services/{service_id}/hosts/{host_id}")
    def unlink_host(service_id: uuid.UUID = Path(), host_id: str = Path(pattern=HOST_ID), who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            S.unlink_service_host(session, default_workspace_id(session), who.user_id, service_id, host_id)
            return {"service_id": str(service_id), "host_id": host_id, "removed": True}

    return router
