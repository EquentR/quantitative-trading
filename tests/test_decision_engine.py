from datetime import UTC, datetime

import pytest

from quantitative_trading.account.models import (
    AccountSnapshot,
    AccountSnapshotStatus,
    PositionValuation,
    PositionValuationStatus,
)
from quantitative_trading.decision.models import DecisionSymbolInput
from quantitative_trading.decision.service import decide_symbol
from quantitative_trading.recommendation.models import RecommendationAction
from quantitative_trading.risk.models import RiskConfig, RiskContext
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)


NOW = datetime(2026, 7, 9, 2, 30, tzinfo=UTC)
VALID_UNTIL = datetime(2026, 7, 9, 7, 0, tzinfo=UTC)


def instrument_metadata(
    *,
    instrument_type: InstrumentType = InstrumentType.A_SHARE,
    settlement: SettlementCycle = SettlementCycle.T1,
) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol="600000",
        name="浦发银行",
        exchange=Exchange.SH if instrument_type is not InstrumentType.UNKNOWN else None,
        instrument_type=instrument_type,
        settlement_cycle=settlement,
        price_limit_ratio=0.10 if instrument_type is not InstrumentType.UNKNOWN else None,
        metadata_source="exchange_catalog",
        metadata_checked_at=NOW,
        rule_version="instrument-rules-v1",
    )


def account_snapshot() -> AccountSnapshot:
    valuation = PositionValuation(
        symbol="600000",
        name="浦发银行",
        quantity=1000,
        available_quantity=1000,
        cost_price=9.5,
        position_cost=9500,
        current_price=10,
        market_value=10000,
        floating_pnl=500,
        floating_pnl_pct=500 / 9500,
        ledger_updated_at=NOW,
        quote_data_time=NOW,
        quote_fetched_at=NOW,
        status=PositionValuationStatus.OK,
        warning="",
    )
    return AccountSnapshot(
        cash_balance=50_000,
        net_principal=70_000,
        market_value=10_000,
        position_cost=9_500,
        floating_pnl=500,
        floating_pnl_pct=500 / 9500,
        total_assets=60_000,
        total_pnl=10_000,
        total_pnl_pct=10_000 / 70_000,
        position_ratio=10_000 / 60_000,
        available_buying_cash=50_000,
        positions=[valuation],
        status=AccountSnapshotStatus.OK,
        warnings=[],
        created_at=NOW,
    )


def decision_input(**overrides: object) -> DecisionSymbolInput:
    data: dict[str, object] = {
        "symbol": "600000",
        "name": "浦发银行",
        "instrument": instrument_metadata(),
        "is_holding": False,
        "current_price": 10.5,
        "support_price": 9.7,
        "stop_loss_price": 9.3,
        "short_ma": 10.1,
        "plan_id": "plan-20260709-v1",
        "plan_active": True,
        "plan_allows_entry": True,
        "plan_condition_met": True,
        "daily_structure_confirmed": True,
        "intraday_strength": "strong",
        "money_flow_confirmed": True,
        "data_quality": "complete",
        "trading_status": "normal",
        "limit_status": "none",
        "position_context": {"source": "manual_ledger", "quantity": 0},
        "account_context": {"source": "manual_cash_account"},
        "price_context": {"current_price": 10.5, "key_levels": {"support": 9.7}},
        "data_references": {
            "ledger": {"snapshot_id": 1, "status": "complete"},
            "account": {"snapshot_id": 2, "status": "complete"},
            "quote": {"snapshot_id": 3, "status": "complete"},
            "history": {"snapshot_id": 4, "status": "complete"},
            "money_flow": {"snapshot_id": 5, "status": "complete"},
            "intraday": {"snapshot_id": 6, "status": "complete"},
            "plan": {"plan_id": "plan-20260709-v1", "status": "active"},
        },
        "invalid_if": ["跌破计划支撑位", "计划收盘失效"],
        "data_time": NOW,
        "fetched_at": NOW,
        "valid_until": VALID_UNTIL,
        "run_id": 7,
        "market_input_snapshot_id": 8,
    }
    data.update(overrides)
    return DecisionSymbolInput(**data)


def test_non_holding_plan_confirmation_produces_risk_approved_buy() -> None:
    recommendation = decide_symbol(
        decision_input(),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(proposed_value=8_000, liquidity_amount=2_000_000),
        recommendation_id="rec-buy",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.BUY
    assert recommendation.run_id == 7
    assert recommendation.plan_id == "plan-20260709-v1"
    assert recommendation.position_constraint["suggested_quantity"] == 700
    assert recommendation.data_references["intraday"]["snapshot_id"] == 6


def test_non_holding_without_active_plan_is_watch_only() -> None:
    recommendation = decide_symbol(
        decision_input(plan_active=False, plan_id=None),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(),
        recommendation_id="rec-watch",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.WATCH
    assert "缺少当日有效收盘计划" in recommendation.reason


def test_holding_stop_loss_overrides_missing_plan() -> None:
    recommendation = decide_symbol(
        decision_input(
            is_holding=True,
            current_price=9.2,
            plan_active=False,
            plan_id=None,
            position_context={
                "source": "manual_ledger",
                "quantity": 1000,
                "available_quantity": 1000,
                "market_value": 9200,
            },
        ),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(),
        recommendation_id="rec-sell",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.SELL
    assert "跌破止损位" in recommendation.reason


def test_holding_sell_is_downgraded_to_hold_when_t1_quantity_is_zero() -> None:
    recommendation = decide_symbol(
        decision_input(
            is_holding=True,
            current_price=9.2,
            position_context={
                "source": "manual_ledger",
                "quantity": 1000,
                "available_quantity": 0,
                "market_value": 9200,
            },
        ),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(),
        recommendation_id="rec-hold-t1",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.HOLD
    assert any("T+1" in note for note in recommendation.risk["notes"])


def test_missing_quote_keeps_holding_in_conservative_management() -> None:
    recommendation = decide_symbol(
        decision_input(
            is_holding=True,
            current_price=None,
            data_quality="failed",
            position_context={
                "source": "manual_ledger",
                "quantity": 1000,
                "available_quantity": 1000,
            },
        ),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(),
        recommendation_id="rec-hold-missing",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.HOLD
    assert recommendation.confidence == "low"
    assert "当前行情不可用，暂停价格触发型动作" in recommendation.reason


def test_failed_required_data_avoids_non_holding_symbol() -> None:
    recommendation = decide_symbol(
        decision_input(current_price=None, data_quality="failed"),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(),
        recommendation_id="rec-avoid",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.AVOID


def test_existing_holding_uses_add_action_when_entry_plan_confirms() -> None:
    recommendation = decide_symbol(
        decision_input(
            is_holding=True,
            position_context={
                "source": "manual_ledger",
                "quantity": 1000,
                "available_quantity": 1000,
                "market_value": 10000,
            },
        ),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(proposed_value=5_000, liquidity_amount=2_000_000),
        recommendation_id="rec-add",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.ADD
    assert recommendation.position_constraint["suggested_quantity"] > 0


@pytest.mark.parametrize(
    "status_override",
    [
        {"trading_status": "unknown"},
        {"limit_status": "unknown"},
    ],
)
def test_unknown_tradeability_fields_block_non_holding_buy(
    status_override: dict[str, str],
) -> None:
    recommendation = decide_symbol(
        decision_input(data_quality="degraded", **status_override),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(proposed_value=8_000, liquidity_amount=2_000_000),
        recommendation_id="rec-unknown-entry",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.WATCH
    assert "tradeability_unknown" in recommendation.risk["machine_reason"]
    assert any("交易状态" in reason for reason in recommendation.reason)


@pytest.mark.parametrize(
    "status_override",
    [
        {"trading_status": "unknown"},
        {"limit_status": "unknown"},
    ],
)
def test_unknown_tradeability_fields_block_holding_add(
    status_override: dict[str, str],
) -> None:
    recommendation = decide_symbol(
        decision_input(
            is_holding=True,
            data_quality="degraded",
            position_context={
                "source": "manual_ledger",
                "quantity": 1000,
                "available_quantity": 1000,
                "market_value": 10000,
            },
            **status_override,
        ),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(proposed_value=5_000, liquidity_amount=2_000_000),
        recommendation_id="rec-unknown-add",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.HOLD
    assert "tradeability_unknown" in recommendation.risk["machine_reason"]


def test_holding_risk_action_at_price_limit_warns_execution_may_fail() -> None:
    recommendation = decide_symbol(
        decision_input(
            is_holding=True,
            current_price=9.2,
            limit_status="down",
            position_context={
                "source": "manual_ledger",
                "quantity": 1000,
                "available_quantity": 1000,
                "market_value": 9200,
            },
        ),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(),
        recommendation_id="rec-limit-risk",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.SELL
    assert any("可能无法成交" in reason for reason in recommendation.reason)


def test_unknown_instrument_gate_runs_before_holding_stop_loss() -> None:
    recommendation = decide_symbol(
        decision_input(
            instrument=instrument_metadata(
                instrument_type=InstrumentType.UNKNOWN,
                settlement=SettlementCycle.UNKNOWN,
            ),
            is_holding=True,
            current_price=9.2,
            position_context={
                "source": "manual_ledger",
                "quantity": 1000,
                "available_quantity": 1000,
                "market_value": 9200,
            },
        ),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(),
        recommendation_id="rec-unknown-hold",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.HOLD
    assert "instrument_metadata_unknown" in recommendation.risk["machine_reason"]


def test_t0_available_quantity_gate_does_not_claim_t1() -> None:
    metadata = InstrumentMetadata(
        symbol="510300",
        name="沪深300ETF",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.ETF,
        settlement_cycle=SettlementCycle.T0,
        price_limit_ratio=0.10,
        metadata_source="exchange_catalog",
        metadata_checked_at=NOW,
        rule_version="instrument-rules-v1",
    )
    recommendation = decide_symbol(
        decision_input(
            symbol="510300",
            name="沪深300ETF",
            instrument=metadata,
            is_holding=True,
            current_price=9.2,
            position_context={
                "source": "manual_ledger",
                "quantity": 1000,
                "available_quantity": 0,
                "market_value": 9200,
            },
        ),
        account_snapshot=account_snapshot(),
        risk_config=RiskConfig(),
        risk_context=RiskContext(instrument=metadata),
        recommendation_id="rec-etf-t0-hold",
        created_at=NOW,
    )

    assert recommendation.action is RecommendationAction.HOLD
    assert all("T+1" not in note for note in recommendation.risk["notes"])
    assert recommendation.instrument == metadata
