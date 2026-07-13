from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
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


class TradingCalendar(Protocol):
    timezone: ZoneInfo

    def is_trading_day(self, value: date) -> bool: ...

    def next_trading_day(self, value: date) -> date: ...

    def previous_trading_day(self, value: date) -> date: ...

    def trading_days(self, start_date: date, end_date: date) -> list[date]: ...

    def sessions_ending(self, end_date: date, count: int) -> list[date]: ...

    def session(self, trade_date: date) -> TradingSession: ...

    def is_trading_minute(self, value: datetime) -> bool: ...


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

    def expected_minutes_through(self, value: datetime) -> int:
        local = value.astimezone(self.timezone)
        session = self.session(local.date())
        cursor = session.open_at
        count = 0
        while cursor <= min(local, session.close_at):
            if self.is_trading_minute(cursor):
                count += 1
            cursor += timedelta(minutes=1)
        return count
