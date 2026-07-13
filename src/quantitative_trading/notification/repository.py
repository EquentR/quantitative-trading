from __future__ import annotations

import sqlite3
from datetime import UTC

from quantitative_trading.notification.models import NotificationStatus, NotificationSummary


class NotificationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, summary: NotificationSummary, *, commit: bool = True) -> NotificationSummary:
        self.connection.execute(
            """
            INSERT INTO notifications (
              notification_id,
              dedup_key,
              recommendation_id,
              symbol,
              action,
              status,
              data_time,
              created_at,
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
            ON CONFLICT(notification_id) DO UPDATE SET
              dedup_key = excluded.dedup_key,
              recommendation_id = excluded.recommendation_id,
              symbol = excluded.symbol,
              action = excluded.action,
              status = excluded.status,
              data_time = excluded.data_time,
              created_at = excluded.created_at,
              payload_json = excluded.payload_json
            """,
            (
                summary.notification_id,
                summary.dedup_key,
                summary.recommendation_id,
                summary.symbol,
                summary.action,
                summary.status.value,
                summary.data_time.astimezone(UTC).isoformat(),
                summary.created_at.astimezone(UTC).isoformat(),
                summary.model_dump_json(),
            ),
        )
        if commit:
            self.connection.commit()
        return summary

    def get_by_dedup_key(self, dedup_key: str) -> NotificationSummary | None:
        row = self.connection.execute(
            "SELECT payload_json FROM notifications WHERE dedup_key = ?",
            (dedup_key,),
        ).fetchone()
        if row is None:
            return None
        return NotificationSummary.model_validate_json(row["payload_json"])

    def get(self, notification_id: str) -> NotificationSummary | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM notifications
            WHERE notification_id = ?
            """,
            (notification_id,),
        ).fetchone()
        if row is None:
            return None
        return NotificationSummary.model_validate_json(row["payload_json"])

    def list_recent(self, *, limit: int = 50) -> list[NotificationSummary]:
        return self.list(limit=limit)

    def list(
        self,
        *,
        status: NotificationStatus | None = None,
        symbol: str | None = None,
        action: str | None = None,
        recommendation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationSummary]:
        clauses: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("status", status.value if status is not None else None),
            ("symbol", symbol),
            ("action", action),
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
            FROM notifications
            {where}
            ORDER BY created_at DESC, notification_id DESC
            LIMIT ? OFFSET ?
            """,
            parameters,
        ).fetchall()
        return [NotificationSummary.model_validate_json(row["payload_json"]) for row in rows]

    def count_unread(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM notifications WHERE status = ?",
            (NotificationStatus.UNREAD.value,),
        ).fetchone()
        return int(row["count"])

    def list_by_recommendation_id(
        self,
        recommendation_id: str,
        *,
        limit: int | None = None,
    ) -> list[NotificationSummary]:
        if limit is None:
            rows = self.connection.execute(
                """
                SELECT payload_json
                FROM notifications
                WHERE recommendation_id = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (recommendation_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT payload_json
                FROM notifications
                WHERE recommendation_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (recommendation_id, limit),
            ).fetchall()
        return [NotificationSummary.model_validate_json(row["payload_json"]) for row in rows]
