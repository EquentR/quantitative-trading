from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.market.adapters import (
    DailyBarProvider,
    MarketProviderError,
    MoneyFlowProvider,
)
from quantitative_trading.market.backfill import HeavyDataBackfillService
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureResultStatus,
    CaptureRunAlreadyActiveError,
    CaptureRunStatus,
    MarketCaptureResult,
    MarketCaptureRun,
)
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    HistorySnapshotRepository,
    MarketCaptureResultRepository,
    MarketCaptureRunRepository,
    MinuteBarRepository,
    MoneyFlowRepository,
    MoneyFlowSnapshotRepository,
)
from quantitative_trading.market.retention import MinuteBarRetentionService
from quantitative_trading.sanitization import safe_error_summary
from quantitative_trading.universe.service import build_universe
from quantitative_trading.watchlist.repository import WatchPinnedRepository


class MarketBackfillSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_type: str = "backfill"
    trade_date: date
    status: CaptureRunStatus
    reused: bool
    symbols: list[str]
    requested_symbols: int = Field(ge=0)
    processed_symbols: int = Field(ge=0)
    provider_calls: int = Field(ge=0)
    provider_duration_ms: float = Field(ge=0)
    rows_received: int = Field(ge=0)
    rows_written: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    warnings: list[str]
    results: list[MarketCaptureResult]


class MarketCleanupSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_type: str = "cleanup"
    trade_date: date
    status: CaptureRunStatus
    reused: bool
    deleted_rows: int = Field(ge=0)


class MarketRunsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0)
    runs: list[dict[str, object]]


class MarketCliService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        calendar: XSHGTradingCalendar,
        daily_provider: DailyBarProvider,
        money_flow_provider: MoneyFlowProvider,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.connection = connection
        self.calendar = calendar
        self.daily_provider = daily_provider
        self.money_flow_provider = money_flow_provider
        self.now = now or (lambda: datetime.now(UTC))

    def backfill(
        self,
        trade_date: date,
        *,
        symbols: Sequence[str] | None = None,
    ) -> MarketBackfillSummary:
        if not self.calendar.is_trading_day(trade_date):
            raise ValueError("backfill date must be an XSHG trading day")
        requested = self._symbols(symbols)
        scope_hash = hashlib.sha256(",".join(requested).encode("ascii")).hexdigest()[:12]
        run_id = f"backfill-{trade_date.isoformat()}-{scope_hash}"
        started_at = self._now()
        run_repository = MarketCaptureRunRepository(self.connection)
        run_repository.fail_expired_workflow_runs(
            "backfill",
            started_before=started_at - timedelta(hours=4),
            finished_at=started_at,
        )
        result_repository = MarketCaptureResultRepository(self.connection)
        run, created = run_repository.get_or_create(
            MarketCaptureRun(
                run_id=run_id,
                workflow_type="backfill",
                trade_date=trade_date,
                idempotency_key=(
                    f"market-backfill:{trade_date.isoformat()}:{','.join(requested)}"
                ),
                status=CaptureRunStatus.RUNNING,
                started_at=started_at,
                requested_symbols=len(requested),
            )
        )
        if not created and run.status in {
            CaptureRunStatus.SUCCEEDED,
            CaptureRunStatus.DEGRADED,
        }:
            return self._backfill_summary(
                run,
                requested,
                result_repository.list_for_run(run.run_id),
                reused=True,
            )
        if not created:
            if (
                run.status is CaptureRunStatus.RUNNING
                and started_at - run.started_at < timedelta(hours=4)
            ):
                raise CaptureRunAlreadyActiveError(run.run_id)
            claimed = run_repository.claim_retry(run, started_at=started_at)
            if claimed is None:
                raise CaptureRunAlreadyActiveError(run.run_id)
            run = claimed

        try:
            return self._run_backfill_claimed(
                trade_date,
                requested=requested,
                run=run,
                run_repository=run_repository,
                result_repository=result_repository,
                reused=not created,
            )
        except Exception as exc:
            try:
                run_repository.fail_if_running(
                    run.run_id,
                    finished_at=self._safe_now(run.started_at),
                    expected_started_at=run.started_at,
                    error_summary=safe_error_summary(exc),
                )
            except sqlite3.Error:
                pass
            raise

    def _run_backfill_claimed(
        self,
        trade_date: date,
        *,
        requested: list[str],
        run: MarketCaptureRun,
        run_repository: MarketCaptureRunRepository,
        result_repository: MarketCaptureResultRepository,
        reused: bool,
    ) -> MarketBackfillSummary:
        backfill = HeavyDataBackfillService(
            calendar=self.calendar,
            daily_provider=self.daily_provider,
            money_flow_provider=self.money_flow_provider,
            daily_repository=DailyBarRepository(self.connection),
            money_flow_repository=MoneyFlowRepository(self.connection),
            history_snapshot_repository=HistorySnapshotRepository(self.connection),
            money_flow_snapshot_repository=MoneyFlowSnapshotRepository(self.connection),
            now=self.now,
        )
        results: list[MarketCaptureResult] = []
        provider_duration_ms = 0.0
        provider_calls = 0
        rows_received = 0
        rows_written = 0
        for symbol in requested:
            provider_started = perf_counter()
            result, calls, received, written = self._capture_dataset(
                run.run_id,
                symbol,
                trade_date,
                CaptureDataset.DAILY_BAR,
                250,
                lambda: backfill.backfill_daily(run.run_id, symbol, trade_date),
            )
            results.append(result)
            provider_calls += calls
            rows_received += received
            rows_written += written
            provider_duration_ms += (perf_counter() - provider_started) * 1000
            provider_started = perf_counter()
            result, calls, received, written = self._capture_dataset(
                run.run_id,
                symbol,
                trade_date,
                CaptureDataset.MONEY_FLOW,
                60,
                lambda: backfill.backfill_money_flow(
                    run.run_id, symbol, trade_date
                ),
            )
            results.append(result)
            provider_calls += calls
            rows_received += received
            rows_written += written
            provider_duration_ms += (perf_counter() - provider_started) * 1000
        for result in results:
            result_repository.upsert(result)

        complete_count = sum(
            result.status is CaptureResultStatus.COMPLETE for result in results
        )
        usable_count = sum(
            result.actual_rows > 0
            and result.status
            in {
                CaptureResultStatus.COMPLETE,
                CaptureResultStatus.DEGRADED,
                CaptureResultStatus.STALE,
            }
            for result in results
        )
        failure_count = sum(
            result.status is CaptureResultStatus.FAILED for result in results
        )
        warning_count = sum(
            result.status is not CaptureResultStatus.COMPLETE for result in results
        )
        if not requested or complete_count == len(results):
            status = CaptureRunStatus.SUCCEEDED
        elif usable_count > 0:
            status = CaptureRunStatus.DEGRADED
        else:
            status = CaptureRunStatus.FAILED
        finished = run.model_copy(
            update={
                "status": status,
                "finished_at": self._now(),
                "processed_symbols": len(requested),
                "provider_calls": provider_calls,
                "provider_duration_ms": provider_duration_ms,
                "rows_received": rows_received,
                "rows_written": rows_written,
                "warning_count": warning_count + (1 if not requested else 0),
                "failure_count": failure_count,
                "error_summary": (
                    "all requested backfill datasets failed"
                    if status is CaptureRunStatus.FAILED
                    else ""
                ),
            }
        )
        run_repository.update_claimed(
            finished,
            claim_started_at=run.started_at,
        )
        return self._backfill_summary(
            finished,
            requested,
            results,
            reused=reused,
        )

    def cleanup(self, trade_date: date) -> MarketCleanupSummary:
        started_at = self._now()
        run_repository = MarketCaptureRunRepository(self.connection)
        run_repository.fail_expired_workflow_runs(
            "cleanup",
            started_before=started_at - timedelta(minutes=30),
            finished_at=started_at,
        )
        run, created = run_repository.get_or_create(
            MarketCaptureRun(
                run_id=f"cleanup-{trade_date.isoformat()}",
                workflow_type="cleanup",
                trade_date=trade_date,
                idempotency_key=f"market-cleanup:{trade_date.isoformat()}",
                status=CaptureRunStatus.RUNNING,
                started_at=started_at,
            )
        )
        if not created and run.status is CaptureRunStatus.SUCCEEDED:
            return MarketCleanupSummary(
                run_id=run.run_id,
                trade_date=run.trade_date,
                status=run.status,
                reused=True,
                deleted_rows=run.cleaned_rows,
            )
        if not created:
            if (
                run.status is CaptureRunStatus.RUNNING
                and started_at - run.started_at < timedelta(minutes=30)
            ):
                raise CaptureRunAlreadyActiveError(run.run_id)
            claimed = run_repository.claim_retry(run, started_at=started_at)
            if claimed is None:
                raise CaptureRunAlreadyActiveError(run.run_id)
            run = claimed
        try:
            deleted = MinuteBarRetentionService(
                MinuteBarRepository(self.connection), self.calendar
            ).cleanup(trade_date)
        except Exception as exc:
            try:
                run_repository.fail_if_running(
                    run.run_id,
                    finished_at=self._safe_now(run.started_at),
                    expected_started_at=run.started_at,
                    error_summary=safe_error_summary(exc),
                )
            except sqlite3.Error:
                pass
            raise
        finished = run.model_copy(
            update={
                "status": CaptureRunStatus.SUCCEEDED,
                "finished_at": self._now(),
                "cleaned_rows": deleted,
            }
        )
        run_repository.update_claimed(
            finished,
            claim_started_at=run.started_at,
        )
        return MarketCleanupSummary(
            run_id=finished.run_id,
            trade_date=finished.trade_date,
            status=finished.status,
            reused=not created,
            deleted_rows=deleted,
        )

    def list_runs(self, *, limit: int) -> MarketRunsSummary:
        rows = self.connection.execute(
            "SELECT run_id FROM market_capture_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        repository = MarketCaptureRunRepository(self.connection)
        runs = [repository.get(str(row["run_id"])) for row in rows]
        result_repository = MarketCaptureResultRepository(self.connection)
        present = [
            {
                **run.model_dump(mode="json"),
                "dataset_counts": self._dataset_counts(
                    result_repository.list_for_run(run.run_id)
                ),
            }
            for run in runs
            if run is not None
        ]
        return MarketRunsSummary(count=len(present), runs=present)

    @staticmethod
    def _dataset_counts(
        results: list[MarketCaptureResult],
    ) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for result in results:
            dataset = counts.setdefault(
                result.dataset.value,
                {status.value: 0 for status in CaptureResultStatus},
            )
            dataset[result.status.value] += 1
        return counts

    def _symbols(self, explicit: Sequence[str] | None) -> list[str]:
        members = build_universe(
            positions=PositionRepository(self.connection).list(),
            watchlist=WatchPinnedRepository(self.connection).list(),
            created_at=self._now(),
        )
        enabled_symbols = {
            member.symbol for member in members if member.plan_enabled
        }
        if explicit:
            symbols = sorted(set(explicit))
            outside_scope = sorted(set(symbols) - enabled_symbols)
            if outside_scope:
                raise ValueError(
                    "symbols are outside the decision-enabled universe: "
                    + ",".join(outside_scope)
                )
        else:
            symbols = sorted(enabled_symbols)
        if any(
            len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit()
            for symbol in symbols
        ):
            raise ValueError("symbols must contain six ASCII digits")
        return symbols

    def _capture_dataset(
        self,
        run_id: str,
        symbol: str,
        trade_date: date,
        dataset: CaptureDataset,
        expected_rows: int,
        capture: Callable[[], object],
    ) -> tuple[MarketCaptureResult, int, int, int]:
        try:
            created = capture()
            snapshot = created.snapshot
            return (
                MarketCaptureResult(
                    run_id=run_id,
                    symbol=symbol,
                    dataset=dataset,
                    status=snapshot.status,
                    data_start=snapshot.data_start,
                    data_end=snapshot.data_end,
                    fetched_at=snapshot.fetched_at,
                    expected_rows=expected_rows,
                    actual_rows=snapshot.row_count,
                    source="akshare",
                    warning=snapshot.warning,
                ),
                created.provider_calls,
                created.rows_received,
                created.rows_written,
            )
        except Exception as exc:
            if not isinstance(exc, MarketProviderError):
                raise
            summary = safe_error_summary(exc)
            return (
                MarketCaptureResult(
                    run_id=run_id,
                    symbol=symbol,
                    dataset=dataset,
                    status=CaptureResultStatus.FAILED,
                    fetched_at=self._now(),
                    expected_rows=expected_rows,
                    actual_rows=0,
                    source="akshare",
                    warning=summary,
                    error_summary=summary,
                ),
                1,
                0,
                0,
            )

    def _backfill_summary(
        self,
        run: MarketCaptureRun,
        symbols: list[str],
        results: list[MarketCaptureResult],
        *,
        reused: bool,
    ) -> MarketBackfillSummary:
        warnings = [
            text
            for result in results
            for text in (result.warning, result.error_summary)
            if text
        ]
        if not symbols:
            warnings.append("empty decision-enabled universe; providers were not called")
        return MarketBackfillSummary(
            run_id=run.run_id,
            trade_date=run.trade_date,
            status=run.status,
            reused=reused,
            symbols=symbols,
            requested_symbols=run.requested_symbols,
            processed_symbols=run.processed_symbols,
            provider_calls=run.provider_calls,
            provider_duration_ms=run.provider_duration_ms,
            rows_received=run.rows_received,
            rows_written=run.rows_written,
            warning_count=run.warning_count,
            failure_count=run.failure_count,
            warnings=list(dict.fromkeys(warnings)),
            results=results,
        )

    def _now(self) -> datetime:
        value = self.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market CLI time must be timezone-aware")
        return value

    def _safe_now(self, fallback: datetime) -> datetime:
        try:
            return self._now()
        except Exception:
            return fallback
