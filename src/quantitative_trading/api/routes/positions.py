from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.ledger.models import Position, PositionInput
from quantitative_trading.ledger.repository import (
    DuplicatePositionError,
    MissingPositionError,
    PositionRepository,
)
from quantitative_trading.ledger.service import LedgerService


router = APIRouter(
    prefix="/positions",
    tags=["positions"],
    dependencies=[Depends(require_token)],
)

CSV_COLUMNS = [
    "symbol",
    "name",
    "quantity",
    "available_quantity",
    "cost_price",
    "opened_at",
    "note",
]


class ImportPositionsRequest(BaseModel):
    positions: list[PositionInput]


class UpdatePositionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str | None = Field(default=None, pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    available_quantity: int = Field(ge=0)
    cost_price: float = Field(gt=0)
    opened_at: date
    note: str = ""


def _position_not_found(symbol: str) -> ApiError:
    return ApiError(
        status_code=404,
        code="position_not_found",
        message="position not found",
        details={"symbol": symbol},
    )


def _validation_error(message: str, *, details: dict[str, object] | None = None) -> ApiError:
    return ApiError(
        status_code=422,
        code="validation_error",
        message=message,
        details=details,
    )


def _position_input_from_update(symbol: str, payload: UpdatePositionRequest) -> PositionInput:
    if payload.symbol is not None and symbol != payload.symbol:
        raise _validation_error(
            "path symbol must match body symbol",
            details={"path_symbol": symbol, "body_symbol": payload.symbol},
        )

    data = payload.model_dump()
    data["symbol"] = symbol
    try:
        return PositionInput.model_validate(data)
    except ValidationError as exc:
        raise _validation_error(
            "request validation failed",
            details={"errors": exc.errors(include_context=False)},
        ) from exc


@router.get("", response_model=list[Position])
def list_positions(container: ApiContainer = Depends(get_container)) -> list[Position]:
    with connection_scope(container.settings) as connection:
        service = LedgerService(PositionRepository(connection))
        return service.list_positions()


@router.post("", response_model=Position, status_code=201)
def create_position(
    payload: PositionInput,
    container: ApiContainer = Depends(get_container),
) -> Position:
    try:
        with connection_scope(container.settings) as connection:
            service = LedgerService(PositionRepository(connection))
            return service.add_position(payload)
    except DuplicatePositionError as exc:
        raise ApiError(
            status_code=409,
            code="position_conflict",
            message="position already exists",
            details={"symbol": payload.symbol},
        ) from exc


@router.post("/import", response_model=list[Position])
def import_positions(
    payload: ImportPositionsRequest,
    container: ApiContainer = Depends(get_container),
) -> list[Position]:
    try:
        with connection_scope(container.settings) as connection:
            service = LedgerService(PositionRepository(connection))
            return service.replace_positions(payload.positions)
    except ValueError as exc:
        raise _validation_error(
            "request validation failed",
            details={"reason": str(exc)},
        ) from exc


@router.post("/import-csv", response_model=list[Position])
async def import_positions_csv(
    file: UploadFile = File(...),
    container: ApiContainer = Depends(get_container),
) -> list[Position]:
    content = await file.read()

    try:
        with NamedTemporaryFile(delete=True, suffix=".csv") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            with connection_scope(container.settings) as connection:
                service = LedgerService(PositionRepository(connection))
                return service.import_csv(Path(temp_file.name))
    except ValueError as exc:
        raise _validation_error(
            "request validation failed",
            details={"reason": str(exc)},
        ) from exc


@router.get("/export-csv")
def export_positions_csv(container: ApiContainer = Depends(get_container)) -> Response:
    with connection_scope(container.settings) as connection:
        service = LedgerService(PositionRepository(connection))
        positions = service.list_positions()

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for position in positions:
        writer.writerow(
            {
                "symbol": position.symbol,
                "name": position.name,
                "quantity": position.quantity,
                "available_quantity": position.available_quantity,
                "cost_price": f"{position.cost_price:g}",
                "opened_at": position.opened_at.isoformat(),
                "note": position.note,
            }
        )
    return Response(content=output.getvalue(), media_type="text/csv")


@router.get("/{symbol}", response_model=Position)
def get_position(
    symbol: str,
    container: ApiContainer = Depends(get_container),
) -> Position:
    with connection_scope(container.settings) as connection:
        service = LedgerService(PositionRepository(connection))
        position = service.get_position(symbol)
    if position is None:
        raise _position_not_found(symbol)
    return position


@router.put("/{symbol}", response_model=Position)
def update_position(
    symbol: str,
    payload: UpdatePositionRequest,
    container: ApiContainer = Depends(get_container),
) -> Position:
    position = _position_input_from_update(symbol, payload)
    try:
        with connection_scope(container.settings) as connection:
            service = LedgerService(PositionRepository(connection))
            return service.update_position(position)
    except MissingPositionError as exc:
        raise _position_not_found(symbol) from exc


@router.delete("/{symbol}", status_code=204)
def delete_position(
    symbol: str,
    container: ApiContainer = Depends(get_container),
) -> Response:
    try:
        with connection_scope(container.settings) as connection:
            service = LedgerService(PositionRepository(connection))
            # 删除只移除手动持仓台账记录，不代表真实卖出或任何券商操作。
            service.remove_position(symbol)
    except MissingPositionError as exc:
        raise _position_not_found(symbol) from exc
    return Response(status_code=204)
