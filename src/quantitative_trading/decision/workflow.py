from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from time import perf_counter
from typing import Any, Callable, Protocol, Sequence

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.decision.models import DecisionSymbolInput
from quantitative_trading.decision.service import decide_symbol
from quantitative_trading.feedback.repository import FeedbackRepository
from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.market.adapters import (
    DailyBarProvider,
    IntradayProvider,
    MarketProviderError,
    MoneyFlowProvider,
)
from quantitative_trading.market.backfill import HeavyDataBackfillService
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.cli_service import (
    MarketBackfillSummary,
    MarketCliService,
)
from quantitative_trading.market.features import (
    IntradayStrengthRules,
    calculate_daily_features,
    calculate_intraday_strength,
    select_market_structure,
)
from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureResultStatus,
    CaptureRunAlreadyActiveError,
    CaptureRunStatus,
    DatasetQuality,
    MarketCaptureResult,
    MarketCaptureRun,
    MarketInputSnapshot,
    QuoteSnapshot,
    QuoteStatus,
)
from quantitative_trading.market.providers import MarketDataProvider
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    HistorySnapshotRepository,
    IntradayStrengthSnapshotRepository,
    MarketCaptureResultRepository,
    MarketCaptureRunRepository,
    MinuteBarRepository,
    MoneyFlowRepository,
    MoneyFlowSnapshotRepository,
)
from quantitative_trading.market.repository import (
    MarketInputSnapshotRepository,
    QuoteSnapshotRepository,
)
from quantitative_trading.planning.models import MarketPlanSymbolInput
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.planning.service import evaluate_plan_conditions
from quantitative_trading.planning.workflow import build_market_trading_plan
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.recommendation.identity import with_recommendation_identity
from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.risk.models import RiskConfig, RiskContext
from quantitative_trading.runtime.account_snapshot_job import (
    create_and_save_account_snapshot_with_connection,
)
from quantitative_trading.sanitization import safe_error_summary
from quantitative_trading.universe.models import (
    UniverseSnapshot,
    UniverseSnapshotStatus,
)
from quantitative_trading.universe.repository import UniverseSnapshotRepository
from quantitative_trading.universe.service import build_universe
from quantitative_trading.watchlist.repository import WatchPinnedRepository


@dataclass(frozen=True)
class CloseWorkflowResult:
    run_id: str
    ready: bool
    reused: bool
    market_input_snapshot_id: int
    plan_id: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntradayWorkflowResult:
    run_id: str
    reused: bool
    status: CaptureRunStatus
    market_input_snapshot_id: int
    recommendation_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CleanupWorkflowResult:
    run_id: str
    trade_date: date
    status: CaptureRunStatus
    reused: bool
    cleaned_rows: int
    warnings: tuple[str, ...] = ()


class RecommendationDispatcher(Protocol):
    def dispatch_recommendation(
        self,
        recommendation,
        *,
        plan_version: str | int | None,
        now: datetime,
    ) -> Any: ...

    def dispatch_daily_summary(
        self,
        *,
        plan_id: str,
        plan_version: str | int,
        recommendations: list,
        now: datetime,
    ) -> Any: ...


class WorkflowAlreadyRunningError(CaptureRunAlreadyActiveError):
    pass


class DecisionWorkflow:
    CLOSE_LEASE = timedelta(minutes=10)
    INTRADAY_LEASE = timedelta(minutes=10)

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        calendar: XSHGTradingCalendar,
        quote_provider: MarketDataProvider,
        daily_provider: DailyBarProvider,
        money_flow_provider: MoneyFlowProvider,
        intraday_provider: IntradayProvider,
        now: Callable[[], datetime],
        stale_trading_minutes: int = 6,
        strength_rules: IntradayStrengthRules | None = None,
        notification_dispatcher: RecommendationDispatcher | None = None,
    ) -> None:
        self.connection = connection
        self.calendar = calendar
        self.quote_provider = quote_provider
        self.daily_provider = daily_provider
        self.money_flow_provider = money_flow_provider
        self.intraday_provider = intraday_provider
        self.now = now
        if stale_trading_minutes < 1:
            raise ValueError("stale trading minute threshold must be positive")
        self.stale_trading_minutes = stale_trading_minutes
        self.strength_rules = strength_rules or IntradayStrengthRules()
        self.notification_dispatcher = notification_dispatcher

    def run_backfill(
        self,
        trade_date: date,
        *,
        symbols: Sequence[str] | None = None,
    ) -> MarketBackfillSummary:
        return self._market_maintenance_service().backfill(
            trade_date,
            symbols=symbols,
        )

    def run_cleanup(self, as_of: date) -> CleanupWorkflowResult:
        result = self._market_maintenance_service().cleanup(as_of)
        return CleanupWorkflowResult(
            run_id=result.run_id,
            trade_date=result.trade_date,
            status=result.status,
            reused=result.reused,
            cleaned_rows=result.deleted_rows,
        )

    def _market_maintenance_service(self) -> MarketCliService:
        return MarketCliService(
            self.connection,
            calendar=self.calendar,
            daily_provider=self.daily_provider,
            money_flow_provider=self.money_flow_provider,
            now=self.now,
        )

    @staticmethod
    def _require_provider_capture_error(exc: Exception) -> None:
        if not isinstance(exc, MarketProviderError):
            raise exc

    def _claim_retry_or_raise(
        self,
        repository: MarketCaptureRunRepository,
        run: MarketCaptureRun,
        *,
        started_at: datetime,
        stale_after: timedelta,
        retryable_statuses: set[CaptureRunStatus],
    ) -> MarketCaptureRun:
        if run.status is CaptureRunStatus.RUNNING:
            if started_at - run.started_at < stale_after:
                raise WorkflowAlreadyRunningError(run.run_id)
        elif run.status not in retryable_statuses:
            raise RuntimeError(
                f"workflow run cannot be retried from status {run.status.value}"
            )
        claimed = repository.claim_retry(run, started_at=started_at)
        if claimed is None:
            raise WorkflowAlreadyRunningError(run.run_id)
        return claimed

    def _mark_capture_run_failed(
        self,
        repository: MarketCaptureRunRepository,
        run_id: str,
        exc: Exception,
        *,
        fallback_time: datetime,
    ) -> None:
        try:
            finished_at = self._now()
        except Exception:
            finished_at = fallback_time
        try:
            repository.fail_if_running(
                run_id,
                finished_at=finished_at,
                expected_started_at=fallback_time,
                error_summary=safe_error_summary(exc),
            )
        except sqlite3.Error:
            pass

    def run_close(
        self,
        trade_date: date,
        *,
        skip_calendar: bool = False,
    ) -> CloseWorkflowResult:
        if not skip_calendar and not self.calendar.is_trading_day(trade_date):
            raise ValueError("close workflow requires an XSHG trading day")
        started_at = self._now()
        run_id = f"close-{trade_date:%Y%m%d}"
        run_repository = MarketCaptureRunRepository(self.connection)
        run_repository.fail_expired_workflow_runs(
            "close",
            started_before=started_at - self.CLOSE_LEASE,
            finished_at=started_at,
        )
        run, created = run_repository.get_or_create(
            MarketCaptureRun(
                run_id=run_id,
                workflow_type="close",
                trade_date=trade_date,
                idempotency_key=f"close:{trade_date.isoformat()}",
                status=CaptureRunStatus.RUNNING,
                started_at=started_at,
            )
        )
        if not created:
            existing_plan = TradingPlanRepository(self.connection).active_for_day(
                self.calendar.next_trading_day(trade_date)
            )
            if existing_plan is not None and existing_plan.source_run_id == run.run_id:
                recovered_status = (
                    CaptureRunStatus.SUCCEEDED
                    if existing_plan.data_quality == "complete"
                    else CaptureRunStatus.DEGRADED
                )
                if run.status is not recovered_status:
                    run = run.model_copy(
                        update={
                            "status": recovered_status,
                            "finished_at": run.finished_at or started_at,
                            "plan_count": 1,
                            "error_summary": "",
                        }
                    )
                    run_repository.update(run)
                self._dispatch_close_daily_summary(
                    existing_plan,
                    trade_date=trade_date,
                    now=started_at,
                )
                return CloseWorkflowResult(
                    run_id=run.run_id,
                    ready=True,
                    reused=True,
                    market_input_snapshot_id=existing_plan.market_input_snapshot_id
                    or 0,
                    plan_id=existing_plan.plan_id,
                    warnings=tuple(existing_plan.warnings),
                )
            if (
                run.status is CaptureRunStatus.SUCCEEDED
                and run.requested_symbols == 0
                and run.plan_count == 0
            ):
                snapshot_id = self._market_input_id_for_run(run.run_id)
                snapshot = MarketInputSnapshotRepository(self.connection).get(snapshot_id)
                return CloseWorkflowResult(
                    run_id=run.run_id,
                    ready=True,
                    reused=True,
                    market_input_snapshot_id=snapshot_id,
                    plan_id=None,
                    warnings=() if snapshot is None else tuple(snapshot.warnings),
                )
        if (
            not created
            and run.status is CaptureRunStatus.RUNNING
            and started_at - run.started_at < self.CLOSE_LEASE
        ):
            raise WorkflowAlreadyRunningError(run.run_id)
        if not created:
            run = self._claim_retry_or_raise(
                run_repository,
                run,
                started_at=started_at,
                stale_after=self.CLOSE_LEASE,
                retryable_statuses={
                    CaptureRunStatus.FAILED,
                    CaptureRunStatus.DEGRADED,
                },
            )

        try:
            return self._run_close_claimed(
                trade_date,
                started_at=started_at,
                run=run,
                run_repository=run_repository,
            )
        except Exception as exc:
            self._mark_capture_run_failed(
                run_repository,
                run.run_id,
                exc,
                fallback_time=started_at,
            )
            raise

    def _run_close_claimed(
        self,
        trade_date: date,
        *,
        started_at: datetime,
        run: MarketCaptureRun,
        run_repository: MarketCaptureRunRepository,
    ) -> CloseWorkflowResult:
        run_id = run.run_id

        positions = PositionRepository(self.connection).list()
        watchlist = WatchPinnedRepository(self.connection).list()
        members = build_universe(
            positions=positions,
            watchlist=watchlist,
            created_at=started_at,
        )
        decision_members = [member for member in members if member.plan_enabled]
        symbols = sorted(member.symbol for member in decision_members)
        universe_snapshot_id = UniverseSnapshotRepository(self.connection).save(
            UniverseSnapshot(
                created_at=started_at,
                status=UniverseSnapshotStatus.OK,
                warnings=[] if symbols else ["决策启用集合为空"],
                members=decision_members,
            )
        )

        run = run.model_copy(update={"requested_symbols": len(symbols)})
        run_repository.update_claimed(run, claim_started_at=run.started_at)
        quote_refs, quotes, warnings, provider_duration_ms = self._capture_quotes(
            run_id, symbols, started_at
        )

        daily_repository = DailyBarRepository(self.connection)
        flow_repository = MoneyFlowRepository(self.connection)
        backfill = HeavyDataBackfillService(
            calendar=self.calendar,
            daily_provider=self.daily_provider,
            money_flow_provider=self.money_flow_provider,
            daily_repository=daily_repository,
            money_flow_repository=flow_repository,
            history_snapshot_repository=HistorySnapshotRepository(self.connection),
            money_flow_snapshot_repository=MoneyFlowSnapshotRepository(self.connection),
            now=self.now,
        )
        history_refs: dict[str, int] = {}
        flow_refs: dict[str, int] = {}
        dataset_quality: dict[str, dict[CaptureDataset, DatasetQuality]] = {}
        result_repository = MarketCaptureResultRepository(self.connection)
        session_close = (
            self.calendar.session(trade_date).close_at
            if self.calendar.is_trading_day(trade_date)
            else None
        )
        hard_ready = session_close is None or started_at >= session_close
        if symbols and not hard_ready:
            warnings.append("交易时段尚未收盘，不能发布次日计划")
        provider_calls = 1 if symbols else 0
        rows_received = len(quotes)
        rows_written = len(quote_refs)

        for symbol in symbols:
            quality: dict[CaptureDataset, DatasetQuality] = {}
            quote = quotes[symbol]
            try:
                provider_started = perf_counter()
                history = backfill.backfill_daily(run_id, symbol, trade_date)
            except Exception as exc:
                self._require_provider_capture_error(exc)
                warning = f"{symbol} 日 K 采集失败: {safe_error_summary(exc)}"
                warnings.append(warning)
                hard_ready = False
                quality[CaptureDataset.DAILY_BAR] = DatasetQuality(
                    status=CaptureResultStatus.FAILED,
                    expected_rows=250,
                    actual_rows=0,
                    source="daily_provider",
                    warning=warning,
                )
                result_repository.upsert(
                    MarketCaptureResult(
                        run_id=run_id,
                        symbol=symbol,
                        dataset=CaptureDataset.DAILY_BAR,
                        status=CaptureResultStatus.FAILED,
                        fetched_at=started_at,
                        expected_rows=250,
                        source="daily_provider",
                        error_summary=warning,
                    )
                )
            else:
                provider_calls += history.provider_calls
                rows_received += history.rows_received
                rows_written += history.rows_written
                history_refs[symbol] = history.snapshot_id
                history_ready = (
                    history.snapshot.data_end == trade_date
                    and history.snapshot.row_count >= 20
                )
                if not history_ready:
                    hard_ready = False
                quality[CaptureDataset.DAILY_BAR] = DatasetQuality(
                    status=(
                        history.snapshot.status
                        if history_ready
                        else CaptureResultStatus.FAILED
                    ),
                    data_start=history.snapshot.data_start,
                    data_end=history.snapshot.data_end,
                    expected_rows=250,
                    actual_rows=history.row_count,
                    source="daily_provider",
                    warning=history.snapshot.warning,
                )
                result_repository.upsert(
                    MarketCaptureResult(
                        run_id=run_id,
                        symbol=symbol,
                        dataset=CaptureDataset.DAILY_BAR,
                        status=quality[CaptureDataset.DAILY_BAR].status,
                        data_start=history.snapshot.data_start,
                        data_end=history.snapshot.data_end,
                        fetched_at=history.snapshot.fetched_at,
                        expected_rows=250,
                        actual_rows=history.row_count,
                        source="daily_provider",
                        warning=history.snapshot.warning,
                    )
                )
            finally:
                provider_duration_ms += (perf_counter() - provider_started) * 1000

            quote = self._verify_close_quote_from_daily_bar(
                quote,
                trade_date=trade_date,
                daily_repository=daily_repository,
                observed_at=started_at,
            )
            if quote is not quotes[symbol]:
                quote_refs[symbol] = QuoteSnapshotRepository(self.connection).save(quote)
                quotes[symbol] = quote
                rows_written += 1
            quote_usable = (
                quote.status in {QuoteStatus.OK, QuoteStatus.PARTIAL}
                and quote.data_time is not None
                and quote.data_time.astimezone(self.calendar.timezone).date()
                == trade_date
            )
            quote_status = (
                CaptureResultStatus.COMPLETE
                if quote_usable and quote.status is QuoteStatus.OK
                else CaptureResultStatus.DEGRADED
                if quote_usable
                else CaptureResultStatus.STALE
                if quote.status is QuoteStatus.STALE
                else CaptureResultStatus.FAILED
            )
            quote_quality = DatasetQuality(
                status=quote_status,
                data_time=quote.data_time,
                expected_rows=1,
                actual_rows=1 if quote_usable else 0,
                source=quote.source,
                warning=quote.warning,
            )
            quality[CaptureDataset.QUOTE] = quote_quality
            result_repository.upsert(
                MarketCaptureResult(
                    run_id=run_id,
                    symbol=symbol,
                    dataset=CaptureDataset.QUOTE,
                    status=quote_status,
                    data_time=quote.data_time,
                    fetched_at=quote.fetched_at,
                    expected_rows=1,
                    actual_rows=quote_quality.actual_rows,
                    source=quote.source,
                    warning=quote.warning,
                )
            )
            if not quote_usable:
                hard_ready = False

            try:
                provider_started = perf_counter()
                flow = backfill.backfill_money_flow(run_id, symbol, trade_date)
            except Exception as exc:
                self._require_provider_capture_error(exc)
                warning = f"{symbol} 资金流采集失败: {safe_error_summary(exc)}"
                warnings.append(warning)
                quality[CaptureDataset.MONEY_FLOW] = DatasetQuality(
                    status=CaptureResultStatus.FAILED,
                    expected_rows=60,
                    actual_rows=0,
                    source="money_flow_provider",
                    warning=warning,
                )
                result_repository.upsert(
                    MarketCaptureResult(
                        run_id=run_id,
                        symbol=symbol,
                        dataset=CaptureDataset.MONEY_FLOW,
                        status=CaptureResultStatus.FAILED,
                        fetched_at=started_at,
                        expected_rows=60,
                        source="money_flow_provider",
                        error_summary=warning,
                    )
                )
            else:
                provider_calls += flow.provider_calls
                rows_received += flow.rows_received
                rows_written += flow.rows_written
                flow_refs[symbol] = flow.snapshot_id
                quality[CaptureDataset.MONEY_FLOW] = DatasetQuality(
                    status=flow.snapshot.status,
                    data_start=flow.snapshot.data_start,
                    data_end=flow.snapshot.data_end,
                    expected_rows=60,
                    actual_rows=flow.row_count,
                    source="money_flow_provider",
                    warning=flow.snapshot.warning,
                )
                result_repository.upsert(
                    MarketCaptureResult(
                        run_id=run_id,
                        symbol=symbol,
                        dataset=CaptureDataset.MONEY_FLOW,
                        status=flow.snapshot.status,
                        data_start=flow.snapshot.data_start,
                        data_end=flow.snapshot.data_end,
                        fetched_at=flow.snapshot.fetched_at,
                        expected_rows=60,
                        actual_rows=flow.row_count,
                        source="money_flow_provider",
                        warning=flow.snapshot.warning,
                    )
                )
            finally:
                provider_duration_ms += (perf_counter() - provider_started) * 1000
            dataset_quality[symbol] = quality

        data_times = [
            quote.data_time for quote in quotes.values() if quote.data_time is not None
        ]
        market_input = MarketInputSnapshot(
            universe_snapshot_id=universe_snapshot_id,
            quote_snapshot_refs=quote_refs,
            history_snapshot_refs=history_refs,
            money_flow_snapshot_refs=flow_refs,
            intraday_strength_snapshot_refs={},
            dataset_quality=dataset_quality,
            capture_run_id=run_id,
            thresholds={
                "stale_trading_minutes": float(self.stale_trading_minutes),
            },
            data_time=(
                session_close
                if symbols and hard_ready and session_close is not None
                else min(data_times)
                if data_times
                else None
            ),
            fetched_at=started_at,
            warnings=warnings,
        )
        market_input_snapshot_id = MarketInputSnapshotRepository(self.connection).save(
            market_input
        )

        if not symbols:
            run_repository.update_claimed(
                run.model_copy(
                    update={
                        "status": CaptureRunStatus.SUCCEEDED,
                        "finished_at": self._now(),
                        "warning_count": len(warnings),
                    }
                ),
                claim_started_at=run.started_at,
            )
            return CloseWorkflowResult(
                run_id=run_id,
                ready=True,
                reused=False,
                market_input_snapshot_id=market_input_snapshot_id,
                plan_id=None,
                warnings=tuple(warnings),
            )

        if not hard_ready:
            finished = self._now()
            failed_count = sum(
                1
                for quality in dataset_quality.values()
                if any(
                    quality.get(dataset) is None
                    or quality[dataset].status
                    in {CaptureResultStatus.FAILED, CaptureResultStatus.STALE}
                    for dataset in (CaptureDataset.QUOTE, CaptureDataset.DAILY_BAR)
                )
            )
            run_repository.update_claimed(
                run.model_copy(
                    update={
                        "status": (
                            CaptureRunStatus.FAILED
                            if symbols and failed_count == len(symbols)
                            else CaptureRunStatus.DEGRADED
                        ),
                        "finished_at": finished,
                        "processed_symbols": len(symbols),
                        "provider_calls": provider_calls,
                        "provider_duration_ms": provider_duration_ms,
                        "rows_received": rows_received,
                        "rows_written": rows_written,
                        "warning_count": len(warnings),
                        "failure_count": failed_count,
                    }
                ),
                claim_started_at=run.started_at,
            )
            return CloseWorkflowResult(
                run_id=run_id,
                ready=False,
                reused=False,
                market_input_snapshot_id=market_input_snapshot_id,
                plan_id=None,
                warnings=tuple(warnings),
            )

        account_created = create_and_save_account_snapshot_with_connection(
            self.connection,
            market=_CapturedMarketProvider(quotes),
        )
        plan_symbols = self._plan_symbol_inputs(
            decision_members,
            quotes,
            daily_repository,
            flow_repository,
            dataset_quality,
            positions=positions,
            account_snapshot_id=account_created.snapshot_id,
            account_snapshot=account_created.snapshot,
            cash_updated_at=(
                None
                if CashAccountRepository(self.connection).get() is None
                else CashAccountRepository(self.connection).get().updated_at
            ),
        )
        plan_symbols = [
            item.model_copy(
                update={
                    "warnings": _stable_unique(
                        [
                            *item.warnings,
                            *[
                                warning
                                for warning in warnings
                                if warning.startswith(item.symbol)
                            ],
                        ]
                    )
                }
            )
            for item in plan_symbols
        ]
        plan_repository = TradingPlanRepository(self.connection)
        next_day = self.calendar.next_trading_day(trade_date)
        version = plan_repository.next_version(next_day)
        plan = build_market_trading_plan(
            trading_day=next_day,
            now=started_at,
            timezone=str(self.calendar.timezone),
            universe_snapshot_id=universe_snapshot_id,
            account_snapshot_id=account_created.snapshot_id,
            ledger_max_updated_at=max(
                (position.updated_at for position in positions), default=None
            ),
            source_run_id=run_id,
            market_input_snapshot_id=market_input_snapshot_id,
            data_time=market_input.data_time or started_at,
            version=version,
            symbols=plan_symbols,
        )
        active_plan = plan_repository.activate(plan)
        summary_delivery = self._dispatch_close_daily_summary(
            active_plan,
            trade_date=trade_date,
            now=started_at,
        )

        has_degradation = any(
            item.status is not CaptureResultStatus.COMPLETE
            for quality in dataset_quality.values()
            for item in quality.values()
        )
        failure_count = sum(
            item.status is CaptureResultStatus.FAILED
            for quality in dataset_quality.values()
            for item in quality.values()
        )
        run_repository.update_claimed(
            run.model_copy(
                update={
                    "status": (
                        CaptureRunStatus.DEGRADED
                        if has_degradation
                        else CaptureRunStatus.SUCCEEDED
                    ),
                    "finished_at": self._now(),
                    "processed_symbols": len(symbols),
                    "provider_calls": provider_calls,
                    "provider_duration_ms": provider_duration_ms,
                    "rows_received": rows_received,
                    "rows_written": rows_written,
                    "plan_count": 1,
                    "email_outbox_count": int(summary_delivery is not None),
                    "warning_count": len(warnings),
                    "failure_count": failure_count,
                }
            ),
            claim_started_at=run.started_at,
        )
        return CloseWorkflowResult(
            run_id=run_id,
            ready=True,
            reused=False,
            market_input_snapshot_id=market_input_snapshot_id,
            plan_id=active_plan.plan_id,
            warnings=tuple(active_plan.warnings),
        )

    def run_intraday(self) -> IntradayWorkflowResult:
        started_at = self._now()
        local_now = started_at.astimezone(self.calendar.timezone)
        trade_date = local_now.date()
        if not self.calendar.is_trading_day(trade_date):
            raise ValueError("intraday workflow requires an XSHG trading day")
        period_minute = local_now.minute - local_now.minute % 3
        period_start = local_now.replace(minute=period_minute, second=0, microsecond=0)
        run_id = f"intraday-{trade_date:%Y%m%d}-{period_start:%H%M}"
        run_repository = MarketCaptureRunRepository(self.connection)
        run_repository.fail_expired_intraday_runs(
            period_ended_by=period_start,
            lease_started_before=started_at - self.INTRADAY_LEASE,
            finished_at=started_at,
        )
        run_repository.fail_expired_workflow_runs(
            "intraday",
            started_before=started_at - self.INTRADAY_LEASE,
            finished_at=started_at,
        )
        try:
            run, created = run_repository.get_or_create(
                MarketCaptureRun(
                    run_id=run_id,
                    workflow_type="intraday",
                    trade_date=trade_date,
                    period_start=period_start,
                    period_end=period_start + timedelta(minutes=3),
                    idempotency_key=f"intraday:{trade_date.isoformat()}:{period_start:%H%M}",
                    status=CaptureRunStatus.RUNNING,
                    started_at=started_at,
                )
            )
        except CaptureRunAlreadyActiveError as exc:
            raise WorkflowAlreadyRunningError(exc.run_id) from exc
        if not created and run.status in {
            CaptureRunStatus.SUCCEEDED,
            CaptureRunStatus.DEGRADED,
        }:
            existing = [
                item
                for item in RecommendationRepository(self.connection).list(limit=2000)
                if item.run_id == run_id
            ]
            snapshot_id = (
                existing[0].market_input_snapshot_id
                if existing and existing[0].market_input_snapshot_id is not None
                else self._market_input_id_for_run(run_id)
            )
            projection_warnings: list[str] = []
            recovered_notification_count = 0
            recovered_outbox_count = 0
            if self.notification_dispatcher is not None:
                for recommendation in existing:
                    dispatch = self.notification_dispatcher.dispatch_recommendation(
                        recommendation,
                        plan_version=recommendation.plan_version,
                        now=started_at,
                    )
                    if dispatch is None:
                        continue
                    recovered_notification_count += int(
                        bool(getattr(dispatch, "created", False))
                    )
                    recovered_outbox_count += int(
                        getattr(dispatch, "email_delivery", None) is not None
                    )
                    projection_warnings.extend(getattr(dispatch, "warnings", ()))
            notification_count = max(
                run.notification_count,
                recovered_notification_count,
            )
            email_outbox_count = max(
                run.email_outbox_count,
                recovered_outbox_count,
            )
            if (
                notification_count != run.notification_count
                or email_outbox_count != run.email_outbox_count
            ):
                run = run.model_copy(
                    update={
                        "notification_count": notification_count,
                        "email_outbox_count": email_outbox_count,
                    }
                )
                run_repository.update(run)
            return IntradayWorkflowResult(
                run_id=run_id,
                reused=True,
                status=run.status,
                market_input_snapshot_id=snapshot_id,
                recommendation_ids=tuple(item.recommendation_id for item in existing),
                warnings=tuple(projection_warnings),
            )
        if not created:
            run = self._claim_retry_or_raise(
                run_repository,
                run,
                started_at=started_at,
                stale_after=self.INTRADAY_LEASE,
                retryable_statuses={CaptureRunStatus.FAILED},
            )

        try:
            return self._run_intraday_claimed(
                started_at=started_at,
                trade_date=trade_date,
                run=run,
                run_repository=run_repository,
            )
        except Exception as exc:
            self._mark_capture_run_failed(
                run_repository,
                run.run_id,
                exc,
                fallback_time=started_at,
            )
            raise

    def _run_intraday_claimed(
        self,
        *,
        started_at: datetime,
        trade_date: date,
        run: MarketCaptureRun,
        run_repository: MarketCaptureRunRepository,
    ) -> IntradayWorkflowResult:
        run_id = run.run_id

        positions = PositionRepository(self.connection).list()
        positions_by_symbol = {position.symbol: position for position in positions}
        plan_repository = TradingPlanRepository(self.connection)
        plan = plan_repository.active_for_day(trade_date)
        if plan is not None and started_at > plan.valid_until:
            plan_repository.mark_expired(plan)
            plan = None
        elif plan is not None and self._plan_authority_context_changed(plan, positions):
            plan_repository.mark_stale(
                plan,
                warning="手动持仓台账或手动资金在计划生成后发生变化",
            )
            plan = None
        plan_symbols = set() if plan is None else set(plan.symbol_contexts)
        symbols = sorted(set(positions_by_symbol) | plan_symbols)

        current_members = build_universe(
            positions=positions,
            watchlist=WatchPinnedRepository(self.connection).list(),
            created_at=started_at,
        )
        member_by_symbol = {member.symbol: member for member in current_members}
        universe_snapshot_id = UniverseSnapshotRepository(self.connection).save(
            UniverseSnapshot(
                created_at=started_at,
                status=UniverseSnapshotStatus.OK,
                warnings=[] if symbols else ["盘中决策集合为空"],
                members=[
                    member_by_symbol[symbol]
                    for symbol in symbols
                    if symbol in member_by_symbol
                ],
            )
        )
        run = run.model_copy(update={"requested_symbols": len(symbols)})
        run_repository.update_claimed(run, claim_started_at=run.started_at)

        quote_refs, quotes, warnings, provider_duration_ms = self._capture_quotes(
            run_id, symbols, started_at
        )
        result_repository = MarketCaptureResultRepository(self.connection)
        dataset_quality: dict[str, dict[CaptureDataset, DatasetQuality]] = {}
        for symbol, quote in quotes.items():
            quote_usable = (
                quote.status in {QuoteStatus.OK, QuoteStatus.PARTIAL}
                and quote.data_time is not None
            )
            status = (
                CaptureResultStatus.COMPLETE
                if quote.status is QuoteStatus.OK
                else CaptureResultStatus.DEGRADED
                if quote_usable
                else CaptureResultStatus.STALE
                if quote.status is QuoteStatus.STALE
                else CaptureResultStatus.FAILED
            )
            dataset_quality[symbol] = {
                CaptureDataset.QUOTE: DatasetQuality(
                    status=status,
                    data_time=quote.data_time,
                    expected_rows=1,
                    actual_rows=1 if quote_usable else 0,
                    source=quote.source,
                    warning=quote.warning,
                )
            }
            result_repository.upsert(
                MarketCaptureResult(
                    run_id=run_id,
                    symbol=symbol,
                    dataset=CaptureDataset.QUOTE,
                    status=status,
                    data_time=quote.data_time,
                    fetched_at=quote.fetched_at,
                    expected_rows=1,
                    actual_rows=1 if quote_usable else 0,
                    source=quote.source,
                    warning=quote.warning,
                )
            )

        close_input = (
            None
            if plan is None or plan.market_input_snapshot_id is None
            else MarketInputSnapshotRepository(self.connection).get(
                plan.market_input_snapshot_id
            )
        )
        history_refs = (
            {}
            if close_input is None
            else {
                symbol: snapshot_id
                for symbol, snapshot_id in close_input.history_snapshot_refs.items()
                if symbol in symbols
            }
        )
        flow_refs = (
            {}
            if close_input is None
            else {
                symbol: snapshot_id
                for symbol, snapshot_id in close_input.money_flow_snapshot_refs.items()
                if symbol in symbols
            }
        )
        if close_input is not None:
            for symbol in symbols:
                for dataset in (CaptureDataset.DAILY_BAR, CaptureDataset.MONEY_FLOW):
                    quality = close_input.dataset_quality.get(symbol, {}).get(dataset)
                    if quality is not None:
                        dataset_quality.setdefault(symbol, {})[dataset] = quality

        minute_repository = MinuteBarRepository(self.connection)
        daily_repository = DailyBarRepository(self.connection)
        strength_repository = IntradayStrengthSnapshotRepository(self.connection)
        strength_refs: dict[str, int] = {}
        strength_by_symbol = {}
        provider_calls = 1 if symbols else 0
        rows_received = len(quotes)
        rows_written = len(quote_refs)
        for symbol in symbols:
            try:
                provider_calls += 1
                provider_started = perf_counter()
                try:
                    fetched_minute_bars = list(
                        self.intraday_provider.get_minute_bars(
                            symbol, trade_date, "1m"
                        )
                    )
                finally:
                    provider_duration_ms += (
                        perf_counter() - provider_started
                    ) * 1000
                rows_received += len(fetched_minute_bars)
                rows_written += minute_repository.upsert_many(fetched_minute_bars)
                minute_bars = minute_repository.for_trade_date(symbol, trade_date)
                daily_bars = daily_repository.current(symbol, limit=2)
                previous_daily = (
                    None
                    if not daily_bars
                    else next(
                        (
                            item.bar
                            for item in reversed(daily_bars)
                            if item.bar.trade_date < trade_date
                        ),
                        None,
                    )
                )
                strength = calculate_intraday_strength(
                    run_id,
                    quotes[symbol],
                    minute_bars,
                    self.calendar,
                    previous_daily_bar=previous_daily,
                    rules=self.strength_rules,
                    fetched_at=started_at,
                )
                strength = strength.model_copy(
                    update={
                        "thresholds": {
                            **strength.thresholds,
                            "stale_minutes": float(self.stale_trading_minutes),
                        }
                    }
                )
                minute_stale = bool(
                    minute_bars
                    and _trading_minute_lag(
                        self.calendar,
                        max(bar.minute for bar in minute_bars),
                        started_at,
                    )
                    > self.stale_trading_minutes
                )
                if minute_stale:
                    stale_warning = (
                        "分时数据落后超过 "
                        f"{self.stale_trading_minutes} 个有效交易分钟"
                    )
                    warnings.append(f"{symbol} {stale_warning}")
                    strength = strength.model_copy(
                        update={
                            "degraded": True,
                            "degradation_reasons": _stable_unique(
                                [*strength.degradation_reasons, stale_warning]
                            ),
                        }
                    )
                strength_id = strength_repository.save(strength)
            except Exception as exc:
                self._require_provider_capture_error(exc)
                warning = f"{symbol} 分时采集或强弱计算失败: {safe_error_summary(exc)}"
                warnings.append(warning)
                dataset_quality.setdefault(symbol, {})[CaptureDataset.MINUTE_BAR] = (
                    DatasetQuality(
                        status=CaptureResultStatus.FAILED,
                        expected_rows=1,
                        actual_rows=0,
                        source="intraday_provider",
                        warning=warning,
                    )
                )
                dataset_quality[symbol][CaptureDataset.INTRADAY_STRENGTH] = (
                    DatasetQuality(
                        status=CaptureResultStatus.FAILED,
                        expected_rows=1,
                        actual_rows=0,
                        source="intraday_strength_v1",
                        warning=warning,
                    )
                )
                result_repository.upsert(
                    MarketCaptureResult(
                        run_id=run_id,
                        symbol=symbol,
                        dataset=CaptureDataset.MINUTE_BAR,
                        status=CaptureResultStatus.FAILED,
                        fetched_at=started_at,
                        expected_rows=1,
                        source="intraday_provider",
                        error_summary=warning,
                    )
                )
                result_repository.upsert(
                    MarketCaptureResult(
                        run_id=run_id,
                        symbol=symbol,
                        dataset=CaptureDataset.INTRADAY_STRENGTH,
                        status=CaptureResultStatus.FAILED,
                        fetched_at=started_at,
                        expected_rows=1,
                        source="intraday_strength_v1",
                        error_summary=warning,
                    )
                )
                continue

            strength_refs[symbol] = strength_id
            strength_by_symbol[symbol] = strength
            minute_status = (
                CaptureResultStatus.STALE
                if minute_stale
                else CaptureResultStatus.COMPLETE
                if minute_bars
                else CaptureResultStatus.FAILED
            )
            strength_status = (
                CaptureResultStatus.STALE
                if minute_stale
                else CaptureResultStatus.DEGRADED
                if strength.degraded
                else CaptureResultStatus.COMPLETE
            )
            dataset_quality.setdefault(symbol, {})[CaptureDataset.MINUTE_BAR] = (
                DatasetQuality(
                    status=minute_status,
                    data_time=strength.data_time,
                    expected_rows=self.calendar.expected_minutes_through(started_at),
                    actual_rows=len(minute_bars),
                    source=strength.source,
                )
            )
            dataset_quality[symbol][CaptureDataset.INTRADAY_STRENGTH] = DatasetQuality(
                status=strength_status,
                data_time=strength.data_time,
                expected_rows=1,
                actual_rows=1,
                source=strength.rule_version,
                warning="; ".join(strength.degradation_reasons),
            )
            for dataset, status, actual in (
                (CaptureDataset.MINUTE_BAR, minute_status, len(minute_bars)),
                (CaptureDataset.INTRADAY_STRENGTH, strength_status, 1),
            ):
                result_repository.upsert(
                    MarketCaptureResult(
                        run_id=run_id,
                        symbol=symbol,
                        dataset=dataset,
                        status=status,
                        data_time=strength.data_time,
                        fetched_at=started_at,
                        expected_rows=(
                            self.calendar.expected_minutes_through(started_at)
                            if dataset is CaptureDataset.MINUTE_BAR
                            else 1
                        ),
                        actual_rows=actual,
                        source=strength.source,
                        warning="; ".join(strength.degradation_reasons),
                    )
                )

        data_times = [
            value
            for value in [
                *[quote.data_time for quote in quotes.values()],
                *[strength.data_time for strength in strength_by_symbol.values()],
            ]
            if value is not None
        ]
        market_input = MarketInputSnapshot(
            universe_snapshot_id=universe_snapshot_id,
            quote_snapshot_refs=quote_refs,
            history_snapshot_refs=history_refs,
            money_flow_snapshot_refs=flow_refs,
            intraday_strength_snapshot_refs=strength_refs,
            dataset_quality=dataset_quality,
            capture_run_id=run_id,
            thresholds={
                "stale_trading_minutes": float(self.stale_trading_minutes),
            },
            data_time=min(data_times) if data_times else None,
            fetched_at=started_at,
            warnings=warnings,
        )
        market_input_id = MarketInputSnapshotRepository(self.connection).save(
            market_input
        )
        account_created = create_and_save_account_snapshot_with_connection(
            self.connection,
            market=_CapturedMarketProvider(quotes),
        )
        account_snapshot = account_created.snapshot
        valuation_by_symbol = {
            valuation.symbol: valuation for valuation in account_snapshot.positions
        }
        flow_repository = MoneyFlowRepository(self.connection)
        risk_config = RiskConfig()
        risk_state = _risk_context_state(
            self.connection,
            calendar=self.calendar,
            trade_date=trade_date,
            config=risk_config,
        )
        reserved_new_buy_value = risk_state.daily_new_buy_value
        reserved_trade_count = risk_state.daily_trade_count
        recommendations = []
        for symbol in symbols:
            position = positions_by_symbol.get(symbol)
            valuation = valuation_by_symbol.get(symbol)
            plan_context = None if plan is None else plan.symbol_contexts.get(symbol)
            strength = strength_by_symbol.get(symbol)
            quote = quotes[symbol]
            verified_price = (
                quote.current_price
                if quote.status in {QuoteStatus.OK, QuoteStatus.PARTIAL}
                and quote.data_time is not None
                else None
            )
            decision_data_time = (
                quote.data_time
                or (None if strength is None else strength.data_time)
                or market_input.data_time
            )
            if decision_data_time is None:
                raise ValueError("decision requires verified market data time")
            current_flows = flow_repository.current(symbol, limit=1)
            money_flow_confirmed = (
                None
                if not current_flows
                else current_flows[-1].flow.main_net_amount > 0
            )
            metrics = {
                "current_price": verified_price,
                "intraday_strength": (
                    "neutral" if strength is None else strength.label.value
                ),
                "money_flow_positive": money_flow_confirmed,
            }
            condition_evaluation = (
                None
                if plan_context is None
                else evaluate_plan_conditions(plan_context.conditions, metrics)
            )
            is_holding = position is not None
            allowed_entry_action = "add" if is_holding else "buy"
            plan_allows_entry = (
                plan_context is not None
                and allowed_entry_action in plan_context.allowed_actions
            )
            quality_values = dataset_quality.get(symbol, {})
            required_statuses = [
                quality_values.get(CaptureDataset.QUOTE),
                quality_values.get(CaptureDataset.DAILY_BAR),
                quality_values.get(CaptureDataset.INTRADAY_STRENGTH),
            ]
            if any(
                item is None or item.status is CaptureResultStatus.FAILED
                for item in required_statuses
            ):
                overall_quality = "failed"
            elif any(
                item.status is CaptureResultStatus.STALE
                for item in required_statuses
            ):
                overall_quality = "stale"
            elif any(
                item.status is not CaptureResultStatus.COMPLETE
                for item in required_statuses
            ):
                overall_quality = "degraded"
            elif (
                quality_values.get(CaptureDataset.MONEY_FLOW) is None
                or quality_values[CaptureDataset.MONEY_FLOW].status
                is not CaptureResultStatus.COMPLETE
            ):
                overall_quality = "degraded"
            else:
                overall_quality = "complete"

            levels = {} if plan is None else plan.key_levels.get(symbol, {})
            position_context: dict[str, Any]
            if position is None:
                position_context = {
                    "source": "manual_ledger",
                    "quantity": 0,
                    "available_quantity": 0,
                    "market_value": 0,
                }
            else:
                position_context = {
                    **position.model_dump(mode="json"),
                    "source": "manual_ledger",
                    "market_value": None
                    if valuation is None
                    else valuation.market_value,
                }
            data_references = self._decision_references(
                symbol,
                market_input,
                plan_id=None if plan is None else plan.plan_id,
                account_snapshot_id=account_created.snapshot_id,
                account_status=(
                    "complete"
                    if account_snapshot.status.value == "ok"
                    else account_snapshot.status.value
                ),
                account_warnings=account_snapshot.warnings,
                ledger_updated_at=None if position is None else position.updated_at,
            )
            invalid_if = (
                ["当日收盘后失效", "数据质量进一步下降"]
                if plan_context is None or not plan_context.invalid_if
                else plan_context.invalid_if
            )
            decision_input = DecisionSymbolInput(
                symbol=symbol,
                name=(
                    position.name
                    if position is not None
                    else plan_context.name
                    if plan_context is not None
                    else symbol
                ),
                is_holding=is_holding,
                current_price=verified_price,
                support_price=levels.get("support"),
                stop_loss_price=levels.get("stop_loss"),
                short_ma=(
                    None
                    if plan_context is None
                    else _number(plan_context.trend.get("ma5"))
                ),
                plan_id=None if plan is None else plan.plan_id,
                plan_active=plan is not None,
                plan_allows_entry=plan_allows_entry,
                plan_condition_met=(
                    False
                    if condition_evaluation is None
                    else condition_evaluation.matched
                ),
                daily_structure_confirmed=_daily_structure_confirmed(
                    plan_context,
                    verified_price,
                ),
                intraday_strength=(
                    "neutral" if strength is None else strength.label.value
                ),
                money_flow_confirmed=money_flow_confirmed,
                data_quality=overall_quality,
                trading_status=quote.trading_status.value,
                limit_status=quote.limit_status.value,
                position_context=position_context,
                account_context={
                    **account_snapshot.model_dump(mode="json"),
                    "snapshot_id": account_created.snapshot_id,
                    "source": "manual_cash_account_and_market_snapshot",
                },
                price_context={
                    "current_price": verified_price,
                    "change_pct": quote.change_pct,
                    "key_levels": levels,
                },
                data_references=data_references,
                invalid_if=invalid_if,
                warnings=_stable_unique([*warnings, *account_snapshot.warnings]),
                data_time=decision_data_time,
                fetched_at=market_input.fetched_at,
                valid_until=(
                    plan.valid_until
                    if plan is not None
                    else datetime.combine(
                        trade_date,
                        time(15, 0),
                        tzinfo=self.calendar.timezone,
                    )
                ),
                run_id=run_id,
                market_input_snapshot_id=market_input_id,
            )
            proposed_value = (
                0
                if account_snapshot.total_assets is None
                else account_snapshot.total_assets
                * risk_config.first_watch_position_min
            )
            recommendation = decide_symbol(
                decision_input,
                account_snapshot=account_snapshot,
                risk_config=risk_config,
                risk_context=risk_state.model_copy(
                    update={
                        "proposed_value": proposed_value,
                        "daily_new_buy_value": reserved_new_buy_value,
                        "daily_trade_count": reserved_trade_count,
                        "liquidity_amount": quote.amount,
                    }
                ),
                recommendation_id=f"rec-{run_id}-{symbol}",
                created_at=started_at,
            )
            recommendation = recommendation.model_copy(
                update={
                    "condition_context": {
                        "plan_conditions": (
                            []
                            if plan_context is None
                            else [
                                condition.model_dump(mode="json")
                                for condition in plan_context.conditions
                            ]
                        ),
                        "evaluation": (
                            []
                            if condition_evaluation is None
                            else [
                                asdict(item) for item in condition_evaluation.items
                            ]
                        ),
                    }
                }
            )
            recommendation = with_recommendation_identity(
                recommendation,
                trade_date=trade_date,
                period_start=run.period_start or started_at,
                plan_version=None if plan is None else plan.version,
            )
            recommendations.append(recommendation)
            if recommendation.action in {
                RecommendationAction.BUY,
                RecommendationAction.ADD,
            }:
                reserved_trade_count += 1
                suggested_value = recommendation.position_constraint.get(
                    "suggested_value",
                    proposed_value,
                )
                reserved_new_buy_value += (
                    float(suggested_value)
                    if isinstance(suggested_value, int | float)
                    else proposed_value
                )
            elif recommendation.action in {
                RecommendationAction.SELL,
                RecommendationAction.REDUCE,
            }:
                reserved_trade_count += 1

        notification_count = 0
        email_outbox_count = 0
        transactional_dispatcher = (
            self.notification_dispatcher
            if self.notification_dispatcher is not None
            and callable(
                getattr(
                    self.notification_dispatcher,
                    "persist_local_recommendation",
                    None,
                )
            )
            else None
        )
        local_dispatches = []
        if transactional_dispatcher is not None:
            savepoint = "recommendation_notification_stage"
            self.connection.execute(f"SAVEPOINT {savepoint}")
            try:
                recommendations = self._persist_recommendations_with_audits(
                    recommendations,
                    created_at=started_at,
                    commit=False,
                )
                local_dispatches = [
                    transactional_dispatcher.persist_local_recommendation(
                        recommendation,
                        plan_version=None if plan is None else plan.version,
                        now=started_at,
                        commit=False,
                    )
                    for recommendation in recommendations
                ]
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                self.connection.commit()
            except BaseException:
                if self.connection.in_transaction:
                    self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                self.connection.rollback()
                raise
            for recommendation, local in zip(
                recommendations,
                local_dispatches,
                strict=True,
            ):
                dispatch = transactional_dispatcher.project_recommendation(
                    recommendation,
                    local,
                    plan_version=None if plan is None else plan.version,
                    now=started_at,
                )
                notification_count += int(bool(getattr(dispatch, "created", False)))
                email_outbox_count += int(
                    getattr(dispatch, "email_delivery", None) is not None
                )
                warnings.extend(getattr(dispatch, "warnings", ()))
        else:
            recommendations = self._persist_recommendations_with_audits(
                recommendations,
                created_at=started_at,
            )
        if self.notification_dispatcher is not None and transactional_dispatcher is None:
            for recommendation in recommendations:
                dispatch = self.notification_dispatcher.dispatch_recommendation(
                    recommendation,
                    plan_version=None if plan is None else plan.version,
                    now=started_at,
                )
                if dispatch is not None:
                    notification_count += int(bool(getattr(dispatch, "created", False)))
                    email_outbox_count += int(
                        getattr(dispatch, "email_delivery", None) is not None
                    )
                    warnings.extend(getattr(dispatch, "warnings", ()))

        degraded = any(
            item.status is not CaptureResultStatus.COMPLETE
            for quality in dataset_quality.values()
            for item in quality.values()
        )
        unusable_quotes = sum(
            1
            for symbol in symbols
            if dataset_quality[symbol][CaptureDataset.QUOTE].status
            in {CaptureResultStatus.FAILED, CaptureResultStatus.STALE}
        )
        final_status = (
            CaptureRunStatus.FAILED
            if symbols and unusable_quotes == len(symbols)
            else CaptureRunStatus.DEGRADED
            if degraded
            else CaptureRunStatus.SUCCEEDED
        )
        run_repository.update_claimed(
            run.model_copy(
                update={
                    "status": final_status,
                    "finished_at": self._now(),
                    "processed_symbols": len(symbols),
                    "provider_calls": provider_calls,
                    "provider_duration_ms": provider_duration_ms,
                    "rows_received": rows_received,
                    "rows_written": rows_written + len(recommendations),
                    "recommendation_count": len(recommendations),
                    "notification_count": notification_count,
                    "email_outbox_count": email_outbox_count,
                    "warning_count": len(warnings),
                    "failure_count": unusable_quotes,
                }
            ),
            claim_started_at=run.started_at,
        )
        return IntradayWorkflowResult(
            run_id=run_id,
            reused=False,
            status=final_status,
            market_input_snapshot_id=market_input_id,
            recommendation_ids=tuple(
                recommendation.recommendation_id for recommendation in recommendations
            ),
            warnings=tuple(warnings),
        )

    def _verify_close_quote_from_daily_bar(
        self,
        quote: QuoteSnapshot,
        *,
        trade_date: date,
        daily_repository: DailyBarRepository,
        observed_at: datetime,
    ) -> QuoteSnapshot:
        if (
            quote.status not in {QuoteStatus.OK, QuoteStatus.PARTIAL}
            or quote.data_time is not None
            or quote.current_price is None
        ):
            return quote
        session_close = self.calendar.session(trade_date).close_at
        if observed_at < session_close:
            return quote
        current_bars = daily_repository.current(quote.symbol, limit=1)
        if not current_bars:
            return quote
        daily_bar = current_bars[-1].bar
        if daily_bar.trade_date != trade_date or not _prices_match(
            quote.current_price,
            daily_bar.close,
        ):
            return quote
        verification_warning = "market time verified against same-day daily close"
        return quote.model_copy(
            update={
                "data_time": session_close,
                "status": QuoteStatus.PARTIAL,
                "warning": "; ".join(
                    value
                    for value in (quote.warning, verification_warning)
                    if value
                ),
            }
        )

    def _capture_quotes(
        self,
        run_id: str,
        symbols: list[str],
        fetched_at: datetime,
    ) -> tuple[dict[str, int], dict[str, QuoteSnapshot], list[str], float]:
        if not symbols:
            return {}, {}, ["决策启用集合为空"], 0.0
        provider_started = perf_counter()
        try:
            returned = self.quote_provider.get_quotes(symbols)
        except MarketProviderError as exc:
            summary = safe_error_summary(exc)
            returned = {}
            provider_warning = f"批量报价失败: {summary}"
        else:
            provider_warning = ""
        provider_duration_ms = (perf_counter() - provider_started) * 1000

        repository = QuoteSnapshotRepository(self.connection)
        refs: dict[str, int] = {}
        quotes: dict[str, QuoteSnapshot] = {}
        warnings: list[str] = []
        for symbol in symbols:
            quote = returned.get(symbol)
            if quote is None or quote.symbol != symbol:
                quote = QuoteSnapshot(
                    symbol=symbol,
                    fetched_at=fetched_at,
                    source="decision_workflow",
                    status=QuoteStatus.FAILED,
                    warning=provider_warning
                    or "provider did not return requested quote",
                )
            elif (
                quote.status in {QuoteStatus.OK, QuoteStatus.PARTIAL}
                and quote.data_time is None
            ):
                quote = quote.model_copy(
                    update={
                        "status": QuoteStatus.PARTIAL,
                        "warning": "; ".join(
                            value
                            for value in (
                                quote.warning,
                                "market source time unavailable",
                            )
                            if value
                        ),
                    }
                )
            elif (
                quote.status in {QuoteStatus.OK, QuoteStatus.PARTIAL}
                and quote.data_time is not None
                and _trading_minute_lag(
                    self.calendar,
                    quote.data_time,
                    fetched_at,
                )
                > self.stale_trading_minutes
            ):
                stale_warning = (
                    "quote data lags by more than "
                    f"{self.stale_trading_minutes} trading minutes"
                )
                quote = quote.model_copy(
                    update={
                        "status": QuoteStatus.STALE,
                        "warning": "; ".join(
                            value
                            for value in (quote.warning, stale_warning)
                            if value
                        ),
                    }
                )
            if quote.status is not QuoteStatus.OK:
                warnings.append(
                    f"{symbol} 报价状态 {quote.status.value}: {quote.warning}"
                )
            refs[symbol] = repository.save(quote, commit=False)
            quotes[symbol] = quote
        self.connection.commit()
        return refs, quotes, warnings, provider_duration_ms

    def _dispatch_close_daily_summary(
        self,
        plan,
        *,
        trade_date: date,
        now: datetime,
    ) -> Any:
        if self.notification_dispatcher is None:
            return None
        summary_actions = {
            RecommendationAction.HOLD,
            RecommendationAction.WATCH,
            RecommendationAction.AVOID,
        }
        daily_recommendations = [
            recommendation
            for recommendation in RecommendationRepository(self.connection).list(
                limit=2000
            )
            if recommendation.action in summary_actions
            and recommendation.created_at.astimezone(self.calendar.timezone).date()
            == trade_date
        ]
        return self.notification_dispatcher.dispatch_daily_summary(
            plan_id=plan.plan_id,
            plan_version=plan.version,
            recommendations=daily_recommendations,
            now=now,
        )

    def _plan_symbol_inputs(
        self,
        members,
        quotes: dict[str, QuoteSnapshot],
        daily_repository: DailyBarRepository,
        flow_repository: MoneyFlowRepository,
        dataset_quality: dict[str, dict[CaptureDataset, DatasetQuality]],
        *,
        positions,
        account_snapshot_id: int,
        account_snapshot,
        cash_updated_at: datetime | None,
    ) -> list[MarketPlanSymbolInput]:
        positions_by_symbol = {position.symbol: position for position in positions}
        results: list[MarketPlanSymbolInput] = []
        for member in members:
            quote = quotes[member.symbol]
            bars = [
                stored.bar
                for stored in daily_repository.current(member.symbol, limit=250)
            ]
            features = calculate_daily_features(bars)
            structure = select_market_structure(quote.current_price, bars, features)  # type: ignore[arg-type]
            feature_values = {
                name: getattr(features, name).value
                for name in features.__dataclass_fields__
            }
            feature_facts = {
                name: asdict(getattr(features, name))
                for name in features.__dataclass_fields__
            }
            current_flows = flow_repository.current(member.symbol, limit=60)
            money_flow = (
                {"status": "failed"}
                if not current_flows
                else {
                    "status": "complete",
                    **current_flows[-1].flow.model_dump(
                        mode="json",
                        exclude={"content_hash"},
                    ),
                }
            )
            symbol_quality = dataset_quality.get(member.symbol, {})
            relevant_quality = [
                symbol_quality.get(CaptureDataset.QUOTE),
                symbol_quality.get(CaptureDataset.DAILY_BAR),
                symbol_quality.get(CaptureDataset.MONEY_FLOW),
            ]
            quality = (
                "complete"
                if all(
                    item is not None
                    and item.status is CaptureResultStatus.COMPLETE
                    for item in relevant_quality
                )
                else "degraded"
            )
            quality_warnings = _stable_unique(
                [
                    item.warning
                    for item in relevant_quality
                    if item is not None and item.warning
                ]
            )
            results.append(
                MarketPlanSymbolInput(
                    symbol=member.symbol,
                    name=member.name,
                    sources=[source.value for source in member.sources],
                    is_holding=any(
                        source.value == "holding" for source in member.sources
                    ),
                    current_price=quote.current_price,
                    daily_features=feature_values,
                    daily_feature_facts=feature_facts,
                    market_structure=asdict(structure),
                    money_flow=money_flow,
                    position_context=self._plan_position_context(
                        positions_by_symbol.get(member.symbol)
                    ),
                    account_context={
                        "snapshot_id": account_snapshot_id,
                        "source": "manual_cash_account_and_market_snapshot",
                        "status": account_snapshot.status.value,
                        "created_at": account_snapshot.created_at.isoformat(),
                        "cash_updated_at": (
                            None
                            if cash_updated_at is None
                            else cash_updated_at.isoformat()
                        ),
                        "total_assets": account_snapshot.total_assets,
                        "position_ratio": account_snapshot.position_ratio,
                        "available_buying_cash": account_snapshot.available_buying_cash,
                        "warnings": list(account_snapshot.warnings),
                    },
                    data_quality=quality,
                    warnings=quality_warnings,
                )
            )
        return results

    @staticmethod
    def _plan_position_context(position) -> dict[str, Any]:
        if position is None:
            return {
                "source": "manual_ledger",
                "status": "no_position",
                "quantity": 0,
                "available_quantity": 0,
                "cost_price": None,
                "updated_at": None,
            }
        return {
            "source": "manual_ledger",
            "status": "holding",
            "symbol": position.symbol,
            "name": position.name,
            "quantity": position.quantity,
            "available_quantity": position.available_quantity,
            "cost_price": position.cost_price,
            "opened_at": position.opened_at.isoformat(),
            "updated_at": position.updated_at.isoformat(),
            "note": position.note,
        }

    def _plan_authority_context_changed(self, plan, positions) -> bool:
        current_positions = {position.symbol: position for position in positions}
        planned_symbols = set(plan.symbol_contexts)
        for symbol in planned_symbols | set(current_positions):
            context = (
                None
                if symbol not in plan.symbol_contexts
                else plan.symbol_contexts[symbol].position_context
            )
            current = current_positions.get(symbol)
            if context is None:
                return True
            planned_updated_at = context.get("updated_at")
            current_updated_at = None if current is None else current.updated_at.isoformat()
            if planned_updated_at != current_updated_at:
                return True
        cash = CashAccountRepository(self.connection).get()
        current_cash_updated_at = None if cash is None else cash.updated_at.isoformat()
        planned_cash_times = {
            context.account_context.get("cash_updated_at")
            for context in plan.symbol_contexts.values()
        }
        return bool(
            planned_cash_times
            and planned_cash_times != {current_cash_updated_at}
        )

    def _persist_recommendations_with_audits(
        self,
        recommendations: list[Recommendation],
        *,
        created_at: datetime,
        commit: bool = True,
    ) -> list[Recommendation]:
        savepoint = "persist_recommendations_with_audits"
        self.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            audited = []
            audit_repository = AuditLogRepository(self.connection)
            for recommendation in recommendations:
                audit_id = f"audit-{recommendation.recommendation_id}"
                audited_recommendation = recommendation.model_copy(
                    update={"audit_id": audit_id}
                )
                audit_repository.save(
                    AuditLog(
                        audit_id=audit_id,
                        event_type="recommendation.generated",
                        recommendation_id=recommendation.recommendation_id,
                        payload={
                            "symbol": recommendation.symbol,
                            "action": recommendation.action.value,
                            "run_id": recommendation.run_id,
                            "plan_id": recommendation.plan_id,
                            "plan_version": recommendation.plan_version,
                            "condition_fingerprint": (
                                recommendation.condition_fingerprint
                            ),
                        },
                        created_at=created_at,
                    ),
                    commit=False,
                )
                audited.append(audited_recommendation)
            saved = RecommendationRepository(self.connection).save_many(
                audited,
                created_at=created_at,
                commit=False,
            )
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        if commit:
            self.connection.commit()
        return saved

    def _decision_references(
        self,
        symbol: str,
        snapshot: MarketInputSnapshot,
        *,
        plan_id: str | None,
        account_snapshot_id: int,
        account_status: str,
        account_warnings: list[str],
        ledger_updated_at: datetime | None,
    ) -> dict[str, dict[str, Any]]:
        def reference(
            mapping: dict[str, int],
            dataset: CaptureDataset,
        ) -> dict[str, Any]:
            quality = snapshot.dataset_quality.get(symbol, {}).get(dataset)
            reference_id = mapping.get(symbol)
            fetched_at = None
            if reference_id is not None:
                if dataset is CaptureDataset.QUOTE:
                    referenced = QuoteSnapshotRepository(self.connection).get(
                        reference_id
                    )
                elif dataset is CaptureDataset.DAILY_BAR:
                    referenced = HistorySnapshotRepository(self.connection).get(
                        reference_id
                    )
                elif dataset is CaptureDataset.MONEY_FLOW:
                    referenced = MoneyFlowSnapshotRepository(self.connection).get(
                        reference_id
                    )
                elif dataset is CaptureDataset.INTRADAY_STRENGTH:
                    referenced = IntradayStrengthSnapshotRepository(
                        self.connection
                    ).get(reference_id)
                else:
                    referenced = None
                fetched_at = None if referenced is None else referenced.fetched_at
            return {
                "snapshot_id": reference_id,
                "status": "missing" if quality is None else quality.status.value,
                "source": "" if quality is None else quality.source,
                "data_start": (
                    None
                    if quality is None or quality.data_start is None
                    else quality.data_start.isoformat()
                ),
                "data_end": (
                    None
                    if quality is None or quality.data_end is None
                    else quality.data_end.isoformat()
                ),
                "data_time": (
                    None
                    if quality is None or quality.data_time is None
                    else quality.data_time.isoformat()
                ),
                "fetched_at": (
                    None if fetched_at is None else fetched_at.isoformat()
                ),
            }

        return {
            "ledger": {
                "updated_at": None
                if ledger_updated_at is None
                else ledger_updated_at.isoformat(),
                "status": "missing" if ledger_updated_at is None else "complete",
            },
            "account": {
                "snapshot_id": account_snapshot_id,
                "status": account_status,
                "warnings": account_warnings,
            },
            "quote": reference(snapshot.quote_snapshot_refs, CaptureDataset.QUOTE),
            "history": reference(
                snapshot.history_snapshot_refs, CaptureDataset.DAILY_BAR
            ),
            "money_flow": reference(
                snapshot.money_flow_snapshot_refs, CaptureDataset.MONEY_FLOW
            ),
            "intraday": reference(
                snapshot.intraday_strength_snapshot_refs,
                CaptureDataset.INTRADAY_STRENGTH,
            ),
            "plan": {
                "plan_id": plan_id,
                "status": "missing" if plan_id is None else "active",
            },
        }

    def _market_input_id_for_run(self, run_id: str) -> int:
        rows = self.connection.execute(
            "SELECT id, payload_json FROM market_input_snapshots ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            snapshot = MarketInputSnapshot.model_validate_json(row["payload_json"])
            if snapshot.capture_run_id == run_id:
                return int(row["id"])
        raise KeyError(f"market input snapshot not found for run: {run_id}")

    def _now(self) -> datetime:
        value = self.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision workflow now must be timezone-aware")
        return value


class _CapturedMarketProvider:
    def __init__(self, quotes: dict[str, QuoteSnapshot]) -> None:
        self.quotes = quotes

    def get_quotes(self, symbols) -> dict[str, QuoteSnapshot]:
        return {
            symbol: self.quotes[symbol] for symbol in symbols if symbol in self.quotes
        }


def _stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _number(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _daily_structure_confirmed(plan_context, current_price: float | None) -> bool:
    if plan_context is None or current_price is None:
        return False
    ma5 = _number(plan_context.trend.get("ma5"))
    ma10 = _number(plan_context.trend.get("ma10"))
    ma20 = _number(plan_context.trend.get("ma20"))
    return (
        ma5 is not None
        and ma10 is not None
        and ma20 is not None
        and current_price >= ma20
        and ma5 >= ma10 >= ma20
    )


def _trading_minute_lag(
    calendar: XSHGTradingCalendar,
    data_time: datetime,
    now: datetime,
) -> float:
    if (
        data_time.tzinfo is None
        or data_time.utcoffset() is None
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        return float("inf")
    local_data_time = data_time.astimezone(calendar.timezone)
    local_now = now.astimezone(calendar.timezone)
    if (
        local_data_time.date() != local_now.date()
        or not calendar.is_trading_day(local_now.date())
        or local_data_time > local_now
    ):
        return float("inf")
    return max(
        0,
        calendar.expected_minutes_through(local_now)
        - calendar.expected_minutes_through(local_data_time),
    )


def _prices_match(left: float, right: float) -> bool:
    return abs(left - right) <= 1e-9


def _risk_context_state(
    connection: sqlite3.Connection,
    *,
    calendar: XSHGTradingCalendar,
    trade_date: date,
    config: RiskConfig,
) -> RiskContext:
    feedbacks = FeedbackRepository(connection).list(limit=2000)
    recommendation_repository = RecommendationRepository(connection)
    daily_trade_count = 0
    daily_new_buy_value = 0.0
    for feedback in feedbacks:
        if not feedback.executed:
            continue
        feedback_date = feedback.created_at.astimezone(calendar.timezone).date()
        if feedback_date != trade_date:
            continue
        daily_trade_count += 1
        recommendation = recommendation_repository.get(feedback.recommendation_id)
        if recommendation is None or recommendation.action not in {
            RecommendationAction.BUY,
            RecommendationAction.ADD,
        }:
            continue
        if feedback.execution_price is not None and feedback.execution_quantity is not None:
            daily_new_buy_value += (
                feedback.execution_price * feedback.execution_quantity
            )
            continue
        suggested_value = recommendation.position_constraint.get("suggested_value")
        if isinstance(suggested_value, int | float):
            daily_new_buy_value += float(suggested_value)
        else:
            # An executed buy with no amount cannot safely leave remaining capacity open.
            daily_new_buy_value += 1e18

    consecutive_losses = 0
    last_loss_date: date | None = None
    for feedback in feedbacks:
        if not feedback.executed or feedback.execution_price is None:
            continue
        recommendation = recommendation_repository.get(feedback.recommendation_id)
        if recommendation is None or recommendation.action not in {
            RecommendationAction.SELL,
            RecommendationAction.REDUCE,
        }:
            continue
        cost_price = recommendation.position_context.get("cost_price")
        if not isinstance(cost_price, int | float):
            continue
        if feedback.execution_price < float(cost_price):
            consecutive_losses += 1
            if last_loss_date is None:
                last_loss_date = feedback.created_at.astimezone(
                    calendar.timezone
                ).date()
            continue
        break

    in_loss_cooldown = False
    if consecutive_losses >= config.loss_cooldown_count and last_loss_date is not None:
        sessions = calendar.trading_days(last_loss_date, trade_date)
        in_loss_cooldown = bool(
            sessions and len(sessions) - 1 <= config.loss_cooldown_trading_days
        )
    return RiskContext(
        daily_new_buy_value=daily_new_buy_value,
        daily_trade_count=daily_trade_count,
        consecutive_losses=consecutive_losses,
        in_loss_cooldown=in_loss_cooldown,
    )
