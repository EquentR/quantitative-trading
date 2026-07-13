from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Generic, TypeVar

from quantitative_trading.market.adapters import DailyBarProvider, MoneyFlowProvider
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    CaptureResultStatus,
    HistorySnapshot,
    MoneyFlowSnapshot,
)
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    HistorySnapshotRepository,
    MoneyFlowRepository,
    MoneyFlowSnapshotRepository,
    content_digest,
)


SnapshotT = TypeVar("SnapshotT", HistorySnapshot, MoneyFlowSnapshot)


@dataclass(frozen=True)
class CreatedDatasetSnapshot(Generic[SnapshotT]):
    snapshot_id: int
    snapshot: SnapshotT

    @property
    def row_count(self) -> int:
        return self.snapshot.row_count


class HeavyDataBackfillService:
    DAILY_WINDOW = 250
    MONEY_FLOW_WINDOW = 60
    CORRECTION_WINDOW = 5

    def __init__(
        self,
        *,
        calendar: XSHGTradingCalendar,
        daily_provider: DailyBarProvider,
        money_flow_provider: MoneyFlowProvider,
        daily_repository: DailyBarRepository,
        money_flow_repository: MoneyFlowRepository,
        history_snapshot_repository: HistorySnapshotRepository,
        money_flow_snapshot_repository: MoneyFlowSnapshotRepository,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.calendar = calendar
        self.daily_provider = daily_provider
        self.money_flow_provider = money_flow_provider
        self.daily_repository = daily_repository
        self.money_flow_repository = money_flow_repository
        self.history_snapshot_repository = history_snapshot_repository
        self.money_flow_snapshot_repository = money_flow_snapshot_repository
        self.now = now or (lambda: datetime.now(UTC))

    def backfill_daily(
        self, run_id: str, symbol: str, as_of: date
    ) -> CreatedDatasetSnapshot[HistorySnapshot]:
        desired = self.calendar.sessions_ending(as_of, self.DAILY_WINDOW)
        existing = {
            stored.bar.trade_date: stored
            for stored in self.daily_repository.current(symbol)
            if stored.bar.trade_date in desired
        }
        start = self._fetch_start(desired, set(existing))
        fetched = list(
            self.daily_provider.get_daily_bars(symbol, start, desired[-1], "forward")
        )
        connection = self.daily_repository.connection
        connection.execute("SAVEPOINT daily_backfill_dataset")
        try:
            for bar in fetched:
                if bar.trade_date in desired:
                    self.daily_repository.save(bar, commit=False)
            current = [
                stored
                for stored in self.daily_repository.current(symbol, limit=self.DAILY_WINDOW)
                if stored.bar.trade_date in desired
            ]
            fetched_at = self._fetched_at()
            complete = len(current) == len(desired)
            snapshot = HistorySnapshot(
                run_id=run_id,
                symbol=symbol,
                data_start=None if not current else current[0].bar.trade_date,
                data_end=None if not current else current[-1].bar.trade_date,
                row_count=len(current),
                content_digest=content_digest([stored.bar.content_hash for stored in current]),
                status=(
                    CaptureResultStatus.COMPLETE
                    if complete
                    else CaptureResultStatus.DEGRADED
                ),
                warning=(
                    ""
                    if complete
                    else f"expected {len(desired)} daily bars, got {len(current)}"
                ),
                fetched_at=fetched_at,
            )
            snapshot_id = self.history_snapshot_repository.save(
                snapshot, [stored.id for stored in current], commit=False
            )
            connection.execute("RELEASE SAVEPOINT daily_backfill_dataset")
            connection.commit()
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT daily_backfill_dataset")
            connection.execute("RELEASE SAVEPOINT daily_backfill_dataset")
            raise
        return CreatedDatasetSnapshot(snapshot_id, snapshot)

    def backfill_money_flow(
        self, run_id: str, symbol: str, as_of: date
    ) -> CreatedDatasetSnapshot[MoneyFlowSnapshot]:
        desired = self.calendar.sessions_ending(as_of, self.MONEY_FLOW_WINDOW)
        existing = {
            stored.flow.trade_date: stored
            for stored in self.money_flow_repository.current(symbol)
            if stored.flow.trade_date in desired
        }
        start = self._fetch_start(desired, set(existing))
        fetched = list(
            self.money_flow_provider.get_daily_money_flow(symbol, start, desired[-1])
        )
        connection = self.money_flow_repository.connection
        connection.execute("SAVEPOINT money_flow_backfill_dataset")
        try:
            for flow in fetched:
                if flow.trade_date in desired:
                    self.money_flow_repository.save(flow, commit=False)
            current = [
                stored
                for stored in self.money_flow_repository.current(
                    symbol, limit=self.MONEY_FLOW_WINDOW
                )
                if stored.flow.trade_date in desired
            ]
            fetched_at = self._fetched_at()
            complete = len(current) == len(desired)
            snapshot = MoneyFlowSnapshot(
                run_id=run_id,
                symbol=symbol,
                data_start=None if not current else current[0].flow.trade_date,
                data_end=None if not current else current[-1].flow.trade_date,
                row_count=len(current),
                content_digest=content_digest([stored.flow.content_hash for stored in current]),
                status=(
                    CaptureResultStatus.COMPLETE
                    if complete
                    else CaptureResultStatus.DEGRADED
                ),
                warning=(
                    ""
                    if complete
                    else f"expected {len(desired)} money-flow rows, got {len(current)}"
                ),
                fetched_at=fetched_at,
            )
            snapshot_id = self.money_flow_snapshot_repository.save(
                snapshot, [stored.id for stored in current], commit=False
            )
            connection.execute("RELEASE SAVEPOINT money_flow_backfill_dataset")
            connection.commit()
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT money_flow_backfill_dataset")
            connection.execute("RELEASE SAVEPOINT money_flow_backfill_dataset")
            raise
        return CreatedDatasetSnapshot(snapshot_id, snapshot)

    def _fetch_start(self, desired: list[date], existing_dates: set[date]) -> date:
        correction = desired[-self.CORRECTION_WINDOW :]
        missing = [day for day in desired if day not in existing_dates]
        return min([*correction, *missing])

    def _fetched_at(self) -> datetime:
        value = self.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("backfill time must be timezone-aware")
        return value
