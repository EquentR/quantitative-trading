from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Generic, TypeVar

from quantitative_trading.market.adapters import (
    DailyBarCoverageProvider,
    DailyBarProvider,
    DailyBarProviderRouter,
    MarketProviderError,
    MoneyFlowProvider,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    CaptureResultStatus,
    DailyBar,
    DailyBarCoverageEvidence,
    HistoryCompleteness,
    HistorySnapshot,
    ListingDateEvidence,
    MoneyFlowSnapshot,
)
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    HistorySnapshotRepository,
    MoneyFlowRepository,
    MoneyFlowSnapshotRepository,
    StoredDailyBar,
    content_digest,
)


SnapshotT = TypeVar("SnapshotT", HistorySnapshot, MoneyFlowSnapshot)
DAILY_HISTORY_WINDOW = 250


def _current_daily_window(
    repository: DailyBarRepository,
    symbol: str,
    desired: list[date],
) -> list[StoredDailyBar]:
    if not desired:
        return []
    desired_set = set(desired)
    return [
        stored
        for stored in repository.current(
            symbol,
            since=desired[0],
            through=desired[-1],
        )
        if stored.bar.trade_date in desired_set
    ]


def _listing_window_is_complete(
    calendar: XSHGTradingCalendar,
    current: list[StoredDailyBar],
    *,
    listing_evidence: ListingDateEvidence,
    as_of: date,
) -> bool:
    expected = calendar.trading_days(
        listing_evidence.listing_date,
        as_of,
    )
    actual = [stored.bar.trade_date for stored in current]
    return bool(expected) and actual == expected


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


class BackfillProviderCaptureError(MarketProviderError):
    def __init__(
        self,
        message: str,
        *,
        provider_calls: int,
        rows_received: int,
        rows_written: int = 0,
    ) -> None:
        super().__init__(message)
        self.provider_calls = provider_calls
        self.rows_received = rows_received
        self.rows_written = rows_written


@dataclass(frozen=True)
class MaterializedHistorySnapshot:
    snapshot_id: int
    snapshot: HistorySnapshot


class LocalHistoryMaterializer:
    DAILY_WINDOW = DAILY_HISTORY_WINDOW

    def __init__(
        self,
        *,
        calendar: XSHGTradingCalendar,
        daily_repository: DailyBarRepository,
        history_snapshot_repository: HistorySnapshotRepository,
    ) -> None:
        if daily_repository.connection is not history_snapshot_repository.connection:
            raise ValueError("history repositories must share the same connection")
        self.calendar = calendar
        self.daily_repository = daily_repository
        self.history_snapshot_repository = history_snapshot_repository

    def materialize(
        self,
        *,
        run_id: str,
        symbol: str,
        cutoff: date,
        fetched_at: datetime,
        listing_evidence: ListingDateEvidence | None = None,
        commit: bool = True,
    ) -> MaterializedHistorySnapshot:
        desired = self.calendar.sessions_ending(cutoff, self.DAILY_WINDOW)
        current = _current_daily_window(self.daily_repository, symbol, desired)
        complete = [stored.bar.trade_date for stored in current] == desired
        verified_listing_evidence = (
            listing_evidence
            if listing_evidence is not None
            and _listing_window_is_complete(
                self.calendar,
                current,
                listing_evidence=listing_evidence,
                as_of=cutoff,
            )
            else None
        )
        snapshot = HistorySnapshot(
            run_id=run_id,
            symbol=symbol,
            data_start=None if not current else current[0].bar.trade_date,
            data_end=None if not current else current[-1].bar.trade_date,
            row_count=len(current),
            content_digest=content_digest(
                [stored.bar.content_hash for stored in current]
            ),
            status=(
                CaptureResultStatus.COMPLETE
                if complete
                else CaptureResultStatus.DEGRADED
            ),
            warning=(
                ""
                if complete
                else (
                    f"local history has {len(current)} rows through "
                    f"{cutoff.isoformat()}; verified from listing date "
                    f"{verified_listing_evidence.listing_date.isoformat()}"
                )
                if verified_listing_evidence is not None
                else (
                    f"local history has {len(current)} rows through "
                    f"{cutoff.isoformat()}; completeness is unverified"
                )
            ),
            fetched_at=fetched_at,
            completeness=(
                HistoryCompleteness.VERIFIED_LISTING_DATE
                if verified_listing_evidence is not None
                else HistoryCompleteness.UNVERIFIABLE
            ),
            listing_evidence=verified_listing_evidence,
        )
        snapshot_id = self.history_snapshot_repository.save(
            snapshot,
            [stored.id for stored in current],
            commit=commit,
        )
        return MaterializedHistorySnapshot(
            snapshot_id=snapshot_id,
            snapshot=snapshot,
        )


class HeavyDataBackfillService:
    DAILY_WINDOW = DAILY_HISTORY_WINDOW
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
        self,
        run_id: str,
        symbol: str,
        as_of: date,
        *,
        listing_evidence: ListingDateEvidence | None = None,
    ) -> CreatedDatasetSnapshot[HistorySnapshot]:
        desired = self.calendar.sessions_ending(as_of, self.DAILY_WINDOW)
        existing = {
            stored.bar.trade_date: stored
            for stored in _current_daily_window(
                self.daily_repository,
                symbol,
                desired,
            )
        }
        daily_provider = self._daily_provider_for(symbol)
        provider_calls = 0
        rows_received = 0
        force_full_coverage = False
        prior = self.history_snapshot_repository.latest_usable_for_symbol(
            symbol,
            as_of=as_of,
            expected_rows=self.DAILY_WINDOW,
        )
        if (
            prior is not None
            and prior.snapshot.completeness
            is HistoryCompleteness.VERIFIED_PROVIDER_WINDOW
            and isinstance(daily_provider, DailyBarCoverageProvider)
        ):
            correction_start = desired[-self.CORRECTION_WINDOW]
            provider_calls += 1
            try:
                correction_result = daily_provider.get_daily_bars_with_coverage(
                    symbol,
                    correction_start,
                    desired[-1],
                    "forward",
                )
            except MarketProviderError as exc:
                raise BackfillProviderCaptureError(
                    str(exc),
                    provider_calls=provider_calls,
                    rows_received=rows_received,
                ) from exc
            correction_bars = list(correction_result.bars)
            self._validate_daily_fetch(
                symbol,
                correction_start,
                desired[-1],
                correction_bars,
                correction_result.coverage_evidence,
            )
            rows_received += len(correction_bars)
            prior_correction = {
                member.bar.trade_date: member.bar.content_hash
                for member in self.history_snapshot_repository.members(
                    prior.snapshot_id
                )
                if member.bar.trade_date >= correction_start
            }
            returned_correction = {
                bar.trade_date: bar.content_hash for bar in correction_bars
            }
            correction_changed = returned_correction != prior_correction
            if (
                correction_result.coverage_evidence.complete_request_window
                and not correction_changed
            ):
                return CreatedDatasetSnapshot(
                    prior.snapshot_id,
                    prior.snapshot,
                    provider_calls=provider_calls,
                    rows_received=rows_received,
                    rows_written=0,
                )
            force_full_coverage = True

        ranges = (
            [(desired[0], desired[-1])]
            if isinstance(daily_provider, DailyBarCoverageProvider)
            and (force_full_coverage or len(existing) < len(desired))
            else self._fetch_ranges(desired, set(existing))
        )
        fetched: list[DailyBar] = []
        coverage_evidence: DailyBarCoverageEvidence | None = None
        for start, end in ranges:
            provider_calls += 1
            if isinstance(daily_provider, DailyBarCoverageProvider):
                try:
                    result = daily_provider.get_daily_bars_with_coverage(
                        symbol,
                        start,
                        end,
                        "forward",
                    )
                except MarketProviderError as exc:
                    raise BackfillProviderCaptureError(
                        str(exc),
                        provider_calls=provider_calls,
                        rows_received=rows_received,
                    ) from exc
                bars = list(result.bars)
                self._validate_daily_fetch(
                    symbol,
                    start,
                    end,
                    bars,
                    result.coverage_evidence,
                )
                if start == desired[0] and end == desired[-1]:
                    coverage_evidence = result.coverage_evidence
            else:
                try:
                    bars = list(
                        daily_provider.get_daily_bars(
                            symbol,
                            start,
                            end,
                            "forward",
                        )
                    )
                except MarketProviderError as exc:
                    raise BackfillProviderCaptureError(
                        str(exc),
                        provider_calls=provider_calls,
                        rows_received=rows_received,
                    ) from exc
            fetched.extend(bars)
            rows_received += len(bars)
        connection = self.daily_repository.connection
        connection.execute("SAVEPOINT daily_backfill_dataset")
        try:
            rows_written = 0
            authoritative_member_ids: dict[date, int] = {}
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
                    member_id = self.daily_repository.save(bar, commit=False)
                    rows_written += int(known is None)
                    if (
                        coverage_evidence is not None
                        and coverage_evidence.complete_request_window
                    ):
                        authoritative_member_ids[bar.trade_date] = member_id
            if (
                coverage_evidence is not None
                and coverage_evidence.complete_request_window
            ):
                current = [
                    stored
                    for trade_day in sorted(authoritative_member_ids)
                    if (
                        stored := self.daily_repository.get(
                            authoritative_member_ids[trade_day]
                        )
                    )
                    is not None
                ]
            else:
                current = _current_daily_window(
                    self.daily_repository,
                    symbol,
                    desired,
                )
            fetched_at = self._fetched_at()
            complete = len(current) == len(desired)
            completeness = HistoryCompleteness.UNVERIFIABLE
            verified_listing_evidence: ListingDateEvidence | None = None
            if (
                coverage_evidence is not None
                and coverage_evidence.complete_request_window
            ):
                completeness = HistoryCompleteness.VERIFIED_PROVIDER_WINDOW
            elif listing_evidence is not None and _listing_window_is_complete(
                self.calendar,
                current,
                listing_evidence=listing_evidence,
                as_of=as_of,
            ):
                completeness = HistoryCompleteness.VERIFIED_LISTING_DATE
                verified_listing_evidence = listing_evidence
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
                    else self._short_history_warning(
                        expected_rows=len(desired),
                        actual_rows=len(current),
                        completeness=completeness,
                        listing_evidence=verified_listing_evidence,
                    )
                ),
                fetched_at=fetched_at,
                completeness=completeness,
                coverage_evidence=coverage_evidence,
                listing_evidence=verified_listing_evidence,
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
            provider_calls=provider_calls,
            rows_received=rows_received,
            rows_written=rows_written,
        )

    def _daily_provider_for(self, symbol: str) -> DailyBarProvider:
        if isinstance(self.daily_provider, DailyBarProviderRouter):
            return self.daily_provider.daily_bar_provider_for(symbol)
        return self.daily_provider

    @staticmethod
    def _validate_daily_fetch(
        symbol: str,
        start: date,
        end: date,
        bars: list[DailyBar],
        evidence: DailyBarCoverageEvidence,
    ) -> None:
        if evidence.requested_start != start or evidence.requested_end != end:
            raise ValueError("daily coverage evidence does not match requested range")
        if any(
            bar.symbol != symbol
            or bar.adjustment != "forward"
            or not start <= bar.trade_date <= end
            for bar in bars
        ):
            raise ValueError("daily provider returned bars outside requested scope")
        dates = [bar.trade_date for bar in bars]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError("daily provider returned duplicate or unordered bars")
        earliest = None if not bars else min(bar.trade_date for bar in bars)
        if evidence.complete_request_window and evidence.earliest_available_date != earliest:
            raise ValueError("daily coverage earliest date does not match returned bars")

    @staticmethod
    def _short_history_warning(
        *,
        expected_rows: int,
        actual_rows: int,
        completeness: HistoryCompleteness,
        listing_evidence: ListingDateEvidence | None,
    ) -> str:
        prefix = f"expected {expected_rows} daily bars, got {actual_rows}"
        if completeness is HistoryCompleteness.VERIFIED_PROVIDER_WINDOW:
            return f"{prefix}; provider observed the complete request window"
        if completeness is HistoryCompleteness.VERIFIED_LISTING_DATE:
            return (
                f"{prefix}; verified from listing date "
                f"{listing_evidence.listing_date.isoformat()}"
            )
        return f"{prefix}; short history start is unverifiable"

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
