from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
import logging
from zoneinfo import ZoneInfo

import uvicorn

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings
from quantitative_trading.decision.factory import (
    build_decision_workflow,
    build_notification_dispatcher,
)
from quantitative_trading.email.outbox import EmailDeliveryService
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    CaptureRunAlreadyActiveError,
    CaptureRunStatus,
)
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
    run_id: str | None = None
    cleaned_rows: int | None = None
    delivery_ids: list[str] | None = None


def run_api_service(settings: Settings) -> None:
    def run_trading_job(reason: str, *, suppress_record_errors: bool = False) -> None:
        started_at = datetime.now(UTC)
        try:
            result = _run_scheduler_task(settings, reason)
        except CaptureRunAlreadyActiveError as exc:
            result = SchedulerJobResult(
                task_type=_task_type_for_reason(reason),
                reason=f"scheduler_overrun:{reason}",
                status="skipped",
                error=safe_error_summary(exc),
            )
        except Exception as exc:
            error = safe_error_summary(exc)
            result = SchedulerJobResult(
                task_type=_task_type_for_reason(reason),
                reason=reason,
                status="failed",
                error=error,
            )

        if result.status == "failed":
            error = result.error or (
                f"{result.task_type} workflow returned failed status"
            )
            try:
                _dispatch_runtime_alert(
                    settings,
                    reason=result.reason,
                    error=error,
                    now=started_at,
                    event_type="workflow.failed",
                )
            except Exception as alert_exc:
                LOGGER.warning(
                    "runtime alert dispatch failed for task_type=%s: %s",
                    result.task_type,
                    safe_error_summary(alert_exc),
                )

        if result.status == "skipped" and result.reason.startswith(
            ("scheduler_overrun:", "scheduler_missed:")
        ):
            event_type = (
                "workflow.overrun"
                if result.reason.startswith("scheduler_overrun:")
                else "workflow.missed"
            )
            error = (
                "scheduler job skipped because the previous run is already running"
                if event_type == "workflow.overrun"
                else "scheduler job skipped because its scheduled run time was missed"
            )
            try:
                _dispatch_runtime_alert(
                    settings,
                    reason=result.reason,
                    error=error,
                    now=started_at,
                    event_type=event_type,
                )
            except Exception as alert_exc:
                LOGGER.warning(
                    "runtime alert dispatch failed for task_type=%s: %s",
                    result.task_type,
                    safe_error_summary(alert_exc),
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
    if state.enabled:
        recovery_reason = _startup_recovery_reason(datetime.now(UTC))
        if recovery_reason == "close_readiness" or (
            recovery_reason is not None and state.run_on_start
        ):
            run_trading_job(recovery_reason, suppress_record_errors=True)

    app = create_app(settings, scheduler=scheduler, restore_scheduler=True)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


def _run_scheduler_task(
    settings: Settings,
    reason: str,
    *,
    now: datetime | None = None,
) -> SchedulerJobResult:
    task_now = now or datetime.now(UTC)
    if task_now.tzinfo is None or task_now.utcoffset() is None:
        raise ValueError("scheduler task time must be timezone-aware")
    calendar = XSHGTradingCalendar()
    local_now = task_now.astimezone(calendar.timezone)
    trade_date = local_now.date()

    if reason.startswith(("scheduler_overrun:", "scheduler_missed:")):
        return SchedulerJobResult(
            task_type="scheduler",
            reason=reason,
            status="skipped",
            recommendation_ids=[],
        )

    if reason == "intraday_decision":
        if not calendar.is_trading_day(trade_date) or not calendar.is_trading_minute(
            local_now
        ):
            return SchedulerJobResult(
                task_type="intraday",
                reason="not_trading_session",
                status="skipped",
                recommendation_ids=[],
            )
        with connect(settings) as connection:
            migrate(connection)
            workflow = _build_decision_workflow(connection, settings, task_now)
            result = workflow.run_intraday()
        return SchedulerJobResult(
            task_type="intraday",
            reason="intraday_reused" if result.reused else "intraday_completed",
            status=(
                "failed"
                if result.status is CaptureRunStatus.FAILED
                else "degraded"
                if result.status is CaptureRunStatus.DEGRADED
                else "success"
            ),
            snapshot_id=result.market_input_snapshot_id,
            recommendation_ids=list(result.recommendation_ids),
            run_id=result.run_id,
        )

    if reason == "close_readiness":
        local_minute = local_now.time().replace(
            tzinfo=None,
            second=0,
            microsecond=0,
        )
        if not calendar.is_trading_day(trade_date) or not (
            time(15, 15) <= local_minute <= time(16, 30)
        ):
            return SchedulerJobResult(
                task_type="close",
                reason="outside_close_window",
                status="skipped",
                recommendation_ids=[],
            )
        with connect(settings) as connection:
            migrate(connection)
            workflow = _build_decision_workflow(connection, settings, task_now)
            result = workflow.run_close(trade_date)
        deadline_not_ready = not result.ready and local_minute >= time(16, 30)
        return SchedulerJobResult(
            task_type="close",
            reason=(
                "close_deadline_not_ready"
                if deadline_not_ready
                else "close_reused"
                if result.reused
                else "close_published"
                if result.ready
                else "close_not_ready"
            ),
            status=(
                "failed"
                if deadline_not_ready
                else "success"
                if result.ready
                else "degraded"
            ),
            error=(
                "close workflow data was not ready by the 16:30 deadline"
                if deadline_not_ready
                else None
            ),
            snapshot_id=result.market_input_snapshot_id,
            plan_id=result.plan_id,
            recommendation_ids=[],
            run_id=result.run_id,
        )

    if reason == "minute_cleanup":
        if not calendar.is_trading_day(trade_date):
            return SchedulerJobResult(
                task_type="cleanup",
                reason="not_trading_day",
                status="skipped",
                recommendation_ids=[],
                cleaned_rows=0,
            )
        with connect(settings) as connection:
            migrate(connection)
            result = _build_decision_workflow(
                connection, settings, task_now
            ).run_cleanup(trade_date)
        return SchedulerJobResult(
            task_type="cleanup",
            reason="minute_cleanup_completed",
            recommendation_ids=[],
            run_id=result.run_id,
            cleaned_rows=result.cleaned_rows,
        )

    if reason == "email_delivery":
        with connect(settings) as connection:
            migrate(connection)
            deliveries = _email_delivery_service(connection, settings).process_due(now=task_now)
        return SchedulerJobResult(
            task_type="email_delivery",
            reason="email_delivery_processed",
            recommendation_ids=[],
            delivery_ids=[delivery.delivery_id for delivery in deliveries],
        )

    raise ValueError("unknown scheduler task")


def _build_decision_workflow(
    connection,
    settings: Settings,
    now: datetime,
):
    return build_decision_workflow(connection, settings, now=lambda: now)


def _email_delivery_service(connection, settings: Settings) -> EmailDeliveryService:
    return build_notification_dispatcher(connection, settings).email_service


def _dispatch_runtime_alert(
    settings: Settings,
    *,
    reason: str,
    error: str,
    now: datetime,
    event_type: str = "workflow.failed",
) -> None:
    with connect(settings) as connection:
        migrate(connection)
        build_notification_dispatcher(connection, settings).dispatch_system_alert(
            alert_key=f"{reason}:{now.astimezone(ZoneInfo(settings.timezone)).date()}",
            event_type=event_type,
            message=f"workflow {reason}: {error}",
            details={
                "workflow_type": _task_type_for_reason(reason),
                "status": "skipped" if event_type != "workflow.failed" else "failed",
                "error": error,
            },
            now=now,
        )


def _startup_recovery_reason(now: datetime) -> str | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("startup recovery time must be timezone-aware")
    calendar = XSHGTradingCalendar()
    local_now = now.astimezone(calendar.timezone)
    if not calendar.is_trading_day(local_now.date()):
        return None
    local_time = local_now.time().replace(tzinfo=None)
    if time(15, 15) <= local_time <= time(16, 30):
        return "close_readiness"
    if calendar.is_trading_minute(local_now):
        return "intraday_decision"
    return None


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
    if reason.startswith(("scheduler_overrun:", "scheduler_missed:")):
        return "scheduler"
    if reason == "intraday_decision":
        return "intraday"
    if reason == "close_readiness":
        return "close"
    if reason == "minute_cleanup":
        return "cleanup"
    if reason == "email_delivery":
        return "email_delivery"
    return "unknown"
