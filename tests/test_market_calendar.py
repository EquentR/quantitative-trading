from datetime import date, datetime
from zoneinfo import ZoneInfo

from quantitative_trading.market.calendar import XSHGTradingCalendar


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_xshg_calendar_skips_weekends_and_exchange_holidays() -> None:
    calendar = XSHGTradingCalendar()

    assert calendar.is_trading_day(date(2026, 10, 1)) is False
    assert calendar.next_trading_day(date(2026, 9, 30)) == date(2026, 10, 8)
    assert calendar.previous_trading_day(date(2026, 10, 8)) == date(2026, 9, 30)


def test_xshg_calendar_exposes_shanghai_session_and_lunch_break() -> None:
    calendar = XSHGTradingCalendar()
    session = calendar.session(date(2026, 7, 13))

    assert session.open_at == datetime(2026, 7, 13, 9, 30, tzinfo=SHANGHAI)
    assert session.break_start == datetime(2026, 7, 13, 11, 30, tzinfo=SHANGHAI)
    assert session.break_end == datetime(2026, 7, 13, 13, 0, tzinfo=SHANGHAI)
    assert session.close_at == datetime(2026, 7, 13, 15, 0, tzinfo=SHANGHAI)
    assert calendar.is_trading_minute(datetime(2026, 7, 13, 10, 0, tzinfo=SHANGHAI))
    assert not calendar.is_trading_minute(
        datetime(2026, 7, 13, 12, 0, tzinfo=SHANGHAI)
    )
    assert calendar.expected_minutes_through(
        datetime(2026, 7, 13, 11, 30, tzinfo=SHANGHAI)
    ) == 120
    assert calendar.expected_minutes_through(
        datetime(2026, 7, 13, 13, 0, tzinfo=SHANGHAI)
    ) == 120
    assert calendar.expected_minutes_through(
        datetime(2026, 7, 13, 15, 0, tzinfo=SHANGHAI)
    ) == 240
