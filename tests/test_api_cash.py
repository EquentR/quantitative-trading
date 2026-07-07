from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings


def authenticated_client(tmp_path) -> tuple[TestClient, dict[str, str]]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_cash_account_lifecycle(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    missing_response = client.get("/api/v1/cash/account", headers=headers)
    init_response = client.post(
        "/api/v1/cash/account",
        json={"cash": 50000, "note": "initial principal"},
        headers=headers,
    )
    transfer_response = client.post(
        "/api/v1/cash/transfers",
        json={"type": "transfer_in", "amount": 10000, "note": "bank transfer in"},
        headers=headers,
    )
    adjust_response = client.post(
        "/api/v1/cash/adjustments",
        json={"cash": 58000, "note": "manual broker correction"},
        headers=headers,
    )
    transactions_response = client.get("/api/v1/cash/transactions?limit=10", headers=headers)

    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "cash_account_not_initialized"
    assert init_response.status_code == 201
    assert init_response.json()["cash_balance"] == 50000
    assert transfer_response.status_code == 200
    assert transfer_response.json()["cash_balance"] == 60000
    assert adjust_response.status_code == 200
    assert adjust_response.json()["cash_balance"] == 58000
    assert adjust_response.json()["net_principal"] == 60000
    assert [item["type"] for item in transactions_response.json()] == [
        "initial_deposit",
        "transfer_in",
        "cash_adjustment",
    ]
    assert transactions_response.json()[0]["cash_before"] == 0
    assert transactions_response.json()[0]["cash_after"] == 50000
    assert transactions_response.json()[0]["occurred_at"] is not None
    assert transactions_response.json()[0]["note"] == "initial principal"


def test_cash_transfer_out_rejects_excess_cash(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post(
        "/api/v1/cash/account",
        json={"cash": 1000, "note": "initial principal"},
        headers=headers,
    )

    response = client.post(
        "/api/v1/cash/transfers",
        json={"type": "transfer_out", "amount": 1001, "note": "too much"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cash_transfer_invalid"


def test_cash_adjustment_requires_note(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post(
        "/api/v1/cash/account",
        json={"cash": 1000, "note": "initial principal"},
        headers=headers,
    )

    response = client.post(
        "/api/v1/cash/adjustments",
        json={"cash": 900, "note": ""},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "cash_transfer_invalid"


def test_cash_endpoints_require_authentication_after_setup(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})

    requests = [
        ("get", "/api/v1/cash/account", None),
        ("post", "/api/v1/cash/account", {"cash": 1000, "note": "initial principal"}),
        (
            "post",
            "/api/v1/cash/transfers",
            {"type": "transfer_in", "amount": 1000, "note": "bank transfer in"},
        ),
        (
            "post",
            "/api/v1/cash/adjustments",
            {"cash": 900, "note": "manual broker correction"},
        ),
        ("get", "/api/v1/cash/transactions", None),
    ]

    for method, path, json_body in requests:
        if json_body is None:
            response = getattr(client, method)(path)
        else:
            response = getattr(client, method)(path, json=json_body)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_cash_account_duplicate_initialization_returns_conflict(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post(
        "/api/v1/cash/account",
        json={"cash": 1000, "note": "initial principal"},
        headers=headers,
    )

    response = client.post(
        "/api/v1/cash/account",
        json={"cash": 2000, "note": "duplicate initial principal"},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "cash_account_already_initialized"
