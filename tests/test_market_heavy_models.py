from datetime import UTC, date, datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureResultStatus,
    CaptureRunStatus,
    DailyBar,
    DailyMoneyFlow,
    DatasetQuality,
    HistorySnapshot,
    IntradayStrengthSnapshot,
    LimitStatus,
    MarketCaptureResult,
    MarketCaptureRun,
    MinuteBar,
    MoneyFlowSnapshot,
    QuoteSnapshot,
    QuoteStatus,
    StrengthConfidence,
    StrengthLabel,
    TradingStatus,
)


FETCHED_AT = datetime(2026, 7, 13, 7, 1, tzinfo=UTC)


def test_quote_snapshot_accepts_standard_optional_market_fields() -> None:
    quote = QuoteSnapshot(
        symbol="600000",
        previous_close=10.0,
        open_price=10.1,
        high_price=10.8,
        low_price=9.9,
        current_price=10.5,
        change_pct=5.0,
        volume=12_300,
        amount=129_150.0,
        trading_status=TradingStatus.NORMAL,
        limit_status=LimitStatus.NONE,
        data_time=FETCHED_AT,
        fetched_at=FETCHED_AT,
        source="fake",
        status=QuoteStatus.OK,
    )

    assert quote.previous_close == 10.0
    assert quote.volume == 12_300
    assert quote.trading_status is TradingStatus.NORMAL


def test_daily_bar_hash_is_stable_across_fetch_times_but_changes_with_facts() -> None:
    first = DailyBar(
        symbol="600000",
        trade_date=date(2026, 7, 10),
        open=10,
        high=11,
        low=9,
        close=10.5,
        volume=100_000,
        amount=1_050_000,
        source="akshare",
        fetched_at=FETCHED_AT,
    )
    later = first.model_copy(update={"fetched_at": datetime(2026, 7, 13, 8, 0, tzinfo=UTC)})
    corrected = first.model_copy(update={"close": 10.6})

    assert first.content_hash == later.content_hash
    assert corrected.content_hash != first.content_hash


def test_minute_bar_requires_shanghai_trading_time_and_matching_trade_date() -> None:
    with pytest.raises(ValidationError, match="Asia/Shanghai"):
        MinuteBar(
            symbol="600000",
            trade_date=date(2026, 7, 13),
            minute=datetime(2026, 7, 13, 10, 0, tzinfo=UTC),
            open=10,
            high=10,
            low=10,
            close=10,
            volume=100,
            amount=1_000,
            source="fake",
            fetched_at=FETCHED_AT,
        )

    with pytest.raises(ValidationError, match="trading session"):
        MinuteBar(
            symbol="600000",
            trade_date=date(2026, 7, 13),
            minute=datetime(
                2026, 7, 13, 12, 0, tzinfo=timezone(timedelta(hours=8))
            ),
            open=10,
            high=10,
            low=10,
            close=10,
            volume=100,
            amount=1_000,
            source="fake",
            fetched_at=FETCHED_AT,
        )


def test_capture_run_result_and_dataset_snapshots_validate_contract() -> None:
    run = MarketCaptureRun(
        run_id="run-20260713-close",
        workflow_type="close",
        trade_date=date(2026, 7, 13),
        idempotency_key="close:2026-07-13",
        status=CaptureRunStatus.RUNNING,
        started_at=FETCHED_AT,
        requested_symbols=2,
    )
    result = MarketCaptureResult(
        run_id=run.run_id,
        symbol="600000",
        dataset=CaptureDataset.DAILY_BAR,
        status=CaptureResultStatus.COMPLETE,
        data_start=date(2026, 7, 10),
        data_end=date(2026, 7, 10),
        fetched_at=FETCHED_AT,
        expected_rows=1,
        actual_rows=1,
        source="akshare",
    )
    history = HistorySnapshot(
        run_id=run.run_id,
        symbol="600000",
        data_start=date(2026, 7, 10),
        data_end=date(2026, 7, 10),
        row_count=1,
        content_digest="a" * 64,
        status=CaptureResultStatus.COMPLETE,
        fetched_at=FETCHED_AT,
    )
    flow = MoneyFlowSnapshot(
        run_id=run.run_id,
        symbol="600000",
        data_start=date(2026, 7, 10),
        data_end=date(2026, 7, 10),
        row_count=1,
        content_digest="b" * 64,
        status=CaptureResultStatus.COMPLETE,
        fetched_at=FETCHED_AT,
    )

    assert result.dataset is CaptureDataset.DAILY_BAR
    assert history.adjustment == "forward"
    assert flow.row_count == 1


def test_money_flow_and_strength_models_reject_non_finite_or_naive_data() -> None:
    with pytest.raises(ValidationError):
        DailyMoneyFlow(
            symbol="600000",
            trade_date=date(2026, 7, 10),
            main_net_amount=float("nan"),
            main_net_pct=1,
            super_large_net_amount=1,
            super_large_net_pct=1,
            large_net_amount=1,
            large_net_pct=1,
            medium_net_amount=1,
            medium_net_pct=1,
            small_net_amount=1,
            small_net_pct=1,
            source="akshare",
            fetched_at=FETCHED_AT,
        )


def test_market_input_snapshot_accepts_run_link_and_dataset_quality_defaults() -> None:
    from quantitative_trading.market.models import MarketInputSnapshot

    legacy = MarketInputSnapshot(
        universe_snapshot_id=1,
        quote_snapshot_refs={},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        fetched_at=FETCHED_AT,
        warnings=[],
    )
    linked = legacy.model_copy(
        update={
            "capture_run_id": "run-1",
            "dataset_quality": {
                "600000": {
                    CaptureDataset.DAILY_BAR: DatasetQuality(
                        status=CaptureResultStatus.COMPLETE,
                        data_start=date(2026, 7, 10),
                        data_end=date(2026, 7, 10),
                        expected_rows=1,
                        actual_rows=1,
                    )
                }
            },
        }
    )

    assert legacy.capture_run_id is None
    assert legacy.dataset_quality == {}
    assert linked.dataset_quality["600000"][CaptureDataset.DAILY_BAR].actual_rows == 1

    with pytest.raises(ValidationError, match="timezone-aware"):
        IntradayStrengthSnapshot(
            run_id="run-1",
            symbol="600000",
            trade_date=date(2026, 7, 13),
            label=StrengthLabel.NEUTRAL,
            confidence=StrengthConfidence.LOW,
            degraded=True,
            degradation_reasons=["insufficient components"],
            components=[],
            thresholds={},
            rule_version="intraday-strength-v1",
            data_coverage=0,
            source="fake",
            data_time=datetime(2026, 7, 13, 10, 0),
            fetched_at=FETCHED_AT,
        )
