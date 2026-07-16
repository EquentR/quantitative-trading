from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.account.models import (
    AccountSnapshot,
    AccountSnapshotStatus,
    PositionValuation,
    PositionValuationStatus,
)
from quantitative_trading.risk.models import RiskConfig, RiskContext
from quantitative_trading.risk.service import apply_risk, calculate_buy_constraint
from quantitative_trading.strategy.models import StrategyAction, StrategySignal
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)


NOW = datetime(2026, 7, 9, 2, 30, tzinfo=UTC)


def a_share_metadata() -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol="600000",
        name="浦发银行",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="exchange_catalog",
        metadata_checked_at=NOW,
        rule_version="instrument-rules-v1",
    )


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
        context=RiskContext(instrument=a_share_metadata()),
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


def test_proposed_buy_cannot_exceed_available_cash() -> None:
    decision = apply_risk(
        signal(StrategyAction.BUY),
        snapshot(available_buying_cash=5000),
        position=None,
        config=RiskConfig(),
        context=RiskContext(proposed_value=6000),
    )

    assert decision.allowed is False
    assert decision.action is StrategyAction.WATCH
    assert any("可用买入资金" in reason for reason in decision.reasons)


def test_daily_new_buy_limit_includes_proposed_value() -> None:
    decision = apply_risk(
        signal(StrategyAction.BUY),
        snapshot(total_assets=60000),
        position=None,
        config=RiskConfig(daily_new_buy_limit=0.20),
        context=RiskContext(
            proposed_value=4000,
            daily_new_buy_value=9000,
        ),
    )

    assert decision.allowed is False
    assert any("单日新增买入" in reason for reason in decision.reasons)


def test_trade_count_and_loss_cooldown_block_new_buy() -> None:
    decision = apply_risk(
        signal(StrategyAction.BUY),
        snapshot(),
        position=None,
        config=RiskConfig(daily_trade_count_limit=3),
        context=RiskContext(
            proposed_value=1000,
            daily_trade_count=3,
            in_loss_cooldown=True,
            consecutive_losses=2,
        ),
    )

    assert decision.allowed is False
    assert any("交易次数" in reason for reason in decision.reasons)
    assert any("连续亏损冷却" in reason for reason in decision.reasons)


def test_liquidity_threshold_blocks_buy_but_not_risk_sell() -> None:
    config = RiskConfig(liquidity_amount_threshold=1_000_000)
    context = RiskContext(liquidity_amount=900_000)

    buy_decision = apply_risk(
        signal(StrategyAction.BUY),
        snapshot(),
        position=None,
        config=config,
        context=context,
    )
    sell_decision = apply_risk(
        signal(StrategyAction.SELL),
        snapshot(),
        position=valuation(),
        config=config,
        context=context,
    )

    assert buy_decision.action is StrategyAction.WATCH
    assert any("流动性" in reason for reason in buy_decision.reasons)
    assert sell_decision.allowed is True
    assert sell_decision.action is StrategyAction.SELL


def test_add_uses_projected_position_value_for_single_symbol_limit() -> None:
    decision = apply_risk(
        signal(StrategyAction.ADD),
        snapshot(total_assets=60000),
        position=valuation(market_value=17000),
        config=RiskConfig(single_position_limit=0.30),
        context=RiskContext(proposed_value=2000),
    )

    assert decision.allowed is False
    assert any("加仓后单票仓位" in reason for reason in decision.reasons)


def test_new_buy_constraint_uses_first_position_cap_and_rounds_to_board_lot() -> None:
    constraint = calculate_buy_constraint(
        current_price=10.01,
        total_assets=60_000,
        available_cash=50_000,
        current_position_value=0,
        current_total_position_value=10_000,
        current_daily_new_buy_value=0,
        has_position=False,
        config=RiskConfig(),
    )

    assert constraint.suggested_quantity == 800
    assert constraint.suggested_value == pytest.approx(8008)
    assert constraint.board_lot == 100
    assert "first_watch_position_max" in constraint.limiting_factors


def test_add_constraint_respects_remaining_single_symbol_capacity() -> None:
    constraint = calculate_buy_constraint(
        current_price=10,
        total_assets=60_000,
        available_cash=50_000,
        current_position_value=17_000,
        current_total_position_value=20_000,
        current_daily_new_buy_value=0,
        has_position=True,
        config=RiskConfig(single_position_limit=0.30),
    )

    assert constraint.suggested_quantity == 100
    assert constraint.suggested_value == 1000
    assert "single_position_limit" in constraint.limiting_factors


def test_buy_constraint_returns_zero_when_cash_cannot_cover_one_board_lot() -> None:
    constraint = calculate_buy_constraint(
        current_price=10,
        total_assets=60_000,
        available_cash=999,
        current_position_value=0,
        current_total_position_value=10_000,
        current_daily_new_buy_value=0,
        has_position=False,
        config=RiskConfig(),
    )

    assert constraint.suggested_quantity == 0
    assert constraint.suggested_value == 0
    assert "available_cash" in constraint.limiting_factors
