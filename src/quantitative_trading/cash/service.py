from __future__ import annotations

from datetime import UTC, datetime

from quantitative_trading.cash.models import CashAccount, CashTransaction, CashTransactionType
from quantitative_trading.cash.repository import (
    CashAccountNotInitializedError,
    CashAccountRepository,
)


class CashTransferError(ValueError):
    pass


def current_time() -> datetime:
    return datetime.now(UTC)


def _operation_time(now: datetime | None) -> datetime:
    value = now or current_time()
    if value.tzinfo is None or value.utcoffset() is None:
        raise CashTransferError("now must be timezone-aware")
    return value


def _require_positive_amount(amount: float) -> None:
    if amount <= 0:
        raise CashTransferError("amount must be positive")


def _require_positive_cash(cash: float) -> None:
    if cash <= 0:
        raise CashTransferError("cash must be positive")


def _require_non_negative_cash(cash: float) -> None:
    if cash < 0:
        raise CashTransferError("cash must be non-negative")


class ReadOnlyCashService:
    def __init__(self, repository: CashAccountRepository) -> None:
        self._repository = repository

    def get_account(self) -> CashAccount | None:
        return self._repository.get()

    def list_transactions(self, *, limit: int = 20) -> list[CashTransaction]:
        return self._repository.list_transactions(limit=limit)


class CashService(ReadOnlyCashService):
    def initialize(
        self,
        cash: float,
        *,
        now: datetime | None = None,
        note: str = "",
    ) -> CashAccount:
        _require_positive_cash(cash)
        occurred_at = _operation_time(now)
        return self._repository.initialize(cash, now=occurred_at, note=note)

    def transfer_in(
        self,
        amount: float,
        *,
        now: datetime | None = None,
        note: str = "",
    ) -> CashAccount:
        account = self._require_account()
        _require_positive_amount(amount)
        occurred_at = _operation_time(now)
        cash_after = account.cash_balance + amount
        return self._repository.save_state_with_transaction(
            cash_balance=cash_after,
            total_transfer_in=account.total_transfer_in + amount,
            total_transfer_out=account.total_transfer_out,
            transaction_type=CashTransactionType.TRANSFER_IN,
            amount=amount,
            cash_before=account.cash_balance,
            cash_after=cash_after,
            now=occurred_at,
            note=note,
        )

    def transfer_out(
        self,
        amount: float,
        *,
        now: datetime | None = None,
        note: str = "",
    ) -> CashAccount:
        account = self._require_account()
        _require_positive_amount(amount)
        if amount > account.cash_balance:
            raise CashTransferError("transfer-out amount cannot exceed cash balance")
        if amount > account.net_principal:
            raise CashTransferError("transfer-out amount cannot exceed net principal")

        occurred_at = _operation_time(now)
        cash_after = account.cash_balance - amount
        return self._repository.save_state_with_transaction(
            cash_balance=cash_after,
            total_transfer_in=account.total_transfer_in,
            total_transfer_out=account.total_transfer_out + amount,
            transaction_type=CashTransactionType.TRANSFER_OUT,
            amount=amount,
            cash_before=account.cash_balance,
            cash_after=cash_after,
            now=occurred_at,
            note=note,
        )

    def adjust_cash(
        self,
        cash: float,
        *,
        now: datetime | None = None,
        note: str,
    ) -> CashAccount:
        account = self._require_account()
        _require_non_negative_cash(cash)
        if not note.strip():
            raise CashTransferError("cash adjustment note is required")

        amount = abs(cash - account.cash_balance)
        if amount == 0:
            raise CashTransferError("cash adjustment must change cash balance")

        occurred_at = _operation_time(now)
        return self._repository.save_state_with_transaction(
            cash_balance=cash,
            total_transfer_in=account.total_transfer_in,
            total_transfer_out=account.total_transfer_out,
            transaction_type=CashTransactionType.CASH_ADJUSTMENT,
            amount=amount,
            cash_before=account.cash_balance,
            cash_after=cash,
            now=occurred_at,
            note=note,
        )

    def _require_account(self) -> CashAccount:
        account = self.get_account()
        if account is None:
            raise CashAccountNotInitializedError("cash account not initialized")
        return account
