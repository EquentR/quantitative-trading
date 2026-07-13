from __future__ import annotations

import sqlite3
from datetime import UTC

from quantitative_trading.audit.models import AuditLog


class AuditLogRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, audit: AuditLog, *, commit: bool = True) -> AuditLog:
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
        if commit:
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
        return self.list(limit=limit)

    def list(
        self,
        *,
        event_type: str | None = None,
        recommendation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditLog]:
        clauses: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("event_type", event_type),
            ("recommendation_id", recommendation_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((limit, offset))
        rows = self.connection.execute(
            f"""
            SELECT payload_json
            FROM audit_logs
            {where}
            ORDER BY created_at DESC, audit_id DESC
            LIMIT ? OFFSET ?
            """,
            parameters,
        ).fetchall()
        return [AuditLog.model_validate_json(row["payload_json"]) for row in rows]
