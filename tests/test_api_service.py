from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from fastapi.testclient import TestClient
import pytest

from quantitative_trading.api.app import create_app
from quantitative_trading.audit.repository import AuditLogRepository
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
    assert payload["overrun_count"] == 0
    assert payload["skipped_count"] == 0
    assert payload["last_status"] is None
    assert payload["last_task_type"] is None
    assert payload["last_plan_id"] is None
    assert payload["last_recommendation_ids"] == []


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

    assert run_response.status_code == 410
    assert run_response.json()["error"]["code"] == "service_run_once_retired"
    assert response.status_code == 200
    assert response.json() == {"auth_status": "configured"}


@pytest.mark.parametrize(
    "authorization", ["Basic token", "Bearer", "Bearer not-a-token"]
)
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
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    with connect(settings) as connection:
        event_types = {
            item.event_type for item in AuditLogRepository(connection).list_recent(limit=20)
        }
    assert event_types >= {"service.scheduler.started", "service.scheduler.stopped"}


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


def test_run_api_service_records_startup_intraday_workflow_result(
    tmp_path, monkeypatch
) -> None:
    import quantitative_trading.runtime.service_app as service_app

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
    workflow_calls = []
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

    def fake_uvicorn_run(app, *, host: str, port: int) -> None:
        uvicorn_calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(
        service_app, "_startup_recovery_reason", lambda now: "intraday_decision"
    )
    monkeypatch.setattr(
        service_app,
        "_run_scheduler_task",
        lambda received_settings, reason: (
            workflow_calls.append((received_settings.database_path, reason))
            or service_app.SchedulerJobResult(
                task_type="intraday",
                reason="intraday_completed",
                snapshot_id=42,
                recommendation_ids=["rec-1"],
                run_id="intraday-20260714-1000",
            )
        ),
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
    assert workflow_calls == [(settings.database_path, "intraday_decision")]
    assert state.last_status == "success"
    assert state.last_reason == "intraday_completed"
    assert state.last_task_type == "intraday"
    assert state.last_snapshot_id == 42
    assert state.last_recommendation_ids == ["rec-1"]
    assert uvicorn_calls[0]["host"] == "127.0.0.1"
    assert uvicorn_calls[0]["port"] == 8123


def test_run_api_service_alerts_and_records_scheduler_overrun(
    tmp_path, monkeypatch
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    captured_jobs = []
    alerts = []

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            captured_jobs.append(job)
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(
        database_path=tmp_path / "api.db",
        enable_market_fetch=False,
    )
    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(service_app.uvicorn, "run", lambda app, *, host, port: None)
    monkeypatch.setattr(
        service_app,
        "_dispatch_runtime_alert",
        lambda settings, **kwargs: alerts.append(kwargs),
    )

    service_app.run_api_service(settings)
    captured_jobs[0]("scheduler_overrun:decision_intraday")

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=datetime.now(UTC),
        )

    assert state.last_task_type == "scheduler"
    assert state.last_status == "skipped"
    assert state.last_reason == "scheduler_overrun:decision_intraday"
    assert alerts[0]["reason"] == "scheduler_overrun:decision_intraday"
    assert alerts[0]["event_type"] == "workflow.overrun"
    assert "already running" in alerts[0]["error"]


def test_run_api_service_alerts_when_workflow_returns_failed_status(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    captured_jobs = []
    alerts = []

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            del interval_seconds, timezone
            captured_jobs.append(job)
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(
        database_path=tmp_path / "failed-result.db",
        enable_market_fetch=False,
    )
    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(service_app.uvicorn, "run", lambda app, *, host, port: None)
    monkeypatch.setattr(
        service_app,
        "_run_scheduler_task",
        lambda settings, reason: service_app.SchedulerJobResult(
            task_type="intraday",
            reason="intraday_completed",
            status="failed",
            error="all requested quotes unavailable",
            recommendation_ids=[],
        ),
    )
    monkeypatch.setattr(
        service_app,
        "_dispatch_runtime_alert",
        lambda settings, **kwargs: alerts.append(kwargs),
    )

    service_app.run_api_service(settings)
    captured_jobs[0]("intraday_decision")

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get()
    assert state is not None
    assert state.last_status == "failed"
    assert state.last_task_type == "intraday"
    assert alerts == [
        {
            "reason": "intraday_completed",
            "error": "all requested quotes unavailable",
            "now": state.last_started_at,
            "event_type": "workflow.failed",
        }
    ]


def test_run_api_service_maps_external_workflow_concurrency_to_overrun(
    tmp_path, monkeypatch
) -> None:
    import quantitative_trading.runtime.service_app as service_app
    from quantitative_trading.decision.workflow import WorkflowAlreadyRunningError

    captured_jobs = []
    alerts = []

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            captured_jobs.append(job)
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(service_app.uvicorn, "run", lambda app, *, host, port: None)
    monkeypatch.setattr(
        service_app,
        "_run_scheduler_task",
        lambda settings, reason: (_ for _ in ()).throw(
            WorkflowAlreadyRunningError("intraday-20260714-1000")
        ),
    )
    monkeypatch.setattr(
        service_app,
        "_dispatch_runtime_alert",
        lambda settings, **kwargs: alerts.append(kwargs),
    )

    service_app.run_api_service(settings)
    captured_jobs[0]("intraday_decision")

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=datetime.now(UTC),
        )

    assert state.last_task_type == "intraday"
    assert state.last_status == "skipped"
    assert state.last_reason == "scheduler_overrun:intraday_decision"
    assert alerts[0]["event_type"] == "workflow.overrun"


def test_run_api_service_recovers_close_window_when_run_on_start_is_false(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(
        database_path=tmp_path / "api.db",
        enable_market_fetch=False,
        intraday_interval_seconds=11,
        service_run_on_start_when_scheduler_enabled=False,
    )
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)
        repository.set_enabled(
            True,
            interval_seconds=99,
            run_on_start=True,
            now=datetime.now(UTC),
        )
        repository.record_result(
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            status="success",
            reason="manual_api",
            error=None,
            snapshot_id=12,
            now=datetime.now(UTC),
        )

    recovery_calls = []
    uvicorn_calls = []

    def fake_uvicorn_run(app, *, host: str, port: int) -> None:
        uvicorn_calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(
        service_app, "_startup_recovery_reason", lambda now: "close_readiness"
    )
    monkeypatch.setattr(
        service_app,
        "_run_scheduler_task",
        lambda settings, reason: (
            recovery_calls.append(reason)
            or service_app.SchedulerJobResult(
                task_type="close",
                reason="close_reused",
                status="success",
                run_id="close-20260713",
            )
        ),
    )
    monkeypatch.setattr(service_app.uvicorn, "run", fake_uvicorn_run)

    service_app.run_api_service(settings)

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=11,
            run_on_start=False,
            now=datetime.now(UTC),
        )

    assert recovery_calls == ["close_readiness"]
    assert uvicorn_calls
    assert state.enabled is True
    assert state.interval_seconds == 11
    assert state.run_on_start is False
    assert state.last_reason == "close_reused"
    assert state.last_status == "success"


def test_run_api_service_reconciles_run_on_start_before_intraday_recovery(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(
        database_path=tmp_path / "api.db",
        enable_market_fetch=False,
        intraday_interval_seconds=13,
        service_run_on_start_when_scheduler_enabled=True,
    )
    with connect(settings) as connection:
        migrate(connection)
        SchedulerStateRepository(connection).set_enabled(
            True,
            interval_seconds=99,
            run_on_start=False,
            now=datetime.now(UTC),
        )

    workflow_calls = []
    uvicorn_calls = []

    def fake_uvicorn_run(app, *, host: str, port: int) -> None:
        uvicorn_calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(
        service_app, "_startup_recovery_reason", lambda now: "intraday_decision"
    )
    monkeypatch.setattr(
        service_app,
        "_run_scheduler_task",
        lambda received_settings, reason: (
            workflow_calls.append((received_settings.database_path, reason))
            or service_app.SchedulerJobResult(
                task_type="intraday",
                reason="intraday_completed",
                snapshot_id=24,
                recommendation_ids=[],
                run_id="intraday-20260714-1000",
            )
        ),
    )
    monkeypatch.setattr(service_app.uvicorn, "run", fake_uvicorn_run)

    service_app.run_api_service(settings)

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=13,
            run_on_start=True,
            now=datetime.now(UTC),
        )

    assert workflow_calls == [(settings.database_path, "intraday_decision")]
    assert uvicorn_calls
    assert state.enabled is True
    assert state.interval_seconds == 13
    assert state.run_on_start is True
    assert state.last_status == "success"
    assert state.last_reason == "intraday_completed"
    assert state.last_task_type == "intraday"
    assert state.last_snapshot_id == 24


def test_run_api_service_runs_startup_recovery_before_restored_scheduler(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            events.append("scheduler_start")
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    with connect(settings) as connection:
        migrate(connection)
        SchedulerStateRepository(connection).set_enabled(
            True,
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=True,
            now=datetime.now(UTC),
        )

    events = []

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(
        service_app, "_startup_recovery_reason", lambda now: "intraday_decision"
    )
    monkeypatch.setattr(
        service_app,
        "_run_scheduler_task",
        lambda settings, reason: (
            events.append("intraday_workflow")
            or service_app.SchedulerJobResult(
                task_type="intraday",
                reason="intraday_completed",
                snapshot_id=42,
                recommendation_ids=[],
            )
        ),
    )
    monkeypatch.setattr(service_app.uvicorn, "run", lambda app, *, host, port: None)

    service_app.run_api_service(settings)

    assert events == ["intraday_workflow", "scheduler_start"]


def test_run_api_service_starts_http_when_startup_result_recording_fails(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    class FailingRecordSchedulerStateRepository:
        def __init__(self, connection) -> None:
            self.repository = SchedulerStateRepository(connection)

        def get_or_create(self, **kwargs):
            return self.repository.get_or_create(**kwargs)

        def set_enabled(self, *args, **kwargs):
            return self.repository.set_enabled(*args, **kwargs)

        def record_result(self, **kwargs):
            raise sqlite3.OperationalError("database is locked: /tmp/private/api.db")

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    with connect(settings) as connection:
        migrate(connection)
        SchedulerStateRepository(connection).set_enabled(
            True,
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=True,
            now=datetime.now(UTC),
        )

    uvicorn_calls = []

    def fake_uvicorn_run(app, *, host: str, port: int) -> None:
        uvicorn_calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(
        service_app, "_startup_recovery_reason", lambda now: "intraday_decision"
    )
    monkeypatch.setattr(
        service_app,
        "SchedulerStateRepository",
        FailingRecordSchedulerStateRepository,
    )
    monkeypatch.setattr(
        service_app,
        "_run_scheduler_task",
        lambda settings, reason: service_app.SchedulerJobResult(
            task_type="intraday",
            reason="intraday_completed",
            snapshot_id=42,
            recommendation_ids=[],
        ),
    )
    monkeypatch.setattr(service_app.uvicorn, "run", fake_uvicorn_run)

    service_app.run_api_service(settings)

    assert uvicorn_calls


def test_run_api_service_starts_http_when_startup_result_get_or_create_fails(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    class FailingStartupGetOrCreateSchedulerStateRepository:
        get_or_create_calls = 0

        def __init__(self, connection) -> None:
            self.repository = SchedulerStateRepository(connection)

        def get_or_create(self, **kwargs):
            type(self).get_or_create_calls += 1
            if type(self).get_or_create_calls == 2:
                raise sqlite3.OperationalError(
                    "database is locked: /tmp/private/api.db?token=secret"
                )
            return self.repository.get_or_create(**kwargs)

        def set_enabled(self, *args, **kwargs):
            return self.repository.set_enabled(*args, **kwargs)

        def record_result(self, **kwargs):
            return self.repository.record_result(**kwargs)

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    with connect(settings) as connection:
        migrate(connection)
        SchedulerStateRepository(connection).set_enabled(
            True,
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=True,
            now=datetime.now(UTC),
        )

    uvicorn_calls = []

    def fake_uvicorn_run(app, *, host: str, port: int) -> None:
        uvicorn_calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(
        service_app, "_startup_recovery_reason", lambda now: "intraday_decision"
    )
    monkeypatch.setattr(
        service_app,
        "SchedulerStateRepository",
        FailingStartupGetOrCreateSchedulerStateRepository,
    )
    monkeypatch.setattr(
        service_app,
        "_run_scheduler_task",
        lambda settings, reason: service_app.SchedulerJobResult(
            task_type="intraday",
            reason="intraday_completed",
            snapshot_id=42,
            recommendation_ids=[],
        ),
    )
    monkeypatch.setattr(service_app.uvicorn, "run", fake_uvicorn_run)

    with caplog.at_level(logging.WARNING, logger=service_app.LOGGER.name):
        service_app.run_api_service(settings)

    assert uvicorn_calls
    assert FailingStartupGetOrCreateSchedulerStateRepository.get_or_create_calls == 2
    assert "startup scheduler result was not recorded" in caplog.text
    assert "snapshot_id=42" in caplog.text
    assert "/tmp/private" not in caplog.text
    assert "secret" not in caplog.text


def test_background_intraday_job_propagates_result_persistence_failure(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    class FailingBackgroundSchedulerStateRepository:
        fail_get_or_create = False

        def __init__(self, connection) -> None:
            self.repository = SchedulerStateRepository(connection)

        def get_or_create(self, **kwargs):
            if type(self).fail_get_or_create:
                raise sqlite3.OperationalError(
                    "database is locked: /tmp/private/api.db?token=secret"
                )
            return self.repository.get_or_create(**kwargs)

        def set_enabled(self, *args, **kwargs):
            return self.repository.set_enabled(*args, **kwargs)

        def record_result(self, **kwargs):
            return self.repository.record_result(**kwargs)

    class FakeSchedulerManager:
        def __init__(self, *, interval_seconds, timezone, job) -> None:
            captured_jobs.append(job)
            self.is_running = False
            self.next_run_time = None

        def start(self) -> bool:
            self.is_running = True
            return True

        def stop(self) -> bool:
            self.is_running = False
            return True

    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    captured_jobs = []
    uvicorn_calls = []

    def fake_uvicorn_run(app, *, host: str, port: int) -> None:
        uvicorn_calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(
        service_app,
        "SchedulerStateRepository",
        FailingBackgroundSchedulerStateRepository,
    )
    monkeypatch.setattr(
        service_app,
        "_run_scheduler_task",
        lambda settings, reason: service_app.SchedulerJobResult(
            task_type="intraday",
            reason="intraday_completed",
            snapshot_id=42,
            recommendation_ids=[],
        ),
    )
    monkeypatch.setattr(service_app.uvicorn, "run", fake_uvicorn_run)

    service_app.run_api_service(settings)

    assert uvicorn_calls
    FailingBackgroundSchedulerStateRepository.fail_get_or_create = True
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        captured_jobs[0]("intraday_decision")


@pytest.mark.parametrize(
    ("now", "expected_status", "expected_reason", "expected_error"),
    [
        (
            datetime(2026, 7, 14, 8, 25, tzinfo=UTC),
            "degraded",
            "close_not_ready",
            None,
        ),
        (
            datetime(2026, 7, 14, 8, 30, 30, tzinfo=UTC),
            "failed",
            "close_deadline_not_ready",
            "close workflow data was not ready by the 16:30 deadline",
        ),
    ],
)
def test_close_not_ready_becomes_failed_only_at_hard_deadline(
    tmp_path,
    monkeypatch,
    now: datetime,
    expected_status: str,
    expected_reason: str,
    expected_error: str | None,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    class NotReadyWorkflow:
        def run_close(self, trade_date):  # noqa: ANN001
            del trade_date

            class Result:
                ready = False
                reused = False
                market_input_snapshot_id = 17
                plan_id = None
                run_id = "close-20260714"

            return Result()

    monkeypatch.setattr(
        service_app,
        "_build_decision_workflow",
        lambda connection, settings: NotReadyWorkflow(),
    )
    settings = Settings(
        database_path=tmp_path / "close-deadline.db",
        enable_market_fetch=False,
    )

    result = service_app._run_scheduler_task(
        settings,
        "close_readiness",
        now=now,
    )

    assert result.status == expected_status
    assert result.reason == expected_reason
    assert result.error == expected_error


@pytest.mark.parametrize("reason", ["intraday", "startup", "manual_api"])
def test_legacy_snapshot_reasons_are_stably_unknown(
    tmp_path,
    monkeypatch,
    reason: str,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    legacy_calls = []
    monkeypatch.setattr(
        service_app,
        "create_and_save_account_snapshot",
        lambda settings: legacy_calls.append(settings.database_path),
        raising=False,
    )
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)

    with pytest.raises(ValueError, match="unknown scheduler task"):
        service_app._run_scheduler_task(
            settings,
            reason,
            now=datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        )

    assert legacy_calls == []
    assert service_app._task_type_for_reason(reason) == "unknown"


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


def test_run_once_is_retired_without_recording_scheduler_state(tmp_path) -> None:
    client, headers = authenticated_client(tmp_path)

    run_response = client.post("/api/v1/service/run-once", headers=headers)
    status_response = client.get("/api/v1/service/status", headers=headers)

    assert run_response.status_code == 410
    assert run_response.json()["error"]["code"] == "service_run_once_retired"
    assert run_response.json()["error"]["details"]["replacement"] == (
        "/api/v1/service/workflows/intraday/run"
    )
    assert status_response.json()["last_status"] is None
    assert status_response.json()["last_snapshot_id"] is None


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


def test_run_once_retirement_does_not_call_legacy_snapshot_factory(
    tmp_path, monkeypatch
) -> None:
    import quantitative_trading.api.routes.service as service_routes

    def failing_snapshot(settings):
        raise RuntimeError(
            "provider failed at /tmp/private/vendor.py with token=secret"
        )

    monkeypatch.setattr(
        service_routes,
        "create_and_save_account_snapshot",
        failing_snapshot,
        raising=False,
    )
    client, headers = authenticated_client(tmp_path)

    response = client.post("/api/v1/service/run-once", headers=headers)

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "service_run_once_retired"
    assert "/tmp/private" not in response.text
    assert "token=secret" not in response.text


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


def test_scheduler_start_failure_returns_uniform_error_without_enabling(
    tmp_path,
) -> None:
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


def test_scheduler_stop_failure_returns_uniform_error_without_disabling(
    tmp_path,
) -> None:
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

    assert response.status_code == 410
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "service_run_once_retired"
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
        (r"failed at C:\Users\alice\private.db", [r"C:\Users\alice\private.db"]),
        (r"failed at \\server\share\private.db", [r"\\server\share\private.db"]),
    ],
)
def test_safe_error_summary_redacts_sensitive_values(raw, forbidden) -> None:
    from quantitative_trading.api.routes.service import _safe_error_summary

    summary = _safe_error_summary(RuntimeError(raw))

    assert len(summary) <= 300
    for value in forbidden:
        assert value not in summary
