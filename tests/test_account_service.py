from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from quantitative_trading.account.models import AccountSnapshotStatus, PositionValuationStatus
from quantitative_trading.account.service import AccountService
from quantitative_trading.cash.models import CashAccount
from quantitative_trading.ledger.models import Position
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


NOW = datetime(2026, 7, 7, 2, 0, tzinfo=UTC)


class FakeLedgerService:
    def __init__(self, positions: list[Position]) -> None:
        self.positions = positions

    def list_positions(self) -> list[Position]:
        return self.positions


class FakeCashService:
    def __init__(self, account: CashAccount | None) -> None:
        self.account = account

    def get_account(self) -> CashAccount | None:
        return self.account


class FakeMarketProvider:
    def __init__(self, quotes: dict[str, QuoteSnapshot]) -> None:
        self.quotes = quotes
        self.calls: list[list[str]] = []

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        requested_symbols = list(symbols)
        self.calls.append(requested_symbols)
        return {
            symbol: self.quotes[symbol]
            for symbol in requested_symbols
            if symbol in self.quotes
        }


class ExplodingMarketProvider:
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        raise AssertionError("market provider should not be called")


class RaisingMarketProvider:
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        raise RuntimeError("akshare timeout")


class SensitiveRaisingMarketProvider:
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        raise RuntimeError(
            "fetch failed token=supersecret Authorization: Bearer abc path=/tmp/private.db"
        )


def cash_account(
    *,
    cash_balance: float = 50000,
    total_transfer_in: float = 50000,
    total_transfer_out: float = 0,
) -> CashAccount:
    return CashAccount(
        cash_balance=cash_balance,
        total_transfer_in=total_transfer_in,
        total_transfer_out=total_transfer_out,
        updated_at=NOW,
    )


def position(
    symbol: str = "600000",
    *,
    name: str = "浦发银行",
    quantity: int = 1000,
    available_quantity: int = 800,
    cost_price: float = 9.5,
) -> Position:
    return Position(
        symbol=symbol,
        name=name,
        quantity=quantity,
        available_quantity=available_quantity,
        cost_price=cost_price,
        opened_at="2026-07-06",
        updated_at=NOW,
    )


def quote(
    symbol: str = "600000",
    *,
    name: str = "浦发银行",
    current_price: float | None = 10.5,
    status: QuoteStatus = QuoteStatus.OK,
    warning: str = "",
) -> QuoteSnapshot:
    if status is QuoteStatus.FAILED:
        current_price = None

    return QuoteSnapshot(
        symbol=symbol,
        name=name,
        current_price=current_price,
        change_pct=1.2 if current_price is not None else None,
        data_time=NOW if status in {QuoteStatus.OK, QuoteStatus.PARTIAL, QuoteStatus.STALE} else None,
        fetched_at=NOW,
        source="fake",
        status=status,
        warning=warning,
    )


def service(
    *,
    account: CashAccount | None,
    positions: list[Position] | None = None,
    market: FakeMarketProvider | ExplodingMarketProvider | RaisingMarketProvider | None = None,
) -> AccountService:
    return AccountService(
        ledger=FakeLedgerService(positions or []),
        cash=FakeCashService(account),
        market=market or FakeMarketProvider({}),
        now=lambda: NOW,
    )


def test_cash_not_initialized_snapshot_does_not_fetch_market_data() -> None:
    snapshot = service(
        account=None,
        positions=[position()],
        market=ExplodingMarketProvider(),
    ).create_snapshot()

    assert snapshot.status is AccountSnapshotStatus.CASH_NOT_INITIALIZED
    assert snapshot.warnings == ["cash account not initialized"]
    assert snapshot.positions == []
    assert snapshot.cash_balance is None
    assert snapshot.total_assets is None


def test_empty_positions_with_initialized_cash_returns_zero_position_metrics() -> None:
    provider = FakeMarketProvider({})

    snapshot = service(account=cash_account(), market=provider).create_snapshot()

    assert snapshot.status is AccountSnapshotStatus.OK
    assert snapshot.cash_balance == 50000
    assert snapshot.net_principal == 50000
    assert snapshot.market_value == 0
    assert snapshot.position_cost == 0
    assert snapshot.floating_pnl == 0
    assert snapshot.floating_pnl_pct is None
    assert snapshot.total_assets == 50000
    assert snapshot.total_pnl == 0
    assert snapshot.total_pnl_pct == 0
    assert snapshot.position_ratio == 0
    assert snapshot.available_buying_cash == 50000
    assert snapshot.positions == []
    assert provider.calls == []


def test_empty_positions_with_zero_total_assets_has_no_position_ratio() -> None:
    snapshot = service(account=cash_account(cash_balance=0, total_transfer_in=0)).create_snapshot()

    assert snapshot.status is AccountSnapshotStatus.OK
    assert snapshot.total_assets == 0
    assert snapshot.position_ratio is None


def test_single_position_snapshot_uses_ledger_and_quote_formulas() -> None:
    snapshot = service(
        account=cash_account(),
        positions=[position()],
        market=FakeMarketProvider({"600000": quote()}),
    ).create_snapshot()

    assert snapshot.status is AccountSnapshotStatus.OK
    assert snapshot.market_value == 10500
    assert snapshot.position_cost == 9500
    assert snapshot.floating_pnl == 1000
    assert snapshot.floating_pnl_pct == pytest.approx(1000 / 9500)
    assert snapshot.total_assets == 60500
    assert snapshot.total_pnl == 10500
    assert snapshot.total_pnl_pct == pytest.approx(10500 / 50000)
    assert snapshot.position_ratio == pytest.approx(10500 / 60500)
    assert snapshot.available_buying_cash == 50000

    valuation = snapshot.positions[0]
    assert valuation.symbol == "600000"
    assert valuation.name == "浦发银行"
    assert valuation.quantity == 1000
    assert valuation.available_quantity == 800
    assert valuation.cost_price == 9.5
    assert valuation.current_price == 10.5
    assert valuation.market_value == 10500
    assert valuation.floating_pnl == 1000
    assert valuation.ledger_updated_at == NOW
    assert valuation.quote_data_time == NOW
    assert valuation.quote_fetched_at == NOW
    assert valuation.status is PositionValuationStatus.OK


def test_zero_net_principal_has_no_total_pnl_pct() -> None:
    snapshot = service(
        account=cash_account(cash_balance=0, total_transfer_in=0),
        positions=[position()],
        market=FakeMarketProvider({"600000": quote()}),
    ).create_snapshot()

    assert snapshot.status is AccountSnapshotStatus.OK
    assert snapshot.total_assets == 10500
    assert snapshot.total_pnl == 10500
    assert snapshot.total_pnl_pct is None
    assert snapshot.position_ratio == 1


def test_mixed_usable_and_unavailable_quotes_returns_partial_without_account_totals() -> None:
    stale_quote = quote("000002", name="万科A", status=QuoteStatus.STALE, warning="quote stale")
    failed_quote = quote(
        "000003",
        name="中航西飞",
        current_price=None,
        status=QuoteStatus.FAILED,
        warning="provider failed",
    )

    snapshot = service(
        account=cash_account(),
        positions=[
            position("600000"),
            position("000001", name="平安银行", cost_price=12),
            position("000002", name="万科A", cost_price=8),
            position("000003", name="中航西飞", cost_price=20),
        ],
        market=FakeMarketProvider(
            {
                "600000": quote(),
                "000002": stale_quote,
                "000003": failed_quote,
            }
        ),
    ).create_snapshot()

    assert snapshot.status is AccountSnapshotStatus.PARTIAL
    assert snapshot.cash_balance == 50000
    assert snapshot.net_principal == 50000
    assert snapshot.available_buying_cash == 50000
    assert snapshot.market_value is None
    assert snapshot.position_cost is None
    assert snapshot.floating_pnl is None
    assert snapshot.floating_pnl_pct is None
    assert snapshot.total_assets is None
    assert snapshot.total_pnl is None
    assert snapshot.total_pnl_pct is None
    assert snapshot.position_ratio is None
    assert [valuation.status for valuation in snapshot.positions] == [
        PositionValuationStatus.OK,
        PositionValuationStatus.FAILED,
        PositionValuationStatus.STALE,
        PositionValuationStatus.FAILED,
    ]
    assert snapshot.positions[0].market_value == 10500
    assert snapshot.positions[0].position_cost == 9500
    assert snapshot.positions[0].floating_pnl == 1000
    assert snapshot.positions[2].current_price is None
    assert snapshot.positions[2].market_value is None
    assert snapshot.positions[2].floating_pnl is None
    assert snapshot.positions[2].quote_data_time == NOW
    assert "account totals unavailable: only 1/4 positions have usable quotes" in snapshot.warnings
    assert any("000001" in warning and "quote unavailable" in warning for warning in snapshot.warnings)
    assert any("000002" in warning and "quote stale" in warning for warning in snapshot.warnings)
    assert any("000003" in warning and "provider failed" in warning for warning in snapshot.warnings)


def test_all_quotes_unavailable_returns_market_data_unavailable() -> None:
    snapshot = service(
        account=cash_account(),
        positions=[position(), position("000001", name="平安银行")],
        market=FakeMarketProvider(
            {
                "000001": quote(
                    "000001",
                    name="平安银行",
                    status=QuoteStatus.STALE,
                    warning="quote stale",
                )
            }
        ),
    ).create_snapshot()

    assert snapshot.status is AccountSnapshotStatus.MARKET_DATA_UNAVAILABLE
    assert snapshot.market_value is None
    assert snapshot.position_cost is None
    assert snapshot.floating_pnl is None
    assert snapshot.total_assets is None
    assert snapshot.total_pnl is None
    assert [valuation.status for valuation in snapshot.positions] == [
        PositionValuationStatus.FAILED,
        PositionValuationStatus.STALE,
    ]


def test_market_provider_exception_returns_market_data_unavailable_snapshot() -> None:
    snapshot = service(
        account=cash_account(),
        positions=[position(), position("000001", name="平安银行")],
        market=RaisingMarketProvider(),
    ).create_snapshot()

    assert snapshot.status is AccountSnapshotStatus.MARKET_DATA_UNAVAILABLE
    assert snapshot.cash_balance == 50000
    assert snapshot.net_principal == 50000
    assert snapshot.available_buying_cash == 50000
    assert snapshot.market_value is None
    assert snapshot.position_cost is None
    assert snapshot.floating_pnl is None
    assert snapshot.total_assets is None
    assert snapshot.total_pnl is None
    assert [valuation.status for valuation in snapshot.positions] == [
        PositionValuationStatus.FAILED,
        PositionValuationStatus.FAILED,
    ]
    assert all("market data provider failed: akshare timeout" == valuation.warning for valuation in snapshot.positions)
    assert any("akshare timeout" in warning for warning in snapshot.warnings)


def test_market_provider_exception_warning_redacts_sensitive_values() -> None:
    snapshot = service(
        account=cash_account(),
        positions=[position()],
        market=SensitiveRaisingMarketProvider(),
    ).create_snapshot()

    warnings = [*snapshot.warnings, snapshot.positions[0].warning or ""]

    assert snapshot.status is AccountSnapshotStatus.MARKET_DATA_UNAVAILABLE
    assert all("market data provider failed" in warning for warning in warnings)
    assert all("fetch failed" in warning for warning in warnings)
    assert all("supersecret" not in warning for warning in warnings)
    assert all("Bearer abc" not in warning for warning in warnings)
    assert all("/tmp/private.db" not in warning for warning in warnings)


def test_partial_quote_with_warning_is_still_valued() -> None:
    snapshot = service(
        account=cash_account(),
        positions=[position()],
        market=FakeMarketProvider(
            {
                "600000": quote(
                    status=QuoteStatus.PARTIAL,
                    warning="quote missing change_pct",
                )
            }
        ),
    ).create_snapshot()

    assert snapshot.status is AccountSnapshotStatus.OK
    assert snapshot.market_value == 10500
    assert snapshot.positions[0].status is PositionValuationStatus.OK
    assert snapshot.positions[0].warning == "quote missing change_pct"
    assert snapshot.warnings == ["600000: quote missing change_pct"]
