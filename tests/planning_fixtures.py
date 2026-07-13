from datetime import UTC, date, datetime, time, timedelta

from quantitative_trading.config import Settings
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.planning.models import TradingPlan, TradingPlanStatus
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.universe.models import (
    UniverseSnapshot,
    UniverseSnapshotStatus,
)
from quantitative_trading.universe.repository import UniverseSnapshotRepository
from quantitative_trading.universe.service import build_universe
from quantitative_trading.watchlist.repository import WatchPinnedRepository


def persist_test_plan(
    settings: Settings,
    *,
    trading_day: date = date(2026, 7, 9),
) -> TradingPlan:
    generated_at = datetime.combine(
        trading_day - timedelta(days=1),
        time(7, 5),
        tzinfo=UTC,
    )
    with connect(settings) as connection:
        migrate(connection)
        positions = PositionRepository(connection).list()
        members = build_universe(
            positions=positions,
            watchlist=WatchPinnedRepository(connection).list(),
            created_at=generated_at,
        )
        universe_snapshot_id = UniverseSnapshotRepository(connection).save(
            UniverseSnapshot(
                created_at=generated_at,
                status=UniverseSnapshotStatus.OK,
                warnings=[],
                members=members,
            )
        )
        holding_symbols = [position.symbol for position in positions]
        holding_set = set(holding_symbols)
        watch_symbols = [
            member.symbol
            for member in members
            if member.symbol not in holding_set and member.plan_enabled
        ]
        symbols = [*holding_symbols, *watch_symbols]
        plan = TradingPlan(
            plan_id=f"plan-{trading_day:%Y%m%d}",
            trading_day=trading_day,
            generated_at=generated_at,
            valid_until=datetime.combine(trading_day, time(7), tzinfo=UTC),
            universe_snapshot_id=universe_snapshot_id,
            watch_symbols=watch_symbols,
            holding_symbols=holding_symbols,
            key_levels={symbol: {} for symbol in symbols},
            candidate_actions={
                symbol: ["hold", "reduce"] if symbol in holding_set else ["watch"]
                for symbol in symbols
            },
            invalid_if={
                symbol: ["test plan invalidation condition"] for symbol in symbols
            },
            warnings=[],
            status=TradingPlanStatus.ACTIVE,
        )
        return TradingPlanRepository(connection).save(plan)
