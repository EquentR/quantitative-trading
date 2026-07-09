from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.datasource.credentials import redact_secret
from quantitative_trading.datasource.status import (
    EASTMONEY_PROVIDER,
    DatasourceCredentialsRepository,
    DatasourceStatus,
    DatasourceStatusService,
)


router = APIRouter(
    prefix="/datasource",
    tags=["datasource"],
    dependencies=[Depends(require_token)],
)


class DatasourceKeyRequest(BaseModel):
    api_key: str


def _blank_api_key_error() -> ApiError:
    return ApiError(
        status_code=422,
        code="validation_error",
        message="api key must not be blank",
    )


def _service(connection) -> DatasourceStatusService:
    return DatasourceStatusService(DatasourceCredentialsRepository(connection))


@router.get("/eastmoney/status", response_model=DatasourceStatus)
def get_eastmoney_status(
    container: ApiContainer = Depends(get_container),
) -> DatasourceStatus:
    with connection_scope(container.settings) as connection:
        return _service(connection).get_status(EASTMONEY_PROVIDER)


@router.put("/eastmoney/key", response_model=DatasourceStatus)
def set_eastmoney_key(
    payload: DatasourceKeyRequest,
    container: ApiContainer = Depends(get_container),
) -> DatasourceStatus:
    if redact_secret(payload.api_key) == "missing":
        raise _blank_api_key_error()
    with connection_scope(container.settings) as connection:
        return _service(connection).set_key(payload.api_key, EASTMONEY_PROVIDER)


@router.delete("/eastmoney/key", response_model=DatasourceStatus)
def delete_eastmoney_key(
    container: ApiContainer = Depends(get_container),
) -> DatasourceStatus:
    with connection_scope(container.settings) as connection:
        return _service(connection).delete_key(EASTMONEY_PROVIDER)


@router.post("/eastmoney/check", response_model=DatasourceStatus)
def check_eastmoney_key(
    container: ApiContainer = Depends(get_container),
) -> DatasourceStatus:
    with connection_scope(container.settings) as connection:
        return _service(connection).check(EASTMONEY_PROVIDER)
