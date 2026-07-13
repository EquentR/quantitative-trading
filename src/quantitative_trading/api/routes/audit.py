from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository


router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(require_token)],
)


class AuditListResponse(BaseModel):
    items: list[AuditLog]
    total: int
    page: int
    page_size: int


def _not_found() -> ApiError:
    return ApiError(status_code=404, code="audit_not_found", message="audit log not found")


def _storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="audit storage failed",
    )


@router.get("", response_model=AuditListResponse)
def list_audit_logs(
    event_type: str | None = None,
    recommendation_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    container: ApiContainer = Depends(get_container),
) -> AuditListResponse:
    try:
        with connection_scope(container.settings) as connection:
            repository = AuditLogRepository(connection)
            return AuditListResponse(
                items=repository.list(
                    event_type=event_type,
                    recommendation_id=recommendation_id,
                    limit=page_size,
                    offset=(page - 1) * page_size,
                ),
                total=repository.count(
                    event_type=event_type,
                    recommendation_id=recommendation_id,
                ),
                page=page,
                page_size=page_size,
            )
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc


@router.get("/{audit_id}", response_model=AuditLog)
def get_audit_log(
    audit_id: str,
    container: ApiContainer = Depends(get_container),
) -> AuditLog:
    try:
        with connection_scope(container.settings) as connection:
            audit = AuditLogRepository(connection).get(audit_id)
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc
    if audit is None:
        raise _not_found()
    return audit
