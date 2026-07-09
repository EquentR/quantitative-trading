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
