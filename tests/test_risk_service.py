from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.account.models import (
    AccountSnapshot,
    AccountSnapshotStatus,
    PositionValuation,
    PositionValuationStatus,
)
from quantitative_trading.risk.models import RiskConfig
from quantitative_trading.risk.service import apply_risk
from quantitative_trading.strategy.models import StrategyAction, StrategySignal


NOW = datetime(2026, 7, 9, 2, 30, tzinfo=UTC)


def signal(action: StrategyAction) -> StrategySignal:
    return StrategySignal(
        symbol="600000",
        action=action,
        confidence="medium",
        machine_reason=["ma_short_above"],
        human_reason=["站上短期均线"],
        invalid_if=["跌破 9.7"],
    )


def valuation(**overrides: object) -> PositionValuation:
    data: dict[str, object] = {
        "symbol": "600000",
        "name": "浦发银行",
        "quantity": 1000,
        "available_quantity": 1000,
        "cost_price": 9.5,
        "position_cost": 9500,
        "current_price": 10.0,
        "market_value": 10000,
        "floating_pnl": 500,
        "floating_pnl_pct": 500 / 9500,
        "ledger_updated_at": NOW,
        "quote_data_time": NOW,
        "quote_fetched_at": NOW,
        "status": PositionValuationStatus.OK,
        "warning": "",
    }
    data.update(overrides)
    return PositionValuation(**data)


def snapshot(**overrides: object) -> AccountSnapshot:
    data: dict[str, object] = {
        "cash_balance": 50000,
        "net_principal": 70000,
        "market_value": 10000,
        "position_cost": 9500,
        "floating_pnl": 500,
        "floating_pnl_pct": 500 / 9500,
        "total_assets": 60000,
        "total_pnl": 10000,
        "total_pnl_pct": 10000 / 70000,
        "position_ratio": 10000 / 60000,
        "available_buying_cash": 50000,
        "positions": [valuation()],
        "status": AccountSnapshotStatus.OK,
        "warnings": [],
        "created_at": NOW,
    }
    data.update(overrides)
    return AccountSnapshot(**data)


def test_risk_config_defaults_match_first_version_policy() -> None:
    config = RiskConfig()

    assert config.single_position_limit == 0.30
    assert config.total_position_limit == 0.80
    assert config.daily_new_buy_limit == 0.20
    assert config.daily_trade_count_limit == 3
    assert config.first_watch_position_min == 0.10
    assert config.first_watch_position_max == 0.15
    assert config.loss_cooldown_count == 2
    assert config.loss_cooldown_trading_days == 1
    assert config.liquidity_amount_threshold == 0


@pytest.mark.parametrize(
    "field",
    [
        "single_position_limit",
        "total_position_limit",
        "daily_new_buy_limit",
        "first_watch_position_min",
        "first_watch_position_max",
        "daily_trade_count_limit",
        "loss_cooldown_count",
        "loss_cooldown_trading_days",
    ],
)
def test_risk_config_rejects_zero_for_positive_limits(field: str) -> None:
    with pytest.raises(ValidationError):
        RiskConfig(**{field: 0})


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (AccountSnapshotStatus.PARTIAL, StrategyAction.BUY),
        (AccountSnapshotStatus.MARKET_DATA_UNAVAILABLE, StrategyAction.BUY),
        (AccountSnapshotStatus.CASH_NOT_INITIALIZED, StrategyAction.ADD),
    ],
)
def test_partial_or_unavailable_account_snapshot_blocks_buy_side_and_downgrades_to_watch(
    status: AccountSnapshotStatus,
    action: StrategyAction,
) -> None:
    decision = apply_risk(
        signal(action),
        snapshot(
            cash_balance=None,
            net_principal=None,
            market_value=None,
            position_cost=None,
            floating_pnl=None,
            total_assets=None,
            total_pnl=None,
            position_ratio=None,
            available_buying_cash=None,
            status=status,
            warnings=["market quote missing"],
        ),
        position=valuation() if action is StrategyAction.ADD else None,
        config=RiskConfig(),
    )

    assert decision.allowed is False
    assert decision.action is StrategyAction.WATCH
    assert any("账户快照" in reason for reason in decision.reasons)


def test_available_quantity_zero_blocks_sell_and_downgrades_to_hold() -> None:
    decision = apply_risk(
        signal(StrategyAction.SELL),
        snapshot(),
        position=valuation(available_quantity=0),
        config=RiskConfig(),
    )

    assert decision.allowed is False
    assert decision.action is StrategyAction.HOLD
    assert any("T+1" in reason for reason in decision.reasons)


def test_total_position_ratio_above_limit_blocks_buy() -> None:
    decision = apply_risk(
        signal(StrategyAction.BUY),
        snapshot(position_ratio=0.81),
        position=None,
        config=RiskConfig(total_position_limit=0.80),
    )

    assert decision.allowed is False
    assert decision.action is StrategyAction.WATCH
    assert any("总仓位" in reason for reason in decision.reasons)


def test_single_position_ratio_above_limit_blocks_add() -> None:
    decision = apply_risk(
        signal(StrategyAction.ADD),
        snapshot(total_assets=60000),
        position=valuation(market_value=20000),
        config=RiskConfig(single_position_limit=0.30),
    )

    assert decision.allowed is False
    assert decision.action is StrategyAction.WATCH
    assert any("单票仓位" in reason for reason in decision.reasons)
