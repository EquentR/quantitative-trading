from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
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
                lambda: self._job("intraday_decision"),
                trigger=_intraday_trigger(self._timezone),
                id="decision_intraday",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            scheduler.add_job(
                lambda: self._job("close_readiness"),
                trigger=_close_readiness_trigger(self._timezone),
                id="decision_close_readiness",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            scheduler.add_job(
                lambda: self._job("minute_cleanup"),
                trigger="cron",
                day_of_week="mon-fri",
                hour=16,
                minute=35,
                timezone=self._timezone,
                id="market_minute_cleanup",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            scheduler.add_job(
                lambda: self._job("email_delivery"),
                trigger="interval",
                seconds=15,
                id="email_delivery_worker",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            if hasattr(scheduler, "add_listener"):
                scheduler.add_listener(
                    self._scheduler_skip_listener,
                    EVENT_JOB_MAX_INSTANCES | EVENT_JOB_MISSED,
                )
            scheduler.start()
            self._scheduler = scheduler
            return True

    def _scheduler_skip_listener(self, event: Any) -> None:
        event_type = (
            "overrun"
            if event.code == EVENT_JOB_MAX_INSTANCES
            else "missed"
        )
        self._job(f"scheduler_{event_type}:{event.job_id}")

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
                minute="30-59/3",
                timezone=timezone,
            ),
            CronTrigger(
                day_of_week="mon-fri",
                hour=10,
                minute="0-59/3",
                timezone=timezone,
            ),
            CronTrigger(
                day_of_week="mon-fri",
                hour=11,
                minute="0-30/3",
                timezone=timezone,
            ),
            CronTrigger(
                day_of_week="mon-fri",
                hour="13-14",
                minute="0-59/3",
                timezone=timezone,
            ),
            CronTrigger(
                day_of_week="mon-fri",
                hour=15,
                minute=0,
                timezone=timezone,
            ),
        ]
    )


def _close_readiness_trigger(timezone: str) -> OrTrigger:
    return OrTrigger(
        [
            CronTrigger(
                day_of_week="mon-fri",
                hour=15,
                minute="15-59/5",
                timezone=timezone,
            ),
            CronTrigger(
                day_of_week="mon-fri",
                hour=16,
                minute="0-30/5",
                timezone=timezone,
            ),
        ]
    )
