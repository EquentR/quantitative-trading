from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from quantitative_trading.feedback.models import ExecutionFeedback, ExecutionFeedbackInput
from quantitative_trading.feedback.repository import FeedbackRepository
from quantitative_trading.sanitization import sanitize_sensitive_data


class FeedbackService:
    def __init__(
        self,
        repository: FeedbackRepository,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self._id_factory = id_factory or (lambda: f"feedback-{uuid4().hex}")

    def record(
        self,
        payload: ExecutionFeedbackInput,
        *,
        now: datetime | None = None,
    ) -> ExecutionFeedback:
        payload = ExecutionFeedbackInput.model_validate(payload)
        feedback = ExecutionFeedback(
            feedback_id=self._id_factory(),
            recommendation_id=payload.recommendation_id,
            executed=payload.executed,
            execution_price=payload.execution_price,
            execution_quantity=payload.execution_quantity,
            note=sanitize_sensitive_data(payload.note),
            created_at=now or datetime.now(UTC),
        )
        return self.repository.save(feedback)

    def list(
        self,
        *,
        recommendation_id: str | None = None,
        limit: int = 50,
    ) -> list[ExecutionFeedback]:
        return self.repository.list(recommendation_id=recommendation_id, limit=limit)
