from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import logging
from zoneinfo import ZoneInfo

import uvicorn

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings
from quantitative_trading.planning.workflow import generate_trading_plan
from quantitative_trading.recommendation.scanner import PlanNotScannableError
from quantitative_trading.recommendation.scanner import scan_latest_plan_recommendations
from quantitative_trading.runtime.account_snapshot_job import create_and_save_account_snapshot
from quantitative_trading.runtime.scheduler import SchedulerManager
from quantitative_trading.sanitization import safe_error_summary
from quantitative_trading.storage.scheduler_state import SchedulerState
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository
from quantitative_trading.storage.sqlite import connect, migrate


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchedulerJobResult:
    task_type: str
    reason: str
    status: str = "success"
    error: str | None = None
    snapshot_id: int | None = None
    plan_id: str | None = None
    recommendation_ids: list[str] | None = None


def run_api_service(settings: Settings) -> None:
    def run_trading_job(reason: str, *, suppress_record_errors: bool = False) -> None:
        started_at = datetime.now(UTC)
        try:
            result = _run_scheduler_task(settings, reason)
        except Exception as exc:
            result = SchedulerJobResult(
                task_type=_task_type_for_reason(reason),
                reason=reason,
                status="failed",
                error=safe_error_summary(exc),
            )

        finished_at = datetime.now(UTC)
        try:
            _record_scheduler_result(
                settings,
                started_at=started_at,
                finished_at=finished_at,
                result=result,
            )
        except Exception as exc:
            if not suppress_record_errors:
                raise
            LOGGER.warning(
                "startup scheduler result was not recorded for snapshot_id=%s: %s",
                result.snapshot_id,
                safe_error_summary(exc),
            )

    state = _sync_scheduler_config_to_state(settings)

    scheduler = SchedulerManager(
        interval_seconds=settings.intraday_interval_seconds,
        timezone=settings.timezone,
        job=run_trading_job,
    )
    if state.enabled and state.run_on_start:
        run_trading_job("startup", suppress_record_errors=True)

    app = create_app(settings, scheduler=scheduler, restore_scheduler=True)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


def _run_scheduler_task(settings: Settings, reason: str) -> SchedulerJobResult:
    if reason in {"intraday", "startup", "manual_api"}:
        created = create_and_save_account_snapshot(settings)
        return SchedulerJobResult(
            task_type="account_snapshot",
            reason=reason,
            snapshot_id=created.snapshot_id,
            recommendation_ids=[],
        )
    if reason == "close_plan_daily":
        return _run_close_plan_job(settings)
    if reason == "intraday_trigger":
        return _run_intraday_trigger_job(settings)
    raise ValueError("unknown scheduler task")


def _run_close_plan_job(settings: Settings) -> SchedulerJobResult:
    now = datetime.now(UTC)
    local_now = now.astimezone(ZoneInfo(settings.timezone))
    trading_day = _next_weekday(local_now.date())
    with connect(settings) as connection:
        migrate(connection)
        created = generate_trading_plan(
            connection,
            trading_day=trading_day,
            now=now,
            timezone=settings.timezone,
        )
    return SchedulerJobResult(
        task_type="close_plan_daily",
        reason="close_plan_generated",
        plan_id=created.plan_id,
        recommendation_ids=[],
    )


def _next_weekday(current_day: date) -> date:
    trading_day = current_day + timedelta(days=1)
    while trading_day.weekday() >= 5:
        trading_day += timedelta(days=1)
    return trading_day


def _run_intraday_trigger_job(settings: Settings) -> SchedulerJobResult:
    now = datetime.now(UTC)
    try:
        with connect(settings) as connection:
            migrate(connection)
            scan = scan_latest_plan_recommendations(connection, now=now)
    except PlanNotScannableError as exc:
        return SchedulerJobResult(
            task_type="recommendation_intraday_trigger",
            reason="no_recommendations_plan_not_scannable",
            plan_id=exc.plan_id,
            recommendation_ids=[],
        )

    if scan is None:
        return SchedulerJobResult(
            task_type="recommendation_intraday_trigger",
            reason="no_recommendations_no_valid_plan",
            recommendation_ids=[],
        )

    return SchedulerJobResult(
        task_type="recommendation_intraday_trigger",
        reason="recommendations_generated",
        plan_id=scan.plan.plan_id,
        recommendation_ids=[
            recommendation.recommendation_id
            for recommendation in scan.recommendations
        ],
    )


def _sync_scheduler_config_to_state(settings: Settings) -> SchedulerState:
    now = datetime.now(UTC)
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)
        state = repository.get_or_create(
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=now,
        )
        return repository.set_enabled(
            state.enabled,
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=now,
        )


def _record_scheduler_result(
    settings: Settings,
    *,
    started_at: datetime,
    finished_at: datetime,
    result: SchedulerJobResult,
) -> None:
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)
        repository.get_or_create(
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=started_at,
        )
        repository.record_result(
            started_at=started_at,
            finished_at=finished_at,
            status=result.status,
            reason=result.reason,
            error=result.error,
            snapshot_id=result.snapshot_id,
            task_type=result.task_type,
            plan_id=result.plan_id,
            recommendation_ids=result.recommendation_ids,
            now=finished_at,
        )


def _task_type_for_reason(reason: str) -> str:
    if reason == "close_plan_daily":
        return "close_plan_daily"
    if reason == "intraday_trigger":
        return "recommendation_intraday_trigger"
    return "account_snapshot"
