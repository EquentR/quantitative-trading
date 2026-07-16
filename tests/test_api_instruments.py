from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings
from quantitative_trading.datasource.miaoxiang import (
    RemoteWatchlistItem,
    RemoteWatchlistResult,
)
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.adapters import InstrumentDirectorySnapshot
from quantitative_trading.instrument.repository import InstrumentRepository
from quantitative_trading.storage.sqlite import connect


NOW = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)


class FakeWatchlistAdapter:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def fetch(self, api_key: str) -> RemoteWatchlistResult:
        self.keys.append(api_key)
        return RemoteWatchlistResult(
            items=[RemoteWatchlistItem("510300", "供应商名称", 1)],
            warnings=[],
        )


class FakeDirectoryAdapter:
    sources = ("test-directory",)

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, trade_date: date) -> InstrumentDirectorySnapshot:
        self.calls += 1
        return InstrumentDirectorySnapshot(
            items=[
                InstrumentMetadata(
                    symbol="510300",
                    name="沪深300ETF",
                    exchange=Exchange.SH,
                    instrument_type=InstrumentType.ETF,
                    settlement_cycle=SettlementCycle.T1,
                    metadata_source="test-directory",
                    metadata_checked_at=NOW,
                    rule_version="test-rules-v1",
                )
            ],
            source_trade_dates={"test-directory": trade_date},
            warnings=[],
        )


def authenticated_client(tmp_path) -> tuple[TestClient, dict[str, str], Settings, FakeWatchlistAdapter]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    adapter = FakeWatchlistAdapter()
    directory_adapter = FakeDirectoryAdapter()
    client = TestClient(
        create_app(
            settings,
            miaoxiang_watchlist_adapter=adapter,
            instrument_directory_adapter=directory_adapter,
        )
    )
    client.app.state.instrument_directory_adapter = directory_adapter
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    client.put(
        "/api/v1/datasource/eastmoney/key",
        json={"api_key": "synthetic-key"},
        headers=headers,
    )
    with connect(settings) as connection:
        InstrumentRepository(connection).replace_catalog(
            [
                InstrumentMetadata(
                    symbol="510300",
                    name="沪深300ETF",
                    exchange=Exchange.SH,
                    instrument_type=InstrumentType.ETF,
                    settlement_cycle=SettlementCycle.T1,
                    metadata_source="test-directory",
                    metadata_checked_at=NOW,
                    rule_version="test-rules-v1",
                )
            ]
        )
    return client, headers, settings, adapter


def test_instrument_preview_routes_require_authentication(tmp_path) -> None:
    client, _headers, _settings, _adapter = authenticated_client(tmp_path)
    assert client.get("/api/v1/instruments/eastmoney-candidates").status_code == 401
    assert client.get("/api/v1/instruments/search?q=510300").status_code == 401
    assert client.post(
        "/api/v1/watchlist/pinned/select",
        json={"preview_id": "00000000-0000-4000-8000-000000000001", "symbols": ["510300"]},
    ).status_code == 401


def test_eastmoney_preview_then_select_is_explicit_and_idempotent(tmp_path) -> None:
    client, headers, _settings, adapter = authenticated_client(tmp_path)

    preview_response = client.get(
        "/api/v1/instruments/eastmoney-candidates", headers=headers
    )
    before = client.get("/api/v1/watchlist/pinned", headers=headers)
    payload = {
        "preview_id": preview_response.json()["preview_id"],
        "symbols": ["510300"],
    }
    selected = client.post(
        "/api/v1/watchlist/pinned/select", json=payload, headers=headers
    )
    repeated = client.post(
        "/api/v1/watchlist/pinned/select", json=payload, headers=headers
    )

    assert preview_response.status_code == 200
    assert preview_response.json()["items"][0]["name"] == "沪深300ETF"
    assert before.json() == []
    assert selected.status_code == 200
    assert selected.json()["items"][0]["plan_enabled"] is True
    assert selected.json()["items"][0]["instrument_type"] == "etf"
    assert selected.json()["items"][0]["settlement_cycle"] == "t1"
    assert selected.json()["items"][0]["exchange"] == "SH"
    assert repeated.json()["items"] == selected.json()["items"]
    assert adapter.keys == ["synthetic-key"]
    assert "synthetic-key" not in preview_response.text + selected.text


def test_search_creates_preview_without_eastmoney_key_or_watchlist_write(tmp_path) -> None:
    client, headers, _settings, adapter = authenticated_client(tmp_path)
    client.delete("/api/v1/datasource/eastmoney/key", headers=headers)

    response = client.get("/api/v1/instruments/search?q=510", headers=headers)

    assert response.status_code == 200
    assert response.json()["query"] == "510"
    assert [item["symbol"] for item in response.json()["items"]] == ["510300"]
    assert client.get("/api/v1/watchlist/pinned", headers=headers).json() == []
    assert adapter.keys == []


def test_blank_search_is_rejected_without_refreshing_directory(tmp_path) -> None:
    client, headers, _settings, _adapter = authenticated_client(tmp_path)
    directory = client.app.state.instrument_directory_adapter

    response = client.get("/api/v1/instruments/search?q=%20", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert directory.calls == 0


def test_select_rejects_client_metadata_and_maps_preview_errors(tmp_path) -> None:
    client, headers, settings, _adapter = authenticated_client(tmp_path)
    preview = client.get(
        "/api/v1/instruments/eastmoney-candidates", headers=headers
    ).json()
    extra = client.post(
        "/api/v1/watchlist/pinned/select",
        json={
            "preview_id": preview["preview_id"],
            "symbols": ["510300"],
            "instrument_type": "a_share",
        },
        headers=headers,
    )
    missing = client.post(
        "/api/v1/watchlist/pinned/select",
        json={
            "preview_id": "00000000-0000-4000-8000-000000000001",
            "symbols": ["510300"],
        },
        headers=headers,
    )
    with connect(settings) as connection:
        connection.execute(
            "UPDATE instrument_previews SET expires_at=? WHERE preview_id=?",
            ("2020-01-01T00:00:00+00:00", preview["preview_id"]),
        )
        connection.commit()
    expired = client.post(
        "/api/v1/watchlist/pinned/select",
        json={"preview_id": preview["preview_id"], "symbols": ["510300"]},
        headers=headers,
    )

    assert extra.status_code == 422
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "instrument_preview_not_found"
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "instrument_preview_expired"
