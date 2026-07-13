from __future__ import annotations

import json
import sqlite3

from quantitative_trading.market.models import MarketInputSnapshot, QuoteSnapshot
from quantitative_trading.market.repositories import validate_heavy_snapshot_references


class QuoteSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, snapshot: QuoteSnapshot, *, commit: bool = True) -> int:
        cursor = self.connection.execute(
            """INSERT INTO quote_snapshots
               (symbol, status, data_time, fetched_at, source, payload_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                snapshot.symbol,
                snapshot.status.value,
                None if snapshot.data_time is None else snapshot.data_time.isoformat(),
                snapshot.fetched_at.isoformat(),
                snapshot.source,
                snapshot.model_dump_json(),
            ),
        )
        if commit:
            self.connection.commit()
        return int(cursor.lastrowid)

    def get(self, snapshot_id: int) -> QuoteSnapshot | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM quote_snapshots
            WHERE id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return QuoteSnapshot.model_validate_json(row["payload_json"])

    def latest_for_symbol(self, symbol: str) -> QuoteSnapshot | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM quote_snapshots
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return QuoteSnapshot.model_validate_json(row["payload_json"])


class MarketInputSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, snapshot: MarketInputSnapshot, *, commit: bool = True) -> int:
        for symbol, quote_id in snapshot.quote_snapshot_refs.items():
            reference = self.connection.execute(
                """
                SELECT 1
                FROM quote_snapshots
                WHERE id = ? AND symbol = ?
                """,
                (quote_id, symbol),
            ).fetchone()
            if reference is None:
                raise ValueError("invalid quote snapshot reference")

        validate_heavy_snapshot_references(self.connection, snapshot)

        cursor = self.connection.execute(
            """
            INSERT INTO market_input_snapshots (
              universe_snapshot_id,
              data_time,
              fetched_at,
              warnings_json,
              payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                snapshot.universe_snapshot_id,
                None if snapshot.data_time is None else snapshot.data_time.isoformat(),
                snapshot.fetched_at.isoformat(),
                json.dumps(snapshot.warnings, ensure_ascii=False),
                snapshot.model_dump_json(),
            ),
        )
        if commit:
            self.connection.commit()
        return int(cursor.lastrowid)

    def get(self, snapshot_id: int) -> MarketInputSnapshot | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM market_input_snapshots
            WHERE id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return MarketInputSnapshot.model_validate_json(row["payload_json"])

    def latest(self) -> MarketInputSnapshot | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM market_input_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return MarketInputSnapshot.model_validate_json(row["payload_json"])
