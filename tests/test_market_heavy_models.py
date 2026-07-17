from datetime import UTC, date, datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

import quantitative_trading.market.models as market_models

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
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
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


def test_history_snapshot_preserves_coverage_evidence_and_legacy_is_unverifiable() -> None:
    evidence = market_models.DailyBarCoverageEvidence(
        requested_start=date(2025, 7, 1),
        requested_end=date(2026, 7, 10),
        observed_start=date(2025, 7, 1),
        observed_end=date(2026, 7, 10),
        earliest_available_date=date(2026, 4, 1),
        complete_request_window=True,
        source="akshare_daily_full_window",
    )
    verified = HistorySnapshot(
        run_id="run-short-history",
        symbol="603459",
        data_start=date(2026, 4, 1),
        data_end=date(2026, 7, 10),
        row_count=68,
        content_digest="a" * 64,
        status=CaptureResultStatus.DEGRADED,
        completeness="verified_provider_window",
        coverage_evidence=evidence,
        fetched_at=FETCHED_AT,
    )
    legacy = HistorySnapshot.model_validate(
        {
            "run_id": "legacy-history",
            "symbol": "600000",
            "data_start": date(2026, 7, 10),
            "data_end": date(2026, 7, 10),
            "row_count": 1,
            "content_digest": "b" * 64,
            "status": CaptureResultStatus.DEGRADED,
            "fetched_at": FETCHED_AT,
        }
    )

    assert verified.coverage_evidence == evidence
    assert verified.completeness == "verified_provider_window"
    assert legacy.completeness == "unverifiable"
    assert legacy.coverage_evidence is None


def test_verified_history_rejects_missing_or_inconsistent_evidence() -> None:
    with pytest.raises(ValidationError, match="observed range"):
        market_models.DailyBarCoverageEvidence(
            requested_start=date(2025, 7, 1),
            requested_end=date(2026, 7, 10),
            complete_request_window=True,
            source="fake",
        )

    incomplete = market_models.DailyBarCoverageEvidence(
        requested_start=date(2025, 7, 1),
        requested_end=date(2026, 7, 10),
        observed_start=date(2026, 4, 1),
        observed_end=date(2026, 7, 10),
        earliest_available_date=date(2026, 4, 1),
        complete_request_window=False,
        source="fake",
    )
    payload = {
        "run_id": "run-invalid-evidence",
        "symbol": "603459",
        "data_start": date(2026, 4, 1),
        "data_end": date(2026, 7, 10),
        "row_count": 68,
        "content_digest": "c" * 64,
        "status": CaptureResultStatus.DEGRADED,
        "fetched_at": FETCHED_AT,
    }
    with pytest.raises(ValidationError, match="complete coverage"):
        HistorySnapshot(
            **payload,
            completeness="verified_provider_window",
            coverage_evidence=incomplete,
        )
    with pytest.raises(ValidationError, match="listing evidence"):
        HistorySnapshot(**payload, completeness="verified_listing_date")

    listing_evidence = market_models.ListingDateEvidence(
        listing_date=date(2026, 3, 31),
        source="exchange_directory",
    )
    listed = HistorySnapshot(
        **payload,
        completeness="verified_listing_date",
        listing_evidence=listing_evidence,
    )
    assert listed.listing_evidence == listing_evidence


def test_intraday_capture_context_is_explicit_and_legacy_defaults_to_decision() -> None:
    lease_expires_at = datetime(2026, 7, 13, 7, 11, tzinfo=UTC)
    display_run = MarketCaptureRun(
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
    legacy_run = MarketCaptureRun(
        run_id="intraday-20260713-1000",
        workflow_type="intraday",
        trade_date=date(2026, 7, 13),
        idempotency_key="intraday:2026-07-13:1000",
        status=CaptureRunStatus.RUNNING,
        started_at=FETCHED_AT,
    )

    assert display_run.mode == "display_only"
    assert display_run.effective_trade_date == date(2026, 7, 13)
    assert display_run.history_cutoff_date == date(2026, 7, 10)
    assert display_run.requested_symbol_scope == ["512480", "600000"]
    assert display_run.lease_expires_at == lease_expires_at
    assert legacy_run.mode == "decision"


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

    display = MarketInputSnapshot(
        universe_snapshot_id=1,
        quote_snapshot_refs={},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        mode="display_only",
        effective_trade_date=date(2026, 7, 13),
        history_cutoff_date=date(2026, 7, 10),
        fetched_at=FETCHED_AT,
        warnings=[],
    )
    assert display.mode == "display_only"
    assert legacy.mode is None

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


def test_etf_money_flow_not_applicable_is_preserved_in_market_input() -> None:
    from quantitative_trading.market.models import MarketInputSnapshot

    metadata = InstrumentMetadata(
        symbol="510300",
        name="沪深300ETF",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.ETF,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="exchange_catalog",
        metadata_checked_at=FETCHED_AT,
        rule_version="instrument-rules-v1",
    )
    snapshot = MarketInputSnapshot(
        universe_snapshot_id=1,
        quote_snapshot_refs={},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        instrument_metadata={"510300": metadata},
        dataset_quality={
            "510300": {
                CaptureDataset.MONEY_FLOW: DatasetQuality(
                    status=CaptureResultStatus.NOT_APPLICABLE,
                    expected_rows=0,
                    actual_rows=0,
                    source="instrument_policy",
                )
            }
        },
        fetched_at=FETCHED_AT,
        warnings=[],
    )

    assert snapshot.instrument_metadata["510300"] == metadata
    assert (
        snapshot.dataset_quality["510300"][CaptureDataset.MONEY_FLOW].status
        is CaptureResultStatus.NOT_APPLICABLE
    )
