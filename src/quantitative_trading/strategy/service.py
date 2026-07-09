from __future__ import annotations

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
