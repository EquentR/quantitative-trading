from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.planning.models import TradingPlan, TradingPlanStatus
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.storage.sqlite import connect, migrate


GENERATED_AT = datetime(2026, 7, 8, 7, 5, tzinfo=UTC)
EARLIER = datetime(2026, 7, 8, 6, 5, tzinfo=UTC)
LATER = datetime(2026, 7, 8, 8, 5, tzinfo=UTC)
VALID_UNTIL = datetime(2026, 7, 9, 7, 0, tzinfo=UTC)
LEDGER_UPDATED_AT = datetime(2026, 7, 8, 6, 55, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path) -> Iterator[TradingPlanRepository]:
    settings = Settings(database_path=tmp_path / "planning.db")
    with connect(settings) as connection:
        migrate(connection)
        yield TradingPlanRepository(connection)


def trading_plan(
    plan_id: str = "plan-20260709",
    *,
    generated_at: datetime = GENERATED_AT,
    status: TradingPlanStatus = TradingPlanStatus.ACTIVE,
) -> TradingPlan:
    return TradingPlan(
        plan_id=plan_id,
        trading_day=date(2026, 7, 9),
        generated_at=generated_at,
        valid_until=VALID_UNTIL,
        universe_snapshot_id=1,
        account_snapshot_id=2,
        ledger_max_updated_at=LEDGER_UPDATED_AT,
        watch_symbols=["600000", "000001"],
        holding_symbols=["600000"],
        key_levels={"600000": {"support": 9.7, "resistance": 10.4}},
        candidate_actions={"600000": ["hold", "reduce"], "000001": ["watch"]},
        invalid_if={"600000": ["breaks support"], "000001": ["liquidity weakens"]},
        warnings=[],
        status=status,
    )


def test_get_returns_none_for_missing_plan(repository: TradingPlanRepository) -> None:
    assert repository.get("missing") is None


def test_save_and_get_round_trip_plan(repository: TradingPlanRepository) -> None:
    plan = trading_plan()

    saved = repository.save(plan)
    restored = repository.get(plan.plan_id)

    assert saved == plan
    assert restored == plan


def test_latest_returns_none_when_table_is_empty(repository: TradingPlanRepository) -> None:
    assert repository.latest() is None


def test_latest_orders_by_generated_at_then_insert_order(
    repository: TradingPlanRepository,
) -> None:
    old = trading_plan("old", generated_at=GENERATED_AT)
    latest = trading_plan("latest", generated_at=LATER)
    same_time_second = trading_plan("same-time-second", generated_at=LATER)
    earlier = trading_plan("earlier", generated_at=EARLIER)

    repository.save(old)
    repository.save(latest)
    repository.save(earlier)
    repository.save(same_time_second)

    assert repository.latest() == same_time_second


def test_latest_orders_mixed_offsets_by_actual_instant(
    repository: TradingPlanRepository,
) -> None:
    earlier_instant = trading_plan(
        "earlier-offset",
        generated_at=datetime.fromisoformat("2026-07-08T15:05:00+08:00"),
    )
    later_instant = trading_plan(
        "later-utc",
        generated_at=datetime.fromisoformat("2026-07-08T15:05:00+00:00"),
    )

    repository.save(later_instant)
    repository.save(earlier_instant)

    earlier_row = repository.connection.execute(
        """
        SELECT generated_at, payload_json
        FROM trading_plans
        WHERE plan_id = ?
        """,
        (earlier_instant.plan_id,),
    ).fetchone()

    assert earlier_row["generated_at"] == "2026-07-08T07:05:00+00:00"
    assert TradingPlan.model_validate_json(earlier_row["payload_json"]) == earlier_instant
    assert repository.latest() == later_instant


def test_save_persists_query_columns_and_payload_json(
    repository: TradingPlanRepository,
) -> None:
    plan = trading_plan(status=TradingPlanStatus.STALE)

    repository.save(plan)

    row = repository.connection.execute(
        """
        SELECT trading_day, generated_at, valid_until, status, payload_json
        FROM trading_plans
        WHERE plan_id = ?
        """,
        (plan.plan_id,),
    ).fetchone()

    assert row["trading_day"] == "2026-07-09"
    assert row["generated_at"] == GENERATED_AT.isoformat()
    assert row["valid_until"] == VALID_UNTIL.isoformat()
    assert row["status"] == "stale"
    assert TradingPlan.model_validate_json(row["payload_json"]) == plan
