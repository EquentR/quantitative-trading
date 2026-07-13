from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI

from quantitative_trading.api import dependencies
from quantitative_trading.api.dependencies import ApiContainer
from quantitative_trading.api.errors import install_error_handlers
from quantitative_trading.api.routes import (
    account,
    audit,
    auth,
    cash,
    datasource,
    email_deliveries,
    email_settings,
    feedback,
    market,
    market_read,
    notifications,
    plans,
    positions,
    recommendations,
    service,
    service_workflows,
    universe,
    watchlist,
)
from quantitative_trading.config import Settings
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository


def create_app(
    settings: Settings,
    *,
    scheduler: Any | None = None,
    restore_scheduler: bool = False,
    email_sender: object | None = None,
    smtp_connection_tester: object | None = None,
) -> FastAPI:
    # 进程启动时完成一次幂等迁移，避免每个 API 请求都产生 schema 写事务。
    with dependencies.connect(settings) as connection:
        dependencies.migrate(connection)

    app = FastAPI(title="Quantitative Trading API")
    container = ApiContainer(
        settings=settings,
        scheduler=scheduler,
        email_sender=email_sender,
        smtp_connection_tester=smtp_connection_tester,
    )
    app.dependency_overrides[dependencies.get_container] = lambda: container

    install_error_handlers(app)
    app.include_router(account.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(cash.router, prefix="/api/v1")
    app.include_router(datasource.router, prefix="/api/v1")
    app.include_router(email_deliveries.router, prefix="/api/v1")
    app.include_router(email_settings.router, prefix="/api/v1")
    app.include_router(email_settings.connection_test_router, prefix="/api/v1")
    app.include_router(feedback.router, prefix="/api/v1")
    app.include_router(market.router, prefix="/api/v1")
    app.include_router(market_read.router, prefix="/api/v1")
    app.include_router(notifications.router, prefix="/api/v1")
    app.include_router(plans.router, prefix="/api/v1")
    app.include_router(positions.router, prefix="/api/v1")
    app.include_router(recommendations.router, prefix="/api/v1")
    app.include_router(service.router, prefix="/api/v1")
    app.include_router(service_workflows.router, prefix="/api/v1")
    app.include_router(universe.router, prefix="/api/v1")
    app.include_router(watchlist.router, prefix="/api/v1")

    if restore_scheduler and scheduler is not None:
        with dependencies.connect(settings) as connection:
            state = SchedulerStateRepository(connection).get_or_create(
                interval_seconds=settings.intraday_interval_seconds,
                run_on_start=settings.service_run_on_start_when_scheduler_enabled,
                now=datetime.now(UTC),
            )
        if state.enabled:
            scheduler.start()

    return app
