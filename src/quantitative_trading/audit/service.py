from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.sanitization import sanitize_sensitive_data


class AuditService:
    def __init__(
        self,
        repository: AuditLogRepository,
        *,
        id_factory: Callable[[], str] | None = None,
        configured_secret_texts: tuple[str, ...] = (),
    ) -> None:
        self.repository = repository
        self._id_factory = id_factory or (lambda: f"audit-{uuid4().hex}")
        self._configured_secret_texts = configured_secret_texts

    def record_event(
        self,
        *,
        event_type: str,
        recommendation_id: str | None,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> AuditLog:
        created_at = now or datetime.now(UTC)
        audit = AuditLog(
            audit_id=self._id_factory(),
            event_type=event_type,
            recommendation_id=recommendation_id,
            payload=sanitize_sensitive_data(
                payload or {},
                configured_secret_texts=self._configured_secret_texts,
            ),
            created_at=created_at,
        )
        return self.repository.save(audit)

    def get(self, audit_id: str) -> AuditLog | None:
        return self.repository.get(audit_id)
