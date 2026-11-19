"""Browser API for alert investigations. Same authorisation gate as the alert routes; every query is bounded and workspace-scoped."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.orm import Session, sessionmaker

from app import errors
from app.clerk_auth import Principal
from app.investigation import service as I


def build_investigation_router(session_factory: sessionmaker[Session], clock: Callable[[], datetime], authorize) -> APIRouter:
    router = APIRouter(prefix="/api/v1/alerts", dependencies=[Depends(authorize)])

    @router.get("/{alert_id}/investigation")
    def get_investigation(
        alert_id: uuid.UUID = Path(),
        window_minutes: int | None = Query(None, description=f"Telemetry and related-alert window either side of the trigger, {I.WINDOW_MIN_MIN}..{I.WINDOW_MAX_MIN} minutes."),
        related_limit: int = Query(20, ge=1, le=I.RELATED_PAGE_MAX),
        related_offset: int = Query(0, ge=0, le=10_000),
        _: Principal = Depends(authorize),
    ):
        window = I.parse_window(window_minutes)
        with session_factory() as session:
            alert = I._load_alert(session, alert_id)
            return I.build_view(session, alert, clock(), window, related_limit, related_offset)

    @router.get("/{alert_id}/investigation/report")
    def get_report(alert_id: uuid.UUID = Path(), _: Principal = Depends(authorize)):
        with session_factory() as session:
            record = I.existing_report(session, alert_id)
            if record is None:
                raise errors.ApiError(404, "report_not_found", "No investigation record has been created for this alert.")
            return I.report_out(record)

    @router.post("/{alert_id}/investigation/report")
    def create_report(
        response: Response,
        alert_id: uuid.UUID = Path(),
        window_minutes: int | None = Query(None),
        who: Principal = Depends(authorize),
    ):
        window = I.parse_window(window_minutes)
        with session_factory() as session, session.begin():
            record, created = I.create_report(session, alert_id, who.user_id, clock(), window)
            out = I.report_out(record)
        response.status_code = 201 if created else 200
        return out

    return router
