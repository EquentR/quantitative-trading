from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import requests

from quantitative_trading.market.models import (
    LimitStatus,
    QuoteSnapshot,
    QuoteStatus,
    TradingStatus,
)
from quantitative_trading.sanitization import safe_error_summary


class QuoteProvider(Protocol):
    """Boundary for market quote adapters.

    Providers may return a sparse mapping: if a requested symbol is absent,
    that quote is unavailable. Providers may also return a FAILED
    QuoteSnapshot when they can report an explicit per-symbol failure.
    """

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        ...


MarketDataProvider = QuoteProvider


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
AKSHARE_PREVIOUS_CLOSE_FIELD = "昨收"
AKSHARE_OPEN_FIELD = "今开"
AKSHARE_HIGH_FIELD = "最高"
AKSHARE_LOW_FIELD = "最低"
AKSHARE_VOLUME_FIELD = "成交量"
AKSHARE_AMOUNT_FIELD = "成交额"
AKSHARE_ETF_OPEN_FIELD = "开盘价"
AKSHARE_ETF_HIGH_FIELD = "最高价"
AKSHARE_ETF_LOW_FIELD = "最低价"
AKSHARE_ETF_UPDATE_TIME_FIELD = "更新时间"
SSE_ETF_PHASE_URL = "https://yunhq.sse.com.cn:32042/v1/sh1/list/exchange/etf"
SZSE_ETF_PHASE_URL = "https://www.szse.cn/api/market/ssjjhq/getTimeData"
OFFICIAL_PHASE_TIMEOUT_SECONDS = 10.0
OFFICIAL_PHASE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.sse.com.cn/",
}
SZSE_PHASE_NORMAL = {"00", "02", "03", "05", "07", "11"}
SZSE_PHASE_SUSPENDED = {"04", "06", "12"}
SSE_PHASE_NORMAL_PREFIXES = {"S", "C", "T", "B", "E"}
SSE_PHASE_SUSPENDED_PREFIXES = {"P"}
EASTMONEY_SINGLE_QUOTE_URL = "https://82.push2.eastmoney.com/api/qt/stock/get"
EASTMONEY_SINGLE_QUOTE_FIELDS = "f57,f58,f43,f170,f124"
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
    source = "akshare"

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
            frame = self._spot_frame(akshare)
        except Exception as exc:
            return self._fallback_single_quotes(
                symbols,
                fetched_at=fetched_at,
                batch_error=exc,
            )

        trading_statuses: dict[str, TradingStatus] = {}
        phase_warning: str | None = None
        try:
            trading_statuses = self._trading_statuses(symbols)
        except Exception as exc:
            phase_warning = (
                "official trading phase fetch failed; trading_status kept unknown: "
                f"{safe_error_summary(exc)}"
            )

        return {
            symbol: self._quote_from_frame(
                frame=frame,
                symbol=symbol,
                fetched_at=fetched_at,
                trading_status=trading_statuses.get(symbol, TradingStatus.UNKNOWN),
                phase_warning=phase_warning,
            )
            for symbol in symbols
        }

    def _quote_from_frame(
        self,
        *,
        frame: Any,
        symbol: str,
        fetched_at: datetime,
        trading_status: TradingStatus,
        phase_warning: str | None,
    ) -> QuoteSnapshot:
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
        data_time: datetime | None = None
        try:
            data_time = self._data_time(row)
        except Exception as exc:
            warnings.append(
                "market source time unavailable; data_time kept unknown: "
                f"{safe_error_summary(exc)}"
            )
        if trading_status is TradingStatus.UNKNOWN:
            warnings.append(
                phase_warning or "trading_status unavailable; kept unknown"
            )
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

        optional_values: dict[str, float | None] = {}
        for model_field, source_field, positive, multiplier in self._optional_fields():
            try:
                value = _required_float(row, source_field, positive=positive)
                if not positive and value < 0:
                    raise ValueError(f"{source_field} must be nonnegative")
                optional_values[model_field] = value * multiplier
            except Exception as exc:
                optional_values[model_field] = None
                warnings.append(f"{source_field} unavailable: {safe_error_summary(exc)}")

        limit_status, limit_warning = self._derive_limit_status(
            symbol=symbol,
            current_price=current_price,
            previous_close=optional_values["previous_close"],
        )
        if limit_warning:
            warnings.append(limit_warning)

        return QuoteSnapshot(
            symbol=symbol,
            name=name,
            current_price=current_price,
            change_pct=change_pct,
            trading_status=trading_status,
            limit_status=limit_status,
            data_time=data_time,
            fetched_at=fetched_at,
            source=self.source,
            status=QuoteStatus.OK if not warnings else QuoteStatus.PARTIAL,
            warning="; ".join(warnings),
            **optional_values,
        )

    def _failed_quote(self, *, symbol: str, fetched_at: datetime, warning: str) -> QuoteSnapshot:
        return QuoteSnapshot(
            symbol=symbol,
            fetched_at=fetched_at,
            source=self.source,
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

    def _spot_frame(self, akshare: Any) -> Any:
        return akshare.stock_zh_a_spot_em()

    def _optional_fields(self) -> tuple[tuple[str, str, bool, float], ...]:
        return (
            ("previous_close", AKSHARE_PREVIOUS_CLOSE_FIELD, True, 1.0),
            ("open_price", AKSHARE_OPEN_FIELD, True, 1.0),
            ("high_price", AKSHARE_HIGH_FIELD, True, 1.0),
            ("low_price", AKSHARE_LOW_FIELD, True, 1.0),
            ("volume", AKSHARE_VOLUME_FIELD, False, 100.0),
            ("amount", AKSHARE_AMOUNT_FIELD, False, 1.0),
        )

    def _data_time(self, row: Any) -> datetime:
        raise ValueError("source time field is unavailable")

    def _trading_statuses(self, symbols: Sequence[str]) -> dict[str, TradingStatus]:
        return {}

    def _derive_limit_status(
        self,
        *,
        symbol: str,
        current_price: float,
        previous_close: float | None,
    ) -> tuple[LimitStatus, str | None]:
        return LimitStatus.UNKNOWN, "limit_status unavailable; kept unknown"

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
        data_time = _optional_eastmoney_timestamp(data, "f124")
        return QuoteSnapshot(
            symbol=symbol,
            name=name,
            current_price=current_price,
            change_pct=change_pct,
            data_time=data_time,
            fetched_at=fetched_at,
            source="eastmoney_single_quote",
            status=QuoteStatus.PARTIAL,
            warning=(
                "standard OHLC/volume/amount unavailable; "
                "trading_status and limit_status kept unknown"
            ),
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
            try:
                data = _tencent_parts_to_quote_data(parts)
                symbol = _required_text(data, "symbol")
            except Exception:
                continue
            if symbol not in requested:
                continue
            try:
                current_price = _required_float(data, "current_price", positive=True)
                name = _required_text(data, "name")
                change_pct = _required_float(data, "change_pct", positive=False)
                data_time = _optional_tencent_timestamp(data, "data_time")
                quotes[symbol] = QuoteSnapshot(
                    symbol=symbol,
                    name=name,
                    current_price=current_price,
                    change_pct=change_pct,
                    data_time=data_time,
                    fetched_at=fetched_at,
                    source="tencent_quote",
                    status=QuoteStatus.PARTIAL,
                    warning=(
                        "standard OHLC/volume/amount unavailable; "
                        "trading_status and limit_status kept unknown"
                    ),
                )
            except Exception as exc:
                quotes[symbol] = self._failed_quote(
                    symbol=symbol,
                    fetched_at=fetched_at,
                    warning=f"tencent quote mapping failed: {safe_error_summary(exc)}",
                )
        return quotes


class AkShareEtfMarketProvider(AkShareMarketProvider):
    source = "akshare_etf"
    price_tick = Decimal("0.001")

    def __init__(
        self,
        *,
        price_limit_ratios: Mapping[str, float] | None = None,
        trading_phase_fetcher: Callable[
            [Sequence[str]], Mapping[str, TradingStatus | str]
        ]
        | None = None,
        akshare_module: Any | None = None,
        now: Callable[[], datetime] | None = None,
        eastmoney_single_quote_fetcher: Callable[..., Any] | None = None,
        tencent_quote_fetcher: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(
            akshare_module=akshare_module,
            now=now,
            eastmoney_single_quote_fetcher=eastmoney_single_quote_fetcher,
            tencent_quote_fetcher=tencent_quote_fetcher,
        )
        self._price_limit_ratios = dict(price_limit_ratios or {})
        self._trading_phase_fetcher = (
            trading_phase_fetcher or _fetch_official_etf_trading_statuses
        )

    def _spot_frame(self, akshare: Any) -> Any:
        return akshare.fund_etf_spot_em()

    def _optional_fields(self) -> tuple[tuple[str, str, bool, float], ...]:
        return (
            ("previous_close", AKSHARE_PREVIOUS_CLOSE_FIELD, True, 1.0),
            ("open_price", AKSHARE_ETF_OPEN_FIELD, True, 1.0),
            ("high_price", AKSHARE_ETF_HIGH_FIELD, True, 1.0),
            ("low_price", AKSHARE_ETF_LOW_FIELD, True, 1.0),
            ("volume", AKSHARE_VOLUME_FIELD, False, 100.0),
            ("amount", AKSHARE_AMOUNT_FIELD, False, 1.0),
        )

    def _data_time(self, row: Any) -> datetime:
        return _required_aware_datetime(row, AKSHARE_ETF_UPDATE_TIME_FIELD)

    def _trading_statuses(self, symbols: Sequence[str]) -> dict[str, TradingStatus]:
        raw_statuses = self._trading_phase_fetcher(symbols)
        if not isinstance(raw_statuses, Mapping):
            raise ValueError("official trading phase response must be a mapping")
        statuses: dict[str, TradingStatus] = {}
        requested = set(symbols)
        for symbol, raw_status in raw_statuses.items():
            if symbol not in requested:
                continue
            try:
                statuses[symbol] = TradingStatus(raw_status)
            except ValueError:
                statuses[symbol] = TradingStatus.UNKNOWN
        return statuses

    def _derive_limit_status(
        self,
        *,
        symbol: str,
        current_price: float,
        previous_close: float | None,
    ) -> tuple[LimitStatus, str | None]:
        ratio = self._price_limit_ratios.get(symbol)
        if ratio is None:
            return (
                LimitStatus.UNKNOWN,
                "price_limit_ratio unavailable; limit_status kept unknown",
            )
        if previous_close is None:
            return (
                LimitStatus.UNKNOWN,
                "previous close unavailable; limit_status kept unknown",
            )

        try:
            ratio_decimal = Decimal(str(ratio))
            current = Decimal(str(current_price))
            previous = Decimal(str(previous_close))
        except InvalidOperation:
            return LimitStatus.UNKNOWN, "invalid limit inputs; limit_status kept unknown"
        if (
            not ratio_decimal.is_finite()
            or not Decimal("0") < ratio_decimal <= Decimal("1")
        ):
            return LimitStatus.UNKNOWN, "invalid price_limit_ratio; limit_status kept unknown"
        if any(
            price != price.quantize(self.price_tick, rounding=ROUND_HALF_UP)
            for price in (current, previous)
        ):
            return LimitStatus.UNKNOWN, "invalid ETF price tick; limit_status kept unknown"

        upper = (previous * (Decimal("1") + ratio_decimal)).quantize(
            self.price_tick, rounding=ROUND_HALF_UP
        )
        lower = (previous * (Decimal("1") - ratio_decimal)).quantize(
            self.price_tick, rounding=ROUND_HALF_UP
        )
        if current == upper:
            return LimitStatus.UP, None
        if current == lower:
            return LimitStatus.DOWN, None
        if lower < current < upper:
            return LimitStatus.NONE, None
        return (
            LimitStatus.UNKNOWN,
            "current price outside calculated price limits; limit_status kept unknown",
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


def _required_aware_datetime(row: Any, field: str) -> datetime:
    value = row[field]
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a datetime") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _trading_status_from_sse_phase(value: Any) -> TradingStatus:
    phase = str(value).strip().upper()
    if not phase:
        return TradingStatus.UNKNOWN
    if phase[0] in SSE_PHASE_SUSPENDED_PREFIXES:
        return TradingStatus.SUSPENDED
    if phase[0] in SSE_PHASE_NORMAL_PREFIXES:
        return TradingStatus.NORMAL
    return TradingStatus.UNKNOWN


def _trading_status_from_szse_phase(value: Any) -> TradingStatus:
    phase = str(value).strip()
    if phase in SZSE_PHASE_SUSPENDED:
        return TradingStatus.SUSPENDED
    if phase in SZSE_PHASE_NORMAL:
        return TradingStatus.NORMAL
    return TradingStatus.UNKNOWN


def _fetch_official_etf_trading_statuses(
    symbols: Sequence[str],
) -> dict[str, TradingStatus]:
    requested = set(symbols)
    statuses: dict[str, TradingStatus] = {}
    errors: list[str] = []
    try:
        response = requests.get(
            SSE_ETF_PHASE_URL,
            params={"select": "code,tradephase", "begin": 0, "end": 2000},
            headers=OFFICIAL_PHASE_HEADERS,
            timeout=OFFICIAL_PHASE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("list") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("SSE trading phase response missing list")
        for row in rows:
            if not isinstance(row, list | tuple) or len(row) < 2:
                continue
            symbol = str(row[0]).strip()
            if symbol in requested:
                statuses[symbol] = _trading_status_from_sse_phase(row[1])
    except Exception as exc:
        errors.append(f"SSE: {safe_error_summary(exc)}")

    szse_headers = {**OFFICIAL_PHASE_HEADERS, "Referer": "https://www.szse.cn/"}
    for symbol in sorted(requested - statuses.keys()):
        try:
            response = requests.get(
                SZSE_ETF_PHASE_URL,
                params={"marketId": 1, "code": symbol},
                headers=szse_headers,
                timeout=OFFICIAL_PHASE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict) or str(data.get("code", "")).strip() != symbol:
                raise ValueError("SZSE trading phase response symbol mismatch")
            statuses[symbol] = _trading_status_from_szse_phase(
                data.get("tradingPhaseCode1")
            )
        except Exception as exc:
            errors.append(f"SZSE {symbol}: {safe_error_summary(exc)}")

    if not statuses and errors:
        raise RuntimeError("; ".join(errors))
    return statuses


def _eastmoney_current_price(row: Any, field: str, *, symbol: str) -> float:
    return _required_float(row, field, positive=True) / _eastmoney_price_divisor(symbol)


def _scaled_eastmoney_quote_float(row: Any, field: str, *, positive: bool) -> float:
    return _required_float(row, field, positive=positive) / 100


def _optional_eastmoney_timestamp(row: Any, field: str) -> datetime | None:
    value = row.get(field) if isinstance(row, dict) else row[field]
    if value in (None, "", "-"):
        return None
    return datetime.fromtimestamp(
        _required_float(row, field, positive=True),
        tz=ZoneInfo("Asia/Shanghai"),
    )


def _optional_tencent_timestamp(row: Any, field: str) -> datetime | None:
    value = row.get(field) if isinstance(row, dict) else row[field]
    if value in (None, "", "-"):
        return None
    return datetime.strptime(str(value).strip(), "%Y%m%d%H%M%S").replace(
        tzinfo=ZoneInfo("Asia/Shanghai")
    )


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
            "data_time": parts[30],
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
