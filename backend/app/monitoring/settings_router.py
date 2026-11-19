"""Console API for monitoring settings (freshness limits + thresholds). Same session gate as the rest of the browser API."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.clerk_auth import Principal
from app.models import AuditEvent
from app.monitoring import settings as ST
from app.registry.service import default_workspace_id


class SettingsOut(BaseModel):
    config: dict
    defaults: dict
    version: int            # 0 = nothing stored yet (built-in defaults are in effect)
    updated_at: datetime | None
    updated_by: str | None


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict
    expected_version: int = Field(ge=0)


def _out(stored: ST.StoredConfig) -> SettingsOut:
    return SettingsOut(config=stored.config.to_document(), defaults=ST.DEFAULT_CONFIG.to_document(), version=stored.version,
                       updated_at=stored.updated_at, updated_by=stored.updated_by)


def build_settings_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], authorize) -> APIRouter:
    router = APIRouter(prefix="/api/v1/settings")

    @router.get("/monitoring", response_model=SettingsOut)
    def get_settings(_: Principal = Depends(authorize)):
        with session_factory() as session:
            return _out(ST.load(session, default_workspace_id(session)))

    @router.put("/monitoring", response_model=SettingsOut)
    def put_settings(body: SettingsIn, who: Principal = Depends(authorize)):
        with session_factory() as session, session.begin():
            ws = default_workspace_id(session)
            saved = ST.save(session, ws, who.user_id, body.config, body.expected_version, clock())
            session.add(AuditEvent(workspace_id=ws, actor=who.user_id, action="monitoring_settings.updated", target_type="settings",
                                   target_id="monitoring", detail={"version": saved.version, "config": saved.config.to_document()}))
            return _out(saved)

    return router
