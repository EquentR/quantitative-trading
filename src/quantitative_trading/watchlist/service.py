from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from quantitative_trading.watchlist.models import (
    WatchPinnedInput,
    WatchPinnedImportResult,
    WatchPinnedItem,
    WatchPinnedSource,
)
from quantitative_trading.watchlist.repository import WatchPinnedRepository


def current_time() -> datetime:
    return datetime.now(UTC)


class ReadOnlyWatchPinnedService:
    def __init__(self, repository: WatchPinnedRepository) -> None:
        self._repository = repository

    def get_pinned(self, symbol: str) -> WatchPinnedItem | None:
        return self._repository.get(symbol)

    def list_pinned(self) -> list[WatchPinnedItem]:
        return self._repository.list()


class WatchPinnedService(ReadOnlyWatchPinnedService):
    def parse_csv(self, path: Path) -> list[WatchPinnedInput]:
        return self._repository.read_csv_items(path)

    def upsert_pinned(
        self,
        item: WatchPinnedInput,
        *,
        source: WatchPinnedSource,
        now: datetime | None = None,
    ) -> WatchPinnedItem:
        return self._repository.upsert(item, source=source, now=now or current_time())

    def remove_pinned(self, symbol: str) -> None:
        self._repository.remove(symbol)

    def replace_pinned(
        self,
        items: list[WatchPinnedInput],
        *,
        source: WatchPinnedSource,
        now: datetime | None = None,
    ) -> list[WatchPinnedItem]:
        return self._repository.replace_all(items, source=source, now=now or current_time())

    def replace_pinned_with_warnings(
        self,
        items: list[WatchPinnedInput],
        *,
        source: WatchPinnedSource,
        now: datetime | None = None,
    ) -> WatchPinnedImportResult:
        return self._repository.replace_all_with_warnings(
            items,
            source=source,
            now=now or current_time(),
        )

    def import_csv(
        self,
        path: Path,
        *,
        source: WatchPinnedSource,
        now: datetime | None = None,
    ) -> list[WatchPinnedItem]:
        return self._repository.import_csv(path, source=source, now=now or current_time())

    def import_csv_with_warnings(
        self,
        path: Path,
        *,
        source: WatchPinnedSource,
        now: datetime | None = None,
    ) -> WatchPinnedImportResult:
        return self._repository.import_csv_with_warnings(
            path,
            source=source,
            now=now or current_time(),
        )

    def merge_synced_pinned(
        self,
        items: list[WatchPinnedInput],
        *,
        now: datetime | None = None,
    ) -> list[WatchPinnedItem]:
        return self._repository.merge_synced(items, now=now or current_time())
