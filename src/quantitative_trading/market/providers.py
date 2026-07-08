from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Protocol

import requests

from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus
from quantitative_trading.sanitization import safe_error_summary


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
EASTMONEY_SINGLE_QUOTE_URL = "https://82.push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_SINGLE_QUOTE_FIELDS = "f57,f58,f43,f170"
EASTMONEY_SINGLE_QUOTE_TIMEOUT_SECONDS = 10.0
EASTMONEY_SINGLE_QUOTE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://quote.eastmoney.com/",
}
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
TENCENT_QUOTE_TIMEOUT_SECONDS = 10.0
TENCENT_QUOTE_HEADERS = {"User-Agent": "Mozilla/5.0"}
TENCENT_QUOTE_PATTERN = re.compile(r'v_[^=]+="([^"]*)";')


class AkShareMarketProvider:
    def __init__(
        self,
        *,
        akshare_module: Any | None = None,
        now: Callable[[], datetime] | None = None,
        eastmoney_single_quote_fetcher: Callable[..., Any] | None = None,
        tencent_quote_fetcher: Callable[..., Any] | None = None,
    ) -> None:
        self._akshare = akshare_module
        self._now = now or (lambda: datetime.now(UTC))
        self._eastmoney_single_quote_fetcher = (
            eastmoney_single_quote_fetcher or _fetch_eastmoney_single_quote
        )
        self._tencent_quote_fetcher = tencent_quote_fetcher or _fetch_tencent_quotes

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        if not symbols:
            return {}

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
            return self._fallback_single_quotes(
                symbols,
                fetched_at=fetched_at,
                batch_error=exc,
            )

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
                warning=f"akshare quote mapping failed: {safe_error_summary(exc)}",
            )

        try:
            row = rows.iloc[0]
            current_price = _required_float(row, AKSHARE_PRICE_FIELD, positive=True)
        except Exception as exc:
            return self._failed_quote(
                symbol=symbol,
                fetched_at=fetched_at,
                warning=f"akshare quote mapping failed: {safe_error_summary(exc)}",
            )

        warnings: list[str] = []
        name = ""
        try:
            name = _required_text(row, AKSHARE_NAME_FIELD)
        except Exception as exc:
            warnings.append(
                f"{AKSHARE_NAME_FIELD} missing or unavailable: {safe_error_summary(exc)}"
            )

        change_pct: float | None = None
        try:
            change_pct = _required_float(row, AKSHARE_CHANGE_PCT_FIELD, positive=False)
        except Exception as exc:
            warnings.append(f"{AKSHARE_CHANGE_PCT_FIELD} unavailable: {safe_error_summary(exc)}")

        return QuoteSnapshot(
            symbol=symbol,
            name=name,
            current_price=current_price,
            change_pct=change_pct,
            data_time=fetched_at,
            fetched_at=fetched_at,
            source="akshare",
            status=QuoteStatus.PARTIAL if warnings else QuoteStatus.OK,
            warning="; ".join(warnings),
        )

    def _failed_quote(self, *, symbol: str, fetched_at: datetime, warning: str) -> QuoteSnapshot:
        return QuoteSnapshot(
            symbol=symbol,
            fetched_at=fetched_at,
            source="akshare",
            status=QuoteStatus.FAILED,
            warning=warning,
        )

    def _fallback_single_quotes(
        self,
        symbols: Sequence[str],
        *,
        fetched_at: datetime,
        batch_error: Exception,
    ) -> dict[str, QuoteSnapshot]:
        batch_warning = f"akshare quote fetch failed: {safe_error_summary(batch_error)}"
        quotes: dict[str, QuoteSnapshot] = {}
        failed_symbols: list[str] = []
        failed_warnings: dict[str, str] = {}
        for symbol in symbols:
            try:
                quotes[symbol] = self._eastmoney_single_quote(symbol, fetched_at=fetched_at)
            except Exception as exc:
                failed_symbols.append(symbol)
                failed_warnings[symbol] = (
                    f"{batch_warning}; eastmoney single quote fallback failed: "
                    f"{safe_error_summary(exc)}"
                )

        if failed_symbols:
            try:
                tencent_quotes = self._tencent_quotes(failed_symbols, fetched_at=fetched_at)
            except Exception as exc:
                for symbol in failed_symbols:
                    quotes[symbol] = self._failed_quote(
                        symbol=symbol,
                        fetched_at=fetched_at,
                        warning=(
                            f"{failed_warnings[symbol]}; tencent quote fallback failed: "
                            f"{safe_error_summary(exc)}"
                        ),
                    )
            else:
                for symbol in failed_symbols:
                    quote = tencent_quotes.get(symbol)
                    if quote is not None:
                        quotes[symbol] = quote
                    else:
                        quotes[symbol] = self._failed_quote(
                            symbol=symbol,
                            fetched_at=fetched_at,
                            warning=f"{failed_warnings[symbol]}; tencent quote not found",
                        )
        return quotes

    def _eastmoney_single_quote(self, symbol: str, *, fetched_at: datetime) -> QuoteSnapshot:
        payload = self._eastmoney_single_quote_fetcher(
            _eastmoney_single_quote_url(symbol),
            timeout=EASTMONEY_SINGLE_QUOTE_TIMEOUT_SECONDS,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("eastmoney single quote response missing data")

        response_symbol = _required_text(data, "f57")
        if response_symbol != symbol:
            raise ValueError("eastmoney single quote symbol mismatch")

        current_price = _eastmoney_current_price(data, "f43", symbol=symbol)
        name = _required_text(data, "f58")
        change_pct = _scaled_eastmoney_quote_float(data, "f170", positive=False)
        return QuoteSnapshot(
            symbol=symbol,
            name=name,
            current_price=current_price,
            change_pct=change_pct,
            data_time=fetched_at,
            fetched_at=fetched_at,
            source="eastmoney_single_quote",
            status=QuoteStatus.OK,
        )

    def _tencent_quotes(
        self,
        symbols: Sequence[str],
        *,
        fetched_at: datetime,
    ) -> dict[str, QuoteSnapshot]:
        payload = self._tencent_quote_fetcher(
            _tencent_quote_url(symbols),
            timeout=TENCENT_QUOTE_TIMEOUT_SECONDS,
        )
        text = _decode_tencent_payload(payload)
        requested = set(symbols)
        quotes: dict[str, QuoteSnapshot] = {}
        for match in TENCENT_QUOTE_PATTERN.finditer(text):
            parts = match.group(1).split("~")
            data = _tencent_parts_to_quote_data(parts)
            symbol = _required_text(data, "symbol")
            if symbol not in requested:
                continue
            current_price = _required_float(data, "current_price", positive=True)
            name = _required_text(data, "name")
            change_pct = _required_float(data, "change_pct", positive=False)
            quotes[symbol] = QuoteSnapshot(
                symbol=symbol,
                name=name,
                current_price=current_price,
                change_pct=change_pct,
                data_time=fetched_at,
                fetched_at=fetched_at,
                source="tencent_quote",
                status=QuoteStatus.OK,
            )
        return quotes


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


def _eastmoney_current_price(row: Any, field: str, *, symbol: str) -> float:
    return _required_float(row, field, positive=True) / _eastmoney_price_divisor(symbol)


def _scaled_eastmoney_quote_float(row: Any, field: str, *, positive: bool) -> float:
    return _required_float(row, field, positive=positive) / 100


def _eastmoney_price_divisor(symbol: str) -> int:
    if symbol.startswith(("1", "5")):
        return 1000
    return 100


def _eastmoney_single_quote_url(symbol: str) -> str:
    return (
        f"{EASTMONEY_SINGLE_QUOTE_URL}"
        f"?secid={_eastmoney_secid(symbol)}&fields={EASTMONEY_SINGLE_QUOTE_FIELDS}"
    )


def _eastmoney_secid(symbol: str) -> str:
    market = "1" if symbol.startswith(("5", "6", "9")) else "0"
    return f"{market}.{symbol}"


def _tencent_quote_url(symbols: Sequence[str]) -> str:
    return f"{TENCENT_QUOTE_URL}{','.join(_tencent_quote_code(symbol) for symbol in symbols)}"


def _tencent_quote_code(symbol: str) -> str:
    market = "sh" if symbol.startswith(("5", "6", "9")) else "sz"
    if symbol.startswith(("4", "8")):
        market = "bj"
    return f"{market}{symbol}"


def _tencent_parts_to_quote_data(parts: list[str]) -> dict[str, str]:
    try:
        return {
            "name": parts[1],
            "symbol": parts[2],
            "current_price": parts[3],
            "change_pct": parts[32],
        }
    except IndexError as exc:
        raise ValueError("tencent quote response has missing fields") from exc


def _decode_tencent_payload(payload: Any) -> str:
    if isinstance(payload, bytes):
        return payload.decode("gbk", errors="replace")
    if isinstance(payload, str):
        return payload
    raise ValueError("tencent quote response must be bytes or text")


def _fetch_eastmoney_single_quote(url: str, *, timeout: float) -> dict[str, Any]:
    response = requests.get(url, headers=EASTMONEY_SINGLE_QUOTE_HEADERS, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("eastmoney single quote response must be an object")
    return payload


def _fetch_tencent_quotes(url: str, *, timeout: float) -> bytes:
    response = requests.get(url, headers=TENCENT_QUOTE_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.content
