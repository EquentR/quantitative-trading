from __future__ import annotations

from typing import Literal

from quantitative_trading.strategy.models import Confidence, StrategyAction, StrategySignal


def _format_price(value: float) -> str:
    return f"{value:.2f}"


def holding_risk_signals(
    *,
    symbol: str,
    current_price: float,
    support_price: float | None = None,
    stop_loss_price: float | None = None,
) -> list[StrategySignal]:
    if stop_loss_price is not None and current_price <= stop_loss_price:
        return [
            StrategySignal(
                symbol=symbol,
                action=StrategyAction.SELL,
                confidence="high",
                machine_reason=["stop_loss_break"],
                human_reason=["跌破止损位"],
                invalid_if=[f"重新站回 {_format_price(stop_loss_price)} 并完成复核"],
            ),
        ]

    if support_price is not None and current_price < support_price:
        return [
            StrategySignal(
                symbol=symbol,
                action=StrategyAction.REDUCE,
                confidence="medium",
                machine_reason=["support_break"],
                human_reason=["跌破关键支撑"],
                invalid_if=[f"重新站回 {_format_price(support_price)}"],
            ),
        ]

    return []


def holding_watch_signals(
    *,
    symbol: str,
    current_price: float,
    support_price: float | None = None,
    short_ma: float | None = None,
    confidence: Confidence = "medium",
) -> list[StrategySignal]:
    if short_ma is None or current_price < short_ma:
        return []
    if support_price is not None and current_price < support_price:
        return []

    invalid_if = "跌破短期均线"
    if support_price is not None:
        invalid_if = f"跌破 {_format_price(support_price)}"

    return [
        StrategySignal(
            symbol=symbol,
            action=StrategyAction.HOLD,
            confidence=confidence,
            machine_reason=["ma_short_above"],
            human_reason=["站上短期均线"],
            invalid_if=[invalid_if],
        ),
    ]


def watch_buy_observation_signals(
    *,
    symbol: str,
    plan_enabled: bool,
    current_price: float,
    breakout_price: float | None = None,
    short_ma: float | None = None,
    volume_ratio: float | None = None,
) -> list[StrategySignal]:
    if not plan_enabled:
        return []
    if breakout_price is None or short_ma is None or volume_ratio is None:
        return []
    if current_price < breakout_price or current_price < short_ma or volume_ratio < 1.5:
        return []

    return [
        StrategySignal(
            symbol=symbol,
            action=StrategyAction.WATCH,
            confidence="medium",
            machine_reason=["breakout_observation", "ma_short_above", "volume_expansion"],
            human_reason=["放量突破观察位", "站上短期均线"],
            invalid_if=[f"跌破 {_format_price(breakout_price)}", "成交量回落至计划阈值下方"],
        ),
    ]


def planned_entry_signal(
    *,
    symbol: str,
    has_position: bool,
    plan_active: bool,
    plan_allows_entry: bool,
    plan_condition_met: bool,
    daily_structure_confirmed: bool,
    intraday_strength: Literal["strong", "neutral", "weak"],
    money_flow_confirmed: bool | None,
    money_flow_applicable: bool = True,
    data_quality: Literal["complete", "degraded", "failed", "stale"],
    invalid_if: list[str],
) -> StrategySignal:
    if data_quality == "failed":
        return StrategySignal(
            symbol=symbol,
            action=StrategyAction.AVOID,
            confidence="low",
            machine_reason=["required_market_data_failed"],
            human_reason=["关键行情数据不可用，当前不参与"],
            invalid_if=invalid_if,
        )

    blockers: list[tuple[str, str]] = []
    if not plan_active:
        blockers.append(("active_plan_missing", "缺少当日有效收盘计划"))
    if not plan_allows_entry:
        blockers.append(("plan_entry_not_allowed", "收盘计划未允许新增买入或加仓"))
    if not plan_condition_met:
        blockers.append(("plan_condition_not_met", "尚未命中计划内条件"))
    if not daily_structure_confirmed:
        blockers.append(("daily_structure_not_confirmed", "日线结构尚未确认"))
    if intraday_strength != "strong":
        blockers.append(("intraday_strength_not_confirmed", "分时强弱尚未确认"))
    if money_flow_confirmed is False:
        blockers.append(("money_flow_filtered", "资金流条件形成过滤"))
    if data_quality == "stale":
        blockers.append(("market_data_stale", "行情数据已经过期"))

    if blockers:
        return StrategySignal(
            symbol=symbol,
            action=StrategyAction.WATCH,
            confidence="low",
            machine_reason=[machine_reason for machine_reason, _ in blockers],
            human_reason=[human_reason for _, human_reason in blockers],
            invalid_if=invalid_if,
        )

    machine_reason = [
        "active_plan_gate_passed",
        "plan_condition_met",
        "daily_structure_confirmed",
        "intraday_strength_confirmed",
    ]
    human_reason = ["命中当日活动计划", "日线结构与分时强弱共同确认"]
    if money_flow_confirmed is True:
        machine_reason.append("money_flow_confirmed")
        human_reason.append("资金流提供额外确认")
    elif not money_flow_applicable:
        machine_reason.append("money_flow_not_applicable")
        human_reason.append("该证券资金流数据不适用，未计入确认")
    else:
        machine_reason.append("money_flow_unavailable")
        human_reason.append("资金流不可用，未计入确认")

    confidence: Confidence = (
        "high"
        if money_flow_confirmed is True and data_quality == "complete"
        else "medium"
    )
    return StrategySignal(
        symbol=symbol,
        action=StrategyAction.ADD if has_position else StrategyAction.BUY,
        confidence=confidence,
        machine_reason=machine_reason,
        human_reason=human_reason,
        invalid_if=invalid_if,
    )
