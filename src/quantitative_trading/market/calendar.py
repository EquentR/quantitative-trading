from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import exchange_calendars


@dataclass(frozen=True)
class TradingSession:
    trade_date: date
    open_at: datetime
    break_start: datetime
    break_end: datetime
    close_at: datetime


@dataclass(frozen=True)
class MarketDateResolution:
    effective_trade_date: date
    history_cutoff_date: date
    warnings: tuple[str, ...] = ()


class TradingCalendar(Protocol):
    timezone: ZoneInfo

    def is_trading_day(self, value: date) -> bool: ...

    def next_trading_day(self, value: date) -> date: ...

    def previous_trading_day(self, value: date) -> date: ...

    def trading_days(self, start_date: date, end_date: date) -> list[date]: ...

    def sessions_ending(self, end_date: date, count: int) -> list[date]: ...

    def session(self, trade_date: date) -> TradingSession: ...

    def is_trading_minute(self, value: datetime) -> bool: ...

    def resolve_market_dates(
        self,
        value: datetime,
        *,
        session_ready: Callable[[date], bool],
    ) -> MarketDateResolution: ...


class XSHGTradingCalendar:
    def __init__(self) -> None:
        self._calendar = exchange_calendars.get_calendar("XSHG")
        self.timezone = ZoneInfo("Asia/Shanghai")

    def is_trading_day(self, value: date) -> bool:
        return bool(self._calendar.is_session(value.isoformat()))

    def next_trading_day(self, value: date) -> date:
        if self.is_trading_day(value):
            return self._calendar.next_session(value.isoformat()).date()
        return self._calendar.date_to_session(value.isoformat(), direction="next").date()

    def previous_trading_day(self, value: date) -> date:
        if self.is_trading_day(value):
            return self._calendar.previous_session(value.isoformat()).date()
        return self._calendar.date_to_session(value.isoformat(), direction="previous").date()

    def trading_days(self, start_date: date, end_date: date) -> list[date]:
        if start_date > end_date:
            return []
        return [
            timestamp.date()
            for timestamp in self._calendar.sessions_in_range(
                start_date.isoformat(), end_date.isoformat()
            )
        ]

    def sessions_ending(self, end_date: date, count: int) -> list[date]:
        if count <= 0:
            return []
        end_session = (
            end_date
            if self.is_trading_day(end_date)
            else self.previous_trading_day(end_date)
        )
        sessions = self._calendar.sessions_window(end_session.isoformat(), -count)
        return [timestamp.date() for timestamp in sessions]

    def session(self, trade_date: date) -> TradingSession:
        if not self.is_trading_day(trade_date):
            raise ValueError(f"not an XSHG trading day: {trade_date.isoformat()}")
        label = trade_date.isoformat()
        return TradingSession(
            trade_date=trade_date,
            open_at=self._calendar.session_open(label).to_pydatetime().astimezone(self.timezone),
            break_start=self._calendar.session_break_start(label)
            .to_pydatetime()
            .astimezone(self.timezone),
            break_end=self._calendar.session_break_end(label)
            .to_pydatetime()
            .astimezone(self.timezone),
            close_at=self._calendar.session_close(label).to_pydatetime().astimezone(self.timezone),
        )

    def is_trading_minute(self, value: datetime) -> bool:
        if value.tzinfo is None or value.utcoffset() is None:
            return False
        local = value.astimezone(self.timezone)
        if not self.is_trading_day(local.date()):
            return False
        session = self.session(local.date())
        return (session.open_at <= local <= session.break_start) or (
            session.break_end <= local <= session.close_at
        )

    def resolve_market_dates(
        self,
        value: datetime,
        *,
        session_ready: Callable[[date], bool],
    ) -> MarketDateResolution:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market date resolution time must be timezone-aware")
        local = value.astimezone(self.timezone)
        local_date = local.date()
        if self.is_trading_day(local_date):
            session = self.session(local_date)
            if local < session.open_at:
                effective_trade_date = self.previous_trading_day(local_date)
                cutoff_candidate = effective_trade_date
            else:
                effective_trade_date = local_date
                cutoff_candidate = (
                    local_date
                    if local > session.close_at
                    else self.previous_trading_day(local_date)
                )
        else:
            effective_trade_date = self.previous_trading_day(local_date)
            cutoff_candidate = effective_trade_date

        history_cutoff_date = cutoff_candidate
        for _ in range(5000):
            if session_ready(history_cutoff_date):
                break
            history_cutoff_date = self.previous_trading_day(history_cutoff_date)
        else:
            raise ValueError("no ready history session found")

        warnings = (
            ()
            if history_cutoff_date == cutoff_candidate
            else (
                f"{cutoff_candidate.isoformat()} daily bar is not ready; "
                f"using {history_cutoff_date.isoformat()}",
            )
        )
        return MarketDateResolution(
            effective_trade_date=effective_trade_date,
            history_cutoff_date=history_cutoff_date,
            warnings=warnings,
        )

    def expected_minutes_through(self, value: datetime) -> int:
        local = value.astimezone(self.timezone)
        session = self.session(local.date())
        if local <= session.open_at:
            return 0
        morning_minutes = int(
            (min(local, session.break_start) - session.open_at).total_seconds() // 60
        )
        if local <= session.break_end:
            return max(0, morning_minutes)
        afternoon_minutes = int(
            (min(local, session.close_at) - session.break_end).total_seconds() // 60
        )
        return max(0, morning_minutes) + max(0, afternoon_minutes)
