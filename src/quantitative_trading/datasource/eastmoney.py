from __future__ import annotations

from quantitative_trading.datasource.credentials import redact_secret
from quantitative_trading.datasource.miaoxiang import (
    DatasourceContractError,
    DatasourceInvalidError,
    DatasourceQuotaExceededError,
    DatasourceUnavailableError,
    MiaoxiangWatchlistAdapter,
    RemoteWatchlistResult,
)
from quantitative_trading.datasource.status import (
    EASTMONEY_PROVIDER,
    DatasourceCredentialsRepository,
    DatasourceCredentialStatus,
    DatasourceStatusService,
)


class DatasourceNotConfiguredError(RuntimeError):
    pass


def fetch_eastmoney_watchlist(
    repository: DatasourceCredentialsRepository,
    adapter: MiaoxiangWatchlistAdapter,
) -> RemoteWatchlistResult:
    credential = repository.get(EASTMONEY_PROVIDER)
    if credential is None or redact_secret(credential.stored_secret) == "missing":
        raise DatasourceNotConfiguredError("eastmoney datasource is not configured")
    service = DatasourceStatusService(repository)
    try:
        result = adapter.fetch(credential.stored_secret)
    except DatasourceInvalidError:
        service.record_remote_check(
            status=DatasourceCredentialStatus.INVALID,
            last_error="datasource_invalid",
        )
        raise
    except DatasourceQuotaExceededError:
        service.record_remote_check(
            status=credential.status,
            last_error="datasource_quota_exceeded",
        )
        raise
    except DatasourceUnavailableError:
        service.record_remote_check(
            status=credential.status,
            last_error="datasource_unavailable",
        )
        raise
    except DatasourceContractError:
        service.record_remote_check(
            status=credential.status,
            last_error="datasource_contract_error",
        )
        raise
    service.record_remote_check(
        status=DatasourceCredentialStatus.CONFIGURED,
        last_error=None,
    )
    return result
