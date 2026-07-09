from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo


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
