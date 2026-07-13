from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from quantitative_trading.recommendation.models import (
    Recommendation,
    RecommendationAction,
)


class RecommendationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save_many(
        self,
        recommendations: list[Recommendation],
        *,
        created_at: datetime,
        commit: bool = True,
    ) -> list[Recommendation]:
        rows = [
            (
                recommendation.recommendation_id,
                recommendation.dedup_key,
                recommendation.symbol,
                recommendation.action.value,
                recommendation.condition_fingerprint,
                recommendation.audit_id,
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
              dedup_key,
              symbol,
              action,
              condition_fingerprint,
              audit_id,
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
            ON CONFLICT DO NOTHING
            """,
            rows,
        )
        if commit:
            self.connection.commit()
        saved: list[Recommendation] = []
        for recommendation in recommendations:
            existing = (
                self.get_by_dedup_key(recommendation.dedup_key)
                if recommendation.dedup_key is not None
                else self.get(recommendation.recommendation_id)
            )
            if existing is None:
                raise sqlite3.IntegrityError(
                    "recommendation was not saved and no matching identity exists"
                )
            saved.append(existing)
        return saved

    def list(
        self,
        *,
        symbol: str | None = None,
        action: RecommendationAction | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Recommendation]:
        clauses: list[str] = []
        parameters: list[object] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            parameters.append(symbol)
        if action is not None:
            clauses.append("action = ?")
            parameters.append(action.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((limit, offset))
        rows = self.connection.execute(
            f"""
            SELECT payload_json
            FROM recommendations
            {where}
            ORDER BY data_time DESC, created_at DESC, rowid ASC
            LIMIT ? OFFSET ?
            """,
            parameters,
        ).fetchall()
        return [Recommendation.model_validate_json(row["payload_json"]) for row in rows]

    def count(
        self,
        *,
        symbol: str | None = None,
        action: RecommendationAction | None = None,
    ) -> int:
        clauses: list[str] = []
        parameters: list[object] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            parameters.append(symbol)
        if action is not None:
            clauses.append("action = ?")
            parameters.append(action.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM recommendations {where}",
            parameters,
        ).fetchone()
        return int(row["count"])

    def latest_for_symbol(self, symbol: str) -> Recommendation | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM recommendations
            WHERE symbol = ?
            ORDER BY data_time DESC, created_at DESC, rowid DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return Recommendation.model_validate_json(row["payload_json"])

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

    def get_by_dedup_key(self, dedup_key: str | None) -> Recommendation | None:
        if dedup_key is None:
            return None
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM recommendations
            WHERE dedup_key = ?
            """,
            (dedup_key,),
        ).fetchone()
        if row is None:
            return None
        return Recommendation.model_validate_json(row["payload_json"])
