from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import ValidationError

from quantitative_trading.api.dependencies import (
    ApiContainer,
    auth_service,
    connection_scope,
    get_container,
    require_token,
    verify_authorization_header,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.sanitization import safe_error_summary as _safe_error_summary  # noqa: F401
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository


router = APIRouter(prefix="/service", tags=["service"])


@router.get("/status")
def status(
    authorization: str | None = Header(default=None),
    container: ApiContainer = Depends(get_container),
) -> dict[str, object]:
    if authorization is None:
        return _public_status_payload(container)
    verify_authorization_header(authorization, container)
    return _status_payload(container)


@router.post("/scheduler/start", dependencies=[Depends(require_token)])
def start_scheduler(container: ApiContainer = Depends(get_container)) -> dict[str, object]:
    changed_live_state = _control_scheduler(container.scheduler, action="start")
    try:
        _set_scheduler_enabled(container, enabled=True)
    except (ApiError, sqlite3.Error, ValidationError) as exc:
        if changed_live_state:
            _compensate_scheduler(container.scheduler, action="stop")
        raise _service_state_failed() from exc
    return _status_payload(container)


@router.post("/scheduler/stop", dependencies=[Depends(require_token)])
def stop_scheduler(container: ApiContainer = Depends(get_container)) -> dict[str, object]:
    changed_live_state = _control_scheduler(container.scheduler, action="stop")
    try:
        _set_scheduler_enabled(container, enabled=False)
    except (ApiError, sqlite3.Error, ValidationError) as exc:
        if changed_live_state:
            _compensate_scheduler(container.scheduler, action="start")
        raise _service_state_failed() from exc
    return _status_payload(container)


@router.post("/run-once", dependencies=[Depends(require_token)])
def run_once(container: ApiContainer = Depends(get_container)) -> dict[str, object]:
    del container
    raise ApiError(
        status_code=410,
        code="service_run_once_retired",
        message="service run-once is retired; use the unified intraday workflow",
        details={"replacement": "/api/v1/service/workflows/intraday/run"},
    )


def _status_payload(container: ApiContainer) -> dict[str, object]:
    now = datetime.now(UTC)
    try:
        with connection_scope(container.settings) as connection:
            current_auth_status = auth_service(container.settings, connection).status()
            scheduler_state = SchedulerStateRepository(connection).get_or_create(
                interval_seconds=container.settings.intraday_interval_seconds,
                run_on_start=container.settings.service_run_on_start_when_scheduler_enabled,
                now=now,
            )
    except (sqlite3.Error, ValidationError) as exc:
        raise _service_state_failed() from exc

    return {
        "auth_status": current_auth_status,
        "scheduler_enabled": scheduler_state.enabled,
        "scheduler_running": _scheduler_running(container.scheduler),
        "interval_seconds": scheduler_state.interval_seconds,
        "timezone": container.settings.timezone,
        "run_on_start": scheduler_state.run_on_start,
        "next_run_time": _scheduler_next_run_time(container.scheduler),
        "last_started_at": scheduler_state.last_started_at,
        "last_finished_at": scheduler_state.last_finished_at,
        "last_status": scheduler_state.last_status,
        "last_reason": scheduler_state.last_reason,
        "last_error": scheduler_state.last_error,
        "last_snapshot_id": scheduler_state.last_snapshot_id,
        "last_task_type": scheduler_state.last_task_type,
        "last_plan_id": scheduler_state.last_plan_id,
        "last_recommendation_ids": scheduler_state.last_recommendation_ids,
        "overrun_count": scheduler_state.overrun_count,
        "skipped_count": scheduler_state.skipped_count,
    }


def _public_status_payload(container: ApiContainer) -> dict[str, str]:
    try:
        with connection_scope(container.settings) as connection:
            current_auth_status = auth_service(container.settings, connection).status()
    except (sqlite3.Error, ValidationError) as exc:
        raise _service_state_failed() from exc

    return {"auth_status": current_auth_status}


def _set_scheduler_enabled(container: ApiContainer, *, enabled: bool) -> None:
    now = datetime.now(UTC)
    try:
        with connection_scope(container.settings) as connection:
            SchedulerStateRepository(connection).set_enabled(
                enabled,
                interval_seconds=container.settings.intraday_interval_seconds,
                run_on_start=container.settings.service_run_on_start_when_scheduler_enabled,
                now=now,
            )
            AuditService(AuditLogRepository(connection)).record_event(
                event_type=(
                    "service.scheduler.started"
                    if enabled
                    else "service.scheduler.stopped"
                ),
                recommendation_id=None,
                payload={"enabled": enabled},
                now=now,
            )
    except (sqlite3.Error, ValidationError) as exc:
        raise _service_state_failed() from exc


def _control_scheduler(scheduler: object | None, *, action: str) -> bool:
    if scheduler is None:
        raise _scheduler_control_failed()
    try:
        result = getattr(scheduler, action)()
    except Exception as exc:
        raise _scheduler_control_failed() from exc
    if isinstance(result, bool):
        return result
    return True


def _compensate_scheduler(scheduler: object | None, *, action: str) -> None:
    if scheduler is None:
        return
    try:
        getattr(scheduler, action)()
    except Exception:
        # 回滚只用于恢复进程内调度状态；失败时仍返回持久化错误，避免泄露底层异常。
        return


def _scheduler_running(scheduler: object | None) -> bool:
    if scheduler is None:
        return False
    value = getattr(scheduler, "is_running", False)
    if callable(value):
        value = value()
    return bool(value)


def _scheduler_next_run_time(scheduler: object | None) -> object | None:
    if scheduler is None:
        return None
    value = getattr(scheduler, "next_run_time", None)
    if callable(value):
        value = value()
    return value


def _scheduler_control_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="scheduler_error",
        message="scheduler control failed",
    )


def _service_state_failed() -> ApiError:
    return ApiError(
        status_code=500,
        code="internal_error",
        message="service state storage failed",
    )
