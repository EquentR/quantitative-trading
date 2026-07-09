from __future__ import annotations

import json
import sqlite3

from quantitative_trading.universe.models import UniverseSnapshot


class UniverseSnapshotRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, snapshot: UniverseSnapshot) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO universe_snapshots (
              created_at,
              status,
              warnings_json,
              payload_json
            ) VALUES (
              ?,
              ?,
              ?,
              ?
            )
            """,
            (
                snapshot.created_at.isoformat(),
                snapshot.status.value,
                json.dumps(snapshot.warnings, ensure_ascii=False),
                snapshot.model_dump_json(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def latest(self) -> UniverseSnapshot | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM universe_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return UniverseSnapshot.model_validate_json(row["payload_json"])

    def get(self, snapshot_id: int) -> UniverseSnapshot | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM universe_snapshots
            WHERE id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return UniverseSnapshot.model_validate_json(row["payload_json"])
