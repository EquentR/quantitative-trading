import sqlite3
import stat

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


def test_connect_restricts_database_and_parent_permissions(tmp_path) -> None:
    database_dir = tmp_path / "local-data"
    database_dir.mkdir(mode=0o755)
    database_path = database_dir / "app.db"
    database_path.touch(mode=0o644)
    database_dir.chmod(0o755)
    database_path.chmod(0o644)

    with connect(Settings(database_path=database_path)) as connection:
        connection.execute("SELECT 1")

    assert stat.S_IMODE(database_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600


def insert_position(
    connection: sqlite3.Connection,
    **overrides: object,
) -> None:
    data = {
        "symbol": "600000",
        "name": "Ping An Bank",
        "quantity": 1000,
        "available_quantity": 800,
        "cost_price": 9.5,
        "opened_at": "2026-07-06",
        "updated_at": "2026-07-06T15:00:00+08:00",
    }
    data.update(overrides)

    connection.execute(
        """
        INSERT INTO positions (
          symbol,
          name,
          quantity,
          available_quantity,
          cost_price,
          opened_at,
          updated_at
        ) VALUES (
          :symbol,
          :name,
          :quantity,
          :available_quantity,
          :cost_price,
          :opened_at,
          :updated_at
        )
        """,
        data,
    )


def insert_cash_account(
    connection: sqlite3.Connection,
    **overrides: object,
) -> None:
    data = {
        "id": 1,
        "cash_balance": 50000.0,
        "total_transfer_in": 50000.0,
        "total_transfer_out": 0.0,
        "updated_at": "2026-07-07T09:00:00+08:00",
    }
    data.update(overrides)

    connection.execute(
        """
        INSERT INTO cash_account (
          id,
          cash_balance,
          total_transfer_in,
          total_transfer_out,
          updated_at
        ) VALUES (
          :id,
          :cash_balance,
          :total_transfer_in,
          :total_transfer_out,
          :updated_at
        )
        """,
        data,
    )


def insert_cash_transaction(
    connection: sqlite3.Connection,
    **overrides: object,
) -> None:
    data = {
        "type": "transfer_in",
        "amount": 1000.0,
        "cash_before": 50000.0,
        "cash_after": 51000.0,
        "occurred_at": "2026-07-07T09:30:00+08:00",
    }
    data.update(overrides)

    columns = list(data)
    placeholders = [f":{column}" for column in columns]
    connection.execute(
        f"""
        INSERT INTO cash_transactions (
          {", ".join(columns)}
        ) VALUES (
          {", ".join(placeholders)}
        )
        """,
        data,
    )


def insert_account_snapshot(
    connection: sqlite3.Connection,
    omitted: set[str] | None = None,
    **overrides: object,
) -> None:
    data = {
        "status": "ok",
        "created_at": "2026-07-07T15:05:00+08:00",
        "cash_account_updated_at": "2026-07-07T09:00:00+08:00",
        "ledger_max_updated_at": "2026-07-07T15:00:00+08:00",
        "market_value": 25000.0,
        "total_assets": 75000.0,
        "total_pnl": 1000.0,
        "position_ratio": 0.33,
        "payload_json": "{}",
    }
    data.update(overrides)
    for field in omitted or set():
        data.pop(field)

    columns = list(data)
    placeholders = [f":{column}" for column in columns]
    connection.execute(
        f"""
        INSERT INTO account_snapshots (
          {", ".join(columns)}
        ) VALUES (
          {", ".join(placeholders)}
        )
        """,
        data,
    )


def insert_quote_snapshot(
    connection: sqlite3.Connection,
    **overrides: object,
) -> None:
    data = {
        "symbol": "600000",
        "status": "ok",
        "data_time": "2026-07-12T14:30:00+08:00",
        "fetched_at": "2026-07-12T14:30:03+08:00",
        "source": "akshare",
        "payload_json": "{}",
    }
    data.update(overrides)

    connection.execute(
        """
        INSERT INTO quote_snapshots (
          symbol,
          status,
          data_time,
          fetched_at,
          source,
          payload_json
        ) VALUES (
          :symbol,
          :status,
          :data_time,
          :fetched_at,
          :source,
          :payload_json
        )
        """,
        data,
    )


def insert_trading_plan(
    connection: sqlite3.Connection,
    omitted: set[str] | None = None,
    **overrides: object,
) -> None:
    data = {
        "plan_id": "plan-20260709",
        "trading_day": "2026-07-09",
        "generated_at": "2026-07-08T15:05:00+08:00",
        "valid_until": "2026-07-09T15:00:00+08:00",
        "status": "active",
        "payload_json": "{}",
    }
    data.update(overrides)
    for field in omitted or set():
        data.pop(field)

    columns = list(data)
    placeholders = [f":{column}" for column in columns]
    connection.execute(
        f"""
        INSERT INTO trading_plans (
          {", ".join(columns)}
        ) VALUES (
          {", ".join(placeholders)}
        )
        """,
        data,
    )


@pytest.mark.parametrize(
    ("table_name", "expected_columns"),
    [
        (
            "positions",
            [
                "symbol",
                "name",
                "quantity",
                "available_quantity",
                "cost_price",
                "opened_at",
                "updated_at",
                "note",
            ],
        ),
        (
            "cash_account",
            [
                "id",
                "cash_balance",
                "total_transfer_in",
                "total_transfer_out",
                "updated_at",
            ],
        ),
        (
            "cash_transactions",
            [
                "id",
                "type",
                "amount",
                "cash_before",
                "cash_after",
                "occurred_at",
                "note",
            ],
        ),
        (
            "account_snapshots",
            [
                "id",
                "status",
                "created_at",
                "cash_account_updated_at",
                "ledger_max_updated_at",
                "market_value",
                "total_assets",
                "total_pnl",
                "position_ratio",
                "payload_json",
            ],
        ),
        (
            "trading_plans",
            [
                "plan_id",
                "trading_day",
                "generated_at",
                "valid_until",
                "status",
                "payload_json",
            ],
        ),
        (
            "audit_logs",
            [
                "audit_id",
                "event_type",
                "recommendation_id",
                "created_at",
                "payload_json",
            ],
        ),
        (
            "notifications",
                [
                    "notification_id",
                    "dedup_key",
                    "recommendation_id",
                "symbol",
                "action",
                "status",
                "data_time",
                "created_at",
                "payload_json",
                ],
            ),
            (
                "smtp_settings",
                [
                    "id",
                    "host",
                    "port",
                    "username",
                    "password",
                    "sender",
                    "recipient",
                    "security",
                    "enabled",
                    "updated_at",
                ],
            ),
            (
                "email_deliveries",
                [
                    "delivery_id",
                    "notification_id",
                    "dedup_key",
                    "recipient",
                    "subject",
                    "body",
                    "payload_json",
                    "status",
                    "attempt_count",
                    "next_attempt_at",
                    "lease_expires_at",
                    "last_error",
                    "sent_at",
                    "created_at",
                    "updated_at",
                ],
            ),
        (
            "execution_feedback",
            [
                "feedback_id",
                "recommendation_id",
                "executed",
                "execution_price",
                "execution_quantity",
                "note",
                "created_at",
                "payload_json",
            ],
        ),
        (
            "scheduler_state",
            [
                "id",
                "enabled",
                "interval_seconds",
                "run_on_start",
                "last_started_at",
                "last_finished_at",
                "last_status",
                "last_reason",
                "last_error",
                "last_snapshot_id",
                "last_task_type",
                "last_plan_id",
                "last_recommendation_ids",
                "updated_at",
            ],
        ),
    ],
)
def test_migrate_creates_expected_table_columns(
    tmp_path,
    table_name: str,
    expected_columns: list[str],
) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()

    assert [column["name"] for column in columns] == expected_columns


def test_migrate_creates_watch_pinned_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")
    with connect(settings) as connection:
        migrate(connection)
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'watch_pinned'"
        ).fetchone()
    assert row is not None


def test_migrate_creates_datasource_credentials_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")
    with connect(settings) as connection:
        migrate(connection)
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'datasource_credentials'"
        ).fetchone()
    assert row is not None


def test_migrate_creates_universe_snapshots_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")
    with connect(settings) as connection:
        migrate(connection)
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'universe_snapshots'"
        ).fetchone()
    assert row is not None


def test_migrate_twice_creates_quote_snapshots_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")

    with connect(settings) as connection:
        migrate(connection)
        migrate(connection)
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'quote_snapshots'"
        ).fetchone()

    assert row is not None


def test_migrate_creates_quote_snapshots_required_columns(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute("PRAGMA table_info(quote_snapshots)").fetchall()

    assert [
        (column["name"], column["type"], column["notnull"], column["pk"])
        for column in columns
    ] == [
        ("id", "INTEGER", 0, 1),
        ("symbol", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0),
        ("data_time", "TEXT", 0, 0),
        ("fetched_at", "TEXT", 1, 0),
        ("source", "TEXT", 1, 0),
        ("payload_json", "TEXT", 1, 0),
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "unknown"},
        {"symbol": "60000"},
        {"symbol": "６０００００"},
        {"symbol": "٦٠٠٠٠٠"},
    ],
)
def test_quote_snapshots_reject_invalid_status_and_symbols(
    tmp_path,
    overrides: dict[str, object],
) -> None:
    settings = Settings(database_path=tmp_path / "app.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_quote_snapshot(connection, **overrides)


def test_migrate_twice_creates_quote_snapshots_symbol_id_index(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")

    with connect(settings) as connection:
        migrate(connection)
        migrate(connection)
        columns = connection.execute(
            "PRAGMA index_xinfo(idx_quote_snapshots_symbol_id)"
        ).fetchall()

    assert [
        (column["name"], column["desc"])
        for column in columns
        if column["key"] == 1
    ] == [("symbol", 0), ("id", 1)]


def test_migrate_creates_market_input_snapshots_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")
    with connect(settings) as connection:
        migrate(connection)
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'market_input_snapshots'"
        ).fetchone()
    assert row is not None


def test_migrate_creates_trading_plans_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")
    with connect(settings) as connection:
        migrate(connection)
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'trading_plans'"
        ).fetchone()
    assert row is not None


def test_connection_enforces_foreign_keys(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")

    with connect(settings) as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert foreign_keys == 1


@pytest.mark.parametrize(
    "symbol",
    ["60000", "6000000", "SH600000", "abcdef", "60000A"],
)
def test_positions_reject_invalid_symbol(tmp_path, symbol: str) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_position(connection, symbol=symbol)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", -1),
        ("available_quantity", -1),
        ("available_quantity", 1001),
        ("cost_price", 0),
        ("cost_price", -1.0),
    ],
)
def test_positions_enforce_quantity_and_cost_constraints(
    tmp_path,
    field: str,
    value: object,
) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_position(connection, **{field: value})


@pytest.mark.parametrize("field", ["name", "opened_at", "updated_at"])
def test_positions_reject_null_required_fields(tmp_path, field: str) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_position(connection, **{field: None})


def test_positions_default_note_to_empty_string(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")

    with connect(settings) as connection:
        migrate(connection)
        insert_position(connection)

        row = connection.execute(
            "SELECT note FROM positions WHERE symbol = ?",
            ("600000",),
        ).fetchone()

    assert row["note"] == ""


def test_cash_account_rejects_non_singleton_id(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_cash_account(connection, id=2)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cash_balance", -1.0),
        ("total_transfer_in", -1.0),
        ("total_transfer_out", -1.0),
    ],
)
def test_cash_account_rejects_negative_amounts(
    tmp_path,
    field: str,
    value: float,
) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_cash_account(connection, **{field: value})


def test_cash_account_rejects_null_updated_at(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_cash_account(connection, updated_at=None)


def test_cash_account_rejects_transfer_out_above_transfer_in(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_cash_account(
                connection,
                cash_balance=0.0,
                total_transfer_in=1000.0,
                total_transfer_out=1000.01,
            )


def test_cash_transactions_reject_invalid_type(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_cash_transaction(connection, type="dividend")


@pytest.mark.parametrize("amount", [0.0, -1.0])
def test_cash_transactions_reject_non_positive_amount(
    tmp_path,
    amount: float,
) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_cash_transaction(connection, amount=amount)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cash_before", -1.0),
        ("cash_after", -1.0),
    ],
)
def test_cash_transactions_reject_negative_balances(
    tmp_path,
    field: str,
    value: float,
) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_cash_transaction(connection, **{field: value})


def test_cash_transactions_reject_null_occurred_at(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_cash_transaction(connection, occurred_at=None)


def test_cash_transactions_default_note_to_empty_string(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)
        insert_cash_transaction(connection)

        row = connection.execute(
            "SELECT note FROM cash_transactions WHERE id = ?",
            (1,),
        ).fetchone()

    assert row["note"] == ""


@pytest.mark.parametrize("field", ["status", "created_at", "payload_json"])
def test_account_snapshots_reject_missing_required_fields(
    tmp_path,
    field: str,
) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_account_snapshot(connection, omitted={field})


@pytest.mark.parametrize("field", ["status", "created_at", "payload_json"])
def test_account_snapshots_reject_null_required_fields(
    tmp_path,
    field: str,
) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_account_snapshot(connection, **{field: None})


def test_account_snapshots_accept_valid_snapshot(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)
        insert_account_snapshot(connection)

        row = connection.execute(
            "SELECT status, payload_json FROM account_snapshots WHERE id = ?",
            (1,),
        ).fetchone()

    assert row["status"] == "ok"
    assert row["payload_json"] == "{}"


def test_migrate_creates_api_auth_state_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")

    with connect(settings) as connection:
        migrate(connection)
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'api_auth_state'"
        ).fetchone()

    assert row is not None


def test_migrate_creates_scheduler_state_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")

    with connect(settings) as connection:
        migrate(connection)
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'scheduler_state'"
        ).fetchone()

    assert row is not None


def test_trading_plans_reject_duplicate_plan_id(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "planning.db")

    with connect(settings) as connection:
        migrate(connection)
        insert_trading_plan(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_trading_plan(connection)


@pytest.mark.parametrize(
    "field",
    ["plan_id", "trading_day", "generated_at", "valid_until", "status", "payload_json"],
)
def test_trading_plans_reject_null_required_fields(tmp_path, field: str) -> None:
    settings = Settings(database_path=tmp_path / "planning.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_trading_plan(connection, **{field: None})


@pytest.mark.parametrize("status", ["ok", "unknown", ""])
def test_trading_plans_reject_unknown_status(tmp_path, status: str) -> None:
    settings = Settings(database_path=tmp_path / "planning.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_trading_plan(connection, status=status)


@pytest.mark.parametrize(
    "status", ["draft", "active", "superseded", "expired", "stale"]
)
def test_trading_plans_accept_full_lifecycle_status(tmp_path, status: str) -> None:
    settings = Settings(database_path=tmp_path / f"planning-{status}.db")

    with connect(settings) as connection:
        migrate(connection)
        insert_trading_plan(connection, status=status)


def test_migrate_expands_legacy_trading_plan_status_constraint(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "legacy-plan.db")
    with connect(settings) as connection:
        connection.execute(
            """
            CREATE TABLE trading_plans (
              plan_id TEXT PRIMARY KEY NOT NULL,
              trading_day TEXT NOT NULL,
              generated_at TEXT NOT NULL,
              valid_until TEXT NOT NULL,
              status TEXT NOT NULL CHECK (status IN ('active', 'expired', 'stale')),
              payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO trading_plans
              (plan_id, trading_day, generated_at, valid_until, status, payload_json)
            VALUES ('legacy', '2026-07-09', '2026-07-08T07:00:00+00:00',
                    '2026-07-09T15:00:00+08:00', 'active', '{}')
            """
        )
        connection.commit()

        migrate(connection)
        insert_trading_plan(
            connection,
            plan_id="replacement",
            status="superseded",
        )
        connection.commit()

        rows = connection.execute(
            "SELECT plan_id, status FROM trading_plans ORDER BY plan_id"
        ).fetchall()

    assert [(row["plan_id"], row["status"]) for row in rows] == [
        ("legacy", "active"),
        ("replacement", "superseded"),
    ]


def test_migrate_creates_market_decision_workflow_tables(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "market-decision.db")
    expected = {
        "market_capture_runs",
        "market_capture_results",
        "daily_bars",
        "history_snapshots",
        "history_snapshot_members",
        "daily_money_flows",
        "money_flow_snapshots",
        "money_flow_snapshot_members",
        "minute_bars",
        "intraday_strength_snapshots",
    }

    with connect(settings) as connection:
        migrate(connection)
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

    assert expected <= {row["name"] for row in rows}


def test_migrate_adds_market_capture_observability_columns_to_existing_db(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "legacy-market-runs.db")
    with connect(settings) as connection:
        connection.execute(
            """
            CREATE TABLE market_capture_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL UNIQUE,
              workflow_type TEXT NOT NULL,
              trade_date TEXT NOT NULL,
              period_start TEXT,
              period_end TEXT,
              idempotency_key TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              requested_symbols INTEGER NOT NULL DEFAULT 0,
              processed_symbols INTEGER NOT NULL DEFAULT 0,
              provider_calls INTEGER NOT NULL DEFAULT 0,
              rows_received INTEGER NOT NULL DEFAULT 0,
              rows_written INTEGER NOT NULL DEFAULT 0,
              warning_count INTEGER NOT NULL DEFAULT 0,
              failure_count INTEGER NOT NULL DEFAULT 0,
              error_summary TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            INSERT INTO market_capture_runs (
              run_id, workflow_type, trade_date, idempotency_key, status, started_at
            ) VALUES (
              'legacy-run', 'close', '2026-07-13', 'close:2026-07-13',
              'succeeded', '2026-07-13T07:15:00+00:00'
            )
            """
        )

        migrate(connection)
        migrate(connection)
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(market_capture_runs)"
            ).fetchall()
        }
        row = connection.execute(
            "SELECT * FROM market_capture_runs WHERE run_id='legacy-run'"
        ).fetchone()

    expected = {
        "provider_duration_ms",
        "cleaned_rows",
        "plan_count",
        "recommendation_count",
        "notification_count",
        "email_outbox_count",
        "retry_count",
    }
    assert expected <= columns
    assert {name: row[name] for name in expected} == {
        "provider_duration_ms": 0.0,
        "cleaned_rows": 0,
        "plan_count": 0,
        "recommendation_count": 0,
        "notification_count": 0,
        "email_outbox_count": 0,
        "retry_count": 0,
    }
