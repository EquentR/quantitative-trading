from datetime import UTC, datetime

from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.ledger.service import LedgerService, ReadOnlyLedgerService
from quantitative_trading.storage.sqlite import connect, migrate


def make_services(tmp_path) -> tuple[LedgerService, ReadOnlyLedgerService, object]:
    settings = Settings(database_path=tmp_path / "ledger.db")
    connection_cm = connect(settings)
    connection = connection_cm.__enter__()
    migrate(connection)
    repository = PositionRepository(connection)
    return LedgerService(repository), ReadOnlyLedgerService(repository), connection_cm


def valid_input() -> PositionInput:
    return PositionInput.model_validate(
        {
            "symbol": "600000",
            "name": "浦发银行",
            "quantity": 1000,
            "available_quantity": 800,
            "cost_price": 9.5,
            "opened_at": "2026-07-06",
            "note": "",
        }
    )


def test_ledger_service_adds_position_with_current_time(tmp_path) -> None:
    service, _, connection_cm = make_services(tmp_path)
    try:
        now = datetime(2026, 7, 6, 10, 30, tzinfo=UTC)

        position = service.add_position(valid_input(), now=now)

        assert position.updated_at == now
        assert service.get_position("600000") is not None
    finally:
        connection_cm.__exit__(None, None, None)


def test_read_only_service_can_list_positions(tmp_path) -> None:
    service, read_only, connection_cm = make_services(tmp_path)
    try:
        service.add_position(valid_input(), now=datetime(2026, 7, 6, 10, 30, tzinfo=UTC))

        positions = read_only.list_positions()

        assert [position.symbol for position in positions] == ["600000"]
    finally:
        connection_cm.__exit__(None, None, None)


def test_read_only_service_exposes_no_mutation_methods(tmp_path) -> None:
    _, read_only, connection_cm = make_services(tmp_path)
    try:
        assert not hasattr(read_only, "repository")
        for method_name in ["add_position", "update_position", "remove_position", "import_csv"]:
            assert not hasattr(read_only, method_name)
    finally:
        connection_cm.__exit__(None, None, None)
