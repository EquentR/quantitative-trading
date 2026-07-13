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
    provider_calls: int
    rows_received: int
    rows_written: int

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
        ranges = self._fetch_ranges(desired, set(existing))
        fetched = [
            bar
            for start, end in ranges
            for bar in self.daily_provider.get_daily_bars(
                symbol, start, end, "forward"
            )
        ]
        connection = self.daily_repository.connection
        connection.execute("SAVEPOINT daily_backfill_dataset")
        try:
            rows_written = 0
            for bar in fetched:
                if bar.trade_date in desired:
                    known = connection.execute(
                        """SELECT 1 FROM daily_bars
                           WHERE symbol=? AND trade_date=? AND adjustment=?
                             AND content_hash=?""",
                        (
                            bar.symbol,
                            bar.trade_date.isoformat(),
                            bar.adjustment,
                            bar.content_hash,
                        ),
                    ).fetchone()
                    self.daily_repository.save(bar, commit=False)
                    rows_written += int(known is None)
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
        return CreatedDatasetSnapshot(
            snapshot_id,
            snapshot,
            provider_calls=len(ranges),
            rows_received=len(fetched),
            rows_written=rows_written,
        )

    def backfill_money_flow(
        self, run_id: str, symbol: str, as_of: date
    ) -> CreatedDatasetSnapshot[MoneyFlowSnapshot]:
        desired = self.calendar.sessions_ending(as_of, self.MONEY_FLOW_WINDOW)
        existing = {
            stored.flow.trade_date: stored
            for stored in self.money_flow_repository.current(symbol)
            if stored.flow.trade_date in desired
        }
        ranges = self._fetch_ranges(desired, set(existing))
        fetched = [
            flow
            for start, end in ranges
            for flow in self.money_flow_provider.get_daily_money_flow(
                symbol, start, end
            )
        ]
        connection = self.money_flow_repository.connection
        connection.execute("SAVEPOINT money_flow_backfill_dataset")
        try:
            rows_written = 0
            for flow in fetched:
                if flow.trade_date in desired:
                    known = connection.execute(
                        """SELECT 1 FROM daily_money_flows
                           WHERE symbol=? AND trade_date=? AND content_hash=?""",
                        (
                            flow.symbol,
                            flow.trade_date.isoformat(),
                            flow.content_hash,
                        ),
                    ).fetchone()
                    self.money_flow_repository.save(flow, commit=False)
                    rows_written += int(known is None)
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
        return CreatedDatasetSnapshot(
            snapshot_id,
            snapshot,
            provider_calls=len(ranges),
            rows_received=len(fetched),
            rows_written=rows_written,
        )

    def _fetch_ranges(
        self,
        desired: list[date],
        existing_dates: set[date],
    ) -> list[tuple[date, date]]:
        requested = set(desired[-self.CORRECTION_WINDOW :]) | {
            day for day in desired if day not in existing_dates
        }
        ranges: list[tuple[date, date]] = []
        start: date | None = None
        end: date | None = None
        for day in desired:
            if day not in requested:
                if start is not None and end is not None:
                    ranges.append((start, end))
                    start = end = None
                continue
            if start is None:
                start = day
            end = day
        if start is not None and end is not None:
            ranges.append((start, end))
        return ranges

    def _fetched_at(self) -> datetime:
        value = self.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("backfill time must be timezone-aware")
        return value
