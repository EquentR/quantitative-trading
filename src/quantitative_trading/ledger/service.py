from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from quantitative_trading.ledger.models import Position, PositionInput
from quantitative_trading.ledger.repository import PositionRepository


def current_time() -> datetime:
    return datetime.now(UTC)


class ReadOnlyLedgerService:
    def __init__(self, repository: PositionRepository) -> None:
        self.repository = repository

    def get_position(self, symbol: str) -> Position | None:
        return self.repository.get(symbol)

    def list_positions(self) -> list[Position]:
        return self.repository.list()


class LedgerService(ReadOnlyLedgerService):
    def add_position(self, position: PositionInput, *, now: datetime | None = None) -> Position:
        return self.repository.add(position, now=now or current_time())

    def update_position(self, position: PositionInput, *, now: datetime | None = None) -> Position:
        return self.repository.update(position, now=now or current_time())

    def remove_position(self, symbol: str) -> None:
        self.repository.remove(symbol)

    def import_csv(self, path: Path, *, now: datetime | None = None) -> list[Position]:
        return self.repository.import_csv(path, now=now or current_time())
