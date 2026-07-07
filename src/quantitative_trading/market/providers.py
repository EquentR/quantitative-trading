from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


class MarketDataProvider(Protocol):
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        ...


class DisabledMarketProvider:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        fetched_at = self._now()
        return {
            symbol: QuoteSnapshot(
                symbol=symbol,
                fetched_at=fetched_at,
                source="disabled",
                status=QuoteStatus.FAILED,
                warning="market fetch disabled",
            )
            for symbol in symbols
        }
