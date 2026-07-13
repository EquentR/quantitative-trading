from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.email.models import EmailDelivery, EmailDeliveryStatus
from quantitative_trading.email.outbox import (
    EmailDeliveryNotRetryableError,
    EmailDeliveryRepository,
)


router = APIRouter(
    prefix="/notifications/email-deliveries",
    tags=["email-deliveries"],
    dependencies=[Depends(require_token)],
)


def _not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="email_delivery_not_found",
        message="email delivery not found",
    )


def _not_retryable() -> ApiError:
    return ApiError(
        status_code=409,
        code="email_delivery_not_retryable",
        message="email delivery is not retryable",
    )


def _storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="email delivery storage failed",
    )


@router.get("", response_model=list[EmailDelivery])
def list_deliveries(
    status: EmailDeliveryStatus | None = None,
    notification_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    container: ApiContainer = Depends(get_container),
) -> list[EmailDelivery]:
    try:
        with connection_scope(container.settings) as connection:
            return EmailDeliveryRepository(connection).list(
                status=status,
                notification_id=notification_id,
                limit=limit,
                offset=offset,
            )
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc


@router.get("/{delivery_id}", response_model=EmailDelivery)
def get_delivery(
    delivery_id: str,
    container: ApiContainer = Depends(get_container),
) -> EmailDelivery:
    try:
        with connection_scope(container.settings) as connection:
            delivery = EmailDeliveryRepository(connection).get(delivery_id)
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc
    if delivery is None:
        raise _not_found()
    return delivery


@router.post("/{delivery_id}/retry", response_model=EmailDelivery)
def retry_delivery(
    delivery_id: str,
    container: ApiContainer = Depends(get_container),
) -> EmailDelivery:
    try:
        with connection_scope(container.settings) as connection:
            repository = EmailDeliveryRepository(connection)
            if repository.get(delivery_id) is None:
                raise _not_found()
            delivery = repository.manual_retry(
                delivery_id,
                now=datetime.now(UTC),
                commit=False,
            )
            AuditService(AuditLogRepository(connection)).record_event(
                event_type="email.delivery.retried",
                recommendation_id=None,
                payload={"delivery_id": delivery_id},
                commit=False,
            )
            connection.commit()
            return delivery
    except ApiError:
        raise
    except EmailDeliveryNotRetryableError as exc:
        raise _not_retryable() from exc
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc
