from __future__ import annotations

import sqlite3
from datetime import datetime

from quantitative_trading.watchlist.models import (
    WatchPinnedInput,
    WatchPinnedItem,
    WatchPinnedSource,
)


class WatchPinnedRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def list(self) -> list[WatchPinnedItem]:
        rows = self.connection.execute(
            """
            SELECT
              symbol,
              name,
              rank,
              plan_enabled,
              source,
              note,
              updated_at
            FROM watch_pinned
            ORDER BY rank, symbol
            """
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def get(self, symbol: str) -> WatchPinnedItem | None:
        row = self.connection.execute(
            """
            SELECT
              symbol,
              name,
              rank,
              plan_enabled,
              source,
              note,
              updated_at
            FROM watch_pinned
            WHERE symbol = ?
            """,
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def upsert(
        self,
        item: WatchPinnedInput,
        *,
        source: WatchPinnedSource,
        now: datetime,
    ) -> WatchPinnedItem:
        item = WatchPinnedInput.model_validate(item)
        persisted = self._with_metadata(item, source=source, now=now)
        self.connection.execute(
            """
            INSERT INTO watch_pinned (
              symbol,
              name,
              rank,
              plan_enabled,
              source,
              note,
              updated_at
            ) VALUES (
              :symbol,
              :name,
              :rank,
              :plan_enabled,
              :source,
              :note,
              :updated_at
            )
            ON CONFLICT(symbol) DO UPDATE SET
              name = excluded.name,
              rank = excluded.rank,
              plan_enabled = excluded.plan_enabled,
              source = excluded.source,
              note = excluded.note,
              updated_at = excluded.updated_at
            """,
            self._to_row(persisted),
        )
        self.connection.commit()
        return persisted

    def remove(self, symbol: str) -> None:
        self.connection.execute(
            "DELETE FROM watch_pinned WHERE symbol = ?",
            (symbol,),
        )
        self.connection.commit()

    def replace_all(
        self,
        items: list[WatchPinnedInput],
        *,
        source: WatchPinnedSource,
        now: datetime,
    ) -> list[WatchPinnedItem]:
        validated = self._validate_unique(items)
        persisted = [
            self._with_metadata(item, source=source, now=now) for item in validated
        ]

        with self.connection:
            self.connection.execute("DELETE FROM watch_pinned")
            self.connection.executemany(
                """
                INSERT INTO watch_pinned (
                  symbol,
                  name,
                  rank,
                  plan_enabled,
                  source,
                  note,
                  updated_at
                ) VALUES (
                  :symbol,
                  :name,
                  :rank,
                  :plan_enabled,
                  :source,
                  :note,
                  :updated_at
                )
                """,
                [self._to_row(item) for item in persisted],
            )

        return self.list()

    def merge_synced(
        self,
        items: list[WatchPinnedInput],
        *,
        now: datetime,
    ) -> list[WatchPinnedItem]:
        validated = self._validate_unique(items)
        synced_symbols = {item.symbol for item in validated}
        existing_by_symbol = {item.symbol: item for item in self.list()}

        persisted: list[WatchPinnedItem] = []
        for item in validated:
            existing = existing_by_symbol.get(item.symbol)
            if existing is None:
                persisted.append(
                    self._with_metadata(
                        WatchPinnedInput(
                            symbol=item.symbol,
                            name=item.name,
                            rank=item.rank,
                            plan_enabled=False,
                            note="",
                        ),
                        source=WatchPinnedSource.SYNCED,
                        now=now,
                    )
                )
                continue

            source = WatchPinnedSource.SYNCED
            if existing.source in {
                WatchPinnedSource.MANUAL,
                WatchPinnedSource.MANUAL_SYNCED,
            }:
                source = WatchPinnedSource.MANUAL_SYNCED
            persisted.append(
                self._with_metadata(
                    WatchPinnedInput(
                        symbol=item.symbol,
                        name=item.name,
                        rank=item.rank,
                        plan_enabled=existing.plan_enabled,
                        note=existing.note,
                    ),
                    source=source,
                    now=now,
                )
            )

        with self.connection:
            self.connection.execute(
                """
                DELETE FROM watch_pinned
                WHERE source = ? AND symbol NOT IN (
                """
                + ",".join("?" for _ in synced_symbols)
                + ")",
                (WatchPinnedSource.SYNCED.value, *synced_symbols),
            )
            self.connection.execute(
                """
                UPDATE watch_pinned
                SET source = ?, updated_at = ?
                WHERE source = ? AND symbol NOT IN (
                """
                + ",".join("?" for _ in synced_symbols)
                + ")",
                (
                    WatchPinnedSource.MANUAL.value,
                    now.isoformat(),
                    WatchPinnedSource.MANUAL_SYNCED.value,
                    *synced_symbols,
                ),
            )
            for item in persisted:
                self.connection.execute(
                    """
                    INSERT INTO watch_pinned (
                      symbol,
                      name,
                      rank,
                      plan_enabled,
                      source,
                      note,
                      updated_at
                    ) VALUES (
                      :symbol,
                      :name,
                      :rank,
                      :plan_enabled,
                      :source,
                      :note,
                      :updated_at
                    )
                    ON CONFLICT(symbol) DO UPDATE SET
                      name = excluded.name,
                      rank = excluded.rank,
                      plan_enabled = excluded.plan_enabled,
                      source = excluded.source,
                      note = excluded.note,
                      updated_at = excluded.updated_at
                    """,
                    self._to_row(item),
                )

        return self.list()

    def _validate_unique(self, items: list[WatchPinnedInput]) -> list[WatchPinnedInput]:
        validated = [WatchPinnedInput.model_validate(item) for item in items]
        seen_symbols: set[str] = set()
        for item in validated:
            if item.symbol in seen_symbols:
                raise ValueError(f"duplicate symbol {item.symbol}")
            seen_symbols.add(item.symbol)
        return validated

    def _with_metadata(
        self,
        item: WatchPinnedInput,
        *,
        source: WatchPinnedSource,
        now: datetime,
    ) -> WatchPinnedItem:
        data = item.model_dump()
        data["source"] = source
        data["updated_at"] = now
        return WatchPinnedItem.model_validate(data)

    def _to_row(self, item: WatchPinnedItem) -> dict[str, object]:
        data = item.model_dump()
        data["plan_enabled"] = int(item.plan_enabled)
        data["source"] = item.source.value
        data["updated_at"] = item.updated_at.isoformat()
        return data

    def _from_row(self, row: sqlite3.Row) -> WatchPinnedItem:
        data = dict(row)
        data["plan_enabled"] = bool(data["plan_enabled"])
        return WatchPinnedItem.model_validate(data)
