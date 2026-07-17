from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from quantitative_trading.decision.models import DecisionSymbolInput
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.planning.models import MarketPlanSymbolInput, PlanSymbolContext
from quantitative_trading.recommendation.models import Recommendation
from quantitative_trading.universe.models import UniverseMember, UniverseSource


NOW = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)


def instrument(symbol: str = "600519") -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        name="instrument",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        metadata_source="test-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )


def universe_payload() -> dict[str, object]:
    return {
        "symbol": "000001",
        "name": "outer",
        "instrument": instrument(),
        "sources": [UniverseSource.WATCH_PINNED],
        "priority": 1,
        "plan_enabled": True,
        "plan_enabled_source": UniverseSource.WATCH_PINNED,
        "created_at": NOW,
    }


def plan_context_payload() -> dict[str, object]:
    return {
        "symbol": "000001",
        "name": "outer",
        "instrument": instrument(),
        "sources": ["watch_pinned"],
        "is_holding": False,
    }


def market_plan_input_payload() -> dict[str, object]:
    return {
        "symbol": "000001",
        "name": "outer",
        "instrument": instrument(),
        "sources": ["watch_pinned"],
        "is_holding": False,
        "current_price": 10.0,
        "daily_features": {},
        "market_structure": {},
        "money_flow": {},
        "data_quality": "complete",
    }


def decision_payload() -> dict[str, object]:
    return {
        "symbol": "000001",
        "name": "outer",
        "instrument": instrument(),
        "is_holding": False,
        "current_price": 10.0,
        "plan_active": True,
        "plan_allows_entry": True,
        "plan_condition_met": True,
        "daily_structure_confirmed": True,
        "intraday_strength": "strong",
        "money_flow_confirmed": None,
        "data_quality": "complete",
        "quote_status": "complete",
        "quote_usable": True,
        "history_status": "complete",
        "history_usable": True,
        "intraday_status": "complete",
        "intraday_usable": True,
        "plan_status": "active",
        "position_context": {},
        "account_context": {},
        "price_context": {},
        "data_references": {},
        "invalid_if": ["condition fails"],
        "data_time": NOW,
        "fetched_at": NOW,
        "valid_until": NOW,
        "run_id": "run-1",
        "market_input_snapshot_id": 1,
    }


def recommendation_payload() -> dict[str, object]:
    return {
        "recommendation_id": "recommendation-1",
        "symbol": "000001",
        "name": "outer",
        "instrument": instrument(),
        "action": "watch",
        "confidence": "medium",
        "position_context": {},
        "account_context": {},
        "price_context": {},
        "reason": ["rule matched"],
        "risk": {"invalid_if": ["condition fails"]},
        "valid_until": NOW,
        "data_time": NOW,
    }


@pytest.mark.parametrize(
    ("model", "payload_factory"),
    [
        (UniverseMember, universe_payload),
        (PlanSymbolContext, plan_context_payload),
        (MarketPlanSymbolInput, market_plan_input_payload),
        (DecisionSymbolInput, decision_payload),
        (Recommendation, recommendation_payload),
    ],
)
def test_outer_symbol_must_match_instrument_symbol(
    model: type[BaseModel],
    payload_factory,
) -> None:
    with pytest.raises(ValidationError, match="symbol must match instrument symbol"):
        model.model_validate(payload_factory())


@pytest.mark.parametrize(
    ("model", "payload_factory"),
    [
        (UniverseMember, universe_payload),
        (PlanSymbolContext, plan_context_payload),
        (MarketPlanSymbolInput, market_plan_input_payload),
        (DecisionSymbolInput, decision_payload),
        (Recommendation, recommendation_payload),
    ],
)
def test_matching_instrument_symbol_is_accepted(
    model: type[BaseModel],
    payload_factory,
) -> None:
    payload = payload_factory()
    payload["instrument"] = instrument("000001")

    assert model.model_validate(payload).symbol == "000001"
