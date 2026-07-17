from __future__ import annotations

from datetime import date

from quantitative_trading.recommendation.models import Recommendation


def notification_canonical_key(
    recommendation: Recommendation,
    *,
    trade_date: date,
    plan_version: str | int | None,
    condition_fingerprint: str,
) -> str:
    return ":".join(
        (
            "notification-v2",
            trade_date.isoformat(),
            recommendation.symbol,
            recommendation.action.value,
            recommendation.plan_id or "no-plan",
            f"v{plan_version}" if plan_version is not None else "no-version",
            condition_fingerprint,
        )
    )
