from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from quantitative_trading.watchlist.models import (
    WatchPinnedInput,
    WatchPinnedItem,
    WatchPinnedSource,
)


WATCH_PINNED_CSV_COLUMNS = ["symbol", "name", "rank", "plan_enabled", "note"]
TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "f", "no", "n", "off", ""}


def parse_watch_pinned_bool(value: bool | str | None) -> bool:
    if isinstance(value, bool):
        return value
    normalized = "" if value is None else value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"invalid boolean value: {value}")


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

    def import_csv(
        self,
        path: Path,
        *,
        source: WatchPinnedSource,
        now: datetime,
    ) -> list[WatchPinnedItem]:
        items = self._read_csv_items(path)
        return self.replace_all(items, source=source, now=now)

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

    def _read_csv_items(self, path: Path) -> list[WatchPinnedInput]:
        items: list[WatchPinnedInput] = []
        seen_symbols: dict[str, int] = {}
        with path.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames != WATCH_PINNED_CSV_COLUMNS:
                expected = ",".join(WATCH_PINNED_CSV_COLUMNS)
                raise ValueError(f"CSV header must exactly match: {expected}")
            for row_number, row in enumerate(reader, start=2):
                try:
                    item = WatchPinnedInput.model_validate(
                        {
                            "symbol": row["symbol"],
                            "name": row["name"],
                            "rank": row["rank"],
                            "plan_enabled": parse_watch_pinned_bool(
                                row["plan_enabled"],
                            ),
                            "note": row["note"],
                        }
                    )
                except (KeyError, ValidationError, ValueError) as exc:
                    raise ValueError(f"invalid watchlist row {row_number}") from exc
                if item.symbol in seen_symbols:
                    first_row = seen_symbols[item.symbol]
                    raise ValueError(
                        f"duplicate symbol {item.symbol} at row {row_number}; "
                        f"first seen at row {first_row}"
                    )
                seen_symbols[item.symbol] = row_number
                items.append(item)
        return items

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
