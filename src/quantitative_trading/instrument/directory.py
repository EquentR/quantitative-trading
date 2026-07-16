from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

from quantitative_trading.instrument.adapters import InstrumentDirectorySnapshot
from quantitative_trading.instrument.models import InstrumentMetadata
from quantitative_trading.instrument.repository import (
    InstrumentCatalogState,
    InstrumentCatalogStateRepository,
    InstrumentCatalogStatus,
    InstrumentRepository,
)
from quantitative_trading.market.calendar import TradingCalendar
from quantitative_trading.sanitization import safe_error_summary


class InstrumentDirectoryAdapter(Protocol):
    sources: tuple[str, ...]

    def fetch(self, trade_date: date) -> InstrumentDirectorySnapshot: ...


class InstrumentDirectoryUnavailableError(RuntimeError):
    pass


_INSTRUMENT_WARNING_RE = re.compile(r"(?<![0-9])[0-9]{6}(?![0-9])")


def directory_summary_warnings(warnings: list[str]) -> list[str]:
    """Keep preview-level directory warnings concise and system-scoped."""
    return list(
        dict.fromkeys(
            warning for warning in warnings if not _INSTRUMENT_WARNING_RE.search(warning)
        )
    )


def latest_completed_directory_trade_date(
    now: datetime,
    calendar: TradingCalendar,
) -> date:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("directory trade-date clock must be timezone-aware")
    local = now.astimezone(calendar.timezone)
    if calendar.is_trading_day(local.date()) and local.time() >= time(16, 30):
        return local.date()
    return calendar.previous_trading_day(local.date())


class InstrumentDirectoryService:
    def __init__(
        self,
        repository: InstrumentRepository,
        state_repository: InstrumentCatalogStateRepository,
        adapter: InstrumentDirectoryAdapter,
        *,
        now: Callable[[], datetime] | None = None,
        timezone: ZoneInfo | None = None,
    ) -> None:
        self._repository = repository
        self._states = state_repository
        self._adapter = adapter
        self._now = now or (lambda: datetime.now(UTC))
        self._timezone = timezone or ZoneInfo("Asia/Shanghai")

    def ensure_current(self, trade_date: date) -> InstrumentDirectorySnapshot:
        now = self._current_time()
        states = {source: self._states.get(source) for source in self._adapter.sources}
        if states and all(
            state is not None
            and state.last_attempt_at.astimezone(self._timezone).date()
            == now.astimezone(self._timezone).date()
            for state in states.values()
        ):
            return self._cached_snapshot(states)

        try:
            snapshot = self._adapter.fetch(trade_date)
            if not snapshot.items:
                raise ValueError("instrument directory returned no valid instruments")
            missing_sources = (
                [
                    source
                    for source in self._adapter.sources
                    if snapshot.source_item_counts.get(source, 0) <= 0
                ]
                if snapshot.source_item_counts
                else []
            )
            if missing_sources:
                raise ValueError(
                    "instrument directory source returned no valid instruments: "
                    + ",".join(missing_sources)
                )
        except Exception as exc:
            return self._handle_refresh_failure(states, exc=exc, now=now)

        with self._repository.connection:
            self._repository.replace_catalog(snapshot.items, commit=False)
            for source in self._adapter.sources:
                self._states.save(
                    InstrumentCatalogState(
                        source=source,
                        last_attempt_at=now,
                        last_success_at=now,
                        data_trade_date=snapshot.source_trade_dates.get(source, trade_date),
                        status=InstrumentCatalogStatus.COMPLETE,
                        last_error="",
                        warnings=list(snapshot.warnings),
                        updated_at=now,
                    ),
                    commit=False,
                )
        return snapshot

    def search(
        self,
        query: str,
        *,
        trade_date: date,
        limit: int = 50,
    ) -> list[InstrumentMetadata]:
        normalized = query.strip()
        if not normalized:
            return []
        self.ensure_current(trade_date)
        return self._repository.search(normalized, limit=limit)

    def _cached_snapshot(
        self,
        states: dict[str, InstrumentCatalogState | None],
    ) -> InstrumentDirectorySnapshot:
        items = self._repository.list_active()
        if not items:
            raise InstrumentDirectoryUnavailableError("instrument directory unavailable")
        source_dates = {
            source: state.data_trade_date
            for source, state in states.items()
            if state is not None and state.data_trade_date is not None
        }
        is_stale = any(
            state is not None and state.status is InstrumentCatalogStatus.STALE
            for state in states.values()
        )
        warnings = list(
            dict.fromkeys(
                warning
                for state in states.values()
                if state is not None
                for warning in state.warnings
            )
        )
        if is_stale:
            warnings.append("instrument directory refresh failed; using stale catalog")
        return InstrumentDirectorySnapshot(
            items=items,
            source_trade_dates=source_dates,
            warnings=list(dict.fromkeys(warnings)),
        )

    def _handle_refresh_failure(
        self,
        states: dict[str, InstrumentCatalogState | None],
        *,
        exc: Exception,
        now: datetime,
    ) -> InstrumentDirectorySnapshot:
        error = safe_error_summary(exc)
        items = self._repository.list_active()
        status = InstrumentCatalogStatus.STALE if items else InstrumentCatalogStatus.FAILED
        with self._repository.connection:
            for source in self._adapter.sources:
                previous = states.get(source)
                self._states.save(
                    InstrumentCatalogState(
                        source=source,
                        last_attempt_at=now,
                        last_success_at=(
                            None if previous is None else previous.last_success_at
                        ),
                        data_trade_date=(
                            None if previous is None else previous.data_trade_date
                        ),
                        status=status,
                        last_error=error,
                        warnings=[
                            "instrument directory refresh failed; using stale catalog"
                        ],
                        updated_at=now,
                    ),
                    commit=False,
                )
        if not items:
            raise InstrumentDirectoryUnavailableError(
                "instrument directory unavailable"
            ) from exc
        return InstrumentDirectorySnapshot(
            items=items,
            source_trade_dates={
                source: state.data_trade_date
                for source, state in states.items()
                if state is not None and state.data_trade_date is not None
            },
            warnings=["instrument directory refresh failed; using stale catalog"],
        )

    def _current_time(self) -> datetime:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("directory service time must be timezone-aware")
        return now
