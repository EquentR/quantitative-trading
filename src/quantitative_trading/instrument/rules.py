from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from quantitative_trading.instrument.models import SettlementCycle


INSTRUMENT_TRADING_RULE_VERSION = "instrument-trading-rules-v2"


class InstrumentProductCategory(StrEnum):
    CROSS_BORDER = "cross_border"
    BOND = "bond"
    GOLD = "gold"
    COMMODITY = "commodity"
    MONEY_MARKET = "money_market"
    DOMESTIC_EQUITY = "domestic_equity"
    DOMESTIC_INDEX = "domestic_index"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TradingRuleResolution:
    category: InstrumentProductCategory
    settlement_cycle: SettlementCycle
    rule_version: str = INSTRUMENT_TRADING_RULE_VERSION
    warning: str = ""


_T0_CATEGORIES = {
    InstrumentProductCategory.CROSS_BORDER,
    InstrumentProductCategory.BOND,
    InstrumentProductCategory.GOLD,
    InstrumentProductCategory.COMMODITY,
    InstrumentProductCategory.MONEY_MARKET,
}
_T1_CATEGORIES = {
    InstrumentProductCategory.DOMESTIC_EQUITY,
    InstrumentProductCategory.DOMESTIC_INDEX,
}

_SSE_CATEGORY_MAP = {
    "01": InstrumentProductCategory.DOMESTIC_INDEX,
    "02": InstrumentProductCategory.BOND,
    "03": InstrumentProductCategory.DOMESTIC_INDEX,
    "04": InstrumentProductCategory.CROSS_BORDER,
    "05": InstrumentProductCategory.MONEY_MARKET,
    "06": InstrumentProductCategory.GOLD,
    "07": InstrumentProductCategory.MONEY_MARKET,
    "09": InstrumentProductCategory.DOMESTIC_INDEX,
    "31": InstrumentProductCategory.DOMESTIC_EQUITY,
    "32": InstrumentProductCategory.BOND,
    "33": InstrumentProductCategory.CROSS_BORDER,
    "34": InstrumentProductCategory.CROSS_BORDER,
    "35": InstrumentProductCategory.DOMESTIC_INDEX,
    "36": InstrumentProductCategory.BOND,
    "37": InstrumentProductCategory.BOND,
    "38": InstrumentProductCategory.COMMODITY,
}

_SZSE_CATEGORY_MAP = {
    ("ETF", "股票基金"): InstrumentProductCategory.DOMESTIC_EQUITY,
    ("ETF", "债券基金"): InstrumentProductCategory.BOND,
    ("ETF", "货币市场基金"): InstrumentProductCategory.MONEY_MARKET,
}


class InstrumentTradingRuleResolver:
    def resolve_a_share(self) -> TradingRuleResolution:
        return self._resolution(InstrumentProductCategory.DOMESTIC_EQUITY)

    def resolve_sse_etf(self, subclass: str | None) -> TradingRuleResolution:
        category = _SSE_CATEGORY_MAP.get("" if subclass is None else subclass.strip())
        return self._resolution(category)

    def resolve_szse_etf(
        self,
        fund_category: str | None,
        investment_category: str | None,
    ) -> TradingRuleResolution:
        key = (
            "" if fund_category is None else fund_category.strip(),
            "" if investment_category is None else investment_category.strip(),
        )
        return self._resolution(_SZSE_CATEGORY_MAP.get(key))

    @staticmethod
    def _resolution(
        category: InstrumentProductCategory | None,
    ) -> TradingRuleResolution:
        if category in _T0_CATEGORIES:
            return TradingRuleResolution(
                category=category,
                settlement_cycle=SettlementCycle.T0,
            )
        if category in _T1_CATEGORIES:
            return TradingRuleResolution(
                category=category,
                settlement_cycle=SettlementCycle.T1,
            )
        return TradingRuleResolution(
            category=InstrumentProductCategory.UNKNOWN,
            settlement_cycle=SettlementCycle.UNKNOWN,
            warning="ETF trading category is missing or unsupported",
        )
