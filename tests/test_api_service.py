from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi.testclient import TestClient
import pytest

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository
from quantitative_trading.storage.sqlite import connect, migrate


def authenticated_client(
    tmp_path,
    *,
    scheduler: object | None = None,
    raise_server_exceptions: bool = True,
) -> tuple[TestClient, dict[str, str]]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(
        create_app(settings, scheduler=scheduler),
        raise_server_exceptions=raise_server_exceptions,
    )
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    return client, {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_service_status_reports_scheduler_state(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    response = client.get("/api/v1/service/status", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["auth_status"] == "configured"
    assert payload["scheduler_enabled"] is False
    assert payload["scheduler_running"] is False
    assert payload["interval_seconds"] == 180
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["run_on_start"] is True
    assert payload["last_status"] is None


def test_setup_required_public_status_returns_only_auth_status(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/service/status")

    assert response.status_code == 200
    assert response.json() == {"auth_status": "setup_required"}


def test_configured_public_status_hides_scheduler_and_last_error(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.api.routes.service as service_routes

    def failing_snapshot(settings):
        raise RuntimeError("provider failed with token=secret")

    monkeypatch.setattr(
        service_routes,
        "create_and_save_account_snapshot",
        failing_snapshot,
        raising=False,
    )
    client, headers = authenticated_client(tmp_path)
    run_response = client.post("/api/v1/service/run-once", headers=headers)

    response = client.get("/api/v1/service/status")

    assert run_response.status_code == 200
    assert run_response.json()["last_error"]
    assert response.status_code == 200
    assert response.json() == {"auth_status": "configured"}


@pytest.mark.parametrize("authorization", ["Basic token", "Bearer", "Bearer not-a-token"])
def test_status_rejects_invalid_authorization_header(
    tmp_path,
    authorization: str,
) -> None:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})

    response = client.get(
        "/api/v1/service/status",
        headers={"Authorization": authorization},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert "not-a-token" not in response.text


def test_scheduler_start_and_stop_persist_state(tmp_path) -> None:
    class FakeScheduler:
        is_running = False
        next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    client, headers = authenticated_client(tmp_path, scheduler=FakeScheduler())

    start_response = client.post("/api/v1/service/scheduler/start", headers=headers)
    started_status = client.get("/api/v1/service/status", headers=headers)
    stop_response = client.post("/api/v1/service/scheduler/stop", headers=headers)

    assert start_response.status_code == 200
    assert start_response.json()["scheduler_enabled"] is True
    assert started_status.json()["scheduler_enabled"] is True
    assert stop_response.status_code == 200
    assert stop_response.json()["scheduler_enabled"] is False


def test_create_app_restores_enabled_scheduler(tmp_path) -> None:
    starts = []

    class FakeScheduler:
        def __init__(self) -> None:
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            starts.append("started")
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings, scheduler=FakeScheduler()))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    client.post("/api/v1/service/scheduler/start", headers=headers)

    restored_client = TestClient(
        create_app(settings, scheduler=FakeScheduler(), restore_scheduler=True)
    )
    status = restored_client.get("/api/v1/service/status", headers=headers)

    assert starts == ["started", "started"]
    assert status.json()["scheduler_enabled"] is True
    assert status.json()["scheduler_running"] is True


def test_run_api_service_records_startup_scheduler_result(tmp_path, monkeypatch) -> None:
    import quantitative_trading.runtime.service_app as service_app

    class CreatedSnapshot:
        snapshot_id = 42

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            schedulers.append(
                {
                    "interval_seconds": interval_seconds,
                    "timezone": timezone,
                    "job": job,
                }
            )
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    schedulers = []
    snapshot_calls = []
    uvicorn_calls = []
    settings = Settings(
        database_path=tmp_path / "api.db",
        enable_market_fetch=False,
        intraday_interval_seconds=7,
        timezone="Asia/Shanghai",
        api_host="127.0.0.1",
        api_port=8123,
    )
    with connect(settings) as connection:
        migrate(connection)
        SchedulerStateRepository(connection).set_enabled(
            True,
            interval_seconds=7,
            run_on_start=True,
            now=datetime.now(UTC),
        )

    def fake_create_and_save_account_snapshot(received_settings):
        snapshot_calls.append(received_settings.database_path)
        return CreatedSnapshot()

    def fake_uvicorn_run(app, *, host: str, port: int) -> None:
        uvicorn_calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(
        service_app,
        "create_and_save_account_snapshot",
        fake_create_and_save_account_snapshot,
    )
    monkeypatch.setattr(service_app.uvicorn, "run", fake_uvicorn_run)

    service_app.run_api_service(settings)

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=7,
            run_on_start=True,
            now=datetime.now(UTC),
        )

    assert schedulers[0]["interval_seconds"] == 7
    assert schedulers[0]["timezone"] == "Asia/Shanghai"
    assert snapshot_calls == [settings.database_path]
    assert state.last_status == "success"
    assert state.last_reason == "startup"
    assert state.last_snapshot_id == 42
    assert uvicorn_calls[0]["host"] == "127.0.0.1"
    assert uvicorn_calls[0]["port"] == 8123


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("/api/v1/service/scheduler/start", "scheduler_enabled"),
        ("/api/v1/service/scheduler/stop", "scheduler_enabled"),
    ],
)
def test_scheduler_control_without_live_scheduler_requires_auth_and_returns_error(
    tmp_path,
    path: str,
    field: str,
) -> None:
    client, headers = authenticated_client(tmp_path, raise_server_exceptions=False)

    unauthenticated_response = client.post(path)
    response = client.post(path, headers=headers)
    status_response = client.get("/api/v1/service/status", headers=headers)

    assert unauthenticated_response.status_code == 401
    assert unauthenticated_response.json()["error"]["code"] == "unauthorized"
    assert response.status_code != 200
    assert response.json()["error"]["code"] == "scheduler_error"
    assert status_response.json()[field] is False


def test_run_once_records_latest_result(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    run_response = client.post("/api/v1/service/run-once", headers=headers)
    status_response = client.get("/api/v1/service/status", headers=headers)

    assert run_response.status_code == 200
    assert run_response.json()["last_status"] == "success"
    assert run_response.json()["last_reason"] == "manual_api"
    assert status_response.json()["last_status"] == "success"
    assert status_response.json()["last_reason"] == "manual_api"
    assert status_response.json()["last_snapshot_id"] == 1


def test_service_control_endpoints_require_auth_after_setup(tmp_path) -> None:
    client, _headers = authenticated_client(tmp_path)

    requests = [
        ("post", "/api/v1/service/scheduler/start"),
        ("post", "/api/v1/service/scheduler/stop"),
        ("post", "/api/v1/service/run-once"),
    ]

    for method, path in requests:
        response = getattr(client, method)(path)

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_run_once_failure_records_failed_state_without_500(tmp_path, monkeypatch) -> None:
    import quantitative_trading.api.routes.service as service_routes

    def failing_snapshot(settings):
        raise RuntimeError("provider failed at /tmp/private/vendor.py with token=secret")

    monkeypatch.setattr(
        service_routes,
        "create_and_save_account_snapshot",
        failing_snapshot,
        raising=False,
    )
    client, headers = authenticated_client(tmp_path)

    response = client.post("/api/v1/service/run-once", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["last_status"] == "failed"
    assert payload["last_reason"] == "manual_api"
    assert payload["last_error"]
    assert "/tmp/private" not in payload["last_error"]
    assert "token=secret" not in payload["last_error"]


def test_scheduler_start_and_stop_call_injected_scheduler(tmp_path) -> None:
    class FakeScheduler:
        def __init__(self) -> None:
            self.started = 0
            self.stopped = 0
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.started += 1
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.stopped += 1
            self.is_running = False
            return True

    scheduler = FakeScheduler()
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings, scheduler=scheduler))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    start_response = client.post("/api/v1/service/scheduler/start", headers=headers)
    stop_response = client.post("/api/v1/service/scheduler/stop", headers=headers)

    assert start_response.status_code == 200
    assert stop_response.status_code == 200
    assert scheduler.started == 1
    assert scheduler.stopped == 1


def test_scheduler_start_failure_returns_uniform_error_without_enabling(tmp_path) -> None:
    class FailingStartScheduler:
        is_running = False
        next_run_time = None

        def start(self) -> bool:
            raise RuntimeError("scheduler backend failed at /tmp/private/scheduler.py")

        def stop(self) -> bool:
            return True

    client, headers = authenticated_client(
        tmp_path,
        scheduler=FailingStartScheduler(),
        raise_server_exceptions=False,
    )

    response = client.post("/api/v1/service/scheduler/start", headers=headers)
    status_response = client.get("/api/v1/service/status", headers=headers)

    assert status_response.json()["scheduler_enabled"] is False
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "scheduler_error"
    assert response.json()["error"]["message"] == "scheduler control failed"
    assert "/tmp/private" not in response.text


def test_scheduler_stop_failure_returns_uniform_error_without_disabling(tmp_path) -> None:
    class FailingStopScheduler:
        def __init__(self) -> None:
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            raise RuntimeError("scheduler backend failed at /tmp/private/scheduler.py")

    client, headers = authenticated_client(
        tmp_path,
        scheduler=FailingStopScheduler(),
        raise_server_exceptions=False,
    )
    client.post("/api/v1/service/scheduler/start", headers=headers)

    response = client.post("/api/v1/service/scheduler/stop", headers=headers)
    status_response = client.get("/api/v1/service/status", headers=headers)

    assert status_response.json()["scheduler_enabled"] is True
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "scheduler_error"
    assert response.json()["error"]["message"] == "scheduler control failed"
    assert "/tmp/private" not in response.text


def test_scheduler_start_persistence_failure_rolls_back_live_start(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.api.routes.service as service_routes

    class RecordingScheduler:
        def __init__(self) -> None:
            self.starts = 0
            self.stops = 0
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.starts += 1
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.stops += 1
            self.is_running = False
            return True

    def fail_set_enabled(container, *, enabled: bool) -> None:
        raise sqlite3.OperationalError("database is locked: /tmp/private/api.db")

    scheduler = RecordingScheduler()
    monkeypatch.setattr(service_routes, "_set_scheduler_enabled", fail_set_enabled)
    client, headers = authenticated_client(
        tmp_path,
        scheduler=scheduler,
        raise_server_exceptions=False,
    )

    response = client.post("/api/v1/service/scheduler/start", headers=headers)
    status_response = client.get("/api/v1/service/status", headers=headers)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == "service state storage failed"
    assert "/tmp/private" not in response.text
    assert scheduler.starts == 1
    assert scheduler.stops == 1
    assert scheduler.is_running is False
    assert status_response.json()["scheduler_enabled"] is False


def test_scheduler_stop_persistence_failure_restores_live_scheduler(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.api.routes.service as service_routes

    class RecordingScheduler:
        def __init__(self) -> None:
            self.starts = 0
            self.stops = 0
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.starts += 1
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.stops += 1
            self.is_running = False
            return True

    def fail_set_enabled(container, *, enabled: bool) -> None:
        raise sqlite3.OperationalError("database is locked: /tmp/private/api.db")

    scheduler = RecordingScheduler()
    client, headers = authenticated_client(
        tmp_path,
        scheduler=scheduler,
        raise_server_exceptions=False,
    )
    client.post("/api/v1/service/scheduler/start", headers=headers)
    monkeypatch.setattr(service_routes, "_set_scheduler_enabled", fail_set_enabled)

    response = client.post("/api/v1/service/scheduler/stop", headers=headers)
    status_response = client.get("/api/v1/service/status", headers=headers)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == "service state storage failed"
    assert "/tmp/private" not in response.text
    assert scheduler.starts == 2
    assert scheduler.stops == 1
    assert scheduler.is_running is True
    assert status_response.json()["scheduler_enabled"] is True


def test_run_once_state_record_failure_returns_uniform_internal_error(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.api.routes.service as service_routes

    class FailingSchedulerStateRepository:
        def __init__(self, connection) -> None:
            pass

        def get_or_create(self, **kwargs):
            return None

        def record_result(self, **kwargs):
            raise sqlite3.OperationalError("database is locked: /tmp/private/api.db")

    monkeypatch.setattr(
        service_routes,
        "SchedulerStateRepository",
        FailingSchedulerStateRepository,
    )
    client, headers = authenticated_client(tmp_path, raise_server_exceptions=False)

    response = client.post("/api/v1/service/run-once", headers=headers)

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == "service state storage failed"
    assert "/tmp/private" not in response.text


@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("token=abc123", ["abc123"]),
        ("password: hunter2", ["hunter2"]),
        ("secret open-sesame", ["open-sesame"]),
        ("api_key=key-123", ["key-123"]),
        ("cookie: session-123", ["session-123"]),
        ("Authorization: Bearer bearer-token-123", ["bearer-token-123"]),
        ("request failed ?token=query-token&x=1", ["query-token"]),
        ("request failed &api_key=query-key&x=1", ["query-key"]),
        ("access_token=access-123", ["access-123"]),
        ("refresh_token: refresh-123", ["refresh-123"]),
        ("token_secret secret-123", ["secret-123"]),
        ("password_hash=hash-123", ["hash-123"]),
        ("request failed ?access_token=query-access&x=1", ["query-access"]),
        ("request failed &refresh_token=query-refresh&x=1", ["query-refresh"]),
        ("api-key=hyphen-key-123", ["hyphen-key-123"]),
        ("https://user:pass@example.com/path", ["user:pass"]),
        ("failed at /tmp/private/vendor.py", ["/tmp/private/vendor.py"]),
    ],
)
def test_safe_error_summary_redacts_sensitive_values(raw, forbidden) -> None:
    from quantitative_trading.api.routes.service import _safe_error_summary

    summary = _safe_error_summary(RuntimeError(raw))

    assert len(summary) <= 300
    for value in forbidden:
        assert value not in summary
