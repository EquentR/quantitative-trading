from __future__ import annotations

from datetime import UTC, datetime

import uvicorn

from quantitative_trading.api.app import create_app
from quantitative_trading.api.routes.service import _safe_error_summary
from quantitative_trading.config import Settings
from quantitative_trading.runtime.account_snapshot_job import create_and_save_account_snapshot
from quantitative_trading.runtime.scheduler import SchedulerManager
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository
from quantitative_trading.storage.sqlite import connect, migrate


def run_api_service(settings: Settings) -> None:
    def run_snapshot_job(reason: str) -> None:
        started_at = datetime.now(UTC)
        status = "success"
        error = None
        snapshot_id = None
        try:
            created = create_and_save_account_snapshot(settings)
            snapshot_id = created.snapshot_id
        except Exception as exc:
            status = "failed"
            error = _safe_error_summary(exc)

        finished_at = datetime.now(UTC)
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
                status=status,
                reason=reason,
                error=error,
                snapshot_id=snapshot_id,
                now=finished_at,
            )

    scheduler = SchedulerManager(
        interval_seconds=settings.intraday_interval_seconds,
        timezone=settings.timezone,
        job=run_snapshot_job,
    )
    app = create_app(settings, scheduler=scheduler, restore_scheduler=True)
    with connect(settings) as connection:
        migrate(connection)
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=datetime.now(UTC),
        )
    if state.enabled and state.run_on_start:
        run_snapshot_job("startup")

    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
