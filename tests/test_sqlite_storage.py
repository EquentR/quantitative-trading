import sqlite3

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


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


@pytest.mark.parametrize("status", ["draft", "ok", ""])
def test_trading_plans_reject_unknown_status(tmp_path, status: str) -> None:
    settings = Settings(database_path=tmp_path / "planning.db")

    with connect(settings) as connection:
        migrate(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_trading_plan(connection, status=status)
