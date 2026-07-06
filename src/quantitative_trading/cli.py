from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from quantitative_trading.config import load_settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import (
    DuplicatePositionError,
    MissingPositionError,
    PositionRepository,
)
from quantitative_trading.ledger.service import LedgerService, ReadOnlyLedgerService
from quantitative_trading.storage.sqlite import connect, migrate


app = typer.Typer()
ledger_app = typer.Typer()
service_app = typer.Typer()

app.add_typer(ledger_app, name="ledger")
app.add_typer(service_app, name="service")


def _services() -> tuple[Any, LedgerService, ReadOnlyLedgerService]:
    settings = load_settings()
    connection_cm = connect(settings)
    entered = False
    try:
        connection = connection_cm.__enter__()
        entered = True
        migrate(connection)
        repository = PositionRepository(connection)
        return connection_cm, LedgerService(repository), ReadOnlyLedgerService(repository)
    except BaseException:
        if entered:
            connection_cm.__exit__(*sys.exc_info())
        raise


def _position_input(
    *,
    symbol: str,
    name: str,
    quantity: int,
    available_quantity: int,
    cost_price: float,
    opened_at: str,
    note: str,
) -> PositionInput:
    try:
        return PositionInput.model_validate(
            {
                "symbol": symbol,
                "name": name,
                "quantity": quantity,
                "available_quantity": available_quantity,
                "cost_price": cost_price,
                "opened_at": opened_at,
                "note": note,
            }
        )
    except ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc


@ledger_app.command("add")
def add_position(
    symbol: Annotated[str, typer.Option("--symbol")],
    name: Annotated[str, typer.Option("--name")],
    quantity: Annotated[int, typer.Option("--quantity")],
    available_quantity: Annotated[int, typer.Option("--available-quantity")],
    cost_price: Annotated[float, typer.Option("--cost-price")],
    opened_at: Annotated[str, typer.Option("--opened-at")],
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    connection_cm, service, _ = _services()
    try:
        position = _position_input(
            symbol=symbol,
            name=name,
            quantity=quantity,
            available_quantity=available_quantity,
            cost_price=cost_price,
            opened_at=opened_at,
            note=note,
        )
        service.add_position(position)
        typer.echo(f"已新增持仓 {position.symbol} {position.name}")
    except DuplicatePositionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("update")
def update_position(
    symbol: Annotated[str, typer.Argument()],
    name: Annotated[str, typer.Option("--name")],
    quantity: Annotated[int, typer.Option("--quantity")],
    available_quantity: Annotated[int, typer.Option("--available-quantity")],
    cost_price: Annotated[float, typer.Option("--cost-price")],
    opened_at: Annotated[str, typer.Option("--opened-at")],
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    connection_cm, service, _ = _services()
    try:
        position = _position_input(
            symbol=symbol,
            name=name,
            quantity=quantity,
            available_quantity=available_quantity,
            cost_price=cost_price,
            opened_at=opened_at,
            note=note,
        )
        service.update_position(position)
        typer.echo(f"已更新持仓 {position.symbol}")
    except MissingPositionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("remove")
def remove_position(symbol: Annotated[str, typer.Argument()]) -> None:
    connection_cm, service, _ = _services()
    try:
        service.remove_position(symbol)
        typer.echo(f"已删除持仓 {symbol}")
    except MissingPositionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("list")
def list_positions() -> None:
    connection_cm, _, read_only = _services()
    try:
        positions = read_only.list_positions()
        if not positions:
            typer.echo("暂无持仓")
            return

        for position in positions:
            typer.echo(
                (
                    f"{position.symbol} {position.name} "
                    f"数量={position.quantity} "
                    f"可用={position.available_quantity} "
                    f"成本={position.cost_price:g} "
                    f"更新={position.updated_at.isoformat()}"
                )
            )
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("import")
def import_positions(path: Annotated[Path, typer.Argument()]) -> None:
    connection_cm, service, _ = _services()
    try:
        positions = service.import_csv(path)
        typer.echo(f"已导入 {len(positions)} 条持仓")
    finally:
        connection_cm.__exit__(None, None, None)


@ledger_app.command("export")
def export_positions() -> None:
    connection_cm, _, read_only = _services()
    try:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(
            [
                "symbol",
                "name",
                "quantity",
                "available_quantity",
                "cost_price",
                "opened_at",
                "note",
            ]
        )
        for position in read_only.list_positions():
            writer.writerow(
                [
                    position.symbol,
                    position.name,
                    position.quantity,
                    position.available_quantity,
                    f"{position.cost_price:g}",
                    position.opened_at.isoformat(),
                    position.note,
                ]
            )
    finally:
        connection_cm.__exit__(None, None, None)


@service_app.command("check")
def check_service() -> None:
    connection_cm, _, read_only = _services()
    try:
        positions = read_only.list_positions()
        typer.echo("服务检查通过")
        typer.echo(f"当前持仓数量: {len(positions)}")
    finally:
        connection_cm.__exit__(None, None, None)
