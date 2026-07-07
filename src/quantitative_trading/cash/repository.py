from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from quantitative_trading.cash.models import CashAccount, CashTransaction, CashTransactionType


class CashAccountAlreadyInitializedError(ValueError):
    pass


class CashAccountNotInitializedError(ValueError):
    pass


class CashAccountInvalidTransferError(ValueError):
    pass


class CashAccountRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self) -> CashAccount | None:
        row = self.connection.execute(
            """
            SELECT cash_balance, total_transfer_in, total_transfer_out, updated_at
            FROM cash_account
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return None
        return CashAccount.model_validate(dict(row))

    def initialize(self, cash: float, *, now: datetime, note: str) -> CashAccount:
        def operation() -> CashAccount:
            if self.get() is not None:
                raise CashAccountAlreadyInitializedError("cash account already initialized")

            account = self._build_account(
                cash_balance=cash,
                total_transfer_in=cash,
                total_transfer_out=0,
                updated_at=now,
            )
            transaction = self._build_transaction(
                transaction_type=CashTransactionType.INITIAL_DEPOSIT,
                amount=cash,
                cash_before=0,
                cash_after=cash,
                occurred_at=now,
                note=note,
            )
            self.connection.execute(
                """
                INSERT INTO cash_account (
                  id,
                  cash_balance,
                  total_transfer_in,
                  total_transfer_out,
                  updated_at
                ) VALUES (
                  1,
                  ?,
                  ?,
                  0,
                  ?
                )
                """,
                (
                    account.cash_balance,
                    account.total_transfer_in,
                    account.updated_at.isoformat(),
                ),
            )
            self._insert_transaction(transaction)

            persisted = self.get()
            if persisted is None:
                raise CashAccountNotInitializedError("cash account not initialized")
            return persisted

        return self._with_immediate_transaction(operation)

    def save_state_with_transaction(
        self,
        *,
        cash_balance: float,
        total_transfer_in: float,
        total_transfer_out: float,
        transaction_type: CashTransactionType,
        amount: float,
        cash_before: float,
        cash_after: float,
        now: datetime,
        note: str,
    ) -> CashAccount:
        current = self.get()
        if current is None:
            raise CashAccountNotInitializedError("cash account not initialized")

        account = self._build_account(
            cash_balance=cash_balance,
            total_transfer_in=total_transfer_in,
            total_transfer_out=total_transfer_out,
            updated_at=now,
        )
        transaction = self._build_transaction(
            transaction_type=transaction_type,
            amount=amount,
            cash_before=cash_before,
            cash_after=cash_after,
            occurred_at=now,
            note=note,
        )
        self._validate_transition(current=current, account=account, transaction=transaction)
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE cash_account
                SET
                  cash_balance = ?,
                  total_transfer_in = ?,
                  total_transfer_out = ?,
                  updated_at = ?
                WHERE id = 1
                """,
                (
                    account.cash_balance,
                    account.total_transfer_in,
                    account.total_transfer_out,
                    account.updated_at.isoformat(),
                ),
            )
            if cursor.rowcount == 0:
                raise CashAccountNotInitializedError("cash account not initialized")
            self._insert_transaction(transaction)

        persisted = self.get()
        if persisted is None:
            raise CashAccountNotInitializedError("cash account not initialized")
        return persisted

    def transfer_in(self, amount: float, *, now: datetime, note: str) -> CashAccount:
        def operation() -> CashAccount:
            current = self._require_current_account()
            cash_after = current.cash_balance + amount
            return self._save_validated_state_with_transaction(
                current=current,
                cash_balance=cash_after,
                total_transfer_in=current.total_transfer_in + amount,
                total_transfer_out=current.total_transfer_out,
                transaction_type=CashTransactionType.TRANSFER_IN,
                amount=amount,
                cash_before=current.cash_balance,
                cash_after=cash_after,
                now=now,
                note=note,
            )

        return self._with_immediate_transaction(operation)

    def transfer_out(self, amount: float, *, now: datetime, note: str) -> CashAccount:
        def operation() -> CashAccount:
            current = self._require_current_account()
            if amount > current.cash_balance:
                raise CashAccountInvalidTransferError(
                    "transfer-out amount cannot exceed cash balance"
                )
            if amount > current.net_principal:
                raise CashAccountInvalidTransferError(
                    "transfer-out amount cannot exceed net principal"
                )

            cash_after = current.cash_balance - amount
            return self._save_validated_state_with_transaction(
                current=current,
                cash_balance=cash_after,
                total_transfer_in=current.total_transfer_in,
                total_transfer_out=current.total_transfer_out + amount,
                transaction_type=CashTransactionType.TRANSFER_OUT,
                amount=amount,
                cash_before=current.cash_balance,
                cash_after=cash_after,
                now=now,
                note=note,
            )

        return self._with_immediate_transaction(operation)

    def adjust_cash(self, cash: float, *, now: datetime, note: str) -> CashAccount:
        def operation() -> CashAccount:
            current = self._require_current_account()
            if not note.strip():
                raise CashAccountInvalidTransferError("cash adjustment note is required")

            amount = abs(cash - current.cash_balance)
            if amount == 0:
                raise CashAccountInvalidTransferError("cash adjustment must change cash balance")

            return self._save_validated_state_with_transaction(
                current=current,
                cash_balance=cash,
                total_transfer_in=current.total_transfer_in,
                total_transfer_out=current.total_transfer_out,
                transaction_type=CashTransactionType.CASH_ADJUSTMENT,
                amount=amount,
                cash_before=current.cash_balance,
                cash_after=cash,
                now=now,
                note=note,
            )

        return self._with_immediate_transaction(operation)

    def list_transactions(self, *, limit: int = 20) -> list[CashTransaction]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        rows = self.connection.execute(
            """
            SELECT id, type, amount, cash_before, cash_after, occurred_at, note
            FROM cash_transactions
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [CashTransaction.model_validate(dict(row)) for row in rows]

    def _with_immediate_transaction(self, operation: Callable[[], CashAccount]) -> CashAccount:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            account = operation()
        except Exception:
            self.connection.rollback()
            raise
        self.connection.commit()
        return account

    def _require_current_account(self) -> CashAccount:
        current = self.get()
        if current is None:
            raise CashAccountNotInitializedError("cash account not initialized")
        return current

    def _save_validated_state_with_transaction(
        self,
        *,
        current: CashAccount,
        cash_balance: float,
        total_transfer_in: float,
        total_transfer_out: float,
        transaction_type: CashTransactionType,
        amount: float,
        cash_before: float,
        cash_after: float,
        now: datetime,
        note: str,
    ) -> CashAccount:
        account = self._build_account(
            cash_balance=cash_balance,
            total_transfer_in=total_transfer_in,
            total_transfer_out=total_transfer_out,
            updated_at=now,
        )
        transaction = self._build_transaction(
            transaction_type=transaction_type,
            amount=amount,
            cash_before=cash_before,
            cash_after=cash_after,
            occurred_at=now,
            note=note,
        )
        self._validate_transition(current=current, account=account, transaction=transaction)
        cursor = self.connection.execute(
            """
            UPDATE cash_account
            SET
              cash_balance = ?,
              total_transfer_in = ?,
              total_transfer_out = ?,
              updated_at = ?
            WHERE id = 1
            """,
            (
                account.cash_balance,
                account.total_transfer_in,
                account.total_transfer_out,
                account.updated_at.isoformat(),
            ),
        )
        if cursor.rowcount == 0:
            raise CashAccountNotInitializedError("cash account not initialized")
        self._insert_transaction(transaction)

        persisted = self.get()
        if persisted is None:
            raise CashAccountNotInitializedError("cash account not initialized")
        return persisted

    def _build_account(
        self,
        *,
        cash_balance: float,
        total_transfer_in: float,
        total_transfer_out: float,
        updated_at: datetime,
    ) -> CashAccount:
        return CashAccount.model_validate(
            {
                "cash_balance": cash_balance,
                "total_transfer_in": total_transfer_in,
                "total_transfer_out": total_transfer_out,
                "updated_at": updated_at,
            }
        )

    def _build_transaction(
        self,
        *,
        transaction_type: CashTransactionType,
        amount: float,
        cash_before: float,
        cash_after: float,
        occurred_at: datetime,
        note: str,
    ) -> CashTransaction:
        return CashTransaction.model_validate(
            {
                "type": transaction_type,
                "amount": amount,
                "cash_before": cash_before,
                "cash_after": cash_after,
                "occurred_at": occurred_at,
                "note": note,
            }
        )

    def _validate_transition(
        self,
        *,
        current: CashAccount,
        account: CashAccount,
        transaction: CashTransaction,
    ) -> None:
        if transaction.type is CashTransactionType.INITIAL_DEPOSIT:
            raise ValueError("initial deposit is only valid during initialization")
        if transaction.cash_before != current.cash_balance:
            raise ValueError("transaction cash_before must match current cash balance")
        if transaction.cash_after != account.cash_balance:
            raise ValueError("transaction cash_after must match new cash balance")

        if transaction.type is CashTransactionType.TRANSFER_IN:
            self._validate_transfer_in(current=current, account=account, transaction=transaction)
            return
        if transaction.type is CashTransactionType.TRANSFER_OUT:
            self._validate_transfer_out(current=current, account=account, transaction=transaction)
            return
        if transaction.type is CashTransactionType.CASH_ADJUSTMENT:
            self._validate_cash_adjustment(
                current=current,
                account=account,
                transaction=transaction,
            )
            return
        raise ValueError(f"unsupported cash transaction type: {transaction.type}")

    def _validate_transfer_in(
        self,
        *,
        current: CashAccount,
        account: CashAccount,
        transaction: CashTransaction,
    ) -> None:
        if transaction.cash_after != transaction.cash_before + transaction.amount:
            raise ValueError("transfer in cash_after must equal cash_before plus amount")
        if account.total_transfer_in != current.total_transfer_in + transaction.amount:
            raise ValueError("transfer in must increase total_transfer_in by amount")
        if account.total_transfer_out != current.total_transfer_out:
            raise ValueError("transfer in must not change total_transfer_out")

    def _validate_transfer_out(
        self,
        *,
        current: CashAccount,
        account: CashAccount,
        transaction: CashTransaction,
    ) -> None:
        if transaction.cash_after != transaction.cash_before - transaction.amount:
            raise ValueError("transfer out cash_after must equal cash_before minus amount")
        if account.total_transfer_out != current.total_transfer_out + transaction.amount:
            raise ValueError("transfer out must increase total_transfer_out by amount")
        if account.total_transfer_in != current.total_transfer_in:
            raise ValueError("transfer out must not change total_transfer_in")

    def _validate_cash_adjustment(
        self,
        *,
        current: CashAccount,
        account: CashAccount,
        transaction: CashTransaction,
    ) -> None:
        if transaction.amount != abs(transaction.cash_after - transaction.cash_before):
            raise ValueError("cash adjustment amount must equal absolute cash change")
        if account.total_transfer_in != current.total_transfer_in:
            raise ValueError("cash adjustment must not change total_transfer_in")
        if account.total_transfer_out != current.total_transfer_out:
            raise ValueError("cash adjustment must not change total_transfer_out")

    def _insert_transaction(
        self,
        transaction: CashTransaction,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO cash_transactions (
              type,
              amount,
              cash_before,
              cash_after,
              occurred_at,
              note
            ) VALUES (
              ?,
              ?,
              ?,
              ?,
              ?,
              ?
            )
            """,
            (
                transaction.type.value,
                transaction.amount,
                transaction.cash_before,
                transaction.cash_after,
                transaction.occurred_at.isoformat(),
                transaction.note,
            ),
        )
