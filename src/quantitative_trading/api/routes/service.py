from __future__ import annotations

from fastapi import APIRouter, Depends

from quantitative_trading.api.dependencies import (
    ApiContainer,
    auth_service,
    connection_scope,
    get_container,
)


router = APIRouter(prefix="/service", tags=["service"])


@router.get("/status")
def status(container: ApiContainer = Depends(get_container)) -> dict[str, object]:
    with connection_scope(container.settings) as connection:
        current_auth_status = auth_service(container.settings, connection).status()
    return {
        "auth_status": current_auth_status,
        "scheduler_enabled": False,
        "scheduler_running": False,
        "next_run_time": None,
        "last_status": None,
        "last_error": None,
    }
