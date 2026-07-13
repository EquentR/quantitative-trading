from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.notification.models import NotificationStatus, NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.recommendation.models import Recommendation
from quantitative_trading.sanitization import sanitize_sensitive_data


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self._id_factory = id_factory or (lambda: f"notification-{uuid4().hex}")

    def build_summary(
        self,
        recommendation: Recommendation,
        audit_ref: AuditLog,
        *,
        dedup_key: str | None = None,
        now: datetime | None = None,
    ) -> NotificationSummary:
        return NotificationSummary(
            notification_id=self._id_factory(),
            dedup_key=dedup_key,
            recommendation_id=recommendation.recommendation_id,
            symbol=recommendation.symbol,
            action=recommendation.action.value,
            confidence=recommendation.confidence,
            key_price=self._current_price_or_none(recommendation),
            reason=list(recommendation.reason),
            risk=self._flatten_risk(recommendation),
            data_time=recommendation.data_time,
            audit_id=audit_ref.audit_id,
            status=NotificationStatus.UNREAD,
            created_at=now or datetime.now(UTC),
        )

    def create_from_recommendation(
        self,
        recommendation: Recommendation,
        audit_ref: AuditLog,
        *,
        dedup_key: str | None = None,
        now: datetime | None = None,
    ) -> NotificationSummary:
        if dedup_key is not None:
            existing = self.repository.get_by_dedup_key(dedup_key)
            if existing is not None:
                return existing
        summary = self.build_summary(
            recommendation,
            audit_ref,
            dedup_key=dedup_key,
            now=now,
        )
        try:
            return self.repository.save(summary)
        except sqlite3.IntegrityError:
            if dedup_key is None:
                raise
            self.repository.connection.rollback()
            existing = self.repository.get_by_dedup_key(dedup_key)
            if existing is None:
                raise
            return existing

    def create_system_alert(
        self,
        *,
        alert_key: str,
        message: str,
        audit_ref: AuditLog,
        dedup_key: str,
        now: datetime | None = None,
    ) -> NotificationSummary:
        existing = self.repository.get_by_dedup_key(dedup_key)
        if existing is not None:
            return existing
        created_at = now or datetime.now(UTC)
        summary = NotificationSummary(
            notification_id=self._id_factory(),
            dedup_key=dedup_key,
            recommendation_id=f"system-alert:{alert_key}",
            symbol="000000",
            action="system_alert",
            confidence="critical",
            key_price=None,
            reason=[message],
            risk=["Review the service state and audit details before retrying."],
            data_time=created_at,
            audit_id=audit_ref.audit_id,
            status=NotificationStatus.UNREAD,
            created_at=created_at,
        )
        try:
            return self.repository.save(summary)
        except sqlite3.IntegrityError:
            self.repository.connection.rollback()
            existing = self.repository.get_by_dedup_key(dedup_key)
            if existing is None:
                raise
            return existing

    def mark_read(
        self,
        notification_id: str,
        *,
        now: datetime | None = None,
        commit: bool = True,
    ) -> NotificationSummary:
        del now
        return self._set_status(notification_id, NotificationStatus.READ, commit=commit)

    def mark_feedback_recorded(
        self,
        notification_id: str,
        *,
        now: datetime | None = None,
        commit: bool = True,
    ) -> NotificationSummary:
        del now
        return self._set_status(
            notification_id,
            NotificationStatus.FEEDBACK_RECORDED,
            commit=commit,
        )

    def get(self, notification_id: str) -> NotificationSummary | None:
        return self.repository.get(notification_id)

    def get_by_dedup_key(self, dedup_key: str) -> NotificationSummary | None:
        return self.repository.get_by_dedup_key(dedup_key)

    def list_notifications(
        self,
        *,
        status: NotificationStatus | None = None,
        symbol: str | None = None,
        action: str | None = None,
        recommendation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationSummary]:
        return self.repository.list(
            status=status,
            symbol=symbol,
            action=action,
            recommendation_id=recommendation_id,
            limit=limit,
            offset=offset,
        )

    def unread_count(self) -> int:
        return self.repository.count_unread()

    def _set_status(
        self,
        notification_id: str,
        status: NotificationStatus,
        *,
        commit: bool = True,
    ) -> NotificationSummary:
        existing = self.repository.get(notification_id)
        if existing is None:
            raise KeyError(f"notification not found: {notification_id}")
        if (
            status is NotificationStatus.READ
            and existing.status is NotificationStatus.FEEDBACK_RECORDED
        ):
            return existing
        if existing.status is status:
            return existing
        updated = existing.model_copy(update={"status": status})
        return self.repository.save(updated, commit=commit)

    @staticmethod
    def _current_price_or_none(recommendation: Recommendation) -> float | None:
        price = recommendation.price_context.get("current_price")
        if isinstance(price, bool):
            return None
        if isinstance(price, int | float):
            return float(price)
        return None

    @staticmethod
    def _flatten_risk(recommendation: Recommendation) -> list[str]:
        risk: list[str] = []
        for field_name in ("invalid_if", "notes"):
            values = recommendation.risk.get(field_name)
            if not isinstance(values, list):
                continue
            risk.extend(
                sanitize_sensitive_data(value)
                for value in values
                if isinstance(value, str)
            )
        return risk
