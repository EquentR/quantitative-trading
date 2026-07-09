from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger


class SchedulerManager:
    def __init__(
        self,
        *,
        interval_seconds: int,
        timezone: str,
        job: Callable[[str], object],
        scheduler_factory: Callable[..., Any] = BackgroundScheduler,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._timezone = timezone
        self._job = job
        self._scheduler_factory = scheduler_factory
        self._scheduler: Any | None = None
        self._lock = Lock()

    @property
    def is_running(self) -> bool:
        scheduler = self._scheduler
        if scheduler is None:
            return False
        return bool(getattr(scheduler, "running", True))

    @property
    def next_run_time(self) -> object | None:
        scheduler = self._scheduler
        if scheduler is None:
            return None
        try:
            jobs = scheduler.get_jobs()
        except AttributeError:
            jobs = getattr(scheduler, "jobs", [])
        if not jobs:
            return None
        first_job = jobs[0]
        return getattr(first_job, "next_run_time", None)

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return False

            scheduler = self._scheduler_factory(timezone=self._timezone)
            scheduler.add_job(
                # 调度器只传递触发原因，实际快照逻辑由注入的共享任务负责。
                lambda: self._job("intraday"),
                trigger="interval",
                seconds=self._interval_seconds,
                id="account_snapshot_intraday",
                max_instances=1,
                replace_existing=True,
            )
            scheduler.add_job(
                lambda: self._job("close_plan_daily"),
                trigger="cron",
                day_of_week="mon-fri",
                hour=15,
                minute=30,
                id="close_plan_daily",
                max_instances=1,
                replace_existing=True,
            )
            scheduler.add_job(
                lambda: self._job("intraday_trigger"),
                trigger=_intraday_trigger(self._timezone),
                id="recommendation_intraday_trigger",
                max_instances=1,
                replace_existing=True,
            )
            scheduler.start()
            self._scheduler = scheduler
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.is_running:
                self._scheduler = None
                return False

            scheduler = self._scheduler
            scheduler.shutdown(wait=False)
            self._scheduler = None
            return True


def _intraday_trigger(timezone: str) -> OrTrigger:
    return OrTrigger(
        [
            CronTrigger(
                day_of_week="mon-fri",
                hour=9,
                minute="35-59",
                timezone=timezone,
            ),
            CronTrigger(
                day_of_week="mon-fri",
                hour=10,
                minute="*",
                timezone=timezone,
            ),
            CronTrigger(
                day_of_week="mon-fri",
                hour=11,
                minute="0-30",
                timezone=timezone,
            ),
            CronTrigger(
                day_of_week="mon-fri",
                hour=13,
                minute="*",
                timezone=timezone,
            ),
            CronTrigger(
                day_of_week="mon-fri",
                hour=14,
                minute="0-55",
                timezone=timezone,
            ),
        ]
    )
