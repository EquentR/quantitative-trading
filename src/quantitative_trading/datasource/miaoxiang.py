from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MIAOXIANG_WATCHLIST_URL = (
    "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get"
)


class DatasourceError(RuntimeError):
    pass


class DatasourceInvalidError(DatasourceError):
    pass


class DatasourceQuotaExceededError(DatasourceError):
    pass


class DatasourceUnavailableError(DatasourceError):
    pass


class DatasourceContractError(DatasourceError):
    pass


@dataclass(frozen=True)
class RemoteWatchlistItem:
    symbol: str
    name: str
    rank: int


@dataclass(frozen=True)
class RemoteWatchlistResult:
    items: list[RemoteWatchlistItem]
    warnings: list[str]


WatchlistTransport = Callable[
    [str, dict[str, str], dict[str, object], float],
    tuple[int, object],
]


class MiaoxiangWatchlistAdapter:
    def __init__(
        self,
        *,
        transport: WatchlistTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._transport = transport or _urllib_transport
        self._timeout_seconds = timeout_seconds

    def fetch(self, api_key: str) -> RemoteWatchlistResult:
        try:
            status, payload = self._transport(
                MIAOXIANG_WATCHLIST_URL,
                {"Content-Type": "application/json", "apikey": api_key},
                {},
                self._timeout_seconds,
            )
        except DatasourceError:
            raise
        except (OSError, TimeoutError, URLError):
            raise DatasourceUnavailableError(
                "eastmoney datasource is unavailable"
            ) from None
        except Exception:
            raise DatasourceUnavailableError(
                "eastmoney datasource is unavailable"
            ) from None

        if status == 401:
            raise DatasourceInvalidError("eastmoney datasource credential is invalid")
        if not 200 <= status < 300:
            raise DatasourceUnavailableError("eastmoney datasource is unavailable")
        if not isinstance(payload, dict):
            raise DatasourceContractError("eastmoney datasource response contract changed")

        business_code = _business_code(payload)
        if business_code == 113:
            raise DatasourceQuotaExceededError("eastmoney datasource quota exceeded")
        if business_code in {114, 115, 116}:
            raise DatasourceInvalidError("eastmoney datasource credential is invalid")
        if business_code not in {None, 0}:
            raise DatasourceContractError("eastmoney datasource returned an unknown status")

        data_list = _data_list(payload)
        items: list[RemoteWatchlistItem] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for rank, raw_item in enumerate(data_list, start=1):
            if not isinstance(raw_item, dict):
                warnings.append(f"remote item at rank {rank} was ignored")
                continue
            symbol = raw_item.get("SECURITY_CODE")
            name = raw_item.get("SECURITY_SHORT_NAME")
            if (
                not isinstance(symbol, str)
                or len(symbol) != 6
                or not symbol.isascii()
                or not symbol.isdigit()
                or not isinstance(name, str)
                or not name.strip()
            ):
                warnings.append(f"remote item at rank {rank} was ignored")
                continue
            if symbol in seen:
                warnings.append(f"duplicate remote symbol {symbol} was ignored")
                continue
            seen.add(symbol)
            items.append(
                RemoteWatchlistItem(symbol=symbol, name=name.strip(), rank=rank)
            )
        return RemoteWatchlistResult(items=items, warnings=warnings)


def _business_code(payload: dict[str, Any]) -> int | None:
    values: list[int] = []
    for field in ("code", "status"):
        value = payload.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            values.append(value)
    nonzero = [value for value in values if value != 0]
    if nonzero:
        return nonzero[0]
    return 0 if values else None


def _data_list(payload: dict[str, Any]) -> list[object]:
    try:
        data_list = payload["data"]["allResults"]["result"]["dataList"]
    except (KeyError, TypeError):
        raise DatasourceContractError(
            "eastmoney datasource response contract changed"
        ) from None
    if not isinstance(data_list, list):
        raise DatasourceContractError("eastmoney datasource response contract changed")
    return data_list


def _urllib_transport(
    url: str,
    headers: dict[str, str],
    body: dict[str, object],
    timeout: float,
) -> tuple[int, object]:
    request = Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = response.status
            content = response.read()
    except HTTPError as exc:
        status = exc.code
        content = exc.read()
    try:
        payload: object = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DatasourceContractError(
            "eastmoney datasource response is not valid JSON"
        ) from None
    return status, payload
