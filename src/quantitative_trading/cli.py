from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
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
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.planning.workflow import generate_trading_plan
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.recommendation.scanner import scan_latest_plan_recommendations
from quantitative_trading.runtime.service_app import run_api_service
from quantitative_trading.runtime.service_runner import DebugServiceRunner
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.watchlist.models import WatchPinnedInput, WatchPinnedSource
from quantitative_trading.watchlist.repository import (
    WATCH_PINNED_CSV_COLUMNS,
    WatchPinnedRepository,
    parse_watch_pinned_bool,
)
from quantitative_trading.watchlist.service import (
    ReadOnlyWatchPinnedService,
    WatchPinnedService,
)


app = typer.Typer()
ledger_app = typer.Typer()
service_app = typer.Typer()
cash_app = typer.Typer()
account_app = typer.Typer()
watchlist_app = typer.Typer()
plan_app = typer.Typer()
recommendations_app = typer.Typer()

app.add_typer(ledger_app, name="ledger")
app.add_typer(service_app, name="service")
app.add_typer(cash_app, name="cash")
app.add_typer(account_app, name="account")
app.add_typer(watchlist_app, name="watchlist")
app.add_typer(plan_app, name="plan")
app.add_typer(recommendations_app, name="recommendations")


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


@contextmanager
def _watchlist_service_scope() -> Iterator[
    tuple[WatchPinnedService, ReadOnlyWatchPinnedService]
]:
    settings = load_settings()
    connection_cm = connect(settings)
    entered = False
    try:
        connection = connection_cm.__enter__()
        entered = True
        migrate(connection)
        repository = WatchPinnedRepository(connection)
        yield WatchPinnedService(repository), ReadOnlyWatchPinnedService(repository)
    except BaseException:
        if entered:
            connection_cm.__exit__(*sys.exc_info())
        raise
    else:
        connection_cm.__exit__(None, None, None)


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


def _watch_pinned_input(
    *,
    symbol: str,
    name: str,
    rank: int,
    plan_enabled: str,
    note: str,
) -> WatchPinnedInput:
    try:
        return WatchPinnedInput.model_validate(
            {
                "symbol": symbol,
                "name": name,
                "rank": rank,
                "plan_enabled": parse_watch_pinned_bool(plan_enabled),
                "note": note,
            }
        )
    except (ValidationError, ValueError) as exc:
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


@contextmanager
def _database_scope() -> Iterator[tuple[Settings, sqlite3.Connection]]:
    settings = load_settings()
    with connect(settings) as connection:
        migrate(connection)
        yield settings, connection


def _parse_trading_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("date must use YYYY-MM-DD") from exc


def _echo_recommendation_line(recommendation) -> None:
    typer.echo(
        f"{recommendation.recommendation_id} "
        f"{recommendation.symbol} "
        f"{recommendation.action.value} "
        f"confidence={recommendation.confidence} "
        f"data_time={recommendation.data_time.isoformat()}"
    )


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


@watchlist_app.command("add")
def add_watch_pinned(
    symbol: Annotated[str, typer.Option("--symbol")],
    name: Annotated[str, typer.Option("--name")],
    rank: Annotated[int, typer.Option("--rank")],
    plan_enabled: Annotated[str, typer.Option("--plan-enabled")] = "false",
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    with _watchlist_service_scope() as (service, _):
        item = _watch_pinned_input(
            symbol=symbol,
            name=name,
            rank=rank,
            plan_enabled=plan_enabled,
            note=note,
        )
        service.upsert_pinned(item, source=WatchPinnedSource.MANUAL)
        typer.echo(f"已新增观察 {item.symbol} {item.name}")


@watchlist_app.command("update")
def update_watch_pinned(
    symbol: Annotated[str, typer.Argument()],
    name: Annotated[str, typer.Option("--name")],
    rank: Annotated[int, typer.Option("--rank")],
    plan_enabled: Annotated[str, typer.Option("--plan-enabled")] = "false",
    note: Annotated[str, typer.Option("--note")] = "",
) -> None:
    with _watchlist_service_scope() as (service, _):
        item = _watch_pinned_input(
            symbol=symbol,
            name=name,
            rank=rank,
            plan_enabled=plan_enabled,
            note=note,
        )
        service.upsert_pinned(item, source=WatchPinnedSource.MANUAL)
        typer.echo(f"已更新观察 {item.symbol}")


@watchlist_app.command("remove")
def remove_watch_pinned(symbol: Annotated[str, typer.Argument()]) -> None:
    with _watchlist_service_scope() as (service, _):
        service.remove_pinned(symbol)
        typer.echo(f"已删除观察 {symbol}")


@watchlist_app.command("list")
def list_watch_pinned() -> None:
    with _watchlist_service_scope() as (_, read_only):
        items = read_only.list_pinned()
        if not items:
            typer.echo("暂无观察股")
            return

        for item in items:
            typer.echo(
                (
                    f"{item.symbol} {item.name} "
                    f"排序={item.rank} "
                    f"计划={str(item.plan_enabled).lower()} "
                    f"来源={item.source.value} "
                    f"更新={item.updated_at.isoformat()} "
                    f"备注={item.note}"
                )
            )


@watchlist_app.command("import")
def import_watch_pinned(path: Annotated[Path, typer.Argument()]) -> None:
    with _watchlist_service_scope() as (service, _):
        try:
            imported = service.import_csv(path, source=WatchPinnedSource.MANUAL)
        except (OSError, ValueError) as exc:
            raise typer.BadParameter(f"导入观察失败 {path.name}: {exc}") from exc
        typer.echo(f"已导入 {len(imported)} 条观察")


@watchlist_app.command("export")
def export_watch_pinned() -> None:
    with _watchlist_service_scope() as (_, read_only):
        writer = csv.DictWriter(
            sys.stdout,
            fieldnames=WATCH_PINNED_CSV_COLUMNS,
            lineterminator="\n",
        )
        writer.writeheader()
        for item in read_only.list_pinned():
            writer.writerow(
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "rank": item.rank,
                    "plan_enabled": str(item.plan_enabled).lower(),
                    "note": item.note,
                }
            )


@watchlist_app.command("sync")
def sync_watch_pinned() -> None:
    with _watchlist_service_scope() as (_, read_only):
        count = len(read_only.list_pinned())
    typer.echo(f"未配置外部自选置顶同步源，未修改观察池；当前观察股数量: {count}")


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


@plan_app.command("generate")
def generate_plan(date_text: Annotated[str, typer.Option("--date")]) -> None:
    trading_day = _parse_trading_day(date_text)
    with _database_scope() as (settings, connection):
        created = generate_trading_plan(
            connection,
            trading_day=trading_day,
            now=datetime.now(UTC),
            timezone=settings.timezone,
        )
    typer.echo(
        f"plan_id={created.plan_id} "
        f"trading_day={created.plan.trading_day.isoformat()} "
        f"holdings={len(created.plan.holding_symbols)} "
        f"watch={len(created.plan.watch_symbols)}"
    )
    for warning in created.plan.warnings:
        typer.echo(f"warning={warning}")


@plan_app.command("latest")
def latest_plan() -> None:
    with _database_scope() as (_settings, connection):
        plan = TradingPlanRepository(connection).latest()
    if plan is None:
        typer.echo("暂无计划")
        return
    typer.echo(
        f"plan_id={plan.plan_id} "
        f"trading_day={plan.trading_day.isoformat()} "
        f"status={plan.status.value} "
        f"holdings={len(plan.holding_symbols)} "
        f"watch={len(plan.watch_symbols)}"
    )
    for warning in plan.warnings:
        typer.echo(f"warning={warning}")


@recommendations_app.command("scan")
def scan_recommendations() -> None:
    with _database_scope() as (_settings, connection):
        scan = scan_latest_plan_recommendations(connection, now=datetime.now(UTC))
    if scan is None:
        typer.echo("暂无计划，无法生成建议")
        return
    typer.echo(f"generated={len(scan.recommendations)} plan_id={scan.plan.plan_id}")
    for recommendation in scan.recommendations:
        _echo_recommendation_line(recommendation)


@recommendations_app.command("list")
def list_recommendations() -> None:
    with _database_scope() as (_settings, connection):
        recommendations = RecommendationRepository(connection).list()
    if not recommendations:
        typer.echo("暂无建议")
        return
    for recommendation in recommendations:
        _echo_recommendation_line(recommendation)


@recommendations_app.command("show")
def show_recommendation(recommendation_id: Annotated[str, typer.Argument()]) -> None:
    with _database_scope() as (_settings, connection):
        recommendation = RecommendationRepository(connection).get(recommendation_id)
    if recommendation is None:
        raise typer.BadParameter(f"recommendation not found: {recommendation_id}")
    typer.echo(f"recommendation_id={recommendation.recommendation_id}")
    typer.echo(f"symbol={recommendation.symbol}")
    typer.echo(f"name={recommendation.name}")
    typer.echo(f"action={recommendation.action.value}")
    typer.echo(f"confidence={recommendation.confidence}")
    typer.echo(f"data_time={recommendation.data_time.isoformat()}")
    typer.echo(f"valid_until={recommendation.valid_until.isoformat()}")
    typer.echo("reason=" + "; ".join(recommendation.reason))
    invalid_if = recommendation.risk.get("invalid_if", [])
    if isinstance(invalid_if, list):
        typer.echo("invalid_if=" + "; ".join(str(item) for item in invalid_if))


@service_app.command("check")
def check_service() -> None:
    with _read_only_service_scope() as read_only:
        positions = [] if read_only is None else read_only.list_positions()
        typer.echo("服务检查通过")
        typer.echo(f"当前持仓数量: {len(positions)}")


@service_app.command("run", help="Run the unified HTTP API and scheduler service.")
def run_service() -> None:
    settings = load_settings()
    typer.echo(f"api service starting host={settings.api_host} port={settings.api_port}")
    run_api_service(settings)


@service_app.command("debug-run", help="Run the debug foreground account snapshot service.")
def debug_run_service(once: Annotated[bool, typer.Option("--once")] = False) -> None:
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
