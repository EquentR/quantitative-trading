from collections.abc import Sequence
from datetime import UTC, datetime

import pandas as pd
import pytest

from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus
from quantitative_trading.market.providers import (
    AkShareMarketProvider,
    DisabledMarketProvider,
    MarketDataProvider,
)


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


class FakeAkShare:
    calls = 0

    @classmethod
    def stock_zh_a_spot_em(cls):
        cls.calls += 1
        return pd.DataFrame(
            [
                {"代码": "600000", "名称": "浦发银行", "最新价": 10.5, "涨跌幅": 1.2},
                {"代码": "000001", "名称": "平安银行", "最新价": 12.3, "涨跌幅": -0.5},
            ]
        )


def test_akshare_provider_maps_dataframe_to_ok_quote() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=FakeAkShare, now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000"])

    assert quotes["600000"].symbol == "600000"
    assert quotes["600000"].name == "浦发银行"
    assert quotes["600000"].current_price == 10.5
    assert quotes["600000"].change_pct == 1.2
    assert quotes["600000"].data_time == fetched_at
    assert quotes["600000"].fetched_at == fetched_at
    assert quotes["600000"].source == "akshare"
    assert quotes["600000"].status is QuoteStatus.OK


def test_akshare_provider_returns_failed_quote_when_symbol_not_found() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=FakeAkShare, now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000", "300750"])

    assert quotes["600000"].status is QuoteStatus.OK
    assert quotes["300750"].symbol == "300750"
    assert quotes["300750"].status is QuoteStatus.FAILED
    assert quotes["300750"].source == "akshare"
    assert quotes["300750"].warning == "quote not found"
    assert quotes["300750"].fetched_at == fetched_at


class FailingAkShare:
    @staticmethod
    def stock_zh_a_spot_em():
        raise RuntimeError("service unavailable")


def test_akshare_provider_wraps_fetch_failure_for_all_requested_symbols() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=FailingAkShare, now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000", "000001"])

    assert set(quotes) == {"600000", "000001"}
    for quote in quotes.values():
        assert quote.status is QuoteStatus.FAILED
        assert quote.source == "akshare"
        assert quote.fetched_at == fetched_at
        assert "akshare quote fetch failed" in quote.warning
        assert "service unavailable" in quote.warning


class MixedQualityAkShare:
    @staticmethod
    def stock_zh_a_spot_em():
        return pd.DataFrame(
            [
                {"代码": "600000", "名称": "浦发银行", "最新价": float("nan"), "涨跌幅": 1.2},
                {"代码": "000001", "名称": "平安银行", "最新价": 12.3, "涨跌幅": -0.5},
            ]
        )


def test_akshare_provider_bad_price_does_not_block_other_quotes() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=MixedQualityAkShare, now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000", "000001"])

    assert quotes["600000"].status is QuoteStatus.FAILED
    assert "akshare quote mapping failed" in quotes["600000"].warning
    assert "最新价" in quotes["600000"].warning
    assert quotes["000001"].status is QuoteStatus.OK
    assert quotes["000001"].current_price == 12.3


def test_akshare_provider_serializes_only_internal_quote_fields() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=FakeAkShare, now=lambda: fetched_at)

    payload_json = provider.get_quotes(["600000"])["600000"].model_dump_json()

    assert "代码" not in payload_json
    assert "最新价" not in payload_json
    assert "涨跌幅" not in payload_json
    assert '"current_price"' in payload_json
    assert '"change_pct"' in payload_json


class CountingAkShare(FakeAkShare):
    calls = 0


def test_akshare_provider_rejects_naive_fetch_time_before_fetch() -> None:
    provider = AkShareMarketProvider(
        akshare_module=CountingAkShare,
        now=lambda: datetime(2026, 7, 7, 2, 30),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        provider.get_quotes(["600000"])

    assert CountingAkShare.calls == 0
