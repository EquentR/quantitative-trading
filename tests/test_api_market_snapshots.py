import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.api.routes import market as market_routes
from quantitative_trading.config import Settings
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


FETCHED_AT = datetime(2026, 7, 13, 2, 30, 3, tzinfo=UTC)
DATA_TIME = datetime(2026, 7, 13, 2, 30, tzinfo=UTC)


class RecordingMarketProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        self.calls.append(list(symbols))
        return {
            symbol: QuoteSnapshot(
                symbol=symbol,
                name=f"Name {symbol}",
                current_price=10.5,
                change_pct=1.2,
                data_time=DATA_TIME,
                fetched_at=FETCHED_AT,
                source="fake_provider",
                status=QuoteStatus.OK,
            )
            for symbol in symbols
        }


class RaisingMarketProvider:
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        raise RuntimeError("provider unavailable")


def authenticated_client(
    tmp_path,
    monkeypatch,
    *,
    provider=None,
    settings_overrides: dict[str, object] | None = None,
    raise_server_exceptions: bool = True,
) -> tuple[TestClient, dict[str, str]]:
    if provider is not None:
        monkeypatch.setattr(
            market_routes,
            "market_provider_from_settings",
            lambda settings: provider,
        )

    settings = Settings(
        database_path=tmp_path / "api.db",
        enable_market_fetch=True,
        **(settings_overrides or {}),
    )
    client = TestClient(
        create_app(settings),
        raise_server_exceptions=raise_server_exceptions,
    )
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    return client, {"Authorization": f"Bearer {login.json()['access_token']}"}


def seed_decision_universe(client: TestClient, headers: dict[str, str]) -> None:
    client.post(
        "/api/v1/positions",
        json={
            "symbol": "600000",
            "name": "Pufa Bank",
            "quantity": 1000,
            "available_quantity": 800,
            "cost_price": 9.5,
            "opened_at": "2026-07-06",
            "note": "first lot",
        },
        headers=headers,
    )
    for symbol, enabled in (("000001", True), ("000002", False)):
        client.post(
            "/api/v1/watchlist/pinned",
            json={
                "symbol": symbol,
                "name": f"Name {symbol}",
                "rank": 1 if enabled else 2,
                "plan_enabled": enabled,
                "note": "manual watch",
            },
            headers=headers,
        )


def assert_storage_error_is_sanitized(response) -> None:
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "market snapshot storage failed",
            "details": {},
        }
    }
    for unsafe in (
        "SELECT secret",
        "trigger_secret",
        "raw_payload",
        "api_key",
        "token",
        "cookie",
        "password",
        "/home/private/market.db",
    ):
        assert unsafe not in response.text


def test_create_market_snapshot_and_read_latest_and_detail(tmp_path, monkeypatch) -> None:
    provider = RecordingMarketProvider()
    client, headers = authenticated_client(tmp_path, monkeypatch, provider=provider)
    seed_decision_universe(client, headers)

    created = client.post("/api/v1/market/snapshots", headers=headers)

    assert created.status_code == 201
    assert set(created.json()) == {"snapshot_id", "snapshot"}
    assert created.json()["snapshot_id"] == 1
    snapshot = created.json()["snapshot"]
    assert set(snapshot["quote_snapshot_refs"]) == {"000001", "600000"}
    assert "000002" not in snapshot["quote_snapshot_refs"]
    assert provider.calls == [["000001", "600000"]]
    for field in ("data_time", "fetched_at"):
        timestamp = datetime.fromisoformat(snapshot[field])
        assert timestamp.tzinfo is not None
        assert timestamp.utcoffset() is not None

    latest = client.get("/api/v1/market/snapshots/latest", headers=headers)
    detail = client.get(
        f"/api/v1/market/snapshots/{created.json()['snapshot_id']}",
        headers=headers,
    )

    assert latest.status_code == 200
    assert latest.json() == snapshot
    assert detail.status_code == 200
    assert detail.json() == snapshot
    assert provider.calls == [["000001", "600000"]]


def test_market_snapshot_routes_require_authentication(tmp_path, monkeypatch) -> None:
    client, _headers = authenticated_client(
        tmp_path,
        monkeypatch,
        provider=RecordingMarketProvider(),
    )

    requests = [
        ("post", "/api/v1/market/snapshots"),
        ("get", "/api/v1/market/snapshots/latest"),
        ("get", "/api/v1/market/snapshots/1"),
    ]
    for method, path in requests:
        response = getattr(client, method)(path)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_missing_latest_and_detail_return_market_snapshot_not_found(
    tmp_path,
    monkeypatch,
) -> None:
    client, headers = authenticated_client(
        tmp_path,
        monkeypatch,
        provider=RecordingMarketProvider(),
    )

    latest = client.get("/api/v1/market/snapshots/latest", headers=headers)
    detail = client.get("/api/v1/market/snapshots/37", headers=headers)

    assert latest.status_code == 404
    assert latest.json() == {
        "error": {
            "code": "market_snapshot_not_found",
            "message": "market snapshot not found",
            "details": {},
        }
    }
    assert detail.status_code == 404
    assert detail.json() == {
        "error": {
            "code": "market_snapshot_not_found",
            "message": "market snapshot not found",
            "details": {"snapshot_id": 37},
        }
    }


def test_market_snapshot_detail_rejects_non_positive_ids_before_lookup(
    tmp_path,
    monkeypatch,
) -> None:
    class RecordingMarketSnapshotRepository:
        calls: list[int] = []

        def __init__(self, connection) -> None:
            pass

        def get(self, snapshot_id):
            self.calls.append(snapshot_id)
            return None

    monkeypatch.setattr(
        market_routes,
        "MarketInputSnapshotRepository",
        RecordingMarketSnapshotRepository,
    )
    client, headers = authenticated_client(
        tmp_path,
        monkeypatch,
        provider=RecordingMarketProvider(),
    )

    for snapshot_id in (0, -1):
        response = client.get(
            f"/api/v1/market/snapshots/{snapshot_id}",
            headers=headers,
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert response.json()["error"]["message"] == "request validation failed"
        assert response.json()["error"]["details"]["errors"][0]["loc"] == [
            "path",
            "snapshot_id",
        ]

    assert RecordingMarketSnapshotRepository.calls == []


def test_market_snapshot_detail_rejects_ids_above_sqlite_integer_max_before_lookup(
    tmp_path,
    monkeypatch,
) -> None:
    lookup_calls: list[int] = []
    repository_get = market_routes.MarketInputSnapshotRepository.get

    def recording_get(repository, snapshot_id):
        lookup_calls.append(snapshot_id)
        return repository_get(repository, snapshot_id)

    monkeypatch.setattr(
        market_routes.MarketInputSnapshotRepository,
        "get",
        recording_get,
    )
    client, headers = authenticated_client(
        tmp_path,
        monkeypatch,
        provider=RecordingMarketProvider(),
        raise_server_exceptions=False,
    )

    response = client.get(
        f"/api/v1/market/snapshots/{2**100}",
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"] == "request validation failed"
    assert response.json()["error"]["details"]["errors"][0]["loc"] == [
        "path",
        "snapshot_id",
    ]
    assert lookup_calls == []


def test_unsupported_provider_matches_account_snapshot_validation_error(
    tmp_path,
    monkeypatch,
) -> None:
    client, headers = authenticated_client(
        tmp_path,
        monkeypatch,
        settings_overrides={"market_provider": "bad"},
    )

    account_response = client.post("/api/v1/account/snapshots", headers=headers)
    market_response = client.post("/api/v1/market/snapshots", headers=headers)

    assert market_response.status_code == account_response.status_code == 422
    assert market_response.json() == account_response.json() == {
        "error": {
            "code": "validation_error",
            "message": "unsupported market provider",
            "details": {"market_provider": "bad"},
        }
    }


def test_provider_exception_still_creates_traceable_market_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    client, headers = authenticated_client(
        tmp_path,
        monkeypatch,
        provider=RaisingMarketProvider(),
    )
    seed_decision_universe(client, headers)

    response = client.post("/api/v1/market/snapshots", headers=headers)

    assert response.status_code == 201
    snapshot = response.json()["snapshot"]
    assert set(snapshot["quote_snapshot_refs"]) == {"000001", "600000"}
    assert any("行情数据源调用失败" in warning for warning in snapshot["warnings"])


def test_create_market_snapshot_storage_error_is_sanitized(tmp_path, monkeypatch) -> None:
    class BrokenMarketSnapshotService:
        def __init__(self, connection, provider) -> None:
            pass

        def capture(self):
            raise sqlite3.OperationalError(
                "SELECT secret trigger_secret raw_payload api_key token cookie password "
                "/home/private/market.db"
            )

    monkeypatch.setattr(
        market_routes,
        "MarketSnapshotService",
        BrokenMarketSnapshotService,
    )
    client, headers = authenticated_client(
        tmp_path,
        monkeypatch,
        provider=RecordingMarketProvider(),
    )

    response = client.post("/api/v1/market/snapshots", headers=headers)

    assert_storage_error_is_sanitized(response)


def test_create_market_snapshot_value_error_is_sanitized(tmp_path, monkeypatch) -> None:
    class BrokenMarketSnapshotService:
        def __init__(self, connection, provider) -> None:
            pass

        def capture(self):
            raise ValueError(
                "SELECT secret trigger_secret raw_payload api_key token cookie password "
                "/home/private/market.db"
            )

    monkeypatch.setattr(
        market_routes,
        "MarketSnapshotService",
        BrokenMarketSnapshotService,
    )
    client, headers = authenticated_client(
        tmp_path,
        monkeypatch,
        provider=RecordingMarketProvider(),
        raise_server_exceptions=False,
    )

    response = client.post("/api/v1/market/snapshots", headers=headers)

    assert_storage_error_is_sanitized(response)


def test_latest_market_snapshot_storage_error_is_sanitized(tmp_path, monkeypatch) -> None:
    class BrokenMarketSnapshotRepository:
        def __init__(self, connection) -> None:
            pass

        def latest(self):
            raise sqlite3.OperationalError(
                "SELECT secret trigger_secret raw_payload api_key token cookie password "
                "/home/private/market.db"
            )

    monkeypatch.setattr(
        market_routes,
        "MarketInputSnapshotRepository",
        BrokenMarketSnapshotRepository,
    )
    client, headers = authenticated_client(
        tmp_path,
        monkeypatch,
        provider=RecordingMarketProvider(),
    )

    response = client.get("/api/v1/market/snapshots/latest", headers=headers)

    assert_storage_error_is_sanitized(response)


def test_market_snapshot_detail_storage_error_is_sanitized(tmp_path, monkeypatch) -> None:
    class BrokenMarketSnapshotRepository:
        def __init__(self, connection) -> None:
            pass

        def get(self, snapshot_id):
            raise sqlite3.OperationalError(
                "SELECT secret trigger_secret raw_payload api_key token cookie password "
                "/home/private/market.db"
            )

    monkeypatch.setattr(
        market_routes,
        "MarketInputSnapshotRepository",
        BrokenMarketSnapshotRepository,
    )
    client, headers = authenticated_client(
        tmp_path,
        monkeypatch,
        provider=RecordingMarketProvider(),
    )

    response = client.get("/api/v1/market/snapshots/1", headers=headers)

    assert_storage_error_is_sanitized(response)
