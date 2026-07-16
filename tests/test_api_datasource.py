from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings
from quantitative_trading.datasource.miaoxiang import (
    DatasourceInvalidError,
    DatasourceQuotaExceededError,
    DatasourceUnavailableError,
    RemoteWatchlistResult,
)
from quantitative_trading.storage.sqlite import connect


class FakeWatchlistAdapter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.keys: list[str] = []

    def fetch(self, api_key: str) -> RemoteWatchlistResult:
        self.keys.append(api_key)
        if self.error is not None:
            raise self.error
        return RemoteWatchlistResult(items=[], warnings=[])


def authenticated_client(
    tmp_path,
    *,
    adapter: FakeWatchlistAdapter | None = None,
) -> tuple[TestClient, dict[str, str]]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings, miaoxiang_watchlist_adapter=adapter))
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


def test_datasource_check_queries_remote_once_and_accepts_empty_result(tmp_path) -> None:
    adapter = FakeWatchlistAdapter()
    client, headers = authenticated_client(tmp_path, adapter=adapter)

    missing_response = client.post("/api/v1/datasource/eastmoney/check", headers=headers)
    client.put(
        "/api/v1/datasource/eastmoney/key",
        json={"api_key": "secret-eastmoney-key"},
        headers=headers,
    )
    configured_response = client.post("/api/v1/datasource/eastmoney/check", headers=headers)

    assert missing_response.status_code == 409
    assert missing_response.json()["error"]["code"] == "datasource_not_configured"
    assert configured_response.status_code == 200
    assert configured_response.json()["status"] == "configured"
    assert configured_response.json()["last_checked_at"] is not None
    assert adapter.keys == ["secret-eastmoney-key"]
    assert "secret-eastmoney-key" not in configured_response.text


def test_datasource_check_marks_invalid_key_and_never_echoes_it(tmp_path) -> None:
    adapter = FakeWatchlistAdapter(DatasourceInvalidError("synthetic-key invalid"))
    client, headers = authenticated_client(tmp_path, adapter=adapter)
    client.put(
        "/api/v1/datasource/eastmoney/key",
        json={"api_key": "synthetic-key"},
        headers=headers,
    )

    response = client.post("/api/v1/datasource/eastmoney/check", headers=headers)
    status = client.get("/api/v1/datasource/eastmoney/status", headers=headers)

    assert response.status_code == 424
    assert response.json()["error"]["code"] == "datasource_invalid"
    assert status.json()["status"] == "invalid"
    assert status.json()["last_error"] == "datasource_invalid"
    assert "synthetic-key" not in response.text + status.text


def test_datasource_check_preserves_configured_status_for_transient_errors(tmp_path) -> None:
    cases = [
        (
            DatasourceQuotaExceededError("raw quota message"),
            429,
            "datasource_quota_exceeded",
        ),
        (DatasourceUnavailableError("raw network message"), 503, "datasource_unavailable"),
    ]
    for index, (error, http_status, code) in enumerate(cases):
        adapter = FakeWatchlistAdapter(error)
        case_path = tmp_path / str(index)
        case_path.mkdir()
        client, headers = authenticated_client(case_path, adapter=adapter)
        client.put(
            "/api/v1/datasource/eastmoney/key",
            json={"api_key": "synthetic-key"},
            headers=headers,
        )

        response = client.post("/api/v1/datasource/eastmoney/check", headers=headers)
        status = client.get("/api/v1/datasource/eastmoney/status", headers=headers)

        assert response.status_code == http_status
        assert response.json()["error"]["code"] == code
        assert status.json()["status"] == "configured"
        assert status.json()["last_error"] == code
        assert "raw" not in response.text + status.text
