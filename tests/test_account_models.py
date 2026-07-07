from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.account.models import (
    AccountSnapshot,
    AccountSnapshotStatus,
    PositionValuation,
    PositionValuationStatus,
)


def test_account_snapshot_accepts_cash_not_initialized_status_with_aware_created_at() -> None:
    snapshot = AccountSnapshot.model_validate(
        {
            "positions": [],
            "status": "cash_not_initialized",
            "warnings": ["cash account not initialized"],
            "created_at": "2026-07-07T09:00:00+08:00",
        }
    )

    assert snapshot.status is AccountSnapshotStatus.CASH_NOT_INITIALIZED
    assert snapshot.created_at.tzinfo is not None
    assert snapshot.cash_balance is None
    assert snapshot.market_value is None


def test_account_snapshot_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError, match="created_at must be timezone-aware"):
        AccountSnapshot(
            positions=[],
            status=AccountSnapshotStatus.OK,
            warnings=[],
            created_at=datetime(2026, 7, 7, 9, 0),
        )


def test_position_valuation_rejects_naive_datetimes() -> None:
    aware = datetime(2026, 7, 7, 2, 0, tzinfo=UTC)

    with pytest.raises(ValidationError, match="ledger_updated_at must be timezone-aware"):
        PositionValuation(
            symbol="600000",
            name="浦发银行",
            quantity=1000,
            available_quantity=800,
            cost_price=9.5,
            position_cost=9500,
            current_price=10.5,
            market_value=10500,
            floating_pnl=1000,
            floating_pnl_pct=1000 / 9500,
            ledger_updated_at=datetime(2026, 7, 7, 2, 0),
            quote_data_time=aware,
            quote_fetched_at=aware,
            status=PositionValuationStatus.OK,
        )
