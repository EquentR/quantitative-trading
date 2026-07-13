from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.planning.models import TradingPlan
from quantitative_trading.planning.repository import TradingPlanRepository


router = APIRouter(
    prefix="/plans",
    tags=["plans"],
    dependencies=[Depends(require_token)],
)


class PlanListResponse(BaseModel):
    items: list[TradingPlan]
    total: int
    page: int
    page_size: int


def _plan_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="plan_not_found",
        message="trading plan not found",
    )


def _plan_storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="trading plan storage failed",
    )


def _plan_write_deprecated() -> ApiError:
    return ApiError(
        status_code=410,
        code="plan_write_deprecated",
        message="direct plan generation is deprecated; use the close decision workflow",
        details={
            "api_workflow": "close",
            "cli": "qt workflow close",
        },
    )


@router.post("", status_code=410)
def reject_legacy_plan_write() -> None:
    raise _plan_write_deprecated()


@router.get("", response_model=PlanListResponse)
def list_plans(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    container: ApiContainer = Depends(get_container),
) -> PlanListResponse:
    try:
        with connection_scope(container.settings) as connection:
            repository = TradingPlanRepository(connection)
            return PlanListResponse(
                items=repository.list(
                    limit=page_size,
                    offset=(page - 1) * page_size,
                ),
                total=repository.count(),
                page=page,
                page_size=page_size,
            )
    except (sqlite3.Error, ValidationError) as exc:
        raise _plan_storage_failed() from exc


@router.get("/latest", response_model=TradingPlan)
def get_latest_plan(container: ApiContainer = Depends(get_container)) -> TradingPlan:
    try:
        with connection_scope(container.settings) as connection:
            plan = TradingPlanRepository(connection).latest()
    except (sqlite3.Error, ValidationError) as exc:
        raise _plan_storage_failed() from exc

    if plan is None:
        raise _plan_not_found()
    return plan


@router.get("/{plan_id}", response_model=TradingPlan)
def get_plan(
    plan_id: str,
    container: ApiContainer = Depends(get_container),
) -> TradingPlan:
    try:
        with connection_scope(container.settings) as connection:
            plan = TradingPlanRepository(connection).get(plan_id)
    except (sqlite3.Error, ValidationError) as exc:
        raise _plan_storage_failed() from exc

    if plan is None:
        raise _plan_not_found()
    return plan
