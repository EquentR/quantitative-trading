from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from quantitative_trading.account.repository import AccountSnapshotRepository
from quantitative_trading.ledger.models import Position
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.planning.models import TradingPlan, TradingPlanStatus
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.planning.service import plan_valid_until
from quantitative_trading.universe.models import UniverseSnapshot, UniverseSnapshotStatus
from quantitative_trading.universe.repository import UniverseSnapshotRepository
from quantitative_trading.universe.service import build_universe
from quantitative_trading.watchlist.repository import WatchPinnedRepository


@dataclass(frozen=True)
class CreatedTradingPlan:
    plan_id: str
    plan: TradingPlan


def generate_trading_plan(
    connection: sqlite3.Connection,
    *,
    trading_day: date,
    now: datetime,
    timezone: str,
) -> CreatedTradingPlan:
    positions = PositionRepository(connection).list()
    watchlist = WatchPinnedRepository(connection).list()
    members = build_universe(positions=positions, watchlist=watchlist, created_at=now)
    snapshot = UniverseSnapshot(
        created_at=now,
        status=UniverseSnapshotStatus.OK,
        warnings=[],
        members=members,
    )
    universe_snapshot_id = UniverseSnapshotRepository(connection).save(snapshot)
    latest_account = AccountSnapshotRepository(connection).latest()

    positions_by_symbol = {position.symbol: position for position in positions}
    holding_symbols = [position.symbol for position in positions]
    watch_symbols = [
        member.symbol
        for member in members
        if member.symbol not in positions_by_symbol and member.plan_enabled
    ]

    plan_id = f"plan-{trading_day:%Y%m%d}"
    plan = TradingPlan(
        plan_id=plan_id,
        trading_day=trading_day,
        generated_at=now.astimezone(UTC),
        valid_until=plan_valid_until(
            datetime.combine(trading_day, time(12, 0), tzinfo=ZoneInfo(timezone)),
            timezone=timezone,
        ),
        universe_snapshot_id=universe_snapshot_id,
        account_snapshot_id=None,
        ledger_max_updated_at=_ledger_max_updated_at(positions),
        watch_symbols=watch_symbols,
        holding_symbols=holding_symbols,
        key_levels=_key_levels(positions),
        candidate_actions=_candidate_actions(positions_by_symbol, watch_symbols),
        invalid_if=_invalid_if(positions_by_symbol, watch_symbols),
        warnings=_plan_warnings(
            positions=positions,
            watch_symbols=watch_symbols,
            has_account=latest_account is not None,
        ),
        status=TradingPlanStatus.ACTIVE,
    )
    TradingPlanRepository(connection).save(plan)
    return CreatedTradingPlan(plan_id=plan_id, plan=plan)


def _ledger_max_updated_at(positions: list[Position]) -> datetime | None:
    if not positions:
        return None
    return max(position.updated_at for position in positions)


def _key_levels(positions: list[Position]) -> dict[str, dict[str, float]]:
    levels: dict[str, dict[str, float]] = {}
    for position in positions:
        levels[position.symbol] = {
            "support": round(position.cost_price * 0.97, 3),
            "stop_loss": round(position.cost_price * 0.95, 3),
            "resistance": round(position.cost_price * 1.05, 3),
        }
    return levels


def _candidate_actions(
    positions_by_symbol: dict[str, Position],
    watch_symbols: list[str],
) -> dict[str, list[str]]:
    actions: dict[str, list[str]] = {
        symbol: ["hold", "reduce"] for symbol in positions_by_symbol
    }
    for symbol in watch_symbols:
        actions[symbol] = ["watch"]
    return actions


def _invalid_if(
    positions_by_symbol: dict[str, Position],
    watch_symbols: list[str],
) -> dict[str, list[str]]:
    invalid_if: dict[str, list[str]] = {
        symbol: ["跌破计划支撑位或手动持仓台账发生变化"]
        for symbol in positions_by_symbol
    }
    for symbol in watch_symbols:
        invalid_if[symbol] = ["计划前行情、量能或资金上下文不可用"]
    return invalid_if


def _plan_warnings(
    *,
    positions: list[Position],
    watch_symbols: list[str],
    has_account: bool,
) -> list[str]:
    warnings: list[str] = []
    if not positions and not watch_symbols:
        warnings.append("股票池为空，计划不包含可决策标的")
    if not has_account:
        warnings.append("账户快照缺失，后续买入或加仓建议应由风控降级")
    warnings.append("未接入实时行情，本计划仅使用手动台账和自选置顶生成")
    return warnings
