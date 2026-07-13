from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.config import Settings
from quantitative_trading.decision.workflow import (
    DecisionWorkflow,
    WorkflowAlreadyRunningError,
)
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
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
)
from quantitative_trading.market.repository import MarketInputSnapshotRepository
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


class SameDayOldQuoteProvider(RecordingQuoteProvider):
    def get_quotes(self, symbols):
        quotes = super().get_quotes(symbols)
        return {
            symbol: quote.model_copy(
                update={"data_time": datetime(2026, 7, 13, 6, 0, tzinfo=UTC)}
            )
            for symbol, quote in quotes.items()
        }


class FailingFlowProvider:
    def get_daily_money_flow(self, symbol, start_date, end_date):
        raise RuntimeError("synthetic flow outage")


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
    assert market_input is not None
    assert market_input.intraday_strength_snapshot_refs["600000"] > 0
    assert len(dispatcher.calls) == 1
    assert run is not None
    assert run.provider_calls == 2
    assert run.recommendation_count == 1
    assert run.notification_count == 1
    assert run.email_outbox_count == 1


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
        )

        result = workflow.run_intraday()
        recommendation = RecommendationRepository(connection).get(
            result.recommendation_ids[0]
        )
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            "600000"
        )

    assert recommendation is not None
    assert recommendation.action is not RecommendationAction.ADD
    assert recommendation.data_quality["overall"] == "stale"
    assert strength is not None
    assert strength.degraded is True
    assert strength.thresholds["stale_minutes"] == 6
