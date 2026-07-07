from datetime import datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.cash.models import CashAccount, CashTransaction, CashTransactionType


def test_cash_account_derives_net_principal() -> None:
    account = CashAccount.model_validate(
        {
            "cash_balance": 48000,
            "total_transfer_in": 50000,
            "total_transfer_out": 2000,
            "updated_at": "2026-07-07T09:00:00+08:00",
        }
    )

    assert account.cash_balance == 48000
    assert account.net_principal == 48000


@pytest.mark.parametrize(
    "field",
    ["cash_balance", "total_transfer_in", "total_transfer_out"],
)
def test_cash_account_rejects_negative_amounts(field: str) -> None:
    data = {
        "cash_balance": 48000,
        "total_transfer_in": 50000,
        "total_transfer_out": 2000,
        "updated_at": "2026-07-07T09:00:00+08:00",
    }
    data[field] = -1

    with pytest.raises(ValidationError):
        CashAccount.model_validate(data)


def test_cash_account_rejects_transfer_out_above_transfer_in() -> None:
    with pytest.raises(ValidationError):
        CashAccount.model_validate(
            {
                "cash_balance": 1000,
                "total_transfer_in": 1000,
                "total_transfer_out": 1001,
                "updated_at": "2026-07-07T09:00:00+08:00",
            }
        )


def test_cash_account_requires_timezone_aware_updated_at() -> None:
    with pytest.raises(ValidationError):
        CashAccount.model_validate(
            {
                "cash_balance": 1000,
                "total_transfer_in": 1000,
                "total_transfer_out": 0,
                "updated_at": datetime(2026, 7, 7, 9, 0),
            }
        )


def test_cash_account_model_dump_includes_net_principal() -> None:
    account = CashAccount.model_validate(
        {
            "cash_balance": 15000,
            "total_transfer_in": 20000,
            "total_transfer_out": 5000,
            "updated_at": "2026-07-07T09:00:00+08:00",
        }
    )

    assert account.model_dump()["net_principal"] == 15000


def test_cash_transaction_accepts_timezone_aware_time() -> None:
    transaction = CashTransaction.model_validate(
        {
            "id": 1,
            "type": "transfer_in",
            "amount": 1000,
            "cash_before": 5000,
            "cash_after": 6000,
            "occurred_at": "2026-07-07T09:00:00+08:00",
            "note": "bank transfer in",
        }
    )

    assert transaction.type is CashTransactionType.TRANSFER_IN
    assert isinstance(transaction.occurred_at, datetime)


def test_cash_transaction_rejects_invalid_type() -> None:
    with pytest.raises(ValidationError):
        CashTransaction.model_validate(
            {
                "type": "dividend",
                "amount": 1000,
                "cash_before": 5000,
                "cash_after": 6000,
                "occurred_at": "2026-07-07T09:00:00+08:00",
            }
        )


def test_cash_transaction_rejects_zero_amount() -> None:
    with pytest.raises(ValidationError):
        CashTransaction.model_validate(
            {
                "type": "cash_adjustment",
                "amount": 0,
                "cash_before": 5000,
                "cash_after": 5000,
                "occurred_at": "2026-07-07T09:00:00+08:00",
                "note": "no change",
            }
        )


@pytest.mark.parametrize("field", ["cash_before", "cash_after"])
def test_cash_transaction_rejects_negative_balances(field: str) -> None:
    data = {
        "type": "cash_adjustment",
        "amount": 100,
        "cash_before": 5000,
        "cash_after": 5100,
        "occurred_at": "2026-07-07T09:00:00+08:00",
    }
    data[field] = -1

    with pytest.raises(ValidationError):
        CashTransaction.model_validate(data)


def test_cash_transaction_requires_timezone_aware_occurred_at() -> None:
    with pytest.raises(ValidationError):
        CashTransaction.model_validate(
            {
                "type": "cash_adjustment",
                "amount": 100,
                "cash_before": 5000,
                "cash_after": 5100,
                "occurred_at": datetime(2026, 7, 7, 9, 0),
            }
        )


def test_cash_transaction_note_defaults_to_empty_string() -> None:
    transaction = CashTransaction.model_validate(
        {
            "type": "cash_adjustment",
            "amount": 100,
            "cash_before": 5000,
            "cash_after": 5100,
            "occurred_at": "2026-07-07T09:00:00+08:00",
        }
    )

    assert transaction.note == ""


def test_cash_transaction_strips_note_whitespace() -> None:
    transaction = CashTransaction.model_validate(
        {
            "type": "cash_adjustment",
            "amount": 100,
            "cash_before": 5000,
            "cash_after": 5100,
            "occurred_at": "2026-07-07T09:00:00+08:00",
            "note": "  manual correction  ",
        }
    )

    assert transaction.note == "manual correction"
