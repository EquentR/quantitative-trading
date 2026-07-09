from __future__ import annotations

import sqlite3
from datetime import UTC

from quantitative_trading.audit.models import AuditLog


class AuditLogRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, audit: AuditLog) -> AuditLog:
        self.connection.execute(
            """
            INSERT INTO audit_logs (
              audit_id,
              event_type,
              recommendation_id,
              created_at,
              payload_json
            ) VALUES (
              ?,
              ?,
              ?,
              ?,
              ?
            )
            ON CONFLICT(audit_id) DO UPDATE SET
              event_type = excluded.event_type,
              recommendation_id = excluded.recommendation_id,
              created_at = excluded.created_at,
              payload_json = excluded.payload_json
            """,
            (
                audit.audit_id,
                audit.event_type,
                audit.recommendation_id,
                audit.created_at.astimezone(UTC).isoformat(),
                audit.model_dump_json(),
            ),
        )
        self.connection.commit()
        return audit

    def get(self, audit_id: str) -> AuditLog | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM audit_logs
            WHERE audit_id = ?
            """,
            (audit_id,),
        ).fetchone()
        if row is None:
            return None
        return AuditLog.model_validate_json(row["payload_json"])

    def list_recent(self, *, limit: int = 50) -> list[AuditLog]:
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM audit_logs
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [AuditLog.model_validate_json(row["payload_json"]) for row in rows]
