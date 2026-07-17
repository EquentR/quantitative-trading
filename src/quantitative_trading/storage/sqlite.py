from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from quantitative_trading.config import Settings
from quantitative_trading.market.schema import MARKET_DECISION_SCHEMA_SQL
from quantitative_trading.notification.migration import (
    migrate_legacy_recommendation_notifications,
)


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


INSTRUMENTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS instruments (
  symbol TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL,
  exchange TEXT CHECK (exchange IS NULL OR exchange IN ('SH', 'SZ')),
  instrument_type TEXT NOT NULL CHECK (instrument_type IN ('a_share', 'etf', 'unknown')),
  settlement_cycle TEXT NOT NULL CHECK (settlement_cycle IN ('t0', 't1', 'unknown')),
  price_limit_ratio REAL CHECK (
    price_limit_ratio IS NULL OR (price_limit_ratio > 0 AND price_limit_ratio <= 1)
  ),
  listing_date TEXT,
  metadata_source TEXT NOT NULL,
  metadata_checked_at TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
  warnings_json TEXT NOT NULL,
  CHECK (symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'),
  CHECK (instrument_type = 'unknown' OR exchange IS NOT NULL),
  CHECK (instrument_type != 'a_share' OR settlement_cycle = 't1'),
  CHECK (instrument_type != 'unknown' OR settlement_cycle = 'unknown')
);
CREATE INDEX IF NOT EXISTS idx_instruments_active_symbol
ON instruments(is_active, symbol);
"""


INSTRUMENT_CATALOG_STATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS instrument_catalog_state (
  source TEXT PRIMARY KEY NOT NULL,
  last_attempt_at TEXT NOT NULL,
  last_success_at TEXT,
  data_trade_date TEXT,
  status TEXT NOT NULL CHECK (status IN ('complete', 'stale', 'failed')),
  last_error TEXT NOT NULL DEFAULT '',
  warnings_json TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL
);
"""


INSTRUMENT_PREVIEWS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS instrument_previews (
  preview_id TEXT PRIMARY KEY NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('eastmoney_watchlist', 'instrument_search')),
  query TEXT,
  items_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_instrument_previews_expires_at
ON instrument_previews(expires_at);
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


QUOTE_SNAPSHOTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS quote_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ok', 'partial', 'failed', 'stale')),
  data_time TEXT,
  fetched_at TEXT NOT NULL,
  source TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  CHECK (symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]')
);
CREATE INDEX IF NOT EXISTS idx_quote_snapshots_symbol_id
ON quote_snapshots(symbol, id DESC);
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
  status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'superseded', 'expired', 'stale')),
  payload_json TEXT NOT NULL
);
"""


RECOMMENDATIONS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recommendations (
  recommendation_id TEXT PRIMARY KEY NOT NULL,
  dedup_key TEXT,
  symbol TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('buy', 'sell', 'add', 'reduce', 'hold', 'watch', 'avoid')),
  condition_fingerprint TEXT,
  condition_fingerprint_version INTEGER CHECK (
    condition_fingerprint_version IS NULL OR condition_fingerprint_version >= 1
  ),
  audit_id TEXT,
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
  dedup_key TEXT,
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


NOTIFICATION_CANONICAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notification_canonical_groups (
  canonical_key TEXT PRIMARY KEY NOT NULL,
  notification_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE (canonical_key, notification_id),
  FOREIGN KEY (notification_id) REFERENCES notifications(notification_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS recommendation_notification_links (
  recommendation_id TEXT PRIMARY KEY NOT NULL,
  notification_id TEXT NOT NULL,
  canonical_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (recommendation_id) REFERENCES recommendations(recommendation_id) ON DELETE RESTRICT,
  FOREIGN KEY (notification_id) REFERENCES notifications(notification_id) ON DELETE RESTRICT,
  FOREIGN KEY (canonical_key, notification_id)
    REFERENCES notification_canonical_groups(canonical_key, notification_id)
    ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_recommendation_notification_links_notification
  ON recommendation_notification_links(notification_id);
"""


SMTP_SETTINGS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS smtp_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  host TEXT NOT NULL,
  port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
  username TEXT NOT NULL,
  password TEXT,
  sender TEXT NOT NULL,
  recipient TEXT NOT NULL,
  security TEXT NOT NULL CHECK (security IN ('none', 'starttls', 'ssl')),
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
  updated_at TEXT NOT NULL
);
"""


EMAIL_DELIVERIES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS email_deliveries (
  delivery_id TEXT PRIMARY KEY NOT NULL,
  notification_id TEXT,
  dedup_key TEXT NOT NULL UNIQUE,
  recipient TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'sending', 'retry', 'sent', 'dead')),
  attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
  next_attempt_at TEXT,
  lease_expires_at TEXT,
  last_error TEXT NOT NULL DEFAULT '',
  sent_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (notification_id) REFERENCES notifications(notification_id)
);
CREATE INDEX IF NOT EXISTS idx_email_deliveries_due
  ON email_deliveries(status, next_attempt_at, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_email_deliveries_notification
  ON email_deliveries(notification_id, created_at DESC);
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
  last_task_type TEXT,
  last_plan_id TEXT,
  last_recommendation_ids TEXT,
  overrun_count INTEGER NOT NULL DEFAULT 0 CHECK (overrun_count >= 0),
  skipped_count INTEGER NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
  updated_at TEXT NOT NULL
);
"""


SCHEMA_STATEMENTS = [
    POSITIONS_SCHEMA_SQL,
    CASH_ACCOUNT_SCHEMA_SQL,
    CASH_TRANSACTIONS_SCHEMA_SQL,
    ACCOUNT_SNAPSHOTS_SCHEMA_SQL,
    WATCH_PINNED_SCHEMA_SQL,
    INSTRUMENTS_SCHEMA_SQL,
    INSTRUMENT_CATALOG_STATE_SCHEMA_SQL,
    INSTRUMENT_PREVIEWS_SCHEMA_SQL,
    DATASOURCE_CREDENTIALS_SCHEMA_SQL,
    UNIVERSE_SNAPSHOTS_SCHEMA_SQL,
    QUOTE_SNAPSHOTS_SCHEMA_SQL,
    MARKET_INPUT_SNAPSHOTS_SCHEMA_SQL,
    MARKET_DECISION_SCHEMA_SQL,
    TRADING_PLANS_SCHEMA_SQL,
    RECOMMENDATIONS_SCHEMA_SQL,
    AUDIT_LOGS_SCHEMA_SQL,
    NOTIFICATIONS_SCHEMA_SQL,
    NOTIFICATION_CANONICAL_SCHEMA_SQL,
    SMTP_SETTINGS_SCHEMA_SQL,
    EMAIL_DELIVERIES_SCHEMA_SQL,
    EXECUTION_FEEDBACK_SCHEMA_SQL,
    API_AUTH_STATE_SCHEMA_SQL,
    SCHEDULER_STATE_SCHEMA_SQL,
]

SCHEMA_SQL = "\n\n".join(SCHEMA_STATEMENTS)


@contextmanager
def connect(settings: Settings) -> Iterator[sqlite3.Connection]:
    database_parent = settings.database_path.parent
    database_parent.mkdir(parents=True, exist_ok=True)
    if database_parent != Path("."):
        database_parent.chmod(0o700)
    connection = sqlite3.connect(settings.database_path)
    settings.database_path.chmod(0o600)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


def migrate(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_SQL)
        _disable_unverified_watch_items(connection)
        _ensure_market_capture_result_status_constraint(connection)
        _ensure_trading_plan_status_constraint(connection)
        _ensure_scheduler_state_columns(connection)
        _ensure_instrument_columns(connection)
        _ensure_instrument_catalog_state_columns(connection)
        _ensure_notification_columns(connection)
        _ensure_recommendation_columns(connection)
        _ensure_market_capture_run_columns(connection)
        migrate_legacy_recommendation_notifications(connection)
        _fail_duplicate_running_workflows(connection)
        _supersede_duplicate_active_plans(connection)
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedup_key
            ON notifications(dedup_key)
            WHERE dedup_key IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_market_capture_runs_running_workflow
            ON market_capture_runs(workflow_type)
            WHERE status = 'running'
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_recommendations_dedup_key
            ON recommendations(dedup_key)
            WHERE dedup_key IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_trading_plans_one_active_day
            ON trading_plans(trading_day)
            WHERE status = 'active'
            """
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _disable_unverified_watch_items(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE watch_pinned
        SET plan_enabled = 0
        WHERE plan_enabled = 1
          AND NOT EXISTS (
            SELECT 1
            FROM instruments
            WHERE instruments.symbol = watch_pinned.symbol
              AND instruments.is_active = 1
              AND instruments.instrument_type IN ('a_share', 'etf')
              AND instruments.settlement_cycle IN ('t0', 't1')
          )
        """
    )


def _ensure_market_capture_result_status_constraint(
    connection: sqlite3.Connection,
) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='market_capture_results'"
    ).fetchone()
    if row is None or "not_applicable" in str(row[0]):
        return
    connection.execute(
        "ALTER TABLE market_capture_results RENAME TO market_capture_results_legacy"
    )
    connection.execute(
        """
        CREATE TABLE market_capture_results (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          symbol TEXT NOT NULL CHECK (symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'),
          dataset TEXT NOT NULL CHECK (dataset IN ('quote','daily_bar','money_flow','minute_bar','intraday_strength')),
          status TEXT NOT NULL CHECK (status IN ('complete','degraded','failed','stale','not_applicable')),
          data_start TEXT,
          data_end TEXT,
          data_time TEXT,
          fetched_at TEXT NOT NULL,
          expected_rows INTEGER NOT NULL DEFAULT 0 CHECK (expected_rows >= 0),
          actual_rows INTEGER NOT NULL DEFAULT 0 CHECK (actual_rows >= 0),
          source TEXT NOT NULL,
          warning TEXT NOT NULL DEFAULT '',
          error_summary TEXT NOT NULL DEFAULT '',
          UNIQUE (run_id, symbol, dataset)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO market_capture_results (
          id, run_id, symbol, dataset, status, data_start, data_end, data_time,
          fetched_at, expected_rows, actual_rows, source, warning, error_summary
        )
        SELECT
          id, run_id, symbol, dataset, status, data_start, data_end, data_time,
          fetched_at, expected_rows, actual_rows, source, warning, error_summary
        FROM market_capture_results_legacy
        """
    )
    connection.execute("DROP TABLE market_capture_results_legacy")


def _fail_duplicate_running_workflows(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """SELECT run_id, workflow_type FROM market_capture_runs
           WHERE status='running'
           ORDER BY workflow_type, started_at DESC, id DESC"""
    ).fetchall()
    retained: set[str] = set()
    for row in rows:
        workflow_type = str(row["workflow_type"])
        if workflow_type not in retained:
            retained.add(workflow_type)
            continue
        connection.execute(
            """UPDATE market_capture_runs SET status='failed',
                 finished_at=started_at, failure_count=failure_count+1,
                 error_summary='duplicate running workflow lease repaired during migration'
               WHERE run_id=?""",
            (row["run_id"],),
        )


def _ensure_trading_plan_status_constraint(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'trading_plans'"
    ).fetchone()
    if row is None or "superseded" in str(row[0]):
        return

    connection.execute("ALTER TABLE trading_plans RENAME TO trading_plans_legacy")
    connection.execute(TRADING_PLANS_SCHEMA_SQL)
    connection.execute(
        """
        INSERT INTO trading_plans (
          plan_id, trading_day, generated_at, valid_until, status, payload_json
        )
        SELECT plan_id, trading_day, generated_at, valid_until, status, payload_json
        FROM trading_plans_legacy
        """
    )
    connection.execute("DROP TABLE trading_plans_legacy")


def _ensure_scheduler_state_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in connection.execute("PRAGMA table_info(scheduler_state)").fetchall()
    }
    columns = {
        "last_task_type": "TEXT",
        "last_plan_id": "TEXT",
        "last_recommendation_ids": "TEXT",
        "overrun_count": "INTEGER NOT NULL DEFAULT 0 CHECK (overrun_count >= 0)",
        "skipped_count": "INTEGER NOT NULL DEFAULT 0 CHECK (skipped_count >= 0)",
    }
    for name, column_type in columns.items():
        if name not in existing:
            connection.execute(
                f"ALTER TABLE scheduler_state ADD COLUMN {name} {column_type}"
            )


def _ensure_instrument_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(instruments)").fetchall()
    }
    if "listing_date" not in columns:
        connection.execute("ALTER TABLE instruments ADD COLUMN listing_date TEXT")


def _ensure_instrument_catalog_state_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(instrument_catalog_state)"
        ).fetchall()
    }
    if "warnings_json" not in existing:
        connection.execute(
            "ALTER TABLE instrument_catalog_state "
            "ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]'"
        )


def _ensure_notification_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in connection.execute("PRAGMA table_info(notifications)").fetchall()
    }
    if "dedup_key" not in existing:
        connection.execute("ALTER TABLE notifications ADD COLUMN dedup_key TEXT")


def _ensure_recommendation_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in connection.execute("PRAGMA table_info(recommendations)").fetchall()
    }
    for name in ("dedup_key", "condition_fingerprint", "audit_id"):
        if name not in existing:
            connection.execute(f"ALTER TABLE recommendations ADD COLUMN {name} TEXT")
    if "condition_fingerprint_version" not in existing:
        connection.execute(
            "ALTER TABLE recommendations ADD COLUMN condition_fingerprint_version INTEGER"
        )


def _supersede_duplicate_active_plans(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT rowid, trading_day, payload_json
        FROM trading_plans
        WHERE status = 'active'
        ORDER BY trading_day, generated_at DESC, rowid DESC
        """
    ).fetchall()
    retained_days: set[str] = set()
    for row in rows:
        trading_day = str(row["trading_day"])
        if trading_day not in retained_days:
            retained_days.add(trading_day)
            continue
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("trading plan payload must be a JSON object")
        payload["status"] = "superseded"
        connection.execute(
            """
            UPDATE trading_plans
            SET status = 'superseded', payload_json = ?
            WHERE rowid = ?
            """,
            (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                row["rowid"],
            ),
        )


def _ensure_market_capture_run_columns(connection: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in connection.execute("PRAGMA table_info(market_capture_runs)").fetchall()
    }
    columns = {
        "mode": "TEXT CHECK (mode IN ('decision','display_only'))",
        "effective_trade_date": "TEXT",
        "history_cutoff_date": "TEXT",
        "requested_symbol_scope_json": "TEXT NOT NULL DEFAULT '[]'",
        "lease_expires_at": "TEXT",
        "provider_duration_ms": "REAL NOT NULL DEFAULT 0 CHECK (provider_duration_ms >= 0)",
        "cleaned_rows": "INTEGER NOT NULL DEFAULT 0 CHECK (cleaned_rows >= 0)",
        "plan_count": "INTEGER NOT NULL DEFAULT 0 CHECK (plan_count >= 0)",
        "recommendation_count": "INTEGER NOT NULL DEFAULT 0 CHECK (recommendation_count >= 0)",
        "notification_count": "INTEGER NOT NULL DEFAULT 0 CHECK (notification_count >= 0)",
        "email_outbox_count": "INTEGER NOT NULL DEFAULT 0 CHECK (email_outbox_count >= 0)",
        "retry_count": "INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0)",
    }
    for name, column_type in columns.items():
        if name not in existing:
            connection.execute(
                f"ALTER TABLE market_capture_runs ADD COLUMN {name} {column_type}"
            )
