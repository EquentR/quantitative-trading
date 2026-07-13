from __future__ import annotations

from math import floor, isfinite
from typing import Any

from quantitative_trading.account.models import AccountSnapshot, AccountSnapshotStatus
from quantitative_trading.risk.models import (
    PositionConstraint,
    RiskConfig,
    RiskContext,
    RiskDecision,
)
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
    context: RiskContext | None = None,
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

        if context is not None:
            available_cash = _read_number(account_snapshot, "available_buying_cash")
            if available_cash is None:
                reasons.append("账户快照缺少可用买入资金，禁止新增买入或加仓")
            elif context.proposed_value > available_cash:
                reasons.append("建议金额超过可用买入资金，禁止新增买入或加仓")

            total_assets = _read_number(account_snapshot, "total_assets")
            if total_assets is None or total_assets <= 0:
                reasons.append("账户快照缺少总资产，无法校验单日新增买入上限")
            elif (
                context.daily_new_buy_value + context.proposed_value
                > total_assets * config.daily_new_buy_limit
            ):
                reasons.append(
                    "单日新增买入金额超过"
                    f"总资产的 {_percent(config.daily_new_buy_limit)} 上限"
                )

            if context.daily_trade_count >= config.daily_trade_count_limit:
                reasons.append(
                    f"当日交易次数已达到 {config.daily_trade_count_limit} 次上限"
                )
            if context.in_loss_cooldown:
                reasons.append(
                    f"连续亏损 {context.consecutive_losses} 次，处于连续亏损冷却期"
                )
            if (
                context.liquidity_amount is not None
                and context.liquidity_amount < config.liquidity_amount_threshold
            ):
                reasons.append("成交额低于流动性阈值，禁止新增买入或加仓")

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
            if context is not None:
                projected_ratio = (market_value + context.proposed_value) / total_assets
                if projected_ratio > config.single_position_limit:
                    reasons.append(
                        "加仓后单票仓位 "
                        f"{_percent(projected_ratio)} 高于上限 "
                        f"{_percent(config.single_position_limit)}，禁止加仓"
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


def calculate_buy_constraint(
    *,
    current_price: float,
    total_assets: float,
    available_cash: float,
    current_position_value: float,
    current_total_position_value: float,
    current_daily_new_buy_value: float,
    has_position: bool,
    config: RiskConfig,
    requested_value: float | None = None,
) -> PositionConstraint:
    numeric_values = (
        current_price,
        total_assets,
        available_cash,
        current_position_value,
        current_total_position_value,
        current_daily_new_buy_value,
    )
    if not all(isfinite(value) for value in numeric_values):
        raise ValueError("position constraint inputs must be finite")
    if current_price <= 0 or total_assets <= 0:
        raise ValueError("current price and total assets must be positive")
    if any(value < 0 for value in numeric_values[2:]):
        raise ValueError("position constraint balances cannot be negative")
    if requested_value is not None and (
        not isfinite(requested_value) or requested_value < 0
    ):
        raise ValueError("requested value must be finite and non-negative")

    capacities = {
        "available_cash": available_cash,
        "single_position_limit": max(
            0.0,
            total_assets * config.single_position_limit - current_position_value,
        ),
        "total_position_limit": max(
            0.0,
            total_assets * config.total_position_limit - current_total_position_value,
        ),
        "daily_new_buy_limit": max(
            0.0,
            total_assets * config.daily_new_buy_limit - current_daily_new_buy_value,
        ),
    }
    if not has_position:
        capacities["first_watch_position_max"] = (
            total_assets * config.first_watch_position_max
        )
    if requested_value is not None and requested_value > 0:
        capacities["requested_value"] = requested_value

    max_value = min(capacities.values())
    limiting_factors = [
        name
        for name, value in capacities.items()
        if abs(value - max_value) <= 1e-9
    ]
    quantity = floor(max_value / current_price / 100) * 100
    suggested_value = quantity * current_price

    return PositionConstraint(
        suggested_quantity=quantity,
        suggested_value=suggested_value,
        max_position_ratio=config.single_position_limit,
        max_total_position_ratio=config.total_position_limit,
        max_daily_new_buy_ratio=config.daily_new_buy_limit,
        limiting_factors=limiting_factors,
    )
