from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError

from quantitative_trading.account.models import AccountSnapshot
from quantitative_trading.account.repository import AccountSnapshotRepository
from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.runtime.account_snapshot_job import (
    AccountSnapshotStorageError,
    CreatedSnapshot,
    UnsupportedMarketProviderError,
    create_and_save_account_snapshot,
    market_provider_from_settings,
)


router = APIRouter(
    prefix="/account",
    tags=["account"],
    dependencies=[Depends(require_token)],
)


class CreatedSnapshotResponse(BaseModel):
    snapshot_id: int
    snapshot: AccountSnapshot


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


def _unsupported_market_provider(exc: UnsupportedMarketProviderError) -> ApiError:
    return ApiError(
        status_code=422,
        code="validation_error",
        message="unsupported market provider",
        details={"market_provider": exc.provider},
    )


def _created_snapshot_response(created: CreatedSnapshot) -> CreatedSnapshotResponse:
    return CreatedSnapshotResponse(
        snapshot_id=created.snapshot_id,
        snapshot=created.snapshot,
    )


def _create_snapshot(container: ApiContainer) -> CreatedSnapshotResponse:
    try:
        return _created_snapshot_response(
            create_and_save_account_snapshot(
                container.settings,
                market_provider_factory=market_provider_from_settings,
            )
        )
    except UnsupportedMarketProviderError as exc:
        raise _unsupported_market_provider(exc) from exc
    except (AccountSnapshotStorageError, sqlite3.Error, ValidationError) as exc:
        raise _snapshot_storage_failed() from exc


def _latest_snapshot(container: ApiContainer) -> AccountSnapshot | None:
    try:
        with connection_scope(container.settings) as connection:
            return AccountSnapshotRepository(connection).latest()
    except (sqlite3.Error, ValidationError) as exc:
        raise _snapshot_storage_failed() from exc


@router.get("/snapshot", response_model=AccountSnapshot | CreatedSnapshotResponse)
def get_account_snapshot(
    fresh: Annotated[bool, Query()] = False,
    container: ApiContainer = Depends(get_container),
) -> AccountSnapshot | CreatedSnapshotResponse:
    if fresh:
        return _create_snapshot(container)

    snapshot = _latest_snapshot(container)
    if snapshot is None:
        raise _snapshot_not_found()
    return snapshot


@router.post("/snapshots", response_model=CreatedSnapshotResponse, status_code=201)
def create_account_snapshot(
    container: ApiContainer = Depends(get_container),
) -> CreatedSnapshotResponse:
    return _create_snapshot(container)


@router.get("/snapshots/latest", response_model=AccountSnapshot)
def get_latest_account_snapshot(
    container: ApiContainer = Depends(get_container),
) -> AccountSnapshot:
    snapshot = _latest_snapshot(container)
    if snapshot is None:
        raise _snapshot_not_found()
    return snapshot
