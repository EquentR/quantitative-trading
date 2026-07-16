from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from math import isfinite
from typing import Any, Protocol

from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import DailyBar, DailyMoneyFlow, MinuteBar


class DailyBarProvider(Protocol):
    def get_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjustment: str,
    ) -> Sequence[DailyBar]: ...


class MoneyFlowProvider(Protocol):
    def get_daily_money_flow(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> Sequence[DailyMoneyFlow]: ...


class IntradayProvider(Protocol):
    def get_minute_bars(
        self,
        symbol: str,
        trade_date: date,
        interval: str,
    ) -> Sequence[MinuteBar]: ...


class MarketProviderError(RuntimeError):
    """External provider request failed before normalized mapping completed."""


def _require_symbol(symbol: str) -> None:
    if len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit():
        raise ValueError("symbol must contain six ASCII digits")


def _finite_float(row: Any, field: str, *, nonnegative: bool = False) -> float:
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not isfinite(value):
        raise ValueError(f"{field} must be finite")
    if nonnegative and value < 0:
        raise ValueError(f"{field} must be nonnegative")
    return value


def _date_value(row: Any, field: str) -> date:
    try:
        value = row[field]
    except KeyError as exc:
        raise ValueError(f"{field} is required") from exc
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


class _AkShareAdapter:
    def __init__(
        self,
        *,
        calendar: XSHGTradingCalendar | None = None,
        akshare_module: Any | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.calendar = calendar or XSHGTradingCalendar()
        self._akshare = akshare_module
        self._now = now or (lambda: datetime.now(UTC))

    def _module(self) -> Any:
        if self._akshare is not None:
            return self._akshare
        import akshare  # type: ignore[import-not-found]

        return akshare

    def _fetched_at(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("adapter fetched_at must be timezone-aware")
        return value


class AkShareDailyBarProvider(_AkShareAdapter):
    source = "akshare"

    def _history_frame(self, **kwargs):
        return self._module().stock_zh_a_hist(**kwargs)

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        adjustment: str,
    ) -> list[DailyBar]:
        _require_symbol(symbol)
        if adjustment != "forward":
            raise ValueError("daily bars require forward adjustment")
        if start_date > end_date:
            raise ValueError("start_date must not exceed end_date")
        fetched_at = self._fetched_at()
        try:
            frame = self._history_frame(
                symbol=symbol,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception as exc:
            raise MarketProviderError("daily market provider request failed") from exc
        bars: list[DailyBar] = []
        for _, row in frame.iterrows():
            trade_date = _date_value(row, "日期")
            if not (start_date <= trade_date <= end_date):
                continue
            if not self.calendar.is_trading_day(trade_date):
                raise ValueError("daily bar date is not an XSHG trading day")
            bars.append(
                DailyBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=_finite_float(row, "开盘"),
                    high=_finite_float(row, "最高"),
                    low=_finite_float(row, "最低"),
                    close=_finite_float(row, "收盘"),
                    volume=_finite_float(row, "成交量", nonnegative=True) * 100,
                    amount=_finite_float(row, "成交额", nonnegative=True),
                    source=self.source,
                    fetched_at=fetched_at,
                )
            )
        return sorted(bars, key=lambda bar: bar.trade_date)


class AkShareEtfDailyBarProvider(AkShareDailyBarProvider):
    source = "akshare_etf"

    def _history_frame(self, **kwargs):
        return self._module().fund_etf_hist_em(**kwargs)


class AkShareMoneyFlowProvider(_AkShareAdapter):
    _FIELDS = {
        "main_net_amount": "主力净流入-净额",
        "main_net_pct": "主力净流入-净占比",
        "super_large_net_amount": "超大单净流入-净额",
        "super_large_net_pct": "超大单净流入-净占比",
        "large_net_amount": "大单净流入-净额",
        "large_net_pct": "大单净流入-净占比",
        "medium_net_amount": "中单净流入-净额",
        "medium_net_pct": "中单净流入-净占比",
        "small_net_amount": "小单净流入-净额",
        "small_net_pct": "小单净流入-净占比",
    }

    def get_daily_money_flow(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[DailyMoneyFlow]:
        _require_symbol(symbol)
        if start_date > end_date:
            raise ValueError("start_date must not exceed end_date")
        fetched_at = self._fetched_at()
        try:
            frame = self._module().stock_individual_fund_flow(
                stock=symbol,
                market=_akshare_market(symbol),
            )
        except Exception as exc:
            raise MarketProviderError("money-flow provider request failed") from exc
        flows: list[DailyMoneyFlow] = []
        for _, row in frame.iterrows():
            trade_date = _date_value(row, "日期")
            if not (start_date <= trade_date <= end_date):
                continue
            if not self.calendar.is_trading_day(trade_date):
                raise ValueError("money-flow date is not an XSHG trading day")
            values = {name: _finite_float(row, field) for name, field in self._FIELDS.items()}
            flows.append(
                DailyMoneyFlow(
                    symbol=symbol,
                    trade_date=trade_date,
                    source="akshare",
                    fetched_at=fetched_at,
                    **values,
                )
            )
        return sorted(flows, key=lambda flow: flow.trade_date)


class AkShareIntradayProvider(_AkShareAdapter):
    source = "akshare"

    def _minute_frame(self, **kwargs):
        return self._module().stock_zh_a_hist_min_em(**kwargs)

    def _sina_minute_frame(self, symbol: str):
        return self._module().stock_zh_a_minute(
            symbol=f"{_akshare_market(symbol)}{symbol}",
            period="1",
            adjust="",
        )

    def get_minute_bars(
        self,
        symbol: str,
        trade_date: date,
        interval: str,
    ) -> list[MinuteBar]:
        _require_symbol(symbol)
        if interval != "1m":
            raise ValueError("intraday provider only supports 1m interval")
        if not self.calendar.is_trading_day(trade_date):
            raise ValueError("trade_date is not an XSHG trading day")
        fetched_at = self._fetched_at()
        try:
            frame = self._minute_frame(
                symbol=symbol,
                start_date=f"{trade_date.isoformat()} 09:30:00",
                end_date=f"{trade_date.isoformat()} 15:00:00",
                period="1",
                adjust="",
            )
            fields = {
                "minute": "时间",
                "open": "开盘",
                "high": "最高",
                "low": "最低",
                "close": "收盘",
                "volume": "成交量",
                "amount": "成交额",
            }
            volume_multiplier = 100.0
            source = self.source
        except Exception:
            try:
                frame = self._sina_minute_frame(symbol)
            except Exception as exc:
                raise MarketProviderError(
                    "intraday market provider request failed"
                ) from exc
            fields = {
                "minute": "day",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
                "amount": "amount",
            }
            volume_multiplier = 1.0
            source = "akshare_sina_minute"
        bars: list[MinuteBar] = []
        for _, row in frame.iterrows():
            raw_minute = row[fields["minute"]]
            if isinstance(raw_minute, datetime):
                minute = raw_minute
            else:
                minute = datetime.fromisoformat(str(raw_minute).strip())
            if minute.tzinfo is None:
                minute = minute.replace(tzinfo=self.calendar.timezone)
            else:
                minute = minute.astimezone(self.calendar.timezone)
            if minute.date() != trade_date or not self.calendar.is_trading_minute(minute):
                continue
            bars.append(
                MinuteBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    minute=minute,
                    open=_finite_float(row, fields["open"]),
                    high=_finite_float(row, fields["high"]),
                    low=_finite_float(row, fields["low"]),
                    close=_finite_float(row, fields["close"]),
                    volume=_finite_float(
                        row, fields["volume"], nonnegative=True
                    )
                    * volume_multiplier,
                    amount=_finite_float(row, fields["amount"], nonnegative=True),
                    source=source,
                    fetched_at=fetched_at,
                )
            )
        return sorted(bars, key=lambda bar: bar.minute)


class AkShareEtfIntradayProvider(AkShareIntradayProvider):
    source = "akshare_etf"

    def _minute_frame(self, **kwargs):
        return self._module().fund_etf_hist_min_em(**kwargs)


def _akshare_market(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return "sh"
    if symbol.startswith(("4", "8")):
        return "bj"
    return "sz"
