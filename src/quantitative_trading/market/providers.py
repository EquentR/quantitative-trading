from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


class MarketDataProvider(Protocol):
    """Boundary for market quote adapters.

    Providers may return a sparse mapping: if a requested symbol is absent,
    that quote is unavailable. Providers may also return a FAILED
    QuoteSnapshot when they can report an explicit per-symbol failure.
    """

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
