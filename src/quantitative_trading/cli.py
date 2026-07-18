from __future__ import annotations

import csv
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

import typer
from pydantic import ValidationError

from quantitative_trading.api.auth import (
    AuthService,
    AuthSetupRequiredError,
    InvalidCredentialsError,
)
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.cash.repository import (
    CashAccountNotInitializedError,
    CashAccountRepository,
)
from quantitative_trading.cash.service import (
    CashService,
    CashTransferError,
    ReadOnlyCashService,
)
from quantitative_trading.config import Settings, load_settings
from quantitative_trading.decision.factory import (
    build_decision_workflow,
    dispatch_workflow_failure_alert,
)
from quantitative_trading.decision.workflow import DecisionWorkflow
from quantitative_trading.email.models import EmailDeliveryStatus
from quantitative_trading.email.outbox import (
    EmailDeliveryNotRetryableError,
    EmailDeliveryRepository,
    EmailDeliveryService,
)
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.email.service import (
    SmtplibEmailSender,
    SmtpSettingsNotConfiguredError,
    SmtpSettingsService,
    sanitized_email_error,
)
from quantitative_trading.datasource.eastmoney import (
    DatasourceNotConfiguredError,
    fetch_eastmoney_watchlist,
)
from quantitative_trading.datasource.miaoxiang import (
    DatasourceError,
    MiaoxiangWatchlistAdapter,
    RemoteWatchlistResult,
)
from quantitative_trading.datasource.status import DatasourceCredentialsRepository
from quantitative_trading.instrument.adapters import AkShareInstrumentDirectoryAdapter
from quantitative_trading.instrument.models import InstrumentType
from quantitative_trading.instrument.directory import (
    InstrumentDirectoryService,
    InstrumentDirectoryUnavailableError,
    directory_summary_warnings,
    latest_completed_directory_trade_date,
)
from quantitative_trading.instrument.repository import (
    InstrumentCatalogStateRepository,
    InstrumentPreviewExpiredError,
    InstrumentPreviewNotFoundError,
    InstrumentRepository,
)
from quantitative_trading.instrument.service import (
    InstrumentCandidateService,
    InstrumentSelectionInvalidError,
)
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import (
    DuplicatePositionError,
    MissingPositionError,
    PositionRepository,
)
from quantitative_trading.ledger.service import LedgerService, ReadOnlyLedgerService
from quantitative_trading.market.providers import (
    AkShareEtfMarketProvider,
    AkShareMarketProvider,
    DisabledMarketProvider,
    MarketDataProvider,
)
from quantitative_trading.market.adapters import (
    AkShareDailyBarProvider,
    AkShareEtfDailyBarProvider,
    AkShareEtfIntradayProvider,
    AkShareIntradayProvider,
    AkShareMoneyFlowProvider,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.cli_service import MarketCliService
from quantitative_trading.market.models import CaptureExecutionMode, CaptureRunStatus
from quantitative_trading.market.repositories import MarketCaptureRunRepository
from quantitative_trading.market.snapshot_service import MarketSnapshotService
from quantitative_trading.notification.models import NotificationStatus
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.notification.service import NotificationService
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.runtime.service_app import run_api_service
from quantitative_trading.sanitization import safe_error_summary
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.storage.api_auth import ApiAuthRepository
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository
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
market_app = typer.Typer()
watchlist_app = typer.Typer()
plan_app = typer.Typer()
recommendations_app = typer.Typer()
notifications_app = typer.Typer()
email_app = typer.Typer()
workflow_app = typer.Typer()

app.add_typer(ledger_app, name="ledger")
app.add_typer(service_app, name="service")
app.add_typer(cash_app, name="cash")
app.add_typer(account_app, name="account")
app.add_typer(market_app, name="market")
app.add_typer(watchlist_app, name="watchlist")
app.add_typer(plan_app, name="plan")
app.add_typer(recommendations_app, name="recommendations")
app.add_typer(notifications_app, name="notifications")
app.add_typer(email_app, name="email")
app.add_typer(workflow_app, name="workflow")


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
    connection = sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=ro", uri=True
    )
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
    connection_cm, ledger_service, ledger_read_only, cash_service, cash_read_only = (
        _services()
    )
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


@contextmanager
def _instrument_cli_scope() -> Iterator[
    tuple[sqlite3.Connection, InstrumentCandidateService, InstrumentDirectoryService]
]:
    settings = load_settings()
    with connect(settings) as connection:
        migrate(connection)
        yield (
            connection,
            InstrumentCandidateService(connection),
            InstrumentDirectoryService(
                InstrumentRepository(connection),
                InstrumentCatalogStateRepository(connection),
                AkShareInstrumentDirectoryAdapter(),
                timezone=ZoneInfo(settings.timezone),
            ),
        )


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


def _etf_market_provider(
    settings: Settings,
    connection: sqlite3.Connection,
) -> MarketDataProvider:
    if not settings.enable_market_fetch:
        return DisabledMarketProvider()

    provider_name = settings.market_provider.strip().lower()
    if provider_name == "akshare":
        return AkShareEtfMarketProvider(
            price_limit_ratios={
                item.symbol: item.price_limit_ratio
                for item in InstrumentRepository(connection).list_active()
                if item.instrument_type is InstrumentType.ETF
                and item.price_limit_ratio is not None
            }
        )

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
    with _instrument_cli_scope() as (connection, _, directory):
        service = WatchPinnedService(WatchPinnedRepository(connection))
        try:
            items = service.parse_csv(path)
        except (OSError, ValueError) as exc:
            raise typer.BadParameter(f"导入观察失败 {path.name}: {exc}") from exc
        directory_warnings: list[str] = []
        try:
            snapshot = directory.ensure_current(
                latest_completed_directory_trade_date(
                    datetime.now(ZoneInfo(load_settings().timezone)),
                    XSHGTradingCalendar(),
                )
            )
            directory_warnings.extend(directory_summary_warnings(snapshot.warnings))
        except InstrumentDirectoryUnavailableError:
            directory_warnings.append(
                "证券目录不可用，未验证导入项已强制关闭计划"
            )
        result = service.replace_pinned_with_warnings(
            items,
            source=WatchPinnedSource.MANUAL,
        )
        typer.echo(f"已导入 {len(result.items)} 条观察")
        for warning in dict.fromkeys([*directory_warnings, *result.warnings]):
            typer.echo(f"warning: {warning}")


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
def sync_watch_pinned(
    symbols: Annotated[str | None, typer.Option("--symbols")] = None,
) -> None:
    with _instrument_cli_scope() as (connection, service, directory):
        try:
            remote = fetch_eastmoney_watchlist(
                DatasourceCredentialsRepository(connection),
                MiaoxiangWatchlistAdapter(),
            )
        except (DatasourceNotConfiguredError, DatasourceError) as exc:
            raise typer.BadParameter(safe_error_summary(exc)) from exc
        directory_warnings: list[str] = []
        directory_available = True
        try:
            snapshot = directory.ensure_current(
                latest_completed_directory_trade_date(
                    datetime.now(ZoneInfo(load_settings().timezone)),
                    XSHGTradingCalendar(),
                )
            )
            directory_warnings.extend(directory_summary_warnings(snapshot.warnings))
        except InstrumentDirectoryUnavailableError:
            directory_available = False
            directory_warnings.append(
                "证券目录不可用，未验证候选不可选择"
            )
        preview = service.preview_eastmoney(
            RemoteWatchlistResult(
                items=remote.items,
                warnings=[*remote.warnings, *directory_warnings],
            ),
            directory_available=directory_available,
        )
        _print_preview_summary(preview)
        if symbols is None:
            return
        selected_symbols = _parse_symbol_csv(symbols)
        try:
            result = service.select(preview.preview_id, selected_symbols)
        except InstrumentSelectionInvalidError as exc:
            raise typer.BadParameter("选择包含不在预览中或不可选择的证券") from exc
        typer.echo(f"已加入监控 {len(result.items)} 条")
        for warning in result.warnings:
            typer.echo(f"warning: {warning}")


@watchlist_app.command("search")
def search_watch_pinned(query: Annotated[str, typer.Argument()]) -> None:
    with _instrument_cli_scope() as (_, service, directory):
        normalized = query.strip()
        if not normalized:
            raise typer.BadParameter("instrument search query must not be blank")
        try:
            snapshot = directory.ensure_current(
                latest_completed_directory_trade_date(
                    datetime.now(ZoneInfo(load_settings().timezone)),
                    XSHGTradingCalendar(),
                )
            )
        except InstrumentDirectoryUnavailableError as exc:
            raise typer.BadParameter("证券目录当前不可用") from exc
        try:
            preview = service.search(
                normalized,
                warnings=directory_summary_warnings(snapshot.warnings),
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        _print_preview_summary(preview)
        for item in preview.items:
            typer.echo(
                f"{item.symbol} {item.name} {item.instrument_type.value} "
                f"{item.exchange.value if item.exchange else '-'} "
                f"{item.settlement_cycle.value}"
            )


@watchlist_app.command("select")
def select_watch_pinned(
    preview_id: Annotated[str, typer.Argument()],
    symbols: Annotated[str, typer.Option("--symbols")],
) -> None:
    try:
        parsed_preview_id = UUID(preview_id)
        selected_symbols = _parse_symbol_csv(symbols)
    except ValueError as exc:
        raise typer.BadParameter("preview id 或证券代码格式无效") from exc
    with _instrument_cli_scope() as (_, service, _directory):
        try:
            result = service.select(parsed_preview_id, selected_symbols)
        except InstrumentPreviewNotFoundError as exc:
            raise typer.BadParameter("候选预览不存在") from exc
        except InstrumentPreviewExpiredError as exc:
            raise typer.BadParameter("候选预览已过期，请重新获取") from exc
        except InstrumentSelectionInvalidError as exc:
            raise typer.BadParameter("选择包含不在预览中或不可选择的证券") from exc
        typer.echo(f"已加入监控 {len(result.items)} 条")
        for warning in result.warnings:
            typer.echo(f"warning: {warning}")


def _parse_symbol_csv(value: str) -> list[str]:
    symbols = [part.strip() for part in value.split(",") if part.strip()]
    if not symbols or any(
        len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit()
        for symbol in symbols
    ):
        raise ValueError("symbols must be comma-separated six-digit codes")
    return symbols


def _print_preview_summary(preview) -> None:  # noqa: ANN001
    selectable = sum(item.selectable for item in preview.items)
    monitored = sum(item.already_monitored for item in preview.items)
    typer.echo(f"preview_id={preview.preview_id}")
    typer.echo(
        f"候选={len(preview.items)} 可选择={selectable} 已监控={monitored}"
    )
    for warning in preview.warnings:
        typer.echo(f"warning: {warning}")


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
def account_snapshot(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    del json_output
    raise typer.BadParameter(
        "qt account snapshot is retired; use qt workflow intraday"
    )


@market_app.command("snapshot")
def market_snapshot() -> None:
    try:
        with _database_scope() as (settings, connection):
            created = MarketSnapshotService(
                connection,
                _market_provider(settings),
                etf_provider=_etf_market_provider(settings, connection),
            ).capture()
    except (sqlite3.Error, ValidationError, ValueError):
        raise typer.BadParameter("market snapshot storage failed") from None
    counts = Counter(quote.status.value for quote in created.quotes.values())
    typer.echo(
        f"market_snapshot_id={created.snapshot_id} "
        f"universe_snapshot_id={created.snapshot.universe_snapshot_id} "
        f"requested={len(created.quotes)} "
        f"ok={counts['ok']} partial={counts['partial']} "
        f"stale={counts['stale']} failed={counts['failed']} "
        f"data_time={created.snapshot.data_time.isoformat() if created.snapshot.data_time else '-'}"
    )
    for warning in created.snapshot.warnings:
        typer.echo(f"warning={warning}")


def _market_cli_service(connection: sqlite3.Connection) -> MarketCliService:
    calendar = XSHGTradingCalendar()
    return MarketCliService(
        connection,
        calendar=calendar,
        daily_provider=AkShareDailyBarProvider(calendar=calendar),
        etf_daily_provider=AkShareEtfDailyBarProvider(calendar=calendar),
        money_flow_provider=AkShareMoneyFlowProvider(calendar=calendar),
    )


def _market_maintenance_workflow(
    connection: sqlite3.Connection,
) -> DecisionWorkflow:
    calendar = XSHGTradingCalendar()
    return DecisionWorkflow(
        connection,
        calendar=calendar,
        quote_provider=DisabledMarketProvider(),
        daily_provider=AkShareDailyBarProvider(calendar=calendar),
        etf_quote_provider=DisabledMarketProvider(),
        etf_daily_provider=AkShareEtfDailyBarProvider(calendar=calendar),
        etf_intraday_provider=AkShareEtfIntradayProvider(calendar=calendar),
        money_flow_provider=AkShareMoneyFlowProvider(calendar=calendar),
        intraday_provider=AkShareIntradayProvider(calendar=calendar),
        now=lambda: datetime.now(UTC),
    )


def _echo_market_backfill(summary) -> None:
    typer.echo(
        f"run_id={summary.run_id} workflow=backfill "
        f"date={summary.trade_date.isoformat()} status={summary.status.value} "
        f"reused={str(summary.reused).lower()} requested={summary.requested_symbols} "
        f"processed={summary.processed_symbols} "
        f"provider_calls={summary.provider_calls} "
        f"provider_duration_ms={summary.provider_duration_ms:.2f} "
        f"rows_received={summary.rows_received} rows_written={summary.rows_written} "
        f"warnings={summary.warning_count} failures={summary.failure_count}"
    )
    for result in summary.results:
        typer.echo(
            f"symbol={result.symbol} dataset={result.dataset.value} "
            f"status={result.status.value} rows={result.actual_rows}/{result.expected_rows}"
        )
    for warning in summary.warnings:
        typer.echo(f"warning={warning}")


@market_app.command("backfill")
def market_backfill(
    date_text: Annotated[str, typer.Option("--date")],
    symbols: Annotated[list[str] | None, typer.Option("--symbol")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    trading_day = _parse_trading_day(date_text)
    try:
        with _database_scope() as (settings, connection):
            if (
                not settings.enable_market_fetch
                or settings.market_provider.strip().lower() != "akshare"
            ):
                raise ValueError("market backfill requires enabled akshare provider")
            summary = _market_maintenance_workflow(connection).run_backfill(
                trading_day,
                symbols=symbols,
            )
            _audit_market_maintenance(
                connection,
                workflow_type="backfill",
                run_id=summary.run_id,
                trade_date=trading_day,
                status=summary.status.value,
                symbols=symbols,
            )
            if summary.status is CaptureRunStatus.FAILED:
                _dispatch_cli_failed_result_alert(
                    settings,
                    connection,
                    workflow_type="backfill",
                    run_id=summary.run_id,
                    warnings=summary.warnings,
                    now=datetime.now(UTC),
                )
    except Exception as exc:
        _audit_cli_failure(
            workflow_type="backfill",
            error=safe_error_summary(exc),
            trade_date=trading_day,
        )
        raise typer.BadParameter("market backfill failed") from None
    if json_output:
        typer.echo(summary.model_dump_json())
    else:
        _echo_market_backfill(summary)
    if summary.status is CaptureRunStatus.FAILED:
        raise typer.Exit(code=1)


@market_app.command("cleanup")
def market_cleanup(
    date_text: Annotated[str, typer.Option("--date")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    trading_day = _parse_trading_day(date_text)
    try:
        with _database_scope() as (_settings, connection):
            result = _market_maintenance_workflow(connection).run_cleanup(trading_day)
            _audit_market_maintenance(
                connection,
                workflow_type="cleanup",
                run_id=result.run_id,
                trade_date=trading_day,
                status=result.status.value,
                symbols=None,
            )
    except Exception as exc:
        _audit_cli_failure(
            workflow_type="cleanup",
            error=safe_error_summary(exc),
            trade_date=trading_day,
        )
        raise typer.BadParameter("market cleanup failed") from None
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "workflow_type": "cleanup",
                    "trade_date": trading_day.isoformat(),
                    "status": result.status.value,
                    "reused": result.reused,
                    "deleted_rows": result.cleaned_rows,
                    "warnings": list(result.warnings),
                },
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(
            f"run_id={result.run_id} workflow=cleanup "
            f"date={trading_day.isoformat()} status={result.status.value} "
            f"reused={str(result.reused).lower()} deleted={result.cleaned_rows}"
        )


@market_app.command("runs")
def market_runs(
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 20,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        with _database_scope() as (_settings, connection):
            summary = _market_cli_service(connection).list_runs(limit=limit)
    except Exception:
        raise typer.BadParameter("market runs failed") from None
    if json_output:
        typer.echo(summary.model_dump_json())
        return
    if not summary.runs:
        typer.echo("no market runs")
        return
    for run in summary.runs:
        dataset_counts = run.get("dataset_counts", {})
        rendered_counts = ";".join(
            f"{dataset}="
            + ",".join(
                f"{status}:{counts.get(status, 0)}"
                for status in ("complete", "degraded", "failed", "stale")
            )
            for dataset, counts in sorted(dataset_counts.items())
        ) or "-"
        typer.echo(
            f"run_id={run['run_id']} workflow={run['workflow_type']} "
            f"date={run['trade_date']} status={run['status']} "
            f"requested={run['requested_symbols']} processed={run['processed_symbols']} "
            f"duration_ms={run['duration_ms'] if run['duration_ms'] is not None else '-'} "
            f"provider_calls={run['provider_calls']} "
            f"provider_duration_ms={float(run['provider_duration_ms']):.2f} "
            f"rows_received={run['rows_received']} rows_written={run['rows_written']} "
            f"cleaned_rows={run['cleaned_rows']} plans={run['plan_count']} "
            f"recommendations={run['recommendation_count']} "
            f"notifications={run['notification_count']} emails={run['email_outbox_count']} "
            f"retries={run['retry_count']} warnings={run['warning_count']} "
            f"failures={run['failure_count']} error={run['error_summary'] or '-'} "
            f"dataset_counts={rendered_counts}"
        )


def _workflow_now() -> datetime:
    return datetime.now(UTC)


def _require_manual_reason(reason: str | None) -> str:
    if reason is None or not reason.strip():
        raise typer.BadParameter(
            "reason is required for forced or calendar-override runs"
        )
    return reason.strip()


def _authenticate_manual_override(
    settings: Settings,
    connection: sqlite3.Connection,
) -> None:
    password = typer.prompt("API password", hide_input=True)
    try:
        AuthService(
            ApiAuthRepository(connection),
            token_ttl_seconds=settings.api_token_ttl_seconds,
            startup_password=settings.api_access_password,
            configured_token_secret=settings.api_token_secret,
        ).login(password)
    except (AuthSetupRequiredError, InvalidCredentialsError) as exc:
        raise typer.BadParameter("manual workflow authentication failed") from exc


def _audit_manual_workflow(
    connection: sqlite3.Connection,
    *,
    workflow_type: str,
    result,
    now: datetime,
    force: bool,
    skip_calendar: bool,
    manual_reason: str | None,
) -> None:
    provenance = {}
    if hasattr(result, "mode"):
        provenance = {
            "mode": result.mode.value,
            "effective_trade_date": result.effective_trade_date.isoformat(),
            "history_cutoff_date": result.history_cutoff_date.isoformat(),
            "requested_symbol_scope": list(result.requested_symbol_scope),
            "lease_expires_at": result.lease_expires_at.isoformat(),
        }
    AuditService(AuditLogRepository(connection)).record_event(
        event_type="workflow.manual_run",
        recommendation_id=None,
        payload={
            "workflow_type": workflow_type,
            "run_id": result.run_id,
            "force": force,
            "skip_calendar": skip_calendar,
            "manual_reason": manual_reason,
            **provenance,
        },
        now=now,
    )


def _audit_market_maintenance(
    connection: sqlite3.Connection,
    *,
    workflow_type: str,
    run_id: str,
    trade_date: date,
    status: str,
    symbols: list[str] | None,
) -> None:
    AuditService(AuditLogRepository(connection)).record_event(
        event_type="workflow.manual_run",
        recommendation_id=None,
        payload={
            "workflow_type": workflow_type,
            "run_id": run_id,
            "trade_date": trade_date.isoformat(),
            "status": status,
            "symbols": symbols,
        },
    )


def _audit_cli_failure(
    *,
    workflow_type: str,
    error: str,
    trade_date: date | None = None,
    force: bool = False,
    skip_calendar: bool = False,
    manual_reason: str | None = None,
    dispatch_alert: bool = True,
    requested_at: datetime | None = None,
    mode: CaptureExecutionMode | None = None,
) -> None:
    try:
        failed_at = datetime.now(UTC)
        with _database_scope() as (settings, connection):
            provenance = (
                {}
                if requested_at is None or mode is None
                else _intraday_failure_provenance(
                    connection,
                    requested_at=requested_at,
                    mode=mode,
                )
            )
            AuditService(AuditLogRepository(connection)).record_event(
                event_type="workflow.manual_run_failed",
                recommendation_id=None,
                payload={
                    "workflow_type": workflow_type,
                    "trade_date": (
                        None if trade_date is None else trade_date.isoformat()
                    ),
                    "force": force,
                    "skip_calendar": skip_calendar,
                    "manual_reason": manual_reason,
                    "error": error,
                    **provenance,
                },
                now=failed_at,
            )
            if dispatch_alert:
                dispatch_workflow_failure_alert(
                    connection,
                    settings,
                    workflow_type=workflow_type,
                    error=error,
                    source="cli",
                    now=failed_at,
                )
    except Exception:
        return


def _intraday_failure_provenance(
    connection: sqlite3.Connection,
    *,
    requested_at: datetime,
    mode: CaptureExecutionMode,
) -> dict[str, object]:
    calendar = XSHGTradingCalendar()
    local_now = requested_at.astimezone(calendar.timezone)
    if mode is CaptureExecutionMode.DISPLAY_ONLY:
        resolution = calendar.resolve_market_dates(
            local_now,
            session_ready=lambda _: True,
        )
        effective_trade_date = resolution.effective_trade_date
        history_cutoff_date = resolution.history_cutoff_date
    else:
        effective_trade_date = local_now.date()
        history_cutoff_date = calendar.previous_trading_day(effective_trade_date)
    period_minute = local_now.minute - local_now.minute % 3
    period_start = local_now.replace(
        minute=period_minute,
        second=0,
        microsecond=0,
    )
    run_id = (
        f"intraday-{effective_trade_date:%Y%m%d}-{period_start:%H%M}"
        if mode is CaptureExecutionMode.DECISION
        else (
            f"intraday-{mode.value}-{effective_trade_date:%Y%m%d}-"
            f"{period_start:%Y%m%d-%H%M}"
        )
    )
    run = MarketCaptureRunRepository(connection).get(run_id)
    return {
        "run_id": run_id,
        "mode": mode.value,
        "effective_trade_date": effective_trade_date.isoformat(),
        "history_cutoff_date": history_cutoff_date.isoformat(),
        "requested_symbol_scope": [] if run is None else run.requested_symbol_scope,
        "lease_expires_at": (
            requested_at + timedelta(minutes=10)
            if run is None or run.lease_expires_at is None
            else run.lease_expires_at
        ).isoformat(),
    }


def _dispatch_cli_failed_result_alert(
    settings: Settings,
    connection: sqlite3.Connection,
    *,
    workflow_type: str,
    run_id: str,
    warnings: list[str] | tuple[str, ...],
    now: datetime,
) -> None:
    try:
        dispatch_workflow_failure_alert(
            connection,
            settings,
            workflow_type=workflow_type,
            error="; ".join(warnings) or "workflow returned failed status",
            source="cli",
            now=now,
            run_id=run_id,
        )
    except Exception:
        return


@workflow_app.command("intraday")
def run_intraday_workflow(
    force: Annotated[bool, typer.Option("--force")] = False,
    display_only: Annotated[bool, typer.Option("--display-only")] = False,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    now = _workflow_now()
    calendar = XSHGTradingCalendar()
    local_now = now.astimezone(calendar.timezone)
    in_session = calendar.is_trading_day(
        local_now.date()
    ) and calendar.is_trading_minute(local_now)
    manual_reason = reason.strip() if reason and reason.strip() else None
    if manual_reason is not None and len(manual_reason) > 500:
        raise typer.BadParameter("reason must be 500 characters or fewer")
    if force and display_only:
        raise typer.BadParameter("--force and --display-only cannot be combined")
    mode = (
        CaptureExecutionMode.DISPLAY_ONLY
        if display_only and not in_session
        else CaptureExecutionMode.DECISION
    )
    requires_auth = force
    if not in_session:
        if not force and not display_only:
            raise typer.BadParameter("intraday workflow is outside an XSHG session")
        if force:
            manual_reason = _require_manual_reason(manual_reason)
    elif force:
        manual_reason = _require_manual_reason(manual_reason)

    try:
        with _database_scope() as (settings, connection):
            if requires_auth:
                _authenticate_manual_override(settings, connection)
            result = build_decision_workflow(
                connection,
                settings,
                now=lambda: now,
            ).run_intraday(
                as_of=now,
                mode=mode,
                manual_reason=manual_reason,
            )
            _audit_manual_workflow(
                connection,
                workflow_type="intraday",
                result=result,
                now=now,
                force=force,
                skip_calendar=False,
                manual_reason=manual_reason,
            )
            if (
                result.status is CaptureRunStatus.FAILED
                and mode is CaptureExecutionMode.DECISION
            ):
                _dispatch_cli_failed_result_alert(
                    settings,
                    connection,
                    workflow_type="intraday",
                    run_id=result.run_id,
                    warnings=result.warnings,
                    now=now,
                )
    except Exception as exc:
        _audit_cli_failure(
            workflow_type="intraday",
            error=safe_error_summary(exc),
            trade_date=local_now.date(),
            force=force,
            manual_reason=manual_reason,
            dispatch_alert=mode is CaptureExecutionMode.DECISION,
            requested_at=now,
            mode=mode,
        )
        raise typer.BadParameter(
            f"intraday workflow failed: {safe_error_summary(exc)}"
        ) from None

    payload = {
        "run_id": result.run_id,
        "status": result.status.value,
        "reused": result.reused,
        "market_input_snapshot_id": result.market_input_snapshot_id,
        "recommendation_ids": list(result.recommendation_ids),
        "warnings": list(result.warnings),
        "mode": result.mode.value,
        "effective_trade_date": result.effective_trade_date.isoformat(),
        "history_cutoff_date": result.history_cutoff_date.isoformat(),
        "requested_symbol_scope": list(result.requested_symbol_scope),
        "lease_expires_at": result.lease_expires_at.isoformat(),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, default=str))
    else:
        typer.echo(
            f"run_id={result.run_id} workflow=intraday "
            f"status={result.status.value} "
            f"reused={str(result.reused).lower()} "
            f"market_input_snapshot_id={result.market_input_snapshot_id} "
            f"recommendations={len(result.recommendation_ids)}"
        )
        for warning in result.warnings:
            typer.echo(f"warning={warning}")
    if result.status is CaptureRunStatus.FAILED:
        raise typer.Exit(code=1)


@workflow_app.command("close")
def run_close_workflow(
    date_text: Annotated[str | None, typer.Option("--date")] = None,
    force: Annotated[bool, typer.Option("--force")] = False,
    skip_calendar: Annotated[bool, typer.Option("--skip-calendar")] = False,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    now = _workflow_now()
    calendar = XSHGTradingCalendar()
    local_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    trade_date = (
        local_now.date() if date_text is None else _parse_trading_day(date_text)
    )
    in_close_window = trade_date == local_now.date() and time(
        15, 15
    ) <= local_now.time().replace(tzinfo=None) <= time(16, 30)
    manual_reason = reason.strip() if reason and reason.strip() else None
    requires_auth = force or skip_calendar or not in_close_window
    if requires_auth:
        manual_reason = _require_manual_reason(manual_reason)
    if (
        trade_date == local_now.date()
        and local_now.time().replace(tzinfo=None) < time(15, 15)
        and not force
    ):
        raise typer.BadParameter("close workflow before 15:15 requires --force")
    if not calendar.is_trading_day(trade_date) and not skip_calendar:
        raise typer.BadParameter("close workflow requires an XSHG trading day")

    try:
        with _database_scope() as (settings, connection):
            if requires_auth:
                _authenticate_manual_override(settings, connection)
            result = build_decision_workflow(
                connection,
                settings,
                now=lambda: now,
            ).run_close(trade_date, skip_calendar=skip_calendar)
            _audit_manual_workflow(
                connection,
                workflow_type="close",
                result=result,
                now=now,
                force=force,
                skip_calendar=skip_calendar,
                manual_reason=manual_reason,
            )
    except Exception as exc:
        _audit_cli_failure(
            workflow_type="close",
            error=safe_error_summary(exc),
            trade_date=trade_date,
            force=force,
            skip_calendar=skip_calendar,
            manual_reason=manual_reason,
        )
        raise typer.BadParameter(
            f"close workflow failed: {safe_error_summary(exc)}"
        ) from None

    payload = {
        "run_id": result.run_id,
        "ready": result.ready,
        "reused": result.reused,
        "market_input_snapshot_id": result.market_input_snapshot_id,
        "plan_id": result.plan_id,
        "warnings": list(result.warnings),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, default=str))
        return
    typer.echo(
        f"run_id={result.run_id} workflow=close "
        f"ready={str(result.ready).lower()} reused={str(result.reused).lower()} "
        f"market_input_snapshot_id={result.market_input_snapshot_id} "
        f"plan_id={result.plan_id or '-'}"
    )
    for warning in result.warnings:
        typer.echo(f"warning={warning}")


@plan_app.command("generate")
def generate_plan(date_text: Annotated[str, typer.Option("--date")]) -> None:
    raise typer.BadParameter("qt plan generate is deprecated; use qt workflow close")


@plan_app.command("latest")
def latest_plan(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _database_scope() as (_settings, connection):
        plan = TradingPlanRepository(connection).latest()
    if plan is None:
        typer.echo("null" if json_output else "暂无计划")
        return
    if json_output:
        typer.echo(plan.model_dump_json())
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
    raise typer.BadParameter(
        "qt recommendations scan is retired; use qt workflow intraday"
    )


@recommendations_app.command("list")
def list_recommendations(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _database_scope() as (_settings, connection):
        recommendations = RecommendationRepository(connection).list()
    if json_output:
        typer.echo(
            json.dumps(
                [item.model_dump(mode="json") for item in recommendations],
                ensure_ascii=False,
                default=str,
            )
        )
        return
    if not recommendations:
        typer.echo("暂无建议")
        return
    for recommendation in recommendations:
        _echo_recommendation_line(recommendation)


@recommendations_app.command("show")
def show_recommendation(
    recommendation_id: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _database_scope() as (_settings, connection):
        recommendation = RecommendationRepository(connection).get(recommendation_id)
    if recommendation is None:
        raise typer.BadParameter(f"recommendation not found: {recommendation_id}")
    if json_output:
        typer.echo(recommendation.model_dump_json())
        return
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


@notifications_app.command("list")
def list_notifications(
    status: Annotated[NotificationStatus | None, typer.Option("--status")] = None,
    symbol: Annotated[str | None, typer.Option("--symbol")] = None,
    action: Annotated[str | None, typer.Option("--action")] = None,
    recommendation_id: Annotated[
        str | None,
        typer.Option("--recommendation-id"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 50,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _database_scope() as (_settings, connection):
        notifications = NotificationService(
            NotificationRepository(connection)
        ).list_notifications(
            status=status,
            symbol=symbol,
            action=action,
            recommendation_id=recommendation_id,
            limit=limit,
            offset=offset,
        )
    if json_output:
        typer.echo(
            json.dumps(
                [item.model_dump(mode="json") for item in notifications],
                ensure_ascii=False,
            )
        )
        return
    if not notifications:
        typer.echo("暂无通知")
        return
    for notification in notifications:
        typer.echo(
            f"{notification.notification_id} "
            f"{notification.symbol} "
            f"{notification.action} "
            f"status={notification.status.value} "
            f"recommendation_id={notification.recommendation_id} "
            f"data_time={notification.data_time.isoformat()}"
        )


@notifications_app.command("unread")
def unread_notifications(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _database_scope() as (_settings, connection):
        count = NotificationService(NotificationRepository(connection)).unread_count()
    typer.echo(json.dumps({"unread": count}) if json_output else f"unread={count}")


@notifications_app.command("read")
def read_notification(
    notification_id: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        with _database_scope() as (_settings, connection):
            service = NotificationService(NotificationRepository(connection))
            updated = service.mark_read(notification_id, commit=False)
            AuditService(AuditLogRepository(connection)).record_event(
                event_type="notification.read",
                recommendation_id=updated.recommendation_id,
                payload={"notification_id": notification_id},
                commit=False,
            )
            connection.commit()
    except KeyError:
        raise typer.BadParameter(f"notification not found: {notification_id}") from None
    if json_output:
        typer.echo(updated.model_dump_json())
    else:
        typer.echo(
            f"notification_id={updated.notification_id} status={updated.status.value}"
        )


@email_app.command("status")
def email_status(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _database_scope() as (_settings, connection):
        settings = SmtpSettingsService(SmtpSettingsRepository(connection)).get_public()
    if json_output:
        typer.echo(settings.model_dump_json())
        return
    typer.echo(
        f"configured={str(settings.configured).lower()} "
        f"enabled={str(settings.enabled).lower()} "
        f"password_configured={str(settings.password_configured).lower()} "
        f"host={settings.host or '-'} "
        f"port={settings.port} "
        f"security={settings.security.value} "
        f"sender={settings.sender or '-'} "
        f"recipient={settings.recipient or '-'}"
    )


@email_app.command("test")
def test_email(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _database_scope() as (_settings, connection):
        repository = SmtpSettingsRepository(connection)
        service = SmtpSettingsService(repository)
        configured = repository.get()
        if configured is None:
            typer.echo(
                json.dumps({"smtp_test": "not_configured"})
                if json_output
                else "smtp_test=not_configured"
            )
            return
        secret_texts = (configured.password,) if configured.password else ()
        audit_service = AuditService(
            AuditLogRepository(connection),
            configured_secret_texts=secret_texts,
        )
        try:
            tested = service.send_test(SmtplibEmailSender())
        except SmtpSettingsNotConfiguredError:
            typer.echo(
                json.dumps({"smtp_test": "not_configured"})
                if json_output
                else "smtp_test=not_configured"
            )
            return
        except Exception as exc:
            error = sanitized_email_error(exc, secret_texts=secret_texts)
            audit_service.record_event(
                event_type="smtp.test.failed",
                recommendation_id=None,
                payload={"error": error},
            )
            raise typer.BadParameter(f"smtp test failed: {error}") from None
        audit_service.record_event(
            event_type="smtp.test.succeeded",
            recommendation_id=None,
            payload={"recipient": tested.recipient},
        )
    typer.echo(
        json.dumps({"smtp_test": "sent", "recipient": tested.recipient})
        if json_output
        else f"smtp_test=sent recipient={tested.recipient}"
    )


def _email_delivery_service(connection: sqlite3.Connection) -> EmailDeliveryService:
    return EmailDeliveryService(
        EmailDeliveryRepository(connection),
        SmtpSettingsRepository(connection),
        SmtplibEmailSender(),
        audit_repository=AuditLogRepository(connection),
    )


@email_app.command("deliveries")
def list_email_deliveries(
    status: Annotated[EmailDeliveryStatus | None, typer.Option("--status")] = None,
    notification_id: Annotated[
        str | None,
        typer.Option("--notification-id"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 50,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    with _database_scope() as (_settings, connection):
        deliveries = _email_delivery_service(connection).list_deliveries(
            status=status,
            notification_id=notification_id,
            limit=limit,
            offset=offset,
        )
    if json_output:
        typer.echo(
            json.dumps(
                [item.model_dump(mode="json") for item in deliveries],
                ensure_ascii=False,
            )
        )
        return
    if not deliveries:
        typer.echo("暂无邮件投递")
        return
    for delivery in deliveries:
        typer.echo(
            f"{delivery.delivery_id} "
            f"status={delivery.status.value} "
            f"attempts={delivery.attempt_count} "
            f"notification_id={delivery.notification_id or '-'} "
            f"next_attempt_at="
            f"{delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else '-'}"
        )


@email_app.command("retry")
def retry_email_delivery(
    delivery_id: Annotated[str, typer.Argument()],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        with _database_scope() as (_settings, connection):
            delivery = _email_delivery_service(connection).manual_retry(delivery_id)
    except KeyError:
        raise typer.BadParameter(f"email delivery not found: {delivery_id}") from None
    except EmailDeliveryNotRetryableError:
        raise typer.BadParameter(
            f"email delivery is not retryable: {delivery_id}"
        ) from None
    if json_output:
        typer.echo(delivery.model_dump_json())
    else:
        typer.echo(
            f"delivery_id={delivery.delivery_id} "
            f"status={delivery.status.value} attempts={delivery.attempt_count}"
        )


@service_app.command("check")
def check_service() -> None:
    with _read_only_service_scope() as read_only:
        positions = [] if read_only is None else read_only.list_positions()
        typer.echo("服务检查通过")
        typer.echo(f"当前持仓数量: {len(positions)}")


@service_app.command("status")
def service_status(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    settings = load_settings()
    if not settings.database_path.exists():
        payload = {"scheduler_state": "missing"}
    else:
        with _connect_read_only(settings.database_path) as connection:
            state = SchedulerStateRepository(connection).get()
        if state is None:
            payload = {"scheduler_state": "missing"}
        else:
            payload = {
                "scheduler_enabled": state.enabled,
                "interval_seconds": state.interval_seconds,
                "run_on_start": state.run_on_start,
                "last_started_at": _isoformat(state.last_started_at),
                "last_finished_at": _isoformat(state.last_finished_at),
                "last_status": state.last_status,
                "last_reason": state.last_reason,
                "last_error": state.last_error,
                "last_snapshot_id": state.last_snapshot_id,
                "last_task_type": state.last_task_type,
                "last_plan_id": state.last_plan_id,
                "last_recommendation_ids": state.last_recommendation_ids,
                "overrun_count": state.overrun_count,
                "skipped_count": state.skipped_count,
                "updated_at": state.updated_at.isoformat(),
            }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    if payload.get("scheduler_state") == "missing":
        typer.echo("scheduler_state=missing")
        return
    for key, value in payload.items():
        if isinstance(value, bool):
            rendered = str(value).lower()
        elif isinstance(value, list):
            rendered = ",".join(value) if value else "-"
        else:
            rendered = "-" if value is None else str(value)
        typer.echo(f"{key}={rendered}")


def _isoformat(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


@service_app.command("run", help="Run the unified HTTP API and scheduler service.")
def run_service() -> None:
    settings = load_settings()
    typer.echo(
        f"api service starting host={settings.api_host} port={settings.api_port}"
    )
    run_api_service(settings)


@service_app.command(
    "debug-run", help="Retired; use workflow intraday or service run."
)
def debug_run_service(once: Annotated[bool, typer.Option("--once")] = False) -> None:
    del once
    raise typer.BadParameter(
        "qt service debug-run is retired; use qt workflow intraday "
        "for one cycle or qt service run for the unified service"
    )
