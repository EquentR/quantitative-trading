from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.market.models import MarketInputSnapshot
from quantitative_trading.market.repository import MarketInputSnapshotRepository
from quantitative_trading.planning.models import TradingPlan
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.recommendation.models import (
    Recommendation,
    RecommendationListItem,
)
from quantitative_trading.recommendation.repository import RecommendationRepository


router = APIRouter(
    prefix="/recommendations",
    tags=["recommendations"],
    dependencies=[Depends(require_token)],
)


class RecommendationListResponse(BaseModel):
    items: list[Recommendation]
    total: int
    page: int
    page_size: int


class LinkedRecommendationListResponse(BaseModel):
    items: list[RecommendationListItem]
    total: int
    page: int
    page_size: int


class RecommendationTraceResponse(BaseModel):
    recommendation: Recommendation
    audit: AuditLog | None
    plan: TradingPlan | None
    market_input_snapshot: MarketInputSnapshot | None
    references: dict[str, dict[str, object]]


def _recommendation_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="recommendation_not_found",
        message="recommendation not found",
    )


def _recommendation_storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="recommendation storage failed",
    )


@router.post("/scan", status_code=410)
def scan_recommendations() -> None:
    raise ApiError(
        status_code=410,
        code="recommendation_scan_retired",
        message="recommendation scan moved to the intraday decision workflow",
        details={"replacement": "/api/v1/service/workflows/intraday/run"},
    )


@router.get(
    "",
    response_model=RecommendationListResponse | LinkedRecommendationListResponse,
)
def list_recommendations(
    view: Literal["current", "history"] | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    container: ApiContainer = Depends(get_container),
) -> RecommendationListResponse | LinkedRecommendationListResponse:
    try:
        with connection_scope(container.settings) as connection:
            repository = RecommendationRepository(connection)
            if view is not None:
                return LinkedRecommendationListResponse(
                    items=repository.list_linked(
                        view=view,
                        limit=page_size,
                        offset=(page - 1) * page_size,
                    ),
                    total=(
                        repository.count_current()
                        if view == "current"
                        else repository.count()
                    ),
                    page=page,
                    page_size=page_size,
                )
            return RecommendationListResponse(
                items=repository.list(
                    limit=page_size,
                    offset=(page - 1) * page_size,
                ),
                total=repository.count(),
                page=page,
                page_size=page_size,
            )
    except (sqlite3.Error, ValidationError) as exc:
        raise _recommendation_storage_failed() from exc


@router.get(
    "/{recommendation_id}/trace",
    response_model=RecommendationTraceResponse,
)
def get_recommendation_trace(
    recommendation_id: str,
    container: ApiContainer = Depends(get_container),
) -> RecommendationTraceResponse:
    try:
        with connection_scope(container.settings) as connection:
            recommendation = RecommendationRepository(connection).get(recommendation_id)
            if recommendation is None:
                raise _recommendation_not_found()
            plan = (
                None
                if recommendation.plan_id is None
                else TradingPlanRepository(connection).get(recommendation.plan_id)
            )
            snapshot = (
                None
                if recommendation.market_input_snapshot_id is None
                else MarketInputSnapshotRepository(connection).get(
                    recommendation.market_input_snapshot_id
                )
            )
            return RecommendationTraceResponse(
                recommendation=recommendation,
                audit=(
                    None
                    if recommendation.audit_id is None
                    else AuditLogRepository(connection).get(recommendation.audit_id)
                ),
                plan=plan,
                market_input_snapshot=snapshot,
                references=recommendation.data_references,
            )
    except ApiError:
        raise
    except (sqlite3.Error, ValidationError) as exc:
        raise _recommendation_storage_failed() from exc


@router.get("/{recommendation_id}", response_model=Recommendation)
def get_recommendation(
    recommendation_id: str,
    container: ApiContainer = Depends(get_container),
) -> Recommendation:
    try:
        with connection_scope(container.settings) as connection:
            recommendation = RecommendationRepository(connection).get(recommendation_id)
    except (sqlite3.Error, ValidationError) as exc:
        raise _recommendation_storage_failed() from exc

    if recommendation is None:
        raise _recommendation_not_found()
    return recommendation
