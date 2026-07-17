import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

import quantitative_trading.market.models as market_models
import quantitative_trading.market.backfill as market_backfill

from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureResultStatus,
    CaptureRunAlreadyActiveError,
    CaptureRunStatus,
    DailyBar,
    DailyMoneyFlow,
    HistorySnapshot,
    IntradayStrengthSnapshot,
    MarketCaptureResult,
    MarketCaptureRun,
    MinuteBar,
    MoneyFlowSnapshot,
    StrengthConfidence,
    StrengthLabel,
)
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    HistorySnapshotRepository,
    IntradayStrengthSnapshotRepository,
    MarketCaptureResultRepository,
    MarketCaptureRunRepository,
    MinuteBarRepository,
    MoneyFlowRepository,
    MoneyFlowSnapshotRepository,
    content_digest,
)
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


def daily_bar(close: float, *, trade_date: date = date(2026, 7, 10)) -> DailyBar:
    return DailyBar(
        symbol="600000",
        trade_date=trade_date,
        open=10,
        high=max(11, close),
        low=9,
        close=close,
        volume=100_000,
        amount=1_000_000,
        source="akshare",
        fetched_at=FETCHED_AT,
    )


def money_flow(amount: float) -> DailyMoneyFlow:
    return DailyMoneyFlow(
        symbol="600000",
        trade_date=date(2026, 7, 10),
        main_net_amount=amount,
        main_net_pct=1,
        super_large_net_amount=2,
        super_large_net_pct=0.2,
        large_net_amount=3,
        large_net_pct=0.3,
        medium_net_amount=-2,
        medium_net_pct=-0.2,
        small_net_amount=-3,
        small_net_pct=-0.3,
        source="akshare",
        fetched_at=FETCHED_AT,
    )


def save_daily_window(
    connection: sqlite3.Connection,
    *,
    calendar: XSHGTradingCalendar,
    cutoff: date,
    count: int,
) -> tuple[list[date], list[int]]:
    days = calendar.sessions_ending(cutoff, count)
    repository = DailyBarRepository(connection)
    ids = [
        repository.save(
            daily_bar(10 + index * 0.001, trade_date=trade_day)
        )
        for index, trade_day in enumerate(days)
    ]
    return days, ids


def test_daily_and_money_flow_facts_append_corrections_and_select_latest(connection) -> None:
    daily = DailyBarRepository(connection)
    flows = MoneyFlowRepository(connection)

    first_daily_id = daily.save(daily_bar(10.5))
    assert daily.save(daily_bar(10.5)) == first_daily_id
    corrected_daily_id = daily.save(daily_bar(10.6))
    first_flow_id = flows.save(money_flow(100))
    corrected_flow_id = flows.save(money_flow(200))

    assert corrected_daily_id != first_daily_id
    assert daily.current("600000")[-1].id == corrected_daily_id
    assert daily.current("600000")[-1].bar.close == 10.6
    assert corrected_flow_id != first_flow_id
    assert flows.current("600000")[-1].flow.main_net_amount == 200


def test_daily_current_applies_cutoff_before_limit_and_selects_corrections(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    days, _ = save_daily_window(
        connection,
        calendar=calendar,
        cutoff=cutoff,
        count=251,
    )
    repository = DailyBarRepository(connection)
    corrected_id = repository.save(daily_bar(20, trade_date=cutoff))
    repository.save(
        daily_bar(21, trade_date=calendar.next_trading_day(cutoff))
    )

    current = repository.current("600000", limit=250, through=cutoff)

    assert [item.bar.trade_date for item in current] == days[-250:]
    assert current[-1].id == corrected_id
    assert all(item.bar.trade_date <= cutoff for item in current)


def test_local_history_materializer_freezes_latest_complete_fact_versions(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    days, original_ids = save_daily_window(
        connection,
        calendar=calendar,
        cutoff=cutoff,
        count=250,
    )
    daily = DailyBarRepository(connection)
    correction_index = 125
    corrected_id = daily.save(
        daily_bar(20, trade_date=days[correction_index])
    )
    daily.save(daily_bar(21, trade_date=calendar.next_trading_day(cutoff)))
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )
    frozen_at = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)

    result = materializer.materialize(
        run_id="intraday-local-history",
        symbol="600000",
        cutoff=cutoff,
        fetched_at=frozen_at,
    )
    persisted = snapshots.get(result.snapshot_id)
    members = snapshots.members(result.snapshot_id)
    expected_ids = [*original_ids]
    expected_ids[correction_index] = corrected_id

    assert result.snapshot == persisted
    assert result.snapshot.run_id == "intraday-local-history"
    assert result.snapshot.fetched_at == frozen_at
    assert result.snapshot.data_start == days[0]
    assert result.snapshot.data_end == cutoff
    assert result.snapshot.row_count == 250
    assert result.snapshot.status is CaptureResultStatus.COMPLETE
    assert result.snapshot.completeness == "unverifiable"
    assert result.snapshot.coverage_evidence is None
    assert result.snapshot.listing_evidence is None
    assert result.snapshot.warning == ""
    assert result.snapshot.is_usable(as_of=cutoff, expected_rows=250)
    assert [member.id for member in members] == expected_ids
    assert result.snapshot.content_digest == content_digest(
        [member.bar.content_hash for member in members]
    )


def test_local_history_materialization_is_immutable_across_later_corrections(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    days, _ = save_daily_window(
        connection,
        calendar=calendar,
        cutoff=cutoff,
        count=250,
    )
    daily = DailyBarRepository(connection)
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )
    first = materializer.materialize(
        run_id="intraday-local-history-v1",
        symbol="600000",
        cutoff=cutoff,
        fetched_at=FETCHED_AT,
    )
    first_members = snapshots.members(first.snapshot_id)
    corrected_id = daily.save(daily_bar(20, trade_date=days[-1]))

    second = materializer.materialize(
        run_id="intraday-local-history-v2",
        symbol="600000",
        cutoff=cutoff,
        fetched_at=datetime(2026, 7, 13, 7, 2, tzinfo=UTC),
    )

    assert snapshots.members(first.snapshot_id) == first_members
    assert snapshots.get(first.snapshot_id).content_digest == first.snapshot.content_digest
    assert snapshots.members(second.snapshot_id)[-1].id == corrected_id
    assert second.snapshot.content_digest != first.snapshot.content_digest


def test_local_history_materializer_does_not_fill_window_gap_with_older_fact(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    days = calendar.sessions_ending(cutoff, 251)
    desired = days[-250:]
    omitted = desired[100]
    daily = DailyBarRepository(connection)
    for index, trade_day in enumerate(
        [days[0], *(day for day in desired if day != omitted)]
    ):
        daily.save(daily_bar(10 + index * 0.001, trade_date=trade_day))
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )

    result = materializer.materialize(
        run_id="intraday-local-history-gap",
        symbol="600000",
        cutoff=cutoff,
        fetched_at=FETCHED_AT,
    )
    member_dates = [
        member.bar.trade_date for member in snapshots.members(result.snapshot_id)
    ]

    assert member_dates == [day for day in desired if day != omitted]
    assert result.snapshot.row_count == 249
    assert result.snapshot.status is CaptureResultStatus.DEGRADED
    assert result.snapshot.completeness == "unverifiable"
    assert result.snapshot.coverage_evidence is None
    assert result.snapshot.listing_evidence is None
    assert not result.snapshot.is_usable(as_of=cutoff, expected_rows=250)


def test_local_history_materializer_filters_off_session_fact_before_window_limit(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    desired, _ = save_daily_window(
        connection,
        calendar=calendar,
        cutoff=cutoff,
        count=250,
    )
    off_session = desired[0]
    while calendar.is_trading_day(off_session):
        off_session += timedelta(days=1)
    assert desired[0] < off_session < cutoff
    daily = DailyBarRepository(connection)
    daily.save(daily_bar(20, trade_date=off_session))
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )

    result = materializer.materialize(
        run_id="intraday-local-history-off-session",
        symbol="600000",
        cutoff=cutoff,
        fetched_at=FETCHED_AT,
    )
    member_dates = [
        member.bar.trade_date for member in snapshots.members(result.snapshot_id)
    ]

    assert member_dates == desired
    assert result.snapshot.row_count == 250
    assert result.snapshot.status is CaptureResultStatus.COMPLETE
    assert result.snapshot.is_usable(as_of=cutoff, expected_rows=250)


def test_local_history_materializer_verifies_contiguous_listing_window(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    days, _ = save_daily_window(
        connection,
        calendar=calendar,
        cutoff=cutoff,
        count=market_models.MIN_HISTORY_ROWS,
    )
    daily = DailyBarRepository(connection)
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )
    evidence = market_models.ListingDateEvidence(
        listing_date=days[0],
        source="distinctive-listing-directory",
    )

    result = materializer.materialize(
        run_id="intraday-local-listing-history",
        symbol="600000",
        cutoff=cutoff,
        fetched_at=FETCHED_AT,
        listing_evidence=evidence,
    )

    assert result.snapshot.row_count == market_models.MIN_HISTORY_ROWS
    assert result.snapshot.status is CaptureResultStatus.DEGRADED
    assert result.snapshot.completeness == "verified_listing_date"
    assert result.snapshot.listing_evidence == evidence
    assert result.snapshot.coverage_evidence is None
    assert result.snapshot.is_usable(as_of=cutoff, expected_rows=250)
    assert snapshots.get(result.snapshot_id) == result.snapshot


def test_local_history_materializer_rejects_listing_evidence_with_internal_gap(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    days = calendar.sessions_ending(cutoff, market_models.MIN_HISTORY_ROWS + 1)
    omitted = days[10]
    daily = DailyBarRepository(connection)
    for index, trade_day in enumerate(day for day in days if day != omitted):
        daily.save(daily_bar(10 + index * 0.001, trade_date=trade_day))
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )

    result = materializer.materialize(
        run_id="intraday-local-listing-gap",
        symbol="600000",
        cutoff=cutoff,
        fetched_at=FETCHED_AT,
        listing_evidence=market_models.ListingDateEvidence(
            listing_date=days[0],
            source="distinctive-listing-directory",
        ),
    )

    assert result.snapshot.row_count == market_models.MIN_HISTORY_ROWS
    assert result.snapshot.completeness == "unverifiable"
    assert result.snapshot.listing_evidence is None
    assert result.snapshot.coverage_evidence is None
    assert not result.snapshot.is_usable(as_of=cutoff, expected_rows=250)


def test_local_history_materializer_preserves_verified_listing_below_strategy_minimum(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    days, _ = save_daily_window(
        connection,
        calendar=calendar,
        cutoff=cutoff,
        count=market_models.MIN_HISTORY_ROWS - 1,
    )
    daily = DailyBarRepository(connection)
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )

    evidence = market_models.ListingDateEvidence(
        listing_date=days[0],
        source="distinctive-listing-directory",
    )
    result = materializer.materialize(
        run_id="intraday-local-listing-too-short",
        symbol="600000",
        cutoff=cutoff,
        fetched_at=FETCHED_AT,
        listing_evidence=evidence,
    )

    assert result.snapshot.row_count == market_models.MIN_HISTORY_ROWS - 1
    assert result.snapshot.completeness == "verified_listing_date"
    assert result.snapshot.listing_evidence == evidence
    assert not result.snapshot.is_usable(as_of=cutoff, expected_rows=250)


def test_local_history_materializer_does_not_attach_listing_evidence_to_empty_history(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    daily = DailyBarRepository(connection)
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )

    result = materializer.materialize(
        run_id="intraday-local-listing-empty",
        symbol="600000",
        cutoff=cutoff,
        fetched_at=FETCHED_AT,
        listing_evidence=market_models.ListingDateEvidence(
            listing_date=cutoff,
            source="distinctive-listing-directory",
        ),
    )

    assert result.snapshot.row_count == 0
    assert result.snapshot.completeness == "unverifiable"
    assert result.snapshot.listing_evidence is None
    assert not result.snapshot.is_usable(as_of=cutoff, expected_rows=250)


def test_local_history_materializer_persists_explicit_empty_snapshot(connection) -> None:
    calendar = XSHGTradingCalendar()
    daily = DailyBarRepository(connection)
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )

    result = materializer.materialize(
        run_id="intraday-local-history-empty",
        symbol="600000",
        cutoff=date(2026, 7, 13),
        fetched_at=FETCHED_AT,
    )

    assert snapshots.get(result.snapshot_id) == result.snapshot
    assert snapshots.members(result.snapshot_id) == []
    assert result.snapshot.row_count == 0
    assert result.snapshot.content_digest == content_digest([])
    assert result.snapshot.status is CaptureResultStatus.DEGRADED
    assert result.snapshot.completeness == "unverifiable"
    assert result.snapshot.coverage_evidence is None
    assert result.snapshot.listing_evidence is None
    assert not result.snapshot.is_usable(as_of=date(2026, 7, 13), expected_rows=250)


def test_local_history_materialization_rolls_back_snapshot_on_member_failure(
    connection,
) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    save_daily_window(
        connection,
        calendar=calendar,
        cutoff=cutoff,
        count=250,
    )
    daily = DailyBarRepository(connection)
    snapshots = HistorySnapshotRepository(connection)
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")
    materializer = materializer_type(
        calendar=calendar,
        daily_repository=daily,
        history_snapshot_repository=snapshots,
    )
    fact_count = connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    connection.execute(
        """
        CREATE TRIGGER reject_local_history_member
        BEFORE INSERT ON history_snapshot_members
        BEGIN
          SELECT RAISE(ABORT, 'synthetic member failure');
        END
        """
    )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic member failure"):
        materializer.materialize(
            run_id="intraday-local-history-failed",
            symbol="600000",
            cutoff=cutoff,
            fetched_at=FETCHED_AT,
        )

    assert connection.execute("SELECT COUNT(*) FROM history_snapshots").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM history_snapshot_members").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0] == fact_count


def test_local_history_materializer_rejects_repository_connection_mismatch(
    connection,
) -> None:
    other = sqlite3.connect(":memory:")
    other.row_factory = sqlite3.Row
    other.execute("PRAGMA foreign_keys = ON")
    other.executescript(MARKET_DECISION_SCHEMA_SQL)
    calendar = XSHGTradingCalendar()
    materializer_type = getattr(market_backfill, "LocalHistoryMaterializer")

    with pytest.raises(ValueError, match="same connection"):
        materializer_type(
            calendar=calendar,
            daily_repository=DailyBarRepository(connection),
            history_snapshot_repository=HistorySnapshotRepository(other),
        )
    other.close()


def test_dataset_snapshot_members_remain_bound_to_original_fact_versions(connection) -> None:
    daily = DailyBarRepository(connection)
    daily_id = daily.save(daily_bar(10.5))
    history_repository = HistorySnapshotRepository(connection)
    snapshot_id = history_repository.save(
        HistorySnapshot(
            run_id="run-1",
            symbol="600000",
            data_start=date(2026, 7, 10),
            data_end=date(2026, 7, 10),
            row_count=1,
            content_digest=content_digest([daily_bar(10.5).content_hash]),
            status=CaptureResultStatus.COMPLETE,
            fetched_at=FETCHED_AT,
        ),
        [daily_id],
    )

    daily.save(daily_bar(10.8))

    members = history_repository.members(snapshot_id)
    assert [member.bar.close for member in members] == [10.5]
    with pytest.raises(sqlite3.IntegrityError):
        history_repository.save(
            HistorySnapshot(
                run_id="run-2",
                symbol="000001",
                data_start=date(2026, 7, 10),
                data_end=date(2026, 7, 10),
                row_count=1,
                content_digest=content_digest([daily_bar(10.5).content_hash]),
                status=CaptureResultStatus.COMPLETE,
                fetched_at=FETCHED_AT,
            ),
            [daily_id],
        )


def test_history_snapshot_repository_round_trips_coverage_evidence(connection) -> None:
    daily = DailyBarRepository(connection)
    daily_id = daily.save(daily_bar(10.5))
    evidence = market_models.DailyBarCoverageEvidence(
        requested_start=date(2025, 7, 1),
        requested_end=date(2026, 7, 10),
        observed_start=date(2025, 7, 1),
        observed_end=date(2026, 7, 10),
        earliest_available_date=date(2026, 7, 10),
        complete_request_window=True,
        source="fake_daily_full_window",
    )
    snapshot = HistorySnapshot(
        run_id="run-evidence",
        symbol="600000",
        data_start=date(2026, 7, 10),
        data_end=date(2026, 7, 10),
        row_count=1,
        content_digest=content_digest([daily_bar(10.5).content_hash]),
        status=CaptureResultStatus.DEGRADED,
        completeness="verified_provider_window",
        coverage_evidence=evidence,
        fetched_at=FETCHED_AT,
    )
    repository = HistorySnapshotRepository(connection)

    snapshot_id = repository.save(snapshot, [daily_id])

    assert repository.get(snapshot_id) == snapshot


def save_provider_history_snapshot(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    cutoff: date,
    row_count: int = market_models.MIN_HISTORY_ROWS,
) -> tuple[int, HistorySnapshot, list[int]]:
    calendar = XSHGTradingCalendar()
    member_days = calendar.sessions_ending(cutoff, row_count + 1)[:row_count]
    daily = DailyBarRepository(connection)
    member_ids = [
        daily.save(daily_bar(10 + index * 0.001, trade_date=trade_day))
        for index, trade_day in enumerate(member_days)
    ]
    requested_start = calendar.sessions_ending(cutoff, 250)[0]
    evidence = market_models.DailyBarCoverageEvidence(
        requested_start=requested_start,
        requested_end=cutoff,
        observed_start=requested_start,
        observed_end=cutoff,
        earliest_available_date=member_days[0],
        complete_request_window=True,
        source="provider-full-window",
    )
    snapshot = HistorySnapshot(
        run_id=run_id,
        symbol="600000",
        data_start=member_days[0],
        data_end=member_days[-1],
        row_count=row_count,
        content_digest=content_digest(
            [daily.get(member_id).bar.content_hash for member_id in member_ids]
        ),
        status=CaptureResultStatus.DEGRADED,
        warning="verified provider window",
        fetched_at=FETCHED_AT,
        completeness="verified_provider_window",
        coverage_evidence=evidence,
    )
    snapshot_id = HistorySnapshotRepository(connection).save(snapshot, member_ids)
    return snapshot_id, snapshot, member_ids


def test_history_repository_reuses_provider_snapshot_across_suspension_and_correction(
    connection,
) -> None:
    cutoff = date(2026, 7, 13)
    repository = HistorySnapshotRepository(connection)
    snapshot_id, snapshot, member_ids = save_provider_history_snapshot(
        connection,
        run_id="provider-suspension",
        cutoff=cutoff,
    )
    assert snapshot.data_end < cutoff
    original_members = repository.members(snapshot_id)
    DailyBarRepository(connection).save(
        daily_bar(20, trade_date=original_members[5].bar.trade_date)
    )
    snapshot_count = connection.execute(
        "SELECT COUNT(*) FROM history_snapshots"
    ).fetchone()[0]

    stored = repository.latest_usable_for_symbol(
        "600000",
        as_of=cutoff,
        expected_rows=250,
    )

    assert stored is not None
    assert stored.snapshot_id == snapshot_id
    assert stored.snapshot == snapshot
    assert repository.members(stored.snapshot_id) == original_members
    assert [member.id for member in original_members] == member_ids
    assert stored.snapshot.content_digest == snapshot.content_digest
    assert stored.snapshot.coverage_evidence == snapshot.coverage_evidence
    assert connection.execute("SELECT COUNT(*) FROM history_snapshots").fetchone()[0] == (
        snapshot_count
    )


def test_history_repository_skips_newer_unusable_snapshots_stably(connection) -> None:
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    repository = HistorySnapshotRepository(connection)
    usable_id, usable, member_ids = save_provider_history_snapshot(
        connection,
        run_id="provider-usable",
        cutoff=cutoff,
    )
    repository.save(
        usable.model_copy(
            update={
                "run_id": "newer-unverifiable",
                "completeness": market_models.HistoryCompleteness.UNVERIFIABLE,
                "coverage_evidence": None,
            }
        ),
        member_ids,
    )
    prior_cutoff = calendar.previous_trading_day(cutoff)
    save_provider_history_snapshot(
        connection,
        run_id="newer-wrong-cutoff",
        cutoff=prior_cutoff,
    )
    save_provider_history_snapshot(
        connection,
        run_id="newer-below-minimum",
        cutoff=cutoff,
        row_count=market_models.MIN_HISTORY_ROWS - 1,
    )

    stored = repository.latest_usable_for_symbol(
        "600000",
        as_of=cutoff,
        expected_rows=250,
    )

    assert stored is not None
    assert stored.snapshot_id == usable_id
    assert stored.snapshot == usable
    assert (
        repository.latest_usable_for_symbol(
            "600000",
            as_of=calendar.next_trading_day(cutoff),
            expected_rows=250,
        )
        is None
    )
    assert (
        repository.latest_usable_for_symbol(
            "000001",
            as_of=cutoff,
            expected_rows=250,
        )
        is None
    )


def test_history_repository_exact_lookup_rejects_symbol_mismatch(connection) -> None:
    cutoff = date(2026, 7, 13)
    repository = HistorySnapshotRepository(connection)
    snapshot_id, _snapshot, _member_ids = save_provider_history_snapshot(
        connection,
        run_id="provider-exact-symbol",
        cutoff=cutoff,
    )

    assert (
        repository.usable_by_id_for_symbol(
            snapshot_id,
            "000001",
            as_of=cutoff,
            expected_rows=250,
        )
        is None
    )


def test_history_repository_exact_lookup_does_not_replace_unusable_snapshot(
    connection,
) -> None:
    cutoff = date(2026, 7, 13)
    repository = HistorySnapshotRepository(connection)
    usable_id, usable, member_ids = save_provider_history_snapshot(
        connection,
        run_id="provider-exact-usable",
        cutoff=cutoff,
    )
    unusable_id = repository.save(
        usable.model_copy(
            update={
                "run_id": "provider-exact-unusable",
                "completeness": market_models.HistoryCompleteness.UNVERIFIABLE,
                "coverage_evidence": None,
            }
        ),
        member_ids,
    )

    assert (
        repository.usable_by_id_for_symbol(
            unusable_id,
            "600000",
            as_of=cutoff,
            expected_rows=250,
        )
        is None
    )
    latest = repository.latest_usable_for_symbol(
        "600000",
        as_of=cutoff,
        expected_rows=250,
    )
    assert latest is not None
    assert latest.snapshot_id == usable_id


def test_history_repository_rejects_corrupt_usable_snapshot_members(connection) -> None:
    cutoff = date(2026, 7, 13)
    repository = HistorySnapshotRepository(connection)
    snapshot_id, _snapshot, _member_ids = save_provider_history_snapshot(
        connection,
        run_id="provider-corrupt",
        cutoff=cutoff,
    )
    connection.execute(
        "DELETE FROM history_snapshot_members WHERE snapshot_id=? AND sequence=5",
        (snapshot_id,),
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="history snapshot members"):
        repository.latest_usable_for_symbol(
            "600000",
            as_of=cutoff,
            expected_rows=250,
        )


def test_history_repository_rejects_corrupt_member_sequence(connection) -> None:
    cutoff = date(2026, 7, 13)
    repository = HistorySnapshotRepository(connection)
    snapshot_id, _snapshot, _member_ids = save_provider_history_snapshot(
        connection,
        run_id="provider-corrupt-sequence",
        cutoff=cutoff,
    )
    connection.execute(
        "UPDATE history_snapshot_members SET sequence=sequence+100 WHERE snapshot_id=?",
        (snapshot_id,),
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="history snapshot member sequence"):
        repository.latest_usable_for_symbol(
            "600000",
            as_of=cutoff,
            expected_rows=250,
        )


def test_dataset_snapshot_rejects_forged_digest_range_and_member_order(connection) -> None:
    calendar = XSHGTradingCalendar()
    days = calendar.sessions_ending(date(2026, 7, 13), 2)
    repository = DailyBarRepository(connection)
    member_ids = [
        repository.save(daily_bar(10.5).model_copy(update={"trade_date": day}))
        for day in days
    ]
    snapshots = HistorySnapshotRepository(connection)

    def snapshot(**overrides):
        payload = {
            "run_id": "run-invalid",
            "symbol": "600000",
            "data_start": days[0],
            "data_end": days[1],
            "row_count": 2,
            "content_digest": content_digest(
                [
                    repository.get(member_id).bar.content_hash
                    for member_id in member_ids
                ]
            ),
            "status": CaptureResultStatus.COMPLETE,
            "fetched_at": FETCHED_AT,
        }
        payload.update(overrides)
        return HistorySnapshot(**payload)

    with pytest.raises(sqlite3.IntegrityError, match="digest"):
        snapshots.save(snapshot(content_digest="f" * 64), member_ids)
    with pytest.raises(sqlite3.IntegrityError, match="range"):
        snapshots.save(snapshot(data_start=days[1]), member_ids)
    with pytest.raises(sqlite3.IntegrityError, match="order"):
        snapshots.save(snapshot(), list(reversed(member_ids)))


def test_money_flow_snapshot_members_and_minute_upsert_are_idempotent(connection) -> None:
    flows = MoneyFlowRepository(connection)
    flow_id = flows.save(money_flow(100))
    snapshot_id = MoneyFlowSnapshotRepository(connection).save(
        MoneyFlowSnapshot(
            run_id="run-1",
            symbol="600000",
            data_start=date(2026, 7, 10),
            data_end=date(2026, 7, 10),
            row_count=1,
            content_digest=content_digest([money_flow(100).content_hash]),
            status=CaptureResultStatus.COMPLETE,
            fetched_at=FETCHED_AT,
        ),
        [flow_id],
    )
    assert MoneyFlowSnapshotRepository(connection).members(snapshot_id)[0].id == flow_id

    minute = datetime.fromisoformat("2026-07-13T10:00:00+08:00")
    repository = MinuteBarRepository(connection)
    original = MinuteBar(
        symbol="600000",
        trade_date=date(2026, 7, 13),
        minute=minute,
        open=10,
        high=10.1,
        low=9.9,
        close=10,
        volume=100,
        amount=1_000,
        source="akshare",
        fetched_at=FETCHED_AT,
    )
    corrected = original.model_copy(update={"close": 10.1, "amount": 1_010})

    assert repository.upsert_many([original]) == 1
    assert repository.upsert_many([corrected]) == 1
    assert len(repository.for_trade_date("600000", date(2026, 7, 13))) == 1
    assert repository.for_trade_date("600000", date(2026, 7, 13))[0].close == 10.1


def test_capture_run_get_or_create_reuses_idempotency_key_and_update_persists_metrics(
    connection,
) -> None:
    repository = MarketCaptureRunRepository(connection)
    first = MarketCaptureRun(
        run_id="run-1",
        workflow_type="backfill",
        trade_date=date(2026, 7, 13),
        idempotency_key="backfill:2026-07-13:600000",
        status=CaptureRunStatus.RUNNING,
        started_at=FETCHED_AT,
        requested_symbols=1,
    )

    stored, created = repository.get_or_create(first)
    reused, created_again = repository.get_or_create(
        first.model_copy(update={"run_id": "run-duplicate"})
    )
    finished = stored.model_copy(
        update={
            "status": CaptureRunStatus.SUCCEEDED,
            "finished_at": datetime(2026, 7, 13, 7, 2, tzinfo=UTC),
            "processed_symbols": 1,
            "provider_calls": 2,
            "provider_duration_ms": 125.5,
            "rows_received": 310,
            "rows_written": 310,
            "cleaned_rows": 12,
            "plan_count": 1,
            "recommendation_count": 3,
            "notification_count": 3,
            "email_outbox_count": 1,
            "retry_count": 2,
        }
    )
    repository.update(finished)

    assert created is True
    assert created_again is False
    assert reused.run_id == "run-1"
    assert repository.get("run-1") == finished
    assert repository.get("run-1").duration_ms == 60_000


def test_capture_run_repository_round_trips_execution_context(connection) -> None:
    repository = MarketCaptureRunRepository(connection)
    lease_expires_at = datetime(2026, 7, 13, 7, 11, tzinfo=UTC)
    run = MarketCaptureRun(
        run_id="intraday-display-20260713-1510",
        workflow_type="intraday",
        mode="display_only",
        trade_date=date(2026, 7, 13),
        effective_trade_date=date(2026, 7, 13),
        history_cutoff_date=date(2026, 7, 10),
        requested_symbol_scope=["512480", "600000"],
        lease_expires_at=lease_expires_at,
        idempotency_key="intraday:display_only:2026-07-13:1510",
        status=CaptureRunStatus.RUNNING,
        started_at=FETCHED_AT,
        requested_symbols=2,
    )

    stored, created = repository.get_or_create(run)

    assert created is True
    assert stored == run
    assert repository.get(run.run_id) == run


def test_capture_run_retry_claim_is_atomic_and_failure_only_updates_running(
    connection,
) -> None:
    repository = MarketCaptureRunRepository(connection)
    running, _ = repository.get_or_create(
        MarketCaptureRun(
            run_id="run-claim",
            workflow_type="intraday",
            trade_date=date(2026, 7, 13),
            idempotency_key="intraday:2026-07-13:1000",
            status=CaptureRunStatus.RUNNING,
            started_at=FETCHED_AT,
        )
    )
    failed_at = datetime(2026, 7, 13, 7, 2, tzinfo=UTC)
    failed = repository.fail_if_running(
        running.run_id,
        finished_at=failed_at,
        expected_started_at=running.started_at,
        error_summary="synthetic failure /tmp/private.db",
    )

    retry_at = datetime(2026, 7, 13, 7, 3, tzinfo=UTC)
    claimed = repository.claim_retry(failed, started_at=retry_at)
    duplicate_claim = repository.claim_retry(failed, started_at=retry_at)

    assert failed is not None
    assert failed.status is CaptureRunStatus.FAILED
    assert failed.error_summary == "synthetic failure [path]"
    assert claimed is not None
    assert claimed.status is CaptureRunStatus.RUNNING
    assert claimed.retry_count == 1
    assert claimed.started_at == retry_at
    assert duplicate_claim is None
    completed = claimed.model_copy(
        update={
            "status": CaptureRunStatus.SUCCEEDED,
            "finished_at": retry_at,
        }
    )
    with pytest.raises(CaptureRunAlreadyActiveError):
        repository.update_claimed(
            completed,
            claim_started_at=failed.started_at,
        )
    repository.update_claimed(completed, claim_started_at=claimed.started_at)
    reclaimed_terminal = repository.get(running.run_id)
    assert reclaimed_terminal.status is CaptureRunStatus.SUCCEEDED

    second_retry_at = datetime(2026, 7, 13, 7, 4, tzinfo=UTC)
    repository.update(
        reclaimed_terminal.model_copy(
            update={
                "status": CaptureRunStatus.RUNNING,
                "started_at": second_retry_at,
                "finished_at": None,
            }
        )
    )
    assert repository.fail_if_running(
        running.run_id,
        finished_at=failed_at,
        expected_started_at=second_retry_at,
        error_summary="final failure",
    ) is not None
    assert repository.fail_if_running(
        running.run_id,
        finished_at=failed_at,
        expected_started_at=second_retry_at,
        error_summary="must not overwrite terminal state",
    ) is None


def test_capture_results_upsert_per_dataset_and_list_in_stable_order(connection) -> None:
    repository = MarketCaptureResultRepository(connection)
    result = MarketCaptureResult(
        run_id="run-1",
        symbol="600000",
        dataset=CaptureDataset.DAILY_BAR,
        status=CaptureResultStatus.DEGRADED,
        fetched_at=FETCHED_AT,
        expected_rows=250,
        actual_rows=249,
        source="akshare",
        warning="one row missing",
    )
    repository.upsert(result)
    repository.upsert(
        result.model_copy(
            update={
                "status": CaptureResultStatus.COMPLETE,
                "actual_rows": 250,
                "warning": "",
            }
        )
    )
    repository.upsert(
        result.model_copy(
            update={
                "dataset": CaptureDataset.MONEY_FLOW,
                "expected_rows": 60,
                "actual_rows": 60,
                "status": CaptureResultStatus.COMPLETE,
                "warning": "",
            }
        )
    )

    listed = repository.list_for_run("run-1")
    assert [item.dataset for item in listed] == [
        CaptureDataset.DAILY_BAR,
        CaptureDataset.MONEY_FLOW,
    ]
    assert listed[0].actual_rows == 250


def test_intraday_strength_repository_returns_latest_for_symbol(connection) -> None:
    repository = IntradayStrengthSnapshotRepository(connection)

    def snapshot(run_id: str, data_time: datetime) -> IntradayStrengthSnapshot:
        return IntradayStrengthSnapshot(
            run_id=run_id,
            symbol="600000",
            trade_date=date(2026, 7, 13),
            label=StrengthLabel.NEUTRAL,
            confidence=StrengthConfidence.LOW,
            degraded=True,
            degradation_reasons=["test"],
            components=[],
            thresholds={},
            rule_version="intraday-strength-v1",
            data_coverage=0,
            source="fake",
            data_time=data_time,
            fetched_at=FETCHED_AT,
        )

    repository.save(snapshot("run-1", datetime(2026, 7, 13, 2, 0, tzinfo=UTC)))
    expected = snapshot("run-2", datetime(2026, 7, 13, 2, 3, tzinfo=UTC))
    repository.save(expected)

    assert repository.latest_for_symbol("600000") == expected


def test_dataset_snapshot_commit_is_persistent_and_caller_can_roll_back(tmp_path) -> None:
    path = tmp_path / "market-heavy.db"
    writer = sqlite3.connect(path)
    writer.row_factory = sqlite3.Row
    writer.execute("PRAGMA foreign_keys = ON")
    writer.executescript(MARKET_DECISION_SCHEMA_SQL)
    fact_id = DailyBarRepository(writer).save(daily_bar(10.5))
    snapshot = HistorySnapshot(
        run_id="run-1",
        symbol="600000",
        data_start=date(2026, 7, 10),
        data_end=date(2026, 7, 10),
        row_count=1,
        content_digest=content_digest([daily_bar(10.5).content_hash]),
        status=CaptureResultStatus.COMPLETE,
        fetched_at=FETCHED_AT,
    )
    persisted_id = HistorySnapshotRepository(writer).save(snapshot, [fact_id])
    rolled_back_id = HistorySnapshotRepository(writer).save(
        snapshot.model_copy(update={"run_id": "run-2"}),
        [fact_id],
        commit=False,
    )
    writer.rollback()
    writer.close()

    reader = sqlite3.connect(path)
    reader.row_factory = sqlite3.Row
    assert HistorySnapshotRepository(reader).get(persisted_id) == snapshot
    assert HistorySnapshotRepository(reader).get(rolled_back_id) is None
    reader.close()


def test_market_decision_schema_is_idempotent_and_exposes_normalized_fact_columns(
    connection,
) -> None:
    connection.executescript(MARKET_DECISION_SCHEMA_SQL)
    daily_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(daily_bars)")
    }
    flow_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(daily_money_flows)")
    }
    minute_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(minute_bars)")
    }
    history_index_columns = connection.execute(
        "PRAGMA index_xinfo(idx_history_snapshots_symbol_id)"
    ).fetchall()

    assert {"open", "high", "low", "close", "volume", "amount"} <= daily_columns
    assert {
        "main_net_amount",
        "main_net_pct",
        "super_large_net_amount",
        "super_large_net_pct",
        "large_net_amount",
        "large_net_pct",
        "medium_net_amount",
        "medium_net_pct",
        "small_net_amount",
        "small_net_pct",
    } <= flow_columns
    assert {"open", "high", "low", "close", "volume", "amount"} <= minute_columns
    assert [
        (column["name"], column["desc"])
        for column in history_index_columns
        if column["key"] == 1
    ] == [("symbol", 0), ("id", 1)]
