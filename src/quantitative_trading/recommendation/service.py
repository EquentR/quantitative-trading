from __future__ import annotations

from datetime import datetime
from typing import Any

from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.risk.models import RiskDecision
from quantitative_trading.strategy.models import StrategySignal


DEFAULT_POSITION_LIMIT_TEXT = "single <= 30%, total <= 80%"


def build_recommendation(
    signal: StrategySignal,
    risk_decision: RiskDecision,
    *,
    recommendation_id: str,
    name: str,
    position_context: dict[str, Any],
    account_context: dict[str, Any],
    price_context: dict[str, Any],
    valid_until: datetime,
    data_time: datetime,
    fetched_at: datetime | None = None,
    position_limit: str = DEFAULT_POSITION_LIMIT_TEXT,
    created_at: datetime | None = None,
    run_id: int | str | None = None,
    market_input_snapshot_id: int | None = None,
    plan_id: str | None = None,
    data_references: dict[str, dict[str, Any]] | None = None,
    data_quality: dict[str, Any] | None = None,
    position_constraint: dict[str, Any] | None = None,
) -> Recommendation:
    final_action = RecommendationAction(risk_decision.action.value)

    return Recommendation(
        recommendation_id=recommendation_id,
        symbol=signal.symbol,
        name=name,
        action=final_action,
        confidence=signal.confidence,
        position_context=position_context,
        account_context=account_context,
        price_context=price_context,
        reason=signal.human_reason,
        risk={
            "position_limit": position_limit,
            "invalid_if": signal.invalid_if,
            "notes": risk_decision.reasons,
            "machine_reason": signal.machine_reason,
            "original_action": risk_decision.original_action.value,
        },
        valid_until=valid_until,
        data_time=data_time,
        fetched_at=fetched_at or data_time,
        created_at=created_at or data_time,
        run_id=run_id,
        market_input_snapshot_id=market_input_snapshot_id,
        plan_id=plan_id,
        data_references=data_references or {},
        data_quality=data_quality
        or {"overall": "degraded", "warnings": ["input trace unavailable"]},
        position_constraint=position_constraint or {"position_limit": position_limit},
    )
