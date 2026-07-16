from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel

from quantitative_trading.datasource.miaoxiang import RemoteWatchlistResult
from quantitative_trading.instrument.models import (
    InstrumentCandidate,
    InstrumentMetadata,
    InstrumentPreview,
    InstrumentPreviewSource,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import (
    InstrumentPreviewRepository,
    InstrumentRepository,
)
from quantitative_trading.watchlist.models import WatchPinnedItem, WatchPinnedSource
from quantitative_trading.watchlist.repository import WatchPinnedRepository


class InstrumentSelectionInvalidError(ValueError):
    pass


class InstrumentSelectionResult(BaseModel):
    items: list[WatchPinnedItem]
    warnings: list[str]


def current_time() -> datetime:
    return datetime.now(UTC)


class InstrumentCandidateService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        now: Callable[[], datetime] = current_time,
        preview_ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        self.connection = connection
        self._instruments = InstrumentRepository(connection)
        self._previews = InstrumentPreviewRepository(connection)
        self._watchlist = WatchPinnedRepository(connection)
        self._now = now
        self._preview_ttl = preview_ttl

    def preview_eastmoney(
        self,
        result: RemoteWatchlistResult,
        *,
        directory_available: bool = True,
    ) -> InstrumentPreview:
        candidates: list[InstrumentCandidate] = []
        filtered = 0
        monitored = {item.symbol for item in self._watchlist.list()}
        now = self._aware_now()
        for remote in result.items:
            metadata = self._instruments.get(remote.symbol)
            if metadata is None:
                if directory_available:
                    filtered += 1
                    continue
                metadata = InstrumentMetadata(
                    symbol=remote.symbol,
                    name=remote.name,
                    exchange=None,
                    instrument_type=InstrumentType.UNKNOWN,
                    settlement_cycle=SettlementCycle.UNKNOWN,
                    metadata_source="eastmoney_watchlist_unverified",
                    metadata_checked_at=now,
                    rule_version="unverified-v1",
                    warnings=["instrument is not verified by the local directory"],
                )
            candidates.append(
                InstrumentCandidate.from_metadata(
                    metadata,
                    source=InstrumentPreviewSource.EASTMONEY_WATCHLIST,
                    source_rank=remote.rank,
                    already_monitored=remote.symbol in monitored,
                    selectable=metadata.instrument_type is not InstrumentType.UNKNOWN,
                )
            )
        warnings = list(result.warnings)
        if filtered:
            noun = "instrument" if filtered == 1 else "instruments"
            warnings.append(
                f"filtered {filtered} remote {noun} outside the supported directory"
            )
        return self._save_preview(
            source=InstrumentPreviewSource.EASTMONEY_WATCHLIST,
            query=None,
            items=candidates,
            warnings=warnings,
            now=now,
        )

    def search(
        self,
        query: str,
        *,
        warnings: list[str] | None = None,
    ) -> InstrumentPreview:
        normalized = query.strip()
        if not 1 <= len(normalized) <= 40:
            raise ValueError("instrument search query must contain 1 to 40 characters")
        monitored = {item.symbol for item in self._watchlist.list()}
        items = [
            InstrumentCandidate.from_metadata(
                metadata,
                source=InstrumentPreviewSource.INSTRUMENT_SEARCH,
                source_rank=None,
                already_monitored=metadata.symbol in monitored,
                selectable=metadata.instrument_type is not InstrumentType.UNKNOWN,
            )
            for metadata in self._instruments.search(normalized, limit=50)
        ]
        return self._save_preview(
            source=InstrumentPreviewSource.INSTRUMENT_SEARCH,
            query=normalized,
            items=items,
            warnings=list(warnings or []),
            now=self._aware_now(),
        )

    def select(
        self,
        preview_id: UUID,
        symbols: list[str],
    ) -> InstrumentSelectionResult:
        now = self._aware_now()
        preview = self._previews.get(preview_id, now=now)
        if not symbols or len(symbols) != len(set(symbols)):
            raise InstrumentSelectionInvalidError(
                "selection must contain unique preview symbols"
            )
        candidates = {item.symbol: item for item in preview.items}
        preview_candidates: list[InstrumentCandidate] = []
        for symbol in symbols:
            candidate = candidates.get(symbol)
            if (
                candidate is None
                or not candidate.selectable
                or candidate.instrument_type is InstrumentType.UNKNOWN
            ):
                raise InstrumentSelectionInvalidError(
                    "selection contains a missing or unselectable symbol"
                )
            preview_candidates.append(candidate)

        warnings: list[str] = []
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            selected: list[InstrumentCandidate] = []
            for candidate in preview_candidates:
                current = self._instruments.get(candidate.symbol)
                if (
                    current is None
                    or current.instrument_type is InstrumentType.UNKNOWN
                ):
                    raise InstrumentSelectionInvalidError(
                        "selection contains a missing or unselectable symbol"
                    )
                selected.append(
                    InstrumentCandidate.from_metadata(
                        current,
                        source=preview.source,
                        source_rank=candidate.source_rank,
                        already_monitored=candidate.already_monitored,
                        selectable=True,
                    )
                )

            existing_by_symbol = {
                item.symbol: item for item in self._watchlist.list()
            }
            max_rank = max(
                (item.rank for item in existing_by_symbol.values()), default=0
            )
            for offset, candidate in enumerate(selected, start=1):
                existing = existing_by_symbol.get(candidate.symbol)
                if existing is None:
                    rank = (
                        candidate.source_rank
                        if preview.source is InstrumentPreviewSource.EASTMONEY_WATCHLIST
                        and candidate.source_rank is not None
                        else max_rank + offset
                    )
                    plan_enabled = candidate.settlement_cycle is not SettlementCycle.UNKNOWN
                    source = (
                        WatchPinnedSource.SYNCED
                        if preview.source is InstrumentPreviewSource.EASTMONEY_WATCHLIST
                        else WatchPinnedSource.MANUAL
                    )
                    note = ""
                else:
                    rank = existing.rank
                    plan_enabled = existing.plan_enabled
                    note = existing.note
                    source = existing.source
                    if (
                        preview.source is InstrumentPreviewSource.EASTMONEY_WATCHLIST
                        and existing.source is WatchPinnedSource.MANUAL
                    ):
                        source = WatchPinnedSource.MANUAL_SYNCED
                    elif (
                        preview.source is InstrumentPreviewSource.INSTRUMENT_SEARCH
                        and existing.source is WatchPinnedSource.SYNCED
                    ):
                        source = WatchPinnedSource.MANUAL_SYNCED
                if candidate.settlement_cycle is SettlementCycle.UNKNOWN:
                    plan_enabled = False
                    warnings.append(
                        f"{candidate.symbol} settlement cycle is unknown; plan remains disabled"
                    )
                if (
                    existing is not None
                    and existing.name == candidate.name
                    and existing.rank == rank
                    and existing.plan_enabled == plan_enabled
                    and existing.source is source
                    and existing.note == note
                ):
                    continue
                self.connection.execute(
                    """
                    INSERT INTO watch_pinned (
                      symbol, name, rank, plan_enabled, source, note, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                      name=excluded.name,
                      rank=excluded.rank,
                      plan_enabled=excluded.plan_enabled,
                      source=excluded.source,
                      note=excluded.note,
                      updated_at=excluded.updated_at
                    """,
                    (
                        candidate.symbol,
                        candidate.name,
                        rank,
                        int(plan_enabled),
                        source.value,
                        note,
                        now.isoformat(),
                    ),
                )

        items = [self._watchlist.get(symbol) for symbol in symbols]
        return InstrumentSelectionResult(
            items=[item for item in items if item is not None],
            warnings=list(dict.fromkeys(warnings)),
        )

    def _save_preview(
        self,
        *,
        source: InstrumentPreviewSource,
        query: str | None,
        items: list[InstrumentCandidate],
        warnings: list[str],
        now: datetime,
    ) -> InstrumentPreview:
        return self._previews.save(
            InstrumentPreview(
                preview_id=uuid4(),
                source=source,
                query=query,
                created_at=now,
                expires_at=now + self._preview_ttl,
                items=items,
                warnings=warnings,
            )
        )

    def _aware_now(self) -> datetime:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("instrument service clock must be timezone-aware")
        return now
