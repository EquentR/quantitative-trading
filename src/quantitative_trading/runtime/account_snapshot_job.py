from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from quantitative_trading.account.models import AccountSnapshot
from quantitative_trading.account.repository import AccountSnapshotRepository
from quantitative_trading.account.service import AccountService
from quantitative_trading.cash.models import CashAccount
from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.cash.service import ReadOnlyCashService
from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import Position
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


class UnsupportedMarketProviderError(ValueError):
    def __init__(self, provider: str) -> None:
        super().__init__("unsupported market provider")
        self.provider = provider


class AccountSnapshotStorageError(RuntimeError):
    pass


MarketProviderFactory = Callable[[Settings], MarketDataProvider]


def market_provider_from_settings(settings: Settings) -> MarketDataProvider:
    if not settings.enable_market_fetch:
        return DisabledMarketProvider()
    if settings.market_provider.strip().lower() == "akshare":
        return AkShareMarketProvider()
    raise UnsupportedMarketProviderError(settings.market_provider)


def create_and_save_account_snapshot(
    settings: Settings,
    *,
    market_provider_factory: MarketProviderFactory = market_provider_from_settings,
) -> CreatedSnapshot:
    try:
        with connect(settings) as connection:
            migrate(connection)
            return create_and_save_account_snapshot_with_connection(
                connection,
                market=market_provider_factory(settings),
            )
    except UnsupportedMarketProviderError:
        raise
    except (sqlite3.Error, ValidationError) as exc:
        raise AccountSnapshotStorageError("account snapshot storage failed") from exc


def create_and_save_account_snapshot_with_connection(
    connection: sqlite3.Connection,
    *,
    market: MarketDataProvider,
) -> CreatedSnapshot:
    try:
        ledger = ReadOnlyLedgerService(PositionRepository(connection))
        cash = ReadOnlyCashService(CashAccountRepository(connection))
        cash_account = cash.get_account()
        positions = ledger.list_positions()
    except (sqlite3.Error, ValidationError) as exc:
        raise AccountSnapshotStorageError("account snapshot storage failed") from exc

    service = AccountService(
        ledger=_FixedLedgerService(positions),
        cash=_FixedCashService(cash_account),
        market=market,
    )

    snapshot = service.create_snapshot()
    ledger_max_updated_at = max(
        (position.updated_at for position in positions),
        default=None,
    )
    # 运行任务只持久化账户快照；现金账户和手动持仓台账保持只读。
    try:
        snapshot_id = AccountSnapshotRepository(connection).save(
            snapshot,
            cash_account_updated_at=cash_account.updated_at if cash_account else None,
            ledger_max_updated_at=ledger_max_updated_at,
        )
    except (sqlite3.Error, ValidationError) as exc:
        raise AccountSnapshotStorageError("account snapshot storage failed") from exc
    return CreatedSnapshot(snapshot_id=snapshot_id, snapshot=snapshot)


class _FixedCashService:
    def __init__(self, account: CashAccount | None) -> None:
        self._account = account

    def get_account(self) -> CashAccount | None:
        return self._account


class _FixedLedgerService:
    def __init__(self, positions: list[Position]) -> None:
        self._positions = positions

    def list_positions(self) -> list[Position]:
        return self._positions
