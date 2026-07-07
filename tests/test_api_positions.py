import csv
from io import StringIO

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


def position_payload(symbol: str = "600000") -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": "浦发银行",
        "quantity": 1000,
        "available_quantity": 800,
        "cost_price": 9.5,
        "opened_at": "2026-07-06",
        "note": "first lot",
    }


def test_positions_crud(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    create_response = client.post("/api/v1/positions", json=position_payload(), headers=headers)
    list_response = client.get("/api/v1/positions", headers=headers)
    detail_response = client.get("/api/v1/positions/600000", headers=headers)
    update_payload = position_payload()
    update_payload["quantity"] = 1200
    update_response = client.put(
        "/api/v1/positions/600000",
        json=update_payload,
        headers=headers,
    )
    delete_response = client.delete("/api/v1/positions/600000", headers=headers)
    empty_response = client.get("/api/v1/positions", headers=headers)

    assert create_response.status_code == 201
    assert create_response.json()["symbol"] == "600000"
    assert list_response.status_code == 200
    assert [item["symbol"] for item in list_response.json()] == ["600000"]
    assert detail_response.status_code == 200
    assert detail_response.json()["name"] == "浦发银行"
    assert update_response.status_code == 200
    assert update_response.json()["quantity"] == 1200
    assert delete_response.status_code == 204
    assert empty_response.json() == []


def test_update_uses_path_symbol_when_body_symbol_is_omitted(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post("/api/v1/positions", json=position_payload(), headers=headers)
    update_payload = position_payload()
    update_payload.pop("symbol")
    update_payload["quantity"] = 1200
    update_payload["available_quantity"] = 900

    update_response = client.put(
        "/api/v1/positions/600000",
        json=update_payload,
        headers=headers,
    )
    detail_response = client.get("/api/v1/positions/600000", headers=headers)

    assert update_response.status_code == 200
    assert update_response.json()["symbol"] == "600000"
    assert update_response.json()["quantity"] == 1200
    assert detail_response.json()["available_quantity"] == 900


def test_position_detail_missing_returns_uniform_error(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.get("/api/v1/positions/600000", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "position_not_found"


def test_get_invalid_path_symbol_returns_validation_error(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.get("/api/v1/positions/invalid", headers=headers)

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["errors"][0]["loc"] == ["path", "symbol"]


def test_positions_json_import_is_atomic(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.post(
        "/api/v1/positions/import",
        json={"positions": [position_payload("600000"), position_payload("000001")]},
        headers=headers,
    )

    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()] == ["000001", "600000"]


def test_positions_csv_import_and_export(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    csv_text = (
        "symbol,name,quantity,available_quantity,cost_price,opened_at,note\n"
        "600000,浦发银行,1000,800,10.0,2026-07-06,first lot\n"
    )

    import_response = client.post(
        "/api/v1/positions/import-csv",
        files={"file": ("positions.csv", csv_text, "text/csv")},
        headers=headers,
    )
    export_response = client.get("/api/v1/positions/export-csv", headers=headers)

    assert import_response.status_code == 200
    assert import_response.json()[0]["symbol"] == "600000"
    assert export_response.status_code == 200
    reader = csv.DictReader(StringIO(export_response.text))
    rows = list(reader)
    assert rows[0]["symbol"] == "600000"
    assert rows[0]["name"] == "浦发银行"
    assert rows[0]["cost_price"] == "10"


def test_positions_csv_import_bad_header_returns_validation_error(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    csv_text = (
        "symbol,name,quantity,available_quantity,cost_price,opened_at,note,extra\n"
        "600000,浦发银行,1000,800,9.5,2026-07-06,extra header,x\n"
    )

    response = client.post(
        "/api/v1/positions/import-csv",
        files={"file": ("positions.csv", csv_text, "text/csv")},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_positions_requires_authentication_after_setup(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})

    response = client.get("/api/v1/positions")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_create_duplicate_position_returns_conflict(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post("/api/v1/positions", json=position_payload(), headers=headers)

    response = client.post("/api/v1/positions", json=position_payload(), headers=headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "position_conflict"


def test_update_path_body_symbol_mismatch_returns_validation_error(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.put(
        "/api/v1/positions/600000",
        json=position_payload("000001"),
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_update_invalid_path_symbol_returns_validation_error(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    update_payload = position_payload()
    update_payload.pop("symbol")

    response = client.put(
        "/api/v1/positions/invalid",
        json=update_payload,
        headers=headers,
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["errors"][0]["loc"] == ["path", "symbol"]


def test_delete_missing_position_returns_not_found(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.delete("/api/v1/positions/600000", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "position_not_found"


def test_delete_invalid_path_symbol_returns_validation_error(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.delete("/api/v1/positions/invalid", headers=headers)

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["errors"][0]["loc"] == ["path", "symbol"]


def test_positions_json_import_failure_preserves_existing_positions(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.post("/api/v1/positions", json=position_payload("600010"), headers=headers)
    invalid_payload = position_payload("600000")
    invalid_payload["available_quantity"] = 2000

    response = client.post(
        "/api/v1/positions/import",
        json={"positions": [position_payload("000001"), invalid_payload]},
        headers=headers,
    )
    list_response = client.get("/api/v1/positions", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert [item["symbol"] for item in list_response.json()] == ["600010"]
