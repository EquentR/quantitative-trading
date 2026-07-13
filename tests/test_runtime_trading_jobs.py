from __future__ import annotations

from datetime import UTC, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.runtime.scheduler import SchedulerManager
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


@pytest.mark.parametrize("reason", ["close_plan_daily", "intraday_trigger"])
def test_runtime_rejects_legacy_scheduler_reasons_without_writing(
    tmp_path, reason: str
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    settings = Settings(
        database_path=tmp_path / "service.db", enable_market_fetch=False
    )

    with pytest.raises(ValueError, match="unknown scheduler task"):
        service_app._run_scheduler_task(
            settings,
            reason,
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
