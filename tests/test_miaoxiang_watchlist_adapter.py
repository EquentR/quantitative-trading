from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from quantitative_trading.datasource.miaoxiang import (
    DatasourceContractError,
    DatasourceInvalidError,
    DatasourceQuotaExceededError,
    DatasourceUnavailableError,
    MiaoxiangWatchlistAdapter,
)


def response(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": 0,
        "data": {"allResults": {"result": {"dataList": items}}},
    }


class RecordingTransport:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self.payload = payload
        self.calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def __call__(self, url, headers, body, timeout):  # noqa: ANN001
        self.calls.append((url, headers, body, timeout))
        return self.status, self.payload


def test_adapter_uses_read_only_vendor_contract_and_preserves_first_rank() -> None:
    transport = RecordingTransport(
        200,
        response(
            [
                {"SECURITY_CODE": "510300", "SECURITY_SHORT_NAME": "沪深300ETF"},
                {"SECURITY_CODE": "600519", "SECURITY_SHORT_NAME": "贵州茅台"},
                {"SECURITY_CODE": "510300", "SECURITY_SHORT_NAME": "重复项"},
            ]
        ),
    )

    result = MiaoxiangWatchlistAdapter(transport=transport).fetch("synthetic-key")

    assert [(item.symbol, item.name, item.rank) for item in result.items] == [
        ("510300", "沪深300ETF", 1),
        ("600519", "贵州茅台", 2),
    ]
    assert result.warnings == ["duplicate remote symbol 510300 was ignored"]
    assert transport.calls == [
        (
            "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get",
            {"Content-Type": "application/json", "apikey": "synthetic-key"},
            {},
            30.0,
        )
    ]


def test_adapter_accepts_successful_empty_watchlist() -> None:
    result = MiaoxiangWatchlistAdapter(
        transport=RecordingTransport(200, response([]))
    ).fetch("synthetic-key")
    assert result.items == []
    assert result.warnings == []


@pytest.mark.parametrize("business_code", [114, 115, 116])
def test_adapter_classifies_invalid_business_codes(business_code: int) -> None:
    adapter = MiaoxiangWatchlistAdapter(
        transport=RecordingTransport(200, {"status": business_code, "message": "raw"})
    )
    with pytest.raises(DatasourceInvalidError, match="invalid"):
        adapter.fetch("synthetic-key")


def test_adapter_classifies_http_401_and_quota() -> None:
    with pytest.raises(DatasourceInvalidError):
        MiaoxiangWatchlistAdapter(
            transport=RecordingTransport(401, {"message": "raw"})
        ).fetch("synthetic-key")


def test_adapter_does_not_allow_zero_code_to_mask_error_status() -> None:
    with pytest.raises(DatasourceInvalidError):
        MiaoxiangWatchlistAdapter(
            transport=RecordingTransport(
                200,
                {"code": 0, "status": 114, "message": "raw"},
            )
        ).fetch("synthetic-key")
    with pytest.raises(DatasourceQuotaExceededError):
        MiaoxiangWatchlistAdapter(
            transport=RecordingTransport(200, {"code": 113, "message": "raw"})
        ).fetch("synthetic-key")


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        {"status": 0},
        {"status": 0, "data": {"allResults": {"result": {"dataList": {}}}}},
    ],
)
def test_adapter_rejects_malformed_response_without_echoing_payload(payload: object) -> None:
    with pytest.raises(DatasourceContractError) as captured:
        MiaoxiangWatchlistAdapter(
            transport=RecordingTransport(200, payload)
        ).fetch("synthetic-key")
    assert "not-json" not in str(captured.value)
    assert json.dumps(payload, ensure_ascii=False) not in str(captured.value)


def test_adapter_maps_transport_failure_without_leaking_key() -> None:
    def failing_transport(*_args):  # noqa: ANN002
        raise URLError("synthetic-key connection failed")

    with pytest.raises(DatasourceUnavailableError) as captured:
        MiaoxiangWatchlistAdapter(transport=failing_transport).fetch("synthetic-key")
    assert "synthetic-key" not in str(captured.value)
    assert captured.value.__cause__ is None
