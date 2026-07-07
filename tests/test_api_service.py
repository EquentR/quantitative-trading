from __future__ import annotations

from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.config import Settings


def authenticated_client(tmp_path) -> tuple[TestClient, dict[str, str]]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))
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


def test_scheduler_start_and_stop_persist_state(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    start_response = client.post("/api/v1/service/scheduler/start", headers=headers)
    started_status = client.get("/api/v1/service/status", headers=headers)
    stop_response = client.post("/api/v1/service/scheduler/stop", headers=headers)

    assert start_response.status_code == 200
    assert start_response.json()["scheduler_enabled"] is True
    assert started_status.json()["scheduler_enabled"] is True
    assert stop_response.status_code == 200
    assert stop_response.json()["scheduler_enabled"] is False


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
