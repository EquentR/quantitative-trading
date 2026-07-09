from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from quantitative_trading.config import Settings
from quantitative_trading.recommendation.scanner import PlanNotScannableError
from quantitative_trading.runtime.scheduler import SchedulerManager
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository
from quantitative_trading.storage.sqlite import connect


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
        "account_snapshot_intraday",
        "close_plan_daily",
        "recommendation_intraday_trigger",
    }
    assert jobs["account_snapshot_intraday"][1]["trigger"] == "interval"
    assert jobs["account_snapshot_intraday"][1]["seconds"] == 17
    assert jobs["close_plan_daily"][1] == {
        "trigger": "cron",
        "hour": 15,
        "minute": 30,
        "day_of_week": "mon-fri",
        "id": "close_plan_daily",
        "max_instances": 1,
        "replace_existing": True,
    }

    intraday_trigger = jobs["recommendation_intraday_trigger"][1]["trigger"]
    assert {
        str(trigger.timezone)
        for trigger in intraday_trigger.triggers
    } == {"Asia/Shanghai"}
    assert {
        str(trigger.fields[4])
        for trigger in intraday_trigger.triggers
    } == {"mon-fri"}
    trigger_text = str(intraday_trigger)
    assert "35-59" in trigger_text
    assert "0-30" in trigger_text
    assert "0-55" in trigger_text

    jobs["account_snapshot_intraday"][0]()
    jobs["close_plan_daily"][0]()
    jobs["recommendation_intraday_trigger"][0]()

    assert calls == ["intraday", "close_plan_daily", "intraday_trigger"]


def test_intraday_trigger_records_holding_risk_only_when_no_valid_plan(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    settings = Settings(database_path=tmp_path / "service.db", enable_market_fetch=False)
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
    assert state.last_reason == "holding_risk_only_no_valid_plan"
    assert state.last_plan_id is None
    assert state.last_recommendation_ids == []


def test_intraday_trigger_records_holding_risk_only_when_plan_not_scannable(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    @dataclass(frozen=True)
    class FakePlan:
        plan_id: str = "plan-20260709"
        status: str = "expired"
        valid_until: datetime = datetime(2026, 7, 9, 15, 0, tzinfo=UTC)

    settings = Settings(database_path=tmp_path / "service.db", enable_market_fetch=False)
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
    assert state.last_reason == "holding_risk_only_plan_not_scannable"
    assert state.last_plan_id == "plan-20260709"
    assert state.last_recommendation_ids == []


def test_close_plan_daily_records_generated_plan(tmp_path, monkeypatch) -> None:
    import quantitative_trading.runtime.service_app as service_app

    @dataclass(frozen=True)
    class FakeCreatedPlan:
        plan_id: str

    settings = Settings(database_path=tmp_path / "service.db", enable_market_fetch=False)
    captured_jobs = []
    generated_for: list[date] = []

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

    def fake_generate(connection, *, trading_day, now, timezone):
        generated_for.append(trading_day)
        return FakeCreatedPlan(plan_id=f"plan-{trading_day:%Y%m%d}")

    monkeypatch.setattr(service_app, "SchedulerManager", FakeSchedulerManager)
    monkeypatch.setattr(service_app, "generate_trading_plan", fake_generate)
    monkeypatch.setattr(service_app.uvicorn, "run", lambda app, *, host, port: None)

    service_app.run_api_service(settings)
    captured_jobs[0]("close_plan_daily")

    with connect(settings) as connection:
        state = SchedulerStateRepository(connection).get_or_create(
            interval_seconds=settings.intraday_interval_seconds,
            run_on_start=settings.service_run_on_start_when_scheduler_enabled,
            now=datetime.now(UTC),
        )

    assert generated_for
    assert state.last_status == "success"
    assert state.last_task_type == "close_plan_daily"
    assert state.last_reason == "close_plan_generated"
    assert state.last_plan_id == f"plan-{generated_for[0]:%Y%m%d}"
