from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from quantitative_trading.ledger.models import Position, PositionInput
from quantitative_trading.ledger.repository import PositionRepository


def current_time() -> datetime:
    return datetime.now(UTC)


class ReadOnlyLedgerService:
    def __init__(self, repository: PositionRepository) -> None:
        self._repository = repository

    def get_position(self, symbol: str) -> Position | None:
        return self._repository.get(symbol)

    def list_positions(self) -> list[Position]:
        return self._repository.list()


class LedgerService(ReadOnlyLedgerService):
    def add_position(self, position: PositionInput, *, now: datetime | None = None) -> Position:
        return self._repository.add(position, now=now or current_time())

    def update_position(self, position: PositionInput, *, now: datetime | None = None) -> Position:
        return self._repository.update(position, now=now or current_time())

    def remove_position(self, symbol: str) -> None:
        self._repository.remove(symbol)

    def replace_positions(
        self,
        positions: list[PositionInput],
        *,
        now: datetime | None = None,
    ) -> list[Position]:
        return self._repository.replace_all(positions, now=now or current_time())

    def import_csv(self, path: Path, *, now: datetime | None = None) -> list[Position]:
        return self._repository.import_csv(path, now=now or current_time())
