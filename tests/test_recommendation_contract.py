from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.recommendation.service import build_recommendation
from quantitative_trading.risk.models import RiskDecision
from quantitative_trading.strategy.models import StrategyAction, StrategySignal


VALID_UNTIL = datetime(2026, 7, 9, 7, 0, tzinfo=UTC)
DATA_TIME = datetime(2026, 7, 9, 2, 30, tzinfo=UTC)


def recommendation_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "recommendation_id": "rec-1",
        "symbol": "600000",
        "name": "浦发银行",
        "action": RecommendationAction.WATCH,
        "confidence": "medium",
        "position_context": {
            "source": "manual_ledger",
            "ledger_updated_at": "2026-07-08T10:00:00+00:00",
        },
        "account_context": {"source": "manual_cash_account"},
        "price_context": {
            "current_price": 10.0,
            "change_pct": 1.0,
            "key_levels": {"support": 9.7},
        },
        "reason": ["站上短期均线"],
        "risk": {"position_limit": "single <= 30%", "invalid_if": ["跌破 9.7"], "notes": []},
        "valid_until": VALID_UNTIL,
        "data_time": DATA_TIME,
    }
    data.update(overrides)
    return data


def test_recommendation_requires_risk_and_data_time() -> None:
    recommendation = Recommendation(
        recommendation_id="rec-1",
        symbol="600000",
        name="浦发银行",
        action=RecommendationAction.WATCH,
        confidence="medium",
        position_context={
            "source": "manual_ledger",
            "ledger_updated_at": "2026-07-08T10:00:00+00:00",
        },
        account_context={"source": "manual_cash_account"},
        price_context={"current_price": 10.0, "change_pct": 1.0, "key_levels": {"support": 9.7}},
        reason=["站上短期均线"],
        risk={"position_limit": "single <= 30%", "invalid_if": ["跌破 9.7"], "notes": []},
        valid_until=datetime(2026, 7, 9, 7, 0, tzinfo=UTC),
        data_time=datetime(2026, 7, 9, 2, 30, tzinfo=UTC),
    )

    assert recommendation.action is RecommendationAction.WATCH
    assert recommendation.risk["invalid_if"] == ["跌破 9.7"]


@pytest.mark.parametrize(
    "action",
    [
        RecommendationAction.BUY,
        RecommendationAction.ADD,
        RecommendationAction.HOLD,
        RecommendationAction.WATCH,
    ],
)
def test_recommendation_rejects_missing_invalid_if_for_constructive_outputs(
    action: RecommendationAction,
) -> None:
    with pytest.raises(ValidationError, match="invalid_if"):
        Recommendation(**recommendation_data(action=action, risk={"position_limit": "single <= 30%"}))

    with pytest.raises(ValidationError, match="invalid_if"):
        Recommendation(
            **recommendation_data(
                action=action,
                risk={"position_limit": "single <= 30%", "invalid_if": []},
            )
        )


def test_recommendation_rejects_naive_valid_until_and_data_time() -> None:
    with pytest.raises(ValidationError, match="valid_until must be timezone-aware"):
        Recommendation(**recommendation_data(valid_until=datetime(2026, 7, 9, 7, 0)))

    with pytest.raises(ValidationError, match="data_time must be timezone-aware"):
        Recommendation(**recommendation_data(data_time=datetime(2026, 7, 9, 2, 30)))


def test_sell_recommendation_copies_present_invalid_if_list() -> None:
    caller_invalid_if = ["重新站回止损位"]
    recommendation = Recommendation(
        **recommendation_data(
            action=RecommendationAction.SELL,
            risk={
                "position_limit": "single <= 30%",
                "invalid_if": caller_invalid_if,
                "notes": [],
            },
        )
    )

    recommendation.risk["invalid_if"].append("人工复核后再调整")

    assert caller_invalid_if == ["重新站回止损位"]
    assert recommendation.risk["invalid_if"] == ["重新站回止损位", "人工复核后再调整"]


def test_risk_downgrade_overrides_stronger_strategy_action() -> None:
    signal = StrategySignal(
        symbol="600000",
        action=StrategyAction.BUY,
        confidence="high",
        machine_reason=["breakout_observation"],
        human_reason=["放量突破观察位"],
        invalid_if=["跌破 10.4"],
    )
    risk_decision = RiskDecision(
        allowed=False,
        original_action=StrategyAction.BUY,
        action=StrategyAction.WATCH,
        reasons=["总仓位 81.00% 高于上限 80.00%，禁止新增买入"],
    )

    recommendation = build_recommendation(
        signal,
        risk_decision,
        recommendation_id="rec-risk-1",
        name="浦发银行",
        position_context={"source": "manual_ledger"},
        account_context={"source": "manual_cash_account", "position_ratio": 0.81},
        price_context={"current_price": 10.5, "key_levels": {"support": 10.4}},
        valid_until=VALID_UNTIL,
        data_time=DATA_TIME,
    )

    assert recommendation.action is RecommendationAction.WATCH
    assert "放量突破观察位" in recommendation.reason
    assert recommendation.risk["invalid_if"] == ["跌破 10.4"]
    assert recommendation.risk["notes"] == ["总仓位 81.00% 高于上限 80.00%，禁止新增买入"]


def test_recommendation_preserves_complete_decision_trace() -> None:
    signal = StrategySignal(
        symbol="600000",
        action=StrategyAction.BUY,
        confidence="high",
        machine_reason=["active_plan_gate_passed", "intraday_strength_confirmed"],
        human_reason=["收盘计划与盘中强弱共同确认"],
        invalid_if=["跌破计划支撑位"],
    )
    decision = RiskDecision(
        allowed=True,
        original_action=StrategyAction.BUY,
        action=StrategyAction.BUY,
        reasons=[],
    )

    recommendation = build_recommendation(
        signal,
        decision,
        recommendation_id="rec-trace-1",
        name="浦发银行",
        position_context={"source": "manual_ledger", "quantity": 0},
        account_context={"source": "manual_cash_account", "total_assets": 60000},
        price_context={"current_price": 10.5},
        valid_until=VALID_UNTIL,
        data_time=DATA_TIME,
        created_at=DATA_TIME,
        run_id=12,
        market_input_snapshot_id=34,
        plan_id="plan-20260709-v1",
        data_references={
            "ledger": {"snapshot_id": 2, "status": "complete"},
            "account": {"snapshot_id": 3, "status": "complete"},
            "quote": {"snapshot_id": 4, "status": "complete"},
            "history": {"snapshot_id": 5, "status": "complete"},
            "money_flow": {"snapshot_id": 6, "status": "complete"},
            "intraday": {"snapshot_id": 7, "status": "complete"},
            "plan": {"plan_id": "plan-20260709-v1", "status": "active"},
        },
        data_quality={"overall": "complete", "warnings": []},
        position_constraint={
            "suggested_quantity": 1000,
            "max_position_ratio": 0.30,
            "max_total_position_ratio": 0.80,
        },
    )

    assert recommendation.run_id == 12
    assert recommendation.market_input_snapshot_id == 34
    assert recommendation.plan_id == "plan-20260709-v1"
    assert recommendation.created_at == DATA_TIME
    assert recommendation.data_references["intraday"]["snapshot_id"] == 7
    assert recommendation.position_constraint["suggested_quantity"] == 1000


def test_recommendation_defaults_to_explicit_missing_data_references() -> None:
    recommendation = Recommendation(**recommendation_data())

    assert set(recommendation.data_references) == {
        "ledger",
        "account",
        "quote",
        "history",
        "money_flow",
        "intraday",
        "plan",
    }
    assert all(
        reference["status"] == "missing"
        for reference in recommendation.data_references.values()
    )
