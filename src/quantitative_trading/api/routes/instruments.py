from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.api.instrument_directory import (
    instrument_directory_service,
    instrument_directory_trade_date,
)
from quantitative_trading.api.routes.datasource import fetch_eastmoney_watchlist
from quantitative_trading.datasource.miaoxiang import RemoteWatchlistResult
from quantitative_trading.instrument.directory import (
    InstrumentDirectoryUnavailableError,
    directory_summary_warnings,
)
from quantitative_trading.instrument.models import InstrumentPreview
from quantitative_trading.instrument.service import InstrumentCandidateService


router = APIRouter(
    prefix="/instruments",
    tags=["instruments"],
    dependencies=[Depends(require_token)],
)


@router.get("/eastmoney-candidates", response_model=InstrumentPreview)
def eastmoney_candidates(
    container: ApiContainer = Depends(get_container),
) -> InstrumentPreview:
    with connection_scope(container.settings) as connection:
        remote = fetch_eastmoney_watchlist(connection, container)
        directory_warnings: list[str] = []
        directory_available = True
        try:
            snapshot = instrument_directory_service(connection, container).ensure_current(
                instrument_directory_trade_date(container)
            )
            directory_warnings.extend(directory_summary_warnings(snapshot.warnings))
        except InstrumentDirectoryUnavailableError:
            directory_available = False
            directory_warnings.append(
                "instrument directory unavailable; unverified candidates are disabled"
            )
        if directory_warnings:
            remote = RemoteWatchlistResult(
                items=remote.items,
                warnings=[*remote.warnings, *directory_warnings],
            )
        return InstrumentCandidateService(connection).preview_eastmoney(
            remote,
            directory_available=directory_available,
        )


@router.get("/search", response_model=InstrumentPreview)
def search_instruments(
    q: Annotated[str, Query(min_length=1, max_length=40)],
    container: ApiContainer = Depends(get_container),
) -> InstrumentPreview:
    normalized = q.strip()
    if not normalized:
        raise ApiError(
            status_code=422,
            code="validation_error",
            message="instrument search query must not be blank",
        )
    with connection_scope(container.settings) as connection:
        try:
            snapshot = instrument_directory_service(connection, container).ensure_current(
                instrument_directory_trade_date(container)
            )
        except InstrumentDirectoryUnavailableError as exc:
            raise ApiError(
                status_code=503,
                code="instrument_directory_unavailable",
                message="instrument directory is unavailable",
            ) from exc
        return InstrumentCandidateService(connection).search(
            normalized,
            warnings=directory_summary_warnings(snapshot.warnings),
        )
