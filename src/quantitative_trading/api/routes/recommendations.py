from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.recommendation.models import Recommendation
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.recommendation.scanner import scan_latest_plan_recommendations


router = APIRouter(
    prefix="/recommendations",
    tags=["recommendations"],
    dependencies=[Depends(require_token)],
)


class RecommendationScanResponse(BaseModel):
    count: int
    recommendations: list[Recommendation]


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


@router.post("/scan", response_model=RecommendationScanResponse, status_code=201)
def scan_recommendations(
    container: ApiContainer = Depends(get_container),
) -> RecommendationScanResponse:
    try:
        with connection_scope(container.settings) as connection:
            scan = scan_latest_plan_recommendations(connection, now=_current_time())
    except (sqlite3.Error, ValidationError) as exc:
        raise _recommendation_storage_failed() from exc

    if scan is None:
        raise _plan_not_found()
    return RecommendationScanResponse(
        count=len(scan.recommendations),
        recommendations=scan.recommendations,
    )


@router.get("", response_model=list[Recommendation])
def list_recommendations(
    container: ApiContainer = Depends(get_container),
) -> list[Recommendation]:
    try:
        with connection_scope(container.settings) as connection:
            return RecommendationRepository(connection).list()
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
