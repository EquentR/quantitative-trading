from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from quantitative_trading.planning.models import PlanCondition


@dataclass(frozen=True)
class ConditionEvaluationItem:
    condition_id: str
    matched: bool
    required: bool
    status: Literal["matched", "unmatched", "missing", "invalid"]
    rationale: str


@dataclass(frozen=True)
class ConditionEvaluation:
    matched: bool
    items: list[ConditionEvaluationItem]
    machine_reasons: list[str]


def plan_valid_until(trading_day: datetime, *, timezone: str) -> datetime:
    tz = ZoneInfo(timezone)
    local_day = trading_day.astimezone(tz).date()
    return datetime.combine(local_day, time(15, 0), tzinfo=tz)


def require_latest_ledger_alignment(
    *,
    latest_ledger_updated_at: datetime | None,
    plan_ledger_updated_at: datetime | None,
) -> str:
    if latest_ledger_updated_at is None or plan_ledger_updated_at is None:
        return "ledger_missing"
    if latest_ledger_updated_at > plan_ledger_updated_at:
        return "ledger_changed"
    return "aligned"


def evaluate_plan_conditions(
    conditions: list[PlanCondition],
    metrics: dict[str, Any],
) -> ConditionEvaluation:
    items: list[ConditionEvaluationItem] = []
    machine_reasons: list[str] = []
    required_matched = True

    for condition in conditions:
        if condition.metric not in metrics or metrics[condition.metric] is None:
            item = ConditionEvaluationItem(
                condition_id=condition.condition_id,
                matched=False,
                required=condition.required,
                status="missing",
                rationale=condition.rationale,
            )
        else:
            try:
                matched = _compare_condition(
                    metrics[condition.metric],
                    condition.operator,
                    condition.threshold,
                )
            except (TypeError, ValueError):
                item = ConditionEvaluationItem(
                    condition_id=condition.condition_id,
                    matched=False,
                    required=condition.required,
                    status="invalid",
                    rationale=condition.rationale,
                )
            else:
                item = ConditionEvaluationItem(
                    condition_id=condition.condition_id,
                    matched=matched,
                    required=condition.required,
                    status="matched" if matched else "unmatched",
                    rationale=condition.rationale,
                )

        items.append(item)
        machine_reasons.append(
            f"plan_condition:{condition.condition_id}:{item.status}"
        )
        if condition.required and not item.matched:
            required_matched = False

    return ConditionEvaluation(
        matched=required_matched,
        items=items,
        machine_reasons=machine_reasons,
    )


def _compare_condition(value: Any, operator: str, threshold: Any) -> bool:
    if operator == "eq":
        return bool(value == threshold)
    if isinstance(value, bool) or isinstance(threshold, bool):
        raise TypeError("ordered condition operands cannot be booleans")
    if not isinstance(value, int | float) or not isinstance(threshold, int | float):
        raise TypeError("ordered condition operands must be numeric")
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    raise ValueError("unsupported plan condition operator")
