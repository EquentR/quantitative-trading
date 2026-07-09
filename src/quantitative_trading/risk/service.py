from __future__ import annotations

from typing import Any

from quantitative_trading.account.models import AccountSnapshot, AccountSnapshotStatus
from quantitative_trading.risk.models import RiskConfig, RiskDecision
from quantitative_trading.strategy.models import StrategyAction, StrategySignal


BUY_SIDE_ACTIONS = {StrategyAction.BUY, StrategyAction.ADD}
SELL_SIDE_ACTIONS = {StrategyAction.SELL, StrategyAction.REDUCE}


def _percent(value: float) -> str:
    return f"{value:.2%}"


def _read_number(source: Any, field_name: str) -> float | int | None:
    if source is None:
        return None
    if isinstance(source, dict):
        value = source.get(field_name)
    else:
        value = getattr(source, field_name, None)
    if isinstance(value, int | float):
        return value
    return None


def _snapshot_allows_buy_side(account_snapshot: AccountSnapshot | None) -> tuple[bool, str | None]:
    if account_snapshot is None:
        return False, "账户快照缺失，禁止新增买入或加仓"
    if account_snapshot.status is not AccountSnapshotStatus.OK:
        return False, f"账户快照状态为 {account_snapshot.status.value}，禁止新增买入或加仓"
    return True, None


def apply_risk(
    signal: StrategySignal,
    account_snapshot: AccountSnapshot | None,
    position: Any | None,
    config: RiskConfig,
) -> RiskDecision:
    reasons: list[str] = []
    final_action = signal.action

    if signal.action in BUY_SIDE_ACTIONS:
        snapshot_allowed, snapshot_reason = _snapshot_allows_buy_side(account_snapshot)
        if not snapshot_allowed and snapshot_reason is not None:
            reasons.append(snapshot_reason)

        position_ratio = _read_number(account_snapshot, "position_ratio")
        if position_ratio is None:
            reasons.append("账户快照缺少总仓位比例，禁止新增买入或加仓")
        elif position_ratio > config.total_position_limit:
            reasons.append(
                f"总仓位 {_percent(position_ratio)} 高于上限 {_percent(config.total_position_limit)}，禁止新增买入"
            )

    if signal.action is StrategyAction.ADD:
        market_value = _read_number(position, "market_value")
        total_assets = _read_number(account_snapshot, "total_assets")
        if market_value is None or total_assets is None or total_assets <= 0:
            reasons.append("缺少单票市值或总资产，禁止加仓")
        else:
            single_position_ratio = market_value / total_assets
            if single_position_ratio > config.single_position_limit:
                reasons.append(
                    f"单票仓位 {_percent(single_position_ratio)} 高于上限 {_percent(config.single_position_limit)}，禁止加仓"
                )

    if signal.action in SELL_SIDE_ACTIONS:
        available_quantity = _read_number(position, "available_quantity")
        if available_quantity is None:
            reasons.append("缺少可用数量，禁止卖出或减仓")
        elif available_quantity <= 0:
            reasons.append("可用数量为 0，受 T+1 可卖数量约束，禁止卖出或减仓")

    if reasons:
        if signal.action in BUY_SIDE_ACTIONS:
            final_action = StrategyAction.WATCH
        elif signal.action in SELL_SIDE_ACTIONS:
            final_action = StrategyAction.HOLD
        else:
            final_action = StrategyAction.AVOID

    return RiskDecision(
        allowed=not reasons,
        original_action=signal.action,
        action=final_action,
        reasons=reasons,
    )
