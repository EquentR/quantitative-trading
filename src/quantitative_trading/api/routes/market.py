from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Path as ApiPath
from pydantic import BaseModel, ValidationError as PydanticValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token as require_auth,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.market.models import MarketInputSnapshot
from quantitative_trading.market.repository import MarketInputSnapshotRepository
from quantitative_trading.market.snapshot_service import (
    CreatedMarketInputSnapshot,
    MarketSnapshotService,
)
from quantitative_trading.runtime.account_snapshot_job import (
    UnsupportedMarketProviderError,
    market_provider_from_settings,
)


router = APIRouter(
    prefix="/market",
    tags=["market"],
    dependencies=[Depends(require_auth)],
)

SQLITE_SIGNED_64_BIT_INTEGER_MAX = 9_223_372_036_854_775_807
MarketSnapshotIdPath = Annotated[
    int,
    ApiPath(gt=0, le=SQLITE_SIGNED_64_BIT_INTEGER_MAX),
]


class CreatedMarketSnapshotResponse(BaseModel):
    snapshot_id: int
    snapshot: MarketInputSnapshot


def _market_snapshot_not_found(snapshot_id: int | None = None) -> ApiError:
    details = {} if snapshot_id is None else {"snapshot_id": snapshot_id}
    return ApiError(
        status_code=404,
        code="market_snapshot_not_found",
        message="market snapshot not found",
        details=details,
    )


def _market_snapshot_storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="market snapshot storage failed",
    )


def _unsupported_market_provider(exc: UnsupportedMarketProviderError) -> ApiError:
    return ApiError(
        status_code=422,
        code="validation_error",
        message="unsupported market provider",
        details={"market_provider": exc.provider},
    )


def _created_snapshot_response(
    created: CreatedMarketInputSnapshot,
) -> CreatedMarketSnapshotResponse:
    return CreatedMarketSnapshotResponse(
        snapshot_id=created.snapshot_id,
        snapshot=created.snapshot,
    )


@router.post("/snapshots", response_model=CreatedMarketSnapshotResponse, status_code=201)
def create_market_snapshot(
    container: ApiContainer = Depends(get_container),
) -> CreatedMarketSnapshotResponse:
    try:
        with connection_scope(container.settings) as connection:
            provider = market_provider_from_settings(container.settings)
            created = MarketSnapshotService(connection, provider).capture()
    except UnsupportedMarketProviderError as exc:
        raise _unsupported_market_provider(exc) from exc
    except (sqlite3.Error, PydanticValidationError, ValueError) as exc:
        raise _market_snapshot_storage_failed() from exc
    return _created_snapshot_response(created)


@router.get("/snapshots/latest", response_model=MarketInputSnapshot)
def get_latest_market_snapshot(
    container: ApiContainer = Depends(get_container),
) -> MarketInputSnapshot:
    try:
        with connection_scope(container.settings) as connection:
            snapshot = MarketInputSnapshotRepository(connection).latest()
    except (sqlite3.Error, PydanticValidationError) as exc:
        raise _market_snapshot_storage_failed() from exc

    if snapshot is None:
        raise _market_snapshot_not_found()
    return snapshot


@router.get("/snapshots/{snapshot_id}", response_model=MarketInputSnapshot)
def get_market_snapshot(
    snapshot_id: MarketSnapshotIdPath,
    container: ApiContainer = Depends(get_container),
) -> MarketInputSnapshot:
    try:
        with connection_scope(container.settings) as connection:
            snapshot = MarketInputSnapshotRepository(connection).get(snapshot_id)
    except (sqlite3.Error, PydanticValidationError) as exc:
        raise _market_snapshot_storage_failed() from exc

    if snapshot is None:
        raise _market_snapshot_not_found(snapshot_id)
    return snapshot
