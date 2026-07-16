from datetime import UTC, date, datetime

from quantitative_trading.planning.models import MarketPlanSymbolInput, TradingPlanStatus
from quantitative_trading.planning.workflow import build_market_trading_plan
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)


NOW = datetime(2026, 7, 13, 7, 30, tzinfo=UTC)


def etf_metadata(*, settlement: SettlementCycle = SettlementCycle.T1) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol="510300",
        name="沪深300ETF",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.ETF,
        settlement_cycle=settlement,
        price_limit_ratio=0.10,
        metadata_source="exchange_catalog",
        metadata_checked_at=NOW,
        rule_version="instrument-rules-v1",
    )


def symbol_input(**overrides: object) -> MarketPlanSymbolInput:
    data: dict[str, object] = {
        "symbol": "600000",
        "name": "浦发银行",
        "sources": ["holding"],
        "is_holding": True,
        "current_price": 10.5,
        "daily_features": {
            "ma5": 10.2,
            "ma10": 10.0,
            "ma20": 9.8,
            "ma60": 9.5,
            "volume_ratio": 1.4,
            "return_20": 8.2,
        },
        "market_structure": {
            "support": 9.8,
            "resistance": 10.8,
            "atr14": 0.4,
            "reasons": ["最近的有效均线支撑", "最近 20 日高点压力"],
        },
        "money_flow": {
            "status": "complete",
            "main_net_amount": 1_000_000,
            "main_net_pct": 2.1,
        },
        "data_quality": "complete",
        "warnings": [],
    }
    data.update(overrides)
    return MarketPlanSymbolInput(**data)


def test_market_plan_uses_structure_and_atr_instead_of_ledger_cost() -> None:
    plan = build_market_trading_plan(
        trading_day=date(2026, 7, 14),
        now=NOW,
        timezone="Asia/Shanghai",
        universe_snapshot_id=1,
        account_snapshot_id=2,
        ledger_max_updated_at=NOW,
        source_run_id=3,
        market_input_snapshot_id=4,
        data_time=NOW,
        version=1,
        symbols=[symbol_input()],
    )

    assert plan.plan_id == "plan-20260714-v1"
    assert plan.status is TradingPlanStatus.ACTIVE
    assert plan.key_levels["600000"] == {
        "support": 9.8,
        "resistance": 10.8,
        "stop_loss": 9.6,
    }
    assert "cost" not in plan.symbol_contexts["600000"].trend
    assert plan.symbol_contexts["600000"].allowed_actions == [
        "sell",
        "reduce",
        "hold",
        "add",
    ]
    assert any(
        condition.metric == "current_price"
        for condition in plan.symbol_contexts["600000"].conditions
    )


def test_non_holding_market_plan_allows_buy_only_with_usable_history() -> None:
    plan = build_market_trading_plan(
        trading_day=date(2026, 7, 14),
        now=NOW,
        timezone="Asia/Shanghai",
        universe_snapshot_id=1,
        account_snapshot_id=2,
        ledger_max_updated_at=None,
        source_run_id=3,
        market_input_snapshot_id=4,
        data_time=NOW,
        version=2,
        symbols=[
            symbol_input(
                symbol="000001",
                name="平安银行",
                sources=["watch_pinned"],
                is_holding=False,
            )
        ],
    )

    context = plan.symbol_contexts["000001"]
    assert plan.watch_symbols == ["000001"]
    assert context.allowed_actions == ["watch", "avoid", "buy"]
    assert "buy" in plan.candidate_actions["000001"]


def test_missing_money_flow_publishes_degraded_plan_with_explicit_warning() -> None:
    plan = build_market_trading_plan(
        trading_day=date(2026, 7, 14),
        now=NOW,
        timezone="Asia/Shanghai",
        universe_snapshot_id=1,
        account_snapshot_id=2,
        ledger_max_updated_at=NOW,
        source_run_id=3,
        market_input_snapshot_id=4,
        data_time=NOW,
        version=1,
        symbols=[
            symbol_input(
                data_quality="degraded",
                money_flow={"status": "failed"},
                warnings=["资金流不可用"],
            )
        ],
    )

    assert plan.status is TradingPlanStatus.ACTIVE
    assert plan.data_quality == "degraded"
    assert "资金流不可用" in plan.warnings
    flow_conditions = [
        condition
        for condition in plan.symbol_contexts["600000"].conditions
        if condition.metric == "money_flow_positive"
    ]
    assert flow_conditions[0].required is False


def test_unknown_settlement_holding_remains_hold_only_when_data_failed() -> None:
    plan = build_market_trading_plan(
        trading_day=date(2026, 7, 14),
        now=NOW,
        timezone="Asia/Shanghai",
        universe_snapshot_id=1,
        account_snapshot_id=2,
        ledger_max_updated_at=NOW,
        source_run_id=3,
        market_input_snapshot_id=4,
        data_time=NOW,
        version=1,
        symbols=[
            symbol_input(
                symbol="510300",
                name="沪深300ETF",
                instrument=etf_metadata(settlement=SettlementCycle.UNKNOWN),
                data_quality="failed",
            )
        ],
    )

    assert plan.symbol_contexts["510300"].allowed_actions == ["hold"]
    assert plan.candidate_actions["510300"] == ["hold"]


def test_missing_market_structure_never_falls_back_to_cost_ratio_levels() -> None:
    plan = build_market_trading_plan(
        trading_day=date(2026, 7, 14),
        now=NOW,
        timezone="Asia/Shanghai",
        universe_snapshot_id=1,
        account_snapshot_id=2,
        ledger_max_updated_at=NOW,
        source_run_id=3,
        market_input_snapshot_id=4,
        data_time=NOW,
        version=1,
        symbols=[symbol_input(market_structure={}, data_quality="degraded")],
    )

    assert plan.key_levels["600000"] == {}
    assert plan.symbol_contexts["600000"].data_quality == "degraded"
    assert any("市场结构" in warning for warning in plan.warnings)


def test_etf_not_applicable_money_flow_is_quality_neutral() -> None:
    metadata = etf_metadata()
    plan = build_market_trading_plan(
        trading_day=date(2026, 7, 14),
        now=NOW,
        timezone="Asia/Shanghai",
        universe_snapshot_id=1,
        account_snapshot_id=2,
        ledger_max_updated_at=None,
        source_run_id=3,
        market_input_snapshot_id=4,
        data_time=NOW,
        version=1,
        symbols=[
            symbol_input(
                symbol="510300",
                name="沪深300ETF",
                sources=["watch_pinned"],
                is_holding=False,
                instrument=metadata,
                money_flow={"status": "not_applicable"},
            )
        ],
    )

    context = plan.symbol_contexts["510300"]
    assert plan.data_quality == "complete"
    assert context.instrument == metadata
    assert all(condition.metric != "money_flow_positive" for condition in context.conditions)
