from datetime import UTC, date, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.planning.models import TradingPlanStatus
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.planning.workflow import generate_trading_plan
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.recommendation.scanner import (
    PlanNotScannableError,
    scan_latest_plan_recommendations,
)
from quantitative_trading.storage.sqlite import connect, migrate


def _seed_plan(connection, *, trading_day: date = date(2026, 7, 9)) -> None:
    PositionRepository(connection).add(
        PositionInput(
            symbol="600000",
            name="浦发银行",
            quantity=1000,
            available_quantity=800,
            cost_price=9.5,
            opened_at=date(2026, 7, 6),
            note="manual ledger",
        ),
        now=datetime(2026, 7, 8, 1, 0, tzinfo=UTC),
    )
    generate_trading_plan(
        connection,
        trading_day=trading_day,
        now=datetime(2026, 7, 8, 7, 0, tzinfo=UTC),
        timezone="Asia/Shanghai",
    )


def test_scanner_rejects_expired_plan_before_persisting(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "scanner.db")
    with connect(settings) as connection:
        migrate(connection)
        _seed_plan(connection)

        with pytest.raises(PlanNotScannableError, match="trading plan is not scannable"):
            scan_latest_plan_recommendations(
                connection,
                now=datetime(2026, 7, 9, 8, 0, tzinfo=UTC),
            )

        assert RecommendationRepository(connection).list() == []


@pytest.mark.parametrize(
    "status",
    [TradingPlanStatus.EXPIRED, TradingPlanStatus.STALE],
)
def test_scanner_rejects_inactive_plan_status_before_persisting(
    tmp_path,
    status: TradingPlanStatus,
) -> None:
    settings = Settings(database_path=tmp_path / "scanner.db")
    with connect(settings) as connection:
        migrate(connection)
        _seed_plan(connection)
        plan = TradingPlanRepository(connection).latest()
        assert plan is not None
        inactive_plan = plan.model_copy(update={"status": status})
        connection.execute(
            """
            UPDATE trading_plans
            SET status = ?, payload_json = ?
            WHERE plan_id = ?
            """,
            (status.value, inactive_plan.model_dump_json(), inactive_plan.plan_id),
        )
        connection.commit()

        with pytest.raises(PlanNotScannableError):
            scan_latest_plan_recommendations(
                connection,
                now=datetime(2026, 7, 9, 6, 0, tzinfo=UTC),
            )

        assert RecommendationRepository(connection).list() == []
