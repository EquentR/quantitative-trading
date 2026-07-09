from __future__ import annotations

import sqlite3
from datetime import UTC

from quantitative_trading.planning.models import TradingPlan


class TradingPlanRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, plan: TradingPlan) -> TradingPlan:
        self.connection.execute(
            """
            INSERT INTO trading_plans (
              plan_id,
              trading_day,
              generated_at,
              valid_until,
              status,
              payload_json
            ) VALUES (
              ?,
              ?,
              ?,
              ?,
              ?,
              ?
            )
            ON CONFLICT(plan_id) DO UPDATE SET
              trading_day = excluded.trading_day,
              generated_at = excluded.generated_at,
              valid_until = excluded.valid_until,
              status = excluded.status,
              payload_json = excluded.payload_json
            """,
            (
                plan.plan_id,
                plan.trading_day.isoformat(),
                plan.generated_at.astimezone(UTC).isoformat(),
                plan.valid_until.isoformat(),
                plan.status.value,
                plan.model_dump_json(),
            ),
        )
        self.connection.commit()
        return plan

    def get(self, plan_id: str) -> TradingPlan | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM trading_plans
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        if row is None:
            return None
        return TradingPlan.model_validate_json(row["payload_json"])

    def latest(self) -> TradingPlan | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM trading_plans
            ORDER BY generated_at DESC, rowid DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return TradingPlan.model_validate_json(row["payload_json"])
