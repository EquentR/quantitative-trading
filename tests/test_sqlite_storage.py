import sqlite3

from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


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


def test_connection_enforces_foreign_keys(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")

    with connect(settings) as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert foreign_keys == 1
