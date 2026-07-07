from datetime import UTC, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.runtime.scheduler import SchedulerManager
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 7, 2, 0, tzinfo=UTC)


def test_scheduler_state_defaults_to_disabled(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "scheduler.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)

        state = repository.get_or_create(
            interval_seconds=180,
            run_on_start=True,
            now=NOW,
        )

    assert state.enabled is False
    assert state.interval_seconds == 180
    assert state.run_on_start is True
    assert state.last_status is None


def test_scheduler_state_persists_enabled_and_last_result(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "scheduler.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)
        repository.set_enabled(True, interval_seconds=7, run_on_start=False, now=NOW)
        repository.record_result(
            started_at=NOW,
            finished_at=NOW,
            status="success",
            reason="manual_api",
            error=None,
            snapshot_id=3,
            now=NOW,
        )
        state = repository.get_or_create(
            interval_seconds=180,
            run_on_start=True,
            now=NOW,
        )

    assert state.enabled is True
    assert state.interval_seconds == 7
    assert state.run_on_start is False
    assert state.last_started_at == NOW
    assert state.last_finished_at == NOW
    assert state.last_status == "success"
    assert state.last_reason == "manual_api"
    assert state.last_error is None
    assert state.last_snapshot_id == 3


def test_scheduler_state_record_result_creates_default_state_when_missing(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "scheduler.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)

        state = repository.record_result(
            started_at=NOW,
            finished_at=NOW,
            status="failed",
            reason="manual_api",
            error="snapshot failed",
            snapshot_id=None,
            now=NOW,
        )

    assert state.enabled is False
    assert state.interval_seconds == 180
    assert state.run_on_start is False
    assert state.last_status == "failed"
    assert state.last_error == "snapshot failed"


def test_scheduler_state_rejects_naive_update_time(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "scheduler.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)

        with pytest.raises(ValueError, match="timezone-aware"):
            repository.set_enabled(
                True,
                interval_seconds=180,
                run_on_start=True,
                now=datetime(2026, 7, 7, 2, 0),
            )


def test_scheduler_manager_start_and_stop_are_idempotent(tmp_path) -> None:
    calls: list[str] = []
    created_schedulers = []

    class FakeScheduler:
        def __init__(self, *, timezone: str) -> None:
            self.timezone = timezone
            self.jobs = []
            self.running = False
            created_schedulers.append(self)

        def add_job(self, func, **kwargs) -> None:
            self.jobs.append((func, kwargs))

        def start(self) -> None:
            self.running = True

        def shutdown(self, wait: bool = False) -> None:
            self.running = False

    manager = SchedulerManager(
        interval_seconds=7,
        timezone="Asia/Shanghai",
        job=lambda reason: calls.append(reason),
        scheduler_factory=FakeScheduler,
    )

    first = manager.start()
    second = manager.start()
    scheduler = created_schedulers[0]
    job_func, job_kwargs = scheduler.jobs[0]
    job_func()
    manager.stop()
    manager.stop()

    assert first is True
    assert second is False
    assert manager.is_running is False
    assert calls == ["intraday"]
    assert job_kwargs == {
        "trigger": "interval",
        "seconds": 7,
        "id": "account_snapshot_intraday",
        "max_instances": 1,
        "replace_existing": True,
    }
