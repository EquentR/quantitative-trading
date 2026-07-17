import sqlite3
from datetime import UTC, date, datetime

import pytest

import quantitative_trading.market.models as market_models

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
