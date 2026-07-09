from __future__ import annotations

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
        now: datetime | None = None,
    ) -> NotificationSummary:
        return NotificationSummary(
            notification_id=self._id_factory(),
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
        now: datetime | None = None,
    ) -> NotificationSummary:
        summary = self.build_summary(recommendation, audit_ref, now=now)
        return self.repository.save(summary)

    def mark_read(
        self,
        notification_id: str,
        *,
        now: datetime | None = None,
    ) -> NotificationSummary:
        del now
        return self._set_status(notification_id, NotificationStatus.READ)

    def mark_feedback_recorded(
        self,
        notification_id: str,
        *,
        now: datetime | None = None,
    ) -> NotificationSummary:
        del now
        return self._set_status(notification_id, NotificationStatus.FEEDBACK_RECORDED)

    def get(self, notification_id: str) -> NotificationSummary | None:
        return self.repository.get(notification_id)

    def _set_status(
        self,
        notification_id: str,
        status: NotificationStatus,
    ) -> NotificationSummary:
        existing = self.repository.get(notification_id)
        if existing is None:
            raise KeyError(f"notification not found: {notification_id}")
        updated = existing.model_copy(update={"status": status})
        return self.repository.save(updated)

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
