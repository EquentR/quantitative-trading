from quantitative_trading.strategy.models import StrategyAction, StrategySignal
from quantitative_trading.strategy.service import (
    holding_risk_signals,
    holding_watch_signals,
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
