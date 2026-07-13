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
from quantitative_trading.market.adapters import MarketProviderError
from quantitative_trading.market.backfill import HeavyDataBackfillService
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureResultStatus,
    DailyBar,
    DailyMoneyFlow,
    CaptureRunStatus,
    CaptureRunAlreadyActiveError,
    LimitStatus,
    QuoteSnapshot,
    QuoteStatus,
    MinuteBar,
    MarketCaptureRun,
    TradingStatus,
)
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    MarketCaptureRunRepository,
    MoneyFlowRepository,
    IntradayStrengthSnapshotRepository,
    MarketCaptureResultRepository,
    MinuteBarRepository,
)
from quantitative_trading.market.repository import MarketInputSnapshotRepository
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.planning.models import TradingPlanStatus
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.recommendation.models import RecommendationAction
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect, migrate


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

    def fail_internal_contract(self, run_id, symbol, as_of):  # noqa: ANN001
        del self, run_id, symbol, as_of
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

    assert result.status in {CaptureRunStatus.SUCCEEDED, CaptureRunStatus.DEGRADED}
    assert stale_plan is not None
    assert stale_plan.status is TradingPlanStatus.STALE
    assert any("手动持仓台账" in warning for warning in stale_plan.warnings)
    assert recommendation.plan_id is None
    assert recommendation.action not in {
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
    assert recommendation.plan_id is None
    assert recommendation.action not in {
        RecommendationAction.BUY,
        RecommendationAction.ADD,
    }


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
    assert strength is not None
    assert strength.degraded is True
    assert strength.thresholds["stale_minutes"] == 7


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


def test_close_plan_is_degraded_when_history_has_only_twenty_bars(tmp_path) -> None:
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

    assert result.ready is True
    assert plan is not None
    assert plan.data_quality == "degraded"
    assert run is not None
    assert run.status is CaptureRunStatus.DEGRADED
    assert plan.symbol_contexts["600000"].data_quality == "degraded"
    feature_facts = plan.symbol_contexts["600000"].daily_feature_facts
    assert feature_facts["ma60"] == {
        "value": None,
        "available": False,
        "reason": "requires 60 daily bars",
    }
    assert any("expected 250 daily bars, got 20" in warning for warning in plan.warnings)


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
