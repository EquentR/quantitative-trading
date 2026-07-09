from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
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
from quantitative_trading.planning.workflow import CreatedTradingPlan, generate_trading_plan


router = APIRouter(
    prefix="/plans",
    tags=["plans"],
    dependencies=[Depends(require_token)],
)


class GeneratePlanRequest(BaseModel):
    trading_day: date | None = None


class CreatedPlanResponse(BaseModel):
    plan_id: str
    plan: TradingPlan


def _current_time() -> datetime:
    return datetime.now(UTC)


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


def _created_plan_response(created: CreatedTradingPlan) -> CreatedPlanResponse:
    return CreatedPlanResponse(plan_id=created.plan_id, plan=created.plan)


def _default_trading_day(container: ApiContainer, now: datetime) -> date:
    return now.astimezone(ZoneInfo(container.settings.timezone)).date()


@router.post("", response_model=CreatedPlanResponse, status_code=201)
def create_plan(
    request: GeneratePlanRequest | None = None,
    container: ApiContainer = Depends(get_container),
) -> CreatedPlanResponse:
    now = _current_time()
    trading_day = (
        request.trading_day
        if request is not None and request.trading_day is not None
        else _default_trading_day(container, now)
    )
    try:
        with connection_scope(container.settings) as connection:
            return _created_plan_response(
                generate_trading_plan(
                    connection,
                    trading_day=trading_day,
                    now=now,
                    timezone=container.settings.timezone,
                )
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
