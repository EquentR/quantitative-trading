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


def test_save_upserts_existing_plan_id_with_latest_payload(
    repository: TradingPlanRepository,
) -> None:
    original = trading_plan(status=TradingPlanStatus.ACTIVE)
    latest = original.model_copy(
        update={
            "generated_at": LATER,
            "status": TradingPlanStatus.STALE,
            "warnings": ["regenerated after close"],
        }
    )

    repository.save(original)
    repository.save(latest)

    restored = repository.get(original.plan_id)
    row = repository.connection.execute(
        """
        SELECT generated_at, status, payload_json
        FROM trading_plans
        WHERE plan_id = ?
        """,
        (original.plan_id,),
    ).fetchone()

    assert restored == latest
    assert row["generated_at"] == LATER.isoformat()
    assert row["status"] == "stale"
    assert TradingPlan.model_validate_json(row["payload_json"]) == latest


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


def test_activate_supersedes_previous_active_plan_for_same_trading_day(
    repository: TradingPlanRepository,
) -> None:
    first = trading_plan("plan-20260709-v1").model_copy(update={"version": 1})
    second = trading_plan(
        "plan-20260709-v2",
        generated_at=LATER,
        status=TradingPlanStatus.DRAFT,
    ).model_copy(update={"version": 2})

    active_first = repository.activate(first)
    active_second = repository.activate(second)

    restored_first = repository.get(first.plan_id)
    restored_second = repository.get(second.plan_id)
    assert active_first.status is TradingPlanStatus.ACTIVE
    assert active_second.status is TradingPlanStatus.ACTIVE
    assert restored_first is not None
    assert restored_first.status is TradingPlanStatus.SUPERSEDED
    assert restored_second == active_second
    assert repository.active_for_day(date(2026, 7, 9)) == active_second


def test_next_version_increments_highest_version_for_trading_day(
    repository: TradingPlanRepository,
) -> None:
    assert repository.next_version(date(2026, 7, 9)) == 1
    repository.save(trading_plan("plan-20260709-v1").model_copy(update={"version": 1}))
    repository.save(
        trading_plan("plan-20260709-v3", generated_at=LATER).model_copy(
            update={"version": 3}
        )
    )

    assert repository.next_version(date(2026, 7, 9)) == 4
