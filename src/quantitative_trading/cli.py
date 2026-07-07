from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from quantitative_trading.account.service import AccountService
from quantitative_trading.cash.repository import CashAccountNotInitializedError, CashAccountRepository
from quantitative_trading.cash.service import CashService, CashTransferError, ReadOnlyCashService
from quantitative_trading.config import Settings, load_settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import (
    DuplicatePositionError,
    MissingPositionError,
    PositionRepository,
)
from quantitative_trading.ledger.service import LedgerService, ReadOnlyLedgerService
from quantitative_trading.market.providers import (
    AkShareMarketProvider,
    DisabledMarketProvider,
    MarketDataProvider,
)
from quantitative_trading.runtime.service_runner import DebugServiceRunner
from quantitative_trading.storage.sqlite import connect, migrate


app = typer.Typer()
ledger_app = typer.Typer()
service_app = typer.Typer()
cash_app = typer.Typer()
account_app = typer.Typer()

app.add_typer(ledger_app, name="ledger")
app.add_typer(service_app, name="service")
app.add_typer(cash_app, name="cash")
app.add_typer(account_app, name="account")


def _services() -> tuple[
    Any,
    LedgerService,
    ReadOnlyLedgerService,
    CashService,
    ReadOnlyCashService,
]:
    settings = load_settings()
    connection_cm = connect(settings)
    entered = False
    try:
        connection = connection_cm.__enter__()
        entered = True
        migrate(connection)
        repository = PositionRepository(connection)
        cash_repository = CashAccountRepository(connection)
        return (
            connection_cm,
            LedgerService(repository),
            ReadOnlyLedgerService(repository),
            CashService(cash_repository),
            ReadOnlyCashService(cash_repository),
        )
    except BaseException:
        if entered:
            connection_cm.__exit__(*sys.exc_info())
        raise


@contextmanager
def _connect_read_only(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def _read_only_services() -> tuple[Any | None, ReadOnlyLedgerService | None]:
    settings = load_settings()
    if not settings.database_path.exists():
        return None, None

    connection_cm = _connect_read_only(settings.database_path)
    entered = False
    try:
        connection = connection_cm.__enter__()
        entered = True
        repository = PositionRepository(connection)
        return connection_cm, ReadOnlyLedgerService(repository)
    except BaseException:
        if entered:
            connection_cm.__exit__(*sys.exc_info())
        raise


@contextmanager
def _service_scope() -> Iterator[
    tuple[LedgerService, ReadOnlyLedgerService, CashService, ReadOnlyCashService]
]:
    connection_cm, ledger_service, ledger_read_only, cash_service, cash_read_only = _services()
    try:
        yield ledger_service, ledger_read_only, cash_service, cash_read_only
    finally:
        connection_cm.__exit__(*sys.exc_info())


@contextmanager
def _read_only_service_scope() -> Iterator[ReadOnlyLedgerService | None]:
    connection_cm, read_only = _read_only_services()
    try:
        yield read_only
    finally:
        if connection_cm is not None:
            connection_cm.__exit__(*sys.exc_info())


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


def _format_money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _cash_cli_error(exc: Exception) -> typer.BadParameter:
    return typer.BadParameter(str(exc))


def _market_provider(settings: Settings) -> MarketDataProvider:
    if not settings.enable_market_fetch:
        return DisabledMarketProvider()

    provider_name = settings.market_provider.strip().lower()
    if provider_name == "akshare":
        return AkShareMarketProvider()

    raise typer.BadParameter(f"unsupported market provider: {settings.market_provider}")


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
    with _service_scope() as (service, _, _, _):
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
    with _service_scope() as (service, _, _, _):
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


@ledger_app.command("remove")
def remove_position(symbol: Annotated[str, typer.Argument()]) -> None:
    with _service_scope() as (service, _, _, _):
        try:
            service.remove_position(symbol)
            typer.echo(f"已删除持仓 {symbol}")
        except MissingPositionError as exc:
            raise typer.BadParameter(str(exc)) from exc


@ledger_app.command("list")
def list_positions() -> None:
    with _service_scope() as (_, read_only, _, _):
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


@ledger_app.command("import")
def import_positions(path: Annotated[Path, typer.Argument()]) -> None:
    with _service_scope() as (service, _, _, _):
        try:
            positions = service.import_csv(path)
        except (OSError, ValueError) as exc:
            # Rich 错误面板会截断很长的临时路径，先输出文件名便于用户和测试定位。
            raise typer.BadParameter(f"导入持仓失败 {path.name}: {exc}") from exc
        typer.echo(f"已导入 {len(positions)} 条持仓")


@ledger_app.command("export")
def export_positions() -> None:
    with _service_scope() as (_, read_only, _, _):
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


@cash_app.command("init")
def init_cash(
    cash: Annotated[float, typer.Option("--cash")],
    note: Annotated[str, typer.Option("--note")] = "initial principal",
) -> None:
    with _service_scope() as (_, _, cash_service, _):
        try:
            account = cash_service.initialize(cash, note=note)
        except CashTransferError as exc:
            raise _cash_cli_error(exc) from exc
        typer.echo(
            "cash account initialized "
            f"cash_balance={account.cash_balance:.2f} "
            f"net_principal={account.net_principal:.2f}"
        )


@cash_app.command("show")
def show_cash(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    with _service_scope() as (_, _, _, cash_read_only):
        account = cash_read_only.get_account()
        if account is None:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "status": "cash_not_initialized",
                            "warning": "cash account not initialized",
                        }
                    )
                )
                return
            typer.echo("cash account not initialized")
            return
        if json_output:
            typer.echo(account.model_dump_json())
            return
        typer.echo(
            f"cash_balance={account.cash_balance:.2f} "
            f"total_transfer_in={account.total_transfer_in:.2f} "
            f"total_transfer_out={account.total_transfer_out:.2f} "
            f"net_principal={account.net_principal:.2f} "
            f"updated_at={account.updated_at.isoformat()}"
        )


@cash_app.command("transfer-in")
def transfer_in(
    amount: Annotated[float, typer.Option("--amount")],
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    with _service_scope() as (_, _, cash_service, _):
        try:
            account = cash_service.transfer_in(amount, note=note)
        except (CashAccountNotInitializedError, CashTransferError) as exc:
            raise _cash_cli_error(exc) from exc
        typer.echo(f"transfer_in={amount:.2f} cash_balance={account.cash_balance:.2f}")


@cash_app.command("transfer-out")
def transfer_out(
    amount: Annotated[float, typer.Option("--amount")],
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    with _service_scope() as (_, _, cash_service, _):
        try:
            account = cash_service.transfer_out(amount, note=note)
        except (CashAccountNotInitializedError, CashTransferError) as exc:
            raise _cash_cli_error(exc) from exc
        typer.echo(f"transfer_out={amount:.2f} cash_balance={account.cash_balance:.2f}")


@cash_app.command("adjust")
def adjust_cash(
    cash: Annotated[float, typer.Option("--cash")],
    note: Annotated[str, typer.Option("--note")],
) -> None:
    with _service_scope() as (_, _, cash_service, _):
        try:
            account = cash_service.adjust_cash(cash, note=note)
        except (CashAccountNotInitializedError, CashTransferError) as exc:
            raise _cash_cli_error(exc) from exc
        typer.echo(
            f"cash_adjustment cash_balance={account.cash_balance:.2f} "
            f"net_principal={account.net_principal:.2f}"
        )


@cash_app.command("transactions")
def list_cash_transactions(
    limit: Annotated[int, typer.Option("--limit", min=1)] = 20,
) -> None:
    with _service_scope() as (_, _, _, cash_read_only):
        transactions = cash_read_only.list_transactions(limit=limit)
        if not transactions:
            typer.echo("no cash transactions")
            return
        for transaction in transactions:
            typer.echo(
                f"{transaction.type.value} "
                f"amount={transaction.amount:.2f} "
                f"cash_before={transaction.cash_before:.2f} "
                f"cash_after={transaction.cash_after:.2f} "
                f"occurred_at={transaction.occurred_at.isoformat()} "
                f"note={transaction.note}"
            )


@account_app.command("snapshot")
def account_snapshot(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    settings = load_settings()
    with _service_scope() as (_, ledger_read_only, _, cash_read_only):
        account_service = AccountService(
            ledger=ledger_read_only,
            cash=cash_read_only,
            market=_market_provider(settings),
        )
        snapshot = account_service.create_snapshot()
        if json_output:
            typer.echo(snapshot.model_dump_json())
            return
        typer.echo(
            f"status={snapshot.status.value} "
            f"cash_balance={_format_money(snapshot.cash_balance)} "
            f"total_assets={_format_money(snapshot.total_assets)}"
        )
        for warning in snapshot.warnings:
            typer.echo(f"warning={warning}")


@service_app.command("check")
def check_service() -> None:
    with _read_only_service_scope() as read_only:
        positions = [] if read_only is None else read_only.list_positions()
        typer.echo("服务检查通过")
        typer.echo(f"当前持仓数量: {len(positions)}")


@service_app.command("run", help="Run the debug foreground account snapshot service.")
def run_service(once: Annotated[bool, typer.Option("--once")] = False) -> None:
    settings = load_settings()

    def snapshot_factory():
        with _service_scope() as (_, ledger_read_only, _, cash_read_only):
            account_service = AccountService(
                ledger=ledger_read_only,
                cash=cash_read_only,
                market=_market_provider(settings),
            )
            return account_service.create_snapshot()

    runner = DebugServiceRunner(snapshot_factory=snapshot_factory, log_dir=settings.log_dir)
    snapshot = runner.run_once(reason="startup")
    typer.echo(f"debug service started status={snapshot.status.value}")
    if once:
        return
    typer.echo(
        "debug service polling "
        f"interval={settings.intraday_interval_seconds}s "
        f"timezone={settings.timezone}"
    )
    runner.start(
        interval_seconds=settings.intraday_interval_seconds,
        timezone=settings.timezone,
    )
