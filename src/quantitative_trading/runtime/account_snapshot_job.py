from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from quantitative_trading.account.models import AccountSnapshot
from quantitative_trading.account.repository import AccountSnapshotRepository
from quantitative_trading.account.service import AccountService
from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.cash.service import ReadOnlyCashService
from quantitative_trading.config import Settings
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.ledger.service import ReadOnlyLedgerService
from quantitative_trading.market.providers import (
    AkShareMarketProvider,
    DisabledMarketProvider,
    MarketDataProvider,
)
from quantitative_trading.storage.sqlite import connect, migrate


@dataclass(frozen=True)
class CreatedSnapshot:
    snapshot_id: int
    snapshot: AccountSnapshot


MarketProviderFactory = Callable[[Settings], MarketDataProvider]


def market_provider_from_settings(settings: Settings) -> MarketDataProvider:
    if not settings.enable_market_fetch:
        return DisabledMarketProvider()
    if settings.market_provider.strip().lower() == "akshare":
        return AkShareMarketProvider()
    raise ValueError(f"unsupported market provider: {settings.market_provider}")


def create_and_save_account_snapshot(
    settings: Settings,
    *,
    market_provider_factory: MarketProviderFactory = market_provider_from_settings,
) -> CreatedSnapshot:
    with connect(settings) as connection:
        migrate(connection)
        return create_and_save_account_snapshot_with_connection(
            connection,
            market=market_provider_factory(settings),
        )


def create_and_save_account_snapshot_with_connection(
    connection: sqlite3.Connection,
    *,
    market: MarketDataProvider,
) -> CreatedSnapshot:
    ledger = ReadOnlyLedgerService(PositionRepository(connection))
    cash = ReadOnlyCashService(CashAccountRepository(connection))
    service = AccountService(
        ledger=ledger,
        cash=cash,
        market=market,
    )

    snapshot = service.create_snapshot()
    cash_account = cash.get_account()
    positions = ledger.list_positions()
    ledger_max_updated_at = max(
        (position.updated_at for position in positions),
        default=None,
    )
    # 运行任务只持久化账户快照；现金账户和手动持仓台账保持只读。
    snapshot_id = AccountSnapshotRepository(connection).save(
        snapshot,
        cash_account_updated_at=cash_account.updated_at if cash_account else None,
        ledger_max_updated_at=ledger_max_updated_at,
    )
    return CreatedSnapshot(snapshot_id=snapshot_id, snapshot=snapshot)
