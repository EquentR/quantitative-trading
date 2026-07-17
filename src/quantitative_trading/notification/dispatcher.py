from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.service import AuditService
from quantitative_trading.email.models import EmailDelivery
from quantitative_trading.email.outbox import EmailDeliveryService
from quantitative_trading.email.service import SmtpSettingsService
from quantitative_trading.notification.jsonl import JsonlNotificationWriter
from quantitative_trading.notification.identity import notification_canonical_key
from quantitative_trading.notification.local_alert import LocalAlertDispatcher
from quantitative_trading.notification.models import NotificationSummary
from quantitative_trading.notification.service import NotificationService
from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.recommendation.identity import (
    CONDITION_FINGERPRINT_VERSION,
    recommendation_condition_fingerprint,
)


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
SHANGHAI = ZoneInfo("Asia/Shanghai")


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
        local_alert_dispatcher: LocalAlertDispatcher | None = None,
    ) -> None:
        self.notification_service = notification_service
        self.audit_service = audit_service
        self.jsonl_writer = jsonl_writer
        self.email_service = email_service
        self.smtp_settings_service = smtp_settings_service
        self.local_alert_dispatcher = local_alert_dispatcher or LocalAlertDispatcher(
            notification_service=notification_service,
            audit_service=audit_service,
            jsonl_writer=jsonl_writer,
        )

    def dispatch_recommendation(
        self,
        recommendation: Recommendation,
        *,
        plan_version: str | int | None,
        now: datetime | None = None,
    ) -> RecommendationDispatchResult:
        local = self.persist_local_recommendation(
            recommendation,
            plan_version=plan_version,
            now=now,
        )
        return self.project_recommendation(
            recommendation,
            local,
            plan_version=plan_version,
            now=now,
        )

    def persist_local_recommendation(
        self,
        recommendation: Recommendation,
        *,
        plan_version: str | int | None,
        now: datetime | None = None,
        commit: bool = True,
    ) -> RecommendationDispatchResult:
        dispatched_at = now or datetime.now(UTC)
        dedup_key = self._notification_dedup_key(
            recommendation,
            plan_version,
            dispatched_at=dispatched_at,
        )
        repository = self.notification_service.repository
        standalone_transaction = commit and not repository.connection.in_transaction
        if standalone_transaction:
            repository.connection.execute("BEGIN IMMEDIATE")
        group = repository.get_canonical_group(dedup_key)
        if group is not None:
            existing = self.notification_service.get(group.notification_id)
            if existing is None:
                if standalone_transaction:
                    repository.connection.rollback()
                raise RuntimeError("canonical notification reference is missing")
            try:
                self._link_persisted_recommendation(
                    recommendation,
                    existing.notification_id,
                    dedup_key,
                    now=dispatched_at,
                    commit=False,
                )
                if commit:
                    repository.connection.commit()
            except BaseException:
                if standalone_transaction:
                    repository.connection.rollback()
                raise
            return RecommendationDispatchResult(
                notification=existing,
                email_delivery=None,
                created=False,
            )

        savepoint = "persist_local_recommendation"
        repository.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            group = repository.get_canonical_group(dedup_key)
            if group is not None:
                existing = self.notification_service.get(group.notification_id)
                if existing is None:
                    raise RuntimeError("canonical notification reference is missing")
                self._link_persisted_recommendation(
                    recommendation,
                    existing.notification_id,
                    dedup_key,
                    now=dispatched_at,
                    commit=False,
                )
                repository.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                if commit:
                    repository.connection.commit()
                return RecommendationDispatchResult(
                    notification=existing,
                    email_delivery=None,
                    created=False,
                )
            audit = self.audit_service.record_event(
                event_type="notification.created",
                recommendation_id=recommendation.recommendation_id,
                payload={
                    "symbol": recommendation.symbol,
                    "action": recommendation.action.value,
                    "plan_id": recommendation.plan_id,
                    "plan_version": plan_version,
                    "condition_fingerprint": self.condition_fingerprint(
                        recommendation
                    ),
                    "condition_fingerprint_version": CONDITION_FINGERPRINT_VERSION,
                },
                now=dispatched_at,
                commit=False,
            )
            notification = self.notification_service.build_summary(
                recommendation,
                audit,
                dedup_key=dedup_key,
                now=dispatched_at,
            )
            repository.save(notification, commit=False)
            repository.save_canonical_group(
                dedup_key,
                notification.notification_id,
                created_at=dispatched_at,
                commit=False,
            )
            self._link_persisted_recommendation(
                recommendation,
                notification.notification_id,
                dedup_key,
                now=dispatched_at,
                commit=False,
            )
            repository.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            if commit:
                repository.connection.commit()
        except sqlite3.IntegrityError:
            repository.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            repository.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            if self.notification_service.get_by_dedup_key(dedup_key) is None:
                if standalone_transaction:
                    repository.connection.rollback()
                raise
            try:
                return self._recover_canonical_notification(
                    recommendation,
                    dedup_key,
                    now=dispatched_at,
                    commit=commit,
                )
            except BaseException:
                if standalone_transaction:
                    repository.connection.rollback()
                raise
        except BaseException:
            repository.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            repository.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            if standalone_transaction:
                repository.connection.rollback()
            raise
        return RecommendationDispatchResult(
            notification=notification,
            email_delivery=None,
            created=True,
        )

    def project_recommendation(
        self,
        recommendation: Recommendation,
        local: RecommendationDispatchResult,
        *,
        plan_version: str | int | None,
        now: datetime | None = None,
    ) -> RecommendationDispatchResult:
        dispatched_at = now or datetime.now(UTC)
        dedup_key = self._notification_dedup_key(
            recommendation,
            plan_version,
            dispatched_at=dispatched_at,
        )
        notification = local.notification
        audit = self.audit_service.get(notification.audit_id)
        if audit is None:
            raise RuntimeError("notification audit reference is missing")
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
            created=local.created,
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
        notification = self.local_alert_dispatcher.dispatch(
            alert_key=alert_key,
            event_type=event_type,
            message=message,
            details=details,
            now=dispatched_at,
        )
        dedup_key = f"email:system-alert:{alert_key}"
        existing = self.email_service.get_by_dedup_key(dedup_key)
        if existing is not None:
            return existing
        recipient = self.smtp_settings_service.delivery_recipient()
        if recipient is None:
            return None
        try:
            return self.email_service.enqueue(
                dedup_key=dedup_key,
                notification_id=notification.notification_id,
                recipient=recipient,
                subject=f"Critical system alert: {event_type}",
                body=message,
                payload={
                    "alert_key": alert_key,
                    "event_type": event_type,
                    "details": details or {},
                    "audit_id": notification.audit_id,
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
        if (
            recommendation.condition_fingerprint_version
            == CONDITION_FINGERPRINT_VERSION
            and recommendation.condition_fingerprint is not None
        ):
            return recommendation.condition_fingerprint
        return recommendation_condition_fingerprint(recommendation)

    def _notification_dedup_key(
        self,
        recommendation: Recommendation,
        plan_version: str | int | None,
        *,
        dispatched_at: datetime,
    ) -> str:
        trade_date = recommendation.decision_trade_date
        if trade_date is None:
            trade_date = dispatched_at.astimezone(SHANGHAI).date()
        return notification_canonical_key(
            recommendation,
            trade_date=trade_date,
            plan_version=plan_version,
            condition_fingerprint=self.condition_fingerprint(recommendation),
        )

    def _recover_canonical_notification(
        self,
        recommendation: Recommendation,
        canonical_key: str,
        *,
        now: datetime,
        commit: bool,
    ) -> RecommendationDispatchResult:
        repository = self.notification_service.repository
        existing = self.notification_service.get_by_dedup_key(canonical_key)
        if existing is None:
            raise sqlite3.IntegrityError(
                "notification conflict did not produce a canonical notification"
            )
        savepoint = "recover_canonical_notification"
        repository.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            group = repository.get_canonical_group(canonical_key)
            if group is None:
                repository.save_canonical_group(
                    canonical_key,
                    existing.notification_id,
                    created_at=existing.created_at,
                    commit=False,
                )
            elif group.notification_id != existing.notification_id:
                raise sqlite3.IntegrityError("canonical notification group conflicts")
            self._link_persisted_recommendation(
                recommendation,
                existing.notification_id,
                canonical_key,
                now=now,
                commit=False,
            )
            repository.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            if commit:
                repository.connection.commit()
        except BaseException:
            repository.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            repository.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        return RecommendationDispatchResult(
            notification=existing,
            email_delivery=None,
            created=False,
        )

    def _link_persisted_recommendation(
        self,
        recommendation: Recommendation,
        notification_id: str,
        canonical_key: str,
        *,
        now: datetime,
        commit: bool,
    ) -> None:
        repository = self.notification_service.repository
        exists = repository.connection.execute(
            "SELECT 1 FROM recommendations WHERE recommendation_id = ?",
            (recommendation.recommendation_id,),
        ).fetchone()
        if exists is None:
            return
        repository.link_recommendation(
            recommendation.recommendation_id,
            notification_id,
            canonical_key,
            created_at=now,
            commit=commit,
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
