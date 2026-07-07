import csv
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import (
    DuplicatePositionError,
    MissingPositionError,
    PositionRepository,
)
from quantitative_trading.storage.sqlite import connect, migrate


@pytest.fixture
def repository(tmp_path) -> Iterator[PositionRepository]:
    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        migrate(connection)
        yield PositionRepository(connection)


def valid_input(symbol: str = "600000") -> PositionInput:
    return PositionInput.model_validate(
        {
            "symbol": symbol,
            "name": "浦发银行",
            "quantity": 1000,
            "available_quantity": 800,
            "cost_price": 9.5,
            "opened_at": "2026-07-06",
            "note": "首批台账",
        }
    )


def fixed_now() -> datetime:
    return datetime(2026, 7, 6, 10, 30, tzinfo=UTC)


def write_positions_csv(path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "symbol",
                "name",
                "quantity",
                "available_quantity",
                "cost_price",
                "opened_at",
                "note",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_csv_text(path, header: list[str], row: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerow(row)


def test_repository_adds_and_gets_position(repository: PositionRepository) -> None:
    repository.add(valid_input(), now=fixed_now())

    position = repository.get("600000")

    assert position is not None
    assert position.symbol == "600000"
    assert position.name == "浦发银行"
    assert position.quantity == 1000
    assert position.available_quantity == 800
    assert position.cost_price == 9.5
    assert position.opened_at.isoformat() == "2026-07-06"
    assert position.note == "首批台账"
    assert position.updated_at == fixed_now()


def test_repository_rejects_duplicate_add(repository: PositionRepository) -> None:
    repository.add(valid_input(), now=fixed_now())

    with pytest.raises(DuplicatePositionError):
        repository.add(valid_input(), now=fixed_now())


def test_repository_updates_existing_position(repository: PositionRepository) -> None:
    repository.add(valid_input(), now=fixed_now())
    updated = PositionInput.model_validate(
        {
            "symbol": "600000",
            "name": "浦发银行",
            "quantity": 1200,
            "available_quantity": 1000,
            "cost_price": 9.4,
            "opened_at": "2026-07-06",
            "note": "手动调整",
        }
    )

    repository.update(updated, now=fixed_now())

    position = repository.get("600000")
    assert position is not None
    assert position.quantity == 1200
    assert position.available_quantity == 1000
    assert position.cost_price == 9.4
    assert position.note == "手动调整"
    assert position.updated_at == fixed_now()


def test_repository_rejects_update_for_missing_position(
    repository: PositionRepository,
) -> None:
    with pytest.raises(MissingPositionError):
        repository.update(valid_input(), now=fixed_now())


def test_repository_removes_existing_position(repository: PositionRepository) -> None:
    repository.add(valid_input(), now=fixed_now())

    repository.remove("600000")

    assert repository.get("600000") is None


def test_repository_remove_missing_position_leaves_no_open_transaction(
    repository: PositionRepository,
) -> None:
    assert repository.connection.in_transaction is False

    with pytest.raises(MissingPositionError):
        repository.remove("600000")

    assert repository.connection.in_transaction is False


def test_repository_lists_positions_by_symbol(repository: PositionRepository) -> None:
    repository.add(valid_input("600001"), now=fixed_now())
    repository.add(valid_input("600000"), now=fixed_now())

    symbols = [position.symbol for position in repository.list()]

    assert symbols == ["600000", "600001"]


def test_repository_import_csv_is_atomic_when_a_row_is_invalid(
    tmp_path,
    repository: PositionRepository,
) -> None:
    csv_path = tmp_path / "positions.csv"
    write_positions_csv(
        csv_path,
        [
            {
                "symbol": "600000",
                "name": "浦发银行",
                "quantity": "1000",
                "available_quantity": "800",
                "cost_price": "9.5",
                "opened_at": "2026-07-06",
                "note": "首批台账",
            },
            {
                "symbol": "600001",
                "name": "邯郸钢铁",
                "quantity": "100",
                "available_quantity": "200",
                "cost_price": "8.8",
                "opened_at": "2026-07-06",
                "note": "invalid",
            },
        ],
    )

    with pytest.raises(ValueError):
        repository.import_csv(csv_path, now=fixed_now())

    assert repository.list() == []


def test_repository_import_csv_preserves_existing_positions_when_a_row_is_invalid(
    tmp_path,
    repository: PositionRepository,
) -> None:
    repository.add(valid_input("600010"), now=fixed_now())
    csv_path = tmp_path / "positions.csv"
    write_positions_csv(
        csv_path,
        [
            {
                "symbol": "600000",
                "name": "浦发银行",
                "quantity": "1000",
                "available_quantity": "800",
                "cost_price": "9.5",
                "opened_at": "2026-07-06",
                "note": "首批台账",
            },
            {
                "symbol": "600001",
                "name": "邯郸钢铁",
                "quantity": "100",
                "available_quantity": "200",
                "cost_price": "8.8",
                "opened_at": "2026-07-06",
                "note": "invalid",
            },
        ],
    )

    with pytest.raises(ValueError):
        repository.import_csv(csv_path, now=fixed_now())

    positions = repository.list()
    assert [position.symbol for position in positions] == ["600010"]
    assert positions[0].note == "首批台账"


def test_repository_import_csv_rejects_duplicate_symbols_before_writing(
    tmp_path,
    repository: PositionRepository,
) -> None:
    repository.add(valid_input("600010"), now=fixed_now())
    csv_path = tmp_path / "positions.csv"
    write_positions_csv(
        csv_path,
        [
            {
                "symbol": "600001",
                "name": "邯郸钢铁",
                "quantity": "100",
                "available_quantity": "100",
                "cost_price": "8.8",
                "opened_at": "2026-07-06",
                "note": "first row",
            },
            {
                "symbol": "600001",
                "name": "邯郸钢铁",
                "quantity": "200",
                "available_quantity": "200",
                "cost_price": "8.9",
                "opened_at": "2026-07-06",
                "note": "duplicate row",
            },
        ],
    )

    with pytest.raises(ValueError, match="duplicate symbol 600001.*row 3"):
        repository.import_csv(csv_path, now=fixed_now())

    positions = repository.list()
    assert [position.symbol for position in positions] == ["600010"]
    assert positions[0].note == "首批台账"


@pytest.mark.parametrize(
    ("header", "row"),
    [
        (
            [
                "symbol",
                "name",
                "quantity",
                "available_quantity",
                "cost_price",
                "opened_at",
                "note",
                "extra",
            ],
            ["600001", "邯郸钢铁", "100", "100", "8.8", "2026-07-06", "extra header", "x"],
        ),
        (
            ["symbol", "name", "quantity", "available_quantity", "cost_price", "opened_at"],
            ["600001", "邯郸钢铁", "100", "100", "8.8", "2026-07-06"],
        ),
        (
            [
                "name",
                "symbol",
                "quantity",
                "available_quantity",
                "cost_price",
                "opened_at",
                "note",
            ],
            ["邯郸钢铁", "600001", "100", "100", "8.8", "2026-07-06", "reordered"],
        ),
    ],
)
def test_repository_import_csv_rejects_non_exact_headers_before_writing(
    tmp_path,
    repository: PositionRepository,
    header: list[str],
    row: list[str],
) -> None:
    repository.add(valid_input("600010"), now=fixed_now())
    csv_path = tmp_path / "positions.csv"
    write_csv_text(csv_path, header, row)

    with pytest.raises(ValueError, match="CSV header must exactly match"):
        repository.import_csv(csv_path, now=fixed_now())

    positions = repository.list()
    assert [position.symbol for position in positions] == ["600010"]
    assert positions[0].note == "首批台账"


def test_repository_import_csv_replaces_existing_positions_and_returns_persisted(
    tmp_path,
    repository: PositionRepository,
) -> None:
    repository.add(valid_input("600010"), now=fixed_now())
    csv_path = tmp_path / "positions.csv"
    write_positions_csv(
        csv_path,
        [
            {
                "symbol": "600001",
                "name": "邯郸钢铁",
                "quantity": "100",
                "available_quantity": "100",
                "cost_price": "8.8",
                "opened_at": "2026-07-06",
                "note": "first row",
            },
            {
                "symbol": "600000",
                "name": "浦发银行",
                "quantity": "1000",
                "available_quantity": "800",
                "cost_price": "9.5",
                "opened_at": "2026-07-06",
                "note": "second row",
            },
        ],
    )

    imported = repository.import_csv(csv_path, now=fixed_now())

    assert [position.symbol for position in imported] == ["600000", "600001"]
    assert repository.get("600010") is None
    assert all(position.updated_at == fixed_now() for position in imported)
