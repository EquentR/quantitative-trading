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

CREATE TABLE IF NOT EXISTS cash_account (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  cash_balance REAL NOT NULL CHECK (cash_balance >= 0),
  total_transfer_in REAL NOT NULL CHECK (total_transfer_in >= 0),
  total_transfer_out REAL NOT NULL CHECK (total_transfer_out >= 0),
  updated_at TEXT NOT NULL,
  CHECK (total_transfer_in >= total_transfer_out)
);

CREATE TABLE IF NOT EXISTS cash_transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  amount REAL NOT NULL CHECK (amount > 0),
  cash_before REAL NOT NULL CHECK (cash_before >= 0),
  cash_after REAL NOT NULL CHECK (cash_after >= 0),
  occurred_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  CHECK (type IN ('initial_deposit', 'transfer_in', 'transfer_out', 'cash_adjustment'))
);

CREATE TABLE IF NOT EXISTS account_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  cash_account_updated_at TEXT,
  ledger_max_updated_at TEXT,
  market_value REAL,
  total_assets REAL,
  total_pnl REAL,
  position_ratio REAL,
  payload_json TEXT NOT NULL
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
