MARKET_DECISION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS market_capture_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  workflow_type TEXT NOT NULL CHECK (workflow_type IN ('close','intraday','backfill','cleanup')),
  mode TEXT CHECK (mode IN ('decision','display_only')),
  trade_date TEXT NOT NULL,
  effective_trade_date TEXT,
  history_cutoff_date TEXT,
  period_start TEXT,
  period_end TEXT,
  requested_symbol_scope_json TEXT NOT NULL DEFAULT '[]',
  lease_expires_at TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('running','succeeded','degraded','failed')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  requested_symbols INTEGER NOT NULL DEFAULT 0 CHECK (requested_symbols >= 0),
  processed_symbols INTEGER NOT NULL DEFAULT 0 CHECK (processed_symbols >= 0),
  provider_calls INTEGER NOT NULL DEFAULT 0 CHECK (provider_calls >= 0),
  provider_duration_ms REAL NOT NULL DEFAULT 0 CHECK (provider_duration_ms >= 0),
  rows_received INTEGER NOT NULL DEFAULT 0 CHECK (rows_received >= 0),
  rows_written INTEGER NOT NULL DEFAULT 0 CHECK (rows_written >= 0),
  cleaned_rows INTEGER NOT NULL DEFAULT 0 CHECK (cleaned_rows >= 0),
  plan_count INTEGER NOT NULL DEFAULT 0 CHECK (plan_count >= 0),
  recommendation_count INTEGER NOT NULL DEFAULT 0 CHECK (recommendation_count >= 0),
  notification_count INTEGER NOT NULL DEFAULT 0 CHECK (notification_count >= 0),
  email_outbox_count INTEGER NOT NULL DEFAULT 0 CHECK (email_outbox_count >= 0),
  retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
  warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
  failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
  error_summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS market_capture_results (
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
);

CREATE TABLE IF NOT EXISTS daily_bars (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  adjustment TEXT NOT NULL CHECK (adjustment = 'forward'),
  open REAL NOT NULL CHECK (open > 0),
  high REAL NOT NULL CHECK (high > 0),
  low REAL NOT NULL CHECK (low > 0),
  close REAL NOT NULL CHECK (close > 0),
  volume REAL NOT NULL CHECK (volume >= 0),
  amount REAL NOT NULL CHECK (amount >= 0),
  source TEXT NOT NULL,
  source_updated_at TEXT,
  fetched_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE (symbol, trade_date, adjustment, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_daily_bars_current
  ON daily_bars(symbol, adjustment, trade_date, id);

CREATE TABLE IF NOT EXISTS daily_money_flows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  main_net_amount REAL NOT NULL,
  main_net_pct REAL NOT NULL,
  super_large_net_amount REAL NOT NULL,
  super_large_net_pct REAL NOT NULL,
  large_net_amount REAL NOT NULL,
  large_net_pct REAL NOT NULL,
  medium_net_amount REAL NOT NULL,
  medium_net_pct REAL NOT NULL,
  small_net_amount REAL NOT NULL,
  small_net_pct REAL NOT NULL,
  source TEXT NOT NULL,
  source_updated_at TEXT,
  fetched_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  UNIQUE (symbol, trade_date, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_money_flows_current
  ON daily_money_flows(symbol, trade_date, id);

CREATE TABLE IF NOT EXISTS history_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  adjustment TEXT NOT NULL CHECK (adjustment = 'forward'),
  data_start TEXT,
  data_end TEXT,
  row_count INTEGER NOT NULL CHECK (row_count >= 0),
  content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
  status TEXT NOT NULL CHECK (status IN ('complete','degraded','failed','stale')),
  warning TEXT NOT NULL DEFAULT '',
  fetched_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_snapshots_symbol_id
  ON history_snapshots(symbol ASC, id DESC);
CREATE TABLE IF NOT EXISTS history_snapshot_members (
  snapshot_id INTEGER NOT NULL REFERENCES history_snapshots(id) ON DELETE RESTRICT,
  sequence INTEGER NOT NULL CHECK (sequence >= 0),
  daily_bar_id INTEGER NOT NULL REFERENCES daily_bars(id) ON DELETE RESTRICT,
  PRIMARY KEY (snapshot_id, sequence),
  UNIQUE (snapshot_id, daily_bar_id)
);

CREATE TABLE IF NOT EXISTS money_flow_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  data_start TEXT,
  data_end TEXT,
  row_count INTEGER NOT NULL CHECK (row_count >= 0),
  content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
  status TEXT NOT NULL CHECK (status IN ('complete','degraded','failed','stale')),
  warning TEXT NOT NULL DEFAULT '',
  fetched_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS money_flow_snapshot_members (
  snapshot_id INTEGER NOT NULL REFERENCES money_flow_snapshots(id) ON DELETE RESTRICT,
  sequence INTEGER NOT NULL CHECK (sequence >= 0),
  money_flow_id INTEGER NOT NULL REFERENCES daily_money_flows(id) ON DELETE RESTRICT,
  PRIMARY KEY (snapshot_id, sequence),
  UNIQUE (snapshot_id, money_flow_id)
);

CREATE TABLE IF NOT EXISTS minute_bars (
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  minute TEXT NOT NULL,
  open REAL NOT NULL CHECK (open > 0),
  high REAL NOT NULL CHECK (high > 0),
  low REAL NOT NULL CHECK (low > 0),
  close REAL NOT NULL CHECK (close > 0),
  volume REAL NOT NULL CHECK (volume >= 0),
  amount REAL NOT NULL CHECK (amount >= 0),
  source TEXT NOT NULL,
  source_updated_at TEXT,
  fetched_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY (symbol, minute)
);
CREATE INDEX IF NOT EXISTS idx_minute_bars_date
  ON minute_bars(symbol, trade_date, minute);

CREATE TABLE IF NOT EXISTS intraday_strength_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  label TEXT NOT NULL CHECK (label IN ('strong','neutral','weak')),
  confidence TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
  degraded INTEGER NOT NULL CHECK (degraded IN (0,1)),
  direction_sum INTEGER NOT NULL,
  minute_volume_ratio REAL,
  last_minute TEXT,
  data_coverage REAL NOT NULL CHECK (data_coverage >= 0 AND data_coverage <= 1),
  rule_version TEXT NOT NULL,
  source TEXT NOT NULL,
  data_time TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intraday_strength_latest
  ON intraday_strength_snapshots(symbol, id);
"""
