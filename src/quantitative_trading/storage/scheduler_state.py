from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime


DEFAULT_SCHEDULER_INTERVAL_SECONDS = 180


@dataclass(frozen=True)
class SchedulerState:
    enabled: bool
    interval_seconds: int
    run_on_start: bool
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_status: str | None
    last_reason: str | None
    last_error: str | None
    last_snapshot_id: int | None
    last_task_type: str | None
    last_plan_id: str | None
    last_recommendation_ids: list[str]
    overrun_count: int
    skipped_count: int
    updated_at: datetime


class SchedulerStateRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_or_create(
        self,
        *,
        interval_seconds: int,
        run_on_start: bool,
        now: datetime,
    ) -> SchedulerState:
        _require_timezone_aware(now)
        row = self._fetch()
        if row is None:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO scheduler_state (
                      id,
                      enabled,
                      interval_seconds,
                      run_on_start,
                      updated_at
                    ) VALUES (
                      1,
                      0,
                      ?,
                      ?,
                      ?
                    )
                    """,
                    (interval_seconds, self._to_int(run_on_start), now.isoformat()),
                )
            row = self._fetch()
        if row is None:
            raise RuntimeError("scheduler state was not initialized")
        return self._from_row(row)

    def set_enabled(
        self,
        enabled: bool,
        *,
        interval_seconds: int,
        run_on_start: bool,
        now: datetime,
    ) -> SchedulerState:
        _require_timezone_aware(now)
        self.get_or_create(
            interval_seconds=interval_seconds,
            run_on_start=run_on_start,
            now=now,
        )
        # 启停调度只修改运行配置，保留最近一次执行结果用于 API 展示和恢复。
        # interval/run_on_start 来自当前配置，和 enabled 一起持久化为下次启动的期望状态。
        with self.connection:
            self.connection.execute(
                """
                UPDATE scheduler_state
                SET
                  enabled = ?,
                  interval_seconds = ?,
                  run_on_start = ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (
                    self._to_int(enabled),
                    interval_seconds,
                    self._to_int(run_on_start),
                    now.isoformat(),
                ),
            )
        return self._current()

    def record_result(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        status: str,
        reason: str,
        error: str | None,
        snapshot_id: int | None,
        task_type: str | None = None,
        plan_id: str | None = None,
        recommendation_ids: list[str] | None = None,
        now: datetime,
    ) -> SchedulerState:
        _require_timezone_aware(started_at)
        _require_timezone_aware(finished_at)
        _require_timezone_aware(now)
        if self._fetch() is None:
            # 极端情况下先记录结果再初始化调度配置，使用项目默认轮询间隔兜底。
            self.get_or_create(
                interval_seconds=DEFAULT_SCHEDULER_INTERVAL_SECONDS,
                run_on_start=False,
                now=now,
            )

        with self.connection:
            self.connection.execute(
                """
                UPDATE scheduler_state
                SET
                  last_started_at = ?,
                  last_finished_at = ?,
                  last_status = ?,
                  last_reason = ?,
                  last_error = ?,
                  last_snapshot_id = ?,
                  last_task_type = ?,
                  last_plan_id = ?,
                  last_recommendation_ids = ?,
                  overrun_count = overrun_count + ?,
                  skipped_count = skipped_count + ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (
                    started_at.isoformat(),
                    finished_at.isoformat(),
                    status,
                    reason,
                    error,
                    snapshot_id,
                    task_type,
                    plan_id,
                    json.dumps(recommendation_ids or [], ensure_ascii=False),
                    int(reason.startswith("scheduler_overrun:")),
                    int(status == "skipped"),
                    now.isoformat(),
                ),
            )
        return self._current()

    def _current(self) -> SchedulerState:
        row = self._fetch()
        if row is None:
            raise RuntimeError("scheduler state was not initialized")
        return self._from_row(row)

    def _fetch(self) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT
              enabled,
              interval_seconds,
              run_on_start,
              last_started_at,
              last_finished_at,
              last_status,
              last_reason,
              last_error,
              last_snapshot_id,
              last_task_type,
              last_plan_id,
              last_recommendation_ids,
              overrun_count,
              skipped_count,
              updated_at
            FROM scheduler_state
            WHERE id = 1
            """
        ).fetchone()

    def _from_row(self, row: sqlite3.Row) -> SchedulerState:
        return SchedulerState(
            enabled=bool(row["enabled"]),
            interval_seconds=row["interval_seconds"],
            run_on_start=bool(row["run_on_start"]),
            last_started_at=self._parse_datetime(row["last_started_at"]),
            last_finished_at=self._parse_datetime(row["last_finished_at"]),
            last_status=row["last_status"],
            last_reason=row["last_reason"],
            last_error=row["last_error"],
            last_snapshot_id=row["last_snapshot_id"],
            last_task_type=row["last_task_type"],
            last_plan_id=row["last_plan_id"],
            last_recommendation_ids=self._parse_recommendation_ids(
                row["last_recommendation_ids"]
            ),
            overrun_count=int(row["overrun_count"]),
            skipped_count=int(row["skipped_count"]),
            updated_at=self._parse_datetime(row["updated_at"]),
        )

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)

    @staticmethod
    def _to_int(value: bool) -> int:
        return int(value)

    @staticmethod
    def _parse_recommendation_ids(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed]


def _require_timezone_aware(value: datetime) -> None:
    # 调度状态会直接暴露给 API，时间必须带时区以保证前端和日志可比较。
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
