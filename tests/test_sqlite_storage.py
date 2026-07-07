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


def test_migrate_creates_positions_table(tmp_path) -> None:
    db_path = tmp_path / "ledger.db"
    settings = Settings(database_path=db_path)

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute("PRAGMA table_info(positions)").fetchall()

    column_names = [column["name"] for column in columns]
    assert column_names == [
        "symbol",
        "name",
        "quantity",
        "available_quantity",
        "cost_price",
        "opened_at",
        "updated_at",
        "note",
    ]


def test_migrate_creates_cash_account_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute("PRAGMA table_info(cash_account)").fetchall()

    assert [column["name"] for column in columns] == [
        "id",
        "cash_balance",
        "total_transfer_in",
        "total_transfer_out",
        "updated_at",
    ]


def test_migrate_creates_cash_transactions_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute("PRAGMA table_info(cash_transactions)").fetchall()

    assert [column["name"] for column in columns] == [
        "id",
        "type",
        "amount",
        "cash_before",
        "cash_after",
        "occurred_at",
        "note",
    ]


def test_migrate_creates_account_snapshots_table(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "account.db")

    with connect(settings) as connection:
        migrate(connection)
        columns = connection.execute("PRAGMA table_info(account_snapshots)").fetchall()

    assert [column["name"] for column in columns] == [
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
    ]


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
