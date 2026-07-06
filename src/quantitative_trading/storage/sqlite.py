from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from quantitative_trading.config import Settings


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity >= 0),
  available_quantity INTEGER NOT NULL CHECK (available_quantity >= 0),
  cost_price REAL NOT NULL CHECK (cost_price > 0),
  opened_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  CHECK (symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'),
  CHECK (available_quantity <= quantity)
);
"""


@contextmanager
def connect(settings: Settings) -> Iterator[sqlite3.Connection]:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.commit()
