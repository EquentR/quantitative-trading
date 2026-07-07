from datetime import UTC, datetime

from quantitative_trading.market.models import QuoteStatus
from quantitative_trading.market.providers import DisabledMarketProvider, MarketDataProvider


def test_disabled_market_provider_returns_failed_quotes_for_each_symbol() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider: MarketDataProvider = DisabledMarketProvider(now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000", "000001"])

    assert set(quotes) == {"600000", "000001"}
    assert quotes["600000"].symbol == "600000"
    assert quotes["600000"].status is QuoteStatus.FAILED
    assert quotes["600000"].source == "disabled"
    assert quotes["600000"].warning == "market fetch disabled"
    assert quotes["600000"].fetched_at == fetched_at
    assert quotes["000001"].symbol == "000001"
    assert quotes["000001"].status is QuoteStatus.FAILED
    assert quotes["000001"].source == "disabled"
    assert quotes["000001"].warning == "market fetch disabled"
    assert quotes["000001"].fetched_at == fetched_at


def test_disabled_market_provider_returns_empty_mapping_for_empty_symbol_list() -> None:
    provider = DisabledMarketProvider(now=lambda: datetime(2026, 7, 7, 2, 30, tzinfo=UTC))

    assert provider.get_quotes([]) == {}
