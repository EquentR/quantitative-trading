from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.feedback.models import ExecutionFeedback, ExecutionFeedbackInput
from quantitative_trading.feedback.repository import FeedbackRepository
from quantitative_trading.feedback.service import FeedbackService
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.notification.service import NotificationService


router = APIRouter(
    prefix="/feedback",
    tags=["feedback"],
    dependencies=[Depends(require_token)],
)


def _current_time() -> datetime:
    return datetime.now(UTC)


def _feedback_storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="feedback storage failed",
    )


@router.post("", response_model=ExecutionFeedback, status_code=201)
def record_feedback(
    payload: ExecutionFeedbackInput,
    container: ApiContainer = Depends(get_container),
) -> ExecutionFeedback:
    try:
        with connection_scope(container.settings) as connection:
            feedback = FeedbackService(FeedbackRepository(connection)).record(
                payload,
                now=_current_time(),
            )
            _mark_matching_notifications_feedback_recorded(
                connection,
                recommendation_id=feedback.recommendation_id,
            )
            return feedback
    except (sqlite3.Error, ValidationError) as exc:
        raise _feedback_storage_failed() from exc


@router.get("", response_model=list[ExecutionFeedback])
def list_feedback(
    recommendation_id: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
    container: ApiContainer = Depends(get_container),
) -> list[ExecutionFeedback]:
    try:
        with connection_scope(container.settings) as connection:
            return FeedbackService(FeedbackRepository(connection)).list(
                recommendation_id=recommendation_id,
                limit=limit,
            )
    except (sqlite3.Error, ValidationError) as exc:
        raise _feedback_storage_failed() from exc


def _mark_matching_notifications_feedback_recorded(
    connection: sqlite3.Connection,
    *,
    recommendation_id: str,
) -> None:
    repository = NotificationRepository(connection)
    service = NotificationService(repository)
    for notification in repository.list_by_recommendation_id(recommendation_id):
        service.mark_feedback_recorded(notification.notification_id)
