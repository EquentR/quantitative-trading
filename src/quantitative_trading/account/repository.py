from __future__ import annotations

import sqlite3
from datetime import datetime

from quantitative_trading.account.models import AccountSnapshot


class AccountSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(
        self,
        snapshot: AccountSnapshot,
        *,
        cash_account_updated_at: datetime | None,
        ledger_max_updated_at: datetime | None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO account_snapshots (
              status,
              created_at,
              cash_account_updated_at,
              ledger_max_updated_at,
              market_value,
              total_assets,
              total_pnl,
              position_ratio,
              payload_json
            ) VALUES (
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?
            )
            """,
            (
                snapshot.status.value,
                snapshot.created_at.isoformat(),
                self._isoformat_or_none(cash_account_updated_at),
                self._isoformat_or_none(ledger_max_updated_at),
                snapshot.market_value,
                snapshot.total_assets,
                snapshot.total_pnl,
                snapshot.position_ratio,
                snapshot.model_dump_json(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def latest(self) -> AccountSnapshot | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM account_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return AccountSnapshot.model_validate_json(row["payload_json"])

    @staticmethod
    def _isoformat_or_none(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.isoformat()
