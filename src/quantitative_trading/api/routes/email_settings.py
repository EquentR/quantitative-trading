from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends
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
from quantitative_trading.email.models import SmtpSettingsPublic, SmtpSettingsUpdate
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.email.service import (
    EmailSender,
    SmtplibEmailSender,
    SmtpConnectionTester,
    SmtpSettingsService,
    sanitized_email_error,
)


router = APIRouter(
    prefix="/settings/notifications/email",
    tags=["email-settings"],
    dependencies=[Depends(require_token)],
)

connection_test_router = APIRouter(
    prefix="/notifications/email/settings",
    tags=["email-settings"],
    dependencies=[Depends(require_token)],
)


class EmailTestResponse(BaseModel):
    status: str


def _storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="email settings storage failed",
    )


def _sender(container: ApiContainer) -> EmailSender:
    return container.email_sender or SmtplibEmailSender()  # type: ignore[return-value]


def _connection_tester(container: ApiContainer) -> SmtpConnectionTester:
    return container.smtp_connection_tester or SmtplibEmailSender()  # type: ignore[return-value]


@router.get("", response_model=SmtpSettingsPublic)
def get_settings(container: ApiContainer = Depends(get_container)) -> SmtpSettingsPublic:
    try:
        with connection_scope(container.settings) as connection:
            return SmtpSettingsService(SmtpSettingsRepository(connection)).get_public()
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc


@router.put("", response_model=SmtpSettingsPublic)
def update_settings(
    request: SmtpSettingsUpdate,
    container: ApiContainer = Depends(get_container),
) -> SmtpSettingsPublic:
    try:
        with connection_scope(container.settings) as connection:
            public = SmtpSettingsService(SmtpSettingsRepository(connection)).update(
                request,
                commit=False,
            )
            AuditService(AuditLogRepository(connection)).record_event(
                event_type="smtp.settings.updated",
                recommendation_id=None,
                payload={
                    "enabled": public.enabled,
                    "host": public.host,
                    "port": public.port,
                    "security": public.security.value,
                    "password_configured": public.password_configured,
                },
                commit=False,
            )
            connection.commit()
            return public
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc


@router.delete("/password", response_model=SmtpSettingsPublic)
def clear_password(
    container: ApiContainer = Depends(get_container),
) -> SmtpSettingsPublic:
    try:
        with connection_scope(container.settings) as connection:
            public = SmtpSettingsService(SmtpSettingsRepository(connection)).clear_password(
                commit=False
            )
            AuditService(AuditLogRepository(connection)).record_event(
                event_type="smtp.password.cleared",
                recommendation_id=None,
                payload={"configured": public.configured},
                commit=False,
            )
            connection.commit()
            return public
    except (sqlite3.Error, ValidationError) as exc:
        raise _storage_failed() from exc


@router.post("/test", response_model=EmailTestResponse)
def test_settings(
    container: ApiContainer = Depends(get_container),
) -> EmailTestResponse:
    with connection_scope(container.settings) as connection:
        repository = SmtpSettingsRepository(connection)
        settings = repository.get()
        if settings is None:
            raise ApiError(
                status_code=409,
                code="smtp_not_configured",
                message="SMTP settings are not configured",
            )
        secret_texts = (settings.password,) if settings.password else ()
        audit_service = AuditService(
            AuditLogRepository(connection),
            configured_secret_texts=secret_texts,
        )
        try:
            _sender(container).send(
                settings,
                recipient=settings.recipient,
                subject="Quantitative Trading email test",
                body="SMTP configuration test succeeded.",
            )
        except Exception as exc:
            error = sanitized_email_error(exc, secret_texts=secret_texts)
            audit_service.record_event(
                event_type="smtp.test.failed",
                recommendation_id=None,
                payload={"error": error},
            )
            raise ApiError(
                status_code=502,
                code="smtp_test_failed",
                message="SMTP test failed",
                details={"reason": error},
            ) from exc
        audit_service.record_event(
            event_type="smtp.test.succeeded",
            recommendation_id=None,
            payload={"recipient": settings.recipient},
        )
        return EmailTestResponse(status="sent")


@connection_test_router.post("/test-connection", response_model=EmailTestResponse)
def test_connection(
    container: ApiContainer = Depends(get_container),
) -> EmailTestResponse:
    with connection_scope(container.settings) as connection:
        repository = SmtpSettingsRepository(connection)
        settings = repository.get()
        if settings is None:
            raise ApiError(
                status_code=409,
                code="smtp_not_configured",
                message="SMTP settings are not configured",
            )
        secret_texts = (settings.password,) if settings.password else ()
        audit_service = AuditService(
            AuditLogRepository(connection),
            configured_secret_texts=secret_texts,
        )
        try:
            SmtpSettingsService(repository).test_connection(
                _connection_tester(container)
            )
        except Exception as exc:
            error = sanitized_email_error(exc, secret_texts=secret_texts)
            audit_service.record_event(
                event_type="smtp.connection_test.failed",
                recommendation_id=None,
                payload={"error": error},
            )
            raise ApiError(
                status_code=502,
                code="smtp_connection_test_failed",
                message="SMTP connection test failed",
                details={"reason": error},
            ) from exc
        audit_service.record_event(
            event_type="smtp.connection_test.succeeded",
            recommendation_id=None,
            payload={
                "host": settings.host,
                "port": settings.port,
                "security": settings.security.value,
            },
        )
        return EmailTestResponse(status="connected")
