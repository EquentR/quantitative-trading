import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.config import Settings
from quantitative_trading.decision.factory import build_notification_dispatcher
from quantitative_trading.decision.workflow import (
    DecisionWorkflow,
    WorkflowAlreadyRunningError,
    _trading_minute_lag,
)
from quantitative_trading.email.models import SmtpSettingsUpdate
from quantitative_trading.email.outbox import EmailDeliveryRepository
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.email.service import SmtpSettingsService
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.market.adapters import DailyBarFetchResult, MarketProviderError
from quantitative_trading.market.backfill import HeavyDataBackfillService
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.cli_service import MarketCliService
from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureExecutionMode,
    CaptureResultStatus,
    DailyBar,
    DailyMoneyFlow,
    CaptureRunStatus,
    CaptureRunAlreadyActiveError,
    DailyBarCoverageEvidence,
    HistorySnapshot,
    LimitStatus,
    QuoteSnapshot,
    QuoteStatus,
    MinuteBar,
    MarketCaptureRun,
    TradingStatus,
)
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    HistorySnapshotRepository,
    MarketCaptureRunRepository,
    MoneyFlowRepository,
    MoneyFlowSnapshotRepository,
    IntradayStrengthSnapshotRepository,
    MarketCaptureResultRepository,
    MinuteBarRepository,
    content_digest,
)
from quantitative_trading.market.repository import (
    MarketInputSnapshotRepository,
    QuoteSnapshotRepository,
)
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.planning.models import TradingPlanStatus
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.recommendation.models import RecommendationAction
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import InstrumentRepository
from quantitative_trading.watchlist.models import WatchPinnedInput, WatchPinnedSource
from quantitative_trading.watchlist.repository import WatchPinnedRepository
from tests.instrument_fixtures import etf_name_variant_metadata


TRADE_DATE = date(2026, 7, 13)
NOW = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)


class RecordingQuoteProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def get_quotes(self, symbols):
        self.calls.append(list(symbols))
        return {
            symbol: QuoteSnapshot(
                symbol=symbol,
                name="浦发银行",
                previous_close=10,
                open_price=10.1,
                high_price=10.8,
                low_price=9.9,
                current_price=10.5,
                change_pct=5,
                volume=1_000_000,
                amount=10_500_000,
                data_time=NOW,
                fetched_at=NOW,
                source="fake",
                status=QuoteStatus.OK,
            )
            for symbol in symbols
        }


class CalendarDailyProvider:
    def __init__(self, calendar: XSHGTradingCalendar) -> None:
        self.calendar = calendar
        self.calls = []

    def get_daily_bars(self, symbol, start_date, end_date, adjustment):
        self.calls.append((symbol, start_date, end_date, adjustment))
        days = self.calendar.trading_days(start_date, end_date)
        return [
            DailyBar(
                symbol=symbol,
                trade_date=day,
                open=9.8 + index * 0.001,
                high=10.8 + index * 0.001,
                low=9.5 + index * 0.001,
                close=10 + index * 0.001,
                volume=1_000_000 + index,
                amount=(1_000_000 + index) * (10 + index * 0.001),
                source="fake",
                fetched_at=NOW,
            )
            for index, day in enumerate(days)
        ]


class FailingFullReproofDailyProvider(CalendarDailyProvider):
    def __init__(self, calendar: XSHGTradingCalendar) -> None:
        super().__init__(calendar)
        self.coverage_calls = []
        self.revision_date: date | None = None
        self.fail_full_reproof = False

    def get_daily_bars(self, symbol, start_date, end_date, adjustment):
        bars = super().get_daily_bars(symbol, start_date, end_date, adjustment)
        return [
            bar.model_copy(
                update={
                    "high": max(bar.high, 12.0),
                    "close": 12.0,
                    "amount": bar.volume * 12.0,
                }
            )
            if bar.trade_date == self.revision_date
            else bar
            for bar in bars
        ]

    def get_daily_bars_with_coverage(
        self,
        symbol,
        start_date,
        end_date,
        adjustment,
    ) -> DailyBarFetchResult:
        self.coverage_calls.append((symbol, start_date, end_date, adjustment))
        requested_days = self.calendar.trading_days(start_date, end_date)
        if self.fail_full_reproof and len(requested_days) > 5:
            raise MarketProviderError("synthetic full-window reproof outage")
        bars = tuple(self.get_daily_bars(symbol, start_date, end_date, adjustment))
        return DailyBarFetchResult(
            bars=bars,
            coverage_evidence=DailyBarCoverageEvidence(
                requested_start=start_date,
                requested_end=end_date,
                observed_start=start_date,
                observed_end=end_date,
                earliest_available_date=bars[0].trade_date,
                complete_request_window=True,
                source="failing_full_reproof_provider",
            ),
        )


class CalendarFlowProvider:
    def __init__(self, calendar: XSHGTradingCalendar) -> None:
        self.calendar = calendar
        self.calls = []

    def get_daily_money_flow(self, symbol, start_date, end_date):
        self.calls.append((symbol, start_date, end_date))
        return [
            DailyMoneyFlow(
                symbol=symbol,
                trade_date=day,
                main_net_amount=1_000_000,
                main_net_pct=2,
                super_large_net_amount=600_000,
                super_large_net_pct=1.2,
                large_net_amount=400_000,
                large_net_pct=0.8,
                medium_net_amount=-300_000,
                medium_net_pct=-0.6,
                small_net_amount=-700_000,
                small_net_pct=-1.4,
                source="fake",
                fetched_at=NOW,
            )
            for day in self.calendar.trading_days(start_date, end_date)
        ]


class NoopIntradayProvider:
    def get_minute_bars(self, symbol, trade_date, interval):
        return []


class IncrementalIntradayProvider:
    def __init__(self, bars):
        self.bars = bars

    def get_minute_bars(self, symbol, trade_date, interval):
        return self.bars


class StaleQuoteProvider(RecordingQuoteProvider):
    def get_quotes(self, symbols):
        quotes = super().get_quotes(symbols)
        return {
            symbol: quote.model_copy(
                update={
                    "status": QuoteStatus.STALE,
                    "warning": "quote is stale",
                    "data_time": datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
                }
            )
            for symbol, quote in quotes.items()
        }


class StaleLowQuoteProvider(StaleQuoteProvider):
    def get_quotes(self, symbols):
        quotes = super().get_quotes(symbols)
        return {
            symbol: quote.model_copy(update={"current_price": 1.0})
            for symbol, quote in quotes.items()
        }


class SameDayOldQuoteProvider(RecordingQuoteProvider):
    def get_quotes(self, symbols):
        quotes = super().get_quotes(symbols)
        return {
            symbol: quote.model_copy(
                update={"data_time": datetime(2026, 7, 13, 6, 0, tzinfo=UTC)}
            )
            for symbol, quote in quotes.items()
        }


class NoTimeQuoteProvider(RecordingQuoteProvider):
    def __init__(self, price: float) -> None:
        super().__init__()
        self.price = price

    def get_quotes(self, symbols):
        quotes = super().get_quotes(symbols)
        return {
            symbol: quote.model_copy(
                update={
                    "current_price": self.price,
                    "data_time": None,
                    "status": QuoteStatus.PARTIAL,
                    "warning": "market source time unavailable",
                }
            )
            for symbol, quote in quotes.items()
        }


class MatchingCloseDailyProvider(CalendarDailyProvider):
    def get_daily_bars(self, symbol, start_date, end_date, adjustment):
        bars = super().get_daily_bars(symbol, start_date, end_date, adjustment)
        last = bars[-1]
        bars[-1] = last.model_copy(
            update={
                "close": 10.5,
                "high": max(last.high, 10.5),
                "amount": last.volume * 10.5,
            }
        )
        return bars


class FailingFlowProvider:
    def get_daily_money_flow(self, symbol, start_date, end_date):
        raise MarketProviderError("synthetic flow outage")


class UnexpectedProvider:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected provider access: {name}")


class FailingRecommendationDispatcher:
    def dispatch_recommendation(self, recommendation, *, plan_version, now):
        del recommendation, plan_version, now
        raise RuntimeError("synthetic downstream failure")


def seed_account(connection) -> None:
    PositionRepository(connection).add(
        PositionInput(
            symbol="600000",
            name="浦发银行",
            quantity=1000,
            available_quantity=1000,
            cost_price=7.0,
            opened_at=date(2026, 7, 1),
            note="manual",
        ),
        now=datetime(2026, 7, 12, 8, 0, tzinfo=UTC),
    )
    CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")


def test_close_workflow_builds_traceable_next_day_plan_and_is_idempotent(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "close-workflow.db")
    calendar = XSHGTradingCalendar()
    quote_provider = RecordingQuoteProvider()
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)

    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=quote_provider,
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        )

        first = workflow.run_close(TRADE_DATE)
        second = workflow.run_close(TRADE_DATE)

        plan = TradingPlanRepository(connection).active_for_day(date(2026, 7, 14))
        capture_run = MarketCaptureRunRepository(connection).get(first.run_id)
        market_input = MarketInputSnapshotRepository(connection).get(
            first.market_input_snapshot_id
        )

        assert first.ready is True
        assert first.reused is False
        assert second.reused is True
        assert second.plan_id == first.plan_id
        assert quote_provider.calls == [["600000"]]
        assert len(DailyBarRepository(connection).current("600000")) == 250
        assert len(MoneyFlowRepository(connection).current("600000")) == 60
        assert plan is not None
        assert plan.status is TradingPlanStatus.ACTIVE
        assert plan.source_run_id == first.run_id
        assert plan.market_input_snapshot_id == first.market_input_snapshot_id
        assert plan.key_levels["600000"]["support"] != 7.0 * 0.97
        assert capture_run is not None
        assert capture_run.status.value == "succeeded"
        assert capture_run.provider_calls == 3
        assert capture_run.provider_duration_ms >= 0
        assert capture_run.plan_count == 1
        assert capture_run.recommendation_count == 0
        assert market_input is not None
        assert market_input.capture_run_id == first.run_id
        assert market_input.history_snapshot_refs["600000"] > 0
        assert market_input.money_flow_snapshot_refs["600000"] > 0
        assert market_input.data_time == calendar.session(TRADE_DATE).close_at
        context = plan.symbol_contexts["600000"]
        assert context.position_context["source"] == "manual_ledger"
        assert context.position_context["quantity"] == 1000
        assert context.position_context["updated_at"]
        assert context.account_context["snapshot_id"] == plan.account_snapshot_id
        assert context.account_context["source"] == (
            "manual_cash_account_and_market_snapshot"
        )


def test_close_workflow_routes_etf_and_skips_money_flow_provider(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-etf-workflow.db")
    calendar = XSHGTradingCalendar()
    metadata = InstrumentMetadata(
        symbol="510300",
        name="沪深300ETF",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.ETF,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="exchange_catalog",
        metadata_checked_at=NOW,
        rule_version="instrument-rules-v1",
    )
    etf_quote_provider = RecordingQuoteProvider()
    etf_daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)

    with connect(settings) as connection:
        migrate(connection)
        PositionRepository(connection).add(
            PositionInput(
                symbol="510300",
                name="沪深300ETF",
                quantity=1000,
                available_quantity=1000,
                cost_price=7.0,
                opened_at=date(2026, 7, 1),
            ),
            now=datetime(2026, 7, 12, 8, 0, tzinfo=UTC),
        )
        CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=UnexpectedProvider(),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            etf_quote_provider=etf_quote_provider,
            etf_daily_provider=etf_daily_provider,
            etf_intraday_provider=NoopIntradayProvider(),
            instrument_metadata_loader=lambda symbols: {
                symbol: metadata for symbol in symbols if symbol == metadata.symbol
            },
            now=lambda: NOW,
        )

        result = workflow.run_close(TRADE_DATE)
        plan = TradingPlanRepository(connection).get(result.plan_id)
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        capture_run = MarketCaptureRunRepository(connection).get(result.run_id)
        capture_results = MarketCaptureResultRepository(connection).list_for_run(
            result.run_id
        )

    flow_result = next(
        item for item in capture_results if item.dataset is CaptureDataset.MONEY_FLOW
    )
    assert flow_provider.calls == []
    assert etf_quote_provider.calls == [["510300"]]
    assert {call[0] for call in etf_daily_provider.calls} == {"510300"}
    assert flow_result.status is CaptureResultStatus.NOT_APPLICABLE
    assert market_input is not None
    assert market_input.instrument_metadata["510300"] == metadata
    assert market_input.money_flow_snapshot_refs == {}
    assert plan is not None
    assert plan.data_quality == "complete"
    assert plan.symbol_contexts["510300"].instrument == metadata
    assert capture_run is not None
    assert capture_run.status is CaptureRunStatus.SUCCEEDED
    assert capture_run.provider_calls == 2


def test_close_workflow_empty_universe_saves_warning_without_publishing_plan(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "close-empty.db")
    calendar = XSHGTradingCalendar()
    quote_provider = RecordingQuoteProvider()
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)
    with connect(settings) as connection:
        migrate(connection)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=quote_provider,
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        )

        first = workflow.run_close(TRADE_DATE)
        second = workflow.run_close(TRADE_DATE)
        run = MarketCaptureRunRepository(connection).get(first.run_id)
        snapshot = MarketInputSnapshotRepository(connection).get(
            first.market_input_snapshot_id
        )

    assert first.ready is True
    assert first.plan_id is None
    assert second.reused is True
    assert second.plan_id is None
    assert quote_provider.calls == []
    assert daily_provider.calls == []
    assert flow_provider.calls == []
    assert run is not None
    assert run.status is CaptureRunStatus.SUCCEEDED
    assert run.plan_count == 0
    assert run.provider_calls == 0
    assert run.warning_count == 1
    assert snapshot is not None
    assert snapshot.quote_snapshot_refs == {}
    assert snapshot.history_snapshot_refs == {}
    assert snapshot.money_flow_snapshot_refs == {}
    assert snapshot.warnings == ["决策启用集合为空"]
    assert first.warnings == ("决策启用集合为空",)


def test_close_workflow_unknown_holding_calls_no_market_provider(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-unknown.db")
    calendar = XSHGTradingCalendar()
    metadata = InstrumentMetadata(
        symbol="900001",
        name="Unknown",
        exchange=None,
        instrument_type=InstrumentType.UNKNOWN,
        settlement_cycle=SettlementCycle.UNKNOWN,
        metadata_source="legacy-unverified",
        metadata_checked_at=NOW,
        rule_version="unverified-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        PositionRepository(connection).add(
            PositionInput(
                symbol="900001",
                name="Unknown",
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=date(2026, 7, 1),
            ),
            now=NOW,
        )
        CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=UnexpectedProvider(),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=UnexpectedProvider(),
            instrument_metadata_loader=lambda symbols: {
                symbol: metadata for symbol in symbols
            },
            now=lambda: NOW,
        ).run_close(TRADE_DATE)
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        capture_results = MarketCaptureResultRepository(connection).list_for_run(
            result.run_id
        )

    assert result.ready is False
    assert run is not None
    assert run.provider_calls == 0
    assert {
        item.dataset: item.status for item in capture_results
    } == {
        CaptureDataset.QUOTE: CaptureResultStatus.FAILED,
        CaptureDataset.DAILY_BAR: CaptureResultStatus.FAILED,
        CaptureDataset.MONEY_FLOW: CaptureResultStatus.FAILED,
    }


def test_intraday_unknown_holding_emits_conservative_hold_without_fake_market_time(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-unknown-holding.db")
    calendar = XSHGTradingCalendar()
    started_at = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)
    metadata_checked_at = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
    metadata = InstrumentMetadata(
        symbol="900001",
        name="Unknown",
        exchange=None,
        instrument_type=InstrumentType.UNKNOWN,
        settlement_cycle=SettlementCycle.UNKNOWN,
        metadata_source="legacy-unverified",
        metadata_checked_at=metadata_checked_at,
        rule_version="unverified-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        PositionRepository(connection).add(
            PositionInput(
                symbol="900001",
                name="Unknown",
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=date(2026, 7, 1),
            ),
            now=metadata_checked_at,
        )
        CashAccountRepository(connection).initialize(
            50_000, now=metadata_checked_at, note="initial"
        )
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=UnexpectedProvider(),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=UnexpectedProvider(),
            instrument_metadata_loader=lambda symbols: {
                symbol: metadata for symbol in symbols
            },
            now=lambda: started_at,
        ).run_intraday()
        recommendations = RecommendationRepository(connection).list(limit=10)

    assert result.status is CaptureRunStatus.FAILED
    assert len(recommendations) == 1
    recommendation = recommendations[0]
    assert recommendation.action is RecommendationAction.HOLD
    assert recommendation.data_time == metadata_checked_at
    assert recommendation.data_quality["data_time_source"] == "instrument_metadata"
    assert recommendation.price_context["market_data_time"] is None
    assert "instrument_metadata_unknown" in recommendation.risk["machine_reason"]
    assert any("不代表行情时间" in warning for warning in recommendation.data_quality["warnings"])


def test_close_workflow_rejects_concurrent_active_run_without_calling_provider(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "close-concurrent.db")
    calendar = XSHGTradingCalendar()
    quote_provider = RecordingQuoteProvider()
    with connect(settings) as connection:
        migrate(connection)
        MarketCaptureRunRepository(connection).get_or_create(
            MarketCaptureRun(
                run_id="close-20260713",
                workflow_type="close",
                trade_date=TRADE_DATE,
                idempotency_key="close:2026-07-13",
                status=CaptureRunStatus.RUNNING,
                started_at=NOW,
            )
        )
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=quote_provider,
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        )

        with pytest.raises(WorkflowAlreadyRunningError, match="already active"):
            workflow.run_close(TRADE_DATE)

    assert quote_provider.calls == []


def test_close_workflow_recovers_published_plan_before_rejecting_active_lease(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "close-published-recovery.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        first = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)
        repository = MarketCaptureRunRepository(connection)
        completed = repository.get(first.run_id)
        assert completed is not None
        repository.update(
            completed.model_copy(
                update={
                    "status": CaptureRunStatus.RUNNING,
                    "started_at": NOW + timedelta(minutes=1),
                    "finished_at": None,
                }
            )
        )

        recovered = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW + timedelta(minutes=2),
        ).run_close(TRADE_DATE)
        restored_run = repository.get(first.run_id)

    assert recovered.reused is True
    assert recovered.plan_id == first.plan_id
    assert restored_run is not None
    assert restored_run.status in {
        CaptureRunStatus.SUCCEEDED,
        CaptureRunStatus.DEGRADED,
    }


def test_close_workflow_keeps_active_lease_at_close_cutoff(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-cutoff-lease.db")
    calendar = XSHGTradingCalendar()
    lease_started = datetime(2026, 7, 13, 8, 25, tzinfo=UTC)
    cutoff = datetime(2026, 7, 13, 8, 30, tzinfo=UTC)
    with connect(settings) as connection:
        migrate(connection)
        MarketCaptureRunRepository(connection).get_or_create(
            MarketCaptureRun(
                run_id="close-20260713",
                workflow_type="close",
                trade_date=TRADE_DATE,
                idempotency_key="close:2026-07-13",
                status=CaptureRunStatus.RUNNING,
                started_at=lease_started,
            )
        )

        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: cutoff,
        )

        with pytest.raises(WorkflowAlreadyRunningError):
            workflow.run_close(TRADE_DATE)
        run = MarketCaptureRunRepository(connection).get("close-20260713")

    assert run is not None
    assert run.retry_count == 0


def test_close_workflow_releases_expired_cross_cycle_lease(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-expired-cross-cycle.db")
    calendar = XSHGTradingCalendar()
    expired_at = NOW - timedelta(days=1)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        MarketCaptureRunRepository(connection).get_or_create(
            MarketCaptureRun(
                run_id="close-20260712",
                workflow_type="close",
                trade_date=date(2026, 7, 12),
                idempotency_key="close:2026-07-12",
                status=CaptureRunStatus.RUNNING,
                started_at=expired_at,
            )
        )
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)
        expired = MarketCaptureRunRepository(connection).get("close-20260712")

    assert result.ready is True
    assert expired is not None
    assert expired.status is CaptureRunStatus.FAILED
    assert "expired" in expired.error_summary


def test_close_workflow_does_not_derive_future_close_time_before_session_close(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "close-before-session-end.db")
    calendar = XSHGTradingCalendar()
    before_close = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        clock = MutableClock(before_close)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )

    assert result.ready is False
    assert result.plan_id is None
    assert market_input is not None
    assert market_input.data_time is None or market_input.data_time <= before_close


def test_intraday_marks_previous_period_running_run_failed(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-expired-run.db")
    calendar = XSHGTradingCalendar()
    previous_start = datetime(2026, 7, 14, 1, 57, tzinfo=UTC)
    current_time = datetime(2026, 7, 14, 2, 10, tzinfo=UTC)
    with connect(settings) as connection:
        migrate(connection)
        MarketCaptureRunRepository(connection).get_or_create(
            MarketCaptureRun(
                run_id="intraday-20260714-0957",
                workflow_type="intraday",
                trade_date=date(2026, 7, 14),
                period_start=previous_start,
                period_end=previous_start + timedelta(minutes=3),
                idempotency_key="intraday:2026-07-14:0957",
                status=CaptureRunStatus.RUNNING,
                started_at=previous_start,
            )
        )

        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: current_time,
        ).run_intraday()
        expired = MarketCaptureRunRepository(connection).get(
            "intraday-20260714-0957"
        )

    assert expired is not None
    assert expired.status is CaptureRunStatus.FAILED
    assert expired.finished_at == current_time
    assert "expired before the next intraday period" in expired.error_summary


def test_intraday_rejects_new_period_while_previous_period_has_active_lease(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-global-lease.db")
    calendar = XSHGTradingCalendar()
    previous_start = datetime(2026, 7, 14, 1, 30, tzinfo=UTC)
    current_time = previous_start + timedelta(minutes=3)
    quote_provider = RecordingQuoteProvider()
    with connect(settings) as connection:
        migrate(connection)
        MarketCaptureRunRepository(connection).get_or_create(
            MarketCaptureRun(
                run_id="intraday-20260714-0930",
                workflow_type="intraday",
                trade_date=date(2026, 7, 14),
                period_start=previous_start,
                period_end=previous_start + timedelta(minutes=3),
                idempotency_key="intraday:2026-07-14:0930",
                status=CaptureRunStatus.RUNNING,
                started_at=previous_start,
            )
        )

        with pytest.raises(WorkflowAlreadyRunningError):
            DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=quote_provider,
                daily_provider=CalendarDailyProvider(calendar),
                money_flow_provider=CalendarFlowProvider(calendar),
                intraday_provider=NoopIntradayProvider(),
                now=lambda: current_time,
            ).run_intraday()

    assert quote_provider.calls == []


def test_close_workflow_marks_capture_run_failed_on_unhandled_exception(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(database_path=tmp_path / "close-failure-terminal.db")
    calendar = XSHGTradingCalendar()

    def fail_positions(_repository):
        raise RuntimeError("synthetic workflow failure /tmp/private.db")

    monkeypatch.setattr(PositionRepository, "list", fail_positions)
    with connect(settings) as connection:
        migrate(connection)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        )

        with pytest.raises(RuntimeError, match="synthetic workflow failure"):
            workflow.run_close(TRADE_DATE)
        run = MarketCaptureRunRepository(connection).get("close-20260713")

    assert run is not None
    assert run.status is CaptureRunStatus.FAILED
    assert run.finished_at == NOW
    assert run.failure_count == 1
    assert run.error_summary == "synthetic workflow failure [path]"


def test_close_workflow_database_error_inside_symbol_capture_terminates_run(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(database_path=tmp_path / "close-database-failure.db")
    calendar = XSHGTradingCalendar()
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)
    original_save = DailyBarRepository.save

    def fail_daily_save(self, bar, *, commit=True):
        del self, bar, commit
        raise sqlite3.IntegrityError("synthetic daily integrity failure")

    monkeypatch.setattr(DailyBarRepository, "save", fail_daily_save)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        with pytest.raises(sqlite3.IntegrityError, match="daily integrity"):
            DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=RecordingQuoteProvider(),
                daily_provider=daily_provider,
                money_flow_provider=flow_provider,
                intraday_provider=NoopIntradayProvider(),
                now=lambda: NOW,
            ).run_close(TRADE_DATE)
        run = MarketCaptureRunRepository(connection).get("close-20260713")

    monkeypatch.setattr(DailyBarRepository, "save", original_save)
    assert run is not None
    assert run.status is CaptureRunStatus.FAILED
    assert flow_provider.calls == []


def test_close_workflow_internal_contract_error_inside_capture_terminates_run(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(database_path=tmp_path / "close-contract-failure.db")
    calendar = XSHGTradingCalendar()

    def fail_internal_contract(  # noqa: ANN001
        self,
        run_id,
        symbol,
        as_of,
        *,
        listing_evidence=None,
    ):
        del self, run_id, symbol, as_of, listing_evidence
        raise ValueError("synthetic daily mapping invariant failure")

    monkeypatch.setattr(
        HeavyDataBackfillService,
        "backfill_daily",
        fail_internal_contract,
    )
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        with pytest.raises(ValueError, match="mapping invariant"):
            DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=RecordingQuoteProvider(),
                daily_provider=CalendarDailyProvider(calendar),
                money_flow_provider=CalendarFlowProvider(calendar),
                intraday_provider=NoopIntradayProvider(),
                now=lambda: NOW,
            ).run_close(TRADE_DATE)
        run = MarketCaptureRunRepository(connection).get("close-20260713")

    assert run is not None
    assert run.status is CaptureRunStatus.FAILED


def test_intraday_database_error_inside_minute_capture_terminates_run(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-database-failure.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)

        def fail_minute_save(self, bars, *, commit=True):
            del self, bars, commit
            raise sqlite3.IntegrityError("synthetic minute integrity failure")

        monkeypatch.setattr(MinuteBarRepository, "upsert_many", fail_minute_save)
        with pytest.raises(sqlite3.IntegrityError, match="minute integrity"):
            DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=ClockQuoteProvider(clock),
                daily_provider=CalendarDailyProvider(calendar),
                money_flow_provider=CalendarFlowProvider(calendar),
                intraday_provider=RisingIntradayProvider(calendar, clock),
                now=clock.now,
            ).run_intraday()
        run = MarketCaptureRunRepository(connection).get("intraday-20260714-1000")

    assert run is not None
    assert run.status is CaptureRunStatus.FAILED


def test_intraday_internal_contract_error_inside_capture_terminates_run(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-contract-failure.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)

        def fail_minute_contract(self, symbol, trade_date):  # noqa: ANN001
            del self, symbol, trade_date
            raise ValueError("synthetic minute mapping invariant failure")

        monkeypatch.setattr(
            MinuteBarRepository,
            "for_trade_date",
            fail_minute_contract,
        )
        with pytest.raises(ValueError, match="mapping invariant"):
            DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=ClockQuoteProvider(clock),
                daily_provider=CalendarDailyProvider(calendar),
                money_flow_provider=CalendarFlowProvider(calendar),
                intraday_provider=RisingIntradayProvider(calendar, clock),
                now=clock.now,
            ).run_intraday()
        run = MarketCaptureRunRepository(connection).get("intraday-20260714-1000")

    assert run is not None
    assert run.status is CaptureRunStatus.FAILED


def test_intraday_local_notification_failure_rolls_back_recommendation_stage(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(
        database_path=tmp_path / "intraday-notification-failure.db",
        log_dir=tmp_path / "logs",
    )
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)

        def fail_notification_save(self, summary, *, commit=True):
            del self, summary, commit
            raise sqlite3.IntegrityError("synthetic notification integrity failure")

        monkeypatch.setattr(NotificationRepository, "save", fail_notification_save)
        with pytest.raises(sqlite3.IntegrityError, match="notification integrity"):
            DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=ClockQuoteProvider(clock),
                daily_provider=CalendarDailyProvider(calendar),
                money_flow_provider=CalendarFlowProvider(calendar),
                intraday_provider=RisingIntradayProvider(calendar, clock),
                now=clock.now,
                notification_dispatcher=build_notification_dispatcher(
                    connection, settings
                ),
            ).run_intraday()

        assert RecommendationRepository(connection).list(limit=20) == []
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0
        event_types = {
            audit.event_type
            for audit in AuditLogRepository(connection).list_recent(limit=50)
        }

    assert "recommendation.generated" not in event_types
    assert "notification.created" not in event_types


def test_close_workflow_does_not_publish_when_required_quote_is_stale(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-stale.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=StaleQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)

        run = MarketCaptureRunRepository(connection).get(result.run_id)
        plan = TradingPlanRepository(connection).active_for_day(date(2026, 7, 14))

    assert result.ready is False
    assert result.plan_id is None
    assert plan is None
    assert run is not None
    assert run.status.value == "failed"


def test_close_workflow_rejects_same_day_quote_older_than_six_trading_minutes(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "close-old-quote.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=SameDayOldQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)

    assert result.ready is False
    assert result.plan_id is None
    assert any("stale" in warning for warning in result.warnings)


def test_close_workflow_verifies_no_time_quote_against_same_day_daily_close(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "close-verified-quote.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=NoTimeQuoteProvider(10.5),
            daily_provider=MatchingCloseDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)
        snapshot = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )

    assert result.ready is True
    assert result.plan_id is not None
    assert snapshot is not None
    quote_quality = snapshot.dataset_quality["600000"][CaptureDataset.QUOTE]
    assert quote_quality.status is CaptureResultStatus.DEGRADED
    assert quote_quality.data_time == calendar.session(TRADE_DATE).close_at
    assert "verified against same-day daily close" in quote_quality.warning


def test_close_workflow_rejects_no_time_quote_when_daily_close_differs(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "close-unverified-quote.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=NoTimeQuoteProvider(10.6),
            daily_provider=MatchingCloseDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)

    assert result.ready is False
    assert result.plan_id is None


def test_close_workflow_publishes_degraded_plan_when_only_flow_fails(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-flow-degraded.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=FailingFlowProvider(),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)

        run = MarketCaptureRunRepository(connection).get(result.run_id)
        plan = TradingPlanRepository(connection).active_for_day(date(2026, 7, 14))

    assert result.ready is True
    assert plan is not None
    assert plan.data_quality == "degraded"
    assert any("资金流采集失败" in warning for warning in result.warnings)
    assert run is not None
    assert run.status.value == "degraded"
    assert run.failure_count == 1


def test_close_workflow_partial_quote_degrades_plan_and_run(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-partial-quote.db")
    calendar = XSHGTradingCalendar()

    class PartialQuoteProvider(RecordingQuoteProvider):
        def get_quotes(self, symbols):
            return {
                symbol: quote.model_copy(
                    update={
                        "status": QuoteStatus.PARTIAL,
                        "warning": "tradeability fields unavailable",
                    }
                )
                for symbol, quote in super().get_quotes(symbols).items()
            }

    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=PartialQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        plan = TradingPlanRepository(connection).get(result.plan_id)

    assert result.ready is True
    assert run is not None
    assert run.status is CaptureRunStatus.DEGRADED
    assert run.warning_count >= 1
    assert plan is not None
    assert plan.data_quality == "degraded"


def test_close_workflow_requires_explicit_calendar_override(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-calendar-override.db")
    calendar = XSHGTradingCalendar()
    workflow_args = {
        "calendar": calendar,
        "quote_provider": RecordingQuoteProvider(),
        "daily_provider": CalendarDailyProvider(calendar),
        "money_flow_provider": CalendarFlowProvider(calendar),
        "intraday_provider": NoopIntradayProvider(),
        "now": lambda: NOW,
    }
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        workflow = DecisionWorkflow(connection, **workflow_args)
        with pytest.raises(ValueError, match="XSHG trading day"):
            workflow.run_close(date(2026, 7, 12))

        forced = workflow.run_close(date(2026, 7, 12), skip_calendar=True)

    assert forced.run_id == "close-20260712"
    assert forced.ready is False


def test_decision_workflow_exposes_idempotent_backfill_and_cleanup(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "maintenance-workflows.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        )

        backfill = workflow.run_backfill(TRADE_DATE)
        cleanup = workflow.run_cleanup(TRADE_DATE)
        cleanup_again = workflow.run_cleanup(TRADE_DATE)

    assert backfill.status.value == "succeeded"
    assert backfill.requested_symbols == 0
    assert cleanup.run_id == "cleanup-2026-07-13"
    assert cleanup.cleaned_rows == 0
    assert cleanup.reused is False
    assert cleanup_again.reused is True


def test_backfill_reuses_completed_non_empty_cutoff_scope_without_provider_calls(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "backfill-scope-reuse.db")
    calendar = XSHGTradingCalendar()
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)
    metadata = InstrumentMetadata(
        symbol="600000",
        name="浦发银行",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="test-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog([metadata])
        seed_account(connection)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        )

        first = workflow.run_backfill(TRADE_DATE, symbols=[metadata.symbol])
        daily_calls = list(daily_provider.calls)
        flow_calls = list(flow_provider.calls)
        second = workflow.run_backfill(TRADE_DATE, symbols=[metadata.symbol])
        persisted = MarketCaptureRunRepository(connection).get(first.run_id)

    assert first.reused is False
    assert second.reused is True
    assert second.run_id == first.run_id
    assert second.results == first.results
    assert daily_provider.calls == daily_calls
    assert flow_provider.calls == flow_calls
    assert persisted is not None
    assert persisted.requested_symbol_scope == [metadata.symbol]
    assert persisted.lease_expires_at == NOW + timedelta(hours=4)
    assert persisted.provider_calls == first.provider_calls == second.provider_calls == 2
    assert persisted.rows_received == first.rows_received == second.rows_received
    assert persisted.rows_written == first.rows_written == second.rows_written


def test_backfill_retry_refreshes_legacy_scope_and_lease_atomically(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "backfill-retry-provenance.db")
    calendar = XSHGTradingCalendar()
    metadata = InstrumentMetadata(
        symbol="600000",
        name="浦发银行",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="test-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )
    old_started_at = NOW - timedelta(hours=5)
    run_id = "backfill-2026-07-13-a3c25177bb8f"
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog([metadata])
        PositionRepository(connection).add(
            PositionInput(
                symbol=metadata.symbol,
                name=metadata.name,
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=TRADE_DATE,
            ),
            now=NOW,
        )
        run_repository = MarketCaptureRunRepository(connection)
        run_repository.get_or_create(
            MarketCaptureRun(
                run_id=run_id,
                workflow_type="backfill",
                trade_date=TRADE_DATE,
                idempotency_key="market-backfill:2026-07-13:600000",
                status=CaptureRunStatus.FAILED,
                started_at=old_started_at,
                finished_at=old_started_at + timedelta(minutes=1),
                requested_symbols=0,
                requested_symbol_scope=[],
                lease_expires_at=old_started_at + timedelta(hours=4),
                error_summary="legacy failed run",
            )
        )
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        )

        result = workflow.run_backfill(TRADE_DATE, symbols=[metadata.symbol])
        persisted = run_repository.get(run_id)

    assert result.reused is True
    assert persisted is not None
    assert persisted.retry_count == 1
    assert persisted.started_at == NOW
    assert persisted.requested_symbols == 1
    assert persisted.requested_symbol_scope == [metadata.symbol]
    assert persisted.lease_expires_at == NOW + timedelta(hours=4)


def test_backfill_routes_etf_daily_marks_flow_not_applicable_and_skips_unknown(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "mixed-backfill.db")
    calendar = XSHGTradingCalendar()
    a_share_daily = CalendarDailyProvider(calendar)
    etf_daily = CalendarDailyProvider(calendar)
    flow = CalendarFlowProvider(calendar)
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog(
            [
                InstrumentMetadata(
                    symbol="600000",
                    name="A share",
                    exchange=Exchange.SH,
                    instrument_type=InstrumentType.A_SHARE,
                    settlement_cycle=SettlementCycle.T1,
                    price_limit_ratio=0.1,
                    metadata_source="test-directory",
                    metadata_checked_at=NOW,
                    rule_version="test-rules-v1",
                ),
                InstrumentMetadata(
                    symbol="510300",
                    name="ETF",
                    exchange=Exchange.SH,
                    instrument_type=InstrumentType.ETF,
                    settlement_cycle=SettlementCycle.T1,
                    price_limit_ratio=0.1,
                    metadata_source="test-directory",
                    metadata_checked_at=NOW,
                    rule_version="test-rules-v1",
                ),
                InstrumentMetadata(
                    symbol="900001",
                    name="Unknown",
                    exchange=None,
                    instrument_type=InstrumentType.UNKNOWN,
                    settlement_cycle=SettlementCycle.UNKNOWN,
                    metadata_source="legacy-unverified",
                    metadata_checked_at=NOW,
                    rule_version="test-rules-v1",
                ),
            ]
        )
        for symbol in ("600000", "510300", "900001"):
            PositionRepository(connection).add(
                PositionInput(
                    symbol=symbol,
                    name=symbol,
                    quantity=100,
                    available_quantity=100,
                    cost_price=10,
                    opened_at=date(2026, 7, 1),
                ),
                now=NOW,
            )
        summary = MarketCliService(
            connection,
            calendar=calendar,
            daily_provider=a_share_daily,
            etf_daily_provider=etf_daily,
            money_flow_provider=flow,
            now=lambda: NOW,
        ).backfill(TRADE_DATE)

    assert [call[0] for call in a_share_daily.calls] == ["600000"]
    assert [call[0] for call in etf_daily.calls] == ["510300"]
    assert [call[0] for call in flow.calls] == ["600000"]
    assert summary.provider_calls == 3
    by_symbol_dataset = {
        (result.symbol, result.dataset): result for result in summary.results
    }
    assert (
        by_symbol_dataset[("510300", CaptureDataset.MONEY_FLOW)].status
        is CaptureResultStatus.NOT_APPLICABLE
    )
    assert by_symbol_dataset[("900001", CaptureDataset.DAILY_BAR)].status is CaptureResultStatus.FAILED
    assert by_symbol_dataset[("900001", CaptureDataset.MONEY_FLOW)].status is CaptureResultStatus.FAILED


@pytest.mark.parametrize("entrypoint", ["cli", "close"])
def test_legacy_daily_provider_routes_only_real_gap_and_correction_ranges(
    tmp_path,
    entrypoint,
) -> None:
    settings = Settings(database_path=tmp_path / f"legacy-gap-{entrypoint}.db")
    calendar = XSHGTradingCalendar()
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)
    desired = calendar.sessions_ending(TRADE_DATE, 250)
    old_gap = desired[20]
    last_five = desired[-5:]
    metadata = InstrumentMetadata(
        symbol="600000",
        name="浦发银行",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="test-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        instrument_repository = InstrumentRepository(connection)
        instrument_repository.replace_catalog([metadata])
        seed_account(connection)
        for bar in daily_provider.get_daily_bars(
            metadata.symbol,
            desired[0],
            desired[-1],
            "forward",
        ):
            if bar.trade_date != old_gap:
                DailyBarRepository(connection).save(bar)
        daily_provider.calls.clear()

        if entrypoint == "cli":
            summary = MarketCliService(
                connection,
                calendar=calendar,
                daily_provider=daily_provider,
                money_flow_provider=flow_provider,
                now=lambda: NOW,
            ).backfill(TRADE_DATE)
            provider_calls = summary.provider_calls
        else:
            loader = lambda symbols: {
                symbol: loaded
                for symbol in symbols
                if (loaded := instrument_repository.get(symbol)) is not None
            }
            result = DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=RecordingQuoteProvider(),
                daily_provider=daily_provider,
                money_flow_provider=flow_provider,
                intraday_provider=NoopIntradayProvider(),
                instrument_metadata_loader=loader,
                now=lambda: NOW,
            ).run_close(TRADE_DATE)
            run = MarketCaptureRunRepository(connection).get(result.run_id)
            assert run is not None
            provider_calls = run.provider_calls

    assert daily_provider.calls == [
        (metadata.symbol, old_gap, old_gap, "forward"),
        (metadata.symbol, last_five[0], last_five[-1], "forward"),
    ]
    assert provider_calls == (3 if entrypoint == "cli" else 4)


@pytest.mark.parametrize("entrypoint", ["cli", "close"])
def test_reproof_failure_preserves_partial_provider_metrics_and_frozen_history(
    tmp_path,
    entrypoint,
) -> None:
    settings = Settings(database_path=tmp_path / f"reproof-failure-{entrypoint}.db")
    calendar = XSHGTradingCalendar()
    provider = FailingFullReproofDailyProvider(calendar)
    metadata = InstrumentMetadata(
        symbol="510300",
        name="沪深300ETF",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.ETF,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="test-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        instrument_repository = InstrumentRepository(connection)
        instrument_repository.replace_catalog([metadata])
        PositionRepository(connection).add(
            PositionInput(
                symbol=metadata.symbol,
                name=metadata.name,
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=date(2026, 7, 1),
            ),
            now=NOW,
        )
        CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")
        history_repository = HistorySnapshotRepository(connection)
        first = HeavyDataBackfillService(
            calendar=calendar,
            daily_provider=provider,
            money_flow_provider=UnexpectedProvider(),
            daily_repository=DailyBarRepository(connection),
            money_flow_repository=MoneyFlowRepository(connection),
            history_snapshot_repository=history_repository,
            money_flow_snapshot_repository=MoneyFlowSnapshotRepository(connection),
            now=lambda: NOW,
        ).backfill_daily("seed-provider-window", metadata.symbol, TRADE_DATE)
        first_snapshot = first.snapshot
        first_members = history_repository.members(first.snapshot_id)
        fact_count = connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM history_snapshots"
        ).fetchone()[0]
        provider.calls.clear()
        provider.coverage_calls.clear()
        provider.revision_date = TRADE_DATE
        provider.fail_full_reproof = True

        if entrypoint == "cli":
            summary = MarketCliService(
                connection,
                calendar=calendar,
                daily_provider=UnexpectedProvider(),
                etf_daily_provider=provider,
                money_flow_provider=UnexpectedProvider(),
                now=lambda: NOW,
            ).backfill(TRADE_DATE, symbols=[metadata.symbol])
            run = MarketCaptureRunRepository(connection).get(summary.run_id)
            provider_calls = summary.provider_calls
            rows_received = summary.rows_received
            rows_written = summary.rows_written
        else:
            loader = lambda symbols: {
                symbol: loaded
                for symbol in symbols
                if (loaded := instrument_repository.get(symbol)) is not None
            }
            result = DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=UnexpectedProvider(),
                etf_quote_provider=RecordingQuoteProvider(),
                daily_provider=UnexpectedProvider(),
                etf_daily_provider=provider,
                money_flow_provider=UnexpectedProvider(),
                intraday_provider=NoopIntradayProvider(),
                instrument_metadata_loader=loader,
                now=lambda: NOW,
            ).run_close(TRADE_DATE)
            run = MarketCaptureRunRepository(connection).get(result.run_id)
            assert run is not None
            provider_calls = run.provider_calls
            rows_received = run.rows_received
            rows_written = run.rows_written

        final_fact_count = connection.execute(
            "SELECT COUNT(*) FROM daily_bars"
        ).fetchone()[0]
        final_snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM history_snapshots"
        ).fetchone()[0]
        final_snapshot = history_repository.get(first.snapshot_id)
        final_members = history_repository.members(first.snapshot_id)

    desired = calendar.sessions_ending(TRADE_DATE, 250)
    last_five = desired[-5:]
    assert provider.coverage_calls == [
        (metadata.symbol, last_five[0], last_five[-1], "forward"),
        (metadata.symbol, desired[0], desired[-1], "forward"),
    ]
    assert provider_calls == (2 if entrypoint == "cli" else 3)
    assert rows_received == (5 if entrypoint == "cli" else 6)
    assert rows_written == (0 if entrypoint == "cli" else 1)
    assert run is not None
    assert run.failure_count == 1
    assert final_fact_count == fact_count
    assert final_snapshot_count == snapshot_count
    assert final_snapshot == first_snapshot
    assert final_members == first_members


@pytest.mark.parametrize(
    ("provider_kind", "expected_completeness", "coverage_complete"),
    [
        ("coverage", "verified_provider_window", True),
        ("legacy", "unverifiable", False),
    ],
)
def test_market_cli_preserves_optional_daily_coverage_evidence(
    tmp_path,
    provider_kind: str,
    expected_completeness: str,
    coverage_complete: bool,
) -> None:
    settings = Settings(database_path=tmp_path / "market-cli-coverage.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog(
            [
                InstrumentMetadata(
                    symbol="600000",
                    name="A share",
                    exchange=Exchange.SH,
                    instrument_type=InstrumentType.A_SHARE,
                    settlement_cycle=SettlementCycle.T1,
                    price_limit_ratio=0.1,
                    metadata_source="test-directory",
                    metadata_checked_at=NOW,
                    rule_version="test-rules-v1",
                )
            ]
        )
        PositionRepository(connection).add(
            PositionInput(
                symbol="600000",
                name="A share",
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=date(2026, 7, 1),
            ),
            now=NOW,
        )
        summary = MarketCliService(
            connection,
            calendar=calendar,
            daily_provider=(
                TwentyDayCoverageProvider(calendar)
                if provider_kind == "coverage"
                else TwentyDayDailyProvider(calendar)
            ),
            money_flow_provider=CalendarFlowProvider(calendar),
            now=lambda: NOW,
        ).backfill(TRADE_DATE)
        row = connection.execute(
            "SELECT id FROM history_snapshots WHERE symbol=? ORDER BY id DESC LIMIT 1",
            ("600000",),
        ).fetchone()
        history = HistorySnapshotRepository(connection).get(int(row["id"]))

    assert summary.status is CaptureRunStatus.DEGRADED
    assert history is not None
    assert history.completeness == expected_completeness
    if coverage_complete:
        assert history.coverage_evidence is not None
        assert history.coverage_evidence.complete_request_window is True
    else:
        assert history.coverage_evidence is None


def test_market_cli_persists_listing_evidence_for_legacy_short_history(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "market-cli-listing-evidence.db")
    calendar = XSHGTradingCalendar()
    listing_date = calendar.sessions_ending(TRADE_DATE, 20)[0]
    metadata = InstrumentMetadata(
        symbol="600000",
        name="A share",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.1,
        listing_date=listing_date,
        metadata_source="distinctive-listing-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog([metadata])
        PositionRepository(connection).add(
            PositionInput(
                symbol=metadata.symbol,
                name=metadata.name,
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=listing_date,
            ),
            now=NOW,
        )

        summary = MarketCliService(
            connection,
            calendar=calendar,
            daily_provider=TwentyDayDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            now=lambda: NOW,
        ).backfill(TRADE_DATE)
        row = connection.execute(
            "SELECT id FROM history_snapshots WHERE symbol=? ORDER BY id DESC LIMIT 1",
            (metadata.symbol,),
        ).fetchone()
        history = HistorySnapshotRepository(connection).get(int(row["id"]))
        run = MarketCaptureRunRepository(connection).get(summary.run_id)

    assert summary.status is CaptureRunStatus.DEGRADED
    assert run is not None
    assert run.status is CaptureRunStatus.DEGRADED
    assert history is not None
    assert history.row_count == 20
    assert history.completeness == "verified_listing_date"
    assert history.listing_evidence is not None
    assert history.listing_evidence.listing_date == listing_date
    assert history.listing_evidence.source == "distinctive-listing-directory"
    assert history.is_usable(as_of=TRADE_DATE, expected_rows=250)


def test_market_cli_listing_evidence_does_not_hide_internal_history_gap(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "market-cli-listing-gap.db")
    calendar = XSHGTradingCalendar()
    listing_date = calendar.sessions_ending(TRADE_DATE, 21)[0]
    metadata = InstrumentMetadata(
        symbol="600000",
        name="A share",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.1,
        listing_date=listing_date,
        metadata_source="distinctive-listing-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog([metadata])
        PositionRepository(connection).add(
            PositionInput(
                symbol=metadata.symbol,
                name=metadata.name,
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=listing_date,
            ),
            now=NOW,
        )

        MarketCliService(
            connection,
            calendar=calendar,
            daily_provider=TwentyOneDayGapDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            now=lambda: NOW,
        ).backfill(TRADE_DATE)
        row = connection.execute(
            "SELECT id FROM history_snapshots WHERE symbol=? ORDER BY id DESC LIMIT 1",
            (metadata.symbol,),
        ).fetchone()
        history = HistorySnapshotRepository(connection).get(int(row["id"]))

    assert history is not None
    assert history.row_count == 20
    assert history.completeness == "unverifiable"
    assert history.listing_evidence is None
    assert not history.is_usable(as_of=TRADE_DATE, expected_rows=250)


def test_close_accepts_legacy_short_history_with_verified_listing_date(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-listing-evidence.db")
    calendar = XSHGTradingCalendar()
    listing_date = calendar.sessions_ending(TRADE_DATE, 20)[0]
    metadata = InstrumentMetadata(
        symbol="600000",
        name="浦发银行",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.1,
        listing_date=listing_date,
        metadata_source="distinctive-close-listing-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        instrument_repository = InstrumentRepository(connection)
        instrument_repository.replace_catalog([metadata])
        PositionRepository(connection).add(
            PositionInput(
                symbol=metadata.symbol,
                name=metadata.name,
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=listing_date,
            ),
            now=NOW,
        )
        CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")
        loader = lambda symbols: {
            symbol: loaded
            for symbol in symbols
            if (loaded := instrument_repository.get(symbol)) is not None
        }
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=TwentyDayDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            instrument_metadata_loader=loader,
            now=lambda: NOW,
        ).run_close(TRADE_DATE)
        plan = TradingPlanRepository(connection).active_for_day(date(2026, 7, 14))
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        history = HistorySnapshotRepository(connection).get(
            market_input.history_snapshot_refs[metadata.symbol]
        )
        run = MarketCaptureRunRepository(connection).get(result.run_id)

    assert result.ready is True
    assert plan is not None
    assert plan.data_quality == "degraded"
    assert run is not None
    assert run.status is CaptureRunStatus.DEGRADED
    assert history is not None
    assert history.row_count == 20
    assert history.completeness == "verified_listing_date"
    assert history.listing_evidence is not None
    assert history.listing_evidence.source == "distinctive-close-listing-directory"
    assert history.is_usable(as_of=TRADE_DATE, expected_rows=250)


def test_close_listing_evidence_does_not_hide_internal_history_gap(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-listing-gap.db")
    calendar = XSHGTradingCalendar()
    listing_date = calendar.sessions_ending(TRADE_DATE, 21)[0]
    metadata = InstrumentMetadata(
        symbol="600000",
        name="浦发银行",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.1,
        listing_date=listing_date,
        metadata_source="distinctive-close-listing-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        instrument_repository = InstrumentRepository(connection)
        instrument_repository.replace_catalog([metadata])
        PositionRepository(connection).add(
            PositionInput(
                symbol=metadata.symbol,
                name=metadata.name,
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=listing_date,
            ),
            now=NOW,
        )
        CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")
        loader = lambda symbols: {
            symbol: loaded
            for symbol in symbols
            if (loaded := instrument_repository.get(symbol)) is not None
        }
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=TwentyOneDayGapDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            instrument_metadata_loader=loader,
            now=lambda: NOW,
        ).run_close(TRADE_DATE)
        plan = TradingPlanRepository(connection).active_for_day(date(2026, 7, 14))
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        history = HistorySnapshotRepository(connection).get(
            market_input.history_snapshot_refs[metadata.symbol]
        )
        run = MarketCaptureRunRepository(connection).get(result.run_id)

    assert result.ready is False
    assert plan is None
    assert run is not None
    assert run.status is CaptureRunStatus.FAILED
    assert history is not None
    assert history.row_count == 20
    assert history.completeness == "unverifiable"
    assert history.listing_evidence is None


def test_maintenance_workflows_reject_concurrent_active_runs(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "maintenance-concurrent.db")
    calendar = XSHGTradingCalendar()
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)
    with connect(settings) as connection:
        migrate(connection)
        repository = MarketCaptureRunRepository(connection)
        repository.get_or_create(
            MarketCaptureRun(
                run_id="backfill-2026-07-13-e3b0c44298fc",
                workflow_type="backfill",
                trade_date=TRADE_DATE,
                idempotency_key="market-backfill:2026-07-13:",
                status=CaptureRunStatus.RUNNING,
                started_at=NOW,
            )
        )
        repository.get_or_create(
            MarketCaptureRun(
                run_id="cleanup-2026-07-13",
                workflow_type="cleanup",
                trade_date=TRADE_DATE,
                idempotency_key="market-cleanup:2026-07-13",
                status=CaptureRunStatus.RUNNING,
                started_at=NOW,
            )
        )
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        )

        with pytest.raises(CaptureRunAlreadyActiveError):
            workflow.run_backfill(TRADE_DATE)
        with pytest.raises(CaptureRunAlreadyActiveError):
            workflow.run_cleanup(TRADE_DATE)

    assert daily_provider.calls == []
    assert flow_provider.calls == []


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class ClockQuoteProvider:
    def __init__(self, clock: MutableClock) -> None:
        self.clock = clock
        self.calls: list[list[str]] = []

    def get_quotes(self, symbols):
        self.calls.append(list(symbols))
        return {
            symbol: QuoteSnapshot(
                symbol=symbol,
                name="浦发银行",
                previous_close=10.2,
                open_price=10.2,
                high_price=11.2,
                low_price=10.1,
                current_price=11.1,
                change_pct=(11.1 / 10.2 - 1) * 100,
                volume=2_000_000,
                amount=22_000_000,
                trading_status=TradingStatus.NORMAL,
                limit_status=LimitStatus.NONE,
                data_time=self.clock.value,
                fetched_at=self.clock.value,
                source="fake",
                status=QuoteStatus.OK,
            )
            for symbol in symbols
        }


class RisingIntradayProvider:
    def __init__(self, calendar: XSHGTradingCalendar, clock: MutableClock) -> None:
        self.calendar = calendar
        self.clock = clock
        self.calls = []

    def get_minute_bars(self, symbol, trade_date, interval):
        self.calls.append((symbol, trade_date, interval))
        session = self.calendar.session(trade_date)
        return [
            MinuteBar(
                symbol=symbol,
                trade_date=trade_date,
                minute=session.open_at + timedelta(minutes=index + 1),
                open=10.2 + index * 0.04,
                high=10.24 + index * 0.04,
                low=10.18 + index * 0.04,
                close=10.2 + index * 0.04,
                volume=200 if index >= 25 else 100,
                amount=(200 if index >= 25 else 100) * (10.2 + index * 0.04),
                source="fake",
                fetched_at=self.clock.value,
            )
            for index in range(30)
        ]


class OldIntradayProvider(RisingIntradayProvider):
    def get_minute_bars(self, symbol, trade_date, interval):
        return super().get_minute_bars(symbol, trade_date, interval)[:10]


class FailingIntradayProvider:
    def get_minute_bars(self, symbol, trade_date, interval):
        raise MarketProviderError("synthetic minute outage")


class TwentyDayDailyProvider(CalendarDailyProvider):
    def get_daily_bars(self, symbol, start_date, end_date, adjustment):
        return super().get_daily_bars(symbol, start_date, end_date, adjustment)[-20:]


class TwentyDayCoverageProvider(TwentyDayDailyProvider):
    def get_daily_bars_with_coverage(
        self,
        symbol,
        start_date,
        end_date,
        adjustment,
    ) -> DailyBarFetchResult:
        bars = tuple(self.get_daily_bars(symbol, start_date, end_date, adjustment))
        return DailyBarFetchResult(
            bars=bars,
            coverage_evidence=DailyBarCoverageEvidence(
                requested_start=start_date,
                requested_end=end_date,
                observed_start=start_date,
                observed_end=end_date,
                earliest_available_date=bars[0].trade_date,
                complete_request_window=True,
                source="fake_daily_full_window",
            ),
        )


class TwentyOneDayGapDailyProvider(CalendarDailyProvider):
    def get_daily_bars(self, symbol, start_date, end_date, adjustment):
        bars = super().get_daily_bars(symbol, start_date, end_date, adjustment)[-21:]
        return [*bars[:10], *bars[11:]]


def save_suspended_provider_history(
    connection,
    *,
    calendar: XSHGTradingCalendar,
    symbol: str,
    cutoff: date,
) -> tuple[int, HistorySnapshot, list[int]]:
    member_days = calendar.sessions_ending(cutoff, 21)[:-1]
    daily = DailyBarRepository(connection)
    member_ids = [
        daily.save(
            DailyBar(
                symbol=symbol,
                trade_date=trade_day,
                open=10,
                high=11,
                low=9,
                close=10 + index * 0.001,
                volume=100_000,
                amount=1_000_000,
                source="provider-frozen",
                fetched_at=NOW,
            )
        )
        for index, trade_day in enumerate(member_days)
    ]
    requested_start = calendar.sessions_ending(cutoff, 250)[0]
    evidence = DailyBarCoverageEvidence(
        requested_start=requested_start,
        requested_end=cutoff,
        observed_start=requested_start,
        observed_end=cutoff,
        earliest_available_date=member_days[0],
        complete_request_window=True,
        source="provider-full-window",
    )
    members = [daily.get(member_id) for member_id in member_ids]
    snapshot = HistorySnapshot(
        run_id="backfill-provider-frozen",
        symbol=symbol,
        data_start=member_days[0],
        data_end=member_days[-1],
        row_count=len(member_ids),
        content_digest=content_digest([member.bar.content_hash for member in members]),
        status=CaptureResultStatus.DEGRADED,
        warning="provider observed the complete request window",
        fetched_at=NOW,
        completeness="verified_provider_window",
        coverage_evidence=evidence,
    )
    snapshot_id = HistorySnapshotRepository(connection).save(snapshot, member_ids)
    return snapshot_id, snapshot, member_ids


def test_intraday_without_plan_reuses_frozen_etf_history_snapshot(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-local-etf-history.db")
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    metadata = etf_name_variant_metadata(now=clock.value, trade_date=cutoff)
    with connect(settings) as connection:
        migrate(connection)
        instrument_repository = InstrumentRepository(connection)
        instrument_repository.replace_catalog([metadata])
        PositionRepository(connection).add(
            PositionInput(
                symbol=metadata.symbol,
                name=metadata.name,
                quantity=1000,
                available_quantity=1000,
                cost_price=7,
                opened_at=date(2026, 7, 1),
            ),
            now=NOW,
        )
        CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")
        source_id, source_snapshot, source_member_ids = save_suspended_provider_history(
            connection,
            calendar=calendar,
            symbol=metadata.symbol,
            cutoff=cutoff,
        )
        original_members = HistorySnapshotRepository(connection).members(source_id)
        corrected = original_members[5].bar.model_copy(
            update={"close": 10.5, "amount": original_members[5].bar.volume * 10.5}
        )
        DailyBarRepository(connection).save(corrected)
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM history_snapshots"
        ).fetchone()[0]
        loader = lambda symbols: {
            symbol: loaded
            for symbol in symbols
            if (loaded := instrument_repository.get(symbol)) is not None
        }

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=UnexpectedProvider(),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=NoopIntradayProvider(),
            etf_quote_provider=ClockQuoteProvider(clock),
            etf_daily_provider=UnexpectedProvider(),
            etf_intraday_provider=RisingIntradayProvider(calendar, clock),
            instrument_metadata_loader=loader,
            now=clock.now,
        ).run_intraday()
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        capture_results = {
            item.dataset: item
            for item in MarketCaptureResultRepository(connection).list_for_run(result.run_id)
            if item.symbol == metadata.symbol
        }
        members_after = HistorySnapshotRepository(connection).members(source_id)
        snapshot_after = HistorySnapshotRepository(connection).get(source_id)
        snapshot_count_after = connection.execute(
            "SELECT COUNT(*) FROM history_snapshots"
        ).fetchone()[0]

    assert market_input is not None
    assert market_input.history_snapshot_refs[metadata.symbol] == source_id
    assert market_input.effective_trade_date == clock.value.date()
    assert market_input.history_cutoff_date == cutoff
    quality = market_input.dataset_quality[metadata.symbol][CaptureDataset.DAILY_BAR]
    assert quality.status is CaptureResultStatus.DEGRADED
    assert quality.data_start == source_snapshot.data_start
    assert quality.data_end == source_snapshot.data_end
    assert quality.expected_rows == 250
    assert quality.actual_rows == source_snapshot.row_count
    assert quality.source == "local_history_snapshot"
    daily_result = capture_results[CaptureDataset.DAILY_BAR]
    assert daily_result.status is CaptureResultStatus.DEGRADED
    assert daily_result.data_start == source_snapshot.data_start
    assert daily_result.data_end == source_snapshot.data_end
    assert daily_result.expected_rows == 250
    assert daily_result.actual_rows == source_snapshot.row_count
    assert daily_result.source == "local_history_snapshot"
    assert daily_result.fetched_at == source_snapshot.fetched_at
    assert run is not None
    assert run.effective_trade_date == clock.value.date()
    assert run.history_cutoff_date == cutoff
    assert run.provider_calls == 2
    assert source_snapshot.data_end < cutoff
    assert snapshot_after == source_snapshot
    assert members_after == original_members
    assert [member.id for member in original_members] == source_member_ids
    assert snapshot_count_after == snapshot_count


def test_intraday_without_plan_materializes_verified_listing_history(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-local-listing-history.db")
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    listing_date = calendar.sessions_ending(cutoff, 20)[0]
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    metadata = InstrumentMetadata(
        symbol="600000",
        name="浦发银行",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.1,
        listing_date=listing_date,
        metadata_source="distinctive-intraday-listing-directory",
        metadata_checked_at=clock.value,
        rule_version="test-rules-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        instrument_repository = InstrumentRepository(connection)
        instrument_repository.replace_catalog([metadata])
        PositionRepository(connection).add(
            PositionInput(
                symbol=metadata.symbol,
                name=metadata.name,
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=listing_date,
            ),
            now=NOW,
        )
        CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")
        for bar in TwentyDayDailyProvider(calendar).get_daily_bars(
            metadata.symbol,
            calendar.sessions_ending(cutoff, 250)[0],
            cutoff,
            "forward",
        ):
            DailyBarRepository(connection).save(bar)
        loader = lambda symbols: {
            symbol: loaded
            for symbol in symbols
            if (loaded := instrument_repository.get(symbol)) is not None
        }

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            instrument_metadata_loader=loader,
            now=clock.now,
        ).run_intraday()
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        history_id = market_input.history_snapshot_refs[metadata.symbol]
        history = HistorySnapshotRepository(connection).get(history_id)
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        daily_result = next(
            item
            for item in MarketCaptureResultRepository(connection).list_for_run(result.run_id)
            if item.symbol == metadata.symbol and item.dataset is CaptureDataset.DAILY_BAR
        )

    assert history is not None
    assert history.run_id == result.run_id
    assert history.data_start == listing_date
    assert history.data_end == cutoff
    assert history.fetched_at == clock.value
    assert history.completeness == "verified_listing_date"
    assert history.listing_evidence is not None
    assert history.listing_evidence.listing_date == listing_date
    assert history.listing_evidence.source == metadata.metadata_source
    assert history.is_usable(as_of=cutoff, expected_rows=250)
    quality = market_input.dataset_quality[metadata.symbol][CaptureDataset.DAILY_BAR]
    assert quality.status is CaptureResultStatus.DEGRADED
    assert quality.actual_rows == 20
    assert quality.source == "local_history_snapshot"
    assert daily_result.status is CaptureResultStatus.DEGRADED
    assert daily_result.actual_rows == 20
    assert daily_result.source == "local_history_snapshot"
    assert daily_result.fetched_at == history.fetched_at
    assert market_input.effective_trade_date == clock.value.date()
    assert market_input.history_cutoff_date == cutoff
    assert run is not None
    assert run.effective_trade_date == clock.value.date()
    assert run.history_cutoff_date == cutoff
    assert run.provider_calls == 2


def test_intraday_without_plan_persists_failed_empty_history_reference(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-local-empty-history.db")
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        history_id = market_input.history_snapshot_refs["600000"]
        history = HistorySnapshotRepository(connection).get(history_id)
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        daily_result = next(
            item
            for item in MarketCaptureResultRepository(connection).list_for_run(result.run_id)
            if item.symbol == "600000" and item.dataset is CaptureDataset.DAILY_BAR
        )

    assert history is not None
    assert history.row_count == 0
    assert history.data_start is None
    assert history.data_end is None
    assert history.fetched_at == clock.value
    assert history.status is CaptureResultStatus.DEGRADED
    assert history.completeness == "unverifiable"
    assert history.listing_evidence is None
    assert not history.is_usable(as_of=cutoff, expected_rows=250)
    quality = market_input.dataset_quality["600000"][CaptureDataset.DAILY_BAR]
    assert quality.status is CaptureResultStatus.FAILED
    assert quality.actual_rows == 0
    assert quality.source == "local_history_snapshot"
    assert quality.warning == history.warning
    assert daily_result.status is CaptureResultStatus.FAILED
    assert daily_result.expected_rows == 250
    assert daily_result.actual_rows == 0
    assert daily_result.source == "local_history_snapshot"
    assert daily_result.warning == history.warning
    assert daily_result.fetched_at == history.fetched_at
    assert market_input.effective_trade_date == clock.value.date()
    assert market_input.history_cutoff_date == cutoff
    assert run is not None
    assert run.effective_trade_date == clock.value.date()
    assert run.history_cutoff_date == cutoff
    assert run.provider_calls == 2


@pytest.mark.parametrize(
    (
        "stored_effective_trade_date",
        "stored_history_cutoff_date",
        "expected_effective_trade_date",
        "expected_history_cutoff_date",
    ),
    [
        (None, None, date(2026, 7, 14), date(2026, 7, 13)),
        (
            date(2026, 7, 13),
            date(2026, 7, 10),
            date(2026, 7, 13),
            date(2026, 7, 10),
        ),
    ],
    ids=("fills_missing_provenance", "preserves_existing_provenance"),
)
def test_intraday_retry_persists_legacy_run_provenance(
    tmp_path,
    stored_effective_trade_date,
    stored_history_cutoff_date,
    expected_effective_trade_date,
    expected_history_cutoff_date,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-legacy-retry.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    period_start = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        MarketCaptureRunRepository(connection).get_or_create(
            MarketCaptureRun(
                run_id="intraday-20260714-1000",
                workflow_type="intraday",
                trade_date=date(2026, 7, 14),
                effective_trade_date=stored_effective_trade_date,
                history_cutoff_date=stored_history_cutoff_date,
                period_start=period_start,
                period_end=period_start + timedelta(minutes=3),
                idempotency_key="intraday:2026-07-14:1000",
                status=CaptureRunStatus.FAILED,
                started_at=period_start - timedelta(minutes=1),
                finished_at=period_start - timedelta(seconds=30),
                failure_count=1,
                error_summary="legacy failure",
            )
        )

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )

    assert run is not None
    assert run.retry_count == 1
    assert run.effective_trade_date == expected_effective_trade_date
    assert run.history_cutoff_date == expected_history_cutoff_date
    assert market_input is not None
    assert market_input.effective_trade_date == expected_effective_trade_date
    assert market_input.history_cutoff_date == expected_history_cutoff_date


def test_intraday_active_plan_keeps_frozen_history_and_materializes_external_symbol(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-plan-mixed-history.db")
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    clock = MutableClock(NOW)
    metadata = [
        InstrumentMetadata(
            symbol="600000",
            name="浦发银行",
            exchange=Exchange.SH,
            instrument_type=InstrumentType.A_SHARE,
            settlement_cycle=SettlementCycle.T1,
            price_limit_ratio=0.10,
            metadata_source="test-directory",
            metadata_checked_at=NOW,
            rule_version="test-rules-v1",
        ),
        InstrumentMetadata(
            symbol="000001",
            name="平安银行",
            exchange=Exchange.SZ,
            instrument_type=InstrumentType.A_SHARE,
            settlement_cycle=SettlementCycle.T1,
            price_limit_ratio=0.10,
            metadata_source="test-directory",
            metadata_checked_at=NOW,
            rule_version="test-rules-v1",
        ),
    ]
    with connect(settings) as connection:
        migrate(connection)
        instrument_repository = InstrumentRepository(connection)
        instrument_repository.replace_catalog(metadata)
        loader = lambda symbols: {
            symbol: loaded
            for symbol in symbols
            if (loaded := instrument_repository.get(symbol)) is not None
        }
        seed_account(connection)
        close = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            instrument_metadata_loader=loader,
            now=clock.now,
        ).run_close(cutoff)
        close_input = MarketInputSnapshotRepository(connection).get(
            close.market_input_snapshot_id
        )
        assert close_input is not None
        frozen_plan_history_id = close_input.history_snapshot_refs["600000"]
        frozen_plan_history = HistorySnapshotRepository(connection).get(
            frozen_plan_history_id
        )
        newer_plan_history_id, newer_plan_history, _ = save_suspended_provider_history(
            connection,
            calendar=calendar,
            symbol="600000",
            cutoff=cutoff,
        )
        assert newer_plan_history.is_usable(as_of=cutoff, expected_rows=250)
        external_daily = CalendarDailyProvider(calendar)
        for bar in external_daily.get_daily_bars(
            "000001",
            calendar.sessions_ending(cutoff, 250)[0],
            cutoff,
            "forward",
        ):
            DailyBarRepository(connection).save(bar)
        WatchPinnedRepository(connection).upsert(
            WatchPinnedInput(
                symbol="000001",
                name="平安银行",
                rank=1,
                plan_enabled=True,
            ),
            source=WatchPinnedSource.MANUAL,
            now=clock.value,
        )
        snapshot_count_before = connection.execute(
            "SELECT COUNT(*) FROM history_snapshots"
        ).fetchone()[0]

        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            instrument_metadata_loader=loader,
            now=clock.now,
        ).run_intraday()
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        assert market_input is not None
        external_history_id = market_input.history_snapshot_refs.get("000001")
        external_history = (
            None
            if external_history_id is None
            else HistorySnapshotRepository(connection).get(external_history_id)
        )
        capture_results = MarketCaptureResultRepository(connection).list_for_run(
            result.run_id
        )
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        snapshot_count_after = connection.execute(
            "SELECT COUNT(*) FROM history_snapshots"
        ).fetchone()[0]

    assert newer_plan_history_id != frozen_plan_history_id
    assert market_input.history_snapshot_refs["600000"] == frozen_plan_history_id
    assert frozen_plan_history is not None
    assert external_history_id is not None
    assert external_history is not None
    assert external_history_id not in {frozen_plan_history_id, newer_plan_history_id}
    assert snapshot_count_after == snapshot_count_before + 1
    frozen_quality = market_input.dataset_quality["600000"][CaptureDataset.DAILY_BAR]
    assert frozen_quality.status is CaptureResultStatus.COMPLETE
    assert frozen_quality.source == "frozen_plan_history_snapshot"
    assert frozen_quality.data_start == frozen_plan_history.data_start
    assert frozen_quality.data_end == frozen_plan_history.data_end
    assert frozen_quality.expected_rows == 250
    assert frozen_quality.actual_rows == frozen_plan_history.row_count == 250
    external_quality = market_input.dataset_quality["000001"][CaptureDataset.DAILY_BAR]
    assert external_quality.status is CaptureResultStatus.COMPLETE
    assert external_quality.source == "local_history_snapshot"
    assert external_quality.data_start == external_history.data_start
    assert external_quality.data_end == external_history.data_end
    assert external_quality.expected_rows == 250
    assert external_quality.actual_rows == external_history.row_count == 250
    daily_results = {
        item.symbol: item
        for item in capture_results
        if item.dataset is CaptureDataset.DAILY_BAR
    }
    assert set(daily_results) == {"000001", "600000"}
    assert daily_results["000001"].status is CaptureResultStatus.COMPLETE
    assert daily_results["000001"].source == "local_history_snapshot"
    assert daily_results["000001"].fetched_at == clock.value
    assert daily_results["000001"].data_start == external_history.data_start
    assert daily_results["000001"].data_end == external_history.data_end
    assert daily_results["000001"].expected_rows == 250
    assert daily_results["000001"].actual_rows == 250
    assert daily_results["600000"].status is CaptureResultStatus.COMPLETE
    assert daily_results["600000"].source == "frozen_plan_history_snapshot"
    assert daily_results["600000"].expected_rows == 250
    assert daily_results["600000"].actual_rows == 250
    assert daily_results["600000"].data_start == frozen_plan_history.data_start
    assert daily_results["600000"].data_end == frozen_plan_history.data_end
    assert daily_results["600000"].fetched_at == frozen_plan_history.fetched_at
    assert market_input.effective_trade_date == date(2026, 7, 14)
    assert market_input.history_cutoff_date == cutoff
    assert run is not None
    assert run.effective_trade_date == date(2026, 7, 14)
    assert run.history_cutoff_date == cutoff
    assert run.provider_calls == 3


@pytest.mark.parametrize(
    "broken_reference",
    ["missing", "dangling", "corrupt_members"],
)
def test_intraday_active_plan_does_not_replace_invalid_frozen_history(
    tmp_path,
    broken_reference,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-invalid-plan-history.db")
    calendar = XSHGTradingCalendar()
    cutoff = date(2026, 7, 13)
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        close = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(cutoff)
        close_input_repository = MarketInputSnapshotRepository(connection)
        close_input = close_input_repository.get(close.market_input_snapshot_id)
        assert close_input is not None
        frozen_history_id = close_input.history_snapshot_refs["600000"]
        if broken_reference == "missing":
            broken_input = close_input.model_copy(update={"history_snapshot_refs": {}})
            connection.execute(
                "UPDATE market_input_snapshots SET payload_json=? WHERE id=?",
                (broken_input.model_dump_json(), close.market_input_snapshot_id),
            )
        elif broken_reference == "dangling":
            broken_input = close_input.model_copy(
                update={"history_snapshot_refs": {"600000": 999_999}}
            )
            connection.execute(
                "UPDATE market_input_snapshots SET payload_json=? WHERE id=?",
                (broken_input.model_dump_json(), close.market_input_snapshot_id),
            )
        else:
            connection.execute(
                "DELETE FROM history_snapshot_members "
                "WHERE snapshot_id=? AND sequence=0",
                (frozen_history_id,),
            )
        connection.commit()
        if broken_reference == "corrupt_members":
            with pytest.raises(
                sqlite3.IntegrityError,
                match="members do not match payload",
            ):
                HistorySnapshotRepository(connection).latest_usable_for_symbol(
                    "600000",
                    as_of=cutoff,
                    expected_rows=250,
                )
        newer_history_id, newer_history, _ = save_suspended_provider_history(
            connection,
            calendar=calendar,
            symbol="600000",
            cutoff=cutoff,
        )
        assert newer_history.is_usable(as_of=cutoff, expected_rows=250)

        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        market_input = close_input_repository.get(result.market_input_snapshot_id)
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        daily_results = {
            item.symbol: item
            for item in MarketCaptureResultRepository(connection).list_for_run(
                result.run_id
            )
            if item.dataset is CaptureDataset.DAILY_BAR
        }

    assert market_input is not None
    assert "600000" not in market_input.history_snapshot_refs
    assert newer_history_id not in market_input.history_snapshot_refs.values()
    quality = market_input.dataset_quality["600000"][CaptureDataset.DAILY_BAR]
    assert quality.status is CaptureResultStatus.FAILED
    assert quality.source == "frozen_plan_history_snapshot"
    assert "frozen plan history" in quality.warning
    assert quality.data_start is None
    assert quality.data_end is None
    assert quality.expected_rows == 250
    assert quality.actual_rows == 0
    assert "600000" in daily_results
    daily_result = daily_results["600000"]
    assert daily_result.status is CaptureResultStatus.FAILED
    assert daily_result.source == "frozen_plan_history_snapshot"
    assert daily_result.warning == quality.warning
    assert daily_result.data_start is None
    assert daily_result.data_end is None
    assert daily_result.expected_rows == 250
    assert daily_result.actual_rows == 0
    assert daily_result.fetched_at == clock.value
    assert market_input.effective_trade_date == date(2026, 7, 14)
    assert market_input.history_cutoff_date == cutoff
    assert run is not None
    assert run.effective_trade_date == date(2026, 7, 14)
    assert run.history_cutoff_date == cutoff
    assert run.provider_calls == 2


class PartialAccountQuoteProvider(ClockQuoteProvider):
    def get_quotes(self, symbols):
        quotes = super().get_quotes(symbols)
        quotes["000001"] = quotes["000001"].model_copy(
            update={
                "status": QuoteStatus.FAILED,
                "warning": "synthetic quote outage",
            }
        )
        return quotes


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls = []
        self.daily_summaries = []

    def dispatch_recommendation(self, recommendation, *, plan_version, now):
        self.calls.append((recommendation, plan_version, now))
        return SimpleNamespace(
            created=True,
            email_delivery=SimpleNamespace(delivery_id="delivery-1"),
            warnings=(),
        )

    def dispatch_daily_summary(
        self,
        *,
        plan_id,
        plan_version,
        recommendations,
        now,
    ):
        self.daily_summaries.append((plan_id, plan_version, recommendations, now))
        return SimpleNamespace(delivery_id="daily-summary-1")


def test_close_workflow_dispatches_one_versioned_daily_summary(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-summary.db")
    calendar = XSHGTradingCalendar()
    dispatcher = RecordingDispatcher()
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(MutableClock(NOW)),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
            notification_dispatcher=dispatcher,
        ).run_close(TRADE_DATE)
        run = MarketCaptureRunRepository(connection).get(result.run_id)

    assert result.ready is True
    assert len(dispatcher.daily_summaries) == 1
    plan_id, plan_version, recommendations, dispatched_at = dispatcher.daily_summaries[
        0
    ]
    assert plan_id == result.plan_id
    assert plan_version == 1
    assert recommendations == []
    assert dispatched_at == NOW
    assert run is not None
    assert run.plan_count == 1
    assert run.email_outbox_count == 1


def test_intraday_workflow_consumes_plan_and_is_idempotent_per_three_minute_cycle(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-workflow.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    close_quotes = ClockQuoteProvider(clock)
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)

    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=close_quotes,
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)

        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
        intraday_quotes = ClockQuoteProvider(clock)
        minute_provider = RisingIntradayProvider(calendar, clock)
        dispatcher = RecordingDispatcher()
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=intraday_quotes,
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=minute_provider,
            now=clock.now,
            notification_dispatcher=dispatcher,
        )

        first = workflow.run_intraday()
        second = workflow.run_intraday()

        recommendations = RecommendationRepository(connection).list(limit=20)
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            "600000"
        )
        market_input = MarketInputSnapshotRepository(connection).get(
            first.market_input_snapshot_id
        )
        run = MarketCaptureRunRepository(connection).get(first.run_id)

    assert first.reused is False
    assert second.reused is True
    assert second.recommendation_ids == first.recommendation_ids
    assert intraday_quotes.calls == [["600000"]]
    assert minute_provider.calls == [("600000", date(2026, 7, 14), "1m")]
    assert len(recommendations) == 1
    assert recommendations[0].action is RecommendationAction.ADD
    assert recommendations[0].run_id == first.run_id
    assert recommendations[0].fetched_at == market_input.fetched_at
    quote_reference = recommendations[0].data_references["quote"]
    assert quote_reference["source"] == "fake"
    assert quote_reference["data_time"] == clock.value.isoformat()
    history_reference = recommendations[0].data_references["history"]
    assert history_reference["source"] == "frozen_plan_history_snapshot"
    assert history_reference["data_start"]
    assert history_reference["data_end"] == TRADE_DATE.isoformat()
    assert recommendations[0].data_references["money_flow"]["source"] == (
        "money_flow_provider"
    )
    assert datetime.fromisoformat(
        recommendations[0].data_references["intraday"]["data_time"]
    ) == clock.value
    assert strength is not None
    assert strength.label.value == "strong"
    assert strength.thresholds["stale_minutes"] == 6
    assert market_input is not None
    assert market_input.intraday_strength_snapshot_refs["600000"] > 0
    assert market_input.thresholds["stale_trading_minutes"] == 6
    assert len(dispatcher.calls) == 2
    assert (
        dispatcher.calls[1][0].recommendation_id
        == dispatcher.calls[0][0].recommendation_id
    )
    assert run is not None
    assert run.provider_calls == 2
    assert run.recommendation_count == 1
    assert run.notification_count == 1
    assert run.email_outbox_count == 1


def test_intraday_captures_enabled_watch_without_creating_decision_outputs(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-display-only.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    metadata = InstrumentMetadata(
        symbol="000001",
        name="平安银行",
        exchange=Exchange.SZ,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="test-directory",
        metadata_checked_at=clock.value,
        rule_version="test-rules-v1",
    )
    quote_provider = ClockQuoteProvider(clock)
    minute_provider = RisingIntradayProvider(calendar, clock)

    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog([metadata])
        WatchPinnedRepository(connection).upsert(
            WatchPinnedInput(
                symbol=metadata.symbol,
                name=metadata.name,
                rank=1,
                plan_enabled=True,
            ),
            source=WatchPinnedSource.MANUAL,
            now=clock.value,
        )
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=quote_provider,
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=minute_provider,
            instrument_metadata_loader=lambda symbols: {
                symbol: metadata for symbol in symbols
            },
            now=clock.now,
        ).run_intraday()
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        account_snapshot_count = connection.execute(
            "SELECT COUNT(*) AS count FROM account_snapshots"
        ).fetchone()["count"]

    assert quote_provider.calls == [[metadata.symbol]]
    assert minute_provider.calls == [(metadata.symbol, clock.value.date(), "1m")]
    assert market_input is not None
    assert market_input.quote_snapshot_refs[metadata.symbol] > 0
    assert market_input.intraday_strength_snapshot_refs[metadata.symbol] > 0
    assert result.recommendation_ids == ()
    assert account_snapshot_count == 0
    assert run is not None
    assert run.requested_symbols == 1
    assert run.processed_symbols == 1
    assert run.provider_calls == 2
    assert run.recommendation_count == 0
    assert run.notification_count == 0


def test_intraday_workflow_routes_etf_metadata_into_recommendation(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-etf-workflow.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    metadata = etf_name_variant_metadata(now=NOW, trade_date=TRADE_DATE)
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)

    with connect(settings) as connection:
        migrate(connection)
        instrument_repository = InstrumentRepository(connection)
        instrument_repository.replace_catalog([metadata])
        loader = lambda symbols: {
            symbol: loaded
            for symbol in symbols
            if (loaded := instrument_repository.get(symbol)) is not None
        }
        PositionRepository(connection).add(
            PositionInput(
                symbol="512480",
                name="半导体ETF国联安",
                quantity=1000,
                available_quantity=1000,
                cost_price=7.0,
                opened_at=date(2026, 7, 1),
            ),
            now=datetime(2026, 7, 12, 8, 0, tzinfo=UTC),
        )
        CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")
        close_quote_provider = ClockQuoteProvider(clock)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=UnexpectedProvider(),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            etf_quote_provider=close_quote_provider,
            etf_daily_provider=daily_provider,
            etf_intraday_provider=NoopIntradayProvider(),
            instrument_metadata_loader=loader,
            now=clock.now,
        ).run_close(TRADE_DATE)

        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
        minute_provider = RisingIntradayProvider(calendar, clock)
        intraday_quote_provider = ClockQuoteProvider(clock)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=UnexpectedProvider(),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            etf_quote_provider=intraday_quote_provider,
            etf_daily_provider=daily_provider,
            etf_intraday_provider=minute_provider,
            instrument_metadata_loader=loader,
            now=clock.now,
        ).run_intraday()
        recommendation = RecommendationRepository(connection).list(limit=1)[0]
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )

    assert close_quote_provider.calls == [["512480"]]
    assert intraday_quote_provider.calls == [["512480"]]
    assert minute_provider.calls == [("512480", date(2026, 7, 14), "1m")]
    assert flow_provider.calls == []
    assert recommendation.instrument == metadata
    assert recommendation.data_references["money_flow"]["status"] == "not_applicable"
    assert recommendation.data_quality["overall"] == "complete"
    assert market_input is not None
    assert market_input.instrument_metadata["512480"] == metadata


@pytest.mark.parametrize("policy_failure", ["provider_missing", "settlement_unknown"])
def test_intraday_etf_policy_failure_does_not_reuse_minute_cache(
    tmp_path,
    policy_failure,
) -> None:
    settings = Settings(
        database_path=tmp_path / f"intraday-etf-policy-{policy_failure}.db"
    )
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    metadata = etf_name_variant_metadata(
        now=clock.value,
        trade_date=date(2026, 7, 13),
    )
    if policy_failure == "settlement_unknown":
        metadata = metadata.model_copy(
            update={"settlement_cycle": SettlementCycle.UNKNOWN}
        )
    with connect(settings) as connection:
        migrate(connection)
        instrument_repository = InstrumentRepository(connection)
        instrument_repository.replace_catalog([metadata])
        PositionRepository(connection).add(
            PositionInput(
                symbol=metadata.symbol,
                name=metadata.name,
                quantity=1000,
                available_quantity=1000,
                cost_price=1.0,
                opened_at=date(2026, 7, 1),
            ),
            now=NOW,
        )
        CashAccountRepository(connection).initialize(50_000, now=NOW, note="initial")
        cached = RisingIntradayProvider(calendar, clock).get_minute_bars(
            metadata.symbol,
            date(2026, 7, 14),
            "1m",
        )
        MinuteBarRepository(connection).upsert_many(cached)
        loader = lambda symbols: {
            symbol: loaded
            for symbol in symbols
            if (loaded := instrument_repository.get(symbol)) is not None
        }
        configured_minute_provider = (
            None
            if policy_failure == "provider_missing"
            else RisingIntradayProvider(calendar, clock)
        )

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=UnexpectedProvider(),
            daily_provider=UnexpectedProvider(),
            money_flow_provider=UnexpectedProvider(),
            intraday_provider=NoopIntradayProvider(),
            etf_quote_provider=ClockQuoteProvider(clock),
            etf_daily_provider=UnexpectedProvider(),
            etf_intraday_provider=(
                configured_minute_provider
            ),
            instrument_metadata_loader=loader,
            now=clock.now,
        ).run_intraday()
        captures = {
            item.dataset: item
            for item in MarketCaptureResultRepository(connection).list_for_run(
                result.run_id
            )
            if item.symbol == metadata.symbol
        }
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            metadata.symbol
        )
        run = MarketCaptureRunRepository(connection).get(result.run_id)

    assert captures[CaptureDataset.MINUTE_BAR].status is CaptureResultStatus.FAILED
    assert captures[CaptureDataset.INTRADAY_STRENGTH].status is CaptureResultStatus.FAILED
    expected_error = (
        "not configured"
        if policy_failure == "provider_missing"
        else "settlement"
    )
    assert expected_error in captures[CaptureDataset.MINUTE_BAR].error_summary
    assert "reused same-day minute_cache" not in captures[
        CaptureDataset.MINUTE_BAR
    ].error_summary
    assert market_input is not None
    assert (
        market_input.dataset_quality[metadata.symbol][CaptureDataset.MINUTE_BAR].status
        is CaptureResultStatus.FAILED
    )
    assert strength is None
    assert run is not None
    assert run.provider_calls == 1
    if configured_minute_provider is not None:
        assert configured_minute_provider.calls == []


def test_intraday_reuse_repairs_failed_notification_projections_idempotently(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(
        database_path=tmp_path / "intraday-projection-recovery.db",
        log_dir=tmp_path / "logs",
    )
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)

    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        SmtpSettingsService(SmtpSettingsRepository(connection)).update(
            SmtpSettingsUpdate(
                host="smtp.example.test",
                port=587,
                sender="robot@example.test",
                recipient="owner@example.test",
                enabled=True,
            ),
            now=NOW,
        )

        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
        dispatcher = build_notification_dispatcher(connection, settings)
        real_write = dispatcher.jsonl_writer.write
        write_calls = 0

        def fail_first_write(summary, recommendation, audit_ref):  # noqa: ANN001
            nonlocal write_calls
            write_calls += 1
            if write_calls == 1:
                raise OSError("synthetic transient JSONL failure")
            return real_write(summary, recommendation, audit_ref)

        real_enqueue = dispatcher.email_service.enqueue
        enqueue_calls = 0

        def fail_first_enqueue(**kwargs):  # noqa: ANN003
            nonlocal enqueue_calls
            enqueue_calls += 1
            if enqueue_calls == 1:
                raise sqlite3.OperationalError("synthetic transient outbox failure")
            return real_enqueue(**kwargs)

        monkeypatch.setattr(dispatcher.jsonl_writer, "write", fail_first_write)
        monkeypatch.setattr(dispatcher.email_service, "enqueue", fail_first_enqueue)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
            notification_dispatcher=dispatcher,
        )

        first = workflow.run_intraday()
        retry = workflow.run_intraday()
        duplicate = workflow.run_intraday()

        notifications = NotificationRepository(connection).list_recent(limit=20)
        deliveries = EmailDeliveryRepository(connection).list(limit=20)
        audits = AuditLogRepository(connection).list_recent(limit=50)
        run = MarketCaptureRunRepository(connection).get(first.run_id)
        records = (settings.log_dir / "notifications.jsonl").read_text().splitlines()

    assert first.reused is False
    assert first.status is CaptureRunStatus.SUCCEEDED
    assert len(first.warnings) == 2
    assert retry.reused is True
    assert duplicate.reused is True
    assert retry.recommendation_ids == first.recommendation_ids
    assert duplicate.recommendation_ids == first.recommendation_ids
    assert write_calls == 3
    assert enqueue_calls == 3
    assert len(notifications) == 1
    assert len(deliveries) == 1
    assert deliveries[0].notification_id == notifications[0].notification_id
    assert len(records) == 1
    assert json.loads(records[0])["summary"]["notification_id"] == notifications[0].notification_id
    assert sum(item.event_type == "recommendation.generated" for item in audits) == 1
    assert sum(item.event_type == "notification.created" for item in audits) == 1
    assert sum(item.event_type == "notification.jsonl_failed" for item in audits) == 1
    assert sum(item.event_type == "email.outbox_failed" for item in audits) == 1
    assert run is not None
    assert run.notification_count == 1
    assert run.email_outbox_count == 1


def test_intraday_marks_plan_stale_when_manual_ledger_changed_after_close(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-plan-stale.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        close = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        old_plan = TradingPlanRepository(connection).get(close.plan_id)
        assert old_plan is not None

        changed_at = datetime(2026, 7, 14, 1, 55, tzinfo=UTC)
        PositionRepository(connection).update(
            PositionInput(
                symbol="600000",
                name="浦发银行",
                quantity=1200,
                available_quantity=1000,
                cost_price=7.2,
                opened_at=date(2026, 7, 1),
                note="manual change",
            ),
            now=changed_at,
        )
        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        stale_plan = TradingPlanRepository(connection).get(old_plan.plan_id)
        recommendation = RecommendationRepository(connection).list(limit=1)[0]
        clock.value = datetime(2026, 7, 14, 2, 3, tzinfo=UTC)
        next_result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        next_recommendation = RecommendationRepository(connection).get(
            next_result.recommendation_ids[0]
        )

    assert result.status in {CaptureRunStatus.SUCCEEDED, CaptureRunStatus.DEGRADED}
    assert stale_plan is not None
    assert stale_plan.status is TradingPlanStatus.STALE
    assert any("手动持仓台账" in warning for warning in stale_plan.warnings)
    assert recommendation.plan_id == old_plan.plan_id
    assert recommendation.plan_version == old_plan.version
    assert recommendation.valid_until == old_plan.valid_until
    assert recommendation.data_quality["plan_status"] == "stale"
    assert recommendation.data_references["plan"] == {
        "plan_id": old_plan.plan_id,
        "status": "stale",
    }
    assert recommendation.action not in {
        RecommendationAction.BUY,
        RecommendationAction.ADD,
    }
    assert next_recommendation is not None
    assert next_recommendation.plan_id == old_plan.plan_id
    assert next_recommendation.plan_version == old_plan.version
    assert next_recommendation.data_quality["plan_status"] == "stale"
    assert next_recommendation.data_references["plan"] == {
        "plan_id": old_plan.plan_id,
        "status": "stale",
    }
    assert next_recommendation.action not in {
        RecommendationAction.BUY,
        RecommendationAction.ADD,
    }


def test_intraday_marks_plan_expired_after_valid_until(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-plan-expired.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        close = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        active = TradingPlanRepository(connection).get(close.plan_id)
        assert active is not None

        clock.value = active.valid_until.astimezone(UTC) + timedelta(minutes=3)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        expired = TradingPlanRepository(connection).get(active.plan_id)
        recommendation = RecommendationRepository(connection).list(limit=1)[0]

    assert expired is not None
    assert expired.status is TradingPlanStatus.EXPIRED
    assert recommendation.plan_id == active.plan_id
    assert recommendation.plan_version == active.version
    assert recommendation.valid_until == active.valid_until
    assert recommendation.data_quality["plan_status"] == "expired"
    assert recommendation.data_references["plan"] == {
        "plan_id": active.plan_id,
        "status": "expired",
    }
    assert recommendation.action not in {
        RecommendationAction.BUY,
        RecommendationAction.ADD,
    }


def test_display_only_intraday_persists_mode_dates_scope_and_wall_clock_bucket(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-display-only-run.db")
    calendar = XSHGTradingCalendar()
    requested_at = datetime(2026, 7, 18, 2, 0, tzinfo=UTC)
    effective_date = date(2026, 7, 17)
    clock = MutableClock(requested_at)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        desired = calendar.sessions_ending(effective_date, 250)
        daily_provider = CalendarDailyProvider(calendar)
        for bar in daily_provider.get_daily_bars(
            "600000", desired[0], desired[-1], "forward"
        ):
            DailyBarRepository(connection).save(bar)

        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        )
        first = workflow.run_intraday(
            as_of=requested_at,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
            manual_reason="market page refresh",
        )
        second = workflow.run_intraday(
            as_of=requested_at + timedelta(minutes=1),
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )
        run = MarketCaptureRunRepository(connection).get(first.run_id)
        market_input = MarketInputSnapshotRepository(connection).get(
            first.market_input_snapshot_id
        )
        audit = AuditLogRepository(connection).get(f"audit-{first.run_id}")

    assert first.run_id == "intraday-display_only-20260717-20260718-1000"
    assert second.run_id == first.run_id
    assert second.reused is True
    assert first.mode is CaptureExecutionMode.DISPLAY_ONLY
    assert first.effective_trade_date == effective_date
    assert first.history_cutoff_date == effective_date
    assert first.requested_symbol_scope == ("600000",)
    assert first.lease_expires_at == requested_at + timedelta(minutes=10)
    assert run is not None
    assert run.mode is CaptureExecutionMode.DISPLAY_ONLY
    assert run.effective_trade_date == effective_date
    assert run.history_cutoff_date == effective_date
    assert run.requested_symbol_scope == ["600000"]
    assert run.lease_expires_at == requested_at + timedelta(minutes=10)
    assert run.idempotency_key == (
        "intraday:display_only:2026-07-17:20260718-1000"
    )
    assert market_input is not None
    assert market_input.mode is CaptureExecutionMode.DISPLAY_ONLY
    assert market_input.effective_trade_date == effective_date
    assert market_input.history_cutoff_date == effective_date
    assert market_input.requested_symbol_scope == ["600000"]
    assert market_input.lease_expires_at == requested_at + timedelta(minutes=10)
    assert audit is not None
    assert audit.event_type == "market_capture.display_only"
    assert audit.payload == {
        "run_id": first.run_id,
        "requested_at": requested_at.isoformat(),
        "manual_reason": "market page refresh",
        "mode": "display_only",
        "effective_trade_date": effective_date.isoformat(),
        "history_cutoff_date": effective_date.isoformat(),
        "requested_symbol_scope": ["600000"],
        "lease_expires_at": (requested_at + timedelta(minutes=10)).isoformat(),
    }


def test_display_only_intraday_hard_exits_before_decision_side_effects(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-display-only-guard.db")
    calendar = XSHGTradingCalendar()
    requested_at = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
    clock = MutableClock(requested_at)

    def forbidden(*args, **kwargs):
        del args, kwargs
        raise AssertionError("display-only crossed the decision boundary")

    monkeypatch.setattr(
        "quantitative_trading.decision.workflow.create_and_save_account_snapshot_with_connection",
        forbidden,
    )
    monkeypatch.setattr(
        "quantitative_trading.decision.workflow.evaluate_plan_conditions",
        forbidden,
    )
    monkeypatch.setattr(
        "quantitative_trading.decision.workflow._risk_context_state",
        forbidden,
    )
    monkeypatch.setattr(
        "quantitative_trading.decision.workflow.decide_symbol",
        forbidden,
    )

    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        desired = calendar.sessions_ending(date(2026, 7, 13), 250)
        daily_provider = CalendarDailyProvider(calendar)
        for bar in daily_provider.get_daily_bars(
            "600000", desired[0], desired[-1], "forward"
        ):
            DailyBarRepository(connection).save(bar)
        protected_tables = (
            "account_snapshots",
            "trading_plans",
            "recommendations",
            "notifications",
            "email_deliveries",
        )
        before = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in protected_tables
        }

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
            notification_dispatcher=FailingRecommendationDispatcher(),
        ).run_intraday(
            as_of=requested_at,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
            manual_reason="manual market refresh",
        )
        after = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in protected_tables
        }

    assert result.recommendation_ids == ()
    assert result.mode is CaptureExecutionMode.DISPLAY_ONLY
    assert after == before


def test_display_only_intraday_does_not_expire_or_rewrite_existing_plan(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "display-plan-immutable.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 16, 7, 20, tzinfo=UTC))
    daily_provider = CalendarDailyProvider(calendar)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        close = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(date(2026, 7, 16))
        before = TradingPlanRepository(connection).get(close.plan_id)
        assert before is not None
        assert before.trading_day == date(2026, 7, 17)

        clock.value = datetime(2026, 7, 18, 2, 0, tzinfo=UTC)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday(
            as_of=clock.value,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )
        after = TradingPlanRepository(connection).get(close.plan_id)

    assert result.recommendation_ids == ()
    assert after == before


def test_display_only_intraday_does_not_resave_or_promote_timed_stale_quote(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "display-timed-quote.db")
    calendar = XSHGTradingCalendar()
    requested_at = datetime(2026, 7, 18, 2, 0, tzinfo=UTC)
    clock = MutableClock(requested_at)
    daily_provider = MatchingCloseDailyProvider(calendar)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        desired = calendar.sessions_ending(date(2026, 7, 17), 250)
        for bar in daily_provider.get_daily_bars(
            "600000", desired[0], desired[-1], "forward"
        ):
            DailyBarRepository(connection).save(bar)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday(
            as_of=requested_at,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        quote = QuoteSnapshotRepository(connection).get(
            market_input.quote_snapshot_refs["600000"]
        )
        quote_count = connection.execute(
            "SELECT COUNT(*) FROM quote_snapshots WHERE symbol='600000'"
        ).fetchone()[0]

    assert quote is not None
    assert quote.status is QuoteStatus.STALE
    assert market_input.dataset_quality["600000"][CaptureDataset.QUOTE].status is (
        CaptureResultStatus.STALE
    )
    assert quote_count == 1


def test_display_only_intraday_restart_reads_provenance_and_next_bucket_is_new(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-display-only-restart.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 18, 2, 0, tzinfo=UTC))
    daily_provider = CalendarDailyProvider(calendar)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        desired = calendar.sessions_ending(date(2026, 7, 17), 250)
        for bar in daily_provider.get_daily_bars(
            "600000", desired[0], desired[-1], "forward"
        ):
            DailyBarRepository(connection).save(bar)
        first = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday(
            as_of=clock.value,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )

    clock.value += timedelta(minutes=3)
    with connect(settings) as connection:
        migrate(connection)
        persisted_run = MarketCaptureRunRepository(connection).get(first.run_id)
        persisted_input = MarketInputSnapshotRepository(connection).get(
            first.market_input_snapshot_id
        )
        second = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday(
            as_of=clock.value,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )

    assert persisted_run is not None
    assert persisted_run.mode is CaptureExecutionMode.DISPLAY_ONLY
    assert persisted_run.requested_symbol_scope == ["600000"]
    assert persisted_input is not None
    assert persisted_input.mode is CaptureExecutionMode.DISPLAY_ONLY
    assert persisted_input.requested_symbol_scope == ["600000"]
    assert persisted_input.lease_expires_at == datetime(
        2026, 7, 18, 2, 10, tzinfo=UTC
    )
    assert second.run_id == "intraday-display_only-20260717-20260718-1003"
    assert second.run_id != first.run_id
    assert second.reused is False


def test_display_only_intraday_reuse_never_reads_recommendations_or_dispatches(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(database_path=tmp_path / "display-reuse-hard-gate.db")
    calendar = XSHGTradingCalendar()
    requested_at = datetime(2026, 7, 18, 2, 0, tzinfo=UTC)
    clock = MutableClock(requested_at)

    def forbidden_list(*args, **kwargs):
        del args, kwargs
        raise AssertionError("display-only reuse read recommendations")

    monkeypatch.setattr(RecommendationRepository, "list", forbidden_list)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        daily_provider = CalendarDailyProvider(calendar)
        desired = calendar.sessions_ending(date(2026, 7, 17), 250)
        for bar in daily_provider.get_daily_bars(
            "600000", desired[0], desired[-1], "forward"
        ):
            DailyBarRepository(connection).save(bar)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
            notification_dispatcher=FailingRecommendationDispatcher(),
        )

        first = workflow.run_intraday(
            as_of=requested_at,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )
        second = workflow.run_intraday(
            as_of=requested_at + timedelta(minutes=1),
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )

    assert first.recommendation_ids == second.recommendation_ids == ()
    assert second.reused is True


def test_display_only_intraday_retries_same_bucket_after_pre_input_exception(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "display-pre-input-retry.db")
    calendar = XSHGTradingCalendar()
    requested_at = datetime(2026, 7, 18, 2, 0, tzinfo=UTC)
    clock = MutableClock(requested_at)

    class FailOnceIntradayProvider(RisingIntradayProvider):
        def __init__(self, calendar, clock):
            super().__init__(calendar, clock)
            self.failed = False

        def get_minute_bars(self, symbol, trade_date, interval):
            if not self.failed:
                self.failed = True
                raise ValueError("synthetic internal display failure")
            return super().get_minute_bars(symbol, trade_date, interval)

    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        daily_provider = CalendarDailyProvider(calendar)
        desired = calendar.sessions_ending(date(2026, 7, 17), 250)
        for bar in daily_provider.get_daily_bars(
            "600000", desired[0], desired[-1], "forward"
        ):
            DailyBarRepository(connection).save(bar)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=FailOnceIntradayProvider(calendar, clock),
            now=clock.now,
        )

        with pytest.raises(ValueError, match="synthetic internal display failure"):
            workflow.run_intraday(
                as_of=requested_at,
                mode=CaptureExecutionMode.DISPLAY_ONLY,
            )
        run_id = "intraday-display_only-20260717-20260718-1000"
        failure_audit = AuditLogRepository(connection).get(
            f"audit-{run_id}-failed-0"
        )
        assert failure_audit is not None
        assert failure_audit.payload["run_id"] == run_id
        assert failure_audit.payload["mode"] == "display_only"
        assert failure_audit.payload["effective_trade_date"] == "2026-07-17"
        assert failure_audit.payload["history_cutoff_date"] == "2026-07-17"
        assert failure_audit.payload["requested_symbol_scope"] == ["600000"]
        assert failure_audit.payload["lease_expires_at"] == (
            requested_at + timedelta(minutes=10)
        ).isoformat()
        assert "synthetic internal display failure" in failure_audit.payload[
            "error_summary"
        ]
        clock.value = requested_at + timedelta(minutes=2)
        retry = workflow.run_intraday(
            as_of=clock.value,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )
        run = MarketCaptureRunRepository(connection).get(retry.run_id)
        market_input = MarketInputSnapshotRepository(connection).get(
            retry.market_input_snapshot_id
        )
        success_audit = AuditLogRepository(connection).get(f"audit-{run_id}")

    assert retry.reused is False
    assert retry.market_input_snapshot_id > 0
    assert retry.lease_expires_at == clock.value + timedelta(minutes=10)
    assert run is not None
    assert run.retry_count == 1
    assert run.lease_expires_at == clock.value + timedelta(minutes=10)
    assert market_input is not None
    assert market_input.lease_expires_at == clock.value + timedelta(minutes=10)
    assert success_audit is not None
    assert success_audit.payload["lease_expires_at"] == (
        clock.value + timedelta(minutes=10)
    ).isoformat()


def test_display_only_intraday_retries_when_terminal_audit_write_fails(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(database_path=tmp_path / "display-audit-retry.db")
    calendar = XSHGTradingCalendar()
    requested_at = datetime(2026, 7, 18, 2, 0, tzinfo=UTC)
    clock = MutableClock(requested_at)
    real_save = AuditLogRepository.save
    failed = False

    def fail_success_audit_once(repository, audit, *, commit=True):
        nonlocal failed
        if audit.event_type == "market_capture.display_only" and not failed:
            failed = True
            raise sqlite3.OperationalError("synthetic display audit failure")
        return real_save(repository, audit, commit=commit)

    monkeypatch.setattr(AuditLogRepository, "save", fail_success_audit_once)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        daily_provider = CalendarDailyProvider(calendar)
        desired = calendar.sessions_ending(date(2026, 7, 17), 250)
        for bar in daily_provider.get_daily_bars(
            "600000", desired[0], desired[-1], "forward"
        ):
            DailyBarRepository(connection).save(bar)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        )

        with pytest.raises(sqlite3.OperationalError, match="display audit failure"):
            workflow.run_intraday(
                as_of=requested_at,
                mode=CaptureExecutionMode.DISPLAY_ONLY,
            )
        run_id = "intraday-display_only-20260717-20260718-1000"
        failed_run = MarketCaptureRunRepository(connection).get(run_id)
        assert failed_run is not None
        assert failed_run.status is CaptureRunStatus.FAILED
        assert AuditLogRepository(connection).get(f"audit-{run_id}") is None

        retry = workflow.run_intraday(
            as_of=requested_at,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )
        completed_run = MarketCaptureRunRepository(connection).get(run_id)
        audit = AuditLogRepository(connection).get(f"audit-{run_id}")

    assert retry.reused is False
    assert completed_run is not None
    assert completed_run.retry_count == 1
    assert audit is not None
    assert audit.event_type == "market_capture.display_only"


@pytest.mark.parametrize(
    ("case", "requested_at", "price", "seed_daily", "verified"),
    (
        (
            "matched_post_close",
            datetime(2026, 7, 18, 2, 0, tzinfo=UTC),
            10.5,
            True,
            True,
        ),
        (
            "mismatched_post_close",
            datetime(2026, 7, 18, 2, 0, tzinfo=UTC),
            10.6,
            True,
            False,
        ),
        (
            "lunch_break",
            datetime(2026, 7, 17, 4, 0, tzinfo=UTC),
            10.5,
            True,
            False,
        ),
        (
            "missing_daily_bar",
            datetime(2026, 7, 18, 2, 0, tzinfo=UTC),
            10.5,
            False,
            False,
        ),
    ),
)
def test_display_only_intraday_strictly_verifies_untimed_post_close_quote(
    tmp_path,
    case,
    requested_at,
    price,
    seed_daily,
    verified,
) -> None:
    settings = Settings(database_path=tmp_path / f"display-quote-{case}.db")
    calendar = XSHGTradingCalendar()
    effective_date = (
        requested_at.astimezone(calendar.timezone).date()
        if calendar.is_trading_day(requested_at.astimezone(calendar.timezone).date())
        else calendar.previous_trading_day(
            requested_at.astimezone(calendar.timezone).date()
        )
    )
    clock = MutableClock(requested_at)
    daily_provider = MatchingCloseDailyProvider(calendar)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        if seed_daily:
            desired = calendar.sessions_ending(effective_date, 250)
            for bar in daily_provider.get_daily_bars(
                "600000", desired[0], desired[-1], "forward"
            ):
                DailyBarRepository(connection).save(bar)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=NoTimeQuoteProvider(price),
            daily_provider=daily_provider,
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday(
            as_of=requested_at,
            mode=CaptureExecutionMode.DISPLAY_ONLY,
        )
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        quote = QuoteSnapshotRepository(connection).get(
            market_input.quote_snapshot_refs["600000"]
        )

    assert quote is not None
    assert (quote.data_time is not None) is verified
    if verified:
        assert quote.data_time == calendar.session(effective_date).close_at
        assert "verified against same-day daily close" in quote.warning


def test_intraday_strength_uses_normalized_full_day_minute_cache(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-cache.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
        full = RisingIntradayProvider(calendar, clock).get_minute_bars(
            "600000", date(2026, 7, 14), "1m"
        )
        MinuteBarRepository(connection).upsert_many(full[:20])
        incremental = [*full[20:25], full[24]]

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=IncrementalIntradayProvider(incremental),
            now=clock.now,
        ).run_intraday()
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            "600000"
        )
        capture = next(
            item
            for item in MarketCaptureResultRepository(connection).list_for_run(
                result.run_id
            )
            if item.dataset is CaptureDataset.MINUTE_BAR
        )

    assert strength is not None
    assert strength.minute_volume_ratio is not None
    assert strength.last_minute == full[24].minute
    assert capture.actual_rows == 25


def test_intraday_retry_keeps_prior_recommendation_when_conditions_change(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-material-change.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    daily_provider = CalendarDailyProvider(calendar)
    flow_provider = CalendarFlowProvider(calendar)

    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)

        with pytest.raises(RuntimeError, match="synthetic downstream failure"):
            DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=ClockQuoteProvider(clock),
                daily_provider=daily_provider,
                money_flow_provider=flow_provider,
                intraday_provider=RisingIntradayProvider(calendar, clock),
                now=clock.now,
                notification_dispatcher=FailingRecommendationDispatcher(),
            ).run_intraday()

        first = RecommendationRepository(connection).list(limit=20)
        assert len(first) == 1
        failed_run = MarketCaptureRunRepository(connection).get(
            "intraday-20260714-1000"
        )
        assert failed_run is not None
        assert failed_run.status is CaptureRunStatus.FAILED

        retried = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=StaleQuoteProvider(),
            daily_provider=daily_provider,
            money_flow_provider=flow_provider,
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        all_recommendations = RecommendationRepository(connection).list(limit=20)

    assert retried.reused is False
    assert len(all_recommendations) == 2
    assert all_recommendations[0].action is not all_recommendations[1].action
    assert all_recommendations[0].recommendation_id != all_recommendations[1].recommendation_id
    assert {
        recommendation.audit_id for recommendation in all_recommendations
    } == {
        f"audit-{recommendation.recommendation_id}"
        for recommendation in all_recommendations
    }


def test_intraday_stale_minutes_cannot_confirm_add(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-stale.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
        workflow = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=OldIntradayProvider(calendar, clock),
            now=clock.now,
            stale_trading_minutes=7,
        )

        result = workflow.run_intraday()
        recommendation = RecommendationRepository(connection).get(
            result.recommendation_ids[0]
        )
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            "600000"
        )
        assert recommendation is not None
        assert recommendation.audit_id is not None
        audit = AuditLogRepository(connection).get(recommendation.audit_id)

    assert recommendation.action is not RecommendationAction.ADD
    assert recommendation.data_quality["overall"] == "stale"
    assert audit is not None
    assert audit.event_type == "recommendation.generated"
    assert audit.recommendation_id == recommendation.recommendation_id
    assert audit.payload["data_quality"] == recommendation.data_quality
    assert strength is not None
    assert strength.degraded is True
    assert strength.thresholds["stale_minutes"] == 7


def test_intraday_cached_strength_after_provider_failure_cannot_confirm_add(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-cached-no-add.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
        cached = RisingIntradayProvider(calendar, clock).get_minute_bars(
            "600000", date(2026, 7, 14), "1m"
        )
        MinuteBarRepository(connection).upsert_many(cached)

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=FailingIntradayProvider(),
            now=clock.now,
        ).run_intraday()
        recommendation = RecommendationRepository(connection).get(
            result.recommendation_ids[0]
        )
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            "600000"
        )

    assert recommendation is not None
    assert recommendation.action is RecommendationAction.HOLD
    assert "intraday_data_unusable" in recommendation.risk["machine_reason"]
    assert recommendation.data_quality["intraday_status"] == "degraded"
    assert recommendation.data_quality["intraday_usable"] is False
    assert strength is not None
    assert strength.label.value == "strong"
    assert strength.degraded is True


def test_intraday_stale_quote_cannot_trigger_holding_sell(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-stale-quote-sell.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        clock.value = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=StaleLowQuoteProvider(),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        recommendation = RecommendationRepository(connection).get(
            result.recommendation_ids[0]
        )

    assert recommendation is not None
    assert recommendation.action is RecommendationAction.HOLD
    assert recommendation.price_context["current_price"] is None
    assert recommendation.data_quality["overall"] == "stale"
    assert any("当前行情不可用" in reason for reason in recommendation.reason)


@pytest.mark.parametrize("threshold", [7, 60])
def test_cross_day_market_time_is_stale_for_every_supported_threshold(
    threshold,
) -> None:
    calendar = XSHGTradingCalendar()

    assert _trading_minute_lag(
        calendar,
        datetime(2026, 7, 13, 7, 0, tzinfo=UTC),
        datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
    ) > threshold


@pytest.mark.parametrize(
    ("data_time", "observed_at"),
    [
        (
            datetime(2026, 7, 16, 6, 6, 54, tzinfo=UTC),
            datetime(2026, 7, 16, 6, 6, 52, tzinfo=UTC),
        ),
        (
            datetime(2026, 7, 16, 6, 8, 0, tzinfo=UTC),
            datetime(2026, 7, 16, 6, 7, 22, tzinfo=UTC),
        ),
    ],
)
def test_subminute_provider_clock_skew_is_not_stale(
    data_time: datetime,
    observed_at: datetime,
) -> None:
    assert _trading_minute_lag(
        XSHGTradingCalendar(), data_time, observed_at
    ) == 0


def test_material_future_market_time_is_stale() -> None:
    assert _trading_minute_lag(
        XSHGTradingCalendar(),
        datetime(2026, 7, 16, 6, 8, 1, tzinfo=UTC),
        datetime(2026, 7, 16, 6, 7, 0, tzinfo=UTC),
    ) == float("inf")


def test_intraday_failure_persists_minute_and_strength_results(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "intraday-failure-results.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=FailingIntradayProvider(),
            now=clock.now,
        ).run_intraday()
        capture_results = MarketCaptureResultRepository(connection).list_for_run(
            result.run_id
        )

    failures = {
        item.dataset: item
        for item in capture_results
        if item.status is CaptureResultStatus.FAILED
    }
    assert failures.keys() >= {
        CaptureDataset.MINUTE_BAR,
        CaptureDataset.INTRADAY_STRENGTH,
    }
    assert failures[CaptureDataset.INTRADAY_STRENGTH].actual_rows == 0
    assert (
        "synthetic minute outage"
        in failures[CaptureDataset.INTRADAY_STRENGTH].error_summary
    )
    assert (
        "reused same-day minute_cache"
        not in failures[CaptureDataset.INTRADAY_STRENGTH].error_summary
    )


def test_intraday_provider_failure_reuses_fresh_same_day_minute_cache(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-provider-cache.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    trade_date = date(2026, 7, 14)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        cached = RisingIntradayProvider(calendar, clock).get_minute_bars(
            "600000", trade_date, "1m"
        )
        MinuteBarRepository(connection).upsert_many(cached)

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=FailingIntradayProvider(),
            now=clock.now,
        ).run_intraday()
        capture_results = {
            item.dataset: item
            for item in MarketCaptureResultRepository(connection).list_for_run(
                result.run_id
            )
        }
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            "600000"
        )
        run = MarketCaptureRunRepository(connection).get(result.run_id)
        cached_after = MinuteBarRepository(connection).for_trade_date(
            "600000", trade_date
        )

    minute = capture_results[CaptureDataset.MINUTE_BAR]
    strength_result = capture_results[CaptureDataset.INTRADAY_STRENGTH]
    assert minute.status is CaptureResultStatus.DEGRADED
    assert minute.actual_rows == len(cached) == 30
    assert "minute_cache" in minute.source
    assert "provider_failure" in minute.source
    assert "synthetic minute outage" in minute.warning
    assert strength_result.status is CaptureResultStatus.DEGRADED
    assert strength_result.actual_rows == 1
    assert "minute_cache" in strength_result.source
    assert "synthetic minute outage" in strength_result.warning
    assert market_input is not None
    minute_quality = market_input.dataset_quality["600000"][CaptureDataset.MINUTE_BAR]
    strength_quality = market_input.dataset_quality["600000"][
        CaptureDataset.INTRADAY_STRENGTH
    ]
    assert minute_quality.actual_rows == 30
    assert minute_quality.status is CaptureResultStatus.DEGRADED
    assert "minute_cache" in minute_quality.source
    assert "provider_failure" in minute_quality.source
    assert "synthetic minute outage" in minute_quality.warning
    assert "minute_cache" in strength_quality.source
    assert "provider_failure" in strength_quality.source
    assert "synthetic minute outage" in strength_quality.warning
    assert strength_result.source == strength_quality.source
    assert strength is not None
    assert strength.degraded is True
    assert any(
        "synthetic minute outage" in reason
        for reason in strength.degradation_reasons
    )
    assert run is not None
    assert run.provider_calls == 2
    assert run.rows_received == 1
    assert run.rows_written == 2
    assert run.recommendation_count == 1
    assert cached_after == cached


def test_intraday_provider_failure_marks_lagging_same_day_cache_stale(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-provider-stale-cache.db")
    calendar = XSHGTradingCalendar()
    cache_clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    observed_clock = MutableClock(datetime(2026, 7, 14, 2, 10, tzinfo=UTC))
    trade_date = date(2026, 7, 14)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        cached = RisingIntradayProvider(calendar, cache_clock).get_minute_bars(
            "600000", trade_date, "1m"
        )
        MinuteBarRepository(connection).upsert_many(cached)

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(observed_clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=FailingIntradayProvider(),
            stale_trading_minutes=6,
            now=observed_clock.now,
        ).run_intraday()
        capture_results = {
            item.dataset: item
            for item in MarketCaptureResultRepository(connection).list_for_run(
                result.run_id
            )
        }
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            "600000"
        )

    minute = capture_results[CaptureDataset.MINUTE_BAR]
    strength_result = capture_results[CaptureDataset.INTRADAY_STRENGTH]
    assert minute.status is CaptureResultStatus.STALE
    assert strength_result.status is CaptureResultStatus.STALE
    assert minute.actual_rows == len(cached) == 30
    assert "synthetic minute outage" in minute.warning
    assert "落后超过 6 个有效交易分钟" in minute.warning
    assert "synthetic minute outage" in strength_result.warning
    assert "落后超过 6 个有效交易分钟" in strength_result.warning
    assert strength is not None
    assert strength.data_time == cached[-1].minute
    assert strength.thresholds["stale_minutes"] == 6


@pytest.mark.parametrize("cache_scope", ["previous_trade_date", "other_symbol"])
def test_intraday_provider_failure_does_not_reuse_wrong_minute_cache_scope(
    tmp_path,
    cache_scope,
) -> None:
    settings = Settings(
        database_path=tmp_path / f"intraday-wrong-cache-{cache_scope}.db"
    )
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    trade_date = date(2026, 7, 14)
    cached_symbol = "000001" if cache_scope == "other_symbol" else "600000"
    cached_date = date(2026, 7, 13) if cache_scope == "previous_trade_date" else trade_date
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        wrong_scope_cache = RisingIntradayProvider(calendar, clock).get_minute_bars(
            cached_symbol, cached_date, "1m"
        )
        MinuteBarRepository(connection).upsert_many(wrong_scope_cache)

        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=FailingIntradayProvider(),
            now=clock.now,
        ).run_intraday()
        capture_results = {
            item.dataset: item
            for item in MarketCaptureResultRepository(connection).list_for_run(
                result.run_id
            )
        }
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            "600000"
        )

    assert capture_results[CaptureDataset.MINUTE_BAR].status is CaptureResultStatus.FAILED
    assert capture_results[CaptureDataset.MINUTE_BAR].actual_rows == 0
    assert (
        capture_results[CaptureDataset.INTRADAY_STRENGTH].status
        is CaptureResultStatus.FAILED
    )
    assert capture_results[CaptureDataset.INTRADAY_STRENGTH].actual_rows == 0
    assert strength is None


@pytest.mark.parametrize("failure_stage", ["cache_read", "strength_calculation"])
def test_intraday_cache_internal_error_after_provider_failure_terminates_run(
    tmp_path,
    monkeypatch,
    failure_stage,
) -> None:
    settings = Settings(
        database_path=tmp_path / f"intraday-cache-internal-{failure_stage}.db"
    )
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    trade_date = date(2026, 7, 14)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        cached = RisingIntradayProvider(calendar, clock).get_minute_bars(
            "600000", trade_date, "1m"
        )
        MinuteBarRepository(connection).upsert_many(cached)

        if failure_stage == "cache_read":
            def fail_cache_read(self, symbol, requested_date):  # noqa: ANN001
                del self, symbol, requested_date
                raise ValueError("synthetic cached minute validation failure")

            monkeypatch.setattr(
                MinuteBarRepository,
                "for_trade_date",
                fail_cache_read,
            )
        else:
            def fail_strength_calculation(*args, **kwargs):  # noqa: ANN002, ANN003
                del args, kwargs
                raise ValueError("synthetic cached strength calculation failure")

            monkeypatch.setattr(
                "quantitative_trading.decision.workflow.calculate_intraday_strength",
                fail_strength_calculation,
            )

        with pytest.raises(ValueError, match="synthetic cached"):
            DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=ClockQuoteProvider(clock),
                daily_provider=CalendarDailyProvider(calendar),
                money_flow_provider=CalendarFlowProvider(calendar),
                intraday_provider=FailingIntradayProvider(),
                now=clock.now,
            ).run_intraday()
        run = MarketCaptureRunRepository(connection).get("intraday-20260714-1000")

    assert run is not None
    assert run.status is CaptureRunStatus.FAILED


def test_intraday_provider_failure_rejects_future_same_day_minute_cache(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-future-cache.db")
    calendar = XSHGTradingCalendar()
    cache_clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    observed_clock = MutableClock(datetime(2026, 7, 14, 1, 55, tzinfo=UTC))
    trade_date = date(2026, 7, 14)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        future_cache = RisingIntradayProvider(calendar, cache_clock).get_minute_bars(
            "600000", trade_date, "1m"
        )
        MinuteBarRepository(connection).upsert_many(future_cache)

        with pytest.raises(ValueError, match="future minute"):
            DecisionWorkflow(
                connection,
                calendar=calendar,
                quote_provider=ClockQuoteProvider(observed_clock),
                daily_provider=CalendarDailyProvider(calendar),
                money_flow_provider=CalendarFlowProvider(calendar),
                intraday_provider=FailingIntradayProvider(),
                now=observed_clock.now,
            ).run_intraday()
        run = MarketCaptureRunRepository(connection).get("intraday-20260714-0954")

    assert run is not None
    assert run.status is CaptureRunStatus.FAILED


def test_intraday_partial_account_is_reflected_in_holding_recommendation(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-partial-account.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        PositionRepository(connection).add(
            PositionInput(
                symbol="000001",
                name="平安银行",
                quantity=1000,
                available_quantity=1000,
                cost_price=9.0,
                opened_at=date(2026, 7, 1),
                note="manual",
            ),
            now=datetime(2026, 7, 12, 8, 0, tzinfo=UTC),
        )
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=PartialAccountQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        recommendations = [
            RecommendationRepository(connection).get(recommendation_id)
            for recommendation_id in result.recommendation_ids
        ]

    recommendation = next(
        item for item in recommendations if item is not None and item.symbol == "600000"
    )
    assert recommendation.data_references["account"]["status"] == "partial"
    assert any(
        "account totals unavailable" in warning
        for warning in recommendation.data_quality["warnings"]
    )
    assert any("账户估值" in note for note in recommendation.risk["notes"])


def test_close_plan_is_not_published_for_unverifiable_twenty_bar_history(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "close-short-history.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=TwentyDayDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)
        plan = TradingPlanRepository(connection).get(result.plan_id or "")
        run = MarketCaptureRunRepository(connection).get(result.run_id)

    assert result.ready is False
    assert result.plan_id is None
    assert plan is None
    assert run is not None
    assert run.status is CaptureRunStatus.FAILED
    assert any("short history start is unverifiable" in warning for warning in result.warnings)


def test_close_plan_accepts_verified_twenty_bar_provider_history(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "close-verified-short-history.db")
    calendar = XSHGTradingCalendar()
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=RecordingQuoteProvider(),
            daily_provider=TwentyDayCoverageProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=lambda: NOW,
        ).run_close(TRADE_DATE)
        plan = TradingPlanRepository(connection).get(result.plan_id or "")
        market_input = MarketInputSnapshotRepository(connection).get(
            result.market_input_snapshot_id
        )
        history = HistorySnapshotRepository(connection).get(
            market_input.history_snapshot_refs["600000"]
        )

    assert result.ready is True
    assert plan is not None
    assert plan.data_quality == "degraded"
    assert history is not None
    assert history.completeness == "verified_provider_window"
    assert history.row_count == 20
    feature_facts = plan.symbol_contexts["600000"].daily_feature_facts
    assert feature_facts["ma60"] == {
        "value": None,
        "available": False,
        "reason": "requires 60 daily bars",
    }


def test_intraday_missing_quote_time_does_not_fall_back_to_run_time(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "intraday-no-quote-time.db")
    calendar = XSHGTradingCalendar()
    clock = MutableClock(NOW)
    with connect(settings) as connection:
        migrate(connection)
        seed_account(connection)
        DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=ClockQuoteProvider(clock),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=NoopIntradayProvider(),
            now=clock.now,
        ).run_close(TRADE_DATE)
        clock.value = datetime(2026, 7, 14, 2, 1, tzinfo=UTC)
        result = DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=NoTimeQuoteProvider(11.1),
            daily_provider=CalendarDailyProvider(calendar),
            money_flow_provider=CalendarFlowProvider(calendar),
            intraday_provider=RisingIntradayProvider(calendar, clock),
            now=clock.now,
        ).run_intraday()
        recommendation = RecommendationRepository(connection).list(limit=1)[0]

    assert result.status is CaptureRunStatus.FAILED
    assert recommendation.price_context["current_price"] is None
    assert recommendation.data_time != clock.value
    assert recommendation.data_time == datetime(
        2026, 7, 14, 10, 0, tzinfo=calendar.timezone
    )
