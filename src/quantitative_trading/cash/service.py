from __future__ import annotations

from datetime import UTC, datetime

from quantitative_trading.cash.models import CashAccount, CashTransaction
from quantitative_trading.cash.repository import (
    CashAccountAlreadyInitializedError,
    CashAccountInvalidTransferError,
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
        try:
            return self._repository.initialize(cash, now=occurred_at, note=note)
        except CashAccountAlreadyInitializedError as exc:
            raise CashTransferError("cash account already initialized") from exc

    def transfer_in(
        self,
        amount: float,
        *,
        now: datetime | None = None,
        note: str = "",
    ) -> CashAccount:
        _require_positive_amount(amount)
        occurred_at = _operation_time(now)
        return self._repository.transfer_in(amount, now=occurred_at, note=note)

    def transfer_out(
        self,
        amount: float,
        *,
        now: datetime | None = None,
        note: str = "",
    ) -> CashAccount:
        _require_positive_amount(amount)
        occurred_at = _operation_time(now)
        try:
            return self._repository.transfer_out(amount, now=occurred_at, note=note)
        except CashAccountInvalidTransferError as exc:
            raise CashTransferError(str(exc)) from exc

    def adjust_cash(
        self,
        cash: float,
        *,
        now: datetime | None = None,
        note: str,
    ) -> CashAccount:
        _require_non_negative_cash(cash)
        occurred_at = _operation_time(now)
        try:
            return self._repository.adjust_cash(cash, now=occurred_at, note=note)
        except CashAccountInvalidTransferError as exc:
            raise CashTransferError(str(exc)) from exc
