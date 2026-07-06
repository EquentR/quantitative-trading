from datetime import date, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.ledger.models import Position, PositionInput


def valid_position_input() -> dict[str, object]:
    return {
        "symbol": "600000",
        "name": "浦发银行",
        "quantity": 1000,
        "available_quantity": 800,
        "cost_price": 9.5,
        "opened_at": "2026-07-06",
        "note": "首批台账",
    }


def test_position_input_accepts_complete_valid_position() -> None:
    position = PositionInput.model_validate(valid_position_input())

    assert position.symbol == "600000"
    assert position.name == "浦发银行"
    assert position.quantity == 1000
    assert position.available_quantity == 800
    assert position.cost_price == 9.5
    assert position.opened_at == date(2026, 7, 6)
    assert position.note == "首批台账"


@pytest.mark.parametrize("symbol", ["60000", "6000000", "SH600000", "abcdef"])
def test_position_input_rejects_invalid_symbol(symbol: str) -> None:
    data = valid_position_input()
    data["symbol"] = symbol

    with pytest.raises(ValidationError):
        PositionInput.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("quantity", -1),
        ("available_quantity", -1),
        ("cost_price", 0),
        ("cost_price", -1.2),
    ],
)
def test_position_input_rejects_invalid_required_values(field: str, value: object) -> None:
    data = valid_position_input()
    data[field] = value

    with pytest.raises(ValidationError):
        PositionInput.model_validate(data)


def test_position_input_rejects_available_quantity_above_quantity() -> None:
    data = valid_position_input()
    data["quantity"] = 100
    data["available_quantity"] = 200

    with pytest.raises(ValidationError):
        PositionInput.model_validate(data)


def test_position_requires_timezone_aware_updated_at() -> None:
    data = valid_position_input()
    data["updated_at"] = datetime(2026, 7, 6, 10, 30)

    with pytest.raises(ValidationError):
        Position.model_validate(data)
