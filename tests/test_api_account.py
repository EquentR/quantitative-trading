import sqlite3

from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings


def authenticated_client(tmp_path, monkeypatch) -> tuple[TestClient, dict[str, str]]:
    del monkeypatch
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


def test_create_snapshot_route_is_retired_without_collecting_market_data(
    tmp_path, monkeypatch
) -> None:
    client, headers = authenticated_client(tmp_path, monkeypatch)
    seed_cash_and_position(client, headers)

    create_response = client.post("/api/v1/account/snapshots", headers=headers)
    latest_response = client.get("/api/v1/account/snapshots/latest", headers=headers)
    assert create_response.status_code == 410
    assert create_response.json()["error"]["code"] == "account_snapshot_create_retired"
    assert create_response.json()["error"]["details"]["replacement"] == (
        "/api/v1/service/workflows/intraday/run"
    )
    assert latest_response.status_code == 404


def test_account_snapshot_fresh_query_is_retired_without_generating_snapshot(
    tmp_path, monkeypatch
) -> None:
    client, headers = authenticated_client(tmp_path, monkeypatch)
    seed_cash_and_position(client, headers)

    response = client.get("/api/v1/account/snapshot?fresh=true", headers=headers)

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "account_fresh_snapshot_retired"
    assert response.json()["error"]["details"]["replacement"] == (
        "/api/v1/service/workflows/intraday/run"
    )


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


def test_retired_create_route_does_not_initialize_configured_market_provider(tmp_path) -> None:
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

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "account_snapshot_create_retired"


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
