from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.account.models import (
    AccountSnapshot,
    AccountSnapshotStatus,
    PositionValuation,
    PositionValuationStatus,
)


NOW = datetime(2026, 7, 7, 2, 0, tzinfo=UTC)


def valuation_data(**overrides):
    data = {
        "symbol": "600000",
        "name": "浦发银行",
        "quantity": 1000,
        "available_quantity": 800,
        "cost_price": 9.5,
        "position_cost": 9500,
        "current_price": 10.5,
        "market_value": 10500,
        "floating_pnl": 1000,
        "floating_pnl_pct": 1000 / 9500,
        "ledger_updated_at": NOW,
        "quote_data_time": NOW,
        "quote_fetched_at": NOW,
        "status": PositionValuationStatus.OK,
        "warning": "",
    }
    data.update(overrides)
    return data


def snapshot_data(**overrides):
    data = {
        "cash_balance": 50000,
        "net_principal": 50000,
        "market_value": 10500,
        "position_cost": 9500,
        "floating_pnl": 1000,
        "floating_pnl_pct": 1000 / 9500,
        "total_assets": 60500,
        "total_pnl": 10500,
        "total_pnl_pct": 10500 / 50000,
        "position_ratio": 10500 / 60500,
        "available_buying_cash": 50000,
        "positions": [PositionValuation(**valuation_data())],
        "status": AccountSnapshotStatus.OK,
        "warnings": [],
        "created_at": NOW,
    }
    data.update(overrides)
    return data


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
    with pytest.raises(ValidationError, match="ledger_updated_at must be timezone-aware"):
        PositionValuation(**valuation_data(ledger_updated_at=datetime(2026, 7, 7, 2, 0)))


def test_position_valuation_rejects_available_quantity_above_quantity() -> None:
    with pytest.raises(ValidationError, match="available_quantity cannot exceed quantity"):
        PositionValuation(**valuation_data(quantity=1000, available_quantity=1001))


@pytest.mark.parametrize(
    ("missing_field", "message"),
    [
        ("current_price", "ok valuation requires current_price"),
        ("market_value", "ok valuation requires market_value"),
        ("floating_pnl", "ok valuation requires floating_pnl"),
        ("quote_data_time", "ok valuation requires quote_data_time"),
        ("quote_fetched_at", "ok valuation requires quote_fetched_at"),
    ],
)
def test_ok_position_valuation_rejects_missing_required_valuation_fields(
    missing_field: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        PositionValuation(**valuation_data(**{missing_field: None}))


@pytest.mark.parametrize(
    "status",
    [PositionValuationStatus.FAILED, PositionValuationStatus.STALE],
)
def test_unusable_position_valuation_requires_warning(status: PositionValuationStatus) -> None:
    with pytest.raises(ValidationError, match=f"{status.value} valuation requires warning"):
        PositionValuation(
            **valuation_data(
                current_price=None,
                market_value=None,
                floating_pnl=None,
                floating_pnl_pct=None,
                quote_data_time=None,
                quote_fetched_at=None,
                status=status,
                warning="",
            )
        )


@pytest.mark.parametrize(
    ("missing_field", "message"),
    [
        ("cash_balance", "ok snapshot requires cash_balance"),
        ("net_principal", "ok snapshot requires net_principal"),
        ("market_value", "ok snapshot requires market_value"),
        ("position_cost", "ok snapshot requires position_cost"),
        ("floating_pnl", "ok snapshot requires floating_pnl"),
        ("total_assets", "ok snapshot requires total_assets"),
        ("total_pnl", "ok snapshot requires total_pnl"),
        ("available_buying_cash", "ok snapshot requires available_buying_cash"),
    ],
)
def test_ok_account_snapshot_rejects_missing_required_summary_fields(
    missing_field: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        AccountSnapshot(**snapshot_data(**{missing_field: None}))
