from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect


def authenticated_client(tmp_path) -> tuple[TestClient, dict[str, str]]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_datasource_key_status_does_not_echo_secret(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    response = client.put(
        "/api/v1/datasource/eastmoney/key",
        json={"api_key": "secret-eastmoney-key"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "configured"
    assert "secret-eastmoney-key" not in response.text

    status_response = client.get("/api/v1/datasource/eastmoney/status", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["status"] in {"configured", "missing", "invalid"}
    assert "secret-eastmoney-key" not in status_response.text


def test_datasource_status_is_missing_before_key_is_configured(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.get("/api/v1/datasource/eastmoney/status", headers=headers)

    assert response.status_code == 200
    assert response.json()["provider"] == "eastmoney"
    assert response.json()["status"] == "missing"
    assert "api_key" not in response.text
    assert "encrypted_secret" not in response.text


def test_datasource_key_rejects_blank_input_without_persisting(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    blank_response = client.put(
        "/api/v1/datasource/eastmoney/key",
        json={"api_key": ""},
        headers=headers,
    )
    whitespace_response = client.put(
        "/api/v1/datasource/eastmoney/key",
        json={"api_key": "   "},
        headers=headers,
    )
    status_response = client.get("/api/v1/datasource/eastmoney/status", headers=headers)

    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    with connect(settings) as connection:
        row_count = connection.execute(
            "SELECT COUNT(*) FROM datasource_credentials WHERE provider = ?",
            ("eastmoney",),
        ).fetchone()[0]

    assert blank_response.status_code == 422
    assert whitespace_response.status_code == 422
    assert "   " not in whitespace_response.text
    assert status_response.json()["status"] == "missing"
    assert row_count == 0


def test_datasource_delete_key_redacts_deleted_secret(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)
    client.put(
        "/api/v1/datasource/eastmoney/key",
        json={"api_key": "secret-eastmoney-key"},
        headers=headers,
    )

    response = client.delete("/api/v1/datasource/eastmoney/key", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "missing"
    assert "secret-eastmoney-key" not in response.text
    assert "api_key" not in response.text
    assert "encrypted_secret" not in response.text


def test_datasource_check_is_local_status_update(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    missing_response = client.post("/api/v1/datasource/eastmoney/check", headers=headers)
    client.put(
        "/api/v1/datasource/eastmoney/key",
        json={"api_key": "secret-eastmoney-key"},
        headers=headers,
    )
    configured_response = client.post("/api/v1/datasource/eastmoney/check", headers=headers)

    assert missing_response.status_code == 200
    assert missing_response.json()["status"] == "missing"
    assert missing_response.json()["last_checked_at"] is not None
    assert configured_response.status_code == 200
    assert configured_response.json()["status"] == "configured"
    assert configured_response.json()["last_checked_at"] is not None
    assert "secret-eastmoney-key" not in configured_response.text
