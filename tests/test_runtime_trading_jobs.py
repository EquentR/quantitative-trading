from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.recommendation.scanner import PlanNotScannableError
from quantitative_trading.runtime.scheduler import SchedulerManager
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository
from quantitative_trading.storage.sqlite import connect, migrate


def test_scheduler_registers_trading_workflow_jobs() -> None:
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
        interval_seconds=17,
        timezone="Asia/Shanghai",
        job=lambda reason: calls.append(reason),
        scheduler_factory=FakeScheduler,
    )

    assert manager.start() is True

    scheduler = created_schedulers[0]
    assert scheduler.timezone == "Asia/Shanghai"
    jobs = {kwargs["id"]: (func, kwargs) for func, kwargs in scheduler.jobs}
    assert set(jobs) == {
        "decision_intraday",
        "decision_close_readiness",
        "market_minute_cleanup",
        "email_delivery_worker",
    }
    assert jobs["market_minute_cleanup"][1] == {
        "trigger": "cron",
        "hour": 16,
        "minute": 35,
        "day_of_week": "mon-fri",
        "timezone": "Asia/Shanghai",
        "id": "market_minute_cleanup",
        "max_instances": 1,
        "coalesce": True,
        "replace_existing": True,
    }
    assert jobs["email_delivery_worker"][1]["trigger"] == "interval"
    assert jobs["email_delivery_worker"][1]["seconds"] == 15

    intraday_trigger = jobs["decision_intraday"][1]["trigger"]
    assert {str(trigger.timezone) for trigger in intraday_trigger.triggers} == {
        "Asia/Shanghai"
    }
    assert {str(trigger.fields[4]) for trigger in intraday_trigger.triggers} == {
        "mon-fri"
    }
    trigger_text = str(intraday_trigger)
    assert "30-59/3" in trigger_text
    assert "0-30/3" in trigger_text

    jobs["decision_intraday"][0]()
    jobs["decision_close_readiness"][0]()
    jobs["market_minute_cleanup"][0]()
    jobs["email_delivery_worker"][0]()

    assert calls == [
        "intraday_decision",
        "close_readiness",
        "minute_cleanup",
        "email_delivery",
    ]


def test_runtime_rejects_legacy_close_plan_reason_without_writing(tmp_path) -> None:
    import quantitative_trading.runtime.service_app as service_app

    settings = Settings(
        database_path=tmp_path / "service.db", enable_market_fetch=False
    )

    with pytest.raises(ValueError, match="unknown scheduler task"):
        service_app._run_scheduler_task(
            settings,
            "close_plan_daily",
            now=datetime(2026, 7, 9, 7, 30, tzinfo=UTC),
        )

    with connect(settings) as connection:
        migrate(connection)
        plan_count = connection.execute(
            "SELECT COUNT(*) FROM trading_plans"
        ).fetchone()[0]
        universe_count = connection.execute(
            "SELECT COUNT(*) FROM universe_snapshots"
        ).fetchone()[0]
    assert plan_count == 0
    assert universe_count == 0


def test_intraday_trigger_records_no_recommendations_when_no_valid_plan(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    settings = Settings(
        database_path=tmp_path / "service.db", enable_market_fetch=False
    )
    captured_jobs = []

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

    def no_plan(connection, *, now):
        return None

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(service_app, "scan_latest_plan_recommendations", no_plan)
    monkeypatch.setattr(service_app.uvicorn, "run", lambda app, *, host, port: None)

    service_app.run_api_service(settings)
    captured_jobs[0]("intraday_trigger")

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=datetime.now(UTC),
        )

    assert state.last_status == "success"
    assert state.last_task_type == "recommendation_intraday_trigger"
    assert state.last_reason == "no_recommendations_no_valid_plan"
    assert state.last_plan_id is None
    assert state.last_recommendation_ids == []


def test_intraday_trigger_records_no_recommendations_when_plan_not_scannable(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    @dataclass(frozen=True)
    class FakePlan:
        plan_id: str = "plan-20260709"
        status: str = "expired"
        valid_until: datetime = datetime(2026, 7, 9, 15, 0, tzinfo=UTC)

    settings = Settings(
        database_path=tmp_path / "service.db", enable_market_fetch=False
    )
    captured_jobs = []

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

    def expired_plan(connection, *, now):
        raise PlanNotScannableError(FakePlan(), now=now)

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(service_app, "scan_latest_plan_recommendations", expired_plan)
    monkeypatch.setattr(service_app.uvicorn, "run", lambda app, *, host, port: None)

    service_app.run_api_service(settings)
    captured_jobs[0]("intraday_trigger")

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=datetime.now(UTC),
        )

    assert state.last_status == "success"
    assert state.last_task_type == "recommendation_intraday_trigger"
    assert state.last_reason == "no_recommendations_plan_not_scannable"
    assert state.last_plan_id == "plan-20260709"
    assert state.last_recommendation_ids == []


def test_intraday_trigger_records_generated_recommendation_ids(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    @dataclass(frozen=True)
    class FakePlan:
        plan_id: str

    @dataclass(frozen=True)
    class FakeRecommendation:
        recommendation_id: str

    @dataclass(frozen=True)
    class FakeScan:
        plan: FakePlan
        recommendations: list[FakeRecommendation]

    settings = Settings(
        database_path=tmp_path / "service.db", enable_market_fetch=False
    )
    captured_jobs = []

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

    def fake_scan(connection, *, now):
        return FakeScan(
            plan=FakePlan(plan_id="plan-20260709"),
            recommendations=[
                FakeRecommendation(recommendation_id="rec-plan-20260709-600000")
            ],
        )

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(service_app, "scan_latest_plan_recommendations", fake_scan)
    monkeypatch.setattr(service_app.uvicorn, "run", lambda app, *, host, port: None)

    service_app.run_api_service(settings)
    captured_jobs[0]("intraday_trigger")

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=datetime.now(UTC),
        )

    assert state.last_status == "success"
    assert state.last_task_type == "recommendation_intraday_trigger"
    assert state.last_reason == "recommendations_generated"
    assert state.last_plan_id == "plan-20260709"
    assert state.last_recommendation_ids == ["rec-plan-20260709-600000"]
