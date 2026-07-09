from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.planning.models import TradingPlan, TradingPlanStatus


GENERATED_AT = datetime(2026, 7, 8, 7, 5, tzinfo=UTC)
VALID_UNTIL = datetime(2026, 7, 9, 7, 0, tzinfo=UTC)
LEDGER_UPDATED_AT = datetime(2026, 7, 8, 6, 55, tzinfo=UTC)


def plan_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "plan_id": "plan-20260709",
        "trading_day": date(2026, 7, 9),
        "generated_at": GENERATED_AT,
        "valid_until": VALID_UNTIL,
        "universe_snapshot_id": 1,
        "account_snapshot_id": 2,
        "ledger_max_updated_at": LEDGER_UPDATED_AT,
        "watch_symbols": ["600000", "000001"],
        "holding_symbols": ["600000"],
        "key_levels": {
            "600000": {"support": 9.7, "resistance": 10.4},
            "000001": {"support": 12.5},
        },
        "candidate_actions": {
            "600000": ["hold", "reduce"],
            "000001": ["watch"],
        },
        "invalid_if": {
            "600000": ["breaks support"],
            "000001": ["liquidity weakens"],
        },
        "warnings": ["manual ledger snapshot is required for real holdings"],
        "status": TradingPlanStatus.ACTIVE,
    }
    data.update(overrides)
    return data


def test_trading_plan_round_trips_through_json() -> None:
    plan = TradingPlan(**plan_data())

    restored = TradingPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan
    assert restored.status is TradingPlanStatus.ACTIVE
    assert restored.watch_symbols == ["600000", "000001"]
    assert restored.key_levels["600000"]["support"] == 9.7
    assert restored.candidate_actions["600000"] == ["hold", "reduce"]
    assert restored.invalid_if["000001"] == ["liquidity weakens"]


@pytest.mark.parametrize(
    "field",
    ["generated_at", "valid_until", "ledger_max_updated_at"],
)
def test_trading_plan_rejects_naive_datetimes(field: str) -> None:
    with pytest.raises(ValidationError, match=f"{field} must be timezone-aware"):
        TradingPlan(**plan_data(**{field: datetime(2026, 7, 8, 7, 5)}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("watch_symbols", ["BAD"]),
        ("holding_symbols", ["60000A"]),
        ("key_levels", {"SH600000": {"support": 9.7}}),
        ("candidate_actions", {"6000000": ["watch"]}),
        ("invalid_if", {"abc123": ["invalid symbol"]}),
    ],
)
def test_trading_plan_rejects_invalid_symbol_collections(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match="six-digit A-share symbol"):
        TradingPlan(**plan_data(**{field: value}))


@pytest.mark.parametrize("level_value", [float("nan"), float("inf")])
def test_trading_plan_rejects_non_finite_key_levels(level_value: float) -> None:
    with pytest.raises(ValidationError, match="must be finite"):
        TradingPlan(**plan_data(key_levels={"600000": {"support": level_value}}))


def test_trading_plan_requires_positive_snapshot_references() -> None:
    with pytest.raises(ValidationError):
        TradingPlan(**plan_data(universe_snapshot_id=0))

    with pytest.raises(ValidationError):
        TradingPlan(**plan_data(account_snapshot_id=0))


def test_trading_plan_accepts_missing_optional_account_and_ledger_reference() -> None:
    plan = TradingPlan(
        **plan_data(account_snapshot_id=None, ledger_max_updated_at=None),
    )

    assert plan.account_snapshot_id is None
    assert plan.ledger_max_updated_at is None
