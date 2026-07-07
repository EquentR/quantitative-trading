from __future__ import annotations

import sqlite3
from datetime import datetime

from quantitative_trading.cash.models import CashAccount, CashTransaction, CashTransactionType


class CashAccountAlreadyInitializedError(ValueError):
    pass


class CashAccountNotInitializedError(ValueError):
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
        with self.connection:
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
