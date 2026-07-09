from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from quantitative_trading.recommendation.models import Recommendation


class RecommendationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save_many(
        self,
        recommendations: list[Recommendation],
        *,
        created_at: datetime,
    ) -> list[Recommendation]:
        rows = [
            (
                recommendation.recommendation_id,
                recommendation.symbol,
                recommendation.action.value,
                recommendation.data_time.astimezone(UTC).isoformat(),
                created_at.astimezone(UTC).isoformat(),
                recommendation.model_dump_json(),
            )
            for recommendation in recommendations
        ]
        self.connection.executemany(
            """
            INSERT INTO recommendations (
              recommendation_id,
              symbol,
              action,
              data_time,
              created_at,
              payload_json
            ) VALUES (
              ?,
              ?,
              ?,
              ?,
              ?,
              ?
            )
            ON CONFLICT(recommendation_id) DO UPDATE SET
              symbol = excluded.symbol,
              action = excluded.action,
              data_time = excluded.data_time,
              created_at = excluded.created_at,
              payload_json = excluded.payload_json
            """,
            rows,
        )
        self.connection.commit()
        return recommendations

    def list(self, *, limit: int = 50) -> list[Recommendation]:
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM recommendations
            ORDER BY created_at DESC, rowid ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [Recommendation.model_validate_json(row["payload_json"]) for row in rows]

    def get(self, recommendation_id: str) -> Recommendation | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM recommendations
            WHERE recommendation_id = ?
            """,
            (recommendation_id,),
        ).fetchone()
        if row is None:
            return None
        return Recommendation.model_validate_json(row["payload_json"])
