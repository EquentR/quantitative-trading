from collections.abc import Sequence
from datetime import UTC, datetime

from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus
from quantitative_trading.market.providers import DisabledMarketProvider, MarketDataProvider


class SparseFakeMarketProvider:
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        return {
            "600000": QuoteSnapshot(
                symbol="600000",
                name="Pufa Bank",
                current_price=10.5,
                change_pct=1.2,
                data_time=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
                fetched_at=datetime(2026, 7, 7, 2, 30, 3, tzinfo=UTC),
                source="fake",
                status=QuoteStatus.OK,
            )
        }


def test_market_data_provider_contract_allows_sparse_quote_mapping() -> None:
    provider: MarketDataProvider = SparseFakeMarketProvider()

    quotes = provider.get_quotes(["600000", "000001"])

    assert set(quotes) == {"600000"}
    assert "000001" not in quotes


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
