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
