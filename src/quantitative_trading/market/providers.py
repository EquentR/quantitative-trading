from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Protocol

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


AKSHARE_SYMBOL_FIELD = "代码"
AKSHARE_NAME_FIELD = "名称"
AKSHARE_PRICE_FIELD = "最新价"
AKSHARE_CHANGE_PCT_FIELD = "涨跌幅"


class AkShareMarketProvider:
    def __init__(self, *, akshare_module: Any | None = None, now: Callable[[], datetime] | None = None) -> None:
        self._akshare = akshare_module
        self._now = now or (lambda: datetime.now(UTC))

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        fetched_at = self._now()
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("akshare quote fetched_at must be timezone-aware")

        try:
            akshare = self._akshare
            if akshare is None:
                import akshare as akshare_module  # type: ignore[import-not-found]

                akshare = akshare_module
            frame = akshare.stock_zh_a_spot_em()
        except Exception as exc:
            return {
                symbol: self._failed_quote(
                    symbol=symbol,
                    fetched_at=fetched_at,
                    warning=f"akshare quote fetch failed: {exc}",
                )
                for symbol in symbols
            }

        return {
            symbol: self._quote_from_frame(frame=frame, symbol=symbol, fetched_at=fetched_at)
            for symbol in symbols
        }

    def _quote_from_frame(self, *, frame: Any, symbol: str, fetched_at: datetime) -> QuoteSnapshot:
        try:
            rows = frame[frame[AKSHARE_SYMBOL_FIELD].astype(str) == symbol]
            if rows.empty:
                return self._failed_quote(
                    symbol=symbol,
                    fetched_at=fetched_at,
                    warning="quote not found",
                )
        except Exception as exc:
            return self._failed_quote(
                symbol=symbol,
                fetched_at=fetched_at,
                warning=f"akshare quote mapping failed: {exc}",
            )

        try:
            row = rows.iloc[0]
            name = _required_text(row, AKSHARE_NAME_FIELD)
            current_price = _required_float(row, AKSHARE_PRICE_FIELD, positive=True)
            change_pct = _required_float(row, AKSHARE_CHANGE_PCT_FIELD, positive=False)
            return QuoteSnapshot(
                symbol=symbol,
                name=name,
                current_price=current_price,
                change_pct=change_pct,
                data_time=fetched_at,
                fetched_at=fetched_at,
                source="akshare",
                status=QuoteStatus.OK,
            )
        except Exception as exc:
            return self._failed_quote(
                symbol=symbol,
                fetched_at=fetched_at,
                warning=f"akshare quote mapping failed: {exc}",
            )

    def _failed_quote(self, *, symbol: str, fetched_at: datetime, warning: str) -> QuoteSnapshot:
        return QuoteSnapshot(
            symbol=symbol,
            fetched_at=fetched_at,
            source="akshare",
            status=QuoteStatus.FAILED,
            warning=warning,
        )


def _required_text(row: Any, field: str) -> str:
    value = row[field]
    if value is None:
        raise ValueError(f"{field} is required")
    text = str(value).strip()
    if not text or text.lower() in {"nan", "<na>", "nat"}:
        raise ValueError(f"{field} is required")
    return text


def _required_float(row: Any, field: str, *, positive: bool) -> float:
    try:
        number = float(row[field])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc

    if not isfinite(number):
        raise ValueError(f"{field} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{field} must be greater than 0")
    return number
