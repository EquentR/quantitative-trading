from __future__ import annotations

import sqlite3
from datetime import UTC, date

from quantitative_trading.planning.models import TradingPlan, TradingPlanStatus


class TradingPlanRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, plan: TradingPlan, *, commit: bool = True) -> TradingPlan:
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
        if commit:
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

    def list(self, *, limit: int = 50, offset: int = 0) -> list[TradingPlan]:
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM trading_plans
            ORDER BY generated_at DESC, rowid DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [TradingPlan.model_validate_json(row["payload_json"]) for row in rows]

    def count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM trading_plans"
        ).fetchone()
        return int(row["count"])

    def active_for_day(self, trading_day: date) -> TradingPlan | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM trading_plans
            WHERE trading_day = ? AND status = 'active'
            ORDER BY generated_at DESC, rowid DESC
            LIMIT 1
            """,
            (trading_day.isoformat(),),
        ).fetchone()
        if row is None:
            return None
        return TradingPlan.model_validate_json(row["payload_json"])

    def next_version(self, trading_day: date) -> int:
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM trading_plans
            WHERE trading_day = ?
            """,
            (trading_day.isoformat(),),
        ).fetchall()
        versions = [
            TradingPlan.model_validate_json(row["payload_json"]).version for row in rows
        ]
        return max(versions, default=0) + 1

    def activate(self, plan: TradingPlan, *, commit: bool = True) -> TradingPlan:
        savepoint = "activate_trading_plan"
        self.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            rows = self.connection.execute(
                """
                SELECT payload_json
                FROM trading_plans
                WHERE trading_day = ? AND status = 'active' AND plan_id != ?
                """,
                (plan.trading_day.isoformat(), plan.plan_id),
            ).fetchall()
            for row in rows:
                existing = TradingPlan.model_validate_json(row["payload_json"])
                self.save(
                    existing.model_copy(
                        update={"status": TradingPlanStatus.SUPERSEDED}
                    ),
                    commit=False,
                )

            active = plan.model_copy(update={"status": TradingPlanStatus.ACTIVE})
            self.save(active, commit=False)
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise

        if commit:
            self.connection.commit()
        return active
