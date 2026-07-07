from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.cash.models import CashTransactionType
from quantitative_trading.cash.repository import (
    CashAccountAlreadyInitializedError,
    CashAccountNotInitializedError,
    CashAccountRepository,
)
from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


@pytest.fixture
def repository(tmp_path) -> Iterator[CashAccountRepository]:
    settings = Settings(database_path=tmp_path / "account.db")
    with connect(settings) as connection:
        migrate(connection)
        yield CashAccountRepository(connection)


def fixed_now() -> datetime:
    return datetime(2026, 7, 7, 9, 0, tzinfo=UTC)


def naive_now() -> datetime:
    return datetime(2026, 7, 7, 9, 0)


def current_transaction_notes(repository: CashAccountRepository) -> list[str]:
    rows = repository.connection.execute(
        """
        SELECT note
        FROM cash_transactions
        ORDER BY id ASC
        """
    ).fetchall()
    return [row["note"] for row in rows]


def test_repository_get_returns_none_before_initialization(
    repository: CashAccountRepository,
) -> None:
    assert repository.get() is None


def test_repository_initializes_account_and_transaction(
    repository: CashAccountRepository,
) -> None:
    account = repository.initialize(50000, now=fixed_now(), note="initial principal")

    transactions = repository.list_transactions()

    assert account.cash_balance == 50000
    assert account.total_transfer_in == 50000
    assert account.total_transfer_out == 0
    assert account.net_principal == 50000
    assert account.updated_at == fixed_now()
    assert transactions[0].type is CashTransactionType.INITIAL_DEPOSIT
    assert transactions[0].amount == 50000
    assert transactions[0].cash_before == 0
    assert transactions[0].cash_after == 50000
    assert transactions[0].occurred_at == fixed_now()
    assert transactions[0].note == "initial principal"


def test_repository_rejects_naive_initialize_time_without_writing(
    repository: CashAccountRepository,
) -> None:
    with pytest.raises(ValidationError):
        repository.initialize(50000, now=naive_now(), note="initial principal")

    assert repository.get() is None
    assert repository.list_transactions() == []


@pytest.mark.parametrize("cash", [0, -1])
def test_repository_rejects_invalid_initialize_cash_without_writing(
    repository: CashAccountRepository,
    cash: float,
) -> None:
    with pytest.raises(ValidationError):
        repository.initialize(cash, now=fixed_now(), note="initial principal")

    assert repository.get() is None
    assert repository.list_transactions() == []


def test_repository_rejects_duplicate_initialization(
    repository: CashAccountRepository,
) -> None:
    repository.initialize(50000, now=fixed_now(), note="initial principal")

    with pytest.raises(CashAccountAlreadyInitializedError):
        repository.initialize(1000, now=fixed_now(), note="duplicate initialization")


def test_repository_saves_new_state_and_transaction(
    repository: CashAccountRepository,
) -> None:
    repository.initialize(50000, now=fixed_now(), note="initial principal")

    account = repository.save_state_with_transaction(
        cash_balance=51000,
        total_transfer_in=51000,
        total_transfer_out=0,
        transaction_type=CashTransactionType.TRANSFER_IN,
        amount=1000,
        cash_before=50000,
        cash_after=51000,
        now=fixed_now(),
        note="bank transfer in",
    )

    transactions = repository.list_transactions()
    assert account.cash_balance == 51000
    assert account.net_principal == 51000
    assert account.updated_at == fixed_now()
    assert [transaction.type for transaction in transactions] == [
        CashTransactionType.INITIAL_DEPOSIT,
        CashTransactionType.TRANSFER_IN,
    ]
    assert transactions[1].amount == 1000
    assert transactions[1].cash_before == 50000
    assert transactions[1].cash_after == 51000
    assert transactions[1].note == "bank transfer in"


def test_repository_rejects_naive_save_time_without_changing_existing_state(
    repository: CashAccountRepository,
) -> None:
    original_account = repository.initialize(
        50000,
        now=fixed_now(),
        note="initial principal",
    )
    original_transactions = repository.list_transactions()

    with pytest.raises(ValidationError):
        repository.save_state_with_transaction(
            cash_balance=51000,
            total_transfer_in=51000,
            total_transfer_out=0,
            transaction_type=CashTransactionType.TRANSFER_IN,
            amount=1000,
            cash_before=50000,
            cash_after=51000,
            now=naive_now(),
            note="bank transfer in",
        )

    assert repository.get() == original_account
    assert repository.list_transactions() == original_transactions


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": 0},
        {"amount": -1},
        {"cash_balance": -1},
        {"total_transfer_in": -1},
        {"total_transfer_out": -1},
        {"total_transfer_in": 1000, "total_transfer_out": 1001},
        {"cash_before": -1},
        {"cash_after": -1},
    ],
)
def test_repository_rejects_invalid_save_values_without_changing_existing_state(
    repository: CashAccountRepository,
    overrides: dict[str, float],
) -> None:
    original_account = repository.initialize(
        50000,
        now=fixed_now(),
        note="initial principal",
    )
    original_transactions = repository.list_transactions()
    values = {
        "cash_balance": 51000,
        "total_transfer_in": 51000,
        "total_transfer_out": 0,
        "transaction_type": CashTransactionType.TRANSFER_IN,
        "amount": 1000,
        "cash_before": 50000,
        "cash_after": 51000,
        "now": fixed_now(),
        "note": "bank transfer in",
    }
    values.update(overrides)

    with pytest.raises(ValidationError):
        repository.save_state_with_transaction(**values)

    assert repository.get() == original_account
    assert repository.list_transactions() == original_transactions


def test_repository_normalizes_transaction_note_before_storage(
    repository: CashAccountRepository,
) -> None:
    repository.initialize(50000, now=fixed_now(), note="  initial principal  ")
    repository.save_state_with_transaction(
        cash_balance=51000,
        total_transfer_in=51000,
        total_transfer_out=0,
        transaction_type=CashTransactionType.TRANSFER_IN,
        amount=1000,
        cash_before=50000,
        cash_after=51000,
        now=fixed_now(),
        note="  bank transfer in  ",
    )

    assert current_transaction_notes(repository) == [
        "initial principal",
        "bank transfer in",
    ]


def test_repository_rejects_save_before_initialization(
    repository: CashAccountRepository,
) -> None:
    with pytest.raises(CashAccountNotInitializedError):
        repository.save_state_with_transaction(
            cash_balance=1000,
            total_transfer_in=1000,
            total_transfer_out=0,
            transaction_type=CashTransactionType.TRANSFER_IN,
            amount=1000,
            cash_before=0,
            cash_after=1000,
            now=fixed_now(),
            note="bank transfer in",
        )

    assert repository.list_transactions() == []


def test_repository_lists_transactions_in_insert_order_with_limit(
    repository: CashAccountRepository,
) -> None:
    repository.initialize(50000, now=fixed_now(), note="initial principal")
    repository.save_state_with_transaction(
        cash_balance=51000,
        total_transfer_in=51000,
        total_transfer_out=0,
        transaction_type=CashTransactionType.TRANSFER_IN,
        amount=1000,
        cash_before=50000,
        cash_after=51000,
        now=fixed_now(),
        note="bank transfer in",
    )
    repository.save_state_with_transaction(
        cash_balance=49000,
        total_transfer_in=51000,
        total_transfer_out=2000,
        transaction_type=CashTransactionType.TRANSFER_OUT,
        amount=2000,
        cash_before=51000,
        cash_after=49000,
        now=fixed_now(),
        note="bank transfer out",
    )

    transactions = repository.list_transactions(limit=2)

    assert [transaction.type for transaction in transactions] == [
        CashTransactionType.INITIAL_DEPOSIT,
        CashTransactionType.TRANSFER_IN,
    ]


@pytest.mark.parametrize("limit", [0, -1])
def test_repository_rejects_non_positive_transaction_limits(
    repository: CashAccountRepository,
    limit: int,
) -> None:
    with pytest.raises(ValueError):
        repository.list_transactions(limit=limit)
