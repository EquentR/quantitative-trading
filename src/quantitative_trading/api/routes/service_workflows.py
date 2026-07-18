from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.decision.factory import (
    build_decision_workflow,
    dispatch_workflow_failure_alert,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    CaptureExecutionMode,
    CaptureRunAlreadyActiveError,
    CaptureRunStatus,
)
from quantitative_trading.market.repositories import MarketCaptureRunRepository
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.sanitization import safe_error_summary
from quantitative_trading.universe.service import build_universe
from quantitative_trading.watchlist.repository import WatchPinnedRepository


router = APIRouter(
    prefix="/service/workflows",
    tags=["service"],
    dependencies=[Depends(require_token)],
)
LOGGER = logging.getLogger(__name__)

WorkflowType = Literal["close", "intraday", "backfill", "cleanup"]
BackfillAsOfMode = Literal["latest_complete"]
OutsideSessionMode = Literal["display_only"]
Symbol = Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class WorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    trade_date: date | None = None
    force: bool = False
    skip_calendar: bool = False
    manual_reason: str | None = Field(default=None, min_length=1, max_length=500)
    as_of: date | None = None
    as_of_mode: BackfillAsOfMode | None = None
    symbols: list[Symbol] | None = Field(default=None, min_length=1, max_length=500)
    outside_session_mode: OutsideSessionMode | None = None


class WorkflowRunResponse(BaseModel):
    task: WorkflowType
    status: Literal["success", "degraded", "failed"]
    run_id: str | None
    snapshot_id: int | None
    plan_id: str | None
    recommendation_ids: list[str]
    warnings: list[str]
    reused: bool
    ready: bool | None
    cleaned_rows: int | None
    mode: CaptureExecutionMode | None = None
    effective_trade_date: date | None = None
    history_cutoff_date: date | None = None
    requested_symbol_scope: list[Symbol] | None = None
    lease_expires_at: datetime | None = None


def _current_time() -> datetime:
    return datetime.now(UTC)


def _invalid_request(workflow_type: str) -> ApiError:
    return ApiError(
        status_code=422,
        code="workflow_request_invalid",
        message="request fields are not valid for workflow type",
        details={"workflow_type": workflow_type},
    )


def _manual_reason_required() -> ApiError:
    return ApiError(
        status_code=422,
        code="manual_reason_required",
        message="manual reason is required for workflow override",
    )


def _calendar_guard_failed(trade_date: date) -> ApiError:
    return ApiError(
        status_code=422,
        code="workflow_calendar_guard",
        message="close workflow requires a trading day or explicit calendar override",
        details={"trade_date": trade_date.isoformat()},
    )


def _backfill_calendar_guard_failed(trade_date: date) -> ApiError:
    return ApiError(
        status_code=422,
        code="workflow_calendar_guard",
        message="backfill workflow requires an XSHG trading day",
        details={"trade_date": trade_date.isoformat()},
    )


def _outside_close_window(trade_date: date) -> ApiError:
    return ApiError(
        status_code=422,
        code="workflow_outside_window",
        message="close workflow is outside the normal execution window",
        details={"trade_date": trade_date.isoformat()},
    )


def _outside_intraday_session() -> ApiError:
    return ApiError(
        status_code=422,
        code="workflow_outside_session",
        message="intraday workflow requires the current trading session",
    )


def _workflow_not_available() -> ApiError:
    return ApiError(
        status_code=422,
        code="workflow_not_available",
        message="backfill workflow requires enabled AkShare market fetching",
    )


def _run_failed(exc: Exception) -> ApiError:
    return ApiError(
        status_code=500,
        code="workflow_run_failed",
        message="workflow execution failed",
        details={"error": safe_error_summary(exc)},
    )


def _workflow_in_progress(exc: CaptureRunAlreadyActiveError) -> ApiError:
    return ApiError(
        status_code=409,
        code="workflow_in_progress",
        message="workflow run is already in progress",
        details={"run_id": exc.run_id},
    )


def _validate_fields(
    workflow_type: WorkflowType,
    request: WorkflowRunRequest,
) -> None:
    if workflow_type == "close":
        if (
            request.as_of is not None
            or request.as_of_mode is not None
            or request.symbols is not None
            or request.outside_session_mode is not None
        ):
            raise _invalid_request(workflow_type)
        return
    if workflow_type == "intraday":
        if any(
            (
                request.trade_date is not None,
                request.force,
                request.skip_calendar,
                request.manual_reason is not None
                and request.outside_session_mode is None,
                request.as_of is not None,
                request.as_of_mode is not None,
                request.symbols is not None,
            )
        ):
            raise _invalid_request(workflow_type)
        return
    if workflow_type == "backfill":
        if any(
            (
                request.force,
                request.skip_calendar,
                request.manual_reason is not None,
                request.as_of is not None,
                request.trade_date is not None and request.as_of_mode is not None,
                request.outside_session_mode is not None,
            )
        ):
            raise _invalid_request(workflow_type)
        return
    if any(
        (
            request.trade_date is not None,
            request.force,
            request.skip_calendar,
            request.manual_reason is not None,
            request.as_of_mode is not None,
            request.symbols is not None,
            request.outside_session_mode is not None,
        )
    ):
        raise _invalid_request(workflow_type)


def _aware_now() -> datetime:
    value = _current_time()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("workflow API clock must be timezone-aware")
    return value


def _audit_request(
    connection,
    *,
    workflow_type: WorkflowType,
    request: WorkflowRunRequest,
    now: datetime,
    trade_date: date | None,
    as_of: date | None,
    late: bool,
) -> None:
    AuditService(AuditLogRepository(connection)).record_event(
        event_type="service.workflow.run_requested",
        recommendation_id=None,
        payload={
            "workflow_type": workflow_type,
            "trade_date": None if trade_date is None else trade_date.isoformat(),
            "as_of": None if as_of is None else as_of.isoformat(),
            "force": request.force,
            "skip_calendar": request.skip_calendar,
            "late": late,
            "manual_reason": request.manual_reason,
            "symbols": request.symbols,
            **(
                {"outside_session_mode": request.outside_session_mode}
                if request.outside_session_mode is not None
                else {}
            ),
            **(
                {"as_of_mode": request.as_of_mode}
                if request.as_of_mode is not None
                else {}
            ),
        },
        now=now,
    )


def _audit_failed_request(
    container: ApiContainer,
    *,
    workflow_type: WorkflowType,
    request: WorkflowRunRequest,
    exc: ApiError,
    now: datetime,
) -> None:
    display_only = False
    if (
        workflow_type == "intraday"
        and request.outside_session_mode == "display_only"
    ):
        calendar = XSHGTradingCalendar()
        local_now = now.astimezone(calendar.timezone)
        display_only = not (
            calendar.is_trading_day(local_now.date())
            and calendar.is_trading_minute(local_now)
        )
    event_type = (
        "service.workflow.run_failed"
        if exc.status_code >= 500
        else "service.workflow.run_rejected"
    )
    try:
        with connection_scope(container.settings) as connection:
            provenance = {}
            if display_only:
                resolution = calendar.resolve_market_dates(
                    local_now,
                    session_ready=lambda _: True,
                )
                period_minute = local_now.minute - local_now.minute % 3
                period_start = local_now.replace(
                    minute=period_minute,
                    second=0,
                    microsecond=0,
                )
                run_id = (
                    "intraday-display_only-"
                    f"{resolution.effective_trade_date:%Y%m%d}-"
                    f"{period_start:%Y%m%d-%H%M}"
                )
                run = MarketCaptureRunRepository(connection).get(run_id)
                provenance = {
                    "run_id": run_id,
                    "mode": CaptureExecutionMode.DISPLAY_ONLY.value,
                    "effective_trade_date": (
                        resolution.effective_trade_date.isoformat()
                    ),
                    "history_cutoff_date": resolution.history_cutoff_date.isoformat(),
                    "requested_symbol_scope": (
                        []
                        if run is None
                        else run.requested_symbol_scope
                    ),
                    "lease_expires_at": (
                        (now + timedelta(minutes=10)).isoformat()
                        if run is None or run.lease_expires_at is None
                        else run.lease_expires_at.isoformat()
                    ),
                }
            AuditService(AuditLogRepository(connection)).record_event(
                event_type=event_type,
                recommendation_id=None,
                payload={
                    "workflow_type": workflow_type,
                    "error_code": exc.code,
                    "force": request.force,
                    "skip_calendar": request.skip_calendar,
                    "manual_reason": request.manual_reason,
                    "trade_date": (
                        None
                        if request.trade_date is None
                        else request.trade_date.isoformat()
                    ),
                    "as_of": None if request.as_of is None else request.as_of.isoformat(),
                    "symbols": request.symbols,
                    **provenance,
                    **(
                        {"outside_session_mode": request.outside_session_mode}
                        if request.outside_session_mode is not None
                        else {}
                    ),
                    **(
                        {"as_of_mode": request.as_of_mode}
                        if request.as_of_mode is not None
                        else {}
                    ),
                },
                now=now,
            )
            if exc.status_code >= 500 and not display_only:
                details = exc.details or {}
                dispatch_workflow_failure_alert(
                    connection,
                    container.settings,
                    workflow_type=workflow_type,
                    error=str(details.get("error", exc.message)),
                    source="http",
                    now=now,
                )
    except Exception as audit_exc:
        LOGGER.warning(
            "workflow request outcome audit failed: %s",
            safe_error_summary(audit_exc),
        )


def _dispatch_failed_result_alert(
    connection,
    container: ApiContainer,
    *,
    workflow_type: WorkflowType,
    run_id: str,
    warnings: list[str] | tuple[str, ...],
    now: datetime,
) -> None:
    error = "; ".join(warnings) or "workflow returned failed status"
    try:
        dispatch_workflow_failure_alert(
            connection,
            container.settings,
            workflow_type=workflow_type,
            error=error,
            source="http",
            now=now,
            run_id=run_id,
        )
    except Exception as alert_exc:
        LOGGER.warning(
            "workflow failed-result alert dispatch failed: %s",
            safe_error_summary(alert_exc),
        )


def _run_close(
    container: ApiContainer,
    request: WorkflowRunRequest,
    now: datetime,
) -> WorkflowRunResponse:
    calendar = XSHGTradingCalendar()
    local_now = now.astimezone(calendar.timezone)
    trade_date = request.trade_date or local_now.date()
    window_start = datetime.combine(trade_date, time(15, 15), calendar.timezone)
    cutoff = datetime.combine(trade_date, time(16, 30), calendar.timezone)
    late = local_now > cutoff

    if not calendar.is_trading_day(trade_date) and not request.skip_calendar:
        raise _calendar_guard_failed(trade_date)
    if (
        request.force or request.skip_calendar or late
    ) and request.manual_reason is None:
        raise _manual_reason_required()
    if local_now < window_start and not request.force:
        raise _outside_close_window(trade_date)

    try:
        with connection_scope(container.settings) as connection:
            _audit_request(
                connection,
                workflow_type="close",
                request=request,
                now=now,
                trade_date=trade_date,
                as_of=None,
                late=late,
            )
            workflow = build_decision_workflow(
                connection,
                container.settings,
                now=lambda: now,
            )
            result = workflow.run_close(
                trade_date,
                skip_calendar=request.skip_calendar,
            )
    except ApiError:
        raise
    except CaptureRunAlreadyActiveError as exc:
        raise _workflow_in_progress(exc) from exc
    except Exception as exc:
        raise _run_failed(exc) from exc

    return WorkflowRunResponse(
        task="close",
        status="success" if result.ready else "degraded",
        run_id=result.run_id,
        snapshot_id=result.market_input_snapshot_id,
        plan_id=result.plan_id,
        recommendation_ids=[],
        warnings=list(result.warnings),
        reused=result.reused,
        ready=result.ready,
        cleaned_rows=None,
    )


def _run_intraday(
    container: ApiContainer,
    request: WorkflowRunRequest,
    now: datetime,
) -> WorkflowRunResponse:
    calendar = XSHGTradingCalendar()
    local_now = now.astimezone(calendar.timezone)
    in_session = calendar.is_trading_day(
        local_now.date()
    ) and calendar.is_trading_minute(local_now)
    if not in_session and request.outside_session_mode != "display_only":
        raise _outside_intraday_session()
    mode = (
        CaptureExecutionMode.DECISION
        if in_session
        else CaptureExecutionMode.DISPLAY_ONLY
    )

    try:
        with connection_scope(container.settings) as connection:
            _audit_request(
                connection,
                workflow_type="intraday",
                request=request,
                now=now,
                trade_date=local_now.date(),
                as_of=None,
                late=False,
            )
            workflow = build_decision_workflow(
                connection,
                container.settings,
                now=lambda: now,
            )
            result = workflow.run_intraday(
                as_of=now,
                mode=mode,
                manual_reason=request.manual_reason,
            )
            plan = (
                TradingPlanRepository(connection).active_for_day(local_now.date())
                if mode is CaptureExecutionMode.DECISION
                else None
            )
            if (
                mode is CaptureExecutionMode.DECISION
                and result.status is CaptureRunStatus.FAILED
            ):
                _dispatch_failed_result_alert(
                    connection,
                    container,
                    workflow_type="intraday",
                    run_id=result.run_id,
                    warnings=result.warnings,
                    now=now,
                )
    except ApiError:
        raise
    except CaptureRunAlreadyActiveError as exc:
        raise _workflow_in_progress(exc) from exc
    except Exception as exc:
        raise _run_failed(exc) from exc

    return WorkflowRunResponse(
        task="intraday",
        status=(
            "failed"
            if result.status is CaptureRunStatus.FAILED
            else "degraded"
            if result.status is CaptureRunStatus.DEGRADED
            else "success"
        ),
        run_id=result.run_id,
        snapshot_id=result.market_input_snapshot_id,
        plan_id=None if plan is None else plan.plan_id,
        recommendation_ids=list(result.recommendation_ids),
        warnings=[
            *result.warnings,
            *(
                ["行情展示已刷新，本次未生成交易建议"]
                if result.mode is CaptureExecutionMode.DISPLAY_ONLY
                else []
            ),
        ],
        reused=result.reused,
        ready=None,
        cleaned_rows=None,
        mode=result.mode,
        effective_trade_date=result.effective_trade_date,
        history_cutoff_date=result.history_cutoff_date,
        requested_symbol_scope=list(result.requested_symbol_scope),
        lease_expires_at=result.lease_expires_at,
    )


def _run_backfill(
    container: ApiContainer,
    request: WorkflowRunRequest,
    now: datetime,
) -> WorkflowRunResponse:
    if (
        not container.settings.enable_market_fetch
        or container.settings.market_provider.strip().lower() != "akshare"
    ):
        raise _workflow_not_available()
    calendar = XSHGTradingCalendar()
    local_date = now.astimezone(calendar.timezone).date()
    try:
        with connection_scope(container.settings) as connection:
            scope = _backfill_scope(connection, request, now)
            resolution_warnings: tuple[str, ...] = ()
            if request.as_of_mode == "latest_complete":
                resolution = calendar.resolve_market_dates(
                    now,
                    session_ready=lambda trade_day: _history_session_ready(
                        connection,
                        calendar=calendar,
                        requested_at=now,
                        local_date=local_date,
                        trade_date=trade_day,
                        symbols=scope,
                    ),
                )
                trade_date = resolution.history_cutoff_date
                resolution_warnings = resolution.warnings
            else:
                trade_date = request.trade_date or local_date
            if not calendar.is_trading_day(trade_date):
                raise _backfill_calendar_guard_failed(trade_date)
            _audit_request(
                connection,
                workflow_type="backfill",
                request=request,
                now=now,
                trade_date=trade_date,
                as_of=None,
                late=False,
            )
            workflow = build_decision_workflow(
                connection,
                container.settings,
                now=lambda: now,
            )
            summary = workflow.run_backfill(
                trade_date,
                symbols=(scope if request.symbols is not None else None),
            )
            if summary.status is CaptureRunStatus.FAILED:
                _dispatch_failed_result_alert(
                    connection,
                    container,
                    workflow_type="backfill",
                    run_id=summary.run_id,
                    warnings=summary.warnings,
                    now=now,
                )
    except ApiError:
        raise
    except CaptureRunAlreadyActiveError as exc:
        raise _workflow_in_progress(exc) from exc
    except Exception as exc:
        raise _run_failed(exc) from exc

    status: Literal["success", "degraded", "failed"]
    if summary.status is CaptureRunStatus.SUCCEEDED:
        status = "success"
    elif summary.status is CaptureRunStatus.DEGRADED:
        status = "degraded"
    else:
        status = "failed"
    return WorkflowRunResponse(
        task="backfill",
        status=status,
        run_id=summary.run_id,
        snapshot_id=None,
        plan_id=None,
        recommendation_ids=[],
        warnings=[*resolution_warnings, *summary.warnings],
        reused=summary.reused,
        ready=None,
        cleaned_rows=None,
    )


def _backfill_scope(
    connection,
    request: WorkflowRunRequest,
    now: datetime,
) -> list[str]:
    members = build_universe(
        positions=PositionRepository(connection).list(),
        watchlist=WatchPinnedRepository(connection).list(),
        created_at=now,
    )
    enabled = {member.symbol for member in members if member.plan_enabled}
    if request.symbols is None:
        return sorted(enabled)
    requested = list(dict.fromkeys(request.symbols))
    if set(requested) - enabled:
        raise _invalid_request("backfill")
    return requested


def _history_session_ready(
    connection,
    *,
    calendar: XSHGTradingCalendar,
    requested_at: datetime,
    local_date: date,
    trade_date: date,
    symbols: list[str],
) -> bool:
    if trade_date < local_date:
        return True
    if trade_date > local_date or not calendar.is_trading_day(local_date):
        return False
    if requested_at.astimezone(calendar.timezone) <= calendar.session(local_date).close_at:
        return False
    if not symbols:
        return True
    placeholders = ",".join("?" for _ in symbols)
    row = connection.execute(
        f"""
        SELECT COUNT(DISTINCT symbol) AS count
        FROM daily_bars
        WHERE trade_date = ?
          AND adjustment = 'forward'
          AND symbol IN ({placeholders})
        """,
        (trade_date.isoformat(), *symbols),
    ).fetchone()
    return int(row["count"]) == len(symbols)


def _run_cleanup(
    container: ApiContainer,
    request: WorkflowRunRequest,
    now: datetime,
) -> WorkflowRunResponse:
    calendar = XSHGTradingCalendar()
    as_of = request.as_of or now.astimezone(calendar.timezone).date()
    try:
        with connection_scope(container.settings) as connection:
            _audit_request(
                connection,
                workflow_type="cleanup",
                request=request,
                now=now,
                trade_date=None,
                as_of=as_of,
                late=False,
            )
            workflow = build_decision_workflow(
                connection,
                container.settings,
                now=lambda: now,
            )
            result = workflow.run_cleanup(as_of)
    except ApiError:
        raise
    except CaptureRunAlreadyActiveError as exc:
        raise _workflow_in_progress(exc) from exc
    except Exception as exc:
        raise _run_failed(exc) from exc

    return WorkflowRunResponse(
        task="cleanup",
        status="success",
        run_id=result.run_id,
        snapshot_id=None,
        plan_id=None,
        recommendation_ids=[],
        warnings=list(result.warnings),
        reused=result.reused,
        ready=None,
        cleaned_rows=result.cleaned_rows,
    )


@router.post("/{workflow_type}/run", response_model=WorkflowRunResponse)
def run_workflow(
    workflow_type: Annotated[WorkflowType, ApiPath()],
    request: WorkflowRunRequest | None = None,
    container: ApiContainer = Depends(get_container),
) -> WorkflowRunResponse:
    payload = request or WorkflowRunRequest()
    try:
        now = _aware_now()
    except Exception as exc:
        raise _run_failed(exc) from exc
    try:
        _validate_fields(workflow_type, payload)
        if workflow_type == "close":
            return _run_close(container, payload, now)
        if workflow_type == "intraday":
            return _run_intraday(container, payload, now)
        if workflow_type == "backfill":
            return _run_backfill(container, payload, now)
        return _run_cleanup(container, payload, now)
    except ApiError as exc:
        _audit_failed_request(
            container,
            workflow_type=workflow_type,
            request=payload,
            exc=exc,
            now=now,
        )
        raise
