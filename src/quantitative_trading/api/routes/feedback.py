from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
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


class FeedbackListResponse(BaseModel):
    items: list[ExecutionFeedback]
    total: int
    page: int
    page_size: int


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
            try:
                connection.execute("BEGIN IMMEDIATE")
                feedback = FeedbackService(FeedbackRepository(connection)).record(
                    payload,
                    now=_current_time(),
                    commit=False,
                )
                _mark_matching_notifications_feedback_recorded(
                    connection,
                    recommendation_id=feedback.recommendation_id,
                )
                AuditService(AuditLogRepository(connection)).record_event(
                    event_type="feedback.recorded",
                    recommendation_id=feedback.recommendation_id,
                    payload={
                        "feedback_id": feedback.feedback_id,
                        "executed": feedback.executed,
                    },
                    now=feedback.created_at,
                    commit=False,
                )
                connection.commit()
                return feedback
            except (sqlite3.Error, ValidationError):
                connection.rollback()
                raise
    except (sqlite3.Error, ValidationError) as exc:
        raise _feedback_storage_failed() from exc


@router.get("", response_model=FeedbackListResponse)
def list_feedback(
    recommendation_id: Annotated[str | None, Query(min_length=1)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(gt=0, le=200)] = 50,
    container: ApiContainer = Depends(get_container),
) -> FeedbackListResponse:
    try:
        with connection_scope(container.settings) as connection:
            repository = FeedbackRepository(connection)
            return FeedbackListResponse(
                items=repository.list(
                    recommendation_id=recommendation_id,
                    limit=page_size,
                    offset=(page - 1) * page_size,
                ),
                total=repository.count(recommendation_id=recommendation_id),
                page=page,
                page_size=page_size,
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
        service.mark_feedback_recorded(notification.notification_id, commit=False)
