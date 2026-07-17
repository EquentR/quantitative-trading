from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.notification.models import NotificationStatus, NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.notification.service import NotificationService


router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_token)],
)


class UnreadCountResponse(BaseModel):
    count: int


class NotificationListResponse(BaseModel):
    items: list[NotificationSummary]
    total: int
    page: int
    page_size: int


def _not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="notification_not_found",
        message="notification not found",
    )


def _storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="notification storage failed",
    )


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    view: Literal["current", "history"] | None = None,
    status: NotificationStatus | None = None,
    symbol: str | None = Query(default=None, pattern=r"^[0-9]{6}$"),
    action: str | None = None,
    recommendation_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    container: ApiContainer = Depends(get_container),
) -> NotificationListResponse:
    try:
        with connection_scope(container.settings) as connection:
            repository = NotificationRepository(connection)
            service = NotificationService(repository)
            selected_view = view or "history"
            return NotificationListResponse(
                items=service.list_notifications(
                    view=selected_view,
                    status=status,
                    symbol=symbol,
                    action=action,
                    recommendation_id=recommendation_id,
                    limit=page_size,
                    offset=(page - 1) * page_size,
                ),
                total=(
                    repository.count_current(
                        status=status,
                        symbol=symbol,
                        action=action,
                        recommendation_id=recommendation_id,
                    )
                    if selected_view == "current"
                    else repository.count(
                        status=status,
                        symbol=symbol,
                        action=action,
                        recommendation_id=recommendation_id,
                    )
                ),
                page=page,
                page_size=page_size,
            )
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc


@router.get("/unread-count", response_model=UnreadCountResponse)
def unread_count(
    container: ApiContainer = Depends(get_container),
) -> UnreadCountResponse:
    try:
        with connection_scope(container.settings) as connection:
            return UnreadCountResponse(
                count=NotificationService(
                    NotificationRepository(connection)
                ).unread_count()
            )
    except sqlite3.Error as exc:
        raise _storage_failed() from exc


@router.post("/{notification_id}/read", response_model=NotificationSummary)
def mark_read(
    notification_id: str,
    container: ApiContainer = Depends(get_container),
) -> NotificationSummary:
    try:
        with connection_scope(container.settings) as connection:
            repository = NotificationRepository(connection)
            if repository.get(notification_id) is None:
                raise _not_found()
            updated = NotificationService(repository).mark_read(
                notification_id,
                commit=False,
            )
            AuditService(AuditLogRepository(connection)).record_event(
                event_type="notification.read",
                recommendation_id=updated.recommendation_id,
                payload={"notification_id": notification_id},
                commit=False,
            )
            connection.commit()
            return updated
    except ApiError:
        raise
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc
