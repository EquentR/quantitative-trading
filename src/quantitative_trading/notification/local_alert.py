from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.service import AuditService
from quantitative_trading.email.models import EmailDelivery
from quantitative_trading.notification.jsonl import JsonlNotificationWriter
from quantitative_trading.notification.models import NotificationSummary
from quantitative_trading.notification.service import NotificationService
from quantitative_trading.sanitization import safe_error_summary, sanitize_sensitive_data


LOGGER = logging.getLogger(__name__)


class LocalAlertDispatcher:
    """Projects operational failures to the local notification channels."""

    def __init__(
        self,
        *,
        notification_service: NotificationService,
        audit_service: AuditService,
        jsonl_writer: JsonlNotificationWriter,
        configured_secret_texts: tuple[str, ...] = (),
    ) -> None:
        self.notification_service = notification_service
        self.audit_service = audit_service
        self.jsonl_writer = jsonl_writer
        self.configured_secret_texts = configured_secret_texts

    def dispatch(
        self,
        *,
        alert_key: str,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
        now: datetime | None = None,
        audit_ref: AuditLog | None = None,
    ) -> NotificationSummary:
        dispatched_at = now or datetime.now(UTC)
        sanitized_alert_key = sanitize_sensitive_data(
            alert_key,
            configured_secret_texts=self.configured_secret_texts,
        )
        dedup_key = f"notification:system-alert:{sanitized_alert_key}"
        existing = self.notification_service.get_by_dedup_key(dedup_key)
        if existing is not None:
            return existing

        sanitized_message = sanitize_sensitive_data(
            message,
            configured_secret_texts=self.configured_secret_texts,
        )
        sanitized_details = sanitize_sensitive_data(
            details or {},
            configured_secret_texts=self.configured_secret_texts,
        )
        audit = audit_ref or self.audit_service.record_event(
            event_type=event_type,
            recommendation_id=None,
            payload={
                "alert_key": sanitized_alert_key,
                "message": sanitized_message,
                "details": sanitized_details,
            },
            now=dispatched_at,
        )
        summary = self.notification_service.create_system_alert(
            alert_key=sanitized_alert_key,
            message=sanitized_message,
            audit_ref=audit,
            dedup_key=dedup_key,
            now=dispatched_at,
        )
        LOGGER.error(
            "system_alert event_type=%s alert_key=%s notification_id=%s message=%s",
            event_type,
            sanitized_alert_key,
            summary.notification_id,
            sanitized_message,
        )
        try:
            self.jsonl_writer.write_system_alert(
                summary,
                audit,
                event_type=event_type,
                alert_key=sanitized_alert_key,
                message=sanitized_message,
                details=sanitized_details,
            )
        except Exception as exc:
            self.audit_service.record_event(
                event_type="notification.system_alert_jsonl_failed",
                recommendation_id=None,
                payload={
                    "notification_id": summary.notification_id,
                    "error": safe_error_summary(exc),
                },
                now=dispatched_at,
            )
        return summary

    def dispatch_dead_email(
        self,
        *,
        delivery: EmailDelivery,
        audit_ref: AuditLog,
        now: datetime,
    ) -> NotificationSummary:
        return self.dispatch(
            alert_key=f"email-delivery-dead:{delivery.delivery_id}",
            event_type="email.delivery.dead",
            message="Email delivery permanently failed",
            details={
                "delivery_id": delivery.delivery_id,
                "attempt_count": delivery.attempt_count,
                "error": delivery.last_error,
            },
            now=now,
            audit_ref=audit_ref,
        )
