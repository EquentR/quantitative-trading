from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from quantitative_trading.account.models import AccountSnapshot
from quantitative_trading.account.repository import AccountSnapshotRepository
from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError


router = APIRouter(
    prefix="/account",
    tags=["account"],
    dependencies=[Depends(require_token)],
)


def _snapshot_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="snapshot_not_found",
        message="account snapshot not found",
    )


def _snapshot_storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="account snapshot storage failed",
    )


def _latest_snapshot(container: ApiContainer) -> AccountSnapshot | None:
    try:
        with connection_scope(container.settings) as connection:
            return AccountSnapshotRepository(connection).latest()
    except (sqlite3.Error, ValidationError) as exc:
        raise _snapshot_storage_failed() from exc


@router.get("/snapshot", response_model=AccountSnapshot)
def get_account_snapshot(
    fresh: Annotated[bool, Query()] = False,
    container: ApiContainer = Depends(get_container),
) -> AccountSnapshot:
    if fresh:
        raise ApiError(
            status_code=410,
            code="account_fresh_snapshot_retired",
            message="fresh account snapshot query is retired; use the unified intraday workflow",
            details={"replacement": "/api/v1/service/workflows/intraday/run"},
        )

    snapshot = _latest_snapshot(container)
    if snapshot is None:
        raise _snapshot_not_found()
    return snapshot


@router.post("/snapshots", status_code=410)
def create_account_snapshot(
    container: ApiContainer = Depends(get_container),
) -> None:
    del container
    raise ApiError(
        status_code=410,
        code="account_snapshot_create_retired",
        message="account snapshot creation is retired; use the unified intraday workflow",
        details={"replacement": "/api/v1/service/workflows/intraday/run"},
    )


@router.get("/snapshots/latest", response_model=AccountSnapshot)
def get_latest_account_snapshot(
    container: ApiContainer = Depends(get_container),
) -> AccountSnapshot:
    snapshot = _latest_snapshot(container)
    if snapshot is None:
        raise _snapshot_not_found()
    return snapshot
