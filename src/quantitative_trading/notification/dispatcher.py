from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.service import AuditService
from quantitative_trading.email.models import EmailDelivery
from quantitative_trading.email.outbox import EmailDeliveryService
from quantitative_trading.email.service import SmtpSettingsService
from quantitative_trading.notification.jsonl import JsonlNotificationWriter
from quantitative_trading.notification.models import NotificationSummary
from quantitative_trading.notification.service import NotificationService
from quantitative_trading.recommendation.models import Recommendation, RecommendationAction


IMMEDIATE_ACTIONS = frozenset(
    {
        RecommendationAction.BUY,
        RecommendationAction.ADD,
        RecommendationAction.SELL,
        RecommendationAction.REDUCE,
    }
)
DAILY_SUMMARY_ACTIONS = (
    RecommendationAction.HOLD,
    RecommendationAction.WATCH,
    RecommendationAction.AVOID,
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecommendationDispatchResult:
    notification: NotificationSummary
    email_delivery: EmailDelivery | None
    created: bool
    warnings: tuple[str, ...] = ()


class NotificationDispatcher:
    def __init__(
        self,
        *,
        notification_service: NotificationService,
        audit_service: AuditService,
        jsonl_writer: JsonlNotificationWriter,
        email_service: EmailDeliveryService,
        smtp_settings_service: SmtpSettingsService,
    ) -> None:
        self.notification_service = notification_service
        self.audit_service = audit_service
        self.jsonl_writer = jsonl_writer
        self.email_service = email_service
        self.smtp_settings_service = smtp_settings_service

    def dispatch_recommendation(
        self,
        recommendation: Recommendation,
        *,
        plan_version: str | int | None,
        now: datetime | None = None,
    ) -> RecommendationDispatchResult:
        dispatched_at = now or datetime.now(UTC)
        dedup_key = self._notification_dedup_key(recommendation, plan_version)
        existing = self.notification_service.get_by_dedup_key(dedup_key)
        if existing is not None:
            delivery, warning = self._enqueue_immediate(
                recommendation,
                existing,
                dedup_key=dedup_key,
                now=dispatched_at,
            )
            return RecommendationDispatchResult(
                notification=existing,
                email_delivery=delivery,
                created=False,
                warnings=() if warning is None else (warning,),
            )

        audit = self.audit_service.record_event(
            event_type="notification.created",
            recommendation_id=recommendation.recommendation_id,
            payload={
                "symbol": recommendation.symbol,
                "action": recommendation.action.value,
                "plan_id": recommendation.plan_id,
                "plan_version": plan_version,
                "condition_fingerprint": self.condition_fingerprint(recommendation),
            },
            now=dispatched_at,
        )
        notification = self.notification_service.create_from_recommendation(
            recommendation,
            audit,
            dedup_key=dedup_key,
            now=dispatched_at,
        )
        LOGGER.info(
            "recommendation notification_id=%s symbol=%s action=%s confidence=%s data_time=%s",
            notification.notification_id,
            recommendation.symbol,
            recommendation.action.value,
            recommendation.confidence,
            recommendation.data_time.isoformat(),
        )
        warnings: list[str] = []
        try:
            self.jsonl_writer.write(notification, recommendation, audit)
        except Exception as exc:
            warning = (
                "jsonl projection failed: "
                f"{self.smtp_settings_service.sanitized_error(exc)}"
            )
            warnings.append(warning)
            self._record_channel_failure(
                event_type="notification.jsonl_failed",
                recommendation_id=recommendation.recommendation_id,
                notification_id=notification.notification_id,
                warning=warning,
                now=dispatched_at,
            )

        delivery, warning = self._enqueue_immediate(
            recommendation,
            notification,
            dedup_key=dedup_key,
            now=dispatched_at,
        )
        if warning is not None:
            warnings.append(warning)
        return RecommendationDispatchResult(
            notification=notification,
            email_delivery=delivery,
            created=True,
            warnings=tuple(warnings),
        )

    def dispatch_daily_summary(
        self,
        *,
        plan_id: str,
        plan_version: str | int,
        recommendations: list[Recommendation],
        now: datetime | None = None,
    ) -> EmailDelivery | None:
        dispatched_at = now or datetime.now(UTC)
        recipient = self.smtp_settings_service.delivery_recipient()
        if recipient is None:
            return None
        dedup_key = f"email:daily-summary:{plan_id}:v{plan_version}"
        existing = self.email_service.get_by_dedup_key(dedup_key)
        if existing is not None:
            return existing
        grouped = {
            action.value: [
                item
                for item in recommendations
                if item.action is action
            ]
            for action in DAILY_SUMMARY_ACTIONS
        }
        counts = {action: len(items) for action, items in grouped.items()}
        payload: dict[str, Any] = {
            "plan_id": plan_id,
            "plan_version": plan_version,
            "counts": counts,
            "recommendation_ids": {
                action: [item.recommendation_id for item in items]
                for action, items in grouped.items()
            },
        }
        try:
            delivery = self.email_service.enqueue(
                dedup_key=dedup_key,
                notification_id=None,
                recipient=recipient,
                subject=f"Daily decision summary {plan_id} v{plan_version}",
                body=(
                    f"hold={counts['hold']} watch={counts['watch']} "
                    f"avoid={counts['avoid']}"
                ),
                payload=payload,
                now=dispatched_at,
            )
        except Exception as exc:
            self.audit_service.record_event(
                event_type="email.daily_summary_failed",
                recommendation_id=None,
                payload={
                    "plan_id": plan_id,
                    "plan_version": plan_version,
                    "error": self.smtp_settings_service.sanitized_error(exc),
                },
                now=dispatched_at,
            )
            return None
        self.audit_service.record_event(
            event_type="email.daily_summary.enqueued",
            recommendation_id=None,
            payload={
                "delivery_id": delivery.delivery_id,
                "plan_id": plan_id,
                "plan_version": plan_version,
                "counts": counts,
            },
            now=dispatched_at,
        )
        return delivery

    def dispatch_system_alert(
        self,
        *,
        alert_key: str,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> EmailDelivery | None:
        dispatched_at = now or datetime.now(UTC)
        dedup_key = f"email:system-alert:{alert_key}"
        existing = self.email_service.get_by_dedup_key(dedup_key)
        if existing is not None:
            return existing
        audit = self.audit_service.record_event(
            event_type=event_type,
            recommendation_id=None,
            payload={"alert_key": alert_key, "message": message, "details": details or {}},
            now=dispatched_at,
        )
        recipient = self.smtp_settings_service.delivery_recipient()
        if recipient is None:
            return None
        try:
            return self.email_service.enqueue(
                dedup_key=dedup_key,
                notification_id=None,
                recipient=recipient,
                subject=f"Critical system alert: {event_type}",
                body=message,
                payload={
                    "alert_key": alert_key,
                    "event_type": event_type,
                    "details": details or {},
                    "audit_id": audit.audit_id,
                },
                now=dispatched_at,
            )
        except Exception as exc:
            self.audit_service.record_event(
                event_type="email.system_alert_failed",
                recommendation_id=None,
                payload={
                    "alert_key": alert_key,
                    "error": self.smtp_settings_service.sanitized_error(exc),
                },
                now=dispatched_at,
            )
            return None

    @staticmethod
    def condition_fingerprint(recommendation: Recommendation) -> str:
        material_conditions = recommendation.model_dump(
            mode="json",
            include={"reason", "risk", "position_constraint"},
        )
        canonical = json.dumps(
            material_conditions,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _notification_dedup_key(
        self,
        recommendation: Recommendation,
        plan_version: str | int | None,
    ) -> str:
        cycle = recommendation.run_id
        if cycle is None:
            created_at = recommendation.created_at
            cycle = created_at.replace(
                minute=created_at.minute - created_at.minute % 3,
                second=0,
                microsecond=0,
            ).isoformat()
        return ":".join(
            (
                "notification",
                recommendation.symbol,
                recommendation.action.value,
                recommendation.plan_id or "no-plan",
                f"v{plan_version if plan_version is not None else 'none'}",
                cycle,
                self.condition_fingerprint(recommendation),
            )
        )

    def _enqueue_immediate(
        self,
        recommendation: Recommendation,
        notification: NotificationSummary,
        *,
        dedup_key: str,
        now: datetime,
    ) -> tuple[EmailDelivery | None, str | None]:
        if recommendation.action not in IMMEDIATE_ACTIONS:
            return None, None
        recipient = self.smtp_settings_service.delivery_recipient()
        if recipient is None:
            return None, "email channel is not configured or enabled"
        try:
            delivery = self.email_service.enqueue(
                dedup_key=f"email:recommendation:{dedup_key}",
                notification_id=notification.notification_id,
                recipient=recipient,
                subject=(
                    f"Decision {recommendation.action.value}: "
                    f"{recommendation.symbol}"
                ),
                body=self._recommendation_email_body(recommendation),
                payload={
                    "recommendation_id": recommendation.recommendation_id,
                    "symbol": recommendation.symbol,
                    "action": recommendation.action.value,
                    "reason": recommendation.reason,
                    "risk": recommendation.risk,
                    "data_time": recommendation.data_time.isoformat(),
                },
                now=now,
            )
        except Exception as exc:
            warning = (
                "email outbox failed: "
                f"{self.smtp_settings_service.sanitized_error(exc)}"
            )
            self._record_channel_failure(
                event_type="email.outbox_failed",
                recommendation_id=recommendation.recommendation_id,
                notification_id=notification.notification_id,
                warning=warning,
                now=now,
            )
            return None, warning
        return delivery, None

    def _record_channel_failure(
        self,
        *,
        event_type: str,
        recommendation_id: str,
        notification_id: str,
        warning: str,
        now: datetime,
    ) -> AuditLog:
        return self.audit_service.record_event(
            event_type=event_type,
            recommendation_id=recommendation_id,
            payload={"notification_id": notification_id, "warning": warning},
            now=now,
        )

    @staticmethod
    def _recommendation_email_body(recommendation: Recommendation) -> str:
        invalid_if = recommendation.risk.get("invalid_if", [])
        return "\n".join(
            (
                f"symbol={recommendation.symbol}",
                f"action={recommendation.action.value}",
                f"confidence={recommendation.confidence}",
                "reason=" + "; ".join(recommendation.reason),
                "invalid_if=" + "; ".join(str(item) for item in invalid_if),
                f"data_time={recommendation.data_time.isoformat()}",
            )
        )
