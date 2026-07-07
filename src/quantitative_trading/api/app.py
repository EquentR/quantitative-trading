from __future__ import annotations

from fastapi import FastAPI

from quantitative_trading.api import dependencies
from quantitative_trading.api.dependencies import ApiContainer
from quantitative_trading.api.errors import install_error_handlers
from quantitative_trading.api.routes import auth, cash, positions, service
from quantitative_trading.config import Settings


def create_app(settings: Settings) -> FastAPI:
    # 进程启动时完成一次幂等迁移，避免每个 API 请求都产生 schema 写事务。
    with dependencies.connect(settings) as connection:
        dependencies.migrate(connection)

    app = FastAPI(title="Quantitative Trading API")
    container = ApiContainer(settings=settings)
    app.dependency_overrides[dependencies.get_container] = lambda: container

    install_error_handlers(app)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(cash.router, prefix="/api/v1")
    app.include_router(positions.router, prefix="/api/v1")
    app.include_router(service.router, prefix="/api/v1")

    return app
