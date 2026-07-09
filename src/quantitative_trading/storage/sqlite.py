from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from quantitative_trading.config import Settings


POSITIONS_SCHEMA_SQL = """
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


CASH_ACCOUNT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cash_account (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  cash_balance REAL NOT NULL CHECK (cash_balance >= 0),
  total_transfer_in REAL NOT NULL CHECK (total_transfer_in >= 0),
  total_transfer_out REAL NOT NULL CHECK (total_transfer_out >= 0),
  updated_at TEXT NOT NULL,
  CHECK (total_transfer_in >= total_transfer_out)
);
"""


CASH_TRANSACTIONS_SCHEMA_SQL = """
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
"""


ACCOUNT_SNAPSHOTS_SCHEMA_SQL = """
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


WATCH_PINNED_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS watch_pinned (
  symbol TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL,
  rank INTEGER NOT NULL CHECK (rank >= 1),
  plan_enabled INTEGER NOT NULL CHECK (plan_enabled IN (0, 1)),
  source TEXT NOT NULL CHECK (source IN ('manual', 'synced', 'manual_synced')),
  note TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  CHECK (symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]')
);
"""


DATASOURCE_CREDENTIALS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS datasource_credentials (
  provider TEXT PRIMARY KEY NOT NULL,
  encrypted_secret TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('configured', 'missing', 'invalid')),
  last_checked_at TEXT,
  last_error TEXT,
  updated_at TEXT NOT NULL
);
"""


UNIVERSE_SNAPSHOTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS universe_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


MARKET_INPUT_SNAPSHOTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS market_input_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  universe_snapshot_id INTEGER NOT NULL,
  data_time TEXT,
  fetched_at TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY (universe_snapshot_id) REFERENCES universe_snapshots(id)
);
"""


TRADING_PLANS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trading_plans (
  plan_id TEXT PRIMARY KEY NOT NULL,
  trading_day TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  valid_until TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'expired', 'stale')),
  payload_json TEXT NOT NULL
);
"""


RECOMMENDATIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recommendations (
  recommendation_id TEXT PRIMARY KEY NOT NULL,
  symbol TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'add', 'reduce', 'hold', 'watch', 'avoid')),
  data_time TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  CHECK (symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]')
);
"""


AUDIT_LOGS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_logs (
  audit_id TEXT PRIMARY KEY NOT NULL,
  event_type TEXT NOT NULL,
  recommendation_id TEXT,
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


NOTIFICATIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notifications (
  notification_id TEXT PRIMARY KEY NOT NULL,
  recommendation_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('unread', 'read', 'feedback_recorded')),
  data_time TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  CHECK (symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]')
);
"""


EXECUTION_FEEDBACK_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS execution_feedback (
  feedback_id TEXT PRIMARY KEY NOT NULL,
  recommendation_id TEXT NOT NULL,
  executed INTEGER NOT NULL CHECK (executed IN (0, 1)),
  execution_price REAL CHECK (execution_price IS NULL OR execution_price > 0),
  execution_quantity INTEGER CHECK (execution_quantity IS NULL OR execution_quantity > 0),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


API_AUTH_STATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS api_auth_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  password_hash TEXT,
  token_secret TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


SCHEDULER_STATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
  interval_seconds INTEGER NOT NULL CHECK (interval_seconds >= 1),
  run_on_start INTEGER NOT NULL CHECK (run_on_start IN (0, 1)),
  last_started_at TEXT,
  last_finished_at TEXT,
  last_status TEXT,
  last_reason TEXT,
  last_error TEXT,
  last_snapshot_id INTEGER,
  updated_at TEXT NOT NULL
);
"""


SCHEMA_STATEMENTS = [
    POSITIONS_SCHEMA_SQL,
    CASH_ACCOUNT_SCHEMA_SQL,
    CASH_TRANSACTIONS_SCHEMA_SQL,
    ACCOUNT_SNAPSHOTS_SCHEMA_SQL,
    WATCH_PINNED_SCHEMA_SQL,
    DATASOURCE_CREDENTIALS_SCHEMA_SQL,
    UNIVERSE_SNAPSHOTS_SCHEMA_SQL,
    MARKET_INPUT_SNAPSHOTS_SCHEMA_SQL,
    TRADING_PLANS_SCHEMA_SQL,
    RECOMMENDATIONS_SCHEMA_SQL,
    AUDIT_LOGS_SCHEMA_SQL,
    NOTIFICATIONS_SCHEMA_SQL,
    EXECUTION_FEEDBACK_SCHEMA_SQL,
    API_AUTH_STATE_SCHEMA_SQL,
    SCHEDULER_STATE_SCHEMA_SQL,
]

SCHEMA_SQL = "\n\n".join(SCHEMA_STATEMENTS)


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
