from datetime import UTC, datetime
from types import SimpleNamespace

from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
import pytest

from quantitative_trading.config import Settings
from quantitative_trading.runtime.scheduler import (
    SchedulerManager,
    _close_readiness_trigger,
    _intraday_trigger,
)
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


def test_scheduler_state_ignores_malformed_recommendation_ids_json(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "scheduler.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)
        repository.record_result(
            started_at=NOW,
            finished_at=NOW,
            status="success",
            reason="intraday_trigger",
            error=None,
            snapshot_id=None,
            task_type="recommendation_intraday_trigger",
            plan_id="plan-20260709",
            recommendation_ids=["rec-1"],
            now=NOW,
        )
        connection.execute(
            """
            UPDATE scheduler_state
            SET last_recommendation_ids = ?
            WHERE id = 1
            """,
            ("not-json",),
        )
        connection.commit()

        state = repository.get_or_create(
            interval_seconds=180,
            run_on_start=True,
            now=NOW,
        )

    assert state.last_recommendation_ids == []


def test_scheduler_state_ignores_non_list_recommendation_ids_json(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "scheduler.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)
        repository.record_result(
            started_at=NOW,
            finished_at=NOW,
            status="success",
            reason="intraday_trigger",
            error=None,
            snapshot_id=None,
            task_type="recommendation_intraday_trigger",
            plan_id="plan-20260709",
            recommendation_ids=["rec-1"],
            now=NOW,
        )
        connection.execute(
            """
            UPDATE scheduler_state
            SET last_recommendation_ids = ?
            WHERE id = 1
            """,
            ('{"id": "rec-1"}',),
        )
        connection.commit()

        state = repository.get_or_create(
            interval_seconds=180,
            run_on_start=True,
            now=NOW,
        )

    assert state.last_recommendation_ids == []


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
            self.listeners = []
            self.running = False
            created_schedulers.append(self)

        def add_job(self, func, **kwargs) -> None:
            self.jobs.append((func, kwargs))

        def add_listener(self, callback, mask) -> None:
            self.listeners.append((callback, mask))

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
    assert calls == ["intraday_decision"]
    assert len(scheduler.jobs) == 4
    assert job_kwargs["id"] == "decision_intraday"
    assert str(job_kwargs["trigger"]) == str(_intraday_trigger("Asia/Shanghai"))
    assert job_kwargs["max_instances"] == 1
    assert job_kwargs["coalesce"] is True
    assert job_kwargs["replace_existing"] is True

    job_ids = [kwargs["id"] for _, kwargs in scheduler.jobs]
    assert job_ids == [
        "decision_intraday",
        "decision_close_readiness",
        "market_minute_cleanup",
        "email_delivery_worker",
    ]

    close_job = scheduler.jobs[1]
    cleanup_job = scheduler.jobs[2]
    email_job = scheduler.jobs[3]

    assert str(close_job[1]["trigger"]) == str(
        _close_readiness_trigger("Asia/Shanghai")
    )
    assert cleanup_job[1]["trigger"] == "cron"
    assert cleanup_job[1]["hour"] == 16
    assert cleanup_job[1]["minute"] == 35
    assert email_job[1]["trigger"] == "interval"
    assert email_job[1]["seconds"] == 15

    for func, _ in scheduler.jobs[1:]:
        func()
    listener, mask = scheduler.listeners[0]
    listener(
        SimpleNamespace(
            code=EVENT_JOB_MAX_INSTANCES,
            job_id="decision_intraday",
        )
    )
    listener(
        SimpleNamespace(
            code=EVENT_JOB_MISSED,
            job_id="decision_close_readiness",
        )
    )
    assert mask == EVENT_JOB_MAX_INSTANCES | EVENT_JOB_MISSED
    assert calls == [
        "intraday_decision",
        "close_readiness",
        "minute_cleanup",
        "email_delivery",
        "scheduler_overrun:decision_intraday",
        "scheduler_missed:decision_close_readiness",
    ]


def test_intraday_trigger_uses_three_minute_a_share_sessions() -> None:
    trigger = _intraday_trigger("Asia/Shanghai")
    rendered = [str(item) for item in trigger.triggers]

    assert any("hour='9'" in item and "minute='30-59/3'" in item for item in rendered)
    assert any("hour='10'" in item and "minute='0-59/3'" in item for item in rendered)
    assert any("hour='11'" in item and "minute='0-30/3'" in item for item in rendered)
    assert any("hour='13-14'" in item and "minute='0-59/3'" in item for item in rendered)
    assert any("hour='15'" in item and "minute='0'" in item for item in rendered)


def test_close_readiness_trigger_checks_every_five_minutes_until_1630() -> None:
    trigger = _close_readiness_trigger("Asia/Shanghai")
    rendered = [str(item) for item in trigger.triggers]

    assert any("hour='15'" in item and "minute='15-59/5'" in item for item in rendered)
    assert any("hour='16'" in item and "minute='0-30/5'" in item for item in rendered)
