from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from quantitative_trading.account.models import (
    AccountSnapshot,
    AccountSnapshotStatus,
    PositionValuation,
    PositionValuationStatus,
)
from quantitative_trading.cash.models import CashAccount
from quantitative_trading.cash.service import ReadOnlyCashService
from quantitative_trading.ledger.models import Position
from quantitative_trading.ledger.service import ReadOnlyLedgerService
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus
from quantitative_trading.market.providers import MarketDataProvider
from quantitative_trading.sanitization import redact_sensitive_text, safe_error_summary


class AccountService:
    def __init__(
        self,
        *,
        ledger: ReadOnlyLedgerService,
        cash: ReadOnlyCashService,
        market: MarketDataProvider,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._ledger = ledger
        self._cash = cash
        self._market = market
        self._now = now or (lambda: datetime.now(UTC))

    def create_snapshot(self) -> AccountSnapshot:
        created_at = self._now()
        cash_account = self._cash.get_account()
        if cash_account is None:
            return AccountSnapshot(
                positions=[],
                status=AccountSnapshotStatus.CASH_NOT_INITIALIZED,
                warnings=["cash account not initialized"],
                created_at=created_at,
            )

        positions = self._ledger.list_positions()
        if not positions:
            return self._empty_position_snapshot(cash_account, created_at)

        try:
            quotes = self._market.get_quotes([position.symbol for position in positions])
        except Exception as exc:
            return self._market_provider_failed_snapshot(
                cash_account,
                positions,
                created_at,
                exc,
            )

        valuations = [
            self._value_position(position, quotes.get(position.symbol))
            for position in positions
        ]
        usable_valuations = [
            valuation
            for valuation in valuations
            if valuation.status is PositionValuationStatus.OK
            and valuation.market_value is not None
        ]

        if not usable_valuations:
            return AccountSnapshot(
                cash_balance=cash_account.cash_balance,
                net_principal=cash_account.net_principal,
                available_buying_cash=cash_account.cash_balance,
                positions=valuations,
                status=AccountSnapshotStatus.MARKET_DATA_UNAVAILABLE,
                warnings=self._collect_warnings(valuations),
                created_at=created_at,
            )

        if len(usable_valuations) < len(valuations):
            warnings = [
                self._coverage_warning(len(usable_valuations), len(valuations)),
                *self._collect_warnings(valuations),
            ]
            return AccountSnapshot(
                cash_balance=cash_account.cash_balance,
                net_principal=cash_account.net_principal,
                available_buying_cash=cash_account.cash_balance,
                positions=valuations,
                status=AccountSnapshotStatus.PARTIAL,
                warnings=warnings,
                created_at=created_at,
            )

        market_value = sum(valuation.market_value or 0 for valuation in usable_valuations)
        position_cost = sum(valuation.position_cost for valuation in usable_valuations)
        floating_pnl = market_value - position_cost
        total_assets = cash_account.cash_balance + market_value
        total_pnl = total_assets - cash_account.net_principal

        return AccountSnapshot(
            cash_balance=cash_account.cash_balance,
            net_principal=cash_account.net_principal,
            market_value=market_value,
            position_cost=position_cost,
            floating_pnl=floating_pnl,
            floating_pnl_pct=self._ratio_or_none(floating_pnl, position_cost),
            total_assets=total_assets,
            total_pnl=total_pnl,
            total_pnl_pct=self._ratio_or_none(total_pnl, cash_account.net_principal),
            position_ratio=self._ratio_or_none(market_value, total_assets),
            available_buying_cash=cash_account.cash_balance,
            positions=valuations,
            status=AccountSnapshotStatus.OK,
            warnings=self._collect_warnings(valuations),
            created_at=created_at,
        )

    def _empty_position_snapshot(
        self,
        cash_account: CashAccount,
        created_at: datetime,
    ) -> AccountSnapshot:
        total_assets = cash_account.cash_balance
        total_pnl = total_assets - cash_account.net_principal
        return AccountSnapshot(
            cash_balance=cash_account.cash_balance,
            net_principal=cash_account.net_principal,
            market_value=0,
            position_cost=0,
            floating_pnl=0,
            floating_pnl_pct=None,
            total_assets=total_assets,
            total_pnl=total_pnl,
            total_pnl_pct=self._ratio_or_none(total_pnl, cash_account.net_principal),
            position_ratio=self._ratio_or_none(0, total_assets),
            available_buying_cash=cash_account.cash_balance,
            positions=[],
            status=AccountSnapshotStatus.OK,
            warnings=[],
            created_at=created_at,
        )

    def _market_provider_failed_snapshot(
        self,
        cash_account: CashAccount,
        positions: list[Position],
        created_at: datetime,
        exc: Exception,
    ) -> AccountSnapshot:
        warning = f"market data provider failed: {safe_error_summary(exc)}"
        valuations = [
            self._unavailable_position(
                position,
                position.quantity * position.cost_price,
                status=PositionValuationStatus.FAILED,
                warning=warning,
            )
            for position in positions
        ]
        return AccountSnapshot(
            cash_balance=cash_account.cash_balance,
            net_principal=cash_account.net_principal,
            available_buying_cash=cash_account.cash_balance,
            positions=valuations,
            status=AccountSnapshotStatus.MARKET_DATA_UNAVAILABLE,
            warnings=self._collect_warnings(valuations),
            created_at=created_at,
        )

    def _value_position(
        self,
        position: Position,
        quote: QuoteSnapshot | None,
    ) -> PositionValuation:
        position_cost = position.quantity * position.cost_price
        if quote is None:
            return self._unavailable_position(
                position,
                position_cost,
                status=PositionValuationStatus.FAILED,
                warning="quote unavailable",
            )

        if quote.status is QuoteStatus.STALE:
            return self._unavailable_position(
                position,
                position_cost,
                status=PositionValuationStatus.STALE,
                warning=self._stale_warning(quote),
                quote=quote,
            )

        if quote.status is QuoteStatus.FAILED or quote.current_price is None:
            return self._unavailable_position(
                position,
                position_cost,
                status=PositionValuationStatus.FAILED,
                warning=self._failed_warning(quote),
                quote=quote,
            )

        market_value = position.quantity * quote.current_price
        floating_pnl = market_value - position_cost
        return PositionValuation(
            symbol=position.symbol,
            name=position.name,
            quantity=position.quantity,
            available_quantity=position.available_quantity,
            cost_price=position.cost_price,
            position_cost=position_cost,
            current_price=quote.current_price,
            market_value=market_value,
            floating_pnl=floating_pnl,
            floating_pnl_pct=self._ratio_or_none(floating_pnl, position_cost),
            ledger_updated_at=position.updated_at,
            quote_data_time=quote.data_time,
            quote_fetched_at=quote.fetched_at,
            status=PositionValuationStatus.OK,
            warning=self._quote_warning(quote),
        )

    def _unavailable_position(
        self,
        position: Position,
        position_cost: float,
        *,
        status: PositionValuationStatus,
        warning: str,
        quote: QuoteSnapshot | None = None,
    ) -> PositionValuation:
        return PositionValuation(
            symbol=position.symbol,
            name=position.name,
            quantity=position.quantity,
            available_quantity=position.available_quantity,
            cost_price=position.cost_price,
            position_cost=position_cost,
            ledger_updated_at=position.updated_at,
            quote_data_time=quote.data_time if quote is not None else None,
            quote_fetched_at=quote.fetched_at if quote is not None else None,
            status=status,
            warning=warning,
        )

    @staticmethod
    def _failed_warning(quote: QuoteSnapshot) -> str:
        if quote.warning:
            return f"quote unavailable: {redact_sensitive_text(quote.warning)}"
        return "quote unavailable"

    @staticmethod
    def _stale_warning(quote: QuoteSnapshot) -> str:
        if quote.warning:
            return f"quote stale/unavailable: {redact_sensitive_text(quote.warning)}"
        return "quote stale/unavailable"

    @staticmethod
    def _quote_warning(quote: QuoteSnapshot) -> str:
        if not quote.warning:
            return ""
        return redact_sensitive_text(quote.warning)

    @staticmethod
    def _collect_warnings(valuations: list[PositionValuation]) -> list[str]:
        return [
            f"{valuation.symbol}: {valuation.warning}"
            for valuation in valuations
            if valuation.warning
        ]

    @staticmethod
    def _coverage_warning(usable_count: int, total_count: int) -> str:
        return (
            "account totals unavailable: only "
            f"{usable_count}/{total_count} positions have usable quotes"
        )

    @staticmethod
    def _ratio_or_none(numerator: float, denominator: float) -> float | None:
        if denominator == 0:
            return None
        return numerator / denominator
