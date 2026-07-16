import pytest

from quantitative_trading.instrument.models import SettlementCycle
from quantitative_trading.instrument.rules import (
    InstrumentProductCategory,
    InstrumentTradingRuleResolver,
)


@pytest.mark.parametrize(
    ("subclass", "category", "cycle"),
    [
        ("01", InstrumentProductCategory.DOMESTIC_INDEX, SettlementCycle.T1),
        ("03", InstrumentProductCategory.DOMESTIC_INDEX, SettlementCycle.T1),
        ("09", InstrumentProductCategory.DOMESTIC_INDEX, SettlementCycle.T1),
        ("31", InstrumentProductCategory.DOMESTIC_EQUITY, SettlementCycle.T1),
        ("35", InstrumentProductCategory.DOMESTIC_INDEX, SettlementCycle.T1),
        ("02", InstrumentProductCategory.BOND, SettlementCycle.T0),
        ("32", InstrumentProductCategory.BOND, SettlementCycle.T0),
        ("36", InstrumentProductCategory.BOND, SettlementCycle.T0),
        ("37", InstrumentProductCategory.BOND, SettlementCycle.T0),
        ("04", InstrumentProductCategory.CROSS_BORDER, SettlementCycle.T0),
        ("33", InstrumentProductCategory.CROSS_BORDER, SettlementCycle.T0),
        ("34", InstrumentProductCategory.CROSS_BORDER, SettlementCycle.T0),
        ("05", InstrumentProductCategory.MONEY_MARKET, SettlementCycle.T0),
        ("07", InstrumentProductCategory.MONEY_MARKET, SettlementCycle.T0),
        ("06", InstrumentProductCategory.GOLD, SettlementCycle.T0),
        ("38", InstrumentProductCategory.COMMODITY, SettlementCycle.T0),
    ],
)
def test_sse_etf_rules_use_exact_exchange_categories(
    subclass: str,
    category: InstrumentProductCategory,
    cycle: SettlementCycle,
) -> None:
    result = InstrumentTradingRuleResolver().resolve_sse_etf(subclass)

    assert result.category is category
    assert result.settlement_cycle is cycle
    assert result.rule_version == "instrument-trading-rules-v2"
    assert result.warning == ""


@pytest.mark.parametrize(
    ("fund_category", "investment_category", "category", "cycle"),
    [
        ("ETF", "股票基金", InstrumentProductCategory.DOMESTIC_EQUITY, SettlementCycle.T1),
        ("ETF", "债券基金", InstrumentProductCategory.BOND, SettlementCycle.T0),
        ("ETF", "货币市场基金", InstrumentProductCategory.MONEY_MARKET, SettlementCycle.T0),
    ],
)
def test_szse_etf_rules_require_an_exact_category_pair(
    fund_category: str,
    investment_category: str,
    category: InstrumentProductCategory,
    cycle: SettlementCycle,
) -> None:
    result = InstrumentTradingRuleResolver().resolve_szse_etf(
        fund_category,
        investment_category,
    )

    assert result.category is category
    assert result.settlement_cycle is cycle


@pytest.mark.parametrize(
    "subclass",
    [None, "", "08", "其他", "跨境", "跨境ETF", "创新跨境", "黄金主题基金", "LOF", "REIT"],
)
def test_sse_unknown_or_similar_text_never_guesses_a_rule(subclass: str | None) -> None:
    result = InstrumentTradingRuleResolver().resolve_sse_etf(subclass)

    assert result.category is InstrumentProductCategory.UNKNOWN
    assert result.settlement_cycle is SettlementCycle.UNKNOWN
    assert result.warning == "ETF trading category is missing or unsupported"


@pytest.mark.parametrize("investment_category", ["ABS", "其它基金", "混合基金"])
def test_szse_observed_non_t0_categories_remain_unknown(
    investment_category: str,
) -> None:
    resolver = InstrumentTradingRuleResolver()

    result = resolver.resolve_szse_etf("ETF", investment_category)

    assert result.settlement_cycle is SettlementCycle.UNKNOWN


def test_szse_non_etf_category_is_unknown() -> None:
    non_etf = InstrumentTradingRuleResolver().resolve_szse_etf("LOF", "债券基金")

    assert non_etf.settlement_cycle is SettlementCycle.UNKNOWN


def test_a_share_rule_is_always_t1() -> None:
    result = InstrumentTradingRuleResolver().resolve_a_share()

    assert result.category is InstrumentProductCategory.DOMESTIC_EQUITY
    assert result.settlement_cycle is SettlementCycle.T1
