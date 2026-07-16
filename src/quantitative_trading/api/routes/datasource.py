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
from quantitative_trading.datasource.eastmoney import (
    DatasourceNotConfiguredError,
    fetch_eastmoney_watchlist as fetch_and_record_eastmoney_watchlist,
)
from quantitative_trading.datasource.miaoxiang import (
    DatasourceContractError,
    DatasourceInvalidError,
    DatasourceQuotaExceededError,
    DatasourceUnavailableError,
    MiaoxiangWatchlistAdapter,
)
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


def fetch_eastmoney_watchlist(connection, container: ApiContainer):  # noqa: ANN001, ANN201
    repository = DatasourceCredentialsRepository(connection)
    adapter = container.miaoxiang_watchlist_adapter or MiaoxiangWatchlistAdapter()
    try:
        return fetch_and_record_eastmoney_watchlist(repository, adapter)
    except DatasourceNotConfiguredError as exc:
        raise ApiError(
            status_code=409,
            code="datasource_not_configured",
            message="eastmoney datasource is not configured",
        ) from exc
    except DatasourceInvalidError as exc:
        raise ApiError(
            status_code=424,
            code="datasource_invalid",
            message="eastmoney datasource credential is invalid",
        ) from exc
    except DatasourceQuotaExceededError as exc:
        raise ApiError(
            status_code=429,
            code="datasource_quota_exceeded",
            message="eastmoney datasource quota exceeded",
        ) from exc
    except DatasourceUnavailableError as exc:
        raise ApiError(
            status_code=503,
            code="datasource_unavailable",
            message="eastmoney datasource is unavailable",
        ) from exc
    except DatasourceContractError as exc:
        raise ApiError(
            status_code=502,
            code="datasource_contract_error",
            message="eastmoney datasource response contract changed",
        ) from exc


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
        fetch_eastmoney_watchlist(connection, container)
        return _service(connection).get_status(EASTMONEY_PROVIDER)
