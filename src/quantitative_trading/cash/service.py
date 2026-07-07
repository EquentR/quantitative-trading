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
        return self._repository.initialize(cash, now=now or current_time(), note=note)

    def transfer_in(
        self,
        amount: float,
        *,
        now: datetime | None = None,
        note: str = "",
    ) -> CashAccount:
        account = self._require_account()
        cash_after = account.cash_balance + amount
        return self._repository.save_state_with_transaction(
            cash_balance=cash_after,
            total_transfer_in=account.total_transfer_in + amount,
            total_transfer_out=account.total_transfer_out,
            transaction_type=CashTransactionType.TRANSFER_IN,
            amount=amount,
            cash_before=account.cash_balance,
            cash_after=cash_after,
            now=now or current_time(),
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
        if amount > account.cash_balance:
            raise CashTransferError("transfer-out amount cannot exceed cash balance")
        if amount > account.net_principal:
            raise CashTransferError("transfer-out amount cannot exceed net principal")

        cash_after = account.cash_balance - amount
        return self._repository.save_state_with_transaction(
            cash_balance=cash_after,
            total_transfer_in=account.total_transfer_in,
            total_transfer_out=account.total_transfer_out + amount,
            transaction_type=CashTransactionType.TRANSFER_OUT,
            amount=amount,
            cash_before=account.cash_balance,
            cash_after=cash_after,
            now=now or current_time(),
            note=note,
        )

    def adjust_cash(
        self,
        cash: float,
        *,
        now: datetime | None = None,
        note: str,
    ) -> CashAccount:
        if not note.strip():
            raise CashTransferError("cash adjustment note is required")

        account = self._require_account()
        amount = abs(cash - account.cash_balance)
        if amount == 0:
            raise CashTransferError("cash adjustment must change cash balance")

        return self._repository.save_state_with_transaction(
            cash_balance=cash,
            total_transfer_in=account.total_transfer_in,
            total_transfer_out=account.total_transfer_out,
            transaction_type=CashTransactionType.CASH_ADJUSTMENT,
            amount=amount,
            cash_before=account.cash_balance,
            cash_after=cash,
            now=now or current_time(),
            note=note,
        )

    def _require_account(self) -> CashAccount:
        account = self.get_account()
        if account is None:
            raise CashAccountNotInitializedError("cash account not initialized")
        return account
