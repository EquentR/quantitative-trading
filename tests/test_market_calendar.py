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


def test_market_date_resolution_never_uses_unfinished_daily_bar() -> None:
    calendar = XSHGTradingCalendar()
    previous = date(2026, 7, 10)
    current = date(2026, 7, 13)

    def resolve(value: datetime, *, current_ready: bool = False):
        return calendar.resolve_market_dates(
            value,
            session_ready=lambda trade_date: (
                current_ready if trade_date == current else True
            ),
        )

    intraday = resolve(datetime(2026, 7, 13, 10, 0, tzinfo=SHANGHAI))
    lunch = resolve(datetime(2026, 7, 13, 12, 0, tzinfo=SHANGHAI))
    before_close = resolve(
        datetime(2026, 7, 13, 14, 59, 59, tzinfo=SHANGHAI),
        current_ready=True,
    )
    exact_close = resolve(
        datetime(2026, 7, 13, 15, 0, tzinfo=SHANGHAI),
        current_ready=True,
    )
    after_close_pending = resolve(
        datetime(2026, 7, 13, 15, 20, tzinfo=SHANGHAI)
    )
    after_close_ready = resolve(
        datetime(2026, 7, 13, 15, 20, tzinfo=SHANGHAI),
        current_ready=True,
    )
    before_open = resolve(datetime(2026, 7, 13, 8, 0, tzinfo=SHANGHAI))
    weekend = calendar.resolve_market_dates(
        datetime(2026, 7, 18, 10, 0, tzinfo=SHANGHAI),
        session_ready=lambda _trade_date: True,
    )

    for resolution in (
        intraday,
        lunch,
        before_close,
        exact_close,
        after_close_pending,
        after_close_ready,
    ):
        assert resolution.effective_trade_date == current
    assert intraday.history_cutoff_date == previous
    assert lunch.history_cutoff_date == previous
    assert before_close.history_cutoff_date == previous
    assert exact_close.history_cutoff_date == previous
    assert after_close_pending.history_cutoff_date == previous
    assert after_close_pending.warnings == (
        "2026-07-13 daily bar is not ready; using 2026-07-10",
    )
    assert after_close_ready.history_cutoff_date == current
    assert after_close_ready.warnings == ()
    assert before_open.effective_trade_date == previous
    assert before_open.history_cutoff_date == previous
    assert weekend.effective_trade_date == date(2026, 7, 17)
    assert weekend.history_cutoff_date == date(2026, 7, 17)

    delayed_weekend = calendar.resolve_market_dates(
        datetime(2026, 7, 18, 10, 0, tzinfo=SHANGHAI),
        session_ready=lambda trade_date: trade_date <= date(2026, 7, 16),
    )
    assert delayed_weekend.effective_trade_date == date(2026, 7, 17)
    assert delayed_weekend.history_cutoff_date == date(2026, 7, 16)
    assert delayed_weekend.warnings == (
        "2026-07-17 daily bar is not ready; using 2026-07-16",
    )


def test_market_date_resolution_requires_timezone_aware_clock() -> None:
    calendar = XSHGTradingCalendar()

    try:
        calendar.resolve_market_dates(
            datetime(2026, 7, 13, 10, 0),
            session_ready=lambda _trade_date: True,
        )
    except ValueError as exc:
        assert str(exc) == "market date resolution time must be timezone-aware"
    else:
        raise AssertionError("expected timezone-aware validation")
