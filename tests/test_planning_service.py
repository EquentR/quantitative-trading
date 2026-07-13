from datetime import UTC, datetime

from quantitative_trading.planning.service import (
    evaluate_plan_conditions,
    plan_valid_until,
    require_latest_ledger_alignment,
)
from quantitative_trading.planning.models import PlanCondition


def test_plan_valid_until_next_trading_day_close() -> None:
    trading_day = datetime(2026, 7, 9, 0, 0, tzinfo=UTC)
    valid_until = plan_valid_until(trading_day, timezone="Asia/Shanghai")
    assert valid_until.isoformat().endswith("15:00:00+08:00")


def test_ledger_alignment_rejects_stale_reference() -> None:
    latest = datetime(2026, 7, 9, 9, 20, tzinfo=UTC)
    referenced = datetime(2026, 7, 8, 15, 10, tzinfo=UTC)
    result = require_latest_ledger_alignment(
        latest_ledger_updated_at=latest,
        plan_ledger_updated_at=referenced,
    )
    assert result == "ledger_changed"


def test_ledger_alignment_rejects_missing_reference() -> None:
    assert (
        require_latest_ledger_alignment(
            latest_ledger_updated_at=None,
            plan_ledger_updated_at=datetime(2026, 7, 9, 9, 20, tzinfo=UTC),
        )
        == "ledger_missing"
    )
    assert (
        require_latest_ledger_alignment(
            latest_ledger_updated_at=datetime(2026, 7, 9, 9, 20, tzinfo=UTC),
            plan_ledger_updated_at=None,
        )
        == "ledger_missing"
    )


def test_ledger_alignment_accepts_current_reference() -> None:
    updated_at = datetime(2026, 7, 9, 9, 20, tzinfo=UTC)
    assert (
        require_latest_ledger_alignment(
            latest_ledger_updated_at=updated_at,
            plan_ledger_updated_at=updated_at,
        )
        == "aligned"
    )


def test_evaluate_plan_conditions_requires_every_required_condition() -> None:
    evaluation = evaluate_plan_conditions(
        [
            PlanCondition(
                condition_id="price-breakout",
                metric="current_price",
                operator="gte",
                threshold=10.4,
                rationale="突破压力位",
            ),
            PlanCondition(
                condition_id="strength",
                metric="intraday_strength",
                operator="eq",
                threshold="strong",
                rationale="分时强弱确认",
            ),
        ],
        {"current_price": 10.5, "intraday_strength": "strong"},
    )

    assert evaluation.matched is True
    assert [item.matched for item in evaluation.items] == [True, True]
    assert evaluation.machine_reasons == [
        "plan_condition:price-breakout:matched",
        "plan_condition:strength:matched",
    ]


def test_evaluate_plan_conditions_marks_missing_required_metric_unmatched() -> None:
    evaluation = evaluate_plan_conditions(
        [
            PlanCondition(
                condition_id="volume",
                metric="volume_ratio",
                operator="gte",
                threshold=1.5,
                rationale="量能放大",
            )
        ],
        {},
    )

    assert evaluation.matched is False
    assert evaluation.items[0].status == "missing"
    assert evaluation.machine_reasons == ["plan_condition:volume:missing"]


def test_evaluate_plan_conditions_does_not_block_on_optional_condition() -> None:
    evaluation = evaluate_plan_conditions(
        [
            PlanCondition(
                condition_id="flow",
                metric="money_flow_positive",
                operator="eq",
                threshold=True,
                required=False,
                rationale="资金流额外确认",
            )
        ],
        {"money_flow_positive": False},
    )

    assert evaluation.matched is True
    assert evaluation.items[0].matched is False
    assert evaluation.machine_reasons == ["plan_condition:flow:unmatched"]
