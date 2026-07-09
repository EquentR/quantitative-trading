from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from quantitative_trading.account.models import AccountSnapshot
from quantitative_trading.account.repository import AccountSnapshotRepository
from quantitative_trading.ledger.models import Position
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.planning.models import TradingPlan, TradingPlanStatus
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.recommendation.models import Recommendation
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.recommendation.service import build_recommendation
from quantitative_trading.risk.models import RiskConfig, RiskDecision
from quantitative_trading.risk.service import apply_risk
from quantitative_trading.strategy.models import StrategyAction, StrategySignal
from quantitative_trading.universe.models import UniverseMember
from quantitative_trading.universe.repository import UniverseSnapshotRepository


@dataclass(frozen=True)
class RecommendationScan:
    plan: TradingPlan
    recommendations: list[Recommendation]


class PlanNotScannableError(ValueError):
    def __init__(self, plan: TradingPlan, *, now: datetime) -> None:
        super().__init__("trading plan is not scannable")
        self.plan_id = plan.plan_id
        self.status = plan.status
        self.valid_until = plan.valid_until
        self.now = now


def scan_latest_plan_recommendations(
    connection: sqlite3.Connection,
    *,
    now: datetime,
) -> RecommendationScan | None:
    plan = TradingPlanRepository(connection).latest()
    if plan is None:
        return None
    if not _is_plan_scannable(plan, now=now):
        raise PlanNotScannableError(plan, now=now)

    universe_snapshot = UniverseSnapshotRepository(connection).get(plan.universe_snapshot_id)
    members = [] if universe_snapshot is None else universe_snapshot.members
    positions = PositionRepository(connection).list()
    positions_by_symbol = {position.symbol: position for position in positions}
    account_snapshot = AccountSnapshotRepository(connection).latest()

    recommendations = [
        _build_conservative_recommendation(
            plan=plan,
            member=member,
            position=positions_by_symbol.get(member.symbol),
            account_snapshot=account_snapshot,
            now=now,
        )
        for member in members
        if _is_scannable_member(plan, member, positions_by_symbol)
    ]
    RecommendationRepository(connection).save_many(recommendations, created_at=now)
    return RecommendationScan(plan=plan, recommendations=recommendations)


def _is_plan_scannable(plan: TradingPlan, *, now: datetime) -> bool:
    return plan.status is TradingPlanStatus.ACTIVE and now <= plan.valid_until


def _is_scannable_member(
    plan: TradingPlan,
    member: UniverseMember,
    positions_by_symbol: dict[str, Position],
) -> bool:
    if member.symbol in positions_by_symbol:
        return True
    return member.symbol in plan.watch_symbols and member.plan_enabled


def _build_conservative_recommendation(
    *,
    plan: TradingPlan,
    member: UniverseMember,
    position: Position | None,
    account_snapshot: AccountSnapshot | None,
    now: datetime,
) -> Recommendation:
    signal = _signal_for_member(plan=plan, member=member, position=position)
    risk_decision = apply_risk(signal, account_snapshot, position, RiskConfig())
    risk_notes = list(risk_decision.reasons)
    risk_notes.append("未接入实时行情，建议仅作为人工复核的保守观察")
    risk_decision = RiskDecision(
        allowed=risk_decision.allowed,
        original_action=risk_decision.original_action,
        action=risk_decision.action,
        reasons=risk_notes,
    )
    return build_recommendation(
        signal,
        risk_decision,
        recommendation_id=f"rec-{plan.plan_id}-{member.symbol}",
        name=member.name,
        position_context=_position_context(position),
        account_context=_account_context(account_snapshot),
        price_context=_price_context(plan, member.symbol, account_snapshot),
        valid_until=plan.valid_until,
        data_time=now,
    )


def _signal_for_member(
    *,
    plan: TradingPlan,
    member: UniverseMember,
    position: Position | None,
) -> StrategySignal:
    if position is not None:
        return StrategySignal(
            symbol=member.symbol,
            action=StrategyAction.HOLD,
            confidence="low",
            machine_reason=["manual_holding_in_plan", "market_data_missing"],
            human_reason=["手动持仓已纳入计划，缺少实时行情时仅维持持有复核"],
            invalid_if=plan.invalid_if.get(
                member.symbol,
                ["跌破计划支撑位或手动持仓台账发生变化"],
            ),
        )
    return StrategySignal(
        symbol=member.symbol,
        action=StrategyAction.WATCH,
        confidence="low",
        machine_reason=["watch_symbol_in_plan", "market_data_missing"],
        human_reason=["自选置顶标的已启用计划，缺少实时行情时仅观察"],
        invalid_if=plan.invalid_if.get(
            member.symbol,
            ["计划前行情、量能或资金上下文不可用"],
        ),
    )


def _position_context(position: Position | None) -> dict[str, Any]:
    if position is None:
        return {
            "source": "manual_ledger",
            "status": "no_position",
        }
    return {
        "source": "manual_ledger",
        "ledger_updated_at": position.updated_at.isoformat(),
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "available_quantity": position.available_quantity,
    }


def _account_context(account_snapshot: AccountSnapshot | None) -> dict[str, Any]:
    if account_snapshot is None:
        return {
            "source": "manual_cash_account",
            "status": "account_snapshot_missing",
        }
    return {
        "source": "manual_cash_account",
        "cash_balance": account_snapshot.cash_balance,
        "net_principal": account_snapshot.net_principal,
        "market_value": account_snapshot.market_value,
        "total_assets": account_snapshot.total_assets,
        "position_ratio": account_snapshot.position_ratio,
        "account_snapshot_time": account_snapshot.created_at.isoformat(),
        "status": account_snapshot.status.value,
        "warnings": account_snapshot.warnings,
    }


def _price_context(
    plan: TradingPlan,
    symbol: str,
    account_snapshot: AccountSnapshot | None,
) -> dict[str, Any]:
    valuation = None
    if account_snapshot is not None:
        valuation = next(
            (
                position
                for position in account_snapshot.positions
                if position.symbol == symbol
            ),
            None,
        )
    return {
        "current_price": None if valuation is None else valuation.current_price,
        "change_pct": None,
        "key_levels": plan.key_levels.get(symbol, {}),
    }
