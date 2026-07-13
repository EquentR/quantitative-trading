import sqlite3
from datetime import UTC, date, datetime

import pytest

from quantitative_trading.market.backfill import HeavyDataBackfillService
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import DailyBar, DailyMoneyFlow, MinuteBar
from quantitative_trading.market.models import (
    IntradayStrengthSnapshot,
    StrengthConfidence,
    StrengthLabel,
)
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    HistorySnapshotRepository,
    MinuteBarRepository,
    MoneyFlowRepository,
    MoneyFlowSnapshotRepository,
    IntradayStrengthSnapshotRepository,
)
from quantitative_trading.market.retention import MinuteBarRetentionService
from quantitative_trading.market.schema import MARKET_DECISION_SCHEMA_SQL


FETCHED_AT = datetime(2026, 7, 13, 7, 1, tzinfo=UTC)


@pytest.fixture
def connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(MARKET_DECISION_SCHEMA_SQL)
    yield connection
    connection.close()


class RecordingDailyProvider:
    def __init__(self, calendar: XSHGTradingCalendar) -> None:
        self.calendar = calendar
        self.calls = []

    def get_daily_bars(self, symbol, start_date, end_date, adjustment):
        self.calls.append((symbol, start_date, end_date, adjustment))
        return [
            DailyBar(
                symbol=symbol,
                trade_date=day,
                open=10,
                high=11,
                low=9,
                close=10,
                volume=100,
                amount=1_000,
                source="fake",
                fetched_at=FETCHED_AT,
            )
            for day in self.calendar.trading_days(start_date, end_date)
        ]


class RecordingFlowProvider:
    def __init__(self, calendar: XSHGTradingCalendar) -> None:
        self.calendar = calendar
        self.calls = []

    def get_daily_money_flow(self, symbol, start_date, end_date):
        self.calls.append((symbol, start_date, end_date))
        return [
            DailyMoneyFlow(
                symbol=symbol,
                trade_date=day,
                main_net_amount=1,
                main_net_pct=1,
                super_large_net_amount=1,
                super_large_net_pct=1,
                large_net_amount=1,
                large_net_pct=1,
                medium_net_amount=-1,
                medium_net_pct=-1,
                small_net_amount=-1,
                small_net_pct=-1,
                source="fake",
                fetched_at=FETCHED_AT,
            )
            for day in self.calendar.trading_days(start_date, end_date)
        ]


def make_service(connection):
    calendar = XSHGTradingCalendar()
    daily_provider = RecordingDailyProvider(calendar)
    flow_provider = RecordingFlowProvider(calendar)
    return (
        HeavyDataBackfillService(
            calendar=calendar,
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            daily_repository=DailyBarRepository(connection),
            money_flow_repository=MoneyFlowRepository(connection),
            history_snapshot_repository=HistorySnapshotRepository(connection),
            money_flow_snapshot_repository=MoneyFlowSnapshotRepository(connection),
            now=lambda: FETCHED_AT,
        ),
        calendar,
        daily_provider,
        flow_provider,
    )


def test_backfill_uses_250_and_60_trading_day_baselines_then_five_day_correction(
    connection,
) -> None:
    service, calendar, daily_provider, flow_provider = make_service(connection)
    as_of = date(2026, 7, 13)

    daily = service.backfill_daily("run-1", "600000", as_of)
    flow = service.backfill_money_flow("run-1", "600000", as_of)

    assert len(DailyBarRepository(connection).current("600000")) == 250
    assert len(MoneyFlowRepository(connection).current("600000")) == 60
    assert daily.row_count == 250
    assert flow.row_count == 60

    service.backfill_daily("run-2", "600000", as_of)
    service.backfill_money_flow("run-2", "600000", as_of)
    last_five = calendar.sessions_ending(as_of, 5)
    assert daily_provider.calls[-1][1:] == (last_five[0], as_of, "forward")
    assert flow_provider.calls[-1][1:] == (last_five[0], as_of)


def test_minute_cleanup_retains_exactly_latest_twenty_xshg_trading_days(connection) -> None:
    calendar = XSHGTradingCalendar()
    days = calendar.sessions_ending(date(2026, 7, 13), 22)
    repository = MinuteBarRepository(connection)
    for day in days:
        repository.upsert_many(
            [
                MinuteBar(
                    symbol="600000",
                    trade_date=day,
                    minute=datetime(day.year, day.month, day.day, 10, 0, tzinfo=calendar.timezone),
                    open=10,
                    high=10,
                    low=10,
                    close=10,
                    volume=100,
                    amount=1_000,
                    source="fake",
                    fetched_at=FETCHED_AT,
                )
            ]
        )

    deleted = MinuteBarRetentionService(repository, calendar).cleanup(date(2026, 7, 13))

    assert deleted == 2
    assert repository.trade_dates("600000") == days[-20:]


def test_backfill_rolls_back_fact_rows_when_snapshot_save_fails(connection) -> None:
    service, _calendar, _daily_provider, _flow_provider = make_service(connection)

    def fail_save(*args, **kwargs):
        raise sqlite3.IntegrityError("forced snapshot failure")

    service.history_snapshot_repository.save = fail_save

    with pytest.raises(sqlite3.IntegrityError, match="forced snapshot failure"):
        service.backfill_daily("run-fail", "600000", date(2026, 7, 13))

    assert connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM history_snapshots").fetchone()[0] == 0


def test_minute_cleanup_does_not_delete_derived_strength_snapshots(connection) -> None:
    repository = IntradayStrengthSnapshotRepository(connection)
    snapshot = IntradayStrengthSnapshot(
        run_id="run-1",
        symbol="600000",
        trade_date=date(2026, 5, 4),
        label=StrengthLabel.NEUTRAL,
        confidence=StrengthConfidence.LOW,
        degraded=True,
        degradation_reasons=["fixture"],
        components=[],
        thresholds={},
        rule_version="intraday-strength-v1",
        data_coverage=0,
        source="fake",
        data_time=datetime(2026, 5, 4, 10, 0, tzinfo=XSHGTradingCalendar().timezone),
        fetched_at=FETCHED_AT,
    )
    snapshot_id = repository.save(snapshot)

    MinuteBarRetentionService(
        MinuteBarRepository(connection), XSHGTradingCalendar()
    ).cleanup(date(2026, 7, 13))

    assert repository.get(snapshot_id) == snapshot
