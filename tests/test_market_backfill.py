import sqlite3
from datetime import UTC, date, datetime

import pytest

import quantitative_trading.market.models as market_models
from quantitative_trading.market.adapters import DailyBarFetchResult
from quantitative_trading.market.backfill import HeavyDataBackfillService
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    DailyBar,
    DailyBarCoverageEvidence,
    DailyMoneyFlow,
    ListingDateEvidence,
    MinuteBar,
)
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


class ShortDailyProvider(RecordingDailyProvider):
    def __init__(
        self,
        calendar: XSHGTradingCalendar,
        *,
        window_rows: int,
        omitted_index: int | None = None,
    ) -> None:
        super().__init__(calendar)
        self.window_rows = window_rows
        self.omitted_index = omitted_index

    def get_daily_bars(self, symbol, start_date, end_date, adjustment):
        bars = super().get_daily_bars(symbol, start_date, end_date, adjustment)[
            -self.window_rows :
        ]
        if self.omitted_index is not None and self.omitted_index < len(bars):
            bars.pop(self.omitted_index)
        return bars


class CoverageShortDailyProvider(ShortDailyProvider):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.coverage_calls = []

    def get_daily_bars_with_coverage(
        self,
        symbol,
        start_date,
        end_date,
        adjustment,
    ) -> DailyBarFetchResult:
        self.coverage_calls.append((symbol, start_date, end_date, adjustment))
        bars = tuple(self.get_daily_bars(symbol, start_date, end_date, adjustment))
        return DailyBarFetchResult(
            bars=bars,
            coverage_evidence=DailyBarCoverageEvidence(
                requested_start=start_date,
                requested_end=end_date,
                observed_start=start_date,
                observed_end=end_date,
                earliest_available_date=(
                    None if not bars else bars[0].trade_date
                ),
                complete_request_window=True,
                source="fake_daily_full_window",
            ),
        )


def make_service(connection, *, daily_provider=None):
    calendar = XSHGTradingCalendar()
    daily_provider = daily_provider or RecordingDailyProvider(calendar)
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


def test_minimum_history_rows_is_a_shared_contract() -> None:
    assert market_models.MIN_HISTORY_ROWS == 20


def test_provider_window_evidence_verifies_short_history_with_session_gap(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    as_of = date(2026, 7, 13)
    coverage_provider = CoverageShortDailyProvider(
        calendar,
        window_rows=21,
        omitted_index=10,
    )
    coverage_service, _, _, _ = make_service(
        connection,
        daily_provider=coverage_provider,
    )

    covered = coverage_service.backfill_daily("run-covered", "600000", as_of)

    assert coverage_provider.coverage_calls == [
        (
            "600000",
            calendar.sessions_ending(as_of, 250)[0],
            as_of,
            "forward",
        )
    ]
    assert covered.snapshot.completeness == "verified_provider_window"
    assert covered.snapshot.coverage_evidence is not None
    assert covered.snapshot.coverage_evidence.complete_request_window is True
    assert covered.snapshot.row_count == 20
    assert covered.snapshot.is_usable(as_of=as_of, expected_rows=250)


def test_provider_window_evidence_allows_no_bar_on_cutoff_session(connection) -> None:
    calendar = XSHGTradingCalendar()
    as_of = date(2026, 7, 13)
    provider = CoverageShortDailyProvider(
        calendar,
        window_rows=21,
        omitted_index=20,
    )
    service, _, _, _ = make_service(connection, daily_provider=provider)

    result = service.backfill_daily("run-cutoff-suspension", "600000", as_of)

    assert result.snapshot.row_count == 20
    assert result.snapshot.data_end == calendar.previous_trading_day(as_of)
    assert result.snapshot.completeness == "verified_provider_window"
    assert result.snapshot.is_usable(as_of=as_of, expected_rows=250)


def test_legacy_short_history_remains_unverifiable(connection) -> None:
    calendar = XSHGTradingCalendar()
    as_of = date(2026, 7, 13)
    legacy_provider = ShortDailyProvider(calendar, window_rows=20)
    legacy_service, _, _, _ = make_service(
        connection,
        daily_provider=legacy_provider,
    )
    legacy = legacy_service.backfill_daily("run-legacy", "000001", as_of)

    assert legacy.snapshot.completeness == "unverifiable"
    assert not legacy.snapshot.is_usable(as_of=as_of, expected_rows=250)


def test_verified_history_below_minimum_rows_is_not_usable(connection) -> None:
    calendar = XSHGTradingCalendar()
    as_of = date(2026, 7, 13)
    insufficient_provider = CoverageShortDailyProvider(calendar, window_rows=19)
    insufficient_service, _, _, _ = make_service(
        connection,
        daily_provider=insufficient_provider,
    )
    insufficient = insufficient_service.backfill_daily(
        "run-insufficient",
        "600001",
        as_of,
    )

    assert insufficient.snapshot.completeness == "verified_provider_window"
    assert insufficient.snapshot.row_count == 19
    assert not insufficient.snapshot.is_usable(as_of=as_of, expected_rows=250)


def test_unverifiable_local_short_cache_requests_full_window_for_provider_evidence(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    as_of = date(2026, 7, 13)
    legacy_service, _, _, _ = make_service(
        connection,
        daily_provider=ShortDailyProvider(
            calendar,
            window_rows=21,
            omitted_index=10,
        ),
    )
    legacy = legacy_service.backfill_daily("run-legacy-cache", "600000", as_of)
    assert legacy.snapshot.completeness == "unverifiable"

    coverage_provider = CoverageShortDailyProvider(
        calendar,
        window_rows=21,
        omitted_index=10,
    )
    coverage_service, _, _, _ = make_service(
        connection,
        daily_provider=coverage_provider,
    )
    verified = coverage_service.backfill_daily(
        "run-verify-cache",
        "600000",
        as_of,
    )

    assert coverage_provider.coverage_calls == [
        (
            "600000",
            calendar.sessions_ending(as_of, 250)[0],
            as_of,
            "forward",
        )
    ]
    assert verified.snapshot.completeness == "verified_provider_window"
    assert verified.snapshot.row_count == 20
    assert verified.snapshot.is_usable(as_of=as_of, expected_rows=250)


def test_short_history_accepts_contiguous_verified_listing_window(connection) -> None:
    calendar = XSHGTradingCalendar()
    as_of = date(2026, 7, 13)
    listing_date = calendar.sessions_ending(as_of, 20)[0]
    provider = ShortDailyProvider(calendar, window_rows=20)
    service, _, _, _ = make_service(connection, daily_provider=provider)

    result = service.backfill_daily(
        "run-listing",
        "603459",
        as_of,
        listing_evidence=ListingDateEvidence(
            listing_date=listing_date,
            source="exchange_directory",
        ),
    )

    assert result.snapshot.completeness == "verified_listing_date"
    assert result.snapshot.listing_evidence == ListingDateEvidence(
        listing_date=listing_date,
        source="exchange_directory",
    )
    assert result.snapshot.is_usable(as_of=as_of, expected_rows=250)


def test_listing_evidence_does_not_hide_an_unexplained_history_gap(connection) -> None:
    calendar = XSHGTradingCalendar()
    as_of = date(2026, 7, 13)
    listing_date = calendar.sessions_ending(as_of, 21)[0]
    provider = ShortDailyProvider(
        calendar,
        window_rows=21,
        omitted_index=10,
    )
    service, _, _, _ = make_service(connection, daily_provider=provider)

    result = service.backfill_daily(
        "run-listing-gap",
        "603459",
        as_of,
        listing_evidence=ListingDateEvidence(
            listing_date=listing_date,
            source="exchange_directory",
        ),
    )

    assert result.snapshot.row_count == 20
    assert result.snapshot.completeness == "unverifiable"
    assert not result.snapshot.is_usable(as_of=as_of, expected_rows=250)


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
    second_daily = service.backfill_daily("run-3", "600000", as_of)
    second_flow = service.backfill_money_flow("run-2", "600000", as_of)
    last_five = calendar.sessions_ending(as_of, 5)
    assert daily_provider.calls[-1][1:] == (last_five[0], as_of, "forward")
    assert flow_provider.calls[-1][1:] == (last_five[0], as_of)
    assert second_daily.rows_received == 5
    assert second_daily.rows_written == 0
    assert second_flow.rows_received == 5
    assert second_flow.rows_written == 0


def test_backfill_requests_old_gap_and_correction_as_separate_ranges(connection) -> None:
    service, calendar, daily_provider, _flow_provider = make_service(connection)
    as_of = date(2026, 7, 13)
    service.backfill_daily("run-1", "600000", as_of)
    old_gap = calendar.sessions_ending(as_of, 250)[20]
    connection.execute("DELETE FROM history_snapshot_members")
    connection.execute("DELETE FROM history_snapshots")
    connection.execute("DELETE FROM daily_bars WHERE trade_date = ?", (old_gap.isoformat(),))
    connection.commit()
    daily_provider.calls.clear()

    result = service.backfill_daily("run-2", "600000", as_of)

    last_five = calendar.sessions_ending(as_of, 5)
    assert daily_provider.calls == [
        ("600000", old_gap, old_gap, "forward"),
        ("600000", last_five[0], as_of, "forward"),
    ]
    assert result.provider_calls == 2
    assert result.rows_received == 6
    assert result.rows_written == 1


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
