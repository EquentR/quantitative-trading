from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.market.models import MarketInputSnapshot
from quantitative_trading.market.repository import MarketInputSnapshotRepository
from quantitative_trading.planning.models import TradingPlan
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.recommendation.models import Recommendation
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.recommendation.scanner import (
    PlanNotScannableError,
    scan_latest_plan_recommendations,
)


router = APIRouter(
    prefix="/recommendations",
    tags=["recommendations"],
    dependencies=[Depends(require_token)],
)


class RecommendationScanResponse(BaseModel):
    count: int
    recommendations: list[Recommendation]


class RecommendationListResponse(BaseModel):
    items: list[Recommendation]
    total: int
    page: int
    page_size: int


class RecommendationTraceResponse(BaseModel):
    recommendation: Recommendation
    plan: TradingPlan | None
    market_input_snapshot: MarketInputSnapshot | None
    references: dict[str, dict[str, object]]


def _current_time() -> datetime:
    return datetime.now(UTC)


def _plan_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="plan_not_found",
        message="trading plan not found",
    )


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


def _plan_not_scannable(exc: PlanNotScannableError) -> ApiError:
    return ApiError(
        status_code=422,
        code="plan_not_scannable",
        message="trading plan is not scannable",
        details={
            "plan_id": exc.plan_id,
            "status": exc.status.value,
            "valid_until": exc.valid_until.isoformat(),
        },
    )


@router.post("/scan", response_model=RecommendationScanResponse, status_code=201)
def scan_recommendations(
    container: ApiContainer = Depends(get_container),
) -> RecommendationScanResponse:
    try:
        with connection_scope(container.settings) as connection:
            scan = scan_latest_plan_recommendations(connection, now=_current_time())
    except PlanNotScannableError as exc:
        raise _plan_not_scannable(exc) from exc
    except (sqlite3.Error, ValidationError) as exc:
        raise _recommendation_storage_failed() from exc

    if scan is None:
        raise _plan_not_found()
    return RecommendationScanResponse(
        count=len(scan.recommendations),
        recommendations=scan.recommendations,
    )


@router.get("", response_model=list[Recommendation] | RecommendationListResponse)
def list_recommendations(
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
    container: ApiContainer = Depends(get_container),
) -> list[Recommendation] | RecommendationListResponse:
    try:
        with connection_scope(container.settings) as connection:
            repository = RecommendationRepository(connection)
            if page is None and page_size is None:
                return repository.list()
            selected_page = page or 1
            selected_page_size = page_size or 20
            return RecommendationListResponse(
                items=repository.list(
                    limit=selected_page_size,
                    offset=(selected_page - 1) * selected_page_size,
                ),
                total=repository.count(),
                page=selected_page,
                page_size=selected_page_size,
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
