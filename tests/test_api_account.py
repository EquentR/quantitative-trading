import sqlite3
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


class FakeMarketProvider:
    calls: list[list[str]] = []

    def get_quotes(self, symbols):
        self.calls.append(list(symbols))
        return {
            "600000": QuoteSnapshot(
                symbol="600000",
                name="Pufa Bank",
                current_price=10.5,
                change_pct=1.2,
                data_time=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
                fetched_at=datetime(2026, 7, 7, 2, 30, 3, tzinfo=UTC),
                source="fake",
                status=QuoteStatus.OK,
            )
        }


class RaisingMarketProvider:
    calls: list[list[str]] = []

    def get_quotes(self, symbols):
        self.calls.append(list(symbols))
        raise RuntimeError("fake provider unavailable")


def authenticated_client(tmp_path, monkeypatch) -> tuple[TestClient, dict[str, str]]:
    import quantitative_trading.api.routes.account as account_routes

    monkeypatch.setattr(
        account_routes,
        "market_provider_from_settings",
        lambda settings: FakeMarketProvider(),
    )
    FakeMarketProvider.calls = []
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=True)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    return client, {"Authorization": f"Bearer {login.json()['access_token']}"}


def seed_cash_and_position(client: TestClient, headers: dict[str, str]) -> None:
    client.post(
        "/api/v1/cash/account",
        json={"cash": 50000, "note": "initial principal"},
        headers=headers,
    )
    client.post(
        "/api/v1/positions",
        json={
            "symbol": "600000",
            "name": "浦发银行",
            "quantity": 1000,
            "available_quantity": 800,
            "cost_price": 9.5,
            "opened_at": "2026-07-06",
            "note": "first lot",
        },
        headers=headers,
    )


def test_latest_snapshot_returns_not_found_when_empty(tmp_path, monkeypatch) -> None:
    client, headers = authenticated_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/account/snapshots/latest", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "snapshot_not_found"


def test_account_snapshot_returns_not_found_when_empty(tmp_path, monkeypatch) -> None:
    client, headers = authenticated_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/account/snapshot", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "snapshot_not_found"


def test_create_snapshot_persists_and_latest_reads_it(tmp_path, monkeypatch) -> None:
    client, headers = authenticated_client(tmp_path, monkeypatch)
    seed_cash_and_position(client, headers)

    create_response = client.post("/api/v1/account/snapshots", headers=headers)
    latest_response = client.get("/api/v1/account/snapshots/latest", headers=headers)
    snapshot_response = client.get("/api/v1/account/snapshot", headers=headers)

    assert create_response.status_code == 201
    assert create_response.json()["snapshot"]["status"] == "ok"
    assert create_response.json()["snapshot_id"] == 1
    assert latest_response.status_code == 200
    assert latest_response.json()["status"] == "ok"
    assert latest_response.json()["market_value"] == 10500
    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["market_value"] == 10500
    assert FakeMarketProvider.calls == [["600000"]]


def test_account_snapshot_fresh_query_generates_snapshot(tmp_path, monkeypatch) -> None:
    client, headers = authenticated_client(tmp_path, monkeypatch)
    seed_cash_and_position(client, headers)

    response = client.get("/api/v1/account/snapshot?fresh=true", headers=headers)

    assert response.status_code == 200
    assert response.json()["snapshot"]["status"] == "ok"
    assert response.json()["snapshot_id"] == 1


def test_account_routes_require_authentication_after_setup(tmp_path, monkeypatch) -> None:
    client, _headers = authenticated_client(tmp_path, monkeypatch)

    requests = [
        ("get", "/api/v1/account/snapshot"),
        ("get", "/api/v1/account/snapshot?fresh=true"),
        ("post", "/api/v1/account/snapshots"),
        ("get", "/api/v1/account/snapshots/latest"),
    ]

    for method, path in requests:
        response = getattr(client, method)(path)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_market_provider_failure_persists_unavailable_snapshot(tmp_path, monkeypatch) -> None:
    import quantitative_trading.api.routes.account as account_routes

    monkeypatch.setattr(
        account_routes,
        "market_provider_from_settings",
        lambda settings: RaisingMarketProvider(),
    )
    RaisingMarketProvider.calls = []
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=True)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    seed_cash_and_position(client, headers)

    create_response = client.post("/api/v1/account/snapshots", headers=headers)
    latest_response = client.get("/api/v1/account/snapshots/latest", headers=headers)

    assert create_response.status_code == 201
    assert create_response.json()["snapshot"]["status"] == "market_data_unavailable"
    assert create_response.json()["snapshot"]["market_value"] is None
    assert "fake provider unavailable" in create_response.json()["snapshot"]["warnings"][0]
    assert latest_response.status_code == 200
    assert latest_response.json()["status"] == "market_data_unavailable"
    assert RaisingMarketProvider.calls == [["600000"]]


def test_unsupported_market_provider_returns_uniform_validation_error(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "api.db",
        enable_market_fetch=True,
        market_provider="bad",
    )
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post("/api/v1/account/snapshots", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"] == "unsupported market provider"
    assert response.json()["error"]["details"] == {"market_provider": "bad"}


def test_latest_snapshot_storage_error_returns_uniform_internal_error(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.api.routes.account as account_routes

    class BrokenSnapshotRepository:
        def __init__(self, connection) -> None:
            pass

        def latest(self):
            raise sqlite3.OperationalError("database disk image is malformed")

    monkeypatch.setattr(
        account_routes,
        "AccountSnapshotRepository",
        BrokenSnapshotRepository,
    )
    client, headers = authenticated_client(tmp_path, monkeypatch)

    response = client.get("/api/v1/account/snapshots/latest", headers=headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == "account snapshot storage failed"
    assert "database disk image" not in response.text


def test_create_snapshot_storage_error_returns_uniform_internal_error(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.api.routes.account as account_routes

    def failing_create_snapshot(settings, *, market_provider_factory):
        raise sqlite3.OperationalError("database is locked: /tmp/private.db")

    monkeypatch.setattr(
        account_routes,
        "create_and_save_account_snapshot",
        failing_create_snapshot,
    )
    client, headers = authenticated_client(tmp_path, monkeypatch)

    response = client.post("/api/v1/account/snapshots", headers=headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == "account snapshot storage failed"
    assert "/tmp/private.db" not in response.text
