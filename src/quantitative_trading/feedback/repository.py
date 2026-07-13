from __future__ import annotations

import sqlite3
from datetime import UTC

from quantitative_trading.feedback.models import ExecutionFeedback


class FeedbackRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, feedback: ExecutionFeedback, *, commit: bool = True) -> ExecutionFeedback:
        self.connection.execute(
            """
            INSERT INTO execution_feedback (
              feedback_id,
              recommendation_id,
              executed,
              execution_price,
              execution_quantity,
              note,
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
              ?
            )
            ON CONFLICT(feedback_id) DO UPDATE SET
              recommendation_id = excluded.recommendation_id,
              executed = excluded.executed,
              execution_price = excluded.execution_price,
              execution_quantity = excluded.execution_quantity,
              note = excluded.note,
              created_at = excluded.created_at,
              payload_json = excluded.payload_json
            """,
            (
                feedback.feedback_id,
                feedback.recommendation_id,
                int(feedback.executed),
                feedback.execution_price,
                feedback.execution_quantity,
                feedback.note,
                feedback.created_at.astimezone(UTC).isoformat(),
                feedback.model_dump_json(),
            ),
        )
        if commit:
            self.connection.commit()
        return feedback

    def get(self, feedback_id: str) -> ExecutionFeedback | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM execution_feedback
            WHERE feedback_id = ?
            """,
            (feedback_id,),
        ).fetchone()
        if row is None:
            return None
        return ExecutionFeedback.model_validate_json(row["payload_json"])

    def list(
        self,
        *,
        recommendation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExecutionFeedback]:
        if recommendation_id is None:
            rows = self.connection.execute(
                """
                SELECT payload_json
                FROM execution_feedback
                ORDER BY created_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT payload_json
                FROM execution_feedback
                WHERE recommendation_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                (recommendation_id, limit, offset),
            ).fetchall()
        return [ExecutionFeedback.model_validate_json(row["payload_json"]) for row in rows]

    def count(self, *, recommendation_id: str | None = None) -> int:
        if recommendation_id is None:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM execution_feedback"
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT COUNT(*) AS count FROM execution_feedback WHERE recommendation_id = ?",
                (recommendation_id,),
            ).fetchone()
        return int(row["count"])
