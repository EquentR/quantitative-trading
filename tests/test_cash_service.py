from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from quantitative_trading.cash.models import CashTransactionType
from quantitative_trading.cash.repository import (
    CashAccountNotInitializedError,
    CashAccountRepository,
)
from quantitative_trading.cash.service import CashService, CashTransferError, ReadOnlyCashService
from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


@pytest.fixture
def repository(tmp_path) -> Iterator[CashAccountRepository]:
    settings = Settings(database_path=tmp_path / "account.db")
    with connect(settings) as connection:
        migrate(connection)
        yield CashAccountRepository(connection)


@pytest.fixture
def service(repository: CashAccountRepository) -> CashService:
    return CashService(repository)


@pytest.fixture
def read_only(repository: CashAccountRepository) -> ReadOnlyCashService:
    return ReadOnlyCashService(repository)


def fixed_now() -> datetime:
    return datetime(2026, 7, 7, 9, 0, tzinfo=UTC)


def later_now() -> datetime:
    return datetime(2026, 7, 7, 10, 0, tzinfo=UTC)


def test_cash_service_transfer_in_increases_cash_and_principal(
    service: CashService,
    read_only: ReadOnlyCashService,
) -> None:
    service.initialize(50000, now=fixed_now(), note="initial principal")

    account = service.transfer_in(10000, now=later_now(), note="bank transfer in")
    transactions = read_only.list_transactions()

    assert account.cash_balance == 60000
    assert account.total_transfer_in == 60000
    assert account.total_transfer_out == 0
    assert account.net_principal == 60000
    assert transactions[-1].type is CashTransactionType.TRANSFER_IN
    assert transactions[-1].amount == 10000
    assert transactions[-1].cash_before == 50000
    assert transactions[-1].cash_after == 60000
    assert transactions[-1].note == "bank transfer in"


def test_cash_service_transfer_out_decreases_cash_and_principal(
    service: CashService,
    read_only: ReadOnlyCashService,
) -> None:
    service.initialize(50000, now=fixed_now(), note="initial principal")

    account = service.transfer_out(5000, now=later_now(), note="bank transfer out")
    transactions = read_only.list_transactions()

    assert account.cash_balance == 45000
    assert account.total_transfer_in == 50000
    assert account.total_transfer_out == 5000
    assert account.net_principal == 45000
    assert transactions[-1].type is CashTransactionType.TRANSFER_OUT
    assert transactions[-1].amount == 5000
    assert transactions[-1].cash_before == 50000
    assert transactions[-1].cash_after == 45000
    assert transactions[-1].note == "bank transfer out"


def test_cash_service_rejects_transfer_out_above_cash(
    service: CashService,
    read_only: ReadOnlyCashService,
) -> None:
    original = service.initialize(1000, now=fixed_now(), note="initial principal")

    with pytest.raises(CashTransferError, match="cannot exceed cash balance"):
        service.transfer_out(1001, now=later_now(), note="too much cash out")

    assert service.get_account() == original
    assert len(read_only.list_transactions()) == 1


def test_cash_service_rejects_transfer_out_above_net_principal(
    service: CashService,
    read_only: ReadOnlyCashService,
) -> None:
    service.initialize(50000, now=fixed_now(), note="initial principal")
    original = service.adjust_cash(60000, now=later_now(), note="manual broker correction")

    with pytest.raises(CashTransferError, match="cannot exceed net principal"):
        service.transfer_out(55000, now=later_now(), note="above net principal")

    assert service.get_account() == original
    assert len(read_only.list_transactions()) == 2


def test_cash_service_adjust_changes_only_cash(
    service: CashService,
    read_only: ReadOnlyCashService,
) -> None:
    service.initialize(50000, now=fixed_now(), note="initial principal")

    account = service.adjust_cash(48000, now=later_now(), note="manual broker correction")
    transactions = read_only.list_transactions()

    assert account.cash_balance == 48000
    assert account.total_transfer_in == 50000
    assert account.total_transfer_out == 0
    assert account.net_principal == 50000
    assert transactions[-1].type is CashTransactionType.CASH_ADJUSTMENT
    assert transactions[-1].amount == 2000
    assert transactions[-1].cash_before == 50000
    assert transactions[-1].cash_after == 48000
    assert transactions[-1].note == "manual broker correction"


@pytest.mark.parametrize("note", ["", "   "])
def test_cash_service_requires_adjustment_note(
    service: CashService,
    note: str,
) -> None:
    original = service.initialize(50000, now=fixed_now(), note="initial principal")

    with pytest.raises(CashTransferError, match="cash adjustment note is required"):
        service.adjust_cash(49000, now=later_now(), note=note)

    assert service.get_account() == original


def test_cash_service_rejects_no_op_adjustment(
    service: CashService,
    read_only: ReadOnlyCashService,
) -> None:
    original = service.initialize(50000, now=fixed_now(), note="initial principal")

    with pytest.raises(CashTransferError, match="must change cash balance"):
        service.adjust_cash(50000, now=later_now(), note="manual broker correction")

    assert service.get_account() == original
    assert len(read_only.list_transactions()) == 1


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("transfer_in", (1000,), {"note": "bank transfer in"}),
        ("transfer_out", (1000,), {"note": "bank transfer out"}),
        ("adjust_cash", (1000,), {"note": "manual broker correction"}),
    ],
)
def test_cash_service_mutations_require_initialized_account(
    service: CashService,
    method_name: str,
    args: tuple[float],
    kwargs: dict[str, str],
) -> None:
    method = getattr(service, method_name)

    with pytest.raises(CashAccountNotInitializedError):
        method(*args, now=fixed_now(), **kwargs)


def test_read_only_cash_service_can_read_account_and_transactions(
    service: CashService,
    read_only: ReadOnlyCashService,
) -> None:
    initialized = service.initialize(50000, now=fixed_now(), note="initial principal")

    assert read_only.get_account() == initialized
    assert [transaction.type for transaction in read_only.list_transactions()] == [
        CashTransactionType.INITIAL_DEPOSIT,
    ]


def test_read_only_cash_service_exposes_no_mutation_methods(
    read_only: ReadOnlyCashService,
) -> None:
    assert not hasattr(read_only, "initialize")
    assert not hasattr(read_only, "transfer_in")
    assert not hasattr(read_only, "transfer_out")
    assert not hasattr(read_only, "adjust_cash")
