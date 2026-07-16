from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from quantitative_trading.recommendation.models import Recommendation


def recommendation_condition_fingerprint(recommendation: Recommendation) -> str:
    material_conditions = recommendation.model_dump(
        mode="json",
        include={
            "reason",
            "risk",
            "position_constraint",
            "condition_context",
            "instrument",
        },
    )
    canonical = json.dumps(
        material_conditions,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def with_recommendation_identity(
    recommendation: Recommendation,
    *,
    trade_date: date,
    period_start: datetime,
    plan_version: str | int | None,
) -> Recommendation:
    if period_start.tzinfo is None or period_start.utcoffset() is None:
        raise ValueError("recommendation period_start must be timezone-aware")
    condition_fingerprint = recommendation_condition_fingerprint(recommendation)
    cycle_minute = period_start.minute - period_start.minute % 3
    decision_cycle = period_start.replace(
        minute=cycle_minute,
        second=0,
        microsecond=0,
    ).isoformat()
    dedup_key = "|".join(
        (
            trade_date.isoformat(),
            decision_cycle,
            recommendation.symbol,
            recommendation.action.value,
            recommendation.plan_id or "no-plan",
            f"v{plan_version if plan_version is not None else 'none'}",
            condition_fingerprint,
        )
    )
    identity_digest = hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()
    return recommendation.model_copy(
        update={
            "recommendation_id": f"rec-{identity_digest[:32]}",
            "decision_cycle": decision_cycle,
            "plan_version": plan_version,
            "condition_fingerprint": condition_fingerprint,
            "dedup_key": dedup_key,
        }
    )
