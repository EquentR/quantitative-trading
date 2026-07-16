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
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.instrument.repository import InstrumentRepository
from quantitative_trading.universe.models import (
    UniverseMember,
    UniverseSnapshot,
    UniverseSnapshotStatus,
)
from quantitative_trading.universe.repository import UniverseSnapshotRepository
from quantitative_trading.universe.service import build_universe
from quantitative_trading.watchlist.repository import WatchPinnedRepository


router = APIRouter(
    prefix="/universe",
    tags=["universe"],
    dependencies=[Depends(require_token)],
)


class CreatedUniverseSnapshotResponse(BaseModel):
    snapshot_id: int
    snapshot: UniverseSnapshot


def _current_time() -> datetime:
    return datetime.now(UTC)


def _snapshot_not_found() -> ApiError:
    return ApiError(
        status_code=404,
        code="snapshot_not_found",
        message="universe snapshot not found",
    )


def _snapshot_storage_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="universe snapshot storage failed",
    )


def _current_universe(container: ApiContainer, *, created_at: datetime) -> list[UniverseMember]:
    with connection_scope(container.settings) as connection:
        positions = PositionRepository(connection).list()
        watchlist = WatchPinnedRepository(connection).list()
        metadata = {
            item.symbol: item for item in InstrumentRepository(connection).list_active()
        }
    return build_universe(
        positions=positions,
        watchlist=watchlist,
        instrument_metadata=metadata,
        created_at=created_at,
    )


def _build_snapshot(container: ApiContainer, *, created_at: datetime) -> UniverseSnapshot:
    return UniverseSnapshot(
        created_at=created_at,
        status=UniverseSnapshotStatus.OK,
        warnings=[],
        members=_current_universe(container, created_at=created_at),
    )


@router.get("", response_model=list[UniverseMember])
def get_universe(container: ApiContainer = Depends(get_container)) -> list[UniverseMember]:
    return _current_universe(container, created_at=_current_time())


@router.post("/snapshots", response_model=CreatedUniverseSnapshotResponse, status_code=201)
def create_universe_snapshot(
    container: ApiContainer = Depends(get_container),
) -> CreatedUniverseSnapshotResponse:
    snapshot = _build_snapshot(container, created_at=_current_time())
    try:
        with connection_scope(container.settings) as connection:
            snapshot_id = UniverseSnapshotRepository(connection).save(snapshot)
    except (sqlite3.Error, ValidationError) as exc:
        raise _snapshot_storage_failed() from exc

    return CreatedUniverseSnapshotResponse(snapshot_id=snapshot_id, snapshot=snapshot)


@router.get("/snapshots/latest", response_model=UniverseSnapshot)
def get_latest_universe_snapshot(
    container: ApiContainer = Depends(get_container),
) -> UniverseSnapshot:
    try:
        with connection_scope(container.settings) as connection:
            snapshot = UniverseSnapshotRepository(connection).latest()
    except (sqlite3.Error, ValidationError) as exc:
        raise _snapshot_storage_failed() from exc

    if snapshot is None:
        raise _snapshot_not_found()
    return snapshot
