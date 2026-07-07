from __future__ import annotations

from fastapi import Depends, FastAPI

from quantitative_trading.api import dependencies
from quantitative_trading.api.dependencies import ApiContainer, require_token
from quantitative_trading.api.errors import install_error_handlers
from quantitative_trading.api.routes import auth, service
from quantitative_trading.config import Settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Quantitative Trading API")
    container = ApiContainer(settings=settings)
    app.dependency_overrides[dependencies.get_container] = lambda: container

    install_error_handlers(app)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(service.router, prefix="/api/v1")

    @app.get("/api/v1/positions", dependencies=[Depends(require_token)])
    def positions_placeholder() -> dict[str, list[object]]:
        # Task 5 会替换为真实持仓路由；当前占位只用于验证业务路由默认受认证保护。
        return {"positions": []}

    return app
