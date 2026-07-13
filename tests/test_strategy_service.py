import pytest

from quantitative_trading.strategy.models import StrategyAction, StrategySignal
from quantitative_trading.strategy.service import (
    holding_risk_signals,
    holding_watch_signals,
    planned_entry_signal,
    watch_buy_observation_signals,
)


def assert_signal_contract(signal: StrategySignal) -> None:
    dumped = signal.model_dump(mode="json")

    assert dumped["symbol"] == "600000"
    assert dumped["action"] in {action.value for action in StrategyAction}
    assert dumped["confidence"] in {"low", "medium", "high"}
    assert dumped["machine_reason"]
    assert dumped["human_reason"]
    assert dumped["invalid_if"]


def test_holding_risk_signals_reduce_when_price_breaks_support() -> None:
    signals = holding_risk_signals(
        symbol="600000",
        current_price=9.60,
        support_price=9.70,
        stop_loss_price=9.30,
    )

    assert len(signals) == 1
    assert signals[0].action is StrategyAction.REDUCE
    assert "support_break" in signals[0].machine_reason
    assert "跌破关键支撑" in signals[0].human_reason
    assert_signal_contract(signals[0])


def test_holding_risk_signals_sell_when_price_breaks_stop_loss() -> None:
    signals = holding_risk_signals(
        symbol="600000",
        current_price=9.30,
        support_price=9.70,
        stop_loss_price=9.30,
    )

    assert len(signals) == 1
    assert signals[0].action is StrategyAction.SELL
    assert "stop_loss_break" in signals[0].machine_reason
    assert "跌破止损位" in signals[0].human_reason
    assert_signal_contract(signals[0])


def test_holding_watch_signals_hold_when_short_ma_is_above_and_support_intact() -> None:
    signals = holding_watch_signals(
        symbol="600000",
        current_price=10.10,
        support_price=9.70,
        short_ma=10.00,
    )

    assert len(signals) == 1
    assert signals[0].action is StrategyAction.HOLD
    assert "ma_short_above" in signals[0].machine_reason
    assert "站上短期均线" in signals[0].human_reason
    assert_signal_contract(signals[0])


def test_watch_buy_observation_signals_match_breakout_rules() -> None:
    signals = watch_buy_observation_signals(
        symbol="600000",
        plan_enabled=True,
        current_price=10.50,
        breakout_price=10.40,
        short_ma=10.20,
        volume_ratio=1.6,
    )

    assert len(signals) == 1
    assert signals[0].action is StrategyAction.WATCH
    assert "breakout_observation" in signals[0].machine_reason
    assert "放量突破观察位" in signals[0].human_reason
    assert_signal_contract(signals[0])


def test_watch_buy_observation_signals_skip_disabled_plan_items() -> None:
    signals = watch_buy_observation_signals(
        symbol="600000",
        plan_enabled=False,
        current_price=10.50,
        breakout_price=10.40,
        short_ma=10.20,
        volume_ratio=1.6,
    )

    assert signals == []


def test_planned_entry_signal_emits_buy_after_plan_and_two_factor_confirmation() -> None:
    signal = planned_entry_signal(
        symbol="600000",
        has_position=False,
        plan_active=True,
        plan_allows_entry=True,
        plan_condition_met=True,
        daily_structure_confirmed=True,
        intraday_strength="strong",
        money_flow_confirmed=True,
        data_quality="complete",
        invalid_if=["跌破计划支撑位", "计划当日收盘失效"],
    )

    assert signal.action is StrategyAction.BUY
    assert signal.confidence == "high"
    assert "active_plan_gate_passed" in signal.machine_reason
    assert "daily_structure_confirmed" in signal.machine_reason
    assert "intraday_strength_confirmed" in signal.machine_reason
    assert "money_flow_confirmed" in signal.machine_reason


def test_planned_entry_signal_emits_add_for_existing_position() -> None:
    signal = planned_entry_signal(
        symbol="600000",
        has_position=True,
        plan_active=True,
        plan_allows_entry=True,
        plan_condition_met=True,
        daily_structure_confirmed=True,
        intraday_strength="strong",
        money_flow_confirmed=None,
        data_quality="degraded",
        invalid_if=["跌破计划支撑位"],
    )

    assert signal.action is StrategyAction.ADD
    assert signal.confidence == "medium"
    assert "money_flow_unavailable" in signal.machine_reason


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"plan_active": False}, "active_plan_missing"),
        ({"plan_allows_entry": False}, "plan_entry_not_allowed"),
        ({"plan_condition_met": False}, "plan_condition_not_met"),
        ({"daily_structure_confirmed": False}, "daily_structure_not_confirmed"),
        ({"intraday_strength": "neutral"}, "intraday_strength_not_confirmed"),
        ({"money_flow_confirmed": False}, "money_flow_filtered"),
    ],
)
def test_planned_entry_signal_downgrades_unconfirmed_opportunity_to_watch(
    overrides: dict[str, object],
    reason: str,
) -> None:
    arguments: dict[str, object] = {
        "symbol": "600000",
        "has_position": False,
        "plan_active": True,
        "plan_allows_entry": True,
        "plan_condition_met": True,
        "daily_structure_confirmed": True,
        "intraday_strength": "strong",
        "money_flow_confirmed": True,
        "data_quality": "complete",
        "invalid_if": ["跌破计划支撑位"],
    }
    arguments.update(overrides)

    signal = planned_entry_signal(**arguments)

    assert signal.action is StrategyAction.WATCH
    assert signal.confidence == "low"
    assert reason in signal.machine_reason


def test_planned_entry_signal_avoids_symbol_with_failed_required_data() -> None:
    signal = planned_entry_signal(
        symbol="600000",
        has_position=False,
        plan_active=True,
        plan_allows_entry=True,
        plan_condition_met=True,
        daily_structure_confirmed=True,
        intraday_strength="strong",
        money_flow_confirmed=True,
        data_quality="failed",
        invalid_if=["数据恢复前不参与"],
    )

    assert signal.action is StrategyAction.AVOID
    assert "required_market_data_failed" in signal.machine_reason
