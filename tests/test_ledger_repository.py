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
    with csv_path.open("w", newline="", encoding="utf-8") as file:
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
        writer.writerow(
            {
                "symbol": "600000",
                "name": "浦发银行",
                "quantity": "1000",
                "available_quantity": "800",
                "cost_price": "9.5",
                "opened_at": "2026-07-06",
                "note": "首批台账",
            }
        )
        writer.writerow(
            {
                "symbol": "600001",
                "name": "邯郸钢铁",
                "quantity": "100",
                "available_quantity": "200",
                "cost_price": "8.8",
                "opened_at": "2026-07-06",
                "note": "invalid",
            }
        )

    with pytest.raises(ValueError):
        repository.import_csv(csv_path, now=fixed_now())

    assert repository.list() == []
