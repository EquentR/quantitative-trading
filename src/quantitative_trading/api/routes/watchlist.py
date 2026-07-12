from __future__ import annotations

import csv
from io import StringIO
from typing import Annotated

from fastapi import APIRouter, Depends, File, Path as ApiPath, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.api.uploads import closed_temporary_upload
from quantitative_trading.watchlist.models import (
    WatchPinnedInput,
    WatchPinnedItem,
    WatchPinnedSource,
)
from quantitative_trading.watchlist.repository import (
    WATCH_PINNED_CSV_COLUMNS,
    WatchPinnedRepository,
)
from quantitative_trading.watchlist.service import WatchPinnedService


router = APIRouter(
    prefix="/watchlist",
    tags=["watchlist"],
    dependencies=[Depends(require_token)],
)

WatchPinnedSymbolPath = Annotated[str, ApiPath(pattern=r"^\d{6}$")]


class ImportWatchPinnedRequest(BaseModel):
    items: list[WatchPinnedInput]


class UpdateWatchPinnedRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str | None = Field(default=None, pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    rank: int = Field(ge=1)
    plan_enabled: bool = False
    note: str = ""


def _validation_error(message: str, *, details: dict[str, object] | None = None) -> ApiError:
    return ApiError(
        status_code=422,
        code="validation_error",
        message=message,
        details=details,
    )


def _watch_input_from_update(
    symbol: str,
    payload: UpdateWatchPinnedRequest,
) -> WatchPinnedInput:
    if payload.symbol is not None and symbol != payload.symbol:
        raise _validation_error(
            "path symbol must match body symbol",
            details={"path_symbol": symbol, "body_symbol": payload.symbol},
        )

    data = payload.model_dump()
    data["symbol"] = symbol
    try:
        return WatchPinnedInput.model_validate(data)
    except ValidationError as exc:
        raise _validation_error(
            "request validation failed",
            details={"errors": exc.errors(include_context=False)},
        ) from exc


@router.get("/pinned", response_model=list[WatchPinnedItem])
def list_pinned(container: ApiContainer = Depends(get_container)) -> list[WatchPinnedItem]:
    with connection_scope(container.settings) as connection:
        service = WatchPinnedService(WatchPinnedRepository(connection))
        return service.list_pinned()


@router.post("/pinned", response_model=WatchPinnedItem, status_code=201)
def create_pinned(
    payload: WatchPinnedInput,
    container: ApiContainer = Depends(get_container),
) -> WatchPinnedItem:
    with connection_scope(container.settings) as connection:
        service = WatchPinnedService(WatchPinnedRepository(connection))
        return service.upsert_pinned(payload, source=WatchPinnedSource.MANUAL)


@router.post("/pinned/import", response_model=list[WatchPinnedItem])
def import_pinned(
    payload: ImportWatchPinnedRequest,
    container: ApiContainer = Depends(get_container),
) -> list[WatchPinnedItem]:
    try:
        with connection_scope(container.settings) as connection:
            service = WatchPinnedService(WatchPinnedRepository(connection))
            return service.replace_pinned(payload.items, source=WatchPinnedSource.MANUAL)
    except ValueError as exc:
        raise _validation_error(
            "request validation failed",
            details={"reason": str(exc)},
        ) from exc


@router.post("/pinned/import-csv", response_model=list[WatchPinnedItem])
async def import_pinned_csv(
    file: UploadFile = File(...),
    container: ApiContainer = Depends(get_container),
) -> list[WatchPinnedItem]:
    content = await file.read()

    try:
        with closed_temporary_upload(content, suffix=".csv") as path:
            with connection_scope(container.settings) as connection:
                service = WatchPinnedService(WatchPinnedRepository(connection))
                return service.import_csv(
                    path,
                    source=WatchPinnedSource.MANUAL,
                )
    except ValueError as exc:
        raise _validation_error(
            "request validation failed",
            details={"reason": str(exc)},
        ) from exc


@router.get("/pinned/export-csv")
def export_pinned_csv(container: ApiContainer = Depends(get_container)) -> Response:
    with connection_scope(container.settings) as connection:
        service = WatchPinnedService(WatchPinnedRepository(connection))
        items = service.list_pinned()

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=WATCH_PINNED_CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                "symbol": item.symbol,
                "name": item.name,
                "rank": item.rank,
                "plan_enabled": str(item.plan_enabled).lower(),
                "note": item.note,
            }
        )
    return Response(content=output.getvalue(), media_type="text/csv")


@router.post("/pinned/sync", response_model=list[WatchPinnedItem])
def sync_pinned(
    payload: ImportWatchPinnedRequest,
    container: ApiContainer = Depends(get_container),
) -> list[WatchPinnedItem]:
    try:
        with connection_scope(container.settings) as connection:
            service = WatchPinnedService(WatchPinnedRepository(connection))
            return service.merge_synced_pinned(payload.items)
    except ValueError as exc:
        raise _validation_error(
            "request validation failed",
            details={"reason": str(exc)},
        ) from exc


@router.put("/pinned/{symbol}", response_model=WatchPinnedItem)
def update_pinned(
    symbol: WatchPinnedSymbolPath,
    payload: UpdateWatchPinnedRequest,
    container: ApiContainer = Depends(get_container),
) -> WatchPinnedItem:
    item = _watch_input_from_update(symbol, payload)
    with connection_scope(container.settings) as connection:
        service = WatchPinnedService(WatchPinnedRepository(connection))
        return service.upsert_pinned(item, source=WatchPinnedSource.MANUAL)


@router.delete("/pinned/{symbol}", status_code=204)
def delete_pinned(
    symbol: WatchPinnedSymbolPath,
    container: ApiContainer = Depends(get_container),
) -> Response:
    with connection_scope(container.settings) as connection:
        service = WatchPinnedService(WatchPinnedRepository(connection))
        # 删除观察池记录只影响本地候选池，不代表真实交易或券商操作。
        service.remove_pinned(symbol)
    return Response(status_code=204)
