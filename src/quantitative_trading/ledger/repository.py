from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from quantitative_trading.ledger.models import Position, PositionInput


class DuplicatePositionError(ValueError):
    pass


class MissingPositionError(ValueError):
    pass


class PositionRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def add(self, position: PositionInput, *, now: datetime) -> Position:
        position = PositionInput.model_validate(position)
        if self.get(position.symbol) is not None:
            raise DuplicatePositionError(f"position already exists: {position.symbol}")

        persisted = self._with_updated_at(position, now)
        self.connection.execute(
            """
            INSERT INTO positions (
              symbol,
              name,
              quantity,
              available_quantity,
              cost_price,
              opened_at,
              updated_at,
              note
            ) VALUES (
              :symbol,
              :name,
              :quantity,
              :available_quantity,
              :cost_price,
              :opened_at,
              :updated_at,
              :note
            )
            """,
            self._to_row(persisted),
        )
        self.connection.commit()
        return persisted

    def update(self, position: PositionInput, *, now: datetime) -> Position:
        position = PositionInput.model_validate(position)
        if self.get(position.symbol) is None:
            raise MissingPositionError(f"position not found: {position.symbol}")

        persisted = self._with_updated_at(position, now)
        self.connection.execute(
            """
            UPDATE positions
            SET
              name = :name,
              quantity = :quantity,
              available_quantity = :available_quantity,
              cost_price = :cost_price,
              opened_at = :opened_at,
              updated_at = :updated_at,
              note = :note
            WHERE symbol = :symbol
            """,
            self._to_row(persisted),
        )
        self.connection.commit()
        return persisted

    def remove(self, symbol: str) -> None:
        cursor = self.connection.execute(
            "DELETE FROM positions WHERE symbol = ?",
            (symbol,),
        )
        if cursor.rowcount == 0:
            self.connection.rollback()
            raise MissingPositionError(f"position not found: {symbol}")
        self.connection.commit()

    def get(self, symbol: str) -> Position | None:
        row = self.connection.execute(
            """
            SELECT
              symbol,
              name,
              quantity,
              available_quantity,
              cost_price,
              opened_at,
              updated_at,
              note
            FROM positions
            WHERE symbol = ?
            """,
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list(self) -> list[Position]:
        rows = self.connection.execute(
            """
            SELECT
              symbol,
              name,
              quantity,
              available_quantity,
              cost_price,
              opened_at,
              updated_at,
              note
            FROM positions
            ORDER BY symbol
            """
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def import_csv(self, path: Path, *, now: datetime) -> list[Position]:
        positions = self._read_csv_positions(path)
        persisted = [self._with_updated_at(position, now) for position in positions]

        with self.connection:
            self.connection.execute("DELETE FROM positions")
            self.connection.executemany(
                """
                INSERT INTO positions (
                  symbol,
                  name,
                  quantity,
                  available_quantity,
                  cost_price,
                  opened_at,
                  updated_at,
                  note
                ) VALUES (
                  :symbol,
                  :name,
                  :quantity,
                  :available_quantity,
                  :cost_price,
                  :opened_at,
                  :updated_at,
                  :note
                )
                """,
                [self._to_row(position) for position in persisted],
            )

        return self.list()

    def _read_csv_positions(self, path: Path) -> list[PositionInput]:
        positions: list[PositionInput] = []
        seen_symbols: dict[str, int] = {}
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row_number, row in enumerate(reader, start=2):
                try:
                    position = PositionInput.model_validate(row)
                except ValidationError as exc:
                    raise ValueError(f"invalid position row {row_number}") from exc
                if position.symbol in seen_symbols:
                    first_row = seen_symbols[position.symbol]
                    raise ValueError(
                        f"duplicate symbol {position.symbol} at row {row_number}; "
                        f"first seen at row {first_row}"
                    )
                seen_symbols[position.symbol] = row_number
                positions.append(position)
        return positions

    def _with_updated_at(self, position: PositionInput, now: datetime) -> Position:
        data = position.model_dump()
        data["updated_at"] = now
        return Position.model_validate(data)

    def _to_row(self, position: Position) -> dict[str, object]:
        data = position.model_dump()
        data["opened_at"] = position.opened_at.isoformat()
        data["updated_at"] = position.updated_at.isoformat()
        return data

    def _from_row(self, row: sqlite3.Row) -> Position:
        return Position.model_validate(dict(row))
