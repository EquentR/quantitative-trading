from datetime import UTC, datetime

from quantitative_trading.planning.service import (
    plan_valid_until,
    require_latest_ledger_alignment,
)


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
